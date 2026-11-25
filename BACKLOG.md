# Backlog: improvements and tech debt

Things we've agreed to do later. Newest first within each section.

## Improvements

| # | Item | Why | Added |
| --- | --- | --- | --- |
| 7 | **Tracing for LLM calls and agents** (e.g. Langfuse, LangSmith or OpenTelemetry): see every prompt, response, token count and cost per run | Agreed to add alongside the agents in phase 7. Until then: `ANTHROPIC_LOG=debug` before a command, or the Usage page on platform.claude.com | 2026-11-25 |
| 1 | **Skip jobs already in `jobs.json` by default**, with a `--refresh` flag to force a re-fetch | Lets you keep appending to `urls.txt`; today every run re-fetches (and re-pays AI for) every link in the file | 2026-11-25 |
| 2 | **Keep the Google Sheet's own column order**, adding only brand-new columns at the end | Today each run rewrites the sheet in the code's `COLUMNS` order, undoing columns you drag in Sheets | 2026-11-25 |
| 3 | **`--rescore` flag**: refresh AI suggestions in `fit_score` / `key_strengths` / `key_gaps` while keeping cells you typed yourself | Today those are your columns, so a re-run never updates them; makes the eval loop easier | 2026-11-25 |
| 4 | **Rubric tuning**: credit founder/CTO experience for founding or start-up roles; recalibrate (Claude scored ~4.5 below your gut, so all 4 came out "Maybe") | First eval: biggest gap was Founding Product Engineer (you 75, Claude 64) | 2026-11-25 |
| 5 | **More LLM providers**: Ollama (free, local) and OpenAI, behind `LLM_PROVIDER` | Compare cost and quality; OpenAI Agents SDK planned for phase 7 | 2026-11-25 |
| 6 | **Parse "6 days ago" into a real `posted_date`** | Sortable dates in the sheet | 2026-11-25 |

## Tech debt

| # | Item | Why | Added |
| --- | --- | --- | --- |
| 1 | Hunter Bond "Front-End (React) Engineer, up to £185,000" (job 4492348794) is missing from `jobs.json` | Its fetch likely failed in the phase 4 run, before `jobs.json` merged by ID. Re-add with a one-link file + `--ai --score --sheet` | 2026-11-25 |
| 2 | Refusal fallback not configured for Claude calls | A declined request just skips that job; the API's server-side fallback could retry on another model | 2026-11-25 |
| 3 | Selectors depend on LinkedIn's HTML | Will break when LinkedIn changes layout; tests use a hand-made sample page, not a saved real one | 2026-11-25 |
| 4 | Whole Sheet is cleared and rewritten each run | Fine for hundreds of rows; slow and quota-hungry for thousands. Move to a database (SQLite/Postgres) with the Sheet as a view in phase 7 | 2026-11-25 |
