"""The whole flow as ONE function: URLs in, RunResult out.

The CLI calls this today. In phase 7 a FastAPI endpoint will call the very same
function (and an agent can call it as a tool), so there's no logic to rewrite.
"""
from collections.abc import Callable

from .extract import parse_job
from .fetchers import FetchError, make_fetcher
from .models import FailedJob, RunResult
from .urls import canonical_url, parse_urls


def run(
    urls: list[str],
    fetcher_name: str | None = None,
    fetcher=None,
    extractor=None,
    on_progress: Callable[[str], None] = print,
) -> RunResult:
    """extractor: an LLM extractor (see llm.py). None = skip phase 4."""
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
            if extractor is not None:
                _enrich(job, extractor, on_progress)
            result.jobs.append(job)
        except Exception as e:  # noqa: BLE001 - one bad URL shouldn't stop the batch
            result.failed.append(FailedJob(url=url, error=str(e)))
            on_progress(f"  failed: {e}")
    return result


def _enrich(job, extractor, on_progress) -> None:
    """Phase 4. A failed LLM call never loses the job: it's saved without insights."""
    try:
        job.insights = extractor.extract(job)
    except Exception as e:  # noqa: BLE001
        on_progress(f"  AI extraction skipped: {e}")
        return
    if not job.salary and job.insights.salary_text:
        job.salary = job.insights.salary_text  # e.g. salary that was only in the title
    skills = ", ".join(job.insights.must_have_skills[:5])
    on_progress(f"  AI: {job.insights.remote_policy}, salary: {job.salary or '-'}, skills: {skills or '-'}")
