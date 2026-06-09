"""End-to-end API test: mine → deposit → trade → owner collects fees → AI spend."""

import time

import pytest
from fastapi.testclient import TestClient

from aitoken import crypto
from aitoken.api import create_app
from aitoken.block import Block, mine_header
from aitoken.config import COIN
from aitoken.node import Node
from aitoken.wallet import Wallet

from .conftest import make_test_config


@pytest.fixture
def client(tmp_path):
    node = Node(make_test_config(tmp_path))
    with TestClient(create_app(node=node)) as c:
        c.node = node
        yield c


def mine_via_api(client: TestClient, address: str) -> dict:
    """Exercise the real miner protocol: template -> grind -> submit."""
    template = client.get(f"/api/mining/template?address={address}").json()
    block = Block.from_dict(template)
    assert mine_header(block)
    resp = client.post("/api/mining/submit", json=block.to_dict())
    assert resp.status_code == 200, resp.text
    return resp.json()


def signed_order(wallet: Wallet, side: str, price: int, quantity: int) -> dict:
    ts = time.time()
    msg = f"ORDER:{wallet.address}:{side}:{price}:{quantity}:{ts}"
    return {
        "address": wallet.address,
        "side": side,
        "price_cents": price,
        "quantity": quantity,
        "timestamp": ts,
        "public_key": wallet.public_key,
        "signature": wallet.sign_message(msg),
    }


def test_status_and_explorer(client):
    status = client.get("/api/status").json()
    assert status["height"] == 0
    assert status["fee_percent"] == 0.5
    chain = client.get("/api/chain").json()
    assert chain["total"] == 1
    assert client.get("/api/block/0").status_code == 200
    assert client.get("/api/block/999").status_code == 404


def test_mining_flow(client):
    miner = Wallet.create()
    result = mine_via_api(client, miner.address)
    assert result == {"accepted": True, "height": 1, "hash": result["hash"]}
    info = client.get(f"/api/address/{miner.address}").json()
    assert info["balance"] == 50 * COIN
    stats = client.get("/api/mining/stats").json()
    assert stats["blocks_by_miner"][miner.address] == 1


def test_stale_block_gets_409(client):
    miner = Wallet.create()
    template = client.get(f"/api/mining/template?address={miner.address}").json()
    stale = Block.from_dict(template)
    assert mine_header(stale)
    mine_via_api(client, miner.address)  # chain advances first
    resp = client.post("/api/mining/submit", json=stale.to_dict())
    assert resp.status_code == 409


def test_transfer_via_signed_tx(client):
    alice, bob = Wallet.create(), Wallet.create()
    mine_via_api(client, alice.address)
    tx = alice.transfer(bob.address, 5 * COIN, fee=0, nonce=0)
    resp = client.post("/api/tx", json=tx.to_dict())
    assert resp.status_code == 200
    assert client.get("/api/mempool").json()["size"] == 1
    mine_via_api(client, alice.address)
    assert client.get(f"/api/address/{bob.address}").json()["balance"] == 5 * COIN


