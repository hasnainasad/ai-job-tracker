# Job tracker

Paste LinkedIn job links, get a clean tracker of those jobs (JSON, CSV and a Google Sheet).
Later phases add LLM fit scoring, resume tailoring, and a React + FastAPI app with agents.

**Status:** phases 0 to 6 working live (fetch, Google Sheet, AI extraction, fit scoring, resume tailoring). Phase 7a (database + web API) built.

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
uv run pytest      # should say "29 passed"
```

**4. Add some jobs and run it.** Open `urls.txt`, paste 3 to 5 LinkedIn job links (one per
line), then:

```bash
uv run job-tracker urls.txt
```

Everything is saved in `tracker.db`, a small database file in this folder (git-ignored).
After each run you also get `jobs.json` (all fields) and `jobs.csv` (open it in Excel) as
exports. Run it again any time: jobs are matched by ID, so nothing duplicates. (If you
already had a `jobs.json` from before phase 7a, the first run copies it into the database.)

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

The sync works both ways: edits you make to your columns in the Sheet are read back into
the database first, then only the cells that changed are written. Your column order and
any columns you added are kept. To sync without fetching anything: `uv run job-tracker --sheet`.

**6. Let an LLM read the descriptions (phase 4, optional).** This fills extra columns:
remote policy, office days, years of experience, must-have and nice-to-have skills, visa
sponsorship, a one-line summary, and the salary when it's only in the job title.

1. Create an API key at [platform.claude.com](https://platform.claude.com) and add a few
   dollars of credit. (The API is billed separately from a Claude Pro/Max subscription.)
2. Put it in `.env` as `ANTHROPIC_API_KEY=...`.
3. Run with `--ai`:

```bash
uv run job-tracker urls.txt --sheet --ai
```

Cost: about $0.014 per job on `claude-opus-5-5` (the default), or about $0.0004 per job
if you set `LLM_MODEL=claude-haiku-5-5` in `.env`. Runs without `--ai` keep the AI columns
you already have.

**7. Score each job against your resume (phase 5, optional).**

1. Set up `profile/` (see `profile.example/README.md`): `master_resume.md`, `templates.md`
   and, optionally, `preferences.md`. It's in `.gitignore`, so it never reaches GitHub.
2. Score the jobs you already have, without touching LinkedIn:

```bash
uv run job-tracker --saved --score --sheet
```

   Or score while adding new jobs: `uv run job-tracker urls.txt --sheet --ai --score`.

**Adding more jobs later.** Keep appending links to `urls.txt` and run the same command.
Jobs already in `jobs.json` are skipped (no LinkedIn request, no AI cost), and
`--saved --ai --score` only runs the steps a saved job doesn't have yet. To redo jobs
you already have (for example after changing your master resume), add `--refresh`:

```bash
uv run job-tracker --saved --score --sheet --refresh    # re-score every saved job
```

This suggests `fit_score`, `key_strengths` and `key_gaps` (only where those cells are
empty, so clear a cell to get a fresh suggestion) and fills `recommendation`
(Apply / Maybe / Skip), `best_template` (A-E), `tailoring_benefit` (High / Medium / Low:
would a tailored resume help beyond that template?), `tailoring_tips` and `fit_reasoning`. Your resume is sent
with a prompt-cache marker, so after the first job it's read from cache at a fraction of
the price; the run ends by printing how many tokens came from cache. Cost: roughly 2 to 5
cents per job on `claude-opus-5-5`.

**8. Tailor your resume for a job (phase 6).** Pick a job from the sheet by its `job_id`:

```bash
uv run job-tracker --tailor 4492071444 --sheet
uv run job-tracker --tailor 4492071444 4487065297      # several at once
uv run job-tracker --tailor 4492071444 --template D    # force a template
```

It starts from the job's `best_template` (from scoring), applies the `tailoring_tips`,
and writes two files to `output/resumes/` (git-ignored):

- `Company_jobid_X.pdf`: the resume, A4, in your template style, kept to 2 pages
  (text shrinks slightly if needed).
- `Company_jobid_X.md`: **read this before sending.** A checklist of anything the
  guardrails flagged, what changed from the template and why, and the full text.

Guardrails against invented facts: every bullet must quote the master-resume line it's
based on (checked in Python), every number must appear in your master resume (checked
in Python), and a second, cheaper model (`VERIFIER_MODEL`, default `claude-haiku-5-5`)
fact-checks the whole resume. The sheet gets `resume_file`, and `resume_status` becomes
"Tailored" if it was empty. Cost: roughly 10 to 15 cents per resume. Needs
`profile/templates/A.md` ... `E.md` (full text of each template).

**9. Use the web API (phase 7a).** The same features, from your browser:

```bash
uv sync                     # once, installs FastAPI, Uvicorn and SQLModel
uv run job-tracker-api      # then open http://127.0.0.1:8000/docs
```

The `/docs` page lists every endpoint. Click one, then **Try it out**, then **Execute**:

| Endpoint | Does |
| --- | --- |
| `GET /jobs` | Your jobs, newest first. Filters: `status`, `recommendation`, `min_fit` |
| `GET /jobs/{job_id}` | One job with all AI output |
| `PATCH /jobs/{job_id}` | Edit your columns, e.g. `{"status": "Applied", "date_applied": "2026-11-26"}` |
| `POST /jobs` | Add URLs: `{"urls": [...], "ai": true, "score": true}` |
| `POST /jobs/{job_id}/score` | Score or re-score one job |
| `POST /jobs/{job_id}/tailor` | Tailored PDF resume (`?template=D` to force one) |
| `GET /jobs/{job_id}/resume` | Download the latest tailored PDF |
| `POST /sync/sheet` | Two-way Google Sheet sync |
| `GET /runs/{id}` | Progress of a background run |

Adding, scoring, tailoring and syncing take a while, so they reply at once with a *run*
(`"status": "queued"`). Call `GET /runs/{id}` to see its progress lines and result. The
server listens on your Mac only (127.0.0.1), so nothing is exposed to your network. The
CLI and the API can run at the same time; they share `tracker.db`.

---

## Level 2: How it works (intermediate)

Each stage hands a clean output to the next:

```
urls.txt ──► urls.py ──► fetchers.py ──► extract.py ──► models.Job ──► db.py ──► sheet_sync.py
            job IDs      raw HTML        parsed fields   validated     tracker.db  Google Sheet
