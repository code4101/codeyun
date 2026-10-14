from __future__ import annotations

"""养成集 → 储物袋使用 → 拜师函；两种道具共用培养位。

按图鉴勾选顺序拉满原生数量控件。单次上限只约束当前批次，
库存有余量时继续核验原生满位提示；该种耗尽才查看下一种。
原生已同步培养位或满位提示是合法终态，无响应不是完成。
真实证据：2026-10-07 普通函512→502，10名弟子，满位重入未消耗。
"""

import time
import math
from typing import Any, Mapping

from backend.core.fanxiu.instrumentation.item_batch_use_dialog import read_item_batch_use_dialog_snapshot
from backend.core.fanxiu.instrumentation.disciple_capacity import read_disciple_capacity_snapshot
from backend.core.fanxiu.storage_bag_disciple_submissions import (
    arm_disciple_letter_submission, complete_disciple_letter_submission,
    list_pending_disciple_letter_submissions,
    mark_disciple_letter_result_observed,
)
from .storage_bag_random_box import (
    StorageBagRandomBoxGuiAdapter, StorageBagRandomBoxRequest,
    StorageBagRandomBoxBlocked, adjust_bounded_use_quantity,
    plan_current_random_box_click,
)

DISCIPLE_LETTER_IDS = frozenset({20300001, 20300003})
DETAIL_SCENE = 934
TRAVEL_RESULT_SCENE = 935
OBTAIN_RESULT_SCENE = 936
TRAVEL_DIALOGUE_SCENE = 942


def finish_existing_disciple_travel(context, *, recovery=False, result_observer=None):
    """Drain an already submitted travel, including its native plot dialogue.

    DaoLvTravelView.ShowFinish may call TalkMgr.AddTalkFromPlot before #935.
    Advancing TaskTalkView.TalkClickRang only finishes that dialogue; it never
    starts another travel. Recovery runs before navigation or inventory plans.
    Unknown dialogue variants remain fail-closed for asset review.
    """
    candidates = [TRAVEL_DIALOGUE_SCENE, TRAVEL_RESULT_SCENE, OBTAIN_RESULT_SCENE]
    if recovery:
        candidates += [34, 525]
    for _ in range(8):
        if recovery:
            scene = yield from context.wait_scene(candidates, wait=0,
                                                  label='拜师函已提交同游收尾')
        else:
            try:
                scene = yield from context.wait_scene_exact(
                    candidates, timeout=30, observation_scenes=[584, 525, DETAIL_SCENE],
                    label='拜师函已提交同游收尾',
                )
            except TimeoutError:
                # Native travel waits for both its character animation and
                # slider before opening dialogue/results. A fixed 30-second
                # observation window is not proof that the submitted use
                # failed. Extend once only for a freshly observed animation;
                # absent/stale/uninitialized state never renews the budget.
                from backend.core.fanxiu.instrumentation.disciple_travel import read_disciple_travel_animation_snapshot
                animation = read_disciple_travel_animation_snapshot()
                if animation.get('complete') is not True or animation['estimated_remaining_seconds'] <= 0:
                    raise
                scene = yield from context.wait_scene_exact(
                    candidates, timeout=30 + math.ceil(animation['estimated_remaining_seconds']),
                    observation_scenes=[584, 525, DETAIL_SCENE],
                    label='拜师函原生动画剩余时间及结果收尾',
                )
        if scene == TRAVEL_DIALOGUE_SCENE:
            yield from context.wait_click(TRAVEL_DIALOGUE_SCENE, '推进对白', timeout=8)
            yield from context.wait_action_settle(0.5)
        elif scene == TRAVEL_RESULT_SCENE:
            yield from context.wait_click(TRAVEL_RESULT_SCENE, '确定', timeout=8)
            # The result remains visible briefly after this click. Require its
            # next landing before re-observing, otherwise the loop clicks a
            # stale #935 again while #936 is already opening.
            yield from context.wait_scene_exact(
                [OBTAIN_RESULT_SCENE], timeout=20, observation_scenes=[TRAVEL_RESULT_SCENE],
                label='拜师函同游结果确认后喜纳弟子',
            )
            scene = OBTAIN_RESULT_SCENE
        if scene == OBTAIN_RESULT_SCENE:
            if result_observer is not None:
                result_observer()
            yield from context.wait_click(OBTAIN_RESULT_SCENE, '点击空白处关闭', timeout=8)
            landed = yield from context.wait_scene_exact([525, 34], timeout=10,
                                                        label='拜师函结果闭环返回')
            if landed == 34:
                yield from context.go_scene(525)
            return {'recovered': recovery, 'outcome': 'travel_closed'}
        elif scene not in [TRAVEL_DIALOGUE_SCENE, TRAVEL_RESULT_SCENE]:
            if recovery:
                return {'recovered': False, 'outcome': 'no_pending_travel'}
            raise StorageBagRandomBoxBlocked(f'拜师函已提交同游收尾落点异常：{scene}')
    raise StorageBagRandomBoxBlocked('拜师函对白推进八次仍未取得结果终态')


