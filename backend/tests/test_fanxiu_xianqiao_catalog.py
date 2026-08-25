from backend.core.fanxiu.catalog.xianqiao import _build_mechanics_from_rows


def test_xianqiao_mechanics_projects_growth_elements_and_trial() -> None:
    core_levels = []
    bases = []
    for part in range(1, 7):
        bases.append({"type": 1, "parts": part, "title_plain": f"窍{part}"})
        core_levels.extend(
            [
                {"type": 1, "parts": part, "level": 0, "grade": 1, "attr": {}},
                {"type": 1, "parts": part, "level": 10, "grade": 2, "consumeExp": 100, "attr": {"ATTACK": 10}},
            ]
        )

    ware_rows = [
        {
            "itemId": 20000110 + quality,
            "type": 1,
            "parts": 1,
            "quality": quality,
            "elementNumLimit": quality,
            "initialMainAttr": "ATTACK",
            "expOff": 8000,
            "exp": quality * 100,
        }
        for quality in range(3, 7)
    ]
    ware_levels = []
    for row in ware_rows:
        ware_levels.extend(
            [
                {"itemId": row["itemId"], "level": 0, "addMainAttr": 10},
                {
                    "itemId": row["itemId"],
                    "level": 10,
                    "consumeExp": 50,
                    "unlockElement": 1,
                    "randomSideAttr": 1,
                    "addMainAttr": 20,
                },
            ]
        )

    mechanics = _build_mechanics_from_rows(
        core={
            "ConfigValue": [
                {"id": "ATTR_UP_VALUE", "value": "35"},
                {"id": "COREWARE_BAG_NUMBER_LIMIT", "value": "6000"},
            ],
            "CoreMap": [{"id": 1, "type": 1, "name_plain": "玄鹤", "desc_plain": "解锁"}],
            "CoreBase": bases,
            "CoreLevel": core_levels,
            "CoreWareBase": ware_rows,
            "CoreWareLevel": ware_levels,
            "CoreWareElementBase": [
                {
                    "element": 1,
                    "sort": 1,
                    "name_plain": "金",
                    "des_plain": "当前体系总金元素达到3/6/9/12，可激活1/2/3/4级效果，在战斗中可大幅提高角色功法增威",
                }
            ],
            "CoreWareElement": [
                {"type": 1, "element": 1, "level": level, "elementNum": level * 3, "effectTxt_plain": f"效果{level}"}
                for level in range(1, 5)
            ],
            "CoreWareSideAttrBank": [{"id": 1}],
        },
        trial={
            "ConfigValue": [
                {"id": "DAILY_REWARD_TIMES", "content": "2"},
                {"id": "COREMAP_ELEMENT_LEVEL_POINT", "content": "1,3,5,7"},
            ],
            "CoreWareTrialBase": [
                {"id": 1, "type": 1, "sort": 1, "coreMapId": 1, "dungeonId": 99, "levelName": "试炼怪"},
            ],
            "CoreWareTrialBuffGroup": [
                {"id": 1, "type": 1, "selectType": 3, "des_plain": "攻击提高【%s】"},
            ],
            "CoreWareTrialBuffLevel": [
                {"buffGroupId": 1, "level": 1, "point": 1},
            ],
            "CoreWareTrialReward": [
                {"dungeonId": 99, "totalPoint": 0},
                {"dungeonId": 99, "totalPoint": 1},
            ],
        },
        attribute_rows=[{"id": "ATTACK", "name_plain": "攻击", "group": 1}],
    )

    assert mechanics["systems"][0]["parts"][0]["grade_checkpoints"] == [
        {"grade": 1, "level": 10, "cumulative_exp": 100},
    ]
    assert mechanics["systems"][0]["elements"][0]["levels"][-1]["required_count"] == 12
    assert mechanics["systems"][0]["elements"][0]["purpose"] == "功法增威"
    assert mechanics["qualities"][0]["initial_element_slots"] == 2
    assert mechanics["qualities"][0]["invested_exp_return_rate"] == 0.8
    assert mechanics["trial"]["daily_reward_times"] == 2
    assert mechanics["trial"]["modes"][0]["difficulty_max"] == 1
