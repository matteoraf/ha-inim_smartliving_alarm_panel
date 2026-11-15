"""Panel identification and the sizes and addresses of each model."""

from dataclasses import dataclass

from .errors import ProtocolError, UnsupportedPanelError

SCENARIOS = 30  # on every model
LABEL_LENGTH = 16
FIXED_OUTPUTS = 3  # O1-O3 on the panel board


@dataclass(frozen=True, slots=True)
class FirmwareInfo:
    """Contents of the 12-byte model string at RAM 0x4000."""

    version: str  # e.g. "6.07"
    major: int  # firmware family, e.g. 6
    model: str  # model number without leading zeros, e.g. "1050"
    text: str  # the whole string, trimmed


def parse_model_string(data: bytes) -> FirmwareInfo:
    """Decode the model string, e.g. b"6.07 01050  "."""
    text = data.decode("ascii", errors="replace")
    version, model_field = text[0:4], text[5:10]
    if not version[:1].isdigit() or not model_field.isdigit():
        raise ProtocolError(f"not a SmartLiving model string: {text!r}")
    return FirmwareInfo(
        version=version,
        major=int(version[0]),
        model=str(int(model_field)),
        text=text.strip(),
    )


@dataclass(frozen=True, slots=True)
class PanelModel:
    """Object counts, record sizes and EEPROM addresses of a model and firmware."""

    name: str
    areas: int  # A
    terminals: int  # L, logical terminals; zones are 2L (second zones of doubles)
    physical_slots: int  # F
    codes: int
    keypads: int
    readers: int
    expanders: int
    keys: int
    timers: int
    scenario_record_length: int
    zone_record_length: int
    code_record_length: int
    code_output_mask_length: int
    event_ring_size: int
    # EEPROM addresses (24-bit)
    pin_table: int
    code_records: int
    zone_records: int
    terminal_records: int
    physical_map: int
    scenario_records: int
    event_log: int
    label_bank: int  # added to the label pointers read from RAM

    scenarios = SCENARIOS
    terminal_record_length = 12
    pin_length = 6

    @property
    def zones(self) -> int:
        """Zone index space: primary zones 0..L-1, second zones L..2L-1."""
        return 2 * self.terminals

    @property
    def area_mode_bytes(self) -> int:
        """Bytes holding one arming nibble per area."""
        return self.areas // 2 + 1

    @property
    def area_status_length(self) -> int:
        """Length of the area status register."""
        return self.area_mode_bytes + 10

    @property
    def zone_state_length(self) -> int:
        """Length of the zone state register (2 bits per zone)."""
        return self.terminals // 2 + 1

    @property
    def zone_bitmap_length(self) -> int:
        """Bytes of a 1-bit-per-zone bitmap over 2L zones."""
        return self.terminals // 4 + 1

    @property
    def zone_inclusion_length(self) -> int:
        """Length of the zone inclusion register (inclusion + test bitmaps)."""
        return 2 * self.zone_bitmap_length

    @property
    def terminal_bitmap_length(self) -> int:
        """Bytes of the terminal tamper memory (1 bit per logical terminal)."""
        return self.terminals // 8 + 1

    @property
    def output_bitmap_length(self) -> int:
        """Bytes of the output image (L terminals, then O1-O3)."""
        return self.terminals // 8 + 2

    @property
    def zone_memory_length(self) -> int:
        """Length of the zone memory register."""
        return (
            self.zone_bitmap_length
            + self.terminal_bitmap_length
            + self.output_bitmap_length
        )

    @property
    def pin_table_length(self) -> int:
        """Length of the PIN table."""
        return self.codes * self.pin_length


SMARTLIVING_1050_FW6 = PanelModel(
    name="SmartLiving 1050",
    areas=10,
    terminals=50,
    physical_slots=130,
    codes=50,
    keypads=10,
    readers=20,
    expanders=20,
    keys=100,
    timers=10,
    scenario_record_length=8,
    zone_record_length=9,
    code_record_length=59,
    code_output_mask_length=8,
    event_ring_size=1000,
    pin_table=0x061F4,
    code_records=0x0632C,
    zone_records=0x05450,
    terminal_records=0x057D4,
    physical_map=0x078FD,
    scenario_records=0x073D8,
    event_log=0x19204,
    label_bank=0x10000,
)


def model_for(info: FirmwareInfo) -> PanelModel:
    """The model profile for a panel, or UnsupportedPanelError."""
    if info.major < 6:
        raise UnsupportedPanelError(
            f"firmware {info.version} is not supported yet (only 6.x is)"
        )
    if info.model == "1050":
        return SMARTLIVING_1050_FW6
    raise UnsupportedPanelError(f"SmartLiving {info.model} is not supported yet")
