"""Spend AIT on AI provider usage (simulated).

Each provider has a deterministic on-chain sink address (no known private
key). "Spending" is a normal signed transaction to that sink with an
AI_SPEND memo; this module prices the purchase and issues a receipt.

This is a simulated integration: no real AI provider accepts AIT today.
`SpendProvider.fulfill` is the extension point where a real integration
(provisioning actual API credits after a confirmed spend) would plug in.
"""

import time
from dataclasses import dataclass

from . import crypto
from .config import COIN

# Price of 1000 model tokens, in AIT base units.
PROVIDERS: dict[str, dict] = {
    "claude": {"name": "Claude (Anthropic)", "price_per_1k_tokens": COIN // 100},
    "gemini": {"name": "Gemini (Google)", "price_per_1k_tokens": COIN // 125},
    "chatgpt": {"name": "ChatGPT (OpenAI)", "price_per_1k_tokens": COIN // 100},
    "grok": {"name": "Grok (xAI)", "price_per_1k_tokens": COIN // 110},
    "llama": {"name": "Llama (Meta)", "price_per_1k_tokens": COIN // 200},
}


class SpendError(Exception):
    pass


def provider_address(provider_id: str) -> str:
    return crypto.address_from_seed(f"AI_PROVIDER:{provider_id}")


def quote(provider_id: str, model_tokens: int) -> int:
    """Cost in AIT base units for `model_tokens` tokens of provider usage."""
    if provider_id not in PROVIDERS:
        raise SpendError(f"unknown provider '{provider_id}'")
    if model_tokens <= 0:
        raise SpendError("model_tokens must be positive")
    price = PROVIDERS[provider_id]["price_per_1k_tokens"]
    return -(-model_tokens * price // 1000)  # ceil


def spend_memo(provider_id: str, model_tokens: int) -> str:
    return f"AI_SPEND:{provider_id}:{model_tokens}"


@dataclass
class Receipt:
    provider: str
    model_tokens: int
    cost: int  # AIT base units
    txid: str
    status: str
    timestamp: float

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model_tokens": self.model_tokens,
            "cost": self.cost,
            "txid": self.txid,
            "status": self.status,
            "timestamp": self.timestamp,
        }


class SpendProvider:
    """Extension point: a real provider integration implements fulfill() to
    provision actual API credits once the spend transaction confirms."""

    def fulfill(self, receipt: Receipt) -> Receipt:
        raise NotImplementedError


class SimulatedProvider(SpendProvider):
    def fulfill(self, receipt: Receipt) -> Receipt:
        receipt.status = "simulated"
        return receipt


def make_receipt(provider_id: str, model_tokens: int, cost: int, txid: str) -> Receipt:
    receipt = Receipt(
        provider=provider_id,
        model_tokens=model_tokens,
        cost=cost,
        txid=txid,
        status="pending",
        timestamp=time.time(),
    )
    return SimulatedProvider().fulfill(receipt)