def is_disciple_letter_entry(entry: Any) -> bool:
    return entry.template == 'special_use' and entry.base_id in DISCIPLE_LETTER_IDS


def reconcile_pending_disciple_letters(context: Any, snapshot_reader: Any):
    """Close an existing native effect and account for it, never replay use."""
    pending = list_pending_disciple_letter_submissions()
    if not pending:
        return []
    # An armed click may never have reached the game. Absence of a travel
    # result is uncertainty, not permission to submit the saved request again.
    def record_result():
        for record in pending:
            mark_disciple_letter_result_observed(record['action_id'])
    if all(record['phase'] == 'result_observed' for record in pending):
        landing = yield from context.wait_scene_exact(
            [936, 525, 34], timeout=15, label='拜师函已观察完成结果的关闭状态',
        )
        if landing == 936:
            yield from finish_existing_disciple_travel(context, result_observer=record_result)
        elif landing == 34:
            yield from context.go_scene(525)
    else:
        yield from finish_existing_disciple_travel(context, result_observer=record_result)
    snapshot = snapshot_reader()
    if snapshot.get('complete') is not True:
        raise StorageBagRandomBoxBlocked('拜师函未结提交核账库存不完整')
    verified = []
    evidence = snapshot.get('evidence') or {}
    identity = (int(evidence.get('pid') or 0), int(evidence.get('process_start_ticks') or 0))
    for record in pending:
        rows = [row for row in snapshot['items'] if str(row.get('instance_id')) == record['instance_id']]
        if len(rows) > 1 or rows and int(rows[0].get('base_id') or 0) != record['base_id']:
            raise StorageBagRandomBoxBlocked('拜师函未结提交实例身份冲突')
        remaining = int(rows[0]['num']) if rows else 0
        verified.append(complete_disciple_letter_submission(
            record['action_id'], remaining_count=remaining, travel_closed=True, process_identity=identity,
        ))
    return verified


def disciple_letter_group_finished(*, before: int, used: int, remaining: int) -> bool:
    """Validate batch consumption; inventory overflow does not prove capacity.

    Only the native capacity-full response in the GUI adapter closes the
    shared group. A quantity dialog may impose a per-use maximum.
    """
    if before <= 0 or used <= 0 or remaining < 0 or before-used != remaining:
        raise StorageBagRandomBoxBlocked('拜师函库存消耗与结果不一致')
    return False


