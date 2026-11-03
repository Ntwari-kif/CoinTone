"""All settings live here. Override any of them with environment variables."""
import os, secrets

BASE = os.path.dirname(os.path.abspath(__file__))
# Supabase: paste the "Session pooler" connection string here (as an environment variable on Render)
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
TIMEZONE = os.environ.get("PV_TZ", "Africa/Nairobi")     # your shop's timezone, e.g. Africa/Dar_es_Salaam, Africa/Kampala
DB_PATH = os.environ.get("PV_DB", os.path.join(BASE, "instance", "payverify.db"))

# ---- Subscription -------------------------------------------------------
SUBSCRIPTION_FEE = int(os.environ.get("PV_FEE", 5000))   # per month
PERIOD_DAYS = 30                                         # days bought by one fee
CURRENCY = os.environ.get("PV_CURRENCY", "KES")          # change to TZS, UGX, GHS...
# Where shop owners send their subscription money (shown on their panel)
PAYMENT_NUMBER = os.environ.get("PV_PAY_NUMBER", "0792032617")
PAYMENT_NAME = os.environ.get("PV_PAY_NAME", "CTone Admin")

# ---- First admin account (created automatically on first start) ---------
ADMIN_EMAIL = os.environ.get("PV_ADMIN_EMAIL", "admin@payverify.local")
ADMIN_PASSWORD = os.environ.get("PV_ADMIN_PASSWORD", "admin123")

