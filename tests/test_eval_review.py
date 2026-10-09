import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hold.ai_review import Review
from scripts.eval_review import CASES, evaluate


class Oracle:
    def __init__(self, cases):
        self.answers = {c["page_text"]: c["expected"] for c in cases}

    def review(self, page, checkout):
        return Review(self.answers[page], [], "test")


class Broken:
    def review(self, page, checkout):
        raise TimeoutError("down")


class EvalReviewTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads(CASES.read_text())

    def test_case_file_is_well_formed(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(c["expected"] in ("approve", "hold", "reject") for c in self.cases))
        self.assertTrue(any(c["expected"] == "reject" for c in self.cases))

    def test_perfect_reviewer_scores_100(self):
        r = evaluate(Oracle(self.cases), self.cases)
        self.assertEqual((r["accuracy"], r["scams_approved"], r["clean_rejected"]), (1.0, 0, 0))

    def test_failing_reviewer_counts_as_hold_never_approve(self):
        r = evaluate(Broken(), self.cases)
        self.assertEqual(r["scams_approved"], 0)
        self.assertTrue(all(row["got"].startswith("hold") for row in r["rows"]))


if __name__ == "__main__":
    unittest.main()
