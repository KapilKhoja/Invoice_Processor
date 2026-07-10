# Generates sample invoice PDFs for testing the pipeline.

import csv
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

INPUT_DIR = Path("inputs")
INPUT_DIR.mkdir(exist_ok=True)

PO_DATA = [
    {"po_number": "PO-4471", "vendor_name": "ACME Supplies Inc.", "po_amount": "1400.00"},
    {"po_number": "PO-4472", "vendor_name": "Bolt & Fastener Co.", "po_amount": "850.00"},
    {"po_number": "PO-4473", "vendor_name": "ACME Supplies Inc.", "po_amount": "2200.00"},
    {"po_number": "PO-4474", "vendor_name": "Bright Line Traders", "po_amount": "600.00"},
]

# PO data as CSV for the matching step
with open("purchase_orders.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["po_number", "vendor_name", "po_amount"])
    writer.writeheader()
    writer.writerows(PO_DATA)


def draw_invoice(filepath, vendor, invoice_number, invoice_date, po_ref,
                  line_items, subtotal, tax, total, note=None):
    c = canvas.Canvas(str(filepath), pagesize=letter)
    width, height = letter
    y = height - 72

    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, y, vendor)
    y -= 24

    c.setFont("Helvetica", 10)
    c.drawString(72, y, f"Invoice #: {invoice_number}        Date: {invoice_date}")
    y -= 14
    c.drawString(72, y, f"PO Reference: {po_ref if po_ref else '(none provided)'}")
    y -= 14
    c.drawString(72, y, "Bill To: Zamp Technologies")
    y -= 28

    c.setFont("Helvetica-Bold", 10)
    c.drawString(72, y, "Description")
    c.drawString(300, y, "Qty")
    c.drawString(360, y, "Unit price")
    c.drawString(460, y, "Amount")
    y -= 6
    c.line(72, y, 540, y)
    y -= 16

    c.setFont("Helvetica", 10)
    for desc, qty, unit_price, amount in line_items:
        c.drawString(72, y, desc)
        c.drawString(300, y, str(qty) if qty is not None else "-")
        c.drawString(360, y, f"${unit_price:,.2f}" if unit_price is not None else "-")
        c.drawString(460, y, f"${amount:,.2f}")
        y -= 16

    y -= 8
    c.line(360, y, 540, y)
    y -= 16
    c.drawString(360, y, "Subtotal:")
    c.drawString(460, y, f"${subtotal:,.2f}")
    y -= 16
    c.drawString(360, y, "Tax:")
    c.drawString(460, y, f"${tax:,.2f}")
    y -= 16
    c.setFont("Helvetica-Bold", 10)
    c.drawString(360, y, "Total:")
    c.drawString(460, y, f"${total:,.2f}")

    if note:
        y -= 40
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(72, y, note)

    c.save()


def main():
    # 1. clean match
    draw_invoice(
        INPUT_DIR / "invoice_2049.pdf",
        vendor="ACME Supplies Inc.", invoice_number="INV-2049", invoice_date="2026-06-15",
        po_ref="PO-4471",
        line_items=[("Steel Brackets", 100, 12.50, 1250.00), ("Shipping", "-", None, 45.00)],
        subtotal=1295.00, tax=103.60, total=1398.60,
    )

    # 2. math error
    draw_invoice(
        INPUT_DIR / "invoice_2050.pdf",
        vendor="Bolt & Fastener Co.", invoice_number="INV-2050", invoice_date="2026-06-16",
        po_ref="PO-4472",
        line_items=[("M8 Bolts (box of 500)", 4, 150.00, 600.00), ("Fasteners assortment", 1, 220.00, 220.00)],
        subtotal=820.00, tax=30.00, total=900.00,
        note="Note: totals as provided by vendor (may contain vendor-side errors).",
    )

    # 3. fuzzy vendor, no PO ref
    draw_invoice(
        INPUT_DIR / "invoice_2051.pdf",
        vendor="ACME Supplies LLC", invoice_number="INV-2051", invoice_date="2026-06-17",
        po_ref="",
        line_items=[("Steel Brackets", 50, 12.50, 625.00)],
        subtotal=625.00, tax=50.00, total=675.00,
    )

    # 4. text-only body, no PDF
    body_text = """From: sales@brightline-traders.com
Subject: Invoice for PO-4474

Invoice #: INV-2052
Date: 2026-06-18
PO Reference: PO-4474
Item: Packaging Film Rolls x 20 - $30.00 each
Total Due: $600.00
"""
    (INPUT_DIR / "invoice_body_only.txt").write_text(body_text)

    # 5. unsupported format
    (INPUT_DIR / "invoice_wrong_format.xlsx").write_bytes(b"placeholder - simulates a non-PDF vendor upload")

    print(f"Generated test invoices in: {INPUT_DIR}")
    for f in sorted(INPUT_DIR.iterdir()):
        print(f" - {f.name}")


if __name__ == "__main__":
    main()