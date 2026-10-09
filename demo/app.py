"""HOLD demo: two storefronts, PayPal AUTHORIZE checkout, Guardian verdict,
capture or void, and a dashboard showing why.

Run:  GUARDIAN_ENV=dev python3 -m demo.app   then open http://localhost:8000
With PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET set it uses the PayPal sandbox;
without them it uses an in-memory simulation and says so on every page.
"""
from __future__ import annotations

import html
import itertools
import json
import os
import threading
import time
import urllib.parse
from datetime import date, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from adapters.paypal import PayPalClient, PayPalError, approve_link
from adapters.telegraph import FakeTelegraph
from agent.verify import verify_purchase
from demo.catalog import SELLERS
from demo.sim_paypal import SimulatedPayPal
from desk.flow import DESK_INTENTS, build_desk
from guardian import Guardian, Mandate, verify
from guardian.config import Config
from hold.ai_review import AIPageChecker, default_reviewer
from hold.flow import HoldFlow, PriceMatchChecker, SellerIdentityChecker, ShipmentChecker

REQUIRED = ("price_match", "seller_identity", "shipment")
MAX_SPEND = Decimal("600.00")
MAX_ORDERS = 50  # one shared demo instance: keep memory bounded
MAX_BODY = 64_000
API_RATE = (30, 60)  # /api/verify: 30 calls per 60 s across all callers (each may be a paid Claude call)


