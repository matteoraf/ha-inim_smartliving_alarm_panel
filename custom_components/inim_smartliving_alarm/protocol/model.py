"""Panel identification and the sizes and addresses of each model."""

from dataclasses import dataclass, replace

from .errors import ProtocolError, UnsupportedPanelError, UnsupportedReason
from .event_codes import FW51_EVENT_CODES

SCENARIOS = 30  # on every model
PHONE_NUMBERS = 15  # on every model
LABEL_LENGTH = 16
FIXED_OUTPUTS = 3  # O1-O3 on the panel board
# Which keypads, readers, expanders and sirens are enabled: one bit per
# device, at the start of EEPROM on every model; its length and the address
# of the general options just after it depend on the firmware (profile)
PERIPHERALS_ADDRESS = 0x00000
# The general options block is 10 bytes; bytes 6-9 are flags the panel sets
# while running, not programming, so only the first 6 are read (as the
# programming software does)
GENERAL_OPTIONS_READ_LENGTH = 6


@dataclass(frozen=True, slots=True)
class FirmwareInfo:
    """Contents of the 12-byte model string at RAM 0x4000."""

    version: str  # e.g. "6.07"
    major: int  # firmware family, e.g. 6
    model: str  # model number without leading zeros, e.g. "1050"
    text: str  # the whole string, trimmed

    @property
    def type_text(self) -> str:
        """The model number as the panel writes it, five digits ("01050")."""
        return self.model.zfill(5)

    @property
    def family(self) -> str | None:
        """The firmware family with its own memory layout: "6" or "5.1".

        None for older firmware (5.0x and below), which has no profile.
        """
        if self.major >= 6:
            return "6"
        if self.version.startswith("5.1"):
            return "5.1"
        return None


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
    area_records: int  # A x 7 bytes: delays and options of each area
    fixed_output_records: int  # O1-O3, 2 bytes each: monostable time, options
    thermostat_records: int  # 195 bytes per keypad: its thermostat
    # Profile not yet confirmed on a real panel
    experimental: bool = False
    sirens: int = 10  # siren labels, on every model
    # Bank 0 setup block: the enabled-peripherals bitmaps (firmware 6 adds
    # 2 bytes of video sensors), then the general options
    peripherals_length: int = 22
    general_options_address: int = 0x00017
    # Firmware 6 code of each of the panel's event codes (None: no such
    # code on firmware 6); None when the panel numbers them as firmware 6
    event_codes: tuple[int | None, ...] | None = None
    # The panel answers the output detail request (dimmer level); firmware 6
    output_detail: bool = True
    # Extra output options, L + 3 bytes (terminals, then O1-O3; bit 0 = home
    # automation output); None where the address is not known
    extra_output_options: int | None = None
    # The panel answers the keypad status block (thermostats on now);
    # firmware 6
    keypad_status: bool = True

    scenarios = SCENARIOS
    phone_numbers = PHONE_NUMBERS
    terminal_record_length = 12
    area_record_length = 7
    pin_length = 6

    @property
    def setup_block_length(self) -> int:
        """Bytes read from the start of EEPROM: peripherals and options."""
        return self.general_options_address + GENERAL_OPTIONS_READ_LENGTH

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

    @property
    def installer_code_index(self) -> int:
        """Index of the installer code in the panel's PIN table.

        The table holds the user codes, then two more entries; the first of
        them is the installer code. pin_table_length covers the user codes
        only, so the integration's PIN lookup never reads it.
        """
        return self.codes


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
    area_records=0x06126,
    fixed_output_records=0x05A2C,
    thermostat_records=0x18400,
    extra_output_options=0x08C7D,
)


SMARTLIVING_10100_FW6 = PanelModel(
    name="SmartLiving 10100",
    areas=15,
    terminals=100,
    physical_slots=240,
    codes=100,
    keypads=15,
    readers=30,
    expanders=40,
    keys=150,
    timers=20,
    scenario_record_length=10,
    zone_record_length=9,
    code_record_length=65,
    code_output_mask_length=14,
    event_ring_size=1000,
    pin_table=0x0A4CD,
    code_records=0x0A731,
    zone_records=0x089C5,
    terminal_records=0x090CD,
    physical_map=0x0CF75,
    scenario_records=0x0C81F,
    event_log=0x19204,
    label_bank=0x10000,
    area_records=0x0A36B,
    fixed_output_records=0x0957D,
    thermostat_records=0x18400,
    extra_output_options=0x0E7B9,
)

SMARTLIVING_505_FW6 = PanelModel(
    name="SmartLiving 505",
    areas=5,
    terminals=10,
    physical_slots=40,
    codes=30,
    keypads=5,
    readers=10,
    expanders=4,  # 5 label slots, at most 4 expanders
    keys=50,
    timers=10,
    scenario_record_length=5,
    zone_record_length=9,
    code_record_length=54,
    code_output_mask_length=3,
    event_ring_size=500,
    pin_table=0x03132,
    code_records=0x031F2,
    zone_records=0x028BD,
    terminal_records=0x02971,
    physical_map=0x03DE0,
    scenario_records=0x03B0A,
    event_log=0x0EE5C,
    label_bank=0,
    area_records=0x030E3,
    fixed_output_records=0x029E9,
    thermostat_records=0x06B00,
    extra_output_options=0x04CCE,
    experimental=True,
)


