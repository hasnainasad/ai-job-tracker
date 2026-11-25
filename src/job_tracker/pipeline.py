"""The whole flow: URLs in, RunResult out.

  run(urls, ...)        fetch + parse each job, then enrich it
  enrich(jobs, ...)     the AI steps only; also used on jobs loaded from jobs.json,
                        so you can score again without re-downloading from LinkedIn

The CLI calls these today. In phase 7 a FastAPI endpoint will call the very same
functions (and an agent can call them as tools), so there's no logic to rewrite.
"""
from collections.abc import Callable

from .extract import parse_job
from .fetchers import FetchError, make_fetcher
from .models import FailedJob, Job, RunResult
from .urls import canonical_url, parse_urls

Progress = Callable[[str], None]


def run(
    urls: list[str],
    fetcher_name: str | None = None,
    fetcher=None,
    extractor=None,
    scorer=None,
    on_progress: Progress = print,
) -> RunResult:
    """extractor: phase 4 (see llm.py). scorer: phase 5 (see scoring.py). None = skip."""
    targets, skipped = parse_urls(urls)
    result = RunResult(failed=[FailedJob(url=u, error="no LinkedIn job ID in URL") for u in skipped])
    fetcher = fetcher or make_fetcher(fetcher_name)  # pass your own to test or to plug in a new source

    on_progress(f"{len(targets)} job(s) to fetch")
    for i, (url, job_id) in enumerate(targets, 1):
        on_progress(f"[{i}/{len(targets)}] {job_id}")
        try:
            job = parse_job(fetcher.fetch(job_id), job_id, canonical_url(job_id))
            if not job.title:
                raise FetchError("page had no job title (expired job, or a login wall)")
            on_progress(f"  ok: {job.title} @ {job.company}")
        except Exception as e:  # noqa: BLE001 - one bad URL shouldn't stop the batch
            result.failed.append(FailedJob(url=url, error=str(e)))
            on_progress(f"  failed: {e}")
            continue
        enrich_one(job, extractor, scorer, on_progress)
        result.jobs.append(job)
    return result


def enrich(jobs: list[Job], extractor=None, scorer=None, on_progress: Progress = print) -> list[Job]:
    """Run the AI steps on jobs you already have (e.g. loaded from jobs.json)."""
    for i, job in enumerate(jobs, 1):
        on_progress(f"[{i}/{len(jobs)}] {job.title} @ {job.company}")
        enrich_one(job, extractor, scorer, on_progress)
    return jobs


def enrich_one(job: Job, extractor, scorer, on_progress: Progress) -> None:
    """A failed AI call never loses the job: it's saved without that step's columns."""
    if extractor is not None:
        try:
            job.insights = extractor.extract(job)
        except Exception as e:  # noqa: BLE001
            on_progress(f"  AI extraction skipped: {e}")
        else:
            if not job.salary and job.insights.salary_text:
                job.salary = job.insights.salary_text  # e.g. salary that was only in the title
            skills = ", ".join(job.insights.must_have_skills[:5])
            on_progress(f"  AI: {job.insights.remote_policy}, salary: {job.salary or '-'}, skills: {skills or '-'}")

    if scorer is not None:
        try:
            job.fit = scorer.score(job)
        except Exception as e:  # noqa: BLE001
            on_progress(f"  scoring skipped: {e}")
        else:
            # Suggestions for YOUR columns. storage.py only writes them where the cell is empty.
            job.fit_score = str(job.fit.fit_score)
            job.key_strengths = "; ".join(job.fit.key_strengths)
            job.key_gaps = "; ".join(job.fit.key_gaps)
            on_progress(
                f"  fit: {job.fit.fit_score} -> {job.fit.recommendation} "
                f"(template {job.fit.best_template}, tailoring benefit: {job.fit.tailoring_benefit})"
            )
