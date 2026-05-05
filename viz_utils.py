"""
Streamlit visual building blocks: KPI tiles, drill-down tables,
and the cross-week transition view.

All click-to-expand state is held in st.session_state so a click on one tile
opens its detail panel without collapsing siblings unintentionally.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data_utils import (
    BUCKET_ORDER,
    DEPARTMENT_BUCKETS,
    FORWARDED_BUCKETS,
    RISK_COLORS,
    RISK_LEVEL_ORDER,
    STATUS_COLORS,
    bucket_counts,
    department_journey_summary,
    ever_forwarded_ids,
    first_forward_row_per_student,
    forwarded_count,
    journey_pivot,
    latest_row_per_student,
    not_responding_split,
    risk_distribution,
    status_distribution_in_dept,
    student_timeline,
    students_in_bucket,
    students_in_dept_with_status,
    students_in_dept_with_status_compared,
)


DETAIL_COLUMNS = [
    "student_id", "student_name", "program", "phone",
    "refer_to", "last_followup", "followup_status",
    "no_of_follow_up", "reason", "remarks",
    "followup_date", "next_followup_date",
    "risk_category", "courses_at_risk", "total_courses",
    "course_attendance_summary",
    "current_accumulative_absent_pct", "current_week_absent_pct",
]


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _ss_key(*parts) -> str:
    return ":".join(str(p) for p in parts)


def _kpi_card(label: str, value: int, color: str, key: str,
              subtitle: str | None = None) -> bool:
    """A clickable KPI tile rendered as a styled st.button. Returns True if clicked."""
    sub_html = f"<div style='font-size:0.85rem;opacity:0.85;margin-top:2px'>{subtitle}</div>" if subtitle else ""
    label_html = (
        f"<div style='text-align:left;'>"
        f"<div style='font-size:0.95rem;font-weight:600;color:white;'>{label}</div>"
        f"<div style='font-size:2rem;font-weight:700;color:white;line-height:1.1;margin-top:4px'>{value}</div>"
        f"{sub_html}"
        f"</div>"
    )
    # use a styled container so we get color; the actual click is a tiny button under it
    st.markdown(
        f"""
        <div style="background:{color};padding:14px 16px;border-radius:10px;
                    box-shadow:0 1px 3px rgba(0,0,0,0.08);min-height:96px;">
            {label_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
    return st.button("View students", key=key, use_container_width=True)


def _detail_table(df: pd.DataFrame, columns: Iterable[str] = DETAIL_COLUMNS):
    cols = [c for c in columns if c in df.columns]
    if df.empty:
        st.info("No students in this group.")
        return

    # mini risk-distribution caption above the table
    if "risk_category" in df.columns:
        rd = risk_distribution(df)
        if not rd.empty:
            chips = []
            for k, v in rd.items():
                bg = RISK_COLORS.get(k, "#475569")
                chips.append(
                    f"<span style='background:{bg};color:white;"
                    f"padding:2px 8px;border-radius:10px;"
                    f"font-size:0.8rem;margin-right:4px'>{k}: {int(v)}</span>"
                )
            chip_html = " ".join(chips)
            st.markdown(
                "<div style='margin:4px 0 8px 0'>"
                "<span style='opacity:.7;font-size:0.85rem'>Course-wise risk:</span> "
                f"{chip_html}</div>",
                unsafe_allow_html=True,
            )

    show = df[cols].reset_index(drop=True).copy()

    def _risk_bg(val):
        if pd.isna(val):
            return ""
        return f"background-color: {RISK_COLORS.get(val, '#475569')}; color: white; font-weight: 600;"

    styler = show.style
    if "risk_category" in show.columns:
        styler = styler.map(_risk_bg, subset=["risk_category"])

    st.dataframe(styler, use_container_width=True, hide_index=True)
    st.download_button(
        "Download CSV",
        show.to_csv(index=False).encode("utf-8"),
        file_name="students.csv",
        mime="text/csv",
        key=_ss_key("download", id(df)),
    )


# ---------------------------------------------------------------------------
# Tab 1: 3rd-Day Report
# ---------------------------------------------------------------------------

def render_three_day_report(df: pd.DataFrame, week: int):
    df_week = df[df["week"] == week].copy()

    counts = bucket_counts(df_week)
    nr_split = not_responding_split(df_week)
    total_flagged = len(df_week)
    fwd = forwarded_count(df_week)
    contacted = total_flagged - counts.get("Pending Contact", 0)

    st.subheader(f"Week {week} — Current Snapshot")
    top1, top2, top3, top4 = st.columns(4)
    with top1:
        st.metric("Total Flagged", total_flagged)
    with top2:
        st.metric("Contacted so far", contacted)
    with top3:
        st.metric("Forwarded to Other Depts.", fwd)
    with top4:
        st.metric("Red Zone (Not Resp. ≥2 follow-ups)", nr_split["Red Zone"])

    st.markdown("---")

    # --- bucket tiles in two rows of four
    row1 = st.columns(4)
    row2 = st.columns(4)

    tiles = [
        ("CCD-Joined",      counts["CCD-Joined"],      STATUS_COLORS["CCD-Joined"]),
        ("CSM",             counts["CSM"],             STATUS_COLORS["CSM"]),
        ("Treasurer",       counts["Treasurer"],       STATUS_COLORS["Treasurer"]),
        ("SFC",             counts["SFC"],             STATUS_COLORS["SFC"]),
        ("SDC",             counts["SDC"],             STATUS_COLORS["SDC"]),
        ("Not Responding",  counts["Not Responding"],  STATUS_COLORS["Not Responding"]),
        ("Closed",          counts["Closed"],          STATUS_COLORS["Closed"]),
        ("Pending Contact", counts["Pending Contact"], STATUS_COLORS["Pending Contact"]),
    ]

    clicked_bucket = st.session_state.get("t1_open_bucket")
    cols = list(row1) + list(row2)

    for i, (label, val, color) in enumerate(tiles):
        with cols[i]:
            sub = None
            if label == "Not Responding":
                sub = f"First Miss: {nr_split['First Miss']}  |  Red Zone: {nr_split['Red Zone']}"
            if _kpi_card(label, val, color, key=_ss_key("t1_btn", label), subtitle=sub):
                st.session_state["t1_open_bucket"] = None if clicked_bucket == label else label
                st.rerun()

    open_b = st.session_state.get("t1_open_bucket")
    if open_b:
        st.markdown("---")
        st.markdown(f"### {open_b} — Student Details")
        if open_b == "Not Responding":
            sub = students_in_bucket(df_week, open_b)
            tab_first, tab_red = st.tabs(
                [f"First Miss ({nr_split['First Miss']})",
                 f"Red Zone ({nr_split['Red Zone']})"])
            with tab_first:
                _detail_table(sub[sub["no_of_follow_up"] <= 1])
            with tab_red:
                _detail_table(sub[sub["no_of_follow_up"] > 1])
        else:
            _detail_table(students_in_bucket(df_week, open_b))


# ---------------------------------------------------------------------------
# Tab 2: Forwarded-Case Journey (whole semester)
# ---------------------------------------------------------------------------

def render_journey_report(df: pd.DataFrame):
    all_weeks = sorted(df["week"].dropna().unique().astype(int).tolist())
    if not all_weeks:
        st.warning("No data loaded.")
        return

    # --- week range filter -------------------------------------------------
    if len(all_weeks) > 1:
        wk_start, wk_end = st.select_slider(
            "Week range",
            options=all_weeks,
            value=(all_weeks[0], all_weeks[-1]),
            key="t2_week_range",
            help="Limit the journey view to a window of weeks.",
        )
    else:
        wk_start = wk_end = all_weeks[0]

    # filter the dataframe to the chosen window — everything downstream uses df
    df = df[df["week"].between(wk_start, wk_end)].copy()
    weeks = sorted(df["week"].dropna().unique().astype(int).tolist())

    fwd_ids = sorted(ever_forwarded_ids(df))
    latest = latest_row_per_student(df)
    first_fwd = first_forward_row_per_student(df)

    st.subheader(
        f"Forwarded-Case Journey — Weeks {weeks[0]} → {weeks[-1]}")

    # --- top metrics -------------------------------------------------------
    t1, t2, t3, t4 = st.columns(4)
    with t1:
        st.metric("Total students tracked",
                  int(df["student_id"].nunique()))
    with t2:
        st.metric("Ever forwarded", len(fwd_ids))
    with t3:
        latest_fwd = latest[latest["student_id"].isin(fwd_ids)]
        currently_open = int(latest_fwd["bucket"].isin(FORWARDED_BUCKETS).sum())
        st.metric("Currently with depts.", currently_open)
    with t4:
        resolved = int(latest_fwd["bucket"].isin(["CCD-Joined", "Closed"]).sum())
        st.metric("Resolved (back at CCD/Closed)", resolved)

    if not fwd_ids:
        st.info("No students have been forwarded to any department yet.")
        return

    st.markdown("---")
    st.markdown("### Department-level journey")

    # --- department cards --------------------------------------------------
    cols = st.columns(len(FORWARDED_BUCKETS))
    open_dept = st.session_state.get("t2_open_dept")
    for col, dept in zip(cols, FORWARDED_BUCKETS):
        with col:
            summ = department_journey_summary(df, dept)
            sub = (f"Now: {summ['currently']} | Resolved: {summ['resolved']} | "
                   f"Returned: {summ['returned']}")
            if _kpi_card(dept, summ["ever"], STATUS_COLORS[dept],
                         key=_ss_key("t2_dept", dept), subtitle=sub):
                st.session_state["t2_open_dept"] = None if open_dept == dept else dept
                st.session_state["t2_open_status"] = None
                st.rerun()

    open_dept = st.session_state.get("t2_open_dept")
    if not open_dept:
        st.caption("Click any department to see who was forwarded, "
                   "their latest status, and a week-by-week journey.")
        return

    # --- selected dept: status sub-tiles based on LATEST status -----------
    st.markdown(f"### {open_dept} — outcome distribution (latest week)")

    dept_ids = (df[df["bucket"] == open_dept]["student_id"]
                .dropna().unique().tolist())
    latest_dept = latest[latest["student_id"].isin(dept_ids)].copy()

    status_counts = (latest_dept["followup_status"].fillna("(no status)")
                                                   .value_counts())
    if status_counts.empty:
        st.info("No follow-up data on these students yet.")
        return

    status_cols = st.columns(min(4, len(status_counts)))
    open_status = st.session_state.get("t2_open_status")
    for i, (status_label, n) in enumerate(status_counts.items()):
        with status_cols[i % len(status_cols)]:
            color = _status_color(status_label)
            if _kpi_card(status_label, int(n), color,
                         key=_ss_key("t2_status", open_dept, status_label)):
                st.session_state["t2_open_status"] = (
                    None if open_status == status_label else status_label)
                st.rerun()

    open_status = st.session_state.get("t2_open_status")
    if not open_status:
        st.caption("Click a status tile to see the underlying students "
                   "with their full week-by-week timeline.")
        return

    # --- chosen status: show timeline pivot for those students ------------
    st.markdown(f"### Students: {open_dept} → currently *{open_status}*")

    if open_status == "(no status)":
        chosen_ids = latest_dept[latest_dept["followup_status"].isna()][
            "student_id"].tolist()
    else:
        chosen_ids = latest_dept[
            latest_dept["followup_status"] == open_status]["student_id"].tolist()

    pivot = journey_pivot(df, chosen_ids)
    if pivot.empty:
        st.info("No students match this slice.")
        return

    _render_journey_pivot(pivot, weeks)

    # optional: pick one student to see full timeline
    st.markdown("#### Inspect one student's full timeline")
    chosen = st.selectbox("Pick a student",
                          [""] + pivot["student_id"].tolist(),
                          format_func=lambda x: x if x else "(none)",
                          key=_ss_key("t2_pick_student", open_dept, open_status))
    if chosen:
        tl = student_timeline(df, chosen)
        cols_show = [c for c in [
            "week", "followup_date", "bucket", "followup_status", "refer_to",
            "last_followup", "no_of_follow_up", "reason", "remarks",
            "next_followup_date", "risk_category", "courses_at_risk", "total_courses",
            "course_attendance_summary"] if c in tl.columns]
        tl_show = tl[cols_show].copy()

        def _risk_bg(val):
            if pd.isna(val): return ""
            return f"background-color: {RISK_COLORS.get(val, '#475569')}; color: white; font-weight: 600;"

        sty = tl_show.style
        if "risk_category" in tl_show.columns:
            sty = sty.map(_risk_bg, subset=["risk_category"])
        st.dataframe(sty, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# helpers used by Tab 2
# ---------------------------------------------------------------------------

def _status_color(label: str) -> str:
    s = label.lower()
    if s.startswith("potential joined"):    return "#16a34a"
    if s.startswith("potential inprocess"): return "#0ea5e9"
    if s.startswith("not responding"):      return "#dc2626"
    if s == "closed":                       return "#64748b"
    if s == "freeze":                       return "#a855f7"
    if "return" in s:                       return "#f59e0b"
    return "#475569"


def _render_journey_pivot(pivot: pd.DataFrame, weeks: list[int]):
    """
    Render a wide table where each wk_X column shows the bucket name as a
    coloured pill. Uses pandas Styler so we get cell-level coloring.
    """
    wk_cols = [f"wk_{w}" for w in weeks]
    show = pivot[["student_id", "student_name", "program"] + wk_cols +
                 ["latest_refer_to", "latest_status", "latest_followups"]].copy()

    def _bg(val):
        if pd.isna(val) or val == "":
            return "background-color: #1e293b; color: #94a3b8;"
        c = STATUS_COLORS.get(val, "#475569")
        return f"background-color: {c}; color: white; font-weight: 600;"

    styled = show.style.map(_bg, subset=wk_cols)
    st.dataframe(styled, use_container_width=True, hide_index=True)
    st.download_button(
        "Download timeline CSV",
        show.to_csv(index=False).encode("utf-8"),
        file_name="journey.csv", mime="text/csv",
        key=_ss_key("dl_journey", id(pivot)))
