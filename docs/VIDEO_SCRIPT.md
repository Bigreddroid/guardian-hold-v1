# Video script: under 3 minutes (target 2:45)

Record the screen with the deployed demo (keys plugged in, so the labels read "PayPal sandbox" and "Claude"). Voice over in a calm, plain tone. Each shot lists what is on screen and what is said.

| Time | On screen | Say |
|---|---|---|
| 0:00–0:15 | Dashboard, three seller buttons | "Scams don't break PayPal. They get you, or your AI agent, to pay the wrong seller. HOLD checks the seller in the gap between authorizing and capturing." |
| 0:15–0:45 | GPU Deals Direct → Buy with PayPal → sandbox approve → dashboard: REJECT → voided, price_match fail | "This seller lists $500 and charges $560. PayPal only authorized the money. Guardian's price check fails, so the authorization is voided. The buyer is never charged, and the AI was never even called, because cheap checks run first." |
| 0:45–1:25 | QuickShip GPUs → Buy → dashboard: price and identity pass, ai_page_review fail with findings | "This one is harder. Right price, right payout account. Every fixed rule passes. But the page pushes you to pay friends-and-family on WhatsApp, and it even tells the AI reviewer to approve. Claude flags it, and it's voided." |
| 1:25–1:50 | Point at the reasons list and the "signature valid" text | "The AI can only make a decision more cautious, never approve something the rules reject. Every verdict is signed." |
| 1:50–2:15 | Nimbus Hardware → Buy → HOLD (no shipment) → Seller marks shipped → captured; timeline "held → captured" | "An honest seller isn't blocked. The money waits, authorized, until the seller ships. Then PayPal captures it." |
| 2:15–2:40 | Claude Code terminal: `verify_purchase` MCP tool call on the trap listing → `"decision": "reject"`, `"money_moved": false` | "AI shopping agents get the same protection. One MCP tool, verify_purchase, and the agent asks before it pays." |
| 2:40–2:50 | Repo page | "HOLD: authorize, verify, then capture. Code and run steps are in the repo." |

Checklist before upload: under 3:00, public on YouTube, and the sandbox labels visible (not "simulated").
