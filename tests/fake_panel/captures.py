"""Frames captured from a SmartLiving 1050 (firmware 6.07) over SmartLAN/SI.

Requests are the 8-byte frames sent by the client; replies include the
trailing checksum byte. The fake panel is tested against these bytes.
"""

FIRMWARE_REQUEST = bytes.fromhex("0000004000000c4c")
FIRMWARE_REPLY = bytes.fromhex("362e3037203031303530202021")  # "6.07 01050  "

BRAND_REQUEST = bytes.fromhex("000000fed20002d2")
BRAND_REPLY = bytes.fromhex("000000")

# First three PIN codes (1234, 4321, 5678) of the PIN table at EEPROM 0x061F4
PIN_TABLE_REQUEST = bytes.fromhex("00000161f400fa50")
PIN_TABLE_PREFIX = bytes.fromhex("01020304ffff04030201ffff05060708ffff")

# User code records 0 and 1 (59 bytes each) at EEPROM 0x0632C
CODE_RECORD_0_REQUEST = bytes.fromhex("000001632c003bcb")
CODE_RECORD_0_REPLY = bytes.fromhex(
    "ff03ffff00000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000004000700000000000000020d"
)
CODE_RECORD_1_REQUEST = bytes.fromhex("0000016367003b06")
CODE_RECORD_1_REPLY = bytes.fromhex(
    "7f00ff3f00000000000000000000000000000000000000000000000003000200"
    "0101010001020103010401050106110014000700000000000000000a"
)

# Physical slot -> logical terminal map (130 bytes) at EEPROM 0x078FD
PHYSICAL_MAP_REPLY = bytes.fromhex(
    "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f202122"
    + "ff" * 75
    + "23ff24"
    + "ff" * 17
    + "3d"
)

AREA_STATUS_REQUEST = bytes.fromhex("0000002000001030")
AREA_STATUS_ALL_DISARMED = bytes.fromhex("4444444444440000000000000000ffff96")
AREA_STATUS_AREA_5_ARMED = bytes.fromhex("4444414444440000000000000000ffff93")
AREA_STATUS_AREAS_3_TO_7_ARMED = bytes.fromhex("4411114144440000000000000000ffff2d")
AREA_STATUS_MEMORY_AREA_4 = bytes.fromhex("4444444444440000000008000000ffff9e")
AREA_STATUS_MEMORY_AREA_1 = bytes.fromhex("4444444444440000000001000000ffff97")

# The 26th byte is not zone data: it changed (4e -> 6f) with every zone at rest
ZONE_STATE_REQUEST = bytes.fromhex("0000002001001a3b")
ZONE_STATE_ALL_REST = bytes.fromhex("55" * 25 + "4e9b")
ZONE_STATE_ALL_REST_ALT_TAIL = bytes.fromhex("55" * 25 + "6fbc")

# The 26th byte is not zone data either (00, 40 and 60 were seen)
ZONE_INCLUSION_REQUEST = bytes.fromhex("0000002002001a3c")
ZONE_INCLUSION_ALL_INCLUDED = bytes.fromhex("ff" * 13 + "00" * 12 + "40" + "33")

ZONE_MEMORY_REQUEST = bytes.fromhex("0000002003001c3f")
ZONE_MEMORY_ZONE_24 = bytes.fromhex("000080" + "00" * 25 + "80")
ZONE_MEMORY_ZONE_20 = bytes.fromhex("000008" + "00" * 25 + "08")

# Scenario arming modes, first three records (8 bytes each) at EEPROM 0x073D8
SCENARIO_MODES_REQUEST = bytes.fromhex("00000173d800f03c")
SCENARIO_MODES_PREFIX = bytes.fromhex(
    "111111010000000044444444000024000100000100002700"
)

ACTIVE_SCENARIO_REQUEST = bytes.fromhex("0000001ffb7f019a")  # area mask 0x007F

