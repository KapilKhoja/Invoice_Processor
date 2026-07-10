"""
Invoice Processing Dashboard (NiceGUI)
Minimalist, two-panel layout:
  - Left:  Run panel - triggers the pipeline. Each step is an expandable
           row with a fixed-height, scrollable summary area (like a
           Jupyter cell output) showing a concise, color-coded outcome
           per file - never raw logs.
  - Right: History panel - every past decision, pulled from SQLite.

No emoji, no colored logos - status is communicated purely through
color-coded text (green / amber / red) and clean typography.

Run (from Modules folder):
    python app.py
Then open the URL it prints (usually http://localhost:8080).
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from nicegui import ui, run

DB_PATH = Path("../invoice_history.db")
OUTPUT_DIR = Path("../outputs")
INPUT_DIR = Path("../inputs")

PIPELINE_STEPS = [
    ("Intake & classification", "Input_classifier.py"),
    ("Extraction", "extraction.py"),
    ("PO matching", "po_matching.py"),
    ("Validation & decision", "validation_decision.py"),
    ("Save to history", "save_history.py"),
]

STATUS_COLOR = {
    "pending": "#9a9a9a",
    "running": "#2f6fed",
    "done": "#1f9254",
    "error": "#c0392b",
}

DECISION_COLOR = {
    "APPROVED": "#1f9254",
    "FLAGGED": "#b8860b",
    "REJECTED": "#c0392b",
}
NEUTRAL_COLOR = "#5a6b8c"

ui.add_head_html("""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  html, body, .nicegui-content {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
    background-color: #fafafa;
  }
  .panel {
    background: #ffffff;
    border: 1px solid #e6e6e6;
    border-radius: 14px;
    padding: 24px;
  }
  .run-button {
    background: linear-gradient(135deg, #f2f6ff 0%, #e4ecff 100%);
    color: #2f4b8f;
    border: 1px solid #d3ddf7;
    border-radius: 10px;
    font-weight: 600;
    padding: 10px 22px;
  }
  .run-button:hover {
    background: linear-gradient(135deg, #e9f0ff 0%, #d9e4ff 100%);
  }
  .secondary-button {
    background: #f5f5f5;
    color: #666;
    border: 1px solid #e0e0e0;
    border-radius: 10px;
    font-weight: 500;
    padding: 10px 18px;
  }
  .section-title {
    font-size: 15px;
    font-weight: 600;
    color: #1a1a1a;
    letter-spacing: 0.2px;
    margin-bottom: 4px;
  }
  .muted {
    color: #8a8a8a;
    font-size: 13px;
  }
  .metric-box {
    background: #f7f8fa;
    border-radius: 10px;
    padding: 14px 18px;
    text-align: center;
    flex: 1;
  }
  .metric-number {
    font-size: 22px;
    font-weight: 700;
  }
  .metric-label {
    font-size: 12px;
    color: #8a8a8a;
    margin-top: 2px;
  }
  .scroll-box {
    max-height: 220px;
    overflow-y: auto;
    padding-right: 4px;
  }
  .scroll-box::-webkit-scrollbar {
    width: 6px;
  }
  .scroll-box::-webkit-scrollbar-thumb {
    background: #d8d8d8;
    border-radius: 3px;
  }
</style>
""", shared=True)


def load_history_rows():
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        "SELECT run_timestamp, source_filename, vendor_name, invoice_number, "
        "total, matched_po_number, decision "
        "FROM invoice_runs ORDER BY id DESC"
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def run_pipeline_step(script_name: str):
    result = subprocess.run([sys.executable, script_name], capture_output=True, text=True)
    return result.returncode == 0, (result.stdout or result.stderr)


def get_current_filenames():
    manifest_path = OUTPUT_DIR / "intake_manifest.json"
    if not manifest_path.exists():
        return set()
    with open(manifest_path) as f:
        return {entry["filename"] for entry in json.load(f)}


def open_source_file(filename: str):
    filepath = INPUT_DIR / filename
    if not filepath.exists():
        ui.notify(f"File not found: {filepath}", type="negative")
        return
    try:
        os.startfile(str(filepath))
    except AttributeError:
        subprocess.run(["open", str(filepath)])
    except Exception as e:
        ui.notify(f"Could not open file: {e}", type="negative")

ROUTE_COLOR = {
    "text_pdf": NEUTRAL_COLOR,
    "text_only": NEUTRAL_COLOR,
    "scanned_pdf": NEUTRAL_COLOR,
    "image": NEUTRAL_COLOR,
    "unsupported": DECISION_COLOR["REJECTED"],
}

MATCH_STATUS_LABELS = {
    "confirmed": "Confirmed",
    "fuzzy_matched": "Fuzzy match",
    "vendor_mismatch": "Vendor mismatch",
    "not_found": "No PO found",
    "no_match": "No PO or vendor match",
    "multiple_references_review_needed": "Needs review",
    "skipped_duplicate": "Skipped (duplicate)",
}

MATCH_STATUS_COLOR = {
    "confirmed": DECISION_COLOR["APPROVED"],
    "fuzzy_matched": DECISION_COLOR["FLAGGED"],
    "vendor_mismatch": DECISION_COLOR["REJECTED"],
    "not_found": DECISION_COLOR["REJECTED"],
    "no_match": DECISION_COLOR["REJECTED"],
    "multiple_references_review_needed": DECISION_COLOR["FLAGGED"],
    "skipped_duplicate": "#9a9a9a",
}


def summarize_intake(_filenames):
    path = OUTPUT_DIR / "intake_manifest.json"
    if not path.exists():
        return []
    with open(path) as f:
        manifest = json.load(f)

    rows = []
    for entry in manifest:
        route = entry["route"]
        rows.append((entry["filename"], route.replace("_", " "), None, ROUTE_COLOR.get(route, NEUTRAL_COLOR)))
    return rows


def summarize_extraction(filenames):
    path = OUTPUT_DIR / "extracted_invoices.json"
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    current = [d for d in data if d.get("source_filename") in filenames]

    rows = []
    for d in current:
        if d.get("is_duplicate"):
            rows.append((d["source_filename"], "Duplicate", "Vendor/invoice already seen - skipping further checks", "#9a9a9a"))
            continue
        mc = d.get("math_check", {})
        if mc.get("performed") and mc.get("passed") is False:
            rows.append((d["source_filename"], "Math mismatch", mc["detail"], DECISION_COLOR["FLAGGED"]))
        else:
            rows.append((d["source_filename"], "Extracted", None, DECISION_COLOR["APPROVED"]))
    return rows


def summarize_matching(filenames):
    path = OUTPUT_DIR / "matched_invoices.json"
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    current = [d for d in data if d.get("source_filename") in filenames and not d.get("is_duplicate")]

    rows = []
    for d in current:
        status = d.get("po_match", {}).get("overall_status", "unknown")
        label = MATCH_STATUS_LABELS.get(status, status)
        color = MATCH_STATUS_COLOR.get(status, NEUTRAL_COLOR)
        rows.append((d["source_filename"], label, None, color))
    return rows


def summarize_decision(filenames):
    path = OUTPUT_DIR / "decisions.json"
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    current = [d for d in data if d.get("source_filename") in filenames and not d.get("is_duplicate")]

    rows = []
    for d in current:
        dec = d.get("decision", "-")
        short = d.get("decision_short_reason", "-")
        rows.append((d["source_filename"], dec, short, DECISION_COLOR.get(dec, "#444")))
    return rows


def summarize_history(filenames):
    return [("All files", f"{len(filenames)} record(s) saved", None, DECISION_COLOR["APPROVED"])]


STEP_SUMMARY_FUNCS = {
    "Input_classifier.py": summarize_intake,
    "extraction.py": summarize_extraction,
    "po_matching.py": summarize_matching,
    "validation_decision.py": summarize_decision,
    "save_history.py": summarize_history,
}


def render_summary_rows(container, rows, enable_view=False):
    container.clear()
    with container:
        with ui.column().classes("w-full scroll-box"):
            if not rows:
                ui.label("No files to report.").classes("muted")
            for filename, tag, detail, color in rows:
                with ui.row().classes("w-full items-center").style(
                    "justify-content: space-between; padding: 6px 4px; "
                    "border-bottom: 1px solid #f2f2f2;"
                ):
                    with ui.column().style("gap: 0;"):
                        ui.label(filename).style("font-size: 13px; color: #333;")
                        if detail:
                            ui.label(detail).style(
                                f"font-size: 12px; color: {color}; font-weight: 500;"
                            )
                    with ui.row().classes("items-center").style("gap: 10px; margin-left: 12px;"):
                        ui.label(tag).style(
                            f"font-size: 12px; font-weight: 700; color: {color}; "
                            f"letter-spacing: 0.3px; white-space: nowrap;"
                        )
                        if enable_view and tag in ("FLAGGED", "REJECTED"):
                            ui.button(
                                "View", on_click=lambda f=filename: open_source_file(f)
                            ).style(
                                "background: transparent; color: #4a5aa8; border: 1px solid #dbe0f5; "
                                "border-radius: 6px; font-size: 11px; font-weight: 600; "
                                "padding: 3px 10px; min-height: 0;"
                            )

@ui.page("/")
def main_page():
    ui.label("Invoice Processing").classes("text-2xl font-semibold").style("margin-bottom: 4px;")
    ui.label("Automated intake, extraction, PO matching, and decisioning.").classes("muted")

    with ui.row().classes("w-full gap-6").style("margin-top: 20px;"):

        with ui.column().classes("panel").style("flex: 1; min-width: 380px;"):
            ui.label("Run").classes("section-title")
            ui.label("Processes every file currently in the inputs folder.").classes("muted")

            with ui.row().classes("items-center").style("gap: 10px; margin: 14px 0;"):
                run_button = ui.button("Run pipeline").classes("run-button")
                reset_button = ui.button("Reset").classes("secondary-button")

            step_rows = {}
            step_summary_containers = {}
            with ui.column().classes("w-full").style("margin-bottom: 18px;"):
                for label, script in PIPELINE_STEPS:
                    expansion = ui.expansion().classes("w-full").style(
                        "border-bottom: 1px solid #f0f0f0;"
                    )
                    with expansion.add_slot("header"):
                        with ui.row().classes("w-full items-center").style("justify-content: space-between;"):
                            ui.label(label).style("font-size: 14px; color: #333;")
                            status_label = ui.label("Pending").style(
                                f"font-size: 13px; font-weight: 500; color: {STATUS_COLOR['pending']}; margin-right: 8px;"
                            )
                    with expansion:
                        summary_container = ui.column().classes("w-full").style("padding: 4px 0 10px 4px;")
                        with summary_container:
                            ui.label("No data yet - run the pipeline.").classes("muted")
                    step_rows[script] = status_label
                    step_summary_containers[script] = summary_container

            ui.separator()

            ui.label("Summary").classes("section-title").style("margin-top: 16px;")
            with ui.row().classes("w-full gap-3").style("margin-top: 8px;"):
                metric_total = ui.column().classes("metric-box")
                metric_approved = ui.column().classes("metric-box")
                metric_flagged = ui.column().classes("metric-box")
                metric_rejected = ui.column().classes("metric-box")

            def render_metric(box, number, label, color="#1a1a1a"):
                box.clear()
                with box:
                    ui.label(str(number)).classes("metric-number").style(f"color: {color};")
                    ui.label(label).classes("metric-label")

            render_metric(metric_total, 0, "Total")
            render_metric(metric_approved, 0, "Approved", DECISION_COLOR["APPROVED"])
            render_metric(metric_flagged, 0, "Flagged", DECISION_COLOR["FLAGGED"])
            render_metric(metric_rejected, 0, "Rejected", DECISION_COLOR["REJECTED"])

            def reset_all_ui():
                for _, script in PIPELINE_STEPS:
                    step_rows[script].set_text("Pending")
                    step_rows[script].style(f"color: {STATUS_COLOR['pending']};")
                    step_summary_containers[script].clear()
                    with step_summary_containers[script]:
                        ui.label("No data yet - run the pipeline.").classes("muted")
                render_metric(metric_total, 0, "Total")
                render_metric(metric_approved, 0, "Approved", DECISION_COLOR["APPROVED"])
                render_metric(metric_flagged, 0, "Flagged", DECISION_COLOR["FLAGGED"])
                render_metric(metric_rejected, 0, "Rejected", DECISION_COLOR["REJECTED"])

            async def on_run_click():
                run_button.disable()
                reset_all_ui()

                current_filenames = None

                for label, script in PIPELINE_STEPS:
                    step_rows[script].set_text("Running")
                    step_rows[script].style(f"color: {STATUS_COLOR['running']};")

                    success, _ = await run.io_bound(run_pipeline_step, script)

                    if not success:
                        step_rows[script].set_text("Error")
                        step_rows[script].style(f"color: {STATUS_COLOR['error']};")
                        ui.notify(f"{label} failed - check terminal for details.", type="negative")
                        run_button.enable()
                        return

                    step_rows[script].set_text("Done")
                    step_rows[script].style(f"color: {STATUS_COLOR['done']};")

                    if script == "Input_classifier.py":
                        current_filenames = get_current_filenames()

                    summary_func = STEP_SUMMARY_FUNCS.get(script)
                    rows = []
                    if summary_func:
                        try:
                            rows = summary_func(current_filenames or set())
                        except Exception as e:
                            rows = [("Error", "Failed", str(e), DECISION_COLOR["REJECTED"])]

                    render_summary_rows(
                        step_summary_containers[script], rows,
                        enable_view=(script == "validation_decision.py")
                    )

                decisions_path = OUTPUT_DIR / "decisions.json"
                if decisions_path.exists():
                    with open(decisions_path) as f:
                        decisions = json.load(f)
                    current_decisions = [d for d in decisions if d.get("source_filename") in (current_filenames or set())]

                    counts = {"APPROVED": 0, "FLAGGED": 0, "REJECTED": 0}
                    for inv in current_decisions:
                        counts[inv.get("decision", "FLAGGED")] = counts.get(inv.get("decision"), 0) + 1

                    render_metric(metric_total, len(current_decisions), "Total")
                    render_metric(metric_approved, counts["APPROVED"], "Approved", DECISION_COLOR["APPROVED"])
                    render_metric(metric_flagged, counts["FLAGGED"], "Flagged", DECISION_COLOR["FLAGGED"])
                    render_metric(metric_rejected, counts["REJECTED"], "Rejected", DECISION_COLOR["REJECTED"])

                render_history()
                run_button.enable()

            def on_reset_click():
                files_to_clear = [
                    OUTPUT_DIR / "processed_files.json",
                    OUTPUT_DIR / "extracted_invoices.json",
                    OUTPUT_DIR / "processed_invoices_log.json",
                    OUTPUT_DIR / "intake_manifest.json",
                    OUTPUT_DIR / "matched_invoices.json",
                    OUTPUT_DIR / "decisions.json",
                ]
                for f in files_to_clear:
                    f.unlink(missing_ok=True)
                reset_all_ui()
                ui.notify("Pipeline state cleared. Ready for a fresh run.", type="positive")

            reset_button.on("click", on_reset_click)
            run_button.on("click", on_run_click)

        with ui.column().classes("panel").style("flex: 1; min-width: 420px;"):
            with ui.row().classes("w-full items-center").style("justify-content: space-between;"):
                with ui.column().style("gap: 0;"):
                    ui.label("History").classes("section-title")
                    ui.label("All invoices processed so far.").classes("muted")
                clear_history_button = ui.button("Clear history").style(
                    "background: transparent; color: #b0392b; border: none; "
                    "font-size: 12px; font-weight: 500; padding: 4px 6px;"
                )

            history_container = ui.column().classes("w-full").style("margin-top: 14px;")

            def render_history():
                history_container.clear()
                rows = load_history_rows()
                if not rows:
                    with history_container:
                        ui.label("No history yet - run the pipeline to get started.").classes("muted")
                    return

                with history_container:
                    with ui.row().classes("w-full").style(
                        "font-size: 12px; color: #8a8a8a; font-weight: 600; padding-bottom: 8px; "
                        "border-bottom: 1px solid #e6e6e6;"
                    ):
                        ui.label("Vendor").style("flex: 2;")
                        ui.label("Invoice #").style("flex: 1;")
                        ui.label("Total").style("flex: 1;")
                        ui.label("PO").style("flex: 1;")
                        ui.label("Decision").style("flex: 1; text-align: right;")

                    with ui.column().classes("w-full scroll-box").style("max-height: 420px;"):
                        for row in rows:
                            _, filename, vendor, inv_num, total, po_num, decision = row
                            color = DECISION_COLOR.get(decision, "#333")
                            with ui.row().classes("w-full").style(
                                "padding: 9px 0; border-bottom: 1px solid #f5f5f5; align-items: center;"
                            ):
                                ui.label(vendor or "-").style("flex: 2; font-size: 13px; color: #333;")
                                ui.label(inv_num or "-").style("flex: 1; font-size: 13px; color: #666;")
                                ui.label(f"${total:,.2f}" if total else "-").style("flex: 1; font-size: 13px; color: #666;")
                                ui.label(po_num or "-").style("flex: 1; font-size: 13px; color: #666;")
                                ui.label(decision or "-").style(
                                    f"flex: 1; text-align: right; font-size: 13px; font-weight: 600; color: {color};"
                                )

            def on_clear_history_click():
                DB_PATH.unlink(missing_ok=True)
                render_history()
                ui.notify("History cleared.", type="positive")

            clear_history_button.on("click", on_clear_history_click)

            render_history()
            ui.button("Refresh", on_click=render_history).classes("run-button").style("margin-top: 16px;")


ui.run(title="Invoice Processing Dashboard", port=8080, reload=False, show=False)