def test_full_exchange_flow_owner_collects_fee(client):
    """The money path: mine -> deposit -> trade -> the owner keeps the fee."""
    owner_address = client.node.cfg.owner_address
    alice, bob = Wallet.create(), Wallet.create()

    # Alice mines 50 AIT and deposits 20 to the exchange.
    mine_via_api(client, alice.address)
    exchange_addr = client.get("/api/exchange/deposit-address").json()["address"]
    deposit = alice.transfer(exchange_addr, 20 * COIN, fee=0, nonce=0)
    assert client.post("/api/tx", json=deposit.to_dict()).status_code == 200
    mine_via_api(client, alice.address)  # confirm the deposit
    info = client.get(f"/api/address/{alice.address}").json()
    assert info["exchange_ait_available"] == 20 * COIN

    # Bob gets demo USD credits from the faucet.
    resp = client.post(
        "/api/faucet/usd", json={"address": bob.address, "amount_cents": 500_00}
    )
    assert resp.status_code == 200

    # Alice asks $10.00/AIT for 10 AIT; Bob lifts the offer (taker).
    resp = client.post(
        "/api/exchange/orders", json=signed_order(alice, "sell", 10_00, 10 * COIN)
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/api/exchange/orders", json=signed_order(bob, "buy", 10_00, 10 * COIN)
    )
    assert resp.status_code == 200, resp.text

    notional = 100_00  # $100
    fee = 50  # 0.5% of $100
    trades = client.get("/api/exchange/trades").json()["trades"]
    assert len(trades) == 1 and trades[0]["fee"] == fee

    fees = client.get("/api/exchange/fees").json()
    assert fees["owner_address"] == owner_address
    assert fees["total_fees_collected_cents"] == fee
    assert fees["owner_usd_balance_cents"] == fee

    alice_info = client.get(f"/api/address/{alice.address}").json()
    bob_info = client.get(f"/api/address/{bob.address}").json()
    assert alice_info["usd_credits_cents"] == notional  # maker: full proceeds
    assert bob_info["usd_credits_cents"] == 500_00 - notional - fee
    assert bob_info["exchange_ait_available"] == 10 * COIN

    # Bob withdraws his AIT back on-chain.
    ts = time.time()
    msg = f"WITHDRAW:{bob.address}:{5 * COIN}:{ts}"
    resp = client.post(
        "/api/exchange/withdraw",
        json={
            "address": bob.address,
            "amount": 5 * COIN,
            "timestamp": ts,
            "public_key": bob.public_key,
            "signature": bob.sign_message(msg),
        },
    )
    assert resp.status_code == 200, resp.text
    mine_via_api(client, alice.address)  # confirm the withdrawal tx
    assert client.get(f"/api/address/{bob.address}").json()["balance"] == 5 * COIN


def test_order_auth_rejected_on_bad_signature(client):
    alice = Wallet.create()
    client.post("/api/faucet/usd", json={"address": alice.address, "amount_cents": 100_00})
    order = signed_order(alice, "buy", 10_00, 1 * COIN)
    order["signature"] = order["signature"][:-2] + "00"
    assert client.post("/api/exchange/orders", json=order).status_code == 401


def test_cancel_order_via_api(client):
    alice = Wallet.create()
    client.post("/api/faucet/usd", json={"address": alice.address, "amount_cents": 100_00})
    order = client.post(
        "/api/exchange/orders", json=signed_order(alice, "buy", 5_00, 1 * COIN)
    ).json()
    resp = client.request(
        "DELETE",
        f"/api/exchange/orders/{order['id']}",
        json={
            "address": alice.address,
            "public_key": alice.public_key,
            "signature": alice.sign_message(f"CANCEL:{order['id']}"),
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_ai_spend_flow(client):
    alice = Wallet.create()
    mine_via_api(client, alice.address)
    providers = client.get("/api/ai/providers").json()
    assert {"claude", "gemini", "chatgpt", "grok"} <= set(providers)

    resp = client.post(
        "/api/ai/spend",
        json={
            "provider": "claude",
            "model_tokens": 100_000,
            "private_key": alice.private_key,
        },
    )
    assert resp.status_code == 200, resp.text
    receipt = resp.json()
    assert receipt["status"] == "simulated"
    mine_via_api(client, alice.address)  # confirm the spend

    providers = client.get("/api/ai/providers").json()
    assert providers["claude"]["total_ait_spent"] == receipt["cost"]
    sink = providers["claude"]["sink_address"]
    assert sink == crypto.address_from_seed("AI_PROVIDER:claude")


def test_wallet_new_and_sign_and_send(client):
    w = client.post("/api/wallet/new").json()
    assert crypto.is_valid_address(w["address"])
    miner = Wallet.create()
    mine_via_api(client, miner.address)
    resp = client.post(
        "/api/wallet/sign-and-send",
        json={
            "private_key": miner.private_key,
            "recipient": w["address"],
            "amount": 1 * COIN,
            "fee": 0,
        },
    )
    assert resp.status_code == 200
    mine_via_api(client, miner.address)
    assert client.get(f"/api/address/{w['address']}").json()["balance"] == 1 * COIN
