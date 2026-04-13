"""
Reporting Service (Event-Driven)

Subscribes to: feature-evaluated (pulls from reporting-sub)
Reads from:    results/ blob container
Writes to:     Slogs/ blob container (final reports and plots)

This is the last service in the pipeline — it generates a summary report.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from contextlib import asynccontextmanager
import threading
import logging
import json
from datetime import datetime, timezone

import pandas as pd

from shared.azure_clients import read_blob, write_blob, poll_and_process, extract_blob_name

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reporting")

# ---------- Core Logic ----------

def generate_report(evaluation_data: dict) -> dict:
    """Generate a summary report from GD&T evaluation results."""
    cmm_id = evaluation_data["cmmId"]
    evaluations = evaluation_data["evaluations"]

    # Build summary DataFrame
    rows = []
    for ev in evaluations:
        rows.append({
            "Feature": ev["type"],
            "Value": round(ev["value"], 6),
            "Tolerance": ev["tolerance"],
            "Status": ev["status"],
        })

    df = pd.DataFrame(rows)

    pass_count = len(df[df["Status"] == "PASS"])
    fail_count = len(df[df["Status"] == "FAIL"])
    total = len(df)

    report = {
        "cmmId": cmm_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_checks": total,
            "passed": pass_count,
            "failed": fail_count,
            "overall_status": "PASS" if fail_count == 0 else "FAIL",
        },
        "details": rows,
        "fitness": evaluation_data.get("fitness"),
        "rmse": evaluation_data.get("rmse"),
        "sourceBlob": evaluation_data.get("sourceBlob"),
    }

    return report

# ---------- Event Handler ----------

def handle_event(event):
    """Process a feature-evaluated event."""
    blob_name = extract_blob_name(event)

    logger.info(f"Processing evaluation results: results/{blob_name}")

    # Read evaluation data
    eval_data = read_blob("results", blob_name)

    # Generate report
    report = generate_report(eval_data)

    # Write report to reports/ container
    report_blob_name = f"report_{blob_name}"
    write_blob("reports", report_blob_name, report)

    logger.info(
        f"Report generated for {blob_name}: "
        f"{report['summary']['overall_status']} "
        f"({report['summary']['passed']}/{report['summary']['total_checks']} passed)"
    )

# ---------- FastAPI App with Background Polling ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    thread = threading.Thread(target=poll_and_process, args=(handle_event,), daemon=True)
    thread.start()
    logger.info("Event polling started")
    yield
    logger.info("Shutting down")

app = FastAPI(title="Reporting Service", lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "healthy", "service": "reporting"}
