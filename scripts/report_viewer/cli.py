"""The `inim_report` command line.

Reads report files only: nothing is sent anywhere. Output goes to the
terminal, or to a file the user names.
"""

import argparse
from collections.abc import Sequence
import json
import os
from pathlib import Path
import sys
from typing import TextIO

from . import blocks, files, html, keys, term, view
from .files import Kind

EXIT_OK = 0
EXIT_FOUND = 1  # check: do not share; diff: the reports differ
EXIT_INPUT = 2  # the input cannot be used
EXIT_FAILED = 3  # the command could not be done (key, cryptography)

_CHECK_TEXT = {
    Kind.ENCRYPTED: "Encrypted report (key ID {key_id}): this is the file to attach.",
    Kind.REDACTED: "Redacted report: safe to share.",
    Kind.PRIVATE_COPY: "PRIVATE COPY: do not share. Attach the encrypted file instead.",
    Kind.PLAIN_FULL: "Readable {level} report: do not share.",
    Kind.NOT_A_REPORT: "Not a system report.",
}


def _write(path: Path, text: str, *, force: bool, private: bool = False) -> None:
    """Write a file the user named; never over an existing one unless forced."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if force else os.O_EXCL)
    try:
        descriptor = os.open(path, flags, 0o600 if private else 0o644)
    except FileExistsError as err:
        raise FileExistsError(f"{path} exists: add --force to overwrite it") from err
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(text)


def _read(path: str, out: TextIO) -> files.ReportFile | None:
    try:
        return files.read(path)
    except files.ReportFileError as err:
        print(err, file=out)
        return None


def _decrypted(report_file: files.ReportFile, args: argparse.Namespace) -> dict:
    """The full report of an encrypted file, decrypted in memory only."""
    return keys.decrypt(
        report_file.document,
        key_path=Path(args.key) if args.key else None,
        folder=Path(args.key_dir) if args.key_dir else keys.KEY_FOLDER,
    )


def cmd_show(args: argparse.Namespace, out: TextIO) -> int:
    report_file = _read(args.file, sys.stderr)
    if report_file is None or report_file.report is None:
        print(f"{args.file}: not a system report", file=sys.stderr)
        return EXIT_INPUT
    report, kind = report_file.report, report_file.kind
    if args.decrypt:
        if kind is not Kind.ENCRYPTED:
            print(f"{args.file}: not an encrypted report", file=sys.stderr)
            return EXIT_INPUT
        try:
            # Nothing readable is written but the page the user names
            report, kind = _decrypted(report_file, args), Kind.PLAIN_FULL
        except keys.KeyFileError as err:
            print(err, file=sys.stderr)
            return EXIT_FAILED
    limit = None if args.events == "all" else int(args.events)
    note = (
        "The full report is encrypted; this is its Redacted view "
        "(--decrypt shows the full report)."
        if kind is Kind.ENCRYPTED
        else None
    )
    if args.html:
        content = view.page(
            report, kind, details=args.details, events_limit=limit, note=note
        )
        try:
            _write(
                Path(args.html),
                html.render(content),
                force=args.force,
                # A readable Full report holds names, and PINs when confidential
                private=kind not in files.SHAREABLE,
            )
        except FileExistsError as err:
            print(err, file=sys.stderr)
            return EXIT_INPUT
        print(f"Written: {args.html}", file=out)
        return EXIT_OK
    sections = view.report_view(report, kind, details=args.details, events_limit=limit)
    if note:
        sections[0].note = note
    colour = not args.no_color and out.isatty()
    print(term.render(sections, colour=colour), file=out)
    return EXIT_OK


def cmd_hex(args: argparse.Namespace, out: TextIO) -> int:
    report_file = _read(args.file, sys.stderr)
    if report_file is None or report_file.report is None:
        return EXIT_INPUT
    by_name = blocks.blocks_by_name(report_file.report)
    names = list(by_name) if args.all else args.block
    if not names:
        print(
            "name a block, or use --all; blocks: " + ", ".join(by_name), file=sys.stderr
        )
        return EXIT_INPUT
    unknown = [n for n in names if n not in by_name]
    if unknown:
        print(f"no such block: {', '.join(unknown)}", file=sys.stderr)
        return EXIT_INPUT
    for name in names:
        block = by_name[name]
        print(blocks.block_header(block), file=out)
        why = blocks.why_no_rows(block)
        if why:
            print(f"  [{why}]", file=out)
        for row in block.get("rows") or []:
            print(f"  {row}", file=out)
        print(file=out)
    return EXIT_OK


def cmd_diff(args: argparse.Namespace, out: TextIO) -> int:
    first, second = _read(args.first, sys.stderr), _read(args.second, sys.stderr)
    if first is None or second is None or first.report is None or second.report is None:
        return EXIT_INPUT
    a, b = first.report, second.report
    every = not (args.blocks or args.config or args.status)
    found = False
    if a.get("level") != b.get("level"):
        print(
            f"Levels differ ({a.get('level')} / {b.get('level')}): "
            "only what both show is compared.",
            file=out,
        )
    if every or args.blocks:
        for result in blocks.diff_blocks(a, b):
            if result.not_comparable:
                if result.not_comparable != "no data":
                    print(
                        f"{result.name}: not comparable ({result.not_comparable})",
                        file=out,
                    )
                continue
            if not result.changed_rows:
                continue
            found = True
            print(f"{result.name}: {result.changed_bytes} bytes differ", file=out)
            for left, right, marks in result.changed_rows:
                print(f"  - {left}", file=out)
                print(f"  + {right}", file=out)
                print(f"    {' ' * 7}{marks}", file=out)
    for part, wanted in (("configuration", args.config), ("status", args.status)):
        if not (every or wanted):
            continue
        for path, left, right in blocks.diff_decoded(a.get(part), b.get(part)):
            found = True
            print(f"{part}.{path}: {left!r} -> {right!r}", file=out)
    if not found:
        print("No differences in what both reports show.", file=out)
    return EXIT_FOUND if found else EXIT_OK


def cmd_check(args: argparse.Namespace, out: TextIO) -> int:
    report_file = _read(args.file, out)
    if report_file is None:
        return EXIT_INPUT
    document = report_file.document
    text = _CHECK_TEXT[report_file.kind].format(
        key_id=(document.get("encrypted") or {}).get("key_id", "?"),
        level=document.get("level", "Full"),
    )
    print(text, file=out)
    for finding in report_file.findings:
        print(f"  note: {finding}", file=out)
    if report_file.kind is Kind.NOT_A_REPORT:
        return EXIT_INPUT
    return EXIT_OK if report_file.kind in files.SHAREABLE else EXIT_FOUND


def cmd_decrypt(args: argparse.Namespace, out: TextIO) -> int:
    report_file = _read(args.file, sys.stderr)
    if report_file is None or report_file.kind is not Kind.ENCRYPTED:
        print(f"{args.file}: not an encrypted report", file=sys.stderr)
        return EXIT_INPUT
    try:
        report = _decrypted(report_file, args)
    except keys.KeyFileError as err:
        print(err, file=sys.stderr)
        return EXIT_FAILED
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        try:
            _write(Path(args.output), text + "\n", force=args.force, private=True)
        except FileExistsError as err:
            print(err, file=sys.stderr)
            return EXIT_INPUT
        print(
            f"Written: {args.output} (readable report: do not share)", file=sys.stderr
        )
    else:
        print(text, file=out)
    return EXIT_OK


def cmd_keygen(args: argparse.Namespace, out: TextIO) -> int:
    try:
        public, key_id = keys.keygen(Path(args.dir) if args.dir else keys.KEY_FOLDER)
    except keys.KeyFileError as err:
        print(err, file=sys.stderr)
        return EXIT_FAILED if "cryptography" in str(err) else EXIT_INPUT
    print(f"Public key: {public}", file=out)
    print(f"Key ID: {key_id}", file=out)
    return EXIT_OK


def parser() -> argparse.ArgumentParser:
    """The command line."""
    main = argparse.ArgumentParser(
        prog="inim_report",
        description="Read, compare and check SmartLiving system reports.",
    )
    sub = main.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="show a report")
    show.add_argument("file")
    show.add_argument("--html", metavar="OUT", help="write one HTML file instead")
    show.add_argument("--force", action="store_true", help="overwrite the HTML file")
    show.add_argument("--details", action="store_true", help="every attribute")
    show.add_argument("--events", default="50", help="how many events, or 'all'")
    show.add_argument("--no-color", action="store_true")
    show.add_argument(
        "--decrypt",
        action="store_true",
        help="show an encrypted report in full, decrypted in memory only",
    )
    show.add_argument("-k", "--key", help="the private key file (with --decrypt)")
    show.add_argument(
        "--key-dir", help="the folder of the private keys (with --decrypt)"
    )
    show.set_defaults(run=cmd_show)

    hexdump = sub.add_parser("hex", help="show raw blocks")
    hexdump.add_argument("file")
    hexdump.add_argument("block", nargs="*")
    hexdump.add_argument("--all", action="store_true", help="every block")
    hexdump.set_defaults(run=cmd_hex)

    diff = sub.add_parser("diff", help="compare two reports")
    diff.add_argument("first")
    diff.add_argument("second")
    diff.add_argument("--blocks", action="store_true", help="raw blocks only")
    diff.add_argument("--config", action="store_true", help="configuration only")
    diff.add_argument("--status", action="store_true", help="status only")
    diff.set_defaults(run=cmd_diff)

    check = sub.add_parser("check", help="say whether a file may be shared")
    check.add_argument("file")
    check.set_defaults(run=cmd_check)

    decrypt = sub.add_parser("decrypt", help="decrypt an encrypted report")
    decrypt.add_argument("file")
    decrypt.add_argument("-k", "--key", help="the private key file")
    decrypt.add_argument("--key-dir", help="the folder of the private keys")
    decrypt.add_argument("-o", "--output", help="write the report to this file")
    decrypt.add_argument("--force", action="store_true", help="overwrite the output")
    decrypt.set_defaults(run=cmd_decrypt)

    keygen = sub.add_parser("keygen", help="make a new key pair")
    keygen.add_argument("--dir", help="the folder of the private keys")
    keygen.set_defaults(run=cmd_keygen)
    return main


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """Run one command; returns its exit code."""
    args = parser().parse_args(argv)
    if args.command == "show" and args.events != "all" and not args.events.isdigit():
        print("--events takes a number or 'all'", file=sys.stderr)
        return EXIT_INPUT
    return args.run(args, out or sys.stdout)
