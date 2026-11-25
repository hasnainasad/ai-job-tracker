"""Phase 3: save jobs to a Google Sheet (or a CSV file if you have no Sheet yet).

Two rules make the tracker safe to re-run:
  1. Rows are matched by job_id, so running the tool twice updates instead of duplicating.
  2. Columns are matched by HEADER NAME, not position, and your USER_COLUMNS are never
     overwritten once they have a value. So you can add, remove or reorder columns and
     type in the tracker freely; the tool works around you.
"""
import csv
import os

from .models import Job

# Your tracker columns come first so they're visible without scrolling.
USER_COLUMNS = [
    "status", "status_note", "date_applied", "resume_status",
    "fit_score", "key_strengths", "key_gaps", "notes",
]
SCRAPED_COLUMNS = [
    "posted", "applicants", "seniority", "employment_type", "job_function",
    "industries", "salary", "url", "scraped_at", "description",
]
COLUMNS = ["job_id", "title", "company", "location", *USER_COLUMNS, *SCRAPED_COLUMNS]
CELL_LIMIT = 49000  # Google Sheets max is 50,000 characters per cell


def _as_dict(job: Job) -> dict[str, str]:
    d = job.model_dump(mode="json")
    return {c: str(d.get(c, "") or "")[:CELL_LIMIT] for c in COLUMNS}


def merge(existing: dict[str, dict[str, str]], jobs: list[Job], header: list[str]) -> list[dict]:
    """Combine what's already in the tracker with freshly scraped jobs.

    existing: {job_id: {column: value}} read from the CSV/Sheet (any column order).
    header:   the final column order. Columns you added yourself are kept as-is.
    """
    rows = {jid: dict(r) for jid, r in existing.items()}
    for job in jobs:
        fresh = _as_dict(job)
        old = rows.get(job.job_id, {})
        for c in USER_COLUMNS:
            if old.get(c):          # you've typed something: yours wins
                fresh[c] = old[c]
        rows[job.job_id] = {**old, **fresh}
    return [{c: r.get(c, "") for c in header} for r in rows.values()]


def _final_header(current: list[str]) -> list[str]:
    """Our columns in our order, plus any extra columns you added yourself (at the end)."""
    extra = [c for c in current if c and c not in COLUMNS]
    return COLUMNS + extra


def save_to_sheet(jobs: list[Job], sheet_id: str, creds_file: str, tab: str = "Jobs"):
    import gspread  # imported here so CSV-only users don't need Google set up

    gc = gspread.service_account(filename=creds_file)
    sh = gc.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab, rows=1000, cols=len(COLUMNS))

    values = ws.get_all_values()  # one read for the whole sheet
    current = values[0] if values else []
    existing = {}
    for raw in values[1:]:
        r = dict(zip(current, raw, strict=False))
        if r.get("job_id"):
            existing[r["job_id"]] = r
    before = set(existing)

    header = _final_header(current)
    rows = merge(existing, jobs, header)
    grid = [header] + [[r[c] for c in header] for r in rows]

    ws.clear()
    ws.update(range_name="A1", values=grid, value_input_option="RAW")  # one write
    ws.freeze(rows=1)
    added = len({j.job_id for j in jobs} - before)
    print(f"Google Sheet: {added} added, {len(jobs) - added} updated -> {sh.url}")


def save_to_csv(jobs: list[Job], path: str):
    existing, current = {}, []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            current = reader.fieldnames or []
            existing = {r["job_id"]: r for r in reader if r.get("job_id")}

    header = _final_header(current)
    rows = merge(existing, jobs, header)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    print(f"CSV: {len(rows)} jobs in {path}")
