"""
Visit engagement helpers and utilities.

This module provides helper functions for interpreting and using visit engagement metrics.
"""

from __future__ import annotations

import pandas as pd


VISIT_STATUS_COLORS = {
    "Never Visited": "#dc2626",      # red
    "Stale (>30d)": "#f97316",       # orange
    "Recent (7-30d)": "#eab308",     # yellow
    "Active (<7d)": "#16a34a",       # green
    "Unknown": "#475569",             # gray
}

ENGAGEMENT_SCORE_RANGES = {
    "Critical (70-100)": (70, 100),
    "High (50-69)": (50, 69),
    "Medium (30-49)": (30, 49),
    "Low (0-29)": (0, 29),
}


def recommend_intervention(row: pd.Series) -> str:
    """
    Based on visit patterns, suggest intervention strategy.

    Returns recommended action string.
    """
    visit_status = row.get("visit_status")
    no_follow_up = row.get("no_of_follow_up", 0)
    engagement_score = row.get("engagement_score", 0)

    # Never visited + multiple attempts = systemic barrier
    if visit_status == "Never Visited" and no_follow_up >= 2:
        return "🏠 HOME VISIT + Personal Call"

    # Stale disengagement = re-engagement needed
    if visit_status == "Stale (>30d)" and no_follow_up >= 1:
        return "📞 Re-engagement Call + SMS"

    # Recent visits = likely receptive
    if visit_status in ["Recent (7-30d)", "Active (<7d)"]:
        return "💬 Warm Follow-up (likely receptive)"

    # Generic fallback
    return "📋 Standard Follow-up"


def interpretation_guide() -> dict:
    """Return interpretation guide for engagement metrics."""
    return {
        "never_visited": {
            "definition": "Zero campus engagement despite being in system",
            "what_it_means": "Student has not visited campus since enrollment",
            "common_causes": [
                "Financial barriers (transport, fees)",
                "Health/family issues",
                "Lack of awareness about program",
                "Enrolled but never started",
                "Academic/career mismatch"
            ],
            "intervention": "Personal outreach via home visit or phone call",
            "urgency": "🚨 CRITICAL"
        },
        "stale_visit": {
            "definition": "Previously visited, now disengaged for 30+ days",
            "what_it_means": "Student was active but engagement has dropped off sharply",
            "common_causes": [
                "Lost motivation or interest",
                "Academic struggle or failure",
                "External pressure (family, work)",
                "Feels isolated or unsupported",
                "Scheduling conflicts"
            ],
            "intervention": "Warm, empathetic re-engagement call (different tone)",
            "urgency": "⚠️ HIGH RISK"
        },
        "recent_visit": {
            "definition": "Campus activity within last 7-30 days despite Not Responding status",
            "what_it_means": "Student IS engaged but communication missed in follow-up window",
            "common_causes": [
                "Follow-up call came on wrong day/time",
                "Incorrect contact info (phone/WhatsApp)",
                "Both parties missed each other",
                "Student active but didn't respond to initial contact",
                "Timing issue: new student, still finding feet"
            ],
            "intervention": "Gentle reminder and proper contact window scheduling",
            "urgency": "✓ POSSIBLE RECOVERY"
        },
        "active_visit": {
            "definition": "Campus activity within last 7 days despite Not Responding status",
            "what_it_means": "Student is actively engaged; likely a communication gap",
            "common_causes": [
                "Attendance is good, just missed follow-up",
                "Different contact method needed",
                "Student may not realize they're flagged",
                "Recent re-engagement success"
            ],
            "intervention": "Simple check-in; likely just needs confirmation",
            "urgency": "✓ LIKELY RECOVERABLE"
        },
    }


def semester_visit_statistics(df_week: pd.DataFrame) -> dict:
    """Calculate semester-wide visit engagement statistics."""
    nr = df_week[df_week["bucket"] == "Not Responding"]

    if nr.empty:
        return {}

    never_visited = nr[nr["visit_status"] == "Never Visited"]
    stale = nr[nr["visit_status"] == "Stale (>30d)"]
    recent = nr[nr["visit_status"].isin(["Recent (7-30d)", "Active (<7d)"])]

    return {
        "total_not_responding": len(nr),
        "never_visited_count": len(never_visited),
        "never_visited_pct": len(never_visited) / len(nr) * 100 if len(nr) > 0 else 0,
        "stale_count": len(stale),
        "stale_pct": len(stale) / len(nr) * 100 if len(nr) > 0 else 0,
        "recent_count": len(recent),
        "recent_pct": len(recent) / len(nr) * 100 if len(nr) > 0 else 0,
        "avg_engagement_score": nr["engagement_score"].mean() if "engagement_score" in nr.columns else 0,
        "avg_visit_count": nr["no_of_visit_in_semester"].mean() if "no_of_visit_in_semester" in nr.columns else 0,
        "avg_follow_ups": nr["no_of_follow_up"].mean() if "no_of_follow_up" in nr.columns else 0,
    }
