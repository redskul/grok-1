"""Signed account-model transactions.

The txid is the sha256 of the canonical JSON of all fields except the
signature; the signature signs the txid. Coinbase transactions (mining
rewards) have sender "COINBASE" and carry no signature.
"""

import json
import time
from dataclasses import asdict, dataclass

from . import crypto

COINBASE_SENDER = "COINBASE"


@dataclass
class Transaction:
    sender: str  # address, or COINBASE_SENDER
    recipient: str
    amount: int  # base units
    fee: int  # miner tip (chain-level fee; distinct from the exchange trade fee)
    nonce: int  # per-sender counter, prevents replay
    timestamp: float
    public_key: str = ""  # hex; empty for coinbase
    signature: str = ""  # hex; empty for coinbase
    memo: str = ""

    def _signable_payload(self) -> bytes:
        d = asdict(self)
        d.pop("signature")
        return json.dumps(d, sort_keys=True, separators=(",", ":")).encode()

    @property
    def txid(self) -> str:
        return crypto.sha256_hex(self._signable_payload())

    @property
    def is_coinbase(self) -> bool:
        return self.sender == COINBASE_SENDER

    def sign(self, private_key_hex: str) -> None:
        self.public_key = crypto.public_key_from_private(private_key_hex)
        self.signature = crypto.sign(private_key_hex, self.txid.encode())

    def verify(self) -> bool:
        """Structural + signature validity (balance/nonce checks live in the chain)."""
        if not isinstance(self.amount, int) or not isinstance(self.fee, int):
            return False
        if self.amount < 0 or self.fee < 0 or self.nonce < 0:
            return False
        if not crypto.is_valid_address(self.recipient):
            return False
        if self.is_coinbase:
            return self.signature == "" and self.public_key == "" and self.fee == 0
        if not crypto.is_valid_address(self.sender):
            return False
        if crypto.address_from_pubkey(self.public_key) != self.sender:
            return False
        return crypto.verify(self.public_key, self.signature, self.txid.encode())

    @classmethod
    def coinbase(cls, miner_address: str, reward: int, height: int) -> "Transaction":
        # The memo embeds the height so every coinbase txid is unique.
        return cls(
            sender=COINBASE_SENDER,
            recipient=miner_address,
            amount=reward,
            fee=0,
            nonce=0,
            timestamp=time.time(),
            memo=f"COINBASE:{height}",
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Transaction":
        return cls(
            sender=d["sender"],
            recipient=d["recipient"],
            amount=int(d["amount"]),
            fee=int(d["fee"]),
            nonce=int(d["nonce"]),
            timestamp=float(d["timestamp"]),
            public_key=d.get("public_key", ""),
            signature=d.get("signature", ""),
            memo=d.get("memo", ""),
        )


def make_transfer(
    private_key_hex: str,
    sender: str,
    recipient: str,
    amount: int,
    fee: int,
    nonce: int,
    memo: str = "",
    timestamp: float | None = None,
) -> Transaction:
    tx = Transaction(
        sender=sender,
        recipient=recipient,
        amount=amount,
        fee=fee,
        nonce=nonce,
        timestamp=timestamp if timestamp is not None else time.time(),
        memo=memo,
    )
    tx.sign(private_key_hex)
    return tx
