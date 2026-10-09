import json
import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.paypal import PayPalClient
from adapters.telegraph import FakeTelegraph
from desk.flow import build_desk
from guardian import Guardian, Mandate, PolicyGate, CheckResult
from hold.flow import HoldFlow, PriceMatchChecker, SellerIdentityChecker, ShipmentChecker

M = Mandate("man_1", "GPU", Decimal("600.00"), "USD", "2099-12-31",
            ("price_match", "seller_identity", "shipment"))


class FakePayPal:
    def __init__(self):
        self.calls = []

    def capture(self, auth_id, amount, currency):
        self.calls.append(("capture", auth_id, amount))
        return {"status": "COMPLETED"}

    def void(self, auth_id):
        self.calls.append(("void", auth_id))
        return {}


class GateTests(unittest.TestCase):
    def test_ai_cannot_upgrade(self):
        fail = [CheckResult("price_match", "fail", "x", detail="mismatch")]
        decision, _ = PolicyGate().decide(M, fail, ai_recommendation="approve")
        self.assertEqual(decision, "reject")

    def test_ai_can_downgrade(self):
        ok = [CheckResult(n, "pass", "x") for n in M.required_checks]
        decision, _ = PolicyGate().decide(M, ok, ai_recommendation="hold")
        self.assertEqual(decision, "hold")

    def test_amount_over_mandate_rejects(self):
        ok = [CheckResult(n, "pass", "x") for n in M.required_checks]
        decision, _ = PolicyGate().decide(M, ok, requested_amount=Decimal("601"))
        self.assertEqual(decision, "reject")

    def test_missing_required_check_holds(self):
        decision, _ = PolicyGate().decide(M, [CheckResult("price_match", "pass", "x")])
        self.assertEqual(decision, "hold")


class HoldTests(unittest.TestCase):
    def flow(self):
        pp = FakePayPal()
        g = Guardian([PriceMatchChecker(), SellerIdentityChecker(), ShipmentChecker()])
        return HoldFlow(g, pp), pp

    def test_honest_seller_is_captured(self):
        flow, pp = self.flow()
        ctx = {"listed_price": "500.00", "checkout_price": "500.00",
               "seller_registered_email": "a@x.com", "seller_payout_email": "A@x.com",
               "shipment_event": True}
        v, outcome, _ = flow.settle(M, "AUTH1", Decimal("500.00"), ctx)
        self.assertEqual((v.decision, outcome), ("approve", "captured"))
        self.assertEqual(pp.calls, [("capture", "AUTH1", Decimal("500.00"))])

    def test_trap_seller_is_voided(self):
        flow, pp = self.flow()
        ctx = {"listed_price": "500.00", "checkout_price": "560.00",
               "seller_registered_email": "a@x.com", "seller_payout_email": "b@y.com"}
        v, outcome, _ = flow.settle(M, "AUTH2", Decimal("560.00"), ctx)
        self.assertEqual((v.decision, outcome), ("reject", "voided"))
        self.assertEqual(pp.calls, [("void", "AUTH2")])

    def test_no_shipment_yet_holds_without_money_moving(self):
        flow, pp = self.flow()
        ctx = {"listed_price": "500.00", "checkout_price": "500.00",
               "seller_registered_email": "a@x.com", "seller_payout_email": "a@x.com"}
        v, outcome, _ = flow.settle(M, "AUTH3", Decimal("500.00"), ctx)
        self.assertEqual((v.decision, outcome), ("hold", "held"))
        self.assertEqual(pp.calls, [])

    def test_signature_present_and_stable_fields(self):
        flow, _ = self.flow()
        v, _, _ = flow.settle(M, "AUTH4", Decimal("500.00"), {})
        self.assertEqual(len(v.signature), 64)


class DeskTests(unittest.TestCase):
    D = Mandate("man_2", "Seller Ltd", Decimal("1.00"), "USD", "2099-12-31",
                ("sanctions_screening_match", "corporate_registry_lookup",
                 "document_authenticity", "vessel_telemetry_verify"))

    def test_stops_at_first_fail_and_saves_spend(self):
        tg = FakeTelegraph({
            "CORPORATE_REGISTRY_LOOKUP": {"ok": True},
            "SANCTIONS_SCREENING_MATCH": {"ok": False, "detail": "list hit"},
            "DOCUMENT_AUTHENTICITY": {"ok": True},
            "VESSEL_TELEMETRY_VERIFY": {"ok": True},
        })
        v = build_desk(tg).run(self.D, {})
        self.assertEqual(v.decision, "reject")
        self.assertEqual(tg.calls, ["SANCTIONS_SCREENING_MATCH"])  # cheapest first, then stop
        self.assertTrue(any("skipped" in r for r in v.reasons))
        self.assertEqual(v.total_cost, Decimal("0.01"))

    def test_all_pass_approves_and_records_receipts(self):
        tg = FakeTelegraph({i: {"ok": True} for i in
                            ["CORPORATE_REGISTRY_LOOKUP", "SANCTIONS_SCREENING_MATCH",
                             "DOCUMENT_AUTHENTICITY", "VESSEL_TELEMETRY_VERIFY"]})
        v = build_desk(tg).run(self.D, {})
        self.assertEqual(v.decision, "approve")
        self.assertTrue(all(c.receipt for c in v.checks))
        self.assertEqual(len(tg.calls), 4)


class PayPalShapeTests(unittest.TestCase):
    def test_request_shapes(self):
        seen = []

        def transport(method, url, headers, body):
            seen.append((method, url, json.loads(body) if body and body.startswith(b"{") else body))
            return {"access_token": "t"} if url.endswith("/token") else {"id": "X"}

        c = PayPalClient("https://api-m.sandbox.paypal.com/", "id", "secret", transport)
        c.create_order(Decimal("5"), "USD")
        c.capture("A1", Decimal("4.5"), "USD")
        c.void("A1")
        self.assertEqual(seen[0][1], "https://api-m.sandbox.paypal.com/v1/oauth2/token")
        self.assertEqual(seen[1][2]["intent"], "AUTHORIZE")
        self.assertEqual(seen[1][2]["purchase_units"][0]["amount"]["value"], "5.00")
        self.assertTrue(seen[2][1].endswith("/v2/payments/authorizations/A1/capture"))
        self.assertEqual(seen[2][2]["amount"]["value"], "4.50")
        self.assertTrue(seen[3][1].endswith("/v2/payments/authorizations/A1/void"))


if __name__ == "__main__":
    unittest.main()
