# アーキテクチャ (Architecture)

最終更新: 2026-10-18

NAS2GP は NAS 上の写真・動画を USB(ADB) 経由で 端末 へ転送し、
Google Photos アプリの標準バックアップ完了を確認したうえで 端末 内の実体を削除する
一連の同期パイプラインです。操作系は **Web UI に一本化** されています (旧デスクトップ GUI は撤去済)。

---

## 1. 全体構成 (レイヤ分割 + Web UI 層)

```
          [Windows PC ブラウザ / コンソール]
                     |
                     v
   source/entry_web.py / app.py / db_admin.py   (薄いエントリポイント)
                     |
                     v
   +================================================================+
   |          nas2gp (メインパッケージ - 7 サブパッケージ)              |
   +================================================================+
    | Layer 1: core/ (横断共通基盤)
    |   settings.py / logger.py / util.py / config.py / config_loader.py
    |   events.py / control.py / maintenance.py / diagnostics.py
    |   disconnect_log.py (切断解析: ハートビート / スナップショット)
    |   consent.py (利用同意: 免責事項ゲート / consent.json 管理)
    |   i18n.py (多言語 + 単語バンク外部化)  /  lang/{ja,en}.py
    |
    | Layer 2: scanner/ (ローカルファイル走査) + storage/media.py
    |   scanner/local.py  (PHOTO_DIR 走査 + 重複排除)
    |
    | Layer 3: device/ (端末 / ADB)  ← 安定化・性能改善の重点レイヤ
    |   adb.py    (retry / 自動再接続 / shell probe 疎通確認 / health)
    |   ui.py     (wake / unlock / scroll_to_top / prepare_ui)
    |   device.py  (push + scan_media_batch 一括化)
    |
    | Layer 4: worker/ (同期フロー制御)
    |   sync_flow.py      (全体フェーズ制御 INIT/SCAN/UPLOAD/WAIT/DELETE/FINALIZE)
    |   backup_waiter.py  (Google Photos バックアップ完了待ち / 受理ルール)
    |
    | Layer 5: uploader/ (バッチ分割・転送)
    |   flow.py  (_build_batches : 件数 / 容量 / ハイブリッド)
    |
    | Layer 6: storage/ (SQLite)
    |   db.py              (スキーマ / 5 状態カラム分離管理)
    |   db_batch.py        (バッチ状態更新)
    |   db_stats.py        (速度計算: backup 待ち除外)
    |   db_view.py         (読み取り専用 参照層 mode=ro / 一覧・時系列集計)
    |   db_debug.py        (整合性検査)
    |   db_maintenance.py  (VACUUM / backup)
    |
    | Layer 7: ui_web/ (FastAPI + Jinja2 + SSE)
    |   main.py            (create_app / ルーティング / SSE / 設定スキーマ)
    |   templates/         (7 ページ)
    +================================================================+
                     |
          +----------+-----------+
          v                      v
   [NAS/SMB 写真共有]      [端末 (USB ADB) 内部ストレージ]
          |                      |
          v                      v
   フォルダ走査・重複排除     ADB push 一括
                          Media Scanner broadcast
                          UIAutomator dump (画面文言)
                          Google Photos 標準バックアップ
```

> クライアントは Web ブラウザ / コンソールです (Windows / macOS / Linux。macOS / Linux 対応は v1.9.0 で追加、ベストエフォート)。
> 起動用ランチャーは `source/bin/` に集約されています (`*.cmd` = Windows、`*.sh` / `*.command` = macOS / Linux)。`.sh` / `.command` は `.venv/bin/python` があればそれを、無ければ `python3` を使用します。

---

## 2. サブパッケージ構成

| パッケージ | 役割 | 主なファイル |
|---|---|---|
| `nas2gp.core/` | 共通基盤 | `settings.py`, `logger.py`, `i18n.py`, `util.py`, `config.py`, `config_loader.py`, `events.py`, `control.py`, `maintenance.py`, `diagnostics.py`, `disconnect_log.py`, `consent.py`, `lang/{ja,en}.py` |
| `nas2gp.device/` | 端末 操作・ADB 抽象 | `adb.py`, `ui.py`, `device.py` |
| `nas2gp.scanner/` | ローカルファイル走査 | `local.py` |
| `nas2gp.storage/` | DB 層 | `db.py`, `db_batch.py`, `db_stats.py`, `db_view.py`, `db_debug.py`, `db_maintenance.py`, `media.py` |
| `nas2gp.uploader/` | バッチ転送ロジック | `flow.py` |
| `nas2gp.worker/` | 同期フロー制御 | `sync_flow.py`, `backup_waiter.py` |
| `nas2gp.ui_web/` | Web UI (FastAPI + Jinja2 + SSE) | `main.py`, `templates/*.html` |

