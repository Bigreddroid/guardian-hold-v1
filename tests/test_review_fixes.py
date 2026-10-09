"""Regression tests for the full-repo code review (Oct 9)."""
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.paypal import PayPalClient, PayPalError
from agent.verify import verify_purchase
from demo.app import DemoApp, RateLimiter, read_body
from demo.sim_paypal import SimulatedPayPal
from guardian.config import Config, is_paypal_sandbox
from hold.ai_review import RuleReviewer
from scripts import doctor
from scripts.sandbox_smoke import run as smoke_run

LISTING = {"item": "GPU", "listed_price": "100", "currency": "USD", "seller_email": "a@x.com",
           "page_text": "boxed"}
CHECKOUT = {"amount": "100", "currency": "USD", "payout_email": "a@x.com", "max_spend": "150"}


def verify(listing=LISTING, checkout=CHECKOUT):
    return verify_purchase(listing, checkout, reviewer=RuleReviewer(), signing_key="k", today=date(2026, 10, 9))


class AgentVerifyFixes(unittest.TestCase):
    def test_max_spend_is_required(self):
        with self.assertRaises(ValueError):
            verify(checkout={k: v for k, v in CHECKOUT.items() if k != "max_spend"})

    def test_null_max_spend_is_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, "max_spend is required"):
            verify(checkout=dict(CHECKOUT, max_spend=None))

    def test_amount_above_limit_rejects(self):
        self.assertEqual(verify(checkout=dict(CHECKOUT, amount="160", max_spend="150"),
                                listing=dict(LISTING, listed_price="160"))["decision"], "reject")

    def test_listing_currency_is_required(self):
        with self.assertRaises(ValueError):
            verify(listing={k: v for k, v in LISTING.items() if k != "currency"})

    def test_currency_mismatch_rejects(self):
        r = verify(listing=dict(LISTING, currency="EUR"))
        self.assertEqual(r["decision"], "reject")
        self.assertTrue(any("currency" in x for x in r["reasons"]))


class SandboxGuard(unittest.TestCase):
    def test_only_real_sandbox_hosts_pass(self):
        self.assertTrue(is_paypal_sandbox("https://api-m.sandbox.paypal.com"))
        self.assertTrue(is_paypal_sandbox("https://api-m.sandbox.paypal.com/"))
        for url in ("https://api-m.paypal.com/?sandbox", "https://api-m.paypal.com/sandbox",
                    "https://sandbox.paypal.com.evil.example", "http://api-m.sandbox.paypal.com", ""):
            self.assertFalse(is_paypal_sandbox(url), url)

    def test_doctor_uses_hostname_guard(self):
        c = Config(11155111, "", "", "", "https://api-m.paypal.com/?sandbox", "i", "s", "k")
        status = {n: s for n, s, _ in doctor.report(c, lambda *a: None)}
        self.assertEqual(status["PayPal"], doctor.BAD)


class FakeHeaders(dict):
    def get(self, k, d=None):
        return super().get(k, d)


class ReadBody(unittest.TestCase):
    def test_negative_or_bad_length_rejected(self):
        for v in ("-1", "abc"):
            with self.assertRaises(ValueError):
                read_body(FakeHeaders({"Content-Length": v}), io.BytesIO(b"x" * 10))

    def test_oversized_length_rejected_without_reading(self):
        stream = io.BytesIO(b"x" * 10)
        with self.assertRaises(OverflowError):
            read_body(FakeHeaders({"Content-Length": "999999999"}), stream)
        self.assertEqual(stream.tell(), 0)

    def test_normal_and_missing(self):
        self.assertEqual(read_body(FakeHeaders({"Content-Length": "3"}), io.BytesIO(b"abcdef")), b"abc")
        self.assertEqual(read_body(FakeHeaders({}), io.BytesIO(b"abc")), b"")


