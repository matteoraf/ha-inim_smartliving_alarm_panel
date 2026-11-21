"""The blocks a system report reads, and which table fits a panel.

A report reads only the blocks of its panel's table: each block names its
memory, address, length and privacy. The tables themselves are in
report_blocks.py. A panel whose model or firmware has no table is read in
basic mode: its identification and status registers only.
"""

from dataclasses import dataclass
from enum import StrEnum

from . import registers as reg
from .masking import Privacy
from .model import FirmwareInfo, PanelModel


class Space(StrEnum):
    """Where a block is read from."""

    EEPROM = "eeprom"
    RAM = "ram"  # flat RAM and the registers read like it
    LABELS = "labels"  # a label table, found through its RAM pointer
    QUERY = "query"  # the not-ready zones register, asked per scenario


@dataclass(frozen=True, slots=True)
class ReportBlock:
    """One block of a report table."""

    name: str
    space: Space
    address: int  # for LABELS: the pointer register
    length: int
    privacy: Privacy


@dataclass(frozen=True, slots=True)
class ReportTable:
    """What a report reads from one layout (model and firmware family)."""

    model: str
    family: str  # "6", "5.1", "5.0", or "basic"
    event_ring_size: int
    label_bank: int
    layout_verified: bool
    # Where label tables may be read (EEPROM start, length); a label table
    # whose pointer leads elsewhere is not read. None: labels are not read
    label_area: tuple[int, int] | None
    blocks: tuple[ReportBlock, ...]

    def block(self, name: str) -> ReportBlock | None:
        """The block of that name, if the table has it."""
        return next((b for b in self.blocks if b.name == name), None)

    def labels_fit(self, address: int, length: int) -> bool:
        """Whether a label table at that EEPROM address lies in the label area."""
        if self.label_area is None:
            return False
        start, size = self.label_area
        return start <= address and address + length <= start + size

    def canonical_text(self) -> str:
        """The table as text, one line per block: what an approval covers."""
        area = (
            "none"
            if self.label_area is None
            else f"{self.label_area[0]}+{self.label_area[1]}"
        )
        lines = [
            f"{self.model}|{self.family}|{self.event_ring_size}|"
            f"{self.label_bank}|{self.layout_verified}|{area}"
        ]
        lines += [
            f"{b.name}|{b.space}|{b.address}|{b.length}|{b.privacy}"
            for b in self.blocks
        ]
        return "\n".join(lines) + "\n"


def report_family(firmware: FirmwareInfo) -> str | None:
    """The firmware family whose table a panel uses; None for basic mode."""
    if firmware.family is not None:
        return firmware.family
    if firmware.version.startswith("5.0"):
        return "5.0"
    return None


def basic_table(model: PanelModel) -> ReportTable:
    """Basic mode: identification and the status registers, sized by model.

    Used when no table fits the panel; no layout-dependent EEPROM read.
    """
    pub = Privacy.PUBLIC
    blocks = (
        ReportBlock("model_string", Space.RAM, reg.MODEL_STRING, 12, pub),
        ReportBlock(
            "area_status", Space.RAM, reg.AREA_STATUS, model.area_status_length, pub
        ),
        ReportBlock(
            "zone_state", Space.RAM, reg.ZONE_STATE, model.zone_state_length, pub
        ),
        ReportBlock(
            "zone_inclusion",
            Space.RAM,
            reg.ZONE_INCLUSION,
            model.zone_inclusion_length,
            pub,
        ),
        ReportBlock(
            "zone_memory", Space.RAM, reg.ZONE_MEMORY, model.zone_memory_length, pub
        ),
        ReportBlock("faults", Space.RAM, reg.FAULTS, reg.FAULTS_LENGTH, pub),
        ReportBlock("active_scenario", Space.RAM, reg.ACTIVE_SCENARIO, 1, pub),
        ReportBlock(
            "event_pointers",
            Space.RAM,
            reg.EVENT_POINTERS,
            reg.EVENT_POINTERS_LENGTH,
            pub,
        ),
    )
    return ReportTable(
        model=model.name,
        family="basic",
        event_ring_size=0,
        label_bank=0,
        layout_verified=False,
        label_area=None,
        blocks=blocks,
    )


def table_for(firmware: FirmwareInfo, model: PanelModel) -> ReportTable:
    """The table of the panel's own model and family, else basic mode.

    model is the profile in use, which sizes the basic-mode registers.
    """
    from .report_blocks import TABLES  # data tables; imported on use

    family = report_family(firmware)
    table = TABLES.get((firmware.model, family)) if family else None
    return table if table is not None else basic_table(model)
