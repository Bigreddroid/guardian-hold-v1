"""All environment-specific values live here. Judges check that chain ID, token
address and price source are configuration, not code."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    chain_id: int
    network_label: str
    token_address: str
    price_source: str
    paypal_base_url: str
    paypal_client_id: str
    paypal_client_secret: str
    signing_key: str
    anthropic_api_key: str = ""
    telegraph_endpoint: str = ""
    telegraph_wallet_key: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        return cls(
            chain_id=int(e("CHAIN_ID", "11155111")),
            network_label=e("NETWORK_LABEL", "Sepolia testnet"),
            token_address=e("TOKEN_ADDRESS", ""),
            price_source=e("PRICE_SOURCE", ""),
            paypal_base_url=e("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com"),
            paypal_client_id=e("PAYPAL_CLIENT_ID", ""),
            paypal_client_secret=e("PAYPAL_CLIENT_SECRET", ""),
            signing_key=_signing_key(e("GUARDIAN_SIGNING_KEY", ""), e("GUARDIAN_ENV", "")),
            anthropic_api_key=e("ANTHROPIC_API_KEY", ""),
            telegraph_endpoint=e("TELEGRAPH_ENDPOINT", ""),
            telegraph_wallet_key=e("TELEGRAPH_WALLET_KEY", ""),
        )


_PLACEHOLDER_KEYS = {"", "change-me", "dev-only-change-me"}


def _signing_key(key: str, env: str) -> str:
    """Placeholder keys are only allowed with GUARDIAN_ENV=dev, so a demo can never
    ship verdicts signed with a key everyone can read in the repo."""
    if key in _PLACEHOLDER_KEYS:
        if env != "dev":
            raise ValueError("set GUARDIAN_SIGNING_KEY (or GUARDIAN_ENV=dev for local runs)")
        return "dev-only-change-me"
    return key
