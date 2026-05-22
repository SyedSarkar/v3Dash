"""
In-app data loading + bucket assignment.

Bucket assignment rule (applied in this order, first match wins):
  1. followup_status == 'Closed'                 -> 'Closed'
  2. followup_status == 'Freeze'                 -> 'SFC'
  3. followup_status == 'Not Responding'
       AND refer_to in {CCD, blank}              -> 'Not Responding'
                                                    (sub: 'First Miss' if no_of_follow_up <= 1
                                                          'Red Zone'   if no_of_follow_up >  1)
  4. followup_status == 'Not Responding'
       AND refer_to is some other dept           -> that dept's bucket  (their headache)
  5. refer_to == 'CCD'                           -> 'CCD-Joined'
  6. refer_to == 'CSM' / 'Treasurer' / 'SFC' / 'SDC' -> that dept's bucket
  7. refer_to is blank/NaN  AND followup_status is blank -> dropped (NA per user, not yet contacted)

Buckets used for KPI tiles:
    BUCKET_ORDER = [Total Missing, CCD-Joined, CSM, Treasurer, SFC, SDC,
                    Not Responding, Closed]
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd
import streamlit as st


# Threshold (in % absent per course) above which a course is flagged "at risk".
# Mirrors the prior dashboard's tuning.
COURSE_AT_RISK_PCT = 22

RISK_LEVEL_ORDER = {"No Data": 0, "Safe Zone": 1, "Low Risk": 2, "At Risk": 3, "High Risk": 4}
RISK_COLORS = {
    "Safe Zone": "#16a34a",   # green
    "Low Risk":  "#eab308",   # yellow
    "At Risk":   "#f97316",   # orange
    "High Risk": "#dc2626",   # red
    "No Data":   "#475569",   # gray
}


BUCKET_ORDER = [
    "CCD-Joined",
    "CSM",
    "Treasurer",
    "SFC",
    "SDC",
    "Not Responding",
    "Closed",
    "Pending Contact",
]

DEPARTMENT_BUCKETS = ["CCD-Joined", "CSM", "Treasurer", "SFC", "SDC"]
FORWARDED_BUCKETS = ["CSM", "Treasurer", "SFC", "SDC"]   # for Tab-2 "Forwarded" tile

STATUS_COLORS = {
    "CCD-Joined":      "#16a34a",  # green
    "CSM":             "#0ea5e9",  # sky
    "Treasurer":       "#f59e0b",  # amber
    "SFC":             "#a855f7",  # violet
    "SDC":             "#ef4444",  # red
    "Not Responding":  "#dc2626",  # darker red
    "Closed":          "#64748b",  # slate
    "Pending Contact": "#475569",  # dark gray
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_long_csv(source) -> pd.DataFrame:
    """Read cleaned/long.csv and run post-load enrichment.

    Intentionally NOT cached: @st.cache_data only invalidates when this
    function's own source changes, not when downstream helpers like
    _post_load / parse_course_attendance change. low_visit cache silently
    served wrong risk numbers after parser tweaks. The data is small
    (a few hundred rows), so re-reading is cheap.
    """
    if hasattr(source, "read"):
        df = pd.read_csv(source, dtype={"student_id": str, "phone": str})
    else:
        df = pd.read_csv(Path(source), dtype={"student_id": str, "phone": str})
    return _post_load(df)


def load_long_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """For when ingest.py is called inline and a DataFrame is already in memory."""
    return _post_load(df.copy())


def _post_load(df: pd.DataFrame) -> pd.DataFrame:
    # normalise types
    if "no_of_follow_up" in df.columns:
        df["no_of_follow_up"] = (
            pd.to_numeric(df["no_of_follow_up"], errors="coerce").fillna(0).astype(int)
        )
    for c in ("refer_to", "followup_status", "last_followup", "reason", "remarks"):
        if c in df.columns:
            df[c] = df[c].apply(lambda v: v.strip() if isinstance(v, str) else v)

    df["bucket"] = df.apply(_assign_bucket, axis=1)
    df["red_zone"] = (
        (df["bucket"] == "Not Responding") & (df["no_of_follow_up"] > 1)
    )

    # Visit engagement enrichment (visit tracking metrics)
    df = _enrich_with_visit_engagement(df)

    # Course-wise risk (parsed from attendance_current — already normalized in ingest).
    df = _enrich_with_course_risk(df)
    return df


# ---------------------------------------------------------------------------
# Visit engagement enrichment
# ---------------------------------------------------------------------------

def _enrich_with_visit_engagement(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add engagement signals for Not Responding students.

    Metrics:
    - days_since_visit: Days elapsed since last_visit_date (null if never visited)
    - visit_status: Categorical (Never Visited, low_visit >30d, Recent, Active)
    - engagement_score: Combined risk indicator (0=healthy → 100=critical)
    """
    if "last_visit_date" not in df.columns:
        df["days_since_visit"] = pd.NA
        df["visit_status"] = "Unknown"
        df["engagement_score"] = pd.NA
        return df

    # Probe the first non-null value to infer a consistent format so pandas
    # doesn't fall back to dateutil element-by-element (which triggers a UserWarning).
    _sample = df["last_visit_date"].dropna().astype(str)
    _fmt = None
    for _s in _sample:
        for _candidate in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y",
                           "%d-%b-%Y", "%d-%b-%y", "%Y/%m/%d"):
            try:
                pd.to_datetime(_s, format=_candidate)
                _fmt = _candidate
                break
            except ValueError:
                continue
        if _fmt:
            break
    df["last_visit_dt"] = pd.to_datetime(
        df["last_visit_date"], format=_fmt, errors="coerce"
    )
    today = pd.Timestamp.now().normalize()
    df["days_since_visit"] = (today - df["last_visit_dt"]).dt.days

    df["visit_status"] = df.apply(lambda row: _categorize_visit_status(
        row.get("days_since_visit"),
        row.get("no_of_visit_in_semester")
    ), axis=1)

    df["engagement_score"] = df.apply(lambda row: _calculate_engagement_score(
        days_since=row.get("days_since_visit"),
        visit_count=row.get("no_of_visit_in_semester"),
        follow_ups=row.get("no_of_follow_up")
    ), axis=1)

    return df


