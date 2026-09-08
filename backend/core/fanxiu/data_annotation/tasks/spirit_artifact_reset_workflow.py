"""单件错升的来源→重置→同步公共编排；不重放未决采购。

2026-09-08：3-3／4-3 单raw重置、返回666、collector同步真实通过；
市场入口已验证复用到账本体及#721结果后的仅收尾重入。
5/6部位、多raw及储物袋实际到账不据此推定通过。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Callable
from filelock import FileLock

from .spirit_artifact_reset_plan import SpiritArtifactResetPlanEntry
from .spirit_artifact_reset_action import classify_owned_raw_reset, reset_spirit_artifact_from_owned_raw
from ...instrumentation.spirit_artifact import read_spirit_artifact_inventory_runtime
from ...instrumentation.spirit_artifact_equipped import read_spirit_artifact_equipped_runtime
from ...instrumentation.spirit_artifact_wash_observation import SpiritArtifactWashTarget
from ...catalog.inventory_models import FanxiuSpiritArtifactHallSnapshot


def begin_reset_source_attempt(path: Path, facts: dict) -> str:
    """原子领取一次来源操作；只有明确 not_sent 可再尝试，旧无状态视为未知。"""
    with FileLock(str(path) + '.lock', timeout=0):
        old = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        if old is not None and old.get('status') != 'not_sent':
            raise RuntimeError(f'未决采购且无到账 raw，禁止重购：{path}')
        attempt = uuid.uuid4().hex
        history = [*old.get('history', []), {k: v for k, v in old.items() if k != 'history'}] if old else []
        value = {**facts, 'at': time.time(), 'attempt_id': attempt,
                 'status': 'pending', 'history': history}
        temporary = path.with_name(path.name + '.' + attempt + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)
        return attempt


def record_reset_source_failure(path: Path, attempt: str, error: Exception) -> None:
    """只信来源提供方明确 False；无标记/True 不推断未消费，保留 pending。"""
    with FileLock(str(path) + '.lock', timeout=0):
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('attempt_id') != attempt or value.get('status') != 'pending':
            raise RuntimeError('来源意图所有权变化，拒绝覆盖证据')
        sent = getattr(error, 'purchase_confirmation_may_have_been_sent', None)
        value.update(status='not_sent' if sent is False else 'pending',
                     confirmation_may_have_been_sent=sent if type(sent) is bool else None,
                     failed_at=time.time(), reason=str(error), error_type=type(error).__name__)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)


def run_spirit_artifact_reset_workflow(
    context, execute, *, entry: SpiritArtifactResetPlanEntry,
    artifact_name: str, selected_part_text: str, evidence_dir: Path, stop_at: float,
    acquire_raw: Callable[..., str] | None = None,
    prepare_reset_entry: Callable[[], None] | None = None,
) -> dict:
    """重新读事实，优先复用已到货 raw；重置成功后显式刷新全馆前端。

    acquire_raw(entry=..., evidence_dir=..., stop_at=...) 是单次 market/box
    来源适配器，负责来源导航、唯一目标确认和到账验证，返回真实新 UID 字符串。
    不从缓存计划 raw_item_ids 推断当前库存。prepare_reset_entry() 只调用已有
    正式导航到 reset 支持的稳定页；已有 raw 时也调用，不要求重新进入采购。
    两 callback 不得重试未决消费，也不得在此函数外另起并行游戏操作。

    evidence_dir 须跨重入保持相同。采购调用前持久化 intent；采购异常且
    fresh 库存未证明到账时，再次调用拒绝采购。标记是消费防重证据，不是旧
    generator 游标；仅来源异常明确 confirmation_may_have_been_sent=False 才
    记 not_sent 并允许下次原子领取，保留全部旧证据；未知/True 始终阻塞。
    更多同 base 库存、未决确认、身份冲突均交提供方失败关闭。3-3单raw闭环已验收。
    """
    if stop_at <= time.time() or entry.assessment.stage != '错升':
        raise ValueError('重置编排需要错升计划及有效运行窗口')
    root = Path(evidence_dir)
    root.mkdir(parents=True, exist_ok=True)
    key = f'{entry.assessment.ware_id}-{entry.assessment.part}-{entry.previous_item_id}'
    intent = root / f'{key}-purchase-intent.json'
    started = time.monotonic()

    def boundary(event, **fields):
        with (root / f'{key}-workflow-events.jsonl').open('a', encoding='utf-8') as output:
            output.write(json.dumps({'event': event, 'at': time.time(),
                'elapsed_seconds': time.monotonic() - started, **fields},
                ensure_ascii=False) + '\n')
    # 同 UID 也可能已经由用户补好词条；旧计划的 grade/is_break 不能授权重置。
    # 正式收集同时同步前端，skipped 分支不做任何游戏动作。
    from ...instrumentation.spirit_artifact_collector import collect_spirit_artifact_snapshot_once
    boundary('fresh_hall_start')
    hall = collect_spirit_artifact_snapshot_once()
    boundary('fresh_hall_done')
    if hall.get('runtime_complete') is not True:
        raise RuntimeError('重置准入要求 fresh 完整全馆事实')
    live = [row for artifact in FanxiuSpiritArtifactHallSnapshot.model_validate(hall).artifacts
            for row in artifact.rows if (row.runtime_ware_id, row.runtime_part) ==
            (entry.assessment.ware_id, entry.assessment.part)]
    if len(live) != 1 or live[0].runtime_base_id != entry.base_id or live[0].stage == '待识别':
        raise RuntimeError('当前部件身份或阶段无法唯一核实')
    current_row = live[0]
    if current_row.runtime_item_id == entry.previous_item_id and current_row.stage != '错升':
        result = {'status': 'skipped', 'reason': '当前目标已不属于错升',
                  'stage': current_row.stage, 'item_id': current_row.runtime_item_id, 'hall': hall}
        (root / f'{key}-result.json').write_text(json.dumps(result, ensure_ascii=False, default=str),
                                              encoding='utf-8')
        return result
    hall_observation = current_row.runtime_observation or hall.get('runtime_debug', {})
    hall_identity = (hall_observation.get('pid'), hall_observation.get('process_start_ticks'))

    def facts(expected_identity=None):
        boundary('inventory_observation_start')
        inventory = read_spirit_artifact_inventory_runtime()
        boundary('inventory_observation_done')
        identity = (inventory['pid'], inventory['process_start_ticks'])
        if (inventory.get('complete') is not True or
                inventory.get('source') != 'runtime_spiritware_all_instances'
                or expected_identity is not None and identity != expected_identity):
            raise RuntimeError('完整库存来源或进程身份不一致')
        boundary('equipped_observation_start')
        equipped = read_spirit_artifact_equipped_runtime([entry.assessment.ware_id])
        boundary('equipped_observation_done')
        slots = [r for r in equipped['items'] if r['part'] == entry.assessment.part]
        if ((equipped['pid'], equipped['process_start_ticks']) != identity or len(slots) != 1
                or slots[0]['base_id'] != entry.base_id):
            raise RuntimeError('重置目标精确装备身份不一致')
        rows = [r for r in inventory['items'] if r['base_id'] == entry.base_id]
        return inventory, identity, slots[0]['item_id'], rows

    inventory, identity, equipped_uid, rows = facts(hall_identity)
    if equipped_uid != current_row.runtime_item_id:
        raise RuntimeError('全馆准入后装备实例变化，重新观察后再规划')
    others = [r for r in rows if r['item_id'] != entry.previous_item_id]
    obtained = False
    if not others:
        if (len(rows) != 1 or rows[0]['item_id'] != entry.previous_item_id
                or equipped_uid != entry.previous_item_id
                or (rows[0]['ware_id'], rows[0]['part']) !=
                   (entry.assessment.ware_id, entry.assessment.part)
                or rows[0]['grade'] != entry.previous_grade or rows[0]['is_break'] is not True
                or rows[0]['quality'] != 6 or rows[0]['quantity'] != 1):
            raise RuntimeError('采购前旧本体事实不符合计划')
        if acquire_raw is None:
            raise RuntimeError('没有现有 raw，也未提供已授权来源适配器')
        attempt = begin_reset_source_attempt(intent, {'entry_key': key, 'pid': identity[0],
            'process_start_ticks': identity[1], 'inventory_before': inventory})
        try:
            received_uid = acquire_raw(entry=entry, evidence_dir=root, stop_at=stop_at)
        except Exception as error:
            record_reset_source_failure(intent, attempt, error)
            raise
        if not isinstance(received_uid, str) or not received_uid:
            raise RuntimeError('来源适配器必须返回实际新本体 UID')
        inventory, _, equipped_uid, rows = facts(identity)
        others = [r for r in rows if r['item_id'] != entry.previous_item_id]
        if len(others) != 1 or others[0]['item_id'] != received_uid:
            raise RuntimeError('采购回执与 fresh 唯一新本体不一致；禁止再次采购')
        obtained = True
    if len(others) != 1:
        raise RuntimeError('同 base 存在多件候选，单件重置不能自行选择材料')
    target = SpiritArtifactWashTarget(others[0]['item_id'], entry.assessment.ware_id,
                                     entry.assessment.part, identity, entry.base_id)
    action = classify_owned_raw_reset(rows, target=target,
        previous_item_id=entry.previous_item_id, expected_previous_grade=entry.previous_grade,
        equipped_item_id=equipped_uid)
    if prepare_reset_entry is not None:
        boundary('navigation_start')
        prepare_reset_entry()
        boundary('navigation_done')
    boundary('reset_start', entry_action=action)
    reset = reset_spirit_artifact_from_owned_raw(context, execute, target=target,
        previous_item_id=entry.previous_item_id, expected_previous_grade=entry.previous_grade,
        artifact_name=artifact_name, selected_part_text=selected_part_text,
        evidence_path=root / f'{key}-reset-events.jsonl', stop_at=stop_at)
    boundary('reset_done')
    boundary('sync_start')
    hall = collect_spirit_artifact_snapshot_once()
    boundary('sync_done')
    result = {'status': 'complete', 'obtained_this_run': obtained, 'entry_action': action,
              'reset': reset, 'hall': hall}
    (root / f'{key}-result.json').write_text(json.dumps(result, ensure_ascii=False, default=str),
                                          encoding='utf-8')
    return result


def reset_spirit_artifact_from_market(
    context, execute, *, entry: SpiritArtifactResetPlanEntry, request,
    artifact_name: str, selected_part_text: str, evidence_dir: Path, stop_at: float,
    assets=None,
) -> dict:
    """正式市场导航→单件采购→可重入重置→同步；已有raw跳过采购。

    初始允许666/34/247/724及reset合法重入页；不使用任意目标go_scene。
    每次wait检查实际落点；未决购买725/确认页不作取消或重放。市场来源导航
    已逐步实测，并验证采购到账后的公共入口重入及结果页收尾。
    储物袋来源使用 reset_spirit_artifact_from_choice_box，实际开箱链单独验收。
    """
    from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
    from .spirit_artifact_purchase import SpiritArtifactPurchaseAssets, purchase_spirit_artifact_body
    if ((request.base_id, request.ware_id, request.part, request.quantity) !=
            (entry.base_id, entry.assessment.ware_id, entry.assessment.part, 1)):
        raise ValueError('市场请求与重置计划目标不一致')
    purchase_assets = assets or SpiritArtifactPurchaseAssets()
    if purchase_assets.shop_scene != 724:
        raise ValueError('此导航入口只支持已验证珍宝阁724')

    def wait(*scenes):
        result = execute(context.wait_scene(list(scenes), wait=12))
        if result.scene_id not in scenes:
            raise RuntimeError(f'市场重置导航要求{scenes}，实际#{result.scene_id}；保留现场')
        return result.scene_id

    def move(scene, shape, destination):
        context.click_shape_center(scene, shape)
        return wait(destination)

    def acquire_raw(**kwargs):
        purchase_started = False
        try:
            scene = wait(666, 34, 247, 724)
            if scene == 666:
                scene = move(666, '返回', 34)
            if scene == 34:
                scene = move(34, '仙市', 247)
            if scene == 247:
                scene = move(247, '珍宝阁', 724)
            move(724, '灵器', 724)
            purchase_started = True
            result = purchase_spirit_artifact_body(context, execute, request=request,
                assets=purchase_assets, evidence_path=Path(evidence_dir) /
                f'{entry.assessment.ware_id}-{entry.assessment.part}-market-events.jsonl',
                stop_at=stop_at)
            ids = result.get('item_ids')
            if not isinstance(ids, (list, tuple)) or len(ids) != 1:
                raise RuntimeError('市场采购未返回唯一实际新本体UID')
            return ids[0]

        except Exception as error:
            if not purchase_started:
                error.purchase_confirmation_may_have_been_sent = False
            raise

    def prepare_reset_entry():
        scene = wait(666, 34, 247, 724, 667, 717, 721, 720, 718, 719)
        if scene in (247, 724):
            scene = move(scene, '返回', 34)
        if scene == 34:
            SpiritArtifactCleanseRuntimeGuiAdapter(context, execute).open_overview()
            wait(666)
        # 其余已是reset准入页；未决确认由reset明确失败关闭。

    return run_spirit_artifact_reset_workflow(context, execute, entry=entry,
        artifact_name=artifact_name, selected_part_text=selected_part_text,
        evidence_dir=evidence_dir, stop_at=stop_at, acquire_raw=acquire_raw,
        prepare_reset_entry=prepare_reset_entry)


def reset_spirit_artifact_from_choice_box(
    context, execute, *, entry: SpiritArtifactResetPlanEntry, request,
    artifact_name: str, selected_part_text: str, evidence_dir: Path, stop_at: float,
) -> dict:
    """明确自选匣开1个→唯一红raw→重置→同步；本轮无目标箱，尚未真实验收。

    request 为实际 StorageBagChoiceBoxRequest，quantity 是整堆现有数量，
    open_quantity 必须显式为1；note必须唯一命名目标奖励，不接受“首个可选”。
    Catalog精确base/灵器/部位/红品质且单箱仅1件。复用既有#525/#586/#587
    对齐与到账验证；超出正式可见候选范围或未决明细失败关闭，不猜、不重开。
    已有raw通过通用workflow跳过开箱；返回的实际UID再次由fresh库存验证。
    """
    from dataclasses import asdict
    from .storage_bag_choice_box import (
        StorageBagChoiceBoxGuiAdapter, StorageBagChoiceBoxRequest,
        choice_rewards_from_catalog, parse_persisted_choice_note,
    )
    from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
    from ...catalog.item import load_fanxiu_item_runtime_index
    from ...catalog.spirit_artifact_wash_rules import load_spirit_artifact_wash_rules
    from ...instrumentation import fanxiu_instrumentation_service
    from ...instrumentation.spirit_artifact_storage_bag import resolve_stable_spirit_artifact_rewards
    from ...runtime_gui import normalize_ocr_name
    if not isinstance(request, StorageBagChoiceBoxRequest) or request.open_quantity != 1:
        raise ValueError('箱子重置需要实际自选请求并明确open_quantity=1')
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id']
    card = cards.get(str(request.base_id)) or {}
    items_by_base_id = load_spirit_artifact_wash_rules()['items_by_base_id']
    if entry.base_id not in resolve_stable_spirit_artifact_rewards(card, items_by_base_id):
        raise ValueError('该容器不是目标红本体的稳定自选来源；随机箱/升品镜/条件奖励不可用')
    rewards = choice_rewards_from_catalog(card, cards)
    kind, name = parse_persisted_choice_note(request.note)
    matches = [r for r in rewards if normalize_ocr_name(r.name) == normalize_ocr_name(name)]
    cfg = items_by_base_id.get(entry.base_id)
    if (kind != 'named' or len(matches) != 1 or matches[0].base_id != entry.base_id
            or not matches[0].is_spirit_artifact or matches[0].count_per_box != 1
            or cfg is None or cfg['quality'] != 6 or cfg['type'] != entry.assessment.ware_id
            or cfg['parts'] != entry.assessment.part):
        raise ValueError('自选备注/Catalog并非明确目标的单件红本体奖励')

    def wait(*scenes):
        observed = execute(context.wait_scene(list(scenes), wait=12))
        if observed.scene_id not in scenes:
            raise RuntimeError(f'箱子重置导航要求{scenes}，实际#{observed.scene_id}；保留现场')
        return observed.scene_id

    def move(scene, shape, destination):
        context.click_shape_center(scene, shape)
        return wait(destination)

    def acquire_raw(**kwargs):
        purchase_started = False
        try:
            scene = wait(666, 34, 525)
            if scene == 666:
                scene = move(666, '返回', 34)
            if scene == 34:
                move(34, '储物袋', 525)
            adapter = StorageBagChoiceBoxGuiAdapter(context=context,
                snapshot_reader=fanxiu_instrumentation_service.backpack_ui_snapshot,
                catalog_cards_by_id=cards)
            purchase_started = True
            result = execute(adapter.execute(request))
            (Path(evidence_dir) / f'{entry.assessment.ware_id}-{entry.assessment.part}-box-result.json').write_text(
                json.dumps(asdict(result), ensure_ascii=False, default=str), encoding='utf-8')
            proof = result.spirit_artifact_outcome
            if (proof is None or proof.reward_base_id != entry.base_id or proof.quantity != 1
                    or len(proof.added_item_ids) != 1):
                raise RuntimeError('自选匣没有目标红本体唯一到账证据；禁止重开')
            wait(525)
            return proof.added_item_ids[0]

        except Exception as error:
            if not purchase_started:
                error.purchase_confirmation_may_have_been_sent = False
            raise

    def prepare_reset_entry():
        scene = wait(525, 34, 666, 667, 717, 721, 720, 718, 719)
        if scene == 525:
            scene = move(525, '返回', 34)
        if scene == 34:
            SpiritArtifactCleanseRuntimeGuiAdapter(context, execute).open_overview()
            wait(666)

    return run_spirit_artifact_reset_workflow(context, execute, entry=entry,
        artifact_name=artifact_name, selected_part_text=selected_part_text,
        evidence_dir=evidence_dir, stop_at=stop_at, acquire_raw=acquire_raw,
        prepare_reset_entry=prepare_reset_entry)
