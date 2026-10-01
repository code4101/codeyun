from __future__ import annotations

"""Pure merge of several already-read activity-rank pages into one fact.

The Runtime exposes only the client's currently loaded ``rankVOS`` window: the
first page may carry ranks ``1..50`` while ``rankListSize`` is ``60``, and one
scroll later the same dictionary holds ``51..60``.  Persisting either page alone
would silently truncate the board, and re-declaring ``rankListSize`` as ``50``
would only hide the loss.  This module merges the observed pages into the single
complete fact and rejects every ambiguous overlap instead of guessing.

It never reads Runtime, never persists and never touches a device; callers pass
already-captured snapshots.
"""

from collections.abc import Mapping, Sequence
from typing import Any


class RankPageMergeError(RuntimeError):
    """The observed rank pages cannot be merged into one complete fact."""


def _coerce_row(
    row: Any,
    *,
    context: str,
    allow_non_positive_rank: bool = False,
) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise RankPageMergeError(f"{context} 不是排名行对象：{row!r}")
    rank = row.get("rank")
    if not isinstance(rank, int) or isinstance(rank, bool):
        raise RankPageMergeError(f"{context} 名次无效：{rank!r}")
    # Board rows always carry a real positive position.  The actor's own row may
    # legitimately be ``-1``/``0`` when it has not joined the board yet, so only
    # that row relaxes the positivity rule; it must still be stable across pages.
    if rank <= 0 and not allow_non_positive_rank:
        raise RankPageMergeError(f"{context} 名次无效：{rank!r}")
    score = row.get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        raise RankPageMergeError(f"{context} 积分无效：{score!r}")
    role_key = str(row.get("role_key") or row.get("key") or "").strip()
    if not role_key:
        raise RankPageMergeError(f"{context} 缺少唯一 role_key")
    return {
        "rank": int(rank),
        "score": int(score),
        "role_id": row.get("role_id"),
        "support_value": row.get("support_value"),
        "role_key": role_key,
        "name": str(row.get("name") or ""),
        "server_id": row.get("server_id"),
        "server_name": str(row.get("server_name") or ""),
        "club_name": str(row.get("club_name") or ""),
    }


def _page_identity(page: Any, *, index: int, rank_activity_id: int) -> tuple[int, tuple[Any, Any]]:
    if not isinstance(page, Mapping):
        raise RankPageMergeError(f"第 {index} 页不是快照对象：{page!r}")
    if page.get("ok") is not True or not (
        page.get("complete") is True or page.get("partial") is True
    ):
        raise RankPageMergeError(
            f"第 {index} 页不完整：{page.get('reason') or page.get('error_code') or 'unknown'}"
        )
    if page.get("partial") is True and (
        int(page.get("loaded_rank_count") or 0) != len(page.get("rankings") or [])
        or int(page.get("declared_rank_count") or 0) != len(page.get("rankings") or [])
    ):
        raise RankPageMergeError(f"第 {index} 页声明行尚未完整读取")
    page_id = int(page.get("rank_activity_id") or 0)
    if page_id != int(rank_activity_id):
        raise RankPageMergeError(
            f"第 {index} 页绑定榜身份不一致：page={page_id}，expected={int(rank_activity_id)}"
        )
    total = int(page.get("rank_list_size") or 0)
    if total <= 0:
        raise RankPageMergeError(f"第 {index} 页总人数无效：{total}")
    evidence = page.get("evidence") if isinstance(page.get("evidence"), Mapping) else {}
    pid = evidence.get("pid")
    process_start_ticks = evidence.get("process_start_ticks")
    if pid is None or process_start_ticks is None:
        raise RankPageMergeError(f"第 {index} 页缺少 pid/process_start_ticks 进程绑定")
    return total, (pid, process_start_ticks)


