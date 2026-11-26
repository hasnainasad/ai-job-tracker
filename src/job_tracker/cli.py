"""Command line entry point. Since phase 7a everything is saved in tracker.db (a SQLite
database); jobs.json and jobs.csv are written after each run as exports.

  uv run job-tracker urls.txt                    fetch new jobs (already-saved ones are skipped)
  uv run job-tracker urls.txt --sheet --ai --score  ...plus AI fields, fit scores, Sheet sync
  uv run job-tracker --saved --score --sheet     AI steps for saved jobs that don't have them yet
  uv run job-tracker --sheet                     just sync the Google Sheet (pull your edits, push ours)
  uv run job-tracker --tailor 4492071444 --sheet tailored PDF resume for a saved job
  uv run job-tracker --search "react lead" --location London --score   find jobs on LinkedIn, add new ones
  uv run job-tracker --searches searches.txt --list-only               just show what the searches find
  add --refresh to redo jobs you already have

The first run moves jobs.json into the database automatically (or use --import-json).
"""
import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlmodel import Session

from .db import (
    add_resume,
    db_url,
    get_job,
    import_json,
    known_ids,
    load_all,
    make_engine,
    upsert_jobs,
)
from .models import Job, load_job
from .pipeline import enrich, run
from .storage import save_to_csv
from .urls import read_url_file


def load_json(path: str) -> list[Job]:
    p = Path(path)
    if not p.exists():
        return []
    return [load_job(d) for d in json.loads(p.read_text(encoding="utf-8"))]


