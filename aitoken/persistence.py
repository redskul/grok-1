"""SQLite persistence: append-only block store plus exchange-state snapshot.

Blocks and transactions get real tables (the explorer queries them); the
exchange state (credit ledgers, orders, trades, fee totals) is small and
saved as a single JSON snapshot after each mutation — atomic and simple.
"""

import json
import sqlite3

from .block import Block

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    height INTEGER PRIMARY KEY,
    hash TEXT NOT NULL,
    data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    txid TEXT PRIMARY KEY,
    height INTEGER NOT NULL,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    amount INTEGER NOT NULL,
    fee INTEGER NOT NULL,
    memo TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tx_sender ON transactions(sender);
CREATE INDEX IF NOT EXISTS idx_tx_recipient ON transactions(recipient);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -------------------------------------------------------------- blocks

    def save_block(self, block: Block) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO blocks (height, hash, data_json) VALUES (?, ?, ?)",
                (block.index, block.header_hash(), json.dumps(block.to_dict())),
            )
            self.conn.executemany(
                "INSERT OR REPLACE INTO transactions "
                "(txid, height, sender, recipient, amount, fee, memo) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (tx.txid, block.index, tx.sender, tx.recipient, tx.amount, tx.fee, tx.memo)
                    for tx in block.transactions
                ],
            )

    def load_blocks(self) -> list[Block]:
        rows = self.conn.execute("SELECT data_json FROM blocks ORDER BY height").fetchall()
        return [Block.from_dict(json.loads(r[0])) for r in rows]

    def find_tx_height(self, txid: str) -> int | None:
        row = self.conn.execute("SELECT height FROM transactions WHERE txid = ?", (txid,)).fetchone()
        return row[0] if row else None

    def txs_for_address(self, address: str, limit: int = 25) -> list[dict]:
        rows = self.conn.execute(
            "SELECT txid, height, sender, recipient, amount, fee, memo FROM transactions "
            "WHERE sender = ? OR recipient = ? ORDER BY height DESC LIMIT ?",
            (address, address, limit),
        ).fetchall()
        keys = ("txid", "height", "sender", "recipient", "amount", "fee", "memo")
        return [dict(zip(keys, r)) for r in rows]

    # ---------------------------------------------------------------- meta

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
