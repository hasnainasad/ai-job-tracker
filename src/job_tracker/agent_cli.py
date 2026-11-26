"""Phase 7d: run the agents from the terminal.

  uv run job-tracker-agent hunt "hybrid React lead roles in London" --max-jobs 5
  uv run job-tracker-agent hunt "remote staff frontend roles in the UK" --tailor 1
  uv run job-tracker-agent review 4492071444            the CV reviewer's verdict on a tailored resume
  uv run job-tracker-agent review 4492071444 --revise   ...and, if it says revise, one revision

Needs OPENAI_API_KEY in .env (the agents' own thinking runs on OpenAI; AGENT_MODEL picks
the model). Fetching, scoring and tailoring still use your Anthropic key, as before.
"""
import argparse
import asyncio
import os

from dotenv import load_dotenv

from .agent_team import HuntBudget, ReviewNotes, hunt, review, revise
from .tracing import flush_tracing, setup_tracing


def print_review(job_id: str, notes: ReviewNotes) -> None:
    print(f"\nReview of job {job_id}: {notes.verdict.upper()} ({notes.score}/10)")
    for s in notes.strengths:
        print(f"  + {s}")
    for i in notes.issues:
        print(f"  - {i}")
    if notes.feedback:
        print(f"  feedback: {notes.feedback}")


def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description="Job tracker agents (phase 7d)")
    sub = ap.add_subparsers(dest="command", required=True)
    h = sub.add_parser("hunt", help="search, add and score jobs for a goal; returns a shortlist")
    h.add_argument("goal", help='e.g. "hybrid React lead roles in London, posted this week"')
    h.add_argument("--max-jobs", type=int, default=5, help="new jobs it may add and score (default 5)")
    h.add_argument("--tailor", type=int, default=0, help="resumes it may tailor, each with one review "
                                                         "and at most one revision (default 0)")
    h.add_argument("--max-turns", type=int, default=40)
    r = sub.add_parser("review", help="CV reviewer on a job's latest tailored resume")
    r.add_argument("job_id")
    r.add_argument("--revise", action="store_true", help="if the verdict is revise: tailor once more "
                                                         "with the feedback (~12 cents), then review again")
    args = ap.parse_args()

    if not os.getenv("OPENAI_API_KEY") and not (os.getenv("AGENT_MODEL") or "").startswith("litellm/"):
        raise SystemExit("OPENAI_API_KEY is missing: add it to .env (see .env.example).")
    where = setup_tracing()
    print(f"Tracing: {where}")
    try:
        if args.command == "hunt":
            asyncio.run(run_hunt(args))
        else:
            asyncio.run(run_review(args))
    finally:
        flush_tracing(where)


async def run_hunt(args) -> None:
    budget = HuntBudget(max_new_jobs=args.max_jobs, max_scores=args.max_jobs, max_tailors=args.tailor,
                        max_reviews=args.tailor * 2)
    print(f"Hunting: {args.goal}\n(this takes a few minutes: LinkedIn is fetched slowly on purpose)")
    shortlist, problems = await hunt(args.goal, budget, max_turns=args.max_turns)
    print(f"\n{shortlist.summary}\n")
    for n, item in enumerate(shortlist.items, 1):
        score = item.fit_score if item.fit_score is not None else "-"
        print(f"{n}. [{score}] {item.title} @ {item.company} ({item.recommendation})  id {item.job_id}")
        print(f"   {item.why}")
        if item.resume:
            print(f"   resume: {item.resume}")
    for step in shortlist.next_steps:
        print(f"next: {step}")
    for p in problems:
        print(f"CHECK: {p}")
    print(f"\nBudget used: {budget.summary()}")
    for rej in budget.rejected:
        print(f"  refused by the budget guard: {rej}")


async def run_review(args) -> None:
    notes = await review(args.job_id)
    print_review(args.job_id, notes)
    if not args.revise or notes.verdict.lower() != "revise":
        return
    print("\nRevising once with the feedback...")
    out = revise(args.job_id, notes.feedback)
    if out["status"] != "done":
        raise SystemExit(f"revision failed: {out['error']}")
    print_review(args.job_id, await review(args.job_id))
    print("That was the one revision. Read the _rev report before sending.")


if __name__ == "__main__":
    main()
