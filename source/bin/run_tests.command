#!/bin/sh
# macOS: Finder で ダブルクリック 実行 用 。 run_tests.sh へ 委譲 する 。
# macOS: for double-click from Finder; delegates to run_tests.sh.
BIN_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$BIN_DIR/run_tests.sh" "$@"
