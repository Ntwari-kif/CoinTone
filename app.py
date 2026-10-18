"""PayVerify – multi-panel mobile-money verification platform.
Run:  python app.py        (then open http://localhost:5000)
"""
import csv, hashlib, io, json, os, re, secrets, time, datetime as dt
from functools import wraps
from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, send_from_directory, session
from jinja2 import ChoiceLoader, FileSystemLoader
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
import config, db, sms

app = Flask(__name__, static_folder=None)
# Works with the normal layout (templates/ and static/ folders) AND with a flat layout (all files in one folder).
app.jinja_loader = ChoiceLoader([FileSystemLoader(os.path.join(config.BASE, "templates")), FileSystemLoader(config.BASE)])
ASSETS = {"style.css", "common.js", "theme.js", "pwa.js", "icon-192.png", "icon-512.png", "icon-maskable.png"}
@app.get("/static/<filename>", endpoint="static")      # makes {{ url_for('static', filename='style.css') }} work
def assets(filename):
    if filename not in ASSETS: abort(404)
    return send_from_directory(_find(filename), filename, max_age=0)   # always revalidate -> no stale CSS
def _find(name):
    for d in (os.path.join(config.BASE, "static"), config.BASE):
        if os.path.exists(os.path.join(d, name)): return d
    abort(404)

@app.get("/sw.js")                                   # service worker must live at the site root to control every page
def service_worker():
    r = send_from_directory(_find("sw.js"), "sw.js", mimetype="application/javascript", max_age=0)
    r.headers["Service-Worker-Allowed"] = "/"; return r

@app.get("/manifest.webmanifest", endpoint="manifest")
def manifest():
    icon = lambda f, s, p: {"src": "/static/" + f, "sizes": f"{s}x{s}", "type": "image/png", "purpose": p}
    m = {"name": "CTone – Payment Verifier", "short_name": "CTone", "description": "Verify mobile-money payments and manage your shop.",
         "id": "/", "start_url": "/", "scope": "/", "display": "standalone", "orientation": "portrait",
         "background_color": "#f3f8f2", "theme_color": "#0a1a3f",
         "icons": [icon("icon-192.png", 192, "any"), icon("icon-512.png", 512, "any"), icon("icon-maskable.png", 512, "maskable")]}
    return Response(json.dumps(m), mimetype="application/manifest+json")

@app.get("/offline")
def offline(): return render_template("offline.html")

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)   # correct client IP / https behind Render's proxy
app.secret_key = config.secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),
                  PERMANENT_SESSION_LIFETIME=dt.timedelta(days=14))
app.teardown_appcontext(db.close)

DAY = 86400000
now_ms = lambda: int(time.time() * 1000)
day_of = lambda ms: dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")
money = lambda n, c: f"{c} {n:,.0f}" if float(n).is_integer() else f"{c} {n:,.2f}"
err = lambda m, code=400: (jsonify(error=m), code)

# ---------------------------------------------------------------- helpers
def shop_out(s):
    d = dict(s); d["branches"] = json.loads(d["branches"]); d["channels"] = json.loads(d["channels"]); return d

def sub_info(s):
    """Subscription state of a shop row."""
    left = None if not s["expires_at"] else (s["expires_at"] - now_ms()) / DAY
    if s["status"] == "suspended": st = "suspended"
    elif left is None: st = "unpaid"
    elif left <= 0: st = "expired"
    elif left <= 7: st = "expiring"
    else: st = "active"
    return {"state": st, "expires_at": s["expires_at"], "days_left": None if left is None else max(0, int(left + .999)),
            "fee": config.SUBSCRIPTION_FEE, "period_days": config.PERIOD_DAYS, "currency": config.CURRENCY,
            "pay_number": config.PAYMENT_NUMBER, "pay_name": config.PAYMENT_NAME}

locked = lambda s: sub_info(s)["state"] in ("unpaid", "expired", "suspended")

def tx_out(r):
    d = dict(r); d["flags"] = json.loads(d["flags"] or "[]"); d["reviewed"] = bool(d["reviewed"]); return d

