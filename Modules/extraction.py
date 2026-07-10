"""
Step 3: Extraction (all-in-one)
Handles every input type in one place:
  - text_pdf     -> pdfplumber direct text extraction
  - text_only    -> plain-text email body parsing
  - scanned_pdf  -> rendered to images, sent to Groq vision API
  - image        -> sent directly to Groq vision API
  - unsupported  -> skipped (handled upstream as manual review)

Also handles:
  - Math check (line items sum to total) - edge case #14
  - po_reference stored as a LIST (an invoice can reference more than
    one PO; single-PO invoices just get a list with one item)
  - A processed-files registry so files already extracted are never
    re-processed (avoids wasted Groq API calls and duplicate work)

Run (from Modules folder):
    python extraction.py
"""

from dotenv import load_dotenv
load_dotenv()
import base64
import json
import os
import re
from pathlib import Path

import fitz          # PyMuPDF - for rendering scanned PDFs to images
import pdfplumber
import requests

INPUT_DIR = Path("../inputs")
OUTPUT_DIR = Path("../outputs")
MANIFEST_PATH = OUTPUT_DIR / "intake_manifest.json"
EXTRACTED_PATH = OUTPUT_DIR / "extracted_invoices.json"
PROCESSED_FILES_LOG = OUTPUT_DIR / "processed_files.json"
PROCESSED_INVOICES_LOG = OUTPUT_DIR / "processed_invoices_log.json"

# --- Groq vision config ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "GROQ_API_KEY")
GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_IMAGES_PER_REQUEST = 5

OCR_PROMPT = """You are looking at one or more pages of a vendor invoice.
Extract the following fields and return ONLY valid JSON, no other text:

{
  "vendor_name": string or null,
  "invoice_number": string or null,
  "invoice_date": string or null,
  "po_reference": [array of strings, e.g. ["PO-4471"] or ["PO-4471", "PO-4473"], empty array if none found],
  "line_items": [ {"description": string, "qty": number or null, "unit_price": number or null, "amount": number} ],
  "subtotal": number or null,
  "tax": number or null,
  "total": number or null
  "currency": string - the 3-letter currency code (e.g. "USD", "EUR", "GBP") based on
    the symbol or wording used on the invoice. Default to "USD" if unclear.
}

If a field is not visible or not present, use null (or empty array for po_reference).
If line items appear across multiple pages provided, include all of them.
Return ONLY the JSON object, nothing else - no markdown formatting, no explanation."""


def blank_invoice_data() -> dict:
    return {
        "vendor_name": None,
        "invoice_number": None,
        "invoice_date": None,
        "po_reference": [],
        "line_items": [],
        "subtotal": None,
        "tax": None,
        "total": None,
        "currency": None,
    }


def detect_currency(text: str) -> str:
    """Light-touch currency detection - no conversion, just flags which
    currency the invoice appears to be in, so PO mismatches can be caught."""
    if "€" in text or "EUR" in text.upper():
        return "EUR"
    if "£" in text or "GBP" in text.upper():
        return "GBP"
    return "USD"


def extract_po_references(text: str) -> list:
    match = re.search(r"PO Reference:\s*(.+)", text)
    if not match:
        return []
    raw = match.group(1).strip()
    if raw.startswith("(none"):
        return []
    tokens = re.split(r",|\band\b", raw)
    refs = [t.strip() for t in tokens if t.strip()]
    return refs

