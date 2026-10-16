from __future__ import annotations

"""Locate the second-day Xutian Yandi decree without consuming it.

The item panel is a local overlay on #614. Full-frame OCR misses its translucent
rows, so the task reads OCR inside the annotated panel and scrolls that panel
only. The final use transaction has not been observed during an eligible window;
this task stops at a verified item row instead of claiming that a tap consumed
one. ``preview`` permits navigation outside the business window for R&D only.
"""

from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any, Iterator

from backend.core.fanxiu.activity.ranking_lifecycle import discover_ranking_occurrences
from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
from backend.core.fanxiu.data_annotation.effective_time import job_now


XUTIAN_YANDI_TASK_TYPE = "xutian_yandi_prepare"
XUTIAN_YANDI_CUTOFF = time(21, 30)
XUTIAN_YANDI_LABEL = "炎帝战令"


def xutian_yandi_window(now: datetime, schedule: dict[str, Any]) -> dict[str, Any]:
    """Admit only the census-selected day; no fixed second-day fallback."""

    occurrences = [
        occurrence
        for occurrence in discover_ranking_occurrences(schedule)
        if occurrence.activity_type == "xutian-palace"
        and occurrence.start_at <= now.astimezone(occurrence.start_at.tzinfo) < occurrence.end_at
    ]
    if len(occurrences) != 1:
        return {"eligible": False, "reason": f"当日有效虚天殿实例数={len(occurrences)}"}
    occurrence = occurrences[0]
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_world_facts
    plan = read_world_facts().get('discoveries', {}).get('xutian-yandi-day-plan:' + occurrence.instance_key)
    if (not isinstance(plan, dict) or plan.get('status') != 'completed'
            or plan.get('owner_seq') != 3023 or plan.get('instance_key') != occurrence.instance_key):
        return {'eligible': False, 'reason': '尚未取得本期首日21点人数选日计划',
                'instance_key': occurrence.instance_key}
    local_now = now.astimezone(occurrence.start_at.tzinfo)
    cutoff = datetime.combine(local_now.date(), XUTIAN_YANDI_CUTOFF, tzinfo=local_now.tzinfo)
    eligible = local_now.date().isoformat() == plan.get('selected_date') and local_now < min(cutoff, occurrence.end_at)
    return {
        "eligible": eligible,
        "reason": "窗口开放" if eligible else f"本期选日{plan.get('selected_date')}，未到选日或已过21:30截止",
        "instance_key": occurrence.instance_key,
        "cutoff": cutoff.isoformat(timespec="seconds"),
        "expected_heaven": int(plan['expected_heaven']),
    }


