"""
Streamlit visual building blocks: KPI tiles, drill-down tables,
and the cross-week transition view.

All click-to-expand state is held in st.session_state so a click on one tile
opens its detail panel without collapsing siblings unintentionally.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data_utils import (
    BUCKET_ORDER,
    DEPARTMENT_BUCKETS,
    FORWARDED_BUCKETS,
    OUTCOME_COLORS,
    OUTCOME_GROUPS,
    RISK_COLORS,
    RISK_LEVEL_ORDER,
    STATUS_COLORS,
    TRIAGE_COLORS,
    TRIAGE_LABELS,
    TRIAGE_REFERRAL,
    bucket_counts,
    department_journey_summary,
    dept_outcome_summary,
    dept_program_breakdown,
    engagement_distribution,
    enrich_with_triage,
    ever_forwarded_ids,
    first_forward_row_per_student,
    forwarded_count,
    journey_pivot,
    latest_row_per_student,
    morning_briefing_df,
    not_responding_by_visit_status,
    not_responding_split,
    risk_distribution,
    status_distribution_in_dept,
    student_timeline,
    students_in_bucket,
    students_in_dept_with_status,
    students_in_dept_with_status_compared,
    students_never_visited,
    students_low_visit_visit,
    triage_counts,
)


DETAIL_COLUMNS = [
    "student_id", "student_name", "program", "phone",
    "last_visit_date", "days_since_visit", "visit_status",
    "no_of_visit_in_semester", "engagement_score",
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

def _detail_table(df: pd.DataFrame, columns: Iterable[str] = DETAIL_COLUMNS,
                  filter_key: str = "risk"):
    """
    Render detail table with interactive multi-select risk filter chips.

    Args:
        df: DataFrame to display
        columns: Columns to show
        filter_key: Unique key for this table's filter session state
    """

    cols = [c for c in columns if c in df.columns]

    if df.empty:
        st.info("No students in this group.")
        return

    # MULTI-SELECT STATE
    selected_risks = st.session_state.get(f"risk_filter_{filter_key}", [])

    # Ensure list
    if not isinstance(selected_risks, list):
        selected_risks = []

    # Mini risk-distribution caption above the table
    if "risk_category" in df.columns:

        rd = risk_distribution(df)

        if not rd.empty:

            st.markdown(
                "<span style='opacity:.7;font-size:0.85rem'>"
                "Course-wise risk (click to filter):"
                "</span>",
                unsafe_allow_html=True
            )

            # Create columns for risk chips
            risk_cols = st.columns(len(rd))

            for i, (k, v) in enumerate(rd.items()):

                with risk_cols[i]:

                    bg = RISK_COLORS.get(k, "#475569")

                    # CHECK IF THIS CATEGORY IS SELECTED
                    is_selected = k in selected_risks

                    border = (
                        "3px solid #1e293b"
                        if is_selected
                        else "none"
                    )

                    st.markdown(
                        f"""
                        <div style='background:{bg};
                                    color:white;
                                    padding:4px 12px;
                                    border-radius:10px;
                                    font-size:0.85rem;
                                    text-align:center;
                                    border:{border};'>
                            {k}: {int(v)}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    btn_label = (
                        "✓ Selected"
                        if is_selected
                        else "Filter"
                    )

                    # TOGGLE FILTER
                    if st.button(
                        btn_label,
                        key=f"risk_btn_{filter_key}_{k}",
                        use_container_width=True
                    ):

                        updated = selected_risks.copy()

                        if k in updated:
                            updated.remove(k)
                        else:
                            updated.append(k)

                        st.session_state[f"risk_filter_{filter_key}"] = updated

                        st.rerun()

    # APPLY MULTI FILTER
    display_df = df.copy()

    if selected_risks and "risk_category" in df.columns:

        display_df = df[
            df["risk_category"].isin(selected_risks)
        ].copy()

    show = display_df[cols].reset_index(drop=True).copy()

    # Styling
    def _risk_bg(val):

        if pd.isna(val):
            return ""

        return (
            f"background-color: "
            f"{RISK_COLORS.get(val, '#475569')}; "
            f"color: white; "
            f"font-weight: 600;"
        )

    styler = show.style

    if "risk_category" in show.columns:
        styler = styler.map(_risk_bg, subset=["risk_category"])

    st.dataframe(
        styler,
        use_container_width=True,
        hide_index=True
    )

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
def _reasons_chart(
    df: pd.DataFrame,
    title: str = "Top Reasons",
    filter_key: str = "risk"
):
    """Create a bar chart of top reasons respecting multi-risk filters."""

    if df.empty or "reason" not in df.columns:
        return

    display_df = df.copy()

    # MULTI-SELECT FILTER
    selected_risks = st.session_state.get(
        f"risk_filter_{filter_key}",
        []
    )

    if selected_risks and "risk_category" in df.columns:

        display_df = df[
            df["risk_category"].isin(selected_risks)
        ].copy()

        title = (
            f"{title} "
            f"(Filtered: {', '.join(selected_risks)})"
        )

    # Remove empty reasons
    reasons = display_df["reason"].dropna()
    reasons = reasons[reasons.str.strip() != ""]

    if reasons.empty:
        st.info("No reason data available for this group.")
        return

    reason_counts = (
        reasons.value_counts()
        .head(10)
        .reset_index()
    )

    reason_counts.columns = ["Reason", "Count"]

    # Chart
    fig = px.bar(
        reason_counts,
        x="Count",
        y="Reason",
        orientation="h",
        title=title,
        color="Count",
        color_continuous_scale="Blues",
        text="Count",
    )

    fig.update_traces(
        textposition="outside",
        textfont_size=12
    )

    fig.update_layout(
        height=max(300, len(reason_counts) * 40),
        yaxis=dict(autorange="reversed"),
        showlegend=False,
        margin=dict(l=10, r=10, t=50, b=10),
    )

    st.plotly_chart(fig, use_container_width=True)
    


