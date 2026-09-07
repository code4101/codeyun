from backend.core.fanxiu.instrumentation.spirit_artifact_advanced import advanced_item_confirmation_names


def test_confirmation_uses_same_item_config_spelling():
    item = {'name': '洗灵·无瑕石', 'useDes': '使用1个<color=#73123a>洗灵·无暇石</color>，可将1条属性提升至满值'}
    assert advanced_item_confirmation_names(item) == ('洗灵无瑕石', '洗灵无暇石')


def test_effect_description_does_not_supply_another_item_alias():
    item = {'name': '洗灵·巅峰石', 'useDes': '属性说明：<color=#73123a>洗灵·无暇石</color>'}
    assert advanced_item_confirmation_names(item) == ('洗灵巅峰石',)
