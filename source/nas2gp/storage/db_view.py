# ============================================
# FILE: nas2gp/storage/db_view.py
# PURPOSE: 転送対象ファイル の 一覧 と 各工程 (転送 / GP / 削除) の
# PURPOSE: read-only accessor for the transfer-target file list and each
#   完了状況 を 参照 する ため の 読み取り専用 アクセサ。
#   stage (transfer / GP / delete) completion status.
#   書き込み 中 の Database 接続 と 競合 し ない よう 別接続
#   Uses a separate connection so it does not conflict with the writer
#   (mode=ro + busy_timeout) で クエリ する。集計 は ページング 済み。
#   (mode=ro + busy_timeout); aggregation is paginated.
# ============================================
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# 画面上 の 各列 (工程) フィルタ は all / done / failed / remaining を 取る。
# Per-column (stage) filters on screen take all / done / failed / remaining.
#   remaining: その工程 が 未完了 (DONE 以外 / NULL)
#   remaining: that stage is incomplete (not DONE / NULL)
#   done:      その工程 が 完了 (DONE)
#   done:      that stage is complete (DONE)
#   failed:    その工程 が 失敗 (FAILED)
#   failed:    that stage is failed (FAILED)
# 列 (工程) → 状態列名。Web 画面 の 各列 フィルタ で 使用。
# Column (stage) -> state column name. Used by each column filter in the Web UI.
_COL_FILTERS: Dict[str, str] = {
    "transfer": "phone_transfer_state",
    "gp": "google_photos_state",
    "delete": "phone_delete_state",
}


def _filter_cond(col: str, val: Optional[str]) -> Optional[str]:
    """1 列 分 の 絞り込み 条件 を 返す (all/未指定 は 条件なし)。

    Return the filter condition for one column (all/unspecified -> no condition).
    """
    v = str(val or "all").strip().lower()
    if v in ("", "all"):
        return None
    if v == "done":
        return f"{col}='DONE'"
    if v == "failed":
        return f"{col}='FAILED'"
    if v == "remaining":
        return f"({col}!='DONE' OR {col} IS NULL)"
    return None

# 並べ替え キー → SQL ORDER BY 断片 (ホワイトリスト のみ。注入 防止)
# Sort key -> SQL ORDER BY fragment (whitelist only; prevents SQL injection)
_SORT_MAP: Dict[str, str] = {
    "id": "id ASC",
    "id_desc": "id DESC",
    "size": "size DESC",
    "size_asc": "size ASC",
    "filename": "filename ASC",
    "updated": "COALESCE(updated_at, '') DESC",
}

_SELECT_COLUMNS = (
    "id, filename, media_type, size, "
    "phone_transfer_state, google_photos_state, phone_delete_state, "
    "retry_count, last_error, updated_at"
)


def resolve_db_path() -> Optional[str]:
    """書き込み側 (Database) が 実際 に 開く DB ファイル パス を 返す。
    Database は db モジュール の module-level DB_FILE を 使用 する ため、
    同じ 値 を 基準 に する (config の DB_FILE と 差異 が ある 場合 の 不整合 防止)。
    相対 パス の 場合 は WORK_ROOT 配下 として 解決 する。

    Return the DB file path actually opened by the writer (Database).
    Since Database uses the module-level DB_FILE in the db module, base the
    result on that same value (prevents mismatch when config DB_FILE differs).
    Relative paths are resolved under WORK_ROOT.
    """
    candidates: List[str] = []
    try:
        from nas2gp.storage import db as _db
        raw = str(getattr(_db, "DB_FILE", "") or "")
        if raw:
            candidates.append(raw)
    except Exception:
        pass
    try:
        from nas2gp.core import settings as _settings
        raw = str(getattr(_settings, "DB_FILE", "") or "")
        if raw and raw not in candidates:
            candidates.append(raw)
    except Exception:
        pass

    if not candidates:
        return None

    root = None
    try:
        from nas2gp.core import settings as _settings
        root = getattr(_settings, "WORK_ROOT", None)
    except Exception:
        root = None
    base = Path(str(root)) if root else Path.cwd()

    resolved: List[str] = []
    for raw in candidates:
        p = Path(raw)
        if not p.is_absolute():
            p = base / p
        resolved.append(str(p))

    for p in resolved:
        try:
            if Path(p).exists():
                return p
        except Exception:
            continue
    return resolved[0]


