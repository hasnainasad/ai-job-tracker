# Job tracker

Paste LinkedIn job links, get a clean tracker of those jobs (JSON, CSV and a Google Sheet).
Later phases add LLM fit scoring, resume tailoring, and a React + FastAPI app with agents.

**Status:** phases 0 to 3 built and working against live LinkedIn (first run: 5 of 5 jobs).

---

## Level 1: Get it running (novice)

**1. Install `uv`** (fast Python + package manager, the course uses it too):

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**2. Install the project** (from this folder):

```bash
uv sync            # creates .venv and installs everything in pyproject.toml
cp .env.example .env
```

**3. Check it works:**

```bash
uv run pytest      # should say "7 passed"
```

**4. Add some jobs and run it.** Open `urls.txt`, paste 3 to 5 LinkedIn job links (one per
line), then:

```bash
uv run job-tracker urls.txt
```

You get `jobs.json` (all fields) and `jobs.csv` (open it in Excel). Run it again any time:
jobs are matched by ID, so nothing duplicates.

**Your tracker columns.** These are yours to fill in, in the CSV or Google Sheet (and in
the web UI later). Once a cell has a value the tool never overwrites it:

| Column | What to put in it |
| --- | --- |
| `status` | To review (default), Applying, Applied, Interview, Offer, Rejected |
| `status_note` | Short update, e.g. "Recruiter call Fri 2pm" |
| `date_applied` | `YYYY-MM-DD`, e.g. 2026-11-25 |
| `resume_status` | Not started, Tailored, Sent |
| `fit_score` | 0 to 100 (phase 5 will suggest one) |
| `key_strengths` | Where you match the job (phase 5 will suggest these) |
| `key_gaps` | What the job wants that you lack (phase 5 will suggest these) |
| `notes` | Anything else |

You can also add your own columns or reorder them; the tool matches columns by header
name and keeps anything it doesn't recognise at the end.

**5. Send it to Google Sheets** (one-time setup, about 10 minutes):

1. Go to [console.cloud.google.com](https://console.cloud.google.com), create a project
   (e.g. "job-tracker").
2. *APIs & Services → Library*: enable **Google Sheets API** and **Google Drive API**.
3. *APIs & Services → Credentials → Create credentials → Service account*. Name it,
   skip the optional steps.
4. Open the service account → *Keys → Add key → JSON*. Save the file in this folder as
   `service_account.json` (it's in `.gitignore`, never commit it).
5. Create an empty Google Sheet. Click **Share** and share it (Editor) with the service
   account's email (looks like `name@project.iam.gserviceaccount.com`).
6. Copy the sheet ID from its URL (`docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`)
   into `.env` as `GOOGLE_SHEET_ID=...`.

```bash
uv run job-tracker urls.txt --sheet
```

A `Jobs` tab appears with a header row and one row per job.

---

## Level 2: How it works (intermediate)

Each stage hands a clean output to the next:

```
urls.txt ──► urls.py ──► fetchers.py ──► extract.py ──► models.Job ──► storage.py
            job IDs      raw HTML        parsed fields   validated     CSV / Sheet
```

| File | Job | Concept to learn |
| --- | --- | --- |
| `src/job_tracker/urls.py` | Pulls the job ID out of any LinkedIn URL shape, removes duplicates | Regular expressions, URL parsing |
| `src/job_tracker/fetchers.py` | Downloads the job page from LinkedIn's public guest endpoint, slowly, with retries | HTTP, rate limits (HTTP 429), back-off |
| `src/job_tracker/extract.py` | Reads title, company, location, seniority, description... out of the HTML | CSS selectors with BeautifulSoup |
| `src/job_tracker/models.py` | Defines what a `Job` is | Pydantic models (validation, JSON) |
| `src/job_tracker/storage.py` | Writes to CSV and Google Sheets; matches rows by `job_id` and columns by header name, never overwrites your columns | Idempotent writes, Sheets API via `gspread` |
| `src/job_tracker/pipeline.py` | `run(urls)` glues it all together and returns a `RunResult` | Separating logic from interface |
| `src/job_tracker/cli.py` | The `job-tracker` command | `argparse`, entry points |

**Why the LinkedIn fetch is careful:** LinkedIn's terms forbid scraping and it blocks bots.
This tool only reads *public, logged-out* job pages, waits 3 to 7 seconds between
requests, and backs off when rate-limited. Never point it at your logged-in account.
Keep batches to a few dozen jobs a day. If a page fails, it's listed at the end and the
rest still save.

**When it gets blocked often**, switch to a scraper API (they handle proxies for you):
sign up at ScraperAPI, put the key in `.env`, and set `FETCHER=scraperapi`. Nothing else
changes.

---

## Level 3: Extending it (advanced)

- **New data source** (another scraper API, Apify, Indeed, a company's Greenhouse/Lever
  jobs API): write a class with a `fetch(job_id) -> html` method in `fetchers.py` and
  register it in `make_fetcher()`. Different HTML means a matching parser in `extract.py`.
- **LinkedIn changed its layout?** The run will report "page had no job title". Save one
  real page as a new test fixture, fix the selectors in `extract.py`, re-run `uv run pytest`.
- **Phase 4 (next):** LLM extraction of fields the HTML doesn't label: must-have skills,
  years required, remote policy, salary. It becomes a new Pydantic model filled via
  structured output, added to `Job`.

---

## Level 4: Where this is heading (expert)

The package is deliberately UI-free. `job_tracker.run()` takes plain data and returns a
Pydantic `RunResult`, so the phase 7 stack sits on top without rewriting anything:

```
React + TypeScript (Vite)  ──HTTP──►  FastAPI  ──►  job_tracker package  ──►  Sheets / DB
                                         │
                                         └──► agents (scoring, tailoring) calling the
                                              same functions as tools
```

A first FastAPI endpoint is about five lines:

```python
from fastapi import FastAPI
from job_tracker import run, RunResult

app = FastAPI()

@app.post("/jobs/import")
def import_jobs(urls: list[str]) -> RunResult:
    return run(urls, on_progress=lambda _: None)
```

Things that will matter then: running fetches as background jobs (the polite delays
make a request slow), a real database (SQLite → Postgres) with the Sheet as a view,
streaming progress to the UI, and types shared with the frontend (FastAPI's OpenAPI
schema → generated TypeScript client).

---

## Project layout

```
job-tracker/
├── pyproject.toml        dependencies + the job-tracker command
├── .env.example          settings template (copy to .env)
├── urls.txt              your input list
├── src/job_tracker/      the package (see Level 2)
└── tests/                pytest tests + a saved sample job page
```