---

## 3. 同期フェーズ

### 3.1 マクロフェーズ (worker/sync_flow.py の流れ)

```
INIT      DB マイグレーション / 整合性チェック / ADB 接続確認 / 設定適用
          atexit + signal 登録 (= 終了時 必ず 端末 画面 OFF)

SCAN      PHOTO_DIR 再帰走査 → 拡張子 whitelist → サイズ+更新日時で重複排除
          SKIP は DB 更新なし (書き込み量を最小化)。完了後 DB バックアップ

UPLOAD    バッチ分割 (件数 / 容量 / ハイブリッド) → ADB push → Media Scan

WAIT      Google Photos バックアップ完了待ち (画面文言ベース判定)

DELETE    バックアップ完了が確定したファイルのみ 端末 内から削除

FINALIZE  統計サマリ出力 / DB VACUUM / 端末 画面 OFF
```

### 3.2 バッチ内 7 工程 (Web UI の工程インジケータと一致)

`uploader/flow.py` が `_publish_phase()` で通知する工程番号 (phase_no = 1..7 / total_phases = 7)。

| # | 工程 | 内容 |
|---|---|---|
| 1 | `CAPACITY` | 容量確保 (バッチ前に空き容量をチェックし必要なら GC) |
| 2 | `GP_STOP` | (任意) Google Photos 停止サイクルの前段 |
| 3 | `UPLOAD` | ADB push による転送 + DB 反映 |
| 4 | `MEDIA_SCAN` | Media Scanner broadcast 一括実行 |
| 5 | `GP_START` | (任意) Google Photos 起動 + scroll_to_top |
| 6 | `WAIT_BACKUP` | Google Photos バックアップ完了待ち |
| 7 | `DELETE` | バックアップ確定ファイルの 端末 内削除 |

補助工程として、バッチ前の `GC` と 復旧時の `RECOVER` も `_publish_phase()` で通知されます。

### 3.3 自動復旧 (RECOVER)

転送や接続の失敗が続いた場合、最大 3 ループ (`_AUTO_RECOVER_MAX = 3`) で段階的に復旧します。

```
S1: GC 再実行 (空き容量の再確保 / NAS2GP 転送済みファイルの削除)
S2: 端末 再起動 (reboot_device, 再起動後 待機 45 秒)
```

- v1.8.0: 個人 の 写真 を 巻き込む 恐れ が あっ た 「/sdcard/DCIM 全体 削除」ステップ は 撤廃 し 、 NAS2GP が 端末 へ 転送 し た ファイル 単位 の 削除 (S1) のみ と し た 。
- v1.9.0: macOS / Linux 対応 を ベストエフォート で 追加 。 ファイル マネージャ 起動 を `open_in_file_manager()` に 集約 (Windows `os.startfile` / macOS `open` / Linux `xdg-open`) し 、 `.sh` / `.command` ランチャー を `source/bin/` に 追加 し た 。 実機 macOS で の 動作 確認 は 未 実施。
3 ループで解決しない場合は、無限ループを避けるため安全に停止します。

---

## 4. DB スキーマ (files テーブル) — 5 状態カラム分離方針

工程ごとに状態を独立したカラムで管理します。単一 `status` カラムは使用しません。

| カラム | 型 | 取りうる値 | 役割 |
|---|---|---|---|
| `nas_state` | TEXT | `NEW` / `UPDATED` | 転送元 (NAS/ローカル) の状態 |
| `phone_transfer_state` | TEXT | `PENDING` / `DONE` / `FAILED` | ADB 転送工程 |
| `google_photos_state` | TEXT | `PENDING` / `DONE` / `FAILED` | Google Photos 側バックアップ |
| `phone_delete_state` | TEXT | `PENDING` / `DONE` / `FAILED` | 端末 内ファイル削除工程 |
| `verify_state` | TEXT | `PENDING` / `DONE` / `FAILED` | 事後検証 (将来用) |

- 主要な付随カラム: `path`, `nas_path`, `filename`, `extension`, `size`, `mtime`, `media_type`,
  `transferred_at`, `backup_started_at`, `backup_completed_at`, `deleted_at`, `verified_at`,
  `retry_count`, `last_error`, `created_at`, `updated_at`
  - `transferred_at` は転送完了 (`phone_transfer_state='DONE'`) 時に記録します (v1.7.19)。
    統計ページの時間別集計 (転送 GB/時間・件数推移) はこの列を基準にします。
