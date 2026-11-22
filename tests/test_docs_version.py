"""The documentation site's version for each manifest version."""

from pathlib import Path
import re
import shlex
import sys

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from docs_version import (  # noqa: E402
    BETA_NOTICE,
    DEV_NOTICE,
    SITE_URL,
    default_pattern,
    main,
    manifest_version,
    site_version,
)


@pytest.mark.parametrize("version", ["0.5.0.dev0", "0.5.0.dev12", "0.5.0a1", "1.2.0a3"])
def test_development_versions_are_dev(version: str) -> None:
    """The version under development and its previews are the dev version."""
    found = site_version(version)
    assert found.name == "dev"
    assert found.title == "dev (unreleased)"
    assert not found.stable
    assert not found.beta
    assert found.notice == DEV_NOTICE
    assert found.url == f"{SITE_URL}dev/"


@pytest.mark.parametrize(
    ("version", "name"),
    [
        ("0.4.0b1", "0.4"),
        ("0.4.0b12", "0.4"),
        ("0.4.0rc1", "0.4"),
        ("1.10.0b2", "1.10"),
    ],
)
def test_betas_are_their_release(version: str, name: str) -> None:
    """A beta or release candidate is its release's version, marked beta."""
    found = site_version(version)
    assert found.name == name
    assert found.title == f"{name} (beta)"
    assert found.beta
    assert not found.stable
    assert found.notice == BETA_NOTICE.format(name=name)
    assert f"the {name} beta" in found.notice


@pytest.mark.parametrize(
    ("version", "name"), [("0.4.0", "0.4"), ("0.4.1", "0.4"), ("2.0.3", "2.0")]
)
def test_stable_versions(version: str, name: str) -> None:
    """A stable version has no banner and is not a beta."""
    found = site_version(version)
    assert (found.name, found.title) == (name, name)
    assert found.stable
    assert not found.beta
    assert found.notice == ""
    assert found.url == f"{SITE_URL}{name}/"


@pytest.mark.parametrize(
    "version", ["", "0.4", "v0.4.0", "0.4.0-beta", "0.4.0b", "0.4.0.post1"]
)
def test_unknown_versions_are_refused(version: str) -> None:
    """A version the site has no rule for is an error, not a guess."""
    with pytest.raises(ValueError, match="not a version"):
        site_version(version)


def test_the_manifest_version_has_a_site_version() -> None:
    """The manifest's version follows the rules above."""
    site_version(manifest_version())


def test_shell_output(capsys: pytest.CaptureFixture[str]) -> None:
    """The workflow reads these assignments with eval."""
    assert main(["--shell", "--version", "0.4.0b1"]) == 0
    values = {}
    for line in capsys.readouterr().out.splitlines():
        key, _, value = line.partition("=")
        values[key] = " ".join(shlex.split(value))
    assert values == {
        "NAME": "0.4",
        "TITLE": "0.4 (beta)",
        "STABLE": "false",
        "BETA": "true",
        "NOTICE": BETA_NOTICE.format(name="0.4"),
    }


def test_plain_output(capsys: pytest.CaptureFixture[str]) -> None:
    """Without --shell, only the version's name."""
    assert main(["--version", "0.4.2"]) == 0
    assert capsys.readouterr().out == "0.4\n"


@pytest.mark.parametrize(
    ("version", "latest_published", "default"),
    [
        # Before the first stable version: the beta is the default.
        ("0.4.0b1", False, "0.4"),
        ("0.4.0rc1", False, "0.4"),
        # Once a stable version exists, latest is, also for the next beta.
        ("0.4.0b2", True, "latest"),
        ("0.5.0b1", True, "latest"),
        # Stable versions and the version under development: always latest.
        ("0.4.0", False, "latest"),
        ("0.4.0", True, "latest"),
        ("0.5.0.dev0", False, "latest"),
        ("0.5.0a1", True, "latest"),
    ],
)
def test_default_version(version: str, latest_published: bool, default: str) -> None:
    """The site's default, with which the version's pages are built."""
    assert site_version(version).default(latest_published) == default


def test_default_output(capsys: pytest.CaptureFixture[str]) -> None:
    """The workflow reads the default, and its pattern, from the command line."""
    assert main(["--version", "0.4.0b1", "--default", "no-latest"]) == 0
    assert main(["--version", "0.4.0b1", "--default", "latest-published"]) == 0
    assert main(["--version", "0.4.0b1", "--default", "no-latest", "--pattern"]) == 0
    assert capsys.readouterr().out == "0.4\nlatest\n^0\\.4$\n"


@pytest.mark.parametrize(
    ("default", "matches", "others"),
    [
        ("0.4", ["0.4"], ["0.45", "10.4", "0x4", "0.5", "dev", "latest"]),
        ("1.10", ["1.10"], ["1.1", "1.100", "11.10"]),
        ("latest", ["latest", "LATEST"], ["0.4", "dev", "not-latest"]),
    ],
)
def test_default_pattern(default: str, matches: list[str], others: list[str]) -> None:
    """The theme reads the default as YAML, then as a case-blind expression.

    As YAML, 0.4 would be the number 0.4 (and 1.10 would be 1.1): the
    pattern stays a string and matches its version only.
    """
    pattern = default_pattern(default)
    assert yaml.safe_load(pattern) == pattern
    for version in matches:
        assert re.search(pattern, version, re.IGNORECASE), version
    for version in others:
        assert not re.search(pattern, version, re.IGNORECASE), version
