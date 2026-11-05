# ============================================
# FILE: source/launcher.py
#
# NAS2GP ランチャー (v2.0): 配布物をダブルクリックで起動するための入口。
#   - 空きポートでローカル Web サーバ (FastAPI/uvicorn) を起動する。
#   - 既定ブラウザを「アプリモード」(Edge/Chrome の --app) で開く。
#   - config/ database/ logs/ を実行ファイルと同じフォルダに用意する。
#   - ADB 未検出時は導入手順をコンソールに表示する (ADB は同梱しない)。
#   Windows / macOS / Linux 共通。macOS / Linux はベストエフォート。
#
# NAS2GP launcher (v2.0): entry point so the distribution starts by double-click.
#   - Starts the local web server (FastAPI/uvicorn) on a free port.
#   - Opens the default browser in "app mode" (Edge/Chrome --app).
#   - Creates config/ database/ logs/ next to the executable.
#   - Prints ADB install guidance when adb is not found (ADB is not bundled).
#   Cross-platform (Windows / macOS / Linux; the latter two are best-effort).
#
# 使い方 / Usage:
#   配布版 : NAS2GP.exe をダブルクリック / double-click NAS2GP.exe
#   ソース版 : python source/launcher.py (リポジトリルートから / from the repo root)
#
# 環境変数 / Environment variables:
#   NAS2GP_NO_BROWSER=1 : ブラウザを自動で開かない (サーバのみ) /
#                         do not open a browser automatically (server only)
# ============================================

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

APP_NAME = "NAS2GP"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000


