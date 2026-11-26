"""Phase 7d: find jobs by keywords on LinkedIn (backlog #11), instead of pasting URLs.

Uses LinkedIn's public "guest" search, the same thing you see logged out at
linkedin.com/jobs/search. Each page returns up to 10 job cards; we read the job ID,
title, company, location and date from each card, then hand the new ones to the
normal pipeline (fetch -> AI -> score) exactly as if you had pasted their URLs.

Politeness: one page = one request through the same fetcher (3-7 s pause, backoff on
429). Keep --limit small; LinkedIn blocks fast scrapers.
"""
import re

from bs4 import BeautifulSoup
from pydantic import BaseModel

from .urls import canonical_url

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
WORKPLACE = {"onsite": "1", "remote": "2", "hybrid": "3"}  # LinkedIn's f_WT codes
_URN = re.compile(r"jobPosting:(\d+)")


class SearchHit(BaseModel):
    job_id: str
    title: str = ""
    company: str = ""
    location: str = ""
    listed: str = ""   # date LinkedIn shows on the card, YYYY-MM-DD when available
    url: str = ""


def _text(card, selector: str) -> str:
    el = card.select_one(selector)
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def parse_search(html: str) -> list[SearchHit]:
    """Job cards on one results page. Cards without a job ID are skipped."""
    soup = BeautifulSoup(html, "html.parser")
    hits = []
    for card in soup.select("[data-entity-urn]"):
        m = _URN.search(card.get("data-entity-urn", ""))
        if not m:
            continue
        when = card.select_one("time")
        hits.append(SearchHit(
            job_id=m.group(1),
            title=_text(card, ".base-search-card__title"),
            company=_text(card, ".base-search-card__subtitle"),
            location=_text(card, ".job-search-card__location"),
            listed=(when.get("datetime") or when.get_text(strip=True)) if when else "",
            url=canonical_url(m.group(1)),
        ))
    return hits


def search_params(keywords: str, location: str = "", days: int | None = 7, workplace: str | None = None,
                  start: int = 0) -> dict:
    params = {"keywords": keywords, "location": location, "start": start}
    if days:
        params["f_TPR"] = f"r{days * 86400}"  # posted in the last N days, in seconds
    if workplace:
        codes = [WORKPLACE[w.strip().lower()] for w in workplace.split(",") if w.strip()]
        params["f_WT"] = ",".join(codes)
    return params


def search_jobs(fetcher, keywords: str, location: str = "", days: int | None = 7, limit: int = 20,
                workplace: str | None = None, on_progress=print) -> list[SearchHit]:
    """Page through results until `limit` unique jobs, or LinkedIn runs out."""
    if workplace:
        bad = [w for w in workplace.split(",") if w.strip().lower() not in WORKPLACE]
        if bad:
            raise ValueError(f"workplace must be onsite, remote or hybrid (got {', '.join(bad)})")
    found: dict[str, SearchHit] = {}
    start, page_no = 0, 0
    while len(found) < limit:
        page_no += 1
        html = fetcher.get(SEARCH_URL, search_params(keywords, location, days, workplace, start),
                           what=f"search page {page_no}")
        page = parse_search(html)
        new = [h for h in page if h.job_id not in found]
        for h in new:
            found[h.job_id] = h
        on_progress(f"  search '{keywords}' page {page_no}: {len(page)} jobs, {len(new)} new")
        if not page or not new:
            break  # no more results (or LinkedIn repeats the last page)
        start += len(page)
    return list(found.values())[:limit]


def read_search_file(path: str) -> list[dict]:
    """searches.txt: one search per line, `keywords | location | days | workplace`.
    Only keywords is required; # comments and blank lines are ignored. Example:
        senior react developer | London | 7 | hybrid,remote
    """
    searches = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")] + ["", "", ""]
            searches.append({"keywords": parts[0], "location": parts[1],
                             "days": int(parts[2]) if parts[2] else 7, "workplace": parts[3] or None})
    return searches
