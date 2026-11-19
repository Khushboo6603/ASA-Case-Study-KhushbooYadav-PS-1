"""Generates realistic sample invoice PDFs (3 vendor layouts + 1 scanned image PDF)."""
import os, subprocess, random, tempfile
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from PIL import Image, ImageFilter

OUT = os.path.join(os.path.dirname(__file__), "samples")
os.makedirs(OUT, exist_ok=True)
for f in os.listdir(OUT):
    os.remove(os.path.join(OUT, f))

def money(x): return f"{x:,.2f}"

def draw(path, inv):
    c = canvas.Canvas(path, pagesize=A4)
    W, H = A4
    L = inv["layout"]
    y = H - 60
    c.setFont("Helvetica-Bold", 18); c.drawString(50, y, inv["vendor"])
    c.setFont("Helvetica", 9); y -= 14; c.drawString(50, y, inv["addr"])
    y -= 40
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, {"A": "TAX INVOICE", "B": "INVOICE", "C": "BILL"}[L])
    c.setFont("Helvetica", 10)
    lab = {"A": ("Invoice No:", "Invoice Date:", "PO Number:"),
           "B": ("Invoice #", "Date:", "Your Ref:"),
           "C": ("Bill No.", "Dated:", "Purchase Order:")}[L]
    y -= 22
    if inv["no"]:   c.drawString(50, y, f"{lab[0]} {inv['no']}"); y -= 14
    if inv["date"]: c.drawString(50, y, f"{lab[1]} {inv['date']}"); y -= 14
    if inv["po"]:   c.drawString(50, y, f"{lab[2]} {inv['po']}"); y -= 14
    c.drawString(50, y, "Bill To: Initech Holdings Inc., 500 Market St, Austin TX"); y -= 30
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Description"); c.drawString(330, y, "Qty"); c.drawString(380, y, "Unit Price"); c.drawString(470, y, "Amount")
    y -= 6; c.line(50, y, 545, y); y -= 16
    c.setFont("Helvetica", 10)
    sub = 0
    for d, q, p in inv["items"]:
        amt = round(q * p, 2); sub += amt
        c.drawString(50, y, d); c.drawString(330, y, str(q)); c.drawString(380, y, money(p)); c.drawString(470, y, money(amt)); y -= 16
    y -= 8; c.line(300, y, 545, y); y -= 18
    sub = round(sub, 2)
    if inv.get("tax_rate"):
        tax = round(sub * inv["tax_rate"], 2)
        total = round(sub + tax, 2) if not inv.get("bad_total") else inv["bad_total"]
        c.drawString(330, y, "Subtotal"); c.drawString(470, y, money(sub)); y -= 16
        c.drawString(330, y, f"Tax ({int(inv['tax_rate']*100)}%)"); c.drawString(470, y, money(tax)); y -= 16
    else:
        total = sub
    if inv.get("hide_total"):
        pass
    else:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(330, y, {"A": "Total Due", "B": "Grand Total", "C": "Amount Due"}[L]); c.drawString(470, y, "$" + money(total))
    c.setFont("Helvetica", 8); c.drawString(50, 50, "Payment terms: Net 30. Remit to the bank account on file.")
    c.save()
    return total

def make_scanned(src_pdf, dst_pdf):
    with tempfile.TemporaryDirectory() as t:
        subprocess.run(["pdftoppm", "-r", "150", "-png", src_pdf, os.path.join(t, "p")], check=True)
        png = [f for f in os.listdir(t) if f.endswith(".png")][0]
        im = Image.open(os.path.join(t, png)).convert("L")
        random.seed(3)
        im = im.rotate(0.6, expand=False, fillcolor=255).filter(ImageFilter.GaussianBlur(0.6))
        px = im.load()
        for _ in range(4000):
            px[random.randrange(im.width), random.randrange(im.height)] = random.randrange(120, 255)
        im.convert("RGB").save(dst_pdf, "PDF", resolution=150)

