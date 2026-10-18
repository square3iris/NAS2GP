# 設定リファレンス (Settings)

最終更新: 2026-10-18

設定は Web UI の **設定** ページ (スキーマ駆動) から編集し、`source/config/config_ui.json` に保存されます。
設定ページの表示項目はバックエンドの `_settings_ui_schema()` が定義しており、バッチ工程 (①〜⑦) の流れに
沿った **9 グループ** の順に並びます。各グループには「どの工程の設定か」「変えるとどう動くか」の説明を
画面上に表示するため、設定の意味と影響が分かりやすい構成になっています。

---

## 1. ファイル配置

```
NAS2GP/ (リポジトリルート)
├── README.md
├── LICENSE
├── .gitignore
├── .gitattributes         ← 改行コードポリシー (v1.9.0)
├── requirements.txt       ← 依存パッケージ定義 (v1.9.0)
├── docs/                  ← ドキュメント (ja / en)
├── archive/
│   └── NAS2GP_v1.9.0.zip  ← リリース版スナップショット
└── source/                ← 実行エントリ・パッケージ・実行時データを集約
    ├── app.py / entry_web.py / db_admin.py   ← 実行エントリ
    ├── nas2gp/            ← メインパッケージ
    ├── bin/               ← 起動用 .cmd / .sh / .command ランチャー
    ├── scripts/           ← 補助スクリプト
    ├── config/
    │   ├── config_ui.json   ← 設定の永続化データ (初回 / 保存時に自動生成)
    │   ├── consent.json     ← 利用同意の記録 (同意日時 / バージョン。Git 管理外)
    │   ├── backups/         ← DB 自動バックアップ (.bak.YYYYMMDD-HHMMSS, 5 世代)
    │   ├── cache/           ← 一時ファイル
    │   └── lang/            ← i18n 外部言語 JSON (部分 / 全部カスタム可)
    ├── database/
    │   └── nas2gp.db    ← SQLite3 DB (実運用の格納先)
    └── logs/                ← 実行ログ
```

---

## 2. config_ui.json (スキーマバージョン 10)

初回起動時 / 旧設定ファイル読込時に不足キーをデフォルト値で自動補完します
(`config_loader._merge_missing_schema`)。旧バージョンのファイルでも KeyError は起きません。

```json
{
  "CONFIG_SCHEMA_VERSION": 10,
  "LANGUAGE": "auto",
  "PHOTO_DIR": "D:/Photos",
  "DEVICE_WORK_DIR": "/sdcard/DCIM/NAS2GP",
  "BATCH_STRATEGY": "hybrid",
  "MAX_FILES_PER_BATCH": 30,
  "MAX_BYTES_PER_BATCH": 2147483648,
  "DEVICE_STORAGE_MARGIN_GB": 4.0,
  "ADB_RECOVER_MIN_INTERVAL_SEC": 45.0,
  "ADB_FAILURE_POLICY": "pause_dialog",
  "ADB_RECOVERY_WAIT_SEC": 300,
  "BATTERY_LOW_THRESHOLD_PCT": 20,
  "BATTERY_WAIT_MAX_SEC": 3600,
  "BATTERY_CHECK_INTERVAL_SEC": 600,
  "DISCONNECT_LOG_ENABLED": true,
  "DISCONNECT_HEARTBEAT_INTERVAL_SEC": 600,
  "BACKUP_TIMEOUT": 600,
  "BACKUP_ABSOLUTE_MAX_SECONDS": 7200,
  "MIN_BACKUP_THROUGHPUT_MBPS": 2.0,
  "BACKUP_CHECK_INTERVAL": 8,
  "MIN_ACCEPT_ELAPSED_SECONDS": 15,
  "BACKUP_STALLED_LIMIT": 15,
  "RESTART_PHOTOS_AFTER_PUSH": false,
  "LOG_VERBOSITY": "standard",
  "ENABLE_NIGHT_LIGHT_MODE": true,
  "SCREEN_BRIGHTNESS_NIGHT": 0,
  "WEB_SERVER_HOST": "0.0.0.0",
  "WEB_SERVER_PORT": 8000
}
```

