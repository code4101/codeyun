from __future__ import annotations

"""Batch medicine component owned by the alchemy checkpoint in resource ranking.

#595 is the authoritative active-queue state. Never click 停止服用. Success
requires returning to #34; an interrupted exit rechecks game state on retry.
"""

from typing import Any

# Pending real item-use/selection acceptance. Acquiring this item in a box is
# not authority for the all-item AutoTakeHandler to consume it. Resolve this
# policy only after its individual use semantics have been accepted.
MEDICINE_BATCH_RESEARCH_HOLDS = frozenset({9070310})

class TakeMedicineBatchSafetyError(RuntimeError):
    """The observed game state cannot authorize the next action."""


def _wait_medicine_scene(context: Any, scenes: tuple[int, ...], timeout: float):
    match = yield from context.wait_scene(scenes, wait=timeout, label="服用丹药")
    if match is None or match.scene_id not in scenes:
        raise TakeMedicineBatchSafetyError(f"服用丹药：预期 {scenes}，实际 {match}")
    return match.scene_id


def _enter_medicine_page(context: Any, *, timeout: float):
    scene = yield from _wait_medicine_scene(context, (34, 20, 405, 408, 593, 594, 595), timeout)
    if scene == 34:
        yield from context.go_scene(20, known_paths_only=True)
        scene = yield from _wait_medicine_scene(context, (20,), timeout)
    if scene == 20:
        context.click_ocr_text(20, "修炼", in_shapes=["菜单"], match_mode="exact")
        scene = yield from _wait_medicine_scene(context, (405, 408), timeout)
    if scene in (405, 408):
        context.click_shape_center(scene, "服用丹药")
        scene = yield from _wait_medicine_scene(context, (593, 595), timeout)
    return scene


def inspect_medicine_selection_page(context: Any, *, timeout: float = 20.0):
    """Open only the choose page, observe and leave without creating a batch.

    ArtificeBatchUseView's close button also submits the batch. A preexisting
    #594 is therefore preserved, never dismissed as if it were a cancellation.
    Existing #595 is left with 返回; 停止服用 is never clicked.
    """
    scene = yield from _enter_medicine_page(context, timeout=timeout)
    if scene == 594:
        return {'complete': False, 'reason': 'unsubmitted_batch_preserved',
                'resource_consumption': 0, 'final_scene': 594}
    if scene == 595:
        from backend.core.fanxiu.instrumentation.medicine import read_medicine_queue_snapshot
        queue = read_medicine_queue_snapshot()
        context.click_shape_center(595, '返回')
        yield from _wait_medicine_scene(context, (405, 408), timeout)
        result = {'complete': False, 'reason': 'medicine_queue_running',
                  'queue_snapshot': queue}
    else:
        from backend.core.fanxiu.instrumentation.medicine import read_medicine_selection_page_snapshot
        result = read_medicine_selection_page_snapshot()
    yield from context.go_scene(34, known_paths_only=True)
    yield from _wait_medicine_scene(context, (34,), timeout)
    return {**result, 'resource_consumption': 0, 'final_scene': 34}


def run_take_medicine_batch_flow(context: Any, *, timeout: float = 20.0):
    """Observe before acting; return a completed result only after reaching #34.

    Existing #595 skips all consumption clicks. Occurrence-level persistence
    belongs to ranking checkpoints; a choose-page inspection never starts it.
    """
    scene = yield from _wait_medicine_scene(context, (34, 20, 405, 408, 593, 594, 595), timeout)
    if scene == 594:
        # Its hidden close button also submits. Neither scene identity nor an
        # earlier attempt's selection intention is current use authorization.
        raise TakeMedicineBatchSafetyError('未提交批次尚无原生清单与用途许可核验，保留 #594，禁止确认或关闭')
    scene = yield from _enter_medicine_page(context, timeout=timeout)
    if scene != 595:
        from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service as ins
        yield from context.go_scene(525, known_paths_only=True)
        bag = ins.backpack_ui_snapshot()
        if bag.get('complete') is not True:
            raise TakeMedicineBatchSafetyError('批量服药前库存未完整加载，禁止创建确认批次')
        held = [row['base_id'] for row in bag['items'] if row.get('num', 0) > 0
                and row.get('base_id') in MEDICINE_BATCH_RESEARCH_HOLDS]
        yield from context.go_scene(34, known_paths_only=True)
        if held:
            return {'status': 'blocked', 'result': 'held', 'current_scene': 34,
                    'confirmation_clicks': 0, 'held_item_ids': sorted(set(held)),
                    'message': '保留批量服药：库存含尚未通过单品服用/选单验收的研究道具'}
        scene = yield from _enter_medicine_page(context, timeout=timeout)
    confirmation_clicks = 0
    queue_snapshot = None
    if scene == 595:
        outcome = "already_running"
    else:
        if scene == 593:
            context.click_shape_center(593, "一键服用")
            # A selection page alone does not prove an empty inventory; fail
            # closed unless a dedicated confirmation is positively recognized.
            scene = yield from _wait_medicine_scene(context, (594,), timeout)
        if scene != 594:
            raise TakeMedicineBatchSafetyError(f"服用丹药：未到批量确认，实际 #{scene}")
        context.click_shape_center(594, "确认")
        confirmation_clicks = 1
        scene = yield from _wait_medicine_scene(context, (595, 405, 408), timeout)
        outcome = "started" if scene == 595 else "completed"
    if scene == 595:
        # Observe the loaded server queue before leaving its page. A failed
        # Runtime observation remains explicit; the recognized #595 still
        # forbids a second submission or stopping the existing queue.
        from backend.core.fanxiu.instrumentation.medicine import read_medicine_queue_snapshot
        queue_snapshot = read_medicine_queue_snapshot()
        context.click_shape_center(595, "返回")
        yield from _wait_medicine_scene(context, (405, 408), timeout)
    yield from context.go_scene(34, known_paths_only=True)
    yield from _wait_medicine_scene(context, (34,), timeout)
    return {
        "status": "completed", "result": outcome, "current_scene": 34,
        "confirmation_clicks": confirmation_clicks,
        "queue_snapshot": queue_snapshot,
        "message": "服用丹药：已在服用，幂等跳过并返回 #34" if outcome == "already_running"
        else "服用丹药：已确认服用并返回 #34",
    }


__all__ = ["TakeMedicineBatchSafetyError", "run_take_medicine_batch_flow", "inspect_medicine_selection_page"]
