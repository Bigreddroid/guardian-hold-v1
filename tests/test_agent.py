import asyncio
import json
import importlib.util
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.verify import verify_purchase
from guardian import Verdict, verify
from hold.ai_review import RuleReviewer

ROOT = os.path.join(os.path.dirname(__file__), "..")
LISTING = {"item": "RTX 4090", "listed_price": "500.00", "seller_email": "a@x.com",
           "page_text": "Sealed box, PayPal checkout."}
CHECKOUT = {"amount": "500.00", "currency": "usd", "payout_email": "a@x.com"}


def run(listing=LISTING, checkout=CHECKOUT):
    return verify_purchase(listing, checkout, reviewer=RuleReviewer(), signing_key="k", today=date(2026, 10, 9))


class VerifyPurchaseTests(unittest.TestCase):
    def test_clean_purchase_approves_and_never_moves_money(self):
        r = run()
        self.assertEqual(r["decision"], "approve")
        self.assertFalse(r["money_moved"])
        self.assertIn("Capture only after", r["next_step"])

    def test_price_switch_rejects(self):
        self.assertEqual(run(checkout=dict(CHECKOUT, amount="560.00"))["decision"], "reject")

    def test_payout_mismatch_rejects(self):
        self.assertEqual(run(checkout=dict(CHECKOUT, payout_email="b@y.com"))["decision"], "reject")

    def test_off_platform_page_rejects(self):
        r = run(listing=dict(LISTING, page_text="Pay friends and family on WhatsApp"))
        self.assertEqual(r["decision"], "reject")

    def test_over_buyer_limit_rejects(self):
        self.assertEqual(run(checkout=dict(CHECKOUT, max_spend="400"))["decision"], "reject")

    def test_missing_seller_email_holds(self):
        self.assertEqual(run(listing={k: v for k, v in LISTING.items() if k != "seller_email"})["decision"], "hold")

    def test_bad_input_raises_value_error(self):
        for listing, checkout in [({}, CHECKOUT), (LISTING, dict(CHECKOUT, amount="lots")),
                                  (LISTING, dict(CHECKOUT, amount="NaN"))]:
            with self.assertRaises(ValueError):
                run(listing, checkout)

    def test_signature_verifies(self):
        r = run()
        sig = r["signature"]
        # Rebuild the verdict from the dict and check the signature matches.
        from guardian.models import CheckResult
        from decimal import Decimal
        v = Verdict(r["id"], r["mandate_id"], r["decision"],
                    [CheckResult(c["name"], c["result"], c["source"], Decimal(c["cost"]), c["receipt"],
                                 c["detail"], c["network"]) for c in r["checks"]],
                    list(r["reasons"]), Decimal(r["total_cost"]), sig)
        self.assertTrue(verify(v, "k"))


@unittest.skipUnless(importlib.util.find_spec("mcp"), "mcp SDK not installed (pip install -r requirements-mcp.txt)")
class MCPServerSmokeTest(unittest.TestCase):
    def test_stdio_list_and_call(self):
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        async def go():
            env = {"GUARDIAN_ENV": "dev", "PATH": os.environ.get("PATH", "")}
            params = StdioServerParameters(command=sys.executable, args=["-m", "agent.mcp_server"],
                                           env=env, cwd=ROOT)
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    names = [t.name for t in (await s.list_tools()).tools]
                    res = await s.call_tool("verify_purchase", {
                        "item": "GPU", "listed_price": "500", "seller_email": "a@x.com",
                        "page_text": "boxed", "amount": "560", "currency": "USD", "payout_email": "a@x.com"})
                    return names, res
        names, res = asyncio.run(asyncio.wait_for(go(), 60))
        self.assertEqual(names, ["verify_purchase"])
        self.assertFalse(res.is_error)
        self.assertEqual(json.loads(res.content[0].text)["decision"], "reject")


if __name__ == "__main__":
    unittest.main()
