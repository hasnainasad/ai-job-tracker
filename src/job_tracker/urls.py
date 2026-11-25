"""Phase 1: turn whatever LinkedIn job URL you paste into a clean job ID."""
import re
from urllib.parse import parse_qs, urlparse

_VIEW_RE = re.compile(r"/jobs/view/(?:[^/]*?-)?(\d{6,})")


def extract_job_id(url: str) -> str | None:
    """Return the numeric LinkedIn job ID in a URL, or None if there isn't one."""
    url = url.strip()
    if url.isdigit():
        return url
    parsed = urlparse(url)
    if "linkedin.com" not in parsed.netloc:
        return None
    query = parse_qs(parsed.query)
    if "currentJobId" in query:
        return query["currentJobId"][0]
    m = _VIEW_RE.search(parsed.path)
    return m.group(1) if m else None


def parse_urls(urls: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """De-duplicate URLs by job ID. Returns ([(url, job_id)], [skipped_urls])."""
    seen, targets, skipped = set(), [], []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        job_id = extract_job_id(url)
        if job_id is None:
            skipped.append(url)
        elif job_id not in seen:
            seen.add(job_id)
            targets.append((url, job_id))
    return targets, skipped


def read_url_file(path: str) -> list[str]:
    """Read a text file: one URL per line, blank lines and # comments ignored."""
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]


def canonical_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}/"
