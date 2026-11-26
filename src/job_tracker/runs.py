"""Background runs, shared by the web API (7a) and the MCP server (7c).

A "run" is a slow piece of work (fetching from LinkedIn, an LLM call, a Sheet sync)
recorded in the `runs` table, so whoever started it can check its progress later:

  row = create_run(session, "score", {...})     # status "queued"
  execute_run(engine, row.id, score_work(...))  # usually in a background thread
  -> status "running" -> "done" | "failed", with a progress log line by line

The *_work() functions describe WHAT to do; execute_run() handles the bookkeeping.
Both the API and the MCP server call these, so they can never behave differently.
"""
import os
from collections.abc import Callable
from dataclasses import dataclass

from sqlmodel import Session

from . import db
from .db import RunRow

Work = Callable[[Session, Callable[[str], None]], list[str]]  # (session, log) -> job IDs touched


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


# ---------------------------------------------------------------- what each run does

def add_work(svc: Services, urls: list[str], ai: bool = False, score: bool = False, refresh: bool = False,
             fetcher=None) -> Work:
    def work(s: Session, log) -> list[str]:
        from .pipeline import run

        result = run(
            urls,
            fetcher=fetcher or svc.fetcher(),
            extractor=svc.extractor() if ai else None,
            scorer=svc.scorer(svc.profile_dir) if score else None,
            on_progress=log,
            known_ids=set() if refresh else db.known_ids(s),
        )
        for f in result.failed:
            log(f"failed: {f.url}: {f.error}")
        db.upsert_jobs(s, result.jobs)
        return [j.job_id for j in result.jobs]

    return work


def score_work(svc: Services, job_id: str, ai: bool = False) -> Work:
    def work(s: Session, log) -> list[str]:
        from .pipeline import enrich

        job = db.get_job(s, job_id)
        enrich([job], svc.extractor() if ai else None, svc.scorer(svc.profile_dir), on_progress=log)
        if job.fit is None:
            raise RuntimeError("scoring failed; see the log above")
        db.upsert_jobs(s, [job])
        return [job_id]

    return work


def tailor_work(svc: Services, job_id: str, template: str | None = None, feedback: str | None = None) -> Work:
    def work(s: Session, log) -> list[str]:
        from .tailor import tailor_job

        job = db.get_job(s, job_id)
        result = tailor_job(job, svc.tailor(svc.profile_dir), out_dir=svc.resumes_dir,
                            template=template, on_progress=log, feedback=feedback)
        db.add_resume(s, job_id, result)
        db.upsert_jobs(s, [job])
        for w in result.warnings:
            log(f"check: {w}")
        return [job_id]

    return work


def search_work(svc: Services, keywords: str, location: str = "", days: int | None = 7, limit: int = 20,
                workplace: str | None = None, ai: bool = False, score: bool = False) -> Work:
    """Search LinkedIn, then add the jobs not saved yet (same as add_work with their URLs)."""
    def work(s: Session, log) -> list[str]:
        from .search import search_jobs

        fetcher = svc.fetcher()
        hits = search_jobs(fetcher, keywords, location, days, limit, workplace, on_progress=log)
        known = db.known_ids(s)
        new = [h.url for h in hits if h.job_id not in known]
        log(f"{len(hits)} found, {len(hits) - len(new)} already saved, {len(new)} to add")
        return add_work(svc, new, ai=ai, score=score, fetcher=fetcher)(s, log) if new else []

    return work


def sheet_work(svc: Services) -> Work:
    def work(s: Session, log) -> list[str]:
        from .sheet_sync import sync_sheet

        plan = sync_sheet(s, svc.worksheet(), on_progress=log)
        return list(plan.pulled)

    return work


# ---------------------------------------------------------------- bookkeeping

def create_run(s: Session, kind: str, params: dict) -> RunRow:
    row = RunRow(kind=kind, params=params)
    s.add(row)
    s.commit()
    s.refresh(row)
    return row


def execute_run(engine, run_id: int, work: Work) -> None:
    """Does the work, saving each progress line as it happens. Never raises."""
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
