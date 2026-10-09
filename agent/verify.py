"""verify_purchase: the one call an AI shopping agent makes before it pays.

It runs Guardian's fixed checks (price, seller identity) and the page review,
then returns a signed verdict. It never moves money; the caller authorizes or
walks away based on the decision. Shared by the MCP tool and POST /api/verify.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from guardian import Guardian, Mandate
from hold.ai_review import AIPageChecker
from hold.flow import PriceMatchChecker, SellerIdentityChecker

# Before payment there is no shipment yet, so only these must pass.
REQUIRED = ("price_match", "seller_identity")
NEXT_STEP = {
    "approve": "Safe to authorize. Capture only after the seller ships.",
    "hold": "Do not pay yet. A required fact is missing or unclear; ask a human.",
    "reject": "Do not pay. Walk away from this seller.",
}


def _money(value, field: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be a number, got {value!r}") from None
    if not amount.is_finite():
        raise ValueError(f"{field} must be a finite number")
    return amount


def _require(d: dict, key: str, where: str):
    if not isinstance(d, dict) or d.get(key) in (None, ""):
        raise ValueError(f"{where}.{key} is required")
    return d[key]


def verify_purchase(listing: dict, checkout: dict, *, reviewer=None, signing_key: str = "dev",
                    today: Optional[date] = None) -> dict:
    """listing: item, listed_price, seller_email, page_text
    checkout: amount, currency, payout_email, optional max_spend (the buyer's limit)"""
    item = str(_require(listing, "item", "listing"))
    listed = _money(_require(listing, "listed_price", "listing"), "listing.listed_price")
    amount = _money(_require(checkout, "amount", "checkout"), "checkout.amount")
    currency = str(_require(checkout, "currency", "checkout")).upper()
    max_spend = _money(checkout.get("max_spend", amount), "checkout.max_spend")
    today = today or date.today()

    mandate = Mandate(f"agent_{uuid.uuid4().hex[:10]}", item, max_spend, currency,
                      (today + timedelta(days=1)).isoformat(), REQUIRED)
    ctx = {
        "listed_price": listed,
        "checkout_price": amount,
        "seller_registered_email": listing.get("seller_email"),
        "seller_payout_email": checkout.get("payout_email"),
        "page_text": listing.get("page_text"),
    }
    guardian = Guardian([PriceMatchChecker(), SellerIdentityChecker(), AIPageChecker(reviewer)],
                        signing_key=signing_key)
    verdict = guardian.run(mandate, ctx, requested_amount=amount, requested_currency=currency, today=today)
    out = verdict.to_dict()
    out["next_step"] = NEXT_STEP[verdict.decision]
    out["money_moved"] = False
    return out
