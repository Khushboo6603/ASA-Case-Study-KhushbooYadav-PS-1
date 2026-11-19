# 5-minute video script + live-interview run order

**Before recording / interview:** open app → Dashboard → **Reset history**. Use STAGE_DELAY=0.7 (default). Close other tabs.

## Video (≤5:00)
**0:00–0:30 — Problem.** "AP teams open every PDF, find the PO in a spreadsheet, check numbers, decide. It's slow and error-prone. I built a process that takes an invoice PDF and returns a decision *with the reasoning visible*."
**0:30–1:30 — Happy path (01).** Run 01. Narrate each stage as it lights up: extraction (text layer), maths check, vendor, duplicate, PO match, APPROVE + reason. Mention: *AI reads; plain code decides, so decisions are explainable.*
**1:30–2:15 — Edge 1: Duplicate (02).** Run 02 → REJECT. "Same vendor + invoice number as run #1 — caught by history, filename is irrelevant."
**2:15–3:30 — Edge 2: Split PO (03 → 04 → 05).** 03 approves 7,000 of 12,000 (partial billing noted). 04 approves 5,000 and completes the PO. 05 → Needs review because PO is fully billed. "The system tracks remaining balance, not just single-invoice amount."
**3:30–4:15 — Edge 3: Missing PO (06) or scanned (12).** 06: no PO on invoice → implied match PO-1003 at low confidence → human review, not auto-approve. Optionally 12: image-only PDF → OCR.
**4:15–5:00 — Dashboard + wrap.** Show KPIs, click a row for trace, drafted vendor message. State assumptions + next steps (email ingestion, ERP write-back, per-vendor tolerances).

## Live interview run order (reset first)
01 happy → 02 duplicate → 03, 04, 05 split PO → 06 implied PO → 10 missing fields → 11 math mismatch → 12 scanned. Keep 07/08/09 as backup answers for tolerance and vendor questions.
Also be ready to upload a *brand-new* PDF via drag-and-drop to prove it isn't hard-coded.

## Questions to expect
- *Why not just use an LLM for the whole decision?* Auditability and repeatability; LLM only where inputs are messy.
- *Why Needs review instead of Approve for implied PO?* Wrong-PO payment risk; confidence is low, human confirms.
- *What breaks?* Unseen invoice layouts, multi-page/multi-currency — next steps above.
- *Tolerance choice?* ±2% is an assumption, configurable per vendor in production.
