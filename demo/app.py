"""HOLD demo: two storefronts, PayPal AUTHORIZE checkout, Guardian verdict,
capture or void, and a dashboard showing why.

Run:  GUARDIAN_ENV=dev python3 -m demo.app   then open http://localhost:8000
With PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET set it uses the PayPal sandbox;
without them it uses an in-memory simulation and says so on every page.
"""
from __future__ import annotations

import html
import itertools
import os
import urllib.parse
from datetime import date, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from adapters.paypal import PayPalClient, PayPalError, approve_link
from demo.catalog import SELLERS
from demo.sim_paypal import SimulatedPayPal
from guardian import Guardian, Mandate, verify
from guardian.config import Config
from hold.ai_review import AIPageChecker
from hold.flow import HoldFlow, PriceMatchChecker, SellerIdentityChecker, ShipmentChecker

REQUIRED = ("price_match", "seller_identity", "shipment")
MAX_SPEND = Decimal("600.00")
MAX_ORDERS = 50  # one shared demo instance: keep memory bounded
CURRENCY = "USD"
e = html.escape


class DemoApp:
    def __init__(self, paypal, signing_key: str, base_url: str, reviewer=None, paypal_label: str = ""):
        self.paypal = paypal
        self.paypal_label = paypal_label or getattr(paypal, "label", "PayPal sandbox")
        self.signing_key = signing_key
        self.base_url = base_url.rstrip("/")
        self.ai = AIPageChecker(reviewer)
        guardian = Guardian([PriceMatchChecker(), SellerIdentityChecker(), ShipmentChecker(), self.ai],
                            signing_key=signing_key)
        self.flow = HoldFlow(guardian, paypal)
        self.orders = {}
        self._keys = itertools.count(1)

    # ---- routing -------------------------------------------------------
    def handle(self, method: str, path: str, query: dict, form: dict):
        parts = [p for p in path.split("/") if p]
        try:
            if method == "GET" and parts == ["healthz"]:
                return 200, {"Content-Type": "text/plain"}, b"ok"
            if method == "GET" and not parts:
                return self._page("HOLD: verify before money moves", self._dashboard())
            if method == "GET" and len(parts) == 2 and parts[0] == "store" and parts[1] in SELLERS:
                return self._page(SELLERS[parts[1]]["name"], self._store(parts[1]))
            if method == "POST" and len(parts) == 2 and parts[0] == "checkout" and parts[1] in SELLERS:
                return self._checkout(parts[1])
            if method == "GET" and parts == ["return"]:
                return self._return(query.get("seller", ""), query.get("token", ""))
            if method == "POST" and len(parts) == 2 and parts[0] == "ship" and parts[1] in self.orders:
                return self._ship(parts[1])
        except PayPalError as exc:
            return self._page("PayPal error", f"<p class=bad>PayPal returned {e(str(exc))}</p>"
                              f"<p><a href='/'>Back to dashboard</a></p>", status=502)
        return self._page("Not found", "<p>Not found. <a href='/'>Dashboard</a></p>", status=404)

    # ---- flow ----------------------------------------------------------
    def _checkout(self, seller: str):
        s = SELLERS[seller]
        q = urllib.parse.urlencode({"seller": seller})
        order = self.paypal.create_order(s["checkout_price"], CURRENCY, reference_id=seller,
                                         return_url=f"{self.base_url}/return?{q}",
                                         cancel_url=f"{self.base_url}/")
        link = approve_link(order)
        if not link:
            return self._page("PayPal error", "<p class=bad>PayPal returned no approval link.</p>", status=502)
        return 303, {"Location": link}, b""

    def _return(self, seller: str, token: str):
        if seller not in SELLERS or not token:
            return self._page("Bad return", "<p>Missing seller or order token.</p>", status=400)
        auth = self.paypal.authorize_order(token)
        a = auth["purchase_units"][0]["payments"]["authorizations"][0]
        key = f"o{next(self._keys)}"
        while len(self.orders) >= MAX_ORDERS:
            self.orders.pop(next(iter(self.orders)))
        self.orders[key] = {
            "seller": seller, "order_id": token, "auth_id": a["id"],
            "authorized": Decimal(a["amount"]["value"]), "currency": a["amount"]["currency_code"],
            "shipped": False, "history": [],
        }
        self._settle(key)
        return 303, {"Location": "/"}, b""

    def _ship(self, key: str):
        o = self.orders[key]
        if o["history"] and o["history"][-1]["outcome"] == "held":
            o["shipped"] = True
            self._settle(key)
        return 303, {"Location": "/"}, b""

    def _settle(self, key: str):
        o, s = self.orders[key], SELLERS[self.orders[key]["seller"]]
        mandate = Mandate(f"man_{key}", s["item"], MAX_SPEND, CURRENCY,
                          (date.today() + timedelta(days=30)).isoformat(), REQUIRED)
        ctx = {
            "listed_price": s["listed_price"], "checkout_price": s["checkout_price"],
            "seller_registered_email": s["registered_email"], "seller_payout_email": s["payout_email"],
            "page_text": s["page_text"], "shipment_event": o["shipped"],
        }
        verdict, outcome, action = self.flow.settle(
            mandate, o["auth_id"], s["checkout_price"], ctx,
            authorized_amount=o["authorized"], currency=o["currency"])
        o["history"].append({"verdict": verdict, "outcome": outcome, "action": action})

    # ---- views ---------------------------------------------------------
    def _store(self, seller: str) -> str:
        s = SELLERS[seller]
        return (f"<p><a href='/'>&larr; Dashboard</a></p><div class=card><h2>{e(s['item'])}</h2>"
                f"<p class=muted>Sold by {e(s['name'])}</p><p class=price>${s['listed_price']:.2f}</p>"
                f"<p>{e(s['page_text'])}</p>"
                f"<form method=post action='/checkout/{seller}'><button>Buy with PayPal</button></form>"
                f"<p class=muted>Your payment is only authorized. Guardian decides whether it is captured.</p></div>")

    def _dashboard(self) -> str:
        stores = "".join(f"<a class=btn href='/store/{k}'>{e(v['name'])}</a> " for k, v in SELLERS.items())
        rows = "".join(self._order(k, o) for k, o in reversed(list(self.orders.items())))
        return (f"<p>Buy the same GPU from each seller: {stores}</p>"
                f"<p class=muted>Payments: {e(self.paypal_label)} · Page review: {e(self._ai_label())}</p>"
                + (rows or "<p class=muted>No orders yet.</p>"))

    def _ai_label(self) -> str:
        return type(self.ai.reviewer).__name__.replace("Reviewer", "") + (
            " (offline rules, not AI)" if type(self.ai.reviewer).__name__ == "RuleReviewer" else "")

    def _order(self, key: str, o: dict) -> str:
        last = o["history"][-1]
        v, outcome = last["verdict"], last["outcome"]
        checks = "".join(
            f"<tr><td>{e(c.name)}</td><td class={e(c.result)}>{e(c.result)}</td>"
            f"<td>{e(c.source)}</td><td>{e(c.detail)}</td></tr>" for c in v.checks)
        reasons = "".join(f"<li>{e(r)}</li>" for r in v.reasons)
        signed = "valid ✓" if verify(v, self.signing_key) else "INVALID ✗"
        ship = (f"<form method=post action='/ship/{key}'><button>Seller marks shipped</button></form>"
                if outcome == "held" else "")
        action = "".join(f"{e(str(k))}: {e(str(val))}<br>" for k, val in last["action"].items())
        timeline = " → ".join(e(h["outcome"]) for h in o["history"])
        return (f"<div class=card><h3>{e(SELLERS[o['seller']]['name'])} · "
                f"<span class={e(v.decision)}>{e(v.decision.upper())}</span> → {e(outcome)}</h3>"
                f"<p class=muted>order {e(o['order_id'])} · authorization {e(o['auth_id'])} · "
                f"authorized {o['authorized']} {e(o['currency'])} · verdict {e(v.id)} · signature {signed}</p>"
                f"<table><tr><th>check</th><th>result</th><th>source</th><th>detail</th></tr>{checks}</table>"
                f"<ul>{reasons}</ul><p class=muted>timeline: {timeline}<br>{action}</p>{ship}</div>")

    def _page(self, title: str, body: str, status: int = 200):
        doc = f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>HOLD · {e(title)}</title><style>
