"""Phase 3: save jobs to a Google Sheet (or a CSV file if you have no Sheet yet).

Rows are keyed by job_id, so running the tool twice updates instead of duplicating,
and your own edits in the status/fit_score/notes columns are never overwritten.
"""
import csv
import os

from .models import Job

COLUMNS = [
    "job_id", "title", "company", "location", "posted", "applicants", "seniority",
    "employment_type", "job_function", "industries", "salary", "url", "status",
    "fit_score", "notes", "scraped_at", "description",
]
USER_COLUMNS = {"status", "fit_score", "notes"}  # yours: never overwritten
CELL_LIMIT = 49000  # Google Sheets max is 50,000 chars per cell


def _as_dict(job: Job) -> dict[str, str]:
    d = job.model_dump(mode="json")
    return {c: str(d.get(c, "") or "") for c in COLUMNS}


def _row(job: Job) -> list[str]:
    d = _as_dict(job)
    return [d[c][:CELL_LIMIT] for c in COLUMNS]


def save_to_sheet(jobs: list[Job], sheet_id: str, creds_file: str, tab: str = "Jobs"):
    import gspread  # imported here so CSV-only users don't need Google set up

    gc = gspread.service_account(filename=creds_file)
    sh = gc.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab, rows=1000, cols=len(COLUMNS))
    if ws.row_values(1) != COLUMNS:
        ws.update(range_name="A1", values=[COLUMNS])
        ws.freeze(rows=1)

    existing = {jid: i + 2 for i, jid in enumerate(ws.col_values(1)[1:])}
    new_rows, added, updated = [], 0, 0
    for job in jobs:
        row = _row(job)
        if job.job_id in existing:
            r = existing[job.job_id]
            current = ws.row_values(r)
            for idx, col in enumerate(COLUMNS):
                if col in USER_COLUMNS and idx < len(current) and current[idx]:
                    row[idx] = current[idx]
            ws.update(range_name=f"A{r}", values=[row])
            updated += 1
        else:
            new_rows.append(row)
            added += 1
    if new_rows:
        ws.append_rows(new_rows, value_input_option="RAW")
    print(f"Google Sheet: {added} added, {updated} updated -> {sh.url}")


def save_to_csv(jobs: list[Job], path: str):
    rows = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            rows = {r["job_id"]: r for r in csv.DictReader(f)}
    for job in jobs:
        old = rows.get(job.job_id, {})
        merged = _as_dict(job)
        for c in USER_COLUMNS:
            if old.get(c):
                merged[c] = old[c]
        rows[job.job_id] = merged
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows.values())
    print(f"CSV: {len(rows)} jobs in {path}")
