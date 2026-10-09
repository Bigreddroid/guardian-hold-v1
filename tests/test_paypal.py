"""PayPal client and HOLD settlement hardening (D9-D12 + error surfacing)."""
import io
import json
import os
import sys
import unittest
import urllib.error
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.paypal import PayPalClient, PayPalError, _urllib_transport, approve_link
from guardian import Guardian, Mandate
from hold.flow import HoldFlow, PriceMatchChecker

M = Mandate("m", "GPU", Decimal("600"), "USD", "2099-12-31", ("price_match",))
OK_CTX = {"listed_price": "500.00", "checkout_price": "500.00"}


class Recorder:
    """Fake transport: records calls, replays queued responses or errors."""

    def __init__(self, *responses):
        self.calls, self.queue = [], list(responses)

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if url.endswith("/v1/oauth2/token"):
            return {"access_token": f"tok{len(self.calls)}", "expires_in": 32400}
        nxt = self.queue.pop(0) if self.queue else {"id": "X", "status": "COMPLETED"}
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def api_calls(self):
        return [c for c in self.calls if not c[1].endswith("/token")]


def client(transport, clock=lambda: 0.0):
    return PayPalClient("https://api-m.sandbox.paypal.com", "id", "secret", transport, clock=clock)


class PayPalClientTests(unittest.TestCase):
    def test_d9_capture_and_void_request_ids_are_deterministic(self):
        t = Recorder()
        c = client(t)
        c.capture("AUTH1", Decimal("5"), "USD")
        c.capture("AUTH1", Decimal("5"), "USD")  # a retry must reuse the same id
        c.void("AUTH2")
        ids = [h["PayPal-Request-Id"] for _, _, h, _ in t.api_calls()]
        self.assertEqual(ids, ["AUTH1-capture", "AUTH1-capture", "AUTH2-void"])

    def test_d11_order_has_experience_context(self):
        t = Recorder()
        client(t).create_order(Decimal("5"), "USD", return_url="https://x/ok", cancel_url="https://x/no")
        body = json.loads(t.api_calls()[0][3])
        ctx = body["payment_source"]["paypal"]["experience_context"]
        self.assertEqual((ctx["return_url"], ctx["cancel_url"]), ("https://x/ok", "https://x/no"))
        self.assertEqual(body["intent"], "AUTHORIZE")

    def test_get_order_is_a_bodyless_get(self):
        t = Recorder({"id": "O1", "status": "APPROVED"})
        self.assertEqual(client(t).get_order("O1")["status"], "APPROVED")
        method, url, _, body = t.api_calls()[0]
        self.assertEqual((method, url.rsplit("/", 1)[-1], body), ("GET", "O1", None))

    def test_approve_link_prefers_payer_action(self):
        order = {"links": [{"rel": "self", "href": "s"}, {"rel": "payer-action", "href": "pa"}]}
        self.assertEqual(approve_link(order), "pa")
        self.assertEqual(approve_link({"links": [{"rel": "approve", "href": "a"}]}), "a")
        self.assertIsNone(approve_link({}))

    def test_d12_token_refreshed_after_expiry(self):
        now = [0.0]
        t = Recorder()
        c = client(t, clock=lambda: now[0])
        c.void("A")
        now[0] = 32400  # past expiry
        c.void("B")
        self.assertEqual(sum(1 for c_ in t.calls if c_[1].endswith("/token")), 2)

    def test_d12_401_refreshes_token_once_and_retries(self):
        t = Recorder(PayPalError(401, {"name": "AUTHENTICATION_FAILURE"}), {"id": "ok"})
        self.assertEqual(client(t).void("A"), {"id": "ok"})
        self.assertEqual(len(t.api_calls()), 2)

    def test_persistent_401_raises(self):
        t = Recorder(PayPalError(401, {}), PayPalError(401, {}))
        with self.assertRaises(PayPalError):
            client(t).void("A")

    def test_http_error_becomes_paypal_error_with_debug_id(self):
        body = json.dumps({"name": "UNPROCESSABLE_ENTITY", "message": "nope", "debug_id": "dbg123"}).encode()
        err = urllib.error.HTTPError("u", 422, "x", {}, io.BytesIO(body))

        def opener(req, timeout):
            raise err

        with self.assertRaises(PayPalError) as cm:
            _urllib_transport("POST", "https://x", {}, b"{}", opener=opener)
        self.assertEqual((cm.exception.status, cm.exception.debug_id), (422, "dbg123"))
        self.assertIn("UNPROCESSABLE_ENTITY", str(cm.exception))


class HoldSettleTests(unittest.TestCase):
    def flow(self, *responses):
        t = Recorder(*responses)
        return HoldFlow(Guardian([PriceMatchChecker()]), client(t)), t

    def test_d10_capture_above_authorized_amount_is_rejected_and_voided(self):
        f, t = self.flow()
        v, outcome, _ = f.settle(M, "AUTH", Decimal("550"), OK_CTX, authorized_amount=Decimal("500"))
        self.assertEqual((v.decision, outcome), ("reject", "voided"))
        self.assertTrue(t.api_calls()[0][1].endswith("/AUTH/void"))

    def test_d10_currency_mismatch_is_rejected(self):
        f, _ = self.flow()
        v, outcome, _ = f.settle(M, "AUTH", Decimal("500"), OK_CTX, currency="EUR")
        self.assertEqual((v.decision, outcome), ("reject", "voided"))

    def test_capture_within_authorization_is_captured(self):
        f, t = self.flow()
        v, outcome, _ = f.settle(M, "AUTH", Decimal("500"), OK_CTX,
                                 authorized_amount=Decimal("500"), currency="USD")
        self.assertEqual((v.decision, outcome), ("approve", "captured"))

    def test_paypal_failure_is_reported_not_raised(self):
        f, _ = self.flow(PayPalError(422, {"name": "AUTHORIZATION_EXPIRED", "debug_id": "d1"}))
        v, outcome, action = f.settle(M, "AUTH", Decimal("500"), OK_CTX)
        self.assertEqual((v.decision, outcome), ("approve", "capture_failed"))
        self.assertEqual(action["debug_id"], "d1")
        self.assertEqual(action["error"], "AUTHORIZATION_EXPIRED")


if __name__ == "__main__":
    unittest.main()
