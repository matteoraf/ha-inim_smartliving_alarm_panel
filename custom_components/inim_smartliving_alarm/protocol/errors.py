"""Errors raised while encoding requests or decoding replies."""

from enum import StrEnum


class ProtocolError(Exception):
    """A reply does not have the expected form."""


class ChecksumError(ProtocolError):
    """A reply's checksum does not match its data."""


class DecryptionError(ProtocolError):
    """An encrypted reply cannot be decrypted (wrong key, on SmartLAN/G)."""


class UnsupportedReason(StrEnum):
    """Why a panel is not supported."""

    FIRMWARE = "firmware"
    MODEL = "model"


class UnsupportedPanelError(Exception):
    """The panel model or firmware is not supported; reason says which."""

    def __init__(self, message: str, reason: UnsupportedReason) -> None:
        """Keep the message and the reason."""
        super().__init__(message)
        self.reason = reason


class InvalidPinError(ValueError):
    """A PIN is not 1 to 6 decimal digits."""
