"""Encryption of system reports for the integration's maintainer.

A report at the Full levels is encrypted to the maintainer's public key, so
it can be attached to a public issue: an ephemeral X25519 key agreement,
HKDF-SHA256, then ChaCha20-Poly1305. The report is compressed before it is
encrypted. The report's ID, level and the key ID are authenticated with it,
so none of them can be changed without the decryption failing.

Only the matching private key decrypts a report. When the key pair changes,
a new release carries the new public key, and the key ID tells which one a
file was encrypted to.
"""

import base64
import hashlib
import os
from typing import Any
import zlib

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SCHEME = "x25519-hkdf-sha256-chacha20poly1305/1"
_INFO = b"inim-report/1"
_NONCE_LENGTH = 12
_KEY_LENGTH = 32

# The maintainer's public key (raw X25519, base64) and its ID
OWNER_PUBLIC_KEY = "pqUQzHYlGQBsYMPsspyZCjKC0w8wADMxO/0QTlXIdGs="
OWNER_KEY_ID = "032f4cd3c2378613"


class ReportDecryptError(Exception):
    """The report could not be decrypted (wrong key, or changed)."""


def key_id(public_key: bytes) -> str:
    """A public key's ID: the first 8 bytes of its SHA-256, as hex."""
    return hashlib.sha256(public_key).digest()[:8].hex()


def _raw_public(key: X25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _aad(report_id: str, level: str, kid: str) -> bytes:
    return b"\x00".join(part.encode() for part in (report_id, level, kid))


def _derive(shared: bytes, ephemeral: bytes, recipient: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_LENGTH,
        salt=ephemeral + recipient,
        info=_INFO,
    ).derive(shared)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def encrypt(
    report: bytes,
    *,
    report_id: str,
    level: str,
    public_key: str = OWNER_PUBLIC_KEY,
) -> dict[str, Any]:
    """Encrypt a report (its JSON text) to a public key (base64)."""
    recipient = base64.b64decode(public_key, validate=True)
    if len(recipient) != _KEY_LENGTH:
        raise ValueError("the public key must be 32 bytes")
    kid = key_id(recipient)
    ephemeral_key = X25519PrivateKey.generate()
    ephemeral = _raw_public(ephemeral_key.public_key())
    shared = ephemeral_key.exchange(X25519PublicKey.from_public_bytes(recipient))
    nonce = os.urandom(_NONCE_LENGTH)
    ciphertext = ChaCha20Poly1305(_derive(shared, ephemeral, recipient)).encrypt(
        nonce, zlib.compress(report, 9), _aad(report_id, level, kid)
    )
    return {
        "scheme": SCHEME,
        "key_id": kid,
        "ephemeral_public": _b64(ephemeral),
        "nonce": _b64(nonce),
        "ciphertext": _b64(ciphertext),
    }


def decrypt(
    envelope: dict[str, Any], private_key: bytes, *, report_id: str, level: str
) -> bytes:
    """Decrypt an encrypted report with the raw 32-byte private key."""
    if envelope.get("scheme") != SCHEME:
        raise ReportDecryptError(f"unknown scheme {envelope.get('scheme')!r}")
    key = X25519PrivateKey.from_private_bytes(private_key)
    recipient = _raw_public(key.public_key())
    kid = key_id(recipient)
    if envelope.get("key_id") != kid:
        raise ReportDecryptError("the report was encrypted to another key")
    try:
        ephemeral = base64.b64decode(envelope["ephemeral_public"], validate=True)
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
        shared = key.exchange(X25519PublicKey.from_public_bytes(ephemeral))
        compressed = ChaCha20Poly1305(_derive(shared, ephemeral, recipient)).decrypt(
            nonce, ciphertext, _aad(report_id, level, kid)
        )
        return zlib.decompress(compressed)
    except (InvalidTag, KeyError, ValueError, zlib.error) as err:
        raise ReportDecryptError("the report could not be decrypted") from err
