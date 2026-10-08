"""Bounded GUI gift batches for selected ordinary NPC gifts, with atomic effects."""
import time
from backend.core.fanxiu.activity.xianyuan_gift_receipts import (
    begin_xianyuan_gift_action, read_xianyuan_gift_actions, settle_xianyuan_gift_action,
)
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.xianyuan_atlas import read_xianyuan_atlas_runtime
from backend.core.fanxiu.instrumentation.xianyuan_gift_page import (
    read_xianyuan_gift_page_snapshot, require_xianyuan_gift_selection,
)
from .xianyuan_auto_gift import STANDARD_JOB_ID, plan_xianyuan_gift_batch
from .storage_bag_auto_claim_policy import SELECTED_RESOURCE_JOB_ORDER


SUPPORTED_GIFTS = {37092001: 4005, 37130904: 4013}


def remember_verified_npc_gift(progress, receipt, *, base_id, remaining_quantity):
    """Keep settled effects before later navigation; preserve unvisited stock.

    This pure accounting boundary is independently testable. Game navigation,
    gift consumption and reward acceptance require live Task evidence.
    """
    if (receipt.get('status') != 'verified' or not receipt.get('action_id')
            or base_id not in SUPPORTED_GIFTS or type(remaining_quantity) is not int
            or remaining_quantity < 0):
        raise ValueError('只有已核验的赠礼和真实剩余库存可写入进度')
    if not any(row['action_id'] == receipt['action_id'] for row in progress['actions']):
        progress['actions'].append(receipt)
    progress['remaining_items'][base_id] = remaining_quantity


class XianyuanDueJobYield(RuntimeError):
    """A non-consuming navigation boundary must yield to the next due Job."""
    def __init__(self, admission):
        super().__init__('仙缘导航让出到期作业运行权')
        self.admission = admission


def require_xianyuan_navigation_budget(*, batch_seconds=30, task_id=STANDARD_JOB_ID):
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_scheduler_batch_admission
    admission = read_scheduler_batch_admission(task_id=task_id,
        batch_seconds=batch_seconds, release_seconds=120, protect_all=True,
        ordered_task_ids=SELECTED_RESOURCE_JOB_ORDER)
    if not admission.get('admitted'):
        raise XianyuanDueJobYield(admission)


def leave_selected_npc_without_gifting(context):
    """Close NPC pages through visible non-consuming controls, then leave.

    A character challenge is never a route back to the world. The gift page's
    Return path was verified on the real client; opening it submits no gift.
    """
    from backend.core.fanxiu.behavior_tree.errors import SceneClickMismatch
    actions = {199: '送礼', 759: '返回', 920: '背景关闭',
               760: '关闭', 761: '查看礼物', 762: '关闭',
               198: '返回', 197: '返回'}
    host_scenes = {34, 69, 85, 185, 768, 86, 304, 678, 630}
    for _ in range(10):
        scene = yield from context.wait_scene(sorted(set(actions) | host_scenes | {47}), wait=15)
        scene_id = int(scene)
        if scene_id == 34:
            return
        if scene_id == 47:
            text = context.ocr_text_in_shapes(47, ['提示正文'], frame_data_url=scene.frame_data_url)
            if ('重置' not in text or '礼物' not in text
                    or context.find_ocr_text(47, '取消', in_shapes=['取消区域'],
                        frame_data_url=scene.frame_data_url, match_mode='exact') is None):
                raise RuntimeError('仙缘离场提示未证明可取消的回礼重置语义')
            context.click_ocr_text(47, '取消', in_shapes=['取消区域'],
                frame_data_url=scene.frame_data_url, match_mode='exact')
            yield from context.wait_action_settle(1)
            continue
        if scene_id in actions:
            try:
                yield from context.wait_click(scene_id, actions[scene_id], timeout=8)
            except SceneClickMismatch as exc:
                # A return animation may finish between observation and the
                # click guard. The guard submitted no click; re-observe only
                # a known safe page rather than replaying the old control.
                if exc.actual_scene_id not in set(actions) | host_scenes:
                    raise
                continue
            yield from context.wait_action_settle(1)
        elif scene_id in host_scenes:
            yield from context.go_scene(34)
            return
        else:
            raise RuntimeError('仙缘离场未识别到已验证的非消费出口')
    raise RuntimeError('仙缘非消费离场超过已验证页面边界')


