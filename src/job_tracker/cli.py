"""Command line entry point.

  uv run job-tracker urls.txt                      fetch new jobs -> jobs.json + jobs.csv
  uv run job-tracker urls.txt --sheet --ai --score  ...plus Google Sheet, AI fields, fit scores
  uv run job-tracker --from-json --score --sheet    re-run AI steps on saved jobs (no LinkedIn)
  uv run job-tracker --tailor 4492071444 --sheet    tailored PDF resume for a saved job
"""
import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from .models import Job
from .pipeline import enrich, run
from .storage import save_to_csv, save_to_sheet
from .urls import read_url_file


def load_json(path: str) -> list[Job]:
    p = Path(path)
    if not p.exists():
        return []
    return [_load_job(d) for d in json.loads(p.read_text(encoding="utf-8"))]


def _load_job(data: dict) -> Job:
    """Load one saved job, coping with files written by an older version of the tool.

    When we add fields to the AI outputs (e.g. tailoring_benefit), older saved results
    no longer match the model. They're only cached AI output, so we drop the stale
    part and the next --ai / --score run regenerates it. (This is "schema migration".)
    """
    try:
        return Job.model_validate(data)
    except ValidationError:
        stale = [k for k in ("fit", "insights") if data.get(k) is not None]
        job = Job.model_validate({**data, **dict.fromkeys(stale)})
        print(f"  note: job {job.job_id} has old-format {' and '.join(stale)}; run --score/--ai to refresh")
        return job


def save_json(jobs: list[Job], path: str) -> int:
    """Merge into the existing file by job_id, so jobs.json keeps every job you've ever added."""
    by_id = {j.job_id: j for j in load_json(path)}
    by_id.update({j.job_id: j for j in jobs})
    Path(path).write_text(
        json.dumps([j.model_dump(mode="json") for j in by_id.values()], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(by_id)


def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description="LinkedIn job URLs -> JSON / CSV / Google Sheet")
    ap.add_argument("urls_file", nargs="?", help="text file of LinkedIn job URLs")
    ap.add_argument("--from-json", action="store_true",
                    help="skip LinkedIn; run the AI steps on the jobs already in --out (jobs.json)")
    ap.add_argument("--out", default="jobs.json")
    ap.add_argument("--csv", default="jobs.csv")
    ap.add_argument("--sheet", action="store_true", help="also write to your Google Sheet")
    ap.add_argument("--fetcher", help="direct | scraperapi (default: FETCHER in .env)")
    ap.add_argument("--ai", action="store_true", help="phase 4: LLM reads each description (uses API credit)")
    ap.add_argument("--score", action="store_true", help="phase 5: LLM scores fit vs your profile/ (uses API credit)")
    ap.add_argument("--profile", default="profile", help="folder with master_resume.md etc.")
    ap.add_argument("--tailor", nargs="+", metavar="JOB_ID",
                    help="phase 6: tailored PDF resume for these saved jobs (uses API credit)")
    ap.add_argument("--template", help="with --tailor: force a template letter (default: the scored best_template)")
    args = ap.parse_args()
    if args.tailor:
        return tailor_command(args)
    if not args.urls_file and not args.from_json:
        ap.error("give a urls file, --from-json, or --tailor JOB_ID")

    extractor = scorer = None
    if args.ai:
        from .llm import make_extractor

        extractor = make_extractor()
    if args.score:
        from .scoring import make_scorer

        scorer = make_scorer(args.profile)

    if args.from_json:
        jobs, failed = enrich(load_json(args.out), extractor, scorer), []
    else:
        result = run(read_url_file(args.urls_file), args.fetcher, extractor=extractor, scorer=scorer)
        jobs, failed = result.jobs, result.failed

    total = save_json(jobs, args.out)
    print(f"JSON: {len(jobs)} job(s) saved, {total} in {args.out}")
    save_to_csv(jobs, args.csv)
    if args.sheet and jobs:
        save_to_sheet(
            jobs,
            os.environ["GOOGLE_SHEET_ID"],
            os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"),
        )
    if scorer is not None and scorer.cache_reads:
        print(f"Prompt cache: {scorer.cache_reads:,} resume tokens read from cache (billed at ~5%)")
    if failed:
        print(f"\n{len(failed)} failed:")
        for f_ in failed:
            print(f"  {f_.url}: {f_.error}")


def tailor_command(args):
    from .tailor import make_tailor, tailor_job

    saved = {j.job_id: j for j in load_json(args.out)}
    missing = [jid for jid in args.tailor if jid not in saved]
    if missing:
        raise SystemExit(f"Not in {args.out}: {', '.join(missing)}. Add them first with a urls file.")
    tailor = make_tailor(args.profile)
    done = []
    for i, jid in enumerate(args.tailor, 1):
        job = saved[jid]
        print(f"[{i}/{len(args.tailor)}] {job.title} @ {job.company}")
        try:
            result = tailor_job(job, tailor, template=args.template)
        except Exception as e:  # noqa: BLE001 - one failure shouldn't stop the others
            print(f"  failed: {e}")
            continue
        done.append(job)
        print(f"  review: {result.report_path}")
    if not done:
        return
    save_json(done, args.out)
    save_to_csv(done, args.csv)
    if args.sheet:
        save_to_sheet(
            done,
            os.environ["GOOGLE_SHEET_ID"],
            os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"),
        )


if __name__ == "__main__":
    main()
