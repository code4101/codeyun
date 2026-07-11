from __future__ import annotations

from sqlmodel import SQLModel, Session, create_engine

from backend.api.zaohua import (
    get_zaohua_alchemy_meta,
    get_zaohua_alchemy_recipe,
    get_zaohua_herb,
    get_zaohua_herb_meta,
    list_zaohua_alchemy_recipes,
    list_zaohua_herbs,
)
from backend.core.zaohua.catalog import (
    get_zaohua_icon_path,
    sync_zaohua_catalog_to_database,
    sync_zaohua_herb_catalog_to_database,
)


def _catalog() -> dict:
    return {
        "source": {"steam_build_id": "24123658"},
        "recipes": [
            {
                "recipe_id": 1,
                "name": "凝血丸丹方",
                "technique": "底部有木系灵草，成丹数+1",
                "output_count": 4,
                "output": {
                    "item_id": 50008,
                    "name": "凝血丸",
                    "grade_id": 301,
                    "grade_name": "一品",
                    "icon_path": "Item/drug/50008",
                    "price": 30,
                    "description": "治疗内出血。",
                    "effect_description": "恢复30点气血",
                    "use_effect": "UpdateHp,30",
                    "add_drug_tolerance": 1,
                    "drug_max": 20,
                },
                "attr_limits": [{"element": "wood", "label": "木", "value": 4}],
                "example_items": [{"item_id": 100015, "name": "木灵草", "count": 2, "icon_path": "Item/herb/100015"}],
                "state_rules": [{"state_id": 19, "name": "木系增丹"}],
                "search_text": "凝血丸丹方 凝血丸 木灵草 木系增丹",
                "source_evidence": {"assembly_type": "TbDrugRecipeCfg"},
                "content_hash": "abc",
            },
            {
                "recipe_id": 2,
                "name": "止血散丹方",
                "technique": "",
                "output_count": 6,
                "output": {
                    "item_id": 50007,
                    "name": "止血散",
                    "grade_id": 302,
                    "grade_name": "二品",
                    "icon_path": "Item/drug/50007",
                    "price": 10,
                    "description": "辅助修炼。",
                    "augment": 100,
                    "efficacy": 1,
                },
                "attr_limits": [],
                "example_items": [],
                "state_rules": [],
                "search_text": "止血散丹方 止血散",
                "source_evidence": {},
                "content_hash": "def",
            },
        ],
    }


def _herb_catalog() -> dict:
    return {
        "source": {"steam_build_id": "24123658"},
        "herbs": [
            {
                "item_id": 70001,
                "display_order": 0,
                "name": "一阶下品灵草",
                "grade_id": 301,
                "grade_name": "一阶下品",
                "element_key": "none",
                "element_name": "无",
                "search_text": "一阶下品灵草",
                "content_hash": "placeholder-herb",
            },
            {
                "item_id": 100015,
                "display_order": 1,
                "name": "回春草",
                "description": "木系灵草。",
                "effect_description": "蕴含少量灵气。",
                "icon_path": "Item/herb/100015",
                "grade_id": 301,
                "grade_name": "一阶下品",
                "element_id": 3,
                "element_key": "wood",
                "element_name": "木",
                "price": 20,
                "lingqi": 2,
                "crafting_attributes": [{"element": "wood", "label": "木", "value": 2}],
                "recipe_count": 1,
                "recipes": [{"recipe_id": 1, "output_name": "凝血丸", "required_count": 2}],
                "search_text": "回春草 木 一阶下品 凝血丸",
                "source_evidence": {"assembly_type": "TbItemCfg"},
                "content_hash": "herb-abc",
            },
            {
                "item_id": 100019,
                "display_order": 2,
                "name": "阳萤草",
                "description": "火系灵草。",
                "effect_description": "",
                "icon_path": "Item/herb/100019",
                "grade_id": 302,
                "grade_name": "一阶中品",
                "element_id": 4,
                "element_key": "fire",
                "element_name": "火",
                "price": 50,
                "lingqi": 6,
                "crafting_attributes": [{"element": "fire", "label": "火", "value": -1}],
                "recipe_count": 0,
                "recipes": [],
                "search_text": "阳萤草 火 一阶中品",
                "source_evidence": {},
                "content_hash": "herb-def",
            },
        ],
    }


