from backend.core.fanxiu.catalog.lilian_event import (
    build_lilian_event_catalog,
    match_lilian_catalog_event,
    select_lilian_catalog_choice,
)


def _catalog():
    return build_lilian_event_catalog(
        [
            {
                "id": 140004,
                "eventType": 4,
                "eventGroupId": 140004,
                "eventName": "酗酒剑仙",
            },
            {
                # The shipped row repeats 140004, while its plots use its id.
                "id": 140005,
                "eventType": 4,
                "eventGroupId": 140004,
                "eventName": "英雄救美",
            },
        ],
        [
            {
                "id": 14000402,
                "eventGroupId": 140004,
                "eventPlotType": 1,
                "eventDes": "赠予美酒",
                "winReward": 2,
                "loseReward": 2,
            },
            {
                "id": 14000403,
                "eventGroupId": 140004,
                "eventPlotType": 1,
                "eventDes": "踹他一脚",
                "winReward": 1,
            },
            {
                "id": 14000502,
                "eventGroupId": 140005,
                "eventPlotType": 1,
                "eventDes": "相信女修",
                "winReward": 2,
                "loseReward": 2,
            },
            {
                "id": 14000503,
                "eventGroupId": 140005,
                "eventPlotType": 1,
                "eventDes": "相信散修",
                "winReward": 1,
            },
        ],
        [
            {"rewardGroupId": 1, "reward": ["Item|16000011_5", "Item|17003_1"]},
            {"rewardGroupId": 2, "reward": ["Item|17003_1", "Item|19070174_5"]},
        ],
        [],
        [
            {"id": 16000011, "name": "悟技符"},
            {"id": 17003, "name": "琉璃玄铁"},
            {"id": 19070174, "name": "珍品丹药宝匣"},
        ],
    )


def test_lilian_catalog_joins_choices_rewards_and_repairs_shipped_group_typo() -> None:
    catalog = _catalog()

    assert catalog["complete"] is True
    assert catalog["event_count"] == 2
    assert catalog["choice_count"] == 4
    assert [event["event_group_id"] for event in catalog["events"]] == [140004, 140005]
    hero = catalog["events"][1]
    assert hero["name"] == "英雄救美"
    assert hero["preferred_choice_ids"] == [14000503]
    assert hero["choices"][1]["win_rewards"][0]["item_name"] == "悟技符"


def test_lilian_catalog_matches_ocr_prompt_and_visible_answer_position() -> None:
    match = select_lilian_catalog_choice(
        _catalog(),
        "事件：英雄救美",
        ["相信女修", "相信散修"],
    )

    assert match is not None
    assert match["observed_text"] == "相信散修"
    assert match["observed_position"] == 1
    assert match["choice"]["preferred_reward_items"][0]["item_name"] == "悟技符"


def test_lilian_catalog_matches_event_title_with_runtime_prefix() -> None:
    match = match_lilian_catalog_event(_catalog(), "当前事件:英雄救美")

    assert match is not None
    assert match["id"] == 140005
    assert match["name"] == "英雄救美"