- `batch_history` テーブル: バッチ単位の実績 (`started_at`, `completed_at`, `file_count`,
  `total_bytes`, `elapsed_seconds`, `upload_seconds`, `backup_seconds`, `status` など)。
  容量ベースの転送量・実効速度は過去分もこのテーブルから集計します。
- インデックス: `phone_transfer_state` / `google_photos_state` / `phone_delete_state` /
  `transferred_at` (`idx_files_transferred_at`, v1.7.19 追加) の各列

> アップロード対象の判定は `WHERE phone_transfer_state='PENDING'` を正とします
> (nas_state と分離しているため再開処理が単純になります)。

### 4.1 DB の格納場所

実運用の DB は `source/database/nas2gp.db` です (`source/database/.gitkeep` によりフォルダは追跡)。
統計・一覧の参照は必ず `db_view.py` の読み取り専用接続 (`mode=ro` + `busy_timeout`) を使い、
書き込みプロセスとの競合を避けます。

---

## 5. Web UI アーキテクチャ (FastAPI + Jinja2 + SSE)

### 5.1 ページ構成 (7 ページ / 共通 `base.html`)

| パス | テンプレート | 内容 |
|---|---|---|
| `/` | `index.html` | ホーム: 開始 / 停止 / 今回のバッチで終了 / ステータスバナー / 工程インジケータ / 接続デバイス |
| `/console` | `console.html` | コンソール: 直近ログ (SSE) / 種別フィルタ |
| `/settings` | `settings.html` | 設定: スキーマ駆動 (工程順 9 グループ + 各説明) / 言語切替 |
| `/device` | `device.html` | 端末: デバイス詳細 / 手動操作 |
| `/management` | `management.html` | 管理: ファイル一覧 (列独立フィルタ + ページング) + 3 OPEN + 3 RESET (OLD rename + 多段確認) |
| `/stats` | `stats.html` | 統計 (分析専用): 期間切替 (24 時間 / 7 日 / 30 日) + 時系列グラフ (転送量・件数・速度・電池・温度) |
| `/diagnostics` | `diagnostics.html` | 診断: デバイス情報 / DB 状態 / 言語整合性 / バグレポート ZIP |

### 5.2 バックエンド → フロントエンド伝播

| 経路 | 内容 | 備考 |
|---|---|---|
| SSE `/progress/sse` | フェーズ変化 / 進捗 / 統計 / デバイス情報 / Google Photos 進捗 | `core/events.py` のイベント経由。既定 1 秒間隔 |
| SSE `/api/logs/sse` | コンソールログ (直近 300 行 → リアルタイム) | `_WebTee` + Logger の Web キャプチャ |
| ポーリング `/api/status` | 同期状態 / バッチ番号 / 統計サマリ | 必要時のみ |
| ポーリング `/api/adb/devices` | 接続デバイス状態 (緑/黄/灰) / ヘルス情報 | 30 秒周期 |
| 操作 API | 開始 / 停止 / 今回のバッチで終了 / 各リセット | `core/control.py` のフラグ経由 |

> `_WebTee` は `sys.stdout/stderr` を横取りし、`Logger` 由来の行と重複しないよう
> フィルタして SSE に流します。

### 5.3 主要 API 一覧

```
GET  /api/status                      同期状態 / 進捗
GET  /api/adb/devices                 接続デバイス一覧 + 状態
GET  /api/adb/device_info             デバイス詳細
GET  /api/settings                    現在の設定
PUT  /api/settings                    設定保存
GET  /api/settings/schema             設定 UI スキーマ
GET  /api/config/schema               設定スキーマ情報
GET  /api/logs                        直近ログ
GET  /api/logs/sse                    ログ SSE
GET  /api/files                       ファイル一覧 (フィルタ / ページング)
GET  /api/files/summary               統計サマリ
GET  /api/stats/timeseries            統計 時系列 集計 (期間 24h/7d/30d, DB + health.jsonl)
GET  /api/diagnostics                 診断情報
GET  /api/diagnostics/bugreport       バグレポート ZIP 生成
GET  /api/consent                     同意 状態 取得 (同意 済み / 同意 日時 / バージョン)
POST /api/consent                     同意 を 記録 (1 回 のみ 確認 し consent.json に 記憶)
DELETE /api/consent                   同意 記録 を 削除 (次回 表示 で 再確認)
POST /api/maintenance/reset_progress  進捗リセット
POST /api/maintenance/reset_settings  設定リセット
POST /api/maintenance/factory_reset   工場出荷時リセット
GET  /api/device/workdir              端末 作業フォルダ 取得
POST /api/device/workdir              端末 作業フォルダ 変更 (DCIM 配下 のみ, config 保存)
GET  /api/device/browse               DCIM 配下 サブフォルダ 一覧 (?path=)
POST /api/device/delete_uploaded      NAS2GP が 転送 し た ファイル のみ 削除 (安全版)
POST /api/device/reconcile_delete     削除 状態 の 整合 (端末 に 実在 する の に DB が DONE の 行 を PENDING へ 戻す)
POST /api/open_path                   フォルダを開く
POST /api/start                       同期開始
POST /api/stop                        即時停止
POST /api/stop_batch                  今回のバッチで終了
GET  /progress/sse                    進捗 SSE
```