> **注意 (DB パス)**: DB パスは設定キーでは変更できません。`settings.DB_FILE`
> (`source/database/nas2gp.db`) を唯一の正とし、書込み (`Database`)・診断・リセットは
> すべて `settings.resolve_db_file()` 経由で同じ実ファイルを参照します。

---

## 3. 設定グループと各項目

設定ページでは、各グループ名の下に「どの工程の設定か」、各項目の下に「変えるとどう動くか」の
説明が常時表示されます。以下はグループを工程順に並べた対応表です。

### 3.1 基本設定 (basic) — 全工程に共通

| キー | 型 | 既定 | 内容 |
|---|---|---|---|
| `LANGUAGE` | select | `auto` | UI / ログの表示言語。`ja` / `en` / `auto` (OS 追従) / `source/config/lang/*` の外部言語 |
| `PHOTO_DIR` | text | `D:/Photos` | 転送元ルート (NAS / SMB / ローカル可)。自分の環境の写真フォルダに変更してください |
| `DEVICE_WORK_DIR` | text | `/sdcard/DCIM/NAS2GP` | 端末側の作業フォルダ (転送先 兼 削除対象)。**DCIM 配下のみ**許可。DCIM 配下は Google Photos の自動バックアップ対象。端末ページからも変更可 |

### 3.2 ① 容量確保 と バッチ分割 (CAPACITY)

工程①『容量確保』と、一度に転送する単位 (バッチ) の決め方です。ここで決めた大きさごとに
転送〜バックアップ〜削除を繰り返します。

| キー | 型 | 既定 | 内容 |
|---|---|---|---|
| `BATCH_STRATEGY` | select | `hybrid` | バッチを区切る基準: `count` (ファイル数) / `bytes` (容量) / `hybrid` (両方) |
| `MAX_FILES_PER_BATCH` | int | `30` | 1 バッチの最大ファイル数 |
| `MAX_BYTES_PER_BATCH` | gb | `2` GB | 1 バッチの最大容量 (画面は GB 単位で編集) |
| `DEVICE_STORAGE_MARGIN_GB` | gb | `4.0` GB | 端末 に残す最小空き容量マージン |

**バッチを区切る基準の意味**
- `count`: ファイル数 (`MAX_FILES_PER_BATCH`) のみで区切る
- `bytes`: 合計容量 (`MAX_BYTES_PER_BATCH`) のみで区切る
- `hybrid`: どちらか先に達した方で区切る (既定)

### 3.3 ③ 転送 (UPLOAD): ADB 通信

工程③『転送』で使う ADB 通信の設定です。1 回の通信の待ち時間や、失敗時の再試行・
切断からの復旧方法を決めます。

| キー | 型 | 既定 | 内容 |
|---|---|---|---|
| `ADB_FAILURE_POLICY` | select | `pause_dialog` | ADB 障害時の挙動: `pause_dialog` (一時停止) / `continue` (警告を出してそのまま続行) |
| `ADB_RECOVER_MIN_INTERVAL_SEC` | float | `45.0` | 自動復旧 (recover) の最小実行間隔 (秒) |
| `ADB_RECOVERY_WAIT_SEC` | int | `300` | 切断検知後に「復旧不能」と判定するまで再接続を粘る上限 (秒)。超過時は失敗分を PENDING に戻して安全停止 |

### 3.4 ③ 転送 (UPLOAD): 電池・切断の安全ゲート

工程③『転送』中の電池切れ対策です。残量が低いと新規転送を止めて充電を待ち、
回復しないまま上限を超えると安全に停止します。

| キー | 型 | 既定 | 内容 |
|---|---|---|---|
| `BATTERY_LOW_THRESHOLD_PCT` | int | `20` | 低残量とみなす閾値 (%)。残量がこれ未満かつ非充電なら転送を一時停止 |
| `BATTERY_WAIT_MAX_SEC` | int | `3600` | 低残量で待機する上限 (秒)。超過時は安全停止 |
| `BATTERY_CHECK_INTERVAL_SEC` | int | `600` | 転送中に残量を確認する間隔 (秒)。各バッチ開始前は必ず確認 |

