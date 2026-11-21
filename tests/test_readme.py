"""The README's sections left out of the Home Assistant Marketplace."""

from pathlib import Path
import re

README = Path(__file__).parent.parent / "README.md"

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
    """The headings around the section are still shown."""
    shown, _ = split_readme()
    for heading in (
        "Installation\n------------",
        "### Preview versions",
        "### Upgrading from 0.3.x",
        "Configuration\n-------------",
    ):
        assert heading in shown