class StorageBagDiscipleLetterGuiAdapter:
    def __init__(self, *, context: Any, snapshot_reader: Any,
                 catalog_cards_by_id: Mapping[str, Mapping[str, Any]], log=None,
                 detail_click_planner=plan_current_random_box_click):
        self.context = context
        self.snapshot_reader = snapshot_reader
        def planner(context, snapshot, request, **kwargs):
            plan = yield from detail_click_planner(context, snapshot, request, **kwargs)
            if log:
                log({'branch': '拜师函', 'base_id': request.base_id, 'status': plan.status,
                     'target_index': plan.runtime_index, 'viewport_start': plan.viewport_runtime_start})
            return plan
        self.detail = StorageBagRandomBoxGuiAdapter(
            context=context, snapshot_reader=snapshot_reader,
            catalog_cards_by_id=catalog_cards_by_id, recorder=lambda *_a, **_k: None,
            detail_scene_id=DETAIL_SCENE, operation_label='拜师函',
            operation_template='special_use', detail_only=True,
            coarse_scrolls=False, click_planner=planner,
        )

    def _quantity(self, snapshot: Mapping[str, Any], request: StorageBagRandomBoxRequest) -> int:
        if snapshot.get('complete') is not True:
            raise StorageBagRandomBoxBlocked('拜师函库存快照不完整')
        rows = [x for x in snapshot.get('items', []) if str(x.get('instance_id')) == request.instance_id]
        if not rows:
            return 0
        if len(rows) != 1 or int(rows[0].get('base_id') or 0) != request.base_id:
            raise StorageBagRandomBoxBlocked('拜师函实例身份不唯一')
        return int(rows[0].get('num') or 0)

    def execute(self, request: StorageBagRandomBoxRequest):
        """Continue bounded batches until native capacity refusal or exhaustion."""
        yield from reconcile_pending_disciple_letters(self.context, self.snapshot_reader)
        consumed = 0
        batches = []
        current = request
        for _ in range(32):
            result = yield from self._execute_batch(current)
            consumed += result['used_count']
            batches.append(dict(result))
            if result['shared_capacity_finished'] or result['remaining'] == 0:
                return {**result, 'used_count': consumed, 'batches': batches}
            current = StorageBagRandomBoxRequest(
                base_id=request.base_id, instance_id=request.instance_id,
                name=request.name, quantity=result['remaining'],
            )
        raise StorageBagRandomBoxBlocked('拜师函已核验32批，仍有余量且未证明培养位已满')

    def _execute_batch(self, request: StorageBagRandomBoxRequest):
        if request.base_id not in DISCIPLE_LETTER_IDS:
            raise StorageBagRandomBoxBlocked('拜师函适配器不接受其他道具')
        c = self.context
        before = self.snapshot_reader()
        owned = self._quantity(before, request)
        if owned != request.quantity or owned <= 0:
            raise StorageBagRandomBoxBlocked('拜师函库存已变化，须重新整单规划')
        # Native synchronized capacity is stable evidence. At full capacity no
        # use should be submitted merely to catch a fleeting refusal toast.
        # Missing/unloaded data falls through to the existing GUI proof; it is
        # never interpreted as zero slots or as a completed branch.
        capacity = read_disciple_capacity_snapshot()
        if capacity.get('complete') is True and capacity.get('full') is True:
            return {'verified': True, 'outcome': 'shared_capacity_already_filled',
                    'used_count': 0, 'remaining': owned, 'shared_capacity_finished': True,
                    'evidence': capacity}
        yield from self.detail.open_detail(request, snapshot=before)
        yield from c.wait_click(DETAIL_SCENE, '使用', timeout=8)
        # Click submission precedes the native response. A fixed-delay frame can
        # still be #934, and the bag underneath a loading dialog can match #525.
        # Observe fresh frames until the quantity panel or the declared refusal
        # appears; neither background landing nor one toast miss proves a result.
        deadline = time.monotonic() + 15
        full = None
        landing = None
        response_frame = ''
        while True:
            response_frame = c.cur_frame(update=True)
            full = c.shape_matches(525, '拜师函培养位已满', frame_data_url=response_frame)
            if full and full.get('matched'):
                break
            landing = yield from c.wait_scene(
                [584, DETAIL_SCENE, 525], wait=0, required=False,
                label='拜师函等待原生使用响应',
            )
            if landing is not None:
                response_frame = landing.frame_data_url or response_frame
                full = c.shape_matches(525, '拜师函培养位已满', frame_data_url=response_frame)
                if (full and full.get('matched')) or landing.scene_id == 584:
                    break
            if time.monotonic() >= deadline:
                from backend.core.fanxiu.data_annotation.scene_diagnostics import save_scene_diagnostic_frame
                evidence = save_scene_diagnostic_frame(
                    c.runner, response_frame, kind='disciple_letter_response',
                    label='拜师函使用响应未确认', expected_scene_ids=[584, 525],
                    matched_scene_id=landing.scene_id if landing is not None else None,
                )
                actual = landing.scene_id if landing is not None else 'unknown'
                raise StorageBagRandomBoxBlocked(
                    f'拜师函使用响应未确认：落点 #{actual}，未见满位提示；响应帧={evidence}'
                )
            yield from c.wait_action_settle(0.1)
        if full and full.get('matched'):
            yield from c.wait_scene_exact([525], timeout=8, label='拜师函满位返回储物袋')
            after = self.snapshot_reader()
            if self._quantity(after, request) != owned:
                raise StorageBagRandomBoxBlocked('满位拒绝后拜师函数量异常变化')
            return {'verified': True, 'outcome': 'capacity_full', 'used_count': 0,
                    'remaining': owned, 'shared_capacity_finished': True,
                    'evidence': '拜师函培养位已满 Shape + 库存未消费'}
        # Only the actual quantity panel above authorizes its private Runtime.
        dialog = read_item_batch_use_dialog_snapshot(expected_item_id=request.base_id)
        if dialog['owned_count'] != owned:
            raise StorageBagRandomBoxBlocked('拜师函数量面板持有数与 Runtime 不一致')
        maximum = min(dialog['single_use_maximum'], dialog['slider_maximum'], owned)
        yield from adjust_bounded_use_quantity(c, maximum, maximum=dialog['slider_maximum'])
        confirmed = read_item_batch_use_dialog_snapshot(expected_item_id=request.base_id)
        if confirmed['current'] != maximum or confirmed['single_use_maximum'] != dialog['single_use_maximum']:
            raise StorageBagRandomBoxBlocked('拜师函原生上限或选择数量变化，尚未消费')
        submission = None
        def arm_submission():
            nonlocal submission
            submission = arm_disciple_letter_submission(
                base_id=request.base_id, instance_id=request.instance_id,
                before_count=owned, submitted_count=maximum,
                process_identity=(dialog['evidence']['pid'], dialog['evidence']['process_start_ticks']),
            )
        yield from c.wait_click(584, '使用', timeout=8, before_click=arm_submission)
        yield from finish_existing_disciple_travel(c, result_observer=lambda:
                                                mark_disciple_letter_result_observed(submission['action_id']))
        after = self.snapshot_reader()
        remaining = self._quantity(after, request)
        evidence = after.get('evidence') or {}
        complete_disciple_letter_submission(submission['action_id'], remaining_count=remaining,
                                            travel_closed=True, process_identity=(
                                                int(evidence.get('pid') or 0),
                                                int(evidence.get('process_start_ticks') or 0)))
        finished = disciple_letter_group_finished(before=owned, used=maximum, remaining=remaining)
        return {'verified': True, 'outcome': 'batch_consumed' if remaining else 'item_exhausted',
                'used_count': maximum, 'remaining': remaining,
                'shared_capacity_finished': finished,
                'evidence': '原生数量拉满 + 同游结果 + 喜纳弟子 + 实例消耗核验'}


