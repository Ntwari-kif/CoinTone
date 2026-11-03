"""Connectors: MTN MoMo Collections, Airtel Money Collections, Supabase, and any other app (generic).
Credentials come from the encrypted vault (Admin > Connections). Uses only the standard library."""
import base64, json, re, time, urllib.request, urllib.error, uuid
import settings

# What each connection asks for. kind: text | secret | select
CATALOG = {
  "mtn": {"title": "MTN MoMo (Collections API)", "help": "momodeveloper.mtn.com → your Collection product → subscription key; create an API user + API key. Needed to send payment prompts to MTN customers.",
    "fields": [("env", "Environment", "select", ["sandbox", "production"]), ("base_url", "API base URL (optional)", "text", None),
               ("subscription_key", "Subscription key (Ocp-Apim)", "secret", None), ("api_user", "API user (UUID)", "text", None),
               ("api_key", "API key", "secret", None), ("country_code", "Country dialling code, e.g. 256", "text", None),
               ("currency", "Currency (sandbox = EUR)", "text", None)]},
  "airtel": {"title": "Airtel Money (Open API)", "help": "developers.airtel.africa → your app → client ID & secret. Needed to send payment prompts to Airtel customers.",
    "fields": [("env", "Environment", "select", ["uat", "production"]), ("base_url", "API base URL (optional)", "text", None),
               ("client_id", "Client ID", "text", None), ("client_secret", "Client secret", "secret", None),
               ("country", "Country code, e.g. UG, RW, KE, TZ", "text", None), ("currency", "Currency, e.g. UGX", "text", None),
               ("country_code", "Dialling code, e.g. 256", "text", None)]},
  "supabase": {"title": "Supabase project", "help": "Supabase → Project Settings → API. The app's own database already runs on DATABASE_URL; this lets the panel check the project's API and link it.",
    "fields": [("url", "Project URL (https://xxxx.supabase.co)", "text", None), ("anon_key", "Anon / publishable key", "secret", None),
               ("service_key", "Service-role key (optional)", "secret", None)]},
  "firebase": {"title": "Firebase (Firestore / Realtime DB)", "help": "Firebase console → Project settings. Add the project ID + Web API key (Firestore) and/or the Realtime Database URL (+ database secret if rules are locked).",
    "fields": [("project_id", "Project ID", "text", None), ("api_key", "Web API key", "secret", None),
               ("database_url", "Realtime Database URL (optional)", "text", None), ("db_secret", "Database secret (optional)", "secret", None)]},
}
GENERIC = [("base_url", "Base URL (https://…)", "text", None), ("api_key", "API key / token", "secret", None),
           ("health_path", "Health-check path, e.g. /health", "text", None)]

def fields_for(name):
    if name in CATALOG: return CATALOG[name]["fields"]
    return GENERIC

def http(method, url, headers=None, body=None, timeout=20):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: raw, code = r.read().decode("utf-8", "replace"), r.status
    except urllib.error.HTTPError as e: raw, code = e.read().decode("utf-8", "replace"), e.code
    except Exception as e: return 0, {"error": str(e)}, int((time.time() - t0) * 1000)
    try: js = json.loads(raw) if raw else {}
    except ValueError: js = {"raw": raw[:300]}
    return code, js, int((time.time() - t0) * 1000)

def msisdn(phone, cc, strip=False):
    d = re.sub(r"\D", "", phone or ""); cc = re.sub(r"\D", "", cc or "")
    if d.startswith("00"): d = d[2:]
    if cc and d.startswith("0"): d = cc + d[1:]
    if strip and cc and d.startswith(cc): d = d[len(cc):]
    return d

