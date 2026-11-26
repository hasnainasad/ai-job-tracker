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
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from . import db
from .db import RunRow
from .models import Job
from .storage import USER_COLUMNS

# ---------------------------------------------------------------- services
# The things that talk to the outside world, behind small factories. Tests swap them
# for fakes, so they run without LinkedIn, Claude or Google.


def _fetcher():
    from .fetchers import make_fetcher

    return make_fetcher(None)


def _extractor():
    from .llm import make_extractor

    return make_extractor()


def _scorer(profile_dir: str):
    from .scoring import make_scorer

    return make_scorer(profile_dir)


def _tailor(profile_dir: str):
    from .tailor import make_tailor

    return make_tailor(profile_dir)


def _worksheet():
    from .sheet_sync import open_worksheet

    return open_worksheet(
        os.environ["GOOGLE_SHEET_ID"], os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    )


@dataclass
class Services:
    fetcher: Callable = _fetcher
    extractor: Callable = _extractor
    scorer: Callable = _scorer
    tailor: Callable = _tailor
    worksheet: Callable = _worksheet
    profile_dir: str = "profile"
    resumes_dir: str = "output/resumes"


# ---------------------------------------------------------------- request / response shapes

class JobSummary(BaseModel):
    """The short form used in lists (no description, no full AI output)."""
    job_id: str
    title: str
    company: str
    location: str
    salary: str
    status: str
    fit_score: str
    recommendation: str = ""
    best_template: str = ""
    resume_status: str
    date_applied: str
    url: str

    @classmethod
    def of(cls, job: Job) -> "JobSummary":
        fit = job.fit
        return cls(**job.model_dump(include=set(cls.model_fields)),
                   recommendation=fit.recommendation if fit else "", best_template=fit.best_template if fit else "")


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
                  work: Callable) -> Run:
        row = RunRow(kind=kind, params=params)
        s.add(row)
        s.commit()
        s.refresh(row)
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
        svc = services_of(request)

        def work(ws: Session, log) -> list[str]:
            from .pipeline import run

            result = run(
                body.urls,
                fetcher=svc.fetcher(),
                extractor=svc.extractor() if body.ai else None,
                scorer=svc.scorer(svc.profile_dir) if body.score else None,
                on_progress=log,
                known_ids=set() if body.refresh else db.known_ids(ws),
            )
            for f in result.failed:
                log(f"failed: {f.url}: {f.error}")
            db.upsert_jobs(ws, result.jobs)
            return [j.job_id for j in result.jobs]

        return start_run(request, s, background, "add", body.model_dump(), work)

    @app.post("/jobs/{job_id}/score", response_model=Run, status_code=202, tags=["jobs"])
    def score_job(s: SessionDep, job_id: str, request: Request, background: BackgroundTasks, ai: bool = False):
        """Score (or re-score) one saved job. ai=true also re-reads the description first."""
        _require(s, job_id)
        svc = services_of(request)

        def work(ws: Session, log) -> list[str]:
            from .pipeline import enrich

            job = db.get_job(ws, job_id)
            enrich([job], svc.extractor() if ai else None, svc.scorer(svc.profile_dir), on_progress=log)
            db.upsert_jobs(ws, [job])
            return [job_id]

        return start_run(request, s, background, "score", {"job_id": job_id, "ai": ai}, work)

    @app.post("/jobs/{job_id}/tailor", response_model=Run, status_code=202, tags=["resumes"])
    def tailor(s: SessionDep, job_id: str, request: Request, background: BackgroundTasks, template: str | None = None):
        """Write a tailored PDF resume for one saved job (uses API credit)."""
        _require(s, job_id)
        svc = services_of(request)

        def work(ws: Session, log) -> list[str]:
            from .tailor import tailor_job

            job = db.get_job(ws, job_id)
            result = tailor_job(job, svc.tailor(svc.profile_dir), out_dir=svc.resumes_dir,
                                template=template, on_progress=log)
            db.add_resume(ws, job_id, result)
            db.upsert_jobs(ws, [job])
            for w in result.warnings:
                log(f"check: {w}")
            return [job_id]

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
        svc = services_of(request)

        def work(ws: Session, log) -> list[str]:
            from .sheet_sync import sync_sheet

            plan = sync_sheet(ws, svc.worksheet(), on_progress=log)
            return list(plan.pulled)

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


def execute_run(engine, run_id: int, work: Callable) -> None:
    """Runs in the background: does the work, saving each progress line as it happens."""
    with Session(engine) as s:
        row = s.get(RunRow, run_id)
        row.status = "running"
        s.commit()

        def log(line: str) -> None:
            row.log = [*row.log, line.strip()]  # a new list, so the JSON column notices the change
            s.commit()

        try:
            row.job_ids = work(s, log) or []
            row.status = "done"
        except Exception as e:  # noqa: BLE001 - the run records the error instead of crashing the server
            s.rollback()
            row.status = "failed"
            row.error = str(e)
        row.finished_at = db.now()
        s.commit()


app = create_app()


def main():
    """Start the server on your own machine only (127.0.0.1: not reachable from other devices)."""
    import uvicorn

    uvicorn.run("job_tracker.api:app", host="127.0.0.1", port=int(os.getenv("API_PORT", "8000")))
