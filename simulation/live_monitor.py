"""
Live Pipeline Monitor Dashboard — real-time text + plots of pipeline progress.

Usage:
    set STORAGE_ACCOUNT_URL=https://metrologyprojectstorage.blob.core.windows.net
    streamlit run simulation/live_monitor.py
"""

import os
import time
import json
from collections import defaultdict

import pandas
import streamlit as st
import plotly.graph_objects as go
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

# ---------- Azure Setup ----------

STORAGE_ACCOUNT_URL = os.environ.get(
    "STORAGE_ACCOUNT_URL",
    "https://metrologyprojectstorage.blob.core.windows.net",
)
credential = DefaultAzureCredential()
blob_service = BlobServiceClient(account_url=STORAGE_ACCOUNT_URL, credential=credential)

STAGES = ["rawscan", "compensated", "aligned", "results", "reports"]
STAGE_LABELS = {
    "rawscan": "Raw Scan",
    "compensated": "Probe Compensation",
    "aligned": "Alignment",
    "results": "GD&T Evaluation",
    "reports": "Reporting",
}
STAGE_COLORS = {
    "rawscan": "#FF6B6B",
    "compensated": "#4ECDC4",
    "aligned": "#45B7D1",
    "results": "#96CEB4",
    "reports": "#FFEAA7",
}


def count_blobs(container: str) -> int:
    try:
        return sum(1 for _ in blob_service.get_container_client(container).list_blobs())
    except Exception:
        return 0


def list_reports() -> list:
    reports = []
    try:
        container = blob_service.get_container_client("reports")
        for blob in container.list_blobs():
            if blob.name.startswith("report_"):
                try:
                    data = json.loads(container.download_blob(blob.name).readall())
                    reports.append(data)
                except Exception:
                    pass
    except Exception:
        pass
    return reports


# ---------- Streamlit UI ----------

st.set_page_config(page_title="Pipeline Monitor", layout="wide")
st.title("Live Pipeline Monitor")

col_refresh, col_interval = st.columns([1, 3])
with col_refresh:
    auto_refresh = st.checkbox("Auto-refresh", value=True)
with col_interval:
    refresh_interval = st.slider("Refresh interval (seconds)", 3, 30, 5)

if st.button("Refresh Now") or auto_refresh:
    pass

# ====================================================
# SECTION 1: Pipeline Stage Progress (text + progress bars)
# ====================================================

st.header("Pipeline Progress")

counts = {}
for stage in STAGES:
    counts[stage] = count_blobs(stage)

total = counts["rawscan"]
completed = counts["reports"]

# Text summary
if total > 0:
    pct = completed / total * 100
    st.markdown(f"**Total scans: {total}  |  Completed: {completed}  |  Progress: {pct:.1f}%**")
else:
    st.markdown("**No scans uploaded yet.**")

# Streamlit native progress bars per stage
for stage in STAGES:
    count = counts[stage]
    pct = count / total if total > 0 else 0
    label = STAGE_LABELS[stage]
    st.text(f"{label:20s}  {count:>4} / {total}  ({pct*100:.0f}%)")
    st.progress(pct)

# ====================================================
# SECTION 2: Stage Metrics (big numbers)
# ====================================================

st.header("Stage Counts")
metric_cols = st.columns(5)
for i, stage in enumerate(STAGES):
    with metric_cols[i]:
        pct = (counts[stage] / total * 100) if total > 0 else 0
        st.metric(
            label=STAGE_LABELS[stage],
            value=f"{counts[stage]}",
            delta=f"{pct:.0f}%",
        )

# ====================================================
# SECTION 3: Pipeline Funnel (plot)
# ====================================================

st.header("Pipeline Funnel")
fig_funnel = go.Figure(go.Funnel(
    y=[STAGE_LABELS[s] for s in STAGES],
    x=[counts[s] for s in STAGES],
    marker_color=[STAGE_COLORS[s] for s in STAGES],
    textinfo="value+percent initial",
))
fig_funnel.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig_funnel, use_container_width=True)

# ====================================================
# SECTION 4: GD&T Results (text + table + plot)
# ====================================================

st.header("GD&T Results")

