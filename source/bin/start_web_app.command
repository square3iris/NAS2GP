#!/bin/sh
# macOS: Finder で ダブルクリック 実行 用 。 start_web_app.sh へ 委譲 する 。
# macOS: for double-click from Finder; delegates to start_web_app.sh.
BIN_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$BIN_DIR/start_web_app.sh" "$@"
