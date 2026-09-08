"""批量补给执行；规划与来源动作解耦，每个目标只发一次数量交易。

消费证据文件跨重入复用。已成功批次不重放，已发确认但无回执的批次由现有
来源接口拒绝；不得通过更换 evidence_dir 绕开未决交易。
此处只完成兑换、开箱；升级模块独立扫描现存材料，不接收来源分配或采购回执。
"""
from dataclasses import asdict
import json
from pathlib import Path
import time

from .spirit_artifact_purchase import (
    SpiritArtifactPurchaseRequest, SpiritArtifactPurchaseAssets, purchase_spirit_artifact_body,
)
from ...catalog.spirit_artifact_market import load_spirit_artifact_market_targets


def _publish_box_snapshot(box_id, snapshot):
    from sqlmodel import Session
    from backend.db import engine
    from ...instrumentation.spirit_artifact_storage_bag import publish_spirit_artifact_box_quantity
    with Session(engine) as session:
        publish_spirit_artifact_box_quantity(session, box_base_id=box_id, runtime_snapshot=snapshot)


def _box_receipt_result(receipt, path):
    """观测留在回执文件，上层仅接收兑换结果与证据位置。"""
    return {**{key: value for key, value in receipt.items() if key != 'inventory_snapshot'},
            'receipt_path': str(path)}


def reconcile_spirit_artifact_box_receipt(*, evidence_dir: Path, proof_path: Path,
                                         after_storage, after_spirit):
    """只核账、不兑换；原异常现场与当前双快照一致后补记未决批次回执。"""
    import os
    from filelock import FileLock
    from .storage_bag_choice_box import (
        StorageBagChoiceBoxRequest, StorageBagChoiceReward,
        verify_spirit_artifact_choice_transaction,
    )
    evidence = json.loads(Path(proof_path).read_text(encoding='utf-8'))
    request = StorageBagChoiceBoxRequest(**evidence['request'])
    reward = StorageBagChoiceReward(**evidence['reward'])
    root = Path(evidence_dir)
    prefix = f'box-{request.base_id}-{reward.base_id}'
    intent_path = root / f'{prefix}-intent.json'
    receipt = root / f'{prefix}-receipt.json'
    with FileLock(str(intent_path) + '.lock', timeout=0):
        if receipt.exists():
            saved = json.loads(receipt.read_text(encoding='utf-8'))
            if saved.get('inventory_snapshot'):
                _publish_box_snapshot(request.base_id, saved['inventory_snapshot'])
            return _box_receipt_result(saved, receipt)
        intent = json.loads(intent_path.read_text(encoding='utf-8'))
        if (intent.get('status') != 'pending' or intent['request'] != evidence['request']
                or intent['before']['items'] != evidence['before']['items']
                or intent['before']['evidence'] != evidence['before']['evidence']):
            raise ValueError('补记证据与原未决来源不一致')
        delta, proof = verify_spirit_artifact_choice_transaction(
            evidence['before'], after_storage, evidence['spirit_before'], after_spirit,
            request=request, reward=reward)
        result = dict(request=asdict(request), reward=asdict(reward), delta=asdict(delta),
                      spirit_outcome=asdict(proof), reconciled_at=time.time(),
                      attempt_id=intent['attempt_id'], inventory_snapshot=after_storage)
        temporary = receipt.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, receipt)
        _publish_box_snapshot(request.base_id, after_storage)
        return _box_receipt_result(result, receipt)


def purchase_balanced_spirit_artifact_market(context, execute, *, batches,
                                            evidence_dir: Path, stop_at: float):
    """已授权计划中的市场批量采购；入口必须在珍宝阁724，结束仍在724。

    batches 为 BalancedSupplyBatch 序列；商品ID/单价来自正式目录且由购买组件
    在实时对话框复验。每批预算仅等于该批数量*单价，不额外消费剩余货币。
    stop_at 仅限制整轮开始；开始后连续兑换完，异常保留现场，不退回主城。
    """
    batches = tuple(batches)
    targets = {t.base_id: t for t in load_spirit_artifact_market_targets()}
    seen = set()
    for batch in batches:
        target = targets.get(batch.reward_id)
        if (target is None or (target.ware_id, target.part) != batch.target
                or type(batch.quantity) is not int or batch.quantity <= 0
                or batch.reward_id in seen):
            raise ValueError('市场分配须为有效目标、正整数数量，且同目标合并成一批')
        seen.add(batch.reward_id)
    root = Path(evidence_dir)
    root.mkdir(parents=True, exist_ok=True)
    plan = [asdict(batch) for batch in batches]
    plan_path = root / 'market-plan.json'
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True)
    if plan_path.exists() and plan_path.read_text(encoding='utf-8') != canonical:
        raise ValueError('批次证据目录已有另一份市场计划')
    plan_path.write_text(canonical, encoding='utf-8')
    results = []
    started = False
    for batch in batches:
        receipt = root / f'market-{batch.reward_id}-receipt.json'
        if receipt.exists():
            results.append(json.loads(receipt.read_text(encoding='utf-8')))
            continue
        if not started and time.time() >= stop_at - 120:
            return dict(status='paused', receipts=results)
        started = True
        target = targets[batch.reward_id]
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
            evidence_path=root / f'market-{batch.reward_id}-events.jsonl', stop_at=float('inf'))
        receipt.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding='utf-8')
        results.append(result)
    # Reuse the latest authoritative debit observation, including receipt replay.
    # Publishing this one field must not demand the bag UI or read Runtime again.
    currency_rows = [result['currency_after'] for result in results if result.get('currency_after')]
    if currency_rows:
        from sqlmodel import Session
        from backend.db import engine
        from ...instrumentation.spirit_artifact_storage_bag import publish_spirit_artifact_market_currency
        currency = max(currency_rows, key=lambda row: float(row['observed_at']))
        with Session(engine) as session:
            publish_spirit_artifact_market_currency(session, currency['amount'], currency)
    return dict(status='complete', receipts=results)