class RateLimiter:
    """Fixed-window limiter: at most `limit` calls per `window` seconds."""

    def __init__(self, limit: int, window: float, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.start, self.count = clock(), 0
        self.lock = threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            now = self.clock()
            if now - self.start >= self.window:
                self.start, self.count = now, 0
            if self.count >= self.limit:
                return False
            self.count += 1
            return True


def read_body(headers, stream) -> bytes:
    """Read a request body safely. ValueError on a bad Content-Length (the caller
    answers 400); OverflowError above MAX_BODY, raised before reading anything (413)."""
    raw = headers.get("Content-Length")
    if raw is None:
        return b""
    try:
        length = int(raw)
    except ValueError:
        raise ValueError(f"bad Content-Length: {raw!r}") from None
    if length < 0:
        raise ValueError(f"bad Content-Length: {raw!r}")
    if length > MAX_BODY:
        raise OverflowError(f"body of {length} bytes is over {MAX_BODY}")
    return stream.read(length)
DESK_BUDGET = Decimal("0.15")
DESK_SCENARIOS = {
    "clean": ("Meridian Grain Trading Ltd", {i: {"ok": True} for i in DESK_INTENTS}),
    "sanctioned": ("Blue Harbor Commodities FZE", {
        **{i: {"ok": True} for i in DESK_INTENTS},
        "SANCTIONS_SCREENING_MATCH": {"ok": False, "detail": "name matches a sanctions list entry (simulated)"},
    }),
}
CURRENCY = "USD"
e = html.escape


class DemoApp:
    def __init__(self, paypal, signing_key: str, base_url: str, reviewer=None, paypal_label: str = "",
                 limiter: "RateLimiter | None" = None):
        self.paypal = paypal
        self.paypal_label = paypal_label or getattr(paypal, "label", "PayPal sandbox")
        self.signing_key = signing_key
        self.base_url = base_url.rstrip("/")
        # One reviewer (and one SDK client) shared by the dashboard and the agent API.
        self.reviewer = reviewer or default_reviewer()
        self.ai = AIPageChecker(self.reviewer)
        self.limiter = limiter or RateLimiter(*API_RATE)
        guardian = Guardian([PriceMatchChecker(), SellerIdentityChecker(), ShipmentChecker(), self.ai],
                            signing_key=signing_key)
        self.flow = HoldFlow(guardian, paypal)
        self.orders = {}
        self._keys = itertools.count(1)
        self.desk_runs = []

    # ---- routing -------------------------------------------------------
    def handle(self, method: str, path: str, query: dict, form: dict, body: bytes = b""):
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
            if method == "POST" and parts == ["api", "verify"]:
                return self._api_verify(body)
            if method == "GET" and parts == ["desk"]:
                return self._page("Counterparty Desk: spend panel", self._desk())
            if method == "POST" and len(parts) == 2 and parts[0] == "desk" and parts[1] in DESK_SCENARIOS:
                return self._desk_run(parts[1])
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
            "listed_currency": CURRENCY, "checkout_currency": o["currency"],
            "seller_registered_email": s["registered_email"], "seller_payout_email": s["payout_email"],
            "page_text": s["page_text"], "shipment_event": o["shipped"],
        }
        verdict, outcome, action = self.flow.settle(
            mandate, o["auth_id"], s["checkout_price"], ctx,
            authorized_amount=o["authorized"], currency=o["currency"])
        o["history"].append({"verdict": verdict, "outcome": outcome, "action": action})

    # ---- agent API -----------------------------------------------------
    def _api_verify(self, body: bytes):
        def reply(status, payload):
            return status, {"Content-Type": "application/json"}, json.dumps(payload, indent=2).encode()
        if len(body) > MAX_BODY:
            return reply(413, {"error": "request body too large"})
        if not self.limiter.allow():
            return reply(429, {"error": "rate limit reached, try again in a minute"})
        try:
            req = json.loads(body or b"{}")
            result = verify_purchase(req.get("listing") or {}, req.get("checkout") or {},
                                     reviewer=self.reviewer, signing_key=self.signing_key)
        except RecursionError:
            return reply(400, {"error": "JSON nested too deeply"})
        except (ValueError, AttributeError) as exc:
            return reply(400, {"error": str(exc)})
        return reply(200, result)

    # ---- Telegraph desk -----------------------------------------------------
    def _desk_run(self, scenario: str):
        name, script = DESK_SCENARIOS[scenario]
        tg = FakeTelegraph(script, prices=dict(DESK_INTENTS))
        mandate = Mandate(f"desk_{scenario}", name, Decimal("0"), CURRENCY,
                          (date.today() + timedelta(days=30)).isoformat(),
                          tuple(i.lower() for i in DESK_INTENTS), check_budget=DESK_BUDGET)
        verdict = build_desk(tg, self.signing_key).run(mandate, {})
        self.desk_runs.append({"name": name, "verdict": verdict, "label": tg.label, "budget": DESK_BUDGET})
        del self.desk_runs[:-MAX_ORDERS]
        return 303, {"Location": "/desk"}, b""

    def _desk(self) -> str:
        full = sum(DESK_INTENTS.values(), Decimal("0"))
        buttons = "".join(
            f"<form method=post action='/desk/{k}' style='display:inline'><button>Screen {e(v[0])}</button></form> "
            for k, v in DESK_SCENARIOS.items())
        cards = []
        for run in reversed(self.desk_runs):
            v = run["verdict"]
            rows = "".join(
                f"<tr><td>{e(c.name.upper())}</td><td class={e(c.result)}>{e(c.result)}</td>"
                f"<td>{e(c.source.removeprefix('telegraph:'))}</td><td>{c.cost}</td>"
                f"<td><code>{e(c.receipt or '')}</code></td><td>{e(c.network)}</td><td>{e(c.detail)}</td></tr>"
                for c in v.checks)
            saved = full - v.total_cost
            if any(c.result == "fail" for c in v.checks):
                why = "screening stopped at the first hard fail"
            elif any("check budget" in r for r in v.reasons):
                why = "the check budget was reached"
            else:
                why = "some checks were not bought"
            cards.append(
                f"<div class=card><h3>{e(run['name'])} · <span class={e(v.decision)}>{e(v.decision.upper())}</span></h3>"
                f"<p class=muted>{e(run['label'])} · spent {v.total_cost} of {run['budget']} budget"
                + (f" · {saved} not spent because {why}" if saved > 0 else "")
                + "</p>"
                f"<div class=scroll><table><tr><th>request</th><th>result</th><th>miner</th><th>amount</th><th>tx hash</th>"
                f"<th>network</th><th>detail</th></tr>{rows}</table></div>"
                f"<ul>{''.join(f'<li>{e(r)}</li>' for r in v.reasons)}</ul></div>")
        return (f"<p><a href='/'>&larr; HOLD dashboard</a></p>"
                f"<p>Screen a counterparty by buying checks through Telegraph, cheapest first, "
                f"stopping at the first hard fail. Check budget: {DESK_BUDGET} USD.</p><p>{buttons}</p>"
                + ("".join(cards) or "<p class=muted>No screenings yet.</p>"))

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
        guide = ("<div class='card guide'><b>Test it in 60 seconds</b><ol>"
                 "<li><b>GPU Deals Direct</b> → Buy with PayPal. Expect <span class=reject>REJECT</span>, "
                 "voided: it charges $560 for a $500 listing.</li>"
                 "<li><b>QuickShip GPUs</b> → Buy with PayPal. Expect <span class=reject>REJECT</span>, "
                 "voided by the page review only: price and payout look fine.</li>"
                 "<li><b>Nimbus Hardware</b> → Buy with PayPal → <i>Seller marks shipped</i>. Expect "
                 "<span class=hold>HOLD</span>, then <span class=approve>APPROVE</span> and captured.</li>"
                 "</ol><span class=muted>Money is only authorized at checkout. Guardian decides whether "
                 "PayPal captures it.</span></div>")
        return (guide + f"<p>Buy the same GPU from each seller: {stores}</p>"
                f"<p class=muted>Also: <a href='/desk'>Counterparty Desk spend panel</a> · "
                f"agents can call <code>POST /api/verify</code></p>"
                f"<p class=muted>Payments: {e(self.paypal_label)} · Page review: {e(self._ai_label())}</p>"
                + (rows or "<p class=muted>No orders yet.</p>"))

    def _ai_label(self) -> str:
        name = type(self.ai.reviewer).__name__
        return {"RuleReviewer": "offline rules (not AI)", "ClaudeReviewer": "Claude"}.get(name, name)

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
                f"<div class=scroll><table><tr><th>check</th><th>result</th><th>source</th><th>detail</th></tr>{checks}</table></div>"
                f"<ul>{reasons}</ul><p class=muted>timeline: {timeline}<br>{action}</p>{ship}</div>")

    def _page(self, title: str, body: str, status: int = 200):
        doc = f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>HOLD · {e(title)}</title><style>
