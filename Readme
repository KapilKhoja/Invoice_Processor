# Invoice Processor

Pipeline that takes vendor invoices (PDF, scanned PDF, image, or plain-text
email body), extracts the data, matches each one against a PO dataset, and
returns a decision: **APPROVED / FLAGGED / REJECTED**, with reasons.

Comes with a NiceGUI dashboard to run the pipeline and browse decision
history.

## Pipeline

```
Input_classifier.py   ->  intake_manifest.json
extraction.py          ->  extracted_invoices.json
po_matching.py          ->  matched_invoices.json
validation_decision.py  ->  decisions.json
save_history.py         ->  invoice_history.db
```

Or run the whole thing from the dashboard:

```bash
python app.py
```

then open `http://localhost:8080`.

## Folder layout

```
project-root/
├── Modules/            # all the .py files above
├── inputs/              # drop invoice files here
├── outputs/              # manifests/JSON produced each run
├── purchase_orders.csv    # PO dataset (po_number, vendor_name, po_amount)
├── invoice_history.db      # sqlite, created by save_history.py
└── .env                     # GROQ_API_KEY=...
```

## Setup

```bash
pip install pdfplumber pymupdf requests python-dotenv nicegui reportlab
```

Create a `.env` file:

```
GROQ_API_KEY=your_key_here
```

Scanned PDFs and images go through Groq vision (`llama-4-scout`) for OCR.

`generate_invoices.py` will generate a handful of sample invoices + a
`purchase_orders.csv` under `inputs/` if you want to test without real data.

## What each step does

- **Input_classifier.py** — routes each file by extension/content: text PDF
  (has a text layer), scanned PDF (no text layer, needs OCR), image, plain
  text/email body, or unsupported. Files already fully processed in a past
  run are skipped.
- **extraction.py** — pulls vendor, invoice number, date, PO reference(s),
  line items, and total out of each file, using pdfplumber, raw text
  parsing, or Groq vision depending on the route. Also runs the duplicate
  and math checks (below).
- **po_matching.py** — normalizes PO reference formatting, looks it up
  against `purchase_orders.csv`, and cross-checks the vendor name on the
  matched PO against the invoice's vendor name. Falls back to fuzzy
  vendor-name matching when there's no usable PO reference.
- **validation_decision.py** — turns the match result into a final
  decision with human-readable reasons.
- **save_history.py** — logs every decision to `invoice_history.db`.

## Edge cases handled

- **Duplicate invoices** — same vendor + invoice number seen in a previous
  run → auto-rejected.
- **Possible duplicates** — same vendor + same total, but invoice number
  doesn't match (or OCR garbled it) → flagged for human review, never
  auto-rejected.
- **No PO reference** — falls back to fuzzy vendor-name matching.
- **PO number exists, wrong vendor** — rejected (likely typo or someone
  citing the wrong PO).
- **Multiple PO references on one invoice** — every reference is checked
  independently; invoice always flagged for review with all candidate
  results pre-computed.
- **Amount outside tolerance** (default 5%) vs. the matched PO → flagged.
- **Math doesn't add up** (line items vs. stated total) → flagged.
- **Currency mismatch** between invoice and matched PO → flagged.
- **Scanned PDFs / image invoices** — routed to OCR instead of direct text
  extraction.
- **Text-only invoices** (email body, no attachment) — parsed as raw text.
- **Unsupported formats** (.xlsx, .docx, etc.) — routed to manual review,
  never silently dropped.
- **Already-processed files** — skipped on subsequent runs so they're not
  re-billed against the OCR API or re-logged into history.

## Notes

- PO amount tolerance is set in `validation_decision.py`
  (`AMOUNT_TOLERANCE_PCT`, default `0.05`).
- Vendor-match thresholds are set in `po_matching.py`
  (`FUZZY_MATCH_THRESHOLD`, `VENDOR_CONFIRM_THRESHOLD`).