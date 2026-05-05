"""
Ingest weekly raw Excel files into a single tidy long-format CSV.

Usage:
    python ingest.py                       # uses default raw_data/ -> cleaned/long.csv
    python ingest.py path/to/raw_folder    # custom raw folder
    python ingest.py path/to/raw_folder path/to/out.csv

Each weekly file is expected to look like:
    Row 1-2: metadata (Semester, Week, Status filters)
    Row N:   header beginning with "Sr#"
    Row N+1+: student records

The week number is parsed from the filename (first integer found).

Column variants normalized:
- "Accumulative Absent % \n >=80%"  -> accumulative_absent_pct
- "Current Accumulative Absent %"   -> current_accumulative_absent_pct
- "Current Week Absent %"           -> current_week_absent_pct
  (the >=80% / >=20% suffix differences across weeks are stripped)
- "Status" (academic) vs "Status.1" (followup) handled by position
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd


CANONICAL_COLUMNS = [
    "week",
    "sr_no",
    "last_status_label",         # 1-New Entry / 2-New Entry / 3-Continue
    "student_id",
    "student_name",
    "program",
    "phone",
    "academic_status",           # Active / Deactive
    "no_of_semester",
    "accumulative_absent_pct",
    "current_accumulative_absent_pct",
    "current_week_absent_pct",
    "balance",
    "last_visit_date",
    "no_of_visit_in_semester",
    "refer_to",                  # CCD / CSM / Treasurer / SFC / SDC / blank
    "last_followup",             # CCD / CSM / Department
    "followup_date",
    "followup_status",           # Potential Joined / Inprocess / Not Responding / Closed / Freeze / Return to CCD
    "followup_week",
    "reason",
    "remarks",
    "follow_up_by",
    "no_of_follow_up",
    "next_followup_date",
    "next_followup_remarks",
    "attendance_prev",           # course-wise string from previous snapshot
    "attendance_current",        # course-wise string current snapshot
]


def _norm(s: str) -> str:
    s = str(s).strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def _detect_header_row(path: Path, sheet=0) -> int:
    """Return zero-based index of the header row (the one starting with 'Sr#')."""
    probe = pd.read_excel(path, sheet_name=sheet, header=None, nrows=15)
    for i, row in probe.iterrows():
        for v in row.values:
            if isinstance(v, str) and re.match(r"^\s*sr\s*#", v, flags=re.IGNORECASE):
                return i
    raise ValueError(f"Could not find a header row containing 'Sr#' in {path.name}")


def _week_from_filename(path: Path) -> int:
    m = re.search(r"(\d+)", path.stem)
    if not m:
        raise ValueError(f"No week number in filename: {path.name}")
    return int(m.group(1))


def _pct_to_float(v):
    if pd.isna(v):
        return pd.NA
    s = str(v).strip().replace("%", "").replace(",", "")
    if s == "" or s.lower() == "nan":
        return pd.NA
    try:
        return float(s)
    except ValueError:
        return pd.NA


def _clean_phone(v):
    """Return canonical 11-digit Pakistani mobile (e.g. '03444606208') or pd.NA."""
    if pd.isna(v):
        return pd.NA
    s = str(v).strip()
    # Excel often turns "03444606208" into the float 3444606208.0 — drop the .0 first
    s = re.sub(r"\.0+$", "", s)
    digits = re.sub(r"\D", "", s)
    if len(digits) == 10 and digits.startswith("3"):
        digits = "0" + digits          # restore leading zero Excel ate
    elif len(digits) == 12 and digits.startswith("92"):
        digits = "0" + digits[2:]      # +92344... -> 0344...
    if len(digits) < 10:
        return pd.NA
    return digits


def _clean_id(v):
    if pd.isna(v):
        return pd.NA
    s = str(v).strip()
    if s == "" or s.lower() == "nan":
        return pd.NA
    m = re.search(r"\d{5,}", s)
    return m.group(0) if m else s


def _build_column_map(raw_columns: list[str]) -> dict[str, str]:
    """
    Map the messy real column names to canonical names.

    Two columns share the base name 'Status' (academic + followup) — pandas
    auto-suffixes the second as 'Status.1'. We keep that distinction here.
    """
    mapping: dict[str, str] = {}
    seen_status = 0  # to handle Status / Status.1 ordering

    for raw in raw_columns:
        n = _norm(raw)

        # collapse "%_>=80%" / "%_>=20%" suffixes (week-to-week wording change)
        n_base = re.sub(r"_(\d+|>=?\d+|gt\d+|gte\d+)$", "", n)
        n_base = re.sub(r"_(\d+)$", "", n_base)

        # academic vs followup status — by appearance order
        if n in ("status", "status_1") or n_base == "status":
            if seen_status == 0:
                mapping[raw] = "academic_status"
            else:
                mapping[raw] = "followup_status"
            seen_status += 1
            continue

        # rule-based renames using normalized name fragments
        rules = [
            ("sr", "sr_no"),
            ("last_status", "last_status_label"),
            ("roll_no", "student_id"),
            ("name", "student_name"),
            ("program", "program"),
            ("mobile", "phone"),
            ("no_of_semester", "no_of_semester"),
            ("accumulative_absent", "accumulative_absent_pct"),
            ("current_accumulative_absent", "current_accumulative_absent_pct"),
            ("current_week_absent", "current_week_absent_pct"),
            ("balance", "balance"),
            ("last_visit_date", "last_visit_date"),
            ("no_of_visit", "no_of_visit_in_semester"),
            ("refer_to", "refer_to"),
            ("last_followup", "last_followup"),
            ("date", "followup_date"),
            ("week", "followup_week"),
            ("reason", "reason"),
            ("remarks", "remarks"),
            ("follow_up_by", "follow_up_by"),
            ("no_of_follow_up", "no_of_follow_up"),
            ("next_follow_up_date", "next_followup_date"),
            ("next_follow_up_remarks", "next_followup_remarks"),
            ("attendance", "attendance_prev"),
            ("current_attendance", "attendance_current"),
        ]

        # most-specific matches first — sort rules by descending key length
        for key, target in sorted(rules, key=lambda kv: -len(kv[0])):
            if key in n:
                # only assign if target slot not already taken by a previous column
                if target not in mapping.values():
                    mapping[raw] = target
                    break

    # second pass: distinguish attendance_prev vs attendance_current
    # the "Current Attendance" header always has 'current' in it; the plain "Attendance"
    # header doesn't. Re-resolve to be safe.
    attn_cols = [c for c in raw_columns if "attend" in _norm(c)]
    if len(attn_cols) >= 2:
        # the one with 'current' is current; the other is prev
        for c in attn_cols:
            if "current" in _norm(c):
                mapping[c] = "attendance_current"
            else:
                mapping[c] = "attendance_prev"

    return mapping


def clean_one_week(path: Path) -> pd.DataFrame:
    week = _week_from_filename(path)
    header_row = _detect_header_row(path)

    df = pd.read_excel(path, header=header_row)
    # drop rows that are entirely NaN
    df = df.dropna(how="all").reset_index(drop=True)
    # drop rows with no roll number / sr number — these are footer / blank lines
    if "Roll No" in df.columns:
        df = df[df["Roll No"].notna()].reset_index(drop=True)
    elif "Sr#" in df.columns:
        df = df[df["Sr#"].notna()].reset_index(drop=True)

    colmap = _build_column_map(list(df.columns))
    df = df.rename(columns=colmap)

    # ensure every canonical column exists
    for c in CANONICAL_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA

    df["week"] = week

    # type cleanup
    df["student_id"] = df["student_id"].apply(_clean_id)
    df["phone"] = df["phone"].apply(_clean_phone)
    for pc in ("accumulative_absent_pct", "current_accumulative_absent_pct", "current_week_absent_pct"):
        df[pc] = df[pc].apply(_pct_to_float)
    df["no_of_follow_up"] = (
        pd.to_numeric(df["no_of_follow_up"].astype(str).str.extract(r"(\d+)", expand=False),
                      errors="coerce").fillna(0).astype(int)
    )

    # strip whitespace on string columns
    for c in df.select_dtypes(include="object").columns:
        df[c] = df[c].apply(lambda v: v.strip() if isinstance(v, str) else v)

    # drop rows still missing student_id (these are noise)
    df = df[df["student_id"].notna()].reset_index(drop=True)

    return df[CANONICAL_COLUMNS].copy()


def ingest_folder(raw_dir: Path, out_csv: Path) -> pd.DataFrame:
    files = sorted(raw_dir.glob("*.xlsx"))
    files = [f for f in files if not f.name.startswith("~$")]
    if not files:
        raise FileNotFoundError(f"No .xlsx files in {raw_dir}")

    frames = []
    for f in files:
        print(f"  - {f.name}")
        try:
            frames.append(clean_one_week(f))
        except Exception as e:
            print(f"    ! skipped ({e})")

    long_df = pd.concat(frames, ignore_index=True)
    long_df = long_df.sort_values(["week", "student_id"]).reset_index(drop=True)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    # cast IDs/phone to string so CSV round-trip doesn't infer them as floats
    for c in ("student_id", "phone"):
        long_df[c] = long_df[c].astype("string")
    long_df.to_csv(out_csv, index=False)
    print(f"\nWrote {len(long_df)} rows across {long_df['week'].nunique()} week(s) -> {out_csv}")
    return long_df


def main(argv: list[str]):
    here = Path(__file__).parent
    raw_dir = Path(argv[1]) if len(argv) > 1 else here / "raw_data"
    out_csv = Path(argv[2]) if len(argv) > 2 else here / "cleaned" / "long.csv"
    ingest_folder(raw_dir, out_csv)


if __name__ == "__main__":
    main(sys.argv)
