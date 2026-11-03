"""Teach-by-example: turn ONE real SMS into a reusable pattern. Amount, transaction code, payer name, phone, dates and
balances become variable slots; the surrounding wording stays fixed. Nothing the user pastes is stored except the pattern."""
import re
import sms

NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
TIME = re.compile(r"\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}(?:,?\s+(?:at\s+)?|T)\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?|\d{1,2}:\d{2}(?::\d{2})?\s*[AaPp][Mm]")

def learn(sample, provider="", code_hint=""):
    s = re.sub(r"\s+", " ", sample or "").strip()
    if len(s) < 20: raise ValueError("Paste the whole SMS (it looks too short).")
    p = sms.parse(s, "", allow_outgoing=True)
    if not p["ok"] and "outgoing" not in p["why"]: raise ValueError(p["why"])
    if not p["ok"]: raise ValueError("I couldn't find an amount in that message.")
    direction = "out" if sms.OUT.search(s) and not re.search(r"received|credited", s, re.I) else "in"
    marks, work = {}, s
    def mark(name, text):
        nonlocal work
        if text and text in work and name not in marks:
            work = work.replace(text, f"\x00{name}\x00", 1); marks[name] = text
    code = (code_hint or "").strip() if (code_hint or "").strip() in s else (p.get("code") or "")
    fm = sms.FROM.search(s)
    payer_lit = re.split(r"\s*\+?\d[\d ]{8,13}\d", fm.group(1))[0].strip() if fm else ""
    if code and code in payer_lit: payer_lit = payer_lit.split(code)[0].strip()
    mark("CODE", code)
    if payer_lit and len(payer_lit) > 1: mark("PAYER", payer_lit)
    mark("PHONE", p.get("phone") or "")
    for m in TIME.finditer(work):                                  # dates / times
        work = work.replace(m.group(0), "\x00TIME\x00", 1)
    amt_done = False                                               # the paid amount = first number equal to it, not a balance/fee
    for m in NUM.finditer(work):
        try: v = float(m.group(0).replace(",", ""))
        except ValueError: continue
        if v == p["amt"] and not re.search(r"balance|fee|charge|cost|bal\b", work[max(0, m.start() - 24):m.start()], re.I):
            work = work[:m.start()] + "\x00AMT\x00" + work[m.end():]; amt_done = True; break
    if not amt_done: raise ValueError("I couldn't locate the paid amount in that message.")
    work = NUM.sub("\x00NUM\x00", work)
    out = []
    for part in re.split(r"(\x00[A-Z]+\x00)", work):
        if part.startswith("\x00"):
            k = part.strip("\x00")
            out.append({"AMT": r"(?P<amt>\d[\d,]*(?:\.\d+)?)", "CODE": r"(?P<code>[A-Za-z0-9][A-Za-z0-9.\-]{4,24})", "PAYER": r"(?P<payer>.{2,70}?)",
                        "PHONE": r"(?P<phone>[\d*+ ]{7,16})", "TIME": r".{6,32}?", "NUM": r"\d[\d,]*(?:\.\d+)?"}[k])
        else: out.append(re.sub(r"(?:\\ )+", r"\\s+", re.escape(part)))
    rx = "".join(out)
    if not re.search(rx, s, re.I): raise ValueError("I couldn't build a reliable pattern from that message.")
    if len(re.sub(r"\(\?P<\w+>.*?\)|\\s\+|[^A-Za-z]", "", rx)) < 8: raise ValueError("That message has too little fixed wording to learn from.")
    return {"provider": provider or p["prov"], "regex": rx, "dir": direction,
            "warning": "" if code else "No transaction code was found. If your SMS has one, type it in the code box and save again – otherwise payments will go to 'review'.",
            "preview": {"amount": p["amt"], "code": code, "payer": payer_lit, "type": "payment you sent" if direction == "out" else "payment received"}}
