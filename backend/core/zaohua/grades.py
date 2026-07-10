from __future__ import annotations

from typing import Any


# TbGradeCfg rows 301-312 define weight/color 1-12. The outline hex values come
# from PackTipContrast.GetOutlineColor in Assembly-CSharp.dll (build 24123658).
GRADE_DEFINITIONS: dict[int, dict[str, Any]] = {
    301: {"name": "一阶下品", "order": 1, "color_index": 1, "color_hex": "#757575"},
    302: {"name": "一阶中品", "order": 2, "color_index": 2, "color_hex": "#76604e"},
    303: {"name": "一阶上品", "order": 3, "color_index": 3, "color_hex": "#0f7b0d"},
    304: {"name": "二阶下品", "order": 4, "color_index": 4, "color_hex": "#0d727b"},
    305: {"name": "二阶中品", "order": 5, "color_index": 5, "color_hex": "#045d95"},
    306: {"name": "二阶上品", "order": 6, "color_index": 6, "color_hex": "#435591"},
    307: {"name": "三阶下品", "order": 7, "color_index": 7, "color_hex": "#6a0495"},
    308: {"name": "三阶中品", "order": 8, "color_index": 8, "color_hex": "#8b770b"},
    309: {"name": "三阶上品", "order": 9, "color_index": 9, "color_hex": "#954b04"},
    310: {"name": "四阶下品", "order": 10, "color_index": 10, "color_hex": "#8b290b"},
    311: {"name": "四阶中品", "order": 11, "color_index": 11, "color_hex": "#8b0b3e"},
    312: {"name": "四阶上品", "order": 12, "color_index": 12, "color_hex": "#8b0b0b"},
    313: {"name": "五阶下品", "order": 13, "color_index": 12, "color_hex": "#8b0b0b"},
    314: {"name": "五阶中品", "order": 14, "color_index": 12, "color_hex": "#8b0b0b"},
    315: {"name": "五阶上品", "order": 15, "color_index": 12, "color_hex": "#8b0b0b"},
}

# CommonConst.CraftingDrugDefaultCostTimeList indexed by TbGradeCfg.weight.
CRAFTING_DRUG_COST_DAYS_BY_GRADE_ORDER = {
    1: 3,
    2: 5,
    3: 10,
    4: 30,
    5: 60,
    6: 120,
    7: 360,
    8: 720,
    9: 1080,
    10: 1800,
    11: 3600,
    12: 7200,
}


def get_crafting_drug_cost_days(grade_id: Any, grade_name: Any = "") -> int:
    grade_order = int(get_grade_visual(grade_id, grade_name)["order"])
    return CRAFTING_DRUG_COST_DAYS_BY_GRADE_ORDER.get(grade_order, 3)


def get_grade_visual(grade_id: Any, grade_name: Any = "") -> dict[str, Any]:
    try:
        normalized_id = int(grade_id or 0)
    except (TypeError, ValueError):
        normalized_id = 0
    definition = GRADE_DEFINITIONS.get(normalized_id)
    if definition is not None:
        return dict(definition)
    return {
        "name": str(grade_name or "未命名"),
        "order": 10_000 + normalized_id,
        "color_index": 0,
        "color_hex": "#757575",
    }