class ApiFixes(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.app = DemoApp(SimulatedPayPal(), "k", "http://x", reviewer=RuleReviewer(),
                           limiter=RateLimiter(3, 60, clock=lambda: self.now[0]))

    def post(self, body):
        return self.app.handle("POST", "/api/verify", {}, {}, body)

    def test_rate_limited(self):
        body = json.dumps({"listing": LISTING, "checkout": CHECKOUT}).encode()
        self.assertEqual([self.post(body)[0] for _ in range(4)], [200, 200, 200, 429])
        self.now[0] = 61
        self.assertEqual(self.post(body)[0], 200)

    def test_deep_nesting_is_400(self):
        self.assertEqual(self.post(b"[" * 60000)[0], 400)

    def test_api_uses_the_dashboard_reviewer(self):
        self.assertIs(self.app.ai.reviewer, self.app.reviewer)


class DoctorRobustness(unittest.TestCase):
    def test_non_json_or_tokenless_response_is_a_problem_row(self):
        for exc in (ValueError("not json"), KeyError("access_token")):
            class Broken:
                def __init__(self, *a):
                    pass

                def _auth(self, exc=exc):
                    raise exc
            c = Config(11155111, "", "", "", "https://api-m.sandbox.paypal.com", "i", "s", "k")
            status = {n: s for n, s, _ in doctor.report(c, Broken)}
            self.assertEqual(status["PayPal"], doctor.BAD)


class SmokeCleanup(unittest.TestCase):
    class Box:
        def __init__(self, capture_fails=False, void_status="VOIDED"):
            self.capture_fails, self.void_status, self.calls, self.n = capture_fails, void_status, [], 0

        def create_order(self, amount, currency, reference_id, return_url, cancel_url):
            self.n += 1
            return {"id": f"O{self.n}", "links": [{"rel": "payer-action", "href": "u"}]}

        def get_order(self, oid):
            return {"status": "APPROVED"}

        def authorize_order(self, oid):
            return {"purchase_units": [{"payments": {"authorizations": [
                {"id": f"A-{oid}", "amount": {"value": "1.00", "currency_code": "USD"}}]}}]}

        def capture(self, aid, amount, currency):
            self.calls.append(("capture", aid))
            if self.capture_fails:
                raise PayPalError(422, {"name": "AUTHORIZATION_EXPIRED"})
            return {"id": "CAP1", "status": "COMPLETED"}

        def void(self, aid):
            self.calls.append(("void", aid))
            return {}

        def get_authorization(self, aid):
            return {"id": aid, "status": self.void_status}

    def go(self, box):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "e.json"
            try:
                smoke_run(box, out, wait=lambda s: None, log=lambda m: None)
                err = None
            except PayPalError as e:
                err = e
            return json.loads(out.read_text()), err

    def test_void_status_is_read_back_not_assumed(self):
        data, _ = self.go(self.Box(void_status="CREATED"))
        self.assertEqual(data["orders"]["void"]["void_status"], "CREATED")
        self.assertFalse(data["ok"])

    def test_failure_voids_open_authorizations_and_writes_evidence(self):
        box = self.Box(capture_fails=True)
        data, err = self.go(box)
        self.assertIsNotNone(err)
        self.assertIn(("void", "A-O1"), box.calls)
        self.assertIn(("void", "A-O2"), box.calls)
        self.assertFalse(data["ok"])
        self.assertIn("AUTHORIZATION_EXPIRED", data["error"])

    def test_happy_path_ok(self):
        data, err = self.go(self.Box())
        self.assertIsNone(err)
        self.assertTrue(data["ok"])


class SmokeConfig(unittest.TestCase):
    def test_smoke_does_not_need_signing_key(self):
        old = dict(os.environ)
        try:
            for k in ("GUARDIAN_SIGNING_KEY", "GUARDIAN_ENV", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"):
                os.environ.pop(k, None)
            cfg = Config.from_env(require_signing_key=False)
            self.assertEqual(cfg.paypal_client_id, "")
        finally:
            os.environ.clear(); os.environ.update(old)


class PayPalGetHasNoBody(unittest.TestCase):
    def test_get_order_sends_no_body_or_content_type(self):
        seen = []

        def t(method, url, headers, body):
            seen.append((method, headers, body))
            return {"access_token": "t", "expires_in": 3600} if url.endswith("/token") else {"status": "APPROVED"}
        PayPalClient("https://api-m.sandbox.paypal.com", "i", "s", t).get_order("O1")
        method, headers, body = seen[-1]
        self.assertEqual((method, body), ("GET", None))
        self.assertNotIn("Content-Type", headers)


class DeskLabels(unittest.TestCase):
    def test_budget_stop_is_not_called_a_hard_fail(self):
        from demo import app as appmod
        a = DemoApp(SimulatedPayPal(), "k", "http://x", reviewer=RuleReviewer())
        old = appmod.DESK_BUDGET
        try:
            appmod.DESK_BUDGET = Decimal("0.03")
            a.handle("POST", "/desk/clean", {}, {})
        finally:
            appmod.DESK_BUDGET = old
        page = a.handle("GET", "/desk", {}, {})[2].decode()
        self.assertNotIn("not spent because screening stopped", page)
        self.assertIn("not spent because the check budget was reached", page)
        self.assertIn("spent 0.03 of 0.03 budget", page)
        self.assertNotIn("no wallet configured", page)


if __name__ == "__main__":
    unittest.main()
