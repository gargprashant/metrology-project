"""
GD&T Evaluation Service (Event-Driven)

Subscribes to: feature-aligned (pulls from gdt-evaluation-sub)
Reads from:    aligned/ blob container
Writes to:     results/ blob container (triggers feature-evaluated event)

Evaluates: flatness, cylindricity, and positional tolerance.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from contextlib import asynccontextmanager
import threading
import logging

import numpy as np
from scipy.optimize import least_squares

from shared.azure_clients import read_blob, write_blob, poll_and_process, extract_blob_name

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gdt-evaluation")

# ---------- GD&T Evaluation Functions ----------

def evaluate_flatness(points: np.ndarray) -> dict:
    """Evaluate flatness by fitting a plane and measuring RMS residual.
    Uses std of signed distances — scales with noise, not shape size."""
    centroid = points.mean(axis=0)
    centered = points - centroid
    _, _, vh = np.linalg.svd(centered)
    normal = vh[-1]
    distances = centered @ normal  # signed distances to best-fit plane
    return {
        "type": "flatness",
        "value": float(np.std(distances)),
        "max_deviation": float(np.abs(distances).max()),
        "mean_deviation": float(np.abs(distances).mean()),
    }


def evaluate_cylindricity(points: np.ndarray) -> dict:
    """Evaluate cylindricity by fitting a cylinder axis and measuring radial deviation.
    Uses std of radial residuals — scales with noise, not shape size."""
    centroid = points.mean(axis=0)
    centered = points - centroid
    _, _, vh = np.linalg.svd(centered)
    axis = vh[0]

    # Project points onto plane perpendicular to axis
    projections = centered - np.outer(centered @ axis, axis)
    radii = np.linalg.norm(projections, axis=1)
    mean_radius = radii.mean()
    radial_residuals = radii - mean_radius

    return {
        "type": "cylindricity",
        "value": float(np.std(radial_residuals)),
        "mean_radius": float(mean_radius),
        "max_radius": float(radii.max()),
        "min_radius": float(radii.min()),
    }


def evaluate_position(points: np.ndarray, nominal_center: list) -> dict:
    """Evaluate positional tolerance as distance from actual centroid to nominal center."""
    actual_center = points.mean(axis=0)
    nominal = np.array(nominal_center)
    deviation = float(np.linalg.norm(actual_center - nominal))

    return {
        "type": "position",
        "value": deviation,
        "actual_center": actual_center.tolist(),
        "nominal_center": nominal_center,
    }


def run_evaluation(aligned_points: list, tolerances: dict = None) -> dict:
    """Run all GD&T evaluations on aligned points."""
    points = np.array([[p["x"], p["y"], p["z"]] for p in aligned_points])

    if tolerances is None:
        tolerances = {"flatness": 0.1, "cylindricity": 0.1, "position": 0.2}

    flatness = evaluate_flatness(points)
    cylindricity = evaluate_cylindricity(points)
    position = evaluate_position(points, nominal_center=[0.0, 0.0, points[:, 2].mean()])

    results = []
    for eval_result, tol_key in [(flatness, "flatness"), (cylindricity, "cylindricity"), (position, "position")]:
        tolerance = tolerances.get(tol_key, 0.1)
        eval_result["tolerance"] = tolerance
        eval_result["status"] = "PASS" if eval_result["value"] <= tolerance else "FAIL"
        results.append(eval_result)

    return {"evaluations": results}

# ---------- Event Handler ----------

def handle_event(event):
    """Process a feature-aligned event."""
    blob_name = extract_blob_name(event)

    logger.info(f"Processing aligned blob: aligned/{blob_name}")

    # Read aligned data
    aligned_data = read_blob("aligned", blob_name)

    # Run GD&T evaluation
    tolerances = aligned_data.get("tolerances", None)
    evaluation = run_evaluation(aligned_data["alignedPoints"], tolerances)

    # Build output
    output = {
        "cmmId": aligned_data["cmmId"],
        "evaluations": evaluation["evaluations"],
        "fitness": aligned_data.get("fitness"),
        "rmse": aligned_data.get("rmse"),
        "sourceBlob": f"aligned/{blob_name}",
    }

    # Write to results/ container (triggers feature-evaluated event)
    write_blob("results", blob_name, output)
    logger.info(f"GD&T evaluation complete for {blob_name}")

# ---------- FastAPI App with Background Polling ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    thread = threading.Thread(target=poll_and_process, args=(handle_event,), daemon=True)
    thread.start()
    logger.info("Event polling started")
    yield
    logger.info("Shutting down")

app = FastAPI(title="GD&T Evaluation Service", lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "healthy", "service": "gdt-evaluation"}