> 低残量を検知すると転送を一時停止し、充電または残量回復を検知すると自動で再開します。
> 待機が `BATTERY_WAIT_MAX_SEC` を超えた場合は、そのバッチの失敗分を `PENDING` に戻して安全停止します。

### 3.5 ⑥ Google Photos バックアップ待機 (WAIT_BACKUP)

工程⑥『Google Photos バックアップ待機』の設定です。端末 に転送した写真が Google フォトの
クラウドへ上がり終えるのを、どう待つかを決めます。

| キー | 型 | 既定 | 内容 |
|---|---|---|---|
| `BACKUP_TIMEOUT` | int | `600` | 無進捗とみなすまでの待機時間 (秒) |
| `BACKUP_ABSOLUTE_MAX_SECONDS` | int | `7200` | バックアップ待機の絶対上限 (秒)。大容量ファイル向けの最終上限 |
| `MIN_BACKUP_THROUGHPUT_MBPS` | float | `2.0` | 無進捗タイムアウトをバッチサイズから見積もる際の「保守的な想定速度」(MB/s)。サイズ ÷ この値 を `BACKUP_TIMEOUT` の下限として使う (`0` で無効)。残り枚数が減らない単一の大容量ファイル (例: 4GB の動画) を誤って打ち切らない |
| `BACKUP_CHECK_INTERVAL` | int | `8` | 画面文言 (UIAutomator dump) を確認する間隔 (秒) |
| `MIN_ACCEPT_ELAPSED_SECONDS` | int | `15` | 完了受理に必要な最低経過時間 (秒) |
| `BACKUP_STALLED_LIMIT` | int | `15` | 停滞リスタートの判定回数。「バックアップ中」が監視ループでこの回数だけ連続したら Google Photos を再起動して立て直す (実時間は `BACKUP_CHECK_INTERVAL` に依存。例: 15回 × 8秒 ≒ 120秒)。大容量ファイルでは無進捗タイムアウトに比例して自動で緩和される (`0` で無効) |
| `RESTART_PHOTOS_AFTER_PUSH` | bool | `false` | バックアップ失敗時のみ Google フォトを再起動する |

### 3.6 端末の画面 (明るさ / 常夜灯)

同期中に 端末 の画面を暗くして、眩しさや画面の焼き付きを防ぐ設定です。
同期の実行中は常夜灯モードになり、運転終了時に元の輝度へ戻します。
画面輝度はスクリーンショット取得 (OCR) やタップ操作とは独立しているため、
輝度 `0` でもバックアップ判定や操作は通常どおり動作します。

| キー | 型 | 既定 | 内容 |
|---|---|---|---|
| `ENABLE_NIGHT_LIGHT_MODE` | bool | `true` | 常夜灯モードを使う |
| `SCREEN_BRIGHTNESS_NIGHT` | int | `0` | 同期運転中の画面輝度 (0–255)。運転終了時に元の輝度へ自動復元 |

### 3.7 解析ログ (切断解析 と 統計データ)

長時間の転送中に端末の状態 (残量 / 温度 / 空き容量) を定期記録するログの設定です。
この記録は切断原因の解析 (`source/logs/disconnect_*.log` のスナップショット) に使われるほか、
統計ページの時系列グラフ (電池 / 温度 / 空き容量) の元データ (`source/logs/health.jsonl`) にも
なります。原因調査やデータ収集のときだけ ON にすれば十分です。

| キー | 型 | 既定 | 内容 |
|---|---|---|---|
| `DISCONNECT_LOG_ENABLED` | bool | `true` | 解析ログを記録する (切断解析 + 統計データ)。OFF にすると記録を停止し、統計の時系列も更新されなくなる |
| `DISCONNECT_HEARTBEAT_INTERVAL_SEC` | int | `600` | 状態を記録する間隔 (秒)。短くすると細かく残るが ADB 呼び出しが増える。上のチェックが ON のときだけ有効 |

### 3.8 ログ表示

画面 (コンソール) に表示するログの量を決めます。普段は「標準」のままで問題ありません。

| キー | 型 | 既定 | 内容 |
|---|---|---|---|
| `LOG_VERBOSITY` | select | `standard` | ログ量: `full` / `standard` / `minimal` |

### 3.9 Web UI

