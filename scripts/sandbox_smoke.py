"""One real PayPal sandbox run, saved as evidence for judges.

    python3 -m scripts.sandbox_smoke

Creates two $1.00 AUTHORIZE orders and prints their approve links. Log in with
the sandbox *Personal* test account to approve both. The script waits, then
authorizes both, captures one and voids the other, and writes
evidence/sandbox-run.json. Sandbox only: it refuses a live PayPal URL.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from adapters.paypal import PayPalClient, approve_link
from guardian.config import Config

AMOUNT = Decimal("1.00")
APPROVED = {"APPROVED", "COMPLETED"}


def _auth_of(authorized: dict) -> dict:
    return authorized["purchase_units"][0]["payments"]["authorizations"][0]


def run(client, out_path: Path, wait=time.sleep, poll_seconds: float = 5, timeout_seconds: float = 900,
        log=print) -> dict:
    """Evidence is always written, even on failure, and any authorization left open
    by a failure is voided so no sandbox funds stay held."""
    stamp = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731
    orders = {}
    evidence = {"environment": "PayPal sandbox", "amount": f"{AMOUNT} USD", "orders": orders, "ok": False}
    try:
        for role in ("capture", "void"):
            order = client.create_order(AMOUNT, "USD", reference_id=f"smoke-{role}",
                                        return_url="https://example.com/approved",
                                        cancel_url="https://example.com/cancelled")
            orders[role] = {"order_id": order["id"], "approve_link": approve_link(order), "created": stamp()}
            log(f"[{role}] approve as the sandbox buyer: {orders[role]['approve_link']}")

        waited = 0.0
        pending = set(orders)
        while pending:
            for role in sorted(pending):
                if client.get_order(orders[role]["order_id"]).get("status") in APPROVED:
                    pending.discard(role)
                    log(f"[{role}] buyer approved")
            if pending:
                if waited >= timeout_seconds:
                    raise TimeoutError(f"buyer did not approve {sorted(pending)} within {timeout_seconds}s")
                wait(poll_seconds)
                waited += poll_seconds

        for role, rec in orders.items():
            auth = _auth_of(client.authorize_order(rec["order_id"]))
            rec["authorization_id"] = auth["id"]
            rec["authorized"] = f"{auth['amount']['value']} {auth['amount']['currency_code']}"
        cap = client.capture(orders["capture"]["authorization_id"], AMOUNT, "USD")
        orders["capture"].update(capture_id=cap.get("id"), capture_status=cap.get("status"), settled=stamp())
        client.void(orders["void"]["authorization_id"])
        # Read the status back from PayPal instead of assuming the void worked.
        status = client.get_authorization(orders["void"]["authorization_id"]).get("status")
        orders["void"].update(void_status=status, settled=stamp())
        evidence["ok"] = orders["capture"]["capture_status"] == "COMPLETED" and status == "VOIDED"
        return evidence
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        for role, rec in orders.items():
            aid = rec.get("authorization_id")
            if aid and not rec.get("capture_id") and not rec.get("void_status"):
                try:
                    client.void(aid)
                    rec["cleanup"] = "voided after failure"
                except Exception as cleanup_exc:  # report, never mask the original error
                    rec["cleanup"] = f"void failed: {cleanup_exc}"
        raise
    finally:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(evidence, indent=2) + "\n")
        log(f"evidence written to {out_path} (ok={evidence['ok']})")


def main() -> int:
    cfg = Config.from_env(require_signing_key=False)  # this run signs nothing
    if not (cfg.paypal_client_id and cfg.paypal_client_secret):
        print("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET (sandbox app) first.")
        return 1
    if not cfg.paypal_is_sandbox:
        print(f"Refusing: {cfg.paypal_base_url} is not the PayPal sandbox.")
        return 1
    client = PayPalClient(cfg.paypal_base_url, cfg.paypal_client_id, cfg.paypal_client_secret)
    evidence = run(client, Path("evidence/sandbox-run.json"))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
