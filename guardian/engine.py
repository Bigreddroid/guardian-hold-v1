from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional, Protocol

from .models import CheckResult, Mandate, Verdict
from .policy import PolicyGate


class Checker(Protocol):
    name: str
    est_cost: Decimal

    def check(self, mandate: Mandate, ctx: dict) -> CheckResult: ...


def sign(verdict: Verdict, key: str) -> str:
    body = verdict.to_dict()
    body["signature"] = ""
    msg = json.dumps(body, sort_keys=True).encode()
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def verify(verdict: Verdict, key: str) -> bool:
    return hmac.compare_digest(verdict.signature, sign(verdict, key))


class Guardian:
    """Runs checkers cheapest-first, stops at the first hard fail, and never
    spends more on checks than the mandate's check budget."""

    def __init__(self, checkers: list, gate: Optional[PolicyGate] = None, signing_key: str = "dev"):
        self.checkers = sorted(checkers, key=lambda c: c.est_cost)
        self.gate = gate or PolicyGate()
        self.signing_key = signing_key

    def run(
        self,
        mandate: Mandate,
        ctx: dict,
        ai_recommendation: Optional[str] = None,
        requested_amount: Optional[Decimal] = None,
        requested_currency: Optional[str] = None,
        today: Optional[date] = None,
        authorized_amount: Optional[Decimal] = None,
    ) -> Verdict:
        results, skipped, over_budget = [], [], []
        spent = Decimal("0")
        for i, checker in enumerate(self.checkers):
            if mandate.check_budget is not None and spent + checker.est_cost > mandate.check_budget:
                # Checkers are sorted by cost, so every later one is over budget too.
                over_budget = [c.name for c in self.checkers[i:]]
                break
            run_ctx = ctx
            if mandate.check_budget is not None:
                # Hard cap for paid checkers: estimates can be wrong, the cap cannot.
                run_ctx = {**ctx, "max_cost": mandate.check_budget - spent}
            try:
                r = checker.check(mandate, run_ctx)
            except Exception as exc:  # a broken source must never crash the verdict
                r = CheckResult(checker.name, "unknown", "error", detail=f"{type(exc).__name__}: {exc}")
            results.append(r)
            spent += r.cost
            if r.result == "fail":
                skipped = [c.name for c in self.checkers[i + 1:]]
                break

        decision, reasons = self.gate.decide(
            mandate, results, ai_recommendation, requested_amount, requested_currency, today,
            authorized_amount)
        if skipped:
            reasons.append("skipped after hard fail: " + ", ".join(skipped))
        if over_budget:
            reasons.append(f"check budget {mandate.check_budget} reached; not bought: " + ", ".join(over_budget))
            if decision == "approve":
                decision = "hold"

        verdict = Verdict(
            id=f"vrd_{uuid.uuid4().hex[:12]}",
            mandate_id=mandate.id,
            decision=decision,
            checks=results,
            reasons=reasons,
            total_cost=sum((r.cost for r in results), Decimal("0")),
        )
        verdict.signature = sign(verdict, self.signing_key)
        return verdict
