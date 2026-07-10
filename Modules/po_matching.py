"""
Step 5: PO Matching (two-step verification)
For each extracted invoice (which may have 0, 1, or multiple PO
references), this step:

  1. Normalizes PO reference formatting (PO4471 / po-4471 / PO #4471
     / 4471 -> all become "PO-4471") - pure formatting cleanup only,
     the actual digits are never altered or guessed.
  2. Looks up each normalized reference against the PO dataset.
  3. Cross-checks the VENDOR NAME on the matched PO against the
     invoice's vendor name (two-step verification) - a PO number
     match alone is not trusted; the vendor must match too.
  4. If no usable PO reference exists (empty, or no digits found),
     falls back to fuzzy vendor-name matching, as before.
  5. If more than one PO reference is present, every one is checked
     independently and the invoice is always marked "Review Needed",
     with all candidate results pre-computed for a fast human decision.

Run (from Modules folder):
    python po_matching.py
"""

import csv
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

OUTPUT_DIR = Path("../outputs")
EXTRACTED_PATH = OUTPUT_DIR / "extracted_invoices.json"
PO_CSV_PATH = Path("../purchase_orders.csv")
MATCHED_PATH = OUTPUT_DIR / "matched_invoices.json"

FUZZY_MATCH_THRESHOLD = 0.6      # minimum similarity to accept a fuzzy vendor match
VENDOR_CONFIRM_THRESHOLD = 0.85  # minimum similarity to consider two vendor names "the same"


def load_pos():
    with open(PO_CSV_PATH) as f:
        return list(csv.DictReader(f))


def name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower().strip(), (b or "").lower().strip()).ratio()


def normalize_po_reference(raw):
    """Strip all formatting noise, keep only the digits, re-wrap as PO-XXXX.
    Returns None if no digits are present at all (falls back to fuzzy vendor match)."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    return f"PO-{digits}"


def find_po_by_number(normalized_ref, pos):
    for po in pos:
        if normalize_po_reference(po["po_number"]) == normalized_ref:
            return po
    return None


def fuzzy_match_vendor(vendor, pos):
    best_po, best_score = None, 0.0
    for po in pos:
        score = name_similarity(vendor, po["vendor_name"])
        if score > best_score:
            best_score, best_po = score, po
    return best_po, best_score


def check_one_po_reference(raw_ref, invoice_vendor, pos):
    """Two-step verification for a single PO reference: number match + vendor match."""
    normalized = normalize_po_reference(raw_ref)
    candidate = {
        "original_reference": raw_ref,
        "normalized_reference": normalized,
        "po_found": False,
        "matched_po": None,
        "vendor_match": None,
        "vendor_similarity": None,
        "status": None,
    }

    if normalized is None:
        candidate["status"] = "not_found"
        return candidate

    matched_po = find_po_by_number(normalized, pos)
    if matched_po is None:
        candidate["status"] = "not_found"
        return candidate

    candidate["po_found"] = True
    candidate["matched_po"] = matched_po

    similarity = name_similarity(invoice_vendor, matched_po["vendor_name"])
    candidate["vendor_similarity"] = round(similarity, 2)

    if similarity >= VENDOR_CONFIRM_THRESHOLD:
        candidate["vendor_match"] = True
        candidate["status"] = "confirmed"
    else:
        candidate["vendor_match"] = False
        candidate["status"] = "vendor_mismatch"

    return candidate


def match_invoice_to_po(invoice, pos):
    po_refs = [r for r in (invoice.get("po_reference") or []) if r and r.strip()]
    vendor = invoice.get("vendor_name") or ""

    if not po_refs:
        best_po, score = fuzzy_match_vendor(vendor, pos)
        if best_po and score >= FUZZY_MATCH_THRESHOLD:
            return {
                "overall_status": "fuzzy_matched",
                "candidates": [{
                    "original_reference": None, "normalized_reference": None,
                    "po_found": True, "matched_po": best_po,
                    "vendor_match": True, "vendor_similarity": round(score, 2),
                    "status": "fuzzy_matched",
                }],
            }
        return {
            "overall_status": "no_match",
            "candidates": [{
                "original_reference": None, "normalized_reference": None,
                "po_found": False, "matched_po": None,
                "vendor_match": None, "vendor_similarity": round(score, 2) if best_po else None,
                "status": "not_found",
            }],
        }

    candidates = [check_one_po_reference(ref, vendor, pos) for ref in po_refs]

    if len(candidates) > 1:
        overall_status = "multiple_references_review_needed"
    else:
        overall_status = candidates[0]["status"]

    return {"overall_status": overall_status, "candidates": candidates}


def run_matching():
    if not EXTRACTED_PATH.exists():
        print(f"No extracted invoices found at {EXTRACTED_PATH}. Run extraction.py first.")
        return []
    if not PO_CSV_PATH.exists():
        print(f"No PO data found at {PO_CSV_PATH}.")
        return []

    manifest_path = OUTPUT_DIR / "intake_manifest.json"
    current_filenames = None
    if manifest_path.exists():
        with open(manifest_path) as f:
            current_filenames = {e["filename"] for e in json.load(f)}

    with open(EXTRACTED_PATH) as f:
        all_invoices = json.load(f)

    # Only process files genuinely new THIS run - extracted_invoices.json
    # accumulates full history, but re-matching/re-deciding old invoices
    # every run would also cause them to be re-logged into history.
    if current_filenames is not None:
        invoices = [inv for inv in all_invoices if inv.get("source_filename") in current_filenames]
    else:
        invoices = all_invoices

    pos = load_pos()

    results = []
    for invoice in invoices:
        if invoice.get("is_duplicate"):
            invoice["po_match"] = {"overall_status": "skipped_duplicate", "candidates": []}
            results.append(invoice)
            print(f"{invoice.get('source_filename', '?'):30s} -> skipped_duplicate (no matching performed)")
            continue

        match = match_invoice_to_po(invoice, pos)
        invoice["po_match"] = match
        results.append(invoice)

        summary_bits = []
        for c in match["candidates"]:
            ref_label = c["normalized_reference"] or "(fuzzy vendor match)"
            summary_bits.append(f"{ref_label}:{c['status']}")

        print(f"{invoice.get('source_filename', '?'):30s} -> {match['overall_status']:32s} "
              f"[{', '.join(summary_bits)}]")

    with open(MATCHED_PATH, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved matching results -> {MATCHED_PATH}")

    return results


if __name__ == "__main__":
    run_matching()