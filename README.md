# NAS2GP
Pixelを使った無制限Googleフォトアップロードツールです。
NASや外付けHDDに保存した写真・動画を、ADB経由で初代Pixel（または無制限対応端末）へ自動転送し、
Google Photosのバックアップ完了を確認してから端末側のファイルを削除します。
「pixel 無制限アップロード ツール」「NASからPixelへ転送」「Googleフォト無制限 活用」などで探している人向け。

A tool for unlimited Google Photos uploads using a Pixel device.

It automatically transfers photos and videos stored on a NAS or external HDD to a first-generation Pixel (or another device eligible for unlimited uploads) via ADB, then deletes the files from the Pixel only after confirming that Google Photos backup has been completed successfully.

Designed for users searching for:

Unlimited Google Photos upload tool for Pixel
Transfer photos from NAS to Pixel
How to take advantage of unlimited Google Photos storage


https://qiita.com/square3iris/items/a5b55a24f4d0da7e0cb2
---

## 日本語

### 概要

- NAS 上の写真フォルダを走査し、未転送のファイルだけを端末へまとめて転送（バッチ転送）します。
- 転送先は端末側の「作業フォルダ」（既定 `/sdcard/DCIM/NAS2GP`）。DCIM 配下のため Google Photos の自動バックアップ対象になります。
- Google Photos のバックアップ完了を待ってから、**NAS2GP が転送したファイルだけ**を端末から削除します（同期中の自動削除はファイル単位で、DCIM 全体などの一括削除は行いません）。端末ページには、作業フォルダの中身をまとめて削除する手動ボタンがあります（二重の確認と同意が必要）。
- 転送状況は SQLite データベースで管理し、Web UI から進捗・統計・ファイル一覧を確認できます。

### 開発を支援

NAS2GP は無料で公開しているフリーツールです。気に入っていただけたら、開発の継続や動作検証用の機材のためにご支援いただけると励みになります。