reports = list_reports()
if reports:
    pass_count = sum(1 for r in reports if r.get("summary", {}).get("overall_status") == "PASS")
    fail_count = len(reports) - pass_count

    # Text summary
    st.markdown(f"""
    **Reports generated:** {len(reports)}
    **PASS:** {pass_count}  |  **FAIL:** {fail_count}
    **Pass rate:** {pass_count/len(reports)*100:.1f}%
    """)

    # Per-check breakdown
    check_results = defaultdict(lambda: {"PASS": 0, "FAIL": 0, "total": 0})
    for r in reports:
        for ev in r.get("details", []):
            feature = ev.get("Feature", "unknown")
            status = ev.get("Status", "FAIL")
            check_results[feature][status] += 1
            check_results[feature]["total"] += 1

    # Text table
    st.subheader("Per-Check Breakdown")
    table_data = []
    for feature, stats in check_results.items():
        rate = stats["PASS"] / stats["total"] * 100 if stats["total"] > 0 else 0
        table_data.append({
            "Check": feature,
            "PASS": stats["PASS"],
            "FAIL": stats["FAIL"],
            "Total": stats["total"],
            "Pass Rate": f"{rate:.1f}%",
        })
    st.table(table_data)

    # Stacked bar plot for per-check results
    features = list(check_results.keys())
    passes = [check_results[f]["PASS"] for f in features]
    fails = [check_results[f]["FAIL"] for f in features]

    fig_checks = go.Figure()
    fig_checks.add_trace(go.Bar(name="PASS", x=features, y=passes, marker_color="#2ECC71"))
    fig_checks.add_trace(go.Bar(name="FAIL", x=features, y=fails, marker_color="#E74C3C"))
    fig_checks.update_layout(
        barmode="stack",
        title="Results by GD&T Check Type",
        height=300,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig_checks, use_container_width=True)

    # ====================================================
    # SECTION 5: CMM Distribution (text + plot)
    # ====================================================

    st.header("Scans per CMM")

    cmm_counts = defaultdict(int)
    cmm_pass = defaultdict(int)
    cmm_fail = defaultdict(int)
    for r in reports:
        cmm_id = r.get("cmmId", "unknown")
        cmm_counts[cmm_id] += 1
        if r.get("summary", {}).get("overall_status") == "PASS":
            cmm_pass[cmm_id] += 1
        else:
            cmm_fail[cmm_id] += 1

    # Text table
    cmm_table = []
    for cmm_id in sorted(cmm_counts.keys()):
        cmm_table.append({
            "CMM ID": cmm_id,
            "Total Scans": cmm_counts[cmm_id],
            "PASS": cmm_pass[cmm_id],
            "FAIL": cmm_fail[cmm_id],
        })
    st.table(cmm_table)

    # Bar plot
    fig_cmm = go.Figure()
    sorted_cmms = sorted(cmm_counts.keys())
    fig_cmm.add_trace(go.Bar(
        name="PASS",
        x=sorted_cmms,
        y=[cmm_pass[c] for c in sorted_cmms],
        marker_color="#2ECC71",
    ))
    fig_cmm.add_trace(go.Bar(
        name="FAIL",
        x=sorted_cmms,
        y=[cmm_fail[c] for c in sorted_cmms],
        marker_color="#E74C3C",
    ))
    fig_cmm.update_layout(barmode="stack", height=300, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig_cmm, use_container_width=True)

    # ====================================================
    # SECTION 6: Alignment Quality (text + plot)
    # ====================================================

    fitness_values = [r.get("fitness") for r in reports if r.get("fitness") is not None]
    rmse_values = [r.get("rmse") for r in reports if r.get("rmse") is not None]

    if fitness_values:
        st.header("Alignment Quality")

        avg_fitness = sum(fitness_values) / len(fitness_values)
        min_fitness = min(fitness_values)
        max_fitness = max(fitness_values)
        avg_rmse = sum(rmse_values) / len(rmse_values)
        min_rmse = min(rmse_values)
        max_rmse = max(rmse_values)

        st.markdown(f"""
        **ICP Fitness:**  avg={avg_fitness:.4f}  |  min={min_fitness:.4f}  |  max={max_fitness:.4f}
        **ICP RMSE:**  avg={avg_rmse:.4f}  |  min={min_rmse:.4f}  |  max={max_rmse:.4f}
        """)

        col_fit, col_rmse = st.columns(2)
        with col_fit:
            fig_fit = go.Figure(data=[go.Histogram(x=fitness_values, nbinsx=20, marker_color="#45B7D1")])
            fig_fit.update_layout(title="ICP Fitness Distribution", height=300, margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig_fit, use_container_width=True)
        with col_rmse:
            fig_rmse = go.Figure(data=[go.Histogram(x=rmse_values, nbinsx=20, marker_color="#FF6B6B")])
            fig_rmse.update_layout(title="ICP RMSE Distribution", height=300, margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig_rmse, use_container_width=True)

    # ====================================================
    # SECTION 7: Recent Reports (live feed)
    # ====================================================

    st.header("Recent Reports (last 10)")

    sorted_reports = sorted(reports, key=lambda r: r.get("timestamp", ""), reverse=True)[:10]
    for r in sorted_reports:
        status = r.get("summary", {}).get("overall_status", "?")
        color = "green" if status == "PASS" else "red"
        cmm = r.get("cmmId", "?")
        checks = r.get("summary", {})
        fitness = r.get("fitness")
        rmse = r.get("rmse")

        fitness_str = f"fitness={fitness:.4f}" if fitness else ""
        rmse_str = f"rmse={rmse:.4f}" if rmse else ""

        details = ", ".join(
            f"{d['Feature']}={'PASS' if d['Status']=='PASS' else 'FAIL'}"
            for d in r.get("details", [])
        )

        st.markdown(
            f":{color}[**{status}**] **{cmm}** | "
            f"{checks.get('passed',0)}/{checks.get('total_checks',0)} passed | "
            f"{fitness_str} {rmse_str} | {details}"
        )

else:
    st.info("No reports generated yet. Submit scans and wait for the pipeline to complete.")

# ---------- Auto-refresh ----------

if auto_refresh:
    time.sleep(refresh_interval)
    st.rerun()
