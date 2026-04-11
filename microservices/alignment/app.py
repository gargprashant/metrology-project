"""
Alignment Service (Event-Driven)

Subscribes to: feature-compensated (pulls from alignment-sub)
Reads from:    compensated/ blob container
Writes to:     aligned/ blob container (triggers feature-aligned event)
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from contextlib import asynccontextmanager
import threading
import logging
import time

import numpy as np
import open3d as o3d

from shared.azure_clients import read_blob, write_blob, poll_and_process, extract_blob_name

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("alignment")

# ---------- Core Logic ----------

def align_points(compensated_points: list, nominal_points: list) -> dict:
    """Align compensated points to nominal geometry using ICP."""
    compensated = np.array([[p["x"], p["y"], p["z"]] for p in compensated_points])
    nominal = np.array([[p["x"], p["y"], p["z"]] for p in nominal_points])

    src = o3d.geometry.PointCloud()
    src.points = o3d.utility.Vector3dVector(compensated)

    tgt = o3d.geometry.PointCloud()
    tgt.points = o3d.utility.Vector3dVector(nominal)

    src = src.voxel_down_sample(voxel_size=0.5)
    tgt = tgt.voxel_down_sample(voxel_size=0.5)

    start = time.time()
    threshold = 2.0
    trans_init = np.eye(4)
    criteria = o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=50)
    reg_p2p = o3d.pipelines.registration.registration_icp(
        src, tgt, threshold, trans_init,
        o3d.pipelines.registration.TransformationEstimationPointToPoint(),
        criteria,
    )
    logger.info(f"ICP took {time.time() - start:.2f}s, fitness={reg_p2p.fitness:.4f}, rmse={reg_p2p.inlier_rmse:.4f}")

    aligned_src = src.transform(reg_p2p.transformation)
    aligned_points = np.asarray(aligned_src.points)

    return {
        "transformationMatrix": reg_p2p.transformation.tolist(),
        "fitness": reg_p2p.fitness,
        "rmse": reg_p2p.inlier_rmse,
        "alignedPoints": [
            {"x": float(x), "y": float(y), "z": float(z)}
            for x, y, z in aligned_points
        ],
    }

# ---------- Event Handler ----------

def handle_event(event):
    """Process a feature-compensated event."""
    blob_name = extract_blob_name(event)

    logger.info(f"Processing compensated blob: compensated/{blob_name}")

    # Read compensated data
    comp_data = read_blob("compensated", blob_name)

    # The compensated blob includes nominal points passed through from the raw scan
    nominal_points = comp_data.get("nominalPoints", [])
    if not nominal_points:
        logger.error(f"No nominal points found in compensated/{blob_name}, skipping")
        return

    result = align_points(comp_data["compensatedPoints"], nominal_points)

    # Build output
    output = {
        "cmmId": comp_data["cmmId"],
        "alignedPoints": result["alignedPoints"],
        "transformationMatrix": result["transformationMatrix"],
        "fitness": result["fitness"],
        "rmse": result["rmse"],
        "sourceBlob": f"compensated/{blob_name}",
    }

    # Write to aligned/ container (triggers feature-aligned event)
    write_blob("aligned", blob_name, output)
    logger.info(f"Alignment complete for {blob_name}")

# ---------- FastAPI App with Background Polling ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    thread = threading.Thread(target=poll_and_process, args=(handle_event,), daemon=True)
    thread.start()
    logger.info("Event polling started")
    yield
    logger.info("Shutting down")

app = FastAPI(title="Alignment Service", lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "healthy", "service": "alignment"}
