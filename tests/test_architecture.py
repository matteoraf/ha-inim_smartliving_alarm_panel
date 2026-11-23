"""The integration's layers, read from the source: who may import what.

Dependencies point down only: protocol, then transport, then the API, then
the Home Assistant integration. Some integration modules are kept free of
Home Assistant, so that the report viewer and the tests can load them
alone. Only stored_config knows the shape of the stored panel
configuration. Only the protocol, the transport, the API and the system
report import the register addresses (directly or through a name another
module imported them as), and the entity modules import no transport.
"""

import ast
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).parent.parent
COMPONENT = ROOT / "custom_components" / "inim_smartliving_alarm"
PACKAGE = "custom_components.inim_smartliving_alarm"
VIEWER = ROOT / "scripts" / "report_viewer"


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(COMPONENT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or "__init__"


MODULES = {_module_name(path): path for path in sorted(COMPONENT.rglob("*.py"))}


def _resolve(name: str) -> str | None:
    """The package module an import name points to (a module or its parent)."""
    while name:
        if name in MODULES:
            return name
        name = name.rpartition(".")[0]
    return None


def _imports(module: str) -> tuple[set[str], set[str]]:
    """(package modules, other top-level packages) a module imports.

    Every import counts, including those inside functions and under
    TYPE_CHECKING.
    """
    path = MODULES[module]
    package = (
        module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    )
    if module == "__init__":
        package = []
    inside, outside = set(), set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"{PACKAGE}."):
                    if found := _resolve(alias.name.removeprefix(f"{PACKAGE}.")):
                        inside.add(found)
                else:
                    outside.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                name = node.module or ""
                if name.startswith(f"{PACKAGE}."):
                    if found := _resolve(name.removeprefix(f"{PACKAGE}.")):
                        inside.add(found)
                else:
                    outside.add(name.split(".")[0])
                continue
            base = package[: len(package) - (node.level - 1)]
            prefix = ".".join(base + (node.module.split(".") if node.module else []))
            for alias in node.names:
                target = _resolve(f"{prefix}.{alias.name}" if prefix else alias.name)
                if target is None and prefix:
                    target = _resolve(prefix)
                if target is not None and target != module:
                    inside.add(target)
    return inside, outside


def _third_party(names: set[str]) -> set[str]:
    return {name for name in names if name not in sys.stdlib_module_names}


def _reach(module: str) -> set[str]:
    """Every package module a module loads, itself included."""
    seen, stack = {module}, [module]
    while stack:
        for target in _imports(stack.pop())[0]:
            if target not in seen:
                seen.add(target)
                stack.append(target)
    return seen


def _layer(module: str) -> str:
    return module.split(".")[0]


# --------------------------------------------------------------- the layers


@pytest.mark.parametrize(
    "module", sorted(m for m in MODULES if _layer(m) == "protocol")
)
def test_protocol_imports_only_the_standard_library(module: str) -> None:
    """Panel knowledge has no I/O, Home Assistant or other dependency."""
    inside, outside = _imports(module)
    assert {_layer(m) for m in inside} <= {"protocol"}
    assert _third_party(outside) == set()


@pytest.mark.parametrize(
    "module", sorted(m for m in MODULES if _layer(m) == "transport")
)
def test_transport_imports_only_protocol(module: str) -> None:
    """Framing and connections: the protocol below, no Home Assistant."""
    inside, outside = _imports(module)
    assert {_layer(m) for m in inside} <= {"protocol", "transport"}
    assert _third_party(outside) <= {"cryptography"}


@pytest.mark.parametrize("module", ["api", "discovery"])
def test_api_imports_only_protocol_and_transport(module: str) -> None:
    """The panel API returns typed objects and knows nothing of Home Assistant."""
    inside, outside = _imports(module)
    assert {_layer(m) for m in inside} <= {"protocol", "transport"}
    assert "homeassistant" not in outside


HA_FREE = [
    "attribute_values",
    "event_log",
    "panel_view",
    "peripherals",
    "polling",
    "report_crypto",
    "selection",
    "stored_config",
    "system_report",
    "user_code",
]


@pytest.mark.parametrize("module", HA_FREE)
def test_modules_free_of_home_assistant(module: str) -> None:
    """These load without Home Assistant, through everything they import."""
    importing_ha = sorted(
        target for target in _reach(module) if "homeassistant" in _imports(target)[1]
    )
    assert importing_ha == []


def test_lower_layers_never_import_the_integration() -> None:
    """protocol, transport and the API never reach an integration module."""
    lower = {"protocol", "transport", "api"}
    for module in MODULES:
        if _layer(module) in lower:
            assert {_layer(m) for m in _reach(module)} <= lower, module


@pytest.mark.parametrize("path", sorted(VIEWER.glob("*.py")), ids=lambda p: p.name)
def test_viewer_loads_the_integration_only_through_its_loader(path: Path) -> None:
    """The report viewer imports no integration or Home Assistant module."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module or ""]
        for name in names:
            assert not name.startswith(("custom_components", "homeassistant")), name


# ------------------------------------------------------------ stored shape


def _block_constants() -> dict[str, str]:
    """stored_config's constants naming a stored block: name -> block."""
    tree = ast.parse(MODULES["stored_config"].read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            found[node.targets[0].id] = node.value.value
    blocks = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "BLOCKS"
    )
    names = {element.id for element in blocks.elts if isinstance(element, ast.Name)}
    return {name: value for name, value in found.items() if name in names}