def _categorize_visit_status(days_since_visit: float, visit_count: float) -> str:
    """Returns: 'Never Visited', 'low_visit (>30d)', 'Recent (7-30d)', 'Active (<7d)', 'Unknown'"""
    if pd.isna(visit_count) or visit_count == 0:
        return "Never Visited"
    if pd.isna(days_since_visit):
        return "Unknown"
    if days_since_visit > 30:
        return "low_visit (>30d)"
    if days_since_visit > 7:
        return "Recent (7-30d)"
    return "Active (<7d)"


def _calculate_engagement_score(days_since: float, visit_count: float,
                                follow_ups: float) -> int:
    """
    Engagement score (0-100, higher = riskier).

    Weights:
    - 50%: Time since last visit (freshness)
    - 30%: Visit count in semester (consistency)
    - 20%: Follow-ups attempted (resistance to contact)
    """
    if pd.isna(visit_count) or visit_count == 0:
        return 100

    days_score = min(100, (days_since or 0) * (100 / 60)) if pd.notna(days_since) else 50
    visit_score = max(0, 100 - (visit_count * 20))
    followup_score = min(100, (follow_ups or 0) * 30)

    return int(0.5 * days_score + 0.3 * visit_score + 0.2 * followup_score)


# ---------------------------------------------------------------------------
# Course-wise attendance + risk classification
# ---------------------------------------------------------------------------

# matches "ACC-410-A 6/28 12-FEB-26" (date is optional — sometimes the export
# omits the last-attended date when there's been no attendance at all, e.g.
# "ENG-205-A 1/1" or "MKT-407-A 3/3")
_COURSE_ENTRY_RE = re.compile(
    r"^([A-Z]+-\d+[A-Z]?-[A-Z])\s+(\d+)\s*/\s*(\d+)(?:\s+([\d\-A-Za-z]+))?\s*$"
)


