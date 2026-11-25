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
# Phase 5 (--score): the LLM's judgement of fit (fit_score/key_strengths/key_gaps are
# suggestions written into YOUR columns above, only where they're empty).
SCORE_COLUMNS = ["recommendation", "best_template", "tailoring_benefit", "tailoring_tips", "fit_reasoning",
                 "resume_file"]  # resume_file: phase 6 (--tailor)
# Phase 4 (--ai): read from the description by the LLM.
AI_COLUMNS = [
    "must_have_skills", "nice_to_have_skills", "visa_sponsorship", "ai_summary",
    "remote_policy", "office_days_per_week", "years_experience_min",
]
SCRAPED_COLUMNS = [
    "posted", "applicants", "seniority", "employment_type", "job_function",
    "industries", "url", "scraped_at", "description",
]
COLUMNS = ["job_id", "title", "company", *USER_COLUMNS, *SCORE_COLUMNS, *AI_COLUMNS, *SCRAPED_COLUMNS,"location", "salary",]
CELL_LIMIT = 49000  # Google Sheets max is 50,000 characters per cell


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _as_dict(job: Job) -> dict[str, str]:
    d = job.model_dump(mode="json", exclude={"insights", "fit"})
    if job.fit:
        d["recommendation"] = job.fit.recommendation
        d["best_template"] = job.fit.best_template
        d["tailoring_benefit"] = job.fit.tailoring_benefit
        d["tailoring_tips"] = "; ".join(job.fit.tailoring_tips)
        d["fit_reasoning"] = f"{job.fit.reasoning} Template: {job.fit.template_reason}"
    if job.insights:  # flatten the nested insights into plain columns
        ins = job.insights.model_dump()
        ins["ai_summary"] = ins.pop("summary")
        d.update({k: v for k, v in ins.items() if k in AI_COLUMNS})
    return {c: _cell(d.get(c))[:CELL_LIMIT] for c in COLUMNS}


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
        for c, v in list(fresh.items()):
            if not v and old.get(c):  # e.g. a run without --ai keeps earlier AI columns
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
