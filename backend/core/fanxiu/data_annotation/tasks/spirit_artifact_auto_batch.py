"""原生自动洗炼的单批启动边界；随机结果和暂停场景仍由调用方观察。

不配置额外停止条件、不自动替换、不重试启动。max_material_cost 是本批
授权上限，实际设置额度必须不超过它。设置、启动与默认额度终止已实测；
无双提前暂停尚待验收。默认原生继续条件使用严格小于，上限10000、
单次400时实际在9600停止，记账必须使用库存差。
"""
from typing import Any
import time
import re
from .spirit_artifact_protected_locks import plan_spirit_artifact_protected_locks
from ...catalog.spirit_artifact_wash_rules import load_spirit_artifact_wash_rules

from ...instrumentation.spirit_artifact_auto import read_spirit_artifact_auto_snapshot
from ...instrumentation.spirit_artifact_ui import read_spirit_artifact_ui_snapshot
from backend.core.fanxiu.runtime_gui.integer_count_control import IntegerSliderAssets, set_verified_integer_slider_count, set_verified_integer_slider_range


AUTO_MATERIAL_SLIDER = IntegerSliderAssets(
    settings_scene_id=671, count_region='预算读数',
    count_decrease='预算减少', count_increase='预算增加',
    count_slider_thumb='预算滑块', count_slider_left_anchor='预算左端',
    count_slider_right_anchor='预算右端',
    count_slider_left_center_offset=-3, count_slider_right_center_offset=20,
)


def configure_spirit_artifact_auto_budget(
    context: Any, execute, *, item_id: str, budget: int,
    max_button_actions: int = 1500, minimum_budget: int | None = None,
) -> dict:
    """只改设置页额度，复用公共三阶段滑轨；不启动、不改变停止条件。

    budget 是精确目标（误差为零），max_button_actions 是 UI 精调动作预算，
    与洗灵石预算不同。每次反馈校验同一进程与目标。
    """
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        raise ValueError('洗灵石额度必须为正整数')
    if execute(context.wait_scene([671], wait=10)).scene_id != 671:
        raise RuntimeError('自动洗炼设置页未就绪')
    before = read_spirit_artifact_auto_snapshot()
    if not before.get('available') or before.get('mode') != 'settings' or before.get('item_id') != item_id:
        raise RuntimeError('自动洗炼设置目标不一致')
    if not before['per_cost'] <= budget <= before['maximum']:
        raise ValueError('额度须至少覆盖一次洗炼且不超过库存')

    def read_budget():
        current = read_spirit_artifact_auto_snapshot()
        for key in ('available', 'mode', 'item_id', 'pid', 'process_start_ticks',
                    'material_id', 'maximum', 'per_cost', 'quality_options', 'attribute_options'):
            if current.get(key) != before.get(key):
                raise RuntimeError(f'配置额度期间自动洗炼状态改变：{key}')
        return current['budget']

    def read_feedback():
        # Use the visible numerator for fast slider feedback. The denominator
        # binds it to this item's per-roll cost; Runtime remains the fallback
        # and the final identity/options/amount check before any consumption.
        text = context.ocr_text_in_shapes(
            671, ('材料预算滑轨（研发禁止）',), crop=True, padding=0,
        )
        values = re.findall(r'(\d+)\s*[/／]\s*(\d+)', text)
        if len(values) == 1:
            amount, cost = map(int, values[0])
            if cost == before['per_cost'] and 0 < amount <= before['maximum']:
                return amount
        return read_budget()

    if minimum_budget is None:
        adjustment = execute(set_verified_integer_slider_count(
            context, AUTO_MATERIAL_SLIDER, budget,
            max_adjustments=30, max_button_actions=max_button_actions,
            maximum=before['maximum'], count_label='洗灵石额度',
            runtime_count_reader=read_feedback, initial_count=before['budget'],
        ))
    else:
        if not before['per_cost'] <= minimum_budget <= budget:
            raise ValueError('洗灵额度下限无效')
        adjustment = execute(set_verified_integer_slider_range(
            context, AUTO_MATERIAL_SLIDER, minimum=minimum_budget, maximum=budget,
            control_maximum=before['maximum'], count_label='洗灵石额度',
            runtime_count_reader=read_feedback, initial_count=before['budget']))
    actual = read_budget()
    if actual != adjustment['after'] or not (minimum_budget or budget) <= actual <= budget:
        raise RuntimeError('洗灵石额度最终复核不一致')
    return {'item_id': item_id, 'before': before, 'budget': actual, 'adjustment': adjustment}


