# Backlog: improvements and tech debt

Things we've agreed to do later. Newest first within each section.

## Improvements

| # | Item | Why | Added |
| --- | --- | --- | --- |
| 8 | **Echo-trap rules + phrase-lift check** in tailoring: reuse the job ad's hard keywords (tools, skills, titles) exactly, never its informal "voice" words; plain-Python check flags 5+ word phrases copied from the ad | Recruiters spot their own sentences and read the CV as AI-generated. Idea from [ai-job-search](https://github.com/ai-job-search-dev/ai-job-search) (MIT) | 2026-11-25 |
| 9 | **ATS text check** on the tailored PDF (`pypdf`): contact details extract as text, sane reading order, job-ad keyword coverage | An ATS reads the PDF's text layer, not the page. Idea from [ai-job-search](https://github.com/ai-job-search-dev/ai-job-search) (MIT) | 2026-11-25 |
| 10 | **Posting guard**: strip hidden HTML, invisible/Unicode-tag characters and instruction-like lines from job descriptions before the LLM sees them; report what was found | Job ads are untrusted text sent to the LLM next to your resume (prompt injection). Idea from [ai-job-search](https://github.com/ai-job-search-dev/ai-job-search) (MIT) | 2026-11-25 |
| 11 | **`--search` discovery**: LinkedIn guest search by keywords + location + last N days, saved terms in `searches.txt`, feeding the existing pipeline | Find jobs instead of pasting URLs. Builds on skip-known-jobs. Like ai-job-search's `/scrape` | 2026-11-25 |
| 12 | **Cover letters** from the same facts, with the same source/number checks | Idea from [ai-job-search](https://github.com/ai-job-search-dev/ai-job-search) (MIT) | 2026-11-25 |
| 13 | **Eligibility gates in scoring**: right to work, language, location as pass/fail before the 0-100 scores | Pairs with #4 rubric tuning. Idea from [ai-job-search](https://github.com/ai-job-search-dev/ai-job-search) (MIT) | 2026-11-25 |
| 14 | **Application lifecycle**: archive exactly what was sent (PDF + posting text), follow-up reminder after ~10 quiet days, drafted follow-up / thank-you notes | Idea from [ai-job-search](https://github.com/ai-job-search-dev/ai-job-search) (MIT) (`/outcome`) | 2026-11-25 |
| 15 | **Phase 7 agents**: Gmail sync agent that *proposes* status changes from interview/rejection emails; reviewer agent that researches the company and critiques the tailored CV | Idea from [ai-job-search](https://github.com/ai-job-search-dev/ai-job-search) (MIT) (`/gmail-sync`, `/apply` reviewer) | 2026-11-25 |
| 16 | **Skill-gap summary + interview prep**: roll up `key_gaps` across all jobs into a learning plan; STAR-based prep for a scheduled interview | Idea from [ai-job-search](https://github.com/ai-job-search-dev/ai-job-search) (MIT) (`/upskill`, `/interview`) | 2026-11-25 |
| 7 | **Tracing for LLM calls and agents** (e.g. Langfuse, LangSmith or OpenTelemetry): see every prompt, response, token count and cost per run | Agreed to add alongside the agents in phase 7. Until then: `ANTHROPIC_LOG=debug` before a command, or the Usage page on platform.claude.com | 2026-11-25 |
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

## Done

| # | Item | Done |
| --- | --- | --- |
| 1 | Skip jobs already in `jobs.json` by default; `--from-json` only runs AI steps a job is missing; `--refresh` redoes everything | 2026-11-25 |
| 2 | Keep the Google Sheet's own column order (phase 7a two-way sync) | 2026-11-26 |
| TD4 | Sheet no longer cleared and rewritten: database is the source of truth, sync writes only changed cells (phase 7a) | 2026-11-26 |
