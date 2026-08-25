from __future__ import annotations

import json

from backend.core.fanxiu.history_museum.packet_capture import tcp_flow


def test_historical_entry_iterator_loads_one_source_lazily(monkeypatch, tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(
        json.dumps(
            {
                "frames": [
                    {
                        "name": "SM_Wallet",
                        "direction": "s2c",
                        "parsed": {"_class": "SM_Wallet", "wallet": []},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            {
                "frames": [
                    {
                        "name": "SM_SyncTime",
                        "direction": "s2c",
                        "parsed": {"_class": "SM_SyncTime"},
                    },
                    {
                        "name": "SM_Login",
                        "direction": "s2c",
                        "parsed": {"_class": "SM_Login", "roleId": "1"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    sources = [
        {"decoded_path": first, "created_at": "2026-07-29 10:00:00", "record_id": "first"},
        {"decoded_path": second, "created_at": "2026-07-29 10:01:00", "record_id": "second"},
    ]
    monkeypatch.setattr(tcp_flow, "_iter_fanxiu_tcp_decoded_sources", lambda _data_dir: sources)
    loaded: list[str] = []
    original_load = tcp_flow._load_json_file

    def tracked_load(path):
        loaded.append(path.name)
        return original_load(path)

    monkeypatch.setattr(tcp_flow, "_load_json_file", tracked_load)

    entries = tcp_flow._iter_fanxiu_tcp_entries(
        str(tmp_path),
        include_display=False,
        newest_first=True,
    )
    assert loaded == []

    first_entry = next(entries)
    assert loaded == ["first.json"]
    assert first_entry["name"] == "SM_Wallet"
    assert "display_text" not in first_entry
    assert [entry["name"] for entry in entries] == ["SM_Login"]
    assert loaded == ["first.json", "second.json"]