SMARTLIVING_515_FW6 = PanelModel(
    name="SmartLiving 515",
    areas=5,
    terminals=20,
    physical_slots=70,
    codes=30,
    keypads=5,
    readers=10,
    expanders=10,
    keys=50,
    timers=10,
    scenario_record_length=5,
    zone_record_length=9,
    code_record_length=55,
    code_output_mask_length=4,
    event_ring_size=500,
    pin_table=0x038C7,
    code_records=0x03987,
    zone_records=0x02F07,
    terminal_records=0x0306F,
    physical_map=0x04595,
    scenario_records=0x042BF,
    event_log=0x0EE5C,
    label_bank=0,
    area_records=0x03859,
    fixed_output_records=0x0315F,
    thermostat_records=0x06B00,
    extra_output_options=0x054BF,
    experimental=True,
)

_FW6_MODELS = {
    "505": SMARTLIVING_505_FW6,
    "515": SMARTLIVING_515_FW6,
    "1050": SMARTLIVING_1050_FW6,
    "10100": SMARTLIVING_10100_FW6,
}

# Firmware 5.1x: the same counts and record formats as firmware 6, other
# addresses; no video sensors, so the setup block is 2 bytes shorter; the
# 1050 keeps its labels in bank 0 and 500 events; other event numbering
_FW51 = {
    "peripherals_length": 20,
    "general_options_address": 0x00015,
    "event_codes": FW51_EVENT_CODES,
    "output_detail": False,
    "extra_output_options": None,
    "keypad_status": False,
    "experimental": True,
}

SMARTLIVING_505_FW51 = replace(
    SMARTLIVING_505_FW6,
    name="SmartLiving 505 (firmware 5.1)",
    pin_table=0x029DB,
    code_records=0x02A9B,
    zone_records=0x0217A,
    terminal_records=0x0222E,
    physical_map=0x03689,
    scenario_records=0x033B3,
    event_log=0x0EE5C,
    area_records=0x0298C,
    fixed_output_records=0x022A6,
    thermostat_records=0x07740,
    **_FW51,
)

SMARTLIVING_515_FW51 = replace(
    SMARTLIVING_515_FW6,
    name="SmartLiving 515 (firmware 5.1)",
    pin_table=0x03058,
    code_records=0x03118,
    zone_records=0x026AC,
    terminal_records=0x02814,
    physical_map=0x03D26,
    scenario_records=0x03A50,
    event_log=0x0EE5C,
    area_records=0x02FEA,
    fixed_output_records=0x02904,
    thermostat_records=0x07740,
    **_FW51,
)

SMARTLIVING_1050_FW51 = replace(
    SMARTLIVING_1050_FW6,
    name="SmartLiving 1050 (firmware 5.1)",
    pin_table=0x052AA,
    code_records=0x053E2,
    zone_records=0x0451A,
    terminal_records=0x0489E,
    physical_map=0x069B3,
    scenario_records=0x0648E,
    event_log=0x0EE5C,
    event_ring_size=500,
    label_bank=0,
    area_records=0x051DC,
    fixed_output_records=0x04AF6,
    thermostat_records=0x07740,
    **_FW51,
)

SMARTLIVING_10100_FW51 = replace(
    SMARTLIVING_10100_FW6,
    name="SmartLiving 10100 (firmware 5.1)",
    pin_table=0x08BC4,
    code_records=0x08E28,
    zone_records=0x070E4,
    terminal_records=0x077EC,
    physical_map=0x0B66C,
    scenario_records=0x0AF16,
    event_log=0x19004,
    area_records=0x08A62,
    fixed_output_records=0x07C9C,
    thermostat_records=0x18000,
    **_FW51,
)

_FW51_MODELS = {
    "505": SMARTLIVING_505_FW51,
    "515": SMARTLIVING_515_FW51,
    "1050": SMARTLIVING_1050_FW51,
    "10100": SMARTLIVING_10100_FW51,
}

_MODELS_BY_FAMILY = {"6": _FW6_MODELS, "5.1": _FW51_MODELS}


def model_for(info: FirmwareInfo) -> PanelModel:
    """The model profile for a panel, or UnsupportedPanelError.

    An unknown model is refused for its model, before its firmware.
    """
    if info.model not in _FW6_MODELS:
        raise UnsupportedPanelError(
            f"SmartLiving {info.model} is not supported yet", UnsupportedReason.MODEL
        )
    models = _MODELS_BY_FAMILY.get(info.family or "")
    if models is None:
        raise UnsupportedPanelError(
            f"firmware {info.version} is not supported (5.1 or newer is needed)",
            UnsupportedReason.FIRMWARE,
        )
    return models[info.model]


# The profile a panel without its own (another model, firmware 5.0x) is handled
# with: its configuration is read, and so must be decoded, with this layout
FALLBACK_MODEL = SMARTLIVING_1050_FW6


def model_for_or_fallback(
    info: FirmwareInfo, fallback: PanelModel | None = FALLBACK_MODEL
) -> tuple[PanelModel, UnsupportedPanelError | None]:
    """The model profile for a panel, else the fallback, with the reason.

    Without a fallback the UnsupportedPanelError is raised.
    """
    try:
        return model_for(info), None
    except UnsupportedPanelError as err:
        if fallback is None:
            raise
        return fallback, err
