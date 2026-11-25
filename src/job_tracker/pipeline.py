"""The whole flow: URLs in, RunResult out.

  run(urls, ...)        fetch + parse each new job, then enrich it (known jobs are skipped)
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
    known_ids: set[str] | frozenset[str] = frozenset(),
) -> RunResult:
    """extractor: phase 4 (see llm.py). scorer: phase 5 (see scoring.py). None = skip.

    known_ids: jobs you already have (e.g. from jobs.json). They're skipped, so you can keep
    appending to urls.txt without re-downloading or re-paying the AI for old links.
    """
    targets, skipped = parse_urls(urls)
    result = RunResult(failed=[FailedJob(url=u, error="no LinkedIn job ID in URL") for u in skipped])
    result.known = [job_id for _, job_id in targets if job_id in known_ids]
    targets = [(url, job_id) for url, job_id in targets if job_id not in known_ids]
    if result.known:
        on_progress(f"{len(result.known)} job(s) already saved, skipped (use --refresh to fetch them again)")
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


def enrich(jobs: list[Job], extractor=None, scorer=None, on_progress: Progress = print,
           only_missing: bool = False) -> list[Job]:
    """Run the AI steps on jobs you already have (e.g. loaded from jobs.json).

    only_missing=True skips a step for a job that already has its result (insights or fit),
    so you only pay for jobs that need it.
    """
    todo = []
    for job in jobs:
        ext = None if only_missing and job.insights else extractor
        sco = None if only_missing and job.fit else scorer
        if ext is not None or sco is not None:
            todo.append((job, ext, sco))
    if only_missing and len(todo) < len(jobs):
        on_progress(f"{len(jobs) - len(todo)} job(s) already done, skipped (use --refresh to redo them)")
    for i, (job, ext, sco) in enumerate(todo, 1):
        on_progress(f"[{i}/{len(todo)}] {job.title} @ {job.company}")
        enrich_one(job, ext, sco, on_progress)
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
