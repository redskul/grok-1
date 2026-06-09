import pytest

from aitoken.blockchain import Blockchain
from aitoken.config import COIN
from aitoken.mempool import Mempool, MempoolError
from aitoken.transaction import Transaction

from .conftest import mine_block


@pytest.fixture
def funded_chain(cfg, wallet):
    chain = Blockchain(cfg)
    mine_block(chain, wallet.address)  # wallet now holds 50 AIT
    return chain


def test_add_and_select(funded_chain, wallet, wallet2):
    pool = Mempool()
    tx = wallet.transfer(wallet2.address, 1 * COIN, fee=10, nonce=0)
    pool.add(tx, funded_chain)
    assert pool.select(10) == [tx]


def test_duplicate_rejected(funded_chain, wallet, wallet2):
    pool = Mempool()
    tx = wallet.transfer(wallet2.address, 1 * COIN, fee=0, nonce=0)
    pool.add(tx, funded_chain)
    with pytest.raises(MempoolError, match="duplicate"):
        pool.add(tx, funded_chain)


def test_coinbase_rejected(funded_chain, wallet):
    pool = Mempool()
    with pytest.raises(MempoolError, match="coinbase"):
        pool.add(Transaction.coinbase(wallet.address, 50, 1), funded_chain)


def test_pending_balance_accounted(funded_chain, wallet, wallet2):
    pool = Mempool()
    pool.add(wallet.transfer(wallet2.address, 30 * COIN, fee=0, nonce=0), funded_chain)
    # Only 20 AIT left once the pending tx is counted.
    with pytest.raises(MempoolError, match="insufficient balance"):
        pool.add(wallet.transfer(wallet2.address, 30 * COIN, fee=0, nonce=1), funded_chain)
    pool.add(wallet.transfer(wallet2.address, 20 * COIN, fee=0, nonce=1), funded_chain)


def test_nonce_gap_rejected(funded_chain, wallet, wallet2):
    pool = Mempool()
    with pytest.raises(MempoolError, match="bad nonce"):
        pool.add(wallet.transfer(wallet2.address, 1 * COIN, fee=0, nonce=5), funded_chain)


def test_fee_priority_selection_preserves_sender_order(cfg, wallet, wallet2):
    chain = Blockchain(cfg)
    mine_block(chain, wallet.address)
    mine_block(chain, wallet2.address)
    pool = Mempool()
    low_then_high = [
        wallet.transfer(wallet2.address, 1 * COIN, fee=1, nonce=0),
        wallet.transfer(wallet2.address, 1 * COIN, fee=100, nonce=1),
    ]
    rich_fee = wallet2.transfer(wallet.address, 1 * COIN, fee=50, nonce=0)
    for tx in low_then_high + [rich_fee]:
        pool.add(tx, chain)
    selected = pool.select(10)
    # wallet2's fee-50 tx outranks wallet's fee-1 head, but wallet's fee-100
    # tx can never jump ahead of its own nonce-0 predecessor.
    assert selected.index(rich_fee) < selected.index(low_then_high[1])
    assert selected.index(low_then_high[0]) < selected.index(low_then_high[1])


def test_purge_confirmed_drops_invalidated(funded_chain, wallet, wallet2):
    pool = Mempool()
    tx = wallet.transfer(wallet2.address, 1 * COIN, fee=0, nonce=0)
    pool.add(tx, funded_chain)
    block = mine_block(funded_chain, wallet.address, [tx])
    pool.purge_confirmed({t.txid for t in block.transactions}, funded_chain)
    assert len(pool) == 0
