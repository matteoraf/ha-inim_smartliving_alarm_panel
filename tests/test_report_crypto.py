"""Encryption of system reports (report_crypto.py), with a throwaway key."""

import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
import pytest

from custom_components.inim_smartliving_alarm.report_crypto import (
    OWNER_KEY_ID,
    OWNER_PUBLIC_KEY,
    SCHEME,
    ReportDecryptError,
    decrypt,
    encrypt,
    key_id,
)

MARKER = b"PLAINTEXT-MARKER-0123456789"
REPORT = json.dumps({"marker": MARKER.decode(), "rows": ["00 11 22"] * 200}).encode()


def throwaway_key() -> tuple[bytes, str]:
    """A key pair made for the test: (raw private key, public key as base64)."""
    key = X25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, base64.b64encode(public).decode()


def test_owner_key_and_its_id() -> None:
    """The embedded public key is 32 bytes and its ID matches."""
    raw = base64.b64decode(OWNER_PUBLIC_KEY)
    assert len(raw) == 32
    assert key_id(raw) == OWNER_KEY_ID


def test_round_trip() -> None:
    """What is encrypted to a key is decrypted with its private key."""
    private, public = throwaway_key()
    envelope = encrypt(REPORT, report_id="r1", level="full", public_key=public)
    assert envelope["scheme"] == SCHEME
    assert envelope["key_id"] == key_id(base64.b64decode(public))
    assert decrypt(envelope, private, report_id="r1", level="full") == REPORT


def test_encrypts_to_the_owner_key_by_default() -> None:
    """Without a key given, the report is encrypted to the owner's key."""
    envelope = encrypt(REPORT, report_id="r1", level="full")
    assert envelope["key_id"] == OWNER_KEY_ID


def test_no_plaintext_in_the_envelope() -> None:
    """Neither the marker nor the report text appear in the envelope."""
    _, public = throwaway_key()
    text = json.dumps(encrypt(REPORT, report_id="r1", level="full", public_key=public))
    assert MARKER.decode() not in text
    assert base64.b64encode(MARKER).decode()[:20] not in text


def test_each_encryption_differs() -> None:
    """A fresh ephemeral key and nonce every time."""
    _, public = throwaway_key()
    first = encrypt(REPORT, report_id="r1", level="full", public_key=public)
    second = encrypt(REPORT, report_id="r1", level="full", public_key=public)
    assert first["ephemeral_public"] != second["ephemeral_public"]
    assert first["ciphertext"] != second["ciphertext"]


def test_wrong_key_fails() -> None:
    """Another private key cannot decrypt it."""
    _, public = throwaway_key()
    other, _ = throwaway_key()
    envelope = encrypt(REPORT, report_id="r1", level="full", public_key=public)
    with pytest.raises(ReportDecryptError):
        decrypt(envelope, other, report_id="r1", level="full")


@pytest.mark.parametrize(
    ("report_id", "level"), [("r2", "full"), ("r1", "confidential")]
)
def test_changed_report_id_or_level_fails(report_id: str, level: str) -> None:
    """The report's ID and level are authenticated with it."""
    private, public = throwaway_key()
    envelope = encrypt(REPORT, report_id="r1", level="full", public_key=public)
    with pytest.raises(ReportDecryptError):
        decrypt(envelope, private, report_id=report_id, level=level)


@pytest.mark.parametrize("field", ["ciphertext", "nonce", "ephemeral_public"])
def test_tampering_fails(field: str) -> None:
    """A changed byte anywhere makes the decryption fail."""
    private, public = throwaway_key()
    envelope = encrypt(REPORT, report_id="r1", level="full", public_key=public)
    raw = bytearray(base64.b64decode(envelope[field]))
    raw[0] ^= 1
    envelope[field] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(ReportDecryptError):
        decrypt(envelope, private, report_id="r1", level="full")


def test_unknown_scheme_fails() -> None:
    """Only the known scheme is accepted."""
    private, public = throwaway_key()
    envelope = encrypt(REPORT, report_id="r1", level="full", public_key=public)
    envelope["scheme"] = "other/1"
    with pytest.raises(ReportDecryptError):
        decrypt(envelope, private, report_id="r1", level="full")


def test_bad_public_key_is_refused() -> None:
    """A public key of the wrong length is refused."""
    with pytest.raises(ValueError):
        encrypt(REPORT, report_id="r1", level="full", public_key="AAAA")
