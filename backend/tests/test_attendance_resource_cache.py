from __future__ import annotations

import base64

from backend.core.attendance.resource_cache import (
    EMPTY_TTL_SECONDS,
    SUCCESS_TTL_SECONDS,
    lookup_resource_cache,
    store_resource_cache,
)


def test_successful_resource_snapshot_is_shared_for_three_hours_and_then_removed(tmp_path):
    content = "用户ID,播放进度\nu_1,100\n".encode("utf-8-sig")
    stored = store_resource_cache(
        resource_type="video",
        shop_id=1,
        resource_url="https://xiaoe.test/detail?b=2&a=1",
        content_base64=base64.b64encode(content).decode("ascii"),
        suffix=".csv",
        root=tmp_path,
        now=1000,
    )

    hit = lookup_resource_cache(
        resource_type="video",
        shop_id=1,
        resource_url="https://xiaoe.test/detail?a=1&b=2",
        root=tmp_path,
        now=1000 + SUCCESS_TTL_SECONDS - 1,
    )

    assert hit["hit"] is True
    assert hit["empty"] is False
    assert hit["captured_at"] == 1000
    assert base64.b64decode(hit["content_base64"]) == content
    assert stored["expires_at"] == 1000 + SUCCESS_TTL_SECONDS

    expired = lookup_resource_cache(
        resource_type="video",
        shop_id=1,
        resource_url="https://xiaoe.test/detail?a=1&b=2",
        root=tmp_path,
        now=1000 + SUCCESS_TTL_SECONDS + 1,
    )

    assert expired["hit"] is False
    assert list(tmp_path.iterdir()) == []


def test_empty_snapshot_uses_short_ttl_and_has_no_payload_file(tmp_path):
    stored = store_resource_cache(
        resource_type="clockin",
        shop_id=2,
        resource_url="https://xiaoe.test/clockin?id=1",
        options={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        empty=True,
        root=tmp_path,
        now=2000,
    )

    hit = lookup_resource_cache(
        resource_type="clockin",
        shop_id=2,
        resource_url="https://xiaoe.test/clockin?id=1",
        options={"end_date": "2026-08-31", "start_date": "2026-08-01"},
        root=tmp_path,
        now=2000 + EMPTY_TTL_SECONDS - 1,
    )

    assert hit["hit"] is True
    assert hit["empty"] is True
    assert "content_base64" not in hit
    assert stored["payload_name"] == ""


def test_cache_identity_separates_shop_and_clockin_export_range(tmp_path):
    payload = base64.b64encode(b"same url, different scope").decode("ascii")
    store_resource_cache(
        resource_type="clockin",
        shop_id=1,
        resource_url="https://xiaoe.test/clockin?id=1",
        options={"start_date": "2026-08-01"},
        content_base64=payload,
        suffix=".csv",
        root=tmp_path,
        now=3000,
    )

    assert lookup_resource_cache(
        resource_type="clockin",
        shop_id=2,
        resource_url="https://xiaoe.test/clockin?id=1",
        options={"start_date": "2026-08-01"},
        root=tmp_path,
        now=3001,
    )["hit"] is False
    assert lookup_resource_cache(
        resource_type="clockin",
        shop_id=1,
        resource_url="https://xiaoe.test/clockin?id=1",
        options={"start_date": "2026-08-02"},
        root=tmp_path,
        now=3001,
    )["hit"] is False