body{{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:0 auto;padding:16px;color:#1b1b1f;background:#fafafa}}
.card{{background:#fff;border:1px solid #ddd;border-radius:10px;padding:14px 16px;margin:14px 0}}
table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border-bottom:1px solid #eee;padding:4px 6px;text-align:left}}
.muted{{color:#666;font-size:13px;overflow-wrap:anywhere}}.scroll{{overflow-x:auto}}.guide{{border-color:#0070ba}}.guide ol{{margin:6px 0;padding-left:20px}}.price{{font-size:24px;font-weight:600}}
.pass,.approve{{color:#0a7a33;font-weight:600}}.fail,.reject,.bad{{color:#b3261e;font-weight:600}}
.unknown,.hold{{color:#9a6700;font-weight:600}}
button,.btn{{display:inline-block;margin:4px 4px 0 0;background:#0070ba;color:#fff;border:0;border-radius:6px;padding:8px 14px;text-decoration:none;cursor:pointer}}
</style></head><body><h1>{e(title)}</h1>{body}</body></html>"""
        return status, {"Content-Type": "text/html; charset=utf-8"}, doc.encode()


def build_app() -> DemoApp:
    cfg = Config.from_env()
    port = int(os.environ.get("PORT", "8000"))
    # Render sets RENDER_EXTERNAL_URL; PayPal needs it for the buyer's return link.
    base = os.environ.get("BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or f"http://localhost:{port}"
    if cfg.paypal_client_id and cfg.paypal_client_secret:
        if not cfg.paypal_is_sandbox:
            raise SystemExit("Refusing to run the demo against a non-sandbox PayPal URL.")
        paypal = PayPalClient(cfg.paypal_base_url, cfg.paypal_client_id, cfg.paypal_client_secret)
        return DemoApp(paypal, cfg.signing_key, base, paypal_label="PayPal sandbox")
    return DemoApp(SimulatedPayPal(), cfg.signing_key, base)


def serve(app: DemoApp, port: int):
    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self, method):
            url = urllib.parse.urlsplit(self.path)
            query = dict(urllib.parse.parse_qsl(url.query))
            try:
                raw = read_body(self.headers, self.rfile)
            except (ValueError, OverflowError) as exc:
                code = 413 if isinstance(exc, OverflowError) else 400
                msg = json.dumps({"error": str(exc)}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(msg)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(msg)
                return
            form = {}
            if raw and "json" not in (self.headers.get("Content-Type") or ""):
                form = dict(urllib.parse.parse_qsl(raw.decode(errors="replace")))
            status, headers, body = app.handle(method, url.path, query, form, raw)
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