def parse_course_attendance(s) -> list[dict]:
    """Parse 'CODE-SEC absent/total date' entries (comma- or newline-separated)."""
    if pd.isna(s) or not str(s).strip():
        return []
    out: list[dict] = []
    for raw in re.split(r"[,\n]+", str(s)):
        entry = raw.strip().rstrip(",")  # tolerate trailing commas
        if not entry:
            continue
        m = _COURSE_ENTRY_RE.match(entry)
        if not m:
            continue
        code = m.group(1)
        absent, total = int(m.group(2)), int(m.group(3))
        date = m.group(4) or ""
        pct = round((absent / total) * 100, 1) if total else 0.0
        out.append({"course": code, "absent": absent, "total": total,
                    "absent_pct": pct, "last_attended": date})
    return out


def classify_course_risk(courses: list[dict]) -> tuple[str, int, int]:
    """Return (category, n_courses_at_risk, n_courses_total)."""
    if not courses:
        return ("No Data", 0, 0)
    total = len(courses)
    at_risk = sum(1 for c in courses if c["absent_pct"] > COURSE_AT_RISK_PCT)
    if at_risk == 0:
        cat = "Safe Zone"
    elif at_risk == total:
        cat = "High Risk"
    elif at_risk / total >= 0.5:
        cat = "At Risk"
    else:
        cat = "Low Risk"
    return (cat, at_risk, total)


def _enrich_with_course_risk(df: pd.DataFrame) -> pd.DataFrame:
    if "attendance_current" not in df.columns:
        df["risk_category"] = "No Data"
        df["courses_at_risk"] = 0
        df["total_courses"] = 0
        df["course_attendance_summary"] = pd.NA
        return df

    parsed = df["attendance_current"].apply(parse_course_attendance)
    risks = parsed.apply(classify_course_risk)

    df["risk_category"]   = risks.apply(lambda t: t[0])
    df["courses_at_risk"] = risks.apply(lambda t: t[1])
    df["total_courses"]   = risks.apply(lambda t: t[2])
    df["risk_level"]      = df["risk_category"].map(RISK_LEVEL_ORDER).fillna(0).astype(int)

    df["course_attendance_summary"] = parsed.apply(
        lambda lst: ", ".join(f"{c['course']}: {c['absent_pct']}%" for c in lst)
        if lst else pd.NA
    )
    return df


def risk_distribution(df_subset: pd.DataFrame) -> pd.Series:
    """Counts of risk categories within the given subset (Safe -> High order)."""
    if df_subset.empty or "risk_category" not in df_subset.columns:
        return pd.Series(dtype=int)
    counts = df_subset["risk_category"].value_counts()
    order = ["High Risk", "At Risk", "Low Risk", "Safe Zone", "No Data"]
    return counts.reindex(order).dropna().astype(int)


# ---------------------------------------------------------------------------
# Bucket logic
# ---------------------------------------------------------------------------

def _assign_bucket(row) -> str:
    status = row.get("followup_status")
    refer = row.get("refer_to")

    s = str(status).strip().lower() if pd.notna(status) else ""
    r = str(refer).strip() if pd.notna(refer) else ""

    # rule 7 — neither contacted nor referred -> Pending Contact (queued for first call)
    if s == "" and r == "":
        return "Pending Contact"

    # rule 0 — For CSM/Treasurer/SFC/SDC, refer_to takes priority over status
    # If refer_to is one of these departments, bucket by refer_to regardless of status
    if r.upper() in ("CSM", "TREASURER", "TREASURE", "SFC", "SDC"):
        return _norm_dept_bucket(r)

    # rule 1 / 2
    if s == "closed":
        return "Closed"
    if s == "freeze":
        return "SFC"

    # rule 3 / 4 — Not Responding
    if s == "not responding":
        if r in ("", "CCD"):
            return "Not Responding"
        return _norm_dept_bucket(r)

    # rule 5 / 6 — bucket by Refer To
    if r in ("", "CCD"):
        return "CCD-Joined"
    return _norm_dept_bucket(r)


def _norm_dept_bucket(refer_value: str) -> str:
    """Map Refer To values onto canonical bucket names (with simple aliases)."""
    r = refer_value.strip().upper()
    aliases = {
        "CSM": "CSM",
        "TREASURER": "Treasurer",
        "TREASURE": "Treasurer",
        "SFC": "SFC",
        "SDC": "SDC",
        "CCD": "CCD-Joined",
    }
    return aliases.get(r, refer_value.strip())


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

