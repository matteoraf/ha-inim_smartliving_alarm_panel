"""Import the integration's modules without Home Assistant.

The integration's package runs Home Assistant code when it is imported, but
the modules the viewer uses (the panel protocol, the report format and its
encryption) do not need it. The package is registered by its folder only,
so its own start-up code is never run, and the modules are imported from
there as usual.
"""

from importlib import import_module
from pathlib import Path
import sys
from types import ModuleType

PACKAGE = "custom_components.inim_smartliving_alarm"
_DEFAULT = (
    Path(__file__).resolve().parents[2] / "custom_components" / "inim_smartliving_alarm"
)


def integration_folder(path: str | Path | None = None) -> Path:
    """The integration's folder: the one given, else the repository's."""
    folder = Path(path) if path else _DEFAULT
    if not (folder / "manifest.json").is_file():
        raise FileNotFoundError(f"no integration found in {folder}")
    return folder.resolve()


def register(path: str | Path | None = None) -> None:
    """Make the integration's modules importable without running its start-up."""
    folder = integration_folder(path)
    if PACKAGE in sys.modules:
        return
    parent = ModuleType("custom_components")
    parent.__path__ = [str(folder.parent)]
    package = ModuleType(PACKAGE)
    package.__path__ = [str(folder)]
    sys.modules.setdefault("custom_components", parent)
    sys.modules[PACKAGE] = package


def module(name: str) -> ModuleType:
    """One of the integration's modules, e.g. "protocol.masking"."""
    register()
    return import_module(f"{PACKAGE}.{name}")
