"""The raw blocks of a report: showing them, and comparing two reports.

Rows are as in the report: an address, then 16 bytes in hex, "xx" for a
masked byte. Two reports are compared only where both show a byte, so
reports of different levels can be compared too.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

MASKED = "xx"


@dataclass(slots=True)
class BlockDiff:
    """How one block differs between two reports."""

    name: str
    not_comparable: str | None = None  # why, when one side has no rows
    changed_rows: list[tuple[str, str, str]] = field(default_factory=list)
    changed_bytes: int = 0


def blocks_by_name(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The report's blocks by name; empty if the section was left out."""
    blocks = report.get("blocks")
    if not isinstance(blocks, list):
        return {}
    return {b.get("name"): b for b in blocks if isinstance(b, Mapping)}


def block_header(block: Mapping[str, Any]) -> str:
    """A block's name and where it comes from."""
    parts = [
        str(block.get("name")),
        f"{block.get('space')} {block.get('address')}",
        f"{block.get('length')} bytes",
        str(block.get("privacy")),
    ]
    if block.get("read_at"):
        parts.append(f"read at {block['read_at']}")
    if block.get("source"):
        parts.append(str(block["source"]))
    return ", ".join(parts)


def why_no_rows(block: Mapping[str, Any]) -> str | None:
    """Why a block has no rows, or None if it has some."""
    if block.get("rows"):
        return None
    for key in ("withheld", "not_read", "error"):
        if block.get(key):
            return f"{key.replace('_', ' ')}: {block[key]}"
    return "no data"


def _parse(rows: list[str]) -> dict[str, list[str]]:
    parsed = {}
    for row in rows:
        address, _, cells = row.partition(": ")
        parsed[address] = cells.split()
    return parsed


def diff_block(name: str, a: Mapping[str, Any], b: Mapping[str, Any]) -> BlockDiff:
    """Compare one block byte by byte where both reports show the byte."""
    result = BlockDiff(name)
    why = why_no_rows(a) or why_no_rows(b)
    if why:
        result.not_comparable = why
        return result
    rows_a, rows_b = _parse(a.get("rows") or []), _parse(b.get("rows") or [])
    for address in sorted(set(rows_a) | set(rows_b)):
        cells_a, cells_b = rows_a.get(address), rows_b.get(address)
        if cells_a is None or cells_b is None:
            continue  # the block's length differs: only common rows
        changed = sum(
            1
            for x, y in zip(cells_a, cells_b, strict=False)
            if MASKED not in (x, y) and x != y
        )
        if changed:
            result.changed_bytes += changed
            marks = " ".join(
                "^^" if MASKED not in (x, y) and x != y else "  "
                for x, y in zip(cells_a, cells_b, strict=False)
            )
            result.changed_rows.append(
                (
                    f"{address}: {' '.join(cells_a)}",
                    f"{address}: {' '.join(cells_b)}",
                    marks,
                )
            )
    return result


def diff_blocks(a: Mapping[str, Any], b: Mapping[str, Any]) -> Iterator[BlockDiff]:
    """Every block both reports name, compared."""
    blocks_a, blocks_b = blocks_by_name(a), blocks_by_name(b)
    for name in blocks_a:
        if name in blocks_b:
            yield diff_block(name, blocks_a[name], blocks_b[name])
    for name in blocks_b:
        if name not in blocks_a:
            yield BlockDiff(name, not_comparable="only in the second report")
    for name in blocks_a:
        if name not in blocks_b:
            yield BlockDiff(name, not_comparable="only in the first report")


def flatten(value: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    """Every leaf of decoded data as (path, value)."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from flatten(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from flatten(item, f"{path}[{index}]")
    else:
        yield path, value


def comparable(value: Any) -> bool:
    """Whether a decoded value is shown in full (not cut or hidden)."""
    if isinstance(value, str):
        return not (
            value.endswith("…")
            or value.startswith("…")
            or "(hidden)" in value
            or value.startswith("xx:")
            or value.startswith("[withheld")
        )
    return True


def diff_decoded(a: Any, b: Any) -> list[tuple[str, Any, Any]]:
    """Leaves that differ, where both reports show them in full."""
    if not isinstance(a, Mapping) or not isinstance(b, Mapping):
        return []
    if "withheld" in a or "withheld" in b or a.get("error") or b.get("error"):
        return []
    left, right = dict(flatten(a)), dict(flatten(b))
    return [
        (path, left[path], right[path])
        for path in sorted(set(left) & set(right))
        if comparable(left[path])
        and comparable(right[path])
        and left[path] != right[path]
    ]
