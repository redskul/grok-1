"""Pending-transaction pool, validated against current chain state."""

from .blockchain import Blockchain
from .transaction import Transaction


class MempoolError(Exception):
    pass


class Mempool:
    def __init__(self):
        self.txs: dict[str, Transaction] = {}  # txid -> tx, insertion-ordered

    def __len__(self) -> int:
        return len(self.txs)

    def _pending_for(self, sender: str) -> list[Transaction]:
        return sorted(
            (tx for tx in self.txs.values() if tx.sender == sender),
            key=lambda tx: tx.nonce,
        )

    def add(self, tx: Transaction, chain: Blockchain) -> None:
        if tx.is_coinbase:
            raise MempoolError("coinbase transactions cannot enter the mempool")
        if tx.txid in self.txs:
            raise MempoolError("duplicate transaction")
        if not tx.verify():
            raise MempoolError("invalid signature or malformed transaction")
        pending = self._pending_for(tx.sender)
        expected_nonce = chain.nonce_of(tx.sender) + len(pending)
        if tx.nonce != expected_nonce:
            raise MempoolError(f"bad nonce: got {tx.nonce}, want {expected_nonce}")
        committed = sum(p.amount + p.fee for p in pending)
        if chain.balance_of(tx.sender) - committed < tx.amount + tx.fee:
            raise MempoolError("insufficient balance (including pending transactions)")
        self.txs[tx.txid] = tx

    def select(self, max_txs: int) -> list[Transaction]:
        """Highest chain-fee first, preserving per-sender nonce order."""
        by_sender: dict[str, list[Transaction]] = {}
        for tx in self.txs.values():
            by_sender.setdefault(tx.sender, []).append(tx)
        for txs in by_sender.values():
            txs.sort(key=lambda tx: tx.nonce)
        selected: list[Transaction] = []
        while len(selected) < max_txs:
            # Pick the best head-of-queue transaction across senders.
            candidates = [txs[0] for txs in by_sender.values() if txs]
            if not candidates:
                break
            best = max(candidates, key=lambda tx: tx.fee)
            selected.append(best)
            by_sender[best.sender].pop(0)
        return selected

    def purge_confirmed(self, confirmed_txids: set[str], chain: Blockchain) -> None:
        """Drop confirmed txs, then re-validate what's left against new state."""
        survivors = [tx for txid, tx in self.txs.items() if txid not in confirmed_txids]
        self.txs = {}
        for tx in survivors:
            try:
                self.add(tx, chain)
            except MempoolError:
                pass  # invalidated by the new block (spent balance / used nonce)
