"""
Live Results Grid — 10 CMMs x 10 features, fully event-driven.

1. Background thread drip-feeds scans (random CMM, random order).
2. Another background thread subscribes to feature-reported events.
3. When a report event arrives → read report → show badge + plot instantly.
4. No polling. Pure event-driven display.

Usage:
    streamlit run simulation/live_grid.py --server.port 8503
"""

import os
import sys
import io
import json
import time
import random
import threading
import queue
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import plotly.graph_objects as go
from azure.identity import ClientSecretCredential
from azure.eventgrid import EventGridConsumerClient
from azure.storage.blob import BlobServiceClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import feature_generator

# ---------- Config ----------

NUM_CMMS = 10
FEATURES_PER_CMM = 10
CONTAINERS = ["rawscan", "compensated", "aligned", "results", "reports"]
ALL_SHAPES = ["cylinder", "sphere", "cone", "circle", "taper"]

STORAGE_ACCOUNT_URL = os.environ.get(
    "STORAGE_ACCOUNT_URL",
    "https://metrologyprojectstorage.blob.core.windows.net",
)
EVENT_GRID_ENDPOINT = os.environ.get(
    "EVENT_GRID_ENDPOINT",
    "",
)

credential = ClientSecretCredential(
    tenant_id=os.environ["AZURE_TENANT_ID"],
    client_id=os.environ["AZURE_CLIENT_ID"],
    client_secret=os.environ["AZURE_CLIENT_SECRET"],
)
blob_service = BlobServiceClient(account_url=STORAGE_ACCOUNT_URL, credential=credential)

# Event Grid consumer for reports
event_consumer = EventGridConsumerClient(
    endpoint=EVENT_GRID_ENDPOINT,
    credential=credential,
    namespace_topic="feature-reported",
    subscription="dashboard-sub",
)

# Queue: event thread pushes report blob names, main thread consumes
report_queue = queue.Queue()


# ---------- Azure Helpers ----------

def read_blob(container, blob_name):
    try:
        return json.loads(
            blob_service.get_container_client(container).download_blob(blob_name).readall()
        )
    except Exception:
        return None


def clear_all_blobs():
    all_jobs = []
    for c in CONTAINERS:
        try:
            for b in blob_service.get_container_client(c).list_blobs():
                all_jobs.append((c, b.name))
        except Exception:
            pass

    def delete_one(job):
        blob_service.get_container_client(job[0]).delete_blob(job[1])

    with ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(delete_one, all_jobs))
    return len(all_jobs)


def extract_blob_name(event):
    """Extract blob name from BlobCreated CloudEvent subject."""
    subject = getattr(event, "subject", "") or ""
    if "/blobs/" in subject:
        return subject.split("/blobs/", 1)[-1]
    data = event.data if hasattr(event, "data") else event
    if isinstance(data, dict):
        url = data.get("url", "")
        if "/" in url:
            parts = url.split("/")
            if len(parts) >= 5:
                return "/".join(parts[4:])
    return ""


# ---------- Event Subscriber (background thread) ----------

def event_listener():
    """Pull events from feature-reported subscription. Push blob names to queue."""
    while True:
        try:
            details_list = event_consumer.receive(max_events=5, max_wait_time=10)
            for detail in details_list:
                event = detail.event
                lock_token = detail.broker_properties.lock_token
                blob_name = extract_blob_name(event)
                if blob_name:
                    report_queue.put(blob_name)
                event_consumer.acknowledge(lock_tokens=[lock_token])
        except Exception:
            time.sleep(2)


# ---------- Plot ----------

def make_static_3d(nominal_pts, actual_pts):
    fig = plt.figure(figsize=(2, 2), dpi=90)
    ax = fig.add_subplot(111, projection="3d")
    if nominal_pts:
        pts = nominal_pts[::3]
        ax.scatter([p["x"] for p in pts], [p["y"] for p in pts], [p["z"] for p in pts],
                   c="#2196F3", s=1, alpha=0.5)
    if actual_pts:
        pts = actual_pts[::3]
        ax.scatter([p["x"] for p in pts], [p["y"] for p in pts], [p["z"] for p in pts],
                   c="#FF5722", s=1, alpha=0.5)
    ax.set_xticklabels([])
    ax.set_yticklabels([])
    ax.set_zticklabels([])
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_zlabel("")
    fig.tight_layout(pad=0)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def make_interactive_3d(nominal_pts, actual_pts, title=""):
    fig = go.Figure()
    if nominal_pts:
        fig.add_trace(go.Scatter3d(
            x=[p["x"] for p in nominal_pts], y=[p["y"] for p in nominal_pts],
            z=[p["z"] for p in nominal_pts],
            mode="markers", marker=dict(size=2, color="#2196F3", opacity=0.6),
            name="Nominal",
        ))
    if actual_pts:
        fig.add_trace(go.Scatter3d(
            x=[p["x"] for p in actual_pts], y=[p["y"] for p in actual_pts],
            z=[p["z"] for p in actual_pts],
            mode="markers", marker=dict(size=2, color="#FF5722", opacity=0.6),
            name="Actual",
        ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=13)),
        height=500, margin=dict(l=0, r=0, t=35, b=0),
        legend=dict(x=0, y=1),
        scene=dict(xaxis_title="X", yaxis_title="Y", zaxis_title="Z"),
    )
    return fig


