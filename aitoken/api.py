"""FastAPI application: REST API + static dashboard."""

import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai_spend, crypto
from .ai_spend import SpendError
from .block import Block
from .blockchain import BlockValidationError
from .config import NodeConfig
from .exchange import ExchangeError
from .mempool import MempoolError
from .node import AuthError, Node
from .transaction import Transaction, make_transfer
from .wallet import Wallet

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class TxBody(BaseModel):
    sender: str
    recipient: str
    amount: int = Field(ge=0)
    fee: int = Field(ge=0)
    nonce: int = Field(ge=0)
    timestamp: float
    public_key: str
    signature: str
    memo: str = ""


class SignAndSendBody(BaseModel):
    """Demo convenience: the browser wallet sends its locally stored private
    key to its own local node for signing. Localhost demo only."""

    private_key: str
    recipient: str
    amount: int = Field(gt=0)
    fee: int = Field(ge=0, default=0)
    memo: str = ""


class BlockBody(BaseModel):
    index: int
    timestamp: float
    prev_hash: str
    merkle_root: str
    difficulty_bits: int
    nonce: int
    transactions: list[dict]


class FaucetBody(BaseModel):
    address: str
    amount_cents: int = Field(gt=0, le=100_000_00)


class OrderBody(BaseModel):
    address: str
    side: str
    price_cents: int = Field(gt=0)
    quantity: int = Field(gt=0)
    timestamp: float
    public_key: str = ""
    signature: str = ""
    private_key: str | None = None  # demo convenience: node signs server-side


class CancelBody(BaseModel):
    address: str
    public_key: str = ""
    signature: str = ""
    private_key: str | None = None


class WithdrawBody(BaseModel):
    address: str
    amount: int = Field(gt=0)
    timestamp: float
    public_key: str = ""
    signature: str = ""
    private_key: str | None = None


class SpendBody(BaseModel):
    provider: str
    model_tokens: int = Field(gt=0)
    tx: TxBody | None = None
    private_key: str | None = None  # demo convenience, same caveat as sign-and-send