class FileView:
    """ファイル一覧 / 集計 を 読み取り専用 接続 で 提供 する。

    Provide the file list / summary over a read-only connection.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or resolve_db_path()
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------
    # 接続
    # Connection
    # ------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        if not self.db_path or not Path(self.db_path).exists():
            raise FileNotFoundError(f"DB file not found: {self.db_path}")
        # 読み取り専用 (mode=ro) + 書き込み 中 でも 待つ ため timeout を 設定。
        # Read-only (mode=ro) + timeout so queries wait even while a writer is active.
        conn = sqlite3.connect(
            f"file:{self.db_path}?mode=ro", uri=True, timeout=5.0
        )
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
        except Exception:
            pass
        conn.row_factory = sqlite3.Row
        self._conn = conn
        return conn

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # ------------------------------------------------------------
    # WHERE 句 生成 (フィルタ / 検索)
    # Build WHERE clause (filters / search)
    # ------------------------------------------------------------
    @staticmethod
    def _build_where(transfer, gp, delete, search):
        where: List[str] = []
        params: List[Any] = []

        for key, val in (("transfer", transfer), ("gp", gp), ("delete", delete)):
            cond = _filter_cond(_COL_FILTERS[key], val)
            if cond:
                where.append(cond)

        s = (search or "").strip()
        if s:
            where.append("(filename LIKE ? OR path LIKE ?)")
            like = f"%{s}%"
            params.extend([like, like])

        clause = (" WHERE " + " AND ".join(where)) if where else ""
        return clause, params

    # ------------------------------------------------------------
    # 一覧
    # Listing
    # ------------------------------------------------------------
    def list_files(
        self,
        page: int = 1,
        page_size: int = 100,
        transfer: str = "all",
        gp: str = "all",
        delete: str = "all",
        search: str = "",
        sort: str = "id",
    ) -> Dict[str, Any]:
        page = max(1, int(page or 1))
        page_size = max(1, min(500, int(page_size or 100)))
        order = _SORT_MAP.get(str(sort or "id"), _SORT_MAP["id"])
        clause, params = self._build_where(transfer, gp, delete, search)

        conn = self._connect()
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM files{clause}", params)
        total = int(cur.fetchone()[0] or 0)

        offset = (page - 1) * page_size
        cur.execute(
            f"SELECT {_SELECT_COLUMNS} FROM files{clause} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            list(params) + [page_size, offset],
        )
        rows = []
        for r in cur.fetchall():
            rows.append({
                "id": r["id"],
                "filename": r["filename"],
                "media_type": r["media_type"],
                "size": r["size"],
                "transfer_state": r["phone_transfer_state"],
                "gp_state": r["google_photos_state"],
                "delete_state": r["phone_delete_state"],
                "retry_count": r["retry_count"],
                "last_error": r["last_error"],
                "updated_at": r["updated_at"],
            })

        page_count = (total + page_size - 1) // page_size if total else 0
        return {
            "ok": True,
            "rows": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "page_count": page_count,
            "filters": {
                "transfer": str(transfer or "all"),
                "gp": str(gp or "all"),
                "delete": str(delete or "all"),
            },
            "search": (search or "").strip(),
            "sort": str(sort or "id"),
        }

    # ------------------------------------------------------------
    # 集計 (各工程 の 件数 / 容量)
    # Summary (per-stage counts / bytes)
    # ------------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        conn = self._connect()
        cur = conn.cursor()

        # 全 項目 を 1 パス で 集計 する (142k 行 でも 数百 ms 以内)。
        # Aggregate all fields in a single pass (hundreds of ms even for 142k rows).
        def agg(col: str, alias: str) -> str:
            return (
                f"SUM(CASE WHEN {col}='DONE' THEN 1 ELSE 0 END) AS {alias}_done, "
                f"SUM(CASE WHEN {col}='FAILED' THEN 1 ELSE 0 END) AS {alias}_failed, "
                f"SUM(CASE WHEN {col}!='DONE' OR {col} IS NULL THEN 1 ELSE 0 END) AS {alias}_remaining"
            )

        cur.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(size), 0) AS total_bytes,
                COALESCE(SUM(CASE WHEN phone_transfer_state='DONE' THEN size ELSE 0 END), 0) AS done_bytes,
                {agg('phone_transfer_state', 'transfer')},
                {agg('google_photos_state', 'gp')},
                {agg('phone_delete_state', 'delete')}
            FROM files
            """
        )
        r = cur.fetchone()

        def counts_for(prefix: str) -> Dict[str, int]:
            return {
                "done": int(r[f"{prefix}_done"] or 0),
                "failed": int(r[f"{prefix}_failed"] or 0),
                "remaining": int(r[f"{prefix}_remaining"] or 0),
            }

        total = int(r["total"] or 0)
        total_bytes = int(r["total_bytes"] or 0)
        done_bytes = int(r["done_bytes"] or 0)
        return {
            "ok": True,
            "total": total,
            "total_bytes": total_bytes,
            "done_bytes": done_bytes,
            "remaining_bytes": total_bytes - done_bytes,
            "transfer": counts_for("transfer"),
            "gp": counts_for("gp"),
            "delete": counts_for("delete"),
        }

    # ------------------------------------------------------------
    # 時系列 集計 (統計 グラフ 用, v1.7.19)
    # Time-series aggregation (for the stats graphs, v1.7.19)
    # ------------------------------------------------------------
    # 表示 レンジ → (バケット種別, 分割数)。
    # Range key -> (bucket kind, number of buckets).
    _RANGE_SPEC: Dict[str, Dict[str, Any]] = {
        "24h": {"bucket": "hour", "span": 24},
        "7d": {"bucket": "hour", "span": 168},
        "30d": {"bucket": "day", "span": 30},
    }

    def get_stats_baseline(self) -> Optional[str]:
        """統計 の 『過去履歴クリア』 基準時刻 を 返す (ローカル 'YYYY-MM-DD HH:MM:SS')。
        未設定 / app_meta 表 が 無い 場合 は None を 返す (例外 は 握り 潰す)。

        Return the stats "clear history" baseline timestamp (local
        'YYYY-MM-DD HH:MM:SS'); None if unset or the app_meta table is absent."""
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute("SELECT value FROM app_meta WHERE key='stats_baseline'")
            row = cur.fetchone()
            if not row:
                return None
            val = str(row[0] or "").strip()
            return val or None
        except Exception:
            return None

    def timeseries(self, range_key: str = "24h", baseline: Optional[str] = None) -> Dict[str, Any]:
        """統計 グラフ 用 の 時系列 集計 を 返す 。
        - 転送量 (bytes) と 実効速度 (bytes/s): batch_history (過去分 も 表示 可)
        - 写真 / 動画 の 枚数: files.transferred_at (v1.7.19 以降 の 記録)
        欠損 バケット は 0 埋め し 、 x 軸 が 連続 に なる よう 揃え る 。

        Return time-series aggregates for the stats graphs:
        - volume (bytes) / effective speed (bytes/s): from batch_history (covers past data)
        - image / video counts: from files.transferred_at (recorded since v1.7.19)
        Missing buckets are zero-filled so the x-axis stays contiguous."""
        spec = self._RANGE_SPEC.get(str(range_key or "24h").lower(), self._RANGE_SPEC["24h"])
        bucket = str(spec["bucket"])
        span = int(spec["span"])
        fmt = "%Y-%m-%d %H:00" if bucket == "hour" else "%Y-%m-%d"
        short = "%H:00" if bucket == "hour" else "%m/%d"

        now = datetime.now()
        if bucket == "hour":
            end = now.replace(minute=0, second=0, microsecond=0)
            starts = [end - timedelta(hours=i) for i in range(span - 1, -1, -1)]
        else:
            end = now.replace(hour=0, minute=0, second=0, microsecond=0)
            starts = [end - timedelta(days=i) for i in range(span - 1, -1, -1)]

        keys = [s.strftime(fmt) for s in starts]
        short_labels = [s.strftime(short) for s in starts]
        idx = {k: i for i, k in enumerate(keys)}
        since = starts[0].strftime("%Y-%m-%d %H:%M:%S")

        # v1.7.20: 『過去履歴クリア』 基準時刻 が あれ ば 、 それ 以降 の データ だけ を
        #   集計 する (非破壊: DB から は 消さ ない)。
        # v1.7.20: If a "clear history" baseline is set, aggregate only data at or
        #   after it (non-destructive; nothing is deleted from the DB).
        bl = str(baseline or "").strip().replace("T", " ")
        if bl and len(bl) >= 19 and bl > since:
            since = bl

        volume_bytes = [0] * len(keys)
        speed_bps = [0] * len(keys)
        images = [0] * len(keys)
        videos = [0] * len(keys)

        conn = self._connect()
        cur = conn.cursor()

        # 1) 転送量 と 実効速度 (batch_history; 過去分 も 集計 できる)。
        # 1) Volume and effective speed (batch_history; also covers past data).
        try:
            cur.execute(
                f"""
                SELECT strftime('{fmt}', replace(started_at,'T',' ')) AS bucket,
                       COALESCE(SUM(total_bytes),0) AS bytes,
                       COALESCE(SUM(CASE WHEN upload_seconds IS NOT NULL AND upload_seconds>0
                                         THEN upload_seconds ELSE elapsed_seconds END),0) AS secs
                FROM batch_history
                WHERE status='DONE' AND replace(started_at,'T',' ') >= ?
                GROUP BY bucket
                """,
                (since,),
            )
            for r in cur.fetchall():
                i = idx.get(r["bucket"])
                if i is None:
                    continue
                b = int(r["bytes"] or 0)
                s = float(r["secs"] or 0)
                volume_bytes[i] = b
                speed_bps[i] = int(b / s) if (s > 0.1 and b > 0) else 0
        except Exception:
            pass

        # 2) 写真 / 動画 の 枚数 (files.transferred_at; v1.7.19 以降 の 記録)。
        # 2) Image / video counts (files.transferred_at; recorded since v1.7.19).
        try:
            cur.execute(
                f"""
                SELECT strftime('{fmt}', transferred_at) AS bucket,
                       SUM(CASE WHEN UPPER(COALESCE(media_type,''))='VIDEO' THEN 1 ELSE 0 END) AS videos,
                       SUM(CASE WHEN UPPER(COALESCE(media_type,''))='IMAGE' THEN 1 ELSE 0 END) AS images
                FROM files
                WHERE transferred_at IS NOT NULL AND transferred_at >= ?
                GROUP BY bucket
                """,
                (since,),
            )
            for r in cur.fetchall():
                i = idx.get(r["bucket"])
                if i is None:
                    continue
                videos[i] = int(r["videos"] or 0)
                images[i] = int(r["images"] or 0)
        except Exception:
            pass

        return {
            "ok": True,
            "range": str(range_key or "24h"),
            "bucket": bucket,
            "labels": short_labels,
            "keys": keys,
            "since": since,
            "volume_bytes": volume_bytes,
            "speed_bps": speed_bps,
            "images": images,
            "videos": videos,
        }