# ---------------------------------------------------------------- MTN MoMo
def _mtn_base(c): return (c.get("base_url") or ("https://sandbox.momodeveloper.mtn.com" if c.get("env", "sandbox") == "sandbox" else "https://proxy.momoapi.mtn.com")).rstrip("/")
def mtn_token(c):
    cred = base64.b64encode(f"{c.get('api_user','')}:{c.get('api_key','')}".encode()).decode()
    code, js, ms = http("POST", _mtn_base(c) + "/collection/token/", {"Authorization": "Basic " + cred, "Ocp-Apim-Subscription-Key": c.get("subscription_key", ""), "Content-Length": "0"}, b"")
    return (js.get("access_token"), code, js, ms)
def mtn_push(c, phone, amount, ref, note):
    tok, code, js, _ = mtn_token(c)
    if not tok: return False, "", f"MTN login failed ({code}): {js.get('message') or js.get('error') or js}"
    rid = str(uuid.uuid4())
    h = {"Authorization": "Bearer " + tok, "X-Reference-Id": rid, "X-Target-Environment": "sandbox" if c.get("env", "sandbox") == "sandbox" else c.get("target_env", "mtnuganda"),
         "Ocp-Apim-Subscription-Key": c.get("subscription_key", ""), "Content-Type": "application/json"}
    if c.get("callback_url"): h["X-Callback-Url"] = c["callback_url"]
    body = {"amount": str(int(amount)), "currency": c.get("currency") or "EUR", "externalId": ref,
            "payer": {"partyIdType": "MSISDN", "partyId": msisdn(phone, c.get("country_code"))}, "payerMessage": note, "payeeNote": note}
    code, js, _ = http("POST", _mtn_base(c) + "/collection/v1_0/requesttopay", h, body)
    return (code in (200, 202), rid, "" if code in (200, 202) else f"MTN refused the request ({code}): {js.get('message') or js}")
def mtn_status(c, rid):
    tok, code, js, _ = mtn_token(c)
    if not tok: return "pending", "", "login failed"
    code, js, _ = http("GET", _mtn_base(c) + f"/collection/v1_0/requesttopay/{rid}", {"Authorization": "Bearer " + tok, "X-Target-Environment": "sandbox" if c.get("env", "sandbox") == "sandbox" else c.get("target_env", "mtnuganda"), "Ocp-Apim-Subscription-Key": c.get("subscription_key", "")})
    st = (js.get("status") or "").upper()
    return ("success" if st == "SUCCESSFUL" else "failed" if st == "FAILED" else "pending"), str(js.get("financialTransactionId") or ""), js.get("reason") or ""

# ---------------------------------------------------------------- Airtel Money
def _air_base(c): return (c.get("base_url") or ("https://openapiuat.airtel.africa" if c.get("env", "uat") == "uat" else "https://openapi.airtel.africa")).rstrip("/")
def airtel_token(c):
    code, js, ms = http("POST", _air_base(c) + "/auth/oauth2/token", {"Content-Type": "application/json", "Accept": "*/*"},
                        {"client_id": c.get("client_id", ""), "client_secret": c.get("client_secret", ""), "grant_type": "client_credentials"})
    return js.get("access_token"), code, js, ms
def airtel_push(c, phone, amount, ref, note):
    tok, code, js, _ = airtel_token(c)
    if not tok: return False, "", f"Airtel login failed ({code}): {js.get('error_description') or js.get('message') or js}"
    h = {"Authorization": "Bearer " + tok, "Content-Type": "application/json", "Accept": "*/*", "X-Country": c.get("country", ""), "X-Currency": c.get("currency", "")}
    body = {"reference": note[:60], "subscriber": {"country": c.get("country", ""), "currency": c.get("currency", ""), "msisdn": msisdn(phone, c.get("country_code"), strip=True)},
            "transaction": {"amount": int(amount), "country": c.get("country", ""), "currency": c.get("currency", ""), "id": ref}}
    code, js, _ = http("POST", _air_base(c) + "/merchant/v1/payments/", h, body)
    ok = code == 200 and str((js.get("status") or {}).get("success")).lower() in ("true", "1")
    return ok, ref, "" if ok else f"Airtel refused the request ({code}): {(js.get('status') or {}).get('message') or js}"
