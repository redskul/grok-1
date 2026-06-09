"""Node configuration.

All monetary values are integers: AIT amounts are in base units
(1 AIT = 10**8 base units, "nanoAIT"), USD credits are in cents.
"""

import os
from dataclasses import dataclass, field

# 1 AIT = 10**8 base units.
COIN = 10**8


def _env(name: str, default):
    raw = os.environ.get(f"AITOKEN_{name}")
    if raw is None:
        return default
    if isinstance(default, bool):
        return raw.lower() in ("1", "true", "yes")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


@dataclass
class NodeConfig:
    # Server
    host: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env("PORT", 8545))
    db_path: str = field(default_factory=lambda: _env("DB_PATH", "./aitoken.db"))

    # Owner / fees. If owner_address is empty the node generates an owner
    # wallet at startup (saved to owner_wallet_path) and uses its address.
    owner_address: str = field(default_factory=lambda: _env("OWNER_ADDRESS", ""))
    owner_wallet_path: str = field(
        default_factory=lambda: _env("OWNER_WALLET_PATH", "./owner_wallet.json")
    )
    # Exchange fee, percent of trade notional, charged to the taker.
    fee_percent: float = field(default_factory=lambda: _env("FEE_PERCENT", 0.5))

    # Chain constants
    initial_reward: int = 50 * COIN
    halving_interval: int = 200  # blocks
    target_block_seconds: float = 10.0
    retarget_interval: int = 20  # blocks
    initial_difficulty_bits: int = 18
    min_difficulty_bits: int = 8
    max_difficulty_bits: int = 32
    max_txs_per_block: int = 100