BLOCK_CONSTANTS = _block_constants()
BLOCK_NAMES = set(BLOCK_CONSTANTS.values())
# Keys entry version 1 stored the configuration under (generic words such as
# "areas" or "zones" left out: other dictionaries use them too)
VERSION_1_KEYS = {
    "system_info",
    "zones_config",
    "zones_config_detailed",
    "scenario_activations",
    "keyboard_names",
    "code_names",
    "key_names",
    "reader_names",
    "expander_names",
    "timer_names",
    "siren_names",
}


def _stored_lookups(tree: ast.AST) -> set[str]:
    """Stored keys a module looks up, as x[key] or x.get(key, ...).

    The key is a string naming a stored block or a version 1 key, or a
    constant of stored_config naming a block, used bare (ZONE_RECORDS) or
    through the module (stored_config.ZONE_RECORDS).
    """
    found = set()
    for node in ast.walk(tree):
        key = None
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            key = node.slice
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
        ):
            key = node.args[0]
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            if key.value in BLOCK_NAMES | VERSION_1_KEYS:
                found.add(key.value)
        elif isinstance(key, ast.Name) and key.id in BLOCK_CONSTANTS:
            found.add(BLOCK_CONSTANTS[key.id])
        elif isinstance(key, ast.Attribute) and key.attr in BLOCK_CONSTANTS:
            found.add(BLOCK_CONSTANTS[key.attr])
    return found


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('config["zone_records"]', {"zone_records"}),
        ('config.get("physical_map")', {"physical_map"}),
        ('config["system_info"]["raw_hex"]', {"system_info"}),
        ("config[ZONE_RECORDS]", {"zone_records"}),
        ("config.get(PHYSICAL_MAP, '')", {"physical_map"}),
        ("config[stored_config.ZONE_RECORDS]", {"zone_records"}),
        ("config.get(sc.MODEL_STRING)", {"model_string"}),
        ('placeholders["zones"]', set()),  # a generic word
        ('status.get("areas")', set()),
        ("config[OTHER_NAME]", set()),
        ('config["zone_records"] = value', set()),  # writing, not reading
    ],
)
def test_stored_lookups_are_found(source: str, found: set[str]) -> None:
    """Each way of looking up a stored key is caught; other keys are not."""
    assert _stored_lookups(ast.parse(source)) == found


def test_every_block_has_a_constant() -> None:
    """The guard knows every stored block by its constant."""
    assert len(BLOCK_CONSTANTS) == 22


@pytest.mark.parametrize("module", sorted(m for m in MODULES if m != "stored_config"))
def test_only_stored_config_reads_the_stored_shape(module: str) -> None:
    """Other modules read the stored configuration through stored_config."""
    tree = ast.parse(MODULES[module].read_text(encoding="utf-8"))
    assert _stored_lookups(tree) == set()


# ----------------------------------------------------------- registers


# Modules that may use the panel's register addresses: the protocol and the
# transport below the API, the API that reads them, and the system report
# that lists them
REGISTER_USERS = {"api", "system_report"}
# The entities and what only they use: no transport, no register address
ENTITY_MODULES = [
    "alarm_control_panel",
    "binary_sensor",
    "button",
    "entity",
    "light",
    "outputs",
    "select",
    "sensor",
    "switch",
]


def _register_names(module: str) -> set[str]:
    """The names a module binds protocol.registers to (import ... as X)."""
    names = set()
    for node in ast.walk(ast.parse(MODULES[module].read_text(encoding="utf-8"))):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "registers" or alias.name.endswith(".registers"):
                    names.add(alias.asname or alias.name.rpartition(".")[2])
    return names


def _registers_reached(module: str) -> bool:
    """Whether a module imports the registers, directly or as a name another
    module of the package bound them to (from .api import reg)."""
    if "protocol.registers" in _imports(module)[0]:
        return True
    path = MODULES[module]
    package = module.split(".")[:-1] if path.name != "__init__.py" else []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if node.level:
            base = package[: len(package) - (node.level - 1)]
            source = _resolve(".".join([*base, *node.module.split(".")]))
        else:
            source = _resolve(node.module.removeprefix(f"{PACKAGE}."))
        if source is None or source == module:
            continue
        bound = _register_names(source)
        if any(alias.name in bound for alias in node.names):
            return True
    return False


@pytest.mark.parametrize("module", sorted(MODULES))
def test_only_lower_layers_and_the_report_use_registers(module: str) -> None:
    """No module but the protocol, the transport, the API and the system
    report imports the register addresses, directly or re-exported."""
    if _layer(module) in ("protocol", "transport") or module in REGISTER_USERS:
        return
    assert not _registers_reached(module)


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("from .api import reg\n", True),  # the name api imported them as
        ("from .protocol import registers\n", True),
        ("from .api import SmartLivingPanel\n", False),
    ],
)
def test_register_imports_are_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, found: bool
) -> None:
    """A direct import and a re-export both count; other names do not."""
    probe = tmp_path / "probe.py"
    probe.write_text(source, encoding="utf-8")
    monkeypatch.setitem(MODULES, "probe", probe)
    assert _registers_reached("probe") is found


@pytest.mark.parametrize("module", ENTITY_MODULES)
def test_entity_modules_import_no_transport(module: str) -> None:
    """The entities, their base and the outputs use the panel API: they import
    no transport module and no register address."""
    assert {_layer(m) for m in _imports(module)[0]} & {"transport"} == set()
    assert not _registers_reached(module)


def test_polling_schedule_reads_nothing() -> None:
    """The polling schedule is plain logic: it reaches no transport, no API
    and no register address."""
    assert {_layer(m) for m in _reach("polling")} & {"transport", "api"} == set()
    assert not _registers_reached("polling")
