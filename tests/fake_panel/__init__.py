"""A fake SmartLiving panel (1050, 505, 515 or 10100; firmware 6 or 5.1) for tests."""

from .server import TRANSPORT_G, TRANSPORT_SI, FakePanel, Request
from .state import (
    EventRecord,
    PanelState,
    checksum,
    encode_pin,
    panel_505,
    panel_505_fw51,
    panel_515,
    panel_1050,
    panel_1050_fw51,
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
    "panel_505",
    "panel_505_fw51",
    "panel_515",
    "panel_1050",
    "panel_1050_fw51",
    "panel_10100",
]