```

The CLI (`cli.py`) and the web API (`api.py`) are two thin front doors to the same code.

| File | Job | Concept to learn |
| --- | --- | --- |
| `src/job_tracker/urls.py` | Pulls the job ID out of any LinkedIn URL shape, removes duplicates | Regular expressions, URL parsing |
| `src/job_tracker/fetchers.py` | Downloads the job page from LinkedIn's public guest endpoint, slowly, with retries | HTTP, rate limits (HTTP 429), back-off |
| `src/job_tracker/extract.py` | Reads title, company, location, seniority, description... out of the HTML | CSS selectors with BeautifulSoup |
| `src/job_tracker/models.py` | Defines what a `Job` is | Pydantic models (validation, JSON) |
| `src/job_tracker/storage.py` | The tracker columns and the CSV export; matches rows by `job_id` and columns by header name, never overwrites your columns | Idempotent writes |
| `src/job_tracker/db.py` | Phase 7a: the `tracker.db` database (jobs, runs, resumes) | SQL databases, SQLModel, upserts |
| `src/job_tracker/sheet_sync.py` | Phase 7a: two-way Google Sheet sync, only changed cells | Three-way merge, batch writes via `gspread` |
| `src/job_tracker/api.py` | Phase 7a: the web API | FastAPI, dependency injection, background tasks |
| `src/job_tracker/llm.py` | Phase 4: sends each description to Claude and gets back a validated `JobInsights` | Structured outputs, prompt rules against guessing, API error handling |
| `src/job_tracker/scoring.py` | Phase 5: scores each job against `profile/` with a written rubric | Rubric prompts, LLM-as-judge, prompt caching |
| `src/job_tracker/tailor.py` | Phase 6: writes a tailored resume, then fact-checks it | Generator + verifier, grounding with source quotes, deterministic checks |
| `src/job_tracker/render.py` | Phase 6: draws the resume as a PDF | Generating documents from code (ReportLab), fitting to a page limit |
| `src/job_tracker/pipeline.py` | `run(urls)` glues it all together and returns a `RunResult` | Separating logic from interface |
| `src/job_tracker/cli.py` | The `job-tracker` command | `argparse`, entry points |

**How the two-way Sheet sync decides who wins.** Each job remembers its *snapshot*: your
columns exactly as the last sync wrote them. If the Sheet differs from the snapshot, you
edited it there, so the Sheet wins (clearing a cell counts). If the Sheet still matches
the snapshot, the database's value wins (e.g. you changed it through the API). This is
called a three-way merge: base (snapshot), theirs (Sheet), ours (database).

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
- **New API endpoint:** add a function in `create_app()` in `api.py` that calls the
  package, plus a test in `tests/test_phase7a.py` using `TestClient` and the fake services.
- **Changing a table's columns:** `create_all` only creates missing tables. Delete
  `tracker.db` and re-import from `jobs.json`, or (better, later) add Alembic migrations.

---

## Level 4: Where this is heading (expert)

The package is deliberately UI-free, so each phase 7 layer is a thin front door to the
same functions (plan: the phase 7 plan doc):

```
CLI ─────────────┐
FastAPI (7a) ────┼──►  job_tracker package  ──►  tracker.db, Google Sheet, LinkedIn, Claude
MCP server (7c) ─┘
   ▲
Agents (7d, OpenAI Agents SDK) use the MCP tools; RAG (7e) adds search by meaning
```

Next: 7c exposes these functions as MCP tools for Claude Desktop, 7d adds agents and
tracing, 7e adds RAG, and 7b (later) a React + TypeScript UI generated from FastAPI's
OpenAPI schema.

---

## Project layout

```
job-tracker/
├── pyproject.toml        dependencies + the job-tracker command
├── .env.example          settings template (copy to .env)
├── urls.txt              your input list
├── tracker.db            the database (git-ignored; created on first run)
├── profile/              your resume + preferences (git-ignored; see profile.example/)
├── src/job_tracker/      the package (see Level 2)
└── tests/                pytest tests + a saved sample job page
```