# ---------- Upload (background thread) ----------

upload_state = {"count": 0, "done": False}


def upload_one(cmm_idx, feat_idx, shape, sigma):
    cmm_id = f"CMM_{str(cmm_idx + 1).zfill(2)}"
    blob_name = f"{cmm_id}_F{str(feat_idx + 1).zfill(2)}.json"

    nominal = feature_generator.generate_features(shape=shape, num_points=300)
    noisy = feature_generator.add_noise(nominal, sigma=sigma)
    tip_points, normals = feature_generator.simulate_probe_tip(noisy, probe_radius=1.0)

    payload = json.loads(feature_generator.export_json(cmm_id, 2.0, tip_points, normals))
    payload["nominalPoints"] = [
        {"x": float(x), "y": float(y), "z": float(z)} for x, y, z in nominal
    ]
    payload["scanId"] = blob_name.replace(".json", "")
    payload["shape"] = shape
    payload["featureIndex"] = feat_idx
    payload["cmmIndex"] = cmm_idx

    blob_service.get_container_client("rawscan").upload_blob(
        name=blob_name, data=json.dumps(payload), overwrite=True
    )


def drip_feed():
    upload_state["count"] = 0
    upload_state["done"] = False

    jobs = []
    for ci in range(NUM_CMMS):
        for fi in range(FEATURES_PER_CMM):
            jobs.append((ci, fi, random.choice(ALL_SHAPES),
                         random.choice([0.005, 0.01, 0.015, 0.02, 0.06, 0.08, 0.10, 0.12])))
    random.shuffle(jobs)

    for ci, fi, shape, sigma in jobs:
        try:
            upload_one(ci, fi, shape, sigma)
            upload_state["count"] += 1
        except Exception:
            pass
        time.sleep(random.uniform(0.2, 1.0))

    upload_state["done"] = True


def parse_key(blob_name):
    """Parse report_CMM_XX_FYY.json or CMM_XX_FYY.json -> (cmm_idx, feat_idx)."""
    try:
        name = blob_name.replace("report_", "").replace(".json", "")
        parts = name.split("_")
        return (int(parts[1]) - 1, int(parts[2][1:]) - 1)
    except Exception:
        return None


# ========== Streamlit UI ==========

st.set_page_config(page_title="Live Results Grid", layout="wide")
st.title("Live Results Grid  -  10 CMMs x 10 Features")

col_start, col_stop, _ = st.columns([1, 1, 6])
with col_start:
    start_clicked = st.button("Start", type="primary", use_container_width=True)
with col_stop:
    stop_clicked = st.button("Stop", type="secondary", use_container_width=True)

if stop_clicked:
    with st.spinner("Clearing all blob data..."):
        deleted = clear_all_blobs()
    st.success(f"Cleared {deleted} blobs. Ready for a fresh run.")
    st.stop()

summary_bar = st.empty()
upload_bar = st.empty()

hcols = st.columns([1] + [1] * FEATURES_PER_CMM)
with hcols[0]:
    st.markdown("**CMM**")
for f in range(FEATURES_PER_CMM):
    with hcols[f + 1]:
        st.markdown(f"**F{f+1:02d}**")
st.divider()

grid_status = {}
grid_plot = {}
for ci in range(NUM_CMMS):
    cols = st.columns([1] + [1] * FEATURES_PER_CMM)
    with cols[0]:
        st.markdown(f"**CMM_{str(ci+1).zfill(2)}**")
    for fi in range(FEATURES_PER_CMM):
        with cols[fi + 1]:
            grid_status[(ci, fi)] = st.empty()
            grid_status[(ci, fi)].markdown(":gray[---]")
            grid_plot[(ci, fi)] = st.empty()

