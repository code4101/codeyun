from __future__ import annotations

"""Event-driven Dongtian seating Job.

Mail is only a wake-up signal.  This Job decides completion exclusively from
one fresh, complete Dongtian Runtime snapshot and never consumes mail content
as action authorization.
"""

import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from backend.core.fanxiu.data_annotation.dongtian_seat_geometry import (
    resolve_dongtian_fixed_seat,
)
from backend.core.fanxiu.data_annotation.dongtian_seating_click import (
    build_dongtian_seating_place_authorization,
)
from backend.core.fanxiu.instrumentation.dongtian import (
    read_dongtian_seating_probe,
    read_dongtian_snapshot,
)


DONGTIAN_SEATING_TASK_ID = "dongtian-seating"
DONGTIAN_TEAM_COUNT = 3


def choose_dongtian_empty_follower_target(
    snapshot: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Choose the first friendly empty follower seat in native mine order.

    This production fast path never inspects or replaces an occupied seat.
    #343 natively selects the lowest-numbered complete idle team when opening
    an empty seat, so the target records that same deterministic team.
    """

    own_union_id = snapshot.get("own_union_id")
    if isinstance(own_union_id, bool) or not isinstance(own_union_id, int):
        return None
    teams = [row for row in snapshot.get("teams") or [] if isinstance(row, Mapping)]
    idle = sorted(
        (
            row
            for row in teams
            if row.get("complete") is True
            and row.get("state") == 1
            and row.get("mine_id") == 0
            and row.get("dead") is False
            and isinstance(row.get("id"), int)
            and not isinstance(row.get("id"), bool)
        ),
        key=lambda row: int(row["id"]),
    )
    if not idle:
        return None
    occupied_mine_ids = {
        int(row["mine_id"])
        for row in teams
        if row.get("state") == 2
        and isinstance(row.get("mine_id"), int)
        and not isinstance(row.get("mine_id"), bool)
        and int(row["mine_id"]) > 0
    }
    for mine in snapshot.get("mines") or []:
        if not isinstance(mine, Mapping):
            continue
        mine_id = mine.get("id")
        group = mine.get("config_group")
        if (
            isinstance(mine_id, bool)
            or not isinstance(mine_id, int)
            or mine_id in occupied_mine_ids
            or mine.get("cross_union_id") != own_union_id
            or mine.get("seats_complete") is not True
            or isinstance(group, bool)
            or not isinstance(group, int)
        ):
            continue
        for seat in mine.get("seats") or []:
            if not isinstance(seat, Mapping):
                continue
            seat_id = seat.get("id")
            if (
                seat.get("complete") is True
                and seat.get("quality") == 2
                and seat.get("empty") is True
                and seat.get("guarder_present") is False
                and seat.get("guarder_type") in {None, 0}
                and isinstance(seat_id, int)
                and not isinstance(seat_id, bool)
            ):
                return {
                    "mine_id": mine_id,
                    "quality": 2,
                    "seat_id": seat_id,
                    "team_id": int(idle[0]["id"]),
                    "config_group": group,
                    "mode": "occupy_empty",
                }
    return None


def classify_dongtian_team_seating(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if (
        snapshot.get("available") is not True
        or snapshot.get("seating_summary_complete") is not True
    ):
        return {
            "ok": False,
            "status": "runtime_incomplete",
            "reason": str(snapshot.get("reason") or "洞天 Runtime 不完整"),
            "seated_team_ids": [],
            "idle_team_ids": [],
        }

    teams = [row for row in snapshot.get("teams") or [] if isinstance(row, Mapping)]
    normalized: dict[int, Mapping[str, Any]] = {}
    for row in teams:
        team_id = row.get("id")
        if isinstance(team_id, bool) or not isinstance(team_id, int):
            continue
        if team_id in normalized:
            return {
                "ok": False,
                "status": "runtime_incomplete",
                "reason": "洞天 Runtime 出现重复队伍",
                "seated_team_ids": [],
                "idle_team_ids": [],
            }
        normalized[team_id] = row
    if set(normalized) != {1, 2, 3} or any(
        row.get("complete") is not True for row in normalized.values()
    ):
        return {
            "ok": False,
            "status": "runtime_incomplete",
            "reason": "洞天三支队伍身份或完整性不足",
            "seated_team_ids": [],
            "idle_team_ids": [],
        }

    seated: list[int] = []
    idle: list[int] = []
    for team_id in (1, 2, 3):
        row = normalized[team_id]
        state = row.get("state")
        mine_id = row.get("mine_id")
        seat_index = row.get("seat_index")
        if state == 2 and isinstance(mine_id, int) and mine_id > 0 and isinstance(seat_index, int) and seat_index > 0:
            seated.append(team_id)
        elif state == 1 and mine_id == 0:
            idle.append(team_id)
        else:
            return {
                "ok": False,
                "status": "runtime_incomplete",
                "reason": f"洞天队伍{team_id}状态无法归类",
                "seated_team_ids": seated,
                "idle_team_ids": idle,
            }
    return {
        "ok": True,
        "status": "all_seated" if len(seated) == DONGTIAN_TEAM_COUNT else "reseat_required",
        "reason": "三支队伍均已上座" if len(seated) == DONGTIAN_TEAM_COUNT else "存在空闲队伍",
        "seated_team_ids": seated,
        "idle_team_ids": idle,
    }


def execute_dongtian_seating_job(
    runner: Any,
    payload: Mapping[str, Any] | None = None,
    *,
    snapshot_reader: Callable[[], Mapping[str, Any]] = read_dongtian_snapshot,
) -> str:
    """Close the idempotent all-seated branch; fail closed before unsafe GUI.

    The friend-swap action path is deliberately not synthesized here: only two
    occupied attendant hitboxes currently have real click evidence, while the
    permanent policy requires the true minimum across every occupied seat in
    a location.  A partial scan must never kick a player.
    """

    task_id = str((payload or {}).get("__scheduler_task_id") or DONGTIAN_SEATING_TASK_ID)
    snapshot = snapshot_reader()
    state = classify_dongtian_team_seating(snapshot)
    if not state["ok"]:
        raise RuntimeError(f"洞天_上座：{state['reason']}")
    if state["status"] != "all_seated":
        raise RuntimeError(
            "洞天_上座：检测到空闲队伍，但全地点当前最低仙侣战力扫描尚未具备完整真实落点；"
            "按3队80%安全规则拒绝从部分候选中换位"
        )

    runner._persist_scheduler_task_next_time(task_id, None)
    runner._log(
        "success",
        "洞天_上座：Runtime 已确认1/2/3队全部在座；邮件只作触发，本轮零点击并清空 next_time",
    )
    return "success"


def execute_dongtian_seating_runtime_job(
    runner: Any,
    ctx: Mapping[str, Any],
    stop_event: threading.Event,
    payload: Mapping[str, Any] | None = None,
    *,
    snapshot_reader: Callable[[], Mapping[str, Any]] = read_dongtian_snapshot,
    probe_reader: Callable[..., Mapping[str, Any]] = read_dongtian_seating_probe,
):
    """Seat idle teams into independently proven empty follower seats.

    Occupied-seat replacement remains fail-closed for AI intervention.  Every
    empty placement is authorized by a fresh targeted probe, uses the verified
    Runtime seat projection, and is accepted only after Runtime reports the
    expected team/mine/seat tuple.
    """

    task_id = str((payload or {}).get("__scheduler_task_id") or DONGTIAN_SEATING_TASK_ID)
    snapshot = dict(snapshot_reader())
    state = classify_dongtian_team_seating(snapshot)
    if not state["ok"]:
        raise RuntimeError(f"洞天_上座：{state['reason']}")
    if state["status"] == "all_seated":
        return execute_dongtian_seating_job(
            runner,
            {"__scheduler_task_id": task_id},
            snapshot_reader=lambda: snapshot,
        )

    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("洞天_上座：缺少资产树路径")
    runtime = runner._fanxiu_runtime(
        dict(ctx),
        asset_tree_path,
        stop_event=stop_event,
    )
    mines = [row for row in snapshot.get("mines") or [] if isinstance(row, Mapping)]
    all_mine_ids = {
        int(row["id"])
        for row in mines
        if isinstance(row.get("id"), int) and not isinstance(row.get("id"), bool)
    }
    placements = 0
    while True:
        state = classify_dongtian_team_seating(snapshot)
        if not state["ok"]:
            raise RuntimeError(f"洞天_上座：{state['reason']}")
        if state["status"] == "all_seated":
            runner._persist_scheduler_task_next_time(task_id, None)
            runner._log(
                "success",
                f"洞天_上座：Runtime 已确认1/2/3队全部在座；本轮空席入驻 {placements} 队并清空 next_time",
            )
            return "success"

        target = choose_dongtian_empty_follower_target(snapshot)
        if target is None:
            raise RuntimeError(
                "洞天_上座：仍有空闲队伍，但本盟不同地点没有可证明的空侍从席；"
                "需要 AI 检查守军详情后决定是否安全替换"
            )
        mine_id = int(target["mine_id"])
        excluded = all_mine_ids - {mine_id}
        probe = dict(probe_reader(excluded_mine_ids=excluded))
        authorization = build_dongtian_seating_place_authorization(probe)

        yield from runtime.goto_view(279)
        yield from runner._daily_dongtian_click_seating_target(
            runtime,
            stop_event,
            authorization,
            max_scrolls=int((payload or {}).get("max_scrolls") or 24),
            probe_reader=probe_reader,
        )
        geometry = resolve_dongtian_fixed_seat(
            2,
            int(target["seat_id"]),
            group=int(target["config_group"]),
        )
        runtime.click_frame_point(341, *geometry.point)
        yield from runtime.wait_scene(
            343,
            timeout=15,
            label="洞天_上座：打开空侍从席队伍确认",
        )
        yield from runtime.wait_click_then_view(
            343,
            "占领",
            341,
            settle_seconds=0.5,
            retry_if_source_remains=False,
            max_clicks=1,
            label="洞天_上座：空席直接入驻并返回地点详情",
        )

        after_probe = dict(probe_reader(excluded_mine_ids=excluded))
        expected_team_id = int(target["team_id"])
        expected_seat_id = int(target["seat_id"])
        teams = [row for row in after_probe.get("teams") or [] if isinstance(row, Mapping)]
        post_team = next((row for row in teams if row.get("id") == expected_team_id), None)
        if not (
            isinstance(post_team, Mapping)
            and post_team.get("state") == 2
            and post_team.get("mine_id") == mine_id
            and post_team.get("seat_index") == expected_seat_id
        ):
            raise RuntimeError(
                "洞天_上座：点击后 Runtime 未证明预期队伍入驻目标空席，拒绝继续下一队"
            )
        snapshot["teams"] = teams
        snapshot["seating_summary_complete"] = after_probe.get("complete") is True
        placements += 1


__all__ = [
    "DONGTIAN_SEATING_TASK_ID",
    "classify_dongtian_team_seating",
    "choose_dongtian_empty_follower_target",
    "execute_dongtian_seating_job",
    "execute_dongtian_seating_runtime_job",
]
