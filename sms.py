"""Mobile-money SMS parser (M-Pesa, MTN MoMo, Airtel, Telecel, Orange...)."""
import re, datetime as dt

CHANNELS = ["Airtel Money", "MTN MoMo", "M-Pesa", "Telecel Cash", "Orange Money", "Cash", "Card / POS", "Bank transfer"]
CUR = {"KSH": "KES", "KES": "KES", "GHS": "GHS", "GH₵": "GHS", "GHC": "GHS", "UGX": "UGX", "TZS": "TZS", "TSH": "TZS",
       "NGN": "NGN", "₦": "NGN", "RWF": "RWF", "ZMW": "ZMW", "XOF": "XOF", "XAF": "XAF", "USD": "USD", "$": "USD"}
AMT = re.compile(r"(?:\b(KSH|KES|GHS|GHC|UGX|TZS|TSH|NGN|RWF|ZMW|XOF|XAF|USD)|(GH₵|₦|\$))\.?\s*([\d,]+(?:\.\d{1,2})?)"
                 r"|([\d,]+(?:\.\d{1,2})?)\s*(KSH|KES|GHS|UGX|TZS|NGN|RWF|ZMW)\b", re.I)
CODE1 = re.compile(r"^\s*([A-Z0-9]{10})\s+Confirmed")
CODE2 = re.compile(r"(?:trans(?:action)?\.?\s*(?:id|ref(?:erence)?|no\.?)?|ref(?:erence)?(?:\s*no)?|txn(?:\s*id)?|financial transaction id|receipt)"
                   r"\s*[:#.\-]?\s*([A-Z0-9][A-Z0-9.\-]{5,24})", re.I)
FROM = re.compile(r"\bfrom\s+(.{2,70}?)(?=\s+on\s|\s+at\s|\.\s|,|\s+(?:current|new|your|balance|transaction|trans|ref|fee|charge)\b|$)", re.I)
PHONE = re.compile(r"\+?\d[\d ]{8,13}\d")
OUT = re.compile(r"\b(you have (sent|paid|bought|withdrawn)|sent to|paid to|withdraw|airtime|bought|transferred to|you have transferred)\b", re.I)
TIME1 = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})\s+(?:at\s+)?(\d{1,2}):(\d{2})\s*(AM|PM)?", re.I)
TIME2 = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})")
FAILED = re.compile(r"\b(fail(ed)?|reversed|reversal|unsuccessful|declined|insufficient|cancel(l)?ed)\b", re.I)

def _bad(why): return {"ok": False, "why": why}

def parse(raw, default_provider="Mobile money", allow_outgoing=False):
    s = re.sub(r"\s+", " ", raw or "").strip()
    if len(s) < 12: return _bad("That text is too short to be a payment SMS.")
    if not allow_outgoing and OUT.search(s) and not re.search(r"received|credited", s, re.I):
        return _bad("This is an outgoing transaction, not a payment received.")
    am = None
    for m in AMT.finditer(s):
        if not re.search(r"balance|fee|charge|cost|tax|levy|bal\b", s[max(0, m.start() - 24):m.start()], re.I):
            am = m; break
    if not am: return _bad("I couldn't find an amount in this message.")
    cu = (am.group(1) or am.group(2) or am.group(5) or "").upper()
    amt = float((am.group(3) or am.group(4)).replace(",", ""))
    code = ""
    m = CODE1.match(s) or CODE2.search(s)
    if m and re.search(r"\d", m.group(1)): code = re.sub(r"[.\-]+$", "", m.group(1).upper())
    payer = phone = ""
    f = FROM.search(s)
    if f:
        ph = PHONE.search(f.group(1))
        if ph: phone = ph.group(0).replace(" ", "")
        payer = re.sub(r"[^A-Za-z.' \-]", "", PHONE.sub("", f.group(1), count=1)).strip()
    prov = ("Airtel Money" if re.search("airtel", s, re.I) else "M-Pesa" if re.search(r"m-?pesa|safaricom", s, re.I)
            else "Telecel Cash" if re.search("telecel|vodafone", s, re.I) else "Orange Money" if re.search("orange", s, re.I)
            else "MTN MoMo" if re.search(r"\bmtn\b|momo", s, re.I) else "M-Pesa" if CODE1.match(s) else default_provider)
    t = 0
    try:
        tm = TIME1.search(s)
        if tm:
            d, mo, y, h, mi, ap = tm.groups(); y = int(y); y = 2000 + y if y < 100 else y; h = int(h)
            if ap:
                ap = ap.upper()
                if ap == "PM" and h < 12: h += 12
                if ap == "AM" and h == 12: h = 0
            t = int(dt.datetime(y, int(mo), int(d), h, int(mi)).timestamp() * 1000)
        else:
            tm = TIME2.search(s)
            if tm: t = int(dt.datetime(*map(int, tm.groups())).timestamp() * 1000)
    except (ValueError, OverflowError):
        t = 0
    failed = bool(FAILED.search(s))
    if not failed and not re.search(r"received|credited|payment|deposit", s, re.I) and not code:
        return _bad("This doesn't look like a payment-received message.")
    return {"ok": True, "amt": amt, "cur": CUR.get(cu, ""), "code": code, "payer": payer or "Unknown payer",
            "phone": phone, "prov": prov, "t": t, "failed": failed, "raw": s[:420]}
