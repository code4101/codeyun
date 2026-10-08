from __future__ import annotations

"""Read-only planning boundary for the independent Xianyuan gift Job."""

import threading
from math import ceil
from collections.abc import Callable, Generator, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_policy import (
    NPC_GIFT_EXTERNAL_ROUTE,
    StorageBagAutoClaimDecision,
    decide_storage_bag_auto_claim_item,
)
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service
from backend.core.fanxiu.instrumentation.storage_bag_catalog import (
    build_storage_bag_catalog_snapshot,
)
from backend.core.fanxiu.instrumentation.xianyuan_atlas import (
    read_xianyuan_atlas_runtime,
)
from backend.core.fanxiu.storage_bag_settings import apply_storage_bag_item_settings
from backend.core.fanxiu.storage_bag_usage import (
    ensure_storage_bag_atlas_analysis,
    storage_bag_analysis_fingerprint,
)
from backend.db import engine


STANDARD_JOB_ID = "xianyuan-auto-gift"
TASK_TYPE = "xianyuan_auto_gift"


def xianyuan_auto_gift_next_time(result, payload, *, now=None):
    """Start ordinary gifts Monday midnight; resume bounded Monday cycles.

    Explicit research remains separate from the weekly automatic cadence.
    Daily bag stock observations never wake this Job.
    """
    moment = (now or datetime.now().astimezone()).astimezone()
    next_monday = (moment + timedelta(days=(7 - moment.weekday()) % 7 or 7)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    if result.get('outcome') not in {'complete', 'partial'}:
        raise RuntimeError('仙缘送礼调度缺少明确业务终态')
    if result['outcome'] == 'complete' or payload.get('gift_probe_only') or moment.weekday() != 0:
        return next_monday.strftime('%Y-%m-%d %H:%M:%S')
    admission = result.get('action_result', {}).get('yield_for_due_job')
    if admission:
        retry = datetime.fromisoformat(admission['retry_at']).astimezone()
    else:
        retry = moment + timedelta(minutes=10)
    if retry.weekday() != 0:
        retry = next_monday
    return retry.astimezone().strftime('%Y-%m-%d %H:%M:%S')

SnapshotReader = Callable[[], dict[str, Any]]
GiftGuiAdapter = Callable[
    [Any, dict[str, Any], dict[str, Any], threading.Event, dict[str, Any]],
    Generator[Any, Any, dict[str, Any]],
]


def read_selected_storage_gifts_runtime() -> dict[str, Any]:
    """Project current read-only backpack Runtime rows with persisted user flags."""

    runtime_snapshot = fanxiu_instrumentation_service.backpack_ui_snapshot()
    cards_by_id = load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"]
    atlas = build_storage_bag_catalog_snapshot(
        runtime_snapshot,
        cards_by_id,
        captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    with Session(engine) as session:
        ensure_storage_bag_atlas_analysis(session, atlas)
        session.commit()
        return apply_storage_bag_item_settings(session, atlas)


def routed_npc_gift_items(
    storage_snapshot: Mapping[str, Any],
) -> list[StorageBagAutoClaimDecision]:
    """Select only the route explicitly owned by this Job."""

    if storage_snapshot.get("complete") is not True:
        raise RuntimeError("仙缘_自动送礼：储物袋 Runtime 清单不完整")
    result: list[StorageBagAutoClaimDecision] = []
    for row in storage_snapshot.get("items") or []:
        if not isinstance(row, Mapping):
            continue
        if row.get("auto_claim") is True and (
            str(row.get("analysis_fingerprint") or "")
            != storage_bag_analysis_fingerprint(row)
        ):
            raise RuntimeError(
                f"仙缘_自动送礼：礼物 {int(row.get('base_id') or 0)} 的持久化分类已过期"
            )
        decision = decide_storage_bag_auto_claim_item(row)
        if (
            decision is not None
            and decision.action == "route"
            and decision.external_route == NPC_GIFT_EXTERNAL_ROUTE
        ):
            result.append(decision)
    return result


def build_xianyuan_auto_gift_plan(
    routes: list[StorageBagAutoClaimDecision],
    xianyuan_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Map gift item IDs to native Xianyuan ``npc_id/runtime_index`` identities."""

    if xianyuan_snapshot.get("runtime_complete") is not True:
        raise RuntimeError("仙缘_自动送礼：仙缘 Runtime 对象清单不完整")

    people = [
        person
        for person in (xianyuan_snapshot.get("people") or [])
        if isinstance(person, Mapping)
    ]
    native_identities: set[tuple[int, int]] = set()
    normalized_people: list[tuple[Mapping[str, Any], int, int]] = []
    for person in people:
        npc_id = int(person.get("npc_id") or 0)
        raw_runtime_index = person.get("runtime_index")
        if npc_id <= 0 or not isinstance(raw_runtime_index, int) or raw_runtime_index < 0:
            continue
        identity = (npc_id, raw_runtime_index)
        if identity in native_identities:
            raise RuntimeError(f"仙缘_自动送礼：Runtime 对象身份重复 {identity}")
        native_identities.add(identity)
        normalized_people.append((person, npc_id, raw_runtime_index))

    items: list[dict[str, Any]] = []
    for route in routes:
        targets: list[dict[str, Any]] = []
        for person, npc_id, runtime_index in normalized_people:
            if person.get("giftable") is not True or person.get("hostile") is True:
                continue
            matching_options = [
                option
                for option in (person.get("gift_options") or [])
                if isinstance(option, Mapping)
                and int(option.get("item_id") or 0) == route.base_id
            ]
            if len(matching_options) > 1:
                raise RuntimeError(
                    f"仙缘_自动送礼：NPC {npc_id} 对礼物 {route.base_id} 存在重复配置映射"
                )
            if not matching_options:
                continue
            option = matching_options[0]
            targets.append({
                "npc_id": npc_id,
                "runtime_index": runtime_index,
                "name": str(person.get("name") or f"仙缘 {npc_id}"),
                "favor": int(person.get("favor") or 0),
                "favor_level": int(person.get("favor_level") or 0),
                "gift_item_id": route.base_id,
                "favorability_per_item": int(option.get("favorability") or 0),
                "career_conditional": bool(option.get("career_conditional")),
                "activity_gift": bool(option.get("activity_gift")),
            })
        if not targets:
            raise RuntimeError(
                f"仙缘_自动送礼：礼物 {route.base_id} 未映射到可送礼 Runtime 对象"
            )
        items.append({
            "base_id": route.base_id,
            "quantity": route.quantity,
            "note": route.note,
            "eligible_targets": targets,
        })

    return {
        "task_type": TASK_TYPE,
        "source_route": NPC_GIFT_EXTERNAL_ROUTE,
        "item_count": len(items),
        "items": items,
    }


def plan_xianyuan_gift_batch(item: Mapping[str, Any], person: Mapping[str, Any]) -> dict[str, Any]:
    """Bound one selected gift to the current recipient's unclaimed reward cycle.

    Permanent capped favor and reset favor are independent tracks. Claim existing
    rewards before giving more, and return a reset requirement after a fully
    claimed cycle. Conditional gifts need their own live eligibility adapter.
    """
    npc_id = int(person.get('npc_id') or 0)
    base_id = int(item.get('base_id') or 0)
    if person.get('giftable') is not True or person.get('hostile'):
        raise ValueError('当前人物不可赠礼')
    targets = [t for t in item.get('eligible_targets', []) if
               t.get('npc_id') == npc_id and t.get('runtime_index') == person.get('runtime_index')]
    options = [o for o in person.get('gift_options', []) if o.get('item_id') == base_id]
    if npc_id <= 0 or base_id <= 0 or len(targets) != 1 or len(options) != 1:
        raise ValueError('赠礼计划与当前原生人物/物品身份不符')
    option = options[0]
    if option.get('career_conditional') or option.get('activity_gift'):
        raise ValueError('条件礼物尚未核验当前资格')
    gain = int(option.get('favorability') or 0)
    rewards = list(person.get('rewards') or [])
    thresholds = {int(t['level']): t for t in person.get('favor_thresholds', [])}
    if gain <= 0 or not rewards or not thresholds or any(r.get('state') not in {0, 1, 2} for r in rewards):
        raise ValueError('赠礼收益或当前奖励阈值事实不完整')
    identity = {'npc_id': npc_id, 'runtime_index': person['runtime_index'], 'base_id': base_id}
    if any(r['state'] == 1 for r in rewards):
        return {**identity, 'action': 'claim_rewards', 'quantity': 0}
    # Empty selected stock ends gift work after any existing rewards are claimed.
    # A fully claimed favor cycle alone is not a reason to reset an NPC.
    if int(item.get('quantity') or 0) <= 0:
        return {**identity, 'action': 'inventory_exhausted', 'quantity': 0}
    pending = [r for r in rewards if r['state'] != 2]
    if not pending:
        return {**identity, 'action': 'reset_required', 'quantity': 0}
    target_level = max(int(r['level']) for r in pending)
    if target_level not in thresholds:
        raise ValueError('当前奖励档次没有明确好感阈值')
    uses_reset = int(person.get('reset_favor_level') or 0) > 0 or int(person.get('favor_level') or 0) >= target_level
    track = 'reset_favor' if uses_reset else 'favor'
    current = int(person.get(track) or 0)
    target = int(thresholds[target_level][track])
    track_cap = max(int(threshold[track]) for threshold in thresholds.values())
    if target <= current:
        raise ValueError('好感已到奖励阈值但领取状态未对齐，先回读奖励')
    quantity = min(max(0, int(item.get('quantity') or 0)), ceil((target - current) / gain))
    return {**identity, 'action': 'gift' if quantity else 'inventory_exhausted', 'quantity': quantity,
            'progress_track': track, 'favor_before': current, 'target_favor': target,
            'favor_per_item': gain, 'planned_favor_gain': quantity * gain,
            'track_cap': track_cap,
            'expected_favor_after': min(track_cap, current + quantity * gain)}


def verify_xianyuan_gift_effect(batch, before, after, *, inventory_before, inventory_after,
                               inventory_before_evidence, inventory_after_evidence):
    """Prove one submitted gift against same-process inventory and native favor.

    A failed observation must be reconciled against the original action; this
    verifier never authorizes a second submission or accepts a GUI success alone.
    """
    if batch.get('action') != 'gift' or int(batch.get('quantity') or 0) <= 0:
        raise ValueError('验账需要明确赠送批次')
    prior, current = before.get('runtime_debug', {}), after.get('runtime_debug', {})
    if not before.get('runtime_complete') or not after.get('runtime_complete') or any(
        not prior.get(key) or prior.get(key) != current.get(key)
        for key in ['pid', 'process_start_ticks']
    ):
        raise RuntimeError('赠礼前后进程身份或完整性不符')
    start, end = before.get('runtime_updated_at'), after.get('runtime_updated_at')
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
        raise RuntimeError('赠礼前后观察时间未按操作顺序刷新')
    if type(inventory_before) is not int or type(inventory_after) is not int or inventory_after < 0:
        raise RuntimeError('赠礼库存读数无效')
    for evidence in [inventory_before_evidence, inventory_after_evidence]:
        if any(evidence.get(k) != prior.get(k) for k in ['pid', 'process_start_ticks']) or (
            evidence.get('source') != 'BackpackMgr.Model.BackpackData.ItemVoDic'
            or evidence.get('read_only') is not True
            or batch['base_id'] not in evidence.get('requested_item_ids', [])
        ):
            raise RuntimeError('赠礼库存来源与人物进程或目标物品不符')
        observed, completed = evidence.get('observed_at'), evidence.get('completed_at')
        if not isinstance(observed, (int, float)) or not isinstance(completed, (int, float)) or not 0 < observed <= completed:
            raise RuntimeError('赠礼库存观察时间无效')
    if inventory_after_evidence['observed_at'] <= inventory_before_evidence['completed_at']:
        raise RuntimeError('赠礼库存观察没有按操作顺序刷新')
    matches = []
    for snapshot in [before, after]:
        people = [p for p in snapshot.get('people', []) if p.get('npc_id') == batch['npc_id']
                  and p.get('runtime_index') == batch['runtime_index']]
        if len(people) != 1:
            raise RuntimeError('赠礼前后原生人物身份不符')
        matches.append(people[0])
    track = batch['progress_track']
    if int(matches[0].get(track, -1)) != batch['favor_before'] or int(matches[1].get(track, -1)) != batch['expected_favor_after']:
        raise RuntimeError('赠礼实际好感与当前批次预期不符，禁止重放')
    if int(inventory_before) - int(inventory_after) != batch['quantity']:
        raise RuntimeError('赠礼实际库存消耗与提交数量不符，禁止重放')
    return {'verified': True, 'npc_id': batch['npc_id'], 'base_id': batch['base_id'],
            'quantity': batch['quantity'], 'favor_delta': batch['expected_favor_after'] - batch['favor_before'],
            'progress_track': track, 'pid': prior['pid'], 'process_start_ticks': prior['process_start_ticks']}


def execute_xianyuan_auto_gift_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    storage_reader: SnapshotReader = read_selected_storage_gifts_runtime,
    xianyuan_reader: SnapshotReader = read_xianyuan_atlas_runtime,
    gui_adapter: GiftGuiAdapter | None = None,
):
    """Execute selected ordinary gifts through the same bounded, verified adapter.

    Remaining inventory and reset cycles stay explicitly partial. Injected readers
    and adapters retain the offline contract without opening a real game page.
    """
    if storage_reader is read_selected_storage_gifts_runtime:
        # Admit the whole observation/navigation batch before touching the game.
        # The adapter's later guards protect each gift, but cannot reserve time
        # already spent opening the bag and reading both Runtime projections.
        from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_scheduler_batch_admission
        from .storage_bag_auto_claim_policy import SELECTED_RESOURCE_JOB_ORDER
        admission = read_scheduler_batch_admission(
            task_id=payload.get('__scheduler_task_id') or STANDARD_JOB_ID,
            batch_seconds=600, release_seconds=120, protect_all=True,
            ordered_task_ids=SELECTED_RESOURCE_JOB_ORDER,
        )
        if not admission.get('admitted'):
            return {'ok': True, 'outcome': 'partial',
                    'message': '仙缘_自动送礼：本次未执行，让出到期作业，待继续验收',
                    'reason': '观察与导航批次让出到期作业运行权',
                    'action_result': {'ok': True, 'verified': True, 'outcome': 'partial',
                                      'actions': [], 'yield_for_due_job': admission}}
        context = runner._behavior_tree_context(ctx, ctx.get('asset_tree_path'), stop_event=stop_event)
        from .xianyuan_gift_gui import (
            leave_selected_npc_without_gifting, recover_current_npc_reset_confirmation,
        )
        yield from recover_current_npc_reset_confirmation(context,
            task_id=payload.get('__scheduler_task_id') or STANDARD_JOB_ID)
        yield from leave_selected_npc_without_gifting(context)
        yield from context.wait_click(34, '右侧菜单/储物袋')
        yield from context.wait_scene([525], wait=15)
        storage = storage_reader()
        yield from context.wait_click(525, '返回')
        yield from context.wait_scene([34], wait=15)
    else:
        storage = storage_reader()
    routes = routed_npc_gift_items(storage)
    if not routes:
        return {
            "ok": True,
            "outcome": "complete",
            "reason": "没有已勾选且路由为 NPC 礼物的现有库存",
            "message": "仙缘_自动送礼：当前勾选礼物库存为空，无需赠礼",
            "plan": {"task_type": TASK_TYPE, "source_route": NPC_GIFT_EXTERNAL_ROUTE, "item_count": 0, "items": []},
        }
    from .xianyuan_gift_gui import SUPPORTED_GIFTS, execute_xianyuan_selected_gift_adapter
    if xianyuan_reader is read_xianyuan_atlas_runtime and all(r.base_id in SUPPORTED_GIFTS for r in routes):
        snapshot = xianyuan_reader(npc_ids={SUPPORTED_GIFTS[r.base_id] for r in routes})
    else:
        snapshot = xianyuan_reader()
    plan = build_xianyuan_auto_gift_plan(routes, snapshot)
    if gui_adapter is None:
        gui_adapter = execute_xianyuan_selected_gift_adapter
    result = yield from gui_adapter(runner, ctx, payload, stop_event, plan)
    if not isinstance(result, dict) or result.get("ok") is not True or result.get("verified") is not True:
        raise RuntimeError("仙缘_自动送礼：GUI adapter 未返回经过 Runtime 复验的成功终态")
    outcome = result.get('outcome')
    if outcome not in {'complete', 'partial'}:
        raise RuntimeError('仙缘_自动送礼：GUI adapter 缺少明确的完成范围')
    # Verified consumption can still leave stock or yield to a due Job.
    # Preserve its business scope instead of inferring all gifts were used.
    return {"ok": True, "outcome": outcome, "plan": plan, "action_result": result,
            "message": ('仙缘_自动送礼：部分执行，待继续验收' if outcome == 'partial'
                        else '仙缘_自动送礼：当前勾选礼物已全部验收完成')}


__all__ = [
    "STANDARD_JOB_ID",
    "TASK_TYPE",
    "build_xianyuan_auto_gift_plan",
    "plan_xianyuan_gift_batch",
    "verify_xianyuan_gift_effect",
    "xianyuan_auto_gift_next_time",
    "execute_xianyuan_auto_gift_task",
    "read_selected_storage_gifts_runtime",
    "routed_npc_gift_items",
]
