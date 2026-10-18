# 運用ガイド (Operation Guide)

最終更新: 2026-10-18

導入・起動・日常運用・メンテナンス・トラブルシューティング・復旧手順を 1 ファイルに集約。

---

## 目次

1. [環境要件](#1-環境要件)
2. [初期セットアップ](#2-初期セットアップ)
3. [起動方法 (Web / CLI)](#3-起動方法-web--cli)
4. [日常運用フロー](#4-日常運用フロー)
5. [月次メンテナンス](#5-月次メンテナンス)
6. [トラブルシューティング FAQ](#6-トラブルシューティング-faq)
7. [復旧手順 (最悪時)](#7-復旧手順-最悪時)

---

## 1. 環境要件

### 1.1 PC 側

| 項目 | 必要 | 推奨 |
|---|---|---|
| OS | Windows 10 (22H2 以降) | Windows 11 (23H2 以降) |
| Python | 3.11 | 3.12 |
| RAM | 4 GB | 8 GB 以上 (大量ファイルのスキャン時) |
| ディスク空き | 20 GB | 100 GB 以上 |
| USB | USB 2.0 以上 | USB 3.2 Gen1 / 端末 純正ケーブル |
| ADB | Android Platform Tools 34+ | 最新版 |
| Web UI 依存 | `fastapi` / `uvicorn` / `jinja2` | 最新版 |

### 1.2 端末 側

| 項目 | 条件 |
|---|---|
| 機種 | Google Pixel 3 以降 (Android 12 以上) |
| 推奨 | Pixel 6 以降 / Android 14 以上 |
| 設定 1 | 開発者オプション ON → USB デバッグ ON → 接続時に「この PC からのデバッグを許可」 |
| 設定 2 | Google Photos 最新版 / 対象アカウントにログイン / バックアップ品質を設定済 |
| 設定 3 | 画面ロック (PIN / パターン) を設定済 (wake / unlock で使用) |
| 設定 4 | (任意・推奨) 充電しながら運用 |

---

## 2. 初期セットアップ

### Step 1. Python + pip の確認

```powershell
python --version    # Python 3.11.x / 3.12.x
pip --version       # pip 24+
```

未導入の場合は https://www.python.org/downloads/windows/ から Python 3.12 x64 を取得し、
「Add python.exe to PATH」を有効にしてインストールします。

### Step 2. ADB のインストール + PATH

1. https://developer.android.com/tools/releases/platform-tools から Windows 版 ZIP を取得
2. `C:\tools\platform-tools\` に展開
3. `C:\tools\platform-tools` を環境変数 PATH に追加
4. 新しい PowerShell で確認:

```powershell
adb version
# -> Android Debug Bridge version 1.0.41 ...
```

### Step 3. 端末 側の設定

1. 設定 → デバイス情報 → ビルド番号を 7 回連打 → 開発者オプションを有効化
2. 設定 → システム → 開発者オプション → USB デバッグ = ON
3. 端末 純正 USB ケーブルで PC に接続
4. 端末 画面の「この PC からのデバッグを許可しますか？」→「常に許可」+ 許可
5. 確認:

```powershell
adb devices
# -> List of devices attached
# -> XXXXXX123456  device      ← unauthorized の場合は手順 4 が未実施
```

### Step 4. 依存パッケージのインストール

```powershell
cd <NAS2GP を配置したフォルダ>\source

# 仮想環境 (推奨)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install --upgrade pip
pip install uvicorn fastapi jinja2
```

### Step 5. 初回 Web UI 起動 + 基本設定

```powershell
python entry_web.py
```

ブラウザで `http://127.0.0.1:8000/` を開き、**設定** ページで以下を設定します。

1. **PHOTO_DIR**: 写真フォルダ (例 `D:\Photos` / NAS なら `\\NAS\Photos`)
2. **バッチ戦略 / バッチサイズ / バッチ最大容量 GB**: 写真中心なら 件数多め・容量小さめ、動画中心なら容量優先
3. **言語**: 日本語 / English (または `source/config/lang/` に追加した外部言語)
4. **保存** をクリック

### Step 6. 少量ファイルでのテスト実行

1. PHOTO_DIR 配下にテスト用の JPG を数枚置く
2. **ホーム → 開始** をクリック
3. 進捗を確認 (工程インジケータが 7 工程を順に進む)
4. **診断** ページで各カードが正常 (緑) であることを確認

---

## 3. 起動方法 (Web / CLI)

いずれも **`source` フォルダ** をカレントディレクトリにして実行します。

```powershell
cd <NAS2GP を配置したフォルダ>\source
```

### Web UI 起動 (通常はこちら)

```powershell
python entry_web.py          # 直接実行 (推奨)
bin\start_web_app.cmd        # .cmd ランチャー経由
```

- 起動時にコンソールへ `http://127.0.0.1:8000/` が表示されます。
- LAN 内のスマホ / 他 PC からは `http://<この PC の IP>:8000/`。
- ポートは `WEB_SERVER_PORT` (既定 8000)。使用中なら 8001〜 へ自動フォールバックします。
- 停止は **Ctrl+C** (ブラウザの SSE 接続が残っていても 3 秒で強制終了します)。

### CLI 起動 (スケジュールタスク等の自動化用)

```powershell
python app.py             # 直接実行 (推奨)
bin\start_app.cmd         # .cmd ランチャー経由
```

`source/config/config_ui.json` の設定を自動使用し、標準出力に各フェーズの進捗とエラーを表示します。

---

## 4. 日常運用フロー

```
[開始時]
 1. PC 起動 → USB ケーブルで 端末 を接続済みか確認
 2. python entry_web.py を起動 → ブラウザで http://127.0.0.1:8000/ を開く
 3. 診断 ページで各カードが正常 (緑) であることを確認
 4. ホーム ページ → 開始
 5. ブラウザを最小化して PC 作業 (エラーはホームのステータスバナーで通知)

[終了時]
 6. コンソール ページで WARN / ERROR が出ていないか確認
 7. 統計 ページで 転送量 / 実効速度 / 件数 の推移 (期間切替: 24時間 / 7日 / 30日) を確認
    (ファイル単位の詳細やフィルタは 管理 ページ)
 8. 「停止」または「今回のバッチで終了」→ コンソールで Ctrl+C → 端末 画面が自動 OFF
```

- **停止**: 実行中の処理を直ちに中断します。
- **今回のバッチで終了**: 現在のバッチを完了させてから安全に停止します。

---

## 5. 月次メンテナンス

推奨: 毎月第 1 日曜日など曜日を固定して実施します。

1. **DB バックアップの確認**: 自動バックアップは `source/config/backups/` に 5 世代保持されます。古い `.bak` は任意で削除してください。
2. **手動 VACUUM**: 管理 ページ → DB フォルダを開く → CLI で `python -m nas2gp.storage.db_maintenance`
3. **容量確認**: `source/database/nas2gp.db` のサイズが大きい場合は VACUUM の頻度を上げてください。
4. **ADB / Platform Tools 更新**: 半年に 1 回程度で十分です。
5. **言語整合性**: 診断 ページで不足キーが増えていないか確認 (増えていれば該当 JSON の `words` へ追記)。

---

## 6. トラブルシューティング FAQ

### 6.1 起動 / ADB 接続

| No | 症状 | 原因 (可能性順) | 解決方法 |
|---|---|---|---|
| 1 | `adb devices` が `unauthorized` | USB デバッグ許可の拒否 / PC 鍵の不一致 | 開発者オプション → USB デバッグ認証を一度取り消す → 再接続 → 再許可 |
| 2 | `adb devices` が `no devices` / `offline` | ケーブル不良 / 充電専用ケーブル / USB 設定 | 純正ケーブルに交換 / USB 設定を「ファイル転送」に / `adb kill-server ; adb start-server` |
| 3 | 起動時に `No module named 'fastapi' / 'uvicorn' / 'jinja2'` | Web UI 依存が未導入 | `.venv` を activate して `pip install uvicorn fastapi jinja2` |
| 4 | 起動時に `No module named 'nas2gp'` | カレントディレクトリが `source` フォルダでない | `cd` で `source` フォルダに移動してから起動 |

### 6.2 SCAN / DB

| No | 症状 | 原因 | 解決方法 |
|---|---|---|---|
| 5 | スキャン時に「PHOTO_DIR が存在しません」 | PHOTO_DIR 不在 / UNC アクセス権なし | ネットワークドライブを再接続 → 設定 ページで PHOTO_DIR を再選択 |
| 6 | 同じ画像が繰り返し転送される | NAS 側でサイズ / 更新日時が変化し重複排除が効かない | 管理 ページ → 進捗リセット (将来版でハッシュ判定を予定) |
| 7 | `database is locked` | 同一 DB へ CLI と Web が同時アクセス | Web UI と CLI を同時起動しない |
| 8 | DB が破損 / SELECT エラー | PC 突然停止 / ディスク不良 | `source/config/backups/` の最新 `.bak` を `source/database/nas2gp.db` へ戻して復元 |

### 6.3 UPLOAD / ADB push

| No | 症状 | 原因 | 解決方法 |
|---|---|---|---|
| 9 | push が FAILED → retry で最終的に成功 | USB 接続が不安定 | 純正 USB 3.2 ケーブルに交換 / PC 背面ポートを使用 |
| 10 | ファイル名の括弧で Media Scanner 登録漏れ | 一部端末の sh エスケープ | バッチ最大容量 GB を小さくし、1 バッチあたりのファイル数を減らす |
| 11 | 残量が閾値を下回り Delete が先走る | 設計どおりの動作 | 大きい動画をまとめて送らない / マージン GB を増やす |
| 12 | push したのに Google Photos に出てこない | Media Scan のタイムラグ | 端末 を再起動 / Google Photos の「今すぐバックアップ」を実行 |

### 6.4 WAIT_BACKUP

| No | 症状 | 原因 | 解決方法 |
|---|---|---|---|
| 13 | `backing_up` から `complete` にならない | ネットワーク不通 / Wi-Fi 未接続 | 端末 の Wi-Fi を接続 (モバイルデータは低速) |
| 14 | バックアップ未完了なのに削除された (誤判定) | スクロール位置が TOP でない / 画面文言マッチ失敗 | 待機条件を厳しくする (`MIN_ACCEPT_ELAPSED_SECONDS` を増やす) / コンソールで受理ルールを確認 |
| 15 | `unknown` が頻発 | ロック画面 / 充電アニメ / Toast の被り | 開発者オプション →「スリープ時 ADB 許可」を ON |
| 16 | 待機が終わらない | Google Photos の一時停止 / アップロード失敗 | アプリを確認し必要なら強制停止して再開 (無進捗タイムアウトと絶対上限で自動脱出します) |

### 6.5 DELETE / FINALIZE

| No | 症状 | 原因 | 解決方法 |
|---|---|---|---|
| 17 | `rm` が Permission denied | 端末 側のセキュリティ | 開発者オプション → USB デバッグ (セキュリティ設定) を ON |
| 18 | 削除後も重複ファイルがある | Google Photos が端末側コピーを再作成 (Google 仕様) | 仕様のため無視可。逼迫時は手動削除 / マージン GB を増やす |
| 19 | 終了後も 端末 画面が ON のまま | signal が殺された / 強制 kill | Web UI の停止ボタンや Ctrl+C で終了する (タスクマネージャーでの強制 kill を避ける) |

### 6.6 ログ / 言語

| No | 症状 | 原因 | 解決方法 |
|---|---|---|---|
| 20 | コンソールページにログが出ない | SSE (`/api/logs/sse`) が未接続 | ブラウザをリロード / サーバーが起動しているか確認 |
| 21 | 重要ログが出ない | ログ種別フィルタが絞られている | フィルタを ALL に戻す |
| 22 | 設定を保存したのに次回起動で消える | `source/config/config_ui.json` の保存権限 / リポジトリフォルダが読み取り専用 | リポジトリフォルダの所有権 / NTFS 権限を確認 |
| 23 | 言語を切り替えても一部が別言語のまま | i18n のキー不足 (診断ページで検出可) | 診断 ページの言語整合性を確認 → 外部 JSON の `words` へ追記 |
| 24 | 他 PC / スマホからアクセスできない | `127.0.0.1` バインド / ファイアウォール | 設定で `WEB_SERVER_HOST = 0.0.0.0` に変更 → 再起動 / ポート 8000 を許可 |
| 25 | リセットしても元に戻らない (と感じる) | OLD rename 方式のため実体は残る (正しい仕様) | 管理 ページで対象フォルダを開き `_OLD_` 付きファイルから手動で戻す |

### 6.7 性能

| No | 症状 | 原因 | 解決方法 |
|---|---|---|---|
| 26 | スキャンが遅い | 大量ファイルの再帰走査 / NAS レイテンシ | SMB 3.1.1 を有効化 / 夜間実行 / VACUUM 直後に実行 |
| 27 | 平均速度が不自然 | backup 待ち時間を速度計算に含んでいた | 現行版では転送時間ベースで算出。DB を最新化すると補正されます |
| 28 | Media Scan が多くて 端末 が重い | 1 バッチあたりのファイル数が多い | バッチサイズを減らし、容量優先に切り替える |

### 6.8 その他

| No | 症状 | 原因 | 解決方法 |
|---|---|---|---|
| 29 | DB が巨大化した | 長期運用で VACUUM 未実施 | 管理 ページ → 工場出荷時リセット (OLD バックアップ作成済みで安全) |
| 30 | 日本語環境でコンソールが文字化け | コードページ 932 | システムロケールで「Unicode UTF-8 を使用する」を ON → PC 再起動 |

---

## 7. 復旧手順 (最悪時)

### 事象 A: DB が破損し SELECT できない

1. 落ち着く: NAS の元ファイルは一切触っていません。最悪でも全件再送で済みます。
2. `source/config/backups/` を開き、直近の正常なバックアップ (`.bak.YYYYMMDD-HHMMSS`) を選ぶ。
3. アプリを完全終了する。
4. `source/database/nas2gp.db` をどけて、手順 2 のコピーを `source/database/nas2gp.db` にリネームする。
5. 再起動。SKIP 済み分はスキップされ、未送信の `PENDING` から再開します。

### 事象 B: 誤って Factory Reset を実行した

1. 落ち着く: `rm` せず OLD rename 方式のため復元可能です。
2. 管理 ページ → DB フォルダ / 設定フォルダを開く。
3. `_OLD_YYYYMMDD_HHMMSS_nas2gp.db` → prefix を外して `nas2gp.db` に戻す。
4. 同様に `_OLD_*_config_ui.json` の prefix を外す。
5. 再起動すると元の状態に復帰します。

### 事象 C: 同期がフリーズ (長時間まったく進まない)

1. Web UI の **停止** ボタンを押す (Worker へ停止シグナル送信)。
2. 30 秒経っても止まらない場合は、`entry_web.py` のコンソールで Ctrl+C を押す。
3. それでも固まる場合はタスクマネージャーから `python.exe` を 1 つだけ終了する。
4. 再実行前に必ず `adb devices` で状態を確認し、`offline` なら `adb reconnect offline` → `adb kill-server ; adb start-server`。
5. 再起動すると途中から再開できます (`PENDING` 判定から再実行される安全設計)。
