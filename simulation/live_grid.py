"""
Live Results Grid — 10 CMMs x 10 features, fully event-driven.

1. Background thread drip-feeds scans (random CMM, random order).
2. Event listener subscribes to feature-reported events via Event Grid.
3. Results stored in session_state — survive button clicks/reruns.
4. Click "3D" button on any cell to open interactive fullscreen dialog.

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
import logging
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import streamlit as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import plotly.graph_objects as go
from azure.identity import DefaultAzureCredential, ClientSecretCredential
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
EVENT_GRID_ENDPOINT = os.environ.get("EVENT_GRID_ENDPOINT", "")

# Use ClientSecretCredential locally (when env vars present), DefaultAzureCredential in cloud
if os.environ.get("AZURE_CLIENT_SECRET"):
    credential = ClientSecretCredential(
        tenant_id=os.environ["AZURE_TENANT_ID"],
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
    )
else:
    credential = DefaultAzureCredential()
blob_service = BlobServiceClient(account_url=STORAGE_ACCOUNT_URL, credential=credential)

event_consumer = EventGridConsumerClient(
    endpoint=EVENT_GRID_ENDPOINT,
    credential=credential,
    namespace_topic="feature-reported",
    subscription="dashboard-sub",
)

# These must NOT be module-level — Streamlit re-executes the script on each
# rerun, which would create fresh objects while background threads still
# reference the old ones.  We lazily initialise them once and stash them in
# st.session_state so every rerun and every thread sees the *same* instance.


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
        try:
            blob_service.get_container_client(job[0]).delete_blob(job[1])
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(delete_one, all_jobs))
    return len(all_jobs)


def extract_blob_name(event):
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


# ---------- Pipeline Counter (background thread) ----------

def pipeline_counter(counters, signal, stop):
    """Periodically count blobs in each pipeline stage container."""
    log.info("pipeline_counter: STARTED")
    containers = ["compensated", "aligned", "results", "reports"]
    while not stop.is_set():
        try:
            for c in containers:
                count = sum(1 for _ in blob_service.get_container_client(c).list_blobs())
                counters[c] = count
            log.info(f"pipeline_counter: comp={counters['compensated']} align={counters['aligned']} eval={counters['results']} report={counters['reports']}")
            signal.set()
        except Exception as e:
            log.error(f"pipeline_counter: {e}", exc_info=True)
        stop.wait(3)  # interruptible sleep
    log.info("pipeline_counter: STOPPED")


# ---------- Event Listener (background thread) ----------

def event_listener(results, img_store, pt_store, counters, signal, stop):
    """Receive events → fetch report + plot data → render image → signal UI.
    Each cell appears complete (result + plot) in one shot."""
    log.info("event_listener: STARTED")
    while not stop.is_set():
        try:
            details_list = event_consumer.receive(max_events=1, max_wait_time=10)
            ack_tokens = []
            for detail in details_list:
                blob_name = extract_blob_name(detail.event)
                token = detail.broker_properties.lock_token
                if not blob_name:
                    ack_tokens.append(token)
                    continue

                key = parse_key(blob_name)
                if key is None or key in results:
                    ack_tokens.append(token)
                    continue
                ci, fi = key
                log.info(f"event_listener: processing {blob_name}")

                report = read_blob("reports", blob_name)
                if not report:
                    log.warning(f"event_listener: report not found: {blob_name} — skipping ack, will redeliver")
                    continue

                ack_tokens.append(token)

                # Fetch plot data — all 3 blobs in parallel
                cmm_id = f"CMM_{str(ci+1).zfill(2)}"
                scan_name = f"{cmm_id}_F{str(fi+1).zfill(2)}.json"
                with ThreadPoolExecutor(max_workers=2) as pool:
                    raw_future = pool.submit(read_blob, "rawscan", scan_name)
                    aligned_future = pool.submit(read_blob, "aligned", scan_name)
                    raw = raw_future.result()
                    aligned = aligned_future.result()
                nom = raw.get("nominalPoints", []) if raw else []
                act = aligned.get("alignedPoints", []) if aligned else []
                pt_store[key] = (nom, act)
                if nom or act:
                    img_store[key] = make_static_3d(nom, act)

                # Store result and update counters
                results[key] = report
                counters["events"] += 1
                p = report.get("summary", {}).get("passed", 0)
                t = report.get("summary", {}).get("total_checks", 0)
                if p == t:
                    counters["pass"] += 1
                else:
                    counters["fail"] += 1
                signal.set()  # wake UI — result + plot ready together

            if ack_tokens:
                event_consumer.acknowledge(lock_tokens=ack_tokens)
        except Exception as e:
            log.error(f"event_listener: error: {e}")
            time.sleep(2)




# ---------- Plot ----------

def make_static_3d(nominal_pts, actual_pts):
    fig = plt.figure(figsize=(2, 2), dpi=90)
    ax = fig.add_subplot(111, projection="3d")
    if nominal_pts:
        pts = nominal_pts[::3]
        ax.scatter3D([p["x"] for p in pts], [p["y"] for p in pts], 0,
                   c="#2196F3", s=1, alpha=0.5)
    if actual_pts:
        pts = actual_pts[::3]
        ax.scatter3D([p["x"] for p in pts], [p["y"] for p in pts], 0,
                   c="#FF5722", s=1, alpha=0.5)
    ax.set_xticklabels([])
    ax.set_yticklabels([])
    #ax.set_zticklabels([])
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
        title=dict(text=title, font=dict(size=14)),
        height=600, width=900, margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(x=0, y=1),
        scene=dict(xaxis_title="X", yaxis_title="Y", zaxis_title="Z"),
    )
    return fig


# ---------- Upload (background thread) ----------


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


def drip_feed(us, signal, stop):
    log.info("drip_feed: STARTED")
    us["count"] = 0
    us["done"] = False

    jobs = []
    for ci in range(NUM_CMMS):
        for fi in range(FEATURES_PER_CMM):
            # ~50% pass: near-zero noise passes GDT checks, high noise fails
            sigma = random.choice([0.0001, 0.0001, 0.0002, 0.0005, 0.5, 0.8, 1.0, 1.5])
            jobs.append((ci, fi, random.choice(ALL_SHAPES), sigma))
    random.shuffle(jobs)

    for ci, fi, shape, sigma in jobs:
        if stop.is_set():
            log.info("drip_feed: STOPPED early")
            return
        try:
            upload_one(ci, fi, shape, sigma)
            us["count"] += 1
            signal.set()  # wake UI — upload count changed
            log.info(f"drip_feed: uploaded {us['count']}/100")
        except Exception as e:
            log.error(f"drip_feed: upload error: {e}")
        time.sleep(random.uniform(0.05, 0.3))

    us["done"] = True
    signal.set()
    log.info("drip_feed: ALL DONE")


def parse_key(blob_name):
    try:
        name = blob_name.replace("report_", "").replace(".json", "")
        parts = name.split("_")
        return (int(parts[1]) - 1, int(parts[2][1:]) - 1)
    except Exception:
        return None


# ---------- Session State Init ----------

if "phase" not in st.session_state:
    st.session_state.phase = "idle"          # idle | running | done
    st.session_state.results = {}            # (ci, fi) -> report dict
    st.session_state.images = {}             # (ci, fi) -> PNG bytes
    st.session_state.point_data = {}         # (ci, fi) -> (nom, act)
    st.session_state.counters = {"pass": 0, "fail": 0, "events": 0, "compensated": 0, "aligned": 0, "results": 0, "reports": 0}
    st.session_state.threads_started = False
    st.session_state.upload_state = {"count": 0, "done": False}
    st.session_state.data_ready = threading.Event()
    st.session_state.stop_signal = threading.Event()

# Convenience aliases — same object on every rerun, threads see the same instance
upload_state = st.session_state.upload_state
data_ready = st.session_state.data_ready






# ---------- Fullscreen 3D Dialog ----------

@st.dialog("Interactive 3D View", width="large")
def show_3d_dialog(ci, fi):
    cmm_label = f"CMM_{str(ci+1).zfill(2)}"
    feat_label = f"F{str(fi+1).zfill(2)}"

    nom, act = st.session_state.point_data.get((ci, fi), ([], []))
    # Try fetching on demand if missing
    if not nom and not act:
        scan_name = f"{cmm_label}_{feat_label}.json"
        raw = read_blob("rawscan", scan_name)
        aligned = read_blob("aligned", scan_name)
        nom = raw.get("nominalPoints", []) if raw else []
        act = aligned.get("alignedPoints", []) if aligned else []
        if nom or act:
            st.session_state.point_data[(ci, fi)] = (nom, act)
    if nom or act:
        st.plotly_chart(
            make_interactive_3d(nom, act, f"{cmm_label} / {feat_label}"),
            width="stretch",
        )
    else:
        st.warning("No point data available.")

    report = st.session_state.results.get((ci, fi))
    if report:
        status = report.get("summary", {}).get("overall_status", "?")
        passed = report.get("summary", {}).get("passed", 0)
        total_checks = report.get("summary", {}).get("total_checks", 0)
        color = "#4CAF50" if status == "PASS" else "#F44336"
        st.markdown(
            f'<div style="background:{color};color:white;padding:8px 12px;'
            f'border-radius:5px;text-align:center;font-weight:bold;font-size:16px;'
            f'margin-bottom:8px;">'
            f'{status} {passed}/{total_checks}</div>',
            unsafe_allow_html=True,
        )
        for d in report.get("details", []):
            s = "PASS" if d.get("Status") == "PASS" else "FAIL"
            icon = "+" if s == "PASS" else "-"
            st.markdown(f"  {icon} **{d.get('Feature','?')}**: {d.get('Value',0):.6f} / {d.get('Tolerance',0)} [{s}]")
        fitness = report.get("fitness")
        rmse = report.get("rmse")
        if fitness is not None:
            st.markdown(f"  **Fitness**: {fitness:.4f}  |  **RMSE**: {rmse:.4f}")


# ========== Streamlit UI ==========

st.set_page_config(page_title="Live Results Grid", layout="wide")
st.title("Live Results Grid  -  10 CMMs x 10 Features")

col_start, col_stop, _ = st.columns([1, 1, 6])
with col_start:
    start_clicked = st.button("Start", type="primary", use_container_width=True)
with col_stop:
    stop_clicked = st.button("Stop", type="secondary", use_container_width=True)

# Summary bar (updatable without full rerun)
summary_bar = st.empty()

# Column headers
hcols = st.columns([1] + [1] * FEATURES_PER_CMM)
with hcols[0]:
    st.markdown("**CMM**")
for f in range(FEATURES_PER_CMM):
    with hcols[f + 1]:
        st.markdown(f"**F{f+1:02d}**")
st.divider()

# Create 100 empty placeholders — one per cell. These persist and can be
# individually updated without touching other cells.
cells = {}
for ci in range(NUM_CMMS):
    cols = st.columns([1] + [1] * FEATURES_PER_CMM)
    with cols[0]:
        st.markdown(f"**CMM_{str(ci+1).zfill(2)}**")
    for fi in range(FEATURES_PER_CMM):
        with cols[fi + 1]:
            cells[(ci, fi)] = st.empty()


def render_cell(placeholder, ci, fi):
    """Render a single cell into its placeholder."""
    key = (ci, fi)
    report = st.session_state.results.get(key)
    if not isinstance(report, dict):
        if report == "pending":
            placeholder.markdown(":orange[loading...]")
        else:
            placeholder.markdown(":gray[---]")
        return

    passed = report.get("summary", {}).get("passed", 0)
    total_checks = report.get("summary", {}).get("total_checks", 0)

    # Detail lines with per-check coloring
    detail_html = ""
    fitness = report.get("fitness")
    rmse = report.get("rmse")
    meta = []
    if fitness is not None:
        meta.append(f"fit={fitness:.3f}")
    if rmse is not None:
        meta.append(f"rmse={rmse:.3f}")
    if meta:
        detail_html += " | ".join(meta) + "<br>"
    for d in report.get("details", []):
        is_pass = d.get("Status") == "PASS"
        if is_pass:
            dc = "#1B8a1B"
            bg = "#e8f5e9"
            tag = "PASS"
        else:
            dc = "#c62828"
            bg = "#ffebee"
            tag = "FAIL"
        detail_html += (
            f'<div style="color:{dc};background:{bg};font-weight:bold;'
            f'padding:2px 4px;margin:1px 0;border-radius:2px;font-size:10px;">'
            f'{d.get("Feature","?")}: {d.get("Value",0):.4f}/{d.get("Tolerance",0)} '
            f'[{tag}]</div>'
        )

    all_pass = passed == total_checks
    color = "#4CAF50" if all_pass else "#F44336"
    failed = total_checks - passed
    label = f"PASS {passed}/{total_checks}" if all_pass else f"FAIL {failed}/{total_checks}"

    img = st.session_state.images.get(key)
    with placeholder.container():
        st.markdown(
            f'<div style="background:{color};color:white;padding:6px 8px;'
            f'border-radius:5px;text-align:center;font-weight:bold;font-size:13px;'
            f'margin-bottom:4px;">'
            f'{label} ({passed}/{total_checks})</div>'
            f'<div style="font-size:10px;line-height:1.4;'
            f'background:#f8f8f8;padding:4px;border-radius:3px;overflow:hidden;">'
            f'{detail_html}</div>',
            unsafe_allow_html=True,
        )
        if img:
            st.image(img, width="stretch")
            with st.popover("3D"):
                nom, act = st.session_state.point_data.get(key, ([], []))
                if nom or act:
                    st.plotly_chart(
                        make_interactive_3d(nom, act, f"CMM_{str(ci+1).zfill(2)} / F{str(fi+1).zfill(2)}"),
                        key=f"plotly3d_{ci}_{fi}",
                    )
                else:
                    st.info("Point data not available.")


def update_summary():
    total = NUM_CMMS * FEATURES_PER_CMM
    pc = st.session_state.counters["pass"]
    fc = st.session_state.counters["fail"]
    done = pc + fc
    uc = upload_state["count"]
    if done >= total:
        summary_bar.success(f"All {total} scans processed!  PASS: {pc}  |  FAIL: {fc}")
    elif st.session_state.phase == "running":
        ec = st.session_state.counters["events"]
        cc = st.session_state.counters["compensated"]
        ac = st.session_state.counters["aligned"]
        gc = st.session_state.counters["results"]
        rc = st.session_state.counters["reports"]
        upload_msg = f"All {uc} scans uploaded." if upload_state["done"] else f"CMMs scanning: {uc}/{total} uploaded..."
        summary_bar.markdown(
            f"**Completed: {done}/{total}  |  "
            f":green[PASS: {pc}]  |  :red[FAIL: {fc}]  |  "
            f"Events: {ec}  |  "
            f"Remaining: {total - done}  |  "
            f"Uploaded: {uc}/{total}**\n\n"
            f"**Pipeline: Compensated: {cc}  |  Aligned: {ac}  |  Evaluated: {gc}  |  Reported: {rc}**\n\n"
            f"{upload_msg}"
        )
    elif st.session_state.phase == "idle":
        summary_bar.info("Press **Start** to begin.")


# ---------- Stop ----------
def drain_stale_events():
    """Acknowledge ALL pending events so they don't pollute the next run."""
    total = 0
    while True:
        try:
            details = event_consumer.receive(max_events=100, max_wait_time=10)
        except Exception:
            break
        if not details:
            break
        tokens = [d.broker_properties.lock_token for d in details]
        event_consumer.acknowledge(lock_tokens=tokens)
        total += len(details)
    return total


