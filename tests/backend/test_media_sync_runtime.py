from __future__ import annotations

import pytest

from backend.plugins.modules.media_sync import runtime


@pytest.mark.parametrize(
    ("platform", "before_pending", "after_home_pending", "expected_remaining"),
    [
        ("pixiv", 264, 314, 150),
        ("pinterest", 2684, 2854, 30),
    ],
)
def test_collect_ids_adds_target_to_existing_pending_pool(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    before_pending: int,
    after_home_pending: int,
    expected_remaining: int,
) -> None:
    final_pending = before_pending + 200
    pending_counts = iter([before_pending, after_home_pending, final_pending])
    related_calls: list[dict[str, object]] = []

    monkeypatch.setattr(runtime, "count_pending_source_candidates", lambda **_kwargs: next(pending_counts))
    monkeypatch.setattr(runtime, f"run_{platform}_home_sync", lambda **_kwargs: {"ok": True})

    def fake_related_sync(**kwargs):
        related_calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(runtime, f"run_{platform}_related_sync", fake_related_sync)

    manager = runtime.SyncJobManager()
    result = getattr(manager, f"_run_{platform}_collect_ids")(
        {
            "user_id": 2,
            "root_dir": r"D:\home\chenkunze\data\m2510mn",
            "platform_download_target_count": 200,
            f"{platform}_related_seed_limit": 12,
        }
    )

    assert result["before_pending_count"] == before_pending
    assert result["target_new_count"] == 200
    assert result["target_count"] == final_pending
    assert result["new_pending_count"] == 200
    assert related_calls
    if platform == "pixiv":
        assert related_calls[0]["download_limit"] == expected_remaining
    else:
        seed_limit = int(related_calls[0]["seed_limit"])
        per_seed_limit = int(related_calls[0]["download_limit"])
        assert per_seed_limit * seed_limit >= expected_remaining