[![Support via PayPal](https://img.shields.io/badge/PayPal-%E6%94%AF%E6%8F%B4%E3%81%99%E3%82%8B-0070ba?logo=paypal&logoColor=white)](https://paypal.me/square3iris)

- PayPal: <https://paypal.me/square3iris>

支援は任意です。支援の有無で機能が制限されることはありません。

### 私の使い方（活用例）

Google Photos の無制限アップロードを活かすための、作者自身の使い方です。

- Google Photos の無制限アップロードに対応した端末（初代 Pixel など）へ、NAS の写真・動画をまとめて転送してバックアップしています。
- そのために、初代 Pixel を **AliExpress で中古購入**しました。無制限アップロード対応端末はバックアップ専用機として割り切って使えます。
- **メイン端末とは別に用意することを推奨します。** 本ツールは Google Photos のバックアップ完了まで端末を長時間占有するため、メイン端末を同時に使いたいときに支障が出ます。
- 端末は充電しながらの長時間運用を前提としています（電池切れ対策として、本ツールに電池残量監視と自動復旧を実装しています）。

Google Photos の無制限アップロードの条件は Google のポリシー変更により変わることがあります。最新の条件はご自身でご確認ください。

### 主な機能

| 機能 | 内容 |
|---|---|
| バッチ転送 | 件数 / 容量 / ハイブリッドの戦略で分割転送 |
| Google Photos バックアップ待機 | 端末 UI を監視し、完了を確認してから次へ進む |
| 安全な削除 | 同期中の自動削除は、DB で管理している「転送済みファイル」のみを1件ずつ。加えて端末ページに、作業フォルダの中身をまとめて削除する手動ボタン（同意必須） |
| 作業フォルダの動的選択 | 端末ページから DCIM 配下のフォルダを選んで変更可能 |
| 障害時の自動復旧 | 電池残量低下・ADB 切断を検知し、段階的に復旧。復旧不能なら安全に停止 |
| 切断解析ログ | 一定間隔で電池 / 温度 / 空き容量を記録し、切断時のスナップショットを保存 |
| Web UI | 進捗 / 統計（時系列グラフ） / 管理（ファイル一覧） / 設定 / 診断 |
| 多言語 | 日本語 / English ＋ JSON を1つ置くだけで任意の言語を追加 |
| 利用同意（初回のみ） | 初回起動時に免責事項への同意を Web UI で確認（2 項目のチェック）。一度同意すると記憶され、次回以降は表示されない |

### 多言語対応（言語を追加できます）

日本語 / English に加えて、**JSON ファイルを1つ置くだけで好きな言語を追加**できます。Web UI もログも切り替わります。

1. `source/config/lang/de.json` のような JSON を作成します（**ファイル名がそのまま言語コード**になります）。

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

2. 起動時に自動検出され、設定ページの言語ドロップダウンに **Deutsch (DE)** が現れます。選択して保存すると UI 全体が切り替わります。
3. **部分翻訳で OK**。書かなかったキーは組み込みの English / 日本語がそのまま使われるため、UI が崩れることはありません。
4. 不足しているキーは 診断 ページの「言語整合性」で確認できます。

### 動作環境

| 項目 | 必要条件 | 推奨 |
|---|---|---|
| OS | Windows 10 (22H2 以降) / macOS / Linux | Windows 11 (23H2 以降) |
| OS (macOS / Linux) | ベストエフォート（実機 macOS では未検証） | - |
| Python | 3.11 | 3.12 |
| ADB | Android Platform Tools 34 以降 | 最新版 |
| Web UI 依存 | `fastapi` / `uvicorn` / `jinja2` | 最新版 |
| 端末 | Android 12 以上（Google Pixel 3 以降で動作確認） | Android 14 以上 |
| 端末設定 | USB デバッグ ON / 画面ロック設定済 / Google Photos ログイン済 | 充電しながらの運用 |

### インストール

**配布版アプリ（推奨・Windows）**

[GitHub Releases](https://github.com/square3iris/NAS2GP/releases) から `NAS2GP_v<version>_win64.zip` をダウンロードし、任意のフォルダへ展開して `NAS2GP.exe` をダブルクリックします。**Python のインストールは不要**です。初回起動時に `config/` `database/` `logs/` が exe と同じフォルダに自動生成されます。

**ソースから実行する場合**

```powershell
python --version          # 3.11 / 3.12
pip install -r requirements.txt
```

いずれの場合も ADB（Android Platform Tools）を導入し、`adb` に PATH を通してください。**ADB は同梱されていません**（未検出時はアプリ起動時に導入手順を表示します）。

### 起動方法

**配布版アプリ（Windows）**

`NAS2GP.exe` をダブルクリックすると、ローカル Web サーバが起動し、既定ブラウザが **アプリモード**（Edge / Chrome の `--app`）で開きます。ウィンドウを閉じるのではなく、**コンソールで Ctrl+C** を押すと停止します。サーバのみ起動してブラウザを開かない場合は、環境変数 `NAS2GP_NO_BROWSER=1` を設定してください。

**ソースから実行する場合**

`source` フォルダをカレントディレクトリにして実行します。

```powershell
cd source
python entry_web.py           # ブラウザで http://127.0.0.1:8000/ を開く
```

または `source\bin\start_web_app.cmd` をダブルクリックします。

**macOS / Linux**（ベストエフォート・実機 macOS 未検証）

```bash
pip install -r requirements.txt   # リポジトリルートで実行
cd source
python3 entry_web.py
```

または `source/bin/start_web_app.sh` を実行、macOS では `source/bin/start_web_app.command` をダブルクリックします。

同一 LAN 内のスマートフォンからも `http://<PCのIP>:8000/` で閲覧できます。

### ディレクトリ構成

```
NAS2GP/
├ README.md
├ LICENSE
├ .gitattributes    改行コードの固定 (v1.9.0)
├ requirements.txt  依存パッケージ定義 (v1.9.0)
├ docs/        ja / en のドキュメント
├ archive/     リリース版のスナップショット (windows/ mac/ linux/ ごとに最新 1 つ)
├ packaging/   配布アプリの PyInstaller spec (NAS2GP.spec)
└ source/      実行エントリ・本体パッケージ・実行時データ
    ├ launcher.py        配布アプリの起動エントリ (Web サーバ + ブラウザ起動)
    ├ entry_web.py / app.py / db_admin.py
    ├ nas2gp/   本体パッケージ
    ├ scripts/  ビルドスクリプト (build_app.py / build_release.py)
    ├ bin/      起動用 .cmd / .sh / .command
    ├ config/   設定ファイル + config/lang/ 外部言語 JSON（個人環境。Git 管理外）
    ├ database/ 転送履歴データベース（Git 管理外）
    └ logs/     実行ログ（Git 管理外）
```

### 配布アプリのビルド（開発者向け）

```powershell
pip install -r requirements.txt      # pyinstaller を含む
python source/scripts/build_app.py   # -> archive/<os>/NAS2GP_v<version>_<os>.zip (例: windows/NAS2GP_v2.0.0_win64.zip)
```

`packaging/NAS2GP.spec` を使って onedir 形式でビルドし、ZIP にまとめます。ZIP を GitHub Releases に添付して配布します。Linux もベストエフォートでビルドできます（未検証）。

macOS 版は PyInstaller がクロスコンパイル非対応のため Windows からは生成できません。`.github/workflows/build-macos.yml` が GitHub Actions の macOS ランナー上で `archive/mac/` に arm64 / x86_64 の ZIP を作成します（Actions から手動実行、または `v*` タグの push で自動実行し Release に添付）。

### 注意事項（必ずお読みください）

> [!CAUTION]
> **操作を間違えると、端末側の写真フォルダからデータが削除されます。**
> 必ずバックアップを取得してから実行してください。ご利用は**自己責任**でお願いします。
> 初回起動時は Web UI で免責事項への同意（2 項目のチェック）が求められます。

- 端末の個人データを扱います。**初回はテスト用の写真で動作を確認**してから本番運用してください。
- `database/` と `logs/`、`config/config_ui.json` には個人情報（ファイルパス・履歴）が含まれるため、Git にはコミットされません。
- 同意の状態（同意済み / 未同意）と同意日時は **設定** ページの「利用への同意」で確認でき、そこで同意をリセット（確認をもう一度表示）できます。
- 本ソフトは現状のまま提供されます。重要なデータのバックアップは別途取得してください。

### 既知の制限・サポートについて

- 作者のスキル上の制約により、**サポートはベストエフォート（可能な範囲）** となります。すべての環境での動作を保証するものではありません。
- 動作確認は Windows と Android の限られた実機・環境で行っています。**macOS / Linux 対応はベストエフォート**であり、実機の macOS では未検証です。他社端末での動作も未検証です。
- SMB / NAS のセッション維持処理は Windows 専用のため、macOS / Linux ではスキップされます。
- Google Photos の「バックアップ完了」判定は端末の画面表示（UI ダンプ）に依存しているため、Google Photos アプリの仕様変更により判定できなくなる可能性があります。
- 長時間運用時の切断・電池切れなどへの自動復旧を実装していますが、完全ではありません。復旧できない場合は安全に停止する挙動をとります。
- 既知の不具合は GitHub のリポジトリ内の `docs/ja/CHANGELOG.md` および `docs/ja/ROADMAP_AND_TODO.md` に随時記載します（配布ZIPには含まれません）。

### ライセンス

MIT License（`LICENSE` を参照）。

---

## English

### Overview

- Scans a photo library on your NAS and transfers only the files that have not been sent yet, in batches.
- The destination is a "working folder" on the device (default `/sdcard/DCIM/NAS2GP`). Being under DCIM, it is eligible for Google Photos auto backup.
- After Google Photos finishes backing the files up, **only the files transferred by NAS2GP** are deleted from the device (automatic deletion during sync works file by file; there is no automatic bulk delete of all of DCIM). The device page also has a manual button that clears the whole working folder (double confirmation and consent required).
- Transfer state is tracked in a SQLite database. The Web UI shows progress, statistics and the file list.

### Support development

NAS2GP is free software. If you find it useful, your support helps cover continued development and test devices for verification.

[![Support via PayPal](https://img.shields.io/badge/PayPal-Support-0070ba?logo=paypal&logoColor=white)](https://paypal.me/square3iris)

- PayPal: <https://paypal.me/square3iris>

Donations are optional and never limit any features.

### My use case

This is how the author actually uses the tool, to make the most of Google Photos unlimited storage.

- Photos and videos from the NAS are transferred in bulk to a device that has the Google Photos unlimited upload benefit (such as the original Pixel) so they get backed up.
- For that purpose I bought a **used original Pixel on AliExpress**. A device with the unlimited upload benefit can be dedicated to backup duty.
- **Using a separate device from your main phone is recommended.** This tool occupies the device for a long time until the Google Photos backup completes, so it gets in the way when you want to use your main phone at the same time.
- The device is expected to run for long hours while charging (the tool includes battery monitoring and automatic recovery for battery depletion).

The conditions for Google Photos unlimited upload may change with Google's policy. Please check the current terms yourself.

### Main features

| Feature | Description |
|---|---|
| Batch transfer | Split by count / size / hybrid strategy |
| Google Photos backup wait | Monitors the device UI and proceeds only after backup completes |
| Safe deletion | Automatic deletion during sync removes only the "already transferred" files tracked in the DB, one at a time. The device page also offers a manual button to clear the whole working folder (consent required) |
| Dynamic working folder | Change it from the device page by browsing folders under DCIM |
| Automatic recovery | Detects low battery / ADB disconnects and recovers step by step; stops safely if it cannot |
| Disconnect analysis log | Periodically records battery / temperature / free space and snapshots the state on a disconnect |
| Web UI | Progress / statistics (time-series charts) / management (file list) / settings / diagnostics |
| Languages | Japanese / English, plus any language you add with a single JSON file |
| Usage consent (first run only) | On first launch, the Web UI asks you to accept the disclaimer (two checkboxes). Once accepted it is remembered and never shown again |

### Languages (you can add your own)

Beyond Japanese / English, you can **add any language by dropping in a single JSON file**. It switches both the Web UI and the logs.

1. Create a JSON such as `source/config/lang/de.json` (the **file name is the language code**).

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

2. It is auto-detected at startup and **Deutsch (DE)** appears in the Settings language dropdown. Selecting and saving switches the whole UI.
3. **Partial translation is fine.** Keys you do not provide fall back to the built-in English / Japanese, so the UI never breaks.
4. Missing keys are listed on the Diagnostics page ("language integrity").

### Requirements

| Item | Required | Recommended |
|---|---|---|
| OS | Windows 10 (22H2+) / macOS / Linux | Windows 11 (23H2+) |
| OS (macOS / Linux) | best-effort (unverified on real macOS hardware) | - |
| Python | 3.11 | 3.12 |
| ADB | Android Platform Tools 34+ | Latest |
| Web UI deps | `fastapi` / `uvicorn` / `jinja2` | Latest |
| Device | Android 12+ (verified on Google Pixel 3 or newer) | Android 14+ |
| Device setup | USB debugging ON / screen lock set / signed in to Google Photos | Run while charging |

### Install

**Packaged app (recommended, Windows)**

Download `NAS2GP_v<version>_win64.zip` from [GitHub Releases](https://github.com/square3iris/NAS2GP/releases), extract it to any folder and double-click `NAS2GP.exe`. **No Python installation is required.** On first launch, `config/`, `database/` and `logs/` are created next to the exe.

**Running from source**

```powershell
python --version          # 3.11 / 3.12
pip install -r requirements.txt
```

In both cases, install ADB (Android Platform Tools) and make sure `adb` is on your PATH. **ADB is not bundled** (if it is missing, the app prints install guidance on startup).

### Run

**Packaged app (Windows)**

Double-clicking `NAS2GP.exe` starts the local web server and opens the default browser in **app mode** (Edge / Chrome `--app`). Do not close the window — press **Ctrl+C in the console** to stop. To start the server without opening a browser, set the environment variable `NAS2GP_NO_BROWSER=1`.

**Running from source**

Make the `source` folder the current directory and launch:

```powershell
cd source
python entry_web.py           # then open http://127.0.0.1:8000/ in a browser
```

You can also double-click `source\bin\start_web_app.cmd`.

**macOS / Linux** (best-effort, unverified on real macOS hardware)

```bash
pip install -r requirements.txt   # run from the repository root
cd source
python3 entry_web.py
```

Or run `source/bin/start_web_app.sh`, or on macOS double-click `source/bin/start_web_app.command`.

Phones on the same LAN can view it at `http://<PC-IP>:8000/`.

### Layout

```
NAS2GP/
├ README.md
├ LICENSE
├ .gitattributes    line-ending policy (v1.9.0)
├ requirements.txt  runtime / test dependencies (v1.9.0)
├ docs/        ja / en documentation
├ archive/     release snapshots (latest zip per windows/ mac/ linux/)
├ packaging/   PyInstaller spec for the distribution app (NAS2GP.spec)
└ source/      entry points, main package and runtime data
    ├ launcher.py       entry point of the packaged app (web server + browser)
    ├ entry_web.py / app.py / db_admin.py
    ├ nas2gp/   main package
    ├ scripts/  build scripts (build_app.py / build_release.py)
    ├ bin/      launcher .cmd / .sh / .command files
    ├ config/   settings + config/lang/ external language JSON (per-environment; not committed)
    ├ database/ transfer history database (not committed)
    └ logs/     runtime logs (not committed)
```

### Building the distribution app (for developers)

```powershell
pip install -r requirements.txt      # includes pyinstaller
python source/scripts/build_app.py   # -> archive/<os>/NAS2GP_v<version>_<os>.zip (例: windows/NAS2GP_v2.0.0_win64.zip)
```

It builds in onedir format using `packaging/NAS2GP.spec` and packs the result into a ZIP, which is attached to GitHub Releases. Linux is best-effort (unverified).

The macOS build cannot be produced on Windows because PyInstaller does not cross-compile. `.github/workflows/build-macos.yml` builds the arm64 / x86_64 ZIPs under `archive/mac/` on GitHub-hosted macOS runners (run it manually from the Actions tab, or push a `v*` tag to build and attach them to the Release).

### Notes (please read first)

> [!CAUTION]
> **Misoperation can delete data from the photo folder on your device.**
> Always take a backup before running. Use at your own risk.
> On the first launch, the Web UI asks you to accept the disclaimer (two checkboxes).

- This tool handles your personal media. **Test with sample photos first** before real use.
- `database/`, `logs/` and `config/config_ui.json` can contain personal information (file paths, history) and are not committed to Git.
- The consent state (accepted / not accepted) and the accepted time are shown in the **Settings** page under "Usage consent", where you can also reset it to show the confirmation again.
- The software is provided as is. Please keep separate backups of important data.

### Known limitations and support

- Due to the author's skill limitations, **support is best-effort** only. Behavior is not guaranteed in every environment.
- Verification has been done on a limited set of Windows / Android environments. **macOS / Linux support is best-effort** and has not been verified on real macOS hardware. Other vendors are untested too.
- The SMB / NAS session keep-alive is Windows-only and is skipped on macOS / Linux.
- The "backup complete" detection relies on the Google Photos screen (UI dump), so a Google Photos UI change may break the detection.
- Automatic recovery from disconnects / battery depletion during long runs is implemented, but it is not perfect; when recovery is not possible the tool stops safely.
- Known issues are recorded in `docs/en/CHANGELOG.md` and `docs/en/ROADMAP_AND_TODO.md` in the GitHub repository (they are not included in the distribution ZIP).

### License

MIT License (see `LICENSE`).