def _render_risk_distribution(df_week: pd.DataFrame):
    """Bar chart showing distribution of students across risk categories based on course attendance."""
    if "risk_category" not in df_week.columns:
        st.info("No risk category data available.")
        return
    
    risk_counts = df_week["risk_category"].value_counts()
    order = ["High Risk", "At Risk", "Low Risk", "Safe Zone", "No Data"]
    risk_counts = risk_counts.reindex(order).fillna(0).astype(int)
    
    fig = px.bar(
        x=risk_counts.index,
        y=risk_counts.values,
        title="Academic Risk Distribution (Based on Course Attendance)",
        labels={"x": "Risk Category", "y": "Number of Students"},
        color=risk_counts.index,
        color_discrete_map={
            "High Risk": "#dc2626",
            "At Risk": "#f97316",
            "Low Risk": "#eab308",
            "Safe Zone": "#16a34a",
            "No Data": "#475569"
        }
    )
    fig.update_layout(showlegend=False)
    st.plotly_chart(fig, use_container_width=True)


def render_three_day_report(df: pd.DataFrame, week: int):
    df_week = df[df["week"] == week].copy()

    counts = bucket_counts(df_week)
    nr_split = not_responding_split(df_week)
    nr_visit_split = not_responding_by_visit_status(df_week)
    eng_dist = engagement_distribution(df_week[df_week["bucket"] == "Not Responding"])
    total_flagged = len(df_week)
    fwd = forwarded_count(df_week)
    contacted = total_flagged - counts.get("Pending Contact", 0)

    st.subheader(f"Week {week} — Current Snapshot")
    top1, top2, top3, top4 = st.columns(4)
    with top1:
        st.metric("Total Flagged", total_flagged)
    with top2:
        contacted_pct = f"{contacted / total_flagged * 100:.1f}%" if total_flagged else "0%"
        st.metric("Contacted so far", contacted, contacted_pct)
    with top3:
        fwd_pct = f"{fwd / total_flagged * 100:.1f}%" if total_flagged else "0%"
        st.metric("Forwarded to Other Depts.", fwd, fwd_pct)
    with top4:
        red_zone = nr_split["Red Zone"]
        red_zone_pct = f"{red_zone / total_flagged * 100:.1f}%" if total_flagged else "0%"
        st.metric("Red Zone (Not Resp. ≥2 follow-ups)", red_zone, red_zone_pct)

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
            sub = f"{val / total_flagged * 100:.1f}% of total" if total_flagged else "0%"
            if label == "Not Responding":
                sub = f"{val / total_flagged * 100:.1f}% of total  |  First Miss: {nr_split['First Miss']}  |  Red Zone: {nr_split['Red Zone']}"
            if _kpi_card(label, val, color, key=_ss_key("t1_btn", label), subtitle=sub):
                st.session_state["t1_open_bucket"] = None if clicked_bucket == label else label
                st.rerun()

    open_b = st.session_state.get("t1_open_bucket")
    if open_b:
        st.markdown("---")
        st.markdown(f"### {open_b} — Student Details")
        
        # Show reasons chart for CCD-Joined bucket
        if open_b == "CCD-Joined":
            bucket_df = students_in_bucket(df_week, open_b)
            filter_key = open_b.lower().replace("-", "_")
            _reasons_chart(bucket_df, title="Top Reasons for CCD-Joined Students (Missing → Joined)", filter_key=filter_key)
            st.markdown("---")
        
        if open_b == "Not Responding":
            sub = students_in_bucket(df_week, open_b)
            sub = enrich_with_triage(sub)
            t_counts = triage_counts(df_week)
            total_nr = len(sub)

            # ----------------------------------------------------------------
            # Section 1: Triage Pyramid — layered by gate × absence × status
            # ----------------------------------------------------------------
            st.markdown("### Triage Pyramid")
            st.caption(
                "Students are split by **campus presence × absence rate × follow-up response**. "
                "Gate entry data distinguishes a *discipline* issue (on campus, skipping) "
                "from a *Suspecious* issue (never came). Different tiers → different referral paths."
            )

            def _pct(n):
                return f"{n / total_nr * 100:.0f}%" if total_nr else "—"

            tier_col1, tier_col2, tier_col3, tier_col4 = st.columns(4)

            with tier_col1:
                n = t_counts["critical"]
                st.markdown(
                    f"""
                    <div style="background:#dc2626;border-radius:10px;padding:14px 16px;min-height:130px;">
                      <div style="color:#fca5a5;font-size:0.8rem;font-weight:600;letter-spacing:.04em;
                                  text-transform:uppercase;">🚨 Critical</div>
                      <div style="color:white;font-size:2rem;font-weight:700;line-height:1.1;
                                  margin-top:4px;">{n}</div>
                      <div style="color:#fca5a5;font-size:0.8rem;margin-top:2px;">{_pct(n)} of Not Responding</div>
                      <div style="color:#fee2e2;font-size:0.78rem;margin-top:8px;line-height:1.4;">
                        On campus, skipping classes<br>+51% absent + no response
                      </div>
                      <div style="color:#fbbf24;font-size:0.75rem;font-weight:600;margin-top:6px;">
                        → Refer to SDC
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            with tier_col2:
                n = t_counts["high_risk"]
                st.markdown(
                    f"""
                    <div style="background:#ea580c;border-radius:10px;padding:14px 16px;min-height:130px;">
                      <div style="color:#fed7aa;font-size:0.8rem;font-weight:600;letter-spacing:.04em;
                                  text-transform:uppercase;">⚠️ High Risk</div>
                      <div style="color:white;font-size:2rem;font-weight:700;line-height:1.1;
                                  margin-top:4px;">{n}</div>
                      <div style="color:#fed7aa;font-size:0.8rem;margin-top:2px;">{_pct(n)} of Not Responding</div>
                      <div style="color:#ffedd5;font-size:0.78rem;margin-top:8px;line-height:1.4;">
                        low_visit campus presence<br>+51% absent, multiple failed follow-ups
                      </div>
                      <div style="color:#fbbf24;font-size:0.75rem;font-weight:600;margin-top:6px;">
                        → Escalate; consider SDC
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            with tier_col3:
                n = t_counts["suspecious"]
                st.markdown(
                    f"""
                    <div style="background:#7c3aed;border-radius:10px;padding:14px 16px;min-height:130px;">
                      <div style="color:#ddd6fe;font-size:0.8rem;font-weight:600;letter-spacing:.04em;
                                  text-transform:uppercase;">⌛ Suspecious</div>
                      <div style="color:white;font-size:2rem;font-weight:700;line-height:1.1;
                                  margin-top:4px;">{n}</div>
                      <div style="color:#ddd6fe;font-size:0.8rem;margin-top:2px;">{_pct(n)} of Not Responding</div>
                      <div style="color:#ede9fe;font-size:0.78rem;margin-top:8px;line-height:1.4;">
                        Never visited campus at all<br>Family/financial/health barrier
                      </div>
                      <div style="color:#fbbf24;font-size:0.75rem;font-weight:600;margin-top:6px;">
                        → CSM/SFC
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            with tier_col4:
                n = t_counts["monitor"]
                st.markdown(
                    f"""
                    <div style="background:#f9d406;border-radius:10px;padding:14px 16px;min-height:130px;">
                      <div style="color:#012939;font-size:0.8rem;font-weight:600;letter-spacing:.04em;
                                  text-transform:uppercase;">📋 Monitor</div>
                      <div style="color:white;font-size:2rem;font-weight:700;line-height:1.1;
                                  margin-top:4px;">{n}</div>
                      <div style="color:#06b3f9;font-size:0.8rem;margin-top:2px;">{_pct(n)} of Not Responding</div>
                      <div style="color:#024864;font-size:0.78rem;margin-top:8px;line-height:1.4;">
                        Lower severity, first miss<br>or lower absence rate
                      </div>
                      <div style="color:#024864;font-size:0.75rem;font-weight:600;margin-top:6px;">
                        → Standard CCD follow-up
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            st.markdown("")  # breathing room after cards
            
            with st.expander("ℹ️ How triage segmentation works", expanded=False):
                st.markdown(
                    """
**The key insight:** "Not Responding" is not one problem — it's at least four.

| Tier | Gate / Visit | Absence | What it means | Where to refer |
|---|---|---|---|---|
| 🚨 Critical | Active / Recent | ≥51% | Came to campus — chose to skip | **SDC** — Discipline |
| ⚠️ High Risk | low_visit >30d | ≥51% | Was engaged, now gone | **CCD escalate → SDC** |
| ⌛ suspecious | Never visited | Any | Never came — barrier to access | **CSM / SFC** |
| 📋 Monitor | Any | <51% | Lower severity, first miss | **Standard CCD follow-up** |

**Campus Presence** (visit data) is the critical separator. A student
entering the gate proves they can get to campus — high absences then
point to a *behavioural / discipline* issue, not a *logistics / suspecious*
issue. Treating them the same wastes resources and delays the right
intervention.
                    """
                )


            # ----------------------------------------------------------------
            # Section: Reasons Donut Chart for Not Responding
            # ----------------------------------------------------------------
            st.markdown("### Reasons for Not Responding")
            st.caption("Breakdown of reasons provided by students who are not responding")

            reasons = sub["reason"].dropna()
            reasons = reasons[reasons.str.strip() != ""]

            if not reasons.empty:
                reason_counts = reasons.value_counts()
                fig_donut = go.Figure(go.Pie(
                    labels=reason_counts.index,
                    values=reason_counts.values,
                    marker_colors=px.colors.qualitative.Set3,
                    hole=0.5,
                    textinfo="label+percent",
                    textfont_size=11,
                    hovertemplate="%{label}: %{value} students<extra></extra>",
                ))
                fig_donut.update_layout(
                    title="Reason Distribution",
                    showlegend=True,
                    margin=dict(l=10, r=10, t=40, b=10),
                    height=350,
                )
                st.plotly_chart(fig_donut, use_container_width=True)
            else:
                st.info("No reason data available for Not Responding students.")

            # ----------------------------------------------------------------
            # Section 2: Morning Briefing Hotlist
            # ----------------------------------------------------------------
            st.markdown("---")
            st.markdown("### 📋 Priority List")
            st.caption(
                "Sorted by severity. Use the filters below to narrow to your action list for today."
            )

            briefing_df = morning_briefing_df(df_week)

            # --- filter row -------------------------------------------------
            f1, f2, f3, f4 = st.columns([2, 2, 2, 2])
            with f1:
                tier_opts = ["All"] + [
                    TRIAGE_LABELS.get(k, k)
                    for k in ["critical", "high_risk", "suspecious", "monitor"]
                ]
                tier_inv = {v: k for k, v in TRIAGE_LABELS.items()}
                tier_sel = st.selectbox(
                    "Priority tier", tier_opts, key="nr_tier_filter"
                )
            with f2:
                gate_opts = ["All", "On Campus (Active/Recent)", "low_visit / Never Visited"]
                gate_sel = st.selectbox("Campus presence", gate_opts, key="nr_gate_filter")
            with f3:
                abs_opts = ["All", ">50% absent", ">75% absent"]
                abs_sel = st.selectbox("Absence threshold", abs_opts, key="nr_abs_filter")
            with f4:
                contact_opts = ["All", ">7 days no contact", ">14 days no contact"]
                contact_sel = st.selectbox("Days since contact", contact_opts, key="nr_contact_filter")

            filtered = briefing_df.copy()

            # apply tier filter
            if tier_sel != "All" and "triage_segment" in filtered.columns:
                seg_key = tier_inv.get(tier_sel, tier_sel)
                filtered = filtered[filtered["triage_segment"] == seg_key]

            # apply gate / visit filter
            if gate_sel == "On Campus (Active/Recent)" and "visit_status" in filtered.columns:
                filtered = filtered[
                    filtered["visit_status"].isin(["Active (<7d)", "Recent (7-30d)"])
                ]
            elif gate_sel == "low_visit / Never Visited" and "visit_status" in filtered.columns:
                filtered = filtered[
                    filtered["visit_status"].isin(["low_visit (>30d)", "Never Visited"])
                ]

            # apply absence filter
            if abs_sel == ">50% absent" and "current_accumulative_absent_pct" in filtered.columns:
                filtered = filtered[
                    filtered["current_accumulative_absent_pct"].fillna(0) > 50
                ]
            elif abs_sel == ">75% absent" and "current_accumulative_absent_pct" in filtered.columns:
                filtered = filtered[
                    filtered["current_accumulative_absent_pct"].fillna(0) > 75
                ]

            # apply days since contact filter
            if contact_sel == ">7 days no contact" and "days_since_followup" in filtered.columns:
                filtered = filtered[filtered["days_since_followup"].fillna(999) > 7]
            elif contact_sel == ">14 days no contact" and "days_since_followup" in filtered.columns:
                filtered = filtered[filtered["days_since_followup"].fillna(999) > 14]

            st.caption(f"Showing **{len(filtered)}** students (of {total_nr} total Not Responding)")

            # --- render styled table ----------------------------------------
            def _row_style(row):
                seg = row.get("triage_segment", "monitor")
                bg = {
                    "critical":  "#fef2f2",
                    "high_risk": "#fff7ed",
                    "suspecious":   "#e1c41e",
                    "monitor":   "#fefce8",
                }.get(seg, "")
                return [f"background-color: {bg}" for _ in row]

            def _seg_cell(val):
                color = TRIAGE_COLORS.get(val, "#475569")
                label = {
                    "critical":  "🚨 Critical",
                    "high_risk": "⚠️ High Risk",
                    "suspecious":   "⌛ Suspecious",
                    "monitor":   "📋 Monitor",
                }.get(val, val)
                return (
                    f"background-color:{color}; color:white; "
                    f"font-weight:600; border-radius:4px; padding:2px 6px;"
                )

            def _absence_cell(val):
                if pd.isna(val):
                    return ""
                v = float(val)
                if v >= 75:
                    return "background-color:#fef2f2; color:#dc2626; font-weight:600;"
                if v >= 51:
                    return "background-color:#fff7ed; color:#ea580c; font-weight:600;"
                return ""

            rename_map = {
                "student_id":                    "ID",
                "student_name":                  "Name",
                "program":                       "Program",
                "visit_status":                  "Campus Presence",
                "current_accumulative_absent_pct": "Absence %",
                "no_of_follow_up":               "Follow-ups Made",
                "days_since_followup":           "Days Since Contact",
                "followup_status":               "Follow-up Status",
                "triage_segment":                "Priority Tier",
                "phone":                         "Phone",
                "reason":                        "Reason",
                "remarks":                       "Remarks",
            }
            show = filtered.rename(columns=rename_map)
            # round float columns that reach the screen
            for col in ("Absence %", "Days Since Contact", "Follow-ups Made"):
                if col in show.columns:
                    show[col] = pd.to_numeric(show[col], errors="coerce").round(1)

            styler = show.style.apply(_row_style, axis=1)
            if "Priority Tier" in show.columns:
                styler = styler.map(_seg_cell, subset=["Priority Tier"])
            if "Absence %" in show.columns:
                styler = styler.map(_absence_cell, subset=["Absence %"])

            st.dataframe(styler, use_container_width=True, hide_index=True)

            st.download_button(
                "⬇ Download morning briefing CSV",
                filtered.to_csv(index=False).encode("utf-8"),
                file_name=f"morning_briefing_week{week}.csv",
                mime="text/csv",
                key=_ss_key("dl_briefing", week),
            )

            # ----------------------------------------------------------------
            # Section 3: Why different referrals? (collapsible explainer)
            # ----------------------------------------------------------------
            
            # ----------------------------------------------------------------
            # Section 4: Legacy breakdown tabs (visit-based, kept for detail)
            # ----------------------------------------------------------------
            st.markdown("---")
            st.markdown("### Engagement Detail (by Visit Pattern)")
            never_visited_count = int((sub["visit_status"] == "Never Visited").sum())
            low_visit_count = int((sub["visit_status"] == "low_visit (>30d)").sum())
            recent_count = int((sub["visit_status"].isin(["Recent (7-30d)", "Active (<7d)"])).sum())

            tab_never, tab_low_visit, tab_responsive = st.tabs([
                f"🚨 Never Visited ({never_visited_count})",
                f"⚠️ low_visit >30d ({low_visit_count})",
                f"✓ Recent Activity ({recent_count})",
            ])

            with tab_never:
                st.warning(
                    "**No campus engagement despite follow-ups.** "
                    "Likely a systemic barrier — financial, family, or health. "
                    "Consider home visit or CSM referral."
                )
                _detail_table(sub[sub["visit_status"] == "Never Visited"],
                              filter_key="never_visited")

            with tab_low_visit:
                st.error(
                    "**High Risk: Disengaged for 30+ days.** "
                    "Was active before. Needs warm re-engagement call; "
                    "if absent >51% and multiple missed calls → SDC."
                )
                _detail_table(sub[sub["visit_status"] == "low_visit (>30d)"],
                              filter_key="low_visit_visits")

            with tab_responsive:
                st.success(
                    "**Positive Signal: Recent campus activity detected.** "
                    "May be a communication gap or timing issue — try a different contact window."
                )
                _detail_table(
                    sub[sub["visit_status"].isin(["Recent (7-30d)", "Active (<7d)"])],
                    filter_key="responsive",
                )
        else:
            _render_dept_detail(df_week, open_b, week)


# ---------------------------------------------------------------------------
# Department detail panel (CCD-Joined, CSM, Treasurer, SFC, SDC)
# ---------------------------------------------------------------------------

def _render_dept_detail(df_week: pd.DataFrame, bucket: str, week: int):
    """
    Rich detail view for any department bucket.

    Layout:
      1. Outcome KPI strip  — Joined / In Process / Not Responding / Returned / Closed
      2. Outcome donut chart (Plotly)  +  reasons bar chart side by side
      3. Status filter chips  →  filtered student table
      4. Program breakdown (horizontal stacked bar)
    """
    summary = dept_outcome_summary(df_week, bucket)
    total   = summary["total"]

    if total == 0:
        st.info(f"No students currently in the {bucket} bucket.")
        return

    dept_df = summary["df"].copy()   # already has outcome_group column

    # ── 1. Outcome KPI strip ────────────────────────────────────────────────
    outcome_order = [
        "Joined / Resolved", "In Process", "Not Responding",
        "Returned to CCD", "Closed / Freeze", "Other",
    ]
    kpi_cols = st.columns(len(outcome_order))
    for col, grp in zip(kpi_cols, outcome_order):
        n   = summary["by_outcome"].get(grp, 0)
        pct = f"{n / total * 100:.0f}%" if total else "—"
        clr = OUTCOME_COLORS[grp]
        with col:
            st.markdown(
                f"""
                <div style="background:{clr};border-radius:8px;padding:10px 12px;
                            min-height:90px;text-align:center;">
                  <div style="color:rgba(255,255,255,.8);font-size:0.72rem;
                              font-weight:600;letter-spacing:.04em;text-transform:uppercase;
                              line-height:1.2;">{grp}</div>
                  <div style="color:white;font-size:1.7rem;font-weight:700;
                              line-height:1.1;margin-top:4px;">{n}</div>
                  <div style="color:rgba(255,255,255,.75);font-size:0.78rem;">{pct}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    st.markdown("")

    # ── 2. Donut + Reasons side by side ─────────────────────────────────────
    ch1, ch2 = st.columns(2)

    with ch1:
        # outcome donut
        grps   = [g for g in outcome_order if summary["by_outcome"].get(g, 0) > 0]
        values = [summary["by_outcome"][g] for g in grps]
        colors = [OUTCOME_COLORS[g] for g in grps]

        fig_donut = go.Figure(go.Pie(
            labels=grps, values=values,
            marker_colors=colors,
            hole=0.55,
            textinfo="label+percent",
            textfont_size=11,
            hovertemplate="%{label}: %{value} students<extra></extra>",
        ))
        fig_donut.update_layout(
            title=f"{bucket} — outcome breakdown",
            showlegend=False,
            margin=dict(l=10, r=10, t=40, b=10),
            height=300,
        )
        st.plotly_chart(fig_donut, use_container_width=True)

    with ch2:
        # reasons bar (same as CCD block but for this dept)
        reasons = dept_df["reason"].dropna()
        reasons = reasons[reasons.str.strip() != ""]
        if not reasons.empty:
            rc = reasons.value_counts().head(8).reset_index()
            rc.columns = ["Reason", "Count"]
            fig_r = px.bar(
                rc, x="Count", y="Reason", orientation="h",
                title="Top reasons given",
                color="Count", color_continuous_scale="Blues",
                text="Count",
            )
            fig_r.update_traces(textposition="outside", textfont_size=11)
            fig_r.update_layout(
                height=300, yaxis=dict(autorange="reversed"),
                showlegend=False, margin=dict(l=10, r=10, t=40, b=10),
            )
            st.plotly_chart(fig_r, use_container_width=True)
        else:
            st.info("No reason data for this bucket.")

    # ── 3. Status filter → student table ────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Filter by follow-up status")

    raw_statuses = ["All"] + summary["by_status"].index.tolist()
    status_sel = st.selectbox(
        "Show students with status",
        raw_statuses,
        key=_ss_key("dept_status_filter", bucket, week),
    )

    if status_sel == "All":
        display_df = dept_df.copy()
    elif status_sel == "(no status)":
        display_df = dept_df[dept_df["followup_status"].isna()].copy()
    else:
        display_df = dept_df[dept_df["followup_status"] == status_sel].copy()

    st.caption(
        f"Showing **{len(display_df)}** of {total} students "
        f"{'(all)' if status_sel == 'All' else f'with status: {status_sel}'}"
    )

    # colour rows by outcome group
    def _outcome_row_style(row):
        grp = row.get("outcome_group", "Other")
        bg = {
            "Joined / Resolved": "#f0fdf4",
            "In Process":        "#f0f9ff",
            "Not Responding":    "#fef2f2",
            "Returned to CCD":   "#fffbeb",
            "Closed / Freeze":   "#f8fafc",
        }.get(grp, "")
        return [f"background-color:{bg}" for _ in row]

    def _outcome_cell_style(val):
        clr = OUTCOME_COLORS.get(val, "#475569")
        return f"background-color:{clr};color:white;font-weight:600;"

    show_cols = [
        "student_id", "student_name", "program", "phone",
        "followup_status", "outcome_group",
        "no_of_follow_up", "reason", "remarks",
        "accumulative_absent_pct", "current_accumulative_absent_pct",
        "risk_category", "course_attendance_summary",
        "last_followup", "followup_date", "next_followup_date",
        "follow_up_by",
    ]
    show = display_df[[c for c in show_cols if c in display_df.columns]].copy()

    # round float columns
    for fc in ("accumulative_absent_pct", "current_accumulative_absent_pct"):
        if fc in show.columns:
            show[fc] = pd.to_numeric(show[fc], errors="coerce").round(1)

    styler = show.style.apply(_outcome_row_style, axis=1)
    if "outcome_group" in show.columns:
        styler = styler.map(_outcome_cell_style, subset=["outcome_group"])

    def _risk_bg(val):
        if pd.isna(val):
            return ""
        return f"background-color:{RISK_COLORS.get(val, '#475569')};color:white;font-weight:600;"

    if "risk_category" in show.columns:
        styler = styler.map(_risk_bg, subset=["risk_category"])

    st.dataframe(styler, use_container_width=True, hide_index=True)

    st.download_button(
        f"⬇ Download {bucket} students CSV",
        display_df.to_csv(index=False).encode("utf-8"),
        file_name=f"{bucket.lower().replace(' ', '_')}_week{week}.csv",
        mime="text/csv",
        key=_ss_key("dl_dept", bucket, week, status_sel),
    )

    # ── 4. Program breakdown stacked bar ────────────────────────────────────
    prog_df = dept_program_breakdown(dept_df)
    if not prog_df.empty:
        st.markdown("---")
        st.markdown("#### Program breakdown")

        # limit to top 12 programs by total count
        top_progs = (
            prog_df.groupby("program")["count"].sum()
                   .nlargest(12).index.tolist()
        )
        prog_df = prog_df[prog_df["program"].isin(top_progs)]

        fig_prog = px.bar(
            prog_df,
            x="count", y="program", color="outcome_group",
            orientation="h",
            color_discrete_map=OUTCOME_COLORS,
            title=f"{bucket} — students by program & outcome",
            labels={"count": "Students", "program": "Program",
                    "outcome_group": "Outcome"},
            text="count",
        )
        fig_prog.update_traces(textposition="inside", textfont_size=10)
        fig_prog.update_layout(
            height=max(280, len(top_progs) * 35),
            yaxis=dict(autorange="reversed"),
            legend=dict(orientation="h", y=-0.15),
            margin=dict(l=10, r=10, t=40, b=60),
        )
        st.plotly_chart(fig_prog, use_container_width=True)


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