S = [
 # 01 happy path: explicit PO, tax-separated, within PO
 ("01_HAPPY_acme_clean.pdf", dict(layout="A", vendor="Acme Office Supplies Inc.", addr="12 Industrial Way, Dallas TX", no="INV-A-2041", date="2026-11-03", po="PO-1001",
   items=[("A4 paper cartons", 40, 45.00), ("Toner cartridges", 10, 220.00)], tax_rate=0.08)),   # 3,800*1.08? (1800+2200=4000)*1.08=4320
 # 02 EDGE: exact duplicate re-submission of #01 (resend / different filename)
 ("02_EDGE_duplicate_acme.pdf", dict(layout="A", vendor="Acme Office Supplies Inc.", addr="12 Industrial Way, Dallas TX", no="INV-A-2041", date="2026-11-03", po="PO-1001",
   items=[("A4 paper cartons", 40, 45.00), ("Toner cartridges", 10, 220.00)], tax_rate=0.08)),
 # 03 EDGE: split PO - first part (7,000 of 12,000)
 ("03_EDGE_split_northwind_part1.pdf", dict(layout="B", vendor="Northwind Logistics LLC", addr="88 Harbor Rd, Newark NJ", no="NW-310", date="Nov 5, 2026", po="PO-1002",
   items=[("Freight - Shipment batch 1", 1, 7000.00)])),
 # 04 EDGE: split PO - second part completes the PO (cumulative 12,000)
 ("04_EDGE_split_northwind_part2.pdf", dict(layout="B", vendor="Northwind Logistics LLC", addr="88 Harbor Rd, Newark NJ", no="NW-311", date="Nov 12, 2026", po="PO-1002",
   items=[("Freight - Shipment batch 2", 1, 5000.00)])),
 # 05 EDGE: split PO - third invoice would over-bill the PO
 ("05_EDGE_split_northwind_overbill.pdf", dict(layout="B", vendor="Northwind Logistics LLC", addr="88 Harbor Rd, Newark NJ", no="NW-312", date="Nov 15, 2026", po="PO-1002",
   items=[("Freight - Extra shipment", 1, 1500.00)])),
 # 06 EDGE: no PO reference (implied) - bundled single line, tax embedded
 ("06_EDGE_no_po_brightline.pdf", dict(layout="C", vendor="BrightLine IT Services", addr="9 Tech Park, San Jose CA", no="BL-778", date="14 Nov 2026", po=None,
   items=[("Managed IT services - Q4 bundle (tax incl.)", 1, 8200.00)])),
 # 07 amount slightly over PO but within 2% tolerance -> approve with note
 ("07_TOLERANCE_ok_globex.pdf", dict(layout="A", vendor="Globex Packaging", addr="3 Pallet Lane, Reno NV", no="GX-5501", date="2026-11-08", po="PO-1004",
   items=[("Corrugated boxes", 100, 27.90)])),
 # 08 amount over tolerance -> review
 ("08_TOLERANCE_breach_globex.pdf", dict(layout="A", vendor="Globex Packaging", addr="3 Pallet Lane, Reno NV", no="GX-5502", date="2026-11-10", po="PO-1007",
   items=[("Corrugated boxes", 100, 29.00)])),
 # 09 vendor not on approved list -> reject
 ("09_REJECT_unapproved_vendor.pdf", dict(layout="C", vendor="Shadow Traders Ltd", addr="Unit 4, Port Zone, Dubai", no="ST-001", date="12 Nov 2026", po="PO-1003",
   items=[("Consulting services", 1, 8200.00)])),
 # 10 missing invoice number and total -> review
 ("10_EDGE_missing_fields.pdf", dict(layout="B", vendor="Summit Cleaning Co", addr="77 Clean St, Denver CO", no=None, date="Nov 9, 2026", po="PO-1005",
   items=[("Office deep clean", 1, 1800.00)], hide_total=True)),
 # 11 arithmetic mismatch (total != subtotal + tax) -> review
 ("11_EDGE_math_mismatch_acme.pdf", dict(layout="A", vendor="Acme Office Supplies Inc.", addr="12 Industrial Way, Dallas TX", no="INV-A-2050", date="2026-11-16", po="PO-1006",
   items=[("Desk organisers", 20, 40.00)], tax_rate=0.08, bad_total=950.00)),
]
for name, inv in S:
    draw(os.path.join(OUT, name), inv)

# 12 scanned (image-only) invoice -> needs OCR
tmp = os.path.join(OUT, "_tmp.pdf")
draw(tmp, dict(layout="A", vendor="Summit Cleaning Co", addr="77 Clean St, Denver CO", no="SC-9021", date="2026-11-11", po="PO-1005",
               items=[("Monthly office cleaning", 1, 1800.00)]))
make_scanned(tmp, os.path.join(OUT, "12_SCANNED_summit_image_only.pdf"))
os.remove(tmp)
print("Generated:", sorted(os.listdir(OUT)))