def _yandi_row(tokens: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return only a row whose ordered OCR tokens spell the target item."""

    lines: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for token in tokens:
        if isinstance(token, dict):
            lines[str(token.get("parent_line_id") or "")].append(token)
    matches = []
    for line in lines.values():
        ordered = sorted(line, key=lambda token: (int(token.get("order") or 0), float(token.get("x") or 0)))
        text = "".join(str(token.get("text") or "") for token in ordered)
        if XUTIAN_YANDI_LABEL not in text:
            continue
        xs = [float(token.get("x") or 0) for token in ordered]
        ys = [float(token.get("y") or 0) for token in ordered]
        rights = [float(token.get("x") or 0) + float(token.get("w") or 0) for token in ordered]
        bottoms = [float(token.get("y") or 0) + float(token.get("h") or 0) for token in ordered]
        matches.append({"text": text, "box": [min(xs), min(ys), max(rights), max(bottoms)]})
    if len(matches) > 1:
        raise RuntimeError(f"炎帝战令 OCR 行不唯一：{matches!r}")
    return matches[0] if matches else None


def execute_xutian_yandi_prepare_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: Any,
) -> Iterator[Any]:
    """Enter #614 through optional business views, then locate the decree."""

    window = xutian_yandi_window(
        job_now(),
        read_fanxiu_activity_runtime_schedule(allow_discovery=True),
    )
    preview = payload.get("preview") is True
    if not window["eligible"] and not preview:
        return {"status": "skipped", "message": window["reason"], "window": window}

    from backend.core.fanxiu.data_annotation.tasks.xutian_native_auto import (
        enter_xutian_map,
    )

    context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
    yield from context.go_scene(34)
    yield from enter_xutian_map(context)
    yield from context.wait_scene([614], wait=8.0)

    context.click_shape_center(614, "道具")
    yield from context.wait_action_settle(0.7)
    target = None
    scrolls = 0
    for scrolls in range(9):
        target = _yandi_row(context.ocr_tokens_in_shapes(614, ("道具列表",), crop=True))
        if target is not None:
            break
        if scrolls == 8 or not (yield from context.scroll_shape_content(614, "道具列表", direction="down")):
            raise RuntimeError("虚天殿道具列表未找到唯一的炎帝战令行")
    result = {
        "status": "prepared",
        "message": "炎帝战令已定位；使用与消耗确认尚待活动窗口真实验收",
        "window": window,
        "target": target,
        "scrolls": scrolls,
        "preview": preview,
    }
    if payload.get("return_world") is True:
        yield from context.go_scene(34)
        result["final_scene"] = 34
    return result


def execute_xutian_yandi_snipe_job(runner, ctx, payload, stop_event):
    """Use one Yandi decree on the native first recommendation (owner seq929).

    A durable intent precedes the tap. An uncertain attempt requires inspection,
    never blind retry. Consumption is verified independently from UI navigation;
    the resulting game page remains visible for result acceptance.
    """
    import time as clock
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
        read_world_facts, record_world_discovery)
    from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
    from backend.core.fanxiu.instrumentation.xutian_runtime import read_xutian_snipe_confirmation
    window = xutian_yandi_window(job_now(), read_fanxiu_activity_runtime_schedule(allow_discovery=True))
    if not window.get('instance_key'):
        raise RuntimeError(window['reason'])
    key = 'xutian-yandi-once:' + window['instance_key']
    previous = (read_world_facts().get('discoveries') or {}).get(key)
    if previous:
        if previous.get('outcome') == 'sniped_verified':
            return previous
        if previous.get('outcome') == 'consumed_verified':
            c = runner._behavior_tree_context(ctx, ctx.get('asset_tree_path'), stop_event=stop_event)
            from .xutian_native_auto import enter_xutian_map
            yield from enter_xutian_map(c)
            return (yield from _accept_yandi_report(c, key, previous))
        raise RuntimeError(f'已有炎帝狙击消费意图，须核验后继续：{previous}')
    # Selected-day admission governs a new irreversible action. Recovery of
    # an already consumed decree above remains valid after that window.
    if not window['eligible']:
        raise RuntimeError(window['reason'])
    if payload.get('target_policy') != 'first_recommended':
        raise ValueError('炎帝狙击须显式传入已授权 first_recommended 策略')
    yield from execute_xutian_yandi_prepare_job(runner, ctx, {}, stop_event)
    c = runner._behavior_tree_context(ctx, ctx.get('asset_tree_path'), stop_event=stop_event)
    yield from c.wait_scene([614], wait=8)
    from backend.core.fanxiu.instrumentation.xutian_runtime import read_xutian_resource_snapshot
    if read_xutian_resource_snapshot(include_auto_settings=False)['current_heaven'] != window['expected_heaven']:
        raise RuntimeError('炎帝选日对应的新地图尚未进入，拒绝消费')
    match = c.find_ocr_text(614, '炎帝战令', in_shapes=['道具列表'], crop=True)
    if not match:
        raise RuntimeError('炎帝战令当前行未核实')
    c.click_frame_point(614, *match.point())
    yield from c.wait_scene([925], wait=12)
    c.click_shape_center(925, '推荐首位狙击')
    yield from c.wait_scene([926], wait=20)
    confirmation = read_xutian_snipe_confirmation()
    for flag, shape in [('_SelectMultiState', '增益一开关'), ('_SelectMultiState2', '增益二开关')]:
        if confirmation[flag] is True:
            c.click_shape_center(926, shape)
            yield from c.wait_action_settle(.5)
    confirmation = read_xutian_snipe_confirmation()
    if (confirmation['snipe_item_id'] != 30030013 or confirmation['snipeNum'] != 1
            or any(confirmation[k] is not False for k in
                   ['_SelectMultiState','_SelectMultiState2','_SelectMultiState3'])):
        raise RuntimeError(f'炎帝单次无额外消耗门禁未满足：{confirmation}')
    ids = [30030013, *confirmation['extra_item_ids']]
    ids = [ident for ident in ids if ident is not None]
    before, evidence = read_backpack_item_counts(ids, manager_key='xutian-yandi-once')
    if before[30030013] < 1:
        raise RuntimeError('炎帝战令不足')
    intent = {'outcome':'consumption_pending', 'instance_key':window['instance_key'],
              'confirmation':confirmation, 'before':before, 'evidence':evidence}
    record_world_discovery(key, intent)
    c.click_shape_center(926, '挑战')
    deadline = clock.monotonic() + 45
    while clock.monotonic() < deadline:
        yield from c.wait_action_settle(2)
        after, after_evidence = read_backpack_item_counts(ids, manager_key='xutian-yandi-once')
        if before[30030013] - after[30030013] == 1:
            if any(after[i] != before[i] for i in ids if i != 30030013):
                raise RuntimeError(f'炎帝狙击额外道具变化须核验：{before} → {after}')
            result = {**intent, 'outcome':'consumed_verified', 'after':after,
                      'after_evidence':after_evidence, 'game_result_verified':False}
            record_world_discovery(key, result)
            return (yield from _accept_yandi_report(c, key, result))
        if after[30030013] != before[30030013]:
            raise RuntimeError(f'炎帝战令非单次变化须核验：{before} → {after}')
    raise RuntimeError('炎帝单次消费未确认；保留意图禁止重复点击')