# Interactive 3D viewer
st.divider()
st.subheader("Interactive 3D Viewer")
vcols = st.columns([1, 1, 4])
with vcols[0]:
    sel_cmm = st.selectbox("CMM", [f"CMM_{str(i+1).zfill(2)}" for i in range(NUM_CMMS)])
with vcols[1]:
    sel_feat = st.selectbox("Feature", [f"F{str(i+1).zfill(2)}" for i in range(FEATURES_PER_CMM)])
viewer = st.empty()

vblob = f"{sel_cmm}_{sel_feat}.json"
vraw = read_blob("rawscan", vblob)
valigned = read_blob("aligned", vblob)
if vraw or valigned:
    vnom = vraw.get("nominalPoints", []) if vraw else []
    vact = valigned.get("alignedPoints", []) if valigned else []
    viewer.plotly_chart(make_interactive_3d(vnom, vact, f"{sel_cmm} / {sel_feat}"), use_container_width=True)
else:
    viewer.info("No data yet.")

if not start_clicked:
    st.info("Press **Start** to begin.")
    st.stop()

# ---------- GO ----------

upload_bar.markdown("**Cleaning previous data...**")
clear_all_blobs()

# Start event listener (receives report events)
threading.Thread(target=event_listener, daemon=True).start()

# Start scan uploads (drip-feed)
threading.Thread(target=drip_feed, daemon=True).start()

total = NUM_CMMS * FEATURES_PER_CMM
pass_count = 0
fail_count = 0
done_count = 0

while True:
    # Update upload counter
    uc = upload_state["count"]
    if upload_state["done"]:
        upload_bar.markdown(f"**All {uc} scans uploaded.**")
    else:
        upload_bar.markdown(f"**CMMs scanning: {uc}/{total} uploaded...**")

    # Drain the event queue — process all new report events
    while not report_queue.empty():
        try:
            blob_name = report_queue.get_nowait()
        except queue.Empty:
            break

        key = parse_key(blob_name)
        if key is None:
            continue
        ci, fi = key

        # Read the report blob
        report = read_blob("reports", blob_name)
        if not report:
            continue

        status = report.get("summary", {}).get("overall_status", "?")
        passed = report.get("summary", {}).get("passed", 0)
        total_checks = report.get("summary", {}).get("total_checks", 0)

        lines = []
        for d in report.get("details", []):
            s = "P" if d.get("Status") == "PASS" else "F"
            lines.append(f"{d.get('Feature','?')}: {d.get('Value',0):.4f}/{d.get('Tolerance',0)} [{s}]")
        fitness = report.get("fitness")
        rmse = report.get("rmse")
        meta = []
        if fitness is not None:
            meta.append(f"fit={fitness:.3f}")
        if rmse is not None:
            meta.append(f"rmse={rmse:.3f}")
        detail = " | ".join(meta) + "\n" + "\n".join(lines)

        # Show result
        color = "#4CAF50" if status == "PASS" else "#F44336"
        grid_status[(ci, fi)].markdown(
            f'<div style="background:{color};color:white;padding:6px 8px;'
            f'border-radius:5px;text-align:center;font-weight:bold;font-size:13px;'
            f'margin-bottom:4px;">'
            f'{status} {passed}/{total_checks}</div>'
            f'<div style="font-size:10px;line-height:1.4;white-space:pre;'
            f'background:#f8f8f8;padding:4px;border-radius:3px;overflow:hidden;">'
            f'{detail}</div>',
            unsafe_allow_html=True,
        )

        # Show 3D plot
        cmm_id = f"CMM_{str(ci+1).zfill(2)}"
        scan_name = f"{cmm_id}_F{str(fi+1).zfill(2)}.json"
        raw = read_blob("rawscan", scan_name)
        aligned = read_blob("aligned", scan_name)
        nom = raw.get("nominalPoints", []) if raw else []
        act = aligned.get("alignedPoints", []) if aligned else []
        if nom or act:
            grid_plot[(ci, fi)].image(make_static_3d(nom, act), use_container_width=True)

        if status == "PASS":
            pass_count += 1
        else:
            fail_count += 1
        done_count += 1

        # Update summary immediately
        summary_bar.markdown(
            f"**Completed: {done_count}/{total}  |  "
            f":green[PASS: {pass_count}]  |  :red[FAIL: {fail_count}]  |  "
            f"Remaining: {total - done_count}  |  "
            f"Uploaded: {upload_state['count']}/{total}**"
        )

    if done_count >= total:
        summary_bar.success(
            f"All {total} scans processed!  PASS: {pass_count}  |  FAIL: {fail_count}"
        )
        break

    # Small sleep — just to avoid busy-spinning, events arrive via queue
    time.sleep(0.1)
