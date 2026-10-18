# NAS2GP

NAS（ネットワークストレージ）に保存した写真・動画を、ADB 経由で Android 端末へ転送し、
**Google Photos のバックアップ完了を確認してから**端末側のファイルを削除する自動同期ツールです。
Windows 用。操作は Web UI（ブラウザ）に一本化しています。

NAS → Android device photo/video sync tool for Windows.
It pushes files over ADB, waits until **Google Photos has finished backing them up**,
and only then deletes them on the device. All operation is done through a browser-based Web UI.

---

## 日本語

### 概要

- NAS 上の写真フォルダを走査し、未転送のファイルだけを端末へまとめて転送（バッチ転送）します。
- 転送先は端末側の「作業フォルダ」（既定 `/sdcard/DCIM/NAS2GP`）。DCIM 配下のため Google Photos の自動バックアップ対象になります。
- Google Photos のバックアップ完了を待ってから、**NAS2GP が転送したファイルだけ**を端末から削除します（個人の写真を巻き込む一括削除は行いません）。
- 転送状況は SQLite データベースで管理し、Web UI から進捗・統計・ファイル一覧を確認できます。

### 主な機能

| 機能 | 内容 |
|---|---|
| バッチ転送 | 件数 / 容量 / ハイブリッドの戦略で分割転送 |
| Google Photos バックアップ待機 | 端末 UI を監視し、完了を確認してから次へ進む |
| 安全な削除 | DB で管理している「転送済みファイル」のみを1件ずつ削除 |
| 作業フォルダの動的選択 | 端末ページから DCIM 配下のフォルダを選んで変更可能 |
| 障害時の自動復旧 | 電池残量低下・ADB 切断を検知し、段階的に復旧。復旧不能なら安全に停止 |
| 切断解析ログ | 一定間隔で電池 / 温度 / 空き容量を記録し、切断時のスナップショットを保存 |
| Web UI | 進捗 / 統計（時系列グラフ） / 管理（ファイル一覧） / 設定 / 診断 |
| 多言語 | 日本語 / English |

### 動作環境

| 項目 | 必要条件 | 推奨 |
|---|---|---|
| OS | Windows 10 (22H2 以降) | Windows 11 (23H2 以降) |
| Python | 3.11 | 3.12 |
| ADB | Android Platform Tools 34 以降 | 最新版 |
| Web UI 依存 | `fastapi` / `uvicorn` / `jinja2` | 最新版 |
| 端末 | Android 12 以上（Google Pixel 3 以降で動作確認） | Android 14 以上 |
| 端末設定 | USB デバッグ ON / 画面ロック設定済 / Google Photos ログイン済 | 充電しながらの運用 |

### インストール

```powershell
python --version          # 3.11 / 3.12
pip install fastapi "uvicorn[standard]" jinja2
```

ADB（Android Platform Tools）を導入し、`adb` に PATH を通してください。

### 起動方法

`source` フォルダをカレントディレクトリにして実行します。

```powershell
cd source
python entry_web.py           # ブラウザで http://127.0.0.1:8000/ を開く
```

または `source\bin\start_web_app.cmd` をダブルクリックします。
同一 LAN 内のスマートフォンからも `http://<PCのIP>:8000/` で閲覧できます。

### ディレクトリ構成

```
NAS2GP/
├ README.md
├ LICENSE
├ docs/        ja / en のドキュメント
├ archive/     リリース版のスナップショット
└ source/      実行エントリ・本体パッケージ・実行時データ
    ├ entry_web.py / app.py / db_admin.py
    ├ nas2gp/   本体パッケージ
    ├ bin/      起動用 .cmd
    ├ config/   設定ファイル（個人環境の設定。Git 管理外）
    ├ database/ 転送履歴データベース（Git 管理外）
    └ logs/     実行ログ（Git 管理外）
```

### 注意事項

- 端末の個人データを扱います。**初回はテスト用の写真で動作を確認**してから本番運用してください。
- `database/` と `logs/`、`config/config_ui.json` には個人情報（ファイルパス・履歴）が含まれるため、Git にはコミットされません。
- 本ソフトは現状のまま提供されます。重要なデータのバックアップは別途取得してください。

### 既知の制限・サポートについて

