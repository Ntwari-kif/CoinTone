"""Database layer.
 - DATABASE_URL set  -> Supabase / PostgreSQL (permanent, lives outside Render)
 - not set           -> local SQLite file (handy for testing on your own computer)
Everything else in the app uses only q() and x(), so it does not care which one is running."""
import os, secrets, sqlite3, threading
from contextlib import contextmanager
from flask import g
import config

PG = bool(config.DATABASE_URL)
BACKEND = "postgres" if PG else "sqlite"
_pool, _ready, _lock = None, False, threading.Lock()

TABLES = ["users", "shops", "sub_payments", "transactions", "app_settings", "sms_inbox", "integrations", "audit_log", "pay_requests"]
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id {PK},
  role TEXT NOT NULL CHECK(role IN('admin','owner','cashier')),
  name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, phone TEXT DEFAULT '',
  password_hash TEXT NOT NULL,
  shop_id BIGINT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS shops(
  id {PK},
  name TEXT NOT NULL, code TEXT NOT NULL UNIQUE, owner_id BIGINT,
  currency TEXT NOT NULL, branches TEXT NOT NULL, channels TEXT NOT NULL,
  big_amount {REAL} NOT NULL DEFAULT 50000, stale_minutes INTEGER NOT NULL DEFAULT 30,
  target {REAL} NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  expires_at BIGINT,
  created_at BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS sub_payments(
  id {PK},
  shop_id BIGINT NOT NULL, amount {REAL} NOT NULL, period_days INTEGER NOT NULL,
  method TEXT DEFAULT '', reference TEXT NOT NULL, sms TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  note TEXT DEFAULT '', created_at BIGINT NOT NULL, reviewed_at BIGINT);
CREATE TABLE IF NOT EXISTS transactions(
  id TEXT PRIMARY KEY, shop_id BIGINT NOT NULL, branch TEXT, cashier_id BIGINT, cashier_name TEXT,
  method TEXT, provider TEXT, payer TEXT, phone TEXT, code TEXT,
  amount {REAL} NOT NULL, currency TEXT, status TEXT NOT NULL, flags TEXT DEFAULT '[]', note TEXT DEFAULT '',
  sms TEXT DEFAULT '', sms_t BIGINT DEFAULT 0, expected {REAL} DEFAULT 0, reviewed INTEGER DEFAULT 0,
  created_at BIGINT NOT NULL, day TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_settings(name TEXT PRIMARY KEY, val TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_tx_shop_day ON transactions(shop_id, day);
CREATE INDEX IF NOT EXISTS ix_tx_shop_code ON transactions(shop_id, code);
CREATE INDEX IF NOT EXISTS ix_users_shop ON users(shop_id);
CREATE TABLE IF NOT EXISTS sms_inbox(
  id {PK}, received_at BIGINT NOT NULL, sender TEXT DEFAULT '', body TEXT NOT NULL, provider TEXT DEFAULT '',
  verdict TEXT DEFAULT '', score INTEGER DEFAULT 0, reasons TEXT DEFAULT '[]', amount {REAL} DEFAULT 0, currency TEXT DEFAULT '',
  code TEXT DEFAULT '', code_key TEXT DEFAULT '', payer TEXT DEFAULT '', phone TEXT DEFAULT '', direction TEXT DEFAULT '',
  status TEXT DEFAULT 'new', used_by BIGINT, sms_time BIGINT DEFAULT 0);
CREATE TABLE IF NOT EXISTS integrations(
  name TEXT PRIMARY KEY, data TEXT NOT NULL, enabled INTEGER DEFAULT 1, updated_at BIGINT NOT NULL,
  last_test_at BIGINT DEFAULT 0, last_ok INTEGER DEFAULT 0, last_msg TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS audit_log(id {PK}, ts BIGINT NOT NULL, actor TEXT DEFAULT '', action TEXT DEFAULT '', detail TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS pay_requests(
  id TEXT PRIMARY KEY, shop_id BIGINT NOT NULL, provider TEXT, phone TEXT, amount {REAL}, status TEXT DEFAULT 'pending',
  ext_ref TEXT DEFAULT '', detail TEXT DEFAULT '', created_at BIGINT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_sms_key ON sms_inbox(code_key)
"""

class Conn:
    """Same q()/x() calls for both databases."""
    def __init__(self, raw): self.raw = raw
    @staticmethod
    def _s(sql): return sql.replace("?", "%s") if PG else sql
    def q(self, sql, args=(), one=False):
        cur = self.raw.execute(self._s(sql), tuple(args)); rows = cur.fetchall(); cur.close()
        return (rows[0] if rows else None) if one else rows
    def x(self, sql, args=(), ret=True):
        s = self._s(sql); ins = ret and s.lstrip().upper().startswith("INSERT")
        try:
            if PG:
                cur = self.raw.execute(s + (" RETURNING id" if ins else ""), tuple(args))
                row = cur.fetchone() if ins else None; rid = row["id"] if row else None
            else:
                cur = self.raw.execute(s, tuple(args)); rid = cur.lastrowid if ins else None
            self.raw.commit(); return rid
        except Exception:
            self.raw.rollback(); raise

    def n(self, sql, args=()):
        """Run an UPDATE/DELETE and return how many rows it changed (used for race-free 'claim once' updates)."""
        try:
            cur = self.raw.execute(self._s(sql), tuple(args)); k = cur.rowcount; self.raw.commit(); return k
        except Exception:
            self.raw.rollback(); raise

def _sqlite():
    c = sqlite3.connect(config.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row; c.execute("PRAGMA journal_mode=WAL"); return c

@contextmanager
def session():
    """A connection outside of a web request (startup jobs)."""
    c = Conn(_pool.getconn() if PG else _sqlite())
    try: yield c
    finally: _pool.putconn(c.raw) if PG else c.raw.close()

def get():
    if "db" not in g: g.db = Conn(_pool.getconn() if PG else _sqlite())
    return g.db

def close(_=None):
    d = g.pop("db", None)
    if d: _pool.putconn(d.raw) if PG else d.raw.close()

def q(*a, **k): return get().q(*a, **k)
def x(*a, **k): return get().x(*a, **k)
def n(*a, **k): return get().n(*a, **k)

def init():
    """Connect and create the tables if they don't exist yet (safe to run every start)."""
    global _pool, _ready
    with _lock:
        if _ready: return
        if PG:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
            kw = {"row_factory": dict_row, "prepare_threshold": None, "connect_timeout": 15}   # works with Supabase poolers
            if "sslmode" not in config.DATABASE_URL: kw["sslmode"] = "require"
            _pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=int(os.environ.get("PV_POOL", 8)), kwargs=kw,
                                   open=False, check=ConnectionPool.check_connection, name="ctone")
            try: _pool.open(wait=True, timeout=30)
            except Exception as e:
                print("\n  !! Could not connect to Supabase. Check DATABASE_URL: use the *Session pooler* string, with your real\n"
                      "  !! password (letters/numbers only) in place of [YOUR-PASSWORD], no brackets.\n  !! Error:", e, "\n"); raise
        else:
            os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
        with session() as c:
            for st in SCHEMA.format(PK="BIGSERIAL PRIMARY KEY" if PG else "INTEGER PRIMARY KEY AUTOINCREMENT",
                                    REAL="DOUBLE PRECISION" if PG else "REAL").split(";\n"):
                if st.strip(): c.x(st, ret=False)
            if PG:   # lock the tables against Supabase's public API: only this server (direct login) can read them
                for t in TABLES: c.x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY", ret=False)
            try: c.x("ALTER TABLE sub_payments ADD COLUMN " + ("IF NOT EXISTS " if PG else "") + "source TEXT DEFAULT 'manual'", ret=False)
            except Exception: pass          # column already there
        _ready = True

def secret_key():
    """Login-cookie signing key, kept in the database so users stay signed in across restarts."""
    if os.environ.get("PV_SECRET_KEY"): return os.environ["PV_SECRET_KEY"]
    with session() as c:
        r = c.q("SELECT val FROM app_settings WHERE name='secret_key'", one=True)
        if not r:
            c.x("INSERT INTO app_settings(name,val) VALUES('secret_key',?) ON CONFLICT (name) DO NOTHING", (secrets.token_hex(32),), ret=False)
            r = c.q("SELECT val FROM app_settings WHERE name='secret_key'", one=True)
        return r["val"]
