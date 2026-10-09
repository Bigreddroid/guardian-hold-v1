from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from .models import CheckResult, Mandate

_SEVERITY = {"approve": 0, "hold": 1, "reject": 2}


class PolicyGate:
    """Fixed rules decide. An AI recommendation can only make the outcome more
    cautious (approve -> hold/reject); it can never upgrade a decision."""

    def decide(
        self,
        mandate: Mandate,
        results: list,
        ai_recommendation: Optional[str] = None,
        requested_amount: Optional[Decimal] = None,
        requested_currency: Optional[str] = None,
        today: Optional[date] = None,
        authorized_amount: Optional[Decimal] = None,
    ):
        reasons = []
        decision = "approve"
        today = today or date.today()

        failed = [r for r in results if r.result == "fail"]
        unknown = [r for r in results if r.result == "unknown"]
        passed = {r.name for r in results if r.result == "pass"}
        missing = [n for n in mandate.required_checks if n not in passed]

        try:
            expired = date.fromisoformat(mandate.deadline) < today
            bad_deadline = False
        except (TypeError, ValueError):
            expired, bad_deadline = False, True

        if failed:
            decision = "reject"
            reasons += [f"{r.name} failed: {r.detail}".strip() for r in failed]
        elif bad_deadline:
            decision = "reject"
            reasons.append(f"mandate deadline is not an ISO date: {mandate.deadline!r}")
        elif expired:
            decision = "reject"
            reasons.append(f"mandate expired on {mandate.deadline}")
        elif requested_amount is not None and requested_amount <= 0:
            decision = "reject"
            reasons.append(f"amount {requested_amount} is not positive")
        elif requested_currency is not None and requested_currency.upper() != mandate.currency.upper():
            decision = "reject"
            reasons.append(f"currency {requested_currency} does not match mandate {mandate.currency}")
        elif (requested_amount is not None and authorized_amount is not None
              and requested_amount > authorized_amount):
            decision = "reject"
            reasons.append(f"amount {requested_amount} exceeds authorized {authorized_amount}")
        elif requested_amount is not None and requested_amount > mandate.max_spend:
            decision = "reject"
            reasons.append(
                f"amount {requested_amount} exceeds mandate max {mandate.max_spend}"
            )
        elif not results:
            decision = "hold"
            reasons.append("no checks ran")
        elif unknown or missing:
            decision = "hold"
            reasons += [f"{r.name} unknown: {r.detail}".strip() for r in unknown]
            reasons += [f"required check not passed: {n}" for n in missing if n not in {r.name for r in unknown}]

        if ai_recommendation is not None:
            ai = str(ai_recommendation).strip().lower()
            if ai not in _SEVERITY:
                reasons.append(f"AI recommendation ignored (unrecognised value): {ai_recommendation!r}")
            elif _SEVERITY[ai] > _SEVERITY[decision]:
                decision = ai
                reasons.append(f"AI recommendation made the outcome more cautious: {ai}")

        if decision == "approve":
            reasons.append("all required checks passed within the mandate")
        return decision, reasons
