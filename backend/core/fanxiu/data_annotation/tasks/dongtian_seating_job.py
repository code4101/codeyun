from __future__ import annotations

"""Keep all three Dongtian teams seated, retaining every existing seat.

Mail is only a wake-up signal.  This Job decides completion exclusively from
one fresh, complete Dongtian Runtime snapshot and never consumes mail content
as action authorization. Fixed daily checks and Monday competition rechecks
share this idempotent goal: fill idle teams, never optimize occupied seats.
"""

import threading
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

from backend.core.fanxiu.data_annotation.dongtian_seat_geometry import (
    resolve_dongtian_fixed_seat,
)
from backend.core.fanxiu.data_annotation.dongtian_seating_click import (
    build_dongtian_seating_place_authorization,
)
from backend.core.fanxiu.data_annotation.dongtian_seating_schedule import next_dongtian_seating_at
from backend.core.fanxiu.instrumentation.dongtian import (
    read_dongtian_seating_probe,
    read_dongtian_snapshot,
)


DONGTIAN_SEATING_TASK_ID = "dongtian-seating"
DONGTIAN_TEAM_COUNT = 3


def open_dongtian_seating_place(context: Any, mine_id: int):
    """Open a current friendly place for inspection, without occupying a seat.

    The provider resolves the full Runtime identity and revalidates it before
    navigation. Both diagnosis and the Job use the same authorized route.
    """
    snapshot = read_dongtian_snapshot()
    if snapshot.get("seating_summary_complete") is not True:
        raise RuntimeError(f"洞天地点检查：Runtime 不完整：{snapshot.get('reason') or snapshot}")
    mine = next((m for m in snapshot.get("mines") or [] if m.get("id") == mine_id), None)
    if mine is None:
        raise ValueError(f"洞天地点不存在：{mine_id}")
    if mine.get("cross_union_id") != snapshot.get("own_union_id"):
        raise RuntimeError("洞天地点检查：目标不是本方联盟地点")
    # The seating probe deliberately omits full places. Inspection must be
    # able to open those places to learn defender strength; it grants no
    # occupy authority and consumes the full model's current identity.
    yield from context.go_scene(279)
    yield from context.runner._daily_dongtian_click_place(
        context, context.stop_event, [str(mine["config_name"])],
        max_scrolls=24, task_label="洞天守军检查",
    )
    return (yield from context.wait_scene([341], wait=15, label="洞天守军检查：地点详情"))


def inspect_dongtian_place_defenders(context: Any, mine_id: int, *, group: int, seat_keys: list[str] | None = None):
    """Read all occupied defenders through their actual native GUI panels.

    Opens no swap/occupy confirmation. Each follower result must identify the
    requested mine and seat in the GUI-populated Runtime cache, so a shifted
    map cannot silently assign one defender's strength to another seat.
    """
    from backend.core.fanxiu.instrumentation.dongtian import DongtianSeatingRuntimeSession

    scores: dict[str, int] = {}
    wanted = set(seat_keys) if seat_keys is not None else {f"{mine_id}:{1 if i < 4 else 2}:{i}" for i in range(1, 13)}
    yield from context.wait_scene_exact([341], timeout=15)
    session = DongtianSeatingRuntimeSession.open(max_age_seconds=600)
    if any(f"{mine_id}:1:{i}" in wanted for i in (1, 2, 3)):
        yield from context.wait_click_then_scene(341, "位置1", 342)
        for seat_id in (1, 2, 3):
            if f"{mine_id}:1:{seat_id}" not in wanted:
                continue
            result = session.cached_seat_detail(mine_id=mine_id, quality=1, seat_id=seat_id)
            detail = result.get("detail") or {}
            if not result.get("complete") or not detail.get("complete"):
                raise RuntimeError(f"洞天尊主详情未就绪：{result}")
            scores[f"{mine_id}:1:{seat_id}"] = int(detail["fight_score"])
        yield from context.wait_click_then_scene(342, "关闭", 341)
    for seat_id in range(4, 13):
        if f"{mine_id}:2:{seat_id}" not in wanted:
            continue
        yield from context.wait_scene_exact([341], timeout=15)
        geometry = resolve_dongtian_fixed_seat(2, seat_id, group=group)
        context.click_frame_point(341, *geometry.point)
        landed = yield from context.wait_scene([607, 343], wait=15)
        if landed.scene_id not in (607, 343):
            raise RuntimeError(f"洞天侍从检查落点异常：#{landed.scene_id}")
        result = session.cached_final_guard_team_detail(mine_id=mine_id, quality=2, seat_id=seat_id)
        detail = result.get("detail") or {}
        if not (result.get("complete") and detail.get("complete")
                and detail.get("observed_mine_id") == mine_id
                and detail.get("observed_seat_id") == seat_id):
            raise RuntimeError(f"洞天侍从详情身份不符：{result}")
        scores[f"{mine_id}:2:{seat_id}"] = int(detail["fight_score"])
        context.runner._log("detail", f"洞天守军：{mine_id}:2:{seat_id} 战力={detail['fight_score']}")
        if landed.scene_id != 607:
            raise RuntimeError("洞天守军检查：当前非友军详情，尚缺经验证的安全返回")
        yield from context.wait_click_then_scene(607, "返回", 341)
    return scores