def execute_selected_disciple_letters(context: Any, *, log=None):
    """Run the selected letter branch inside the parent's storage-use stage.

    Other selected boxes retain their existing storage operation ownership.
    No child Job or separate cadence is created for recruitment letters.
    """
    from datetime import datetime
    from sqlmodel import Session
    from backend.db import engine
    from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
    from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service
    from backend.core.fanxiu.instrumentation.storage_bag_catalog import sync_storage_bag_atlas
    from backend.core.fanxiu.storage_bag_settings import apply_storage_bag_item_settings
    from backend.core.fanxiu.storage_bag_usage import ensure_storage_bag_atlas_analysis
    from .storage_bag_auto_claim_plan import build_storage_bag_auto_claim_plan

    yield from context.go_scene(525)
    reader = fanxiu_instrumentation_service.backpack_ui_snapshot
    before = reader()
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id']
    atlas = sync_storage_bag_atlas(before, cards, captured_at=datetime.now().astimezone().isoformat())
    with Session(engine) as session:
        ensure_storage_bag_atlas_analysis(session, atlas)
        session.commit()
        selected = apply_storage_bag_item_settings(session, atlas)
    plan = build_storage_bag_auto_claim_plan(selected, before)
    failures = [entry.to_dict() for entry in plan.failures if entry.base_id in DISCIPLE_LETTER_IDS]
    if failures:
        raise StorageBagRandomBoxBlocked(f'拜师函勾选计划未就绪：{failures}')
    adapter = StorageBagDiscipleLetterGuiAdapter(context=context, snapshot_reader=reader, catalog_cards_by_id=cards, log=log)
    receipts = []
    full = False
    for entry in plan.action_queue:
        if not is_disciple_letter_entry(entry):
            continue
        if full:
            result = {'verified': True, 'outcome': 'shared_capacity_already_filled', 'used_count': 0}
        else:
            result = yield from adapter.execute(StorageBagRandomBoxRequest(
                base_id=entry.base_id, instance_id=entry.instance_id,
                name=entry.name, quantity=entry.quantity,
            ))
            full = result['shared_capacity_finished']
        receipts.append({'base_id': entry.base_id, 'name': entry.name, **result})
    if any(row['used_count'] for row in receipts):
        sync_storage_bag_atlas(reader(), cards, captured_at=datetime.now().astimezone().isoformat())
    yield from context.go_scene(34)
    return {'outcome': 'complete', 'branch': '拜师函', 'receipts': receipts}


__all__ = ['DISCIPLE_LETTER_IDS', 'StorageBagDiscipleLetterGuiAdapter',
           'is_disciple_letter_entry', 'disciple_letter_group_finished',
           'execute_selected_disciple_letters']
