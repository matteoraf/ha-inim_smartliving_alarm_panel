"""Serve the demo panel the documentation's screenshots are taken from.

A made-up SmartLiving 1050 (firmware 6.07, SmartLAN/SI) with plain names,
served on this computer only, with a console to change its state:

    python3 scripts/docs_demo_panel.py            # panel on 127.0.0.1:15004
    nc 127.0.0.1 15099                            # console; type "help"

Taking the screenshots again (for example after an interface change):

1.  Start the demo panel with the test requirements installed.
2.  Start a separate Home Assistant (never your own installation) on
    another port, with this integration copied into its custom_components.
3.  Add the panel: "SmartLAN/SI board", host 127.0.0.1, port 15004,
    PIN 1234, panel name "Home"; keep what the items step proposes.
4.  Set the states each image needs with the console (for example
    "area 1 total", "zone 3 open"), and turn every analytics option off.
5.  Capture at twice the screen resolution and crop to the dialog or card.
6.  Save as WebP (lossless, at most 200 kB and 1600 px wide) in docs/images/,
    named as the image it replaces.
7.  Run: pytest tests/test_docs_assets.py tests/test_demo_panel.py
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.fake_panel import (  # noqa: E402
    FakePanel,
    const as c,  # noqa: E402
    panel_demo,
)

MODES = {
    "total": c.MODE_AWAY,
    "partial": c.MODE_STAY,
    "instant": c.MODE_INSTANT,
    "disarm": c.MODE_DISARMED,
}
ZONE_STATES = {
    "rest": c.ZONE_REST,
    "open": c.ZONE_ALARM,
    "alarm": c.ZONE_ALARM,
    "tamper": c.ZONE_TAMPER,
}
HELP = """commands:
  area N total|partial|instant|disarm   arm or disarm area N (1-10)
  zone N rest|open|alarm|tamper         set zone N (1-100); open in an armed
                                        area sets off its alarm
  bypass N on|off                       bypass zone N, or include it again
  reset                                 reset the alarm memories
  status                                areas, zones not at rest, bypasses
  quit                                  close the console"""


class Console:
    """Text commands that change the demo panel's state."""

    def __init__(self, panel: FakePanel) -> None:
        self.state = panel.state

    def area(self, number: int, mode: str) -> str:
        s = self.state
        s.area_modes[number - 1] = MODES[mode]
        event = c.EVENT_AREA_DISARMED if mode == "disarm" else c.EVENT_AREA_ARMED
        text = "Area disarmed" if mode == "disarm" else "Area armed"
        s._log(1 << (number - 1), 0, event | c.EVENT_ACTIVATION, text)
        return f"area {number}: {mode}"

    def zone(self, number: int, name: str) -> str:
        s = self.state
        index = number - 1
        s.zone_states[index] = ZONE_STATES[name]
        armed = sum(
            1 << area
            for area in range(c.NUM_AREAS)
            if s.zone_areas[index] >> area & 1 and s.area_modes[area] != c.MODE_DISARMED
        )
        if name in ("open", "alarm") and armed and s.zone_included[index]:
            s.area_alarm |= armed
            s.area_alarm_memory |= armed
            s.zone_memory[index] = True
            s._log(armed, index, c.EVENT_ZONE_ALARM | c.EVENT_ACTIVATION, "Zone alarm")
            return f"zone {number}: {name}, alarm in areas {s.area_numbers(armed)}"
        if name == "rest":
            s.area_alarm &= ~sum(
                1 << area
                for area in range(c.NUM_AREAS)
                if not any(
                    s.zone_areas[z] >> area & 1 and s.zone_states[z] == c.ZONE_ALARM
                    for z in range(c.NUM_ZONES)
                )
            )
        return f"zone {number}: {name}"

    def bypass(self, number: int, on: bool) -> str:
        self.state.zone_included[number - 1] = not on
        return f"zone {number}: {'bypassed' if on else 'included'}"

    def reset(self) -> str:
        s = self.state
        s.area_alarm_memory = s.area_tamper_memory = 0
        s.zone_memory = [False] * len(s.zone_memory)
        return "alarm memories reset"

    def status(self) -> str:
        s = self.state
        names = s.labels[c.RAM_LABELS_ZONES]
        lines = [
            f"armed areas: {s.armed_areas() or 'none'}",
            f"alarm in areas: {s.area_numbers(s.area_alarm) or 'none'}",
            f"alarm memory in areas: {s.area_numbers(s.area_alarm_memory) or 'none'}",
        ]
        for index, state in enumerate(s.zone_states):
            if state != c.ZONE_REST and names[index]:
                lines.append(f"zone {index + 1} ({names[index]}) not at rest")
        for index, included in enumerate(s.zone_included):
            if not included and names[index]:
                lines.append(f"zone {index + 1} ({names[index]}) bypassed")
        return "\n".join(lines)

    def run(self, line: str) -> str:
        words = line.split()
        try:
            match words:
                case ["area", number, mode] if mode in MODES:
                    return self.area(int(number), mode)
                case ["zone", number, name] if name in ZONE_STATES:
                    return self.zone(int(number), name)
                case ["bypass", number, ("on" | "off") as value]:
                    return self.bypass(int(number), value == "on")
                case ["reset"]:
                    return self.reset()
                case ["status"]:
                    return self.status()
                case []:
                    return ""
        except (ValueError, IndexError):
            pass
        return HELP


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=15004, help="panel port")
    parser.add_argument("--console", type=int, default=15099, help="console port")
    args = parser.parse_args()

    panel = FakePanel(panel_demo())
    await panel.start("127.0.0.1", args.port)
    console = Console(panel)

    async def session(reader, writer) -> None:
        writer.write(b"demo panel console; type help\n> ")
        while raw := await reader.readline():
            line = raw.decode(errors="replace").strip()
            if line in ("quit", "exit"):
                break
            writer.write((console.run(line) + "\n> ").encode())
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(session, "127.0.0.1", args.console)
    print(
        f"demo panel on 127.0.0.1:{args.port}, console on 127.0.0.1:{args.console}",
        flush=True,
    )
    try:
        await server.serve_forever()
    finally:
        await panel.stop()


if __name__ == "__main__":
    asyncio.run(main())
