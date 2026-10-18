"""All settings live here. Override any of them with environment variables."""
import os, secrets

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("PV_DB", os.path.join(BASE, "instance", "payverify.db"))

# ---- Subscription -------------------------------------------------------
SUBSCRIPTION_FEE = int(os.environ.get("PV_FEE", 5000))   # per month
PERIOD_DAYS = 30                                         # days bought by one fee
CURRENCY = os.environ.get("PV_CURRENCY", "KES")          # change to TZS, UGX, GHS...
# Where shop owners send their subscription money (shown on their panel)
PAYMENT_NUMBER = os.environ.get("PV_PAY_NUMBER", "0700 000 000")
PAYMENT_NAME = os.environ.get("PV_PAY_NAME", "PayVerify Admin")

# ---- First admin account (created automatically on first start) ---------
ADMIN_EMAIL = os.environ.get("PV_ADMIN_EMAIL", "admin@payverify.local")
ADMIN_PASSWORD = os.environ.get("PV_ADMIN_PASSWORD", "admin123")

def secret_key():
    """Persistent random key for signing login cookies."""
    p = os.path.join(BASE, "instance", "secret.key")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(secrets.token_hex(32))
    return open(p).read().strip()
