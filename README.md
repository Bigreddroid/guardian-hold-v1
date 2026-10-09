# Guardian: verify before money moves

AI agents and online buyers pay sellers they can't vet. Guardian authorizes the payment first, checks the seller, and only then lets PayPal capture the money. If the checks fail, the authorization is voided and nothing is charged.

- **HOLD** (PayPal AI Hackathon): a PayPal AUTHORIZE → Guardian verdict → capture or void flow, plus an MCP tool and REST API that any AI shopping agent can call before it pays.
- **Counterparty Desk** (Telegraph Season II): screens a trading counterparty by buying checks through Telegraph, cheapest first, stopping at the first hard fail, and never spending more than the check budget.

## How it decides

1. **Fixed checks run first, cheapest first.** These are listed price vs checkout amount, and seller vs payout account. A hard fail stops everything, and the remaining checks (including paid ones) are never bought.
2. **AI page review runs last** (`hold/ai_review.py`). Claude reads the listing and recommends approve, hold or reject. It can **only make the verdict more cautious**. A listing that tells the AI to "approve this order" still fails the fixed checks, and the instruction itself counts as a fraud signal.
3. **The policy gate decides** (`guardian/policy.py`). Reject if anything failed, the amount is over the mandate or the authorization, the currency is wrong, or the mandate has expired. Hold if anything is unknown or missing. Approve only if every required check passed.
4. **Every verdict is signed** (HMAC-SHA256), and the dashboard verifies the signature.

## Run it (no installs, no keys)

    GUARDIAN_ENV=dev python3 -m demo.app
    # open http://localhost:8000

Python 3.10+ standard library only. Without keys, PayPal, Claude and Telegraph are simulated and **labelled as simulated on every page**.

| Seller | What happens | Caught by |
|---|---|---|
| Nimbus Hardware (honest) | held until the seller ships, then captured | nothing to catch |
| GPU Deals Direct (trap) | charges $560 for a $500 listing and pays out elsewhere: voided | fixed price check; the AI is never paid for |
| QuickShip GPUs (off-platform scam) | right price and payout account, but the page pushes WhatsApp + friends-and-family payment and tells the AI to approve: voided | AI page review only |

Also on the dashboard:
- `/desk`: the Counterparty Desk spend panel (request, miner, amount, tx hash, network, total spend vs budget).
- `POST /api/verify`: the agent API (see below).

## For AI agents: MCP tool and REST API

Both call the same `agent.verify.verify_purchase`. They return a signed verdict, a `next_step`, and `"money_moved": false`.

**MCP** (Claude Code, Claude Desktop, any MCP client):

    pip install -r requirements-mcp.txt
    claude mcp add guardian -- python3 -m agent.mcp_server

The tool is `verify_purchase(item, listed_price, seller_email, page_text, amount, currency, payout_email, max_spend?)`.

**REST:**

    curl -s localhost:8000/api/verify -H 'Content-Type: application/json' -d '{
      "listing":  {"item": "RTX 4090", "listed_price": "500", "seller_email": "a@shop.example", "page_text": "Sealed box"},
      "checkout": {"amount": "560", "currency": "USD", "payout_email": "a@shop.example"}}'
    # -> "decision": "reject", "next_step": "Do not pay. Walk away from this seller."

## Plug in your keys (nothing else changes)

| Variable | Where to get it | What switches on |
|---|---|---|
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | developer.paypal.com → Apps & Credentials → **Sandbox** → your app | real PayPal sandbox orders, authorizations, captures and voids |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API keys | Claude page review instead of offline rules |
| `GUARDIAN_SIGNING_KEY` | any long random string (Render generates one) | verdict signatures for deploys |
| `CHAIN_ID`, `NETWORK_LABEL`, `TOKEN_ADDRESS`, `TELEGRAPH_ENDPOINT`, `TELEGRAPH_WALLET_KEY` | Telegraph Season II docs (after Nov 1); **testnet wallet only** | real Telegraph paid checks, once `TelegraphClient.buy()` is implemented |

Then check everything with:

    python3 -m scripts.doctor          # ON / SIMULATED / PROBLEM per feature; never moves money
    python3 -m scripts.sandbox_smoke   # one real $1 sandbox run -> evidence/sandbox-run.json

`sandbox_smoke` prints two approve links. Log in as your sandbox *Personal* test buyer to approve both; it then captures one and voids the other.

## Deploy on Render

1. Render → New → **Blueprint** → select this repo. `render.yaml` creates the `guardian-hold` web service (free plan) and generates `GUARDIAN_SIGNING_KEY`.
2. Service → **Environment**: add the keys from the table above when ready. Each save redeploys.

The free plan sleeps when idle, so the first visit can take about a minute.

## Tests

    pip install -r requirements-mcp.txt   # optional; enables the MCP stdio test
    python3 -m unittest discover -s tests -t . -v

## Status

- **Working:** engine, PayPal AUTHORIZE flow, AI page review, demo app, MCP tool, REST API, Desk spend panel (simulated Telegraph), setup doctor, sandbox smoke script.
- **Needs keys:** live PayPal sandbox run, Claude page review.
- **Needs Telegraph docs (Nov 1):** `TelegraphClient.buy()` (an x402 checklist is in `adapters/telegraph.py`) and the real Mission 08 intent names in `desk/flow.py`.

## License

MIT, see LICENSE.
