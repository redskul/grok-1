import pytest

from aitoken.block import mine_header
from aitoken.blockchain import Blockchain, BlockValidationError, make_genesis_block
from aitoken.config import COIN
from aitoken.transaction import Transaction

from .conftest import make_test_config, mine_block


def test_genesis_is_deterministic(cfg):
    assert make_genesis_block().header_hash() == make_genesis_block().header_hash()
    chain_a, chain_b = Blockchain(cfg), Blockchain(cfg)
    assert chain_a.tip.header_hash() == chain_b.tip.header_hash()
    assert chain_a.height == 0


def test_reward_halving_schedule(cfg):
    chain = Blockchain(cfg)
    h = cfg.halving_interval
    assert chain.block_reward(0) == 50 * COIN
    assert chain.block_reward(h - 1) == 50 * COIN
    assert chain.block_reward(h) == 25 * COIN
    assert chain.block_reward(2 * h) == 25 * COIN // 2
    assert chain.block_reward(64 * h) == 0  # fully emitted


def test_mining_credits_reward(cfg, wallet):
    chain = Blockchain(cfg)
    mine_block(chain, wallet.address)
    assert chain.height == 1
    assert chain.balance_of(wallet.address) == 50 * COIN


def test_transfer_moves_balance_and_fees_go_to_miner(cfg, wallet, wallet2):
    chain = Blockchain(cfg)
    mine_block(chain, wallet.address)
    tx = wallet.transfer(wallet2.address, 10 * COIN, fee=5, nonce=0)
    block = mine_block(chain, wallet2.address, [tx])
    assert block.transactions[0].amount == 50 * COIN + 5  # reward + tx fee
    assert chain.balance_of(wallet.address) == 40 * COIN - 5
    assert chain.balance_of(wallet2.address) == 10 * COIN + 50 * COIN + 5


def test_overdrawn_transaction_rejected(cfg, wallet, wallet2):
    chain = Blockchain(cfg)
    mine_block(chain, wallet.address)
    tx = wallet.transfer(wallet2.address, 1000 * COIN, fee=0, nonce=0)
    block = chain.build_block_template(wallet.address, [tx])
    mine_header(block)
    with pytest.raises(BlockValidationError, match="insufficient balance"):
        chain.append_block(block)


def test_replayed_nonce_rejected(cfg, wallet, wallet2):
    chain = Blockchain(cfg)
    mine_block(chain, wallet.address)
    tx = wallet.transfer(wallet2.address, 1 * COIN, fee=0, nonce=0)
    mine_block(chain, wallet.address, [tx])
    # Same nonce again: classic double-spend replay across blocks.
    replay = wallet.transfer(wallet2.address, 1 * COIN, fee=0, nonce=0)
    block = chain.build_block_template(wallet.address, [replay])
    mine_header(block)
    with pytest.raises(BlockValidationError, match="bad nonce"):
        chain.append_block(block)


def test_bad_pow_rejected(cfg, wallet):
    chain = Blockchain(cfg)
    block = chain.build_block_template(wallet.address, [])
    mine_header(block)
    block.nonce += 1  # almost certainly breaks the PoW
    if block.meets_difficulty():
        pytest.skip("astronomically unlucky nonce collision")
    with pytest.raises(BlockValidationError, match="proof of work"):
        chain.append_block(block)


def test_stale_block_rejected(cfg, wallet):
    chain = Blockchain(cfg)
    stale = chain.build_block_template(wallet.address, [])
    mine_header(stale)
    mine_block(chain, wallet.address)
    with pytest.raises(BlockValidationError, match="stale"):
        chain.append_block(stale)


def test_oversized_coinbase_rejected(cfg, wallet):
    chain = Blockchain(cfg)
    block = chain.build_block_template(wallet.address, [])
    block.transactions[0] = Transaction.coinbase(wallet.address, 51 * COIN, height=1)
    block.merkle_root = block.compute_merkle_root()
    mine_header(block)
    with pytest.raises(BlockValidationError, match="coinbase"):
        chain.append_block(block)


def test_tampered_merkle_root_rejected(cfg, wallet):
    chain = Blockchain(cfg)
    block = chain.build_block_template(wallet.address, [])
    mine_header(block)
    block.transactions[0].amount += 1  # mutate tx without rebuilding the root
    with pytest.raises(BlockValidationError, match="merkle"):
        chain.append_block(block)


def test_difficulty_retargets_up_when_blocks_too_fast(tmp_path, wallet):
    cfg = make_test_config(tmp_path, retarget_interval=5, target_block_seconds=1000.0)
    chain = Blockchain(cfg)
    # First retarget window starts after one full interval (the genesis-anchored
    # window is skipped), so mine two intervals. Instant mining => far faster
    # than the 1000s target => difficulty rises.
    for _ in range(10):
        mine_block(chain, wallet.address)
    assert chain.current_difficulty() == cfg.initial_difficulty_bits + 1


def test_difficulty_retargets_down_when_blocks_too_slow(tmp_path, wallet):
    cfg = make_test_config(tmp_path, retarget_interval=5, target_block_seconds=0.000001)
    chain = Blockchain(cfg)
    for _ in range(10):
        mine_block(chain, wallet.address)
    assert chain.current_difficulty() == cfg.initial_difficulty_bits - 1


def test_full_chain_revalidation_roundtrip(cfg, wallet, wallet2):
    chain = Blockchain(cfg)
    mine_block(chain, wallet.address)
    tx = wallet.transfer(wallet2.address, 3 * COIN, fee=2, nonce=0)
    mine_block(chain, wallet.address, [tx])
    rebuilt = Blockchain.from_blocks(cfg, chain.blocks)
    assert rebuilt.height == chain.height
    assert rebuilt.balances == chain.balances
    assert rebuilt.nonces == chain.nonces