def open_selected_npc_detail(context, *, name, task_id=STANDARD_JOB_ID):
    """Navigate to the visible exact NPC name, without gifting or resetting."""
    require_xianyuan_navigation_budget(task_id=task_id)
    # A reset opens the same detail again. Reuse only a freshly identified
    # exact person, never a saved GUI cursor from an earlier attempt.
    current = yield from context.wait_scene([34, 69, 85, 86, 185, 197, 198,
        199, 304, 630, 678, 759, 760, 761, 762, 768, 920], wait=15)
    if int(current) == 198:
        title = context.ocr_text_in_shapes(198, ['人物名称'],
            frame_data_url=current.frame_data_url)
        if name in ''.join(title.split()):
            return
    yield from leave_selected_npc_without_gifting(context)
    # The generic graph also contains seasonal banquet routes.  Gifting uses
    # the permanent daily entry even when its challenge quota is completed.
    require_xianyuan_navigation_budget(task_id=task_id)
    yield from context.enter_daily_list_direct(label='仙缘赠礼入口')
    yield from context.open_daily_entry(
        label='仙缘赠礼入口', title_pattern=r'挑战\s*仙缘',
        exclude_pattern=r'斗法', progress_can_mark_done=False,
    )
    yield from context.wait_scene([197], wait=20)
    found = False
    for direction in ('up', 'down'):
        for _ in range(32):
            require_xianyuan_navigation_budget(task_id=task_id)
            scene = yield from context.wait_scene([197], wait=15)
            match = context.find_ocr_text(197, name, in_shapes=['人物列表'],
                frame_data_url=scene.frame_data_url, match_mode='exact')
            if match is not None:
                context.click_ocr_text(197, name, in_shapes=['人物列表'],
                    frame_data_url=scene.frame_data_url, match_mode='exact')
                found = True
                break
            if not (yield from context.scroll_shape_content(197, '人物列表', direction=direction)):
                break
        if found:
            break
    if not found:
        raise RuntimeError('仙缘列表未精确找到' + name)
    yield from context.wait_scene([198], wait=15)
    # The detail shell may precede its new portrait/name. A freshly loaded
    # correct name was observed after the former single-frame rejection.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        title = context.ocr_text_in_shapes(198, ['人物名称'],
            frame_data_url=context.cur_frame(update=True))
        if name in ''.join(title.split()):
            return
        yield from context.wait_action_settle(1)
    raise RuntimeError('仙缘详情人物与计划不符')


def open_selected_npc_gift_page(context, *, name, npc_id, task_id=STANDARD_JOB_ID):
    """Open the gift page and prove its native recipient after name navigation."""
    from backend.core.fanxiu.instrumentation.xianyuan_gift_page import read_xianyuan_gift_bag_snapshot
    current = yield from context.wait_scene([34, 69, 85, 86, 185, 197, 198,
        199, 304, 630, 678, 759, 760, 761, 762, 768, 920], wait=15)
    reuse_bag = False
    if int(current) == 762:
        bag = read_xianyuan_gift_bag_snapshot()
        reuse_bag = bag.get('complete') and bag.get('npc_id') == npc_id
    if reuse_bag:
        # Native NpcBagsPanel.GiftBtnClick closes the bag and goes to the
        # same NPC. It submits no gift; selection is checked later on #759.
        require_xianyuan_navigation_budget(task_id=task_id)
        deadline = time.monotonic() + 25
        while True:
            frame = context.cur_frame(update=True)
            if context.find_ocr_text(762, '前往送礼', in_shapes=['一键领取'],
                    frame_data_url=frame, match_mode='exact') is not None:
                context.click_ocr_text(762, '前往送礼', in_shapes=['一键领取'],
                    frame_data_url=frame, match_mode='exact')
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('当前人物储物袋未出现前往送礼入口')
            yield from context.wait_action_settle(1)
    else:
        yield from open_selected_npc_detail(context, name=name, task_id=task_id)
        yield from context.wait_click(198, '前往')
    yield from context.wait_scene([199], wait=75)
    yield from context.wait_click(199, '送礼')
    yield from context.wait_scene([759], wait=15)
    page = read_xianyuan_gift_page_snapshot()
    if not page.get('complete') or page.get('npc_id') != npc_id:
        raise RuntimeError('赠礼页面原生人物与计划不符')
    page['navigation_path'] = 'current_npc_bag' if reuse_bag else 'npc_detail'
    return page


