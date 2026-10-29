"""CTone extensions: system health, connections vault, MTN/Airtel payment prompts, SMS listener with authenticity
checks, automatic subscription activation, users, audit log, backup, announcements and maintenance mode."""
import hashlib, hmac, io, json, os, re, resource, secrets, sys, time, uuid
from flask import Response, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash
import config, db, settings as S, smsguard, providers

STARTED = time.time()
DAY = 86400000
now_ms = lambda: int(time.time() * 1000)
CTX = {}

# ---------------------------------------------------------------- crediting a subscription (one place, used by every path)
def credit(shop_id, amount, ref, source, sms_text="", note="", pay_id=None):
    """Give a shop the days it paid for. Returns True if applied (never twice for one payment)."""
    fee = S.fee(); months = max(1, min(12, int(amount // fee))); days = months * config.PERIOD_DAYS
    if pay_id:
        if not db.n("UPDATE sub_payments SET status='approved',amount=?,period_days=?,reviewed_at=?,note=?,source=? WHERE id=? AND status='pending'",
                    (amount, days, now_ms(), note, source, pay_id)): return False
    else:
        if db.q("SELECT 1 FROM sub_payments WHERE reference=? AND status!='rejected'", (ref,), one=True): return False
        db.x("INSERT INTO sub_payments(shop_id,amount,period_days,method,reference,sms,status,note,created_at,reviewed_at,source) VALUES(?,?,?,?,?,?,'approved',?,?,?,?)",
             (shop_id, amount, days, source, ref, sms_text, note, now_ms(), now_ms(), source))
    s = db.q("SELECT expires_at FROM shops WHERE id=?", (shop_id,), one=True)
    db.x("UPDATE shops SET expires_at=? WHERE id=?", (max(now_ms(), s["expires_at"] or 0) + days * DAY, shop_id), ret=False)
    S.audit("system", "auto-activate", f"shop {shop_id} +{days}d via {source} ref {ref}")
    return True

def try_match(pay):
    """Match a pending subscription payment with a GENUINE SMS that arrived through the listener."""
    if not S.flag("auto_activate"): return False
    k = smsguard.key(pay["reference"])
    if not k: return False
    m = db.q("SELECT * FROM sms_inbox WHERE code_key=? AND verdict='genuine' AND direction='in' AND status='new' ORDER BY id LIMIT 1", (k,), one=True)
    if not m or m["amount"] < S.fee(): return False
    if m["currency"] and m["currency"] != S.get("currency").upper():
        db.x("UPDATE sub_payments SET note=? WHERE id=?", (f"Waiting: the SMS is in {m['currency']} but your subscription currency is {S.get('currency')} (Settings → Billing).", pay["id"]), ret=False); return False
    if now_ms() - m["received_at"] > int(S.get("sms_max_age_h") or 72) * 3600000: return False
    if not db.n("UPDATE sms_inbox SET status='used',used_by=? WHERE id=? AND status='new'", (pay["id"], m["id"])): return False
    if credit(pay["shop_id"], m["amount"], pay["reference"], "sms-auto", pay["sms"] or "", f"Verified automatically: genuine {m['provider']} SMS, code {m['code']}", pay["id"]): return True
    db.x("UPDATE sms_inbox SET status='new',used_by=NULL WHERE id=?", (m["id"],), ret=False); return False

def recheck(shop_id=None):
    """Try to activate pending payments (called on every owner refresh and every incoming SMS)."""
    rows = db.q("SELECT * FROM sub_payments WHERE status='pending'" + (" AND shop_id=?" if shop_id else ""), (shop_id,) if shop_id else ())
    return any([try_match(r) for r in rows])

def after_submit(pid):
    """Called right after an owner submits payment details. Returns (activated, message)."""
    p = db.q("SELECT * FROM sub_payments WHERE id=?", (pid,), one=True)
    if try_match(p): return True, "✅ Payment verified – your shop is now active."
    if S.flag("trust_pasted") and p["sms"]:
        a = smsguard.assess(p["sms"])
        if a["verdict"] == "format_ok" and credit(p["shop_id"], a["parsed"]["amt"], p["reference"], "sms-pasted", p["sms"], "Activated from pasted SMS (format checked)", pid): return True, "✅ Payment accepted – your shop is now active."
    return False, "Payment received. As soon as the money's SMS reaches us your shop activates automatically."

# ---------------------------------------------------------------- SMS ingest (phone forwarder -> server)
PICK = {"sender": ("from", "sender", "address", "originatingaddress", "number", "phone", "sender_id", "senderid"),
        "body": ("message", "text", "body", "content", "sms", "msg", "messagebody"),
        "time": ("sentstamp", "timestamp", "receivedstamp", "time", "date", "received_at")}

def _pick(d, names):
    low = {str(k).lower(): v for k, v in d.items()}
    for n in names:
        if low.get(n) not in (None, ""): return str(low[n])
    return ""

def ingest(sender, body, tms=0, trusted=True):
    a = smsguard.assess(body, sender, trusted_source=trusted); p = a["parsed"]
    code = p.get("code", "") if p.get("ok") else ""
    direction = "in" if p.get("ok") else ("out" if a["verdict"] == "outgoing" else "other")
    status = "new" if a["verdict"] == "genuine" else "ignored"
    if code and db.q("SELECT 1 FROM sms_inbox WHERE code_key=? AND direction='in' AND verdict='genuine'", (smsguard.key(code),), one=True) and status == "new": status = "duplicate"
    sid = db.x("INSERT INTO sms_inbox(received_at,sender,body,provider,verdict,score,reasons,amount,currency,code,code_key,payer,phone,direction,status,sms_time) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (now_ms(), sender[:60], body[:1500], a["provider"], a["verdict"], a["score"], json.dumps(a["reasons"]), p.get("amt", 0) or 0, p.get("cur", "") or "",
                code, smsguard.key(code), p.get("payer", "") or "", p.get("phone", "") or "", direction, status, p.get("t", 0) or 0))
    if status == "new": recheck()
    return sid, a

# ---------------------------------------------------------------- health
def health():
    checks, t0 = [], time.time()
    def chk(name, lvl, msg): checks.append({"name": name, "level": lvl, "msg": msg})
    try: db.q("SELECT 1"); lat = int((time.time() - t0) * 1000); chk("Database connection", "ok" if lat < 800 else "warn", f"{db.BACKEND} answered in {lat} ms")
    except Exception as e: lat = -1; chk("Database connection", "bad", str(e)[:150])
    chk("Database location", "ok" if db.PG else "bad" if os.environ.get("RENDER") else "warn",
        "Supabase (PostgreSQL) – data is safe outside Render" if db.PG else "Local SQLite file – DATABASE_URL is not set")
    a = db.q("SELECT password_hash FROM users WHERE role='admin' ORDER BY id LIMIT 1", one=True)
    chk("Admin password", "bad" if a and check_password_hash(a["password_hash"], "admin123") else "ok", "Still the default 'admin123' – change it now" if a and check_password_hash(a["password_hash"], "admin123") else "Changed from the default")
    chk("HTTPS", "ok" if request.is_secure else "warn", "Secure connection" if request.is_secure else "Not HTTPS (fine on localhost, not for real use)")
    chk("Pay number", "ok" if re.sub(r"\D", "", S.get("pay_number")).__len__() >= 9 else "bad", S.get("pay_number") or "not set")
    li = db.q("SELECT MAX(received_at) t, COUNT(*) n FROM sms_inbox WHERE received_at>?", (now_ms() - DAY,), one=True)
    last = db.q("SELECT MAX(received_at) t FROM sms_inbox", one=True)["t"]
    chk("SMS listener", "ok" if last and now_ms() - last < 2 * DAY else "warn", "No SMS ever received – set up the phone forwarder" if not last else f"{li['n']} message(s) in 24h, last {int((now_ms()-last)/60000)} min ago")
    for n in ("mtn", "airtel", "supabase", "firebase"):
        r = db.q("SELECT * FROM integrations WHERE name=?", (n,), one=True)
        chk(providers.CATALOG[n]["title"], "warn" if not r else ("ok" if r["last_ok"] else "bad") if r["last_test_at"] else "warn",
            "Not connected" if not r else (r["last_msg"] or "Saved – not tested yet"))
    pend = db.q("SELECT COUNT(*) n FROM sub_payments WHERE status='pending'", one=True)["n"]
    chk("Payments waiting", "ok" if pend == 0 else "warn", f"{pend} waiting for their SMS / your review")
    tabs = {}
    for t in db.TABLES:
        try: tabs[t] = db.q(f"SELECT COUNT(*) n FROM {t}", one=True)["n"]
        except Exception: tabs[t] = None
    try:
        size = db.q("SELECT pg_database_size(current_database()) s", one=True)["s"] if db.PG else os.path.getsize(config.DB_PATH)
    except Exception: size = None
    try: mem = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except Exception: mem = None
    score = max(0, 100 - 25 * sum(c["level"] == "bad" for c in checks) - 8 * sum(c["level"] == "warn" for c in checks))
    return dict(score=score, checks=checks, tables=tabs, db_ms=lat, db_size=size, uptime_s=int(time.time() - STARTED), python=sys.version.split()[0], mem_mb=mem,
                backend=db.BACKEND, tz=config.TIMEZONE, now=now_ms())

# ---------------------------------------------------------------- registration of all routes
def register(app, auth, err, sub_info_fn):
    admin = auth("admin")

    @app.before_request
    def maintenance():
        if request.path.startswith("/api/") and not request.path.startswith(("/api/admin", "/api/sms/ingest", "/api/pay/callback", "/api/login", "/api/logout")):
            try: on = S.flag("maintenance")
            except Exception: return
            if on:
                u = db.q("SELECT role FROM users WHERE id=?", (session.get("uid"),), one=True) if session.get("uid") else None
                if not u or u["role"] != "admin": return err("CTone is being updated. Please try again in a few minutes.", 503)

    # ---- health
    @app.get("/api/admin/health")
    @admin
    def a_health(): return jsonify(health())

    # ---- connections vault
    def mask(c, fields):
        out = {}
        for k, _, kind, _o in fields:
            v = c.get(k, "")
            out[k] = ("•" * 6 + str(v)[-4:] if len(str(v)) > 4 else "•" * len(str(v))) if kind == "secret" and v else v
        return out

    @app.get("/api/admin/integrations")
    @admin
    def a_integrations():
        rows = {r["name"]: r for r in db.q("SELECT * FROM integrations")}
        names = ["mtn", "airtel", "supabase", "firebase"] + sorted(n for n in rows if n.startswith("app:"))
        out = []
        for n in names:
            r = rows.get(n); f = providers.fields_for(n); c = S.unseal(r["data"]) if r else {}
            out.append(dict(name=n, title=providers.CATALOG[n]["title"] if n in providers.CATALOG else n[4:], help=providers.CATALOG.get(n, {}).get("help", "Any other application you want to keep an eye on."),
                            fields=[dict(key=k, label=l, kind=kd, options=o) for k, l, kd, o in f], values=mask(c, f), saved=bool(r), enabled=bool(r and r["enabled"]),
                            tested=bool(r and r["last_test_at"]), ok=bool(r and r["last_ok"]), msg=r["last_msg"] if r else "", tested_at=r["last_test_at"] if r else 0))
        return jsonify(items=out, callback={"mtn": request.url_root.rstrip("/") + "/api/pay/callback/mtn", "airtel": request.url_root.rstrip("/") + "/api/pay/callback/airtel"})

    @app.post("/api/admin/integrations/<name>")
    @admin
    def a_integ_save(name):
        d = request.get_json(silent=True) or {}
        if name.startswith("app:"): name = "app:" + re.sub(r"[^a-z0-9_\-]", "", name[4:].lower())[:30]
        elif name not in providers.CATALOG: return err("Unknown connection.", 404)
        if name == "app:": return err("Give the connection a name.")
        S.integ_save(name, {k: str(v).strip() for k, v in (d.get("values") or {}).items()}, d.get("enabled", True))
        S.audit(g.user["email"], "connection-saved", name)
        return jsonify(ok=True, name=name)

    @app.post("/api/admin/integrations/<name>/test")
    @admin
    def a_integ_test(name):
        c = S.integ(name); ok, msg, ms = providers.test(name, c)
        if db.q("SELECT 1 FROM integrations WHERE name=?", (name,), one=True):
            db.x("UPDATE integrations SET last_test_at=?,last_ok=?,last_msg=? WHERE name=?", (now_ms(), 1 if ok else 0, f"{msg} ({ms} ms)"[:250], name), ret=False)
        return jsonify(ok=ok, message=msg, ms=ms)

    @app.post("/api/admin/integrations/<name>/delete")
    @admin
    def a_integ_del(name):
        db.x("DELETE FROM integrations WHERE name=?", (name,), ret=False); S.audit(g.user["email"], "connection-removed", name); return jsonify(ok=True)

    @app.post("/api/admin/dburl/test")
    @admin
    def a_dburl():
        ok, msg, ms = providers.test_db_url((request.get_json(silent=True) or {}).get("url", "").strip())
        return jsonify(ok=ok, message=msg, ms=ms)

    # ---- settings
    KEYS = ("fee", "currency", "pay_number", "pay_name", "auto_activate", "trust_pasted", "sms_max_age_h", "maintenance", "pay_network", "country_code")
    @app.get("/api/admin/settings")
    @admin
    def a_settings(): return jsonify({k: S.get(k) for k in KEYS + ("announcement",)})

    @app.post("/api/admin/settings")
    @admin
    def a_settings_save():
        d = request.get_json(silent=True) or {}
        for k in KEYS + ("announcement",):
            if k in d:
                v = str(d[k]).strip()[:400]
                if k == "fee":
                    try: assert float(v) > 0
                    except Exception: return err("Fee must be a number above 0.")
                if k == "currency": v = v.upper()[:4]
                S.put(k, v)
        S.audit(g.user["email"], "settings-saved", ", ".join(k for k in d if k in KEYS + ("announcement",)))
        return jsonify(ok=True)

    # ---- SMS listener
    def token():
        r = db.q("SELECT val FROM app_settings WHERE name='sms_token'", one=True)
        if r: return r["val"]
        t = secrets.token_urlsafe(24); S.put("sms_token", t); return t

    @app.get("/api/admin/sms")
    @admin
    def a_sms():
        rows = []
        for r in db.q("SELECT * FROM sms_inbox ORDER BY id DESC LIMIT 100"):
            d = dict(r); d["reasons"] = json.loads(d["reasons"] or "[]"); rows.append(d)
        st = db.q("SELECT verdict, COUNT(*) n FROM sms_inbox GROUP BY verdict")
        return jsonify(items=rows, stats={r["verdict"]: r["n"] for r in st}, token=token(), url=request.url_root.rstrip("/") + "/api/sms/ingest",
                       formats=json.loads(S.get("sms_formats") or "[]"), providers=list(smsguard.PROFILES), last=db.q("SELECT MAX(received_at) t FROM sms_inbox", one=True)["t"])

    @app.post("/api/admin/sms/token")
    @admin
    def a_sms_token():
        t = secrets.token_urlsafe(24); S.put("sms_token", t); S.audit(g.user["email"], "sms-token-rotated"); return jsonify(ok=True, token=t)

    @app.post("/api/admin/sms/test")
    @admin
    def a_sms_test():
        d = request.get_json(silent=True) or {}
        a = smsguard.assess(d.get("body", ""), (d.get("sender") or "").strip() or None, trusted_source=bool(d.get("sender")))
        p = a.pop("parsed"); a["amount"] = p.get("amt"); a["code"] = p.get("code"); a["payer"] = p.get("payer")
        return jsonify(a)

    @app.post("/api/admin/sms/formats")
    @admin
    def a_sms_formats():
        fs = []
        for f in (request.get_json(silent=True) or {}).get("formats", [])[:60]:
            rx = (f.get("regex") or "").strip()
            if not rx or f.get("provider") not in smsguard.PROFILES: continue
            try: re.compile(rx)
            except re.error as e: return err(f"Bad pattern '{rx[:40]}': {e}")
            fs.append({"provider": f["provider"], "regex": rx[:300]})
        S.put("sms_formats", json.dumps(fs)); S.audit(g.user["email"], "sms-formats-saved", f"{len(fs)} custom"); return jsonify(ok=True)

    @app.post("/api/admin/sms/<int:sid>/<action>")
    @admin
    def a_sms_action(sid, action):
        m = db.q("SELECT * FROM sms_inbox WHERE id=?", (sid,), one=True)
        if not m: return err("Not found.", 404)
        if action == "recheck":
            a = smsguard.assess(m["body"], m["sender"], trusted_source=True); p = a["parsed"]
            db.x("UPDATE sms_inbox SET verdict=?,score=?,reasons=?,status=? WHERE id=?", (a["verdict"], a["score"], json.dumps(a["reasons"]), "new" if a["verdict"] == "genuine" and m["status"] != "used" else m["status"], sid), ret=False)
            recheck()
        elif action == "trust" and m["status"] != "used": db.x("UPDATE sms_inbox SET verdict='genuine',status='new' WHERE id=?", (sid,), ret=False); recheck()
        elif action == "ignore" and m["status"] != "used": db.x("UPDATE sms_inbox SET status='ignored' WHERE id=?", (sid,), ret=False)
        else: return err("Unknown action.")
        S.audit(g.user["email"], f"sms-{action}", sid); return jsonify(ok=True)

    @app.route("/api/sms/ingest", methods=["GET", "POST"])
    def sms_ingest():
        """The phone forwarder calls this. Accepts JSON, form fields or query string; token in header, field or ?token=."""
        d = {}
        d.update(request.args.to_dict()); d.update(request.form.to_dict())
        js = request.get_json(silent=True, force=True)
        if isinstance(js, dict): d.update(js)
        tok = (request.headers.get("X-Token") or request.headers.get("Authorization", "").replace("Bearer ", "").strip() or str(d.get("token") or d.get("secret") or d.get("key") or ""))
        real = token()
        k = "ing|" + (request.remote_addr or "")
        if len([x for x in app.config.setdefault("_ING", {}).get(k, []) if time.time() - x < 60]) > 120: return err("Slow down.", 429)
        if not tok or not hmac.compare_digest(tok, real):
            app.config["_ING"].setdefault(k, []).append(time.time()); return err("Bad token.", 401)
        app.config["_ING"].setdefault(k, []).append(time.time())
        body, sender = _pick(d, PICK["body"]), _pick(d, PICK["sender"])
        if not body: return err("No message text found.")
        tms = 0
        try:
            tv = float(_pick(d, PICK["time"]) or 0); tms = int(tv if tv > 1e12 else tv * 1000) if tv else 0
        except ValueError: tms = 0
        sid, a = ingest(sender, body, tms)
        return jsonify(ok=True, id=sid, verdict=a["verdict"], score=a["score"])

    # ---- users / audit / backup / announcement
    @app.get("/api/admin/users")
    @admin
    def a_users():
        return jsonify(items=[dict(r) for r in db.q("SELECT u.id,u.role,u.name,u.email,u.phone,u.status,u.created_at,s.name shop FROM users u LEFT JOIN shops s ON s.id=u.shop_id ORDER BY u.created_at DESC LIMIT 300")])

    @app.post("/api/admin/users/<int:uid>/<action>")
    @admin
    def a_user_act(uid, action):
        u = db.q("SELECT * FROM users WHERE id=?", (uid,), one=True)
        if not u: return err("Not found.", 404)
        if u["id"] == g.user["id"]: return err("You can't change your own account here.")
        if action == "disable": db.x("UPDATE users SET status='disabled' WHERE id=?", (uid,), ret=False)
        elif action == "enable": db.x("UPDATE users SET status=? WHERE id=?", ("approved" if u["role"] == "cashier" else "active", uid), ret=False)
        elif action == "resetpw":
            pw = secrets.token_urlsafe(6); db.x("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(pw), uid), ret=False)
            S.audit(g.user["email"], "password-reset", u["email"]); return jsonify(ok=True, password=pw)
        else: return err("Unknown action.")
        S.audit(g.user["email"], f"user-{action}", u["email"]); return jsonify(ok=True)

    @app.get("/api/admin/audit")
    @admin
    def a_audit(): return jsonify(items=[dict(r) for r in db.q("SELECT * FROM audit_log ORDER BY id DESC LIMIT 150")])

    @app.get("/api/admin/backup")
    @admin
    def a_backup():
        out = {"exported_at": now_ms(), "backend": db.BACKEND}
        for t in ("users", "shops", "sub_payments", "transactions", "sms_inbox", "pay_requests", "audit_log"):
            rows = [dict(r) for r in db.q(f"SELECT * FROM {t}")]
            if t == "users":
                for r in rows: r.pop("password_hash", None)
            out[t] = rows
        S.audit(g.user["email"], "backup-downloaded")
        return Response(json.dumps(out, default=str), mimetype="application/json", headers={"Content-Disposition": f"attachment; filename=ctone-backup-{time.strftime('%Y%m%d-%H%M')}.json"})

    # ---- owner: payment prompt to the customer's phone (MTN / Airtel)
    def live_providers(): return [n for n in ("mtn", "airtel") if S.integ(n)]

    @app.post("/api/owner/pay/push")
    @auth("owner")
    def pay_push():
        d = request.get_json(silent=True) or {}; name = d.get("provider")
        if name not in live_providers(): return err("That payment method isn't switched on yet.")
        try: months = max(1, min(12, int(d.get("months") or 1)))
        except ValueError: months = 1
        phone = re.sub(r"[^\d+]", "", d.get("phone") or "")
        if len(phone) < 9: return err("Enter the mobile-money number to charge.")
        c = S.integ(name); amount = S.fee() * months; rid = uuid.uuid4().hex[:16]
        ok, ext, msg = providers.push(name, c, phone, amount, rid, f"CTone subscription {g.shop['code']}")
        db.x("INSERT INTO pay_requests(id,shop_id,provider,phone,amount,status,ext_ref,detail,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
             (rid, g.shop["id"], name, phone, amount, "pending" if ok else "failed", ext, msg, now_ms()), ret=False)
        if not ok: return err(msg or "The request could not be sent.")
        return jsonify(ok=True, id=rid, message="Check your phone and enter your PIN to approve the payment.")

    def settle(r):
        """Ask the provider whether a prompt was paid; activate the shop if so."""
        if r["status"] != "pending": return r["status"]
        st, txid, why = providers.status(r["provider"], S.integ(r["provider"]), r["ext_ref"])
        if st == "success":
            if db.n("UPDATE pay_requests SET status='success',detail=? WHERE id=? AND status='pending'", (txid, r["id"])):
                credit(r["shop_id"], r["amount"], (txid or r["id"]).upper(), r["provider"] + "-push", "", f"Paid by prompt to {r['phone']}")
        elif st == "failed": db.x("UPDATE pay_requests SET status='failed',detail=? WHERE id=?", (why or "declined", r["id"]), ret=False)
        return st

    @app.get("/api/owner/pay/status/<rid>")
    @auth("owner")
    def pay_status(rid):
        r = db.q("SELECT * FROM pay_requests WHERE id=? AND shop_id=?", (rid, g.shop["id"]), one=True)
        if not r: return err("Not found.", 404)
        st = settle(r); return jsonify(ok=True, status=st)

    @app.route("/api/pay/callback/<name>", methods=["POST", "PUT"])
    def pay_callback(name):
        """Provider calls this when the customer approves. We never trust the body: we re-ask the provider."""
        d = request.get_json(silent=True) or {}
        ref = str(d.get("externalId") or (d.get("transaction") or {}).get("id") or "")
        r = db.q("SELECT * FROM pay_requests WHERE (id=? OR ext_ref=?) AND provider=?", (ref, ref, name), one=True) if ref else None
        if r: settle(r)
        return jsonify(ok=True)

    CTX.update(live_providers=live_providers)

def owner_extras():
    return dict(push=CTX["live_providers"](), notice=S.get("announcement"))
