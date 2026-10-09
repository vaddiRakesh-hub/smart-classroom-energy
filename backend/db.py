"""SQLite database abstraction layer with thread-safe connection pooling and transactions.

Provides parameterized query execution, schema initialization, high-performance WAL
journaling, and memory caching pragmas.
"""
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Sequence, Union
import os
import sqlite3
import threading

from config import DB_PATH
from timetable import ROOMS, build_timetable

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
_depth: int = 0

SCHEMA: str = """
CREATE TABLE IF NOT EXISTS classrooms (
    id INTEGER PRIMARY KEY, name TEXT, kind TEXT, building TEXT, capacity INTEGER,
    light_w INTEGER, fan_w INTEGER, ac_w INTEGER
);
CREATE TABLE IF NOT EXISTS timetable (
    id INTEGER PRIMARY KEY AUTOINCREMENT, classroom_id INTEGER, dow INTEGER,
    start_min INTEGER, end_min INTEGER, subject TEXT, students INTEGER
);
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, classroom_id INTEGER, ts TEXT,
    pir INTEGER, temp REAL, humidity REAL, lux REAL, prob REAL,
    light INTEGER, fan INTEGER, ac INTEGER,
    power_w REAL, baseline_w REAL, interval_min REAL
);
CREATE INDEX IF NOT EXISTS idx_readings_room_ts ON readings(classroom_id, ts);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);
CREATE TABLE IF NOT EXISTS state (
    classroom_id INTEGER PRIMARY KEY, ts TEXT, light INTEGER DEFAULT 0, fan INTEGER DEFAULT 0,
    ac INTEGER DEFAULT 0, mode TEXT DEFAULT 'auto', prob REAL DEFAULT 0, occupied INTEGER DEFAULT 0,
    reason TEXT DEFAULT '', pir INTEGER DEFAULT 0, temp REAL, humidity REAL, lux REAL, power_w REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, classroom_id INTEGER, kind TEXT, message TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_id ON events(id DESC);
CREATE INDEX IF NOT EXISTS idx_timetable_cid_dow ON timetable(classroom_id, dow);
"""


def conn() -> sqlite3.Connection:
    """Return the shared thread-safe SQLite connection initialized with high-performance pragmas."""
    global _conn
    if _conn is None:
        db_dir = os.path.dirname(os.path.abspath(DB_PATH))
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.execute("PRAGMA cache_size=-64000")
        _conn.execute("PRAGMA temp_store=MEMORY")
    return _conn


@contextmanager
def transaction():
    """Context manager grouping multiple database write operations into a single atomic commit."""
    global _depth
    with _lock:
        _depth += 1
        try:
            yield
        finally:
            _depth -= 1
            if _depth == 0:
                conn().commit()


def q(sql: str, args: Union[Sequence[Any], Dict[str, Any]] = ()) -> List[Dict[str, Any]]:
    """Execute a parameterized SQL query and return rows as standard Python dictionaries.

    Args:
        sql: Parameterized SQL statement with ? or :named placeholders.
        args: Sequence or mapping of parameters to bind safely.

    Returns:
        List of dictionaries corresponding to the query result rows.
    """
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args: Union[Sequence[Any], Dict[str, Any]] = ()) -> Optional[Dict[str, Any]]:
    """Execute a parameterized query expecting at most one row.

    Returns:
        Dictionary representing the first matching row, or None if no match.
    """
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql: str, args: Union[Sequence[Any], Dict[str, Any]] = ()) -> Optional[int]:
    """Execute a parameterized DML/DDL statement (INSERT, UPDATE, DELETE).

    Returns:
        The lastrowid integer if available, or None.
    """
    with _lock:
        cur = conn().execute(sql, args)
        if _depth == 0:
            conn().commit()
        return cur.lastrowid


def init_db() -> None:
    """Initialize database tables, indexes, and baseline seed data if empty."""
    with _lock:
        conn().executescript(SCHEMA)
        if not one("SELECT 1 AS n FROM classrooms"):
            for r in ROOMS:
                x("INSERT INTO classrooms VALUES (:id,:name,:kind,:building,:capacity,:light_w,:fan_w,:ac_w)", r)
                x("INSERT INTO state (classroom_id) VALUES (?)", (r["id"],))
            for t in build_timetable():
                x("INSERT INTO timetable (classroom_id,dow,start_min,end_min,subject,students) "
                  "VALUES (:classroom_id,:dow,:start_min,:end_min,:subject,:students)", t)