def occupy_dongtian_empty_seat(context: Any, target: Mapping[str, Any]):
    """Commit a prepared empty seat, verifying team, vacancy and exact result."""
    from backend.core.fanxiu.data_annotation.dongtian_seating_plan import plan_dongtian_friend_staging
    from backend.core.fanxiu.data_annotation.dongtian_seating_postcondition import evaluate_dongtian_seating_postcondition

    snapshot = read_dongtian_snapshot()
    # Navigation freezes a safe staging destination, not a globally optimal
    # live ranking. A newly vacated different place must not cause endless
    # chasing; revalidate both players' absence and the selected seat here.
    exact = {**snapshot, "mines": [m for m in snapshot.get("mines") or [] if m.get("id") == target["mine_id"]]}
    if "friend_role_id" in target:
        fresh = plan_dongtian_friend_staging(
            exact, friend_role_id=int(target["friend_role_id"]), team_id=int(target["team_id"]),
        )
        keys = ("status", "mine_id", "quality", "seat_id", "team_id", "seat_kind")
        if any(fresh.get(k) != target.get(k) for k in keys) or fresh.get("seat_kind") != "empty":
            raise RuntimeError(f"洞天交换落脚席已变化，未占领：{fresh}")
    if not classify_dongtian_team_seating(snapshot)["ok"]:
        raise RuntimeError("洞天空席：队伍状态不完整")
    team = next(t for t in snapshot["teams"] if t["id"] == target["team_id"])
    mine = next(m for m in snapshot["mines"] if m["id"] == target["mine_id"])
    seat = next(s for s in mine["seats"] if s["quality"] == target["quality"] and s["id"] == target["seat_id"])
    if (mine.get("cross_union_id") != snapshot["own_union_id"]
            or seat.get("complete") is not True or seat.get("empty") is not True
            or team.get("state") != 1 or team.get("mine_id") != 0
            or any(t.get("mine_id") == target["mine_id"] for t in snapshot["teams"])):
        raise RuntimeError("洞天空席：归属、空席或队伍状态已变化，未占领")
    yield from context.wait_scene_exact([886], timeout=15)
    yield from context.wait_click(886, f"队伍{target['team_id']}")
    yield from context.wait_action_settle(1.5)
    for observation in range(5):
        yield from context.wait_scene_exact([886], timeout=15)
        text = context.ocr_text_in_shapes(886, ["我方当前战力"], frame_data_url=context.cur_frame(update=True), crop=True)
        match = re.search(r"战力[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*([万亿兆京]?)", text)
        if match:
            scale = {"": 1, "万": 10**4, "亿": 10**8, "兆": 10**12, "京": 10**16}[match[2]]
            shown = Decimal(match[1]) * scale
            quantum = Decimal(10) ** Decimal(-len(match[1].partition('.')[2])) * scale
            if abs(shown - int(team["fight_score"])) <= quantum:
                break
        if observation == 4:
            raise RuntimeError(f"洞天空席：未确认所选队伍战力，未占领：{text}")
        yield from context.wait_action_settle(1.0)
    yield from context.wait_click_then_scene(886, "占领", 341, max_clicks=1, retry_if_source_remains=False)
    after = read_dongtian_snapshot()
    checked = evaluate_dongtian_seating_postcondition(after, target)
    if not checked.get("ok"):
        raise RuntimeError(f"洞天空席占领后验证失败：{checked}")
    return after


