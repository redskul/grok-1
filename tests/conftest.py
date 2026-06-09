import pytest

from aitoken.block import mine_header
from aitoken.config import NodeConfig
from aitoken.wallet import Wallet

# Trivial difficulty so test mining takes microseconds.
TEST_BITS = 8


def make_test_config(tmp_path, **overrides) -> NodeConfig:
    owner = overrides.pop("owner_wallet", None) or Wallet.create()
    cfg = NodeConfig(
        db_path=str(tmp_path / "aitoken-test.db"),
        owner_address=owner.address,
        owner_wallet_path=str(tmp_path / "owner_wallet.json"),
    )
    cfg.initial_difficulty_bits = TEST_BITS
    cfg.min_difficulty_bits = 1
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


@pytest.fixture
def cfg(tmp_path) -> NodeConfig:
    return make_test_config(tmp_path)


@pytest.fixture
def wallet() -> Wallet:
    return Wallet.create()


@pytest.fixture
def wallet2() -> Wallet:
    return Wallet.create()


def mine_block(chain, miner_address, mempool_txs=()):
    """Build, solve, and append the next block; returns it."""
    block = chain.build_block_template(miner_address, list(mempool_txs))
    assert mine_header(block)
    chain.append_block(block)
    return block