if stop_clicked:
    st.session_state.stop_signal.set()  # signal all background threads to exit
    with st.spinner("Stopping threads, clearing blobs, draining events..."):
        deleted = clear_all_blobs()
        drained = drain_stale_events()
    st.session_state.phase = "idle"
    st.session_state.results = {}
    st.session_state.images = {}
    st.session_state.point_data = {}
    st.session_state.counters = {"pass": 0, "fail": 0, "events": 0, "compensated": 0, "aligned": 0, "results": 0, "reports": 0}
    st.session_state.threads_started = False
    upload_state["count"] = 0
    upload_state["done"] = False
    for key, ph in cells.items():
        ph.markdown(":gray[---]")
    summary_bar.success(f"Cleared {deleted} blobs, drained {drained} events. Ready for a fresh run.")
    st.stop()

# ---------- Start ----------
if start_clicked and st.session_state.phase == "idle":
    with st.spinner("Preparing fresh run — clearing data, draining events..."):
        clear_all_blobs()
        drain_stale_events()
    st.session_state.results = {}
    st.session_state.images = {}
    st.session_state.point_data = {}
    st.session_state.counters = {"pass": 0, "fail": 0, "events": 0, "compensated": 0, "aligned": 0, "results": 0, "reports": 0}
    upload_state["count"] = 0
    upload_state["done"] = False
    st.session_state.threads_started = False
    st.session_state.stop_signal = threading.Event()  # fresh signal for new threads
    st.session_state.rendered = set()
    for key, ph in cells.items():
        ph.markdown(":gray[---]")
    st.session_state.phase = "running"

