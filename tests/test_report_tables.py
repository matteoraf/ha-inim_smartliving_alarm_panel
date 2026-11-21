"""The system report's tables (report_blocks.py, report_layout.py).

Each table was reviewed and approved block by block; its fingerprint is
recorded here, so a changed table fails until it is approved again.
"""

import hashlib

import pytest

from custom_components.inim_smartliving_alarm.protocol.masking import Privacy
from custom_components.inim_smartliving_alarm.protocol.model import (
    SMARTLIVING_505_FW6,
    SMARTLIVING_505_FW51,
    SMARTLIVING_515_FW6,
    SMARTLIVING_515_FW51,
    SMARTLIVING_1050_FW6,
    SMARTLIVING_1050_FW51,
    SMARTLIVING_10100_FW6,
    SMARTLIVING_10100_FW51,
    PanelModel,
    parse_model_string,
)
from custom_components.inim_smartliving_alarm.protocol.report_blocks import TABLES
from custom_components.inim_smartliving_alarm.protocol.report_layout import (
    ReportTable,
    Space,
    table_for,
)

# SHA-256 of each table's canonical text, as approved
APPROVED_TABLES = {
    ("505", "6"): "c5ce431b909a1fa11d38b410787ebc735156a663c3624c9c811e5afd247838b2",
    ("505", "5.1"): "349614a7f06ea61c0afd7982f4c606e8b24bb78535b122f34f10cc1f4039691b",
    ("505", "5.0"): "eed7f259c27f9cbca6aa1ea2d3f98d630085f999644deffb15e4435fe1ce9468",
    ("515", "6"): "18675847803e3e442205af5474e36a763eb05510a80ac3bf99993ad682085441",
    ("515", "5.1"): "86270853028fde39cb558aa4f0dbacd4b165f17040692f45c4a7483a3769b804",
    ("515", "5.0"): "3e5270eb90e8134db4e147102e1657746b4a16f528b688678ad697c5bc14195a",
    ("1050", "6"): "6d9ffbfbdb9e213566d156f95b5a66d2032167d16baf904ed2707f146a0bc8f5",
    ("1050", "5.1"): "8b371ea5321ede9e6ca8e600d8260aab5aae288d48833fc810078ac1359c321c",
    ("1050", "5.0"): "5f0f327dac687bbce238da7d5155117902e92f876f63f570ce69ed923d393888",
    ("10100", "6"): "684644cd27ae1850c7f7b5fb594a8abe8c54c4e2af6b8495d76b331846bd1869",
    (
        "10100",
        "5.1",
    ): "c1930f8da3c23e3406a2c57bc0f8063a78ad76745f2d2ee08f77b4527725bbb5",
    (
        "10100",
        "5.0",
    ): "9b710e38637a62c3d82d58779f6071b913a0387079bdbba43ef8730588440e15",
}

PROFILES: dict[tuple[str, str], PanelModel] = {
    ("505", "6"): SMARTLIVING_505_FW6,
    ("515", "6"): SMARTLIVING_515_FW6,
    ("1050", "6"): SMARTLIVING_1050_FW6,
    ("10100", "6"): SMARTLIVING_10100_FW6,
    ("505", "5.1"): SMARTLIVING_505_FW51,
    ("515", "5.1"): SMARTLIVING_515_FW51,
    ("1050", "5.1"): SMARTLIVING_1050_FW51,
    ("10100", "5.1"): SMARTLIVING_10100_FW51,
}
EEPROM_END = 0x20000


def test_every_table_is_approved() -> None:
    """No table is added, removed or changed without a new approval."""
    assert set(TABLES) == set(APPROVED_TABLES)
    for key, table in TABLES.items():
        text = table.canonical_text().encode()
        assert hashlib.sha256(text).hexdigest() == APPROVED_TABLES[key], key


@pytest.mark.parametrize("key", list(TABLES))
def test_blocks_do_not_overlap(key: tuple[str, str]) -> None:
    """EEPROM blocks are apart and inside the EEPROM; names are unique."""
    table = TABLES[key]
    names = [b.name for b in table.blocks]
    assert len(names) == len(set(names))
    spans = sorted(
        (b.address, b.address + b.length)
        for b in table.blocks
        if b.space is Space.EEPROM
    )
    assert spans
    for (_, end), (start, _) in zip(spans, spans[1:], strict=False):
        assert end <= start
    assert spans[-1][1] <= EEPROM_END
    if table.label_area is not None:
        area_start, area_size = table.label_area
        for start, end in spans:
            assert end <= area_start or area_start + area_size <= start