def select_single_npc_gift(context, batch, *, page):
    """Only the sole available ordinary gift may use the annotated first slot."""
    from backend.core.fanxiu.runtime_gui.integer_count_control import IntegerSliderAssets, set_verified_integer_slider_count
    available = [item for item in page['available_items'] if item['quantity'] > 0]
    if page.get('selected_items') or len(available) != 1 or available[0]['base_id'] != batch['base_id']:
        raise RuntimeError('赠礼首格尚未唯一绑定指定物品；禁止全选或猜测礼品')
    yield from context.wait_click(759, '礼品首格')
    yield from context.wait_scene([920, 759], wait=15)
    selected = read_xianyuan_gift_page_snapshot()
    modal = selected.get('quantity_modal')
    if modal is not None:
        if modal['base_id'] != batch['base_id'] or modal['favor_per_item'] != batch['favor_per_item']:
            raise RuntimeError('赠礼数量弹窗物品、收益或容量不符')
        # Native useMax is the current reward-cycle capacity, capped by stock.
        # The owner's rule is to fill that capacity on every submission.
        batch.update(quantity=modal['maximum'],
            native_quantity_maximum=modal['maximum'],
            native_inventory_quantity=modal['inventory_quantity'],
            native_capacity_observed_at=selected['captured_at'],
            planned_favor_gain=modal['maximum'] * batch['favor_per_item'],
            expected_favor_after=min(batch['track_cap'],
                batch['favor_before'] + modal['maximum'] * batch['favor_per_item']))
        assets = IntegerSliderAssets(settings_scene_id=920, count_region='数量', count_decrease='减少',
            count_increase='增加', count_slider_thumb=None, count_slider_track='滑轨')
        def count():
            try:
                values, _ = context.ocr_numbers_in_shapes(920, ['数量'], crop=True,
                    frame_data_url=context.cur_frame(update=True), max_attempts=1, retry_interval=0)
                numbers = {int(value) for value in values if 1 <= int(value) <= modal['maximum']}
                if len(numbers) == 1:
                    return numbers.pop()
            except (RuntimeError, ValueError):
                pass
            observed = read_xianyuan_gift_page_snapshot()
            current = observed.get('quantity_modal')
            if not current or current['base_id'] != batch['base_id']:
                raise RuntimeError('赠礼调整期间数量弹窗身份变化')
            return current['quantity']
        yield from set_verified_integer_slider_count(context, assets, batch['quantity'],
            maximum=modal['maximum'], initial_count=modal['quantity'], runtime_count_reader=count,
            max_adjustments=30, count_label='指定仙缘礼物数量')
        checked = read_xianyuan_gift_page_snapshot().get('quantity_modal')
        if not checked or checked['base_id'] != batch['base_id'] or checked['quantity'] != batch['quantity']:
            raise RuntimeError('赠礼数量未形成最终运行凭证')
        yield from context.wait_click(920, '使用')
        yield from context.wait_scene([759], wait=15)
    return read_xianyuan_gift_page_snapshot()


