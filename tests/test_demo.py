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

    def test_pages_render(self):
        for path in ("/", "/store/honest", "/store/trap", "/store/offsite"):
            self.assertEqual(self.app.handle("GET", path, {}, {})[0], 200, path)
        self.assertEqual(self.app.handle("GET", "/healthz", {}, {})[0], 200)
        self.assertEqual(self.app.handle("GET", "/nope", {}, {})[0], 404)
        self.assertEqual(self.app.handle("GET", "/return", {}, {})[0], 400)


if __name__ == "__main__":
    unittest.main()
