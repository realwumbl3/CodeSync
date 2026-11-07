"""SQLite record of every synced path."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Row:
    path: str
    hash: str
    counter: int
    deleted: bool
    size: int = 0
    mtime: int = 0
    file_id: str = ""


class Index:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                share_id TEXT NOT NULL,
                path TEXT NOT NULL,
                hash TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                mtime INTEGER NOT NULL DEFAULT 0,
                counter INTEGER NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                file_id TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (share_id, path)
            )
            """
        )
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def snapshot(self, share_id: str) -> dict[str, Row]:
        with self._lock:
            return {
                row.path: row
                for row in self._rows(
                    "SELECT path, hash, counter, deleted, size, mtime, file_id FROM files WHERE share_id=?",
                    (share_id,),
                )
            }

    def get(self, share_id: str, path: str) -> Row | None:
        with self._lock:
            rows = self._rows(
                """
                SELECT path, hash, counter, deleted, size, mtime, file_id
                FROM files WHERE share_id=? AND path=?
                """,
                (share_id, path),
            )
        return rows[0] if rows else None

    def under(self, share_id: str, prefix: str) -> list[Row]:
        needle = prefix.rstrip("/") + "/"
        return [
            row
            for row in self.snapshot(share_id).values()
            if not row.deleted and row.path.startswith(needle)
        ]

    def upsert(
        self,
        share_id: str,
        path: str,
        digest: str,
        size: int,
        mtime: int,
        counter: int,
        file_id: str,
    ) -> None:
        with self._lock:
            self._db.execute(
                """
                INSERT INTO files (share_id, path, hash, size, mtime, counter, deleted, file_id)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(share_id, path) DO UPDATE SET
                    hash=excluded.hash,
                    size=excluded.size,
                    mtime=excluded.mtime,
                    counter=excluded.counter,
                    deleted=0,
                    file_id=excluded.file_id
                """,
                (share_id, path, digest, size, mtime, counter, file_id),
            )
            self._db.commit()

    def touch(self, share_id: str, path: str, mtime: int, file_id: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE files SET mtime=?, file_id=? WHERE share_id=? AND path=?",
                (mtime, file_id, share_id, path),
            )
            self._db.commit()

    def set_counter(self, share_id: str, path: str, counter: int) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE files SET counter=? WHERE share_id=? AND path=? AND counter<?",
                (counter, share_id, path, counter),
            )
            self._db.commit()

    def tombstone(self, share_id: str, path: str, digest: str, counter: int) -> None:
        with self._lock:
            self._db.execute(
                """
                INSERT INTO files (share_id, path, hash, size, mtime, counter, deleted, file_id)
                VALUES (?, ?, ?, 0, 0, ?, 1, '')
                ON CONFLICT(share_id, path) DO UPDATE SET
                    hash=excluded.hash,
                    size=0,
                    mtime=0,
                    counter=excluded.counter,
                    deleted=1,
                    file_id=''
                """,
                (share_id, path, digest, counter),
            )
            self._db.commit()

    def forget(self, share_id: str, path: str) -> None:
        """Stop tracking a path without recording a deletion."""
        with self._lock:
            self._db.execute("DELETE FROM files WHERE share_id=? AND path=?", (share_id, path))
            self._db.commit()

    def drop_share(self, share_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM files WHERE share_id=?", (share_id,))
            self._db.commit()

    def _rows(self, sql: str, args: tuple) -> list[Row]:
        found: list[Row] = []
        for path, digest, counter, deleted, size, mtime, file_id in self._db.execute(sql, args):
            found.append(
                Row(
                    path=path,
                    hash=digest,
                    counter=int(counter),
                    deleted=bool(deleted),
                    size=int(size),
                    mtime=int(mtime),
                    file_id=file_id or "",
                )
            )
        return found
