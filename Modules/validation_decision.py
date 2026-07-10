"""
Step 6-7: Validation + Decision
Takes matched invoices and turns them into a final call: APPROVED /
FLAGGED / REJECTED, with clear reasoning plus a short glanceable tag.

Duplicate invoices are caught earlier, in extraction.py. If an invoice
arrives here already flagged as a duplicate, we skip all other checks
and reject immediately.

Run (from Modules folder):
    python validation_decision.py
"""

import json
from pathlib import Path

OUTPUT_DIR = Path("../outputs")
MATCHED_PATH = OUTPUT_DIR / "matched_invoices.json"
DECISIONS_PATH = OUTPUT_DIR / "decisions.json"

AMOUNT_TOLERANCE_PCT = 0.05


def check_amount_tolerance(invoice_total, po_amount):
    if invoice_total is None or po_amount is None:
        return None, None
    po_amount = float(po_amount)
    if po_amount == 0:
        return None, None
    variance_pct = abs(invoice_total - po_amount) / po_amount
    return variance_pct <= AMOUNT_TOLERANCE_PCT, variance_pct


def downgrade(current, new):
    order = {"APPROVED": 0, "FLAGGED": 1, "REJECTED": 2}
    return new if order[new] > order[current] else current


def evaluate_invoice(invoice):
    # --- Hard duplicate: caught during extraction, definitively the same invoice ---
    if invoice.get("is_duplicate"):
        return {
            "decision": "REJECTED",
            "reasons": ["Duplicate invoice - this vendor/invoice number combination "
                        "was already processed in a previous run."],
            "short_reason": "Duplicate invoice",
        }

    reasons = []
    short_tags = []
    decision = "APPROVED"

    # --- Soft possible-duplicate: same vendor + amount, but not a confirmed
    # match. Never auto-rejected - always surfaced for a human to confirm. ---
    if invoice.get("is_possible_duplicate"):
        decision = downgrade(decision, "FLAGGED")
        reasons.append(invoice.get("possible_duplicate_note") or
                        "Possible duplicate - same vendor and amount as a previous invoice.")
        short_tags.append("Possible duplicate - verify")

    po_match = invoice.get("po_match", {})
    overall_status = po_match.get("overall_status")
    candidates = po_match.get("candidates", [])
    math_check = invoice.get("math_check", {})
    invoice_total = invoice.get("total")


    if overall_status in ("not_found", "no_match"):
        decision = "REJECTED"
        reasons.append("No matching PO found for this invoice, and no vendor match could be made either.")
        short_tags.append("No PO or vendor match")

    elif overall_status == "vendor_mismatch":
        decision = "REJECTED"
        c = candidates[0]
        reasons.append(
            f"PO reference '{c['normalized_reference']}' exists but is registered to "
            f"'{c['matched_po']['vendor_name']}', not the invoice vendor "
            f"'{invoice.get('vendor_name')}' (similarity: {c['vendor_similarity']:.0%}). "
            f"Possible typo or incorrect PO citation - verify before processing."
        )
        short_tags.append(f"PO belongs to a different vendor ({c['matched_po']['vendor_name']})")

    elif overall_status == "fuzzy_matched":
        decision = downgrade(decision, "FLAGGED")
        c = candidates[0]
        reasons.append(
            f"No PO reference provided - matched by vendor name similarity only "
            f"({c['vendor_similarity']:.0%} match to '{c['matched_po']['vendor_name']}'). "
            f"Confirm this is the correct vendor/PO."
        )
        short_tags.append("No PO reference - matched by vendor name only")
        within_tol, variance = check_amount_tolerance(invoice_total, c["matched_po"]["po_amount"])
        if within_tol is False:
            decision = downgrade(decision, "FLAGGED")
            reasons.append(
                f"Invoice total (${invoice_total:,.2f}) differs from matched PO amount "
                f"(${float(c['matched_po']['po_amount']):,.2f}) by {variance:.1%}, "
                f"exceeding the {AMOUNT_TOLERANCE_PCT:.0%} tolerance."
            )
            short_tags.append(f"Amount variance {variance:.0%}")

    elif overall_status == "confirmed":
        c = candidates[0]
        within_tol, variance = check_amount_tolerance(invoice_total, c["matched_po"]["po_amount"])
        if within_tol is False:
            decision = downgrade(decision, "FLAGGED")
            reasons.append(
                f"Invoice total (${invoice_total:,.2f}) differs from PO amount "
                f"(${float(c['matched_po']['po_amount']):,.2f}) by {variance:.1%}, "
                f"exceeding the {AMOUNT_TOLERANCE_PCT:.0%} tolerance."
            )
            short_tags.append(f"Amount variance {variance:.0%}")

    elif overall_status == "multiple_references_review_needed":
        decision = downgrade(decision, "FLAGGED")
        reasons.append(f"Invoice cites {len(candidates)} PO references - human review required. "
                        f"Pre-computed results for each:")
        short_tags.append(f"{len(candidates)} PO references - needs review")
        for c in candidates:
            ref_label = c["normalized_reference"] or "(no reference)"
            if c["status"] == "confirmed":
                within_tol, variance = check_amount_tolerance(
                    invoice_total, c["matched_po"]["po_amount"]
                )
                amount_note = ""
                if within_tol is False:
                    amount_note = f", but amount differs by {variance:.1%} from this PO"
                elif within_tol is True:
                    amount_note = ", amount within tolerance"
                reasons.append(f"  - {ref_label}: verified, vendor confirmed{amount_note}.")
            elif c["status"] == "vendor_mismatch":
                reasons.append(
                    f"  - {ref_label}: PO exists but registered to a different vendor "
                    f"('{c['matched_po']['vendor_name']}') - likely not the correct reference."
                )
            else:
                reasons.append(f"  - {ref_label}: not found in PO records.")

    if math_check.get("performed") and math_check.get("passed") is False:
        decision = downgrade(decision, "FLAGGED")
        reasons.append(f"Math check failed: {math_check['detail']}")
        short_tags.append("Line items don't add up to total")

    invoice_currency = invoice.get("currency") or "USD"
    if candidates and candidates[0].get("matched_po"):
        po_currency = candidates[0]["matched_po"].get("currency") or "USD"
        if invoice_currency != po_currency:
            decision = downgrade(decision, "FLAGGED")
            reasons.append(
                f"Currency mismatch: invoice appears to be in {invoice_currency}, "
                f"but the matched PO is in {po_currency} - verify conversion before approving."
            )
            short_tags.append(f"Currency mismatch ({invoice_currency} vs {po_currency})")

    if not reasons:
        reasons.append("All checks passed: PO and vendor confirmed, amount within tolerance, "
                        "math verified, no duplicates.")

    short_reason = short_tags[0] if short_tags else "All checks passed"

    return {"decision": decision, "reasons": reasons, "short_reason": short_reason}


def run_validation():
    if not MATCHED_PATH.exists():
        print(f"No matched invoices found at {MATCHED_PATH}. Run po_matching.py first.")
        return []

    with open(MATCHED_PATH) as f:
        invoices = json.load(f)

    results = []

    for invoice in invoices:
        outcome = evaluate_invoice(invoice)
        invoice["decision"] = outcome["decision"]
        invoice["decision_reasons"] = outcome["reasons"]
        invoice["decision_short_reason"] = outcome["short_reason"]
        results.append(invoice)

        print(f"{invoice.get('source_filename', '?'):30s} -> {outcome['decision']}")
        for r in outcome["reasons"]:
            print(f"    {r}")

    with open(DECISIONS_PATH, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved final decisions -> {DECISIONS_PATH}")

    return results


if __name__ == "__main__":
    run_validation()