"""Show what is switched on, simulated, or misconfigured. Never moves money.

    python3 -m scripts.doctor
"""
from __future__ import annotations

import sys

from adapters.paypal import PayPalClient, PayPalError
from guardian.config import DEV_SIGNING_KEY, Config

ON, SIM, BAD = "ON", "SIMULATED", "PROBLEM"
SEPOLIA, BASE_SEPOLIA = 11155111, 84532


def report(cfg: Config, paypal_factory=PayPalClient) -> list:
    rows = []

    if cfg.paypal_client_id and cfg.paypal_client_secret:
        if not cfg.paypal_is_sandbox:
            rows.append(("PayPal", BAD, f"{cfg.paypal_base_url} is not the sandbox; refusing live money"))
        else:
            try:
                client = paypal_factory(cfg.paypal_base_url, cfg.paypal_client_id, cfg.paypal_client_secret)
                client._auth()  # token request only; no orders, no money
                rows.append(("PayPal", ON, "sandbox credentials accepted"))
            except PayPalError as exc:
                rows.append(("PayPal", BAD, f"sandbox rejected the credentials: {exc}"))
            except OSError as exc:
                rows.append(("PayPal", BAD, f"could not reach {cfg.paypal_base_url}: {exc}"))
            except (ValueError, KeyError) as exc:
                rows.append(("PayPal", BAD, f"unexpected token response from {cfg.paypal_base_url}: {exc!r}"))
    elif cfg.paypal_client_id or cfg.paypal_client_secret:
        rows.append(("PayPal", BAD, "set both PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET"))
    else:
        rows.append(("PayPal", SIM, "no sandbox keys; demo uses the in-memory simulation"))

    if cfg.anthropic_api_key:
        try:
            import anthropic  # noqa: F401
            rows.append(("Page review", ON, "Claude (ANTHROPIC_API_KEY set)"))
        except ImportError:
            rows.append(("Page review", BAD, "key set but SDK missing: pip install -r requirements-ai.txt"))
    else:
        rows.append(("Page review", SIM, "offline rules, labelled 'not AI'"))

    if cfg.signing_key == DEV_SIGNING_KEY:
        rows.append(("Signing key", SIM, "dev placeholder (GUARDIAN_ENV=dev); set GUARDIAN_SIGNING_KEY for deploys"))
    else:
        rows.append(("Signing key", ON, "set"))

    chain = {SEPOLIA: "Ethereum Sepolia", BASE_SEPOLIA: "Base Sepolia"}.get(cfg.chain_id, f"chain {cfg.chain_id}")
    if cfg.telegraph_endpoint and cfg.telegraph_wallet_key:
        rows.append(("Telegraph", SIM, f"configured for {chain}, but TelegraphClient.buy() is built after Nov 1"))
    else:
        rows.append(("Telegraph", SIM, f"simulated; confirm the chain after Nov 1 (currently {chain})"))
    return rows


def main() -> int:
    try:
        cfg = Config.from_env()
    except ValueError as exc:
        print(f"PROBLEM  config: {exc}")
        return 1
    rows = report(cfg)
    width = max(len(r[0]) for r in rows)
    for name, status, detail in rows:
        print(f"{status:<10} {name:<{width}}  {detail}")
    return 1 if any(r[1] == BAD for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