# ----------------------------------------------------------------------
# TEXT-BASED PDF EXTRACTION
# ----------------------------------------------------------------------
def extract_text_pdf(filepath: Path) -> dict:
    with pdfplumber.open(filepath) as pdf:
        text = pdf.pages[0].extract_text() or ""

    data = blank_invoice_data()
    data["currency"] = detect_currency(text)
    lines = text.split("\n")

    if lines:
        data["vendor_name"] = lines[0].strip()

    inv_match = re.search(r"Invoice #:\s*(\S+)\s+Date:\s*(\S+)", text)
    if inv_match:
        data["invoice_number"] = inv_match.group(1)
        data["invoice_date"] = inv_match.group(2)

    data["po_reference"] = extract_po_references(text)

    in_table = False
    for line in lines:
        if line.startswith("Description"):
            in_table = True
            continue
        if line.startswith("Subtotal:"):
            in_table = False
        if in_table:
            item_match = re.match(r"^(.+?)\s+(\S+)\s+(\S+)\s+\$([\d,]+\.\d{2})$", line)
            if item_match:
                desc, qty, unit_price, amount = item_match.groups()
                data["line_items"].append({
                    "description": desc.strip(),
                    "qty": qty if qty != "-" else None,
                    "unit_price": unit_price if unit_price != "-" else None,
                    "amount": float(amount.replace(",", "")),
                })

    subtotal_match = re.search(r"Subtotal:\s*\$([\d,]+\.\d{2})", text)
    tax_match = re.search(r"Tax:\s*\$([\d,]+\.\d{2})", text)
    total_match = re.search(r"Total:\s*\$([\d,]+\.\d{2})", text)

    if subtotal_match:
        data["subtotal"] = float(subtotal_match.group(1).replace(",", ""))
    if tax_match:
        data["tax"] = float(tax_match.group(1).replace(",", ""))
    if total_match:
        data["total"] = float(total_match.group(1).replace(",", ""))

    return data


# ----------------------------------------------------------------------
# BODY-ONLY TEXT EMAIL EXTRACTION
# ----------------------------------------------------------------------
def extract_text_only(filepath: Path) -> dict:
    text = filepath.read_text()
    data = blank_invoice_data()

    inv_match = re.search(r"Invoice #:\s*(\S+)", text)
    date_match = re.search(r"Date:\s*(\S+)", text)
    total_match = re.search(r"Total Due:\s*\$([\d,]+\.\d{2})", text)
    from_match = re.search(r"From:\s*\S+@([\w\-]+)\.", text)

    if inv_match:
        data["invoice_number"] = inv_match.group(1)
    if date_match:
        data["invoice_date"] = date_match.group(1)
    if total_match:
        data["total"] = float(total_match.group(1).replace(",", ""))
    if from_match:
        data["vendor_name"] = from_match.group(1).replace("-", " ").title()

    data["po_reference"] = extract_po_references(text)

    return data


# ----------------------------------------------------------------------
# OCR EXTRACTION (scanned PDF or direct image) VIA GROQ
# ----------------------------------------------------------------------
def pdf_to_images(filepath: Path, dpi: int = 200) -> list:
    images = []
    doc = fitz.open(filepath)
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    for page in doc:
        pix = page.get_pixmap(matrix=matrix)
        images.append(pix.tobytes("png"))
    doc.close()
    return images


def call_groq_vision(image_batch: list) -> dict:
    content = [{"type": "text", "text": OCR_PROMPT}]
    for img_bytes in image_batch:
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}

    response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    raw_text = response.json()["choices"][0]["message"]["content"]

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        cleaned = raw_text.strip().strip("```").replace("json", "", 1).strip()
        return json.loads(cleaned)


def merge_ocr_batches(batch_results: list) -> dict:
    merged = blank_invoice_data()
    for batch in batch_results:
        for field in ["vendor_name", "invoice_number", "invoice_date", "currency"]:
            if merged[field] is None and batch.get(field):
                merged[field] = batch[field]

        for ref in (batch.get("po_reference") or []):
            if ref not in merged["po_reference"]:
                merged["po_reference"].append(ref)

        merged["line_items"].extend(batch.get("line_items") or [])

        for field in ["subtotal", "tax", "total"]:
            if batch.get(field) is not None:
                merged[field] = batch[field]

    return merged


