"""Phase 7a: two-way sync between the database and your Google Sheet.

Until the React UI exists, the Sheet is where you edit your columns (status, notes...),
so the database has to learn about those edits before it writes anything back.
Each sync is two steps:

  1. PULL  your columns from the Sheet into the database, if you changed them there.
  2. PUSH  everything else back, cell by cell, only where the Sheet differs.

How do we know you "changed" a cell? Each job remembers its `sheet_snapshot`: your
columns exactly as the last sync wrote them. If the Sheet now differs from that
snapshot, you edited it in the Sheet, and the Sheet wins (clearing a cell counts too).
If it still matches, any newer value in the database (e.g. set through the API) wins.
This is a "three-way merge": base (snapshot), theirs (Sheet), ours (database).

Other rules:
  - The Sheet's own column order is kept; new columns are added at the end (backlog #2).
  - Columns you added yourself, and rows the database doesn't know, are never touched.
  - Only changed cells are written, in one request (no more clear-and-rewrite).
"""
from dataclasses import dataclass, field

from .models import Job
from .storage import COLUMNS, USER_COLUMNS, _as_dict


@dataclass
class SyncPlan:
    header: list[str]
    pulled: dict[str, dict[str, str]] = field(default_factory=dict)   # job_id -> your edits found in the Sheet
    cell_updates: list[tuple[int, int, str]] = field(default_factory=list)  # (row, col), 1-based like Sheets
    new_rows: list[list[str]] = field(default_factory=list)
    snapshots: dict[str, dict[str, str]] = field(default_factory=dict)  # job_id -> your columns after the sync


def plan_sheet_sync(grid: list[list[str]], jobs: list[Job],
                    snapshots: dict[str, dict | None]) -> SyncPlan:
    """Pure function: what to pull and what to write. No network, so it's easy to test.

    grid: the Sheet's values (row 0 = header). snapshots: job_id -> last sheet_snapshot.
    """
    current = list(grid[0]) if grid else []
    header = current + [c for c in COLUMNS if c not in current]
    plan = SyncPlan(header=header)

    # Header cells that are new or missing.
    for j, name in enumerate(header, 1):
        if j > len(current) or current[j - 1] != name:
            plan.cell_updates.append((1, j, name))

    sheet_rows: dict[str, tuple[int, dict[str, str]]] = {}
    for i, raw in enumerate(grid[1:], 2):
        row = dict(zip(current, raw, strict=False))
        if row.get("job_id"):
            sheet_rows[row["job_id"]] = (i, row)

    for job in jobs:
        desired = _as_dict(job)
        if job.job_id in sheet_rows:
            row_num, row = sheet_rows[job.job_id]
            snap = snapshots.get(job.job_id)
            edits = {}
            for c in USER_COLUMNS:
                if c not in row:
                    continue
                in_sheet = row[c]
                base = desired[c] if snap is None else snap.get(c, "")
                # No snapshot yet (first sync): the Sheet is the truth for your columns.
                if (snap is None and in_sheet != desired[c]) or (snap is not None and in_sheet != base):
                    edits[c] = in_sheet
            if edits:
                plan.pulled[job.job_id] = edits
                desired.update(edits)
            for j, name in enumerate(header, 1):
                if name in COLUMNS and row.get(name, "") != desired[name]:
                    plan.cell_updates.append((row_num, j, desired[name]))
        else:
            plan.new_rows.append([desired.get(name, "") for name in header])
        plan.snapshots[job.job_id] = {c: desired[c] for c in USER_COLUMNS}
    return plan


def open_worksheet(sheet_id: str, creds_file: str, tab: str = "Jobs"):
    import gspread  # imported here so CSV-only users don't need Google set up

    gc = gspread.service_account(filename=creds_file)
    sh = gc.open_by_key(sheet_id)
    try:
        return sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=tab, rows=1000, cols=len(COLUMNS))


def apply_sync(ws, plan: SyncPlan, was_empty: bool) -> None:
    """Write a plan to a gspread worksheet: one batch of cell updates + one append."""
    from gspread.utils import rowcol_to_a1

    if ws.col_count < len(plan.header):
        ws.add_cols(len(plan.header) - ws.col_count)
    if plan.cell_updates:
        ws.batch_update(
            [{"range": rowcol_to_a1(r, c), "values": [[v]]} for r, c, v in plan.cell_updates],
            value_input_option="RAW",
        )
    if plan.new_rows:
        ws.append_rows(plan.new_rows, value_input_option="RAW", table_range="A1")
    if was_empty:
        ws.freeze(rows=1)


def sync_sheet(session, ws, on_progress=print) -> SyncPlan:
    """Pull your edits into the database, then push the database to the Sheet."""
    from sqlmodel import select

    from .db import JobRow, load_all, update_user_fields

    grid = ws.get_all_values()
    jobs = load_all(session)
    snapshots = {r.job_id: r.sheet_snapshot for r in session.exec(select(JobRow))}
    plan = plan_sheet_sync(grid, jobs, snapshots)

    for job_id, edits in plan.pulled.items():
        update_user_fields(session, job_id, edits)
    apply_sync(ws, plan, was_empty=not grid)
    for job_id, snap in plan.snapshots.items():
        session.get(JobRow, job_id).sheet_snapshot = snap
    session.commit()

    changed_cells = sum(1 for r, _, _ in plan.cell_updates if r > 1)
    on_progress(
        f"Google Sheet: pulled your edits for {len(plan.pulled)} job(s), "
        f"{len(plan.new_rows)} row(s) added, {changed_cells} cell(s) updated"
    )
    return plan