def test_sync_and_query_zaohua_alchemy_database() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        result = sync_zaohua_catalog_to_database(session, _catalog())
        assert result == {"catalog_count": 2, "created": 2, "updated": 0, "deactivated": 0}
        sync_zaohua_herb_catalog_to_database(session, _herb_catalog())

        meta = get_zaohua_alchemy_meta(session=session)
        assert meta["recipe_count"] == 2
        assert meta["storage"] == "database"
        assert [item["name"] for item in meta["grades"]] == ["一品", "二品"]
        assert meta["grades"][0]["color_hex"] == "#757575"

        page = list_zaohua_alchemy_recipes(q="木灵草", page=1, page_size=20, session=session)
        assert page["total"] == 1
        assert page["items"][0]["name"] == "凝血丸丹方"
        assert page["items"][0]["example_items"][0]["name"] == "木灵草"
        assert page["items"][0]["output"]["icon_url"] == "/api/zaohua/media/icons/Item/drug/50008"
        assert page["items"][0]["output"]["grade_color_hex"] == "#757575"
        assert page["items"][0]["output"]["grade_rank"] == 1
        assert page["items"][0]["output"]["price"] == 30
        assert page["items"][0]["output"]["effect_text"] == "恢复30点气血"
        assert page["items"][0]["output"]["add_drug_tolerance"] == 1
        assert page["items"][0]["output"]["drug_max"] == 20
        assert page["items"][0]["cost_days"] == 3
        assert page["items"][0]["output"]["description"] == "治疗内出血。"
        assert page["items"][0]["example_items"][0]["icon_url"] == "/api/zaohua/media/icons/Item/herb/100015"
        assert page["items"][0]["example_items"][0]["crafting_attributes"] == [
            {"element": "wood", "label": "木", "value": 2}
        ]

        grade_page = list_zaohua_alchemy_recipes(grade="二品", page=1, page_size=20, session=session)
        assert grade_page["total"] == 1
        assert grade_page["items"][0]["output"]["name"] == "止血散"
        assert grade_page["items"][0]["output"]["effect_text"] == "修炼效率 +100%，持续 1 月"
        assert grade_page["items"][0]["cost_days"] == 5

        descending_page = list_zaohua_alchemy_recipes(
            sort_by="number", sort_order="desc", page=1, page_size=20, session=session
        )
        assert [item["recipe_id"] for item in descending_page["items"]] == [2, 1]

        grade_descending_page = list_zaohua_alchemy_recipes(
            sort_by="grade", sort_order="desc", page=1, page_size=20, session=session
        )
        assert [item["output"]["grade_rank"] for item in grade_descending_page["items"]] == [2, 1]

        detail = get_zaohua_alchemy_recipe(1, session=session)
        assert detail["source_evidence"]["assembly_type"] == "TbDrugRecipeCfg"


def test_get_zaohua_icon_path_stays_in_media_root(tmp_path, monkeypatch) -> None:
    icon_path = tmp_path / "media" / "icons" / "item" / "drug" / "50008.png"
    icon_path.parent.mkdir(parents=True)
    icon_path.write_bytes(b"png")
    monkeypatch.setenv("ZAOHUA_REVERSE_ROOT", str(tmp_path))

    assert get_zaohua_icon_path("Item/drug/50008") == icon_path


def test_sync_and_query_zaohua_herbs() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        result = sync_zaohua_herb_catalog_to_database(session, _herb_catalog())
        assert result == {"catalog_count": 3, "created": 3, "updated": 0, "deactivated": 0}

        meta = get_zaohua_herb_meta(session=session)
        assert meta["herb_count"] == 2
        assert [item["name"] for item in meta["grades"]] == ["一阶下品", "一阶中品"]
        assert [item["name"] for item in meta["elements"]] == ["木", "火"]

        all_page = list_zaohua_herbs(page=1, page_size=20, session=session)
        assert all_page["total"] == 2
        assert all(item["item_id"] != 70001 for item in all_page["items"])

        page = list_zaohua_herbs(q="凝血丸", page=1, page_size=20, session=session)
        assert page["total"] == 1
        assert page["items"][0]["name"] == "回春草"
        assert page["items"][0]["icon_url"] == "/api/zaohua/media/icons/Item/herb/100015"
        assert page["items"][0]["grade_color_hex"] == "#757575"
        assert page["items"][0]["grade_rank"] == 1
        assert page["items"][0]["crafting_attributes"] == [
            {"element": "wood", "label": "木", "value": 2}
        ]

        element_page = list_zaohua_herbs(element="fire", page=1, page_size=20, session=session)
        assert element_page["total"] == 1
        assert element_page["items"][0]["name"] == "阳萤草"

        descending_page = list_zaohua_herbs(
            sort_by="number", sort_order="desc", page=1, page_size=20, session=session
        )
        assert [item["display_order"] for item in descending_page["items"]] == [2, 1]

        grade_descending_page = list_zaohua_herbs(
            sort_by="grade", sort_order="desc", page=1, page_size=20, session=session
        )
        assert [item["grade_rank"] for item in grade_descending_page["items"]] == [2, 1]

        detail = get_zaohua_herb(100015, session=session)
        assert detail["recipes"][0]["output_name"] == "凝血丸"
        assert detail["crafting_attributes"][0]["value"] == 2
