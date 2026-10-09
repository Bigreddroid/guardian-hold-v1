"""Telegraph adapter. The real paid-request call is NOT implemented yet: the
Telegraph docs site did not render when this was scaffolded. After registering,
read https://docs.telegraphprotocol.com/ and implement TelegraphClient.buy().

Hard rules from the Apps & Agents track: no direct API calls (every check goes
through a paid Telegraph request), and the spend panel must show request, miner,
amount, Sepolia tx hash and network label."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class TelegraphAnswer:
    intent: str
    payload: dict
    miner: str
    amount: Decimal
    tx_hash: str  # Sepolia transaction hash
    network: str  # e.g. "Sepolia testnet"


class PriceAboveCap(Exception):
    """The quoted price is above what the caller allowed; nothing was paid."""


class TelegraphClient:
    def __init__(self, config):
        self.config = config  # chain_id, token_address, price_source come from here

    def buy(self, intent: str, params: dict, max_price: Optional[Decimal] = None) -> TelegraphAnswer:
        """Must raise PriceAboveCap *before paying* when the 402 quote exceeds max_price."""
        raise NotImplementedError(
            "Implement after reading docs.telegraphprotocol.com: signing, paid request, receipt."
        )


class FakeTelegraph:
    """Test double. `script` maps intent -> payload dict, e.g. {"ok": True}."""

    def __init__(self, script: dict, price: Decimal = Decimal("0.01")):
        self.script = script
        self.price = price
        self.calls = []

    def buy(self, intent: str, params: dict, max_price: Optional[Decimal] = None) -> TelegraphAnswer:
        if max_price is not None and self.price > max_price:
            raise PriceAboveCap(f"quote {self.price} above cap {max_price}")
        self.calls.append(intent)
        return TelegraphAnswer(intent, self.script[intent], "m-test", self.price,
                               f"0xfake{len(self.calls):04d}", "Sepolia testnet (fake)")
