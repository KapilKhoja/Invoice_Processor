"""
Step: History Logging (SQLite)
Writes each final decision as a permanent row, so there's a full
history across every pipeline run. Updated to read the new po_match
structure (overall_status + candidates list) instead of the old
single matched_po field.

Run (from Modules folder):
    python save_history.py
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

OUTPUT_DIR = Path("../outputs")
DECISIONS_PATH = OUTPUT_DIR / "decisions.json"
DB_PATH = Path("../invoice_history.db")


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invoice_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp TEXT,
            source_filename TEXT,
            vendor_name TEXT,
            invoice_number TEXT,
            invoice_date TEXT,
            po_references TEXT,
            total REAL,
            matched_po_number TEXT,
            po_match_status TEXT,
            decision TEXT,
            decision_reasons TEXT
        )
    """)
    conn.commit()


def get_primary_matched_po(po_match):
    candidates = po_match.get("candidates", [])
    for c in candidates:
        if c.get("status") in ("confirmed", "fuzzy_matched") and c.get("matched_po"):
            return c["matched_po"]["po_number"]
    for c in candidates:
        if c.get("matched_po"):
            return c["matched_po"]["po_number"]
    return None


def save_decisions_to_db(invoices):
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    run_timestamp = datetime.now().isoformat(timespec="seconds")
    saved_count = 0
    skipped_duplicate_count = 0

    for invoice in invoices:
        if invoice.get("is_duplicate"):
            skipped_duplicate_count += 1
            continue

        po_match = invoice.get("po_match", {})

        conn.execute("""
            INSERT INTO invoice_runs (
                run_timestamp, source_filename, vendor_name, invoice_number,
                invoice_date, po_references, total, matched_po_number,
                po_match_status, decision, decision_reasons
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_timestamp,
            invoice.get("source_filename"),
            invoice.get("vendor_name"),
            invoice.get("invoice_number"),
            invoice.get("invoice_date"),
            ", ".join(invoice.get("po_reference") or []),
            invoice.get("total"),
            get_primary_matched_po(po_match),
            po_match.get("overall_status"),
            invoice.get("decision"),
            json.dumps(invoice.get("decision_reasons", [])),
        ))
        saved_count += 1

    conn.commit()
    conn.close()
    return saved_count, skipped_duplicate_count


def run_save_history():
    if not DECISIONS_PATH.exists():
        print(f"No decisions found at {DECISIONS_PATH}. Run validation_decision.py first.")
        return

    with open(DECISIONS_PATH) as f:
        invoices = json.load(f)

    saved_count, skipped_duplicate_count = save_decisions_to_db(invoices)
    print(f"Saved {saved_count} decision(s) to {DB_PATH}"
          + (f" ({skipped_duplicate_count} duplicate(s) not re-logged)" if skipped_duplicate_count else ""))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        "SELECT run_timestamp, source_filename, decision FROM invoice_runs ORDER BY id DESC LIMIT 10"
    )
    print("\nMost recent rows in history:")
    for row in cursor.fetchall():
        print(f"  {row[0]}  |  {row[1]:25s}  |  {row[2]}")
    conn.close()


if __name__ == "__main__":
    run_save_history()