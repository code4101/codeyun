from __future__ import annotations

from datetime import date, timedelta
from typing import Any


CROSS_SIZE = 64
ANCHOR_REGION_NAME = "天澜圣殿"
ANCHOR_REGION_START_DATE = "2025-02-27"
SERVER_ID_ANCHOR_REGION_NAME = "天澜圣殿"
SERVER_ID_ANCHOR_SERVER_NAME = "岁序更替"
SERVER_ID_ANCHOR_VALUE = 22077
LATE_REGION_FIRST_NAME = "古云大陆"
OBSERVED_LATE_REGION_NAME = "神驹降世"
OBSERVED_LATE_REGION_SERVER_ORDER = 7
OBSERVED_LATE_REGION_SERVER_DATE = "2026-04-27"

REGION_NAMES = [
    "七玄风云",
    "黄枫轶事",
    "禁地试炼",
    "太岳灵眼",
    "越国皇宫",
    "天南大陆",
    "星海风光",
    "妙音秘坊",
    "风兽巢穴",
    "古修遗址",
    "无名荒岛",
    "天一石城",
    "阴冥之地",
    "无尽之海",
    "绿踪沼泽",
    "慕兰草原",
    "天澜圣殿",
    "极西之地",
    "古云大陆",
    "冥寒大陆",
    "魔金山脉",
    "大话西游",
    "一马当先",
    "神驹降世",
]

KNOWN_REGION_SERVER_NAMES: dict[str, list[str]] = {
    "妙音秘坊": [
        "千锤百炼",
        "悬壶济世",
        "远见卓识",
        "候鸟南飞",
        "意气风发",
        "逆境求生",
        "走马观花",
        "矢志不移",
        "寸草不生",
        "春风得意",
        "闻风而动",
        "一身正气",
        "梦枕新歌",
        "专注致志",
        "反求诸己",
        "视若无睹",
        "从容应对",
        "持之以恒",
        "莫测高深",
        "凭借内力",
        "修身养性",
        "甜水巷里",
        "以和为贵",
        "红尘仙境",
        "沂水弦歌",
        "知易行难",
        "鸳鸯翩跹",
        "逆流而上",
        "朱砂红藻",
        "瑰丽山河",
        "沐清海平",
        "一枕槐安",
        "瑶池仙境",
        "雁过留声",
        "碧水长流",
        "青山不老",
        "竹影婆娑",
        "银汉流照",
        "独步青云",
        "风雨如磐",
        "鹤骨松姿",
        "雪中送炭",
        "烟雨梦幻",
        "一笑莲海",
        "知音难觅",
        "纵情天地",
        "松林幽径",
        "玄黄共存",
        "翩翩起舞",
        "天穹之巅",
        "雾笼山谷",
        "雨落梧桐",
        "律转鸿钧",
        "火树银花",
        "炼玉流金",
        "稳如泰山",
        "仙鹤翩翩",
        "霜降寒露",
        "梦中仙境",
        "暮色苍茫",
        "风卷残云",
        "云深雾绕",
        "心有灵犀",
        "心念如玉",
    ],
    "天澜圣殿": [
        "云霞绚烂",
        "银装素裹",
        "九州风云",
        "稳操胜养",
        "莺啼燕语",
        "虎踞龙盘",
        "福泽天下",
        "瑞气盈门",
        "大夜弥天",
        "空谷足音",
        "无边映月",
        "雁阵高飞",
        "夜以继日",
        "星星点点",
        "春色茫茫",
        "心向往之",
        "巍巍青山",
        "小桥流水",
        "志在千里",
        "镇山静海",
        "斗转星移",
        "霞光万道",
        "枫桥夜泊",
        "帘外骤雨",
        "万事如意",
        "潮起东方",
        "神采飞扬",
        "碧血丹心",
        "光明正大",
        "时节如流",
        "鸾凤和鸣",
        "忠肝义胆",
        "彩云追月",
        "落霞孤骛",
        "夜幕深深",
        "茫茫烟波",
        "天冷气清",
        "丹心如火",
        "漫步长阳",
        "落宝八方",
        "前途无量",
        "才华出众",
        "光芒四射",
        "自由飞翔",
        "丝丝入扣",
        "听风起雨",
        "纵横捭阖",
        "花容月貌",
        "快步流星",
        "月白风清",
        "洞若观火",
        "海浪无声",
        "岁序更替",
        "大智若愚",
        "金相玉质",
        "春华秋实",
        "才子佳人",
        "风云人物",
        "烈日灼心",
        "金相玉振",
        "情深义重",
        "古往今来",
        "脱颖而出",
        "喜笑颜开",
    ],
    "极西之地": [
        "耳濡目染",
        "隔却山海",
        "万里晴空",
        "漫漫长路",
        "瑶草奇花",
        "长歌倚楼",
        "琼壶歌月",
        "任重道远",
        "杨柳依依",
    ],
}

