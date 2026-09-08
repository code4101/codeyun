"""批量补给执行；规划与来源动作解耦，每个目标只发一次数量交易。

消费证据文件跨重入复用。已成功批次不重放，已发确认但无回执的批次由现有
来源接口拒绝；不得通过更换 evidence_dir 绕开未决交易。此处不执行升级。
"""
from dataclasses import asdict
import json
from pathlib import Path
import time

from .spirit_artifact_purchase import (
    SpiritArtifactPurchaseRequest, SpiritArtifactPurchaseAssets, purchase_spirit_artifact_body,
)
from ...catalog.spirit_artifact_market import load_spirit_artifact_market_targets


def purchase_balanced_spirit_artifact_market(context, execute, *, batches,
                                            evidence_dir: Path, stop_at: float):
    """已授权计划中的市场批量采购；入口必须在珍宝阁724，结束仍在724。

    batches 为 BalancedSupplyBatch 序列；商品ID/单价来自正式目录且由购买组件
    在实时对话框复验。每批预算仅等于该批数量*单价，不额外消费剩余货币。
    """
    root = Path(evidence_dir)
    root.mkdir(parents=True, exist_ok=True)
    plan = [asdict(batch) for batch in batches]
    plan_path = root / 'market-plan.json'
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True)
    if plan_path.exists() and plan_path.read_text(encoding='utf-8') != canonical:
        raise ValueError('批次证据目录已有另一份市场计划')
    plan_path.write_text(canonical, encoding='utf-8')
    targets = {t.base_id: t for t in load_spirit_artifact_market_targets()}
    results = []
    for batch in batches:
        receipt = root / f'market-{batch.reward_id}-receipt.json'
        if receipt.exists():
            results.append(json.loads(receipt.read_text(encoding='utf-8')))
            continue
        if time.time() >= stop_at - 120:
            return dict(status='paused', receipts=results)
        target = targets.get(batch.reward_id)
        if target is None or (target.ware_id, target.part) != batch.target:
            raise ValueError('批量目标与正式市场目录不符')
        match = execute(context.wait_scene([724], wait=8))
        if match.scene_id != 724:
            raise RuntimeError('市场采购未处于珍宝阁')
        # Category is not encoded by scene724. Re-enter it to reset the shared
        # scroller: subsequent targets can precede the last purchased row.
        execute(context.wait_click(724, '灵装'))
        execute(context.wait_click(724, '灵器'))
        execute(context.wait_action_settle(.8))
        request = SpiritArtifactPurchaseRequest(
            goods_id=target.goods_id, base_id=target.base_id, ware_id=target.ware_id,
            part=target.part, name=target.name, cost_item_id=target.cost_item_id,
            quantity=batch.quantity, unit_price=target.unit_price,
            currency_limit=target.unit_price * batch.quantity)
        result = purchase_spirit_artifact_body(context, execute, request=request,
            assets=SpiritArtifactPurchaseAssets(),
            evidence_path=root / f'market-{batch.reward_id}-events.jsonl', stop_at=stop_at)
        receipt.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding='utf-8')
        results.append(result)
    return dict(status='complete', receipts=results)


def open_balanced_spirit_artifact_boxes(context, execute, *, sources,
                                       evidence_dir: Path, stop_at: float):
    """按目标批量自选，入口/出口525；每批观察现存堆栈，不把上批数量复用。

    sources 为 supply_round 中 storage_bag 项（含 base_id 与 batches）。
    正式自选组件校验奖励、选中态、数量和到账；未决批次不自动重试。
    """
    from .storage_bag_choice_box import StorageBagChoiceBoxRequest, StorageBagChoiceBoxGuiAdapter
    from .spirit_artifact_reset_workflow import begin_reset_source_attempt, record_reset_source_failure
    from ...catalog.item import load_fanxiu_item_runtime_index
    from ...instrumentation import fanxiu_instrumentation_service
    root = Path(evidence_dir)
    root.mkdir(parents=True, exist_ok=True)
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id']
    results = []
    for source in sources:
        if source['kind'] != 'storage_bag':
            continue
        for batch in source['batches']:
            box_id, reward_id, quantity = source['base_id'], batch['reward_id'], batch['quantity']
            receipt = root / f'box-{box_id}-{reward_id}-receipt.json'
            if receipt.exists():
                results.append(json.loads(receipt.read_text(encoding='utf-8')))
                continue
            if time.time() >= stop_at - 120:
                return dict(status='paused', receipts=results)
            if execute(context.wait_scene([525], wait=8)).scene_id != 525:
                raise RuntimeError('批量自选入口必须为储物袋')
            before = fanxiu_instrumentation_service.backpack_ui_snapshot()
            stacks = [r for r in before.get('items', []) if r.get('base_id') == box_id
                      and not r.get('is_padding') and r.get('num', 0) >= quantity]
            if before.get('complete') is not True or len(stacks) != 1:
                raise RuntimeError('无法唯一确定足够数量的箱子堆栈')
            request = StorageBagChoiceBoxRequest(base_id=box_id,
                instance_id=str(stacks[0]['instance_id']), name=cards[str(box_id)]['name'],
                quantity=stacks[0]['num'], open_quantity=quantity,
                note='选' + cards[str(reward_id)]['name'])
            # Claim before any potential consumption; failures retain the baseline for reconciliation.
            intent = root / f'box-{box_id}-{reward_id}-intent.json'
            attempt = begin_reset_source_attempt(intent, dict(request=asdict(request), before=before))
            adapter = StorageBagChoiceBoxGuiAdapter(context=context,
                snapshot_reader=fanxiu_instrumentation_service.backpack_ui_snapshot,
                catalog_cards_by_id=cards)
            try:
                outcome = execute(adapter.execute(request))
            except Exception as error:
                record_reset_source_failure(intent, attempt, error)
                raise
            if (outcome.delta.opened_count != quantity or outcome.delta.reward_base_id != reward_id
                    or outcome.delta.reward_quantity != quantity):
                raise RuntimeError('箱子消耗或奖励到账与计划不符，禁止重开')
            result = asdict(outcome)
            receipt.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding='utf-8')
            results.append(result)
    return dict(status='complete', receipts=results)