def extract_via_ocr(filepath: Path, route: str) -> dict:
    if route == "scanned_pdf":
        images = pdf_to_images(filepath)
    else:  # "image"
        images = [filepath.read_bytes()]

    batches = [images[i:i + MAX_IMAGES_PER_REQUEST]
               for i in range(0, len(images), MAX_IMAGES_PER_REQUEST)]

    print(f"  {filepath.name}: {len(images)} page(s)/image(s) -> {len(batches)} Groq batch(es)")

    batch_results = []
    for i, batch in enumerate(batches, start=1):
        print(f"    Sending batch {i}/{len(batches)} ({len(batch)} image(s)) to Groq...")
        batch_results.append(call_groq_vision(batch))

    return merge_ocr_batches(batch_results)


# ----------------------------------------------------------------------
# MATH CHECK (edge case #14)
# ----------------------------------------------------------------------
def check_math(data: dict) -> dict:
    check = {"performed": False, "passed": None, "detail": None}

    if not data["line_items"] or data["total"] is None:
        check["detail"] = "Not enough data to verify (missing line items or total)."
        return check

    items_sum = sum(item["amount"] for item in data["line_items"] if item.get("amount") is not None)
    expected_total = items_sum + (data["tax"] or 0)
    check["performed"] = True

    if abs(expected_total - data["total"]) < 0.01:
        check["passed"] = True
        check["detail"] = f"Line items + tax (${expected_total:,.2f}) match stated total."
    else:
        check["passed"] = False
        check["detail"] = (
            f"Line items + tax sum to ${expected_total:,.2f}, but stated total is "
            f"${data['total']:,.2f} - mismatch of ${abs(expected_total - data['total']):,.2f}."
        )

    return check


# ----------------------------------------------------------------------
# PROCESSED-FILES REGISTRY (avoid re-processing / re-billing Groq calls)
# ----------------------------------------------------------------------
def load_processed_files() -> list:
    if PROCESSED_FILES_LOG.exists():
        with open(PROCESSED_FILES_LOG) as f:
            return json.load(f)
    return []


def save_processed_files(processed: list):
    with open(PROCESSED_FILES_LOG, "w") as f:
        json.dump(processed, f, indent=2)


def load_existing_extractions() -> list:
    if EXTRACTED_PATH.exists():
        with open(EXTRACTED_PATH) as f:
            return json.load(f)
    return []


def load_processed_invoices() -> list:
    """Business-level duplicate log. Stores full records (not just keys) so
    we can also do a SOFT duplicate check (same vendor + same amount, even
    if the invoice number is missing/garbled by OCR - e.g. the same invoice
    sent once as a PDF and once as a photo)."""
    if PROCESSED_INVOICES_LOG.exists():
        with open(PROCESSED_INVOICES_LOG) as f:
            return json.load(f)
    return []


def save_processed_invoices(processed: list):
    with open(PROCESSED_INVOICES_LOG, "w") as f:
        json.dump(processed, f, indent=2)


def normalize_for_matching(text: str) -> str:
    """Lowercase, strip whitespace/punctuation - so 'INV-2049', 'inv 2049',
    and 'INV2049' are all treated as the same identity."""
    import re as _re
    return _re.sub(r"[^a-z0-9]", "", (text or "").lower())


def make_invoice_key(data: dict) -> str:
    return f"{normalize_for_matching(data.get('vendor_name'))}::{normalize_for_matching(data.get('invoice_number'))}"


