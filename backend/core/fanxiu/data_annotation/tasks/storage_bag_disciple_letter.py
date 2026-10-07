from __future__ import annotations

"""养成集 → 储物袋使用 → 拜师函；两种道具共用培养位。

按图鉴勾选顺序拉满原生数量控件。使用后仍有该种余数即结束整组；
该种耗尽才查看下一种。满位提示是合法终态，无响应不是完成。
真实证据：2026-10-07 普通函512→502，10名弟子，满位重入未消耗。
"""

from typing import Any, Mapping

from backend.core.fanxiu.instrumentation.item_batch_use_dialog import read_item_batch_use_dialog_snapshot
from .storage_bag_random_box import (
    StorageBagRandomBoxGuiAdapter, StorageBagRandomBoxRequest,
    StorageBagRandomBoxBlocked, adjust_bounded_use_quantity,
)

DISCIPLE_LETTER_IDS = frozenset({20300001, 20300003})
DETAIL_SCENE = 934
TRAVEL_RESULT_SCENE = 935
OBTAIN_RESULT_SCENE = 936


def is_disciple_letter_entry(entry: Any) -> bool:
    return entry.template == 'special_use' and entry.base_id in DISCIPLE_LETTER_IDS


def disciple_letter_group_finished(*, before: int, used: int, remaining: int) -> bool:
    """Only a verified maximal use with overflow closes shared capacity."""
    if before <= 0 or used <= 0 or remaining < 0 or before-used != remaining:
        raise StorageBagRandomBoxBlocked('拜师函库存消耗与结果不一致')
    return remaining > 0


class StorageBagDiscipleLetterGuiAdapter:
    def __init__(self, *, context: Any, snapshot_reader: Any,
                 catalog_cards_by_id: Mapping[str, Mapping[str, Any]]):
        self.context = context
        self.snapshot_reader = snapshot_reader
        self.detail = StorageBagRandomBoxGuiAdapter(
            context=context, snapshot_reader=snapshot_reader,
            catalog_cards_by_id=catalog_cards_by_id, recorder=lambda *_a, **_k: None,
            detail_scene_id=DETAIL_SCENE, operation_label='拜师函',
            operation_template='special_use', detail_only=True,
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
        if request.base_id not in DISCIPLE_LETTER_IDS:
            raise StorageBagRandomBoxBlocked('拜师函适配器不接受其他道具')
        c = self.context
        before = self.snapshot_reader()
        owned = self._quantity(before, request)
        if owned != request.quantity or owned <= 0:
            raise StorageBagRandomBoxBlocked('拜师函库存已变化，须重新整单规划')
        yield from self.detail.open_detail(request, snapshot=before)
        yield from c.wait_click(DETAIL_SCENE, '使用', timeout=8)
        # Capture after the native response, before its transient capacity toast
        # disappears. Shape conditions use the saved frame, not guessed text.
        yield from c.wait_action_settle(0.5)
        # The toast lasts less than a global recognition pass. Capture it once
        # here, using its declared Shape, then wait for the stable landing.
        response_frame = c.cur_frame(update=True)
        full = c.shape_matches(525, '拜师函培养位已满', frame_data_url=response_frame)
        branch = 'capacity_full' if full and full.get('matched') else 'quantity'
        if branch == 'capacity_full':
            yield from c.wait_scene([525], wait=8, label='拜师函满位返回储物袋')
            after = self.snapshot_reader()
            if self._quantity(after, request) != owned:
                raise StorageBagRandomBoxBlocked('满位拒绝后拜师函数量异常变化')
            return {'verified': True, 'outcome': 'capacity_full', 'used_count': 0,
                    'remaining': owned, 'shared_capacity_finished': True,
                    'evidence': '拜师函培养位已满 Shape + 库存未消费'}
        yield from c.wait_scene([584], wait=8, label='拜师函数量面板')
        dialog = read_item_batch_use_dialog_snapshot(expected_item_id=request.base_id)
        if dialog['owned_count'] != owned:
            raise StorageBagRandomBoxBlocked('拜师函数量面板持有数与 Runtime 不一致')
        maximum = min(dialog['single_use_maximum'], dialog['slider_maximum'], owned)
        yield from adjust_bounded_use_quantity(c, maximum, maximum=dialog['slider_maximum'])
        confirmed = read_item_batch_use_dialog_snapshot(expected_item_id=request.base_id)
        if confirmed['current'] != maximum or confirmed['single_use_maximum'] != dialog['single_use_maximum']:
            raise StorageBagRandomBoxBlocked('拜师函原生上限或选择数量变化，尚未消费')
        yield from c.wait_click(584, '使用', timeout=8)
        yield from c.wait_scene([TRAVEL_RESULT_SCENE], wait=15, label='拜师函同游结果')
        yield from c.wait_click(TRAVEL_RESULT_SCENE, '确定', timeout=8)
        yield from c.wait_scene([OBTAIN_RESULT_SCENE], wait=15, label='拜师函喜纳弟子')
        yield from c.wait_click(OBTAIN_RESULT_SCENE, '点击空白处关闭', timeout=8)
        yield from c.wait_scene([525], wait=10, label='拜师函结果闭环返回储物袋')
        remaining = self._quantity(self.snapshot_reader(), request)
        finished = disciple_letter_group_finished(before=owned, used=maximum, remaining=remaining)
        return {'verified': True, 'outcome': 'capacity_filled' if finished else 'item_exhausted',
                'used_count': maximum, 'remaining': remaining,
                'shared_capacity_finished': finished,
                'evidence': '原生数量拉满 + 同游结果 + 喜纳弟子 + 实例消耗核验'}


def execute_selected_disciple_letters(context: Any):
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
    adapter = StorageBagDiscipleLetterGuiAdapter(context=context, snapshot_reader=reader, catalog_cards_by_id=cards)
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
