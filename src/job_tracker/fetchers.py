"""Phase 1: download the job page HTML.

Two interchangeable fetchers, both with the same .fetch(job_id) -> html method:

  DirectFetcher      Calls LinkedIn's public "guest" job endpoint yourself. Free, no
                     login, but LinkedIn rate-limits and blocks quickly. Use it to learn
                     with a handful of URLs, slowly.
  ScraperAPIFetcher  Sends the same request through ScraperAPI, which handles proxies,
                     retries and blocks for you. Paid (free tier available).

Adding another provider (Apify, Bright Data, ScrapingBee...) = one more small class.
"""
import os
import random
import time

import requests

GUEST_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class FetchError(Exception):
    pass


class DirectFetcher:
    def __init__(self, min_delay: float = 3.0, max_delay: float = 7.0, retries: int = 2):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.min_delay, self.max_delay, self.retries = min_delay, max_delay, retries
        self._first = True

    def _polite_pause(self):
        # Space requests out like a human would. Hammering = instant 429s.
        if not self._first:
            time.sleep(random.uniform(self.min_delay, self.max_delay))
        self._first = False

    def fetch(self, job_id: str) -> str:
        return self.get(GUEST_URL.format(job_id=job_id), what=f"job {job_id}")

    def get(self, url: str, params: dict | None = None, what: str = "page") -> str:
        """Any public LinkedIn guest page, politely (used by fetch() and by search.py)."""
        for attempt in range(self.retries + 1):
            self._polite_pause()
            r = self.session.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r.text
            if r.status_code == 429 and attempt < self.retries:
                wait = 30 * (attempt + 1)
                print(f"  rate-limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            raise FetchError(f"HTTP {r.status_code} for {what}")
        raise FetchError(f"gave up on {what}")


class ScraperAPIFetcher:
    API = "https://api.scraperapi.com/"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("SCRAPERAPI_KEY is empty. Put it in your .env file.")
        self.api_key = api_key

    def fetch(self, job_id: str) -> str:
        return self.get(GUEST_URL.format(job_id=job_id), what=f"job {job_id}")

    def get(self, url: str, params: dict | None = None, what: str = "page") -> str:
        target = requests.Request("GET", url, params=params).prepare().url
        r = requests.get(self.API, params={"api_key": self.api_key, "url": target}, timeout=70)
        if r.status_code != 200:
            raise FetchError(f"ScraperAPI HTTP {r.status_code} for {what}")
        return r.text


def make_fetcher(name: str | None = None):
    name = (name or os.getenv("FETCHER", "direct")).lower()
    if name == "direct":
        return DirectFetcher()
    if name == "scraperapi":
        return ScraperAPIFetcher(os.getenv("SCRAPERAPI_KEY", ""))
    raise ValueError(f"Unknown fetcher '{name}'. Use 'direct' or 'scraperapi'.")
