import pytest

from aitoken.config import COIN
from aitoken.crypto import address_from_seed
from aitoken.exchange import Exchange, ExchangeError, notional_cents

from .conftest import make_test_config

ALICE = address_from_seed("test:alice")
BOB = address_from_seed("test:bob")
CAROL = address_from_seed("test:carol")
EXCHANGE_ADDR = address_from_seed("test:exchange")


@pytest.fixture
def ex(tmp_path) -> Exchange:
    cfg = make_test_config(tmp_path)  # fee_percent = 0.5
    return Exchange(cfg=cfg, exchange_address=EXCHANGE_ADDR)


def fund(ex: Exchange, address: str, usd_cents: int = 0, ait: int = 0):
    if usd_cents:
        ex.grant_usd(address, usd_cents)
    if ait:
        ex.ait_available[address] = ex.ait_available.get(address, 0) + ait


def total_usd(ex: Exchange) -> int:
    open_locks = sum(
        o.locked for o in ex.orders.values() if o.side == "buy" and o.status == "open"
    )
    return sum(ex.credits.values()) + open_locks


def total_ait(ex: Exchange) -> int:
    open_locks = sum(
        o.locked for o in ex.orders.values() if o.side == "sell" and o.status == "open"
    )
    return sum(ex.ait_available.values()) + open_locks


def test_exact_match_with_fee_to_owner(ex):
    owner = ex.cfg.owner_address
    fund(ex, ALICE, ait=10 * COIN)
    fund(ex, BOB, usd_cents=200_00)
    ex.place_order(ALICE, "sell", price=10_00, quantity=10 * COIN)  # maker
    ex.place_order(BOB, "buy", price=10_00, quantity=10 * COIN)  # taker

    notional = notional_cents(10_00, 10 * COIN)  # $100.00
    fee = ex.fee_for(notional)  # 0.5% => 50 cents
    assert fee == 50
    assert ex.ait_balance(BOB) == 10 * COIN
    assert ex.usd_balance(ALICE) == notional  # maker pays no fee
    assert ex.usd_balance(BOB) == 200_00 - notional - fee  # taker paid the fee
    assert ex.usd_balance(owner) == fee
    assert ex.total_fees_collected == fee


def test_taker_seller_pays_fee_from_proceeds(ex):
    owner = ex.cfg.owner_address
    fund(ex, BOB, usd_cents=200_00)
    fund(ex, ALICE, ait=10 * COIN)
    ex.place_order(BOB, "buy", price=10_00, quantity=10 * COIN)  # maker
    ex.place_order(ALICE, "sell", price=10_00, quantity=10 * COIN)  # taker

    notional = notional_cents(10_00, 10 * COIN)
    fee = ex.fee_for(notional)
    assert ex.usd_balance(ALICE) == notional - fee
    assert ex.usd_balance(owner) == fee
    # Maker buyer's unused fee allowance was refunded.
    assert ex.usd_balance(BOB) == 200_00 - notional


def test_partial_fill_rests_remainder(ex):
    fund(ex, ALICE, ait=4 * COIN)
    fund(ex, BOB, usd_cents=200_00)
    ex.place_order(ALICE, "sell", price=10_00, quantity=4 * COIN)
    order = ex.place_order(BOB, "buy", price=10_00, quantity=10 * COIN)
    assert order.status == "open"
    assert order.remaining == 6 * COIN
    assert ex.ait_balance(BOB) == 4 * COIN
    book = ex.orderbook()
    assert book["bids"] == [{"price": 10_00, "quantity": 6 * COIN}]
    assert book["asks"] == []


def test_execution_at_resting_price_with_price_time_priority(ex):
    fund(ex, ALICE, ait=5 * COIN)
    fund(ex, CAROL, ait=5 * COIN)
    fund(ex, BOB, usd_cents=500_00)
    ex.place_order(CAROL, "sell", price=11_00, quantity=5 * COIN)
    ex.place_order(ALICE, "sell", price=9_00, quantity=5 * COIN)
    # Bob bids 11.00 — must fill Alice's cheaper ask first, at HER price.
    ex.place_order(BOB, "buy", price=11_00, quantity=5 * COIN)
    assert len(ex.trades) == 1
    assert ex.trades[0].price == 9_00
    assert ex.trades[0].sell_order_id in {
        o.id for o in ex.orders.values() if o.owner == ALICE
    }