def create_app(config: NodeConfig | None = None, node: Node | None = None) -> FastAPI:
    node = node or Node(config or NodeConfig())
    app = FastAPI(title="AI Token (AIT) Platform", version="0.1.0")
    app.state.node = node

    def http_error(status: int, exc: Exception) -> HTTPException:
        return HTTPException(status_code=status, detail=str(exc))

    def demo_sign(body, message: str) -> tuple[str, str]:
        """Demo convenience: if the caller sent a private key instead of a
        signature, sign the auth message node-side (localhost demo only)."""
        if body.private_key:
            pk = crypto.public_key_from_private(body.private_key)
            return pk, crypto.sign(body.private_key, message.encode())
        return body.public_key, body.signature

    # ------------------------------------------------------------- chain

    @app.get("/api/status")
    def status():
        return node.status()

    @app.get("/api/chain")
    def chain(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        return node.get_blocks(offset, limit)

    @app.get("/api/block/{ref}")
    def block(ref: str):
        result = node.get_block(ref)
        if result is None:
            raise HTTPException(status_code=404, detail="block not found")
        return result

    @app.get("/api/tx/{txid}")
    def tx(txid: str):
        result = node.get_tx(txid)
        if result is None:
            raise HTTPException(status_code=404, detail="transaction not found")
        return result

    @app.get("/api/address/{address}")
    def address(address: str):
        if not crypto.is_valid_address(address):
            raise HTTPException(status_code=400, detail="invalid address")
        return node.address_info(address)

    @app.get("/api/mempool")
    def mempool():
        return {"size": len(node.mempool), "transactions": [
            {**tx.to_dict(), "txid": txid} for txid, tx in node.mempool.txs.items()
        ]}

    @app.post("/api/tx")
    def submit_tx(body: TxBody):
        try:
            txid = node.submit_tx(Transaction.from_dict(body.model_dump()))
        except MempoolError as e:
            raise http_error(400, e)
        return {"txid": txid, "status": "pending"}

    # ------------------------------------------------------------ wallets

    @app.post("/api/wallet/new")
    def wallet_new():
        w = Wallet.create()
        return {
            "address": w.address,
            "public_key": w.public_key,
            "private_key": w.private_key,
            "warning": "Demo wallet: the private key is shown once; whoever has it owns the funds.",
        }

    @app.post("/api/wallet/sign-and-send")
    def sign_and_send(body: SignAndSendBody):
        try:
            pk = crypto.public_key_from_private(body.private_key)
            sender = crypto.address_from_pubkey(pk)
            tx = make_transfer(
                body.private_key,
                sender,
                body.recipient,
                body.amount,
                body.fee,
                node.next_nonce(sender),
                memo=body.memo,
            )
            txid = node.submit_tx(tx)
        except MempoolError as e:
            raise http_error(400, e)
        except ValueError as e:
            raise http_error(400, e)
        return {"txid": txid, "status": "pending"}

    # ------------------------------------------------------------- mining

    @app.get("/api/mining/template")
    def mining_template(address: str):
        try:
            return node.get_block_template(address)
        except ValueError as e:
            raise http_error(400, e)

    @app.post("/api/mining/submit")
    def mining_submit(body: BlockBody):
        try:
            return node.submit_block(Block.from_dict(body.model_dump()))
        except BlockValidationError as e:
            raise HTTPException(status_code=409, detail=str(e))

    @app.get("/api/mining/stats")
    def mining_stats():
        return node.mining_stats()

    # ----------------------------------------------------------- exchange

    @app.post("/api/faucet/usd")
    def faucet(body: FaucetBody):
        if not crypto.is_valid_address(body.address):
            raise HTTPException(status_code=400, detail="invalid address")
        try:
            return node.faucet_usd(body.address, body.amount_cents)
        except ExchangeError as e:
            raise http_error(400, e)

    @app.get("/api/exchange/deposit-address")
    def deposit_address():
        return {"address": node.exchange_wallet.address}

    @app.post("/api/exchange/orders")
    def place_order(body: OrderBody):
        message = (
            f"ORDER:{body.address}:{body.side}:{body.price_cents}"
            f":{body.quantity}:{body.timestamp}"
        )
        try:
            public_key, signature = demo_sign(body, message)
            return node.place_order(
                body.address,
                body.side,
                body.price_cents,
                body.quantity,
                body.timestamp,
                public_key,
                signature,
            )
        except AuthError as e:
            raise http_error(401, e)
        except (ExchangeError, ValueError) as e:
            raise http_error(400, e)

    @app.delete("/api/exchange/orders/{order_id}")
    def cancel_order(order_id: str, body: CancelBody):
        try:
            public_key, signature = demo_sign(body, f"CANCEL:{order_id}")
            return node.cancel_order(order_id, body.address, public_key, signature)
        except AuthError as e:
            raise http_error(401, e)
        except (ExchangeError, ValueError) as e:
            raise http_error(400, e)

    @app.get("/api/exchange/orders")
    def open_orders(address: str):
        return {"orders": [o.to_dict() for o in node.exchange.open_orders(address)]}

    @app.post("/api/exchange/withdraw")
    def withdraw(body: WithdrawBody):
        message = f"WITHDRAW:{body.address}:{body.amount}:{body.timestamp}"
        try:
            public_key, signature = demo_sign(body, message)
            return node.withdraw(
                body.address, body.amount, body.timestamp, public_key, signature
            )
        except AuthError as e:
            raise http_error(401, e)
        except (ExchangeError, MempoolError, ValueError) as e:
            raise http_error(400, e)

    @app.get("/api/exchange/orderbook")
    def orderbook():
        return node.exchange.orderbook()

    @app.get("/api/exchange/trades")
    def trades(limit: int = Query(50, ge=1, le=200)):
        return {"trades": [t.to_dict() for t in node.exchange.recent_trades(limit)]}

    @app.get("/api/exchange/fees")
    def fees():
        return node.exchange.fee_summary()

    # ----------------------------------------------------------- ai spend

    @app.get("/api/ai/providers")
    def providers():
        return node.ai_providers()

    @app.post("/api/ai/spend")
    def spend(body: SpendBody):
        try:
            if body.tx is not None:
                tx = Transaction.from_dict(body.tx.model_dump())
            elif body.private_key is not None:
                pk = crypto.public_key_from_private(body.private_key)
                sender = crypto.address_from_pubkey(pk)
                cost = ai_spend.quote(body.provider, body.model_tokens)
                tx = make_transfer(
                    body.private_key,
                    sender,
                    ai_spend.provider_address(body.provider),
                    cost,
                    0,
                    node.next_nonce(sender),
                    memo=ai_spend.spend_memo(body.provider, body.model_tokens),
                )
            else:
                raise HTTPException(status_code=400, detail="provide tx or private_key")
            return node.spend_on_ai(body.provider, body.model_tokens, tx)
        except (SpendError, MempoolError, ValueError) as e:
            raise http_error(400, e)

    # ---------------------------------------------------------- dashboard

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def run_node(config: NodeConfig) -> None:
    import uvicorn

    app = create_app(config)
    status = app.state.node.status()
    print(f"[aitoken] Chain height {status['height']}, owner {status['owner_address']}")
    print(f"[aitoken] Dashboard: http://localhost:{config.port}/")
    print(
        "[aitoken] Mine with: python -m aitoken miner "
        f"--node http://localhost:{config.port} --address <your-address>"
    )
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")
