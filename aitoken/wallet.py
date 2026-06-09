"""Wallets: keypair + address, JSON keyfile persistence.

Keyfiles are plaintext JSON — fine for this demo platform, not production
custody. Anyone holding the file controls the funds.
"""

import json
import os
from dataclasses import dataclass

from . import crypto
from .transaction import Transaction, make_transfer


@dataclass
class Wallet:
    private_key: str
    public_key: str
    address: str

    @classmethod
    def create(cls) -> "Wallet":
        sk, pk = crypto.generate_keypair()
        return cls(private_key=sk, public_key=pk, address=crypto.address_from_pubkey(pk))

    @classmethod
    def load(cls, path: str) -> "Wallet":
        with open(path) as f:
            d = json.load(f)
        wallet = cls(
            private_key=d["private_key"],
            public_key=d["public_key"],
            address=d["address"],
        )
        if crypto.address_from_pubkey(wallet.public_key) != wallet.address:
            raise ValueError(f"corrupt wallet file: address mismatch in {path}")
        return wallet

    def save(self, path: str) -> None:
        data = {
            "private_key": self.private_key,
            "public_key": self.public_key,
            "address": self.address,
            "warning": "Demo keyfile: plaintext private key, keep it to yourself.",
        }
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)

    def transfer(
        self, recipient: str, amount: int, fee: int, nonce: int, memo: str = ""
    ) -> Transaction:
        return make_transfer(
            self.private_key, self.address, recipient, amount, fee, nonce, memo=memo
        )

    def sign_message(self, message: str) -> str:
        return crypto.sign(self.private_key, message.encode())
