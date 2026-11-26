"""The Italian pages match the English pages.

The site is written in English and translated into Italian (``docs/it/``,
built by ``mkdocs.it.yml``). The words are checked by people; these tests
check the rest: the same pages in the same order, the same heading ids,
images, links, identifiers and tables, and the same site configuration
apart from the language.
"""

from collections import Counter
from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ITALIAN = DOCS / "it"
CONFIG = ROOT / "mkdocs.yml"
CONFIG_IT = ROOT / "mkdocs.it.yml"

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6}) (.*?)\s*$")
HEADING_ID = re.compile(r"\{ #([\w-]+) \}$")
IMAGE = re.compile(r"!\[[^\]]*\]\((images/[^)\s]+)\)(\{[^}]*\})?")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
CODE = re.compile(r"`([^`\n]+)`")
# Code that names something in Home Assistant or the panel, which stays the
# same in every language: entity IDs, attributes and their values, events,
# actions, options.
IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*")


class _Loader(yaml.SafeLoader):
    """Reads the site configuration, keeping its !ENV tags as they are."""


_Loader.add_constructor(
    "!ENV", lambda loader, node: ("!ENV", loader.construct_sequence(node))
)


def config(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)


def nav_files(path: Path) -> list[str]:
    return [next(iter(item.values())) for item in config(path)["nav"]]


ENGLISH_PAGES = nav_files(CONFIG)


def lines(page: Path) -> list[str]:
    """The lines of a page outside code blocks."""
    found, in_fence = [], False
    for line in page.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            found.append(line)
    return found


def headings(page: Path) -> list[tuple[int, str | None]]:
    """Each heading's level and explicit id (None if it has none)."""
    found = []
    for line in lines(page):
        match = HEADING.match(line)
        if match:
            explicit = HEADING_ID.search(match.group(2))
            found.append((len(match.group(1)), explicit and explicit.group(1)))
    return found


def text(page: Path) -> str:
    return "\n".join(lines(page))


def tables(page: Path) -> list[list[int]]:
    """The tables of a page: the number of cells in each row."""
    found: list[list[int]] = []
    rows: list[int] = []
    for line in [*lines(page), ""]:
        if line.strip().startswith("|"):
            rows.append(len(line.strip().strip("|").split("|")))
        elif rows:
            found.append(rows)
            rows = []
    return found


def test_the_same_pages_in_the_same_order() -> None:
    """Every English page has an Italian page with the same file name."""
    assert nav_files(CONFIG_IT) == ENGLISH_PAGES
    assert {page.name for page in ITALIAN.glob("*.md")} == set(ENGLISH_PAGES)
    assert {page.name for page in DOCS.glob("*.md")} == set(ENGLISH_PAGES)


def test_the_same_configuration_apart_from_the_language() -> None:
    """mkdocs.it.yml is mkdocs.yml with the Italian names and folder."""
    english, italian = config(CONFIG), config(CONFIG_IT)
    assert english["exclude_docs"].split() == ["it/"]
    assert italian["site_url"] == english["site_url"] + "it/"
    assert (english["docs_dir"], italian["docs_dir"]) == ("docs", "docs/it")
    assert (english["theme"]["language"], italian["theme"]["language"]) == ("en", "it")
    for site in (english, italian):
        del site["site_name"], site["site_url"], site["docs_dir"], site["nav"]
        site.pop("exclude_docs", None)
        del site["theme"]["language"]
        for scheme in site["theme"]["palette"]:
            del scheme["toggle"]["name"]
        for link in site["extra"]["social"]:
            del link["name"]
    assert italian == english


@pytest.mark.parametrize("name", ENGLISH_PAGES)
def test_every_heading_has_its_id(name: str) -> None:
    """The same ids in both languages, so links and the language switch keep
    their place on the page."""
    english, italian = headings(DOCS / name), headings(ITALIAN / name)
    assert all(heading_id for _, heading_id in english), english
    assert italian == english


@pytest.mark.parametrize("name", ENGLISH_PAGES)
def test_the_same_images(name: str) -> None:
    """The same images, in the same order and at the same sizes."""
    assert IMAGE.findall(text(ITALIAN / name)) == IMAGE.findall(text(DOCS / name))


@pytest.mark.parametrize("name", ENGLISH_PAGES)
def test_the_same_links(name: str) -> None:
    """The same pages, headings and addresses are linked."""
    english = Counter(LINK.findall(text(DOCS / name)))
    italian = Counter(LINK.findall(text(ITALIAN / name)))
    assert italian == english


@pytest.mark.parametrize("name", ENGLISH_PAGES)
def test_the_same_identifiers(name: str) -> None:
    """Names of entities, attributes, events and options are not translated."""

    def identifiers(page: Path) -> set[str]:
        return {code for code in CODE.findall(text(page)) if IDENTIFIER.fullmatch(code)}

    assert identifiers(ITALIAN / name) == identifiers(DOCS / name)


@pytest.mark.parametrize("name", ENGLISH_PAGES)
def test_the_same_tables(name: str) -> None:
    """The same tables, with the same rows and columns."""
    assert tables(ITALIAN / name) == tables(DOCS / name)
