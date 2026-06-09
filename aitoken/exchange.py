"""Built-in exchange: AIT / simulated-USD order book with owner fee accrual.

Custody model (like a real centralized exchange): users deposit AIT by
sending an ordinary on-chain transaction to the exchange's address; once it
confirms in a block their internal trading balance is credited. USD is a
simulated off-chain credits ledger (cents), funded by a demo faucet.

Matching is price-time priority; fills execute at the resting order's
price. The TAKER pays the trade fee (fee_percent of the notional, minimum
1 cent), and every fee is credited to the platform owner's USD balance.
"""

import itertools
import time
import uuid
from dataclasses import dataclass, field

from .block import Block
from .config import COIN, NodeConfig


class ExchangeError(Exception):
    pass


def notional_cents(price_cents_per_ait: int, quantity_base_units: int) -> int:
    # Floor rounding: the sum of per-fill notionals can never exceed the
    # notional of the whole order, so locked funds always cover the fills.
    return price_cents_per_ait * quantity_base_units // COIN


@dataclass
class Order:
    id: str
    owner: str
    side: str  # "buy" | "sell"
    price: int  # USD cents per whole AIT
    quantity: int  # AIT base units
    remaining: int
    status: str  # "open" | "filled" | "cancelled"
    created_at: float
    locked: int = 0  # remaining locked USD cents (buy) or AIT base units (sell)
    seq: int = 0  # tiebreaker for time priority

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "owner": self.owner,
            "side": self.side,
            "price": self.price,
            "quantity": self.quantity,
            "remaining": self.remaining,
            "status": self.status,
            "created_at": self.created_at,
            "locked": self.locked,
            "seq": self.seq,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        return cls(**d)


@dataclass
class Trade:
    id: str
    buy_order_id: str
    sell_order_id: str
    price: int  # cents per whole AIT (resting order's price)
    quantity: int  # base units
    fee: int  # cents, paid by the taker, credited to the owner
    taker: str  # taker's address
    timestamp: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "buy_order_id": self.buy_order_id,
            "sell_order_id": self.sell_order_id,
            "price": self.price,
            "quantity": self.quantity,
            "fee": self.fee,
            "taker": self.taker,
            "timestamp": self.timestamp,
        }


