"""Files the documentation site copies from the integration."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_site_icon_is_the_integration_icon() -> None:
    """The site's logo and favicon are the integration's own icon."""
    brand = ROOT / "custom_components" / "inim_smartliving_alarm" / "brand" / "icon.png"
    assert (ROOT / "docs" / "assets" / "icon.png").read_bytes() == brand.read_bytes()