この Web 画面自体の公開設定です。変更後はアプリ (`entry_web.py`) の再起動が必要です。

| キー | 型 | 既定 | 内容 |
|---|---|---|---|
| `WEB_SERVER_HOST` | text | `0.0.0.0` | バインド先。`0.0.0.0` で LAN 全体に公開。ヘッダは実 LAN IP を自動表示 |
| `WEB_SERVER_PORT` | int | `8000` | 待受ポート。使用中なら 8001〜 へ自動フォールバック |

> 再起動を伴うキー (HOST / PORT など) は次回起動時から有効になります。
> 変更は保存時にインライン通知で案内されます。

**設定ページ最下部の「利用への同意」カード**: 同意状態 (同意済み / 未同意) と同意日時を表示し、
赤い「同意をリセット」ボタンで同意記録 (`config/consent.json`) を削除できます。リセットすると、
次にページを開いたときに同意の確認 (全画面オーバーレイ) が再表示されます。

---

## 4. i18n 外部言語 JSON の追加手順

既存の日本語 / 英語以外の言語 (例: ドイツ語 `de`) を追加し、文言をカスタムする場合の手順です。

### Step 1. `source/config/lang/de.json` を作成

```json
{
  "label": "Deutsch (DE)",
  "words": {
    "web.btn.start": "Starten",
    "web.btn.stop": "Anhalten",
    "web.nav.settings": "Einstellungen"
  }
}
```

> **部分翻訳 OK**: `words` には任意のキーだけ書けば十分です。書かなかったキーは
> 組込 JA (最下層) が残るため、UI が壊れることはありません。

### Step 2. 検出と選択

起動時 (`i18n.list_available_languages()`) に自動検出され、設定ページの言語ドロップダウンに
**Deutsch (DE)** が現れます。選択して保存するとページが自動リロードされ、UI 全体が再翻訳されます。

### Step 3. (任意) 整合性確認

診断 ページの言語整合性カードで不足キー / 重複 / 外部 JSON 情報を確認できます。
CLI でも確認できます (`source` フォルダをカレントディレクトリにして実行)。

```powershell
python -c "from nas2gp.core import i18n; print(i18n.diagnose_wordbank('de'))"
```

---

## 5. 管理操作

Web UI の **管理** ページは、次の 2 つで構成されます。

1. **ファイル一覧 / 進捗**: 転送対象ファイルの状態 (転送 / GP バックアップ / 削除) を列ごとに
   絞り込み・検索・並べ替えできます (旧「統計」ページから移設)。
2. **操作ボタン**: 3 つの OPEN ボタンと 3 つの RESET ボタン。

```
[DB フォルダを開く]  [設定フォルダを開く]  [ログフォルダを開く]
[進捗リセット]  [設定リセット]  [工場出荷時リセット]
```

> 転送量・速度・件数の推移などの「分析」は **統計** ページに集約されています。統計ページは
> 集計グラフ専用で、ファイル一覧は管理ページに移動しました。

### 5.1 OPEN ボタン

各 OS のファイルマネージャーで対象フォルダを開くだけです (何も削除しません)。

### 5.2 RESET ボタン (安全 OLD rename 方式)

`rm` は使用しません。`_OLD_YYYYMMDD_HHMMSS_` prefix を付けてリネームするため、誤操作しても復元できます。

| ボタン | 操作 | 復元方法 |
|---|---|---|
| 進捗リセット | DB 本体 + WAL/SHM ジャーナル + 旧バックアップ DB を OLD 退避 | ファイル名から prefix を外す |
| 設定リセット | `source/config/config_ui.json` を OLD 退避 | 同上 |
| 工場出荷時リセット | DB フォルダ内 + 設定 + 過去ログ (`*.log`) を OLD 退避 | 同上 |

> 工場出荷時リセットなど重要操作は、誤操作防止のため 2〜3 段階のインライン確認を挟みます。

### 5.3 DB メンテナンス CLI

以下は `source` フォルダをカレントディレクトリにして実行します。

