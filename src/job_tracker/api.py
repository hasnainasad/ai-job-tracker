"""Phase 7a: the web API (FastAPI).

Run it:   uv run job-tracker-api        then open http://127.0.0.1:8000/docs

The /docs page lists every endpoint and lets you call them from the browser. Until the
React UI exists (7b), that page is your UI.

How it fits together:
  - Every endpoint is a small function that calls the same code the CLI uses
    (pipeline.run, tailor_job, sync_sheet...). Nothing is duplicated.
  - Slow work (LinkedIn fetches wait 3-7 s per job, LLM calls take seconds) runs as a
    background task: the endpoint replies at once with a run, and you check its progress
    at GET /runs/{id}.
  - Request and response bodies are Pydantic models, so FastAPI validates input and
    documents everything automatically.
"""
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from . import db
from .db import RunRow
from .models import Job, JobSummary
from .runs import (
    Services,
    add_work,
    create_run,
    execute_run,
    score_work,
    sheet_work,
    tailor_work,
)
from .storage import USER_COLUMNS

# ---------------------------------------------------------------- request / response shapes

class JobUpdate(BaseModel):
    """Your columns. Send only the ones you want to change."""
    status: str | None = None
    status_note: str | None = None
    date_applied: str | None = Field(default=None, examples=["2026-11-26"])
    resume_status: str | None = None
    fit_score: str | None = None
    key_strengths: str | None = None
    key_gaps: str | None = None
    notes: str | None = None


class AddJobs(BaseModel):
    urls: list[str] = Field(min_length=1, examples=[["https://www.linkedin.com/jobs/view/4484482518/"]])
    ai: bool = False
    score: bool = False
    refresh: bool = False


class Run(BaseModel):
    id: int
    kind: str
    status: str
    params: dict
    log: list[str]
    job_ids: list[str]
    error: str
    created_at: datetime
    finished_at: datetime | None


assert set(JobUpdate.model_fields) == set(USER_COLUMNS), "JobUpdate must list exactly your columns"

# ---------------------------------------------------------------- app


def get_session(request: Request):
    """A database session per request ("dependency injection": FastAPI calls this for you)."""
    with Session(request.app.state.engine) as s:
        yield s


SessionDep = Annotated[Session, Depends(get_session)]