def test_cancel_releases_locked_funds(ex):
    fund(ex, BOB, usd_cents=100_00)
    order = ex.place_order(BOB, "buy", price=10_00, quantity=5 * COIN)
    locked = order.locked
    assert ex.usd_balance(BOB) == 100_00 - locked
    ex.cancel_order(order.id, BOB)
    assert ex.usd_balance(BOB) == 100_00
    with pytest.raises(ExchangeError, match="already cancelled"):
        ex.cancel_order(order.id, BOB)


def test_cancel_requires_owner(ex):
    fund(ex, BOB, usd_cents=100_00)
    order = ex.place_order(BOB, "buy", price=10_00, quantity=5 * COIN)
    with pytest.raises(ExchangeError, match="owner"):
        ex.cancel_order(order.id, ALICE)


def test_insufficient_funds_rejected(ex):
    with pytest.raises(ExchangeError, match="insufficient USD"):
        ex.place_order(BOB, "buy", price=10_00, quantity=5 * COIN)
    with pytest.raises(ExchangeError, match="insufficient AIT"):
        ex.place_order(ALICE, "sell", price=10_00, quantity=5 * COIN)


def test_conservation_and_owner_fee_sum_across_trade_sequence(ex):
    """Nothing is created or destroyed by trading; the only flow is fees
    moving to the owner."""
    owner = ex.cfg.owner_address
    fund(ex, ALICE, ait=100 * COIN, usd_cents=50_00)
    fund(ex, BOB, usd_cents=1000_00)
    fund(ex, CAROL, usd_cents=500_00, ait=20 * COIN)
    usd_before, ait_before = total_usd(ex), total_ait(ex)

    ex.place_order(ALICE, "sell", price=10_00, quantity=30 * COIN)
    ex.place_order(BOB, "buy", price=10_00, quantity=10 * COIN)
    ex.place_order(CAROL, "buy", price=10_50, quantity=25 * COIN)
    ex.place_order(ALICE, "sell", price=10_25, quantity=20 * COIN)
    ex.place_order(BOB, "buy", price=12_00, quantity=15 * COIN)
    for order in list(ex.orders.values()):
        if order.status == "open":
            ex.cancel_order(order.id, order.owner)

    assert total_usd(ex) == usd_before
    assert total_ait(ex) == ait_before
    assert len(ex.trades) >= 3
    assert ex.usd_balance(owner) == sum(t.fee for t in ex.trades)
    assert ex.total_fees_collected == sum(t.fee for t in ex.trades)
    summary = ex.fee_summary()
    assert summary["total_fees_collected_cents"] == ex.total_fees_collected
    assert summary["owner_address"] == owner


def test_minimum_fee_is_one_cent(ex):
    fund(ex, ALICE, ait=1 * COIN)
    fund(ex, BOB, usd_cents=10_00)
    # 0.01 AIT at $1.00/AIT => 1 cent notional; 0.5% would round to 0, but
    # the fee floors at 1 cent.
    ex.place_order(ALICE, "sell", price=1_00, quantity=COIN // 100)
    ex.place_order(BOB, "buy", price=1_00, quantity=COIN // 100)
    assert ex.trades and ex.trades[0].fee == 1


def test_sub_cent_dust_order_rejected(ex):
    fund(ex, ALICE, ait=1 * COIN)
    with pytest.raises(ExchangeError, match="too small"):
        ex.place_order(ALICE, "sell", price=1, quantity=COIN // 100)


def test_self_trade_prevented(ex):
    fund(ex, ALICE, ait=10 * COIN, usd_cents=200_00)
    ex.place_order(ALICE, "sell", price=10_00, quantity=5 * COIN)
    order = ex.place_order(ALICE, "buy", price=10_00, quantity=5 * COIN)
    assert not ex.trades
    assert order.status == "open"
