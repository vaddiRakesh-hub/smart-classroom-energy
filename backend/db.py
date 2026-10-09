"""Tiny SQLite layer (one shared connection guarded by a re-entrant lock)."""
import os
import sqlite3
import threading
from contextlib import contextmanager

from config import DB_PATH
from timetable import ROOMS, build_timetable

_lock = threading.RLock()
_conn = None
_depth = 0

SCHEMA = """
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
"""


def conn():
    global _conn
    if _conn is None:
        db_dir = os.path.dirname(os.path.abspath(DB_PATH))
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn


@contextmanager
def transaction():
    """Group many writes into one commit (used by the backfill)."""
    global _depth
    with _lock:
        _depth += 1
        try:
            yield
        finally:
            _depth -= 1
            if _depth == 0:
                conn().commit()


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql, args=()):
    with _lock:
        cur = conn().execute(sql, args)
        if _depth == 0:
            conn().commit()
        return cur.lastrowid


def init_db():
    with _lock:
        conn().executescript(SCHEMA)
        if not one("SELECT 1 AS n FROM classrooms"):
            for r in ROOMS:
                x("INSERT INTO classrooms VALUES (:id,:name,:kind,:building,:capacity,:light_w,:fan_w,:ac_w)", r)
                x("INSERT INTO state (classroom_id) VALUES (?)", (r["id"],))
            for t in build_timetable():
                x("INSERT INTO timetable (classroom_id,dow,start_min,end_min,subject,students) "
                  "VALUES (:classroom_id,:dow,:start_min,:end_min,:subject,:students)", t)
