"""Errors raised while encoding requests or decoding replies."""


class ProtocolError(Exception):
    """A reply does not have the expected form."""


class ChecksumError(ProtocolError):
    """A reply's checksum does not match its data."""


class UnsupportedPanelError(Exception):
    """The panel model or firmware is not supported."""


class InvalidPinError(ValueError):
    """A PIN is not 1 to 6 decimal digits."""