> `POST /api/open_path` は `core/util.py` の `open_in_file_manager()` を呼び出します。Windows は `os.startfile`、macOS は `open`、Linux は `xdg-open` を使い分け、「フォルダを開く」が Windows / macOS / Linux で動作します (v1.9.0)。
>
> `POST /api/start` (同期開始) と `POST /api/device/delete_uploaded` (転送済みファイル削除) は、先頭で利用同意を確認し、未同意なら HTTP 409 と `{"ok": false, "error": "consent_required", "message": "..."}` を返します (v1.10.0)。同意状態は `nas2gp.core.consent` が `config/consent.json` で管理します。

---

## 6. 多言語 i18n アーキテクチャ (単語バンク 3 階層合成)

```
最下層 (マスター / 100% 保証):  nas2gp.core.lang.ja.WORDS
          ↓  不足キーは最終的にこの値が残る
中間層 (英語選択時に上書き):   nas2gp.core.lang.en.WORDS
          ↓  部分 / 全部を上書き可
最上層 (ユーザー自由カスタム): source/config/lang/{言語コード}.json
          { "label": "表示名", "words": { "web.btn.start": "始める", ... } }
```

- **部分翻訳 OK**: `words` に任意のキーだけ書けば、残りは自動的に下層 (最悪 JA) が残るため UI は壊れません。
- **動的スキャン**: `i18n.list_available_languages()` が `source/config/lang/*.json` を glob し、起動時 / 設定保存時に言語リストへ追加します。
- **診断 API**: `i18n.diagnose_wordbank(code)` が不足キー数 / JA-EN キー一致 / 重複 / 外部 JSON 情報を返し、診断ページの言語整合性カードに反映します。
- **スキーマ merge**: `config_loader._merge_missing_schema()` が旧 config に不足するキーをデフォルト値で補完し、KeyError を防ぎます。
- フロント JS は `window.I18N` (テンプレートと同じ辞書) を参照し、文言を統一します。

---

## 7. 設計原則 / ハード制約

| 制約 | 内容 | 該当箇所 |
|---|---|---|
| SJIS 絵文字禁止 | Windows CMD の UnicodeEncodeError 防止。コンソール出力に絵文字を使わない。 | コンソール出力全般 |
| 終了時 端末 OFF | atexit + signal (SIGINT/SIGTERM) の両方で必ず画面 OFF を発行。 | core/util の atexit 登録 |
| Web ログキャプチャ | stdout/stderr + Logger を SSE へ流しコンソールページに反映。 | `ui_web.main._WebTee` |
| 安全リセット = OLD rename | `rm` ではなく `_OLD_YYYYMMDD_HHMMSS_` prefix で rename。誤操作から復元可能。 | `core/maintenance.py` |
| 読み取り専用 DB 参照 | 統計・一覧は `mode=ro` + `busy_timeout` の接続を使う。 | `storage/db_view.py` |
| 多言語キー整合 | ログ / UI キー追加時は `ja.py` と `en.py` の両方に追加。 | `core/lang/*.py` |
| 容量不足時の段階復旧 | GC (NAS2GP 転送済みファイル削除) → 再起動 の 2 段階を最大 3 ループ、解決しなければ強制停止。DCIM 全体削除は自動処理では撤廃（手動の全削除ボタンのみ、二重確認＋同意必須）。 | `uploader/flow.py` 復旧シーケンス |
| バックアップ待機の二段構え | 無進捗タイムアウト（`MIN_BACKUP_THROUGHPUT_MBPS` によりバッチサイズ比例で下限を延長）と `BACKUP_ABSOLUTE_MAX_SECONDS` (既定 7200 秒) の絶対上限。 | `worker/backup_waiter.py` |
| 停滞リスタートの比例緩和 | 「バックアップ中」が `BACKUP_STALLED_LIMIT` 回連続したら Google Photos を再起動。大容量バッチでは無進捗タイムアウトに比例して自動緩和し、進行中の大容量ファイルを誤って中断しない（`compute_idle_timeout` / `compute_stall_limit`）。 | `worker/backup_waiter.py` |
