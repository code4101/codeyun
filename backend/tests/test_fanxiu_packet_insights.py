import json
import subprocess

from backend.core import fanxiu_packet_insights as insights


def _write_self_attr_decoded(decoded_path, *, pcap_name: str = "fanxiu_runtime_snapshot_self_attrs.pcap") -> None:
    decoded_path.write_text(
        json.dumps(
            {
                "record_id": "self-attrs-record",
                "pcap": pcap_name,
                "stream": 0,
                "frames": [
                    {
                        "name": "SM_RoleChangedAttrs",
                        "direction": "s2c",
                        "offset": 128,
                        "pro_id": 30021,
                        "sn": 7,
                        "parsed": {
                            "_class": "SM_RoleChangedAttrs",
                            "attrs": {
                                "finalAttrs": {
                                    "items": [
                                        {"key": 2001, "value": 123456},
                                        {"key": 3001, "value": 222},
                                    ]
                                }
                            },
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_runtime_player_profile_rows_collects_unique_snapshot_records() -> None:
    valid_row = {
        "name": "止清",
        "combat_attributes": [{"key": 2001, "value": 1, "text": "1"}],
        "evidence": {"packet_id": "packet-1", "protocol": "SM_RoleChangedAttrs"},
    }
    duplicate_row = {
        "name": "止清",
        "combat_attributes": [{"key": 2001, "value": 2, "text": "2"}],
        "evidence": {"packet_id": "packet-1", "protocol": "SM_RoleChangedAttrs"},
    }
    record_only_row = {
        "name": "玩家",
        "combat_attributes": [{"key": 2001, "value": 3, "text": "3"}],
        "evidence": {"packet_id": "packet-2", "protocol": "SM_ShowOther"},
    }

    rows = insights._runtime_player_profile_rows(
        {
            "player_profiles": {
                "daily_records": [valid_row, {"name": "missing evidence"}],
                "records": [duplicate_row, record_only_row],
            }
        }
    )

    assert rows == [valid_row, record_only_row]


def test_decode_result_with_worldline_activity_triggers_activity_sync(monkeypatch) -> None:
    calls = []

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {
            "inserted": 1,
            "updated": 0,
            "rank_inserted": 0,
            "rank_updated": 0,
        }

    monkeypatch.setattr(insights, "sync_fanxiu_activity_packets", fake_sync)

    result = insights.sync_fanxiu_packet_runtime_insights_for_decode_result(
        {"frames": [{"name": "SM_WorldLineActivitySync"}]},
        data_dir="data-dir",
        export_root="export-root",
    )

    assert calls == [{"data_dir": "data-dir", "export_root": "export-root", "force": False}]
    assert result == {
        "ok": True,
        "changed": True,
        "activity_packet_sync": {
            "inserted": 1,
            "updated": 0,
            "rank_inserted": 0,
            "rank_updated": 0,
        },
    }


def test_decode_result_business_ingestor_persists_incremental_rows(monkeypatch) -> None:
    business_rows = []
    profile_rows = []

    monkeypatch.setattr(insights, "_load_item_index", lambda **_kwargs: {})
    monkeypatch.setattr(
        insights,
        "_extract_wallet",
        lambda _parsed, _entry, _item_index: {
            "captured_at": "2026-06-10 14:00:00",
            "resources": [{"type": 1, "code": 2, "id": 2, "name": "仙玉", "amount": 3}],
            "evidence": {
                "packet_id": "wallet-record|s2c|10|30001|1",
                "protocol": "SM_Wallet",
                "pcap_name": "fanxiu_runtime_20260610_140000.pcap",
            },
        },
    )
    monkeypatch.setattr(
        insights,
        "_extract_sync_player_profile",
        lambda _parsed, _entry, **_kwargs: {
            "captured_at": "2026-06-10 14:00:01",
            "role_id_text": "24082878061086206",
            "name": "止清ღ羊驼",
            "combat_attributes": [{"key": 2001, "value": 1, "text": "1"}],
            "evidence": {
                "packet_id": "profile-record|s2c|20|30002|2",
                "protocol": "SM_SyncPlayer",
                "record_id": "profile-record",
                "pcap_name": "fanxiu_runtime_20260610_140000.pcap",
            },
        },
    )

    class FakeSession:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("sqlmodel.Session", FakeSession)
    monkeypatch.setattr(
        insights,
        "upsert_fanxiu_packet_business_records",
        lambda _session, rows: business_rows.extend(rows) or {"created": len(rows), "updated": 0, "skipped_invalid": 0, "skipped_duplicate": 0},
    )
    monkeypatch.setattr(
        insights,
        "upsert_fanxiu_player_profile_rows",
        lambda _session, rows: profile_rows.extend(rows) or {"created": len(rows), "skipped_invalid": 0, "skipped_duplicate": 0},
    )

    result = insights.sync_fanxiu_packet_business_for_decode_result(
        {
            "record_id": "decoded-record",
            "pcap_name": "fanxiu_runtime_20260610_140000.pcap",
            "frames": [
                {"name": "SM_Wallet", "parsed": {"_class": "SM_Wallet"}, "direction": "s2c", "offset": 10, "pro_id": 30001, "sn": 1},
                {"name": "SM_SyncPlayer", "parsed": {"_class": "SM_SyncPlayer"}, "direction": "s2c", "offset": 20, "pro_id": 30002, "sn": 2},
            ],
        }
    )

    assert result["ok"] is True
    assert result["changed"] is True
    assert [row["domain"] for row in business_rows] == ["wallet_resource"]
    assert len(profile_rows) == 1
    assert profile_rows[0]["evidence"]["protocol"] == "SM_SyncPlayer"


def test_self_attribute_change_uses_fallback_identity_for_player_profile(tmp_path) -> None:
    decoded_path = tmp_path / "decoded.json"
    _write_self_attr_decoded(decoded_path)

    rows = insights._player_profile_rows_from_decoded_source(
        {
            "decoded_path": decoded_path,
            "record_id": "self-attrs-record",
            "pcap_name": "fanxiu_runtime_snapshot_self_attrs.pcap",
            "created_at": "2026-06-09 23:40:00",
            "stream": 0,
        },
        fallback_self_identity={
            "role_id": "24082878061086206",
            "name": "止清ღ羊驼",
            "level": 201,
            "server": 22077,
        },
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["source_kind"] == "self_attribute_change"
    assert row["role_id_text"] == "24082878061086206"
    assert row["name"] == "止清ღ羊驼"
    assert row["captured_at"] == "2026-06-09 23:40:00"
    assert row["evidence"]["packet_id"] == "self-attrs-record|s2c|128|30021|7"
    assert row["evidence"]["protocol"] == "SM_RoleChangedAttrs"
    assert any(attr["key"] == 2001 and attr["value"] == 123456 for attr in row["combat_attributes"])


def test_self_attribute_change_rejects_unconfirmed_fallback_identity(tmp_path) -> None:
    decoded_path = tmp_path / "decoded.json"
    _write_self_attr_decoded(decoded_path)

    rows = insights._player_profile_rows_from_decoded_source(
        {
            "decoded_path": decoded_path,
            "record_id": "self-attrs-record",
            "pcap_name": "fanxiu_runtime_snapshot_self_attrs.pcap",
            "created_at": "2026-06-09 23:40:00",
            "stream": 0,
        },
        fallback_self_identity={
            "role_id": "24077380502945993",
            "name": "玉清道宗",
            "level": 230,
            "server": 22077,
        },
    )

    assert rows == []


def test_snapshot_self_identity_rejects_non_owner_profile_rows() -> None:
    snapshot = {
        "account": {
            "latest_login": {
                "role_id": "24077380502945993",
                "name": "玉清道宗",
                "captured_at": "2026-06-09 23:49:42",
            }
        },
        "player_profiles": {
            "daily_records": [
                {
                    "role_id_text": "24077380502945993",
                    "name": "玉清道宗",
                    "captured_at": "2026-06-09 23:49:42",
                    "combat_attributes": [{"key": 2001, "value": 1}],
                    "evidence": {"packet_id": "bad", "protocol": "SM_RoleChangedAttrs"},
                }
            ]
        },
    }

    assert insights._latest_self_profile_identity_from_snapshot(snapshot) is None


def test_truncated_show_other_redecode_timeout_falls_back(monkeypatch) -> None:
    def fake_redecode(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["tshark"], timeout=1)

    monkeypatch.setattr(insights, "extract_tcp_stream_payloads_with_tshark", fake_redecode)

    row = insights._extract_show_other_profile(
        {
            "_class": "SM_ShowOther",
            "otherRoleVO": {
                "roleId": 24082878061087586,
                "name": "凌霄༅青风”",
                "server": 22077,
                "level": 230,
                "attrMap": {
                    "_count": 32,
                    "items": [{"key": 7740022, "value": 1}],
                    "_truncated_items": 31,
                },
            },
        },
        {
            "id": "packet-1",
            "name": "SM_ShowOther",
            "decoded_at": "2026-06-09 23:35:08",
            "record_id": "record-1",
            "pcap_name": "fanxiu_runtime_snapshot_timeout.pcap",
            "stored_pcap": "missing.pcap",
        },
    )

    assert row is not None
    assert row["name"] == "凌霄༅青风”"
    assert row["is_truncated"] is True
    assert insights._player_profile_attack_attr(row) is None