def find_possible_duplicate(data, processed_invoices):
    """Soft check: same vendor + same total amount, even without a matching
    invoice number. This is NEVER auto-rejected - it's evidence for a human
    to look at, not proof."""
    vendor_norm = normalize_for_matching(data.get("vendor_name"))
    total = data.get("total")
    if not vendor_norm or total is None:
        return None

    for record in processed_invoices:
        if record.get("vendor_norm") == vendor_norm and record.get("total") == total:
            return record
    return None


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def run_extraction():
    if not MANIFEST_PATH.exists():
        print(f"No intake manifest found at {MANIFEST_PATH}. Run intake classifier first.")
        return []

    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    if not manifest:
        print("No new files to extract.")
        return []

    processed_files = load_processed_files()
    processed_invoices = load_processed_invoices()
    all_results = load_existing_extractions()
    new_results = []

    for entry in manifest:
        filename = entry["filename"]

        if filename in processed_files:
            continue

        filepath = INPUT_DIR / filename
        route = entry["route"]

        try:
            if route == "text_pdf":
                data = extract_text_pdf(filepath)
            elif route == "text_only":
                data = extract_text_only(filepath)
            elif route in ("scanned_pdf", "image"):
                data = extract_via_ocr(filepath, route)
            else:
                print(f"{filename:30s} -> route '{route}', extraction not supported, skipping.")
                continue
        except Exception as e:
            print(f"{filename:30s} -> extraction failed: {e}")
            continue

        data["source_filename"] = filename
        data["math_check"] = check_math(data)
        data["is_duplicate"] = False
        data["is_possible_duplicate"] = False
        data["possible_duplicate_note"] = None

        has_identity = bool((data.get("vendor_name") or "").strip()) and \
                       bool((data.get("invoice_number") or "").strip())

        if has_identity:
            invoice_key = make_invoice_key(data)
            existing_keys = {r["key"] for r in processed_invoices}

            if invoice_key in existing_keys:
                data["is_duplicate"] = True
                print(f"{filename:30s} -> DUPLICATE (vendor+invoice# already processed) - "
                      f"skipping PO matching and validation.")
            else:
                soft_match = find_possible_duplicate(data, processed_invoices)
                if soft_match:
                    data["is_possible_duplicate"] = True
                    data["possible_duplicate_note"] = (
                        f"Same vendor and amount (${data['total']:,.2f}) as previously "
                        f"processed file '{soft_match['source_filename']}' - invoice number "
                        f"differs or wasn't matched. Verify this isn't the same invoice."
                    )
                    print(f"{filename:30s} -> POSSIBLE DUPLICATE - {data['possible_duplicate_note']}")
                else:
                    print(f"{filename:30s} -> extracted. Vendor: {data['vendor_name']}, "
                          f"PO ref(s): {data['po_reference']}, Total: {data['total']}, "
                          f"Math check passed: {data['math_check']['passed']}")

                processed_invoices.append({
                    "key": invoice_key,
                    "vendor_norm": normalize_for_matching(data.get("vendor_name")),
                    "total": data.get("total"),
                    "invoice_date": data.get("invoice_date"),
                    "source_filename": filename,
                })
        else:
            soft_match = find_possible_duplicate(data, processed_invoices)
            if soft_match:
                data["is_possible_duplicate"] = True
                data["possible_duplicate_note"] = (
                    f"Same vendor and amount as previously processed file "
                    f"'{soft_match['source_filename']}' - no invoice number available to confirm."
                )
                print(f"{filename:30s} -> POSSIBLE DUPLICATE - {data['possible_duplicate_note']}")
            else:
                print(f"{filename:30s} -> extracted. Vendor: {data['vendor_name']}, "
                      f"PO ref(s): {data['po_reference']}, Total: {data['total']}, "
                      f"Math check passed: {data['math_check']['passed']} "
                      f"(duplicate check limited - missing vendor/invoice number)")

        new_results.append(data)
        processed_files.append(filename)

    all_results.extend(new_results)

    with open(EXTRACTED_PATH, "w") as f:
        json.dump(all_results, f, indent=2)
    save_processed_files(processed_files)
    save_processed_invoices(processed_invoices)

    print(f"\nExtracted {len(new_results)} new file(s) this run. "
          f"Total in history: {len(all_results)}. Saved -> {EXTRACTED_PATH}")

    return new_results


if __name__ == "__main__":
    run_extraction()