def _app_root() -> Path:
    """config / database / logs を置く基準フォルダを返す。

    配布版 (frozen) は exe のあるフォルダ、ソース版はこのファイルのある
    source/ フォルダを使う (書き込み可能で永続する場所にするのが目的)。

    Return the base folder that holds config / database / logs.
    Packaged (frozen) uses the folder containing the exe; from source it uses
    this file's source/ folder (a writable, persistent location).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _prepare_runtime(app_root: Path) -> None:
    """CWD と WORK_ROOT を app_root に固定し、データ用フォルダを用意する。

    frozen 時は settings.WORK_ROOT が一時展開先 (_MEIPASS) を指してしまい、
    設定 / DB / ログが起動ごとに消えるため、定数とキャッシュを上書きする。

    Pin the CWD and WORK_ROOT to app_root and prepare the data folders.
    When frozen, settings.WORK_ROOT would point at the temporary extract dir
    (_MEIPASS) and the settings / DB / logs would vanish each run, so both the
    constant and the cached logger dir are overridden.
    """
    try:
        os.chdir(app_root)
    except OSError:
        pass
    for name in ("config", "database", "logs"):
        try:
            (app_root / name).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # nas2gp の import より前に import し、WORK_ROOT を確定させる。
    # Import first and pin WORK_ROOT before anything else uses nas2gp.
    import nas2gp.core.settings as _settings

    _settings.WORK_ROOT = app_root
    try:
        # Logger.LOG_DIR は import 時に確定するため、キャッシュも上書きする。
        # Logger.LOG_DIR is fixed at import time, so override the cached value too.
        from nas2gp.core.logger import Logger

        Logger.LOG_DIR = (app_root / "logs").resolve()
    except Exception:
        pass


def _check_adb() -> bool:
    """adb が見つかるか確認し、無ければ導入手順を表示する。

    Check whether adb is available and print install guidance if not.
    """
    if shutil.which("adb"):
        return True
    print(f"[{APP_NAME}] ADB (adb) が 見つかり ませ ん 。 / ADB (adb) was not found.")
    print("  - Android Platform Tools を 導入 し 、 adb に PATH を 通し て ください 。")
    print("  - Install Android Platform Tools and put adb on your PATH.")
    print("  - https://developer.android.com/tools/releases/platform-tools")
    print("  導入 後 、 この アプリ を 再起動 し て ください 。 / Restart this app after installing.")
    return False


def _windows_browser_candidates() -> list[str | None]:
    """アプリモード対応ブラウザ (Edge/Chrome) の候補パスを優先順で返す。

    Return candidate paths of app-mode capable browsers (Edge/Chrome), in order.
    """
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA")
    return [
        shutil.which("msedge"),
        shutil.which("chrome"),
        fr"{pf86}\Microsoft\Edge\Application\msedge.exe",
        fr"{pf}\Microsoft\Edge\Application\msedge.exe",
        fr"{pf}\Google\Chrome\Application\chrome.exe",
        fr"{pf86}\Google\Chrome\Application\chrome.exe",
        (fr"{local}\Google\Chrome\Application\chrome.exe" if local else None),
    ]


def _open_app_window(url: str) -> None:
    """既定ブラウザをアプリモードで開く。失敗時は通常ブラウザで開く。

    Open the default browser in app mode; fall back to a normal browser tab.
    """
    if os.environ.get("NAS2GP_NO_BROWSER"):
        return
    try:
        if sys.platform.startswith("win"):
            for cand in _windows_browser_candidates():
                if cand and Path(cand).exists():
                    subprocess.Popen([cand, f"--app={url}"], close_fds=True)
                    return
        elif sys.platform == "darwin":
            for app in ("Google Chrome", "Microsoft Edge"):
                try:
                    subprocess.Popen(["open", "-na", app, "--args", f"--app={url}"])
                    return
                except OSError:
                    continue
        else:
            for exe in ("google-chrome", "chromium", "microsoft-edge"):
                p = shutil.which(exe)
                if p:
                    subprocess.Popen([p, f"--app={url}"])
                    return
    except Exception:
        pass
    import webbrowser

    webbrowser.open(url)


def main() -> int:
    # 日本語ログを Windows のコンソールへ安全に出すための保険。
    # Safeguard so Japanese logs print safely on the Windows console.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    app_root = _app_root()
    _prepare_runtime(app_root)

    from entry_web import _find_free_port

    import nas2gp.core.settings as _settings

    host = str(getattr(_settings, "WEB_SERVER_HOST", DEFAULT_HOST) or DEFAULT_HOST)
    port_cfg = int(getattr(_settings, "WEB_SERVER_PORT", DEFAULT_PORT) or DEFAULT_PORT)
    port = _find_free_port(host, port_cfg, max_attempts=10)
    if port is None:
        print(f"[{APP_NAME}][FATAL] ポート {port_cfg} 〜 {port_cfg + 9} が すべて 使用 中 です 。")
        print(f"[{APP_NAME}][FATAL] All ports {port_cfg}-{port_cfg + 9} are in use.")
        return 2
    if port != port_cfg:
        print(f"[{APP_NAME}][NOTE] ポート {port_cfg} は 使用 中 の ため {port} に 変更 し まし た 。")

    url = f"http://127.0.0.1:{port}/"
    print(f"[{APP_NAME}] バージョン / version : {getattr(_settings, 'APP_VERSION', '?')}")
    print(f"[{APP_NAME}] Web UI : {url}")
    _check_adb()
    print(f"[{APP_NAME}] 終了 する に は この ウィンドウ で Ctrl+C を 押し て ください 。")
    print(f"[{APP_NAME}] Press Ctrl+C in this window to stop.")

    import uvicorn

    from nas2gp.ui_web import app

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()

    # サーバ起動を待ってからブラウザを開く (起動前オープンの失敗を防ぐ)。
    # Wait for the server to start before opening the browser.
    for _ in range(150):
        if getattr(server, "started", False) or not thread.is_alive():
            break
        time.sleep(0.1)
    _open_app_window(url)

    try:
        while thread.is_alive() and not getattr(server, "should_exit", False):
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n[{APP_NAME}] 停止 し て い ます ... / stopping ...")
        server.should_exit = True
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
