"""Measure a page reviewer against evals/page_review_cases.json.

    python3 -m scripts.eval_review          # Claude if ANTHROPIC_API_KEY is set, else offline rules
    python3 -m scripts.eval_review --rules  # force the offline rules

Each Claude run costs one API call per case. Prints a confusion table and every
miss. The number that matters most: scam pages that were approved (money lost).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from hold.ai_review import RuleReviewer, default_reviewer

CASES = Path(__file__).resolve().parent.parent / "evals" / "page_review_cases.json"
LABELS = ("approve", "hold", "reject")


def evaluate(reviewer, cases: list) -> dict:
    rows, confusion = [], Counter()
    for case in cases:
        try:
            got = str(reviewer.review(case["page_text"], {"checkout_price": "500.00"}).recommendation)
        except Exception as exc:  # a failing reviewer counts as hold, like in production
            got = f"hold ({type(exc).__name__})"
        verdict = got.split(" ")[0]
        confusion[(case["expected"], verdict)] += 1
        rows.append({"id": case["id"], "expected": case["expected"], "got": got})
    correct = sum(n for (e, g), n in confusion.items() if e == g)
    return {
        "rows": rows,
        "confusion": confusion,
        "accuracy": correct / len(cases) if cases else 0.0,
        "scams_approved": confusion[("reject", "approve")],
        "clean_rejected": confusion[("approve", "reject")],
    }


def main(argv: list) -> int:
    reviewer = RuleReviewer() if "--rules" in argv else default_reviewer()
    result = evaluate(reviewer, json.loads(CASES.read_text()))
    print(f"reviewer: {type(reviewer).__name__}   cases: {len(result['rows'])}   accuracy: {result['accuracy']:.0%}")
    print("expected \\ got   " + "  ".join(f"{l:>7}" for l in LABELS))
    for e in LABELS:
        print(f"{e:<16} " + "  ".join(f"{result['confusion'][(e, g)]:>7}" for g in LABELS))
    print(f"scam pages approved (money lost): {result['scams_approved']}")
    print(f"clean pages rejected (sales lost): {result['clean_rejected']}")
    for r in result["rows"]:
        if not r["got"].startswith(r["expected"]):
            print(f"  MISS {r['id']}: expected {r['expected']}, got {r['got']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
