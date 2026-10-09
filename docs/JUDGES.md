# For judges: test in 60 seconds

**Live demo:** [RENDER URL]. On the free plan the first load can take about a minute while it wakes up.

**Sandbox buyer login** (PayPal sandbox test account, fake money): [SANDBOX BUYER EMAIL] / [PASSWORD]

1. Click **GPU Deals Direct** → **Buy with PayPal**, and log in as the sandbox buyer. Expected: **REJECT → voided** (price mismatch).
2. Click **QuickShip GPUs** → **Buy with PayPal**. Expected: **REJECT → voided** by `ai_page_review` only.
3. Click **Nimbus Hardware** → **Buy with PayPal**. Expected: **HOLD → held**. Then click **Seller marks shipped**. Expected: **APPROVE → captured**.
4. Optional: open `/desk` and screen both counterparties to see the Telegraph spend panel.

**Agent API:**

    curl -s [RENDER URL]/api/verify -H 'Content-Type: application/json' -d '{"listing":{"item":"GPU","listed_price":"500","currency":"USD","seller_email":"a@x.com","page_text":"boxed"},"checkout":{"amount":"560","currency":"USD","payout_email":"a@x.com","max_spend":"600"}}'

**Run it locally instead:** `GUARDIAN_ENV=dev python3 -m demo.app`. No installs are needed. Without keys, every page shows "simulated".