def open_balanced_spirit_artifact_boxes(context, execute, *, sources,
                                       evidence_dir: Path, stop_at: float):
    """按目标批量自选，入口/出口525；每批观察现存堆栈，不把上批数量复用。

    sources 为 supply_round 中 storage_bag 项（含 base_id 与 batches）。
    正式自选组件校验奖励、选中态、数量和到账；未决批次不自动重试。
    同一种箱子连续开完；stop_at 只限制开始下一种箱子，不在同箱分批间暂停。
    正常结束仍在储物袋，异常保留现场，不提供返回主城的收尾动作。
    """
    from .storage_bag_choice_box import (
        StorageBagChoiceBoxRequest, StorageBagChoiceBoxGuiAdapter, choice_rewards_from_catalog,
    )
    from .spirit_artifact_reset_workflow import begin_reset_source_attempt, record_reset_source_failure
    from ...catalog.item import load_fanxiu_item_runtime_index
    from ...instrumentation import fanxiu_instrumentation_service
    sources = tuple(s for s in sources if s['kind'] == 'storage_bag')
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)['cards_by_id']
    seen_boxes = set()
    for source in sources:
        box_id = source['base_id']
        if box_id in seen_boxes:
            raise ValueError('同种箱子必须合并为一份连续分配')
        seen_boxes.add(box_id)
        rewards = {r.base_id for r in choice_rewards_from_catalog(cards.get(str(box_id), {}), cards)}
        seen_rewards = set()
        for batch in source['batches']:
            reward_id, quantity = batch['reward_id'], batch['quantity']
            if (reward_id not in rewards or reward_id in seen_rewards
                    or type(quantity) is not int or quantity <= 0):
                raise ValueError('箱子分配须为目录内奖励、正整数数量，且同目标合并成一批')
            seen_rewards.add(reward_id)
    root = Path(evidence_dir)
    root.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(sources, ensure_ascii=False, sort_keys=True)
    plan_path = root / 'box-plan.json'
    if plan_path.exists() and plan_path.read_text(encoding='utf-8') != canonical:
        raise ValueError('批次证据目录已有另一份自选计划')
    plan_path.write_text(canonical, encoding='utf-8')
    results = []
    for source in sources:
        # One adapter owns this fixed box list for the entire uninterrupted loop.
        latest_snapshot = None
        def read_snapshot():
            nonlocal latest_snapshot
            latest_snapshot = fanxiu_instrumentation_service.backpack_ui_snapshot()
            return latest_snapshot
        adapter = StorageBagChoiceBoxGuiAdapter(context=context,
            snapshot_reader=read_snapshot, catalog_cards_by_id=cards)
        started = False
        for batch in source['batches']:
            box_id, reward_id, quantity = source['base_id'], batch['reward_id'], batch['quantity']
            receipt = root / f'box-{box_id}-{reward_id}-receipt.json'
            if receipt.exists():
                saved = json.loads(receipt.read_text(encoding='utf-8'))
                if saved.get('inventory_snapshot'):
                    _publish_box_snapshot(box_id, saved['inventory_snapshot'])
                results.append(_box_receipt_result(saved, receipt))
                continue
            if not started and time.time() >= stop_at - 120:
                return dict(status='paused', receipts=results)
            started = True
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
            def record_plan(plan):
                with (root / f'box-{box_id}-{reward_id}-position.jsonl').open('a', encoding='utf-8') as output:
                    output.write(json.dumps(dict(at=time.time(), attempt_id=attempt,
                        **asdict(plan)), ensure_ascii=False, default=str) + '\n')
            adapter.plan_observer = record_plan
            try:
                outcome = execute(adapter.execute(request, snapshot=before))
            except (Exception, KeyboardInterrupt) as error:
                record_reset_source_failure(intent, attempt, error)
                raise
            if (outcome.delta.opened_count != quantity or outcome.delta.reward_base_id != reward_id
                    or outcome.delta.reward_quantity != quantity):
                raise RuntimeError('箱子消耗或奖励到账与计划不符，禁止重开')
            result = asdict(outcome)
            result['inventory_snapshot'] = latest_snapshot
            receipt.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding='utf-8')
            _publish_box_snapshot(box_id, latest_snapshot)
            results.append(_box_receipt_result(result, receipt))
        # Planned batches are not the completion criterion: this box must be empty.
        # Also covers a resumed invocation whose receipts were all already present.
        final_snapshot = read_snapshot()
        if final_snapshot.get('complete') is not True:
            raise RuntimeError('同箱循环结束未取得完整储物袋快照')
        remaining = sum(int(row.get('num') or 0) for row in final_snapshot.get('items', [])
                        if row.get('base_id') == source['base_id'] and not row.get('is_padding'))
        _publish_box_snapshot(source['base_id'], final_snapshot)
        if remaining:
            raise RuntimeError(f"同箱分配已执行但仍剩 {remaining} 个箱子；保留储物袋现场，补全分配后再继续")
    return dict(status='complete', receipts=results)
