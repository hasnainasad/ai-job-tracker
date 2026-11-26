"""Phase 7a: the database. One SQLite file (tracker.db) is now the single source of truth.

Why a database instead of jobs.json?
  - The CLI, the API and (in 7c) the MCP server can all read and write it at the same
    time without overwriting each other. Two programs saving one JSON file can't do that.
  - You can ask it questions ("all Apply jobs without a tailored resume") without
    loading everything.

SQLModel = Pydantic models that are also database tables (made by FastAPI's author).
Each table below is a class; each row is an instance. jobs.json and jobs.csv are still
written after each run, but only as exports you can open or back up.

Three tables:
  jobs     one row per job (your Job model, with insights/fit stored as JSON)
  runs     one row per background task started from the API (progress log, status)
  resumes  one row per tailored PDF
"""
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import JSON, Column, event
from sqlmodel import Field, Session, SQLModel, create_engine, select

from .models import Job, load_job
from .storage import USER_COLUMNS


def now() -> datetime:
    return datetime.now(UTC)


class JobRow(SQLModel, table=True):
    """The `jobs` table. The fields mirror models.Job (a test checks they stay in sync)."""
    __tablename__ = "jobs"

    job_id: str = Field(primary_key=True)
    url: str
    title: str = ""
    company: str = ""
    location: str = ""
    posted: str = ""
    applicants: str = ""
    seniority: str = ""
    employment_type: str = ""
    job_function: str = ""
    industries: str = ""
    salary: str = ""
    description: str = ""
    scraped_at: datetime = Field(default_factory=now)
    # AI output is stored as JSON, so changing JobInsights/FitAssessment never needs a
    # table change; load_job() drops stale shapes, exactly like it did for jobs.json.
    insights: dict | None = Field(default=None, sa_column=Column(JSON))
    fit: dict | None = Field(default=None, sa_column=Column(JSON))
    # Your columns
    status: str = "To review"
    status_note: str = ""
    date_applied: str = ""
    resume_status: str = ""
    fit_score: str = ""
    key_strengths: str = ""
    key_gaps: str = ""
    notes: str = ""
    resume_file: str = ""
    # Bookkeeping (not part of Job)
    sheet_snapshot: dict | None = Field(default=None, sa_column=Column(JSON))  # your columns as last written to the Sheet
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class RunRow(SQLModel, table=True):
    """The `runs` table: one row per background task (add jobs, score, tailor, sheet sync)."""
    __tablename__ = "runs"

    id: int | None = Field(default=None, primary_key=True)
    kind: str                      # add | score | tailor | sheet
    status: str = "queued"         # queued -> running -> done | failed
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    log: list = Field(default_factory=list, sa_column=Column(JSON))       # progress lines, as the CLI prints them
    job_ids: list = Field(default_factory=list, sa_column=Column(JSON))   # jobs this run touched
    error: str = ""
    created_at: datetime = Field(default_factory=now)
    finished_at: datetime | None = None


class ResumeRow(SQLModel, table=True):
    """The `resumes` table: one row per tailored resume."""
    __tablename__ = "resumes"

    id: int | None = Field(default=None, primary_key=True)
    job_id: str = Field(index=True)
    template: str = ""
    pdf_path: str
    report_path: str
    pages: int = 0
    warnings: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now)


# ---------------------------------------------------------------- connecting

def db_url(path: str | None = None) -> str:
    """--db path wins, then DATABASE_URL in .env, then tracker.db in the current folder."""
    if path:
        return path if "://" in path else f"sqlite:///{path}"
    return os.getenv("DATABASE_URL") or "sqlite:///tracker.db"


def make_engine(url: str | None = None):
    """An engine = the connection settings. Creates any missing tables.

    (create_all only adds missing tables; it never changes existing ones. If we ever
    change a table's columns, we'll add Alembic, the standard migration tool.)
    """
    url = url or db_url()
    sqlite = url.startswith("sqlite")
    # check_same_thread=False: the API's background tasks use the DB from another thread.
    engine = create_engine(url, connect_args={"check_same_thread": False} if sqlite else {})
    if sqlite:
        @event.listens_for(engine, "connect")
        def _sqlite_settings(dbapi_connection, _record):
            # WAL lets readers (the API) keep working while a writer (a CLI run) saves.
            dbapi_connection.execute("PRAGMA journal_mode=WAL")
            dbapi_connection.execute("PRAGMA busy_timeout=5000")
    SQLModel.metadata.create_all(engine)
    return engine