def merge_activity_rank_pages(
    pages: Sequence[Mapping[str, Any]],
    *,
    rank_activity_id: int,
    captured_at: str,
) -> dict[str, Any]:
    """Merge observed pages into one complete, occurrence-agnostic rank fact.

    Every page must bind the same ``rank_activity_id``, the same process
    (``pid``/``process_start_ticks``) and the same total.  Rows are keyed by
    rank; an overlapping rank with a different person or different numbers is an
    explicit failure, never a silent overwrite.  The result only exists when the
    union covers exactly ``1..total`` and every ``role_key`` is unique.
    """

    if not str(captured_at or "").strip():
        raise RankPageMergeError("合并活动榜事实必须带采集时间")
    bound_id = int(rank_activity_id)
    if bound_id <= 0:
        raise RankPageMergeError(f"合并活动榜事实缺少绑定身份：{rank_activity_id!r}")
    rows = tuple(pages)
    if not rows:
        raise RankPageMergeError("没有可合并的活动榜页面")

    merged: dict[int, dict[str, Any]] = {}
    role_keys: dict[str, int] = {}
    totals: set[int] = set()
    processes: set[tuple[Any, Any]] = set()
    self_rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    evidence_pages: list[dict[str, Any]] = []

    for index, page in enumerate(rows, start=1):
        total, process = _page_identity(
            page, index=index, rank_activity_id=bound_id
        )
        totals.add(total)
        processes.add(process)
        self_row = _coerce_row(
            page.get("self_ranking"),
            context=f"第 {index} 页自身排名",
            allow_non_positive_rank=True,
        )
        self_key = tuple(sorted(self_row.items()))
        self_rows.setdefault(self_key, self_row)
        evidence = page.get("evidence") if isinstance(page.get("evidence"), Mapping) else {}
        page_captured_at = str(page.get("captured_at") or captured_at)
        evidence_pages.append({
            "captured_at": page_captured_at,
            "declared_rank_count": int(page.get("declared_rank_count") or 0),
            "loaded_rank_count": int(page.get("loaded_rank_count") or 0),
            "runtime_object_identity": str(page.get("runtime_object_identity") or ""),
            "process_start_ticks": process[1],
        })
        for raw_row in page.get("rankings") or []:
            row = _coerce_row(raw_row, context=f"第 {index} 页排名行")
            rank = int(row["rank"])
            if rank > total:
                raise RankPageMergeError(
                    f"第 {index} 页名次 {rank} 超出总人数 {total}"
                )
            existing = merged.get(rank)
            if existing is not None:
                if existing["role_key"] != row["role_key"]:
                    raise RankPageMergeError(
                        f"名次 {rank} 冲突：{existing['role_key']} != {row['role_key']}"
                    )
                if existing != row:
                    raise RankPageMergeError(f"名次 {rank} 数值在页间发生变化")
                continue
            merged[rank] = row

    if len(totals) != 1:
        raise RankPageMergeError(f"各页总人数不一致：{sorted(totals)}")
    total = next(iter(totals))
    if len(processes) != 1:
        raise RankPageMergeError(f"活动榜在换进程期间被读取，进程绑定不一致：{processes}")
    if len(self_rows) != 1:
        raise RankPageMergeError("各页自身排名身份或数值不一致")
    pid, process_start_ticks = next(iter(processes))

    expected = list(range(1, total + 1))
    if sorted(merged) != expected:
        missing = sorted(set(expected) - set(merged))
        extra = sorted(set(merged) - set(expected))
        raise RankPageMergeError(
            f"活动榜页码不完整：total={total}，缺少={missing[:20]}，越界={extra[:20]}"
        )
    for rank, row in merged.items():
        previous = role_keys.get(row["role_key"])
        if previous is not None:
            raise RankPageMergeError(
                f"role_key 重复：{row['role_key']} 同时出现在名次 {previous} 和 {rank}"
            )
        role_keys[row["role_key"]] = rank

    self_row = next(iter(self_rows.values()))
    return {
        "ok": True,
        "complete": True,
        "source": "runtime_memory_activity_rank_merged",
        "rank_activity_id": bound_id,
        "rank_list_size": int(total),
        "loaded_rank_count": int(total),
        "declared_rank_count": int(total),
        "self_ranking": dict(self_row),
        "rankings": [merged[rank] for rank in expected],
        "captured_at": str(captured_at),
        "evidence": {
            "pid": pid,
            "process_start_ticks": process_start_ticks,
            "source_kind": "read_only_runtime_memory",
            "captured_at": str(captured_at),
            "pages": evidence_pages,
        },
    }


__all__ = [
    "RankPageMergeError",
    "merge_activity_rank_pages",
]
