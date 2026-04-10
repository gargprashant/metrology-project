"""
CMM Dashboard — uploads raw scan data to Azure Blob Storage and polls for results.

Flow:
  1. Generate simulated CMM scan data
  2. Upload to rawscan/ blob (triggers the async pipeline)
  3. Poll results/ and Slogs/ for the final report
  4. Display results and 3D plots
"""

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from simulation import feature_generator

import streamlit as st
import numpy as np
import plotly.graph_objects as go
import json
import time
import uuid
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

# ---------- Azure Blob Client ----------

STORAGE_ACCOUNT_URL = os.environ.get(
    "STORAGE_ACCOUNT_URL",
    "https://metrologyprojectstorage.blob.core.windows.net",
)
credential = DefaultAzureCredential()
blob_service = BlobServiceClient(account_url=STORAGE_ACCOUNT_URL, credential=credential)


def upload_blob(container: str, blob_name: str, data: dict):
    container_client = blob_service.get_container_client(container)
    container_client.upload_blob(
        name=blob_name,
        data=json.dumps(data, indent=2),
        overwrite=True,
    )


def read_blob(container: str, blob_name: str) -> dict | None:
    try:
        container_client = blob_service.get_container_client(container)
        blob_data = container_client.download_blob(blob_name).readall()
        return json.loads(blob_data)
    except Exception:
        return None


def blob_exists(container: str, blob_name: str) -> bool:
    try:
        container_client = blob_service.get_container_client(container)
        container_client.get_blob_properties(blob_name)
        return True
    except Exception:
        return False


# ---------- Dashboard UI ----------

st.title("CMM Metrology Dashboard")
st.markdown("Upload simulated CMM scans and view async pipeline results.")

col1, col2 = st.columns(2)

with col1:
    cmm_id = st.text_input("CMM ID", value="CMM_01")
    probe_diameter = st.number_input("Probe Diameter (mm)", value=2.0, step=0.1)
    shape = st.selectbox("Feature Shape", ["cylinder", "sphere", "cone", "circle", "taper"])

with col2:
    num_points = st.number_input("Number of Points", value=500, step=100, min_value=50)
    noise_sigma = st.number_input("Noise Sigma", value=0.05, step=0.01)

if st.button("Submit Scan to Pipeline"):
    # Generate simulated data
    nominal = feature_generator.generate_features(shape=shape, num_points=num_points)
    noisy = feature_generator.add_noise(nominal, sigma=noise_sigma)
    tip_points, normals = feature_generator.simulate_probe_tip(noisy, probe_radius=probe_diameter / 2.0)

    # Build payload — includes nominal points for alignment stage
    scan_id = f"{cmm_id}_{shape}_{uuid.uuid4().hex[:8]}"
    blob_name = f"{scan_id}.json"

    payload = json.loads(feature_generator.export_json(cmm_id, probe_diameter, tip_points, normals))
    payload["nominalPoints"] = [
        {"x": float(x), "y": float(y), "z": float(z)}
        for x, y, z in nominal
    ]
    payload["scanId"] = scan_id
    payload["shape"] = shape

    # Upload to rawscan/ — triggers the pipeline
    upload_blob("rawscan", blob_name, payload)
    st.success(f"Scan uploaded: rawscan/{blob_name}")
    st.info("Pipeline running asynchronously. Click 'Check Results' when ready.")

    st.session_state["last_blob_name"] = blob_name
    st.session_state["last_nominal"] = nominal
    st.session_state["last_tip_points"] = tip_points

st.divider()

# ---------- Results Polling ----------

if st.button("Check Results"):
    blob_name = st.session_state.get("last_blob_name")
    if not blob_name:
        st.warning("No scan submitted yet.")
    else:
        report_blob = f"report_{blob_name}"

        with st.spinner("Checking pipeline stages..."):
            # Check each stage
            stages = [
                ("rawscan", blob_name, "Raw Scan"),
                ("compensated", blob_name, "Probe Compensation"),
                ("aligned", blob_name, "Alignment"),
                ("results", blob_name, "GD&T Evaluation"),
                ("Slogs", report_blob, "Report"),
            ]

            progress = st.empty()
            for container, name, label in stages:
                exists = blob_exists(container, name)
                status = "done" if exists else "waiting..."
                progress.write(f"**{label}**: {status}")
                if not exists:
                    break

        # If report is ready, display it
        report = read_blob("Slogs", report_blob)
        if report:
            st.subheader("Pipeline Complete")

            # Summary
            summary = report["summary"]
            status_color = "green" if summary["overall_status"] == "PASS" else "red"
            st.markdown(f"### Overall Status: :{status_color}[{summary['overall_status']}]")
            st.write(f"Checks: {summary['passed']}/{summary['total_checks']} passed")

            if report.get("fitness"):
                st.write(f"ICP Fitness: {report['fitness']:.4f}")
            if report.get("rmse"):
                st.write(f"ICP RMSE: {report['rmse']:.4f}")

            # Detail table
            st.subheader("GD&T Evaluation Details")
            st.table(report["details"])

            # Load intermediate data for plots
            compensated_data = read_blob("compensated", blob_name)
            aligned_data = read_blob("aligned", blob_name)

            if compensated_data and aligned_data:
                nominal = np.array(st.session_state.get("last_nominal", []))
                tip_points = np.array(st.session_state.get("last_tip_points", []))
                compensated = np.array([[p["x"], p["y"], p["z"]] for p in compensated_data["compensatedPoints"]])
                aligned = np.array([[p["x"], p["y"], p["z"]] for p in aligned_data["alignedPoints"]])

                fig = go.Figure()

                if len(tip_points) > 0:
                    fig.add_trace(go.Scatter3d(
                        x=tip_points[:, 0], y=tip_points[:, 1], z=tip_points[:, 2],
                        mode="markers", marker=dict(size=3, color="red"),
                        name="Noisy (Tip Points)",
                    ))

                fig.add_trace(go.Scatter3d(
                    x=compensated[:, 0], y=compensated[:, 1], z=compensated[:, 2],
                    mode="markers", marker=dict(size=3, color="blue"),
                    name="Compensated",
                ))

                fig.add_trace(go.Scatter3d(
                    x=aligned[:, 0], y=aligned[:, 1], z=aligned[:, 2],
                    mode="markers", marker=dict(size=3, color="green"),
                    name="Aligned",
                ))

                if len(nominal) > 0:
                    fig.add_trace(go.Scatter3d(
                        x=nominal[:, 0], y=nominal[:, 1], z=nominal[:, 2],
                        mode="markers", marker=dict(size=3, color="orange"),
                        name="Nominal Geometry",
                    ))

                fig.update_layout(
                    title="Measurement Pipeline Results",
                    scene=dict(xaxis_title="X", yaxis_title="Y", zaxis_title="Z"),
                )
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("Report not ready yet. Pipeline is still processing — try again in a few seconds.")
