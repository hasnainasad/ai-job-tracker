"""Command line entry point:  uv run job-tracker urls.txt [--sheet]"""
import argparse
import json
import os

from dotenv import load_dotenv

from .pipeline import run
from .storage import save_to_csv, save_to_sheet
from .urls import read_url_file


def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description="LinkedIn job URLs -> JSON / CSV / Google Sheet")
    ap.add_argument("urls_file")
    ap.add_argument("--out", default="jobs.json")
    ap.add_argument("--csv", default="jobs.csv")
    ap.add_argument("--sheet", action="store_true", help="also write to your Google Sheet")
    ap.add_argument("--fetcher", help="direct | scraperapi (default: FETCHER in .env)")
    args = ap.parse_args()

    result = run(read_url_file(args.urls_file), args.fetcher)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump([j.model_dump(mode="json") for j in result.jobs], f, indent=2, ensure_ascii=False)
    print(f"JSON: {len(result.jobs)} job(s) -> {args.out}")
    save_to_csv(result.jobs, args.csv)

    if args.sheet and result.jobs:
        save_to_sheet(
            result.jobs,
            os.environ["GOOGLE_SHEET_ID"],
            os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"),
        )
    if result.failed:
        print(f"\n{len(result.failed)} failed:")
        for f_ in result.failed:
            print(f"  {f_.url}: {f_.error}")


if __name__ == "__main__":
    main()
