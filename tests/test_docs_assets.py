"""Files the documentation site copies from the integration, its images and lists."""

from itertools import pairwise
from pathlib import Path
import re

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
IMAGES = sorted((DOCS / "images").iterdir())
MAX_BYTES = 200_000
MAX_WIDTH = 1600
LIST_ITEM = re.compile(r"^( *)(?:[*+-]|\d+\.) +\S")


def used_images() -> set[str]:
    """The image files the pages show."""
    found = set()
    for page in DOCS.glob("*.md"):
        for target in re.findall(r"!\[[^\]]*\]\(images/([^)#\s]+)", page.read_text()):
            found.add(target)
    return found


def test_site_icon_is_the_integration_icon() -> None:
    """The site's logo and favicon are the integration's own icon."""
    brand = ROOT / "custom_components" / "inim_smartliving_alarm" / "brand" / "icon.png"
    assert (ROOT / "docs" / "assets" / "icon.png").read_bytes() == brand.read_bytes()


@pytest.mark.parametrize("image", IMAGES, ids=lambda path: path.name)
def test_image_size_and_format(image: Path) -> None:
    """Small enough to load fast, and no wider than a screenshot needs."""
    assert image.suffix in (".webp", ".png")
    assert image.stat().st_size <= MAX_BYTES
    with Image.open(image) as opened:
        assert opened.width <= MAX_WIDTH


def test_every_image_is_used_and_exists() -> None:
    assert used_images() == {image.name for image in IMAGES}


def test_light_and_dark_images_come_in_pairs() -> None:
    names = {image.name for image in IMAGES}
    for name in names:
        if "-light." in name:
            assert name.replace("-light.", "-dark.") in names, name
        if "-dark." in name:
            assert name.replace("-dark.", "-light.") in names, name


@pytest.mark.parametrize("page", sorted(DOCS.glob("*.md")), ids=lambda path: path.name)
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