def start_spirit_artifact_auto_batch(
    context: Any, execute, *, item_id: str, max_material_cost: int,
    max_rolls: int | None = None,
    discard_non_target_candidate: bool = False,
) -> dict:
    """从设置页启动一次已配置的默认无双洗炼，返回启动前完整证据。

    调用即授权在给定上限内消耗洗灵奇石。返回只证明已发出启动点击，
    不证明运行中或目标完成；调用方须观察真实终态，不能据此重发。
    discard_non_target_candidate 显式允许继续洗掉已有非无双候选；无双候选必停。
    """
    if isinstance(max_material_cost, bool) or not isinstance(max_material_cost, int) or max_material_cost <= 0:
        raise ValueError('本批洗灵石上限必须为正整数')
    if max_rolls is not None and (isinstance(max_rolls, bool) or not isinstance(max_rolls, int) or max_rolls <= 0):
        raise ValueError('洗炼次数上限必须为正整数')
    match = execute(context.wait_scene([671], wait=10))
    if match.scene_id != 671:
        raise RuntimeError('自动洗炼设置页未就绪')
    settings = read_spirit_artifact_auto_snapshot()
    selected = read_spirit_artifact_ui_snapshot()
    if not settings.get('available') or settings.get('mode') != 'settings':
        raise RuntimeError('原生自动洗炼设置未加载')
    if settings['item_id'] != item_id or selected.get('item_id') != item_id:
        raise RuntimeError('自动洗炼目标已变化')
    if (settings['pid'], settings['process_start_ticks']) != (selected['pid'], selected['process_start_ticks']):
        raise RuntimeError('自动洗炼观察跨进程')
    cost = settings['per_cost']
    if (settings['material_id'] != 14000002 or selected.get('material_id') != 14000002
            or cost != selected.get('material_cost') or cost <= 0):
        raise RuntimeError('洗灵奇石或单次成本不一致')
    if not cost <= settings['budget'] <= min(max_material_cost, settings['maximum']):
        raise RuntimeError('实际自动洗炼额度不在本批授权范围')
    if max_rolls is not None and settings['budget'] // cost > max_rolls:
        raise RuntimeError('实际自动洗炼次数超过本批上限')
    if not selected.get('is_break'):
        raise RuntimeError('目标尚未突破')
    from ...instrumentation.spirit_artifact_affixes import enrich_spirit_artifact_effects
    enrich_spirit_artifact_effects(selected['effects'] + selected['pending_effects'],
        pid=selected['pid'], process_start_ticks=selected['process_start_ticks'])
    lock_plan = plan_spirit_artifact_protected_locks(
        selected['effects'], {e['cleanse_id']: e for e in selected['effects']},
        load_spirit_artifact_wash_rules()['wares'][selected['ware_id']]['a_codes'])
    if not lock_plan['should_wash']:
        return dict(status='skipped', reason='all_attributes_protected', consumed=0)
    if {e['cleanse_id'] for e in selected['effects'] if e['locked']} != set(lock_plan['desired_lock_ids']):
        raise RuntimeError('自动启动前须在洗炼页完成 A/S 锁定并释放非 A/S 词条')
    if any(effect.get('name') == '灵器无双' for effect in selected['effects'] + selected['pending_effects']):
        raise RuntimeError('已出现灵器无双，停止继续洗炼')
    if selected['pending_effects'] and not discard_non_target_candidate:
        raise RuntimeError('存在待处理候选，尚未授权继续洗掉非目标候选')
    if any(row.get('isSelect') for row in settings['quality_options'] + settings['attribute_options']):
        raise RuntimeError('当前存在额外停止条件，保留设置供研发确认')
    evidence = {'item_id': item_id, 'authorized_ceiling': max_material_cost,
                'settings': settings, 'selected': selected, 'started_at': time.time(),
                'status': 'start_click_sent'}
    context.click_shape_center(671, '开启自动（研发禁止）')
    return evidence