def selected_npc_reward_completion_reader(before, observed):
    """Bind reward completion to the exact NPC, process and eligible native keys.

    The bag's bottom button is gift/reset navigation, not a receipt. Cache the
    successful native observation so the caller need not read the atlas twice.
    """
    person = before['people'][0]
    expected = {row['reward_key'] for row in person['rewards'] if row['state'] == 1}
    def read():
        after = read_xianyuan_atlas_runtime(npc_ids=[person['npc_id']])
        if (not after.get('runtime_complete') or len(after.get('people', [])) != 1
                or any(before['runtime_debug'].get(key) != after['runtime_debug'].get(key)
                       for key in ('pid', 'process_start_ticks'))):
            raise RuntimeError('回礼领取验收的人物或原生进程变化')
        current = after['people'][0]
        received = {row['reward_key'] for row in current['rewards'] if row['state'] == 2}
        if current['npc_id'] != person['npc_id']:
            raise RuntimeError('回礼领取验收的人物变化')
        if expected <= received and not any(row['state'] == 1 for row in current['rewards']):
            observed['after'] = after
            return True
        return False
    return read


def claim_selected_npc_rewards(context, *, target, before, task_id=STANDARD_JOB_ID):
    """Recover a previous gift's return rewards before planning another batch."""
    from .theme_npc_gift import ThemeNpcGiftSpec, claim_npc_return_gifts
    from backend.core.fanxiu.instrumentation.xianyuan_gift_page import read_xianyuan_gift_bag_snapshot
    require_xianyuan_navigation_budget(batch_seconds=180, task_id=task_id)
    yield from open_selected_npc_detail(context, name=target['name'], task_id=task_id)
    yield from context.wait_click(198, '储物袋页签')
    yield from context.wait_scene([762], wait=15)
    require_xianyuan_navigation_budget(batch_seconds=120, task_id=task_id)
    bag = read_xianyuan_gift_bag_snapshot()
    if not bag.get('complete') or bag.get('npc_id') != target['npc_id']:
        raise RuntimeError('领取回礼前人物储物袋身份不符')
    spec = ThemeNpcGiftSpec(target['name'], target['gift_item_id'],
        str(target['gift_item_id']), 197, '人物列表')
    observed = {}
    yield from claim_npc_return_gifts(context, spec,
        completion_reader=selected_npc_reward_completion_reader(before, observed))
    after = observed['after']
    if not after.get('runtime_complete') or len(after.get('people', [])) != 1:
        raise RuntimeError('回礼恢复后人物事实不完整')
    expected = {row['reward_key'] for row in before['people'][0]['rewards'] if row['state'] == 1}
    received = {row['reward_key'] for row in after['people'][0]['rewards'] if row['state'] == 2}
    if not expected or not expected <= received:
        raise RuntimeError('回礼恢复未形成原生领取凭证')
    from backend.core.fanxiu.activity.xianyuan_gift_receipts import recovered_npc_reward_receipt
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
    receipt = recovered_npc_reward_receipt(read_xianyuan_gift_actions(), before, after)
    if receipt is not None:
        record_world_discovery('xianyuan-rewards:' + receipt['action_id'], receipt)
    return after


