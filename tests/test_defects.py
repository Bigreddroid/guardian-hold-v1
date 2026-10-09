"""One regression test per defect found in validation (D1-D8, D13)."""
import os
import sys
import unittest
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.telegraph import FakeTelegraph
from desk.flow import DESK_INTENTS, build_desk
from guardian import CheckResult, Guardian, Mandate, PolicyGate, verify
from guardian.config import Config
from hold.flow import PriceMatchChecker

INTENTS = list(DESK_INTENTS)
TODAY = date(2026, 10, 9)


def mandate(**kw):
    base = dict(id="m", subject="S", max_spend=Decimal("600"), currency="USD",
                deadline="2099-12-31", required_checks=())
    base.update(kw)
    return Mandate(**base)


class Boom:
    def buy(self, intent, params, max_price=None):
        raise TimeoutError("miner timeout")


class DefectTests(unittest.TestCase):
    def test_d1_desk_respects_check_budget(self):
        m = mandate(check_budget=Decimal("0.03"), required_checks=tuple(i.lower() for i in INTENTS))
        tg = FakeTelegraph({i: {"ok": True} for i in INTENTS}, price=Decimal("0.50"))
        v = build_desk(tg).run(m, {})
        self.assertLessEqual(v.total_cost, Decimal("0.03"))
        self.assertEqual(v.decision, "hold")
        self.assertTrue(any("budget" in r for r in v.reasons))

    def test_d2_no_checks_never_approves(self):
        self.assertEqual(Guardian([]).run(mandate(), {}).decision, "hold")

    def test_d3_checker_exception_becomes_unknown(self):
        m = mandate(required_checks=tuple(i.lower() for i in INTENTS))
        v = build_desk(Boom()).run(m, {})
        self.assertEqual(v.decision, "hold")
        self.assertTrue(all(c.result == "unknown" for c in v.checks))
        self.assertIn("TimeoutError", v.checks[0].detail)

    def test_d4_non_bool_ok_is_unknown(self):
        m = mandate(required_checks=tuple(i.lower() for i in INTENTS))
        tg = FakeTelegraph({i: {"ok": "false"} for i in INTENTS})
        v = build_desk(tg).run(m, {})
        self.assertEqual(v.decision, "hold")
        self.assertTrue(all(c.result == "unknown" for c in v.checks))

    def test_d5_ai_recommendation_case_insensitive(self):
        ok = [CheckResult("x", "pass", "s")]
        d, _ = PolicyGate().decide(mandate(), ok, ai_recommendation=" REJECT ")
        self.assertEqual(d, "reject")

    def test_d5_unrecognised_ai_value_is_logged(self):
        ok = [CheckResult("x", "pass", "s")]
        _, reasons = PolicyGate().decide(mandate(), ok, ai_recommendation="maybe")
        self.assertTrue(any("ignored" in r for r in reasons))

    def test_d6_expired_mandate_rejects(self):
        ok = [CheckResult("x", "pass", "s")]
        d, _ = PolicyGate().decide(mandate(deadline="2020-01-01"), ok, today=TODAY)
        self.assertEqual(d, "reject")

    def test_d6_bad_deadline_rejects(self):
        ok = [CheckResult("x", "pass", "s")]
        d, _ = PolicyGate().decide(mandate(deadline="soon"), ok, today=TODAY)
        self.assertEqual(d, "reject")

    def test_d7_non_positive_amount_rejects(self):
        ok = [CheckResult("x", "pass", "s")]
        for amt in ("0", "-5"):
            d, _ = PolicyGate().decide(mandate(), ok, requested_amount=Decimal(amt))
            self.assertEqual(d, "reject", amt)

    def test_d7_currency_mismatch_rejects(self):
        ok = [CheckResult("x", "pass", "s")]
        d, _ = PolicyGate().decide(mandate(), ok, requested_amount=Decimal("5"),
                                   requested_currency="EUR")
        self.assertEqual(d, "reject")

    def test_d8_bad_price_is_unknown_not_crash(self):
        r = PriceMatchChecker().check(mandate(), {"listed_price": "$500", "checkout_price": "500"})
        self.assertEqual(r.result, "unknown")

    def test_d13_verify_detects_tampering(self):
        v = Guardian([PriceMatchChecker()], signing_key="k").run(
            mandate(), {"listed_price": "1", "checkout_price": "1"})
        self.assertTrue(verify(v, "k"))
        self.assertFalse(verify(v, "wrong"))
        v.decision = "approve" if v.decision != "approve" else "reject"
        self.assertFalse(verify(v, "k"))

    def test_d13_default_key_refused_outside_dev(self):
        env = {k: v for k, v in os.environ.items() if k not in ("GUARDIAN_SIGNING_KEY", "GUARDIAN_ENV")}
        old = dict(os.environ)
        try:
            os.environ.clear(); os.environ.update(env)
            with self.assertRaises(ValueError):
                Config.from_env()
            os.environ["GUARDIAN_ENV"] = "dev"
            Config.from_env()
            os.environ["GUARDIAN_ENV"] = "prod"
            os.environ["GUARDIAN_SIGNING_KEY"] = "change-me"
            with self.assertRaises(ValueError):
                Config.from_env()
        finally:
            os.environ.clear(); os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
