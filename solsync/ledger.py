"""Persistent state in SQLite: the daily quota ledger, item history and settings.

Quota rule: each row in `uploads` is one solution file. A row counts against its
`day` while it is 'reserved', 'committed' or 'verified'. Only 'failed' rows
(nothing reached GitHub) free their slot.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from .config import manila_now

COUNTING = ("reserved", "committed", "verified")

SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    id          INTEGER PRIMARY KEY,
    batch_id    TEXT NOT NULL,     -- one batch = one exercise (all its files)
    day         TEXT NOT NULL,     -- Philippine date the quota is charged to
    platform    TEXT NOT NULL,
    item_key    TEXT NOT NULL,
    file_path   TEXT NOT NULL,     -- path inside the solution repo
    sha256      TEXT NOT NULL,
    commit_sha  TEXT,
    status      TEXT NOT NULL,     -- reserved | committed | verified | failed
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    item_key    TEXT PRIMARY KEY,
    platform    TEXT NOT NULL,
    title       TEXT,
    language    TEXT,
    url         TEXT,
    method      TEXT,
    evidence    TEXT,
    source_ref  TEXT,
    status      TEXT,
    dest        TEXT,
    note        TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_retry  TEXT,
    last_seen   TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _now() -> str:
    return manila_now().isoformat(timespec="seconds")


class Ledger:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: we manage transactions ourselves with BEGIN IMMEDIATE.
        self.db = sqlite3.connect(db_path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ---- quota -------------------------------------------------------------
    def used_on(self, day: str) -> int:
        q = f"SELECT COUNT(*) FROM uploads WHERE day=? AND status IN {COUNTING}"
        return self.db.execute(q, (day,)).fetchone()[0]

    def reserve(self, day: str, limit: int, platform: str, item_key: str,
                files: list[tuple[str, str]]) -> str | None:
        """Atomically reserve quota for all files of one exercise.

        files = [(repo_path, sha256), ...]. Returns a batch id, or None if the
        whole exercise does not fit in today's remaining quota.
        """
        self.db.execute("BEGIN IMMEDIATE")  # blocks other writers until COMMIT
        try:
            if self.used_on(day) + len(files) > limit:
                self.db.execute("ROLLBACK")
                return None
            batch = uuid.uuid4().hex
            now = _now()
            self.db.executemany(
                "INSERT INTO uploads(batch_id, day, platform, item_key, file_path, sha256,"
                " status, created_at, updated_at) VALUES (?,?,?,?,?,?,'reserved',?,?)",
                [(batch, day, platform, item_key, p, h, now, now) for p, h in files])
            self.db.execute("COMMIT")
            return batch
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def move_batch_to_day(self, batch: str, day: str, limit: int) -> bool:
        """Charge an unpushed batch to `day` (used when retrying on a later day)."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            rows = self.db.execute("SELECT day FROM uploads WHERE batch_id=?", (batch,)).fetchall()
            if all(r["day"] == day for r in rows):
                self.db.execute("COMMIT")
                return True
            if self.used_on(day) + len(rows) > limit:
                self.db.execute("ROLLBACK")
                return False
            self.db.execute("UPDATE uploads SET day=?, updated_at=? WHERE batch_id=?",
                            (day, _now(), batch))
            self.db.execute("COMMIT")
            return True
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def set_batch(self, batch: str, status: str, commit_sha: str | None = None,
                  error: str | None = None) -> None:
        self.db.execute(
            "UPDATE uploads SET status=?, commit_sha=COALESCE(?, commit_sha), error=?,"
            " updated_at=? WHERE batch_id=?", (status, commit_sha, error, _now(), batch))

    def open_batches(self, platform: str) -> list[dict]:
        """Batches that were started but not yet verified on GitHub."""
        rows = self.db.execute(
            "SELECT * FROM uploads WHERE platform=? AND status IN ('reserved','committed')"
            " ORDER BY id", (platform,)).fetchall()
        batches: dict[str, dict] = {}
        for r in rows:
            b = batches.setdefault(r["batch_id"], {
                "batch_id": r["batch_id"], "day": r["day"], "item_key": r["item_key"],
                "status": r["status"], "commit_sha": r["commit_sha"], "files": []})
            b["files"].append((r["file_path"], r["sha256"]))
        return list(batches.values())

    def uploaded_keys(self) -> set[str]:
        rows = self.db.execute(
            "SELECT DISTINCT item_key FROM uploads WHERE status IN ('reserved','committed','verified')")
        return {r[0] for r in rows}

    # ---- items -------------------------------------------------------------
    def save_item(self, c, attempts_delta: int = 0, next_retry: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO items(item_key, platform, title, language, url, method, evidence,"
            " source_ref, status, dest, note, attempts, next_retry, last_seen)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(item_key) DO UPDATE SET title=excluded.title,"
            " language=excluded.language, url=excluded.url, method=excluded.method,"
            " evidence=excluded.evidence, source_ref=excluded.source_ref,"
            " status=excluded.status, dest=excluded.dest, note=excluded.note,"
            " attempts=items.attempts + ?, next_retry=excluded.next_retry,"
            " last_seen=excluded.last_seen",
            (c.key, c.platform, c.title, c.language, c.url, c.method, c.evidence,
             c.source_ref, c.status, ";".join(c.dest_paths), c.note, attempts_delta,
             next_retry, _now(), attempts_delta))

    def item(self, key: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM items WHERE item_key=?", (key,)).fetchone()

    def reset_item(self, key: str) -> bool:
        cur = self.db.execute(
            "UPDATE items SET attempts=0, next_retry=NULL WHERE item_key=?", (key,))
        return cur.rowcount > 0

    # ---- meta --------------------------------------------------------------
    def get_meta(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO meta(key, value) VALUES (?,?) ON CONFLICT(key)"
                        " DO UPDATE SET value=excluded.value", (key, value))


def retry_after(attempts: int) -> str:
    """Backoff for transient failures: 1h, 2h, 4h ... capped at 48h."""
    from datetime import timedelta
    hours = min(2 ** attempts, 48)
    return (manila_now() + timedelta(hours=hours)).isoformat(timespec="seconds")


def is_due(next_retry: str | None) -> bool:
    return not next_retry or datetime.fromisoformat(next_retry) <= manila_now()
