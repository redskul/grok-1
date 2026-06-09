import json
import sqlite3

import pytest

from aitoken.blockchain import BlockValidationError
from aitoken.config import COIN
from aitoken.node import Node
from aitoken.persistence import Storage

from .conftest import mine_block


def test_chain_and_exchange_survive_restart(cfg, wallet, wallet2):
    node = Node(cfg)
    mine_block(node.chain, wallet.address)
    node.storage.save_block(node.chain.tip)
    node.faucet_usd(wallet2.address, 100_00)
    node.exchange.ait_available[wallet.address] = 5 * COIN
    node._save_exchange()
    height, balances = node.chain.height, dict(node.chain.balances)
    node.storage.close()

    reopened = Node(cfg)
    assert reopened.chain.height == height
    assert reopened.chain.balances == balances
    assert reopened.exchange.usd_balance(wallet2.address) == 100_00
    assert reopened.exchange.ait_balance(wallet.address) == 5 * COIN
    # The same exchange wallet (custody key) must come back.
    assert reopened.exchange_wallet.address == node.exchange_wallet.address
    reopened.storage.close()


def test_orders_and_fees_survive_restart(cfg, wallet, wallet2):
    node = Node(cfg)
    node.faucet_usd(wallet.address, 500_00)
    node.exchange.ait_available[wallet2.address] = 10 * COIN
    node.exchange.place_order(wallet2.address, "sell", 10_00, 5 * COIN)
    node.exchange.place_order(wallet.address, "buy", 10_00, 2 * COIN)
    node._save_exchange()
    fees = node.exchange.total_fees_collected
    assert fees > 0
    node.storage.close()

    reopened = Node(cfg)
    assert reopened.exchange.total_fees_collected == fees
    assert len(reopened.exchange.trades) == 1
    open_orders = reopened.exchange.open_orders(wallet2.address)
    assert len(open_orders) == 1 and open_orders[0].remaining == 3 * COIN
    # The reloaded book still matches: fill the resting remainder.
    reopened.exchange.place_order(wallet.address, "buy", 10_00, 3 * COIN)
    assert len(reopened.exchange.trades) == 2
    reopened.storage.close()


def test_tampered_block_detected_on_load(cfg, wallet):
    node = Node(cfg)
    mine_block(node.chain, wallet.address)
    node.storage.save_block(node.chain.tip)
    node.storage.close()

    conn = sqlite3.connect(cfg.db_path)
    row = conn.execute("SELECT data_json FROM blocks WHERE height = 1").fetchone()
    data = json.loads(row[0])
    data["transactions"][0]["amount"] = 9_999 * COIN  # inflate the reward
    conn.execute(
        "UPDATE blocks SET data_json = ? WHERE height = 1", (json.dumps(data),)
    )
    conn.commit()
    conn.close()

    with pytest.raises(BlockValidationError):
        Node(cfg)


def test_explorer_queries(cfg, wallet, wallet2):
    storage = Storage(cfg.db_path)
    node = Node(cfg, storage=storage)
    mine_block(node.chain, wallet.address)
    node.storage.save_block(node.chain.tip)
    tx = wallet.transfer(wallet2.address, 1 * COIN, fee=0, nonce=0)
    mine_block(node.chain, wallet.address, [tx])
    node.storage.save_block(node.chain.tip)

    assert storage.find_tx_height(tx.txid) == 2
    history = storage.txs_for_address(wallet2.address)
    assert any(h["txid"] == tx.txid for h in history)
    storage.close()
