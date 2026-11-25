"""The README: the sections left out of the Marketplace, and the site links."""

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
MKDOCS = ROOT / "mkdocs.yml"
sys.path.insert(0, str(ROOT / "scripts"))

from docs_version import (  # noqa: E402
    MANIFEST,
    SITE_URL,
    manifest_version,
    site_version,
)

MARKER = re.compile(r"<!--\s*ha-marketplace:exclude-(start|end)\s*-->")
FENCE = re.compile(r"^\s*(```|~~~)")


def readme_lines() -> list[str]:
    """The README, line by line."""
    return README.read_text(encoding="utf-8").split("\n")


def markers() -> list[tuple[int, str]]:
    """Line numbers (0-based) and kinds of the exclude markers outside code."""
    found = []
    in_fence = False
    for number, line in enumerate(readme_lines()):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            found.extend((number, match.group(1)) for match in MARKER.finditer(line))
    return found


def split_readme() -> tuple[str, str]:
    """The README as the Marketplace shows it, and the part it leaves out."""
    lines = readme_lines()
    (start, _), (end, _) = markers()
    shown = lines[:start] + lines[end + 1 :]
    hidden = lines[start + 1 : end]
    return "\n".join(shown), "\n".join(hidden)


def test_one_section_is_left_out() -> None:
    """One start marker, then one end marker."""
    assert [kind for _, kind in markers()] == ["start", "end"]


def test_markers_stand_alone() -> None:
    """Each marker is a line of its own at the margin, after a blank line.

    Indented, a marker would be code or list text, and the Marketplace would
    show the section, or leave out the rest of the README.
    """
    lines = readme_lines()
    for number, kind in markers():
        assert lines[number] == f"<!-- ha-marketplace:exclude-{kind} -->"
        assert not lines[number - 1].strip(), f"no blank line before {kind}"


def test_installation_steps_are_left_out() -> None:
    """The button and the install steps are for GitHub only."""
    shown, hidden = split_readme()
    assert "my.home-assistant.io/redirect/marketplace_repository/" in hidden
    assert "as a custom repository of type **Integration**" in hidden
    assert "**Manual installation:**" in hidden
    assert "my.home-assistant.io" not in shown
    assert "custom repository" not in shown


def test_the_rest_stays() -> None:
    """The headings around the section and the documentation link are shown."""
    shown, _ = split_readme()
    for heading in (
        "Installation\n------------",
        "Documentation\n-------------",
        "Upgrading from 0.4\n------------------",
        "Upgrading from 0.3.x\n--------------------",
        "Sharing a system report\n-----------------------",
        "Troubleshooting\n---------------",
    ):
        assert heading in shown
    assert f"**Documentation: <{site_version(manifest_version()).url}>**" in shown


def test_site_links_match_the_version() -> None:
    """The README and the manifest link to the site's version of this release.

    The development branch links to the dev version, a release branch to its
    own version (0.4 for 0.4.0b1 and 0.4.0): a release changes the manifest
    version, and these links with it.
    """
    expected = site_version(manifest_version()).url
    links = re.findall(
        rf"{re.escape(SITE_URL)}[^)>\s]*", README.read_text(encoding="utf-8")
    )
    assert links
    for link in links:
        assert link.startswith(expected), link
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["documentation"] == expected


def heading_ids(page: Path) -> set[str]:
    """The ids the site gives the headings of a page (MkDocs' default slugs)."""
    ids = set()
    for heading in re.findall(r"^#+ (.+)$", page.read_text(encoding="utf-8"), re.M):
        explicit = re.search(r"\{ *#([\w-]+) *\}$", heading)
        if explicit:
            ids.add(explicit.group(1))
            continue
        slug = re.sub(r"[^\w\s-]", "", heading).strip().lower()
        ids.add(re.sub(r"[-\s]+", "-", slug))
    return ids


def test_site_links_point_to_pages() -> None:
    """Every page and heading the README links to exists on the site."""
    nav = set(
        re.findall(r"^  - [^:]+: (\S+)\.md$", MKDOCS.read_text(encoding="utf-8"), re.M)
    )
    base = site_version(manifest_version()).url
    links = re.findall(
        rf"{re.escape(base)}([^)>\s#]*)(?:#([\w-]+))?",
        README.read_text(encoding="utf-8"),
    )
    assert links
    for path, anchor in links:
        page = path.strip("/") or "index"
        assert page in nav, path
        if anchor:
            assert anchor in heading_ids(ROOT / "docs" / f"{page}.md"), (path, anchor)
