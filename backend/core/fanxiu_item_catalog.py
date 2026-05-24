from __future__ import annotations

import csv
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.core.fanxiu_gongfa_catalog import (
    DEFAULT_FAZE_EFFECT_RESOURCE_ROWS,
    DEFAULT_FAZE_RESOURCE_ROWS,
    PROGRESSION_TABLES,
    _compact_progression_row,
    _progression_sort_key,
)
from backend.core.fanxiu_resources import FanxiuResourceError, resolve_fanxiu_export_root
from backend.core.fanxiu_timeline import (
    TIMELINE_SOURCE_ROWS,
    build_timeline_context,
    card_timeline_sort_value,
    first_timeline_hint,
)
from backend.core.fanxiu_lua_config import parse_fanxiu_generated_lua_config


DEFAULT_ITEM_ROWS = Path("parsed_configs/Item/rows.json")
DEFAULT_QUALITY_ROWS = Path("parsed_configs/Quality/rows.json")
DEFAULT_ITEM_CATALOG = Path("parsed_configs/item_catalog/item_catalog.json")
DEFAULT_GONGFA_FEATURE_LINKS = Path("parsed_configs/gongfa_feature_probe/feature_links.tsv")
ITEM_CATALOG_SCHEMA_VERSION = 10
_WHITESPACE_RE = re.compile(r"\s+")
_BRACKET_TERM_RE = re.compile(r"【([^】]{1,30})】")
_UNKNOWN_ITEM_CATEGORY_KEY = "__missing__"

ITEM_TYPE_LABELS: dict[str, str] = {
    "0": "铜钱",
    "1": "特殊道具",
    "2": "礼包宝匣",
    "3": "功法",
    "4": "碎片",
    "5": "材料",
    "6": "丹方",
    "7": "灵蕴",
    "8": "时装",
    "9": "货币",
    "11": "修真心得",
    "12": "功法经验",
    "13": "丹药",
    "14": "药品",
    "15": "称号",
    "16": "副本凭证",
    "17": "首领次数药",
    "18": "六相石",
    "19": "游历体力",
    "20": "NPC礼物",
    "21": "自选匣",
    "22": "战斗丹药",
    "23": "法则",
    "24": "任务道具",
    "25": "潜修道具",
    "26": "NPC挑战",
    "27": "内丹",
    "28": "灵兽养成",
    "30": "魔道入侵",
    "31": "魔道增益",
    "32": "VIP经验",
    "33": "仙盟争夺",
    "34": "活动增益",
    "35": "虚天次数",
    "36": "虚天探查",
    "37": "论道道具",
    "38": "灵兽道具",
    "39": "兽渊次数",
    "40": "兽渊探查",
    "41": "命魂",
    "42": "飞升转换",
    "43": "阁位邀请",
    "44": "虚天高级探查",
    "45": "红包",
    "46": "云梦论剑",
    "47": "三界气",
    "48": "仙环气铠",
    "50": "采集材料",
    "51": "新礼包宝匣",
    "52": "元气丹",
    "53": "仙盟道具",
    "54": "特殊时装",
    "55": "神兵部件",
    "56": "仙盟分身",
    "57": "顿悟手记",
    "58": "闻道符",
    "60": "显灵石",
    "62": "封魔杀",
    "63": "切磋令",
    "64": "聚灵道具",
    "65": "仙侣历练",
    "66": "洞天试炼",
    "67": "阵营次数",
    "68": "阵营分身",
    "69": "宝石自选",
    "70": "微槽",
    "71": "限时盒",
    "72": "仙侣心得",
    "73": "跑商体力",
    "74": "神通修为",
    "75": "誓约",
    "77": "仙侣修为",
    "79": "仙缘道具",
    "80": "云梦小道具",
    "81": "神印",
    "82": "仙侣塔",
    "83": "剑纹",
    "84": "自选抽奖",
    "85": "兽渊特殊符",
    "86": "异火",
    "87": "虚天增益",
    "88": "聚灵收益",
    "89": "云梦速战",
    "90": "炎域",
    "91": "炎域建筑",
    "95": "星海探秘",
    "96": "魂晶",
    "97": "兽渊寻宝",
    "98": "镇物",
    "99": "道丹",
    "100": "幸运道具",
    "102": "玄荒令牌",
    "103": "药灵升级",
    "104": "炼体",
    "105": "魔道场景",
    "106": "兑换道具",
    "107": "称号赞助",
    "126": "弃用道具",
    "128": "镇物养成",
    "999": "特殊资源",
}