SERVER_MARKS = {
    ("妙音秘坊", "梦枕新歌"): {"mark_type": "past", "mark_label": "曾玩", "mark_title": "以前短暂玩过的区服"},
    ("天澜圣殿", "岁序更替"): {"mark_type": "current", "mark_label": "当前", "mark_title": "当前在玩的区服"},
}


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _add_days(value: str, days: int) -> str:
    return (_parse_date(value) + timedelta(days=days)).isoformat()


def _region_start_date(region_index: int) -> str:
    anchor_index = REGION_NAMES.index(ANCHOR_REGION_NAME)
    late_first_index = REGION_NAMES.index(LATE_REGION_FIRST_NAME)
    observed_late_index = REGION_NAMES.index(OBSERVED_LATE_REGION_NAME)
    observed_late_start_date = _add_days(
        OBSERVED_LATE_REGION_SERVER_DATE,
        1 - OBSERVED_LATE_REGION_SERVER_ORDER,
    )
    if region_index >= late_first_index:
        return _add_days(observed_late_start_date, (region_index - observed_late_index) * CROSS_SIZE)
    return _add_days(ANCHOR_REGION_START_DATE, (region_index - anchor_index) * CROSS_SIZE)


def resolve_fanxiu_region_server_by_id(value: Any) -> dict[str, Any]:
    try:
        server_id = int(value)
    except (TypeError, ValueError):
        return {
            "server_id": value,
            "region_number": None,
            "region_name": "",
            "server_order": None,
            "server_name": "",
            "global_order": None,
            "known": False,
            "source": "server_id_formula",
        }

    anchor_region_index = REGION_NAMES.index(SERVER_ID_ANCHOR_REGION_NAME)
    anchor_server_order = KNOWN_REGION_SERVER_NAMES[SERVER_ID_ANCHOR_REGION_NAME].index(SERVER_ID_ANCHOR_SERVER_NAME) + 1
    anchor_global_order = anchor_region_index * CROSS_SIZE + anchor_server_order
    global_order = anchor_global_order + server_id - SERVER_ID_ANCHOR_VALUE
    region_index = (global_order - 1) // CROSS_SIZE
    server_order = (global_order - 1) % CROSS_SIZE + 1
    if region_index < 0 or region_index >= len(REGION_NAMES):
        return {
            "server_id": server_id,
            "region_number": None,
            "region_name": "",
            "server_order": server_order,
            "server_name": "",
            "global_order": global_order,
            "known": False,
            "source": "server_id_formula",
        }

    region_name = REGION_NAMES[region_index]
    server_names = KNOWN_REGION_SERVER_NAMES.get(region_name, [])
    server_name = server_names[server_order - 1] if 0 <= server_order - 1 < len(server_names) else ""
    return {
        "server_id": server_id,
        "region_number": region_index + 1,
        "region_name": region_name,
        "server_order": server_order,
        "server_name": server_name,
        "global_order": global_order,
        "known": bool(server_name),
        "source": "server_id_formula",
    }
