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
        """Buy one answer. To implement once Season II docs are readable (Nov 1):

        1. POST the request for `intent`; expect HTTP 402 with a price quote (x402).
        2. If the quote is above `max_price`, raise PriceAboveCap BEFORE paying.
        3. Sign and pay the quote with the wallet on config.chain_id / config.token_address.
        4. Retry the request with the payment proof; read the answer and the tx hash.
        5. Keep at most one request in flight per wallet (Season I apps hit this limit).
        6. Return TelegraphAnswer with the miner id, amount paid, tx hash and
           config.network_label, so the spend panel can show all five fields.
        """
        raise NotImplementedError(
            "Implement after reading docs.telegraphprotocol.com: x402 quote, pay, retry, receipt."
        )


class FakeTelegraph:
    """Test double and demo stand-in. `script` maps intent -> payload dict, e.g.
    {"ok": True}. `prices` optionally maps intent -> price; otherwise `price`."""

    label = "simulated Telegraph (live adapter arrives after Nov 1)"

    def __init__(self, script: dict, price: Decimal = Decimal("0.01"), prices: Optional[dict] = None,
                 network: str = "testnet (simulated)"):
        self.script = script
        self.price = price
        self.prices = prices or {}
        self.network = network
        self.calls = []

    def buy(self, intent: str, params: dict, max_price: Optional[Decimal] = None) -> TelegraphAnswer:
        price = self.prices.get(intent, self.price)
        if max_price is not None and price > max_price:
            raise PriceAboveCap(f"quote {price} above cap {max_price}")
        self.calls.append(intent)
        return TelegraphAnswer(intent, self.script[intent], "m-sim", price,
                               f"0xsim{len(self.calls):04d}", self.network)