ITEM_TYPE_ENUM_SYMBOLS: dict[str, str] = {
    "0": "Coins",
    "1": "CONSUMABLE",
    "2": "PANDORA",
    "3": "GONGFA_BOOK",
    "4": "DEBRIS",
    "5": "MATERIAL",
    "6": "FORMULA",
    "7": "PELLET",
    "8": "FASHION",
    "9": "CURRENCY",
    "11": "EXP_ITEM",
    "12": "GONGFA_EXP_ITEM",
    "13": "PILL",
    "14": "MEDICINE",
    "15": "TITLE_TYPE",
    "16": "DUNGEON_BOOK",
    "17": "BOSS_TIMES_POTION",
    "18": "CHARACTER_POTION",
    "19": "TravelPower",
    "20": "NPC_GIFT",
    "21": "OPTIONAL_GIFT",
    "22": "Type_22",
    "23": "Type_23",
    "24": "TaskItem",
    "25": "PracticeItem",
    "26": "NpcChallengeItem",
    "27": "BeastItem",
    "28": "PetUpItem",
    "30": "MagicInvadeItem",
    "31": "MagicInvadeAddItem",
    "32": "VipExpItem",
    "33": "AlliancePCItem",
    "34": "AllianceBuffItem",
    "35": "HeavenAddCountItem",
    "36": "HeavenDedectItem",
    "37": "LunDaoItem",
    "38": "LingShouItem",
    "39": "BeastexplodeAddCountItem",
    "40": "BeastexplodeDedectItem",
    "41": "HpSoulItem",
    "42": "AscensionChangeItem",
    "43": "GsItem",
    "44": "HeavenHeightDetect",
    "45": "RedBagItem",
    "46": "YunMengPkItem",
    "47": "CeleDemonItem",
    "48": "EquipItem",
    "50": "MaterialscollectItem",
    "51": "PANDORA_NEW",
    "54": "Fashion_Special_Show",
    "55": "SpiritWarePart",
    "56": "LandContendSuperMirror",
    "57": "CreateSkill",
    "64": "VenisItem",
    "65": "PartnerTravel",
    "66": "ImmHoleWareTrial",
    "67": "CampContendAddCountItem",
    "68": "CampContendSuperMirror",
    "69": "GemOptionalGift",
    "70": "Microgroove",
    "71": "LimitBox",
    "72": "PartnerExp",
    "74": "Partner_Skill_Exp",
    "75": "Partner_Oath",
    "77": "Partner_Rexp",
    "80": "YunMengPkMiniItem",
    "81": "GodWrath",
    "82": "Xianlvtower",
    "83": "SwordSpiritPart",
    "85": "BeastexplodeEyesItem",
    "86": "ExoticFlameItem",
    "87": "HeavenBuffItem87",
    "88": "VenisEarningsItem",
    "89": "YunMengQuickItem",
    "90": "Flamesquare",
    "91": "FlamesquareBuild",
    "96": "BeastSpiritJade",
    "97": "BeastExplodeFindItem",
    "99": "BottleQuickUpgradeItem",
    "100": "LuckyItem",
    "103": "MedicalElfUpLevel",
    "104": "PhysicalExercise",
    "105": "MagicinvadeSceneItem",
    "106": "ExchangeItem",
    "107": "TitleSponsorshipItem",
    "999": "CannotUseItem",
}

ITEM_SUB_TYPE_LABELS: dict[tuple[str, str], str] = {
    ("1", "41"): "优惠券",
    ("1", "47"): "仙侣碎片",
    ("1", "66"): "邀约函",
    ("1", "94"): "仙侣技能",
    ("2", "1"): "礼包",
    ("2", "2"): "随机匣",
    ("3", "8"): "功法",
    ("3", "9"): "炼体秘术",
    ("5", "6"): "法宝",
    ("5", "7"): "残篇残简",
    ("5", "37"): "契约",
    ("5", "53"): "玄鹤道具",
    ("5", "74"): "仙侣玉",
    ("5", "97"): "道器",
    ("8", "6"): "外观",
    ("21", "21"): "自选匣",
    ("48", "29"): "仙环气铠",
    ("48", "38"): "灵环",
    ("55", "34"): "神兵部件",
    ("86", "6"): "异火",
    ("98", "99"): "镇物部位",
    ("999", "33"): "真悟手记",
    ("999", "107"): "月卡优惠券",
}

BACKPACK_SUB_TYPE_LABELS: dict[str, str] = {
    "1": "通用",
    "2": "经验",
    "3": "功法经验",
    "6": "仙环",
    "7": "碎片",
    "8": "功法",
    "9": "炼体秘术",
    "14": "麒麟",
    "16": "NPC礼物",
    "17": "游历体力",
    "22": "付费道具",
    "23": "VIP",
    "24": "职业",
    "25": "法相碎片",
    "26": "性别",
    "27": "仙",
    "28": "魔",
    "29": "装备",
    "30": "装备材料",
    "31": "龙舟",
    "32": "成员",
    "33": "功法升品",
    "34": "神兵部件",
    "35": "仙环材料",
    "36": "时装",
    "37": "灵物",
    "38": "宝石",
    "39": "花灯",
    "40": "砍树",
    "41": "充值折扣",
    "42": "砍树元素",
    "43": "百族战旗",
    "44": "装备升品",
    "45": "二阶灵纹盒",
    "46": "三阶灵纹盒",
    "47": "仙侣",
    "48": "仙侣历练",
    "49": "劳动节",
    "50": "周年庆砍树",
    "53": "核心法宝",
    "54": "核心法宝经验",
    "66": "仙侣历练类型",
    "67": "弟子战力",
    "68": "仙侣历练体力",
    "69": "仙侣道具",
    "70": "结缘",
    "71": "功法升品2",
    "72": "成员装备",
    "73": "仙侣特殊历练",
    "74": "仙侣玉",
    "75": "仙侣玉经验",
    "76": "仙侣武器",
    "77": "仙侣历练额外",
    "79": "仙侣异变",
    "80": "NPC祈福",
    "83": "剑灵部件",
    "84": "剑灵命格",
    "85": "灵兽觉醒",
    "86": "玄泽探索经验",
    "87": "通玄升品",
    "88": "剑灵升品",
    "89": "斗破砍树",
    "90": "南宫历练",
    "91": "灵蜕",
    "92": "秘渠",
    "93": "巅峰红包",
    "94": "仙侣技能",
    "96": "魂晶",
    "97": "能量宝物",
    "98": "锁魂",
    "102": "药灵进阶",
    "104": "炼体",
    "105": "战令折扣",
    "106": "转盘",
    "107": "月卡折扣",
}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_export_file(path: str | Path | None, default: Path, *, export_root: str | Path | None = None) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    raw_path = Path(path) if path else default
    resolved = raw_path.expanduser().resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    if not _is_relative_to(resolved, root):
        raise FanxiuResourceError(f"文件必须位于导出根目录内：{root}")
    if not resolved.is_file():
        raise FanxiuResourceError(f"文件不存在：{resolved}")
    return resolved


