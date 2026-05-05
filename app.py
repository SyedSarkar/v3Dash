"""
Streamlit entry — two tabs:
  1. 3rd-Day Report          (single-week initial categorisation)
  2. Weekly Follow-up Report (compare two weeks, drill into forwarded depts)

Data source: either drop weekly raw_data*.xlsx files into v2_dashboard/raw_data/
(then click "Re-run ingest"), or upload them via the sidebar.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from data_utils import load_long_csv, load_long_dataframe
from ingest import clean_one_week, ingest_folder
from viz_utils import render_three_day_report, render_journey_report


HERE = Path(__file__).parent
RAW_DIR = HERE / "raw_data"
CLEANED_CSV = HERE / "cleaned" / "long.csv"


st.set_page_config(page_title="CCD Dropout Follow-up Dashboard",
                   layout="wide", page_icon=":bar_chart:")
st.title("CCD Dropout Follow-up Dashboard")
st.caption("Spring 2026 onwards — new workflow with department forwarding")


# ---------------------------------------------------------------------------
# Sidebar: data source
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Data")

    mode = st.radio("Source",
                    ["Use cleaned CSV", "Re-run ingest from raw_data/", "Upload weekly file(s)"],
                    index=0)

    if st.button("Clear cache & reload", help="Use this if numbers look stale "
                                              "after a code change."):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()

    df = None

    if mode == "Use cleaned CSV":
        if CLEANED_CSV.exists():
            df = load_long_csv(str(CLEANED_CSV))
            st.success(f"Loaded {len(df)} rows from cleaned/long.csv")
        else:
            st.warning(
                "No cleaned/long.csv found. Switch to 'Re-run ingest' or upload files.")

    elif mode == "Re-run ingest from raw_data/":
        st.write(f"Folder: `{RAW_DIR}`")
        files = sorted(RAW_DIR.glob("*.xlsx"))
        files = [f for f in files if not f.name.startswith("~$")]
        st.write(f"Found {len(files)} file(s):")
        for f in files:
            st.write(f"  • {f.name}")
        if st.button("Run ingest now", type="primary"):
            with st.spinner("Cleaning weekly files..."):
                long_df = ingest_folder(RAW_DIR, CLEANED_CSV)
            st.success(f"Ingested {len(long_df)} rows.")
            df = load_long_csv(str(CLEANED_CSV))

    else:  # upload mode
        uploads = st.file_uploader(
            "Drop one or more weekly .xlsx files",
            type=["xlsx"], accept_multiple_files=True)
        if uploads:
            frames = []
            for up in uploads:
                # write to a temp path so clean_one_week can use openpyxl
                tmp = HERE / "_tmp_upload" / up.name
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(up.getvalue())
                try:
                    frames.append(clean_one_week(tmp))
                except Exception as e:
                    st.error(f"{up.name}: {e}")
            if frames:
                long_df = pd.concat(frames, ignore_index=True).sort_values(
                    ["week", "student_id"]).reset_index(drop=True)
                df = load_long_dataframe(long_df)
                st.success(f"Loaded {len(df)} rows from {len(uploads)} upload(s).")

    if df is not None:
        weeks = sorted(df["week"].dropna().unique().astype(int).tolist())
        st.write("Weeks available:", weeks)


# ---------------------------------------------------------------------------
# Main content
# ---------------------------------------------------------------------------

if df is None:
    st.info("← Pick a data source in the sidebar to begin.")
    st.stop()

weeks = sorted(df["week"].dropna().unique().astype(int).tolist())

tab1, tab2 = st.tabs(["Current Snapshot", "Forwarded-Case Journey"])

with tab1:
    if not weeks:
        st.warning("No weekly data loaded.")
    else:
        wk = st.selectbox("Week to view", weeks, index=len(weeks) - 1, key="t1_week")
        render_three_day_report(df, wk)

with tab2:
    render_journey_report(df)
