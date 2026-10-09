"""MCP server exposing Guardian to any AI shopping agent.

    pip install -r requirements-mcp.txt
    GUARDIAN_ENV=dev python3 -m agent.mcp_server      # stdio transport

Add to Claude Code:  claude mcp add guardian -- python3 -m agent.mcp_server
"""
from __future__ import annotations

from agent.verify import verify_purchase
from guardian.config import Config

INSTRUCTIONS = (
    "Call verify_purchase before paying any seller. Only proceed when decision is "
    "'approve'; on 'hold' ask the human, on 'reject' do not pay."
)


def build_server(signing_key: str, reviewer=None):
    from mcp.server.mcpserver import MCPServer  # optional dependency, imported here

    server = MCPServer(name="guardian", instructions=INSTRUCTIONS)

    @server.tool(name="verify_purchase", description=(
        "Check a purchase before paying: compares listed price to checkout amount, seller "
        "to payout account, and reviews the listing page for fraud signals. Returns a signed "
        "verdict (approve / hold / reject) and next_step. Never moves money."))
    def verify_purchase_tool(item: str, listed_price: str, seller_email: str, page_text: str,
                             amount: str, currency: str, payout_email: str,
                             max_spend: str | None = None) -> dict:
        checkout = {"amount": amount, "currency": currency, "payout_email": payout_email}
        if max_spend is not None:
            checkout["max_spend"] = max_spend
        listing = {"item": item, "listed_price": listed_price, "seller_email": seller_email,
                   "page_text": page_text}
        return verify_purchase(listing, checkout, reviewer=reviewer, signing_key=signing_key)

    return server


if __name__ == "__main__":
    build_server(Config.from_env().signing_key).run("stdio")