def bucket_counts(df_week: pd.DataFrame) -> dict[str, int]:
    counts = df_week["bucket"].value_counts(dropna=True).to_dict()
    return {b: int(counts.get(b, 0)) for b in BUCKET_ORDER}


def not_responding_split(df_week: pd.DataFrame) -> dict[str, int]:
    nr = df_week[df_week["bucket"] == "Not Responding"]
    return {
        "First Miss": int((nr["no_of_follow_up"] <= 1).sum()),
        "Red Zone":   int((nr["no_of_follow_up"] >  1).sum()),
    }


def not_responding_by_visit_status(df_week: pd.DataFrame) -> dict[str, int]:
    """Split Not Responding into engagement tiers based on visit patterns."""
    nr = df_week[df_week["bucket"] == "Not Responding"]
    return {
        "Never Visited": int((nr["visit_status"] == "Never Visited").sum()),
        "low_visit (>30d)": int((nr["visit_status"] == "low_visit (>30d)").sum()),
        "Recent (7-30d)": int((nr["visit_status"] == "Recent (7-30d)").sum()),
        "Active (<7d)": int((nr["visit_status"] == "Active (<7d)").sum()),
    }


def engagement_distribution(df_subset: pd.DataFrame) -> dict:
    """Return engagement score distribution breakdown (critical/high/medium/low)."""
    if "engagement_score" not in df_subset.columns:
        return {"critical": 0, "high": 0, "medium": 0, "low": 0, "mean": 0.0}
    scores = df_subset["engagement_score"].dropna()
    if scores.empty:
        return {"critical": 0, "high": 0, "medium": 0, "low": 0, "mean": 0.0}
    return {
        "critical": int((scores >= 70).sum()),
        "high": int(((scores >= 50) & (scores < 70)).sum()),
        "medium": int(((scores >= 30) & (scores < 50)).sum()),
        "low": int((scores < 30).sum()),
        "mean": float(scores.mean()),
    }



# ---------------------------------------------------------------------------
# Triage segmentation (gate-entry × absence × follow-up layering)
# ---------------------------------------------------------------------------

def _assign_triage_segment(row) -> str:
    """
    Three-tier triage based on:
      - visit_status  (gate / campus engagement proxy)
      - current_accumulative_absent_pct
      - bucket / followup status

    Tier 1 CRITICAL  — on campus but skipping + not reachable  → SDC
    Tier 2 HIGH RISK — in process or low_visit, high absences      → escalate
    Tier 3 MONITOR   — everything else that is Not Responding
    """
    bucket = str(row.get("bucket", "")).strip()
    visit  = str(row.get("visit_status", "")).strip()
    absent = row.get("current_accumulative_absent_pct")
    fu     = int(row.get("no_of_follow_up", 0) or 0)

    if bucket != "Not Responding":
        return "other"

    high_absence = pd.notna(absent) and float(absent) >= 51

    # CRITICAL: came to campus (Active or Recent) + high absence + not answering
    if visit in ("Active (<7d)", "Recent (7-30d)") and high_absence:
        return "critical"

    # HIGH RISK: low_visit engagement + high absence, OR multiple failed follow-ups
    if (visit == "low_visit (>30d)" and high_absence) or (fu >= 2 and high_absence):
        return "high_risk"

    # suspecious: never visited at all — different problem, different referral
    if visit == "Never Visited":
        return "suspecious"

    # DEFAULT MONITOR
    return "monitor"


TRIAGE_LABELS = {
    "critical":  "Critical — On Campus, Skipping Classes",
    "high_risk": "High Risk — low_visit Engagement, High Absences",
    "suspecious":   "suspecious / Family — Never Visited Campus",
    "monitor":   "Monitor — Lower Severity",
}

TRIAGE_COLORS = {
    "critical":  "#dc2626",   # red
    "high_risk": "#f97316",   # orange
    "suspecious":   "#a855f7",   # purple
    "monitor":   "#eab308",   # yellow
    "other":     "#475569",   # gray
}

