"""The chain state machine: validation, balances, rewards, difficulty.

Single-authority chain: blocks must extend the current tip; there are no
forks or reorgs (a stale submission is simply rejected and the miner
fetches a fresh template).
"""

import statistics
import time

from .block import Block
from .config import NodeConfig
from .transaction import Transaction

GENESIS_PREV_HASH = "0" * 64
GENESIS_TIMESTAMP = 1700000000.0
MAX_FUTURE_DRIFT_SECONDS = 120.0


class BlockValidationError(Exception):
    pass


def make_genesis_block() -> Block:
    """Deterministic genesis with zero premine: every node starts identically
    and the owner earns only mining rewards and exchange fees."""
    block = Block(
        index=0,
        timestamp=GENESIS_TIMESTAMP,
        prev_hash=GENESIS_PREV_HASH,
        merkle_root="",
        difficulty_bits=0,
        nonce=0,
        transactions=[],
    )
    block.merkle_root = block.compute_merkle_root()
    return block


class Blockchain:
    def __init__(self, config: NodeConfig):
        self.cfg = config
        self.blocks: list[Block] = [make_genesis_block()]
        self.balances: dict[str, int] = {}
        self.nonces: dict[str, int] = {}

    # ------------------------------------------------------------- queries

    @property
    def height(self) -> int:
        return len(self.blocks) - 1

    @property
    def tip(self) -> Block:
        return self.blocks[-1]

    def balance_of(self, address: str) -> int:
        return self.balances.get(address, 0)

    def nonce_of(self, address: str) -> int:
        return self.nonces.get(address, 0)

    def block_reward(self, height: int) -> int:
        return self.cfg.initial_reward >> (height // self.cfg.halving_interval)

    def current_difficulty(self) -> int:
        """Difficulty bits required for the next block.

        Every retarget_interval blocks, compare the actual time the last
        window took against the target and step the difficulty by one bit.
        """
        next_index = len(self.blocks)
        interval = self.cfg.retarget_interval
        if next_index <= interval:
            return self.cfg.initial_difficulty_bits
        bits = self.tip.difficulty_bits
        if next_index % interval == 0:
            window_start = self.blocks[next_index - interval]
            actual = self.tip.timestamp - window_start.timestamp
            expected = interval * self.cfg.target_block_seconds
            if actual < expected / 2:
                bits += 1
            elif actual > expected * 2:
                bits -= 1
        return max(self.cfg.min_difficulty_bits, min(self.cfg.max_difficulty_bits, bits))

    def median_time_past(self, window: int = 11) -> float:
        return statistics.median(b.timestamp for b in self.blocks[-window:])

    # ---------------------------------------------------------- validation

    def validate_block(self, block: Block, now: float | None = None) -> None:
        """Raise BlockValidationError unless `block` validly extends the tip."""
        now = now if now is not None else time.time()
        if block.index != self.height + 1:
            raise BlockValidationError(
                f"stale or out-of-order block: got index {block.index}, want {self.height + 1}"
            )
        if block.prev_hash != self.tip.header_hash():
            raise BlockValidationError("prev_hash does not match current tip")
        required_bits = self.current_difficulty()
        if block.difficulty_bits != required_bits:
            raise BlockValidationError(
                f"wrong difficulty: got {block.difficulty_bits}, want {required_bits}"
            )
        if not block.meets_difficulty():
            raise BlockValidationError("proof of work not satisfied")
        if block.merkle_root != block.compute_merkle_root():
            raise BlockValidationError("merkle root mismatch")
        if block.timestamp <= self.median_time_past():
            raise BlockValidationError("timestamp not after median of recent blocks")
        if block.timestamp > now + MAX_FUTURE_DRIFT_SECONDS:
            raise BlockValidationError("timestamp too far in the future")
        if not block.transactions:
            raise BlockValidationError("block has no coinbase transaction")
        if len(block.transactions) > self.cfg.max_txs_per_block + 1:
            raise BlockValidationError("too many transactions")

        coinbase = block.transactions[0]
        if not coinbase.is_coinbase:
            raise BlockValidationError("first transaction must be the coinbase")
        rest = block.transactions[1:]
        if any(tx.is_coinbase for tx in rest):
            raise BlockValidationError("multiple coinbase transactions")

        total_fees = 0
        seen_txids = {coinbase.txid}
        balances = dict(self.balances)
        nonces = dict(self.nonces)
        for tx in rest:
            if tx.txid in seen_txids:
                raise BlockValidationError(f"duplicate transaction {tx.txid}")
            seen_txids.add(tx.txid)
            if not tx.verify():
                raise BlockValidationError(f"invalid transaction {tx.txid}")
            if tx.nonce != nonces.get(tx.sender, 0):
                raise BlockValidationError(
                    f"bad nonce for {tx.sender}: got {tx.nonce}, want {nonces.get(tx.sender, 0)}"
                )
            if balances.get(tx.sender, 0) < tx.amount + tx.fee:
                raise BlockValidationError(f"insufficient balance for {tx.sender}")
            balances[tx.sender] = balances.get(tx.sender, 0) - tx.amount - tx.fee
            balances[tx.recipient] = balances.get(tx.recipient, 0) + tx.amount
            nonces[tx.sender] = nonces.get(tx.sender, 0) + 1
            total_fees += tx.fee

        if not coinbase.verify():
            raise BlockValidationError("invalid coinbase transaction")
        max_reward = self.block_reward(block.index) + total_fees
        if coinbase.amount > max_reward:
            raise BlockValidationError(
                f"coinbase pays {coinbase.amount}, exceeds reward+fees {max_reward}"
            )

    def append_block(self, block: Block, now: float | None = None) -> None:
        self.validate_block(block, now=now)
        self._apply_block(block)

    def _apply_block(self, block: Block) -> None:
        for tx in block.transactions:
            if tx.is_coinbase:
                self.balances[tx.recipient] = self.balances.get(tx.recipient, 0) + tx.amount
            else:
                self.balances[tx.sender] = self.balances.get(tx.sender, 0) - tx.amount - tx.fee
                self.balances[tx.recipient] = self.balances.get(tx.recipient, 0) + tx.amount
                self.nonces[tx.sender] = self.nonces.get(tx.sender, 0) + 1
        self.blocks.append(block)

    # ------------------------------------------------------------- loading

    @classmethod
    def from_blocks(cls, config: NodeConfig, blocks: list[Block]) -> "Blockchain":
        """Rebuild chain state from stored blocks, fully revalidating."""
        chain = cls(config)
        genesis = make_genesis_block()
        if not blocks or blocks[0].header_hash() != genesis.header_hash():
            raise BlockValidationError("stored chain has a foreign genesis block")
        for block in blocks[1:]:
            # Stored blocks are historical: skip the wall-clock future check.
            chain.append_block(block, now=block.timestamp + MAX_FUTURE_DRIFT_SECONDS)
        return chain

    # ----------------------------------------------------------- templates

    def build_block_template(
        self, miner_address: str, mempool_txs: list[Transaction]
    ) -> Block:
        """Assemble the next block (coinbase included) for a miner to grind."""
        height = self.height + 1
        txs = mempool_txs[: self.cfg.max_txs_per_block]
        reward = self.block_reward(height) + sum(tx.fee for tx in txs)
        coinbase = Transaction.coinbase(miner_address, reward, height)
        timestamp = max(time.time(), self.median_time_past() + 0.001)
        block = Block(
            index=height,
            timestamp=timestamp,
            prev_hash=self.tip.header_hash(),
            merkle_root="",
            difficulty_bits=self.current_difficulty(),
            nonce=0,
            transactions=[coinbase] + txs,
        )
        block.merkle_root = block.compute_merkle_root()
        return block
