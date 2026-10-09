"""HOLD: capture only when Guardian approves; void when it rejects; leave the
authorization open when the verdict is hold (honor period is 3 days)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional

from adapters.paypal import PayPalError
from guardian import CheckResult, Mandate


class PriceMatchChecker:
    name = "price_match"
    est_cost = Decimal("0")

    def check(self, mandate: Mandate, ctx: dict) -> CheckResult:
        listed, checkout = ctx.get("listed_price"), ctx.get("checkout_price")
        if listed is None or checkout is None:
            return CheckResult(self.name, "unknown", "marketplace", detail="price data missing")
        try:
            ok = Decimal(str(listed)) == Decimal(str(checkout))
        except InvalidOperation:
            return CheckResult(self.name, "unknown", "marketplace",
                               detail=f"unparseable price: listed {listed!r}, checkout {checkout!r}")
        return CheckResult(self.name, "pass" if ok else "fail", "marketplace",
                           detail="" if ok else f"listed {listed}, checkout {checkout}")


class SellerIdentityChecker:
    name = "seller_identity"
    est_cost = Decimal("0")

    def check(self, mandate: Mandate, ctx: dict) -> CheckResult:
        a, b = ctx.get("seller_registered_email"), ctx.get("seller_payout_email")
        if not a or not b:
            return CheckResult(self.name, "unknown", "marketplace", detail="seller data missing")
        ok = a.strip().lower() == b.strip().lower()
        return CheckResult(self.name, "pass" if ok else "fail", "marketplace",
                           detail="" if ok else "payout account differs from registered seller")


class ShipmentChecker:
    name = "shipment"
    est_cost = Decimal("0")

    def check(self, mandate: Mandate, ctx: dict) -> CheckResult:
        if ctx.get("shipment_event"):
            return CheckResult(self.name, "pass", "simulated seller", detail="shipment event received (simulated)")
        return CheckResult(self.name, "unknown", "simulated seller", detail="no shipment event yet")


class HoldFlow:
    def __init__(self, guardian, paypal):
        self.guardian = guardian
        self.paypal = paypal

    def settle(self, mandate: Mandate, authorization_id: str, amount: Decimal, ctx: dict,
               ai_recommendation: Optional[str] = None,
               authorized_amount: Optional[Decimal] = None, currency: Optional[str] = None):
        verdict = self.guardian.run(mandate, ctx, ai_recommendation, requested_amount=amount,
                                    requested_currency=currency, authorized_amount=authorized_amount)
        if verdict.decision == "hold":
            return verdict, "held", {}
        approve = verdict.decision == "approve"
        try:
            if approve:
                action = self.paypal.capture(authorization_id, amount, currency or mandate.currency)
            else:
                action = self.paypal.void(authorization_id)
        except PayPalError as exc:
            # The verdict stands; the money action failed and must be visible, not swallowed.
            outcome = "capture_failed" if approve else "void_failed"
            return verdict, outcome, {"error": exc.name, "status": exc.status, "debug_id": exc.debug_id}
        return verdict, "captured" if approve else "voided", action
