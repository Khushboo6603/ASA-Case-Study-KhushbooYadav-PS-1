"""Invoice-to-decision engine. process() is a generator yielding one event per stage so the UI can show a live run.

Design: AI/OCR is used only where input is messy (reading the document). Every business decision
(vendor, duplicate, PO match, tolerance, final verdict) is plain deterministic code so each decision is explainable.
"""
import os, re, csv, json, time, subprocess, tempfile, difflib, urllib.request
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
TOLERANCE = 0.02          # +/-2% of PO remaining balance
DELAY = float(os.environ.get("STAGE_DELAY", "0.7"))   # slows stages so the live view is watchable

STAGES = [("ingest", "Receive invoice"), ("extract", "Read & extract fields"), ("validate", "Validate completeness & maths"),
          ("vendor", "Check approved vendor"), ("duplicate", "Duplicate detection"), ("po_match", "Match to purchase order"),
          ("decision", "Final decision")]

def load_pos():
    with open(os.path.join(BASE, "data/purchase_orders.csv")) as f:
        return {r["po_number"]: {**r, "po_amount": float(r["po_amount"])} for r in csv.DictReader(f)}

def load_vendors():
    with open(os.path.join(BASE, "data/approved_vendors.csv")) as f:
        return list(csv.DictReader(f))

SUFFIX = re.compile(r"\b(inc|ltd|llc|co|corp|corporation|company|limited|gmbh)\b\.?", re.I)
def canon(name): return re.sub(r"\s+", " ", SUFFIX.sub("", re.sub(r"[.,]", " ", name or "")).strip().lower())
def num(s): return float(s.replace(",", ""))
def pct(a, b): return (a - b) / b * 100

# ---------- reading the document ----------
def read_text(path):
    txt = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True).stdout
    if len(txt.strip()) >= 40:
        return txt, "text-layer"
    with tempfile.TemporaryDirectory() as t:     # scanned: rasterise + OCR
        subprocess.run(["pdftoppm", "-r", "200", "-png", path, os.path.join(t, "p")], check=True)
        out = ""
        for f in sorted(os.listdir(t)):
            out += subprocess.run(["tesseract", os.path.join(t, f), "-", "--psm", "6"], capture_output=True, text=True).stdout
    return out, "ocr"

MONEY = r"\$?\s*([\d,]+\.\d{2})"
DATE_FORMATS = ["%Y-%m-%d", "%d %b %Y", "%b %d, %Y", "%B %d, %Y", "%d %B %Y", "%d/%m/%Y"]
def parse_date(s):
    s = s.strip().rstrip(".")
    for f in DATE_FORMATS:
        try: return datetime.strptime(s, f).date().isoformat()
        except ValueError: pass
    return None

def regex_extract(text):
    d = {}
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    d["vendor"] = lines[0] if lines else None
    m = re.search(r"(?:invoice\s*(?:no\.?|number|#)|inv\s*(?:no\.?|#)|bill\s*no\.?)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{2,})", text, re.I)
    d["invoice_no"] = m.group(1).upper() if m else None
    m = re.search(r"(?:invoice\s*date|dated?)\s*[:\-]?\s*([A-Za-z0-9 ,/\-]{6,20}?)\s*(?:\n|$)", text, re.I)
    d["date"] = parse_date(m.group(1)) if m else None
    m = re.search(r"\bPO[-\s]?(\d{3,6})\b", text, re.I)
    d["po_ref"] = f"PO-{m.group(1)}" if m else None
    m = re.search(r"sub\s*-?total\s*" + MONEY, text, re.I); d["subtotal"] = num(m.group(1)) if m else None
    m = re.search(r"^[ \t]*(?:tax|vat|gst)\b[^\n]*?\s" + MONEY + r"[ \t]*$", text, re.I | re.M); d["tax"] = num(m.group(1)) if m else None
    tot = re.findall(r"(?<!sub)(?<!sub )(?<!sub-)(?:grand\s+total|total\s+due|amount\s+due|invoice\s+total|total)\s*[:\-]?\s*(?:USD)?\s*" + MONEY, text, re.I)
    d["total"] = num(tot[-1]) if tot else None
    items = []
    skip = re.compile(r"^(sub\s*-?total|total|grand|tax|vat|gst|amount\s+due|balance|payment)", re.I)
    for l in lines:
        m = re.search(MONEY + r"\s*$", l)
        if m and not skip.match(l) and not re.search(r"invoice|bill to|date|ref|order", l, re.I):
            items.append({"line": re.sub(r"\s+", " ", l), "amount": num(m.group(1))})
    d["line_items"] = items
    return d