body{{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:0 auto;padding:16px;color:#1b1b1f;background:#fafafa}}
.card{{background:#fff;border:1px solid #ddd;border-radius:10px;padding:14px 16px;margin:14px 0}}
table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border-bottom:1px solid #eee;padding:4px 6px;text-align:left}}
.muted{{color:#666;font-size:13px}}.price{{font-size:24px;font-weight:600}}
.pass,.approve{{color:#0a7a33;font-weight:600}}.fail,.reject,.bad{{color:#b3261e;font-weight:600}}
.unknown,.hold{{color:#9a6700;font-weight:600}}
button,.btn{{background:#0070ba;color:#fff;border:0;border-radius:6px;padding:8px 14px;text-decoration:none;cursor:pointer}}
</style></head><body><h1>{e(title)}</h1>{body}</body></html>"""
        return status, {"Content-Type": "text/html; charset=utf-8"}, doc.encode()


def build_app() -> DemoApp:
    cfg = Config.from_env()
    port = int(os.environ.get("PORT", "8000"))
    # Render sets RENDER_EXTERNAL_URL; PayPal needs it for the buyer's return link.
    base = os.environ.get("BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or f"http://localhost:{port}"
    if cfg.paypal_client_id and cfg.paypal_client_secret:
        if "sandbox" not in cfg.paypal_base_url:
            raise SystemExit("Refusing to run the demo against a non-sandbox PayPal URL.")
        paypal = PayPalClient(cfg.paypal_base_url, cfg.paypal_client_id, cfg.paypal_client_secret)
        return DemoApp(paypal, cfg.signing_key, base, paypal_label="PayPal sandbox")
    return DemoApp(SimulatedPayPal(), cfg.signing_key, base)


def serve(app: DemoApp, port: int):
    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self, method):
            url = urllib.parse.urlsplit(self.path)
            query = dict(urllib.parse.parse_qsl(url.query))
            length = int(self.headers.get("Content-Length") or 0)
            form = dict(urllib.parse.parse_qsl(self.rfile.read(length).decode())) if length else {}
            status, headers, body = app.handle(method, url.path, query, form)
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    print(f"HOLD demo on http://localhost:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    serve(build_app(), int(os.environ.get("PORT", "8000")))
