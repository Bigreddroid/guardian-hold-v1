import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from demo.app import DemoApp
from demo.sim_paypal import SimulatedPayPal
from hold.ai_review import RuleReviewer


def buy(app, seller):
    status, headers, _ = app.handle("POST", f"/checkout/{seller}", {}, {})
    assert status == 303, status
    url = urllib.parse.urlsplit(headers["Location"])
    status, _, _ = app.handle("GET", url.path, dict(urllib.parse.parse_qsl(url.query)), {})
    assert status == 303, status
    return list(app.orders)[-1]


class DemoFlowTests(unittest.TestCase):
    def setUp(self):
        self.pp = SimulatedPayPal()
        self.app = DemoApp(self.pp, "k", "http://localhost:8000", reviewer=RuleReviewer())

    def test_trap_seller_is_voided(self):
        key = buy(self.app, "trap")
        last = self.app.orders[key]["history"][-1]
        self.assertEqual((last["verdict"].decision, last["outcome"]), ("reject", "voided"))
        self.assertEqual(self.pp.auths[self.app.orders[key]["auth_id"]], "VOIDED")

    def test_honest_seller_held_until_shipped_then_captured(self):
        key = buy(self.app, "honest")
        self.assertEqual(self.app.orders[key]["history"][-1]["outcome"], "held")
        self.assertEqual(self.pp.auths[self.app.orders[key]["auth_id"]], "CREATED")
        self.app.handle("POST", f"/ship/{key}", {}, {})
        last = self.app.orders[key]["history"][-1]
        self.assertEqual((last["verdict"].decision, last["outcome"]), ("approve", "captured"))
        self.assertEqual(self.pp.auths[self.app.orders[key]["auth_id"]], "CAPTURED")

    def test_offsite_scam_caught_only_by_page_review(self):
        key = buy(self.app, "offsite")
        v = self.app.orders[key]["history"][-1]["verdict"]
        results = {c.name: c.result for c in v.checks}
        self.assertEqual(results["price_match"], "pass")
        self.assertEqual(results["seller_identity"], "pass")
        self.assertEqual(results["ai_page_review"], "fail")
        self.assertEqual((v.decision, self.app.orders[key]["history"][-1]["outcome"]), ("reject", "voided"))

    def test_honest_timeline_shows_hold_then_capture(self):
        key = buy(self.app, "honest")
        self.app.handle("POST", f"/ship/{key}", {}, {})
        _, _, body = self.app.handle("GET", "/", {}, {})
        self.assertIn("held → captured", body.decode())

    def test_ship_after_void_does_nothing(self):
        key = buy(self.app, "trap")
        self.app.handle("POST", f"/ship/{key}", {}, {})
        self.assertEqual(len(self.app.orders[key]["history"]), 1)

    def test_dashboard_escapes_seller_content_and_shows_signature(self):
        buy(self.app, "trap")
        _, _, body = self.app.handle("GET", "/", {}, {})
        page = body.decode()
        self.assertIn("signature valid", page)
        self.assertIn("simulated PayPal", page)
        self.assertIn("not AI", page)
        self.assertNotIn("<script", page)

    def test_orders_are_capped(self):
        from demo import app as appmod
        for _ in range(appmod.MAX_ORDERS + 5):
            buy(self.app, "trap")
        self.assertEqual(len(self.app.orders), appmod.MAX_ORDERS)

    def test_build_app_uses_render_url(self):
        from demo import app as appmod
        env = {"GUARDIAN_ENV": "dev", "RENDER_EXTERNAL_URL": "https://hold.onrender.com"}
        old = dict(os.environ)
        try:
            for k in ("BASE_URL", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"):
                os.environ.pop(k, None)
            os.environ.update(env)
            self.assertEqual(appmod.build_app().base_url, "https://hold.onrender.com")
        finally:
            os.environ.clear(); os.environ.update(old)

    def api(self, payload):
        import json
        status, headers, body = self.app.handle("POST", "/api/verify", {}, {}, json.dumps(payload).encode())
        return status, json.loads(body)

    def test_api_verify_rejects_trap_and_approves_clean(self):
        listing = {"item": "GPU", "listed_price": "500", "seller_email": "a@x.com", "page_text": "boxed"}
        status, r = self.api({"listing": listing, "checkout": {"amount": "560", "currency": "USD",
                                                               "payout_email": "a@x.com"}})
        self.assertEqual((status, r["decision"], r["money_moved"]), (200, "reject", False))
        status, r = self.api({"listing": listing, "checkout": {"amount": "500", "currency": "USD",
                                                               "payout_email": "a@x.com"}})
        self.assertEqual((status, r["decision"]), (200, "approve"))

    def test_api_verify_bad_input_is_400(self):
        self.assertEqual(self.api({"listing": {}, "checkout": {}})[0], 400)
        status, _, _ = self.app.handle("POST", "/api/verify", {}, {}, b"not json")
        self.assertEqual(status, 400)
        status, _, _ = self.app.handle("POST", "/api/verify", {}, {}, b"[1, 2]")
        self.assertEqual(status, 400)

    def test_desk_sanctioned_stops_early_and_shows_spend(self):
        self.app.handle("POST", "/desk/sanctioned", {}, {})
        v = self.app.desk_runs[-1]["verdict"]
        self.assertEqual(v.decision, "reject")
        self.assertEqual([c.name for c in v.checks], ["sanctions_screening_match"])
        self.assertTrue(v.checks[0].receipt and v.checks[0].network)
        page = self.app.handle("GET", "/desk", {}, {})[2].decode()
        self.assertIn("simulated Telegraph", page)
        self.assertIn("not spent because screening stopped", page)

    def test_desk_clean_within_budget_approves(self):
        self.app.handle("POST", "/desk/clean", {}, {})
        v = self.app.desk_runs[-1]["verdict"]
        self.assertEqual((v.decision, len(v.checks)), ("approve", 4))

    def test_pages_render(self):
        for path in ("/", "/store/honest", "/store/trap", "/store/offsite", "/desk"):
            self.assertEqual(self.app.handle("GET", path, {}, {})[0], 200, path)
        self.assertEqual(self.app.handle("GET", "/healthz", {}, {})[0], 200)
        self.assertEqual(self.app.handle("GET", "/nope", {}, {})[0], 404)
        self.assertEqual(self.app.handle("GET", "/return", {}, {})[0], 400)


if __name__ == "__main__":
    unittest.main()
