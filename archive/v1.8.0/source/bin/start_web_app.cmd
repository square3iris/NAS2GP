@echo off
REM NAS2GP Web UI 起動 スクリプト
REM 設定: WEB_SERVER_PORT (デフォルト 8000), WEB_SERVER_HOST=0.0.0.0
REM   ⇒ LAN 内 の スマホ / 他PC から  http://<あなたのPCのIP>:<PORT>/  で 閲覧 可能
setlocal
cd /d "%~dp0"
cd ..
REM .venv があればそちら、無ければ PATH 上の python を使用
set "PY=python"
if exist "%CD%\.venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
set "PYTHONPATH=%CD%;%PYTHONPATH%"
REM Web UI では Qt は 不要 だが Import で 落ちる 場合 を 考慮 し て offscreen
set QT_QPA_PLATFORM=offscreen
echo ==================================================
echo NAS2GP Web UI 起動中...
echo ブラウザ で 次 を 開いて ください:
echo   この PC 内:   http://127.0.0.1:8000/
echo   LAN 内 他機器:  http://<あなたのPCのIPアドレス>:8000/
echo   (ポート は settings.WEB_SERVER_PORT で 変更 可能)
echo ==================================================
REM uvicorn 起動 (pip install "uvicorn[standard]" fastapi jinja2  必須)
"%PY%" -m uvicorn nas2gp.ui_web:app --host 0.0.0.0 --port 8000 --reload --reload-include nas2gp
endlocal