# ---------- Running: live update loop ----------
if st.session_state.phase == "running":
    if not st.session_state.threads_started:
        log.info("PHASE running: starting background threads")
        stop = st.session_state.stop_signal
        threading.Thread(target=event_listener, args=(
            st.session_state.results, st.session_state.images,
            st.session_state.point_data,
            st.session_state.counters, data_ready, stop,
        ), daemon=True).start()
        threading.Thread(target=drip_feed, args=(upload_state, data_ready, stop), daemon=True).start()
        threading.Thread(target=pipeline_counter, args=(st.session_state.counters, data_ready, stop), daemon=True).start()
        st.session_state.threads_started = True

    if "rendered" not in st.session_state:
        st.session_state.rendered = set()

    total = NUM_CMMS * FEATURES_PER_CMM

    # Event-driven loop — sleeps until data_ready is signalled
    while True:
        # Block until a background thread signals new data (no CPU burn)
        data_ready.wait(timeout=5)
        data_ready.clear()

        update_summary()

        # Update only cells that have new data
        for key, ph in cells.items():
            ci, fi = key
            result = st.session_state.results.get(key)
            img_ready = key in st.session_state.images
            already = key in st.session_state.rendered

            if isinstance(result, dict) and img_ready and not already:
                render_cell(ph, ci, fi)
                st.session_state.rendered.add(key)
            elif result == "pending" and not already:
                ph.markdown(":orange[loading...]")

        done = st.session_state.counters["pass"] + st.session_state.counters["fail"]
        rendered_count = len(st.session_state.rendered)
        if done >= total and rendered_count >= total:
            update_summary()
            st.session_state.phase = "done"
            break

elif st.session_state.phase == "idle":
    update_summary()
