"""Laying a report view out for the terminal."""

from collections.abc import Iterable

from .view import Cell, Section, State

_COLOURS = {
    State.MASKED: "\033[33m",  # yellow
    State.WITHHELD: "\033[31m",  # red
    State.MISSING: "\033[2m",  # dim
}
_RESET = "\033[0m"
_BOLD = "\033[1m"
MAX_WIDTH = 60  # longest cell before it is cut in a table


def _paint(cell: Cell, text: str, colour: bool) -> str:
    code = _COLOURS.get(cell.state)
    return f"{code}{text}{_RESET}" if colour and code else text


def _fit(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def render_section(section: Section, *, colour: bool) -> list[str]:
    """One section as lines."""
    title = f"{_BOLD}{section.title}{_RESET}" if colour else section.title
    lines = [title, "=" * len(section.title)]
    if section.note:
        lines.append(section.note)
    if not section.rows:
        return lines + [""]
    count = max(len(row) for row in section.rows)
    widths = [len(name) for name in section.columns[:count]]
    widths += [0] * (count - len(widths))
    for row in section.rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], min(len(cell.text), MAX_WIDTH))
    header = [name for name in section.columns if name]
    if header:
        lines.append(
            "  ".join(
                n.ljust(widths[i]) for i, n in enumerate(section.columns)
            ).rstrip()
        )
        lines.append("  ".join("-" * w for w in widths))
    for row in section.rows:
        cells = []
        for i, cell in enumerate(row):
            text = _fit(cell.text, MAX_WIDTH) if i < count - 1 else cell.text
            cells.append(
                _paint(cell, text.ljust(widths[i]) if i < count - 1 else text, colour)
            )
        lines.append("  ".join(cells).rstrip())
    return lines + [""]


def render(sections: Iterable[Section], *, colour: bool) -> str:
    """Every section, one after the other."""
    lines: list[str] = []
    for section in sections:
        lines += render_section(section, colour=colour)
    return "\n".join(lines)