def auth(*roles, active=False):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            u = db.q("SELECT * FROM users WHERE id=?", (session.get("uid"),), one=True) if session.get("uid") else None
            if not u: return err("Please sign in again.", 401)
            if roles and u["role"] not in roles: return err("Not allowed.", 403)
            if u["role"] == "cashier" and u["status"] != "approved":
                session.clear(); return err("Your access was removed by the shop owner.", 401)
            shop = db.q("SELECT * FROM shops WHERE id=?", (u["shop_id"],), one=True) if u["shop_id"] else None
            if active and shop and locked(shop):
                return err("This shop's subscription is not active. Ask the owner to renew.", 402)
            g.user, g.shop = u, shop
            return f(*a, **k)
        return w
    return deco

def page(role, tpl):
    u = db.q("SELECT * FROM users WHERE id=?", (session.get("uid"),), one=True) if session.get("uid") else None
    if not u or u["role"] != role or (role == "cashier" and u["status"] != "approved"): return redirect("/login")
    return render_template(tpl)

FAILS = {}
def throttled(key):
    t = time.time(); FAILS[key] = [x for x in FAILS.get(key, []) if t - x < 600]; return len(FAILS[key]) >= 8

def shop_code():
    while True:
        c = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        if not db.q("SELECT 1 FROM shops WHERE code=?", (c,), one=True): return c

