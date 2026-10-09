"""Pure routing based on observed configuration texts; no stock or GUI simulation."""
from backend.core.fanxiu.storage_bag_catalog_purpose import classify_storage_bag_catalog_purpose
from backend.core.fanxiu.storage_bag_unknown_research import (
    sync_storage_bag_catalog_purpose_findings, read_storage_bag_unknown_research,
    project_storage_bag_research_policy,
)


def card(**fields):
    return {'id': 1, 'name': 'configured item', **fields}


def test_artifact_buff_mentions_do_not_turn_refinement_into_prayer_consumption():
    result = classify_storage_bag_catalog_purpose(card(type=1, sub_type=35,
        effect_description='可用于神炼【法宝·月魄流光剑】。\n【洗灵证武】活动持续期间每消耗洗灵奇石获得个人榜单积分'))
    assert result['route'] == 'artifact_refinement'
    assert result['purpose_status'] == 'independent_lifecycle'


def test_description_category_is_not_a_complete_pool_or_scoring_proof():
    result = classify_storage_bag_catalog_purpose(card(type=2, sub_type=1,
        description='装有炼体法相道具的宝匣，打开后可获得大量炼体法相道具。'))
    assert result['purpose_status'] == 'reserve'
    assert result['declared_categories'] == ['炼体法相']
    assert result['reward_graph_complete'] is False
    assert result['prayer_eligibility'] == 'unverified'


def test_random_text_does_not_assign_use_and_missing_types_are_technical_unknown():
    result = classify_storage_bag_catalog_purpose(card(type=2, effect_description='打开后会随机掉落以下道具'))
    assert result['purpose_status'] == 'await_owner'
    result = classify_storage_bag_catalog_purpose(card(name='【中秋】'))
    assert result['purpose_status'] == 'unknown' and result['route'] == 'unresolved_metadata'


def test_conversion_materials_are_retained_even_with_no_direct_prayer_category():
    root = card(type=2, optional_gift_rewards=[{'id': 32004}])
    cards = {'1': root, '32004': card(id=32004, name='废料·灰暗灵丹',
        description='可转化为其中之一：上品饲灵丸/珍品饲灵丸/绝品饲灵丸/仙品饲灵丸')}
    result = classify_storage_bag_catalog_purpose(root, cards_by_id=cards,
        reward_rule={'classification_complete': True, 'resource_categories': []})
    assert result['route'] == 'resource_conversion_candidate'
    assert result['purpose_status'] == 'reserve'


def test_catalog_persistence_never_creates_stock_discovery_or_enables_usage(tmp_path):
    path = tmp_path / 'ledger.json'
    item = card(type=5, sub_type=76, description='可补全星海法则，提升轮回域之品阶')
    sync_storage_bag_catalog_purpose_findings([item], path=path)
    state = read_storage_bag_unknown_research(path=path)
    assert state['items'] == {} and len(state['catalog_findings']) == 1
    atlas = {'items': [{'base_id': 1, 'item': item, 'auto_claim': False}]}
    projected = project_storage_bag_research_policy(atlas, current_signatures=[], path=path)
    assert projected['items'][0]['research_hold'] == 'independent_lifecycle'
    assert projected['items'][0]['auto_claim'] is False
    changed = {**item, 'description': 'new configuration'}
    projected = project_storage_bag_research_policy({'items': [{'base_id': 1, 'item': changed}]}, current_signatures=[], path=path)
    assert 'research_hold' not in projected['items'][0]


def test_catalog_question_uses_durable_outbox_without_stock(tmp_path):
    from backend.core.messaging.wechat_agent_store import AgentStore
    from backend.core.fanxiu.storage_bag_unknown_research import (
        configure_storage_bag_research_confirmation, queue_storage_bag_catalog_confirmation,
    )
    path = tmp_path / 'ledger.json'
    item = card(type=2, effect_description='打开后可获得以下道具')
    finding = sync_storage_bag_catalog_purpose_findings([item], path=path)[0]
    store = AgentStore(tmp_path / 'outbox.sqlite')
    store.ensure_hook('group', 'account', 'chat')
    store.subscribe_codex_thread('thread', 'group', [])
    configure_storage_bag_research_confirmation(thread_id='thread', path=path, store=store)
    for _ in range(2):
        queue_storage_bag_catalog_confirmation([finding['fingerprint']], text='请确认是否加入日常规则', path=path, store=store)
    assert len(AgentStore(tmp_path / 'outbox.sqlite').codex_notifications_status()['notifications']) == 1
    assert read_storage_bag_unknown_research(path=path)['items'] == {}


def test_saved_purpose_question_is_reused_on_later_current_identity(tmp_path, monkeypatch):
    from backend.core.fanxiu.storage_bag_unknown_research import (
        discover_storage_bag_unknown_items, dispatch_storage_bag_unknown_research,
    )
    from backend.core.fanxiu.data_annotation import ai_assistance
    path = tmp_path / 'ledger.json'
    item = card(type=2, metadata_only=True, effect_description='打开后可获得以下道具')
    sync_storage_bag_catalog_purpose_findings([item], path=path)
    snapshot = {'complete': True, 'source': 'active_backpack_panel_item_info_list',
                'captured_at_epoch': 1000, 'items': [{'base_id': 1, 'num': 1}]}
    signatures = discover_storage_bag_unknown_items({'items': [
        {'base_id': 1, 'item': item, 'operation_template': 'random_box'}]}, snapshot,
        rules={}, path=path, now=1000)
    monkeypatch.setattr(ai_assistance, 'request_ai_assistance', lambda *a, **k: (_ for _ in ()).throw(AssertionError('duplicate investigation')))
    assert dispatch_storage_bag_unknown_research(signatures[0], path=path, now=1000)['status'] == 'awaiting_owner'
