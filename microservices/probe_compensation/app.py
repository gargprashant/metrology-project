"""
Probe Compensation Service (Event-Driven)

Subscribes to: feature-scanned (pulls from probe-compensation-sub)
Reads from:    rawscan/ blob container
Writes to:     compensated/ blob container (triggers feature-compensated event)
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from contextlib import asynccontextmanager
import threading
import logging

from shared.azure_clients import read_blob, write_blob, poll_and_process

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("probe-compensation")

# ---------- Core Logic ----------

def compensate_points(cmm_id: str, probe_diameter: float, points: list) -> list:
    """Apply probe radius compensation to tip points."""
    compensated = []
    radius = probe_diameter / 2.0

    for pt in points:
        nx, ny, nz = pt["normal"]
        norm_len = (nx**2 + ny**2 + nz**2) ** 0.5
        nx, ny, nz = nx / norm_len, ny / norm_len, nz / norm_len

        compensated.append({
            "x": pt["x"] - radius * nx,
            "y": pt["y"] - radius * ny,
            "z": pt["z"] - radius * nz,
        })

    return compensated

# ---------- Event Handler ----------

def handle_event(event_data: dict):
    """Process a feature-scanned event."""
    subject = event_data.get("subject", "")
    # Extract blob name from the subject path
    # Subject format: /blobServices/default/containers/rawscan/blobs/<blob_name>
    blob_name = subject.split("/blobs/", 1)[-1] if "/blobs/" in subject else event_data.get("blob_name", "")

    logger.info(f"Processing raw scan blob: rawscan/{blob_name}")

    # Read raw scan data from blob
    raw_data = read_blob("rawscan", blob_name)

    # Compensate
    compensated_points = compensate_points(
        cmm_id=raw_data["cmmId"],
        probe_diameter=raw_data["probeDiameter"],
        points=raw_data["points"],
    )

    # Build output — pass nominal points through for alignment stage
    output = {
        "cmmId": raw_data["cmmId"],
        "probeDiameter": raw_data["probeDiameter"],
        "compensatedPoints": compensated_points,
        "nominalPoints": raw_data.get("nominalPoints", []),
        "sourceBlob": f"rawscan/{blob_name}",
    }

    # Write to compensated/ container (triggers feature-compensated event)
    write_blob("compensated", blob_name, output)
    logger.info(f"Compensation complete for {blob_name}")

# ---------- FastAPI App with Background Polling ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    thread = threading.Thread(target=poll_and_process, args=(handle_event,), daemon=True)
    thread.start()
    logger.info("Event polling started")
    yield
    logger.info("Shutting down")

app = FastAPI(title="Probe Compensation Service", lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "healthy", "service": "probe-compensation"}
