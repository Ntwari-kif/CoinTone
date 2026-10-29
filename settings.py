"""Runtime settings (editable from the Admin panel), encrypted credential vault, and audit log.
Settings live in the database (app_settings table) and override the defaults in config.py."""
import base64, hashlib, json, os, time
import config, db

_cache = {"t": 0, "v": {}}
_fernet = None

def defaults():
    return {"fee": str(config.SUBSCRIPTION_FEE), "currency": config.CURRENCY, "pay_number": config.PAYMENT_NUMBER,
            "pay_name": config.PAYMENT_NAME, "auto_activate": "1", "trust_pasted": "0", "sms_max_age_h": "72",
            "maintenance": "0", "announcement": "", "pay_network": "", "country_code": "", "sms_formats": "[]"}

def _load():
    if time.time() - _cache["t"] > 5:
        v = defaults()
        for r in db.q("SELECT name,val FROM app_settings"):
            if r["name"] in v: v[r["name"]] = r["val"]
        _cache.update(t=time.time(), v=v)
    return _cache["v"]

def get(name): return _load().get(name, defaults().get(name, ""))
def flag(name): return str(get(name)) in ("1", "true", "on", "yes")
def fee():
    try: return max(1, int(float(get("fee"))))
    except ValueError: return config.SUBSCRIPTION_FEE
def put(name, val):
    db.x("INSERT INTO app_settings(name,val) VALUES(?,?) ON CONFLICT (name) DO UPDATE SET val=EXCLUDED.val", (name, str(val)), ret=False)
    _cache["t"] = 0
def snapshot(): return dict(_load())

# ---------------------------------------------------------------- vault (credentials are encrypted at rest)
def _f():
    global _fernet
    if _fernet is None:
        from cryptography.fernet import Fernet
        k = os.environ.get("PV_VAULT_KEY") or db.secret_key()
        _fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(("ctone-vault:" + k).encode()).digest()))
    return _fernet

def seal(d): return _f().encrypt(json.dumps(d).encode()).decode()
def unseal(t):
    try: return json.loads(_f().decrypt(t.encode()).decode())
    except Exception: return {}

def integ(name):
    """Decrypted credentials of one connection ({} if not saved or switched off)."""
    r = db.q("SELECT * FROM integrations WHERE name=?", (name,), one=True)
    return unseal(r["data"]) if r and r["enabled"] else {}

def integ_save(name, data, enabled=True):
    old = db.q("SELECT data FROM integrations WHERE name=?", (name,), one=True)
    cur = unseal(old["data"]) if old else {}
    cur.update({k: v for k, v in data.items() if v not in (None, "")})   # blank = keep the saved value
    db.x("INSERT INTO integrations(name,data,enabled,updated_at) VALUES(?,?,?,?) ON CONFLICT (name) DO UPDATE SET data=EXCLUDED.data, enabled=EXCLUDED.enabled, updated_at=EXCLUDED.updated_at",
         (name, seal(cur), 1 if enabled else 0, int(time.time() * 1000)), ret=False)

def audit(actor, action, detail=""):
    try: db.x("INSERT INTO audit_log(ts,actor,action,detail) VALUES(?,?,?,?)", (int(time.time() * 1000), actor, action, str(detail)[:400]), ret=False)
    except Exception: pass
