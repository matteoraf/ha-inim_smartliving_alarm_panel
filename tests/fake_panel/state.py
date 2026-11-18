"""State of the fake SmartLiving panel and how it is encoded on the wire."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import captures, const as c


def checksum(data: bytes) -> int:
    """Sum of all bytes, modulo 256."""
    return sum(data) & 0xFF


def encode_pin(pin: str | None) -> bytes:
    """Encode a PIN as six bytes, one digit per byte, padded with 0xFF."""
    if pin is None:
        return b"\xff" * c.PIN_LENGTH
    digits = bytes(int(digit) for digit in pin)
    return digits + b"\xff" * (c.PIN_LENGTH - len(digits))


def _label(text: str) -> bytes:
    return text.encode("ascii")[: c.LABEL_LENGTH].ljust(c.LABEL_LENGTH, b" ")


def _bitmap(bits: list[bool], length: int, pad: bool = False) -> bytes:
    out = bytearray(b"\xff" * length if pad else bytes(length))
    for index in range(length * 8):
        value = bits[index] if index < len(bits) else pad
        if value:
            out[index // 8] |= 1 << (index % 8)
        else:
            out[index // 8] &= ~(1 << (index % 8)) & 0xFF
    return bytes(out)


def _mask_bits(mask: int, count: int) -> list[int]:
    """1-based numbers of the set bits of a mask."""
    return [bit + 1 for bit in range(count) if mask >> bit & 1]


@dataclass
class EventRecord:
    """One entry of the event log."""

    timestamp: int  # seconds since 2000-01-01, panel local time
    areas: int  # area bitmask
    agent: int
    location: int
    code: int  # event code, bit 7 set for an activation
    text: str = ""
    agent_text: str = ""
    location_text: str = ""

    def to_bytes(self) -> bytes:
        """The 9-byte record stored in EEPROM."""
        return (
            self.timestamp.to_bytes(4, "little")
            + self.areas.to_bytes(2, "little")
            + bytes((self.agent, self.location, self.code))
        )

    @classmethod
    def from_bytes(cls, data: bytes, text: str = "") -> EventRecord:
        """Parse a 9-byte record."""
        return cls(
            timestamp=int.from_bytes(data[0:4], "little"),
            areas=int.from_bytes(data[4:6], "little"),
            agent=data[6],
            location=data[7],
            code=data[8],
            text=text,
        )


# An area without delays: pre-arm warning 3 min, patrol 5 min, reset the alarm
# memory on arming and clear the call queue on disarm (as on a real 1050)
AREA_RECORD = bytes((0x00, 0x00, 0x00, 0x03, 0x00, 0x05, 0xA0))


@dataclass
class PanelState:
    """Configuration and live state of a fake SmartLiving panel (1050 by default).

    Lists sized by the model are created for the layout given, e.g.
    PanelState(layout=c.LAYOUT_10100).
    """

    layout: c.Layout = c.LAYOUT_1050
    firmware: str = ""  # default: "6.07 " + the layout's model field
    brand: int = 0
    hardware_signature: bytes = b"\xb2\x55"
    serial_number: str = "0000000000000001"  # made up; real ones are 16 digits

    # Configuration
    pins: list[str | None] = field(default_factory=lambda: [None] * c.NUM_CODES)
    code_records: list[bytes] = field(
        default_factory=lambda: [bytes(c.CODE_RECORD_LENGTH)] * c.NUM_CODES
    )
    terminal_types: list[int] = field(
        default_factory=lambda: [c.TERMINAL_NULL] * c.NUM_TERMINALS
    )
    physical_map: list[int] = field(
        default_factory=lambda: [0xFF] * c.NUM_PHYSICAL_SLOTS
    )
    zone_areas: list[int] = field(default_factory=lambda: [0] * c.NUM_ZONES)
    # Zone record options (byte 3) and options 2 (byte 4), by 0-based zone
    zone_options: dict[int, tuple[int, int]] = field(default_factory=dict)
    # Whole zone (9-byte) and terminal (12-byte) records, by 0-based index,
    # in place of the ones built from the fields above
    zone_records: dict[int, bytes] = field(default_factory=dict)
    terminal_records: dict[int, bytes] = field(default_factory=dict)
    # Area records (7 bytes) by 0-based area; the others read as AREA_RECORD
    area_records: dict[int, bytes] = field(default_factory=dict)
    scenario_records: list[bytes] = field(
        default_factory=lambda: [bytes(c.SCENARIO_RECORD_LENGTH)] * c.NUM_SCENARIOS
    )
    labels: dict[int, list[str]] = field(default_factory=dict)
    # Enabled devices: kind ("keypads", "readers", ...) -> 0-based numbers
    peripherals: dict[str, list[int]] = field(default_factory=dict)

    # Live state
    area_modes: list[int] = field(
        default_factory=lambda: [c.MODE_DISARMED] * c.NUM_AREAS
    )
    area_alarm: int = 0
    area_tamper: int = 0
    area_alarm_memory: int = 0
    area_tamper_memory: int = 0
    area_auto_arm: int = 0xFFFF
    zone_states: list[int] = field(default_factory=lambda: [c.ZONE_REST] * c.NUM_ZONES)
    zone_included: list[bool] = field(default_factory=lambda: [True] * c.NUM_ZONES)
    zone_memory: list[bool] = field(default_factory=lambda: [False] * c.NUM_ZONES)
    outputs: dict[int, int] = field(default_factory=dict)  # logical terminal -> value
    fixed_outputs: list[bool] = field(default_factory=lambda: [False] * 3)
    active_scenario: int | None = None
    # Fault flags and supply voltage (13.8 V): bytes 0-3 of the faults register,
    # which ends with the panel state flags
    faults: bytes = bytes((0, 0)) + (910).to_bytes(2, "little")
    keypad_temperatures: dict[int, float] = field(default_factory=dict)
    command_result: int = c.RESULT_OK
    # Arming an area (directly or by scenario) with a zone not at rest is
    # accepted, as a real 1050 does; True refuses it with the "open zones"
    # result, as a panel programmed not to allow arming with zones not ready
    block_arming_when_not_ready: bool = False
    # Programming mode: shown in the panel state flags; commands are ignored
    # and the result register keeps the previous result, as on a real panel
    in_programming: bool = False
    # Enter programming mode when the next command arrives (before it is run)
    enter_programming_on_command: bool = False
    voice_board: bool = True
    sirens_stopped: int = 0
    calls_cleared: int = 0

    # Bytes seen on the hardware whose meaning is unknown
    zone_state_tail: int = 0x4E
    zone_inclusion_tail: bytes = bytes(12) + b"\x40"
    zone_memory_middle: bytes = bytes(c.NUM_TERMINALS // 8 + 1)

    # Event log ring
    events: list[EventRecord | None] = field(
        default_factory=lambda: [None] * c.EVENT_RING_SIZE
    )
    event_head: int = 0
    event_tail: int = 0
    clock: int = 847787415  # seconds since 2000-01-01 (2026-11-12 08:30:15)

    def __post_init__(self) -> None:
        """Size the model-dependent lists for the layout."""
        lay = self.layout
        if not self.firmware:
            self.firmware = f"6.07 {lay.model}  "
        sized = {
            "pins": (lay.codes, None),
            "code_records": (lay.codes, bytes(lay.code_record_length)),
            "terminal_types": (lay.terminals, c.TERMINAL_NULL),
            "physical_map": (lay.physical_slots, 0xFF),
            "zone_areas": (lay.zones, 0),
            "scenario_records": (c.NUM_SCENARIOS, bytes(lay.scenario_record_length)),
            "area_modes": (lay.areas, c.MODE_DISARMED),
            "zone_states": (lay.zones, c.ZONE_REST),
            "zone_included": (lay.zones, True),
            "zone_memory": (lay.zones, False),
            "events": (lay.event_ring_size, None),
        }
        for name, (count, fill) in sized.items():
            if len(getattr(self, name)) != count:
                setattr(self, name, [fill] * count)
        if len(self.zone_memory_middle) != lay.terminal_bitmap_length:
            self.zone_memory_middle = bytes(lay.terminal_bitmap_length)
        if len(self.zone_inclusion_tail) != lay.zone_bitmap_length:
            self.zone_inclusion_tail = bytes(lay.zone_bitmap_length)

    # ------------------------------------------------------------------ memory

    def ram_read(self, address: int, length: int) -> bytes:
        """Read RAM (registers with special framing are handled by the server).

        The status registers are built on request and answer at their own
        address only. All other RAM is flat, as on the real panel: a read may
        start anywhere, e.g. inside the model string, and unknown bytes are 0.
        """
        dynamic = {
            c.RAM_AREA_STATUS: self.encode_area_status,
            c.RAM_ZONE_STATE: self.encode_zone_state,
            c.RAM_ZONE_INCLUSION: self.encode_zone_inclusion,
            c.RAM_ZONE_MEMORY: self.encode_zone_memory,
            c.RAM_COMMAND_RESULT: lambda: bytes((self.command_result,)),
            c.RAM_FAULTS: lambda: self.faults + self.encode_panel_state(),
        }
        if address in dynamic:
            return dynamic[address]()[:length].ljust(length, b"\x00")
        memory: dict[int, int] = {}
        for start, data in self._static_ram().items():
            for offset, byte in enumerate(data):
                memory[start + offset] = byte
        return bytes(memory.get(a, 0) for a in range(address, address + length))

    def _static_ram(self) -> dict[int, bytes]:
        """Blocks of plain RAM by start address."""
        ram = {
            c.RAM_FIRMWARE: self.firmware.encode("ascii"),
            c.RAM_SERIAL_NUMBER: self.serial_number.encode("ascii"),
            c.RAM_HW_SIGNATURE: self.hardware_signature,
            c.RAM_BRAND: self.brand.to_bytes(2, "little"),
            c.RAM_PANEL_STATE: self.encode_panel_state(),
        }
        for register, (offset, _count) in self.layout.labels.items():
            ram[register] = offset.to_bytes(2, "little")
        return ram

    def encode_panel_state(self) -> bytes:
        """Panel state flags (stato_centrale)."""
        flags = c.PANEL_STATE_UNKNOWN_BITS
        if not self.armed_areas():
            flags |= c.PANEL_STATE_NO_AREA_ARMED
        if self.in_programming:
            flags |= c.PANEL_STATE_PROGRAMMING
        if not self.voice_board:
            flags |= c.PANEL_STATE_NO_VOICE_BOARD
        return bytes((c.PANEL_STATE_UNKNOWN_BYTE_0, flags))

    def eeprom_image(self) -> bytearray:
        """Build the EEPROM contents from the configuration and the event log."""
        image = bytearray(0x20000)
        lay = self.layout

        def put(address: int, data: bytes) -> None:
            image[address : address + len(data)] = data

        if self.block_arming_when_not_ready:
            put(c.EEPROM_GENERAL_OPTIONS + 1, b"\x20")
        for kind, numbers in self.peripherals.items():
            offset, length = c.PERIPHERAL_BITMAPS[kind]
            bitmap = sum(1 << number for number in numbers)
            put(c.EEPROM_PERIPHERALS + offset, bitmap.to_bytes(length, "little"))
        for index, pin in enumerate(self.pins):
            put(lay.eeprom_pins + index * c.PIN_LENGTH, encode_pin(pin))
        for index, record in enumerate(self.code_records):
            put(lay.eeprom_codes + index * lay.code_record_length, record)
        for index, areas in enumerate(self.zone_areas):
            options = self.zone_options.get(index, (0, 0))
            record = (
                areas.to_bytes(2, "little")
                + bytes((0, *options))
                + bytes(c.ZONE_RECORD_LENGTH - 5)
            )
            record = self.zone_records.get(index, record)
            put(lay.eeprom_zones + index * c.ZONE_RECORD_LENGTH, record)
        for index, terminal_type in enumerate(self.terminal_types):
            record = self.terminal_records.get(
                index, bytes(c.TERMINAL_TYPE_OFFSET) + bytes((terminal_type,))
            )
            put(lay.eeprom_terminals + index * c.TERMINAL_RECORD_LENGTH, record)
        put(lay.eeprom_physical_map, bytes(self.physical_map))
        for index in range(lay.areas):
            record = self.area_records.get(index, AREA_RECORD)
            put(lay.eeprom_areas + index * len(AREA_RECORD), record)
        for index, record in enumerate(self.scenario_records):
            put(lay.eeprom_scenario_modes + index * lay.scenario_record_length, record)
        for register, (offset, count) in lay.labels.items():
            names = self.labels.get(register, [])
            data = b"".join(
                _label(names[i] if i < len(names) else "") for i in range(count)
            )
            put(c.LABEL_BANK + offset, data)
        for index, event in enumerate(self.events):
            if event is not None:
                put(lay.eeprom_events + index * c.EVENT_RECORD_LENGTH, event.to_bytes())
        return image

    def eeprom_read(self, address: int, length: int) -> bytes:
        """Read EEPROM using a 24-bit address."""
        return bytes(self.eeprom_image()[address : address + length])

    # ---------------------------------------------------------------- encoders

    def encode_area_status(self) -> bytes:
        """Reply of RAM 0x2000: area mode nibbles, then five 16-bit area masks."""
        modes = bytearray(
            b"\x44" * self.layout.area_mode_bytes
        )  # unused nibbles read as 4
        for index, mode in enumerate(self.area_modes):
            shift = 4 if index % 2 else 0
            modes[index // 2] = modes[index // 2] & ~(0x0F << shift) | mode << shift
        masks = (
            self.area_alarm,
            self.area_tamper,
            self.area_alarm_memory,
            self.area_tamper_memory,
            self.area_auto_arm,
        )
        return bytes(modes) + b"".join(mask.to_bytes(2, "little") for mask in masks)

    def encode_zone_state(self) -> bytes:
        """Reply of RAM 0x2001: 2 bits per zone, four zones per byte, LSB first."""
        out = bytearray(self.layout.zone_state_length - 1)
        for zone, state in enumerate(self.zone_states):
            out[zone // 4] |= state << (2 * (zone % 4))
        return bytes(out) + bytes((self.zone_state_tail,))

    def encode_zone_inclusion(self) -> bytes:
        """Reply of RAM 0x2002: 1 bit per zone (1 = included), then unknown bytes."""
        return _bitmap(self.zone_included, self.layout.zone_bitmap_length, pad=True) + (
            self.zone_inclusion_tail
        )

    def encode_zone_memory(self) -> bytes:
        """Reply of RAM 0x2003: zone alarm memory, unknown bytes, output image."""
        output_bits = [
            self.outputs.get(terminal, 0) > 0
            for terminal in range(self.layout.terminals)
        ] + list(self.fixed_outputs)
        output_length = self.layout.zone_memory_length - self.layout.output_image_offset
        return (
            _bitmap(self.zone_memory, self.layout.zone_bitmap_length)
            + self.zone_memory_middle
            + _bitmap(output_bits, output_length)
        )

    def not_ready_zones(self, selector: int, length: int) -> bytes:
        """Reply of RAM 0x1FF9: zones that would stop a scenario or an area arming."""
        if selector & 0x80:
            record = self.scenario_records[selector & 0x7F]
            areas = [
                area
                for area, action in self._scenario_actions(record).items()
                if action in (c.MODE_AWAY, c.MODE_STAY, c.MODE_INSTANT)
            ]
        else:  # one area, 0-based, in the mode of bits 5-6 (not used here)
            areas = [(selector & 0x1F) + 1]
        bits = [
            any(self._zone_open_in_area(zone, area) for area in areas)
            for zone in range(self.layout.zones)
        ]
        return _bitmap(bits, length)

    def active_scenario_for(self, area_mask: int) -> int:
        """Reply of RAM 0x1FFB: scenario matching the current state of the areas."""
        if self.active_scenario is not None:
            return self.active_scenario
        for index, record in enumerate(self.scenario_records):
            actions = {
                area: action
                for area, action in self._scenario_actions(record).items()
                if area_mask >> (area - 1) & 1
            }
            if actions and all(
                self.area_modes[area - 1] == action for area, action in actions.items()
            ):
                return index
        return c.NO_SCENARIO

    def output_detail(self, pin_index: int, terminal: int) -> bytes:
        """Reply of RAM 0x1FFD: type, two 16-bit values, phase, level, status."""
        value = self.outputs.get(terminal, 0)
        return bytes((5, 0, 0, 0, 0, 0, value & 0x7F, 1 if value else 0))

    def event_text(self, index: int) -> bytes:
        """Reply of RAM 0x1FFF: an event with its texts already resolved."""
        event = self.events[index % self.layout.event_ring_size]
        if event is None:
            return bytes(c.EVENT_TEXT_LENGTH)
        area_names = self.labels.get(c.RAM_LABELS_AREAS, [])
        out = bytearray(c.EVENT_TEXT_LENGTH)
        out[0:4] = event.timestamp.to_bytes(4, "little")
        out[4:6] = event.areas.to_bytes(2, "little")
        out[6:22] = _label(event.text)
        out[26:42] = _label(event.agent_text)
        out[46:62] = _label(event.location_text)
        for area in range(15):
            name = (
                area_names[area]
                if event.areas >> area & 1 and area < len(area_names)
                else ""
            )
            out[66 + 20 * area : 82 + 20 * area] = _label(name)
        return bytes(out)

    def event_pointers(self) -> bytes:
        """Reply of RAM 0x1FFE: head and tail of the event ring."""
        return self.event_head.to_bytes(2, "little") + self.event_tail.to_bytes(
            2, "little"
        )

    def keypad_temperature(self, keypad: int) -> bytes:
        """Peripheral read of a keypad temperature, tenths of a degree."""
        tenths = round(self.keypad_temperatures.get(keypad, 0.0) * 10)
        return tenths.to_bytes(2, "little", signed=True)

    # ---------------------------------------------------------------- commands

    def _code_index(self, pin: bytes) -> int | None:
        for index, stored in enumerate(self.pins):
            if stored is not None and encode_pin(stored) == pin:
                return index
        return None

    def _code_area_mask(self, code: int) -> int:
        return int.from_bytes(self.code_records[code][0:2], "little")

    def _zone_open_in_area(self, zone: int, area: int) -> bool:
        return (
            self.zone_included[zone]
            and self.zone_states[zone] != c.ZONE_REST
            and bool(self.zone_areas[zone] >> (area - 1) & 1)
        )

    def _area_has_open_zones(self, area: int) -> bool:
        return any(
            self._zone_open_in_area(zone, area) for zone in range(self.layout.zones)
        )

    def _blocks_arming(self, area: int) -> bool:
        return self.block_arming_when_not_ready and self._area_has_open_zones(area)

    def _scenario_actions(self, record: bytes) -> dict[int, int]:
        actions = {}
        for area in range(1, self.layout.areas + 1):
            byte = record[(area - 1) // 2]
            action = byte >> 4 if area % 2 == 0 else byte & 0x0F
            if action:
                actions[area] = action
        return actions

    def _log(self, areas: int, agent: int, code: int, text: str) -> None:
        self.events[self.event_head] = EventRecord(
            timestamp=self.clock,
            areas=areas,
            agent=agent,
            location=c.LOCATION_NETWORK,
            code=code,
            text=text,
        )
        self.event_head = (self.event_head + 1) % self.layout.event_ring_size

    def execute_arm(self, payload: bytes) -> int:
        """Register 0x2006: PIN + one nibble per area."""
        code = self._code_index(payload[0:6])
        if code is None:
            return c.RESULT_INVALID_CODE
        requested = self._scenario_actions(payload[6:14])
        allowed = self._code_area_mask(code)
        if any(not allowed >> (area - 1) & 1 for area in requested):
            return c.RESULT_CODE_NOT_ENABLED
        if any(
            action != c.MODE_DISARMED and self._blocks_arming(area)
            for area, action in requested.items()
        ):
            return c.RESULT_OPEN_ZONES
        armed = disarmed = 0
        for area, action in requested.items():
            self.area_modes[area - 1] = action
            if action == c.MODE_DISARMED:
                disarmed |= 1 << (area - 1)
            else:
                armed |= 1 << (area - 1)
        self.active_scenario = None
        if armed:
            self._log(
                armed, code, c.EVENT_AREA_ARMED | c.EVENT_ACTIVATION, "Area armed"
            )
        if disarmed:
            self._log(
                disarmed,
                code,
                c.EVENT_AREA_DISARMED | c.EVENT_ACTIVATION,
                "Area disarmed",
            )
        return c.RESULT_OK

    def execute_reset(self, payload: bytes) -> int:
        """Register 0x2008: PIN + 16-bit area mask."""
        code = self._code_index(payload[0:6])
        if code is None:
            return c.RESULT_INVALID_CODE
        areas = int.from_bytes(payload[6:8], "little")
        if areas & ~self._code_area_mask(code):
            return c.RESULT_OPEN_ZONES  # "code not enabled" for this command
        self.area_alarm_memory &= ~areas
        self.area_tamper_memory &= ~areas
        for zone in range(self.layout.zones):
            if self.zone_areas[zone] & areas:
                self.zone_memory[zone] = False
        self._log(areas, code, c.EVENT_AREA_RESET | c.EVENT_ACTIVATION, "Area reset")
        return c.RESULT_OK

    def execute_bypass(self, payload: bytes) -> int:
        """Register 0x2009: PIN + physical slot + second-zone flag + action."""
        code = self._code_index(payload[0:6])
        if code is None:
            return c.RESULT_INVALID_CODE
        slot, second, action = payload[6], payload[7], payload[8]
        if slot >= self.layout.physical_slots:
            return c.RESULT_SLOT_OUT_OF_RANGE
        terminal = self.physical_map[slot]
        if terminal == 0xFF:
            return c.RESULT_SLOT_NOT_CONNECTED
        if self.terminal_types[terminal] == c.TERMINAL_NULL:
            return c.RESULT_NULL_TERMINAL
        if second and self.terminal_types[terminal] != c.TERMINAL_DOUBLE:
            return c.RESULT_DOUBLE_ZONE
        zone = terminal + (self.layout.terminals if second else 0)
        exclude = action == 1
        if self.zone_included[zone] != exclude:
            return c.RESULT_ALREADY_IN_STATE
        self.zone_included[zone] = not exclude
        self._log(
            self.zone_areas[zone],
            zone,
            c.EVENT_ZONE_BYPASS | (c.EVENT_ACTIVATION if exclude else 0),
            "Zone bypassed" if exclude else "Zone included",
        )
        return c.RESULT_OK

    def execute_output(self, payload: bytes) -> int:
        """Register 0x2007: PIN + physical slot + value."""
        code = self._code_index(payload[0:6])
        if code is None:
            return c.RESULT_INVALID_CODE
        slot, value = payload[6], payload[7]
        if slot >= self.layout.physical_slots:
            return c.RESULT_SLOT_OUT_OF_RANGE
        terminal = self.physical_map[slot]
        if terminal == 0xFF:
            return c.RESULT_SLOT_NOT_CONNECTED
        if self.terminal_types[terminal] not in (c.TERMINAL_OUTPUT, c.TERMINAL_IN_OUT):
            return c.RESULT_NOT_ENABLED
        mask = self.code_records[code][c.CODE_OUTPUT_MASK_OFFSET + 1 + terminal // 8]
        if not mask >> (terminal % 8) & 1:
            return c.RESULT_NOT_ENABLED
        if self.outputs.get(terminal, 0) == value:
            return c.RESULT_ALREADY_IN_STATE
        self.outputs[terminal] = value
        return c.RESULT_OK

    def execute_scenario(self, payload: bytes) -> int:
        """Register 0x200A: PIN + scenario number."""
        code = self._code_index(payload[0:6])
        if code is None:
            return c.RESULT_INVALID_CODE
        scenario = payload[6]
        if scenario >= c.NUM_SCENARIOS:
            return c.RESULT_CODE_NOT_ENABLED
        actions = self._scenario_actions(self.scenario_records[scenario])
        allowed = self._code_area_mask(code)
        if any(not allowed >> (area - 1) & 1 for area in actions):
            return c.RESULT_CODE_NOT_ENABLED
        if any(
            action != c.MODE_DISARMED and self._blocks_arming(area)
            for area, action in actions.items()
        ):
            return c.RESULT_OPEN_ZONES
        for area, action in actions.items():
            self.area_modes[area - 1] = action
        self.active_scenario = scenario
        areas = sum(1 << (area - 1) for area in actions)
        self._log(areas, scenario, c.EVENT_SCENARIO | c.EVENT_ACTIVATION, "Scenario")
        return c.RESULT_OK

    def execute_stop(self, payload: bytes) -> int:
        """Register 0x200B: PIN + 0 (stop sirens) or 1 (clear call queue)."""
        if self._code_index(payload[0:6]) is None:
            return c.RESULT_INVALID_CODE
        if payload[6] == 1:
            self.calls_cleared += 1
        else:
            self.sirens_stopped += 1
        return c.RESULT_OK

    # ------------------------------------------------------------ inspection

    def armed_areas(self) -> list[int]:
        """1-based numbers of the areas not disarmed."""
        return [
            index + 1
            for index, mode in enumerate(self.area_modes)
            if mode not in (c.MODE_DISARMED, c.MODE_NONE)
        ]

    def area_numbers(self, mask: int) -> list[int]:
        """1-based area numbers of a mask."""
        return _mask_bits(mask, self.layout.areas)


def panel_1050() -> PanelState:
    """A 1050 configured like the panel the captures in captures.py come from."""
    state = PanelState()

    state.pins[0:3] = ["1234", "4321", "5678"]
    state.code_records[0] = captures.CODE_RECORD_0_REPLY[:-1]
    state.code_records[1] = captures.CODE_RECORD_1_REPLY[:-1]
    state.code_records[2] = (0x0001).to_bytes(2, "little") + bytes(
        c.CODE_RECORD_LENGTH - 2
    )

    # Physical slots 0-34 carry logical terminals 0-34; two keypad terminals
    # sit at slots 110 and 112 (logical 35 and 36).
    state.physical_map = list(captures.PHYSICAL_MAP_REPLY[:-1])
    for terminal in range(37):
        state.terminal_types[terminal] = c.TERMINAL_INPUT

    # Area of each zone (bitmask). Zone 20 is in area 1, zone 24 in area 4 and
    # zone 36 in area 7, as in the captured events.
    for zone in range(37):
        if zone < 20:
            area = 1
        elif zone < 23:
            area = 2
        elif zone < 28:
            area = 4
        elif zone < 32:
            area = 5
        elif zone < 35:
            area = 6
        elif zone == 35:
            area = 7
        else:
            area = 3
        state.zone_areas[zone] = 1 << (area - 1)

    prefix = captures.SCENARIO_MODES_PREFIX
    for index in range(len(prefix) // c.SCENARIO_RECORD_LENGTH):
        start = index * c.SCENARIO_RECORD_LENGTH
        state.scenario_records[index] = prefix[start : start + c.SCENARIO_RECORD_LENGTH]

    state.labels = {
        c.RAM_LABELS_AREAS: [f"Area {n}" for n in range(1, c.NUM_AREAS + 1)],
        c.RAM_LABELS_ZONES: [f"Zone {n}" for n in range(1, c.NUM_ZONES + 1)],
        c.RAM_LABELS_CODES: ["Master"]
        + [f"User {n}" for n in range(2, c.NUM_CODES + 1)],
        c.RAM_LABELS_KEYPADS: ["Tast Zona Giorno", "Tast Garage"],
        c.RAM_LABELS_SCENARIOS: [
            f"Scenario {n}" for n in range(1, c.NUM_SCENARIOS + 1)
        ],
        c.RAM_LABELS_READERS: ["PK Giorno", "PK Garage"],
        c.RAM_LABELS_KEYS: ["Key Anna", "Key Marco"],
        c.RAM_LABELS_EXPANDERS: ["Exp Kitchen"],
        c.RAM_LABELS_TIMERS: ["Timer 1"],
        c.RAM_LABELS_SIRENS: ["Outdoor siren"],
    }
    # Two keypads, three readers (the third without a name), one expander
    # and one siren
    state.peripherals = {
        "keypads": [0, 1],
        "readers": [0, 1, 2],
        "expanders": [0],
        "sirens": [0],
    }
    state.keypad_temperatures = {0: 24.5, 1: 27.7}

    # Captured events, newest at ring index 467 (head 0x01D4, tail 0x01D5).
    samples = sorted(
        (
            EventRecord.from_bytes(bytes.fromhex(raw)[:9], text)
            for raw, text in captures.EVENT_SAMPLES
        ),
        key=lambda event: event.timestamp,
    )
    state.event_head = 0x01D4
    state.event_tail = 0x01D5
    for offset, event in enumerate(reversed(samples)):
        state.events[(state.event_head - 1 - offset) % c.EVENT_RING_SIZE] = event

    return state


def panel_10100() -> PanelState:
    """A 10100 (firmware 6.x) with every area, 60 input terminals and scenarios.

    Not from captures: a made-up configuration. PINs as on
    the 1050 fake; code 0 (1234) has all 15 areas, code 1 (4321) areas 1-7,
    code 2 (5678) area 15 only. Zone n (0-based) is in area n % 15 + 1;
    terminals 0-59 are inputs on physical slots 0-59.
    """
    lay = c.LAYOUT_10100
    state = PanelState(layout=lay)

    state.pins[0:3] = ["1234", "4321", "5678"]
    for code, mask in enumerate((0x7FFF, 0x007F, 0x4000)):
        state.code_records[code] = mask.to_bytes(2, "little") + bytes(
            lay.code_record_length - 2
        )

    for terminal in range(60):
        state.terminal_types[terminal] = c.TERMINAL_INPUT
        state.physical_map[terminal] = terminal
    for zone in range(lay.zones):
        state.zone_areas[zone] = 1 << (zone % lay.areas)

    def scenario(modes: dict[int, int]) -> bytes:
        record = bytearray(lay.scenario_record_length)
        for area, mode in modes.items():
            shift = 4 if area % 2 == 0 else 0
            record[(area - 1) // 2] |= mode << shift
        return bytes(record)

    every_area = range(1, lay.areas + 1)
    state.scenario_records[0] = scenario(dict.fromkeys(every_area, c.MODE_AWAY))
    state.scenario_records[1] = scenario(dict.fromkeys(every_area, c.MODE_DISARMED))
    state.scenario_records[2] = scenario({15: c.MODE_AWAY})

    state.labels = {
        c.RAM_LABELS_AREAS: [f"Area {n}" for n in range(1, lay.areas + 1)],
        c.RAM_LABELS_ZONES: [f"Zone {n}" for n in range(1, lay.zones + 1)],
        c.RAM_LABELS_CODES: ["Master"] + [f"User {n}" for n in range(2, lay.codes + 1)],
        c.RAM_LABELS_KEYPADS: [f"Keypad {n}" for n in range(1, 4)],
        c.RAM_LABELS_SCENARIOS: [
            f"Scenario {n}" for n in range(1, c.NUM_SCENARIOS + 1)
        ],
    }
    # Keypads 1-3 and 15, readers 1 and 30, expanders 1 and 40
    state.peripherals = {
        "keypads": [0, 1, 2, 14],
        "readers": [0, 29],
        "expanders": [0, 39],
    }
    state.active_scenario = None
    return state
