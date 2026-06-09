"""ECDSA (SECP256k1) keypairs, signatures, and AIT addresses.

Address format: "AIT" + first 40 hex chars of sha256(pubkey_bytes) + 8-char
checksum (first 8 hex chars of sha256 over the body). Human-checkable and
typo-resistant, deliberately simpler than Bitcoin's base58check.
"""

import hashlib

import ecdsa

ADDRESS_PREFIX = "AIT"
ADDRESS_BODY_LEN = 40
ADDRESS_CHECKSUM_LEN = 8
ADDRESS_LEN = len(ADDRESS_PREFIX) + ADDRESS_BODY_LEN + ADDRESS_CHECKSUM_LEN


def generate_keypair() -> tuple[str, str]:
    """Return (private_key_hex, public_key_hex)."""
    sk = ecdsa.SigningKey.generate(curve=ecdsa.SECP256k1)
    return sk.to_string().hex(), sk.get_verifying_key().to_string().hex()


def public_key_from_private(sk_hex: str) -> str:
    sk = ecdsa.SigningKey.from_string(bytes.fromhex(sk_hex), curve=ecdsa.SECP256k1)
    return sk.get_verifying_key().to_string().hex()


def _checksum(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()[:ADDRESS_CHECKSUM_LEN]


def address_from_pubkey(pk_hex: str) -> str:
    body = hashlib.sha256(bytes.fromhex(pk_hex)).hexdigest()[:ADDRESS_BODY_LEN]
    return ADDRESS_PREFIX + body + _checksum(body)


def address_from_seed(seed: str) -> str:
    """Deterministic address with no known private key (provider sinks, burn)."""
    body = hashlib.sha256(seed.encode()).hexdigest()[:ADDRESS_BODY_LEN]
    return ADDRESS_PREFIX + body + _checksum(body)


def is_valid_address(addr: str) -> bool:
    if not isinstance(addr, str) or len(addr) != ADDRESS_LEN:
        return False
    if not addr.startswith(ADDRESS_PREFIX):
        return False
    body = addr[len(ADDRESS_PREFIX) : len(ADDRESS_PREFIX) + ADDRESS_BODY_LEN]
    return addr.endswith(_checksum(body))


def sign(sk_hex: str, message: bytes) -> str:
    sk = ecdsa.SigningKey.from_string(bytes.fromhex(sk_hex), curve=ecdsa.SECP256k1)
    return sk.sign_deterministic(message, hashfunc=hashlib.sha256).hex()


def verify(pk_hex: str, signature_hex: str, message: bytes) -> bool:
    try:
        vk = ecdsa.VerifyingKey.from_string(bytes.fromhex(pk_hex), curve=ecdsa.SECP256k1)
        return vk.verify(bytes.fromhex(signature_hex), message, hashfunc=hashlib.sha256)
    except Exception:
        return False


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