TRIAGE_REFERRAL = {
    "critical":  "→ Refer to SDC (Student Discipline Center)",
    "high_risk": "→ Escalate via CCD; consider SDC if unresolved",
    "suspecious":   "→ suspecious/home visit; refer to CSM or SFC",
    "monitor":   "→ Standard follow-up; continue CCD contact",
}


def enrich_with_triage(df_week: pd.DataFrame) -> pd.DataFrame:
    """Add triage_segment column and days_since_followup to a week's dataframe."""
    df = df_week.copy()
    df["triage_segment"] = df.apply(_assign_triage_segment, axis=1)

    # days since last follow-up attempt
    if "followup_date" in df.columns:
        df["followup_dt"] = pd.to_datetime(df["followup_date"], errors="coerce")
        today = pd.Timestamp.now().normalize()
        df["days_since_followup"] = (today - df["followup_dt"]).dt.days
    else:
        df["days_since_followup"] = pd.NA

    return df


def triage_counts(df_week: pd.DataFrame) -> dict[str, int]:
    """Count of Not Responding students per triage tier."""
    nr = df_week[df_week["bucket"] == "Not Responding"].copy()
    if "triage_segment" not in nr.columns:
        nr = enrich_with_triage(nr)
    counts = nr["triage_segment"].value_counts().to_dict()
    return {k: int(counts.get(k, 0)) for k in ["critical", "high_risk", "suspecious", "monitor"]}


def morning_briefing_df(df_week: pd.DataFrame) -> pd.DataFrame:
    """
    Return the priority hotlist for Not Responding students with all columns
    needed for the morning briefing table, sorted by triage_segment severity.
    """
    nr = df_week[df_week["bucket"] == "Not Responding"].copy()
    if "triage_segment" not in nr.columns:
        nr = enrich_with_triage(nr)

    sort_order = {"critical": 0, "high_risk": 1, "suspecious": 2, "monitor": 3, "other": 4}
    nr["_sort"] = nr["triage_segment"].map(sort_order).fillna(4)

    display_cols = [
        "student_id", "student_name", "program",
        "visit_status", "current_accumulative_absent_pct",
        "no_of_follow_up", "days_since_followup",
        "followup_status", "triage_segment",
        "phone", "reason", "remarks",
    ]
    available = [c for c in display_cols if c in nr.columns]
    return (nr.sort_values(["_sort", "no_of_follow_up"], ascending=[True, False])
              .reset_index(drop=True)[available])


# ---------------------------------------------------------------------------
# Department bucket detail helpers (CCD-Joined, CSM, Treasurer, SFC, SDC)
# ---------------------------------------------------------------------------

# Canonical outcome groups — shared across all departments now that CSM/SFC/etc.
# also record Potential Joined / Potential Inprocess / Not Responding.
OUTCOME_GROUPS = {
    "Joined / Resolved": ["Potential Joined", "CCD-Joined", "Joined"],
    "In Process":        ["Potential Inprocess", "Inprocess", "In Process"],
    "Not Responding":    ["Not Responding"],
    "Returned to CCD":   ["Return to CCD", "Return CCD"],
    "Closed / Freeze":   ["Closed", "Freeze"],
    "Other":             [],          # catch-all
}

OUTCOME_COLORS = {
    "Joined / Resolved": "#16a34a",
    "In Process":        "#0ea5e9",
    "Not Responding":    "#dc2626",
    "Returned to CCD":   "#f59e0b",
    "Closed / Freeze":   "#64748b",
    "Other":             "#475569",
}


def _outcome_group(status: str) -> str:
    """Map a raw followup_status string to a canonical outcome group label."""
    if pd.isna(status) or str(status).strip() == "":
        return "Other"
    s = str(status).strip()
    for group, members in OUTCOME_GROUPS.items():
        if group == "Other":
            continue
        if any(s.lower() == m.lower() for m in members):
            return group
    # partial match fallback
    sl = s.lower()
    if "joined" in sl:
        return "Joined / Resolved"
    if "inprocess" in sl or "in process" in sl:
        return "In Process"
    if "not responding" in sl:
        return "Not Responding"
    if "return" in sl:
        return "Returned to CCD"
    if "closed" in sl or "freeze" in sl:
        return "Closed / Freeze"
    return "Other"


