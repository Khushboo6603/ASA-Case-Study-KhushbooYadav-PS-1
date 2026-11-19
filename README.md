# InvoiceFlow — PS-1: Invoice processing, from PDF to decision

Upload an invoice PDF → the process extracts fields (text layer or OCR), validates, checks vendor, detects duplicates,
matches to a PO with tolerance, and outputs **APPROVE / NEEDS REVIEW / REJECT** with reasons. A live run view shows each stage
as it executes; a dashboard shows history, status, PO utilisation and a full trace per run.

## Run locally
```bash
# system deps: poppler-utils (pdftotext/pdftoppm) and tesseract-ocr
pip install -r requirements.txt
python generate_data.py      # (re)creates the 12 sample invoices in samples/
python app.py                # http://localhost:5000
```
## Deploy (needs a public link for submission)
Use the included `Dockerfile` on Render / Railway / Fly.io (poppler + tesseract are installed in the image). Set PORT if required.
Optional: set `ANTHROPIC_API_KEY` to enable the LLM fallback for fields the rules could not read.

## Design choice (say this in the interview)
AI/OCR is used only where input is messy — *reading* the document. Every business decision (vendor, duplicate, PO match,
tolerance, verdict) is plain deterministic code, so each decision is repeatable and explainable to an auditor.
The optional LLM fallback only fills missing extraction fields and is told never to guess.

## Rules
| Check | Rule | Outcome |
|---|---|---|
| Completeness | vendor, invoice no., date, total required | missing → Needs review |
| Arithmetic | subtotal + tax = total; line items = subtotal (±0.02) | mismatch → Needs review |
| Vendor | fuzzy match (≥85%, ignoring Inc/Ltd/LLC) to approved list | not approved → **Reject** |
| Duplicate | same vendor + invoice no., or same vendor + amount + date, vs history | duplicate → **Reject** |
| PO match | explicit PO ref; else implied by vendor + amount (unique candidate only) | implied → Needs review (low confidence) |
| Tolerance | invoice vs PO *remaining* balance, ±2%; above → review; below → partial billing allowed | over → Needs review |
| Split PO | remaining balance = PO amount − approved invoices on that PO | over-billing / fully billed → Needs review |
Priority: any Reject beats Needs review beats Approve.

## Samples (run in order 01 → 12 after a reset)
| File | Scenario | Expected |
|---|---|---|
| 01 | Happy path, explicit PO, tax separate | APPROVE |
| 02 | **Edge:** same invoice re-sent | REJECT (duplicate) |
| 03 / 04 | **Edge:** split PO — 7,000 then 5,000 of 12,000 | APPROVE / APPROVE (PO fully billed) |
| 05 | **Edge:** third invoice on already fully-billed PO | NEEDS REVIEW |
| 06 | **Edge:** no PO number, bundled line, tax included | NEEDS REVIEW (implied PO-1003) |
| 07 | +1.45% over PO, inside tolerance | APPROVE with note |
| 08 | +5.45% over PO, outside tolerance | NEEDS REVIEW |
| 09 | Vendor not on approved list | REJECT |
| 10 | **Edge:** invoice no. and total missing | NEEDS REVIEW |
| 11 | **Edge:** subtotal + tax ≠ total | NEEDS REVIEW |
| 12 | Scanned image-only PDF → OCR | APPROVE |

## Assumptions (note these for the pitch)
- Single currency (USD); one invoice per PDF; invoices arrive as files (email ingestion would be the next integration).
- Only *approved* invoices consume PO balance; items awaiting review do not.
- "Needs review" = a human decides; the process never pays or emails by itself — vendor messages are drafts.
- Tolerance ±2% and vendor-match threshold 85% are config constants at the top of `engine.py` / in the vendor stage.
- Extraction is rule-based tuned to the 3 invoice layouts + OCR; a production version would add an LLM/layout model for unseen formats.

## Known limitations / next steps
Multi-page and multi-currency invoices; email inbox ingestion; ERP write-back; approver queue with sign-off; per-vendor tolerance rules.