def clean(v, n=120): return (v or "").strip()[:n]
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def new_user(d, role, shop_id, status):
    name, email, pw = clean(d.get("name")), clean(d.get("email")).lower(), d.get("password") or ""
    if not name: return None, "Enter your name."
    if not EMAIL.match(email): return None, "Enter a valid email."
    if len(pw) < 6: return None, "Password must be at least 6 characters."
    if db.q("SELECT 1 FROM users WHERE email=?", (email,), one=True): return None, "That email is already registered."
    return db.x("INSERT INTO users(role,name,email,phone,password_hash,shop_id,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (role, name, email, clean(d.get("phone"), 30), generate_password_hash(pw), shop_id, status, now_ms())), None

# ---------------------------------------------------------------- pages (5 separate panels)
@app.get("/")
def home():
    u = db.q("SELECT role FROM users WHERE id=?", (session.get("uid"),), one=True) if session.get("uid") else None
    return redirect("/" + u["role"] if u else "/login")
@app.get("/login")
def p_login(): return render_template("login.html")
@app.get("/register")
def p_register(): return render_template("register.html")
@app.get("/admin")
def p_admin(): return page("admin", "admin.html")
@app.get("/owner")
def p_owner(): return page("owner", "owner.html")
@app.get("/cashier")
def p_cashier(): return page("cashier", "cashier.html")

# ---------------------------------------------------------------- auth API
@app.post("/api/register/owner")
def reg_owner():
    d = request.get_json(silent=True) or {}
    sname = clean(d.get("shop_name"))
    if not sname: return err("Enter your shop name.")
    uid, e = new_user(d, "owner", None, "active")
    if e: return err(e)
    sid = db.x("INSERT INTO shops(name,code,owner_id,currency,branches,channels,created_at) VALUES(?,?,?,?,?,?,?)",
               (sname, shop_code(), uid, config.CURRENCY, json.dumps(["Main"]), json.dumps(sms.CHANNELS), now_ms()))
    db.x("UPDATE users SET shop_id=? WHERE id=?", (sid, uid))
    session.clear(); session["uid"] = uid; session.permanent = True
    return jsonify(ok=True, redirect="/owner")

@app.post("/api/register/cashier")
def reg_cashier():
    d = request.get_json(silent=True) or {}
    shop = db.q("SELECT * FROM shops WHERE code=?", (clean(d.get("shop_code")).upper(),), one=True)
    if not shop: return err("Shop code not found. Ask the shop owner for the 6-letter code.")
    uid, e = new_user(d, "cashier", shop["id"], "pending")
    if e: return err(e)
    return jsonify(ok=True, message=f"Request sent to {shop['name']}. You can sign in once the owner approves you.")

@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    email, key = clean(d.get("email")).lower(), f"{request.remote_addr}|{clean(d.get('email')).lower()}"
    if throttled(key): return err("Too many attempts. Try again in 10 minutes.", 429)
    u = db.q("SELECT * FROM users WHERE email=?", (email,), one=True)
    if not u or not check_password_hash(u["password_hash"], d.get("password") or ""):
        FAILS.setdefault(key, []).append(time.time()); return err("Wrong email or password.", 401)
    if u["role"] == "cashier":
        msg = {"pending": "Your request is waiting for the shop owner to approve you.",
               "rejected": "The shop owner declined your request.",
               "revoked": "The shop owner removed your access."}.get(u["status"])
        if msg: return err(msg, 403)
    session.clear(); session["uid"] = u["id"]; session.permanent = True
    return jsonify(ok=True, redirect="/" + u["role"])

@app.post("/api/logout")
def logout(): session.clear(); return jsonify(ok=True)

@app.post("/api/password")
@auth()
def change_pw():
    d = request.get_json(silent=True) or {}
    if not check_password_hash(g.user["password_hash"], d.get("old") or ""): return err("Current password is wrong.")
    if len(d.get("new") or "") < 6: return err("New password must be at least 6 characters.")
    db.x("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(d["new"]), g.user["id"]))
    return jsonify(ok=True)

# ---------------------------------------------------------------- transactions core
FLAG_TITLES = {"verified": ("ok", "PAYMENT VERIFIED"), "review": ("chk", "CHECK BEFORE RELEASING GOODS"), "failed": ("bad", "PAYMENT ISSUE")}

def add_tx(shop, **o):
    t = now_ms()
    r = dict(id=secrets.token_hex(6), shop_id=shop["id"], branch=shop["branches"][0], cashier_id=g.user["id"],
             cashier_name=g.user["name"], method="mobile", provider="", payer="Walk-in customer", phone="", code="",
             amount=0, currency=shop["currency"], status="verified", flags=[], note="", sms="", sms_t=0, expected=0,
             reviewed=0, created_at=t, day=day_of(t))
    r.update(o); r["flags"] = json.dumps(r["flags"])
    db.x(f"INSERT INTO transactions({','.join(r)}) VALUES({','.join('?' * len(r))})", tuple(r.values()))
    return tx_out(db.q("SELECT * FROM transactions WHERE id=?", (r["id"],), one=True))

def is_dupe(shop_id, code):
    return bool(code) and bool(db.q("SELECT 1 FROM transactions WHERE shop_id=? AND code=? AND status NOT IN('failed','void')", (shop_id, code), one=True))

def branch_of(S, d): return d.get("branch") if d.get("branch") in S["branches"] else S["branches"][0]

def reply(tx):
    lvl, title = FLAG_TITLES[tx["status"]]
    if tx["method"] == "cash" and lvl == "ok": title = "CASH RECORDED"
    elif tx["method"] != "mobile" and lvl == "ok": title = "PAYMENT RECORDED"
    if tx["status"] == "review" and "manual" in tx["flags"]: title = "SENT FOR REVIEW"
    sub = tx["note"] if lvl != "ok" else " · ".join(x for x in (tx["payer"], tx["provider"], tx["code"]) if x)
    return jsonify(ok=True, level=lvl, title=title, amount=money(tx["amount"], tx["currency"]), sub=sub, tx=tx)

@app.post("/api/cashier/verify")
@auth("cashier", active=True)
def verify():
    d = request.get_json(silent=True) or {}
    raw = clean(d.get("sms"), 2000)
    if not raw: return err("Paste the payment SMS first.")
    S = shop_out(g.shop); cur = S["currency"]
    mobile = [c for c in S["channels"] if sms.CHANNELS.index(c) < 5] if all(c in sms.CHANNELS for c in S["channels"]) else []
    p = sms.parse(raw, mobile[0] if mobile else "Mobile money")
    if not p["ok"]:
        return jsonify(ok=True, level="bad", title="CAN'T READ MESSAGE", amount="—", sub=p["why"] + " Use a manual entry if the payment is real.")
    if p["prov"] not in S["channels"]:
        return jsonify(ok=True, level="bad", title="CHANNEL NOT ENABLED", amount="—", sub=f"{p['prov']} isn't switched on for this shop. Ask the owner to enable it.")
    try: exp = float(d.get("expected") or 0)
    except ValueError: exp = 0
    fl, st, note = [], "verified", ""
    code = p["code"] or "TXT-" + hashlib.sha1(p["raw"].encode()).hexdigest()[:8].upper()
    c2 = p["cur"] or cur
    if not p["code"]: fl.append("nocode"); st = "review"; note = "No transaction code found in the SMS"
    if p["failed"]: st = "failed"; note = "The provider's message says this payment failed or was reversed"
    if is_dupe(S["id"], code): st = "failed"; fl.append("duplicate"); note = "This transaction code was already used"
    if exp and p["amt"] < exp - .005 and st != "failed":
        st = "failed"; fl.append("mismatch"); note = f"Short by {money(exp - p['amt'], c2)} (expected {money(exp, cur)})"
    if exp and p["amt"] > exp + .005:
        fl.append("overpaid"); note = (note + ". " if note else "") + f"Overpaid by {money(p['amt'] - exp, c2)}"
    if p["cur"] and p["cur"] != cur:
        fl.append("currency"); st = "review" if st == "verified" else st; note = (note + ". " if note else "") + f"SMS currency is {p['cur']}"
    if p["t"] and abs(now_ms() - p["t"]) > S["stale_minutes"] * 60000:
        fl.append("stale"); st = "review" if st == "verified" else st
        note = (note + ". " if note else "") + "SMS is dated " + dt.datetime.fromtimestamp(p["t"] / 1000).strftime("%d %b %Y %H:%M")
    if p["amt"] >= S["big_amount"]: fl.append("large")
    return reply(add_tx(S, branch=branch_of(S, d), payer=p["payer"], phone=p["phone"], amount=p["amt"], currency=c2, code=code,
                        provider=p["prov"], status=st, flags=fl, note=note, sms=p["raw"], sms_t=p["t"], expected=exp))

@app.post("/api/cashier/manual")
@auth("cashier", active=True)
def manual():
    d = request.get_json(silent=True) or {}
    S, m = shop_out(g.shop), d.get("method")
    if m not in ("cash", "card", "bank", "mobile"): return err("Unknown payment type.")
    try: a = float(d.get("amount"))
    except (TypeError, ValueError): return err("Enter the amount.")
    if not a > 0: return err("Enter the amount.")
    fl, st, note, code, prov = [], "verified", "", "", ""
    ref = clean(d.get("ref"), 60).upper()
    if m == "mobile":
        prov = next((c for c in S["channels"] if c in sms.CHANNELS[:5]), "Mobile money"); code = ref
        fl.append("manual"); st = "review"; note = "Typed in by cashier – awaiting owner approval"
        if code and is_dupe(S["id"], code): fl.append("duplicate"); st = "failed"; note = "This transaction code was already used"
    elif m != "cash":
        code = ref
        if not ref: fl.append("noref"); st = "review"; note = "No reference given"
    if a >= S["big_amount"]: fl.append("large")
    return reply(add_tx(S, branch=branch_of(S, d), method=m, amount=a, payer=clean(d.get("payer")) or "Walk-in customer",
                        code=code, provider=prov, status=st, flags=fl, note=note))

@app.get("/api/cashier/state")
@auth("cashier")
def cashier_state():
    S = shop_out(g.shop); sub = sub_info(g.shop)
    out = dict(user={"name": g.user["name"]}, shop={k: S[k] for k in ("name", "currency", "branches", "channels")}, locked=locked(g.shop), sub_state=sub["state"])
    if not out["locked"]:
        out["tx"] = [tx_out(r) for r in db.q("SELECT * FROM transactions WHERE shop_id=? AND day=? ORDER BY created_at DESC LIMIT 300", (S["id"], day_of(now_ms())))]
    return jsonify(out)

# ---------------------------------------------------------------- owner API
def alerts_for(txs):
    out, manual_n = [], {}
    for x in sorted(txs, key=lambda x: x["created_at"]):
        r = list(x["flags"])
        if x["status"] == "void" and "void" not in r: r.append("void")
        if r and not x["reviewed"]: out.append({"id": x["id"], "flags": r})
        if "manual" in x["flags"]: manual_n[x["cashier_name"]] = manual_n.get(x["cashier_name"], 0) + 1
    pats = [f"{c} made {n} manual entries today – a pattern worth checking." for c, n in manual_n.items() if n >= 3]
    return out, pats

@app.get("/api/owner/overview")
@auth("owner")
def owner_overview():
    s = g.shop; S = shop_out(s); sub = sub_info(s)
    day = request.args.get("day") or day_of(now_ms())
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day): day = day_of(now_ms())
    out = dict(user={"name": g.user["name"], "email": g.user["email"]}, shop={k: S[k] for k in ("name", "code", "currency", "branches", "channels", "big_amount", "stale_minutes", "target")},
               sub=sub, locked=locked(s), all_channels=sms.CHANNELS, day=day,
               payments=[dict(r) for r in db.q("SELECT * FROM sub_payments WHERE shop_id=? ORDER BY created_at DESC LIMIT 20", (s["id"],))])
    if out["locked"]: return jsonify(out)
    txs = [tx_out(r) for r in db.q("SELECT * FROM transactions WHERE shop_id=? AND day=? ORDER BY created_at DESC", (s["id"], day))]
    a, p = alerts_for(txs)
    wk0 = day_of(now_ms() - 6 * DAY)
    out.update(tx=txs[:300], alerts=a, patterns=p,
               week=[dict(r) for r in db.q("SELECT day, SUM(amount) total FROM transactions WHERE shop_id=? AND status='verified' AND day>=? GROUP BY day", (s["id"], wk0))],
               cashiers=[{k: r[k] for k in ("id", "name", "email", "phone", "status", "created_at")} for r in
                         db.q("SELECT * FROM users WHERE shop_id=? AND role='cashier' ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, created_at DESC", (s["id"],))])
    return jsonify(out)

@app.post("/api/owner/settings")
@auth("owner", active=True)
def owner_settings():
    d = request.get_json(silent=True) or {}
    br = [clean(b, 40) for b in (d.get("branches") or []) if clean(b, 40)] or ["Main"]
    ch = [c for c in (d.get("channels") or []) if c in sms.CHANNELS] or sms.CHANNELS
    try: big, st, tg = float(d.get("big_amount") or 50000), int(d.get("stale_minutes") or 30), float(d.get("target") or 0)
    except ValueError: return err("Numbers only please.")
    db.x("UPDATE shops SET name=?,currency=?,branches=?,channels=?,big_amount=?,stale_minutes=?,target=? WHERE id=?",
         (clean(d.get("name")) or g.shop["name"], (clean(d.get("currency"), 4) or config.CURRENCY).upper(), json.dumps(br), json.dumps(ch), big, st, tg, g.shop["id"]))
    return jsonify(ok=True)

@app.post("/api/owner/cashiers/<int:cid>/<action>")
@auth("owner")
def owner_cashier(cid, action):
    c = db.q("SELECT * FROM users WHERE id=? AND role='cashier' AND shop_id=?", (cid, g.shop["id"]), one=True)
    if not c: return err("Cashier not found.", 404)
    new = {"approve": "approved", "reject": "rejected", "revoke": "revoked"}.get(action)
    if not new: return err("Unknown action.")
    db.x("UPDATE users SET status=? WHERE id=?", (new, cid))
    return jsonify(ok=True)

@app.post("/api/owner/tx/<tid>/<action>")
@auth("owner", active=True)
def owner_tx(tid, action):
    t = db.q("SELECT * FROM transactions WHERE id=? AND shop_id=?", (tid, g.shop["id"]), one=True)
    if not t: return err("Transaction not found.", 404)
    note = (t["note"] + " · " if t["note"] else "")
    if action == "ok": db.x("UPDATE transactions SET status='verified',reviewed=1,note=? WHERE id=?", (note + "Approved by owner", tid))
    elif action == "no": db.x("UPDATE transactions SET status='failed',reviewed=1,note=? WHERE id=?", (note + "Rejected by owner", tid))
    elif action == "dismiss": db.x("UPDATE transactions SET reviewed=1,note=? WHERE id=?", (note + "Reviewed by owner", tid))
    elif action == "void":
        r = clean((request.get_json(silent=True) or {}).get("reason"), 200) or "no reason"
        f = json.loads(t["flags"] or "[]"); f = f + ["void"] if "void" not in f else f
        db.x("UPDATE transactions SET status='void',flags=?,note=? WHERE id=?", (json.dumps(f), "Voided by owner: " + r, tid))
    else: return err("Unknown action.")
    return jsonify(ok=True)

@app.post("/api/owner/subscribe")
@auth("owner")
def owner_subscribe():
    d = request.get_json(silent=True) or {}
    ref, raw, fee = clean(d.get("reference"), 40).upper(), clean(d.get("sms"), 2000), config.SUBSCRIPTION_FEE
    amount, note = 0.0, ""
    if raw:
        p = sms.parse(raw, allow_outgoing=True)
        if not p["ok"]: return err(p["why"])
        if p["ok"]:
            amount = p["amt"]; ref = ref or p["code"]
            note = "Amount read from the SMS" + ("" if not p["code"] else f", code {p['code']}")
            if p["failed"]: return err("That SMS says the payment failed.")
    if not amount:
        try: amount = float(d.get("amount") or 0)
        except ValueError: amount = 0
    if not ref: return err("Enter the transaction code from your payment SMS.")
    if amount < fee: return err(f"The monthly fee is {config.CURRENCY} {fee:,}. Amount paid must be at least that.")
    if db.q("SELECT 1 FROM sub_payments WHERE reference=? AND status!='rejected'", (ref,), one=True):
        return err("That transaction code was already submitted.")
    months = min(12, int(amount // fee))
    db.x("INSERT INTO sub_payments(shop_id,amount,period_days,method,reference,sms,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
         (g.shop["id"], amount, months * config.PERIOD_DAYS, clean(d.get("method"), 30), ref, raw, note, now_ms()))
    return jsonify(ok=True, message="Payment submitted. The admin will confirm it shortly.")

@app.get("/api/owner/export")
@auth("owner", active=True)
def owner_export():
    day = request.args.get("day") or day_of(now_ms()); buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["Date", "Time", "Branch", "Cashier", "Channel", "Payer", "Phone", "Code", "Amount", "Currency", "Status", "Flags", "Note"])
    for r in db.q("SELECT * FROM transactions WHERE shop_id=? AND day=? ORDER BY created_at", (g.shop["id"], day)):
        x = tx_out(r); w.writerow([x["day"], dt.datetime.fromtimestamp(x["created_at"] / 1000).strftime("%H:%M"), x["branch"], x["cashier_name"],
                                   x["provider"] or x["method"], x["payer"], x["phone"], x["code"], x["amount"], x["currency"], x["status"], "|".join(x["flags"]), x["note"]])
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=ledger-{day}.csv"})

def storage_warning():
    """On Render, warn loudly if the database is NOT on a persistent disk (it would be erased on every restart/sleep)."""
    if not os.environ.get("RENDER"): return ""
    p = os.path.dirname(os.path.abspath(config.DB_PATH))
    while p not in ("/", ""):
        if os.path.ismount(p): return ""
        p = os.path.dirname(p)
    return ("The database is on temporary storage and will be ERASED whenever the service restarts or sleeps. "
            "In Render: set the plan to Starter, add a Disk mounted at /var/data, and set PV_DB=/var/data/payverify.db.")

# ---------------------------------------------------------------- admin API
@app.get("/api/admin/overview")
@auth("admin")
def admin_overview():
    shops = []
    for s in db.q("""SELECT s.*, u.name owner_name, u.email owner_email, u.phone owner_phone,
        (SELECT COUNT(*) FROM users c WHERE c.shop_id=s.id AND c.role='cashier' AND c.status='approved') cashiers,
        (SELECT COALESCE(SUM(amount),0) FROM transactions t WHERE t.shop_id=s.id AND t.status='verified') sales
        FROM shops s LEFT JOIN users u ON u.id=s.owner_id ORDER BY s.created_at DESC"""):
        d = {k: s[k] for k in ("id", "name", "code", "currency", "status", "created_at", "owner_name", "owner_email", "owner_phone", "cashiers", "sales")}
        d["sub"] = sub_info(s); shops.append(d)
    pays = [dict(r) for r in db.q("""SELECT p.*, s.name shop_name FROM sub_payments p JOIN shops s ON s.id=p.shop_id
        ORDER BY CASE p.status WHEN 'pending' THEN 0 ELSE 1 END, p.created_at DESC LIMIT 100""")]
    rev = db.q("SELECT COALESCE(SUM(amount),0) v FROM sub_payments WHERE status='approved'", one=True)["v"]
    return jsonify(warning=storage_warning(), shops=shops, payments=pays, fee=config.SUBSCRIPTION_FEE, currency=config.CURRENCY, user={"name": g.user["name"]},
                   kpis=dict(shops=len(shops), active=sum(s["sub"]["state"] in ("active", "expiring") for s in shops),
                             unpaid=sum(s["sub"]["state"] in ("unpaid", "expired") for s in shops), pending=sum(p["status"] == "pending" for p in pays), revenue=rev,
                             cashiers=db.q("SELECT COUNT(*) n FROM users WHERE role='cashier' AND status='approved'", one=True)["n"]))

@app.post("/api/admin/payments/<int:pid>/<action>")
@auth("admin")
def admin_payment(pid, action):
    p = db.q("SELECT * FROM sub_payments WHERE id=?", (pid,), one=True)
    if not p or p["status"] != "pending": return err("Payment not found or already handled.", 404)
    if action == "approve":
        s = db.q("SELECT * FROM shops WHERE id=?", (p["shop_id"],), one=True)
        base = max(now_ms(), s["expires_at"] or 0)
        db.x("UPDATE shops SET expires_at=? WHERE id=?", (base + p["period_days"] * DAY, s["id"]))
        db.x("UPDATE sub_payments SET status='approved',reviewed_at=? WHERE id=?", (now_ms(), pid))
    elif action == "reject": db.x("UPDATE sub_payments SET status='rejected',reviewed_at=? WHERE id=?", (now_ms(), pid))
    else: return err("Unknown action.")
    return jsonify(ok=True)

@app.post("/api/admin/shops/<int:sid>/<action>")
@auth("admin")
def admin_shop(sid, action):
    s = db.q("SELECT * FROM shops WHERE id=?", (sid,), one=True)
    if not s: return err("Shop not found.", 404)
    if action == "suspend": db.x("UPDATE shops SET status='suspended' WHERE id=?", (sid,))
    elif action == "activate": db.x("UPDATE shops SET status='active' WHERE id=?", (sid,))
    elif action == "extend":
        try: days = int((request.get_json(silent=True) or {}).get("days") or 30)
        except ValueError: return err("Days must be a number.")
        db.x("UPDATE shops SET expires_at=? WHERE id=?", (max(now_ms(), s["expires_at"] or 0) + days * DAY, sid))
    else: return err("Unknown action.")
    return jsonify(ok=True)

# ---------------------------------------------------------------- startup
def bootstrap():
    db.init()
    if storage_warning(): print("\n  WARNING: " + storage_warning() + "\n")
    c = db._connect()
    if not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        c.execute("INSERT INTO users(role,name,email,password_hash,status,created_at) VALUES('admin','Platform Admin',?,?, 'active',?)",
                  (config.ADMIN_EMAIL, generate_password_hash(config.ADMIN_PASSWORD), now_ms())); c.commit()
        print(f"\n  First admin created ->  {config.ADMIN_EMAIL} / {config.ADMIN_PASSWORD}  (change it after logging in)\n")
    c.close()

bootstrap()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))      # Render sets PORT automatically
    try:
        from waitress import serve
        print(f"PayVerify running on port {port}"); serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:                            # e.g. Pydroid without waitress
        app.run(host="0.0.0.0", port=port)
