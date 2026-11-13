"""State of the fake SmartLiving 1050 and how it is encoded on the wire."""

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


# Label offsets inside EEPROM bank 1. Areas, zones, keypads and scenarios match
# captured requests; the others are placed in the gaps between them.
LABEL_LAYOUT: dict[int, tuple[int, int]] = {
    # pointer register: (offset, number of labels)
    c.RAM_LABELS_AREAS: (0x0000, c.NUM_AREAS),
    c.RAM_LABELS_ZONES: (0x00A0, c.NUM_ZONES),
    c.RAM_LABELS_CODES: (0x06E0, c.NUM_CODES),
    c.RAM_LABELS_READERS: (0x0A00, 20),
    c.RAM_LABELS_KEYPADS: (0x0B40, c.NUM_KEYPADS),
    c.RAM_LABELS_EXPANDERS: (0x0BE0, 20),
    c.RAM_LABELS_KEYS: (0x0D20, 100),
    c.RAM_LABELS_PHONES: (0x1360, 15),
    c.RAM_LABELS_SCENARIOS: (0x1450, c.NUM_SCENARIOS),
    c.RAM_LABELS_TIMERS: (0x1630, 10),
    c.RAM_LABELS_OUTPUTS: (0x16D0, 3),
}


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


@dataclass
class PanelState:
    """Configuration and live state of a fake SmartLiving 1050."""

    firmware: str = "6.07 01050  "
    brand: int = 0
    hardware_signature: bytes = b"\xb2\x55"

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
    scenario_records: list[bytes] = field(
        default_factory=lambda: [bytes(c.SCENARIO_RECORD_LENGTH)] * c.NUM_SCENARIOS
    )
    labels: dict[int, list[str]] = field(default_factory=dict)

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
    faults: bytes = bytes((0, 0)) + (910).to_bytes(2, "little") + bytes(3)  # 13.8 V
    keypad_temperatures: dict[int, float] = field(default_factory=dict)
    command_result: int = c.RESULT_OK
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

    # ------------------------------------------------------------------ memory

    def ram_read(self, address: int, length: int) -> bytes:
        """Read plain RAM (registers with special framing are handled by the server)."""
        dynamic = {
            c.RAM_AREA_STATUS: self.encode_area_status,
            c.RAM_ZONE_STATE: self.encode_zone_state,
            c.RAM_ZONE_INCLUSION: self.encode_zone_inclusion,
            c.RAM_ZONE_MEMORY: self.encode_zone_memory,
            c.RAM_COMMAND_RESULT: lambda: bytes((self.command_result,)),
            c.RAM_FAULTS: lambda: self.faults,
        }
        if address in dynamic:
            data = dynamic[address]()
        else:
            data = self._static_ram().get(address, b"")
        return data[:length].ljust(length, b"\x00")

    def _static_ram(self) -> dict[int, bytes]:
        ram = {
            c.RAM_FIRMWARE: self.firmware.encode("ascii"),
            c.RAM_BRAND: self.brand.to_bytes(2, "little"),
            c.RAM_HW_SIGNATURE: self.hardware_signature,
        }
        for register, (offset, _count) in LABEL_LAYOUT.items():
            ram[register] = offset.to_bytes(2, "little")
        return ram

    def eeprom_image(self) -> bytearray:
        """Build the EEPROM contents from the configuration and the event log."""
        image = bytearray(0x20000)

        def put(address: int, data: bytes) -> None:
            image[address : address + len(data)] = data

        for index, pin in enumerate(self.pins):
            put(c.EEPROM_PINS + index * c.PIN_LENGTH, encode_pin(pin))
        for index, record in enumerate(self.code_records):
            put(c.EEPROM_CODES + index * c.CODE_RECORD_LENGTH, record)
        for index, areas in enumerate(self.zone_areas):
            record = areas.to_bytes(2, "little") + bytes(c.ZONE_RECORD_LENGTH - 2)
            put(c.EEPROM_ZONES + index * c.ZONE_RECORD_LENGTH, record)
        for index, terminal_type in enumerate(self.terminal_types):
            record = bytes(c.TERMINAL_TYPE_OFFSET) + bytes((terminal_type,))
            put(c.EEPROM_TERMINALS + index * c.TERMINAL_RECORD_LENGTH, record)
        put(c.EEPROM_PHYSICAL_MAP, bytes(self.physical_map))
        for index, record in enumerate(self.scenario_records):
            put(c.EEPROM_SCENARIO_MODES + index * c.SCENARIO_RECORD_LENGTH, record)
        for register, (offset, count) in LABEL_LAYOUT.items():
            names = self.labels.get(register, [])
            data = b"".join(
                _label(names[i] if i < len(names) else "") for i in range(count)
            )
            put(c.LABEL_BANK + offset, data)
        for index, event in enumerate(self.events):
            if event is not None:
                put(c.EEPROM_EVENTS + index * c.EVENT_RECORD_LENGTH, event.to_bytes())
        return image

    def eeprom_read(self, address: int, length: int) -> bytes:
        """Read EEPROM using a 24-bit address."""
        return bytes(self.eeprom_image()[address : address + length])

    # ---------------------------------------------------------------- encoders

    def encode_area_status(self) -> bytes:
        """Reply of RAM 0x2000: area mode nibbles, then five 16-bit area masks."""
        modes = bytearray(b"\x44" * c.AREA_MODE_BYTES)  # unused nibbles read as 4
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
        out = bytearray(c.ZONE_STATE_LENGTH - 1)
        for zone, state in enumerate(self.zone_states):
            out[zone // 4] |= state << (2 * (zone % 4))
        return bytes(out) + bytes((self.zone_state_tail,))

    def encode_zone_inclusion(self) -> bytes:
        """Reply of RAM 0x2002: 1 bit per zone (1 = included), then unknown bytes."""
        return _bitmap(self.zone_included, c.ZONE_BITMAP_LENGTH, pad=True) + (
            self.zone_inclusion_tail
        )

    def encode_zone_memory(self) -> bytes:
        """Reply of RAM 0x2003: zone alarm memory, unknown bytes, output image."""
        output_bits = [
            self.outputs.get(terminal, 0) > 0 for terminal in range(c.NUM_TERMINALS)
        ] + list(self.fixed_outputs)
        output_length = c.ZONE_MEMORY_LENGTH - c.OUTPUT_IMAGE_OFFSET
        return (
            _bitmap(self.zone_memory, c.ZONE_BITMAP_LENGTH)
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
        else:
            areas = [selector & 0x1F]
        bits = [
            any(self._zone_open_in_area(zone, area) for area in areas)
            for zone in range(c.NUM_ZONES)
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
        event = self.events[index % c.EVENT_RING_SIZE]
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
        return any(self._zone_open_in_area(zone, area) for zone in range(c.NUM_ZONES))

    @staticmethod
    def _scenario_actions(record: bytes) -> dict[int, int]:
        actions = {}
        for area in range(1, c.NUM_AREAS + 1):
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
        self.event_head = (self.event_head + 1) % c.EVENT_RING_SIZE

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
            action != c.MODE_DISARMED and self._area_has_open_zones(area)
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
        for zone in range(c.NUM_ZONES):
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
        if slot >= c.NUM_PHYSICAL_SLOTS:
            return c.RESULT_SLOT_OUT_OF_RANGE
        terminal = self.physical_map[slot]
        if terminal == 0xFF:
            return c.RESULT_SLOT_NOT_CONNECTED
        if self.terminal_types[terminal] == c.TERMINAL_NULL:
            return c.RESULT_NULL_TERMINAL
        if second and self.terminal_types[terminal] != c.TERMINAL_DOUBLE:
            return c.RESULT_DOUBLE_ZONE
        zone = terminal + (c.NUM_TERMINALS if second else 0)
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
        if slot >= c.NUM_PHYSICAL_SLOTS:
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
            action != c.MODE_DISARMED and self._area_has_open_zones(area)
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
        return _mask_bits(mask, c.NUM_AREAS)


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
