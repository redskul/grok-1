"""Node facade: ties together chain, mempool, exchange, and storage.

All mutating operations take a single coarse RLock — FastAPI handlers run
in a threadpool, and correctness beats throughput for this platform.
"""

import itertools
import json
import threading
import time

from . import ai_spend, crypto
from .block import Block
from .blockchain import Blockchain
from .config import NodeConfig
from .exchange import Exchange, Order, Trade
from .mempool import Mempool, MempoolError
from .persistence import Storage
from .transaction import Transaction
from .wallet import Wallet

AUTH_WINDOW_SECONDS = 300.0


class AuthError(Exception):
    pass


class Node:
    def __init__(self, config: NodeConfig, storage: Storage | None = None):
        self.cfg = config
        self.lock = threading.RLock()
        self.storage = storage or Storage(config.db_path)
        self.mempool = Mempool()

        self._init_owner()
        self._init_exchange_wallet()
        self._load_chain()
        self._load_exchange()

    # ---------------------------------------------------------------- init

    def _init_owner(self) -> None:
        if self.cfg.owner_address:
            return
        stored = self.storage.get_meta("owner_address")
        if stored:
            self.cfg.owner_address = stored
            return
        wallet = Wallet.create()
        wallet.save(self.cfg.owner_wallet_path)
        self.cfg.owner_address = wallet.address
        self.storage.set_meta("owner_address", wallet.address)
        print(f"[aitoken] Generated platform owner wallet: {wallet.address}")
        print(f"[aitoken] Owner keyfile saved to {self.cfg.owner_wallet_path} — keep it safe.")

    def _init_exchange_wallet(self) -> None:
        stored = self.storage.get_meta("exchange_wallet")
        if stored:
            d = json.loads(stored)
            self.exchange_wallet = Wallet(
                private_key=d["private_key"],
                public_key=d["public_key"],
                address=d["address"],
            )
        else:
            self.exchange_wallet = Wallet.create()
            self.storage.set_meta(
                "exchange_wallet",
                json.dumps(
                    {
                        "private_key": self.exchange_wallet.private_key,
                        "public_key": self.exchange_wallet.public_key,
                        "address": self.exchange_wallet.address,
                    }
                ),
            )

    def _load_chain(self) -> None:
        blocks = self.storage.load_blocks()
        if blocks:
            self.chain = Blockchain.from_blocks(self.cfg, blocks)
        else:
            self.chain = Blockchain(self.cfg)
            self.storage.save_block(self.chain.tip)

    def _load_exchange(self) -> None:
        self.exchange = Exchange(cfg=self.cfg, exchange_address=self.exchange_wallet.address)
        snapshot = self.storage.get_meta("exchange_state")
        if not snapshot:
            return
        d = json.loads(snapshot)
        self.exchange.credits = {k: int(v) for k, v in d["credits"].items()}
        self.exchange.ait_available = {k: int(v) for k, v in d["ait_available"].items()}
        self.exchange.orders = {k: Order.from_dict(v) for k, v in d["orders"].items()}
        self.exchange.trades = [Trade(**t) for t in d["trades"]]
        self.exchange.total_fees_collected = int(d["total_fees_collected"])
        self.exchange.processed_deposit_txids = set(d["processed_deposit_txids"])
        self.exchange._seq = itertools.count(int(d.get("seq", len(self.exchange.orders))))

    def _save_exchange(self) -> None:
        snapshot = {
            "credits": self.exchange.credits,
            "ait_available": self.exchange.ait_available,
            "orders": {k: o.to_dict() for k, o in self.exchange.orders.items()},
            "trades": [t.to_dict() for t in self.exchange.trades],
            "total_fees_collected": self.exchange.total_fees_collected,
            "processed_deposit_txids": sorted(self.exchange.processed_deposit_txids),
            "seq": next(self.exchange._seq),
        }
        self.storage.set_meta("exchange_state", json.dumps(snapshot))

    # ---------------------------------------------------------------- auth

    @staticmethod
    def _verify_signed_request(
        message: str, public_key: str, signature: str, address: str
    ) -> None:
        if crypto.address_from_pubkey(public_key) != address:
            raise AuthError("public key does not match address")
        if not crypto.verify(public_key, signature, message.encode()):
            raise AuthError("bad signature")

    @staticmethod
    def _check_fresh(timestamp: float) -> None:
        if abs(time.time() - timestamp) > AUTH_WINDOW_SECONDS:
            raise AuthError("request timestamp outside the allowed window")

    # --------------------------------------------------------------- chain

    def status(self) -> dict:
        with self.lock:
            return {
                "height": self.chain.height,
                "tip_hash": self.chain.tip.header_hash(),
                "difficulty_bits": self.chain.current_difficulty(),
                "block_reward": self.chain.block_reward(self.chain.height + 1),
                "next_halving_height": (
                    (self.chain.height // self.cfg.halving_interval + 1)
                    * self.cfg.halving_interval
                ),
                "mempool_size": len(self.mempool),
                "estimated_hashrate": self._estimate_hashrate(),
                "owner_address": self.cfg.owner_address,
                "exchange_address": self.exchange_wallet.address,
                "fee_percent": self.cfg.fee_percent,
            }

    def _estimate_hashrate(self, window: int = 10) -> float:
        blocks = self.chain.blocks[-(window + 1) :]
        if len(blocks) < 2:
            return 0.0
        elapsed = max(blocks[-1].timestamp - blocks[0].timestamp, 1e-9)
        hashes = sum(float(2**b.difficulty_bits) for b in blocks[1:])
        return hashes / elapsed

    def get_blocks(self, offset: int = 0, limit: int = 20) -> dict:
        with self.lock:
            total = self.chain.height + 1
            # offset counts back from the tip (explorer shows newest first).
            start = max(total - offset - limit, 0)
            end = total - offset
            blocks = self.chain.blocks[start:end][::-1] if end > 0 else []
            return {"total": total, "blocks": [b.to_dict() for b in blocks]}

    def get_block(self, ref: str) -> dict | None:
        with self.lock:
            if ref.isdigit() and int(ref) <= self.chain.height:
                return self.chain.blocks[int(ref)].to_dict()
            for b in self.chain.blocks:
                if b.header_hash() == ref:
                    return b.to_dict()
            return None

    def get_tx(self, txid: str) -> dict | None:
        with self.lock:
            height = self.storage.find_tx_height(txid)
            if height is not None:
                for tx in self.chain.blocks[height].transactions:
                    if tx.txid == txid:
                        d = tx.to_dict()
                        d.update(txid=txid, height=height, confirmed=True)
                        return d
            tx = self.mempool.txs.get(txid)
            if tx:
                d = tx.to_dict()
                d.update(txid=txid, height=None, confirmed=False)
                return d
            return None

    def address_info(self, address: str) -> dict:
        with self.lock:
            return {
                "address": address,
                "balance": self.chain.balance_of(address),
                "nonce": self.chain.nonce_of(address),
                "usd_credits_cents": self.exchange.usd_balance(address),
                "usd_locked_cents": self.exchange.locked_usd(address),
                "exchange_ait_available": self.exchange.ait_balance(address),
                "exchange_ait_locked": self.exchange.locked_ait(address),
                "recent_transactions": self.storage.txs_for_address(address),
            }

    def submit_tx(self, tx: Transaction) -> str:
        with self.lock:
            self.mempool.add(tx, self.chain)
            return tx.txid

    def next_nonce(self, address: str) -> int:
        with self.lock:
            pending = [tx for tx in self.mempool.txs.values() if tx.sender == address]
            return self.chain.nonce_of(address) + len(pending)

    # -------------------------------------------------------------- mining

    def get_block_template(self, miner_address: str) -> dict:
        if not crypto.is_valid_address(miner_address):
            raise ValueError("invalid miner address")
        with self.lock:
            txs = self.mempool.select(self.cfg.max_txs_per_block)
            block = self.chain.build_block_template(miner_address, txs)
            d = block.to_dict()
            d["reward"] = block.transactions[0].amount
            return d

    def submit_block(self, block: Block) -> dict:
        with self.lock:
            self.chain.append_block(block)
            self.storage.save_block(block)
            confirmed = {tx.txid for tx in block.transactions}
            self.mempool.purge_confirmed(confirmed, self.chain)
            deposits = self.exchange.process_confirmed_block(block)
            if deposits:
                self._save_exchange()
            return {"accepted": True, "height": block.index, "hash": block.header_hash()}

    def mining_stats(self) -> dict:
        with self.lock:
            miners: dict[str, int] = {}
            for b in self.chain.blocks[1:]:
                addr = b.transactions[0].recipient
                miners[addr] = miners.get(addr, 0) + 1
            recent = self.chain.blocks[-20:]
            intervals = [
                round(recent[i].timestamp - recent[i - 1].timestamp, 2)
                for i in range(1, len(recent))
            ]
            return {
                "blocks_by_miner": dict(
                    sorted(miners.items(), key=lambda kv: -kv[1])[:20]
                ),
                "recent_block_intervals": intervals,
                "difficulty_history": [b.difficulty_bits for b in self.chain.blocks[-50:]],
            }

    # ------------------------------------------------------------ exchange

    def faucet_usd(self, address: str, cents: int) -> dict:
        with self.lock:
            self.exchange.grant_usd(address, cents)
            self._save_exchange()
            return {"address": address, "usd_credits_cents": self.exchange.usd_balance(address)}

    def place_order(
        self,
        address: str,
        side: str,
        price: int,
        quantity: int,
        timestamp: float,
        public_key: str,
        signature: str,
    ) -> dict:
        message = f"ORDER:{address}:{side}:{price}:{quantity}:{timestamp}"
        self._check_fresh(timestamp)
        self._verify_signed_request(message, public_key, signature, address)
        with self.lock:
            order = self.exchange.place_order(address, side, price, quantity)
            self._save_exchange()
            return order.to_dict()

    def cancel_order(
        self, order_id: str, address: str, public_key: str, signature: str
    ) -> dict:
        self._verify_signed_request(f"CANCEL:{order_id}", public_key, signature, address)
        with self.lock:
            order = self.exchange.cancel_order(order_id, address)
            self._save_exchange()
            return order.to_dict()

    def withdraw(
        self, address: str, amount: int, timestamp: float, public_key: str, signature: str
    ) -> dict:
        message = f"WITHDRAW:{address}:{amount}:{timestamp}"
        self._check_fresh(timestamp)
        self._verify_signed_request(message, public_key, signature, address)
        with self.lock:
            self.exchange.withdraw(address, amount)
            try:
                tx = self.exchange_wallet.transfer(
                    recipient=address,
                    amount=amount,
                    fee=0,
                    nonce=self.next_nonce(self.exchange_wallet.address),
                    memo="EXCHANGE_WITHDRAWAL",
                )
                self.mempool.add(tx, self.chain)
            except MempoolError:
                # Roll back the ledger deduction if the on-chain tx failed.
                self.exchange.ait_available[address] = (
                    self.exchange.ait_available.get(address, 0) + amount
                )
                raise
            finally:
                self._save_exchange()
            return {"txid": tx.txid, "amount": amount, "status": "pending_confirmation"}

    # ------------------------------------------------------------ ai spend

    def spend_on_ai(
        self, provider_id: str, model_tokens: int, tx: Transaction
    ) -> dict:
        cost = ai_spend.quote(provider_id, model_tokens)
        sink = ai_spend.provider_address(provider_id)
        if tx.recipient != sink:
            raise ai_spend.SpendError(f"transaction must pay the provider sink {sink}")
        if tx.amount < cost:
            raise ai_spend.SpendError(f"insufficient payment: need {cost} base units")
        if tx.memo != ai_spend.spend_memo(provider_id, model_tokens):
            raise ai_spend.SpendError("memo does not match the spend request")
        with self.lock:
            self.mempool.add(tx, self.chain)
        receipt = ai_spend.make_receipt(provider_id, model_tokens, tx.amount, tx.txid)
        return receipt.to_dict()

    def ai_providers(self) -> dict:
        with self.lock:
            providers = {}
            for pid, info in ai_spend.PROVIDERS.items():
                sink = ai_spend.provider_address(pid)
                providers[pid] = {
                    **info,
                    "id": pid,
                    "sink_address": sink,
                    "total_ait_spent": self.chain.balance_of(sink),
                }
            return providers
