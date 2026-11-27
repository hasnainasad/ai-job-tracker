"""Phase 7e: is search by meaning any good? Measure it instead of guessing.

An eval set is a few questions with the answers you expect (evals/search.jsonl):
    {"query": "fintech design systems lead", "expect": ["4484482518"]}

For each question we search, then score:
  hit@k   did at least one expected job appear in the top k?        (1 or 0)
  recall  what share of the expected jobs appeared in the top k?
  MRR     1 / rank of the first expected job (1st = 1.0, 2nd = 0.5, missing = 0)
Run it after changing the chunking, the model or the job header, and compare.
"""
import json
from pathlib import Path


def load_cases(path: str) -> list[dict]:
    cases = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            cases.append(json.loads(line))
    return cases


def evaluate(index, cases: list[dict], k: int = 5) -> dict:
    rows = []
    for case in cases:
        found = [h.job_id for h in index.find_jobs(case["query"], limit=k)]
        expect = set(case["expect"])
        ranks = [i for i, jid in enumerate(found, 1) if jid in expect]
        rows.append({"query": case["query"], "found": found, "hit": int(bool(ranks)),
                     "recall": len(expect & set(found)) / len(expect) if expect else 0.0,
                     "rr": 1 / ranks[0] if ranks else 0.0})
    n = len(rows) or 1
    return {"k": k, "cases": rows, "hit_rate": sum(r["hit"] for r in rows) / n,
            "recall": sum(r["recall"] for r in rows) / n, "mrr": sum(r["rr"] for r in rows) / n}


def print_report(report: dict) -> None:
    for r in report["cases"]:
        mark = "ok  " if r["hit"] else "MISS"
        print(f"  {mark} rr={r['rr']:.2f} recall={r['recall']:.2f}  {r['query']}")
    print(f"hit@{report['k']}: {report['hit_rate']:.0%}   recall@{report['k']}: {report['recall']:.0%}   "
          f"MRR: {report['mrr']:.2f}   ({len(report['cases'])} questions)")
