from __future__ import annotations

"""Pure tests for merging partially-loaded activity-rank pages.

These build Runtime-shaped snapshots by hand and never touch a GUI or device.
"""

import pytest

from backend.core.fanxiu.activity.rank_page_merge import (
    RankPageMergeError,
    merge_activity_rank_pages,
)


RANK_ACTIVITY_ID = 43005
CAPTURE = "2026-09-03T21:00:05+08:00"


def _row(rank: int, role_key: str, score: int) -> dict:
    return {
        "rank": rank,
        "score": score,
        "role_key": role_key,
        "name": role_key,
        "server_id": 1,
        "server_name": "s1",
        "club_name": "",
    }


def _page(
    rows: list[dict],
    *,
    total: int,
    declared: int,
    loaded: int,
    pid: int = 111,
    process_start_ticks: int = 222,
    captured_at: str = "2026-09-03T21:00:00+08:00",
    self_rank: int = 1,
    self_role_key: str = "self",
    self_score: int = 10,
) -> dict:
    return {
        "ok": True,
        "complete": True,
        "rank_activity_id": RANK_ACTIVITY_ID,
        "rank_list_size": total,
        "declared_rank_count": declared,
        "loaded_rank_count": loaded,
        "self_ranking": _row(self_rank, self_role_key, self_score),
        "rankings": rows,
        "captured_at": captured_at,
        "evidence": {"pid": pid, "process_start_ticks": process_start_ticks},
    }


def test_merge_sixty_from_fifty_plus_ten() -> None:
    first = _page(
        [_row(rank, f"k{rank}", 1000 - rank) for rank in range(1, 51)],
        total=60,
        declared=50,
        loaded=50,
        captured_at="2026-09-03T21:00:00+08:00",
    )
    second = _page(
        [_row(rank, f"k{rank}", 1000 - rank) for rank in range(51, 61)],
        total=60,
        declared=10,
        loaded=10,
        captured_at="2026-09-03T21:00:02+08:00",
    )

    merged = merge_activity_rank_pages(
        [first, second], rank_activity_id=RANK_ACTIVITY_ID, captured_at=CAPTURE
    )

    assert merged["rank_list_size"] == 60
    assert merged["declared_rank_count"] == 60
    assert merged["loaded_rank_count"] == 60
    assert [row["rank"] for row in merged["rankings"]] == list(range(1, 61))
    assert merged["captured_at"] == CAPTURE
    assert len(merged["evidence"]["pages"]) == 2
    assert merged["evidence"]["captured_at"] == CAPTURE
    assert merged["evidence"]["process_start_ticks"] == 222


def test_merge_missing_page_is_explicit_failure() -> None:
    only_first = _page(
        [_row(rank, f"k{rank}", 1000 - rank) for rank in range(1, 51)],
        total=60,
        declared=50,
        loaded=50,
    )

    with pytest.raises(RankPageMergeError, match="不完整"):
        merge_activity_rank_pages(
            [only_first], rank_activity_id=RANK_ACTIVITY_ID, captured_at=CAPTURE
        )


def test_merge_same_rank_different_person_fails() -> None:
    first = _page([_row(1, "alice", 10)], total=1, declared=1, loaded=1)
    second = _page([_row(1, "bob", 10)], total=1, declared=1, loaded=1)

    with pytest.raises(RankPageMergeError, match="冲突"):
        merge_activity_rank_pages(
            [first, second], rank_activity_id=RANK_ACTIVITY_ID, captured_at=CAPTURE
        )


def test_merge_same_rank_value_change_fails() -> None:
    first = _page([_row(1, "alice", 10)], total=1, declared=1, loaded=1)
    second = _page([_row(1, "alice", 99)], total=1, declared=1, loaded=1)

    with pytest.raises(RankPageMergeError, match="数值"):
        merge_activity_rank_pages(
            [first, second], rank_activity_id=RANK_ACTIVITY_ID, captured_at=CAPTURE
        )


def test_merge_across_process_change_fails() -> None:
    first = _page(
        [_row(rank, f"k{rank}", 1000 - rank) for rank in range(1, 51)],
        total=60,
        declared=50,
        loaded=50,
        pid=111,
        process_start_ticks=222,
    )
    second = _page(
        [_row(rank, f"k{rank}", 1000 - rank) for rank in range(51, 61)],
        total=60,
        declared=10,
        loaded=10,
        pid=999,
        process_start_ticks=888,
    )

    with pytest.raises(RankPageMergeError, match="进程"):
        merge_activity_rank_pages(
            [first, second], rank_activity_id=RANK_ACTIVITY_ID, captured_at=CAPTURE
        )


def test_merge_total_change_fails() -> None:
    first = _page(
        [_row(rank, f"k{rank}", 1000 - rank) for rank in range(1, 51)],
        total=60,
        declared=50,
        loaded=50,
    )
    second = _page(
        [_row(rank, f"k{rank}", 1000 - rank) for rank in range(51, 60)],
        total=59,
        declared=9,
        loaded=9,
    )

    with pytest.raises(RankPageMergeError, match="总人数"):
        merge_activity_rank_pages(
            [first, second], rank_activity_id=RANK_ACTIVITY_ID, captured_at=CAPTURE
        )


def test_merge_duplicate_role_key_fails() -> None:
    page = _page(
        [_row(1, "same", 10), _row(2, "same", 20)],
        total=2,
        declared=2,
        loaded=2,
    )

    with pytest.raises(RankPageMergeError, match="role_key"):
        merge_activity_rank_pages(
            [page], rank_activity_id=RANK_ACTIVITY_ID, captured_at=CAPTURE
        )


def test_merge_requires_capture_time() -> None:
    page = _page([_row(1, "alice", 10)], total=1, declared=1, loaded=1)

    with pytest.raises(RankPageMergeError, match="采集时间"):
        merge_activity_rank_pages(
            [page], rank_activity_id=RANK_ACTIVITY_ID, captured_at=""
        )


def test_merge_allows_unranked_self_with_negative_rank() -> None:
    first = _page(
        [_row(rank, f"k{rank}", 1000 - rank) for rank in range(1, 51)],
        total=60,
        declared=50,
        loaded=50,
        self_rank=-1,
        self_score=0,
    )
    second = _page(
        [_row(rank, f"k{rank}", 1000 - rank) for rank in range(51, 61)],
        total=60,
        declared=10,
        loaded=10,
        self_rank=-1,
        self_score=0,
    )

    merged = merge_activity_rank_pages(
        [first, second], rank_activity_id=RANK_ACTIVITY_ID, captured_at=CAPTURE
    )

    assert merged["self_ranking"]["rank"] == -1
    assert merged["self_ranking"]["score"] == 0
    assert merged["loaded_rank_count"] == 60


def test_merge_rejects_non_positive_board_rank() -> None:
    page = _page([_row(0, "alice", 10)], total=1, declared=1, loaded=1)

    with pytest.raises(RankPageMergeError, match="名次无效"):
        merge_activity_rank_pages(
            [page], rank_activity_id=RANK_ACTIVITY_ID, captured_at=CAPTURE
        )


def test_merge_requires_self_identity_consistency_across_pages() -> None:
    first = _page(
        [_row(1, "k1", 9)], total=2, declared=1, loaded=1,
        self_rank=-1, self_score=0,
    )
    second = _page(
        [_row(2, "k2", 8)], total=2, declared=1, loaded=1,
        self_rank=7, self_score=100,
    )

    with pytest.raises(RankPageMergeError, match="自身排名"):
        merge_activity_rank_pages(
            [first, second], rank_activity_id=RANK_ACTIVITY_ID, captured_at=CAPTURE
        )
