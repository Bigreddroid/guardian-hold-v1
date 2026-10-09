"""Counterparty Desk: screen an inbound offer by buying checks through Telegraph,
cheapest first, stopping at the first hard fail."""
from __future__ import annotations

from decimal import Decimal

from adapters.telegraph import PriceAboveCap
from guardian import CheckResult, Guardian, Mandate

# Intents named on Telegraph's Commodities mission page. est_cost values are
# placeholders: replace with the real prices you see on testnet.
DESK_INTENTS = {
    "CORPORATE_REGISTRY_LOOKUP": Decimal("0.02"),
    "SANCTIONS_SCREENING_MATCH": Decimal("0.01"),
    "DOCUMENT_AUTHENTICITY": Decimal("0.05"),
    "VESSEL_TELEMETRY_VERIFY": Decimal("0.04"),
}


class TelegraphChecker:
    def __init__(self, intent: str, est_cost: Decimal, client):
        self.name = intent.lower()
        self.intent = intent
        self.est_cost = est_cost
        self.client = client

    def check(self, mandate: Mandate, ctx: dict) -> CheckResult:
        cap = ctx.get("max_cost")  # remaining check budget, set by Guardian.run
        try:
            ans = self.client.buy(self.intent, ctx.get("params", {}).get(self.intent, {}), max_price=cap)
        except PriceAboveCap as exc:
            return CheckResult(self.name, "unknown", "telegraph",
                               detail=f"not bought, over remaining check budget: {exc}")
        ok = ans.payload.get("ok")
        # Only a real boolean counts; "false", 0 or a missing key must not pass.
        result = "pass" if ok is True else "fail" if ok is False else "unknown"
        return CheckResult(
            self.name, result, f"telegraph:{ans.miner}", ans.amount, ans.tx_hash,
            detail=str(ans.payload.get("detail", "")), network=ans.network,
        )


def build_desk(client, signing_key: str = "dev") -> Guardian:
    checkers = [TelegraphChecker(i, c, client) for i, c in DESK_INTENTS.items()]
    return Guardian(checkers, signing_key=signing_key)