def reset_selected_npc_rewards(context, *, target, before, task_id=STANDARD_JOB_ID):
    """Reset one fully claimed, free native reward stage and prove fresh progress.

    Exact visible OCR locates controls; no assumed reset/confirmation point is
    used. An uncertain result stops, and a new attempt replans native states.
    """
    import time
    from backend.core.fanxiu.instrumentation.xianyuan_gift_page import (
        read_xianyuan_gift_bag_snapshot, require_free_single_step_npc_reset,
    )
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
    person = before['people'][0]
    require_xianyuan_navigation_budget(task_id=task_id)
    scene = yield from context.wait_scene([34, 69, 85, 86, 185, 197, 198,
        199, 304, 630, 678, 759, 760, 761, 762, 768, 920], wait=15)
    reuse_bag = False
    if int(scene) == 762:
        current_bag = read_xianyuan_gift_bag_snapshot()
        reuse_bag = current_bag.get('complete') and current_bag.get('npc_id') == target['npc_id']
    if not reuse_bag:
        yield from open_selected_npc_detail(context, name=target['name'], task_id=task_id)
        yield from context.wait_click(198, '储物袋页签')
        scene = yield from context.wait_scene([762], wait=15)
    deadline = time.monotonic() + 25
    labels = []
    while time.monotonic() < deadline:
        # The shell loads before the native reward list and reset button.
        # Observe fresh frames rather than classifying its first empty frame.
        frame = context.cur_frame(update=True)
        labels = [label for label in ('重置礼物', '重置回礼') if context.find_ocr_text(
            762, label, in_shapes=['一键领取'], frame_data_url=frame, match_mode='exact') is not None]
        # Exact OCR can still match a token subsequence: “重置” belongs to
        # “重置礼物”, so the short fallback is not a second button.
        if not labels and context.find_ocr_text(762, '重置', in_shapes=['一键领取'],
                frame_data_url=frame, match_mode='exact') is not None:
            labels = ['重置']
        if labels:
            break
        yield from context.wait_action_settle(1)
    if len(labels) != 1:
        raise RuntimeError('人物回礼重置按钮未唯一识别')
    bag = read_xianyuan_gift_bag_snapshot()
    require_free_single_step_npc_reset(bag, person, expected_process=before['runtime_debug'])
    require_xianyuan_navigation_budget(batch_seconds=120, task_id=task_id)
    context.click_ocr_text(762, labels[0], in_shapes=['一键领取'],
        frame_data_url=context.cur_frame(update=True), match_mode='exact')
    # One-step reset may execute directly when the native confirmation preference
    # is already saved; otherwise only an actual reset confirmation is accepted.
    yield from context.wait_action_settle(1)
    # The old bag stays visible while the confirmation is being constructed.
    # Accepting it immediately races the modal; observe the explicit prompt
    # first. A saved native preference can still take the direct reset path,
    # whose clearing receipt is verified below after this bounded wait.
    modal = yield from context.wait_scene([47], wait=8, required=False)
    result = yield from complete_selected_npc_reset(context, target=target,
        before=before, modal=modal, task_id=task_id)
    return {**result, 'reset_entry_from_current_bag': bool(reuse_bag)}


def complete_selected_npc_reset(context, *, target, before, modal, task_id=STANDARD_JOB_ID):
    """Validate a current free reset confirmation and its native clearing receipt.

    A new attempt may observe the already-open confirmation; it proves the
    current dialog, NPC and cost afresh instead of reopening the reset button.
    """
    import time
    from backend.core.fanxiu.instrumentation.xianyuan_gift_page import (
        read_xianyuan_gift_bag_snapshot, require_free_single_step_npc_reset,
    )
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
    person = before['people'][0]
    if modal is not None and int(modal) == 47:
        modal = yield from context.wait_scene([47], wait=5)
        text = context.ocr_text_in_shapes(47, ['提示正文'], frame_data_url=modal.frame_data_url)
        if '重置' not in text:
            raise RuntimeError('人物回礼确认窗口未证明重置语义')
        confirms = [label for label in ('确定', '确认') if context.find_ocr_text(
            47, label, in_shapes=['确认区域'], frame_data_url=modal.frame_data_url, match_mode='exact') is not None]
        if len(confirms) != 1:
            raise RuntimeError('人物回礼重置确认按钮未唯一识别')
        fresh = read_xianyuan_gift_bag_snapshot()
        require_free_single_step_npc_reset(fresh, person, expected_process=before['runtime_debug'])
        require_xianyuan_navigation_budget(batch_seconds=120, task_id=task_id)
        context.click_ocr_text(47, confirms[0], in_shapes=['确认区域'],
            frame_data_url=context.cur_frame(update=True), match_mode='exact')
    yield from context.wait_scene([762], wait=15)
    after = read_xianyuan_atlas_runtime(npc_ids=[target['npc_id']])
    if not after.get('runtime_complete') or len(after.get('people', [])) != 1:
        raise RuntimeError('人物回礼重置后事实不完整')
    current = after['people'][0]
    if (any(before['runtime_debug'].get(k) != after['runtime_debug'].get(k)
            for k in ('pid', 'process_start_ticks'))
            or current.get('npc_id') != person['npc_id']
            or current.get('favor') != person['favor']
            or current.get('favor_level') != person['favor_level']
            or current.get('reset_favor') != 0 or current.get('reset_favor_level') != 0
            or not current.get('rewards') or any(r['state'] != 0 for r in current['rewards'])):
        raise RuntimeError('人物回礼重置未形成原生进度清零凭证')
    record_world_discovery(f'xianyuan-reset:{target["npc_id"]}:{time.time_ns()}',
        {'npc_id': target['npc_id'], 'reset_cost_quantity': 0, 'before': before, 'after': after})
    return after



