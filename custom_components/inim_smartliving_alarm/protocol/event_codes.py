"""Event code numbering of the firmware families.

The integration works with firmware 6 codes (0-113). Firmware 5.1 has no
"area disarmed" code (12 on firmware 6) and no video-sensor events, so its
later codes are lower; its free events 1-14 (codes 97-110) have no
firmware 6 code.
"""

EVENT_CODES = (
    128  # 7 bits; the record's top bit says whether the event started or ended
)

# (first, last, firmware 6 code - firmware 5.1 code)
_FW51_RUNS = (
    (0, 11, 0),
    (12, 45, 1),
    (46, 51, 2),
    (52, 96, 3),
)


def _table(runs: tuple[tuple[int, int, int], ...]) -> tuple[int | None, ...]:
    table: list[int | None] = [None] * EVENT_CODES
    for first, last, shift in runs:
        for code in range(first, last + 1):
            table[code] = code + shift
    return tuple(table)


# Firmware 6 code of each firmware 5.1 code, None where there is none
FW51_EVENT_CODES = _table(_FW51_RUNS)

# Firmware 5.1's free events 1-14: codes with no firmware 6 code
FW51_FREE_EVENTS = range(97, 111)