def dept_outcome_summary(df_week: pd.DataFrame, bucket: str) -> dict:
    """
    For one bucket (e.g. 'CSM'), return outcome group counts and totals.
    Returns dict with keys: total, by_outcome (dict group->count), by_status (Series).
    """
    sub = df_week[df_week["bucket"] == bucket].copy()
    if sub.empty:
        return {"total": 0, "by_outcome": {}, "by_status": pd.Series(dtype=int)}

    sub["outcome_group"] = sub["followup_status"].apply(_outcome_group)
    by_outcome = sub["outcome_group"].value_counts().to_dict()
    by_status  = sub["followup_status"].fillna("(no status)").value_counts()

    return {
        "total":      len(sub),
        "by_outcome": {g: int(by_outcome.get(g, 0)) for g in OUTCOME_GROUPS},
        "by_status":  by_status,
        "df":         sub,
    }


def dept_program_breakdown(df_dept: pd.DataFrame) -> pd.DataFrame:
    """Top programs represented in a department bucket, with outcome split."""
    if df_dept.empty or "program" not in df_dept.columns:
        return pd.DataFrame()
    df = df_dept.copy()
    if "outcome_group" not in df.columns:
        df["outcome_group"] = df["followup_status"].apply(_outcome_group)
    return (
        df.groupby(["program", "outcome_group"])
          .size()
          .reset_index(name="count")
          .sort_values("count", ascending=False)
    )


def students_never_visited(df_week: pd.DataFrame) -> pd.DataFrame:
    """Flag students with zero campus engagement despite multiple follow-ups."""
    nr = df_week[df_week["bucket"] == "Not Responding"]
    return nr[(nr["visit_status"] == "Never Visited") & (nr["no_of_follow_up"] >= 2)].copy()


def students_low_visit_visit(df_week: pd.DataFrame, days_threshold: int = 30) -> pd.DataFrame:
    """Students not visited in N+ days but previously engaged."""
    nr = df_week[df_week["bucket"] == "Not Responding"]
    return nr[
        (nr["no_of_visit_in_semester"] > 0) &
        (nr["days_since_visit"] >= days_threshold)
    ].copy()


def forwarded_count(df_week: pd.DataFrame) -> int:
    return int(df_week["bucket"].isin(FORWARDED_BUCKETS).sum())


def status_distribution_in_dept(df_week: pd.DataFrame, dept_bucket: str) -> pd.Series:
    """For Tab 2 drill: for students in a department, show their followup_status counts."""
    sub = df_week[df_week["bucket"] == dept_bucket]
    return sub["followup_status"].fillna("(no status)").value_counts()


def students_in_bucket(df_week: pd.DataFrame, bucket: str) -> pd.DataFrame:
    return df_week[df_week["bucket"] == bucket].copy()


def students_in_dept_with_status(df_week: pd.DataFrame, dept_bucket: str, status: str) -> pd.DataFrame:
    sub = df_week[df_week["bucket"] == dept_bucket]
    if status == "(no status)":
        return sub[sub["followup_status"].isna()].copy()
    return sub[sub["followup_status"] == status].copy()


# ---------------------------------------------------------------------------
# Journey helpers (Tab 2 — full semester timeline)
# ---------------------------------------------------------------------------

def ever_forwarded_ids(df_all: pd.DataFrame) -> set[str]:
    """Student IDs that were ever forwarded to a non-CCD department in any week."""
    mask = df_all["bucket"].isin(FORWARDED_BUCKETS)
    return set(df_all.loc[mask, "student_id"].dropna().unique())


def latest_row_per_student(df_all: pd.DataFrame) -> pd.DataFrame:
    """For every student, return the row from the latest week they appear in."""
    idx = df_all.groupby("student_id")["week"].idxmax()
    return df_all.loc[idx].reset_index(drop=True)


def first_forward_row_per_student(df_all: pd.DataFrame) -> pd.DataFrame:
    """For every ever-forwarded student, return the row from the FIRST week they
    appeared in a forwarded bucket. Useful for 'forwarded by which dept first'."""
    fwd = df_all[df_all["bucket"].isin(FORWARDED_BUCKETS)]
    idx = fwd.groupby("student_id")["week"].idxmin()
    return fwd.loc[idx].reset_index(drop=True)