@dataclass
class Exchange:
    cfg: NodeConfig
    exchange_address: str
    # USD credits in cents. Buy-order locks live inside Order.locked.
    credits: dict[str, int] = field(default_factory=dict)
    # AIT trading balances in base units (deposited custody).
    ait_available: dict[str, int] = field(default_factory=dict)
    orders: dict[str, Order] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    total_fees_collected: int = 0
    processed_deposit_txids: set[str] = field(default_factory=set)
    _seq: itertools.count = field(default_factory=itertools.count)

    @property
    def fee_bps(self) -> int:
        return int(round(self.cfg.fee_percent * 100))

    def fee_for(self, notional: int) -> int:
        if notional <= 0:
            return 0
        return max(1, -(-notional * self.fee_bps // 10000))  # ceil, min 1 cent

    # ----------------------------------------------------------- balances

    def usd_balance(self, address: str) -> int:
        return self.credits.get(address, 0)

    def ait_balance(self, address: str) -> int:
        return self.ait_available.get(address, 0)

    def locked_usd(self, address: str) -> int:
        return sum(
            o.locked for o in self.orders.values()
            if o.owner == address and o.side == "buy" and o.status == "open"
        )

    def locked_ait(self, address: str) -> int:
        return sum(
            o.locked for o in self.orders.values()
            if o.owner == address and o.side == "sell" and o.status == "open"
        )

    def grant_usd(self, address: str, cents: int) -> None:
        if cents <= 0:
            raise ExchangeError("faucet amount must be positive")
        self.credits[address] = self.credits.get(address, 0) + cents

    # ----------------------------------------------------------- deposits

    def process_confirmed_block(self, block: Block) -> int:
        """Credit AIT trading balances for confirmed deposits to the exchange
        address. Returns the number of deposits processed."""
        count = 0
        for tx in block.transactions:
            if tx.is_coinbase or tx.recipient != self.exchange_address:
                continue
            if tx.txid in self.processed_deposit_txids:
                continue
            self.processed_deposit_txids.add(tx.txid)
            self.ait_available[tx.sender] = self.ait_available.get(tx.sender, 0) + tx.amount
            count += 1
        return count

    def withdraw(self, address: str, amount: int) -> None:
        """Deduct from the trading balance (the caller sends the on-chain tx)."""
        if amount <= 0:
            raise ExchangeError("withdrawal amount must be positive")
        if self.ait_available.get(address, 0) < amount:
            raise ExchangeError("insufficient AIT trading balance")
        self.ait_available[address] -= amount

    # ------------------------------------------------------------- orders

    def place_order(self, owner: str, side: str, price: int, quantity: int) -> Order:
        if side not in ("buy", "sell"):
            raise ExchangeError("side must be 'buy' or 'sell'")
        if price <= 0 or quantity <= 0:
            raise ExchangeError("price and quantity must be positive")
        if notional_cents(price, quantity) < 1:
            raise ExchangeError("order too small: notional is below one cent")
        order = Order(
            id=uuid.uuid4().hex,
            owner=owner,
            side=side,
            price=price,
            quantity=quantity,
            remaining=quantity,
            status="open",
            created_at=time.time(),
            seq=next(self._seq),
        )
        if side == "buy":
            cost = notional_cents(price, quantity)
            lock = cost + self.fee_for(cost)
            if self.credits.get(owner, 0) < lock:
                raise ExchangeError("insufficient USD credits (cost plus taker fee allowance)")
            self.credits[owner] -= lock
            order.locked = lock
        else:
            if self.ait_available.get(owner, 0) < quantity:
                raise ExchangeError("insufficient AIT trading balance (deposit first)")
            self.ait_available[owner] -= quantity
            order.locked = quantity
        self.orders[order.id] = order
        self._match(order)
        return order

    def cancel_order(self, order_id: str, owner: str) -> Order:
        order = self.orders.get(order_id)
        if order is None:
            raise ExchangeError("no such order")
        if order.owner != owner:
            raise ExchangeError("only the order's owner can cancel it")
        if order.status != "open":
            raise ExchangeError(f"order is already {order.status}")
        order.status = "cancelled"
        self._release_lock(order)
        return order

    def _release_lock(self, order: Order) -> None:
        if order.locked > 0:
            if order.side == "buy":
                self.credits[order.owner] = self.credits.get(order.owner, 0) + order.locked
            else:
                self.ait_available[order.owner] = (
                    self.ait_available.get(order.owner, 0) + order.locked
                )
            order.locked = 0

    # ----------------------------------------------------------- matching

    def _book(self, side: str) -> list[Order]:
        orders = [o for o in self.orders.values() if o.side == side and o.status == "open"]
        if side == "buy":
            orders.sort(key=lambda o: (-o.price, o.seq))  # best bid first
        else:
            orders.sort(key=lambda o: (o.price, o.seq))  # best ask first
        return orders

    def _match(self, taker: Order) -> None:
        while taker.remaining > 0:
            book = self._book("sell" if taker.side == "buy" else "buy")
            book = [o for o in book if o.id != taker.id and o.owner != taker.owner]
            if not book:
                break
            maker = book[0]
            crosses = (
                maker.price <= taker.price if taker.side == "buy" else maker.price >= taker.price
            )
            if not crosses:
                break
            qty = min(taker.remaining, maker.remaining)
            price = maker.price  # execute at the resting order's price
            n = notional_cents(price, qty)
            fee = self.fee_for(n)

            buy, sell = (taker, maker) if taker.side == "buy" else (maker, taker)

            # Buyer pays the notional from locked funds (taker buyer also
            # pays the fee from its lock, which included a fee allowance).
            buyer_charge = n + (fee if buy is taker else 0)
            if buy.locked < buyer_charge:
                # Can only happen to a taker buyer whose fee allowance was
                # eaten by minimum fees across many dust fills: stop matching
                # and let the remainder rest.
                break
            buy.locked -= buyer_charge
            # Seller hands over AIT from its lock; taker seller pays the fee
            # out of the USD proceeds.
            sell.locked -= qty
            self.ait_available[buy.owner] = self.ait_available.get(buy.owner, 0) + qty
            seller_proceeds = n - (fee if sell is taker else 0)
            self.credits[sell.owner] = self.credits.get(sell.owner, 0) + seller_proceeds

            # Every fee lands in the platform owner's wallet.
            self.credits[self.cfg.owner_address] = (
                self.credits.get(self.cfg.owner_address, 0) + fee
            )
            self.total_fees_collected += fee

            for order in (buy, sell):
                order.remaining -= qty
                if order.remaining == 0:
                    order.status = "filled"
                    self._release_lock(order)

            self.trades.append(
                Trade(
                    id=uuid.uuid4().hex,
                    buy_order_id=buy.id,
                    sell_order_id=sell.id,
                    price=price,
                    quantity=qty,
                    fee=fee,
                    taker=taker.owner,
                    timestamp=time.time(),
                )
            )

    # ------------------------------------------------------------ queries

    def orderbook(self, depth: int = 25) -> dict:
        def aggregate(orders: list[Order]) -> list[dict]:
            levels: dict[int, int] = {}
            for o in orders:
                levels[o.price] = levels.get(o.price, 0) + o.remaining
            return [{"price": p, "quantity": q} for p, q in list(levels.items())[:depth]]

        return {"bids": aggregate(self._book("buy")), "asks": aggregate(self._book("sell"))}

    def open_orders(self, address: str) -> list[Order]:
        return [o for o in self.orders.values() if o.owner == address and o.status == "open"]

    def recent_trades(self, limit: int = 50) -> list[Trade]:
        return self.trades[-limit:][::-1]

    def fee_summary(self, limit: int = 50) -> dict:
        fee_events = [t.to_dict() for t in self.trades if t.fee > 0][-limit:][::-1]
        return {
            "owner_address": self.cfg.owner_address,
            "fee_percent": self.cfg.fee_percent,
            "total_fees_collected_cents": self.total_fees_collected,
            "owner_usd_balance_cents": self.usd_balance(self.cfg.owner_address),
            "trade_count": len(self.trades),
            "recent_fee_events": fee_events,
        }
