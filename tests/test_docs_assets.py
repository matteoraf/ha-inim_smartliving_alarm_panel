"""Files the documentation site copies from the integration, its images and lists."""

from itertools import pairwise
from pathlib import Path
import re

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
# The English pages, and the Italian pages with their own images
TREES = [DOCS, DOCS / "it"]
IMAGES = sorted(image for tree in TREES for image in (tree / "images").iterdir())
PAGES = sorted(page for tree in TREES for page in tree.glob("*.md"))
MAX_BYTES = 200_000
MAX_WIDTH = 1600
LIST_ITEM = re.compile(r"^( *)(?:[*+-]|\d+\.) +\S")


def used_images(tree: Path) -> set[str]:
    """The image files the pages of one language show."""
    found = set()
    for page in tree.glob("*.md"):
        for target in re.findall(r"!\[[^\]]*\]\(images/([^)#\s]+)", page.read_text()):
            found.add(target)
    return found


@pytest.mark.parametrize("tree", TREES, ids=lambda path: path.name)
def test_site_icon_is_the_integration_icon(tree: Path) -> None:
    """The site's logo and favicon are the integration's own icon."""
    brand = ROOT / "custom_components" / "inim_smartliving_alarm" / "brand" / "icon.png"
    assert (tree / "assets" / "icon.png").read_bytes() == brand.read_bytes()


@pytest.mark.parametrize("image", IMAGES, ids=lambda path: str(path.relative_to(DOCS)))
def test_image_size_and_format(image: Path) -> None:
    """Small enough to load fast, and no wider than a screenshot needs."""
    assert image.suffix in (".webp", ".png")
    assert image.stat().st_size <= MAX_BYTES
    with Image.open(image) as opened:
        assert opened.width <= MAX_WIDTH


@pytest.mark.parametrize("tree", TREES, ids=lambda path: path.name)
def test_every_image_is_used_and_exists(tree: Path) -> None:
    assert used_images(tree) == {image.name for image in (tree / "images").iterdir()}


@pytest.mark.parametrize("tree", TREES, ids=lambda path: path.name)
def test_light_and_dark_images_come_in_pairs(tree: Path) -> None:
    names = {image.name for image in (tree / "images").iterdir()}
    for name in names:
        if "-light." in name:
            assert name.replace("-light.", "-dark.") in names, name
        if "-dark." in name:
            assert name.replace("-dark.", "-light.") in names, name


@pytest.mark.parametrize("page", PAGES, ids=lambda path: str(path.relative_to(DOCS)))
def test_nested_lists_follow_a_blank_line(page: Path) -> None:
    """A list inside a list item starts after a blank line.

    Without it, the site can show the item as an empty bullet with its
    text after the nested list.
    """
    lines = page.read_text().splitlines()
    for number, (above, line) in enumerate(pairwise(lines), start=2):
        item, parent = LIST_ITEM.match(line), LIST_ITEM.match(above)
        if item and parent:
            assert len(item.group(1)) <= len(parent.group(1)), f"line {number}"