def student_timeline(df_all: pd.DataFrame, student_id: str) -> pd.DataFrame:
    """All rows for one student, sorted by week (a chronological case file)."""
    return (df_all[df_all["student_id"] == student_id]
            .sort_values("week")
            .reset_index(drop=True))


def journey_pivot(df_all: pd.DataFrame, student_ids: list[str]) -> pd.DataFrame:
    """
    For the given students, return a wide table:
        student_id | student_name | program | wk_1 | wk_2 | ... | wk_N | latest_refer_to
    Each wk_X cell holds the bucket for that student in that week (empty if absent).
    """
    weeks = sorted(df_all["week"].dropna().unique().astype(int).tolist())
    sub = df_all[df_all["student_id"].isin(student_ids)].copy()

    pivoted = sub.pivot_table(
        index="student_id", columns="week", values="bucket",
        aggfunc="first").reindex(columns=weeks)
    pivoted.columns = [f"wk_{int(w)}" for w in pivoted.columns]

    latest = latest_row_per_student(sub).set_index("student_id")
    pivoted["latest_refer_to"] = latest["refer_to"]
    pivoted["latest_status"] = latest["followup_status"]
    pivoted["latest_followups"] = latest["no_of_follow_up"]

    # attach static identity columns
    ident = (sub.sort_values("week")
                .drop_duplicates("student_id", keep="last")
                .set_index("student_id")[["student_name", "program", "phone"]])
    pivoted = ident.join(pivoted, how="right")
    return pivoted.reset_index()


def department_journey_summary(df_all: pd.DataFrame, dept: str) -> dict:
    """
    Aggregate stats for one forwarded department across the whole semester:
      - total ever forwarded
      - currently with this dept (latest week the student appears in == this bucket)
      - resolved (latest bucket == CCD-Joined or Closed)
      - returned to CCD (latest followup_status == 'Return to CCD')
      - still not responding
    """
    fwd = df_all[df_all["bucket"] == dept]
    ids = set(fwd["student_id"].dropna().unique())
    if not ids:
        return {"ever": 0, "currently": 0, "resolved": 0,
                "returned": 0, "still_open": 0, "ids": []}

    latest = latest_row_per_student(df_all[df_all["student_id"].isin(ids)])
    currently = int((latest["bucket"] == dept).sum())
    resolved  = int(latest["bucket"].isin(["CCD-Joined", "Closed"]).sum())
    returned  = int(latest["followup_status"].fillna("").str.lower()
                          .eq("return to ccd").sum())
    not_resp  = int((latest["bucket"] == "Not Responding").sum())

    return {
        "ever":       len(ids),
        "currently":  currently,
        "resolved":   resolved,
        "returned":   returned,
        "still_open": not_resp,
        "ids":        sorted(ids),
    }


# ---------------------------------------------------------------------------
# Cross-week comparison (kept for ad-hoc use; Tab 2 no longer uses it)
# ---------------------------------------------------------------------------

def compare_two_weeks(df_initial: pd.DataFrame, df_followup: pd.DataFrame) -> pd.DataFrame:
    """
    Inner join on student_id; columns suffixed _initial and _followup so we can
    show 'Status in Week X -> Status in Week Y' side by side.
    """
    keep = ["student_id", "student_name", "program", "phone",
            "refer_to", "last_followup", "followup_status",
            "no_of_follow_up", "reason", "remarks", "bucket"]
    a = df_initial[[c for c in keep if c in df_initial.columns]].copy()
    b = df_followup[[c for c in keep if c in df_followup.columns]].copy()
    merged = a.merge(b, on="student_id", how="left",
                     suffixes=("_initial", "_followup"))
    return merged


def students_in_dept_with_status_compared(merged: pd.DataFrame,
                                          dept_bucket: str,
                                          status: str) -> pd.DataFrame:
    sub = merged[merged["bucket_initial"] == dept_bucket]
    if status == "(no status)":
        return sub[sub["followup_status_followup"].isna()].copy()
    return sub[sub["followup_status_followup"] == status].copy()