CRITICAL = ["vendor", "invoice_no", "date", "total"]

def llm_fallback(text, missing):
    """Optional: ask an LLM to fill fields regex could not find. Only runs if ANTHROPIC_API_KEY is set."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key: return None, "skipped (no ANTHROPIC_API_KEY set)"
    body = {"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"), "max_tokens": 400,
            "messages": [{"role": "user", "content": "From this invoice text, return ONLY JSON with keys " + ",".join(missing)
                          + " (date as YYYY-MM-DD, total as number, null if truly absent - never guess).\n\n" + text[:6000]}]}
    try:
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", json.dumps(body).encode(),
                                     {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        out = json.load(urllib.request.urlopen(req, timeout=30))["content"][0]["text"]
        return json.loads(re.search(r"\{.*\}", out, re.S).group(0)), "LLM fallback used"
    except Exception as e:
        return None, f"LLM fallback failed ({e.__class__.__name__}); continuing with regex result"

# ---------- the pipeline ----------
def process(path, prior_runs):
    """prior_runs: list of dicts {vendor_key, invoice_no, po, total, decision, run_id} from history."""
    pos, vendors = load_pos(), load_vendors()
    flags, rejects = [], []          # flags -> needs review; rejects -> reject
    ev = lambda stage, status, summary, detail=None, data=None: {"stage": stage, "status": status, "summary": summary, "detail": detail or [], "data": data or {}}
    def pause(): time.sleep(DELAY)

    yield ev("ingest", "running", "Receiving file…"); pause()
    size = os.path.getsize(path)
    yield ev("ingest", "done", f"Received {os.path.basename(path)} ({size/1024:.0f} KB)")

    yield ev("extract", "running", "Reading document…"); pause()
    text, method = read_text(path)
    d = regex_extract(text)
    notes = [f"Method: {'text layer (machine-readable PDF)' if method=='text-layer' else 'scanned image detected → OCR (tesseract)'}"]
    missing = [k for k in CRITICAL if not d.get(k)]
    if missing:
        fill, msg = llm_fallback(text, missing)
        notes.append(msg)
        for k, v in (fill or {}).items():
            if v and k in missing: d[k] = v; notes.append(f"LLM filled {k}")
    notes.append(f"Found: vendor={d['vendor']!r}, invoice_no={d['invoice_no']}, date={d['date']}, PO={d['po_ref']}, total={d['total']}")
    notes.append(f"{len(d['line_items'])} line item(s) detected")
    yield ev("extract", "done", f"Extracted via {'OCR' if method=='ocr' else 'text layer'}", notes, d)

    # validate
    yield ev("validate", "running", "Checking completeness and arithmetic…"); pause()
    v = []
    miss = [k for k in CRITICAL if not d.get(k)]
    if miss:
        flags.append(f"Missing required field(s): {', '.join(miss)}"); v.append("✗ Missing: " + ", ".join(miss))
    else: v.append("✓ All required fields present (vendor, invoice no., date, total)")
    items_sum = round(sum(i["amount"] for i in d["line_items"]), 2)
    arith_ok = True
    if d["total"] is not None:
        if d["subtotal"] is not None and d["tax"] is not None:
            exp = round(d["subtotal"] + d["tax"], 2)
            if abs(exp - d["total"]) > 0.02:
                arith_ok = False; flags.append(f"Arithmetic mismatch: subtotal {d['subtotal']:,.2f} + tax {d['tax']:,.2f} = {exp:,.2f}, but invoice total is {d['total']:,.2f}")
                v.append(f"✗ Subtotal + tax = {exp:,.2f} ≠ total {d['total']:,.2f}")
            else: v.append(f"✓ Subtotal {d['subtotal']:,.2f} + tax {d['tax']:,.2f} = total {d['total']:,.2f}")
        ref = d["subtotal"] if d["subtotal"] is not None else d["total"]
        line_items = [i for i in d["line_items"] if d["subtotal"] is None or True]
        if d["line_items"] and abs(items_sum - ref) > 0.02 and arith_ok:
            # items include subtotal/tax lines? exclude those already filtered; flag genuine mismatch
            flags.append(f"Line items sum {items_sum:,.2f} does not match {'subtotal' if d['subtotal'] is not None else 'total'} {ref:,.2f}"); v.append(f"✗ Line items {items_sum:,.2f} ≠ {ref:,.2f}")
        elif d["line_items"]: v.append(f"✓ Line items sum to {items_sum:,.2f}")
    yield ev("validate", "fail" if (miss or not arith_ok) else "done", "Validation " + ("found problems" if (miss or not arith_ok) else "passed"), v)

    # vendor
    yield ev("vendor", "running", "Comparing vendor to approved list…"); pause()
    vend_name, vend_key, best = d.get("vendor"), None, (0, None)
    if vend_name:
        for r in vendors:
            sc = difflib.SequenceMatcher(None, canon(vend_name), canon(r["vendor"])).ratio()
            if sc > best[0]: best = (sc, r)
    if best[0] >= 0.85:
        vend_key = best[1]["vendor"]
        yield ev("vendor", "done", f"Approved vendor: {vend_key} ({best[1]['vendor_id']})", [f"Name similarity {best[0]:.0%} after normalising suffixes (Inc/Ltd/LLC…)"])
    else:
        rejects.append(f"Vendor '{vend_name}' is not on the approved vendor list")
        yield ev("vendor", "fail", f"NOT an approved vendor: {vend_name}", [f"Closest approved match scored {best[0]:.0%} (threshold 85%)"])

    # duplicate
    yield ev("duplicate", "running", "Searching invoice history…"); pause()
    dup = None
    if d.get("invoice_no"):
        for r in prior_runs:
            if r["decision"] != "REJECT" and r["invoice_no"] == d["invoice_no"] and (r["vendor_key"] == vend_key or r["vendor_key"] is None and vend_key is None):
                dup = r; break
    if not dup and d.get("total") and d.get("date") and vend_key:
        for r in prior_runs:
            if r["decision"] != "REJECT" and r["vendor_key"] == vend_key and r["total"] == d["total"] and r.get("date") == d["date"]:
                dup = r; break
    if dup:
        rejects.append(f"Duplicate of earlier invoice {dup['invoice_no']} (run #{dup['run_id']}, {dup['decision']})")
        yield ev("duplicate", "fail", f"Duplicate of run #{dup['run_id']} ({dup['invoice_no']})", ["Rule: same vendor + invoice number, or same vendor + amount + date, already processed"])
    else:
        yield ev("duplicate", "done", "No duplicate found", [f"Checked {len(prior_runs)} earlier run(s) by vendor+invoice no. and vendor+amount+date"] if d.get("invoice_no") else ["Invoice number missing — only amount+date rule could be applied"])

    # PO match
    yield ev("po_match", "running", "Matching to purchase order…"); pause()
    po, po_note, confidence, remaining, po_fail = None, [], None, None, False
    def billed(pn): return round(sum(r["total"] for r in prior_runs if r["po"] == pn and r["decision"] == "APPROVE" and r["total"]), 2)
    if d.get("total") is None:
        po_note.append("Cannot match: invoice total unavailable"); status, summ = "fail", "Cannot match — total missing"
    elif not vend_key:
        po_note.append("Skipped: vendor not approved"); status, summ = "skipped", "Skipped (vendor not approved)"
    else:
        if d.get("po_ref"):
            po = pos.get(d["po_ref"]); confidence = "explicit"
            if not po:
                flags.append(f"PO reference {d['po_ref']} not found in procurement system"); po_note.append(f"{d['po_ref']} does not exist")
            elif canon(po["vendor"]) != canon(vend_key):
                flags.append(f"PO {d['po_ref']} belongs to {po['vendor']}, not {vend_key}"); po_note.append("PO / vendor mismatch"); po = None
        else:
            cands = []
            for pn, p in pos.items():
                if canon(p["vendor"]) == canon(vend_key):
                    rem = p["po_amount"] - billed(pn)
                    if rem > 0 and abs(pct(d["total"], rem)) <= TOLERANCE * 100: cands.append(p)
            if len(cands) == 1:
                po, confidence = cands[0], "implied"
                flags.append(f"No PO reference on invoice; best match is {po['po_number']} (vendor + amount) — human must confirm")
                po_note.append(f"No PO number on invoice. Implied match by vendor + amount → {po['po_number']} (LOW confidence)")
            else:
                flags.append("No PO reference and no unique PO matches vendor + amount"); po_note.append(f"{len(cands)} candidate POs — cannot infer")
        if po:
            already = billed(po["po_number"]); remaining = round(po["po_amount"] - already, 2)
            if remaining <= 0.005:
                po_note.append(f"PO {po['po_number']} amount {po['po_amount']:,.2f}, already billed {already:,.2f} — nothing left to bill")
                flags.append(f"PO {po['po_number']} is already fully billed ({already:,.2f} of {po['po_amount']:,.2f})")
                po_fail = True
            else:
                diff = pct(d["total"], remaining)
                po_note += [f"PO {po['po_number']}: amount {po['po_amount']:,.2f}, already billed {already:,.2f}, remaining {remaining:,.2f}",
                            f"This invoice {d['total']:,.2f} vs remaining → {diff:+.2f}% (tolerance ±{TOLERANCE*100:.0f}%)"]
                if d["total"] > remaining * (1 + TOLERANCE):
                    over = d["total"] - remaining
                    po_fail = True
                    flags.append(f"Invoice {d['total']:,.2f} exceeds PO {po['po_number']} remaining balance {remaining:,.2f} by {over:,.2f} ({diff:.1f}%) — outside ±{TOLERANCE*100:.0f}% tolerance")
                elif d["total"] < remaining * (1 - TOLERANCE):
                    po_note.append(f"Partial billing: {remaining - d['total']:,.2f} will remain open on {po['po_number']} (split-invoice pattern)")
                elif abs(diff) > 0.005: po_note.append(f"Within tolerance ({diff:+.2f}%) — accepted")
                else: po_note.append("Exact match to remaining balance — PO fully billed")
        status = "done" if po and not po_fail else "fail"
        if confidence == "implied": status = "warn"
        summ = (f"{po['po_number']} ({confidence})" if po else "No reliable PO match") + (f" · remaining {remaining:,.2f}" if remaining is not None else "")
    yield ev("po_match", status, summ, po_note, {"po": po["po_number"] if po else None, "remaining": remaining, "confidence": confidence})

    # decision
    yield ev("decision", "running", "Deciding…"); pause()
    if rejects:
        decision, reasons = "REJECT", rejects + flags
    elif flags:
        decision, reasons = "NEEDS_REVIEW", flags
    else:
        decision = "APPROVE"
        reasons = [f"All checks passed: approved vendor, no duplicate, matches {po['po_number']} within tolerance"] + [n for n in po_note if "Partial" in n or "Within" in n or "fully" in n]
    nxt = {"APPROVE": "Release to payment run.",
           "NEEDS_REVIEW": "Route to AP reviewer. Draft vendor/PO-owner note generated below.",
           "REJECT": "Do not pay. Notify vendor / procurement."}[decision]
    msg = None
    if decision != "APPROVE":
        msg = (f"Hello {d.get('vendor') or 'team'},\n\nWe could not process invoice {d.get('invoice_no') or '(number not found)'} for the following reason(s):\n"
               + "\n".join(f"- {r}" for r in reasons) + "\n\nPlease reply with a corrected invoice or the missing information.\n\nAccounts Payable")
    result = {"decision": decision, "reasons": reasons, "next_action": nxt, "vendor_message": msg, "fields": d, "vendor_key": vend_key,
              "po": po["po_number"] if po else None, "po_confidence": confidence, "po_remaining": remaining}
    yield ev("decision", {"APPROVE": "done", "NEEDS_REVIEW": "warn", "REJECT": "fail"}[decision], decision.replace("_", " "), reasons, result)