def _accept_yandi_report(context, key, fact):
    """Accept native report text for this consumption; never consume on recovery."""
    import re
    import base64
    from backend.core.temp_paths import codeyun_temp_root
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
    match = yield from context.wait_scene([614, 927], wait=45)
    if int(match) == 614:
        context.click_shape_center(614, '战报')
    yield from context.wait_scene([927], wait=15)
    observed = datetime.fromisoformat(fact['confirmation']['captured_at'])
    # Client reports use the local minute of the completed single transaction.
    date_tokens = [f'{observed.year}年', f'{observed.month}月', f'{observed.day}日',
                   f'{observed.hour}:{observed.minute:02d}']
    # Incoming attacks prepend new reports while we inspect; under a flood
    # (several per minute) our row sat 14 pages deep minutes later. Search
    # the native list with bounded, overlapping scrolls instead of assuming
    # our row stays first; the list end stops the scan earlier.
    for page in range(60):
        tokens = context.ocr_tokens_in_shapes(927, ['战报内容'], crop=True)
        text = ''.join(str(t.get('text') or '') for t in
                       sorted(tokens, key=lambda t:(t.get('line_order',0),t.get('order',0))))
        normalized = re.sub(r'\s+', '', text)
        # The verb varies by encounter flavor (通过/凭借); the item clause is fixed.
        report = re.search(r'1个虚天[·.。・•]?炎帝战令扣除其(\d+)积分', normalized)
        if report is not None and all(token in normalized for token in date_tokens):
            break
        advancing = context.observe_scroll_content(context.shape(927, '战报内容'),
            [normalized], direction='down', reset=page == 0)
        if page == 59 or not advancing:
            raise RuntimeError(f'已消耗的炎帝战令战报尚未验收，禁止重复使用：{text}')
        # White paper dominates the pixel signature; text is the progress key.
        yield from context.scroll_shape_content(927, '战报内容', direction='down')
    path = codeyun_temp_root('fanxiu-question-evidence') / 'yandi-native-report-accepted.png'
    path.write_bytes(base64.b64decode(context.cur_frame().split(',', 1)[1]))
    result = {**fact, 'game_result_verified':True, 'report_text':text,
              'opponent_score_deducted':int(report.group(1)), 'report_evidence':str(path)}
    record_world_discovery(key, result)
    context.click_shape_center(927, '返回背景')
    yield from context.wait_scene([614], wait=12)
    yield from context.go_scene(34)
    result.update(outcome='sniped_verified', final_scene=34)
    record_world_discovery(key, result)
    return result
