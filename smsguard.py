"""Decides whether a mobile-money SMS is GENUINE for each network (MTN MoMo, Airtel Money, M-Pesa).
Checks: who sent it (the network's sender ID, not a normal phone number), its wording/structure, the transaction-code
format, and scam wording. Admins can add extra formats from the panel (SMS Listener > Formats)."""
import json, re
import sms, settings

PROFILES = {
  "MTN MoMo": {
    "sender": r"^(mtn|momo|mtn[ _\-]?momo|mtnmobilemoney|mobilemoney|mtn[ _\-]?(ug|rw|gh|cm|zm|ci|ng|za|bj|sz|ss)|momopay|mtncash)$",
    "words": r"\b(mtn|momo|mobile ?money)\b",
    "code": r"^[A-Z0-9]{8,14}$",
    "formats": [
      r"you have received .{3,40}? from .{2,60}",
      r"payment (?:of |for )?.{0,12}?[\d,.]+.{0,6}? (?:has been )?received",
      r"(?:financial )?trans(?:action)? ?id ?[:#]? ?\w{6,}",
      r"(?:deposit|cash ?in|credited).{0,40}?[\d,.]+",
      r"you have been credited"],
  },
  "Airtel Money": {
    "sender": r"^(airtel|airtel[ _\-]?money|airtelmoney|airtel[ _\-]?(ug|rw|ke|tz|zm|mw|ng|cd|mg)|airtelcash|airtelmoni)$",
    "words": r"\b(airtel|airtel ?money)\b",
    "code": r"^(?:[A-Z]{2}\d{6}\.\d{4}\.[A-Z0-9]{5,8}|[A-Z0-9]{8,14})$",
    "formats": [
      r"(?:trans(?:action)?\.? ?id|tid|txn ?id)\s*[:#.]?\s*[A-Z0-9][A-Z0-9.\-]{5,}",
      r"you have received .{3,40}? from .{2,60}",
      r"(?:received|credited).{0,40}?[\d,.]+.{0,40}?from",
      r"airtel ?money"],
  },
  "M-Pesa": {
    "sender": r"^(m-?pesa|mpesa|safaricom)$",
    "words": r"\b(m-?pesa|safaricom)\b",
    "code": r"^[A-Z0-9]{10}$",
    "formats": [
      r"^[A-Z0-9]{10} confirmed\.? .{0,12}?[\d,.]+ received from .{2,60}",
      r"new m-?pesa balance is",
      r"transaction cost, ?"],
  },
  "Telecel Cash": {"sender": r"^(telecel|vodafone|telecel[ _\-]?cash|vodafone[ _\-]?cash)$", "words": r"\b(telecel|vodafone)\b",
                   "code": r"^[A-Z0-9]{8,16}$", "formats": [r"(?:received|credited).{0,40}?[\d,.]+", r"trans(?:action)? ?id"]},
  "Orange Money": {"sender": r"^(orange|orange[ _\-]?money|orangemoney)$", "words": r"\borange\b",
                   "code": r"^[A-Z0-9]{8,16}$", "formats": [r"(?:received|credited).{0,40}?[\d,.]+", r"trans(?:action)? ?id"]},
}
SCAM = re.compile(r"(https?://|www\.|bit\.ly|click|reply with|enter your pin|send your pin|your pin|congratulations|winner|"
                  r"wrongly (?:sent|deposited)|please (?:return|refund|reverse)|kindly (?:return|refund|reverse)|reversal request|"
                  r"call me|urgent)", re.I)
PHONE_SENDER = re.compile(r"^\+?[\d ]{7,16}$")

def _formats(provider):
    out = list(PROFILES.get(provider, {}).get("formats", []))
    try:
        for f in json.loads(settings.get("sms_formats") or "[]"):
            if f.get("provider") == provider and f.get("regex"): out.append(f["regex"])
    except ValueError: pass
    return out

def key(code): return re.sub(r"[^A-Z0-9]", "", (code or "").upper())

def assess(body, sender=None, trusted_source=False):
    """Returns dict(provider, verdict, score, reasons, parsed).
    verdict: genuine | format_ok (looks right but source can't be proven) | suspicious | fake | outgoing | unreadable."""
    p = sms.parse(body, "", allow_outgoing=False)
    s = re.sub(r"\s+", " ", body or "").strip()
    if not p["ok"]:
        out = "outgoing" if "outgoing" in p["why"] else "unreadable"
        return dict(provider="", verdict=out, score=0, reasons=[p["why"]], parsed=p, scam=False)
    prov = p["prov"] if p["prov"] in PROFILES else ""
    if not prov:                                             # unknown wording: try the sender ID
        for n, pr in PROFILES.items():
            if sender and re.match(pr["sender"], sender.strip(), re.I): prov = n; break
    score, why = 0, []
    sd = (sender or "").strip()
    if sd and prov:
        if re.match(PROFILES[prov]["sender"], sd, re.I): score += 35; why.append(f"Sender ID '{sd}' matches {prov}")
        elif PHONE_SENDER.match(sd): score -= 40; why.append(f"Sent from an ordinary phone number ({sd}), not from {prov}")
        else: score -= 25; why.append(f"Sender '{sd}' is not a known {prov} ID")
    elif not sd: why.append("No sender given (pasted text) – source can't be proven")
    pr = PROFILES.get(prov)
    if pr:
        if re.search(pr["words"], s, re.I): score += 10
        if any(re.search(f, s, re.I) for f in _formats(prov)): score += 25; why.append(f"Wording matches a known {prov} format")
        else: score -= 10; why.append(f"Wording doesn't match any known {prov} format")
        if p["code"] and re.match(pr["code"], key(p["code"]) if prov != "Airtel Money" else p["code"].upper()): score += 15; why.append("Transaction code format is valid")
        elif p["code"]: score -= 15; why.append("Transaction code format is not valid for this network")
        else: why.append("No transaction code found")
    else: why.append("Could not tell which network this message is from")
    if re.search(r"received|credited|deposit", s, re.I): score += 10
    score += 10                                              # amount was found by the parser
    if SCAM.search(s): score -= 40; why.append("Contains scam-style wording (link, PIN request, refund request…)")
    score = max(0, min(100, score))
    if score >= 70 and (trusted_source or sd): v = "genuine" if trusted_source else "format_ok"
    elif score >= 55 and not sd: v = "format_ok"
    elif score >= 35: v = "suspicious"
    else: v = "fake"
    if v == "genuine" and (p["failed"] or SCAM.search(s)): v = "suspicious"
    return dict(provider=prov or p["prov"], verdict=v, score=score, reasons=why, parsed=p, scam=bool(SCAM.search(s)))
