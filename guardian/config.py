"""All environment-specific values live here. Judges check that chain ID, token
address and price source are configuration, not code."""
from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass

DEV_SIGNING_KEY = "dev-only-change-me"
_SANDBOX_HOSTS = ("api-m.sandbox.paypal.com", "api.sandbox.paypal.com")


def is_paypal_sandbox(url: str) -> bool:
    """True only for an https URL whose host is PayPal's sandbox API. A substring
    check would let `https://api-m.paypal.com/?sandbox` through to live money."""
    parts = urllib.parse.urlsplit(url or "")
    return parts.scheme == "https" and (parts.hostname or "") in _SANDBOX_HOSTS


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

    @property
    def paypal_is_sandbox(self) -> bool:
        return is_paypal_sandbox(self.paypal_base_url)

    @classmethod
    def from_env(cls, require_signing_key: bool = True) -> "Config":
        e = os.environ.get
        key, env = e("GUARDIAN_SIGNING_KEY", ""), e("GUARDIAN_ENV", "")
        return cls(
            chain_id=int(e("CHAIN_ID", "11155111")),
            network_label=e("NETWORK_LABEL", "Sepolia testnet"),
            token_address=e("TOKEN_ADDRESS", ""),
            price_source=e("PRICE_SOURCE", ""),
            paypal_base_url=e("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com"),
            paypal_client_id=e("PAYPAL_CLIENT_ID", ""),
            paypal_client_secret=e("PAYPAL_CLIENT_SECRET", ""),
            signing_key=_signing_key(key, env if require_signing_key else "dev"),
            anthropic_api_key=e("ANTHROPIC_API_KEY", ""),
            telegraph_endpoint=e("TELEGRAPH_ENDPOINT", ""),
            telegraph_wallet_key=e("TELEGRAPH_WALLET_KEY", ""),
        )


_PLACEHOLDER_KEYS = {"", "change-me", DEV_SIGNING_KEY}


def _signing_key(key: str, env: str) -> str:
    """Placeholder keys are only allowed with GUARDIAN_ENV=dev, so a demo can never
    ship verdicts signed with a key everyone can read in the repo."""
    if key in _PLACEHOLDER_KEYS:
        if env != "dev":
            raise ValueError("set GUARDIAN_SIGNING_KEY (or GUARDIAN_ENV=dev for local runs)")
        return DEV_SIGNING_KEY
    return key
