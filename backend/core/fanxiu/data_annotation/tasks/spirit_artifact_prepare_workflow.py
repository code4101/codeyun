"""初始→预备的市场编排：已有本体优先、按缺口采购、装配、逐颗升6、同步。

仅明确稳定市场来源、1–4部位。采购6件/729不继承/逐阶组件已有真实现场，
此整合入口首跑尚待验收；不扩大到箱子、5/6、洗炼或突破。
"""
from __future__ import annotations

from dataclasses import replace
import json
import time
from pathlib import Path

from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
from .spirit_artifact_equip_action import equip_owned_spirit_artifact_raw
from .spirit_artifact_prepare_action import prepare_owned_spirit_artifact_to_six
from .spirit_artifact_purchase import SpiritArtifactPurchaseAssets, purchase_spirit_artifact_body
from .spirit_artifact_reset_workflow import begin_reset_source_attempt, record_reset_source_failure
from ...instrumentation.spirit_artifact_collector import collect_spirit_artifact_snapshot_once
from ...instrumentation.spirit_artifact_equipped import read_spirit_artifact_owned_runtime
from ...instrumentation.spirit_artifact_wash_observation import SpiritArtifactWashTarget
from ...catalog.inventory_models import FanxiuSpiritArtifactHallSnapshot


def prepare_spirit_artifact_from_market(
    context, execute, *, request, artifact_name: str, evidence_dir: Path,
    stop_at: float, assets=None,
) -> dict:
    """request明确稳定市场目标及最多采购数量/总费用；实际只购买到6的缺口。

    此调用授权使用目标同base现有红raw1，旧非红仍留库；非raw备件不计阶数。
    evidence_dir须跨重入稳定。优先fresh完整库存，已有到货不会重购；不足且
    上次确认未知则复用来源intent门禁阻塞。真实完整全馆同步先于阶段判定。
    已装备红>=6且未突破直接同步幂等返回，不再购买/更换/升阶。
    """
    if request.part not in range(1, 5) or stop_at <= time.time():
        raise ValueError('当前仅开放1–4部位初始培养，需有效运行窗口')
    purchase_assets = assets or SpiritArtifactPurchaseAssets()
    if purchase_assets.shop_scene != 724:
        raise ValueError('仅支持已核实珍宝阁724稳定来源')
    root = Path(evidence_dir)
    root.mkdir(parents=True, exist_ok=True)
    key = f'{request.ware_id}-{request.part}-{request.base_id}'
    events = root / f'{key}-prepare-workflow-events.jsonl'

    def record(event, **fields):
        with events.open('a', encoding='utf-8') as out:
            out.write(json.dumps(dict(event=event, at=time.time(), **fields),
                                 ensure_ascii=False, default=str) + '\n')

    def sync():
        record('hall_start')
        hall = collect_spirit_artifact_snapshot_once()
        if hall.get('runtime_complete') is not True:
            raise RuntimeError('初始培养要求完整fresh全馆事实')
        record('hall_done')
        return hall

    hall = sync()
    live = [r for a in FanxiuSpiritArtifactHallSnapshot.model_validate(hall).artifacts
            for r in a.rows if (r.runtime_ware_id, r.runtime_part) == (request.ware_id, request.part)]
    if len(live) != 1 or live[0].stage == '待识别':
        raise RuntimeError('全馆无法唯一确认目标阶段')
    live = live[0]
    identity = None

    def facts():
        nonlocal identity
        record('owned_start')
        state = read_spirit_artifact_owned_runtime([request.ware_id])
        inv, eq = state['inventory'], state['equipped']
        current_identity = (inv['pid'], inv['process_start_ticks'])
        if (inv.get('complete') is not True or (eq['pid'], eq['process_start_ticks']) != current_identity
                or identity is not None and identity != current_identity):
            raise RuntimeError('完整库存/装备进程证据不一致')
        identity = current_identity
        slots = [r for r in eq['items'] if r['ware_id'] == request.ware_id and r['part'] == request.part]
        if len(slots) != 1:
            raise RuntimeError('此入口要求唯一现有装备；完全空部位另行验收')
        rows = {r['item_id']: r for r in inv['items']}
        current = rows.get(slots[0]['item_id'])
        if current is None or (current['ware_id'], current['part']) != (request.ware_id, request.part):
            raise RuntimeError('装备本体库存身份不符')
        raws = {uid for uid, r in rows.items() if r['base_id'] == request.base_id
                and (r['ware_id'], r['part']) == (request.ware_id, request.part)
                and r['quality'] == 6 and r['grade'] == 1 and r['quantity'] == 1
                and r['is_break'] is False and uid != current['item_id']}
        record('owned_done', current=current, raw_item_ids=sorted(raws))
        return rows, current, raws

    rows, current, raws = facts()
    hall_obs = live.runtime_observation or hall.get('runtime_debug', {})
    if (live.runtime_item_id != current['item_id'] or
            (hall_obs.get('pid'), hall_obs.get('process_start_ticks')) != identity):
        raise RuntimeError('全馆阶段与当前库存/进程改变，停止旧阶段决策')
    red = current['quality'] == 6 and current['base_id'] == request.base_id
    already_prepared = red and current['grade'] >= 6 and current['is_break'] is False
    if not already_prepared and (live.stage != '初始' or red and current['is_break'] is not False):
        raise RuntimeError('目标不在初始阶段，禁止按此流程采购或培养')
    if current['quality'] == 6 and not red:
        raise RuntimeError('当前红色base与明确市场目标冲突')
    deficit = max(0, 6 - (current['grade'] if red else 0) - len(raws))
    if deficit > request.quantity or deficit * request.unit_price > request.currency_limit:
        raise ValueError('到6缺口超出明确采购数量或费用授权')

    def wait(*scenes):
        match = execute(context.wait_scene(list(scenes), wait=12))
        if match.scene_id not in scenes:
            raise RuntimeError(f'初始市场导航要求{scenes}，实际#{match.scene_id}')
        return match.scene_id

    def move(scene, shape, dest):
        if time.time() >= stop_at:
            raise RuntimeError('运行窗口截止')
        context.click_shape_center(scene, shape)
        return wait(dest)

    def cover():
        scene = wait(666, 667, 717, 668, 714, 720, 721, 34, 247, 724)
        for _ in range(12):
            if scene not in (720, 721):
                break
            context.click_shape_center(scene, '点击屏幕继续')
            scene = wait(666, 667, 717, 668, 714, 720, 721)
        else:
            raise RuntimeError('连续结果页超过收尾上限')
        if scene in (668, 714):
            scene = move(scene, '装配', 667)
        if scene == 717:
            scene = move(717, '装配', 667)
        if scene == 667:
            scene = move(667, '返回', 666)
        if scene in (247, 724):
            scene = move(scene, '返回', 34)
        if scene == 34:
            SpiritArtifactCleanseRuntimeGuiAdapter(context, execute).open_overview()
            wait(666)

    if already_prepared:
        cover()
        result = dict(status='already_prepared', item_id=current['item_id'],
                      grade=current['grade'], hall=hall, final_scene_id=666)
        record('already_prepared', item_id=current['item_id'])
        return result

    if deficit:
        intent = root / f'{key}-source-intent.json'
        attempt = begin_reset_source_attempt(intent, dict(base_id=request.base_id,
            ware_id=request.ware_id, part=request.part, quantity=deficit,
            inventory_item_ids=sorted(rows), process_identity=identity))
        started = False
        try:
            cover()
            move(666, '返回', 34)
            move(34, '仙市', 247)
            move(247, '珍宝阁', 724)
            move(724, '灵器', 724)
            started = True
            receipt = purchase_spirit_artifact_body(context, execute,
                request=replace(request, quantity=deficit), assets=purchase_assets,
                evidence_path=root / f'{key}-purchase-{attempt}.jsonl', stop_at=stop_at)
            ids = receipt.get('item_ids', [])
            if len(ids) != deficit or len(set(ids)) != deficit:
                raise RuntimeError('采购未返回缺口数量的唯一UID')
        except Exception as exc:
            if not started:
                exc.purchase_confirmation_may_have_been_sent = False
            record_reset_source_failure(intent, attempt, exc)
            raise
        rows, latest, raws = facts()
        if latest != current or not set(ids) <= raws:
            raise RuntimeError('采购后旧装备改变或到账本体不符合raw条件')
    if (current['grade'] if red else 0) + len(raws) < 6:
        raise RuntimeError('真实库存仍不足6阶，禁止继续采购重试')
    cover()
    if not red:
        representative = sorted(raws)[0]
        target = SpiritArtifactWashTarget(representative, request.ware_id, request.part, identity, request.base_id)
        equipped = equip_owned_spirit_artifact_raw(context, execute, target=target,
            authorized_raw_ids=raws, expected_previous_item_id=current['item_id'],
            artifact_name=artifact_name, evidence_path=root / f'{key}-equip.jsonl', stop_at=stop_at)
        target = replace(target, item_id=equipped['item_id'])
        raws -= {target.item_id}
    else:
        target = SpiritArtifactWashTarget(current['item_id'], request.ware_id, request.part, identity, request.base_id)
    result = prepare_owned_spirit_artifact_to_six(context, execute, target=target,
        authorized_raw_ids=raws, artifact_name=artifact_name,
        evidence_path=root / f'{key}-grade.jsonl', stop_at=stop_at)
    result['hall'] = sync()
    (root / f'{key}-result.json').write_text(json.dumps(result, ensure_ascii=False, default=str), encoding='utf-8')
    return result