# ---------------------------------------------------------------- jobs

JOB_FIELDS = list(Job.model_fields)


def to_job(row: JobRow) -> Job:
    data = {f: getattr(row, f) for f in JOB_FIELDS}
    if data["scraped_at"].tzinfo is None:  # SQLite stores times without a time zone; ours are UTC
        data["scraped_at"] = data["scraped_at"].replace(tzinfo=UTC)
    return load_job(data)


def _values(job: Job) -> dict:
    d = job.model_dump(exclude={"insights", "fit"})
    d["insights"] = job.insights.model_dump(mode="json") if job.insights else None
    d["fit"] = job.fit.model_dump(mode="json") if job.fit else None
    return d


def upsert_jobs(session: Session, jobs: list[Job]) -> tuple[int, int]:
    """Insert new jobs, update known ones. Returns (added, updated).

    Same rules as the Sheet always had:
      - your columns are never overwritten once they have a value (AI suggestions only
        fill empty ones);
      - an empty fresh value never wipes a saved one (a run without --ai keeps insights).
    Edits you make on purpose go through update_user_fields() instead.
    """
    added = updated = 0
    for job in jobs:
        fresh = _values(job)
        row = session.get(JobRow, job.job_id)
        if row is None:
            session.add(JobRow(**fresh))
            added += 1
            continue
        for key, value in fresh.items():
            old = getattr(row, key)
            if key in USER_COLUMNS and old:
                continue
            if not value and old:
                continue
            setattr(row, key, value)
        row.updated_at = now()
        updated += 1
    session.commit()
    return added, updated


def update_user_fields(session: Session, job_id: str, changes: dict[str, str]) -> Job | None:
    """Set your columns (from the API, or edits pulled from the Sheet). Other keys are ignored."""
    row = session.get(JobRow, job_id)
    if row is None:
        return None
    for key, value in changes.items():
        if key in USER_COLUMNS:
            setattr(row, key, value)
    row.updated_at = now()
    session.commit()
    return to_job(row)


def get_job(session: Session, job_id: str) -> Job | None:
    row = session.get(JobRow, job_id)
    return to_job(row) if row else None


def load_all(session: Session) -> list[Job]:
    return [to_job(r) for r in session.exec(select(JobRow).order_by(JobRow.created_at))]


def known_ids(session: Session) -> set[str]:
    return set(session.exec(select(JobRow.job_id)))


def list_jobs(session: Session, status: str | None = None, recommendation: str | None = None,
              min_fit: int | None = None, limit: int = 100, offset: int = 0) -> list[Job]:
    """Filtered list, newest first.

    Filtering happens in Python: with hundreds of jobs that's instant and keeps this
    readable. With tens of thousands we'd move the filters into the SQL query.
    """
    jobs = [to_job(r) for r in session.exec(select(JobRow).order_by(JobRow.scraped_at.desc()))]
    if status:
        jobs = [j for j in jobs if j.status.lower() == status.lower()]
    if recommendation:
        jobs = [j for j in jobs if j.fit and j.fit.recommendation.lower() == recommendation.lower()]
    if min_fit is not None:
        jobs = [j for j in jobs if j.fit_score.isdigit() and int(j.fit_score) >= min_fit]
    return jobs[offset:offset + limit]


def import_json(session: Session, path: str) -> int:
    """One-time move from jobs.json into the database. Safe to repeat (same merge rules)."""
    p = Path(path)
    if not p.exists():
        return 0
    jobs = [load_job(d) for d in json.loads(p.read_text(encoding="utf-8"))]
    upsert_jobs(session, jobs)
    return len(jobs)


# ---------------------------------------------------------------- resumes

def add_resume(session: Session, job_id: str, result) -> ResumeRow:
    row = ResumeRow(job_id=job_id, template=result.template, pdf_path=result.pdf_path,
                    report_path=result.report_path, pages=result.pages, warnings=list(result.warnings))
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def latest_resume(session: Session, job_id: str) -> ResumeRow | None:
    query = select(ResumeRow).where(ResumeRow.job_id == job_id).order_by(ResumeRow.id.desc())
    return session.exec(query).first()
