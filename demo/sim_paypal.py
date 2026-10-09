"""In-memory stand-in for the PayPal sandbox, used when no sandbox credentials
are configured. Same method names and response shapes as PayPalClient, and the
dashboard labels every ID it produces as simulated."""
from __future__ import annotations

import itertools
from decimal import Decimal
from typing import Optional

_ids = itertools.count(1)


class SimulatedPayPal:
    label = "simulated PayPal (no sandbox keys)"

    def __init__(self):
        self.orders, self.auths = {}, {}

    def create_order(self, amount: Decimal, currency: str, reference_id: str = "default",
                     return_url: Optional[str] = None, cancel_url: Optional[str] = None) -> dict:
        oid = f"SIM-ORDER-{next(_ids)}"
        self.orders[oid] = (amount, currency)
        return {"id": oid, "status": "PAYER_ACTION_REQUIRED",
                "links": [{"rel": "payer-action", "href": f"{return_url}&token={oid}"}]}

    def authorize_order(self, order_id: str) -> dict:
        amount, currency = self.orders[order_id]
        aid = f"SIM-AUTH-{next(_ids)}"
        self.auths[aid] = "CREATED"
        return {"id": order_id, "status": "COMPLETED", "purchase_units": [{"payments": {"authorizations": [
            {"id": aid, "status": "CREATED", "amount": {"currency_code": currency, "value": f"{amount:.2f}"}}]}}]}

    def capture(self, authorization_id: str, amount: Decimal, currency: str) -> dict:
        self.auths[authorization_id] = "CAPTURED"
        return {"id": f"SIM-CAPTURE-{next(_ids)}", "status": "COMPLETED"}

    def void(self, authorization_id: str) -> dict:
        self.auths[authorization_id] = "VOIDED"
        return {}
