from asyncio.log import logger
import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from simulation import feature_generator

import streamlit as st
import requests
import numpy as np
import plotly.graph_objects as go
import json

st.title("CMM Probe Compensation + Alignment Dashboard")

def call_gdt_evaluation(aligned_points, nominal_features, tolerances):
     # Ensure everything is JSON‑safe
    if isinstance(aligned_points, np.ndarray):
        aligned_points = aligned_points.tolist()

    clean_nominal = {}
    for k, v in nominal_features.items():
        if isinstance(v, np.ndarray):
            clean_nominal[k] = v.tolist()
        else:
            clean_nominal[k] = v

    payload = {
        "alignedPoints": aligned_points,
        "nominalFeatures": clean_nominal,
        "tolerances": tolerances
    }

    resp = requests.post("http://gdt_evaluation:8083/evaluate", json=payload, timeout=60)
    return resp.json()["evaluationResults"]

if st.button("Run Full Pipeline"):
    # Step 1: Generate noisy points
    nominal = feature_generator.generate_cylinder()
    noisy = feature_generator.add_noise(nominal)
    tip_points, normals = feature_generator.simulate_probe_tip(noisy, probe_radius=1.0)

    # Export JSON payload safely
    payload = json.loads(feature_generator.export_json("CMM_01", 2.0, tip_points, normals))

    # Step 2: Call Microservice 1 (Compensation)
    resp1 = requests.post("http://probe_compensation:8080/compensateProbe", json=payload)
    compensated = np.array([[p["x"], p["y"], p["z"]] for p in resp1.json()["compensatedPoints"]])
    
    # Step 3: Call Microservice 2 (Alignment)
    align_payload = {
        "compensatedPoints": [{"x": float(x), "y": float(y), "z": float(z)} for x, y, z in compensated],
        "nominalPoints": [{"x": float(x), "y": float(y), "z": float(z)} for x, y, z in nominal]
    }
    resp2 = requests.post("http://alignment:8081/alignPoints", json=align_payload, timeout=60)
    result2 = resp2.json()
    aligned = np.array([[p["x"], p["y"], p["z"]] for p in result2["alignedPoints"]])


    # Display metrics
    st.write(f"ICP Fitness: {result2['fitness']:.4f}")
    st.write(f"ICP RMSE: {result2['rmse']:.4f}")

    # Call Microservice 3 (GD&T Evaluation)
    gdt_results = call_gdt_evaluation(
        aligned,
        {"cylinder": nominal},
        {"flatness": 0.1, "position": 0.1, "cylindricity": 0.1}
    )
    st.write("GD&T Evaluation Results:")
    st.json(gdt_results)

    # Step 4: Plot noisy, compensated, aligned, and nominal points
    fig = go.Figure()

    fig.add_trace(go.Scatter3d(
        x=tip_points[:,0], y=tip_points[:,1], z=tip_points[:,2],
        mode='markers', marker=dict(size=3, color='red'),
        name='Noisy Points'
    ))

    fig.add_trace(go.Scatter3d(
        x=compensated[:,0], y=compensated[:,1], z=compensated[:,2],
        mode='markers', marker=dict(size=3, color='blue'),
        name='Compensated Points'
    ))

    fig.add_trace(go.Scatter3d(
        x=aligned[:,0], y=aligned[:,1], z=aligned[:,2],
        mode='markers', marker=dict(size=3, color='green'),
        name='Aligned Points'
    ))

    fig.add_trace(go.Scatter3d(
        x=nominal[:,0], y=nominal[:,1], z=nominal[:,2],
        mode='markers', marker=dict(size=3, color='orange'),
        name='Nominal Geometry'
    ))

    fig.update_layout(
        title="Noisy vs Compensated vs Aligned vs Nominal Points",
        scene=dict(xaxis_title='X', yaxis_title='Y', zaxis_title='Z')
    )

    st.plotly_chart(fig, use_container_width=True)