"""
Step 1-2: Intake + Classification
Scans the inputs/ folder, identifies each file's type, and decides
which extraction path it should be routed to.

Files that have ALREADY been fully extracted in a previous run (tracked
in processed_files.json) are skipped entirely here - they never appear
in the manifest, so no downstream step ever sees or reports on them again.

Run (from Modules folder):
    python Input_classifier.py
"""

import json
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

INPUT_DIR = Path("../inputs")
OUTPUT_DIR = Path("../outputs")
PROCESSED_FILES_LOG = OUTPUT_DIR / "processed_files.json"

SUPPORTED_IMAGE_EXT = {".jpg", ".jpeg", ".png"}
UNSUPPORTED_EXT = {".xlsx", ".xls", ".docx", ".doc"}
TEXT_ONLY_EXT = {".txt", ".eml"}


def load_processed_files() -> list:
    if PROCESSED_FILES_LOG.exists():
        with open(PROCESSED_FILES_LOG) as f:
            return json.load(f)
    return []


def classify_file(filepath: Path) -> dict:
    ext = filepath.suffix.lower()
    result = {"filename": filepath.name, "file_type_detected": None, "route": None, "notes": None}

    if ext in UNSUPPORTED_EXT:
        result["file_type_detected"] = ext.replace(".", "")
        result["route"] = "unsupported"
        result["notes"] = "Unsupported file format – send to manual review."
        return result

    if ext in TEXT_ONLY_EXT:
        result["file_type_detected"] = "text_body"
        result["route"] = "text_only"
        result["notes"] = "No PDF/image attachment found – parsing as raw text body."
        return result

    if ext in SUPPORTED_IMAGE_EXT:
        result["file_type_detected"] = "image"
        result["route"] = "image"
        result["notes"] = "Image file received directly (not a PDF) – route to OCR/vision path."
        return result

    if ext == ".pdf":
        has_text = pdf_has_text_layer(filepath)
        if has_text:
            result["file_type_detected"] = "text_pdf"
            result["route"] = "text_pdf"
            result["notes"] = "Text layer detected – direct extraction (pdfplumber)."
        else:
            result["file_type_detected"] = "scanned_pdf"
            result["route"] = "scanned_pdf"
            result["notes"] = "No text layer detected – likely scanned. Route to OCR/vision."
        return result

    result["file_type_detected"] = ext.replace(".", "") or "unknown"
    result["route"] = "unsupported"
    result["notes"] = "Unrecognized file type – send to manual review."
    return result


def pdf_has_text_layer(filepath: Path) -> bool:
    if pdfplumber is None:
        return False
    try:
        with pdfplumber.open(filepath) as pdf:
            if not pdf.pages:
                return False
            first_page_text = pdf.pages[0].extract_text() or ""
            return len(first_page_text.strip()) > 20
    except Exception:
        return False


def run_intake():
    OUTPUT_DIR.mkdir(exist_ok=True)
    processed_files = load_processed_files()

    all_files = sorted([f for f in INPUT_DIR.iterdir() if f.is_file()])
    new_files = [f for f in all_files if f.name not in processed_files]
    skipped_count = len(all_files) - len(new_files)

    if skipped_count:
        print(f"Skipping {skipped_count} file(s) already fully processed in a previous run.")

    if not new_files:
        print("No new files to classify.")
        with open(OUTPUT_DIR / "intake_manifest.json", "w") as out:
            json.dump([], out, indent=2)
        return []

    manifest = []
    for f in new_files:
        classification = classify_file(f)
        manifest.append(classification)
        print(f"{f.name:30s} -> route: {classification['route']:12s} "
              f"({classification['notes']})")

    manifest_path = OUTPUT_DIR / "intake_manifest.json"
    with open(manifest_path, "w") as out:
        json.dump(manifest, out, indent=2)
    print(f"\nSaved intake manifest -> {manifest_path}")

    return manifest


if __name__ == "__main__":
    run_intake()