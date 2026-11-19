"""Reading report files and telling what they are.

A file can be the one Home Assistant downloads (the report under "data"),
a report on its own, or the output of `decrypt`.
"""

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
from typing import Any

REPORT_VERSION = 1
PRIVATE_COPY = "PRIVATE COPY"


class Kind(StrEnum):
    """What a file is, for sharing."""

    ENCRYPTED = "encrypted"  # the file to attach (Full levels)
    REDACTED = "redacted"  # safe to share
    PRIVATE_COPY = "private_copy"  # the readable copy: never share
    PLAIN_FULL = "plain_full"  # a readable Full report, e.g. decrypted
    NOT_A_REPORT = "not_a_report"


SHAREABLE = frozenset({Kind.ENCRYPTED, Kind.REDACTED})


class ReportFileError(Exception):
    """The file cannot be read as a report."""


@dataclass(frozen=True, slots=True)
class ReportFile:
    """A report file as read."""

    path: Path
    kind: Kind
    document: dict[str, Any]  # the report, or the encrypted envelope
    findings: tuple[str, ...] = ()

    @property
    def report(self) -> dict[str, Any] | None:
        """The readable report: the Redacted view of an encrypted file."""
        if self.kind is Kind.ENCRYPTED:
            return self.document.get("redacted_view")
        if self.kind is Kind.NOT_A_REPORT:
            return None
        return self.document


def classify(document: Any) -> tuple[Kind, tuple[str, ...]]:
    """The kind of a report document, with what looks wrong in it."""
    if not isinstance(document, dict):
        return Kind.NOT_A_REPORT, ("not a JSON object",)
    findings = []
    version = document.get("report_version")
    if version is None:
        return Kind.NOT_A_REPORT, ("no report_version",)
    if version != REPORT_VERSION:
        findings.append(f"report_version {version} is not known to this viewer")
    if "encrypted" in document:
        if not isinstance(document.get("redacted_view"), dict):
            findings.append("encrypted file without its Redacted view")
        return Kind.ENCRYPTED, tuple(findings)
    errors = document.get("errors") or []
    if any(isinstance(e, dict) and e.get("error") == "self-check" for e in errors):
        findings.append("parts were left out by the privacy check")
    if PRIVATE_COPY in document:
        return Kind.PRIVATE_COPY, tuple(findings)
    if document.get("level") == "redacted":
        return Kind.REDACTED, tuple(findings)
    return Kind.PLAIN_FULL, tuple(findings)


def unwrap(data: Any) -> Any:
    """The report inside a Home Assistant diagnostics file, else the data."""
    if isinstance(data, dict) and "report_version" not in data:
        inner = data.get("data")
        if isinstance(inner, dict):
            return inner
    return data


def read(path: str | Path) -> ReportFile:
    """Read and classify a report file."""
    file = Path(path)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ReportFileError(f"{file}: cannot be read as JSON ({err})") from err
    document = unwrap(data)
    kind, findings = classify(document)
    return ReportFile(
        file, kind, document if isinstance(document, dict) else {}, findings
    )
