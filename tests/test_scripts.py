import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.paypal import PayPalError
from guardian.config import Config
from scripts import doctor
from scripts.sandbox_smoke import run as smoke_run


def cfg(**kw):
    base = dict(chain_id=11155111, network_label="", token_address="", price_source="",
                paypal_base_url="https://api-m.sandbox.paypal.com", paypal_client_id="",
                paypal_client_secret="", signing_key="dev-only-change-me")
    base.update(kw)
    return Config(**base)


class GoodPayPal:
    def __init__(self, *a):
        pass

    def _auth(self):
        return "tok"


class BadPayPal(GoodPayPal):
    def _auth(self):
        raise PayPalError(401, {"name": "invalid_client"})


class DoctorTests(unittest.TestCase):
    def status(self, c, factory=GoodPayPal):
        return {name: st for name, st, _ in doctor.report(c, factory)}

    def test_no_keys_is_all_simulated(self):
        self.assertEqual(set(self.status(cfg()).values()), {doctor.SIM})

    def test_valid_sandbox_keys_on(self):
        self.assertEqual(self.status(cfg(paypal_client_id="i", paypal_client_secret="s"))["PayPal"], doctor.ON)

    def test_rejected_keys_flagged(self):
        s = self.status(cfg(paypal_client_id="i", paypal_client_secret="s"), BadPayPal)
        self.assertEqual(s["PayPal"], doctor.BAD)

    def test_live_url_refused(self):
        s = self.status(cfg(paypal_client_id="i", paypal_client_secret="s",
                            paypal_base_url="https://api-m.paypal.com"))
        self.assertEqual(s["PayPal"], doctor.BAD)

    def test_half_configured_flagged(self):
        self.assertEqual(self.status(cfg(paypal_client_id="i"))["PayPal"], doctor.BAD)

    def test_real_signing_key_on(self):
        self.assertEqual(self.status(cfg(signing_key="real"))["Signing key"], doctor.ON)


class FakeSandbox:
    """Order lifecycle: created -> approved after N polls -> authorized -> capture/void."""

    def __init__(self, approve_after=2):
        self.n, self.polls, self.approve_after, self.calls = 0, {}, approve_after, []

    def create_order(self, amount, currency, reference_id, return_url, cancel_url):
        self.n += 1
        oid = f"O{self.n}"
        self.polls[oid] = 0
        return {"id": oid, "links": [{"rel": "payer-action", "href": f"https://sandbox/approve/{oid}"}]}

    def get_order(self, oid):
        self.polls[oid] += 1
        return {"status": "APPROVED" if self.polls[oid] > self.approve_after else "PAYER_ACTION_REQUIRED"}

    def authorize_order(self, oid):
        return {"purchase_units": [{"payments": {"authorizations": [
            {"id": f"A-{oid}", "amount": {"value": "1.00", "currency_code": "USD"}}]}}]}

    def capture(self, aid, amount, currency):
        self.calls.append(("capture", aid, amount))
        return {"id": "CAP1", "status": "COMPLETED"}

    def void(self, aid):
        self.calls.append(("void", aid))
        return {}

    def get_authorization(self, aid):
        return {"id": aid, "status": "VOIDED"}


class SandboxSmokeTests(unittest.TestCase):
    def test_full_run_writes_evidence(self):
        sb, waits, logs = FakeSandbox(), [], []
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "evidence" / "run.json"
            smoke_run(sb, out, wait=waits.append, log=logs.append)
            data = json.loads(out.read_text())
        self.assertEqual(sb.calls, [("capture", "A-O1", Decimal("1.00")), ("void", "A-O2")])
        self.assertEqual(data["orders"]["capture"]["capture_id"], "CAP1")
        self.assertEqual(data["orders"]["void"]["void_status"], "VOIDED")
        self.assertTrue(any("approve/O1" in l for l in logs))
        self.assertTrue(waits)

    def test_times_out_if_never_approved(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(TimeoutError):
                smoke_run(FakeSandbox(approve_after=10**9), Path(d) / "x.json",
                          wait=lambda s: None, poll_seconds=5, timeout_seconds=20, log=lambda m: None)


if __name__ == "__main__":
    unittest.main()
