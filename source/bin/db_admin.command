#!/bin/sh
# macOS: Finder で ダブルクリック 実行 用 。 db_admin.sh へ 委譲 する 。
# macOS: for double-click from Finder; delegates to db_admin.sh.
BIN_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$BIN_DIR/db_admin.sh" "$@"
