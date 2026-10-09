# Guardian: HOLD (PayPal) and Counterparty Desk (Telegraph)

One verdict engine, two adapters. Verify before money moves.

- `guardian/` shared core: mandate, cheapest-first checkers, policy gate (an AI recommendation can only make the outcome more cautious), signed verdict.
- `hold/` PayPal AUTHORIZE flow: capture on approve, void on reject, leave open on hold.
- `hold/ai_review.py` Claude reads the seller's listing page and recommends approve / hold / reject. It runs last and can only make the verdict more cautious, so a listing that tells the AI to "approve" still fails the fixed checks. Set `ANTHROPIC_API_KEY` and `pip install -r requirements-ai.txt` to use Claude; without a key an offline rule check runs and is labelled "not AI".
- `adapters/paypal.py` sandbox client (orders, authorize, capture, void). Zero dependencies.
- `desk/` + `adapters/telegraph.py` Telegraph checks. **`TelegraphClient.buy()` is not implemented yet**; read https://docs.telegraphprotocol.com/ after registering.

## Run the demo

    GUARDIAN_ENV=dev python3 -m demo.app
    # open http://localhost:8000

No installs needed (Python 3.10+ standard library). Buy the same GPU from three sellers:

| Seller | What happens | Caught by |
|---|---|---|
| Nimbus Hardware (honest) | held until the seller ships, then captured | nothing to catch |
| GPU Deals Direct (trap) | charges $560 for a $500 listing, pays out elsewhere: voided | fixed price check (the AI is never paid for) |
| QuickShip GPUs (off-platform scam) | right price, right payout account, but the page pushes WhatsApp + friends-and-family payment and tells the AI to approve: voided | AI page review only |

Without `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` the demo uses an in-memory PayPal simulation and says so on the page. With them it uses the PayPal sandbox (it refuses to start against a non-sandbox URL).

## Deploy on Render

1. Render dashboard > New > Blueprint > select this repo. `render.yaml` creates the `guardian-hold` web service (free plan, no build step) and generates `GUARDIAN_SIGNING_KEY`.
2. In the service's Environment tab, set `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` (sandbox app). Leave them empty to run the labelled simulation.
3. Optional, last: `ANTHROPIC_API_KEY` switches page review from offline rules to Claude.

The free plan sleeps when idle, so the first visit can take about a minute to wake up.

## Run the tests

    python3 -m unittest discover -s tests -t . -v

## Configure

Copy `.env.example` and export the values. Chain ID, token address and price source are config, never code.

## Status

Working: engine, PayPal AUTHORIZE flow, AI page review, demo app. Not yet built: hosted deployment, live sandbox run, Telegraph paid requests and spend panel.

## License

MIT, see LICENSE.