def airtel_status(c, tid):
    tok, code, js, _ = airtel_token(c)
    if not tok: return "pending", "", "login failed"
    code, js, _ = http("GET", _air_base(c) + f"/standard/v1/payments/{tid}", {"Authorization": "Bearer " + tok, "Accept": "*/*", "X-Country": c.get("country", ""), "X-Currency": c.get("currency", "")})
    d = (js.get("data") or {}).get("transaction") or {}; st = (d.get("status") or "").upper()
    return ("success" if st == "TS" else "failed" if st in ("TF", "TE") else "pending"), str(d.get("airtel_money_id") or ""), d.get("message") or ""

def push(name, c, phone, amount, ref, note): return (mtn_push if name == "mtn" else airtel_push)(c, phone, amount, ref, note)
def status(name, c, rid): return (mtn_status if name == "mtn" else airtel_status)(c, rid)

# ---------------------------------------------------------------- connection tests
def test(name, c):
    """Returns (ok, message, latency_ms)."""
    if not c: return False, "Nothing saved yet.", 0
    if name == "mtn":
        tok, code, js, ms = mtn_token(c)
        return bool(tok), "MTN accepted your keys – connected." if tok else f"MTN rejected the keys ({code}): {js.get('message') or js.get('error') or js}", ms
    if name == "airtel":
        tok, code, js, ms = airtel_token(c)
        return bool(tok), "Airtel accepted your keys – connected." if tok else f"Airtel rejected the keys ({code}): {js.get('error_description') or js.get('message') or js}", ms
    if name == "supabase":
        u = (c.get("url") or "").rstrip("/"); k = c.get("service_key") or c.get("anon_key") or ""
        if not u.startswith("https://"): return False, "Project URL must start with https://", 0
        code, js, ms = http("GET", u + "/rest/v1/", {"apikey": k, "Authorization": "Bearer " + k})
        return code in (200, 401, 404) and code != 0 and code != 401, ("Supabase API reachable – key accepted." if code == 200 else f"Supabase answered {code}: {js.get('message') or js.get('error') or js}"), ms
    if name == "firebase":
        if c.get("database_url"):
            u = c["database_url"].rstrip("/") + "/.json?shallow=true&limitToFirst=1" + (("&auth=" + c["db_secret"]) if c.get("db_secret") else "")
            code, js, ms = http("GET", u)
            return code == 200, "Realtime Database reachable." if code == 200 else f"Realtime Database answered {code}: {js.get('error') or js}", ms
        if c.get("project_id") and c.get("api_key"):
            code, js, ms = http("GET", f"https://firestore.googleapis.com/v1/projects/{c['project_id']}/databases/(default)/documents?pageSize=1&key={c['api_key']}")
            return code in (200, 403) and code != 0, ("Firestore reachable." if code == 200 else "Project found, but Firestore rules deny anonymous reads (that's normal & safe)." if code == 403 else f"Firestore answered {code}: {(js.get('error') or {}).get('message') if isinstance(js.get('error'), dict) else js}"), ms
        return False, "Enter the project ID + API key, or the Realtime Database URL.", 0
    u = (c.get("base_url") or "").rstrip("/")
    if not u.startswith("http"): return False, "Base URL must start with http(s)://", 0
    h = {"Authorization": "Bearer " + c["api_key"]} if c.get("api_key") else {}
    code, js, ms = http("GET", u + (c.get("health_path") or "/"), h)
    return 200 <= code < 400, f"Answered {code}" if code else f"Unreachable: {js.get('error')}", ms

def test_db_url(url):
    """Try a Supabase/PostgreSQL connection string without saving it."""
    try:
        import psycopg
        t0 = time.time()
        kw = {} if "sslmode" in url else {"sslmode": "require"}
        with psycopg.connect(url, connect_timeout=10, prepare_threshold=None, **kw) as c:
            c.execute("SELECT 1")
        return True, "Connected to the database successfully.", int((time.time() - t0) * 1000)
    except Exception as e: return False, f"Could not connect: {str(e)[:200]}", 0