def prepare_dongtian_friend_swap(context: Any, target: Mapping[str, Any], staging: Mapping[str, Any]):
    """Verify both destinations and open the team picker, without submitting."""
    from backend.core.fanxiu.instrumentation.dongtian import read_dongtian_cached_final_guard_team_detail
    from backend.core.fanxiu.data_annotation.dongtian_seating_postcondition import evaluate_dongtian_seating_postcondition

    snapshot = read_dongtian_snapshot()
    checked = evaluate_dongtian_seating_postcondition(snapshot, staging)
    if not checked.get("ok"):
        raise RuntimeError(f"洞天互换：落脚席不属于预定队伍：{checked}")
    mine = next(m for m in snapshot["mines"] if m["id"] == target["mine_id"])
    stage_mine = next(m for m in snapshot["mines"] if m["id"] == staging["mine_id"])
    seat = next(s for s in mine["seats"] if s["id"] == target["seat_id"] and s["quality"] == target["quality"])
    friend = int(staging["friend_role_id"])
    if (mine["cross_union_id"] != snapshot["own_union_id"]
            or seat.get("guarder_role_id") != friend
            or any(s.get("guarder_role_id") == friend for s in stage_mine["seats"])
            or any(s.get("guarder_role_id") == snapshot["own_role_id"] for s in mine["seats"])):
        raise RuntimeError("洞天互换：双方身份或地点互斥条件已变化，未申请")
    detail = read_dongtian_cached_final_guard_team_detail(
        mine_id=int(target["mine_id"]), quality=int(target["quality"]), seat_id=int(target["seat_id"]),
    )
    score = (detail.get("detail") or {}).get("fight_score")
    team3 = next(t for t in snapshot["teams"] if t["id"] == 3)
    if not detail.get("complete") or not isinstance(score, int) or score * 5 >= team3["fight_score"] * 4:
        raise RuntimeError(f"洞天互换：守军超过三队80%安全线或事实不完整：{detail}")
    yield from context.wait_click_then_scene(607, "互换采气", 612, max_clicks=1, retry_if_source_remains=False)
    yield from context.wait_click(612, f"{target['team_id']}队")
    yield from context.wait_action_settle(1.5)
    return {"target": dict(target), "staging": dict(staging), "friend_role_id": friend}


def complete_dongtian_friend_swap(context: Any, target: Mapping[str, Any], staging: Mapping[str, Any]):
    """Swap the staged team and prove the ally received the staging seat."""
    from backend.core.fanxiu.data_annotation.dongtian_seating_postcondition import evaluate_dongtian_seating_postcondition

    yield from prepare_dongtian_friend_swap(context, target, staging)
    yield from context.wait_click_then_scene(612, "申请换位", 613, max_clicks=1, retry_if_source_remains=False)
    yield from context.wait_click_then_scene(613, "确认", 341, max_clicks=1, retry_if_source_remains=False)
    after = read_dongtian_snapshot()
    checked = evaluate_dongtian_seating_postcondition(after, target)
    if not checked.get("ok"):
        raise RuntimeError(f"洞天互换后我方席位不符：{checked}")
    mine = next(m for m in after["mines"] if m["id"] == staging["mine_id"])
    seat = next(s for s in mine["seats"] if s["quality"] == staging["quality"] and s["id"] == staging["seat_id"])
    if not seat.get("complete") or seat.get("guarder_role_id") != staging["friend_role_id"]:
        raise RuntimeError(f"洞天互换后未确认盟友落脚席：{seat}")
    context.runner._log("success", f"洞天互换：{target['team_id']}队进入 {target['mine_id']}:{target['seat_id']}，盟友进入 {staging['mine_id']}:{staging['seat_id']}")
    return after


