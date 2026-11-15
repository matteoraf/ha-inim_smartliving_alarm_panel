"""A fake SmartLiving panel (1050, or 10100) for tests."""

from .server import TRANSPORT_G, TRANSPORT_SI, FakePanel, Request
from .state import (
    EventRecord,
    PanelState,
    checksum,
    encode_pin,
    panel_1050,
    panel_10100,
)

__all__ = [
    "TRANSPORT_G",
    "TRANSPORT_SI",
    "EventRecord",
    "FakePanel",
    "PanelState",
    "Request",
    "checksum",
    "encode_pin",
    "panel_1050",
    "panel_10100",
]