def save_json(jobs: list[Job], path: str) -> int:
    """Merge into the existing file by job_id, so jobs.json keeps every job you've ever added."""
    by_id = {j.job_id: j for j in load_json(path)}
    by_id.update({j.job_id: j for j in jobs})
    Path(path).write_text(
        json.dumps([j.model_dump(mode="json") for j in by_id.values()], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(by_id)


def sync_sheet_now(session: Session) -> None:
    from .sheet_sync import open_worksheet, sync_sheet

    ws = open_worksheet(
        os.environ["GOOGLE_SHEET_ID"], os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    )
    sync_sheet(session, ws)


def export(session: Session, changed: list[Job], args) -> None:
    """jobs.json gets every job; jobs.csv merges the changed ones (keeping what you typed there)."""
    total = save_json(load_all(session), args.out)
    print(f"Exports: {total} job(s) in {args.out}", end="; ")
    save_to_csv(changed, args.csv)


def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description="LinkedIn job URLs -> database -> JSON / CSV / Google Sheet")
    ap.add_argument("urls_file", nargs="?", help="text file of LinkedIn job URLs")
    ap.add_argument("--saved", "--from-json", dest="saved", action="store_true",
                    help="skip LinkedIn; run the AI steps on jobs already in the database")
    ap.add_argument("--db", help="database file or URL (default: DATABASE_URL in .env, else tracker.db)")
    ap.add_argument("--import-json", action="store_true", help="copy --out (jobs.json) into the database")
    ap.add_argument("--out", default="jobs.json", help="JSON export (and the file --import-json reads)")
    ap.add_argument("--csv", default="jobs.csv", help="CSV export")
    ap.add_argument("--sheet", action="store_true", help="also sync your Google Sheet (on its own: sync only)")
    ap.add_argument("--fetcher", help="direct | scraperapi (default: FETCHER in .env)")
    ap.add_argument("--ai", action="store_true", help="phase 4: LLM reads each description (uses API credit)")
    ap.add_argument("--score", action="store_true", help="phase 5: LLM scores fit vs your profile/ (uses API credit)")
    ap.add_argument("--refresh", action="store_true",
                    help="redo jobs you already have: re-fetch them, and re-run --ai/--score on them")
    ap.add_argument("--profile", default="profile", help="folder with master_resume.md etc.")
    ap.add_argument("--tailor", nargs="+", metavar="JOB_ID",
                    help="phase 6: tailored PDF resume for these saved jobs (uses API credit)")
    ap.add_argument("--search", metavar="KEYWORDS", help="phase 7d: search LinkedIn and add the new jobs found")
    ap.add_argument("--searches", metavar="FILE", help="several searches, one per line (see searches.example.txt)")
    ap.add_argument("--location", default="", help="with --search: e.g. London, United Kingdom")
    ap.add_argument("--days", type=int, default=7, help="with --search: posted in the last N days (default 7)")
    ap.add_argument("--limit", type=int, default=20, help="with --search: at most N results per search (default 20)")
    ap.add_argument("--workplace", help="with --search: remote, hybrid, onsite, or a list like remote,hybrid")
    ap.add_argument("--list-only", action="store_true", help="with --search: show results, add nothing")
    ap.add_argument("--template", help="with --tailor: force a template letter (default: the scored best_template)")
    args = ap.parse_args()
    if not (args.urls_file or args.saved or args.tailor or args.sheet or args.import_json
            or args.search or args.searches):
        ap.error("give a urls file, --search, --searches, --saved, --sheet, --import-json or --tailor JOB_ID")

    engine = make_engine(db_url(args.db))
    with Session(engine) as session:
        if args.import_json or (not known_ids(session) and Path(args.out).exists()):
            n = import_json(session, args.out)
            print(f"Database: imported {n} job(s) from {args.out}")
        if args.tailor:
            return tailor_command(args, session)
        if args.search or args.searches:
            urls = search_command(args, session)
            if args.list_only:
                return
            if not urls:
                print("Nothing new to add.")
            else:
                fetch_and_enrich(args, session, urls)
        elif args.urls_file or args.saved:
            fetch_and_enrich(args, session)
        if args.sheet:
            sync_sheet_now(session)


def search_command(args, session: Session) -> list[str]:
    """Run the search(es); print the hits; return the URLs of jobs not saved yet."""
    from .fetchers import make_fetcher
    from .search import read_search_file, search_jobs

    searches = read_search_file(args.searches) if args.searches else []
    if args.search:
        searches.append({"keywords": args.search, "location": args.location, "days": args.days,
                         "workplace": args.workplace})
    fetcher = make_fetcher(args.fetcher)
    args.fetcher_obj = fetcher  # reuse it, so the polite pause carries on into the job fetches
    known, new = known_ids(session), {}
    for q in searches:
        print(f"Searching: {q['keywords']}" + (f" in {q['location']}" if q["location"] else ""))
        for h in search_jobs(fetcher, q["keywords"], q["location"], q["days"], args.limit, q["workplace"]):
            mark = "saved" if h.job_id in known else ("dup" if h.job_id in new else "NEW")
            print(f"  [{mark:5}] {h.job_id}  {h.title} @ {h.company} ({h.location}) {h.listed}")
            if mark == "NEW":
                new[h.job_id] = h.url
    print(f"{len(new)} new job(s) found")
    return list(new.values())


def fetch_and_enrich(args, session: Session, urls: list[str] | None = None) -> None:
    extractor = scorer = None
    if args.ai:
        from .llm import make_extractor

        extractor = make_extractor()
    if args.score:
        from .scoring import make_scorer

        scorer = make_scorer(args.profile)

    failed = []
    if args.saved:
        jobs = enrich(load_all(session), extractor, scorer, only_missing=not args.refresh)
    else:
        known = set() if args.refresh else known_ids(session)
        result = run(urls or read_url_file(args.urls_file), args.fetcher,
                     fetcher=getattr(args, "fetcher_obj", None), extractor=extractor, scorer=scorer,
                     known_ids=known)
        jobs, failed = result.jobs, result.failed
        if result.known and (extractor or scorer):
            print("  tip: to add AI columns to saved jobs that lack them: --saved --ai --score")

    if jobs:
        added, updated = upsert_jobs(session, jobs)
        print(f"Database: {added} added, {updated} updated, {len(known_ids(session))} in total")
        export(session, jobs, args)
    else:
        print("Nothing new to save.")
    if scorer is not None and scorer.cache_reads:
        print(f"Prompt cache: {scorer.cache_reads:,} resume tokens read from cache (billed at ~5%)")
    report_failed(failed)


def report_failed(failed) -> None:
    if failed:
        print(f"\n{len(failed)} failed:")
        for f_ in failed:
            print(f"  {f_.url}: {f_.error}")


def tailor_command(args, session: Session):
    from .tailor import make_tailor, tailor_job

    saved = {jid: get_job(session, jid) for jid in args.tailor}
    missing = [jid for jid, job in saved.items() if job is None]
    if missing:
        raise SystemExit(f"Not saved yet: {', '.join(missing)}. Add them first with a urls file.")
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
        add_resume(session, jid, result)
        done.append(job)
        print(f"  review: {result.report_path}")
    if not done:
        return
    upsert_jobs(session, done)
    export(session, done, args)
    if args.sheet:
        sync_sheet_now(session)


if __name__ == "__main__":
    main()
