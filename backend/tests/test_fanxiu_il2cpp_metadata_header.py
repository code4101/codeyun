import struct

from backend.core.fanxiu.catalog.il2cpp_metadata import (
    IL2CPP_METADATA_HEADER_TABLES,
    IL2CPP_METADATA_HEADER_TABLES_V242,
    IL2CPP_METADATA_MAGIC,
    _read_header,
)


def _header(pair_count: int) -> bytes:
    header_size = 8 + pair_count * 8
    pairs = b"".join(struct.pack("<II", header_size + index * 4, 4) for index in range(pair_count))
    return struct.pack("<II", IL2CPP_METADATA_MAGIC, 24) + pairs + b"\x00" * (pair_count * 4)


def test_v242_header_without_rgctx_keeps_later_tables_aligned():
    header = _read_header(_header(32))

    assert header["layout"] == "v24.2+"
    assert "rgctx_entries" not in header["tables"]
    assert header["tables"]["type_definitions"]["index"] == 19
    assert header["tables"]["images"]["index"] == 20
    assert header["tables"]["metadata_usage_pairs"]["index"] == 23
    assert header["tables"]["exported_type_definitions"]["index"] == 31
    assert not header["warnings"]


def test_v240_header_with_rgctx_keeps_33_tables():
    header = _read_header(_header(33))

    assert header["layout"] == "v24.0-24.1"
    assert header["tables"]["rgctx_entries"]["index"] == 20
    assert header["tables"]["images"]["index"] == 21
    assert header["tables"]["exported_type_definitions"]["index"] == 32
    assert len(IL2CPP_METADATA_HEADER_TABLES) == len(IL2CPP_METADATA_HEADER_TABLES_V242) + 1


def test_unknown_table_count_is_reported_not_named():
    header = _read_header(_header(28))

    assert header["layout"] == "unknown"
    assert "unknown_20" in header["tables"]
    assert header["warnings"]