def _load_json_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise FanxiuResourceError(f"JSON 文件不是行列表：{path}")
    return [item for item in data if isinstance(item, dict)]


def _load_optional_json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return _load_json_rows(path)


def _find_optional_gift_lua(root: Path) -> Path | None:
    candidates = [
        path
        for path in root.glob("by_source/lscripts/generate/cfg/item_*/text_assets/OptionalGift.lua")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return None


def _text_value(row: dict[str, Any], field: str) -> str:
    value = row.get(f"{field}_plain")
    if value is None or value == "":
        value = row.get(field)
    return "" if value is None else str(value)


def _linked_item_from_row(row: dict[str, Any], count: Any = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": row.get("id") or row.get("_row_key"),
        "name": _text_value(row, "name"),
        "icon": row.get("icon"),
        "small_icon": row.get("smallIcon") or row.get("small_icon"),
        "quality": row.get("quality"),
        "description": _text_value(row, "descript"),
    }
    if count not in (None, ""):
        item["count"] = count
    return item


def _preview(value: Any, limit: int = 180) -> str:
    text = _WHITESPACE_RE.sub(" ", str(value or "")).strip()
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _normalize_search_text(value: Any) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip().lower()


def _extract_terms(*values: Any, limit: int = 8) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        for match in _BRACKET_TERM_RE.finditer(str(value or "")):
            term = match.group(1).strip()
            if not term or term in seen:
                continue
            seen.add(term)
            terms.append(term)
            if len(terms) >= limit:
                return terms
    return terms


def _sort_value(value: Any, fallback: int = 10**12) -> int:
    parsed = _as_int(value)
    return parsed if parsed is not None else fallback


def _compact_quality_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "quality_name": _text_value(row, "name"),
        "quality_color": row.get("color"),
        "quality_tab": _text_value(row, "tab"),
        "quality_icon": row.get("squareBg") or row.get("circleBg") or row.get("skillBg"),
    }


