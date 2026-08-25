from backend.core.fanxiu.instrumentation import gongfa_detail


def test_usage_detail_resolves_exact_runtime_effect(monkeypatch):
    monkeypatch.setattr(
        gongfa_detail,
        "_render_effect_rows",
        lambda *_args: (
            {
                "effect_id": 100,
                "section": "main_effect",
                "plain_text": "主效果",
                "rich_text": "<color=#864c00>主效果</color>",
            },
            {
                "effect_id": 200,
                "section": "side_effect",
                "plain_text": "副效果",
                "rich_text": "<color=#2a4b10>副效果</color>",
            },
        ),
    )

    detail = gongfa_detail._project_usage(
        {"book_id": 10, "star": 3, "jie": 20, "pin": 2},
        {
            "category": "gongfa",
            "slot": 2,
            "equipped_name": "春解甲",
            "role": "side",
            "effect_id": 200,
        },
    )

    assert detail["location_name"] == "春解甲"
    assert detail["role_name"] == "副书"
    assert detail["effect_text"] == "副效果"
    assert detail["effect_rich_text"] == "<color=#2a4b10>副效果</color>"


def test_book_detail_groups_xianyuan_and_exchange_channels(monkeypatch):
    monkeypatch.setattr(
        gongfa_detail,
        "load_inventory_hall_snapshot",
        lambda _session, key: {
            "runtime_complete": True,
            "people": [
                {
                    "npc_id": 7,
                    "name": "南宫婉",
                    "rewards": [
                        {"item_id": 11, "name": "功法自选匣", "level": 12, "count": 1},
                        {"item_id": 13, "name": "通玄玉简", "level": 20, "count": 2},
                    ],
                }
            ],
        } if key == "xianyuan_atlas" else None,
    )
    monkeypatch.setattr(
        gongfa_detail,
        "_target_support_by_item",
        lambda _book: {
            11: {"kind": "融合", "mode": "自选"},
            12: {"kind": "悟境", "mode": "兑换"},
            13: {"kind": "通玄", "mode": "直接"},
        },
    )
    monkeypatch.setattr(
        gongfa_detail,
        "_item_index",
        lambda: {
            11: {"name_plain": "功法自选匣"},
            12: {"name_plain": "悟境残页", "descript_plain": "真悟阁兑换"},
            13: {"name_plain": "通玄玉简"},
        },
    )

    detail = gongfa_detail.build_gongfa_book_detail(
        object(),
        {"book_id": 10, "quality_grade_name": "仙品"},
    )

    assert [(row["kind"], row["source"], row["title"]) for row in detail["acquisition_channels"]] == [
        ("融合", "仙缘送礼", "南宫婉"),
        ("悟境", "仙市·真悟阁", "悟境残页"),
        ("通玄", "仙缘送礼", "南宫婉"),
    ]


def test_same_usage_merges_multiple_xian_effects(monkeypatch):
    monkeypatch.setattr(
        gongfa_detail,
        "_render_effect_rows",
        lambda *_args: (
            {"effect_id": 101, "section": "main_effect", "plain_text": "效果甲"},
            {"effect_id": 102, "section": "side_effect", "plain_text": "效果乙"},
        ),
    )
    book = {
        "book_id": 10,
        "upgrade_usages": [
            {"category": "gongfa", "slot": 1, "equipped_name": "甲元浩", "role": "xian", "effect_id": 101},
            {"category": "gongfa", "slot": 1, "equipped_name": "甲元浩", "role": "xian", "effect_id": 102},
        ],
    }

    usages = gongfa_detail._book_usages(book)

    assert len(usages) == 1
    assert usages[0]["effect_ids"] == [101, 102]
    assert usages[0]["effect_text"] == "效果甲\n效果乙"