@pytest.mark.parametrize("key", list(PROFILES))
def test_tables_agree_with_the_profiles(key: tuple[str, str]) -> None:
    """Core blocks sit where the integration's own profile says."""
    table, model = TABLES[key], PROFILES[key]

    def at(name: str) -> tuple[int, int]:
        block = table.block(name)
        assert block is not None, name
        return block.address, block.length

    assert at("pin_table")[0] == model.pin_table
    assert at("pin_table")[1] >= model.pin_table_length
    assert at("code_records")[0] == model.code_records
    assert at("code_records")[1] >= model.codes * model.code_record_length
    assert at("zone_records") == (model.zone_records, model.zones * 9)
    assert at("terminal_records") == (model.terminal_records, model.terminals * 12)
    assert at("physical_map")[0] == model.physical_map
    assert at("scenario_records")[0] == model.scenario_records
    assert at("area_records") == (model.area_records, model.areas * 7)
    assert at("fixed_output_records") == (model.fixed_output_records, 6)
    assert at("event_log") == (model.event_log, model.event_ring_size * 9)
    assert table.event_ring_size == model.event_ring_size
    assert table.label_bank == model.label_bank


@pytest.mark.parametrize("key", list(TABLES))
def test_secret_blocks(key: tuple[str, str]) -> None:
    """The PIN table is secret everywhere; on full tables the other secrets too."""
    table = TABLES[key]
    assert table.block("pin_table").privacy is Privacy.SECRET
    if table.family != "5.0":
        for name in ("phone_numbers", "key_serials", "code_buffer", "keypad_queue"):
            assert table.block(name).privacy is Privacy.SECRET, name
    for block in table.blocks:
        if block.space is Space.LABELS:
            assert block.privacy is Privacy.PERSONAL


@pytest.mark.parametrize(
    ("model_string", "expected"),
    [
        (b"6.07 01050  ", ("1050", "6")),
        (b"6.10 01050  ", ("1050", "6")),
        (b"6.07 10100  ", ("10100", "6")),
        (b"5.12 01050  ", ("1050", "5.1")),
        (b"5.12 00505  ", ("505", "5.1")),
        (b"5.07 01050  ", ("1050", "5.0")),
        (b"4.02 01050  ", None),
        (b"6.07 02050  ", None),
    ],
)
def test_table_for_a_panel(model_string: bytes, expected) -> None:
    """The panel's own model and family, else basic mode."""
    firmware = parse_model_string(model_string)
    table: ReportTable = table_for(firmware, SMARTLIVING_1050_FW6)
    if expected is None:
        assert table.family == "basic"
        assert table.label_area is None
        assert all(b.space is Space.RAM for b in table.blocks)
    else:
        assert (table.model, table.family) == expected


def test_only_the_1050_fw6_layout_is_verified() -> None:
    """The others are marked as not checked on hardware."""
    verified = [key for key, table in TABLES.items() if table.layout_verified]
    assert verified == [("1050", "6")]


@pytest.mark.parametrize("model", ["505", "515", "1050", "10100"])
def test_cloud_reads(model: str) -> None:
    """Firmware 6 reads the 1-byte cloud flag and the last cloud result only.

    The bytes next to the flag are never read, by any block.
    """
    table = TABLES[(model, "6")]
    flag = table.block("cloud_enabled")
    assert flag is not None
    assert (flag.space, flag.length, flag.privacy) == (Space.EEPROM, 1, Privacy.PUBLIC)
    before = range(flag.address - 8, flag.address)
    for block in table.blocks:
        if block.space is Space.EEPROM and block is not flag:
            span = range(block.address, block.address + block.length)
            assert flag.address not in span, block.name
            assert not set(before) & set(span), block.name
    result = table.block("cloud_last_result")
    assert (result.space, result.length, result.privacy) == (
        Space.RAM,
        4,
        Privacy.PUBLIC,
    )
    for family in ("5.1", "5.0"):
        assert TABLES[(model, family)].block("cloud_enabled") is None