def recover_current_npc_reset_confirmation(context, *, task_id=STANDARD_JOB_ID):
    """Recover only the supported NPC's current, explicitly free reset dialog."""
    from backend.core.fanxiu.instrumentation.xianyuan_gift_page import read_xianyuan_gift_bag_snapshot
    current = yield from context.wait_scene([47, 34, 69, 85, 86, 185, 197, 198,
        199, 304, 630, 678, 759, 760, 761, 762, 768, 920], wait=15)
    if int(current) != 47:
        return None
    text = context.ocr_text_in_shapes(47, ['提示正文'], frame_data_url=current.frame_data_url)
    if '重置' not in text or '礼物' not in text:
        raise RuntimeError('当前提示不是已支持的仙缘回礼重置确认')
    require_xianyuan_navigation_budget(batch_seconds=240, task_id=task_id)
    bag = read_xianyuan_gift_bag_snapshot()
    npc_id = bag.get('npc_id')
    base_id = next((item for item, npc in SUPPORTED_GIFTS.items() if npc == npc_id), None)
    if base_id is None:
        raise RuntimeError('当前重置确认的人物未绑定已支持礼物')
    before = read_xianyuan_atlas_runtime(npc_ids=[npc_id])
    if not before.get('runtime_complete') or len(before.get('people', [])) != 1:
        raise RuntimeError('当前重置确认的人物事实不完整')
    target = {'npc_id': npc_id, 'name': before['people'][0]['name'], 'gift_item_id': base_id}
    return (yield from complete_selected_npc_reset(context, target=target,
        before=before, modal=current, task_id=task_id))


def execute_xianyuan_selected_gift_adapter(runner, ctx, payload, stop_event, plan):
    """Yield at safe navigation boundaries without abandoning a submitted gift."""
    # Share verified facts immediately: a navigation yield may interrupt a
    # cycle after consumption but before the cycle can return its result.
    progress = {'actions': [], 'remaining_items': {
        item['base_id']: item['quantity'] for item in plan['items']}}
    try:
        for _ in range(32):
            result = yield from run_xianyuan_selected_gift_adapter(
                runner, ctx, payload, stop_event, plan, progress=progress)
            result['actions'] = list(progress['actions'])
            # Admission can return before a batch reads inventory. Preserve
            # the whole selected scope, including NPCs not visited yet.
            result.setdefault('remaining_items', [
                {'base_id': base_id, 'quantity': quantity}
                for base_id, quantity in progress['remaining_items'].items()])
            if result['outcome'] == 'complete' or result.get('yield_for_due_job') or payload.get('gift_probe_only'):
                return result
        result['bounded_cycle_limit'] = 32
        context = runner._behavior_tree_context(ctx, ctx.get('asset_tree_path'), stop_event=stop_event)
        yield from leave_selected_npc_without_gifting(context)
        return result
    except XianyuanDueJobYield as exc:
        context = runner._behavior_tree_context(ctx, ctx.get('asset_tree_path'), stop_event=stop_event)
        yield from leave_selected_npc_without_gifting(context)
        return {'ok': True, 'verified': True, 'outcome': 'partial',
                'actions': list(progress['actions']),
                'remaining_items': [
                    {'base_id': base_id, 'quantity': quantity}
                    for base_id, quantity in progress['remaining_items'].items()],
                'yield_for_due_job': exc.admission}


