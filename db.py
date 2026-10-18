"""SQLite database layer (real, file-based, WAL mode so all panels can work at once)."""
import os, sqlite3
from flask import g
import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role TEXT NOT NULL CHECK(role IN('admin','owner','cashier')),
  name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, phone TEXT DEFAULT '',
  password_hash TEXT NOT NULL,
  shop_id INTEGER,
  status TEXT NOT NULL DEFAULT 'active',      -- cashier: pending/approved/rejected/revoked
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS shops(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, code TEXT NOT NULL UNIQUE, owner_id INTEGER,
  currency TEXT NOT NULL, branches TEXT NOT NULL, channels TEXT NOT NULL,
  big_amount REAL NOT NULL DEFAULT 50000, stale_minutes INTEGER NOT NULL DEFAULT 30,
  target REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',      -- active / suspended (by admin)
  expires_at INTEGER,                         -- NULL = never paid
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sub_payments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shop_id INTEGER NOT NULL, amount REAL NOT NULL, period_days INTEGER NOT NULL,
  method TEXT DEFAULT '', reference TEXT NOT NULL, sms TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',     -- pending / approved / rejected
  note TEXT DEFAULT '', created_at INTEGER NOT NULL, reviewed_at INTEGER);
CREATE TABLE IF NOT EXISTS transactions(
  id TEXT PRIMARY KEY, shop_id INTEGER NOT NULL, branch TEXT, cashier_id INTEGER, cashier_name TEXT,
  method TEXT, provider TEXT, payer TEXT, phone TEXT, code TEXT,
  amount REAL NOT NULL, currency TEXT, status TEXT NOT NULL, flags TEXT DEFAULT '[]', note TEXT DEFAULT '',
  sms TEXT DEFAULT '', sms_t INTEGER DEFAULT 0, expected REAL DEFAULT 0, reviewed INTEGER DEFAULT 0,
  created_at INTEGER NOT NULL, day TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_tx_shop_day ON transactions(shop_id, day);
CREATE INDEX IF NOT EXISTS ix_tx_shop_code ON transactions(shop_id, code);
CREATE INDEX IF NOT EXISTS ix_users_shop ON users(shop_id);
"""

def _connect():
    c = sqlite3.connect(config.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c

def get():
    if "db" not in g:
        g.db = _connect()
    return g.db

def close(_=None):
    d = g.pop("db", None)
    if d: d.close()

def init():
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    c = _connect(); c.executescript(SCHEMA); c.commit(); c.close()

def q(sql, args=(), one=False):
    cur = get().execute(sql, args); rows = cur.fetchall(); cur.close()
    return (rows[0] if rows else None) if one else rows

def x(sql, args=()):
    d = get(); cur = d.execute(sql, args); d.commit(); return cur.lastrowid
