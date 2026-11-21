"""SQLite store (WAL). Columns ending in _json hold JSON and come back decoded."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from .config import DATA

_PATH = DATA / "surge.db"
_local = threading.local()
_write = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL, platform TEXT, channel TEXT, title TEXT,
  kind TEXT NOT NULL DEFAULT 'live',            -- live | vod
  status TEXT NOT NULL DEFAULT 'idle',          -- idle | starting | live | analyzing | rendering | done | error | stopped
  stage TEXT, progress REAL DEFAULT 0, error TEXT,
  settings_json TEXT DEFAULT '{}', stats_json TEXT DEFAULT '{}',
  created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER, streamer TEXT, platform TEXT, game TEXT,
  peak REAL, start REAL, end REAL, duration REAL,
  score REAL DEFAULT 0, kind TEXT, chat_class TEXT,
  signals_json TEXT DEFAULT '{}', chat_json TEXT DEFAULT '[]',
  words_json TEXT DEFAULT '[]', transcript TEXT,
  hook TEXT, title TEXT, description TEXT, hashtags_json TEXT DEFAULT '[]', variants_json TEXT DEFAULT '[]',
  layout TEXT, caption_style TEXT, cam_json TEXT,
  raw_path TEXT, path TEXT, thumb TEXT,
  status TEXT NOT NULL DEFAULT 'queued',        -- queued | cutting | transcribing | writing | rendering | ready | approved | rejected | failed
  error TEXT, spoiler INTEGER DEFAULT 0,
  created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,                       -- youtube | instagram
  external_id TEXT, name TEXT, handle TEXT, avatar TEXT,
  token_file TEXT, token_expires REAL,
  tags_json TEXT DEFAULT '[]', windows_json TEXT DEFAULT '["10:00","13:00","16:00","19:00","22:00"]',
  daily_cap INTEGER DEFAULT 4, active INTEGER DEFAULT 1,
  status TEXT DEFAULT 'ok', error TEXT,
  followers INTEGER DEFAULT 0, views_total INTEGER DEFAULT 0,
  created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  clip_id INTEGER NOT NULL, account_id INTEGER NOT NULL,
  scheduled_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',        -- queued | uploading | scheduled | published | failed | cancelled
  title TEXT, description TEXT, hashtags_json TEXT DEFAULT '[]',
  external_id TEXT, url TEXT, error TEXT, attempts INTEGER DEFAULT 0,
  views INTEGER DEFAULT 0, likes INTEGER DEFAULT 0, comments INTEGER DEFAULT 0,
  stats_at REAL, created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, color TEXT,
  tags_json TEXT DEFAULT '[]', windows_json TEXT DEFAULT '["10:00","13:00","16:00","19:00","22:00"]',
  daily_cap INTEGER DEFAULT 4, created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, url TEXT,
  rate REAL DEFAULT 0, budget REAL DEFAULT 0,
  hashtags_json TEXT DEFAULT '[]', mentions_json TEXT DEFAULT '[]', streamers_json TEXT DEFAULT '[]',
  accounts_json TEXT DEFAULT '[]', platforms_json TEXT DEFAULT '["youtube","instagram"]',
  min_seconds REAL DEFAULT 0, max_seconds REAL DEFAULT 0,
  status TEXT DEFAULT 'active', ends_at REAL, notes TEXT, created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS watch (
  id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, platform TEXT, channel TEXT, name TEXT, avatar TEXT,
  priority INTEGER DEFAULT 5, auto INTEGER DEFAULT 1, category TEXT,
  live INTEGER DEFAULT 0, viewers INTEGER DEFAULT 0, title TEXT, game TEXT, last_live REAL, source_id INTEGER,
  created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS sites (
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, name TEXT NOT NULL, url TEXT, notes TEXT,
  enabled INTEGER DEFAULT 0, custom INTEGER DEFAULT 0, accounts_json TEXT DEFAULT '[]',
  created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, ref_id INTEGER, day TEXT,
  views INTEGER DEFAULT 0, followers INTEGER DEFAULT 0, likes INTEGER DEFAULT 0,
  created_at REAL, updated_at REAL, UNIQUE(kind, ref_id, day)
);
CREATE INDEX IF NOT EXISTS ix_clips_status ON clips(status);
CREATE INDEX IF NOT EXISTS ix_posts_status ON posts(status, scheduled_at);
CREATE INDEX IF NOT EXISTS ix_posts_account ON posts(account_id, scheduled_at);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.c = c
    return c


def init() -> None:
    with _write:
        conn().executescript(SCHEMA)
        for table, cols in EXTRA_COLUMNS.items():
            have = {r[1] for r in conn().execute(f"PRAGMA table_info({table})").fetchall()}
            for col, decl in cols.items():
                if col not in have:
                    conn().execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        conn().commit()
        # anything mid-flight when the app last closed can't resume: mark it
        conn().execute("UPDATE sources SET status='stopped' WHERE status IN ('starting','live','analyzing','rendering')")
        conn().execute("UPDATE clips SET status='failed', error='app closed while processing' "
                       "WHERE status IN ('cutting','transcribing','writing','rendering')")
        conn().execute("UPDATE posts SET status='queued' WHERE status='uploading'")
        conn().commit()


def _decode(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in list(d):
        if k.endswith("_json"):
            try:
                d[k[:-5]] = json.loads(d.pop(k) or "null")
            except json.JSONDecodeError:
                d[k[:-5]] = None
    return d


def _encode(values: dict) -> dict:
    out = {}
    for k, v in values.items():
        col = k if k.endswith("_json") else (f"{k}_json" if k in JSON_FIELDS else k)
        out[col] = json.dumps(v, ensure_ascii=False) if col.endswith("_json") else v
    return out


JSON_FIELDS = {"settings", "stats", "signals", "chat", "words", "hashtags", "variants", "cam", "tags", "windows", "fx",
               "mentions", "streamers", "accounts", "platforms"}

EXTRA_COLUMNS = {
    "clips": {"fx_json": "TEXT DEFAULT '{}'"},
    "accounts": {"group_id": "INTEGER", "warmup": "INTEGER DEFAULT 0", "warmup_started": "REAL",
                 "last_check": "REAL", "last_ok": "REAL", "last_post_at": "REAL"},
    "posts": {"campaign_id": "INTEGER", "submit_status": "TEXT", "submitted_at": "REAL"},
    "campaigns": {"site_id": "INTEGER", "campaign_url": "TEXT", "submit_url": "TEXT",
                  "submit_mode": "TEXT DEFAULT 'link'", "code": "TEXT", "dedicated": "INTEGER DEFAULT 1"},
}


def all(sql: str, args: tuple | list = ()) -> list[dict]:
    return [_decode(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args: tuple | list = ()) -> dict | None:
    return _decode(conn().execute(sql, args).fetchone())


def scalar(sql: str, args: tuple | list = ()) -> Any:
    r = conn().execute(sql, args).fetchone()
    return r[0] if r else None


def insert(table: str, values: dict) -> int:
    now = time.time()
    values = _encode({"created_at": now, "updated_at": now, **values})
    cols = ",".join(values)
    with _write:
        cur = conn().execute(f"INSERT INTO {table} ({cols}) VALUES ({','.join('?' * len(values))})",
                             list(values.values()))
        conn().commit()
        return int(cur.lastrowid)


def update(table: str, row_id: int, values: dict) -> None:
    values = _encode({**values, "updated_at": time.time()})
    sets = ",".join(f"{k}=?" for k in values)
    with _write:
        conn().execute(f"UPDATE {table} SET {sets} WHERE id=?", [*values.values(), row_id])
        conn().commit()


def execute(sql: str, args: tuple | list = ()) -> None:
    with _write:
        conn().execute(sql, args)
        conn().commit()


def get(table: str, row_id: int) -> dict | None:
    return one(f"SELECT * FROM {table} WHERE id=?", (row_id,))