def _item_category_key(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return text if text else _UNKNOWN_ITEM_CATEGORY_KEY


def _item_category_sort_value(value: Any) -> int:
    key = str(value or "").strip()
    if key == _UNKNOWN_ITEM_CATEGORY_KEY:
        return 10**12
    return _sort_value(key)


def _item_type_label(type_key: str) -> str:
    if type_key == _UNKNOWN_ITEM_CATEGORY_KEY:
        return "类型未知"
    return ITEM_TYPE_LABELS.get(type_key) or ITEM_TYPE_ENUM_SYMBOLS.get(type_key) or f"类型 {type_key}"


def _item_sub_type_label(type_key: str, sub_type_key: str) -> str:
    if sub_type_key == _UNKNOWN_ITEM_CATEGORY_KEY:
        return "子类未知"
    return ITEM_SUB_TYPE_LABELS.get((type_key, sub_type_key)) or BACKPACK_SUB_TYPE_LABELS.get(sub_type_key) or f"子类 {sub_type_key}"


def _item_sub_type_pair_key(type_key: str, sub_type_key: str) -> str:
    return f"{type_key}:{sub_type_key}"


def _item_type_meta(row: dict[str, Any]) -> dict[str, str]:
    type_key = _item_category_key(row.get("type"))
    sub_type_key = _item_category_key(row.get("subType"))
    type_name = _item_type_label(type_key)
    sub_type_name = _item_sub_type_label(type_key, sub_type_key)
    return {
        "type_key": type_key,
        "type_name": type_name,
        "sub_type_key": _item_sub_type_pair_key(type_key, sub_type_key),
        "sub_type_raw_key": sub_type_key,
        "sub_type_name": sub_type_name,
        "type_sub_type_name": f"{type_name} · {sub_type_name}",
    }


def _optional_gift_group_id(effect_value: Any) -> str:
    text = "" if effect_value is None else str(effect_value).strip()
    if not text:
        return ""
    parts = [part.strip() for part in text.split("_") if part.strip()]
    if len(parts) >= 2 and _as_int(parts[1]) is not None:
        return parts[1]
    if len(parts) == 1 and _as_int(parts[0]) is not None:
        return parts[0]
    return ""


def _build_optional_gift_rewards_by_group(root: Path, item_rows: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    optional_gift_path = _find_optional_gift_lua(root)
    if optional_gift_path is None:
        return {}, {"optional_gift_row_count": 0, "optional_gift_source": ""}
    parsed = parse_fanxiu_generated_lua_config(optional_gift_path)
    item_by_id = {
        str(item_id): row
        for row in item_rows
        if (item_id := row.get("id") or row.get("_row_key")) not in (None, "")
    }
    rewards_by_group: dict[str, list[dict[str, Any]]] = {}
    for row in parsed.get("rows") or []:
        if not isinstance(row, dict):
            continue
        group_id = str(row.get("groupID") or "").strip()
        gift_id = str(row.get("giftID") or "").strip()
        if not group_id or not gift_id:
            continue
        item_row = item_by_id.get(gift_id)
        reward = _linked_item_from_row(item_row or {"id": gift_id, "name": gift_id}, row.get("number"))
        reward.update(
            {
                "optional_gift_row_id": row.get("id") or row.get("_row_key"),
                "limit_number": row.get("limitNumber"),
                "show_sort": row.get("showSort"),
                "show_condition": row.get("showCondition"),
            }
        )
        rewards_by_group.setdefault(group_id, []).append(reward)
    for rewards in rewards_by_group.values():
        rewards.sort(
            key=lambda item: (
                _sort_value(item.get("show_sort"), 10**12),
                -_sort_value(item.get("quality"), 0),
                _sort_value(item.get("id")),
            )
        )
    return rewards_by_group, {
        "optional_gift_row_count": len(parsed.get("rows") or []),
        "optional_gift_source": str(optional_gift_path),
    }


def _compact_item_row(
    row: dict[str, Any],
    quality_by_id: dict[int, dict[str, Any]],
    progression_counts_by_gid: dict[int, dict[str, int]] | None = None,
    time_hints_by_id: dict[str, list[dict[str, Any]]] | None = None,
    optional_gift_rewards_by_group: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    quality = row.get("quality")
    effect_text = _text_value(row, "effDescript")
    effect_value = row.get("effectValue")
    effect_gid = _as_int(effect_value)
    progression_counts = dict((progression_counts_by_gid or {}).get(effect_gid or -1, {}))
    optional_gift_group_id = _optional_gift_group_id(effect_value)
    optional_gift_rewards = list((optional_gift_rewards_by_group or {}).get(optional_gift_group_id) or [])
    card = {
        "id": row.get("id") or row.get("_row_key"),
        "name": _text_value(row, "name"),
        "quality": quality,
        "icon": row.get("icon"),
        "small_icon": row.get("smallIcon"),
        "description": _text_value(row, "descript"),
        "effect_description": effect_text,
        "show_effect": _text_value(row, "showEffect"),
        "type": row.get("type"),
        "sub_type": row.get("subType"),
        "overlay": row.get("overlay"),
        "backpack": row.get("backpack"),
        "effect_value": effect_value,
        "can_use": row.get("canUse"),
        "sort": row.get("sort"),
        "progression_counts": progression_counts,
        "source_row_key": row.get("_row_key"),
    }
    if optional_gift_rewards:
        card["optional_gift_group_id"] = optional_gift_group_id
        card["optional_gift_rewards"] = optional_gift_rewards
    card.update(_item_type_meta(row))
    card.update(quality_by_id.get(_as_int(quality) or -1, {}))
    time_hints = (time_hints_by_id or {}).get(str(card["id"]))
    if time_hints:
        card["time_hints"] = time_hints
        card["first_time_hint"] = first_timeline_hint(time_hints)
    return card


def _item_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    return (
        _sort_value(row.get("type")),
        _sort_value(row.get("sort"), _sort_value(row.get("id"))),
        str(row.get("id") or row.get("_row_key") or ""),
    )


def _default_catalog_source_files(root: Path) -> list[Path]:
    optional_gift_path = _find_optional_gift_lua(root)
    return [
        root / DEFAULT_ITEM_ROWS,
        root / DEFAULT_QUALITY_ROWS,
        *([optional_gift_path] if optional_gift_path else []),
        root / DEFAULT_FAZE_RESOURCE_ROWS,
        root / DEFAULT_FAZE_EFFECT_RESOURCE_ROWS,
        *(root / relative_path for relative_path in PROGRESSION_TABLES.values()),
        *(root / relative_path for relative_path in TIMELINE_SOURCE_ROWS),
    ]


def _is_default_catalog_stale(catalog_path: Path, root: Path) -> bool:
    if not catalog_path.is_file():
        return True
    try:
        with catalog_path.open("r", encoding="utf-8") as file:
            header = file.read(4096)
    except OSError:
        return True
    match = re.search(r'"schema_version"\s*:\s*(\d+)', header)
    if not match or int(match.group(1)) != ITEM_CATALOG_SCHEMA_VERSION:
        return True
    catalog_mtime_ns = catalog_path.stat().st_mtime_ns
    return any(
        source_path.is_file() and source_path.stat().st_mtime_ns > catalog_mtime_ns
        for source_path in _default_catalog_source_files(root)
    )


def _resolve_catalog_file(export_root: str | Path | None = None, *, rebuild_missing: bool = True) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    path = (root / DEFAULT_ITEM_CATALOG).resolve()
    if not _is_relative_to(path, root):
        raise FanxiuResourceError(f"文件必须位于导出根目录内：{root}")
    if rebuild_missing and _is_default_catalog_stale(path, root):
        build_fanxiu_item_catalog(export_root=export_root)
    if not path.is_file():
        raise FanxiuResourceError(f"道具目录不存在，请先生成：{path}")
    return path


@lru_cache(maxsize=4)
def _load_item_catalog_cached(path_text: str, mtime_ns: int, size: int, export_root_text: str) -> dict[str, Any]:
    catalog_path = Path(path_text)
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
        raise FanxiuResourceError(f"道具目录格式不正确：{catalog_path}")
    return {
        "export_root": export_root_text,
        "catalog_path": str(catalog_path),
        **data,
    }


def load_fanxiu_item_catalog(
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    catalog_path = _resolve_catalog_file(export_root, rebuild_missing=rebuild_missing)
    root = resolve_fanxiu_export_root(export_root)
    stat = catalog_path.stat()
    return _load_item_catalog_cached(str(catalog_path), stat.st_mtime_ns, stat.st_size, str(root))


def _build_item_search_doc(card: dict[str, Any], index: int) -> dict[str, Any]:
    item_id = _normalize_search_text(card.get("id"))
    name = _normalize_search_text(card.get("name"))
    icon = _normalize_search_text(card.get("icon"))
    description = _normalize_search_text(card.get("description"))
    effect_description = _normalize_search_text(card.get("effect_description"))
    quality_texts = tuple(
        _normalize_search_text(value)
        for value in (
            card.get("quality"),
            card.get("quality_name"),
            card.get("quality_tab"),
        )
    )
    type_texts = tuple(
        _normalize_search_text(value)
        for value in (
            card.get("type"),
            card.get("type_name"),
            card.get("type_key"),
            card.get("sub_type"),
            card.get("sub_type_name"),
            card.get("sub_type_raw_key"),
            card.get("effect_value"),
            card.get("backpack"),
        )
    )
    progression_text = _normalize_search_text(
        " ".join(
            " ".join(
                str(row.get(field) or "")
                for field in ("name", "title", "describe", "top_describe", "down_describe", "upgrade_desc", "attr", "attributes")
            )
            for rows in (card.get("progression") or {}).values()
            if isinstance(rows, list)
            for row in rows
            if isinstance(row, dict)
        )
    )
    optional_gift_text = _normalize_search_text(
        " ".join(
            " ".join(str(reward.get(field) or "") for field in ("id", "name", "description", "count"))
            for reward in card.get("optional_gift_rewards") or []
            if isinstance(reward, dict)
        )
    )
    return {
        "index": index,
        "card": card,
        "item_id": item_id,
        "name": name,
        "icon": icon,
        "description": description,
        "effect_description": effect_description,
        "progression_text": progression_text,
        "optional_gift_text": optional_gift_text,
        "quality_values": _item_quality_filter_values(card),
        "type_values": tuple(str(value or "").strip() for value in (card.get("type_key"), card.get("type"), card.get("type_name"))),
        "sub_type_values": tuple(
            str(value or "").strip()
            for value in (card.get("sub_type_key"), card.get("sub_type_raw_key"), card.get("sub_type"), card.get("sub_type_name"))
        ),
        "quality_texts": quality_texts,
        "type_texts": type_texts,
        "combined": " ".join([item_id, name, icon, description, effect_description, progression_text, optional_gift_text, *quality_texts, *type_texts]),
    }


@lru_cache(maxsize=4)
def _load_item_runtime_index_cached(path_text: str, mtime_ns: int, size: int, export_root_text: str) -> dict[str, Any]:
    catalog = _load_item_catalog_cached(path_text, mtime_ns, size, export_root_text)
    cards = [card for card in catalog.get("cards") or [] if isinstance(card, dict)]
    cards_by_id = {str(card.get("id")): card for card in cards if card.get("id") not in (None, "")}
    return {
        "catalog": catalog,
        "cards_by_id": cards_by_id,
        "quality_options": _build_item_quality_options(cards),
        "type_options": _build_item_type_options(cards),
        "sub_type_options": _build_item_sub_type_options(cards),
        "search_docs": tuple(_build_item_search_doc(card, index) for index, card in enumerate(cards)),
    }


def load_fanxiu_item_runtime_index(
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    catalog_path = _resolve_catalog_file(export_root, rebuild_missing=rebuild_missing)
    root = resolve_fanxiu_export_root(export_root)
    stat = catalog_path.stat()
    return _load_item_runtime_index_cached(str(catalog_path), stat.st_mtime_ns, stat.st_size, str(root))


def _card_terms(card: dict[str, Any], *, limit: int = 8) -> list[str]:
    return _extract_terms(
        card.get("name"),
        card.get("description"),
        card.get("effect_description"),
        *(reward.get("name") for reward in card.get("optional_gift_rewards") or [] if isinstance(reward, dict)),
        limit=limit,
    )


def _format_item_search_item(card: dict[str, Any], score: int) -> dict[str, Any]:
    item = {
        "id": card.get("id"),
        "name": card.get("name") or str(card.get("id") or "未命名"),
        "quality": card.get("quality"),
        "quality_name": card.get("quality_name"),
        "quality_color": card.get("quality_color"),
        "quality_tab": card.get("quality_tab"),
        "icon": card.get("icon"),
        "small_icon": card.get("small_icon"),
        "type": card.get("type"),
        "type_key": card.get("type_key"),
        "type_name": card.get("type_name"),
        "sub_type": card.get("sub_type"),
        "sub_type_key": card.get("sub_type_key"),
        "sub_type_raw_key": card.get("sub_type_raw_key"),
        "sub_type_name": card.get("sub_type_name"),
        "type_sub_type_name": card.get("type_sub_type_name"),
        "description_preview": _preview(card.get("description"), 140),
        "effect_preview": _preview(card.get("effect_description"), 160),
        "progression_counts": card.get("progression_counts") or {},
        "terms": _card_terms(card),
        "score": score,
    }
    if card.get("first_time_hint"):
        item["first_time_hint"] = card.get("first_time_hint")
    return item


def _build_item_progression_counts_by_gid(root: Path) -> tuple[dict[int, dict[str, int]], dict[str, int]]:
    counts_by_gid: dict[int, dict[str, int]] = {}
    table_counts: dict[str, int] = {}
    for table_name, relative_path in PROGRESSION_TABLES.items():
        rows = _load_optional_json_rows(root / relative_path)
        table_counts[table_name] = len(rows)
        for row in rows:
            gid = _as_int(row.get("gid"))
            if gid is None:
                continue
            table_counts_for_gid = counts_by_gid.setdefault(gid, {})
            table_counts_for_gid[table_name] = table_counts_for_gid.get(table_name, 0) + 1
    return counts_by_gid, table_counts


def _catalog_cards_as_item_rows(cards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(card["id"]): {
            "id": card.get("id"),
            "name": card.get("name"),
            "icon": card.get("icon"),
            "smallIcon": card.get("small_icon"),
            "quality": card.get("quality"),
            "descript": card.get("description"),
        }
        for card in cards
        if card.get("id") not in (None, "")
    }


def _load_feature_links(root: Path) -> dict[tuple[str, str], dict[str, str]]:
    path = root / DEFAULT_GONGFA_FEATURE_LINKS
    if not path.is_file():
        return {}
    result: dict[tuple[str, str], dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            feature = str(row.get("feature") or "").strip()
            source_gid = str(row.get("source_gid") or "").strip()
            if not feature or not source_gid:
                continue
            result[(feature, source_gid)] = {
                "feature": feature,
                "source_gid": source_gid,
                "source_jie": str(row.get("source_jie") or "").strip(),
                "source_name": str(row.get("source_name") or "").strip(),
                "source_describe": str(row.get("source_describe") or "").strip(),
                "match_kind": str(row.get("match_kind") or "").strip(),
                "direct_match_count": str(row.get("direct_match_count") or "").strip(),
                "family_match_count": str(row.get("family_match_count") or "").strip(),
                "config_ids": str(row.get("config_ids") or "").strip(),
                "config_descriptions": str(row.get("config_descriptions") or "").strip(),
                "timelines": str(row.get("timelines") or "").strip(),
                "effect_paths": str(row.get("effect_paths") or "").strip(),
                "sound_ids": str(row.get("sound_ids") or "").strip(),
                "hit_frames": str(row.get("hit_frames") or "").strip(),
            }
    return result


def _build_item_progression_for_gid(
    root: Path,
    gid: int,
    cards: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    items_by_id = _catalog_cards_as_item_rows(cards)
    feature_links = _load_feature_links(root)
    faze_resource_rows = _load_optional_json_rows(root / DEFAULT_FAZE_RESOURCE_ROWS)
    faze_effect_rows = _load_optional_json_rows(root / DEFAULT_FAZE_EFFECT_RESOURCE_ROWS)
    faze_resource_by_id = {
        faze_id: row
        for row in faze_resource_rows
        if (faze_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    faze_effect_by_id = {
        effect_id: row
        for row in faze_effect_rows
        if (effect_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    result: dict[str, list[dict[str, Any]]] = {}
    for table_name, relative_path in PROGRESSION_TABLES.items():
        rows = _load_optional_json_rows(root / relative_path)
        compact_rows: list[dict[str, Any]] = []
        for row in sorted(rows, key=_progression_sort_key):
            row_gid = _as_int(row.get("gid"))
            if row_gid != gid:
                continue
            compact_row = _compact_progression_row(row, items_by_id, faze_resource_by_id, faze_effect_by_id)
            feature_link = feature_links.get((str(compact_row.get("feature") or "").strip(), str(gid)))
            if feature_link:
                compact_row["feature_link"] = feature_link
            compact_rows.append(compact_row)
        if compact_rows:
            result[table_name] = compact_rows
    return result


def _build_item_quality_options(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        label = _item_quality_filter_label(card)
        item = grouped.setdefault(
            label,
            {
                "value": label,
                "label": label,
                "count": 0,
                "quality": card.get("quality"),
                "quality_color": card.get("quality_color"),
                "quality_tab": card.get("quality_tab"),
            },
        )
        item["count"] += 1
    return sorted(grouped.values(), key=lambda item: (_sort_value(item.get("quality")), str(item.get("label") or "")))


def _item_quality_filter_label(card: dict[str, Any]) -> str:
    label = str(card.get("quality_name") or "").strip()
    if label:
        return label
    quality = card.get("quality")
    return f"品质 {quality}" if quality not in (None, "") else "品质未知"


def _item_quality_filter_values(card: dict[str, Any]) -> tuple[str, ...]:
    values = {
        str(card.get("quality_name") or "").strip(),
        str(card.get("quality_tab") or "").strip(),
        str(card.get("quality") or "").strip(),
        _item_quality_filter_label(card),
    }
    return tuple(value for value in values if value)


def _build_item_type_options(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        type_key = str(card.get("type_key") or _UNKNOWN_ITEM_CATEGORY_KEY)
        item = grouped.setdefault(
            type_key,
            {
                "value": type_key,
                "label": card.get("type_name") or _item_type_label(type_key),
                "count": 0,
                "type": card.get("type"),
                "type_key": type_key,
            },
        )
        item["count"] += 1
    return sorted(grouped.values(), key=lambda item: (-int(item.get("count") or 0), _item_category_sort_value(item.get("type_key"))))


def _build_item_sub_type_options(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        sub_type_key = str(card.get("sub_type_key") or _item_sub_type_pair_key(_UNKNOWN_ITEM_CATEGORY_KEY, _UNKNOWN_ITEM_CATEGORY_KEY))
        item = grouped.setdefault(
            sub_type_key,
            {
                "value": sub_type_key,
                "label": card.get("type_sub_type_name") or card.get("sub_type_name") or sub_type_key,
                "count": 0,
                "type": card.get("type"),
                "type_key": card.get("type_key"),
                "type_name": card.get("type_name"),
                "sub_type": card.get("sub_type"),
                "sub_type_raw_key": card.get("sub_type_raw_key"),
                "sub_type_name": card.get("sub_type_name"),
            },
        )
        item["count"] += 1
    return sorted(
        grouped.values(),
        key=lambda item: (
            -int(item.get("count") or 0),
            _item_category_sort_value(item.get("type_key")),
            _item_category_sort_value(item.get("sub_type_raw_key")),
            str(item.get("label") or ""),
        ),
    )


def _matches_item_quality_filter(card: dict[str, Any], quality_name: str) -> bool:
    if not quality_name:
        return True
    values = [
        card.get("quality_name"),
        card.get("quality_tab"),
        card.get("quality"),
    ]
    return any(str(value or "").strip() == quality_name for value in values)


def _score_item_search_doc(doc: dict[str, Any], terms: tuple[str, ...]) -> int:
    if not terms:
        return 1
    if not all(term in doc["combined"] for term in terms):
        return 0

    score = 0
    for term in terms:
        if doc["item_id"] == term:
            score += 180
        if doc["name"] == term:
            score += 220
        if term in doc["name"]:
            score += 90
        if term in doc["icon"]:
            score += 28
        if term in doc["description"]:
            score += 18
        if term in doc["effect_description"]:
            score += 28
        if term in doc["progression_text"]:
            score += 24
        if term in doc["optional_gift_text"]:
            score += 18
        if any(term in text for text in doc["quality_texts"]):
            score += 24
        if any(term in text for text in doc["type_texts"]):
            score += 10
    return score


def _build_item_facet_index(scored_rows: list[tuple[int, int, dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    rows: dict[str, dict[str, list[str]]] = {
        "quality_name": {},
        "type_key": {},
        "sub_type_key": {},
    }
    object_ids: list[str] = []
    for _score, _index, card, doc in scored_rows:
        object_id = str(card.get("id") or "")
        if not object_id:
            continue
        object_ids.append(object_id)
        label = _item_quality_filter_label(card)
        rows["quality_name"].setdefault(label, []).append(object_id)
        type_key = str(card.get("type_key") or _UNKNOWN_ITEM_CATEGORY_KEY)
        sub_type_key = str(card.get("sub_type_key") or _item_sub_type_pair_key(type_key, _UNKNOWN_ITEM_CATEGORY_KEY))
        rows["type_key"].setdefault(type_key, []).append(object_id)
        rows["sub_type_key"].setdefault(sub_type_key, []).append(object_id)
    return {
        "object_ids": object_ids,
        "rows": rows,
    }


def search_fanxiu_item_cards(
    *,
    query: str = "",
    quality_name: str = "",
    type_key: str = "",
    sub_type_key: str = "",
    sort_by: str = "default",
    sort_order: str = "asc",
    limit: int = 80,
    offset: int = 0,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    runtime_index = load_fanxiu_item_runtime_index(export_root=export_root, rebuild_missing=rebuild_missing)
    catalog = runtime_index["catalog"]
    quality_name = str(quality_name or "").strip()
    type_key = str(type_key or "").strip()
    sub_type_key = str(sub_type_key or "").strip()
    sort_by = str(sort_by or "default").strip()
    sort_order = str(sort_order or "asc").strip().lower()
    if sort_by not in {"default", "time"}:
        sort_by = "default"
    if sort_order not in {"asc", "desc"}:
        sort_order = "asc"
    terms = tuple(item.strip().lower() for item in re.split(r"\s+", query or "") if item.strip())
    query_rows: list[tuple[int, int, dict[str, Any], dict[str, Any]]] = []
    for doc in runtime_index["search_docs"]:
        card = doc["card"]
        score = _score_item_search_doc(doc, terms)
        if score <= 0:
            continue
        query_rows.append((score, int(doc["index"]), card, doc))
    scored_rows: list[tuple[int, int, dict[str, Any]]] = []
    for score, index, card, doc in query_rows:
        if quality_name and quality_name not in doc["quality_values"]:
            continue
        if type_key and type_key not in doc["type_values"]:
            continue
        if sub_type_key and sub_type_key not in doc["sub_type_values"]:
            continue
        scored_rows.append((score, index, card))
    if sort_by == "time":
        if sort_order == "desc":
            scored_rows.sort(
                key=lambda item: (
                    -card_timeline_sort_value(item[2]),
                    _sort_value(item[2].get("type")),
                    _sort_value(item[2].get("id")),
                    item[1],
                )
            )
        else:
            scored_rows.sort(
                key=lambda item: (
                    card_timeline_sort_value(item[2]),
                    _sort_value(item[2].get("type")),
                    _sort_value(item[2].get("id")),
                    item[1],
                )
            )
    elif terms:
        scored_rows.sort(key=lambda item: (-item[0], _sort_value(item[2].get("type")), _sort_value(item[2].get("id"))))
    else:
        scored_rows.sort(key=lambda item: (_sort_value(item[2].get("type")), _sort_value(item[2].get("id")), item[1]))
    page_rows = scored_rows[offset : offset + limit]
    return {
        "query": query,
        "quality_name": quality_name,
        "type_key": type_key,
        "sub_type_key": sub_type_key,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "limit": limit,
        "offset": offset,
        "total": len(scored_rows),
        "stats": catalog.get("stats") or {},
        "catalog_path": catalog["catalog_path"],
        "quality_options": runtime_index["quality_options"],
        "type_options": runtime_index["type_options"],
        "sub_type_options": runtime_index["sub_type_options"],
        "facet_index": _build_item_facet_index(query_rows),
        "items": [_format_item_search_item(card, score) for score, _index, card in page_rows],
    }


def get_fanxiu_item_card(
    item_id: str | int,
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    requested = str(item_id)
    runtime_index = load_fanxiu_item_runtime_index(export_root=export_root, rebuild_missing=rebuild_missing)
    catalog = runtime_index["catalog"]
    card = runtime_index["cards_by_id"].get(requested)
    if card:
        effect_gid = _as_int(card.get("effect_value"))
        progression = (
            _build_item_progression_for_gid(resolve_fanxiu_export_root(export_root), effect_gid, catalog.get("cards") or [])
            if effect_gid is not None and any((card.get("progression_counts") or {}).values())
            else {}
        )
        return {
            "catalog_path": catalog["catalog_path"],
            "card": {
                **card,
                "progression": progression,
                "terms": _card_terms(card, limit=20),
            },
        }
    raise FanxiuResourceError(f"没有找到道具：{item_id}")


def build_fanxiu_item_catalog(
    *,
    item_rows_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    item_path = _resolve_export_file(item_rows_path, DEFAULT_ITEM_ROWS, export_root=export_root)
    quality_path = root / DEFAULT_QUALITY_ROWS

    item_rows = _load_json_rows(item_path)
    quality_rows = _load_optional_json_rows(quality_path)
    quality_by_id = {
        quality_id: _compact_quality_row(row)
        for row in quality_rows
        if (quality_id := _as_int(row.get("id"))) is not None
    }
    progression_counts_by_gid, progression_table_counts = _build_item_progression_counts_by_gid(root)
    timeline_context = build_timeline_context(root)
    time_hints_by_id = timeline_context["item_hints_by_id"]
    optional_gift_rewards_by_group, optional_gift_stats = _build_optional_gift_rewards_by_group(root, item_rows)

    cards = [
        _compact_item_row(row, quality_by_id, progression_counts_by_gid, time_hints_by_id, optional_gift_rewards_by_group)
        for row in sorted(item_rows, key=_item_sort_key)
    ]
    out_dir = root / "parsed_configs" / "item_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = out_dir / "item_catalog.json"
    stats = {
        "item_count": len(item_rows),
        "quality_count": len(quality_rows),
        "type_count": len({card.get("type_key") for card in cards}),
        "sub_type_count": len({card.get("sub_type_key") for card in cards}),
        "progression_linked_item_count": sum(1 for card in cards if any((card.get("progression_counts") or {}).values())),
        "progression_table_counts": progression_table_counts,
        "activity_count": timeline_context["stats"]["activity_count"],
        "item_with_time_hint_count": sum(1 for card in cards if card.get("time_hints")),
        "optional_gift_group_count": len(optional_gift_rewards_by_group),
        "optional_gift_reward_count": sum(len(items) for items in optional_gift_rewards_by_group.values()),
        "item_with_optional_gift_count": sum(1 for card in cards if card.get("optional_gift_rewards")),
        **optional_gift_stats,
    }
    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": ITEM_CATALOG_SCHEMA_VERSION,
                "source": {
                    "item_rows": str(item_path),
                    "quality_rows": str(quality_path) if quality_path.is_file() else "",
                    "timeline_rows": [
                        str(root / relative_path)
                        for relative_path in TIMELINE_SOURCE_ROWS
                        if (root / relative_path).is_file()
                    ],
                    "optional_gift": optional_gift_stats.get("optional_gift_source") or "",
                },
                "stats": stats,
                "cards": cards,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(out_dir),
        "stats": stats,
        "files": {
            "catalog": str(catalog_path),
        },
    }