def open_dongtian_empty_seat(context: Any, target: Mapping[str, Any]):
    """Open an empty seat's team picker; no occupy action is submitted."""
    yield from open_dongtian_seating_place(context, int(target["mine_id"]))
    geometry = resolve_dongtian_fixed_seat(int(target["quality"]), int(target["seat_id"]), group=int(target.get("config_group") or 4))
    context.click_frame_point(341, *geometry.point)
    if target["quality"] == 1:
        yield from context.wait_scene_exact([342], timeout=15)
        current = read_dongtian_snapshot()
        mine = next(m for m in current.get("mines") or [] if m["id"] == target["mine_id"])
        empty_ids = [s["id"] for s in mine["seats"] if s["quality"] == 1 and s.get("empty") is True]
        if not current.get("seating_summary_complete") or empty_ids != [target["seat_id"]]:
            raise RuntimeError(f"洞天空尊主：列表未证明唯一目标空席，未选择：{empty_ids}")
        yield from context.wait_click_then_scene(342, "占领", 886, max_clicks=1, retry_if_source_remains=False)
    return (yield from context.wait_scene_exact([886], timeout=15))


def seat_dongtian_team_by_friendly_swap(context: Any, snapshot: Mapping[str, Any], target: Mapping[str, Any], scores: Mapping[str, int]):
    """Prepare a safe vacant Fudi, then exchange both seats atomically in game."""
    from backend.core.fanxiu.data_annotation.dongtian_seating_plan import plan_dongtian_friend_staging

    mine = next(m for m in snapshot["mines"] if m["id"] == target["mine_id"])
    seat = next(s for s in mine["seats"] if s["quality"] == target["quality"] and s["id"] == target["seat_id"])
    staging = plan_dongtian_friend_staging(
        snapshot, friend_role_id=int(seat["guarder_role_id"]), team_id=int(target["team_id"]), defender_scores=scores,
    )
    if staging.get("status") != "ready" or staging.get("seat_kind") != "empty":
        raise RuntimeError(f"洞天互换：尚无已验证的空落脚席路线：{staging}")
    yield from open_dongtian_empty_seat(context, staging)
    yield from occupy_dongtian_empty_seat(context, staging)
    yield from open_dongtian_seating_place(context, int(target["mine_id"]))
    if target["quality"] != 2:
        raise RuntimeError("洞天互换尊主入口尚未实机验证，保留已入驻的落脚席")
    geometry = resolve_dongtian_fixed_seat(2, int(target["seat_id"]), group=int(target["config_group"]))
    context.click_frame_point(341, *geometry.point)
    yield from context.wait_scene_exact([607], timeout=15)
    return (yield from complete_dongtian_friend_swap(context, target, staging))


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


