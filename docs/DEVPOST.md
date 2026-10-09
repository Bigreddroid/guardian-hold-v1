# Devpost submission: HOLD

> Fill in the bracketed items before submitting. Every claim below is true of the code in this repo; keep it that way.

**Name:** HOLD by Guardian
**Tagline:** Authorize first, verify the seller, then let PayPal capture. AI can say "slow down", never "pay".

## Inspiration
Marketplace scams don't need to break PayPal. They work by getting the buyer, or now the buyer's AI agent, to pay the wrong seller: a price that changes at checkout, a payout account that isn't the listed seller, a page that pushes you to pay off-platform. Once money is captured, the buyer is left chasing a dispute. We wanted the check to happen in the gap PayPal already gives us: between authorization and capture.

## What it does
- The buyer pays with PayPal using **intent AUTHORIZE**. Funds are held, not taken.
- Guardian runs checks **cheapest first**: listed price vs checkout amount, and listed seller vs payout account. Then an **AI page review** reads the listing.
- A fixed **policy gate** decides:
  - **approve** → capture (only after the seller ships);
  - **reject** → void, so the buyer is never charged;
  - **hold** → leave the authorization open for a human.
- The AI can only make the decision more cautious. A listing that tells the AI to "approve this order" is still voided.
- Every verdict is **signed**, and the dashboard verifies it.
- AI shopping agents get the same protection through an **MCP tool** (`verify_purchase`) and a REST endpoint (`POST /api/verify`). The agent asks before it pays.

## How we built it
- Python standard library only for the core, the PayPal client and the demo server, so judges can run it with one command.
- PayPal **Orders v2** (`intent: AUTHORIZE`, `experience_context` return/cancel URLs) and **Payments v2** (`authorizations/{id}/capture` and `/void`).
- `PayPal-Request-Id` is tied to the authorization, so a retried capture can't charge twice.
- Captures above the authorized amount, or in another currency, are rejected.
- **Claude** (`claude-opus-5-5`) for the page review, with structured JSON output and refusal fallback. Refusals, timeouts and malformed answers all become **hold**, never approve.
- The official **MCP Python SDK** for the agent tool.
- 78 unit tests, including a prompt-injection test and a real MCP client calling the server over stdio. CI on GitHub Actions; deployed on Render.

## Challenges
- **Making the AI useful without trusting it.** The answer was architectural: the AI is one checker among several, runs last, and the gate only lets it lower the outcome.
- **Fail-open bugs.** Our first scaffold approved orders when no checks ran, when a check returned the string "false", or when the AI answered "REJECT" in capitals. Each now has a regression test.

## Accomplishments
- The off-platform scam seller passes every fixed check, and **only the AI page review** catches it.
- The price-trap seller is voided **before** the AI is ever called, so no AI cost is spent on obvious fraud.

## What's next
- Seller shipment webhooks (replacing the simulated "mark shipped" button).
- Per-buyer mandates with spending limits for agents.
- PayPal Agent Toolkit integration for order creation.

## Built with
Python, PayPal Orders v2, PayPal Payments v2 (authorize / capture / void), PayPal Sandbox, Claude (Anthropic API), Model Context Protocol (MCP Python SDK), Render, GitHub Actions.

## Links
- Repo: https://github.com/Bigreddroid/guardian-hold-v1
- Live demo: [RENDER URL]
- Video: [YOUTUBE URL, under 3 minutes]
