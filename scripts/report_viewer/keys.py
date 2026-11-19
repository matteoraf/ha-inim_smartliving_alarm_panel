"""The maintainer's key pair: making it and decrypting reports with it.

The private key is a passphrase-protected PKCS8 PEM file, one per key, named
after the key's ID, in a folder only its owner can read. Several keys may be
kept there, so reports encrypted to an older key can still be read.
"""

import base64
from collections.abc import Callable
import getpass
import json
import os
from pathlib import Path
import re
from typing import Any

from . import loader

KEY_FOLDER = Path.home() / ".config" / "inim-report"
NEEDS_CRYPTOGRAPHY = (
    "this command needs the Python package cryptography (pip install cryptography)"
)


class KeyFileError(Exception):
    """A key could not be made, found or used."""


# A key ID as the integration makes it: 8 bytes of a SHA-256, in hex
_KEY_ID = re.compile(r"[0-9a-f]{16}")


def check_key_id(key_id: object) -> str:
    """The key ID if it has the right form; a file name is made from it."""
    if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id):
        raise KeyFileError("the report names no valid key ID")
    return key_id


def private_folder(folder: Path) -> None:
    """Create the key folder for its owner only, or check an existing one.

    An existing folder is never changed: if others may read or enter it,
    keygen stops and asks for a private folder.
    """
    if folder.exists():
        if not folder.is_dir():
            raise KeyFileError(f"{folder} is not a folder")
        if folder.stat().st_mode & 0o077:
            raise KeyFileError(
                f"{folder} can be read by others: use a private folder "
                "(one only you can read), or a new one"
            )
        return
    folder.mkdir(mode=0o700, parents=True)
    os.chmod(folder, 0o700)  # the mode given to mkdir is narrowed by the umask


def _crypto():
    """The report encryption module; it needs the cryptography package."""
    try:
        return loader.module("report_crypto")
    except ImportError as err:
        raise KeyFileError(NEEDS_CRYPTOGRAPHY) from err


def key_file(folder: Path, key_id: str) -> Path:
    """Where the private key of that ID is kept."""
    return folder / f"inim-report-key-{key_id}.pem"


def keygen(
    folder: Path = KEY_FOLDER, ask: Callable[[str], str] | None = None
) -> tuple[str, str]:
    """Make a new key pair; returns (public key as base64, key ID).

    The passphrase is asked twice. The folder is created readable by its
    owner only (an existing one must be private already), and an existing
    key file is never overwritten.
    """
    crypto = _crypto()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    ask = ask or getpass.getpass
    private_folder(folder)

    passphrase = ask("Passphrase for the new key: ")
    if not passphrase or passphrase != ask("Passphrase again: "):
        raise KeyFileError("the passphrases are empty or do not match")
    key = X25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    key_id = crypto.key_id(public)
    path = key_file(folder, key_id)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(passphrase.encode()),
    )
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as err:
        raise KeyFileError(f"{path} exists already") from err
    with os.fdopen(descriptor, "wb") as file:
        file.write(pem)
    return base64.b64encode(public).decode(), key_id


def _private_key(path: Path, ask: Callable[[str], str]) -> bytes:
    """The raw private key from its PEM file, asking for the passphrase."""
    from cryptography.hazmat.primitives import serialization

    passphrase = ask(f"Passphrase for {path.name}: ")
    try:
        key = serialization.load_pem_private_key(
            path.read_bytes(), password=passphrase.encode() or None
        )
    except (TypeError, ValueError) as err:
        raise KeyFileError(f"{path.name}: wrong passphrase or not a key") from err
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def decrypt(
    envelope_document: dict[str, Any],
    *,
    key_path: Path | None = None,
    folder: Path = KEY_FOLDER,
    ask: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """The readable report of an encrypted file.

    The key is the one given, else the one whose ID the file names, in the
    key folder. The report's ID and level are checked with the encryption.
    """
    crypto = _crypto()
    ask = ask or getpass.getpass
    envelope = envelope_document.get("encrypted") or {}
    key_id = check_key_id(envelope.get("key_id"))
    path = key_path or key_file(folder, key_id)
    if not path.is_file():
        raise KeyFileError(f"no key for key ID {key_id or '?'} (looked for {path})")
    try:
        plain = crypto.decrypt(
            envelope,
            _private_key(path, ask),
            report_id=str(envelope_document.get("report_id", "")),
            level=str(envelope_document.get("level", "")),
        )
    except crypto.ReportDecryptError as err:
        raise KeyFileError(str(err)) from err
    return json.loads(plain)
