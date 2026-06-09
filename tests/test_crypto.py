from aitoken import crypto
from aitoken.transaction import Transaction, make_transfer
from aitoken.wallet import Wallet


def test_sign_verify_roundtrip():
    sk, pk = crypto.generate_keypair()
    sig = crypto.sign(sk, b"hello")
    assert crypto.verify(pk, sig, b"hello")
    assert not crypto.verify(pk, sig, b"tampered")


def test_address_format_and_checksum():
    _, pk = crypto.generate_keypair()
    addr = crypto.address_from_pubkey(pk)
    assert addr.startswith("AIT")
    assert len(addr) == crypto.ADDRESS_LEN
    assert crypto.is_valid_address(addr)
    # Any single-character corruption breaks the checksum.
    corrupted = addr[:10] + ("0" if addr[10] != "0" else "1") + addr[11:]
    assert not crypto.is_valid_address(corrupted)
    assert not crypto.is_valid_address("AITshort")


def test_seed_addresses_deterministic():
    a = crypto.address_from_seed("AI_PROVIDER:claude")
    b = crypto.address_from_seed("AI_PROVIDER:claude")
    assert a == b and crypto.is_valid_address(a)
    assert crypto.address_from_seed("AI_PROVIDER:gemini") != a


def test_transaction_signature_validates():
    alice, bob = Wallet.create(), Wallet.create()
    tx = make_transfer(alice.private_key, alice.address, bob.address, 100, 1, 0)
    assert tx.verify()


def test_tampered_transaction_rejected():
    alice, bob = Wallet.create(), Wallet.create()
    tx = make_transfer(alice.private_key, alice.address, bob.address, 100, 1, 0)
    tx.amount = 999  # changes the txid, invalidating the signature
    assert not tx.verify()


def test_sender_must_match_public_key():
    alice, bob = Wallet.create(), Wallet.create()
    tx = make_transfer(alice.private_key, alice.address, bob.address, 100, 1, 0)
    tx.sender = bob.address
    assert not tx.verify()


def test_coinbase_structure():
    miner = Wallet.create()
    cb = Transaction.coinbase(miner.address, 50, height=7)
    assert cb.is_coinbase and cb.verify()
    assert "7" in cb.memo


def test_wallet_save_load_roundtrip(tmp_path):
    w = Wallet.create()
    path = str(tmp_path / "w.json")
    w.save(path)
    loaded = Wallet.load(path)
    assert loaded.address == w.address
    assert loaded.private_key == w.private_key