def create_app(engine=None, services: Services | None = None) -> FastAPI:
    """Build the app. Tests pass their own database and fake services."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from dotenv import load_dotenv

        load_dotenv()  # .env: DATABASE_URL, API keys, GOOGLE_SHEET_ID
        app.state.engine = engine or db.make_engine()
        yield

    app = FastAPI(
        title="Job tracker",
        description="Your LinkedIn job tracker: jobs, fit scores, tailored resumes and Sheet sync.",
        version="0.7.0",
        lifespan=lifespan,
    )
    app.state.services = services or Services()

    def services_of(request: Request) -> Services:
        return request.app.state.services

    def start_run(request: Request, s: Session, background: BackgroundTasks, kind: str, params: dict,
                  work) -> Run:
        row = create_run(s, kind, params)
        background.add_task(execute_run, request.app.state.engine, row.id, work)
        return Run.model_validate(row, from_attributes=True)

    # ------------------------------------------------ jobs

    @app.get("/health", tags=["meta"])
    def health():
        return {"ok": True}

    @app.get("/jobs", response_model=list[JobSummary], tags=["jobs"])
    def list_jobs(s: SessionDep, status: str | None = None, recommendation: str | None = None,
                  min_fit: int | None = None,
                  limit: int = 100, offset: int = 0):
        """Saved jobs, newest first. Filter by your status, the AI's recommendation, or a minimum fit score."""
        jobs = db.list_jobs(s, status=status, recommendation=recommendation, min_fit=min_fit,
                            limit=limit, offset=offset)
        return [JobSummary.of(j) for j in jobs]

    @app.get("/jobs/{job_id}", response_model=Job, tags=["jobs"])
    def get_job(s: SessionDep, job_id: str):
        job = db.get_job(s, job_id)
        if job is None:
            raise HTTPException(404, f"job {job_id} not found")
        return job

    @app.patch("/jobs/{job_id}", response_model=Job, tags=["jobs"])
    def update_job(s: SessionDep, job_id: str, changes: JobUpdate):
        """Edit your columns. These always win over AI suggestions."""
        job = db.update_user_fields(s, job_id, changes.model_dump(exclude_none=True))
        if job is None:
            raise HTTPException(404, f"job {job_id} not found")
        return job

    @app.post("/jobs", response_model=Run, status_code=202, tags=["jobs"])
    def add_jobs(s: SessionDep, body: AddJobs, request: Request, background: BackgroundTasks):
        """Add LinkedIn job URLs. Starts a background run; check it at /runs/{id}."""
        work = add_work(services_of(request), body.urls, body.ai, body.score, body.refresh)
        return start_run(request, s, background, "add", body.model_dump(), work)

    @app.post("/jobs/{job_id}/score", response_model=Run, status_code=202, tags=["jobs"])
    def score_job(s: SessionDep, job_id: str, request: Request, background: BackgroundTasks, ai: bool = False):
        """Score (or re-score) one saved job. ai=true also re-reads the description first."""
        _require(s, job_id)
        work = score_work(services_of(request), job_id, ai)
        return start_run(request, s, background, "score", {"job_id": job_id, "ai": ai}, work)

    @app.post("/jobs/{job_id}/tailor", response_model=Run, status_code=202, tags=["resumes"])
    def tailor(s: SessionDep, job_id: str, request: Request, background: BackgroundTasks, template: str | None = None):
        """Write a tailored PDF resume for one saved job (uses API credit)."""
        _require(s, job_id)
        work = tailor_work(services_of(request), job_id, template)
        return start_run(request, s, background, "tailor", {"job_id": job_id, "template": template}, work)

    @app.get("/jobs/{job_id}/resume", tags=["resumes"], response_class=FileResponse)
    def download_resume(s: SessionDep, job_id: str):
        """The latest tailored PDF for this job."""
        row = db.latest_resume(s, job_id)
        if row is None or not Path(row.pdf_path).exists():
            raise HTTPException(404, f"no tailored resume for job {job_id} yet")
        return FileResponse(row.pdf_path, media_type="application/pdf", filename=Path(row.pdf_path).name)

    # ------------------------------------------------ sheet + runs

    @app.post("/sync/sheet", response_model=Run, status_code=202, tags=["sheet"])
    def sync(s: SessionDep, request: Request, background: BackgroundTasks):
        """Two-way Google Sheet sync: pull your edits, then push changed cells."""
        work = sheet_work(services_of(request))
        return start_run(request, s, background, "sheet", {}, work)

    @app.get("/runs", response_model=list[Run], tags=["runs"])
    def list_runs(s: SessionDep, limit: int = 20):
        rows = s.exec(select(RunRow).order_by(RunRow.id.desc()).limit(limit))
        return [Run.model_validate(r, from_attributes=True) for r in rows]

    @app.get("/runs/{run_id}", response_model=Run, tags=["runs"])
    def get_run(s: SessionDep, run_id: int):
        row = s.get(RunRow, run_id)
        if row is None:
            raise HTTPException(404, f"run {run_id} not found")
        return Run.model_validate(row, from_attributes=True)

    return app


def _require(s: Session, job_id: str) -> None:
    if s.get(db.JobRow, job_id) is None:
        raise HTTPException(404, f"job {job_id} not found")


app = create_app()


def main():
    """Start the server on your own machine only (127.0.0.1: not reachable from other devices)."""
    import uvicorn

    uvicorn.run("job_tracker.api:app", host="127.0.0.1", port=int(os.getenv("API_PORT", "8000")))
