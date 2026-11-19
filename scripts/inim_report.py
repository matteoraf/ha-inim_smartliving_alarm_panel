#!/usr/bin/env python3
"""Read, compare and check the system reports of the Inim SmartLiving integration.

    python3 scripts/inim_report.py show REPORT.json
    python3 scripts/inim_report.py check REPORT.json

Run `python3 scripts/inim_report.py --help` for every command. It reads
files only; `decrypt` and `keygen` need the Python package cryptography.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report_viewer.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
