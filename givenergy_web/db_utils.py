"""Shared SQLite connection utilities with consistent WAL and busy_timeout settings."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def open_sqlite(
    db_path: str | Path,
    *,
    timeout: float = 15.0,
    temp_store_memory: bool = True,
    cache_size_kb: int | None = None,
) -> sqlite3.Connection:
    """Open a SQLite connection configured for concurrent WAL access."""
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)};")
    if temp_store_memory:
        conn.execute("PRAGMA temp_store=MEMORY;")
    if cache_size_kb is not None:
        conn.execute(f"PRAGMA cache_size=-{abs(int(cache_size_kb))};")
    return conn
