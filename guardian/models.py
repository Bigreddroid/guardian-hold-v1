from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class Mandate:
    """Limits a human sets before an agent may act."""
    id: str
    subject: str  # item (HOLD) or counterparty (Desk)
    max_spend: Decimal
    currency: str
    deadline: str  # ISO date
    required_checks: tuple = ()
    check_budget: Optional[Decimal] = None  # max total spend on paid checks; None = unlimited


@dataclass
class CheckResult:
    name: str
    result: str  # "pass" | "fail" | "unknown"
    source: str
    cost: Decimal = Decimal("0")
    receipt: Optional[str] = None  # tx hash / receipt id when the check was bought
    detail: str = ""
    network: str = ""  # chain the receipt lives on, for paid checks


@dataclass
class Verdict:
    id: str
    mandate_id: str
    decision: str  # "approve" | "hold" | "reject"
    checks: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    total_cost: Decimal = Decimal("0")
    signature: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "mandate_id": self.mandate_id,
            "decision": self.decision,
            "checks": [
                {
                    "name": c.name,
                    "result": c.result,
                    "source": c.source,
                    "cost": str(c.cost),
                    "receipt": c.receipt,
                    "detail": c.detail,
                    "network": c.network,
                }
                for c in self.checks
            ],
            "reasons": list(self.reasons),
            "total_cost": str(self.total_cost),
            "signature": self.signature,
        }
