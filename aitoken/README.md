# AI Token (AIT) — Mining & Trading Platform

A self-contained, Bitcoin-style cryptocurrency platform themed around paying for AI
usage. It is a complete working system you can run on a laptop:

- **Own blockchain** — SHA-256 proof-of-work, auto-adjusting difficulty, block rewards
  that halve over time (50 AIT → 25 AIT → …), ECDSA (SECP256k1) wallets, signed
  transactions with replay protection, full chain validation.
- **CLI miner** — anyone can mine AIT against the node with zero extra installs
  (pure Python stdlib).
- **Built-in exchange** — deposit AIT, trade it against simulated USD credits on a
  price-time-priority order book. **Every matched trade pays a fee (default 0.5%,
  taker-paid) straight to the platform owner's wallet** — your revenue stream.
- **Spend on AI** — burn AIT to provider sink addresses (Claude, Gemini, ChatGPT,
  Grok, Llama) with a simulated receipt. `SpendProvider` in `ai_spend.py` is the
  extension point for a real provider integration.
- **Web dashboard** — explorer, mining stats, browser wallet, trading UI, and a
  Fee Revenue panel showing the owner's cumulative earnings.

## Quickstart

```bash
pip install -r requirements-aitoken.txt

# Terminal 1 — the node (prints your owner address on first run and
# saves its key to ./owner_wallet.json)
python -m aitoken node --port 8545

# Terminal 2 — create a wallet and mine to it
python -m aitoken wallet new --out alice.wallet.json
python -m aitoken miner --node http://localhost:8545 --address <alice-address>

# Browser — dashboard
open http://localhost:8545/
```

Then in the dashboard: create a browser wallet, mine some AIT to it, grab demo USD
from the faucet, deposit AIT to the exchange, trade — and watch the **Fee Revenue**
tab tick up with every trade.

## Configuration

CLI flags on `python -m aitoken node`: `--port`, `--db`, `--owner <address>`,
`--fee-percent <pct>`. Environment variables: `AITOKEN_PORT`, `AITOKEN_DB_PATH`,
`AITOKEN_OWNER_ADDRESS`, `AITOKEN_FEE_PERCENT`, etc. (see `aitoken/config.py`).

Chain parameters (demo-tuned, in `config.py`): 10 s target block time, retarget
every 20 blocks, halving every 200 blocks, 18-bit starting difficulty (a laptop
CPU mines a block every few seconds).

## API

Interactive docs at `http://localhost:8545/docs`. Highlights:

| Endpoint | Purpose |
|---|---|
| `GET /api/mining/template`, `POST /api/mining/submit` | miner protocol |
| `POST /api/tx` | submit a signed transaction |
| `GET /api/exchange/orderbook`, `POST /api/exchange/orders` | trading |
| `GET /api/exchange/fees` | owner fee revenue |
| `POST /api/ai/spend`, `GET /api/ai/providers` | spend AIT on AI usage |

## Tests

```bash
python -m pytest tests/
```

Covers PoW and chain validation, halving and difficulty retargeting, double-spend
rejection, order matching, fee accrual to the owner (with conservation invariants:
no AIT or USD is ever created or destroyed by trading — except fees flowing to the
owner), restart persistence, and a full mine→deposit→trade→spend API flow.

## Scope and honesty notes

- **Single authoritative node.** No P2P networking, forks, or reorgs; a stale mined
  block is rejected and the miner refetches. Right-sized for a demo and a foundation
  to extend.
- **Simulated USD and simulated AI usage.** Real AI providers don't accept AIT —
  making that real is a business/integration matter, not a software switch. The
  `SpendProvider` hook is where a real integration would go.
- **Demo-grade key handling.** Wallet keyfiles are plaintext JSON, and the browser
  wallet sends its private key to its own local node for signing. Do not use real
  value with this software; operating a real-money exchange is a regulated activity.