def choose_dongtian_ranked_empty_target(
    snapshot: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Execute only a vacancy chosen by the SAME planner used by research.

    An unresolved higher-priority defender is not evidence that its location
    is unavailable. Never skip it to find an easier but inferior vacant mine.
    Team-specific safety/yield preferences therefore cannot diverge here.
    """
    from backend.core.fanxiu.data_annotation.dongtian_seating_plan import plan_dongtian_next_team
    decision = plan_dongtian_next_team(snapshot)
    if decision.get("status") != "ready" or decision.get("action") != "occupy_empty":
        return None
    return {**decision, "mode": "occupy_empty"}


def validate_dongtian_empty_follower_target(
    snapshot: Mapping[str, Any], target: Mapping[str, Any],
) -> bool:
    """Revalidate the exact empty seat and natively selected idle team.

    Navigation can outlive an empty seat during the Monday reset.  An older
    place authorization proves neither current vacancy nor which team #343
    will select.  Re-evaluate both from one complete current model before
    opening the seat and again before committing its confirmation.
    """
    if not classify_dongtian_team_seating(snapshot)["ok"]:
        return False
    exact = dict(snapshot)
    exact["mines"] = [
        mine for mine in snapshot.get("mines") or []
        if isinstance(mine, Mapping) and mine.get("id") == target.get("mine_id")
    ]
    if len(exact["mines"]) != 1:
        return False
    mine = dict(exact["mines"][0])
    mine["seats"] = [
        seat for seat in mine.get("seats") or []
        if isinstance(seat, Mapping) and seat.get("id") == target.get("seat_id")
    ]
    if len(mine["seats"]) != 1:
        return False
    exact["mines"] = [mine]
    return choose_dongtian_ranked_empty_target(exact) == dict(target)


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

    runner._persist_scheduler_task_next_time(task_id, next_dongtian_seating_at().strftime("%Y-%m-%d %H:%M:%S"))
    runner._log(
        "success",
        "洞天_上座：Runtime 已确认1/2/3队全部在座；保留现有座位，已安排下次固定检查",
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
    """Seat idle teams through verified vacancies or staged friendly swaps.

    Read missing defender facts, prepare a safe destination for a displaced
    ally, and verify both seats after exchange. Unsupported conquest/master
    swap routes still stop with their exact decision and preserve the UI.
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
    context = runner._behavior_tree_context(
        dict(ctx),
        asset_tree_path,
        stop_event=stop_event,
    )
    placements = 0
    defender_scores: dict[str, int] = {}
    while True:
        # The initial read or the previous placement's full postcondition
        # supplies fresh places and teams; do not repeat that expensive read.
        state = classify_dongtian_team_seating(snapshot)
        if not state["ok"]:
            raise RuntimeError(f"洞天_上座：{state['reason']}")
        if state["status"] == "all_seated":
            runner._persist_scheduler_task_next_time(task_id, next_dongtian_seating_at().strftime("%Y-%m-%d %H:%M:%S"))
            runner._log(
                "success",
                f"洞天_上座：Runtime 已确认1/2/3队全部在座；本轮完成入座 {placements} 队，已安排下次固定检查",
            )
            return "success"

        from backend.core.fanxiu.data_annotation.dongtian_seating_plan import plan_dongtian_next_team
        decision = plan_dongtian_next_team(snapshot, defender_scores=defender_scores)
        if decision.get("status") == "needs_defender_scores":
            mine_id = int(decision["mine_id"])
            mine = next(m for m in snapshot["mines"] if m["id"] == mine_id)
            yield from open_dongtian_seating_place(context, mine_id)
            defender_scores.update((yield from inspect_dongtian_place_defenders(
                context, mine_id, group=int(mine["config_group"]), seat_keys=list(decision["seat_keys"]),
            )))
            snapshot = dict(snapshot_reader())
            continue
        if decision.get("status") == "needs_friend_swap_research" and decision.get("quality") == 2:
            snapshot = yield from seat_dongtian_team_by_friendly_swap(context, snapshot, decision, defender_scores)
        elif decision.get("status") == "ready" and decision.get("action") == "occupy_empty":
            yield from open_dongtian_empty_seat(context, decision)
            snapshot = yield from occupy_dongtian_empty_seat(context, decision)
        else:
            raise RuntimeError(f"洞天_上座：当前计划尚缺真实执行分支：{decision}")
        placements += 1
        defender_scores.clear()



__all__ = [
    "DONGTIAN_SEATING_TASK_ID",
    "classify_dongtian_team_seating",
    "choose_dongtian_empty_follower_target",
    "validate_dongtian_empty_follower_target",
    "execute_dongtian_seating_job",
    "execute_dongtian_seating_runtime_job",
]