```powershell
# 整合性検査
python -m nas2gp.storage.db_debug

# VACUUM + 5 世代バックアップ
python -m nas2gp.storage.db_maintenance

# ADB エラー行のみ PENDING に強制リセット
python db_admin.py reset-failed

# 転送 / Google Photos / 削除 の状態リセット
python db_admin.py reset-transfer
python db_admin.py reset-google
python db_admin.py reset-delete

# 失敗行の再試行 / 集計
python db_admin.py retry-failed
python db_admin.py summary
python db_admin.py failed

# ヘルプ
python db_admin.py --help
```

---

## 6. チューニングガイド

### 6.1 写真中心のライブラリ (多ファイル / 低容量)

`MAX_FILES_PER_BATCH` を多め (例 50)・`MAX_BYTES_PER_BATCH` を小さめにするか、
`BATCH_STRATEGY = count` を使います。1 バッチを早く消化でき、待機回数を減らせます。

### 6.2 動画中心のライブラリ (少ファイル / 高容量)

`BATCH_STRATEGY = bytes` または `hybrid` とし、`MAX_BYTES_PER_BATCH` で区切ります。
動画 1 本ごとに転送 → バックアップ → 削除が回り、端末 の空き容量枯渇を防げます。

### 6.3 ADB 通信が不安定

`ADB_FAILURE_POLICY` は `pause_dialog` のままにして原因を確認します。
切断が頻発する場合は `ADB_RECOVERY_WAIT_SEC` を長め (例 600 = 10 分) にすると自動復旧の成功率が上がります。

### 6.4 バックアップ完了判定の調整

診断は **コンソール ページ** で受理の状況をリアルタイムに確認しながら行うのが最短です。

- 判定が遅い: `MIN_ACCEPT_ELAPSED_SECONDS` を短くする
- 誤判定が多い (早すぎる): `MIN_ACCEPT_ELAPSED_SECONDS` を増やす / `BACKUP_ABSOLUTE_MAX_SECONDS` を調整する
- 大容量ファイル (数 GB の動画 1 本など) で打ち切られる: `MIN_BACKUP_THROUGHPUT_MBPS` を下げる (タイムアウトが延長される) / `BACKUP_TIMEOUT` を増やす
- 大容量ファイルで Google Photos が再起動を繰り返す (停滞リスタートの誤発動): `BACKUP_STALLED_LIMIT` を増やす。無進捗タイムアウトが延長された場合は自動で比例緩和されるため、通常は既定のままで問題ない

### 6.5 長時間運転 (スマホの電池切れ対策)

夜間に長時間運転すると、端末の電池切れで転送が止まる場合があります。次の設定で自動復旧・安全停止します。

- `BATTERY_LOW_THRESHOLD_PCT`: 低残量とみなす閾値 (既定 20%)。残量がこれ未満かつ非充電なら待機
- `BATTERY_WAIT_MAX_SEC`: 待機の上限 (既定 3600 = 60 分)。超過で安全停止
- `BATTERY_CHECK_INTERVAL_SEC`: 転送中の残量確認間隔 (既定 600 = 10 分)
- `ADB_RECOVERY_WAIT_SEC`: 切断後の復旧待ち上限 (既定 300 = 5 分)

> **注意**: USB から電源が供給されていれば通常は電池切れを防げます。端末が完全に電源 OFF に
> なった場合は ADB では復旧できないため、物理的な充電と電源 ON が必要です。本ソフトが
> 端末の電源を切る処理を行うことはありません (画面 OFF と再起動のみ)。

### 6.6 切断原因の解析 (解析ログ)

長時間評価で切断が起きたときの原因を後から切り分けるには、`DISCONNECT_LOG_ENABLED` を
有効 (既定) にしておきます。`source/logs/health.jsonl` に残量推移が残るため、切断が「緩やかな
電池切れ (残量が段階的に低下)」なのか「突発的切断 (残量は十分) = ケーブル / ADB 異常」なのかを
傾向で判別できます。`DISCONNECT_HEARTBEAT_INTERVAL_SEC` を短くすると推移が細かく残りますが、
その分だけ記録 (adb 呼び出し) の頻度が上がります。

> この解析ログは、統計ページの時系列グラフ (電池 / 温度 / 空き容量) の元データも兼ねています。
> OFF にすると統計の時系列も更新されなくなるため、解析・統計のどちらかを使うときは ON にしてください。
