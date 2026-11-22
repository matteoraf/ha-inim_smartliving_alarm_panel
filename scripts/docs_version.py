"""Which version of the documentation site a manifest version belongs to.

The site keeps one version per branch: ``dev`` for the version under
development (``next``), ``X.Y`` for a release (its beta and stable builds).
The Documentation workflow publishes with these values, and the tests check
that the README and the manifest link to the same version.

    python3 scripts/docs_version.py            # the site version, e.g. dev
    python3 scripts/docs_version.py --shell    # NAME, TITLE, STABLE, BETA, NOTICE
    python3 scripts/docs_version.py --default no-latest   # the site's default
    python3 scripts/docs_version.py --default no-latest --pattern   # for mkdocs.yml
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shlex
import sys

SITE_URL = "https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/"
MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "inim_smartliving_alarm"
    / "manifest.json"
)

_VERSION = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:(?P<pre>a|b|rc)(?P<pre_n>\d+)|\.dev(?P<dev_n>\d+))?$"
)

DEV_NOTICE = (
    "This is the documentation of the version under development: it describes "
    "changes that are not released yet. Choose your version from the menu at "
    "the top."
)
BETA_NOTICE = (
    "This is the documentation of the {name} beta, a pre-release. Choose your "
    "version from the menu at the top."
)


@dataclass(frozen=True)
class SiteVersion:
    """One version of the documentation site."""

    name: str
    """The folder and the identifier on the site: ``dev`` or ``X.Y``."""
    title: str
    """The title in the version menu."""
    stable: bool
    """A stable release: it becomes ``latest`` and the default."""
    beta: bool
    """A beta or release candidate of a release."""
    notice: str
    """The banner shown on every page, empty for a stable release."""

    @property
    def url(self) -> str:
        """The address of this version's home page."""
        return f"{SITE_URL}{self.name}/"

    def default(self, latest_published: bool) -> str:
        """The site's default version, as this version's pages are built.

        Pages of a version other than the default say so and link to the
        latest version. Until a stable version is published (no ``latest``
        yet), a beta is the site's default, so its pages don't point away
        from themselves.
        """
        if self.beta and not latest_published:
            return self.name
        return "latest"


def default_pattern(default: str) -> str:
    """The default version as the theme's setting expects it.

    The theme reads its default versions as regular expressions, and the
    setting is read as YAML, where ``0.4`` would be a number: an anchored
    pattern such as ``^0\\.4$`` is a string and matches that version only.
    """
    return f"^{re.escape(default)}$"


def site_version(version: str) -> SiteVersion:
    """The site version for a manifest version such as ``0.4.0b1``.

    Development versions and previews (``.devN``, ``aN``) are only ever on the
    development branch, so they are ``dev``. Betas and release candidates are
    ``X.Y (beta)``; stable versions are ``X.Y``.
    """
    match = _VERSION.match(version)
    if match is None:
        raise ValueError(f"not a version this site knows: {version!r}")
    if match["dev_n"] is not None or match["pre"] == "a":
        return SiteVersion("dev", "dev (unreleased)", False, False, DEV_NOTICE)
    name = f"{match['major']}.{match['minor']}"
    if match["pre"] is not None:
        notice = BETA_NOTICE.format(name=name)
        return SiteVersion(name, f"{name} (beta)", False, True, notice)
    return SiteVersion(name, name, True, False, "")


def manifest_version(manifest: Path = MANIFEST) -> str:
    """The integration's version in its manifest."""
    return json.loads(manifest.read_text(encoding="utf-8"))["version"]


def main(argv: list[str] | None = None) -> int:
    """Print the site version of the manifest (or of ``--version``)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", help="a version instead of the manifest's")
    parser.add_argument(
        "--shell", action="store_true", help="print shell variable assignments"
    )
    parser.add_argument(
        "--default",
        choices=["latest-published", "no-latest"],
        help="print the site's default version, given whether latest exists",
    )
    parser.add_argument(
        "--pattern",
        action="store_true",
        help="with --default: print it as the theme's setting expects it",
    )
    args = parser.parse_args(argv)
    found = site_version(args.version or manifest_version())
    if args.default:
        default = found.default(args.default == "latest-published")
        print(default_pattern(default) if args.pattern else default)
        return 0
    if not args.shell:
        print(found.name)
        return 0
    values = {
        "NAME": found.name,
        "TITLE": found.title,
        "STABLE": str(found.stable).lower(),
        "BETA": str(found.beta).lower(),
        "NOTICE": found.notice,
    }
    for key, value in values.items():
        print(f"{key}={shlex.quote(value)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
