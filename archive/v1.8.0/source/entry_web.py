import os, sys


def _find_free_port(host, start_port, max_attempts=10):
    import socket
    for offset in range(max_attempts):
        port = int(start_port) + int(offset)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                try:
                    s.bind((str(host), int(port)))
                except OSError:
                    continue
                return int(port)
        except Exception:
            continue
    return None


if __name__ == "__main__":
    import uvicorn
    ROOT = os.path.abspath(os.path.dirname(__file__))
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)

    try:
        from nas2gp.core import settings as _s
        host = str(getattr(_s, "WEB_SERVER_HOST", "0.0.0.0") or "0.0.0.0")
        port_cfg = int(getattr(_s, "WEB_SERVER_PORT", 8000) or 8000)
    except Exception:
        host, port_cfg = "0.0.0.0", 8000

    port = _find_free_port(host, port_cfg, max_attempts=10)
    if port is None:
        print(f"[Web UI][FATAL] ポート {port_cfg} 〜 {port_cfg + 9} まで の 計 10 個 すべて が 使用 中 で す 。")
        print(f"[Web UI][FATAL] タスク マネージャ 等 で 既存 の python/uvicorn プロセス を 終了 する か 、 設定 で WEB_SERVER_PORT を 空い て いる ポート に 変更 し て ください 。")
        sys.exit(2)

    if port != port_cfg:
        print(
            f"[Web UI][NOTE] ポート {port_cfg} は 使用 中 の ため 、 空き ポート {port} に 自動 フォールバック し まし た 。"
        )
    print(f"[Web UI] http://127.0.0.1:{port}/  (host={host}, port={port})")
    try:
        # access_log=False: 「GET /api/status 200 OK」 等 の リクエスト ログ (SSE/ポーリング で 毎秒 流れる)
        #   を 抑止 。 エラー / 警告 は 従来 どおり 表示 さ れ ます 。
        # timeout_graceful_shutdown=3: ブラウザ の SSE (/progress/sse 等) が 接続 を 開い た まま だ と
        #   Ctrl+C で 「Waiting for connections to close」 の まま 停止 し ない ため 、
        #   3 秒 で 残 接続 を 強制 クローズ し て 終了 する 。
        uvicorn.run(
            "nas2gp.ui_web:app",
            host=host,
            port=port,
            reload=False,
            access_log=False,
            timeout_graceful_shutdown=3,
        )
    except OSError as e:
        import errno
        err_no = getattr(e, "winerror", None) or getattr(e, "errno", None)
        if str(err_no) == "10048" or "10048" in str(e) or err_no == errno.EADDRINUSE:
            print(f"[Web UI][FATAL] uvicorn 起動 時 に も ポート {port} が バインド でき ませ ん でし た (EADDRINUSE / WinError 10048)。")
            print(f"[Web UI][FATAL] netstat -ano | findstr :{port} など で 占有 プロセス を 確認 し 、 解放 する か 別 の ポート を 指定 し て ください 。")
            sys.exit(3)
        raise
