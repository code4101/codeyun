import json
from pathlib import Path

from backend.core.fanxiu_tcp_flow import list_fanxiu_tcp_business_entries, resolve_fanxiu_tcp_store_root


def _write_decoded_record(store_root: Path) -> None:
    record_dir = store_root / "sample_stream1"
    record_dir.mkdir(parents=True)
    decoded_path = record_dir / "decoded.json"
    decoded_path.write_text(
        json.dumps(
            {
                "frames": [
                    {
                        "direction": "s2c",
                        "name": "SM_RewardResult",
                        "pro_id": 20021,
                        "sn": 1,
                        "offset": 0,
                        "parsed": {
                            "_class": "SM_RewardResult",
                            "rewards": [{"type": 1, "id": 1, "count": 2}],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (record_dir / "meta.json").write_text(
        json.dumps(
            {
                "record_id": "sample_stream1",
                "pcap_name": "sample.pcap",
                "stored_pcap": str(record_dir / "sample.pcap"),
                "decoded_path": str(decoded_path),
                "stream": 1,
                "capture_sha256": "abc",
                "created_at": "2026-05-25 17:10:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_fanxiu_tcp_business_entries_accept_store_root_data_dir(tmp_path):
    store_root = tmp_path / "fanxiu" / "tcp-flow"
    _write_decoded_record(store_root)

    assert resolve_fanxiu_tcp_store_root(store_root) == store_root

    result = list_fanxiu_tcp_business_entries(data_dir=store_root, page=1, page_size=10)

    assert result["total"] == 1
    assert result["items"][0]["name"] == "SM_RewardResult"
    assert result["category_summary"][0]["category"] == "奖励/消耗/道具"
