"""Which version of the documentation site a manifest version belongs to.

The site keeps one version per branch: ``dev`` for the version under
development (``next``), ``X.Y`` for a release (its beta and stable builds).
Each language has its own versions: English at the site's root, Italian
under ``it/``. The Documentation workflow publishes with these values, and
the tests check that the README and the manifest link to the same version.

    python3 scripts/docs_version.py            # the site version, e.g. dev
    python3 scripts/docs_version.py --shell    # NAME, TITLE, STABLE, BETA, NOTICE,
                                               # LINK_EN, LINK_IT
    python3 scripts/docs_version.py --shell --language it   # Italian title, banner
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
LANGUAGES = ("en", "it")
"""The site's languages: English first, at the site's root."""
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
DEV_NOTICE_IT = (
    "Questa è la documentazione della versione in sviluppo: descrive modifiche "
    "non ancora rilasciate. Scegli la tua versione dal menu in alto."
)
BETA_NOTICE_IT = (
    "Questa è la documentazione della beta {name}, una versione non ancora "
    "definitiva. Scegli la tua versione dal menu in alto."
)

_NOTICES = {"en": (DEV_NOTICE, BETA_NOTICE), "it": (DEV_NOTICE_IT, BETA_NOTICE_IT)}
_DEV_TITLES = {"en": "dev (unreleased)", "it": "dev (non rilasciata)"}


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
    language: str = "en"
    """The language of the pages: one of ``LANGUAGES``."""

    @property
    def url(self) -> str:
        """The address of this version's home page."""
        return self.link(self.language)

    def link(self, language: str) -> str:
        """The address of this version's home page in a language."""
        prefix = "" if language == LANGUAGES[0] else f"{language}/"
        return f"{SITE_URL}{prefix}{self.name}/"

    def default(self, latest_published: bool, dev_only: bool = False) -> str:
        """The site's default version, as this version's pages are built.

        Each language has its own default. Pages of a version other than
        the default say so and link to the latest version. Until a stable
        version is published (no ``latest`` yet), a beta is the default, so
        its pages don't point away from themselves; while a language has
        only the version under development (``dev_only``), that is.
        """
        if self.stable or latest_published:
            return "latest"
        if self.beta:
            return self.name
        if dev_only:
            return self.name
        return "latest"


def default_pattern(default: str) -> str:
    """The default version as the theme's setting expects it.

    The theme reads its default versions as regular expressions, and the
    setting is read as YAML, where ``0.4`` would be a number: an anchored
    pattern such as ``^0\\.4$`` is a string and matches that version only.
    """
    return f"^{re.escape(default)}$"


def site_version(version: str, language: str = "en") -> SiteVersion:
    """The site version for a manifest version such as ``0.4.0b1``.

    Development versions and previews (``.devN``, ``aN``) are only ever on the
    development branch, so they are ``dev``. Betas and release candidates are
    ``X.Y (beta)``; stable versions are ``X.Y``. The title and the banner are
    in the given language.
    """
    if language not in LANGUAGES:
        raise ValueError(f"not a language of this site: {language!r}")
    match = _VERSION.match(version)
    if match is None:
        raise ValueError(f"not a version this site knows: {version!r}")
    dev_notice, beta_notice = _NOTICES[language]
    if match["dev_n"] is not None or match["pre"] == "a":
        title = _DEV_TITLES[language]
        return SiteVersion("dev", title, False, False, dev_notice, language)
    name = f"{match['major']}.{match['minor']}"
    if match["pre"] is not None:
        notice = beta_notice.format(name=name)
        return SiteVersion(name, f"{name} (beta)", False, True, notice, language)
    return SiteVersion(name, name, True, False, "", language)


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
        "--language",
        choices=LANGUAGES,
        default=LANGUAGES[0],
        help="the language of the title and the banner",
    )
    parser.add_argument(
        "--default",
        choices=["latest-published", "no-latest", "dev-only"],
        help=(
            "print the site's default version, given whether latest exists "
            "(dev-only: no latest, and no version other than dev yet)"
        ),
    )
    parser.add_argument(
        "--pattern",
        action="store_true",
        help="with --default: print it as the theme's setting expects it",
    )
    args = parser.parse_args(argv)
    found = site_version(args.version or manifest_version(), args.language)
    if args.default:
        default = found.default(
            args.default == "latest-published", args.default == "dev-only"
        )
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
        **{f"LINK_{language.upper()}": found.link(language) for language in LANGUAGES},
    }
    for key, value in values.items():
        print(f"{key}={shlex.quote(value)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