# Zones not ready for scenario 0: zones 12, 14, 18, 19, 24 and 36 (1-based)
NOT_READY_REQUEST = bytes.fromhex("0000001ff9800da5")
NOT_READY_REPLY = bytes.fromhex("00288600080000000000000000b6")
NOT_READY_ZONES = (11, 13, 17, 18, 23, 35)  # 0-based logical zones

COMMAND_RESULT_REQUEST = bytes.fromhex("0000002004000125")

EVENT_POINTERS_REQUEST = bytes.fromhex("0000001ffe000421")
EVENT_POINTERS_REPLY = bytes.fromhex("d401d501ab")  # head 0x01D4, tail 0x01D5

KEYPAD_1_TEMPERATURE_REQUEST = bytes.fromhex("100001075a000274")
KEYPAD_1_TEMPERATURE_REPLY = bytes.fromhex("f500f5")  # 24.5 °C
KEYPAD_2_TEMPERATURE_REQUEST = bytes.fromhex("100101075a000275")
KEYPAD_2_TEMPERATURE_REPLY = bytes.fromhex("150116")  # 27.7 °C

# Command frames (header + payload) with PIN 1234 and the acknowledgement byte
ARM_AREA_3 = bytes.fromhex("0100002006000e3501020304ffff0001000000000000")
ARM_AREA_3_ACK = 0x09
DISARM_AREA_7 = bytes.fromhex("0100002006000e3501020304ffff0000000400000000")
DISARM_AREA_7_ACK = 0x0C
ACTIVATE_SCENARIO_0 = bytes.fromhex("010000200a00073201020304ffff00")
ACTIVATE_SCENARIO_0_ACK = 0x08
BYPASS_SLOT_23 = bytes.fromhex("010000200900093301020304ffff170001")
BYPASS_SLOT_23_ACK = 0x20
RESET_AREA_7 = bytes.fromhex("010000200800083101020304ffff4000")
RESET_AREA_7_ACK = 0x48

# Event records (9 bytes + checksum) and what the panel's own log showed for them
EVENT_SAMPLES: tuple[tuple[str, str], ...] = (
    ("bd03602fff0001009eed", "Scenario 1 activated"),
    ("bc03602f780000fe0ace", "End of areas 4-5-6-7 armed (Master)"),
    ("bc03602f780000fe8c50", "Areas 4-5-6-7 disarmed (Master)"),
    ("cba15f2f480005009ee5", "Scenario 5 activated"),
    ("cba15f2f700006009e0e", "Scenario 6 activated"),
    ("cba15f2f780008009e18", "Scenario 8 activated"),
    ("caa15f2f7800ff000c7c", "End of areas 4-5-6-7 disarmed (keypad Tast Zona Giorno)"),
    ("caa15f2f7800ff008afa", "Areas 4-5-6-7 armed (keypad Tast Zona Giorno)"),
    ("caa15f2f7800ffff8dfc", "Areas 4-5-6-7 reset"),
    ("c87c5f2f00000b001bf8", "End of failed call (Cloud)"),
    ("5c7c5f2f00000b009b0c", "Failed call (Cloud)"),
    ("6f545f2f00000000be0f", "Telephone line lost"),
    ("1d545f2fff0300008c8d", "Areas 1-10 disarmed (Master, keypad Tast Zona Giorno)"),
    ("1d545f2f000000004241", "Programming ended"),
    ("04345f2f00000001ba81", "Installer code (keypad Tast Garage)"),
    ("04345f2f000000005f25", "Call ended"),
    ("04345f2f00000000c288", "Programming started"),
    ("3215782f010013008082", "Zone 20 alarm, area 1"),
    ("3615782f010013000006", "End of zone 20 alarm, area 1"),
    ("c60d792f40002300805e", "Zone 36 alarm, area 7"),
    ("c90d792f4000230000e1", "End of zone 36 alarm, area 7"),
    ("6874e12f4000230085d4", "Zone 36 bypassed, area 7"),
    ("7c74e12f400023000568", "End of zone 36 bypass, area 7"),
)
# For "Zone 36 alarm", "Zone 36 bypassed" and "End of zone 36 bypass" only the
# 9 data bytes were captured; their checksum byte is computed.