def run_xianyuan_selected_gift_adapter(runner, ctx, payload, stop_event, plan, *, progress=None):
    """One batch of the first remaining NPC; unresolved effects never replay.

    Remaining inventory/reset cycles produce ``partial``. The caller must not
    interpret the Cell's success as all selected gifts being consumed.
    """
    from .theme_npc_gift import ThemeNpcGiftSpec, claim_npc_return_gifts
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_scheduler_batch_admission
    task_id = str(payload.get('__scheduler_task_id') or STANDARD_JOB_ID)
    context = runner._behavior_tree_context(ctx, ctx.get('asset_tree_path'), stop_event=stop_event)
    receipts = []
    remaining = []
    if progress is None:
        progress = {'actions': [], 'remaining_items': {}}
    for item in plan['items']:
        progress['remaining_items'].setdefault(item['base_id'], item['quantity'])
    for item in plan['items']:
        cycle_started = time.perf_counter()
        reset_performed = False
        base_id = item['base_id']
        npc_id = SUPPORTED_GIFTS.get(base_id)
        if npc_id is None or len(item['eligible_targets']) != 1 or item['eligible_targets'][0]['npc_id'] != npc_id:
            raise RuntimeError('当前送礼执行器尚未实机绑定该物品/人物')
        unresolved = [row for row in read_xianyuan_gift_actions() if row['status'] == 'pending']
        if unresolved:
            original = unresolved[0]
            observed = read_xianyuan_atlas_runtime(npc_ids=[original['batch']['npc_id']])
            stock, evidence = read_backpack_item_counts([original['batch']['base_id']], manager_key='npc-gift-effects')
            recovered = settle_xianyuan_gift_action(original['action_id'], observed,
                inventory_after=stock[original['batch']['base_id']], inventory_after_evidence=evidence)
            remember_verified_npc_gift(progress, recovered,
                base_id=original['batch']['base_id'], remaining_quantity=stock[original['batch']['base_id']])
        admission = read_scheduler_batch_admission(task_id=task_id,
            batch_seconds=600, release_seconds=120, protect_all=True,
            ordered_task_ids=SELECTED_RESOURCE_JOB_ORDER)
        if not admission.get('admitted'):
            yield from leave_selected_npc_without_gifting(context)
            return {'ok': True, 'verified': True, 'outcome': 'partial', 'actions': receipts, 'yield_for_due_job': admission}
        target = item['eligible_targets'][0]
        before = read_xianyuan_atlas_runtime(npc_ids=[npc_id])
        person = before['people'][0] if before.get('runtime_complete') and len(before['people']) == 1 else None
        if person is None:
            raise RuntimeError('指定人物运行数据未唯一加载')
        counts, before_evidence = read_backpack_item_counts([base_id], manager_key='npc-gift-effects')
        if progress is not None:
            progress['remaining_items'][base_id] = counts[base_id]
        fresh_item = {**item, 'quantity': counts[base_id]}
        batch = plan_xianyuan_gift_batch(fresh_item, person)
        if batch['action'] == 'claim_rewards':
            before = yield from claim_selected_npc_rewards(context, target=target, before=before, task_id=task_id)
            counts, before_evidence = read_backpack_item_counts([base_id], manager_key='npc-gift-effects')
            if progress is not None:
                progress['remaining_items'][base_id] = counts[base_id]
            fresh_item = {**item, 'quantity': counts[base_id]}
            batch = plan_xianyuan_gift_batch(fresh_item, before['people'][0])
        if batch['action'] == 'inventory_exhausted':
            remaining.append({'base_id': base_id, 'quantity': 0})
            continue
        if batch['action'] == 'reset_required':
            before = yield from reset_selected_npc_rewards(context, target=target, before=before, task_id=task_id)
            reset_performed = True
            counts, before_evidence = read_backpack_item_counts([base_id], manager_key='npc-gift-effects')
            if progress is not None:
                progress['remaining_items'][base_id] = counts[base_id]
            fresh_item = {**item, 'quantity': counts[base_id]}
            batch = plan_xianyuan_gift_batch(fresh_item, before['people'][0])
        if batch['action'] != 'gift':
            raise RuntimeError('当前周期须先处理领取/重置或库存：' + batch['action'])
        page = yield from open_selected_npc_gift_page(context, name=target['name'], npc_id=npc_id, task_id=task_id)
        yield from select_single_npc_gift(context, batch, page=page)
        # Fresh membership/selection, after quantity confirmation and immediately
        # before the sole consuming action. Never use the preparation snapshot.
        submission = {}
        def before_click():
            require_xianyuan_navigation_budget(batch_seconds=300, task_id=task_id)
            selection = read_xianyuan_gift_page_snapshot()
            require_xianyuan_gift_selection(selection, batch, expected_process=before['runtime_debug'])
            submission['action'] = begin_xianyuan_gift_action(batch, before,
                inventory_before=counts[base_id], inventory_before_evidence=before_evidence)
        yield from context.wait_click(759, '赠送', before_click=before_click)
        action = submission['action']
        yield from context.wait_scene([760], wait=25)
        after = read_xianyuan_atlas_runtime(npc_ids=[npc_id])
        stocks, after_evidence = read_backpack_item_counts([base_id], manager_key='npc-gift-effects')
        receipt = settle_xianyuan_gift_action(action['action_id'], after,
            inventory_after=stocks[base_id], inventory_after_evidence=after_evidence)
        receipts.append(receipt)
        if progress is not None:
            remember_verified_npc_gift(progress, receipt,
                base_id=base_id, remaining_quantity=stocks[base_id])
        yield from context.wait_click(760, '关闭')
        scene = yield from context.wait_scene([761, 762], wait=15)
        if int(scene) == 761:
            yield from context.wait_click(761, '查看礼物')
        spec = ThemeNpcGiftSpec(target['name'], base_id, str(base_id), 197, '人物列表')
        observed = {}
        yield from claim_npc_return_gifts(context, spec,
            completion_reader=selected_npc_reward_completion_reader(after, observed))
        claimed = observed['after']
        if not claimed.get('runtime_complete') or len(claimed.get('people', [])) != 1:
            raise RuntimeError('回礼后指定人物事实未完整加载')
        claimable = {r['reward_key'] for r in after['people'][0]['rewards'] if r['state'] == 1}
        claimed_keys = {r['reward_key'] for r in claimed['people'][0]['rewards'] if r['state'] == 2}
        if not claimable <= claimed_keys:
            raise RuntimeError('指定人物回礼未形成原生领取凭证')
        from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
        record_world_discovery(f'xianyuan-rewards:{action["action_id"]}', {
            'action_id': action['action_id'], 'npc_id': npc_id,
            'eligible_reward_keys': sorted(claimable),
            'claimed_reward_keys': sorted(claimed_keys),
            'claimable_after': [r['reward_key'] for r in claimed['people'][0]['rewards'] if r['state'] == 1],
            'evidence': claimed['runtime_debug'],
            'cycle_elapsed_seconds': time.perf_counter() - cycle_started,
            'gift_navigation_path': page.get('navigation_path'),
            'reset_performed': reset_performed,
            'reset_entry_from_current_bag': before.get('reset_entry_from_current_bag') if reset_performed else None,
        })
        # Finish one batch per pass. The next pass prioritizes this same NPC
        # while stock remains, re-observing #762 instead of discarding it.
        quantities = [{'base_id': item_id, 'quantity': quantity}
            for item_id, quantity in progress['remaining_items'].items()]
        outcome = 'partial' if any(item['quantity'] for item in quantities) else 'complete'
        if outcome == 'complete' or payload.get('gift_probe_only'):
            yield from leave_selected_npc_without_gifting(context)
        return {'ok': True, 'verified': True, 'outcome': outcome,
                'actions': receipts, 'remaining_items': quantities}
    yield from leave_selected_npc_without_gifting(context)
    return {'ok': True, 'verified': True, 'outcome': 'partial' if any(x['quantity'] for x in remaining) else 'complete',
            'actions': receipts, 'remaining_items': remaining}