- 作者のスキル上の制約により、**サポートはベストエフォート（可能な範囲）** となります。すべての環境での動作を保証するものではありません。
- 動作確認は Windows と Android の限られた実機・環境で行っています。他社端末・他 OS での動作は未検証です。
- Google Photos の「バックアップ完了」判定は端末の画面表示（UI ダンプ）に依存しているため、Google Photos アプリの仕様変更により判定できなくなる可能性があります。
- 長時間運用時の切断・電池切れなどへの自動復旧を実装していますが、完全ではありません。復旧できない場合は安全に停止する挙動をとります。
- 既知の不具合は `docs/ja/CHANGELOG.md` および `docs/ja/ROADMAP_AND_TODO.md` に随時記載します。

### ライセンス

MIT License（`LICENSE` を参照）。

---

## English

### Overview

- Scans a photo library on your NAS and transfers only the files that have not been sent yet, in batches.
- The destination is a "working folder" on the device (default `/sdcard/DCIM/NAS2GP`). Being under DCIM, it is eligible for Google Photos auto backup.
- After Google Photos finishes backing the files up, **only the files transferred by NAS2GP** are deleted from the device (there is no bulk delete that could touch your personal photos).
- Transfer state is tracked in a SQLite database. The Web UI shows progress, statistics and the file list.

### Main features

| Feature | Description |
|---|---|
| Batch transfer | Split by count / size / hybrid strategy |
| Google Photos backup wait | Monitors the device UI and proceeds only after backup completes |
| Safe deletion | Deletes only the "already transferred" files tracked in the DB, one file at a time |
| Dynamic working folder | Change it from the device page by browsing folders under DCIM |
| Automatic recovery | Detects low battery / ADB disconnects and recovers step by step; stops safely if it cannot |
| Disconnect analysis log | Periodically records battery / temperature / free space and snapshots the state on a disconnect |
| Web UI | Progress / statistics (time-series charts) / management (file list) / settings / diagnostics |
| Languages | Japanese / English |

### Requirements

| Item | Required | Recommended |
|---|---|---|
| OS | Windows 10 (22H2+) | Windows 11 (23H2+) |
| Python | 3.11 | 3.12 |
| ADB | Android Platform Tools 34+ | Latest |
| Web UI deps | `fastapi` / `uvicorn` / `jinja2` | Latest |
| Device | Android 12+ (verified on Google Pixel 3 or newer) | Android 14+ |
| Device setup | USB debugging ON / screen lock set / signed in to Google Photos | Run while charging |

### Install

```powershell
python --version          # 3.11 / 3.12
pip install fastapi "uvicorn[standard]" jinja2
```

Install ADB (Android Platform Tools) and make sure `adb` is on your PATH.

### Run

Make the `source` folder the current directory and launch:

```powershell
cd source
python entry_web.py           # then open http://127.0.0.1:8000/ in a browser
```

You can also double-click `source\bin\start_web_app.cmd`.
Phones on the same LAN can view it at `http://<PC-IP>:8000/`.

### Layout

```
NAS2GP/
├ README.md
├ LICENSE
├ docs/        ja / en documentation
├ archive/     release snapshots (one zip per version)
└ source/      entry points, main package and runtime data
    ├ entry_web.py / app.py / db_admin.py
    ├ nas2gp/   main package
    ├ bin/      launcher .cmd files
    ├ config/   settings (per-environment; not committed)
    ├ database/ transfer history database (not committed)
    └ logs/     runtime logs (not committed)
```

### Notes

- This tool handles your personal media. **Test with sample photos first** before real use.
- `database/`, `logs/` and `config/config_ui.json` can contain personal information (file paths, history) and are not committed to Git.
- The software is provided as is. Please keep separate backups of important data.

### Known limitations and support

- Due to the author's skill limitations, **support is best-effort** only. Behavior is not guaranteed in every environment.
- Verification has been done on a limited set of Windows / Android environments. Other vendors and OSes are untested.
- The "backup complete" detection relies on the Google Photos screen (UI dump), so a Google Photos UI change may break the detection.
- Automatic recovery from disconnects / battery depletion during long runs is implemented, but it is not perfect; when recovery is not possible the tool stops safely.
- Known issues are recorded in `docs/en/CHANGELOG.md` and `docs/en/ROADMAP_AND_TODO.md`.

### License

MIT License (see `LICENSE`).
