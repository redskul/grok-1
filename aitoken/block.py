"""Blocks: header hashing, merkle root, proof-of-work check."""

import hashlib
import json
from dataclasses import dataclass, field

from .transaction import Transaction


def merkle_root(txids: list[str]) -> str:
    """Sha256 pair-tree over txids (duplicate last node on odd levels)."""
    if not txids:
        return hashlib.sha256(b"").hexdigest()
    level = list(txids)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [
            hashlib.sha256((level[i] + level[i + 1]).encode()).hexdigest()
            for i in range(0, len(level), 2)
        ]
    return level[0]


@dataclass
class Block:
    index: int
    timestamp: float
    prev_hash: str
    merkle_root: str
    difficulty_bits: int
    nonce: int
    transactions: list[Transaction] = field(default_factory=list)

    def header_dict(self) -> dict:
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "prev_hash": self.prev_hash,
            "merkle_root": self.merkle_root,
            "difficulty_bits": self.difficulty_bits,
            "nonce": self.nonce,
        }

    def header_hash(self) -> str:
        payload = json.dumps(self.header_dict(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()

    def meets_difficulty(self) -> bool:
        return hash_meets_difficulty(self.header_hash(), self.difficulty_bits)

    def compute_merkle_root(self) -> str:
        return merkle_root([tx.txid for tx in self.transactions])

    def to_dict(self) -> dict:
        d = self.header_dict()
        d["hash"] = self.header_hash()
        d["transactions"] = [tx.to_dict() for tx in self.transactions]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        return cls(
            index=int(d["index"]),
            timestamp=float(d["timestamp"]),
            prev_hash=d["prev_hash"],
            merkle_root=d["merkle_root"],
            difficulty_bits=int(d["difficulty_bits"]),
            nonce=int(d["nonce"]),
            transactions=[Transaction.from_dict(t) for t in d.get("transactions", [])],
        )


def hash_meets_difficulty(hash_hex: str, difficulty_bits: int) -> bool:
    return int(hash_hex, 16) < (1 << (256 - difficulty_bits))


def mine_header(block: Block, start_nonce: int = 0, max_iterations: int | None = None) -> bool:
    """Grind the nonce in place until the header meets difficulty.

    Returns True when solved; False if max_iterations was exhausted (the
    caller can refresh the template and resume). Shared by the CLI miner
    and the tests so both exercise the same code path.
    """
    nonce = start_nonce
    while max_iterations is None or nonce - start_nonce < max_iterations:
        block.nonce = nonce
        if block.meets_difficulty():
            return True
        nonce += 1
    return False
