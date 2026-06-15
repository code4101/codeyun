from __future__ import annotations

import csv
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.core.fanxiu.catalog.gongfa import (
    DEFAULT_FAZE_EFFECT_RESOURCE_ROWS,
    DEFAULT_FAZE_RESOURCE_ROWS,
    PROGRESSION_TABLES,
    _compact_progression_row,
    _progression_sort_key,
)
from backend.core.fanxiu.catalog.resources import FanxiuResourceError, resolve_fanxiu_export_root
from backend.core.fanxiu.catalog.timeline import (
    TIMELINE_SOURCE_ROWS,
    build_timeline_context,
    card_timeline_sort_value,
    first_timeline_hint,
)
from backend.core.fanxiu.catalog.lua_config import _find_default_lang_path, load_fanxiu_lang_map, parse_fanxiu_generated_lua_config


DEFAULT_ITEM_ROWS = Path("parsed_configs/Item/rows.json")
DEFAULT_QUALITY_ROWS = Path("parsed_configs/Quality/rows.json")
DEFAULT_ITEM_CATALOG = Path("parsed_configs/item_catalog/item_catalog.json")
DEFAULT_GONGFA_FEATURE_FAMILIES = Path("parsed_configs/gongfa_feature_probe/feature_families.tsv")
DEFAULT_GONGFA_FEATURE_LINKS = Path("parsed_configs/gongfa_feature_probe/feature_links.tsv")
ITEM_CATALOG_SCHEMA_VERSION = 51
_WHITESPACE_RE = re.compile(r"\s+")
_BRACKET_TERM_RE = re.compile(r"【([^】]{1,30})】")
_RICH_TAG_RE = re.compile(r"<[^>]+>")
_UNKNOWN_ITEM_CATEGORY_KEY = "__missing__"
SPIRITUAL_BODY_TYPE_LABELS = {
    "1": "万灵类型：魔灵",
    "2": "万灵类型：仙灵",
    "3": "万灵类型：妖灵",
}
SPIRITWARE_CLEANSE_ITEM_TYPE_LABELS = {
    "1": "重洗词条",
    "2": "提升词条",
    "3": "无双词条",
    "4": "混沌词条",
    "5": "巅峰词条",
}

PREFIXED_ITEM_EFFECT_LABELS: dict[str, tuple[str, str]] = {
    "BONUS_POOL_WALLET_RATE": ("奖池比例资源", "按比例领取奖池资源"),
    "BUILDING_EFFECT": ("建筑产出道具", "洞府建筑产出补偿"),
    "BEAST_EXPLODE_BUFF": ("兽渊探秘增益", "兽渊探秘活动增益"),
    "YUNMENG": ("云梦试剑资源", "云梦试剑活动资源"),
    "YUNMENG_MINI": ("仙缘夺魁资源", "仙缘夺魁活动资源"),
    "PARTNER_YUNMENG": ("仙缘夺魁资源", "仙缘夺魁活动资源"),
    "DRAGON_BOAT": ("仙舟竞速龙舟", "仙舟竞速载具"),
    "RACES_BATTLE_RESOURCE": ("百族大战资源", "百族大战活动资源"),
    "BASEACTIVITY_BATTLE_PASS_SCORE": ("活动令积分", "战令/福令积分"),
    "CELESTIAL_DEMON": ("仙魔因果资源", "仙魔成长资源"),
    "GOD_EVIL_VALUE": ("仙魔值资源", "仙魔数值资源"),
    "MAGIC_INVADE": ("魔道入侵资源", "魔道入侵活动资源"),
    "MAGIC_INVADE_FLAG": ("魔道入侵旗帜", "魔道入侵活动道具"),
    "HEAVEN": ("虚天殿资源", "虚天殿活动资源"),
    "BEAST_EXPLODE": ("兽渊探秘资源", "兽渊探秘活动资源"),
    "LAND_CONTEND_COUNT": ("仙盟争霸次数", "仙盟争霸活动资源"),
    "CAMP_CONTEND_COUNT": ("阵营争霸次数", "阵营争霸活动资源"),
    "2_DRT": ("限时激活道具", "限时外观/称号时长"),
    "16_DRT": ("限时激活道具", "限时称号时长"),
    "5#Item": ("条件奖励道具", "条件分支奖励"),
    "PET_EXP": ("灵兽经验", "灵兽养成资源"),
    "EXP": ("修为资源", "境界修为资源"),
    "LINGJIE_RAID_COUNT": ("灵界周本次数", "万象天宫奖励次数"),
    "RACES_BATTLE_COUNT": ("百族大战体力", "百族大战活动次数"),
    "CHAOS_SEA_COUNT": ("星海伐魔体力", "星海伐魔活动次数"),
    "ADD_RAID_TIMES": ("副本次数", "副本挑战次数"),
    "APOLOGIZEBOSS": ("活动挑战次数", "活动 Boss 挑战次数"),
    "DISCIPLE": ("弟子资源", "弟子系统资源"),
    "PARTNER_STRENGTH": ("仙侣历练体力", "同游传道体力"),
    "LING_ARENA_COUNT": ("道法争锋次数", "道法争锋挑战机会"),
    "JIN_SHEN_EXP": ("凝炼修为", "凝炼系统资源"),
    "PARTNER_ARENA_COUNT": ("仙缘斗法次数", "仙缘斗法挑战机会"),
}

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


def _find_talisman_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/talisman_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_spiritual_body_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/spiritualbody_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_title_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/title_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_attribute_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/attribute_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_fashion_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/fashion_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_gongfa_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/gongfa_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_physical_exercise_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/physicalexercise_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_partner_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/partner_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_npc_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/npc_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_hidden_world_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/hiddenworld_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_pet_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/pet_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_dragon_member_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/dragonmember_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_take_medicine_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/takemedicine_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_medical_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/medical_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_resource_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/resource_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_dragon_boat_festival_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/dragonboatfestival_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_boss_kill_effect_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/bosskilleffect_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_spiritware_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/spiritware_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_swordsoul_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/swordsoul_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_flame_square_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/flamesquare_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_redbag_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/redbag_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_equipment_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/equipment_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_core_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/core_*/text_assets/{file_name}")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_partner_weapon_lua(root: Path, file_name: str) -> Path | None:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/partnerweapon_*/text_assets/{file_name}")
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


def _rich_text_value(row: dict[str, Any] | None, field: str) -> str:
    if not isinstance(row, dict):
        return ""
    value = row.get(field)
    if value is None or value == "":
        value = row.get(f"{field}_plain")
    return "" if value is None else str(value)


def _plain_rich_text(value: Any) -> str:
    text = "" if value is None else str(value)
    if not text:
        return ""
    text = text.replace("\\n", "\n")
    text = _RICH_TAG_RE.sub("", text)
    return text.strip()


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


def _build_optional_gift_effect_detail(group_id: str, rewards: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not group_id or not rewards:
        return None
    reward_parts: list[str] = []
    for reward in rewards:
        if not isinstance(reward, dict):
            continue
        name = reward.get("name") or reward.get("id")
        count = reward.get("count")
        if count not in (None, "", 0):
            reward_parts.append(f"{name} x{count}")
        elif name not in (None, ""):
            reward_parts.append(str(name))
    if not reward_parts:
        return None
    reward_text = "；".join(reward_parts[:30])
    if len(reward_parts) > 30:
        reward_text += f"；... 共{len(reward_parts)}项"
    description = "\n".join(
        part
        for part in (
            f"奖励组：{group_id}",
            f"候选奖励：{len(reward_parts)}项",
            f"奖励列表：{reward_text}",
        )
        if part
    )
    return {
        "kind": "optional_gift_rewards",
        "title": "礼包/自选奖励",
        "subtitle": f"奖励组 {group_id}",
        "description": description,
        "plain_description": description,
        "source": "Item.OptionalGift",
        "source_id": group_id,
        "optional_gift_group_id": group_id,
        "optional_gift_reward_count": len(reward_parts),
        "optional_gift_reward_text": reward_text,
    }


def _linked_talisman_id(effect_value: Any) -> int | None:
    text = "" if effect_value is None else str(effect_value).strip()
    if not text:
        return None
    parts = [part.strip() for part in text.split("_") if part.strip()]
    if len(parts) >= 2:
        return _as_int(parts[1])
    return _as_int(parts[0]) if len(parts) == 1 else None


def _linked_spiritual_body_id(effect_value: Any) -> int | None:
    return _linked_talisman_id(effect_value)


def _build_talisman_refine_material_detail(
    row: dict[str, Any],
    talisman_details_by_id: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if str(row.get("type")) != "1" or str(row.get("subType")) != "35":
        return None
    effect_value = row.get("effectValue")
    text = "" if effect_value is None else str(effect_value).strip()
    parts = [part.strip() for part in text.split("_") if part.strip()]
    if len(parts) < 2 or parts[0] != "3097":
        return None
    target_talisman_id = _as_int(parts[1])
    if target_talisman_id is None:
        return None
    effect_description = _rich_text_value(row, "effDescript") or _text_value(row, "effDescript")
    plain_effect_description = _plain_rich_text(effect_description)
    description = _rich_text_value(row, "descript") or _text_value(row, "descript")
    plain_description = _plain_rich_text(description)
    target_detail = (talisman_details_by_id or {}).get(target_talisman_id) or {}
    configured_target_name = str(target_detail.get("title") or "").strip()
    target_name_from_text = ""
    target_match = re.search(r"【法宝[·:：]?([^】]+)】", plain_effect_description or plain_description)
    if target_match:
        target_name_from_text = target_match.group(1).strip()
    target_name = configured_target_name or target_name_from_text
    if not target_name:
        target_name = _text_value(row, "name").replace("神炼元炁·", "").strip()
    lines = [
        "用于将指定法宝神炼为后天古宝。",
        f"目标法宝：{target_name}" if target_name else "",
        f"目标法宝ID：{target_talisman_id}",
        (
            f"原始文案目标：{target_name_from_text}"
            if target_name_from_text and configured_target_name and target_name_from_text != configured_target_name
            else ""
        ),
        f"神炼说明：{effect_description}" if effect_description else "",
        f"材料说明：{description}" if description else "",
    ]
    detail_description = "\n".join(part for part in lines if part)
    if not detail_description:
        return None
    return {
        "kind": "talisman_refine_material",
        "title": "法宝神炼材料",
        "subtitle": f"{target_name} · 后天古宝" if target_name else f"法宝 {target_talisman_id} · 后天古宝",
        "description": detail_description,
        "plain_description": _plain_rich_text(detail_description),
        "source": "Item.effectValue.type1_sub35",
        "source_id": row.get("id") or row.get("_row_key"),
        "effect_prefix": parts[0],
        "linked_talisman_id": target_talisman_id,
        "target_talisman_id": target_talisman_id,
        "target_talisman_name": target_name,
        "target_talisman_text_name": target_name_from_text,
    }


def _linked_title_id(effect_value: Any) -> int | None:
    text = "" if effect_value is None else str(effect_value).strip()
    if not text:
        return None
    if text.startswith("TITLE|"):
        parts = [part.strip() for part in text.split("|")]
        return _as_int(parts[1]) if len(parts) >= 2 else None
    parts = [part.strip() for part in text.split("_") if part.strip()]
    return _as_int(parts[0]) if parts else None


def _linked_fashion_id(effect_value: Any) -> int | None:
    return _linked_title_id(effect_value)


def _linked_gongfa_id(effect_value: Any) -> int | None:
    return _linked_title_id(effect_value)


def _linked_physical_exercise_id(effect_value: Any) -> int | None:
    return _linked_title_id(effect_value)


def _linked_partner_id(effect_value: Any) -> int | None:
    text = "" if effect_value is None else str(effect_value).strip()
    if not text:
        return None
    for prefix in ("PARTNER|", "PartnerFragment|"):
        if text.startswith(prefix):
            parts = [part.strip() for part in text.split("|")]
            return _as_int(parts[1]) if len(parts) >= 2 else None
    return None


def _linked_partner_gift_target_ids(effect_value: Any) -> list[int]:
    text = "" if effect_value is None else str(effect_value).strip()
    if not text:
        return []
    ids: list[int] = []
    seen: set[int] = set()
    for part in re.split(r"[,_，、\s]+", text):
        value = _as_int(part)
        if value is None or value in seen:
            continue
        ids.append(value)
        seen.add(value)
    return ids


def _linked_hidden_world_item_id(effect_value: Any) -> int | None:
    text = "" if effect_value is None else str(effect_value).strip()
    if not text.startswith("HIDDEN_WORLD_SKILL|"):
        return None
    parts = [part.strip() for part in text.split("|", 1)]
    return _as_int(parts[1]) if len(parts) == 2 else None


def _linked_pet_gift_id(effect_value: Any) -> int | None:
    text = "" if effect_value is None else str(effect_value).strip()
    if not text.startswith("PET_GIFT|"):
        return None
    parts = [part.strip() for part in text.split("|", 1)]
    return _as_int(parts[1]) if len(parts) == 2 else None


def _linked_member_id(effect_value: Any) -> int | None:
    text = "" if effect_value is None else str(effect_value).strip()
    if not (text.startswith("MEMBER|") or text.startswith("JIERIMEMBER|")):
        return None
    parts = [part.strip() for part in text.split("|")]
    return _as_int(parts[1]) if len(parts) >= 2 else None


def _linked_member_equipment_group_id(effect_value: Any) -> int | None:
    value = _as_int(effect_value)
    return value if value is not None and value > 0 else None


def _linked_member_equipment_item_id(effect_value: Any) -> int | None:
    text = "" if effect_value is None else str(effect_value).strip()
    if not text.startswith("JIERIMEMBEREUIPMENT|"):
        return None
    parts = [part.strip() for part in text.split("|")]
    return _as_int(parts[1]) if len(parts) >= 2 else None


def _is_member_equipment_marker(effect_value: Any) -> bool:
    text = "" if effect_value is None else str(effect_value).strip()
    return text.startswith("MEMBEREUIPMENT|") or text.startswith("JIERIMEMBEREUIPMENT|")


def _linked_faze_id(effect_value: Any) -> int | None:
    text = "" if effect_value is None else str(effect_value).strip()
    if not text:
        return None
    first = text.split("_", 1)[0].strip()
    return _as_int(first)


def _linked_medical_id(effect_value: Any) -> int | None:
    text = "" if effect_value is None else str(effect_value).strip()
    if not text:
        return None
    first = re.split(r"[_|,;#]", text, maxsplit=1)[0].strip()
    return _as_int(first)


def _linked_wallet_resource_id(effect_value: Any) -> int | None:
    text = "" if effect_value is None else str(effect_value).strip()
    if not text.startswith("WALLET|"):
        return None
    parts = [part.strip() for part in text.split("|", 1)]
    return _as_int(parts[1]) if len(parts) == 2 else None


def _linked_boss_kill_effect_id(effect_value: Any) -> int | None:
    text = "" if effect_value is None else str(effect_value).strip()
    if not (text.startswith("FELLING_TREE_ITEM|") or text.startswith("DOU_PO_FELLING_TREE_ITEM|")):
        return None
    parts = [part.strip() for part in text.split("|", 1)]
    return _as_int(parts[1]) if len(parts) == 2 else None


def _format_percent_from_basis_points(value: int | None) -> str:
    if value is None:
        return ""
    percent = value / 100
    if percent.is_integer():
        return f"{int(percent)}%"
    return f"{percent:g}%"


def _format_duration_seconds(value: int | None) -> str:
    if value is None:
        return ""
    if value % 86400 == 0:
        return f"{value // 86400}天"
    if value % 3600 == 0:
        return f"{value // 3600}小时"
    if value % 60 == 0:
        return f"{value // 60}分钟"
    return f"{value}秒"


def _build_prefixed_item_effect_detail(row: dict[str, Any]) -> dict[str, Any] | None:
    effect_value = row.get("effectValue")
    text = "" if effect_value is None else str(effect_value).strip()
    if "|" in text:
        prefix, payload = [part.strip() for part in text.split("|", 1)]
    else:
        prefix, payload = text, ""
    if prefix not in PREFIXED_ITEM_EFFECT_LABELS:
        return None
    title, subtitle = PREFIXED_ITEM_EFFECT_LABELS[prefix]
    description = _text_value(row, "descript")
    effect_description = _text_value(row, "effDescript")
    fields: list[str] = []
    parsed: dict[str, Any] = {}
    if prefix == "BONUS_POOL_WALLET_RATE":
        rate_value = _as_int(payload)
        parsed["rate_basis_points"] = rate_value
        percent = _format_percent_from_basis_points(rate_value)
        if percent:
            fields.append(f"奖池比例：{percent}")
    elif prefix == "BUILDING_EFFECT":
        building_part, _, value_part = payload.partition("#")
        building_id, _, effect_type = building_part.partition("_")
        parsed.update(
            {
                "building_id": _as_int(building_id),
                "building_effect_type": _as_int(effect_type),
                "building_effect_value": _as_int(value_part),
            }
        )
        if building_id:
            fields.append(f"建筑ID：{building_id}")
        if effect_type:
            fields.append(f"效果类型：{effect_type}")
        if value_part:
            fields.append(f"效果参数：{value_part}")
    elif prefix == "RACES_BATTLE_RESOURCE":
        parts = [part.strip() for part in payload.split("|")]
        if parts:
            parsed["resource_type"] = _as_int(parts[0])
            fields.append(f"资源类型：{parts[0]}")
        if len(parts) >= 2:
            parsed["resource_item_id"] = _as_int(parts[1])
            fields.append(f"资源道具ID：{parts[1]}")
    elif prefix == "BASEACTIVITY_BATTLE_PASS_SCORE":
        parsed["activity_id"] = _as_int(payload)
        fields.append(f"活动ID：{payload}")
    elif prefix in {"2_DRT", "16_DRT"}:
        duration = _as_int(payload)
        parsed["duration_seconds"] = duration
        duration_text = _format_duration_seconds(duration)
        if duration_text:
            fields.append(f"有效期：{duration_text}")
        if payload:
            fields.append(f"有效期秒数：{payload}")
    elif prefix == "5#Item":
        parsed["effect_rule"] = payload
        if payload:
            fields.append(f"条件奖励规则：{payload}")
    elif prefix in {"PARTNER_STRENGTH", "LING_ARENA_COUNT", "PARTNER_ARENA_COUNT"}:
        parts = [part.strip() for part in payload.split("|") if part.strip()]
        if parts:
            parsed["resource_id"] = _as_int(parts[0])
            fields.append(f"资源ID：{parts[0]}")
        if len(parts) >= 2:
            parsed["resource_type"] = _as_int(parts[1])
            fields.append(f"资源类型：{parts[1]}")
    else:
        parsed["resource_id"] = _as_int(payload)
        if payload:
            fields.append(f"资源ID：{payload}")
    if description:
        fields.append(f"道具说明：{description}")
    if effect_description:
        fields.append(f"使用效果：{effect_description}")
    if not fields:
        return None
    detail = {
        "kind": "prefixed_item_effect",
        "title": title,
        "subtitle": subtitle,
        "description": "\n".join(fields),
        "plain_description": "\n".join(fields),
        "source": "Item.effectValue",
        "source_id": row.get("id") or row.get("_row_key"),
        "effect_prefix": prefix,
        "effect_payload": payload,
        **{key: value for key, value in parsed.items() if value not in (None, "", [], {})},
    }
    return {key: value for key, value in detail.items() if value not in (None, "", [], {})}


def _show_effect_attr_entries(value: Any) -> list[dict[str, Any]]:
    text = "" if value is None else str(value).strip()
    if not text:
        return []
    entries: list[dict[str, Any]] = []
    for raw_part in text.split("|"):
        part = raw_part.strip()
        if not part:
            continue
        label, sep, raw_value = part.rpartition("_")
        if not sep:
            entries.append({"key": part, "label": part})
            continue
        label = label.strip()
        raw_value = raw_value.strip()
        if not label or not raw_value:
            continue
        parsed_value: int | str = int(raw_value) if re.fullmatch(r"-?\d+", raw_value) else raw_value
        entries.append({"key": label, "label": label, "value": parsed_value})
    return entries


def _format_attr_entries(label: str, entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""
    parts: list[str] = []
    for entry in entries:
        name = entry.get("label") or entry.get("key")
        value = entry.get("value")
        if value in (None, ""):
            parts.append(str(name))
        else:
            prefix = "+" if isinstance(value, int) and value >= 0 else ""
            parts.append(f"{name} {prefix}{value}")
    return f"{label}：" + "；".join(parts)


def _build_show_effect_detail(row: dict[str, Any]) -> dict[str, Any] | None:
    entries = _show_effect_attr_entries(row.get("showEffect") or row.get("showEffect_plain"))
    if not entries:
        return None
    attr_text = _format_attr_entries("显示属性", entries)
    return {
        "kind": "item_show_effect",
        "title": "实际提升属性",
        "description": attr_text,
        "plain_description": attr_text,
        "source": "Item.showEffect",
        "source_id": row.get("id") or row.get("_row_key"),
        "attr_text": attr_text,
        "attr_entries": entries,
    }


def _effect_detail_search_text(detail: dict[str, Any]) -> str:
    return " ".join(
        str(detail.get(field) or "")
        for field in (
            "title",
            "subtitle",
            "description",
            "plain_description",
            "stage_name",
            "quality_name",
            "type_label",
            "attr_text",
            "tips",
            "condition",
            "model_text",
            "level_group",
            "gongfa_exp",
            "stage_text",
            "feature_stage_text",
            "feature_asset_text",
            "gongfa_feature_gid",
            "gongfa_feature_status",
            "gongfa_local_effect_id",
            "gongfa_local_terms",
            "gongfa_local_personality",
            "consume_text",
            "partner_skill_text",
            "partner_active_skill_text",
            "partner_arane_text",
            "member_skill_text",
            "medicine_type",
            "max_times_text",
            "cooldown_text",
            "activity_name",
            "effect_type_label",
            "target_partner_ids",
            "target_partner_names",
            "target_partner_unknown_ids",
            "npc_id",
            "npc_gift_item_ids",
            "hidden_world_item_id",
            "hidden_world_item_type_label",
            "skill_ids",
            "skill_names",
            "pet_gift_id",
            "pet_gift_rate",
            "boss_kill_effect_id",
            "effect_type",
            "max_value",
            "param",
            "effect_prefix",
            "effect_payload",
            "building_id",
            "building_effect_type",
            "building_effect_value",
            "rate_basis_points",
            "resource_type",
            "resource_item_id",
            "activity_id",
            "resource_id",
            "duration_seconds",
            "effect_rule",
            "optional_gift_group_id",
            "optional_gift_reward_count",
            "optional_gift_reward_text",
            "spiritware_item_id",
            "spiritware_type",
            "spiritware_name",
            "spiritware_part",
            "spiritware_quality_name",
            "spiritware_base_attr_text",
            "spiritware_max_attr_text",
            "spiritware_cleanse_item_text",
            "spiritware_ultra_text",
            "spiritware_target_part_text",
            "spiritware_ultra_material_text",
            "spiritware_soul_grade_count",
            "spiritware_skill_ids",
            "spiritware_cleanse_type_label",
            "spiritware_cleanse_part_count",
            "linked_talisman_id",
            "target_talisman_id",
            "target_talisman_name",
            "target_talisman_text_name",
            "swordsoul_item_id",
            "swordsoul_id",
            "swordsoul_name",
            "swordsoul_part",
            "swordsoul_part_name",
            "swordsoul_stage_count",
            "swordsoul_awaken_text",
            "swordsoul_unlock_text",
            "swordsoul_open_condition",
            "sword_item_id",
            "sword_id",
            "sword_name",
            "sword_local_target_name",
            "sword_cost_text",
            "sword_show_condition",
            "sword_initial_text",
            "sword_final_text",
            "sword_key_point_text",
            "sword_level_count",
            "sword_key_point_count",
            "flame_item_id",
            "flame_id",
            "flame_name",
            "flame_level_count",
            "flame_condition_text",
            "flame_cost_text",
            "flame_initial_attr_text",
            "flame_final_attr_text",
            "flame_square_text",
            "equipment_item_id",
            "equipment_type",
            "equipment_type_name",
            "equipment_suit_title",
            "equipment_attr_text",
            "equipment_fixed_tag_text",
            "equipment_affix_text",
            "equipment_level_group",
            "equipment_star_group",
            "gem_item_id",
            "gem_type",
            "gem_level",
            "gem_score",
            "gem_attr_text",
            "gem_location_text",
            "gem_suit_title",
            "gem_suit_text",
            "gem_skill_id",
            "gem_skill_text",
            "redbag_id",
            "redbag_name",
            "redbag_reward_text",
            "redbag_tier_text",
            "redbag_condition_text",
            "redbag_event_text",
        )
    )


def _build_talisman_effect_details_by_id(root: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    talisman_path = _find_talisman_lua(root, "Talisman.lua")
    if talisman_path is None:
        return {}, {"talisman_source": "", "talisman_grade_source": "", "talisman_detail_count": 0}

    grade_path = _find_talisman_lua(root, "TalismanGrade.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    talisman_rows = list(parse_fanxiu_generated_lua_config(talisman_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    grade_rows = (
        list(parse_fanxiu_generated_lua_config(grade_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if grade_path is not None
        else []
    )

    grades_by_talisman: dict[int, list[dict[str, Any]]] = {}
    for row in grade_rows:
        if not isinstance(row, dict):
            continue
        talisman_id = _as_int(row.get("Talismanid"))
        if talisman_id is None:
            continue
        grades_by_talisman.setdefault(talisman_id, []).append(row)
    for rows in grades_by_talisman.values():
        rows.sort(key=lambda item: (_sort_value(item.get("stage")), _sort_value(item.get("id") or item.get("_row_key"))))

    details_by_id: dict[int, dict[str, Any]] = {}
    for row in talisman_rows:
        if not isinstance(row, dict):
            continue
        talisman_id = _as_int(row.get("id") or row.get("_row_key"))
        if talisman_id is None:
            continue
        grade_rows_for_talisman = grades_by_talisman.get(talisman_id) or []
        init_stage = _as_int(row.get("initStage"))
        grade = next((item for item in grade_rows_for_talisman if _as_int(item.get("stage")) == init_stage), None)
        if grade is None and grade_rows_for_talisman:
            grade = grade_rows_for_talisman[0]

        description = _rich_text_value(row, "descript") or _rich_text_value(grade, "descript")
        plain_description = _text_value(row, "descript") or _text_value(grade or {}, "descript")
        if not description and not plain_description:
            continue

        quality_name = _text_value(grade or {}, "qualityname")
        stage_name = _text_value(grade or {}, "stagename")
        subtitle_parts = [part for part in (quality_name, stage_name) if part]
        detail = {
            "kind": "talisman",
            "title": _text_value(row, "name") or f"法宝 {talisman_id}",
            "subtitle": " · ".join(subtitle_parts),
            "description": description or plain_description,
            "plain_description": plain_description or description,
            "source": "Talisman.Talisman",
            "source_id": talisman_id,
            "stage": grade.get("stage") if isinstance(grade, dict) else None,
            "stage_name": stage_name,
            "quality_name": quality_name,
        }
        if isinstance(grade, dict) and grade.get("id") not in (None, ""):
            detail["grade_id"] = grade.get("id")
        details_by_id[talisman_id] = {key: value for key, value in detail.items() if value not in (None, "")}

    return details_by_id, {
        "talisman_source": str(talisman_path),
        "talisman_grade_source": str(grade_path or ""),
        "talisman_row_count": len(talisman_rows),
        "talisman_grade_row_count": len(grade_rows),
        "talisman_detail_count": len(details_by_id),
    }


def _build_spiritual_body_effect_details_by_id(root: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    body_path = _find_spiritual_body_lua(root, "SpiritualBody.lua")
    jie_path = _find_spiritual_body_lua(root, "SpiritualBodyJie.lua")
    if body_path is None or jie_path is None:
        return {}, {
            "spiritual_body_source": str(body_path or ""),
            "spiritual_body_jie_source": str(jie_path or ""),
            "spiritual_body_detail_count": 0,
        }

    quality_path = _find_spiritual_body_lua(root, "SpiritualQuality.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    body_rows = list(parse_fanxiu_generated_lua_config(body_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    jie_rows = list(parse_fanxiu_generated_lua_config(jie_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    quality_rows = (
        list(parse_fanxiu_generated_lua_config(quality_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if quality_path is not None
        else []
    )

    quality_name_by_id = {
        quality_id: _text_value(row, "name")
        for row in quality_rows
        if isinstance(row, dict) and (quality_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    jie_rows_by_body: dict[int, list[dict[str, Any]]] = {}
    for row in jie_rows:
        if not isinstance(row, dict):
            continue
        body_id = _as_int(row.get("lingID"))
        if body_id is None:
            continue
        jie_rows_by_body.setdefault(body_id, []).append(row)
    for rows in jie_rows_by_body.values():
        rows.sort(key=lambda item: (_sort_value(item.get("jie")), _sort_value(item.get("id") or item.get("_row_key"))))

    details_by_id: dict[int, dict[str, Any]] = {}
    for body in body_rows:
        if not isinstance(body, dict):
            continue
        body_id = _as_int(body.get("id") or body.get("_row_key"))
        if body_id is None:
            continue
        rows = [row for row in jie_rows_by_body.get(body_id, []) if _rich_text_value(row, "describe")]
        if not rows:
            continue

        first_row = next((row for row in rows if _as_int(row.get("jie")) == 1), rows[0])
        type_label = SPIRITUAL_BODY_TYPE_LABELS.get(str(body.get("type") or ""), "")
        quality_name = quality_name_by_id.get(_as_int(first_row.get("quality")) or -1, "")
        stage_name = _text_value(first_row, "name") or (
            f"{first_row.get('jie')}阶" if first_row.get("jie") not in (None, "") else ""
        )
        premium_tip = _rich_text_value(first_row, "premiumTips")
        rich_descriptions = [_rich_text_value(row, "describe") for row in rows if _rich_text_value(row, "describe")]
        plain_descriptions = [_text_value(row, "describe") for row in rows if _text_value(row, "describe")]
        description_parts = [part for part in ([premium_tip] if premium_tip else []) + rich_descriptions if part]
        plain_premium_tip = _text_value(first_row, "premiumTips")
        plain_parts = [
            part
            for part in ([plain_premium_tip] if plain_premium_tip else []) + plain_descriptions
            if part
        ]
        subtitle = " · ".join(part for part in (type_label, quality_name, stage_name) if part)
        detail = {
            "kind": "spiritual_body",
            "title": _text_value(body, "name") or f"万灵 {body_id}",
            "subtitle": subtitle,
            "description": "\n\n".join(description_parts),
            "plain_description": "\n\n".join(plain_parts),
            "source": "SpiritualBody.SpiritualBody",
            "source_id": body_id,
            "stage": first_row.get("jie"),
            "stage_name": stage_name,
            "quality_name": quality_name,
            "type_label": type_label,
        }
        details_by_id[body_id] = {key: value for key, value in detail.items() if value not in (None, "")}

    return details_by_id, {
        "spiritual_body_source": str(body_path),
        "spiritual_body_jie_source": str(jie_path),
        "spiritual_body_quality_source": str(quality_path or ""),
        "spiritual_body_row_count": len(body_rows),
        "spiritual_body_jie_row_count": len(jie_rows),
        "spiritual_body_quality_row_count": len(quality_rows),
        "spiritual_body_detail_count": len(details_by_id),
    }


def _title_attr_entries(value: Any, attr_name_by_key: dict[str, str]) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    entries: list[dict[str, Any]] = []
    for key in sorted(value):
        raw = value.get(key)
        if raw in (None, "", 0):
            continue
        key_text = str(key)
        entries.append(
            {
                "key": key_text,
                "label": attr_name_by_key.get(key_text, key_text),
                "value": raw,
            }
        )
    return entries


def _format_title_attr_text(label: str, entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""
    parts = [f"{entry.get('label') or entry.get('key')} +{entry.get('value')}" for entry in entries]
    return f"{label}：" + "；".join(parts)


def _spiritware_attr_entries(value: Any, attr_meta_by_key: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    entries: list[dict[str, Any]] = []
    for key in sorted(value):
        raw = value.get(key)
        if raw in (None, "", 0):
            continue
        key_text = str(key)
        meta = attr_meta_by_key.get(key_text, {})
        entries.append(
            {
                "key": key_text,
                "label": meta.get("name") or key_text,
                "value": raw,
                "group": meta.get("group"),
            }
        )
    return entries


def _format_spiritware_attr_value(entry: dict[str, Any]) -> str:
    value = entry.get("value")
    number = _as_int(value)
    if number is None:
        return str(value)
    key = str(entry.get("key") or "")
    group = str(entry.get("group") or "")
    if key.endswith("_RATE") or group in {"Ratio", "RatioAttribute"}:
        percent = number / 100
        percent_text = f"{percent:.2f}".rstrip("0").rstrip(".")
        prefix = "+" if number >= 0 else ""
        return f"{prefix}{percent_text}%"
    prefix = "+" if number >= 0 else ""
    return f"{prefix}{number}"


def _format_spiritware_attr_entries(label: str, entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""
    parts = [
        f"{entry.get('label') or entry.get('key')} {_format_spiritware_attr_value(entry)}"
        for entry in entries
    ]
    return f"{label}：" + "；".join(parts)


def _equipment_tag_name(tag_id: Any, tags_by_id: dict[int, dict[str, Any]]) -> str:
    parsed = _as_int(tag_id)
    if parsed is None:
        return str(tag_id or "").strip()
    tag = tags_by_id.get(parsed) or {}
    name = _text_value(tag, "name")
    return name or str(parsed)


def _format_equipment_fixed_tag_text(value: Any, tags_by_id: dict[int, dict[str, Any]]) -> str:
    if not isinstance(value, list):
        return ""
    names = [_equipment_tag_name(item, tags_by_id) for item in value]
    names = [name for name in names if name]
    return "、".join(names)


def _format_equipment_affix_text(value: Any, tags_by_id: dict[int, dict[str, Any]]) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        text = "" if item is None else str(item).strip()
        if not text:
            continue
        if ":" in text:
            tag_part, weight_part = text.split(":", 1)
            name = _equipment_tag_name(tag_part, tags_by_id)
            weight = _as_int(weight_part)
            parts.append(f"{name} {weight}%" if weight is not None else f"{name} {weight_part}")
        else:
            parts.append(_equipment_tag_name(text, tags_by_id))
    return "；".join(part for part in parts if part)


def _format_equipment_location_text(value: Any, equipment_by_type: dict[int, dict[str, Any]]) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    grouped: dict[str, int] = {}
    for part in text.split(","):
        raw = part.strip()
        if not raw:
            continue
        equip_part = raw.split("_", 1)[0]
        equip_type = _as_int(equip_part)
        equipment = equipment_by_type.get(equip_type or -1, {})
        label = _text_value(equipment, "name") or f"部位{equip_part}"
        grouped[label] = grouped.get(label, 0) + 1
    return "；".join(f"{label} {count}孔" for label, count in grouped.items())


def _equipment_gem_family_name(item_name: str, subscript: str = "") -> str:
    text = str(item_name or "").strip()
    if subscript and text.startswith(subscript):
        text = text[len(subscript):].strip()
    text = re.sub(r"^[一二三四五六七八九十]+[阶品]", "", text).strip()
    text = re.sub(r"\(废弃\)$", "", text).strip()
    return text


def _item_token_id_count(token: Any) -> tuple[int | None, int | None]:
    text = "" if token is None else str(token).strip()
    match = re.fullmatch(r"Item\|(-?\d+)_(-?\d+)", text)
    if not match:
        return None, None
    return _as_int(match.group(1)), _as_int(match.group(2))


def _linked_item_text(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    name = item.get("name") or item.get("id")
    if name in (None, ""):
        return ""
    count = item.get("count")
    return f"{name} x{count}" if count not in (None, "", 0) else str(name)


def _format_item_token_text(token: Any, items_by_id: dict[int, dict[str, Any]]) -> str:
    item_id, count = _item_token_id_count(token)
    if item_id is None:
        return "" if token in (None, "") else str(token)
    item = items_by_id.get(item_id, {})
    name = _text_value(item, "name") if isinstance(item, dict) else ""
    text = name or str(item_id)
    return f"{text} x{count}" if count not in (None, "", 0) else text


def _format_redbag_amount_range(min_value: int | None, max_value: int | None) -> str:
    if min_value is None and max_value is None:
        return ""
    if min_value is None:
        return str(max_value)
    if max_value is None or max_value == min_value:
        return str(min_value)
    return f"{min_value}-{max_value}"


def _parse_redbag_reward_range(value: Any, items_by_id: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    text = "" if value is None else str(value).strip()
    if not text or "|" not in text:
        return None
    item_part, amount_part = [part.strip() for part in text.split("|", 1)]
    item_id = _as_int(item_part)
    if item_id is None:
        return None
    min_value: int | None = None
    max_value: int | None = None
    if "_" in amount_part:
        min_raw, max_raw = [part.strip() for part in amount_part.split("_", 1)]
        min_value = _as_int(min_raw)
        max_value = _as_int(max_raw)
    else:
        min_value = _as_int(amount_part)
        max_value = min_value
    item = _linked_item_from_row(items_by_id.get(item_id) or {"id": item_id, "name": item_id})
    item["range_min"] = min_value
    item["range_max"] = max_value
    item["range_text"] = _format_redbag_amount_range(min_value, max_value)
    item["token"] = text
    return item


def _parse_redbag_tiers(value: Any) -> list[dict[str, Any]]:
    text = "" if value is None else str(value).strip()
    if not text:
        return []
    entries: list[dict[str, Any]] = []
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part or "|" not in part:
            continue
        weight_part, amount_part = [item.strip() for item in part.split("|", 1)]
        weight = _as_int(weight_part)
        min_value: int | None = None
        max_value: int | None = None
        if "_" in amount_part:
            min_raw, max_raw = [item.strip() for item in amount_part.split("_", 1)]
            min_value = _as_int(min_raw)
            max_value = _as_int(max_raw)
        else:
            min_value = _as_int(amount_part)
            max_value = min_value
        entries.append(
            {
                "weight": weight,
                "range_min": min_value,
                "range_max": max_value,
                "range_text": _format_redbag_amount_range(min_value, max_value),
                "token": part,
            }
        )
    total_weight = sum(entry.get("weight") or 0 for entry in entries)
    for entry in entries:
        weight = entry.get("weight")
        if isinstance(weight, int) and total_weight > 0:
            percent = weight * 100 / total_weight
            entry["percent"] = f"{percent:.2f}".rstrip("0").rstrip(".") + "%"
    return entries


def _format_redbag_tier_text(tiers: list[dict[str, Any]], item_name: Any = None) -> str:
    parts: list[str] = []
    name = "" if item_name in (None, "") else str(item_name)
    for tier in tiers:
        range_text = tier.get("range_text") or ""
        weight_text = tier.get("percent") or (f"权重{tier.get('weight')}" if tier.get("weight") not in (None, "") else "")
        reward_text = f"{name} {range_text}".strip() if range_text else name
        if reward_text and weight_text:
            parts.append(f"{weight_text}：{reward_text}")
        elif reward_text:
            parts.append(reward_text)
        elif weight_text:
            parts.append(weight_text)
    return "；".join(parts)


def _build_redbag_effect_details_by_id(
    root: Path,
    item_rows: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    redbag_path = _find_redbag_lua(root, "RedBag.lua")
    if redbag_path is None:
        return {}, {"redbag_source": "", "redbag_row_count": 0, "redbag_detail_count": 0}

    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    rows = list(parse_fanxiu_generated_lua_config(redbag_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    items_by_id = {
        item_id: row
        for row in item_rows
        if (item_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    details_by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        redbag_id = _as_int(row.get("id") or row.get("_row_key"))
        if redbag_id is None:
            continue
        reward_item = _parse_redbag_reward_range(row.get("parameter1"), items_by_id)
        tiers = _parse_redbag_tiers(row.get("parameter2"))
        redbag_name = _text_value(row, "name")
        reward_name = reward_item.get("name") if reward_item else ""
        reward_range = reward_item.get("range_text") if reward_item else ""
        reward_text = f"{reward_name} {reward_range}".strip() if reward_name else str(row.get("parameter1") or "")
        tier_text = _format_redbag_tier_text(tiers, reward_name)
        condition_text = str(row.get("receiveCondition") or "").strip()
        event_text = "；".join(
            part
            for part in (
                f"事件类型：{row.get('eventType')}" if row.get("eventType") not in (None, "", 0) else "",
                f"事件参数：{row.get('eventParameter1')}" if row.get("eventParameter1") not in (None, "") else "",
                f"聊天频道：{row.get('idChat') or row.get('chatId')}" if row.get("idChat") or row.get("chatId") else "",
            )
            if part
        )
        description = "\n".join(
            part
            for part in (
                f"红包配置：{redbag_name}（RedBag {redbag_id}）" if redbag_name else f"红包配置：RedBag {redbag_id}",
                f"发放数量：{row.get('quantity')}" if row.get("quantity") not in (None, "") else "",
                f"每日次数：{row.get('dailyNum')}" if row.get("dailyNum") not in (None, "") else "",
                f"领取条件：{condition_text}" if condition_text else "",
                f"基础奖励：{reward_text}" if reward_text else "",
                f"奖励档位：{tier_text}" if tier_text else "",
                event_text,
            )
            if part
        )
        if not description:
            continue
        detail = {
            "kind": "redbag",
            "title": "红包奖励配置",
            "subtitle": redbag_name,
            "description": description,
            "plain_description": description,
            "source": "RedBag.RedBag",
            "source_id": redbag_id,
            "redbag_id": redbag_id,
            "redbag_name": redbag_name,
            "redbag_quantity": row.get("quantity"),
            "redbag_daily_num": row.get("dailyNum"),
            "redbag_condition_text": condition_text,
            "redbag_event_text": event_text,
            "redbag_reward_item_id": reward_item.get("id") if reward_item else None,
            "redbag_reward_item_name": reward_name,
            "redbag_reward_text": reward_text,
            "redbag_tier_text": tier_text,
            "redbag_tiers": tiers,
        }
        details_by_id[redbag_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_id, {
        "redbag_source": str(redbag_path),
        "redbag_row_count": len(rows),
        "redbag_detail_count": len(details_by_id),
    }


def _build_equipment_effect_details_by_item_id(
    root: Path,
    item_rows: list[dict[str, Any]],
    quality_by_id: dict[int, dict[str, Any]] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    equipment_path = _find_equipment_lua(root, "Equipment.lua")
    equipment_item_path = _find_equipment_lua(root, "EquipmentItem.lua")
    equipment_tag_path = _find_equipment_lua(root, "EquipmentTag.lua")
    gem_develop_path = _find_equipment_lua(root, "GemDevelop.lua")
    gem_suit_path = _find_equipment_lua(root, "GemSuit.lua")
    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    if equipment_item_path is None and gem_develop_path is None:
        return {}, {
            "equipment_source": str(equipment_path or ""),
            "equipment_item_source": "",
            "equipment_tag_source": str(equipment_tag_path or ""),
            "equipment_gem_source": "",
            "equipment_gem_suit_source": str(gem_suit_path or ""),
            "equipment_attribute_source": str(attribute_path or ""),
            "equipment_item_row_count": 0,
            "equipment_gem_row_count": 0,
            "equipment_detail_count": 0,
        }

    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None

    def parse_optional(path: Path | None) -> list[dict[str, Any]]:
        if path is None:
            return []
        return [
            row
            for row in parse_fanxiu_generated_lua_config(path, lang_path=lang_path, lang_map=lang_map).get("rows") or []
            if isinstance(row, dict)
        ]

    equipment_rows = parse_optional(equipment_path)
    equipment_item_rows = parse_optional(equipment_item_path)
    equipment_tag_rows = parse_optional(equipment_tag_path)
    gem_rows = parse_optional(gem_develop_path)
    gem_suit_rows = parse_optional(gem_suit_path)
    attribute_rows = parse_optional(attribute_path)
    equipment_by_type = {
        equip_type: row
        for row in equipment_rows
        if (equip_type := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    tags_by_id = {
        tag_id: row
        for row in equipment_tag_rows
        if (tag_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    attr_meta_by_key = {
        str(row.get("id")): {
            "name": _text_value(row, "name") or row.get("id"),
            "group": row.get("group"),
        }
        for row in attribute_rows
        if row.get("id") not in (None, "")
    }
    items_by_id = {
        item_id: row
        for row in item_rows
        if isinstance(row, dict) and (item_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    details_by_item_id: dict[int, dict[str, Any]] = {}
    gem_suit_rows_by_title: dict[str, list[dict[str, Any]]] = {}
    for row in gem_suit_rows:
        title = str(row.get("conditionTitle") or "").strip()
        if not title:
            continue
        gem_suit_rows_by_title.setdefault(title, []).append(row)
    for rows in gem_suit_rows_by_title.values():
        rows.sort(key=lambda item: (_sort_value(item.get("id") or item.get("_row_key")), str(item.get("package") or "")))

    def item_name(item_id: Any) -> str:
        parsed = _as_int(item_id)
        if parsed is None:
            return str(item_id or "").strip()
        row = items_by_id.get(parsed) or {}
        return _text_value(row, "name") or str(parsed)

    def item_quality_name(item_id: int) -> str:
        row = items_by_id.get(item_id) or {}
        quality_id = _as_int(row.get("quality"))
        if quality_id is None:
            return ""
        return (quality_by_id or {}).get(quality_id, {}).get("quality_name", "")

    for row in equipment_item_rows:
        item_id = _as_int(row.get("itemId") or row.get("_row_key"))
        if item_id is None:
            continue
        equipment_type = _as_int(row.get("type"))
        equipment = equipment_by_type.get(equipment_type or -1, {})
        title = item_name(item_id)
        type_name = _text_value(equipment, "name") or (f"部位 {equipment_type}" if equipment_type is not None else "")
        suit_title = _text_value(equipment, "suitTitle")
        quality_name = item_quality_name(item_id)
        attr_entries = _spiritware_attr_entries(row.get("attr"), attr_meta_by_key)
        attr_text = _format_spiritware_attr_entries("装备属性", attr_entries)
        fixed_tag_text = _format_equipment_fixed_tag_text(row.get("preAffix"), tags_by_id)
        affix_text = _format_equipment_affix_text(row.get("affix"), tags_by_id)
        default_item_id = _as_int(equipment.get("defaultItem"))
        gem_item_id = _as_int(equipment.get("gemItem"))
        special_gem_item_id = _as_int(equipment.get("specialGemItem"))
        description = "\n".join(
            part
            for part in (
                f"装备部位：{suit_title}{type_name}" if suit_title or type_name else "",
                f"装备品质：{quality_name}" if quality_name else "",
                attr_text,
                f"固定灵纹：{fixed_tag_text}" if fixed_tag_text else "",
                f"灵纹权重：{affix_text}" if affix_text else "",
                f"等级组：{equipment.get('levelGroup')}" if equipment.get("levelGroup") not in (None, "") else "",
                f"星级组：{equipment.get('starGroup')}" if equipment.get("starGroup") not in (None, "") else "",
                f"默认装备：{item_name(default_item_id)}" if default_item_id is not None else "",
                f"默认灵环：{item_name(gem_item_id)}" if gem_item_id is not None else "",
                f"核心灵环：{item_name(special_gem_item_id)}" if special_gem_item_id is not None else "",
            )
            if part
        )
        if not description:
            continue
        detail = {
            "kind": "equipment_item",
            "title": title,
            "subtitle": " · ".join(part for part in (suit_title, type_name, quality_name) if part),
            "description": description,
            "plain_description": description,
            "source": "Equipment.EquipmentItem",
            "source_id": item_id,
            "equipment_item_id": item_id,
            "equipment_type": equipment_type,
            "equipment_type_name": type_name,
            "equipment_suit_title": suit_title,
            "equipment_attr_text": attr_text,
            "equipment_fixed_tag_text": fixed_tag_text,
            "equipment_affix_text": affix_text,
            "equipment_level_group": equipment.get("levelGroup"),
            "equipment_star_group": equipment.get("starGroup"),
            "equipment_gem_item_id": gem_item_id,
            "equipment_special_gem_item_id": special_gem_item_id,
            "attr_entries": attr_entries,
        }
        details_by_item_id[item_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    for row in gem_rows:
        item_id = _as_int(row.get("itemId") or row.get("_row_key"))
        if item_id is None:
            continue
        title = item_name(item_id)
        quality_name = item_quality_name(item_id)
        subscript = str(row.get("subscript") or "").strip()
        gem_family_name = _equipment_gem_family_name(title, subscript)
        gem_suit_rows_for_item = gem_suit_rows_by_title.get(gem_family_name) or []
        gem_suit_preview_rows = [gem_suit_rows_for_item[0], gem_suit_rows_for_item[-1]] if len(gem_suit_rows_for_item) > 1 else gem_suit_rows_for_item[:1]
        gem_suit_text = "；".join(
            part
            for part in (
                "：".join(
                    item
                    for item in (
                        _text_value(suit_row, "package"),
                        _plain_rich_text(_rich_text_value(suit_row, "skillDesc")) or _text_value(suit_row, "skillDesc"),
                    )
                    if item
                )
                for suit_row in gem_suit_preview_rows
            )
            if part
        )
        attr_entries = _spiritware_attr_entries(row.get("attr"), attr_meta_by_key)
        attr_text = _format_spiritware_attr_entries("灵环属性", attr_entries)
        location_text = _format_equipment_location_text(row.get("equipLocation"), equipment_by_type)
        skill_text = _plain_rich_text(_rich_text_value(row, "skillDesc")) or _plain_rich_text(_rich_text_value(row, "CoreContent"))
        extra_parts = [
            f"装备属性数值：{row.get('equipattr')}" if row.get("equipattr") not in (None, "") else "",
            f"全装备属性加成：{row.get('allEquipAttrAdd')}" if row.get("allEquipAttrAdd") not in (None, "") else "",
            f"灵威加成：{row.get('gemAdd')}" if row.get("gemAdd") not in (None, "") else "",
            f"气血聚灵加成：{row.get('MAXMP_ASSEMBLY_RATE')}" if row.get("MAXMP_ASSEMBLY_RATE") not in (None, "") else "",
        ]
        extra_text = "；".join(part for part in extra_parts if part)
        description = "\n".join(
            part
            for part in (
                f"灵环阶位：{subscript}" if subscript else "",
                f"灵环品质：{quality_name}" if quality_name else "",
                f"灵环评分：{row.get('gemScore')}" if row.get("gemScore") not in (None, "") else "",
                f"可镶嵌：{location_text}" if location_text else "",
                attr_text,
                extra_text,
                f"连携效果：{gem_suit_text}" if gem_suit_text else "",
                f"核心技能：{row.get('skill')}" if row.get("skill") not in (None, "") else "",
                skill_text,
            )
            if part
        )
        if not description:
            continue
        detail = {
            "kind": "equipment_gem",
            "title": title,
            "subtitle": " · ".join(part for part in (quality_name, subscript, "灵环") if part),
            "description": description,
            "plain_description": description,
            "source": "Equipment.GemDevelop",
            "source_id": item_id,
            "gem_item_id": item_id,
            "gem_type": row.get("gemType"),
            "gem_level": row.get("gemLevel"),
            "gem_score": row.get("gemScore"),
            "gem_attr_text": attr_text,
            "gem_location_text": location_text,
            "gem_suit_title": gem_family_name,
            "gem_suit_text": gem_suit_text,
            "gem_skill_id": row.get("skill"),
            "gem_skill_text": skill_text,
            "attr_entries": attr_entries,
        }
        details_by_item_id[item_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_item_id, {
        "equipment_source": str(equipment_path or ""),
        "equipment_item_source": str(equipment_item_path or ""),
        "equipment_tag_source": str(equipment_tag_path or ""),
        "equipment_gem_source": str(gem_develop_path or ""),
        "equipment_gem_suit_source": str(gem_suit_path or ""),
        "equipment_attribute_source": str(attribute_path or ""),
        "equipment_item_row_count": len(equipment_item_rows),
        "equipment_gem_row_count": len(gem_rows),
        "equipment_gem_suit_row_count": len(gem_suit_rows),
        "equipment_item_detail_count": sum(1 for detail in details_by_item_id.values() if detail.get("kind") == "equipment_item"),
        "equipment_gem_detail_count": sum(1 for detail in details_by_item_id.values() if detail.get("kind") == "equipment_gem"),
        "equipment_detail_count": len(details_by_item_id),
    }


def _format_single_attr_text(attr_key: Any, value: Any, attr_meta_by_key: dict[str, dict[str, Any]]) -> str:
    key = "" if attr_key is None else str(attr_key)
    if not key:
        return ""
    entries = _spiritware_attr_entries({key: value}, attr_meta_by_key)
    if not entries:
        return ""
    return _format_spiritware_attr_entries("", entries).lstrip("：")


def _level_span_text(level_rows: list[dict[str, Any]]) -> str:
    levels = [level for row in level_rows if (level := _as_int(row.get("level"))) is not None]
    if not levels:
        return ""
    low = min(levels)
    high = max(levels)
    return f"{low}-{high}级" if low != high else f"{high}级"


def _total_consume_exp(level_rows: list[dict[str, Any]]) -> int:
    total = 0
    for row in level_rows:
        value = _as_int(row.get("consumeExp") or row.get("exp"))
        if value is not None:
            total += value
    return total


def _build_coreware_effect_details_by_item_id(
    root: Path,
    item_rows: list[dict[str, Any]],
    quality_by_id: dict[int, dict[str, Any]] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    core_base_path = _find_core_lua(root, "CoreBase.lua")
    core_map_path = _find_core_lua(root, "CoreMap.lua")
    coreware_base_path = _find_core_lua(root, "CoreWareBase.lua")
    coreware_level_path = _find_core_lua(root, "CoreWareLevel.lua")
    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    if coreware_base_path is None:
        return {}, {
            "coreware_base_source": "",
            "coreware_level_source": str(coreware_level_path or ""),
            "core_base_source": str(core_base_path or ""),
            "core_map_source": str(core_map_path or ""),
            "coreware_attribute_source": str(attribute_path or ""),
            "coreware_base_row_count": 0,
            "coreware_level_row_count": 0,
            "coreware_detail_count": 0,
        }

    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None

    def parse_optional(path: Path | None) -> list[dict[str, Any]]:
        if path is None:
            return []
        return [
            row
            for row in parse_fanxiu_generated_lua_config(path, lang_path=lang_path, lang_map=lang_map).get("rows") or []
            if isinstance(row, dict)
        ]

    core_rows = parse_optional(core_base_path)
    core_map_rows = parse_optional(core_map_path)
    coreware_rows = parse_optional(coreware_base_path)
    level_rows = parse_optional(coreware_level_path)
    attribute_rows = parse_optional(attribute_path)
    items_by_id = {
        item_id: row
        for row in item_rows
        if isinstance(row, dict) and (item_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    attr_meta_by_key = {
        str(row.get("id")): {
            "name": _text_value(row, "name") or row.get("id"),
            "group": row.get("group"),
        }
        for row in attribute_rows
        if row.get("id") not in (None, "")
    }
    core_by_type_part = {
        (core_type, part): row
        for row in core_rows
        if (core_type := _as_int(row.get("type"))) is not None and (part := _as_int(row.get("parts"))) is not None
    }
    core_map_by_type = {
        core_type: row
        for row in core_map_rows
        if (core_type := _as_int(row.get("type") or row.get("id") or row.get("_row_key"))) is not None
    }
    levels_by_item_id: dict[int, list[dict[str, Any]]] = {}
    for row in level_rows:
        item_id = _as_int(row.get("itemId"))
        if item_id is None:
            continue
        levels_by_item_id.setdefault(item_id, []).append(row)
    for rows in levels_by_item_id.values():
        rows.sort(key=lambda row: (_sort_value(row.get("level"), 0), _sort_value(row.get("id") or row.get("_row_key"))))

    details_by_item_id: dict[int, dict[str, Any]] = {}
    for row in coreware_rows:
        item_id = _as_int(row.get("itemId") or row.get("_row_key"))
        if item_id is None:
            continue
        item_row = items_by_id.get(item_id)
        if not item_row or str(item_row.get("type")) != "5" or str(item_row.get("subType")) != "53":
            continue
        core_type = _as_int(row.get("type"))
        part = _as_int(row.get("parts"))
        core = core_by_type_part.get((core_type or -1, part or -1), {})
        core_map = core_map_by_type.get(core_type or -1, {})
        quality_id = _as_int(item_row.get("quality") if item_row else None) or _as_int(row.get("quality"))
        quality_name = (quality_by_id or {}).get(quality_id or -1, {}).get("quality_name", "") if quality_id is not None else ""
        item_name = _text_value(item_row, "name") or str(item_id)
        system_name = _text_value(core_map, "name") or (f"仙窍类型 {core_type}" if core_type is not None else "")
        part_name = _text_value(core, "title") or _text_value(core, "name") or (f"部位 {part}" if part is not None else "")
        main_attr_key = row.get("initialMainAttr")
        main_attr_name = (attr_meta_by_key.get(str(main_attr_key)) or {}).get("name") or str(main_attr_key or "")
        rows = levels_by_item_id.get(item_id) or []
        first_level = rows[0] if rows else {}
        max_level = rows[-1] if rows else {}
        initial_attr_text = _format_single_attr_text(main_attr_key, first_level.get("addMainAttr"), attr_meta_by_key)
        max_attr_text = _format_single_attr_text(main_attr_key, max_level.get("addMainAttr"), attr_meta_by_key)
        level_text = _level_span_text(rows)
        total_exp = _total_consume_exp(rows)
        unlock_levels = [
            str(level)
            for level in (_as_int(item.get("level")) for item in rows if item.get("unlockElement") not in (None, "", 0))
            if level is not None
        ]
        side_attr_levels = [
            str(level)
            for level in (_as_int(item.get("level")) for item in rows if item.get("randomSideAttr") not in (None, "", 0))
            if level is not None
        ]
        description = "\n".join(
            part_text
            for part_text in (
                f"仙窍体系：{system_name}" if system_name else "",
                f"仙窍部位：{part_name}" if part_name else "",
                f"道纹品质：{quality_name}" if quality_name else "",
                f"主属性：{main_attr_name}" if main_attr_name else "",
                f"初始主属性：{initial_attr_text}" if initial_attr_text else "",
                f"满级主属性：{max_attr_text}" if max_attr_text else "",
                f"强化等级：{level_text}" if level_text else "",
                f"升级经验合计：{total_exp}" if total_exp else "",
                f"元素槽上限：{row.get('elementNumLimit')}" if row.get("elementNumLimit") not in (None, "") else "",
                f"元素解锁等级：{', '.join(unlock_levels)}" if unlock_levels else "",
                f"随机副属性等级：{', '.join(side_attr_levels)}" if side_attr_levels else "",
                f"分解/经验值：{row.get('exp')}" if row.get("exp") not in (None, "") else "",
                f"经验折算：{row.get('expOff')}" if row.get("expOff") not in (None, "") else "",
                f"入口道具ID：{core.get('coreWareWay')}" if core.get("coreWareWay") not in (None, "") else "",
                f"解锁说明：{_text_value(core, 'unlockDes')}" if _text_value(core, "unlockDes") else "",
                f"体系条件：{_text_value(core_map, 'desc')}" if _text_value(core_map, "desc") else "",
            )
            if part_text
        )
        if not description:
            continue
        detail = {
            "kind": "coreware_item",
            "title": item_name,
            "subtitle": " · ".join(part_text for part_text in (system_name, part_name, quality_name) if part_text),
            "description": description,
            "plain_description": description,
            "source": "Core.CoreWareBase",
            "source_id": item_id,
            "coreware_item_id": item_id,
            "coreware_type": core_type,
            "coreware_type_name": system_name,
            "coreware_part": part,
            "coreware_part_name": part_name,
            "coreware_quality": quality_id,
            "coreware_quality_name": quality_name,
            "coreware_main_attr": main_attr_key,
            "coreware_main_attr_name": main_attr_name,
            "coreware_initial_attr_text": initial_attr_text,
            "coreware_max_attr_text": max_attr_text,
            "coreware_level_text": level_text,
            "coreware_level_count": len(rows),
            "coreware_total_exp": total_exp,
            "coreware_element_num_limit": row.get("elementNumLimit"),
            "coreware_unlock_element_levels": unlock_levels,
            "coreware_random_side_attr_levels": side_attr_levels,
            "coreware_exp": row.get("exp"),
            "coreware_exp_off": row.get("expOff"),
            "coreware_condition_text": _text_value(core_map, "desc"),
        }
        details_by_item_id[item_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_item_id, {
        "core_base_source": str(core_base_path or ""),
        "core_map_source": str(core_map_path or ""),
        "coreware_base_source": str(coreware_base_path or ""),
        "coreware_level_source": str(coreware_level_path or ""),
        "coreware_attribute_source": str(attribute_path or ""),
        "core_base_row_count": len(core_rows),
        "core_map_row_count": len(core_map_rows),
        "coreware_base_row_count": len(coreware_rows),
        "coreware_level_row_count": len(level_rows),
        "coreware_detail_count": len(details_by_item_id),
    }


def _build_partner_weapon_stone_effect_details_by_item_id(
    root: Path,
    item_rows: list[dict[str, Any]],
    quality_by_id: dict[int, dict[str, Any]] | None = None,
    partner_details_by_id: dict[int, dict[str, Any]] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    stone_base_path = _find_partner_weapon_lua(root, "WeaponStoneBase.lua")
    stone_level_path = _find_partner_weapon_lua(root, "WeaponStoneLevel.lua")
    stone_upgrade_path = _find_partner_weapon_lua(root, "WeaponStoneUpgrade.lua")
    weapon_base_path = _find_partner_weapon_lua(root, "WeaponBase.lua")
    stone_combination_path = _find_partner_weapon_lua(root, "WeaponStoneCombination.lua")
    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    if stone_base_path is None:
        return {}, {
            "partner_weapon_stone_base_source": "",
            "partner_weapon_stone_level_source": str(stone_level_path or ""),
            "partner_weapon_stone_upgrade_source": str(stone_upgrade_path or ""),
            "partner_weapon_base_source": str(weapon_base_path or ""),
            "partner_weapon_stone_combination_source": str(stone_combination_path or ""),
            "partner_weapon_attribute_source": str(attribute_path or ""),
            "partner_weapon_stone_base_row_count": 0,
            "partner_weapon_stone_detail_count": 0,
        }

    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None

    def parse_optional(path: Path | None) -> list[dict[str, Any]]:
        if path is None:
            return []
        return [
            row
            for row in parse_fanxiu_generated_lua_config(path, lang_path=lang_path, lang_map=lang_map).get("rows") or []
            if isinstance(row, dict)
        ]

    stone_rows = parse_optional(stone_base_path)
    level_rows = parse_optional(stone_level_path)
    upgrade_rows = parse_optional(stone_upgrade_path)
    weapon_rows = parse_optional(weapon_base_path)
    combination_rows = parse_optional(stone_combination_path)
    attribute_rows = parse_optional(attribute_path)
    items_by_id = {
        item_id: row
        for row in item_rows
        if isinstance(row, dict) and (item_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    attr_meta_by_key = {
        str(row.get("id")): {
            "name": _text_value(row, "name") or row.get("id"),
            "group": row.get("group"),
        }
        for row in attribute_rows
        if row.get("id") not in (None, "")
    }
    weapon_by_partner_and_stone_type: dict[tuple[int, int], dict[str, Any]] = {}
    weapon_by_id = {
        weapon_id: row
        for row in weapon_rows
        if (weapon_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    for row in weapon_rows:
        partner_id = _as_int(row.get("partner"))
        if partner_id is None:
            continue
        stone_types = row.get("stoneType") if isinstance(row.get("stoneType"), list) else []
        for stone_type_raw in stone_types:
            stone_type = _as_int(stone_type_raw)
            if stone_type is not None:
                weapon_by_partner_and_stone_type[(partner_id, stone_type)] = row
    levels_by_item_id: dict[int, list[dict[str, Any]]] = {}
    for row in level_rows:
        item_id = _as_int(row.get("stoneItem"))
        if item_id is None:
            continue
        levels_by_item_id.setdefault(item_id, []).append(row)
    for rows in levels_by_item_id.values():
        rows.sort(key=lambda row: (_sort_value(row.get("level"), 0), _sort_value(row.get("id") or row.get("_row_key"))))
    upgrades_by_item_id: dict[int, list[dict[str, Any]]] = {}
    for row in upgrade_rows:
        item_id = _as_int(row.get("stoneItem"))
        if item_id is None:
            continue
        upgrades_by_item_id.setdefault(item_id, []).append(row)
    for rows in upgrades_by_item_id.values():
        rows.sort(key=lambda row: (_sort_value(row.get("grade"), 0), _sort_value(row.get("id") or row.get("_row_key"))))
    combinations_by_weapon_id: dict[int, list[dict[str, Any]]] = {}
    for row in combination_rows:
        weapon_id = _as_int(row.get("weaponId"))
        if weapon_id is None:
            continue
        combinations_by_weapon_id.setdefault(weapon_id, []).append(row)
    for rows in combinations_by_weapon_id.values():
        rows.sort(key=lambda row: (_sort_value(row.get("id") or row.get("_row_key")), _sort_value(row.get("type"))))

    def item_name(item_id: Any) -> str:
        parsed = _as_int(item_id)
        if parsed is None:
            return str(item_id or "").strip()
        row = items_by_id.get(parsed) or {}
        return _text_value(row, "name") or str(parsed)

    def quality_name(item_row: dict[str, Any] | None, fallback_quality: Any) -> str:
        quality_id = _as_int((item_row or {}).get("quality"))
        if quality_id is None:
            quality_id = _as_int(fallback_quality)
        if quality_id is None:
            return ""
        return (quality_by_id or {}).get(quality_id, {}).get("quality_name") or str(quality_id)

    def attr_text(label: str, row: dict[str, Any]) -> str:
        return _format_spiritware_attr_entries(label, _spiritware_attr_entries(row.get("attr"), attr_meta_by_key))

    details_by_item_id: dict[int, dict[str, Any]] = {}
    for row in stone_rows:
        item_id = _as_int(row.get("itemId") or row.get("_row_key"))
        if item_id is None:
            continue
        item_row = items_by_id.get(item_id)
        if not item_row or str(item_row.get("type")) != "5" or str(item_row.get("subType")) != "74":
            continue
        stone_type = _as_int(row.get("type"))
        partner_id = _as_int(row.get("belong"))
        weapon = weapon_by_partner_and_stone_type.get((partner_id or -1, stone_type or -1), {})
        if not weapon and partner_id is not None:
            weapon = next((item for item in weapon_rows if _as_int(item.get("partner")) == partner_id), {})
        weapon_id = _as_int(weapon.get("id") or weapon.get("_row_key"))
        partner_detail = (partner_details_by_id or {}).get(partner_id or -1, {})
        partner_name = str(partner_detail.get("title") or "").strip() or (f"仙侣 {partner_id}" if partner_id is not None else "")
        weapon_name = _text_value(weapon, "name") or (f"专属武器 {weapon_id}" if weapon_id is not None else "")
        item_title = item_name(item_id)
        stone_label = item_title.split("·", 1)[0].strip() if "·" in item_title else item_title
        item_quality_name = quality_name(item_row, row.get("quality"))
        level_list = levels_by_item_id.get(item_id) or []
        first_level = level_list[0] if level_list else {}
        max_level = level_list[-1] if level_list else {}
        initial_attr_text = attr_text("初始属性", first_level)
        max_attr_text = attr_text("满级属性", max_level)
        level_text = _level_span_text(level_list)
        upgrade_list = upgrades_by_item_id.get(item_id) or []
        first_upgrade = upgrade_list[0] if upgrade_list else {}
        max_upgrade = upgrade_list[-1] if upgrade_list else {}
        upgrade_initial_attr_text = attr_text("升品初始属性", first_upgrade)
        upgrade_max_attr_text = attr_text("升品满阶属性", max_upgrade)
        upgrade_text = _level_span_text([{"level": item.get("grade"), "id": item.get("id")} for item in upgrade_list]).replace("级", "阶")
        upgrade_consume = _format_item_token_text(first_upgrade.get("consume"), items_by_id) if first_upgrade else ""
        combination_preview = ""
        combo_rows = combinations_by_weapon_id.get(weapon_id or -1) if weapon_id is not None else []
        if combo_rows:
            preview_rows = [combo_rows[0], combo_rows[-1]] if len(combo_rows) > 1 else combo_rows[:1]
            combination_preview = "；".join(
                part
                for part in (
                    "：".join(
                        item
                        for item in (
                            _text_value(combo, "des"),
                            _format_spiritware_attr_entries("", _spiritware_attr_entries(combo.get("attr"), attr_meta_by_key)).lstrip("："),
                        )
                        if item
                    )
                    for combo in preview_rows
                )
                if part
            )
        can_flags = "、".join(
            part
            for part in (
                "可升级" if row.get("canUpgrade") not in (None, "", 0) else "",
                "可作为经验消耗" if row.get("canConsume") not in (None, "", 0) else "",
            )
            if part
        )
        description = "\n".join(
            part
            for part in (
                f"目标仙侣：{partner_name}" if partner_name else "",
                f"专属武器：{weapon_name}" if weapon_name else "",
                f"玉石类型：{stone_label}（类型 {stone_type}）" if stone_label or stone_type is not None else "",
                f"玉石品质：{item_quality_name}" if item_quality_name else "",
                f"默认经验：{row.get('defaultExp')}" if row.get("defaultExp") not in (None, "") else "",
                f"规则：{can_flags}" if can_flags else "",
                f"强化等级：{level_text}" if level_text else "",
                initial_attr_text,
                max_attr_text,
                f"强化经验合计：{_total_consume_exp(level_list)}" if level_list else "",
                f"升品阶数：{upgrade_text}" if upgrade_text else "",
                f"升品消耗：{upgrade_consume}" if upgrade_consume else "",
                upgrade_initial_attr_text,
                upgrade_max_attr_text,
                f"武器共鸣预览：{combination_preview}" if combination_preview else "",
            )
            if part
        )
        if not description:
            continue
        detail = {
            "kind": "partner_weapon_stone",
            "title": item_title,
            "subtitle": " · ".join(part for part in (partner_name, weapon_name, item_quality_name) if part),
            "description": description,
            "plain_description": description,
            "source": "PartnerWeapon.WeaponStoneBase",
            "source_id": item_id,
            "partner_weapon_stone_item_id": item_id,
            "partner_weapon_stone_type": stone_type,
            "partner_weapon_stone_type_name": stone_label,
            "partner_weapon_partner_id": partner_id,
            "partner_weapon_partner_name": partner_name,
            "partner_weapon_id": weapon_id,
            "partner_weapon_name": weapon_name,
            "partner_weapon_stone_quality": row.get("quality"),
            "partner_weapon_stone_quality_name": item_quality_name,
            "partner_weapon_stone_default_exp": row.get("defaultExp"),
            "partner_weapon_stone_level_text": level_text,
            "partner_weapon_stone_initial_attr_text": initial_attr_text,
            "partner_weapon_stone_max_attr_text": max_attr_text,
            "partner_weapon_stone_upgrade_text": upgrade_text,
            "partner_weapon_stone_upgrade_consume_text": upgrade_consume,
            "partner_weapon_stone_upgrade_initial_attr_text": upgrade_initial_attr_text,
            "partner_weapon_stone_upgrade_max_attr_text": upgrade_max_attr_text,
            "partner_weapon_stone_combination_text": combination_preview,
        }
        details_by_item_id[item_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_item_id, {
        "partner_weapon_stone_base_source": str(stone_base_path or ""),
        "partner_weapon_stone_level_source": str(stone_level_path or ""),
        "partner_weapon_stone_upgrade_source": str(stone_upgrade_path or ""),
        "partner_weapon_base_source": str(weapon_base_path or ""),
        "partner_weapon_stone_combination_source": str(stone_combination_path or ""),
        "partner_weapon_attribute_source": str(attribute_path or ""),
        "partner_weapon_stone_base_row_count": len(stone_rows),
        "partner_weapon_stone_level_row_count": len(level_rows),
        "partner_weapon_stone_upgrade_row_count": len(upgrade_rows),
        "partner_weapon_base_row_count": len(weapon_rows),
        "partner_weapon_stone_combination_row_count": len(combination_rows),
        "partner_weapon_stone_detail_count": len(details_by_item_id),
    }


def _format_numeric_range(values: list[int]) -> str:
    if not values:
        return ""
    low = min(values)
    high = max(values)
    return str(low) if low == high else f"{low}-{high}"


def _build_special_gongfa_jie_effect_details_by_item_id(
    root: Path,
    item_rows: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    jie_path = _find_gongfa_lua(root, "Special-GongfaJie.lua")
    skill_path = _find_gongfa_lua(root, "GongfaSkill.lua")
    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    if jie_path is None:
        return {}, {
            "special_gongfa_jie_source": "",
            "special_gongfa_skill_source": str(skill_path or ""),
            "special_gongfa_attribute_source": str(attribute_path or ""),
            "special_gongfa_jie_row_count": 0,
            "special_gongfa_skill_row_count": 0,
            "special_gongfa_jie_detail_count": 0,
        }

    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    jie_rows = list(parse_fanxiu_generated_lua_config(jie_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    skill_rows = (
        list(parse_fanxiu_generated_lua_config(skill_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if skill_path is not None
        else []
    )
    attribute_rows = (
        list(parse_fanxiu_generated_lua_config(attribute_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if attribute_path is not None
        else []
    )
    item_by_id = {
        item_id: row
        for row in item_rows
        if isinstance(row, dict) and (item_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    attr_name_by_key = {
        str(row.get("id")): _text_value(row, "name")
        for row in attribute_rows
        if isinstance(row, dict) and row.get("id") not in (None, "") and _text_value(row, "name")
    }
    skill_by_group_pin: dict[tuple[int, int], dict[str, Any]] = {}
    skill_by_group: dict[int, dict[str, Any]] = {}
    for row in skill_rows:
        if not isinstance(row, dict):
            continue
        group = _as_int(row.get("group"))
        if group is None:
            continue
        pin = _as_int(row.get("pin")) or 0
        skill_by_group_pin.setdefault((group, pin), row)
        skill_by_group.setdefault(group, row)

    rows_by_gid: dict[int, list[dict[str, Any]]] = {}
    for row in jie_rows:
        if not isinstance(row, dict):
            continue
        gid = _as_int(row.get("gid"))
        if gid is not None:
            rows_by_gid.setdefault(gid, []).append(row)
    for rows in rows_by_gid.values():
        rows.sort(key=lambda item: (_sort_value(item.get("jie")), _sort_value(item.get("id") or item.get("_row_key"))))

    def plain(row: dict[str, Any] | None, field: str) -> str:
        if not isinstance(row, dict):
            return ""
        return _plain_rich_text(_rich_text_value(row, field)) or _text_value(row, field)

    def format_consume(value: Any) -> str:
        tokens = value if isinstance(value, list) else [value]
        parts = [_format_item_token_text(token, item_by_id) for token in tokens if token not in (None, "")]
        return "；".join(part for part in parts if part)

    details_by_item_id: dict[int, dict[str, Any]] = {}
    for row in jie_rows:
        if not isinstance(row, dict):
            continue
        item_id = _as_int(row.get("id") or row.get("_row_key"))
        if item_id is None:
            continue
        item = item_by_id.get(item_id)
        if not item or str(item.get("type")) != "98":
            continue
        gid = _as_int(row.get("gid"))
        pin = _as_int(row.get("pin")) or 0
        skill_group = _as_int(row.get("skill"))
        skill = (
            skill_by_group_pin.get((skill_group, pin))
            if skill_group is not None
            else None
        ) or (skill_by_group.get(skill_group) if skill_group is not None else None)
        skill_name = plain(skill, "skillName") or plain(skill, "name")
        skill_desc = _rich_text_value(skill, "describe") or _rich_text_value(row, "describe")
        row_desc = _rich_text_value(row, "describe") or skill_desc
        cd_text = plain(skill, "cd")
        origin_text = _rich_text_value(skill, "origin")
        attr_entries = _title_attr_entries(row.get("attr"), attr_name_by_key)
        attr_text = _format_title_attr_text("当前属性", attr_entries)
        consume_text = format_consume(row.get("consume"))
        all_rows = rows_by_gid.get(gid or -1) or []
        max_row = all_rows[-1] if all_rows else None
        max_desc = _rich_text_value(max_row, "describe") if isinstance(max_row, dict) else ""
        max_attr_entries = _title_attr_entries(max_row.get("attr") if isinstance(max_row, dict) else None, attr_name_by_key)
        max_attr_text = _format_title_attr_text("满重属性", max_attr_entries)
        item_name = _text_value(item, "name") or str(item_id)
        item_part = item_name.split("·", 1)[-1] if "·" in item_name else ""
        jie = row.get("jie")
        description = "\n".join(
            part
            for part in (
                f"镇物部位：{item_part}" if item_part else "",
                f"镇物阶段：{row.get('name') or row.get('name_plain') or (str(jie) + '重' if jie not in (None, '') else '')}",
                f"技能：{skill_name}" if skill_name else "",
                f"技能效果：{row_desc}" if row_desc else "",
                f"冷却：{cd_text}" if cd_text else "",
                f"来源：{origin_text}" if origin_text else "",
                attr_text,
                f"突破消耗：{consume_text}" if consume_text else "",
                f"满重预览：{max_desc}" if max_desc and max_row is not row else "",
                max_attr_text if max_row is not row else "",
            )
            if part
        )
        if not description:
            continue
        detail = {
            "kind": "special_gongfa_jie_item",
            "title": skill_name or item_name,
            "subtitle": " · ".join(part for part in ("镇物", f"{jie}重" if jie not in (None, "") else "", item_part) if part),
            "description": description,
            "plain_description": _plain_rich_text(description),
            "source": "Gongfa.Special-GongfaJie",
            "source_id": item_id,
            "special_gongfa_item_id": item_id,
            "special_gongfa_gid": gid,
            "special_gongfa_jie": jie,
            "special_gongfa_pin": pin,
            "special_gongfa_skill_group": skill_group,
            "special_gongfa_skill_name": skill_name,
            "special_gongfa_skill_text": _plain_rich_text(row_desc),
            "special_gongfa_cd_text": cd_text,
            "special_gongfa_origin_text": _plain_rich_text(origin_text),
            "special_gongfa_attr_text": attr_text,
            "special_gongfa_max_attr_text": max_attr_text,
            "consume_text": consume_text,
            "attr_entries": attr_entries,
        }
        details_by_item_id[item_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_item_id, {
        "special_gongfa_jie_source": str(jie_path),
        "special_gongfa_skill_source": str(skill_path or ""),
        "special_gongfa_attribute_source": str(attribute_path or ""),
        "special_gongfa_jie_row_count": len(jie_rows),
        "special_gongfa_skill_row_count": len(skill_rows),
        "special_gongfa_jie_detail_count": len(details_by_item_id),
    }


def _build_swordsoul_line_effect_details_by_item_id(
    root: Path,
    item_rows: list[dict[str, Any]],
    quality_by_id: dict[int, dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    base_path = _find_swordsoul_lua(root, "SwordSoulBase.lua")
    lines_path = _find_swordsoul_lua(root, "SwordSoulLines.lua")
    line_base_path = _find_swordsoul_lua(root, "SwordLinesBase.lua")
    line_level_path = _find_swordsoul_lua(root, "SwordLinesLevel.lua")
    line_attr_path = _find_swordsoul_lua(root, "SwordLinesAttr.lua")
    line_attr_quality_path = _find_swordsoul_lua(root, "SwordLinesAttrQuality.lua")
    soul_eff_path = _find_swordsoul_lua(root, "SwordSoulEff.lua")
    wash_path = _find_swordsoul_lua(root, "SwordLinesWash.lua")
    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    if line_base_path is None:
        return {}, {
            "swordsoul_line_base_source": "",
            "swordsoul_line_detail_count": 0,
            "swordsoul_line_wash_detail_count": 0,
        }

    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None

    def parse_rows(path: Path | None) -> list[dict[str, Any]]:
        return (
            list(parse_fanxiu_generated_lua_config(path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
            if path is not None
            else []
        )

    base_rows = parse_rows(base_path)
    line_rows = parse_rows(lines_path)
    line_base_rows = parse_rows(line_base_path)
    level_rows = parse_rows(line_level_path)
    attr_rows = parse_rows(line_attr_path)
    attr_quality_rows = parse_rows(line_attr_quality_path)
    eff_rows = parse_rows(soul_eff_path)
    wash_rows = parse_rows(wash_path)
    attribute_rows = parse_rows(attribute_path)
    item_by_id = {
        item_id: row
        for row in item_rows
        if isinstance(row, dict) and (item_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }

    def quality_name(value: Any) -> str:
        parsed = _as_int(value)
        if parsed is None:
            return ""
        return (quality_by_id or {}).get(parsed, {}).get("quality_name") or f"品质 {parsed}"

    def plain(row: dict[str, Any] | None, field: str) -> str:
        if not isinstance(row, dict):
            return ""
        return _plain_rich_text(_rich_text_value(row, field)) or _text_value(row, field)

    soul_by_id = {
        soul_id: row
        for row in base_rows
        if isinstance(row, dict) and (soul_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    line_by_soul_part = {
        (soul_id, part): row
        for row in line_rows
        if isinstance(row, dict)
        and (soul_id := _as_int(row.get("soulID") or row.get("soulId"))) is not None
        and (part := _as_int(row.get("part"))) is not None
    }
    levels_by_group: dict[int, list[dict[str, Any]]] = {}
    for row in level_rows:
        if not isinstance(row, dict):
            continue
        group = _as_int(row.get("group"))
        if group is not None:
            levels_by_group.setdefault(group, []).append(row)
    for rows in levels_by_group.values():
        rows.sort(key=lambda item: (_sort_value(item.get("level"), 0), _sort_value(item.get("id") or item.get("_row_key"))))

    attr_ids_by_group: dict[int, list[int]] = {}
    for row in attr_rows:
        if not isinstance(row, dict):
            continue
        group = _as_int(row.get("group"))
        attr_id = _as_int(row.get("attrId"))
        if group is None or attr_id is None:
            continue
        attr_ids_by_group.setdefault(group, [])
        if attr_id not in attr_ids_by_group[group]:
            attr_ids_by_group[group].append(attr_id)

    attr_quality_by_key: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for row in attr_quality_rows:
        if not isinstance(row, dict):
            continue
        soul_id = _as_int(row.get("soulId") or row.get("soulID"))
        attr_id = _as_int(row.get("attrId"))
        quality = _as_int(row.get("quality"))
        if soul_id is None or attr_id is None or quality is None:
            continue
        attr_quality_by_key.setdefault((soul_id, attr_id, quality), []).append(row)

    attr_meta_by_code: dict[str, dict[str, Any]] = {}
    for row in attribute_rows:
        if not isinstance(row, dict):
            continue
        for key in (row.get("code"), row.get("_row_key"), row.get("id")):
            if key not in (None, ""):
                attr_meta_by_code[str(key)] = row

    def attr_label(attr_id: Any) -> str:
        meta = attr_meta_by_code.get(str(attr_id)) or {}
        return _text_value(meta, "name") if isinstance(meta, dict) else str(attr_id)

    def format_attr_quality_text(soul_id: int, item_quality: int, attr_ids: list[int]) -> str:
        parts: list[str] = []
        for attr_id in attr_ids:
            rows = attr_quality_by_key.get((soul_id, attr_id, item_quality)) or []
            if not rows:
                parts.append(attr_label(attr_id))
                continue
            initials = [_as_int(row.get("initial")) for row in rows]
            levelups = [_as_int(row.get("levelup")) for row in rows]
            scores = [_as_int(row.get("scoreValue")) for row in rows]
            word_quality_names = sorted(
                {
                    name
                    for row in rows
                    if (name := quality_name(row.get("wordQuality")))
                }
            )
            detail_parts = [
                f"词条{'/'.join(word_quality_names)}" if word_quality_names else "",
                f"初始 {_format_numeric_range([item for item in initials if item is not None])}",
                f"每级 {_format_numeric_range([item for item in levelups if item is not None])}",
                f"评分 {_format_numeric_range([item for item in scores if item is not None])}",
            ]
            parts.append(f"{attr_label(attr_id)}（{'；'.join(item for item in detail_parts if item)}）")
        return "；".join(parts)

    eff_rows_by_soul_title: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for row in eff_rows:
        if not isinstance(row, dict):
            continue
        soul_id = _as_int(row.get("soulId") or row.get("soulID"))
        if soul_id is None:
            continue
        title = plain(row, "groupTitle") or f"效果组{row.get('group')}"
        eff_rows_by_soul_title.setdefault(soul_id, {}).setdefault(title, []).append(row)
    for groups in eff_rows_by_soul_title.values():
        for rows in groups.values():
            rows.sort(key=lambda item: (_sort_value(item.get("sort")), _sort_value(item.get("id") or item.get("_row_key"))))

    def format_eff_preview(soul_id: int) -> str:
        groups = eff_rows_by_soul_title.get(soul_id) or {}
        parts: list[str] = []
        for title, rows in list(groups.items())[:6]:
            if not rows:
                continue
            first = rows[0]
            last = rows[-1]
            first_desc = plain(first, "des")
            last_desc = plain(last, "des")
            if first_desc and last_desc and first_desc != last_desc:
                parts.append(f"{title}：{first_desc}；最高 {last_desc}")
            elif first_desc:
                parts.append(f"{title}：{first_desc}")
        return "；".join(parts)

    def format_level_text(level_group: Any) -> tuple[str, int | None, str]:
        group = _as_int(level_group)
        rows = levels_by_group.get(group or -1) if group is not None else []
        if not rows:
            return "", None, ""
        max_level = max((_as_int(row.get("level")) or 0) for row in rows)
        cost_token = next((row.get("cost") for row in rows if row.get("cost") not in (None, "")), None)
        cost_text = _format_item_token_text(cost_token, item_by_id) if cost_token is not None else ""
        final_row = max(rows, key=lambda item: (_sort_value(item.get("level"), 0), _sort_value(item.get("id") or item.get("_row_key"))))
        final_rate = _format_percent_from_basis_points(_as_int(final_row.get("amplification")))
        parts = [
            f"最高 {max_level}级",
            f"每级消耗 {cost_text}" if cost_text else "",
            f"满级倍率 {final_rate}" if final_rate else "",
        ]
        return "；".join(part for part in parts if part), max_level, cost_text

    details_by_item_id: dict[int, dict[str, Any]] = {}
    for row in line_base_rows:
        if not isinstance(row, dict):
            continue
        item_id = _as_int(row.get("itemId") or row.get("id") or row.get("_row_key"))
        if item_id is None:
            continue
        soul_id = _as_int(row.get("soulId") or row.get("soulID"))
        part = _as_int(row.get("part"))
        item_quality = _as_int(row.get("quality"))
        if soul_id is None or part is None:
            continue
        soul = soul_by_id.get(soul_id, {})
        line = line_by_soul_part.get((soul_id, part), {})
        soul_name = plain(soul, "name") or f"剑灵{soul_id}"
        part_name = plain(line, "name") or f"部位{part}"
        item = item_by_id.get(item_id, {})
        item_name = _text_value(item, "name") if isinstance(item, dict) else ""
        pool_group = soul_id * 100 + part if part <= 4 else soul_id * 100 + 50 + part - 4
        attr_ids = attr_ids_by_group.get(pool_group) or []
        attr_text = format_attr_quality_text(soul_id, item_quality or 0, attr_ids) if item_quality is not None else ""
        level_text, max_level, level_cost_text = format_level_text(row.get("levelGroup"))
        eff_preview = format_eff_preview(soul_id)
        breakdown_text = _format_item_token_text(row.get("breakDown"), item_by_id)
        breakdown_per_level_text = _format_item_token_text(row.get("breakDownPerLevel"), item_by_id)
        unlock_text = plain(line, "unlockTips") or plain(soul, "lockDes")
        quality_text = quality_name(item_quality)
        description = "\n".join(
            part_text
            for part_text in (
                f"剑灵：{soul_name}",
                f"剑府部位：{part_name}",
                f"剑纹品质：{quality_text}" if quality_text else "",
                f"词条数：{row.get('entryNum')}" if row.get("entryNum") not in (None, "", 0) else "",
                f"可洗炼词条：{'、'.join(attr_label(attr_id) for attr_id in attr_ids)}" if attr_ids else "",
                f"词条数值：{attr_text}" if attr_text else "",
                f"强化：{level_text}" if level_text else "",
                f"分解：{breakdown_text}" if breakdown_text else "",
                f"每级返还：{breakdown_per_level_text}" if breakdown_per_level_text else "",
                f"剑灵效果预览：{eff_preview}" if eff_preview else "",
                f"解锁：{unlock_text}" if unlock_text else "",
                "支持高级洗炼" if row.get("canSuperCleanse") not in (None, "", 0) else "",
            )
            if part_text
        )
        if not description:
            continue
        detail = {
            "kind": "swordsoul_line",
            "title": item_name or f"{part_name}剑纹",
            "subtitle": " · ".join(part for part in (soul_name, part_name, quality_text) if part),
            "description": description,
            "plain_description": _plain_rich_text(description),
            "source": "SwordSoul.SwordLinesBase",
            "source_id": item_id,
            "swordsoul_line_item_id": item_id,
            "swordsoul_id": soul_id,
            "swordsoul_name": soul_name,
            "swordsoul_part": part,
            "swordsoul_part_name": part_name,
            "swordsoul_line_quality": item_quality,
            "swordsoul_line_quality_name": quality_text,
            "swordsoul_line_entry_num": row.get("entryNum"),
            "swordsoul_line_attr_group": pool_group,
            "swordsoul_line_attr_text": attr_text,
            "swordsoul_line_level_group": row.get("levelGroup"),
            "swordsoul_line_max_level": max_level,
            "swordsoul_line_level_text": level_text,
            "swordsoul_line_level_cost_text": level_cost_text,
            "swordsoul_line_effect_text": eff_preview,
            "swordsoul_line_breakdown_text": breakdown_text,
            "swordsoul_line_breakdown_per_level_text": breakdown_per_level_text,
            "swordsoul_unlock_text": unlock_text,
        }
        details_by_item_id[item_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    wash_detail_count = 0
    for row in wash_rows:
        if not isinstance(row, dict):
            continue
        item_id = _as_int(row.get("id") or row.get("_row_key"))
        if item_id is None:
            continue
        soul_ids = [_as_int(value) for value in row.get("souls") or []] if isinstance(row.get("souls"), list) else []
        part_ids = [_as_int(value) for value in row.get("parts") or []] if isinstance(row.get("parts"), list) else []
        soul_names = [plain(soul_by_id.get(soul_id or -1), "name") for soul_id in soul_ids if soul_id is not None]
        part_names: list[str] = []
        for soul_id in soul_ids:
            if soul_id is None:
                continue
            for part in part_ids:
                if part is None:
                    continue
                name = plain(line_by_soul_part.get((soul_id, part)), "name")
                if name and name not in part_names:
                    part_names.append(name)
        short_text = _rich_text_value(row, "shortDes")
        use_text = _rich_text_value(row, "useDes")
        description = "\n".join(
            part
            for part in (
                short_text,
                use_text,
                f"目标剑灵：{'、'.join(name for name in soul_names if name)}" if soul_names else "",
                f"目标部位：{'、'.join(part_names)}" if part_names else "",
                f"单次消耗：{row.get('costAmount')}" if row.get("costAmount") not in (None, "", 0) else "",
            )
            if part
        )
        if not description:
            continue
        detail = {
            "kind": "swordsoul_line_wash_item",
            "title": "剑纹洗炼道具",
            "subtitle": " · ".join(part for part in ("、".join(soul_names), "、".join(part_names)) if part),
            "description": description,
            "plain_description": _plain_rich_text(description),
            "source": "SwordSoul.SwordLinesWash",
            "source_id": item_id,
            "swordsoul_line_wash_item_id": item_id,
            "swordsoul_line_wash_target_souls": [item for item in soul_ids if item is not None],
            "swordsoul_line_wash_target_parts": [item for item in part_ids if item is not None],
        }
        details_by_item_id[item_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}
        wash_detail_count += 1

    return details_by_item_id, {
        "swordsoul_line_base_source": str(line_base_path),
        "swordsoul_line_level_source": str(line_level_path or ""),
        "swordsoul_line_attr_source": str(line_attr_path or ""),
        "swordsoul_line_attr_quality_source": str(line_attr_quality_path or ""),
        "swordsoul_eff_source": str(soul_eff_path or ""),
        "swordsoul_line_wash_source": str(wash_path or ""),
        "swordsoul_line_attribute_source": str(attribute_path or ""),
        "swordsoul_line_base_row_count": len(line_base_rows),
        "swordsoul_line_level_row_count": len(level_rows),
        "swordsoul_line_attr_row_count": len(attr_rows),
        "swordsoul_line_attr_quality_row_count": len(attr_quality_rows),
        "swordsoul_eff_row_count": len(eff_rows),
        "swordsoul_line_wash_row_count": len(wash_rows),
        "swordsoul_line_detail_count": sum(1 for detail in details_by_item_id.values() if detail.get("kind") == "swordsoul_line"),
        "swordsoul_line_wash_detail_count": wash_detail_count,
    }


def _build_swordsoul_awakening_effect_details_by_item_id(root: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    base_path = _find_swordsoul_lua(root, "SwordSoulBase.lua")
    awakening_path = _find_swordsoul_lua(root, "SwordSoulAwakening.lua")
    lines_path = _find_swordsoul_lua(root, "SwordSoulLines.lua")
    if awakening_path is None:
        return {}, {
            "swordsoul_base_source": str(base_path or ""),
            "swordsoul_awakening_source": "",
            "swordsoul_lines_source": str(lines_path or ""),
            "swordsoul_base_row_count": 0,
            "swordsoul_awakening_row_count": 0,
            "swordsoul_lines_row_count": 0,
            "swordsoul_awakening_detail_count": 0,
            "swordsoul_awakening_empty_cost_row_count": 0,
        }

    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    base_rows = (
        list(parse_fanxiu_generated_lua_config(base_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if base_path is not None
        else []
    )
    awakening_rows = list(
        parse_fanxiu_generated_lua_config(awakening_path, lang_path=lang_path, lang_map=lang_map).get("rows") or []
    )
    line_rows = (
        list(parse_fanxiu_generated_lua_config(lines_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if lines_path is not None
        else []
    )

    def plain(row: dict[str, Any] | None, field: str) -> str:
        if not isinstance(row, dict):
            return ""
        return _plain_rich_text(_rich_text_value(row, field)) or _text_value(row, field)

    base_by_id = {
        soul_id: row
        for row in base_rows
        if isinstance(row, dict) and (soul_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    line_by_soul_part = {
        (soul_id, part): row
        for row in line_rows
        if isinstance(row, dict)
        and (soul_id := _as_int(row.get("soulID") or row.get("soulId"))) is not None
        and (part := _as_int(row.get("part"))) is not None
    }

    rows_by_soul_part: dict[tuple[int, int], list[dict[str, Any]]] = {}
    item_ids_by_soul_part: dict[tuple[int, int], set[int]] = {}
    empty_cost_row_count = 0
    for row in awakening_rows:
        if not isinstance(row, dict):
            continue
        soul_id = _as_int(row.get("soulId") or row.get("soulID"))
        part = _as_int(row.get("part"))
        if soul_id is None or part is None:
            continue
        key = (soul_id, part)
        rows_by_soul_part.setdefault(key, []).append(row)
        item_id, _count = _item_token_id_count(row.get("costItem"))
        if item_id is not None:
            item_ids_by_soul_part.setdefault(key, set()).add(item_id)
        elif row.get("costItem") in (None, ""):
            empty_cost_row_count += 1

    details_by_item_id: dict[int, dict[str, Any]] = {}
    for (soul_id, part), item_ids in item_ids_by_soul_part.items():
        rows = sorted(
            rows_by_soul_part.get((soul_id, part)) or [],
            key=lambda item: (
                _sort_value(_as_int(item.get("quality")) or 0),
                _sort_value(item.get("id") or item.get("_row_key")),
            ),
        )
        base = base_by_id.get(soul_id, {})
        line = line_by_soul_part.get((soul_id, part), {})
        soul_name = plain(base, "name") or f"剑灵{soul_id}"
        part_name = plain(line, "name") or f"部位{part}"
        unlock_text = plain(line, "unlockTips") or plain(base, "lockDes")
        open_condition = str(base.get("openCondition") or "") if isinstance(base, dict) else ""
        show_condition = str(base.get("showCondition") or "") if isinstance(base, dict) else ""

        stage_lines: list[str] = []
        row_ids: list[Any] = []
        max_quality: int | None = None
        for row in rows:
            row_ids.append(row.get("id") or row.get("_row_key"))
            quality = _as_int(row.get("quality"))
            if quality is not None:
                max_quality = quality if max_quality is None else max(max_quality, quality)
            desc = plain(row, "skillDesc")
            show = plain(row, "skillShow")
            pieces = [item for item in (desc, show) if item]
            if not pieces:
                continue
            if len(pieces) == 2 and pieces[0] == pieces[1]:
                stage_lines.append(pieces[0])
            else:
                stage_lines.append("\n".join(pieces))

        awakening_text = "\n\n".join(stage_lines).strip()
        description_parts = [
            f"剑灵：{soul_name}",
            f"部位：{part_name}",
        ]
        if unlock_text:
            description_parts.append(f"解锁：{unlock_text}")
        if open_condition:
            description_parts.append(f"开放条件：{open_condition}")
        elif show_condition:
            description_parts.append(f"显示条件：{show_condition}")
        if awakening_text:
            description_parts.append(f"境界效果：\n{awakening_text}")
        description = "\n".join(description_parts)
        subtitle_parts = [soul_name, part_name]
        if max_quality is not None:
            subtitle_parts.append(f"{max_quality}境")
        detail = {
            "kind": "swordsoul_awakening_material",
            "title": "剑灵道蕴材料",
            "subtitle": " · ".join(subtitle_parts),
            "description": description,
            "plain_description": description,
            "source": "SwordSoulAwakening.costItem",
            "source_id": f"{soul_id}:{part}",
            "source_row_ids": row_ids,
            "swordsoul_id": soul_id,
            "swordsoul_name": soul_name,
            "swordsoul_part": part,
            "swordsoul_part_name": part_name,
            "swordsoul_stage_count": len(stage_lines),
            "swordsoul_awaken_text": awakening_text,
            "swordsoul_unlock_text": unlock_text,
            "swordsoul_open_condition": open_condition,
            "swordsoul_show_condition": show_condition,
        }
        cleaned_detail = {key: value for key, value in detail.items() if value not in (None, "", [], {})}
        for item_id in sorted(item_ids):
            details_by_item_id[item_id] = {**cleaned_detail, "swordsoul_item_id": item_id}

    return details_by_item_id, {
        "swordsoul_base_source": str(base_path or ""),
        "swordsoul_awakening_source": str(awakening_path),
        "swordsoul_lines_source": str(lines_path or ""),
        "swordsoul_base_row_count": len(base_rows),
        "swordsoul_awakening_row_count": len(awakening_rows),
        "swordsoul_lines_row_count": len(line_rows),
        "swordsoul_awakening_detail_count": len(details_by_item_id),
        "swordsoul_awakening_empty_cost_row_count": empty_cost_row_count,
    }


def _build_sword_base_effect_details_by_item_id(
    root: Path,
    item_rows: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    base_path = _find_swordsoul_lua(root, "SwordBase.lua")
    level_path = _find_swordsoul_lua(root, "SwordLevelUp.lua")
    key_point_path = _find_swordsoul_lua(root, "SwordKeyPoint.lua")
    if base_path is None:
        return {}, {
            "sword_base_source": "",
            "sword_level_up_source": str(level_path or ""),
            "sword_key_point_source": str(key_point_path or ""),
            "sword_base_row_count": 0,
            "sword_level_up_row_count": 0,
            "sword_key_point_row_count": 0,
            "sword_base_detail_count": 0,
        }

    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    base_rows = list(parse_fanxiu_generated_lua_config(base_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    level_rows = (
        list(parse_fanxiu_generated_lua_config(level_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if level_path is not None
        else []
    )
    key_point_rows = (
        list(parse_fanxiu_generated_lua_config(key_point_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if key_point_path is not None
        else []
    )
    item_by_id = {
        item_id: row
        for row in item_rows
        if isinstance(row, dict) and (item_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }

    def plain(row: dict[str, Any] | None, field: str) -> str:
        if not isinstance(row, dict):
            return ""
        return _plain_rich_text(_rich_text_value(row, field)) or _text_value(row, field)

    def local_target_name(item: dict[str, Any] | None) -> str:
        effect_text = plain(item, "effDescript")
        match = re.search(r"激活(.+)$", effect_text)
        if not match:
            return ""
        return match.group(1).strip()

    def normalize_name(value: str) -> str:
        return re.sub(r"\s+", "", value or "")

    levels_by_sword: dict[int, list[dict[str, Any]]] = {}
    for row in level_rows:
        if not isinstance(row, dict):
            continue
        sword_id = _as_int(row.get("swordId"))
        if sword_id is None:
            continue
        levels_by_sword.setdefault(sword_id, []).append(row)
    for rows in levels_by_sword.values():
        rows.sort(
            key=lambda item: (
                _sort_value(item.get("stage")),
                _sort_value(item.get("level")),
                _sort_value(item.get("id") or item.get("_row_key")),
            )
        )

    key_points_by_sword: dict[int, list[dict[str, Any]]] = {}
    for row in key_point_rows:
        if not isinstance(row, dict):
            continue
        sword_id = _as_int(row.get("swordId"))
        if sword_id is None:
            continue
        key_points_by_sword.setdefault(sword_id, []).append(row)
    for rows in key_points_by_sword.values():
        rows.sort(key=lambda item: (_sort_value(item.get("pin")), _sort_value(item.get("id") or item.get("_row_key"))))

    details_by_item_id: dict[int, dict[str, Any]] = {}
    for row in base_rows:
        if not isinstance(row, dict):
            continue
        sword_id = _as_int(row.get("id") or row.get("_row_key"))
        item_id, item_count = _item_token_id_count(row.get("cost"))
        if sword_id is None or item_id is None:
            continue
        sword_name = plain(row, "name") or f"本命飞剑{sword_id}"
        item_row = item_by_id.get(item_id)
        item_name = plain(item_row, "name") or str(item_id)
        local_target = local_target_name(item_row)
        level_rows_for_sword = levels_by_sword.get(sword_id) or []
        key_points = key_points_by_sword.get(sword_id) or []
        first_level = level_rows_for_sword[0] if level_rows_for_sword else {}
        final_level = level_rows_for_sword[-1] if level_rows_for_sword else {}
        first_desc = plain(first_level, "desc")
        final_desc = plain(final_level, "desc")
        key_point_lines = [
            "：".join(item for item in (plain(point, "skillName"), plain(point, "des")) if item)
            for point in key_points
        ]
        key_point_text = "\n".join(item for item in key_point_lines if item)
        cost_text = f"{item_name} x{item_count}" if item_count not in (None, "", 0) else item_name
        description_parts = [
            f"本命飞剑：{sword_name}",
            f"激活道具：{cost_text}",
        ]
        if local_target and normalize_name(local_target) != normalize_name(sword_name):
            description_parts.append(f"原始文案目标：{local_target}")
        if row.get("showCondition"):
            description_parts.append(f"显示条件：{row.get('showCondition')}")
        if first_desc:
            description_parts.append(f"初始效果：\n{first_desc}")
        if final_desc and final_desc != first_desc:
            final_level_label = final_level.get("level") or final_level.get("stage") or final_level.get("id")
            description_parts.append(f"满级效果（{final_level_label}级）：\n{final_desc}")
        if key_point_text:
            description_parts.append(f"觉醒节点：\n{key_point_text}")
        description = "\n".join(description_parts)
        subtitle_parts = ["本命飞剑"]
        if level_rows_for_sword:
            subtitle_parts.append(f"{len(level_rows_for_sword)}级")
        if key_points:
            subtitle_parts.append(f"{len(key_points)}觉醒")
        detail = {
            "kind": "sword_base_activation",
            "title": sword_name,
            "subtitle": " · ".join(subtitle_parts),
            "description": description,
            "plain_description": description,
            "source": "SwordBase.cost",
            "source_id": sword_id,
            "sword_item_id": item_id,
            "sword_id": sword_id,
            "sword_name": sword_name,
            "sword_model": row.get("model"),
            "sword_effect_asset": row.get("eff"),
            "sword_local_target_name": local_target,
            "sword_cost_text": cost_text,
            "sword_show_condition": row.get("showCondition"),
            "sword_level_count": len(level_rows_for_sword),
            "sword_initial_text": first_desc,
            "sword_final_text": final_desc,
            "sword_key_point_count": len(key_points),
            "sword_key_point_text": key_point_text,
            "sword_initial_faze_id": first_level.get("fazeId") if isinstance(first_level, dict) else None,
            "sword_final_faze_id": final_level.get("fazeId") if isinstance(final_level, dict) else None,
        }
        details_by_item_id[item_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_item_id, {
        "sword_base_source": str(base_path),
        "sword_level_up_source": str(level_path or ""),
        "sword_key_point_source": str(key_point_path or ""),
        "sword_base_row_count": len(base_rows),
        "sword_level_up_row_count": len(level_rows),
        "sword_key_point_row_count": len(key_point_rows),
        "sword_base_detail_count": len(details_by_item_id),
    }


def _build_flame_square_effect_details_by_item_id(
    root: Path,
    item_rows: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    flame_level_path = _find_flame_square_lua(root, "FlameLevel.lua")
    build_path = _find_flame_square_lua(root, "FlameSquareBuild.lua")
    square_level_path = _find_flame_square_lua(root, "FlameSquareLevel.lua")
    if flame_level_path is None:
        return {}, {
            "flame_level_source": "",
            "flame_square_build_source": str(build_path or ""),
            "flame_square_level_source": str(square_level_path or ""),
            "flame_attribute_source": "",
            "flame_level_row_count": 0,
            "flame_square_build_row_count": 0,
            "flame_square_level_row_count": 0,
            "flame_square_detail_count": 0,
        }

    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    flame_level_rows = list(
        parse_fanxiu_generated_lua_config(flame_level_path, lang_path=lang_path, lang_map=lang_map).get("rows") or []
    )
    build_rows = (
        list(parse_fanxiu_generated_lua_config(build_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if build_path is not None
        else []
    )
    square_level_rows = (
        list(parse_fanxiu_generated_lua_config(square_level_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if square_level_path is not None
        else []
    )
    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    attribute_rows = (
        list(parse_fanxiu_generated_lua_config(attribute_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if attribute_path is not None
        else []
    )
    attr_meta_by_key = {
        str(row.get("id")): {
            "name": _text_value(row, "name") or row.get("id"),
            "group": row.get("group"),
        }
        for row in attribute_rows
        if isinstance(row, dict) and row.get("id") not in (None, "")
    }
    items_by_id = {
        item_id: row
        for row in item_rows
        if isinstance(row, dict) and (item_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }

    def plain(row: dict[str, Any] | None, field: str) -> str:
        if not isinstance(row, dict):
            return ""
        return _plain_rich_text(_rich_text_value(row, field)) or _text_value(row, field)

    def item_token_text(token: Any) -> str:
        text = "" if token is None else str(token).strip()
        match = re.fullmatch(r"(?i)item\|(-?\d+)_(-?\d+)", text)
        if not match:
            return text
        item_id = _as_int(match.group(1))
        count = _as_int(match.group(2))
        item_name = plain(items_by_id.get(item_id), "name") if item_id is not None else ""
        label = item_name or str(item_id)
        return f"{label} x{count}" if count not in (None, "", 0) else label

    levels_by_flame: dict[int, list[dict[str, Any]]] = {}
    for row in flame_level_rows:
        if not isinstance(row, dict):
            continue
        flame_id = _as_int(row.get("flameId"))
        if flame_id is None:
            continue
        levels_by_flame.setdefault(flame_id, []).append(row)
    for rows in levels_by_flame.values():
        rows.sort(key=lambda item: (_sort_value(item.get("level")), _sort_value(item.get("id") or item.get("_row_key"))))

    build = build_rows[0] if build_rows and isinstance(build_rows[0], dict) else {}
    build_name = plain(build, "name")
    build_desc = plain(build, "des")
    build_unlock = plain(build, "unlockDes")
    square_text = "；".join(part for part in (build_name, build_desc, build_unlock) if part)

    details_by_item_id: dict[int, dict[str, Any]] = {}
    for item_id, item_row in items_by_id.items():
        if str(item_row.get("type")) != "86" or str(item_row.get("subType")) != "6":
            continue
        flame_id = _as_int(item_row.get("effectValue"))
        if flame_id is None:
            continue
        rows = levels_by_flame.get(flame_id) or []
        level_rows = [row for row in rows if _as_int(row.get("level")) is not None]
        if not rows:
            continue
        first_level = level_rows[0] if level_rows else rows[0]
        final_level = level_rows[-1] if level_rows else rows[-1]
        flame_name = plain(item_row, "name") or f"异火{flame_id}"
        condition_text = plain(first_level, "conditionDes")
        cost_text = item_token_text(first_level.get("cost")) if isinstance(first_level, dict) else ""
        initial_attr = _format_spiritware_attr_entries(
            f"初始属性（{first_level.get('level') or 0}级）",
            _spiritware_attr_entries(first_level.get("attr") if isinstance(first_level, dict) else None, attr_meta_by_key),
        )
        final_attr = _format_spiritware_attr_entries(
            f"满级属性（{final_level.get('level') or final_level.get('id')}级）",
            _spiritware_attr_entries(final_level.get("attr") if isinstance(final_level, dict) else None, attr_meta_by_key),
        )
        description_parts = [
            f"异火：{flame_name}",
            f"异火ID：{flame_id}",
        ]
        if build_name:
            description_parts.append(f"所属建筑：{build_name}")
        if condition_text:
            description_parts.append(f"激活条件：{condition_text}")
        if cost_text:
            description_parts.append(f"升级消耗：{cost_text}")
        if initial_attr:
            description_parts.append(initial_attr)
        if final_attr and final_attr != initial_attr:
            description_parts.append(final_attr)
        if build_desc:
            description_parts.append(f"建筑说明：{build_desc}")
        description = "\n".join(description_parts)
        detail = {
            "kind": "flame_square_flame",
            "title": flame_name,
            "subtitle": f"异火 · {len(level_rows)}级" if level_rows else "异火",
            "description": description,
            "plain_description": description,
            "source": "FlameLevel.flameId",
            "source_id": flame_id,
            "flame_item_id": item_id,
            "flame_id": flame_id,
            "flame_name": flame_name,
            "flame_level_count": len(level_rows),
            "flame_condition_text": condition_text,
            "flame_cost_text": cost_text,
            "flame_initial_attr_text": initial_attr,
            "flame_final_attr_text": final_attr,
            "flame_square_text": square_text,
        }
        details_by_item_id[item_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_item_id, {
        "flame_level_source": str(flame_level_path),
        "flame_square_build_source": str(build_path or ""),
        "flame_square_level_source": str(square_level_path or ""),
        "flame_attribute_source": str(attribute_path or ""),
        "flame_level_row_count": len(flame_level_rows),
        "flame_square_build_row_count": len(build_rows),
        "flame_square_level_row_count": len(square_level_rows),
        "flame_square_detail_count": len(details_by_item_id),
    }


def _build_spiritware_effect_details_by_item_id(
    root: Path,
    item_rows: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    spiritware_path = _find_spiritware_lua(root, "SpiritWare.lua")
    item_path = _find_spiritware_lua(root, "SpiritWareItem.lua")
    if spiritware_path is None or item_path is None:
        return {}, {
            "spiritware_source": str(spiritware_path or ""),
            "spiritware_item_source": str(item_path or ""),
            "spiritware_detail_count": 0,
        }

    base_path = _find_spiritware_lua(root, "SpiritWareBase.lua")
    ultra_path = _find_spiritware_lua(root, "SpiritWareUltra.lua")
    soul_path = _find_spiritware_lua(root, "SpiritWareSoul.lua")
    cleanse_item_path = _find_spiritware_lua(root, "SpiritWareCleanseItem.lua")
    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None

    spiritware_rows = list(parse_fanxiu_generated_lua_config(spiritware_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    spiritware_item_rows = list(parse_fanxiu_generated_lua_config(item_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    base_rows = (
        list(parse_fanxiu_generated_lua_config(base_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if base_path is not None
        else []
    )
    ultra_rows = (
        list(parse_fanxiu_generated_lua_config(ultra_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if ultra_path is not None
        else []
    )
    soul_rows = (
        list(parse_fanxiu_generated_lua_config(soul_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if soul_path is not None
        else []
    )
    cleanse_item_rows = (
        list(parse_fanxiu_generated_lua_config(cleanse_item_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if cleanse_item_path is not None
        else []
    )
    attribute_rows = (
        list(parse_fanxiu_generated_lua_config(attribute_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if attribute_path is not None
        else []
    )

    items_by_id = {
        item_id: row
        for row in item_rows
        if (item_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    attr_meta_by_key = {
        str(row.get("id")): {
            "name": _text_value(row, "name") or row.get("id"),
            "group": row.get("group"),
        }
        for row in attribute_rows
        if isinstance(row, dict) and row.get("id") not in (None, "")
    }
    ware_by_type = {
        ware_type: row
        for row in spiritware_rows
        if isinstance(row, dict) and (ware_type := _as_int(row.get("type") or row.get("id") or row.get("_row_key"))) is not None
    }
    base_by_part: dict[int, list[dict[str, Any]]] = {}
    for row in base_rows:
        if not isinstance(row, dict):
            continue
        part_item = _as_int(row.get("partsItem"))
        if part_item is not None:
            base_by_part.setdefault(part_item, []).append(row)
    for rows in base_by_part.values():
        rows.sort(key=lambda item: (_sort_value(item.get("grade")), _sort_value(item.get("id") or item.get("_row_key"))))

    ultra_by_part: dict[int, list[dict[str, Any]]] = {}
    for row in ultra_rows:
        if not isinstance(row, dict):
            continue
        part_item = _as_int(row.get("partsItem"))
        if part_item is not None:
            ultra_by_part.setdefault(part_item, []).append(row)
    for rows in ultra_by_part.values():
        rows.sort(key=lambda item: (_sort_value(item.get("grade")), _sort_value(item.get("id") or item.get("_row_key"))))
    spiritware_item_by_id = {
        item_id: row
        for row in spiritware_item_rows
        if isinstance(row, dict) and (item_id := _as_int(row.get("itemId") or row.get("_row_key"))) is not None
    }

    details_by_item_id: dict[int, dict[str, Any]] = {}
    detail_kind_counts: dict[str, int] = {}

    def add_detail(item_id: int | None, detail: dict[str, Any]) -> None:
        if item_id is None:
            return
        cleaned = {key: value for key, value in detail.items() if value not in (None, "", [], {})}
        if not cleaned:
            return
        details_by_item_id[item_id] = cleaned
        kind = str(cleaned.get("kind") or "spiritware")
        detail_kind_counts[kind] = detail_kind_counts.get(kind, 0) + 1

    for part in spiritware_item_rows:
        if not isinstance(part, dict):
            continue
        item_id = _as_int(part.get("itemId") or part.get("_row_key"))
        if item_id is None:
            continue
        ware_type = _as_int(part.get("type"))
        ware = ware_by_type.get(ware_type)
        item_row = items_by_id.get(item_id) or {"id": item_id, "name": item_id}
        ware_name = _text_value(ware or {}, "name") or (f"灵器 {ware_type}" if ware_type is not None else "灵器")
        part_no = _as_int(part.get("parts"))
        quality = _as_int(part.get("quality"))
        quality_name = _text_value(part, "qualityName") or (f"品质 {quality}" if quality is not None else "")
        base_for_part = base_by_part.get(item_id) or []
        initial_base = base_for_part[0] if base_for_part else None
        final_base = base_for_part[-1] if len(base_for_part) > 1 else None
        initial_grade = initial_base.get("grade") if isinstance(initial_base, dict) else None
        final_grade = final_base.get("grade") if isinstance(final_base, dict) else None
        initial_attr_label = f"基础属性（{initial_grade}境）" if initial_grade not in (None, "") else "基础属性"
        final_attr_label = f"满境属性（{final_grade}境）" if final_grade not in (None, "") else "满境属性"
        initial_attr = _format_spiritware_attr_entries(
            initial_attr_label,
            _spiritware_attr_entries(initial_base.get("attr") if isinstance(initial_base, dict) else None, attr_meta_by_key),
        )
        final_attr = _format_spiritware_attr_entries(
            final_attr_label,
            _spiritware_attr_entries(final_base.get("attr") if isinstance(final_base, dict) else None, attr_meta_by_key),
        )
        cleanse_item = _parse_medical_item_token(part.get("cleanseItem"), items_by_id)
        ultra_for_part = ultra_by_part.get(item_id) or []
        ultra_first = ultra_for_part[0] if ultra_for_part else None
        ultra_final = ultra_for_part[-1] if len(ultra_for_part) > 1 else None
        ultra_lines = [
            _text_value(ultra_first, "nodeText") if isinstance(ultra_first, dict) else "",
            _text_value(ultra_final, "nodeText") if isinstance(ultra_final, dict) else "",
        ]
        ultra_text = "；".join(dict.fromkeys(part for part in ultra_lines if part))
        lines = [
            f"所属灵器：{ware_name}",
            f"部位：{part_no}" if part_no is not None else "",
            f"部件品质：{quality_name}" if quality_name else "",
            initial_attr,
            final_attr if final_attr and final_attr != initial_attr else "",
            f"洗灵消耗：{_linked_item_text(cleanse_item)}" if cleanse_item else "",
            f"洗灵初始词条数：{part.get('initialNum')}" if part.get("initialNum") not in (None, "") else "",
            f"无双节点：{ultra_text}" if ultra_text else "",
        ]
        description = "\n".join(part for part in lines if part)
        if not description:
            continue
        add_detail(
            item_id,
            {
                "kind": "spiritware_part",
                "title": _text_value(item_row, "name") or f"灵器部件 {item_id}",
                "subtitle": " · ".join(part for part in (ware_name, f"部位{part_no}" if part_no is not None else "", quality_name) if part),
                "description": description,
                "plain_description": description,
                "source": "SpiritWare.SpiritWareItem",
                "source_id": item_id,
                "spiritware_item_id": item_id,
                "spiritware_type": ware_type,
                "spiritware_name": ware_name,
                "spiritware_part": part_no,
                "spiritware_quality": quality,
                "spiritware_quality_name": quality_name,
                "spiritware_base_attr_text": initial_attr,
                "spiritware_max_attr_text": final_attr,
                "spiritware_cleanse_item_text": _linked_item_text(cleanse_item),
                "spiritware_ultra_text": ultra_text,
            },
        )

    cleanse_use_by_item: dict[int, dict[str, Any]] = {}
    for part in spiritware_item_rows:
        item_id, count = _item_token_id_count(part.get("cleanseItem"))
        if item_id is None:
            continue
        usage = cleanse_use_by_item.setdefault(item_id, {"part_count": 0, "counts": set()})
        usage["part_count"] += 1
        if count is not None:
            usage["counts"].add(count)

    for item_id, usage in cleanse_use_by_item.items():
        item_row = items_by_id.get(item_id) or {"id": item_id, "name": item_id}
        counts = sorted(usage.get("counts") or [])
        count_text = "、".join(str(count) for count in counts)
        lines = [
            "用于洗炼灵器部位。",
            f"覆盖部件：{usage.get('part_count')} 个",
            f"单次消耗数量：{count_text}" if count_text else "",
        ]
        description = "\n".join(part for part in lines if part)
        add_detail(
            item_id,
            {
                "kind": "spiritware_cleanse_material",
                "title": "灵器通用洗炼材料",
                "subtitle": "灵器部件洗灵",
                "description": description,
                "plain_description": description,
                "source": "SpiritWare.SpiritWareItem.cleanseItem",
                "source_id": item_id,
                "spiritware_item_id": item_id,
                "spiritware_cleanse_part_count": usage.get("part_count"),
                "spiritware_cleanse_item_text": _text_value(item_row, "name") or str(item_id),
            },
        )

    ultra_material_use_by_item: dict[int, list[dict[str, Any]]] = {}
    ultra_material_counts: dict[int, set[int]] = {}
    for row in ultra_rows:
        if not isinstance(row, dict):
            continue
        item_id, count = _item_token_id_count(row.get("consume"))
        part_item = _as_int(row.get("partsItem"))
        if item_id is None or part_item is None or item_id == part_item:
            continue
        ultra_material_use_by_item.setdefault(item_id, []).append(row)
        if count is not None:
            ultra_material_counts.setdefault(item_id, set()).add(count)
    for rows in ultra_material_use_by_item.values():
        rows.sort(
            key=lambda item: (
                _sort_value(item.get("partsItem")),
                _sort_value(item.get("grade")),
                _sort_value(item.get("id") or item.get("_row_key")),
            )
        )

    for item_id, rows in ultra_material_use_by_item.items():
        if item_id in details_by_item_id:
            continue
        item_row = items_by_id.get(item_id) or {"id": item_id, "name": item_id}
        target_part_ids = list(dict.fromkeys(_as_int(row.get("partsItem")) for row in rows if _as_int(row.get("partsItem")) is not None))
        target_part_names = [
            _text_value(items_by_id.get(part_id) or {"id": part_id, "name": part_id}, "name")
            for part_id in target_part_ids
        ]
        target_part_text = "、".join(part for part in target_part_names if part)
        ware_names: list[str] = []
        for part_id in target_part_ids:
            part_config = spiritware_item_by_id.get(part_id)
            ware_type = _as_int(part_config.get("type")) if isinstance(part_config, dict) else None
            ware = ware_by_type.get(ware_type)
            ware_name = _text_value(ware or {}, "name")
            if ware_name and ware_name not in ware_names:
                ware_names.append(ware_name)
        ware_name_text = "、".join(ware_names)
        grade_values = [row.get("grade") for row in rows if row.get("grade") not in (None, "")]
        first_row = rows[0] if rows else None
        final_row = rows[-1] if len(rows) > 1 else None
        first_node = _text_value(first_row, "nodeText") if isinstance(first_row, dict) else ""
        final_node = _text_value(final_row, "nodeText") if isinstance(final_row, dict) else ""
        node_text = "；".join(dict.fromkeys(part for part in (first_node, final_node) if part))
        first_attr = _format_spiritware_attr_entries(
            f"{first_row.get('grade')}境属性" if isinstance(first_row, dict) and first_row.get("grade") not in (None, "") else "初始突破属性",
            _spiritware_attr_entries(first_row.get("attr") if isinstance(first_row, dict) else None, attr_meta_by_key),
        )
        final_attr = _format_spiritware_attr_entries(
            f"{final_row.get('grade')}境属性" if isinstance(final_row, dict) and final_row.get("grade") not in (None, "") else "最高突破属性",
            _spiritware_attr_entries(final_row.get("attr") if isinstance(final_row, dict) else None, attr_meta_by_key),
        )
        count_text = "、".join(str(count) for count in sorted(ultra_material_counts.get(item_id) or []))
        lines = [
            "用于灵器部件升境/突破。",
            f"所属灵器：{ware_name_text}" if ware_name_text else "",
            f"目标部件：{target_part_text}" if target_part_text else "",
            f"覆盖境界：{min(grade_values)}-{max(grade_values)}境" if grade_values else "",
            f"单次消耗数量：{count_text}" if count_text else "",
            node_text,
            first_attr,
            final_attr if final_attr and final_attr != first_attr else "",
        ]
        description = "\n".join(part for part in lines if part)
        if not description:
            continue
        add_detail(
            item_id,
            {
                "kind": "spiritware_ultra_material",
                "title": _text_value(item_row, "name") or "灵器升境材料",
                "subtitle": f"{ware_name_text} · 升境材料" if ware_name_text else "灵器升境材料",
                "description": description,
                "plain_description": _plain_rich_text(description),
                "source": "SpiritWare.SpiritWareUltra.consume",
                "source_id": item_id,
                "spiritware_item_id": item_id,
                "spiritware_name": ware_name_text,
                "spiritware_target_part_text": target_part_text,
                "spiritware_ultra_material_text": _text_value(item_row, "name") or str(item_id),
                "spiritware_ultra_text": node_text,
            },
        )

    soul_by_item: dict[int, list[dict[str, Any]]] = {}
    for row in soul_rows:
        if not isinstance(row, dict):
            continue
        item_id, _count = _item_token_id_count(row.get("consume"))
        if item_id is not None:
            soul_by_item.setdefault(item_id, []).append(row)
    for rows in soul_by_item.values():
        rows.sort(key=lambda item: (_sort_value(item.get("grade")), _sort_value(item.get("id") or item.get("_row_key"))))

    for item_id, rows in soul_by_item.items():
        item_row = items_by_id.get(item_id) or {"id": item_id, "name": item_id}
        ware_names = list(dict.fromkeys(_text_value(row, "name") for row in rows if _text_value(row, "name")))
        ware_name_text = "、".join(ware_names)
        grade_values = [row.get("grade") for row in rows if row.get("grade") not in (None, "")]
        stage_lines: list[str] = []
        skill_ids: list[str] = []
        for row in rows:
            attr_text = _format_spiritware_attr_entries("属性", _spiritware_attr_entries(row.get("attr"), attr_meta_by_key))
            skill_desc = _rich_text_value(row, "skillDesc") or _text_value(row, "skillDesc")
            plain_skill_desc = _plain_rich_text(skill_desc)
            title = _text_value(row, "text") or f"{ware_name_text}·器灵{row.get('grade')}"
            pieces = [title, attr_text, skill_desc]
            stage_lines.append("：".join([pieces[0], "；".join(part for part in pieces[1:] if part)]))
            if row.get("skill") not in (None, ""):
                skill_ids.append(str(row.get("skill")))
            if plain_skill_desc and plain_skill_desc != skill_desc:
                pass
        description = "\n".join(
            part
            for part in (
                f"所属灵器：{ware_name_text}" if ware_name_text else "",
                f"器灵阶数：{min(grade_values)}-{max(grade_values)}阶" if grade_values else "",
                *stage_lines,
            )
            if part
        )
        if not description:
            continue
        add_detail(
            item_id,
            {
                "kind": "spiritware_soul",
                "title": _text_value(item_row, "name") or f"器灵 {item_id}",
                "subtitle": f"{ware_name_text} · {len(rows)}阶器灵" if ware_name_text else f"{len(rows)}阶器灵",
                "description": description,
                "plain_description": _plain_rich_text(description),
                "source": "SpiritWare.SpiritWareSoul",
                "source_id": item_id,
                "spiritware_item_id": item_id,
                "spiritware_name": ware_name_text,
                "spiritware_soul_grade_count": len(rows),
                "spiritware_skill_ids": "、".join(skill_ids),
            },
        )

    for row in cleanse_item_rows:
        if not isinstance(row, dict):
            continue
        item_id = _as_int(row.get("item"))
        if item_id is None:
            continue
        item_row = items_by_id.get(item_id) or {"id": item_id, "name": item_id}
        type_key = str(row.get("type") or "")
        type_label = SPIRITWARE_CLEANSE_ITEM_TYPE_LABELS.get(type_key) or (f"类型 {type_key}" if type_key else "")
        short_desc = _rich_text_value(row, "shortDes") or _text_value(row, "shortDes")
        use_desc = _rich_text_value(row, "useDes") or _text_value(row, "useDes")
        description = "\n".join(
            part
            for part in (
                f"洗灵用途：{type_label}" if type_label else "",
                short_desc,
                use_desc,
            )
            if part
        )
        if not description:
            continue
        add_detail(
            item_id,
            {
                "kind": "spiritware_cleanse_item",
                "title": _text_value(item_row, "name") or "灵器洗灵材料",
                "subtitle": type_label or "灵器洗灵",
                "description": description,
                "plain_description": _plain_rich_text(description),
                "source": "SpiritWare.SpiritWareCleanseItem",
                "source_id": row.get("id") or row.get("_row_key"),
                "spiritware_item_id": item_id,
                "spiritware_cleanse_type": row.get("type"),
                "spiritware_cleanse_type_label": type_label,
                "spiritware_cleanse_limit_type": row.get("limitType"),
            },
        )

    return details_by_item_id, {
        "spiritware_source": str(spiritware_path),
        "spiritware_item_source": str(item_path),
        "spiritware_base_source": str(base_path or ""),
        "spiritware_ultra_source": str(ultra_path or ""),
        "spiritware_soul_source": str(soul_path or ""),
        "spiritware_cleanse_item_source": str(cleanse_item_path or ""),
        "spiritware_attribute_source": str(attribute_path or ""),
        "spiritware_row_count": len(spiritware_rows),
        "spiritware_item_row_count": len(spiritware_item_rows),
        "spiritware_base_row_count": len(base_rows),
        "spiritware_ultra_row_count": len(ultra_rows),
        "spiritware_soul_row_count": len(soul_rows),
        "spiritware_cleanse_item_row_count": len(cleanse_item_rows),
        "spiritware_detail_count": len(details_by_item_id),
        "spiritware_part_detail_count": detail_kind_counts.get("spiritware_part", 0),
        "spiritware_soul_detail_count": detail_kind_counts.get("spiritware_soul", 0),
        "spiritware_cleanse_item_detail_count": detail_kind_counts.get("spiritware_cleanse_item", 0),
        "spiritware_cleanse_material_detail_count": detail_kind_counts.get("spiritware_cleanse_material", 0),
        "spiritware_ultra_material_detail_count": detail_kind_counts.get("spiritware_ultra_material", 0),
    }


def _build_title_effect_details_by_id(
    root: Path,
    quality_by_id: dict[int, dict[str, Any]] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]], dict[str, Any]]:
    title_path = _find_title_lua(root, "Title.lua")
    if title_path is None:
        return {}, {}, {"title_source": "", "attribute_source": "", "title_detail_count": 0, "title_item_link_count": 0}

    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    title_rows = list(parse_fanxiu_generated_lua_config(title_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    attribute_rows = (
        list(parse_fanxiu_generated_lua_config(attribute_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if attribute_path is not None
        else []
    )
    attr_name_by_key = {
        str(row.get("id")): _text_value(row, "name")
        for row in attribute_rows
        if isinstance(row, dict) and row.get("id") not in (None, "") and _text_value(row, "name")
    }

    details_by_id: dict[int, dict[str, Any]] = {}
    details_by_item_id: dict[int, dict[str, Any]] = {}
    for row in title_rows:
        if not isinstance(row, dict):
            continue
        title_id = _as_int(row.get("id") or row.get("_row_key"))
        if title_id is None:
            continue
        title_text = _rich_text_value(row, "titleText")
        description = _rich_text_value(row, "descript")
        tips = _rich_text_value(row, "tips")
        condition = _rich_text_value(row, "condition")
        attr_entries = _title_attr_entries(row.get("attr"), attr_name_by_key)
        wear_entries = _title_attr_entries(row.get("wear"), attr_name_by_key)
        attr_text = _format_title_attr_text("属性", attr_entries)
        wear_text = _format_title_attr_text("佩戴属性", wear_entries)
        effect_params = _text_value(row, "effectParams")
        effect_type = row.get("effectType")

        description_parts = [
            f"称号文本：{title_text}" if title_text else "",
            description,
            f"获取：{tips}" if tips else "",
            f"条件：{condition}" if condition else "",
            attr_text,
            wear_text,
            f"特殊效果：type={effect_type} params={effect_params}" if effect_type not in (None, "", 0) or effect_params else "",
        ]
        description_text = "\n".join(part for part in description_parts if part)
        if not description_text:
            continue

        quality_id = _as_int(row.get("quality"))
        quality_name = (quality_by_id or {}).get(quality_id or -1, {}).get("quality_name", "") if quality_id is not None else ""
        detail: dict[str, Any] = {
            "kind": "title",
            "title": _text_value(row, "name") or f"称号 {title_id}",
            "subtitle": quality_name,
            "description": description_text,
            "plain_description": description_text,
            "tips": tips,
            "attr_text": " ".join(part for part in (attr_text, wear_text) if part),
            "attr_entries": attr_entries,
            "wear_attr_entries": wear_entries,
            "source": "Title.Title",
            "source_id": title_id,
            "quality": quality_id,
            "quality_name": quality_name,
            "title_effect": row.get("titleEffect"),
            "effect_type": effect_type,
            "effect_params": effect_params,
        }
        compact_detail = {key: value for key, value in detail.items() if value not in (None, "", [], {})}
        details_by_id[title_id] = compact_detail
        raw_item_ids = row.get("itemId")
        item_id_values = raw_item_ids if isinstance(raw_item_ids, list) else [raw_item_ids]
        for raw_item_id in item_id_values:
            item_id = _as_int(raw_item_id)
            if item_id is not None:
                details_by_item_id[item_id] = compact_detail

    return details_by_id, details_by_item_id, {
        "title_source": str(title_path),
        "attribute_source": str(attribute_path or ""),
        "title_row_count": len(title_rows),
        "attribute_row_count": len(attribute_rows),
        "title_detail_count": len(details_by_id),
        "title_item_link_count": len(details_by_item_id),
    }


def _build_title_local_fallback_detail(
    row: dict[str, Any],
    linked_title_id: int | None,
) -> dict[str, Any] | None:
    if str(row.get("type")) != "15":
        return None
    title_name = _text_value(row, "name")
    description = _rich_text_value(row, "descript") or _text_value(row, "descript")
    effect_text = _rich_text_value(row, "effDescript") or _text_value(row, "effDescript")
    if not any((title_name, description, effect_text)):
        return None

    effect_value = row.get("effectValue")
    parts = [
        f"称号：{title_name}" if title_name else "",
        description,
        f"使用效果：{effect_text}" if effect_text and effect_text != description else "",
    ]
    if linked_title_id is not None:
        parts.append(f"Title引用：{linked_title_id}（当前 Title.Title 未找到对应行）")
    elif effect_value not in (None, ""):
        parts.append(f"原始effectValue：{effect_value}")
    description_text = "\n".join(part for part in parts if part)
    item_id = row.get("id") or row.get("_row_key")
    return {
        "kind": "title_item_local",
        "title": title_name or f"称号道具 {item_id}",
        "subtitle": "称号 · 本地道具文案",
        "description": description_text,
        "plain_description": _plain_rich_text(description_text),
        "source": "Item.title_local_fallback",
        "source_id": item_id,
        "title_effect_value": effect_value,
    }


def _build_fashion_effect_details_by_id(root: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    fashion_path = _find_fashion_lua(root, "Fashion.lua")
    if fashion_path is None:
        return {}, {"fashion_source": "", "fashion_level_source": "", "fashion_type_source": "", "fashion_detail_count": 0}

    level_path = _find_fashion_lua(root, "FashionLevel.lua")
    type_path = _find_fashion_lua(root, "FashionType.lua")
    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    fashion_rows = list(parse_fanxiu_generated_lua_config(fashion_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    level_rows = (
        list(parse_fanxiu_generated_lua_config(level_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if level_path is not None
        else []
    )
    type_rows = (
        list(parse_fanxiu_generated_lua_config(type_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if type_path is not None
        else []
    )
    attribute_rows = (
        list(parse_fanxiu_generated_lua_config(attribute_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if attribute_path is not None
        else []
    )
    attr_name_by_key = {
        str(row.get("id")): _text_value(row, "name")
        for row in attribute_rows
        if isinstance(row, dict) and row.get("id") not in (None, "") and _text_value(row, "name")
    }
    type_name_by_id = {
        type_id: _text_value(row, "name")
        for row in type_rows
        if isinstance(row, dict) and (type_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    levels_by_fashion: dict[int, list[dict[str, Any]]] = {}
    for row in level_rows:
        if not isinstance(row, dict):
            continue
        fashion_id = _as_int(row.get("fashion"))
        if fashion_id is None:
            continue
        levels_by_fashion.setdefault(fashion_id, []).append(row)
    for rows in levels_by_fashion.values():
        rows.sort(key=lambda item: (_sort_value(item.get("level")), _sort_value(item.get("id") or item.get("_row_key"))))

    details_by_id: dict[int, dict[str, Any]] = {}
    for row in fashion_rows:
        if not isinstance(row, dict):
            continue
        fashion_id = _as_int(row.get("id") or row.get("_row_key"))
        if fashion_id is None:
            continue
        levels = levels_by_fashion.get(fashion_id) or []
        init_level = _as_int(row.get("initLevel"))
        init_row = next((item for item in levels if _as_int(item.get("level")) == init_level), None) or (levels[0] if levels else None)
        max_row = levels[-1] if levels else None
        type_name = type_name_by_id.get(_as_int(row.get("type")) or -1, "")
        condition = _rich_text_value(row, "condition")
        model_text = "；".join(
            part
            for part in (
                f"男模 {row.get('modelMan')}" if row.get("modelMan") not in (None, "", 0) else "",
                f"女模 {row.get('modelWoman')}" if row.get("modelWoman") not in (None, "", 0) else "",
            )
            if part
        )
        init_attr_entries = _title_attr_entries(init_row.get("attr") if isinstance(init_row, dict) else None, attr_name_by_key)
        max_attr_entries = _title_attr_entries(max_row.get("attr") if isinstance(max_row, dict) else None, attr_name_by_key)
        init_wear_entries = _title_attr_entries(init_row.get("wear") if isinstance(init_row, dict) else None, attr_name_by_key)
        max_wear_entries = _title_attr_entries(max_row.get("wear") if isinstance(max_row, dict) else None, attr_name_by_key)
        init_attr_text = _format_title_attr_text("初始属性", init_attr_entries)
        max_attr_text = _format_title_attr_text("满级属性", max_attr_entries) if max_row is not init_row else ""
        init_wear_text = _format_title_attr_text("初始穿戴属性", init_wear_entries)
        max_wear_text = _format_title_attr_text("满级穿戴属性", max_wear_entries) if max_row is not init_row else ""
        talk_text = "；".join(
            part
            for part in (
                _rich_text_value(row, "upTalk"),
                _rich_text_value(row, "midTalk"),
                _rich_text_value(row, "downTalk"),
            )
            if part
        )
        level_summary = ""
        if levels:
            first_level = levels[0].get("level")
            last_level = levels[-1].get("level")
            level_summary = f"等级 {first_level}" if first_level == last_level else f"等级 {first_level}-{last_level}"
        description_parts = [
            f"类型：{type_name}" if type_name else "",
            f"解锁条件：{condition}" if condition else "",
            _rich_text_value(row, "describe"),
            _rich_text_value(init_row, "describe") if isinstance(init_row, dict) else "",
            init_attr_text,
            init_wear_text,
            max_attr_text,
            max_wear_text,
            f"模型：{model_text}" if model_text else "",
            f"展示话术：{talk_text}" if talk_text else "",
        ]
        description = "\n".join(part for part in description_parts if part)
        if not description:
            continue
        detail: dict[str, Any] = {
            "kind": "fashion",
            "title": _text_value(row, "name") or f"外观 {fashion_id}",
            "subtitle": " · ".join(part for part in (type_name, level_summary) if part),
            "description": description,
            "plain_description": description,
            "condition": condition,
            "attr_text": " ".join(part for part in (init_attr_text, init_wear_text, max_attr_text, max_wear_text) if part),
            "attr_entries": init_attr_entries,
            "wear_attr_entries": init_wear_entries,
            "source": "Fashion.Fashion",
            "source_id": fashion_id,
            "type_label": type_name,
            "model_text": model_text,
            "level_count": len(levels),
            "init_level": init_level,
            "max_level": max_row.get("level") if isinstance(max_row, dict) else None,
            "item_id": row.get("item"),
            "model_man": row.get("modelMan"),
            "model_woman": row.get("modelWoman"),
            "suit": row.get("suit"),
        }
        details_by_id[fashion_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_id, {
        "fashion_source": str(fashion_path),
        "fashion_level_source": str(level_path or ""),
        "fashion_type_source": str(type_path or ""),
        "fashion_row_count": len(fashion_rows),
        "fashion_level_row_count": len(level_rows),
        "fashion_type_row_count": len(type_rows),
        "fashion_detail_count": len(details_by_id),
    }


def _build_gongfa_effect_details_by_id(root: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    gongfa_path = _find_gongfa_lua(root, "Gongfa.lua")
    if gongfa_path is None:
        return {}, {
            "gongfa_source": "",
            "gongfa_pin_source": "",
            "gongfa_career_source": "",
            "gongfa_detail_count": 0,
        }

    pin_path = _find_gongfa_lua(root, "GongfaPin.lua")
    career_path = _find_gongfa_lua(root, "GongfaCareer.lua")
    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    gongfa_rows = list(parse_fanxiu_generated_lua_config(gongfa_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    pin_rows = (
        list(parse_fanxiu_generated_lua_config(pin_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if pin_path is not None
        else []
    )
    career_rows = (
        list(parse_fanxiu_generated_lua_config(career_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if career_path is not None
        else []
    )
    attribute_rows = (
        list(parse_fanxiu_generated_lua_config(attribute_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if attribute_path is not None
        else []
    )
    attr_name_by_key = {
        str(row.get("id")): _text_value(row, "name")
        for row in attribute_rows
        if isinstance(row, dict) and row.get("id") not in (None, "") and _text_value(row, "name")
    }
    pin_by_id = {
        pin_id: row
        for row in pin_rows
        if isinstance(row, dict) and (pin_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    career_by_id = {
        career_id: row
        for row in career_rows
        if isinstance(row, dict) and (career_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }

    details_by_id: dict[int, dict[str, Any]] = {}
    for row in gongfa_rows:
        if not isinstance(row, dict):
            continue
        gongfa_id = _as_int(row.get("id") or row.get("_row_key"))
        if gongfa_id is None:
            continue
        pin = pin_by_id.get(_as_int(row.get("quality")) or -1)
        pin_name = _text_value(pin or {}, "name") if isinstance(pin, dict) else ""
        type_name = _text_value(pin or {}, "typeName") if isinstance(pin, dict) else ""
        career_id = _as_int((pin or {}).get("typeId") if isinstance(pin, dict) else None)
        career = career_by_id.get(career_id or -1)
        career_name = _text_value(career or {}, "name") if isinstance(career, dict) else ""
        career_desc = _rich_text_value(career, "careerDesc")
        career_desc_long = _rich_text_value(career, "careerDesc1")
        attr_entries = _title_attr_entries(row.get("attr"), attr_name_by_key)
        attr_text = _format_title_attr_text("基础属性", attr_entries)
        condition = _rich_text_value(row, "condition")
        consume = row.get("consume")
        consume_text = ""
        if isinstance(consume, list):
            consume_text = "；".join(str(item) for item in consume if item not in (None, ""))
        elif consume not in (None, ""):
            consume_text = str(consume)

        description_parts = [
            f"功法品阶：{pin_name}" if pin_name else "",
            f"流派：{type_name or career_name}" if (type_name or career_name) else "",
            f"职业特性：{career_desc}" if career_desc else "",
            career_desc_long,
            _rich_text_value(row, "descript"),
            attr_text,
            f"解锁条件：{condition}" if condition else "",
            f"学习消耗：{consume_text}" if consume_text else "",
            f"功法经验：{row.get('gongfaExp')}" if row.get("gongfaExp") not in (None, "", 0) else "",
            f"等级组：{row.get('levelGroup')}" if row.get("levelGroup") not in (None, "", 0) else "",
            f"模型：{row.get('model')}" if row.get("model") not in (None, "", 0) else "",
        ]
        description = "\n".join(part for part in description_parts if part)
        if not description:
            continue
        detail: dict[str, Any] = {
            "kind": "gongfa_book",
            "title": _text_value(row, "name") or f"功法 {gongfa_id}",
            "subtitle": " · ".join(part for part in (pin_name, type_name or career_name) if part),
            "description": description,
            "plain_description": description,
            "condition": condition,
            "attr_text": attr_text,
            "attr_entries": attr_entries,
            "source": "Gongfa.Gongfa",
            "source_id": gongfa_id,
            "quality": row.get("quality"),
            "quality_name": pin_name,
            "type_label": type_name or career_name,
            "career_id": career_id,
            "career_name": career_name,
            "level_group": row.get("levelGroup"),
            "gongfa_exp": row.get("gongfaExp"),
            "model": row.get("model"),
            "consume_text": consume_text,
        }
        details_by_id[gongfa_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_id, {
        "gongfa_source": str(gongfa_path),
        "gongfa_pin_source": str(pin_path or ""),
        "gongfa_career_source": str(career_path or ""),
        "gongfa_row_count": len(gongfa_rows),
        "gongfa_pin_row_count": len(pin_rows),
        "gongfa_career_row_count": len(career_rows),
        "gongfa_detail_count": len(details_by_id),
    }


def _build_gongfa_jie_book_effect_details_by_id(root: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    gongfa_path = _find_gongfa_lua(root, "Gongfa.lua")
    jie_path = _find_gongfa_lua(root, "Renjie-GongfaJie.lua")
    skill_path = _find_gongfa_lua(root, "GongfaSkill.lua")
    if gongfa_path is None or jie_path is None:
        return {}, {
            "gongfa_jie_book_source": str(jie_path or ""),
            "gongfa_jie_book_gongfa_source": str(gongfa_path or ""),
            "gongfa_jie_book_skill_source": str(skill_path or ""),
            "gongfa_jie_book_detail_count": 0,
        }

    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    gongfa_rows = list(parse_fanxiu_generated_lua_config(gongfa_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    jie_rows = list(parse_fanxiu_generated_lua_config(jie_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    skill_rows = (
        list(parse_fanxiu_generated_lua_config(skill_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if skill_path is not None
        else []
    )
    attribute_rows = (
        list(parse_fanxiu_generated_lua_config(attribute_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if attribute_path is not None
        else []
    )
    attr_name_by_key = {
        str(row.get("id")): _text_value(row, "name")
        for row in attribute_rows
        if isinstance(row, dict) and row.get("id") not in (None, "") and _text_value(row, "name")
    }
    gongfa_by_id = {
        gongfa_id: row
        for row in gongfa_rows
        if isinstance(row, dict) and (gongfa_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }

    jie_rows_by_effect_id: dict[int, list[dict[str, Any]]] = {}
    for row in jie_rows:
        if not isinstance(row, dict):
            continue
        row_id = _as_int(row.get("id") or row.get("_row_key"))
        if row_id is None or row_id <= 0:
            continue
        effect_id = row_id // 1000
        suffix = row_id - effect_id * 1000
        # Only the direct 1..200 重 block belongs to this book. Higher suffixes under
        # the same numeric prefix are chained/side unlock rows and would over-associate.
        if 1 <= suffix <= 200:
            jie_rows_by_effect_id.setdefault(effect_id, []).append(row)
    for rows in jie_rows_by_effect_id.values():
        rows.sort(key=lambda item: (_sort_value(item.get("jie")), _sort_value(item.get("id") or item.get("_row_key"))))

    skill_rows_by_effect_id: dict[int, list[dict[str, Any]]] = {}
    for row in skill_rows:
        if not isinstance(row, dict):
            continue
        skill_id = str(row.get("id") or row.get("_row_key") or "")
        match = re.fullmatch(r"(\d+)000_(\d+)", skill_id)
        if not match:
            continue
        skill_rows_by_effect_id.setdefault(int(match.group(1)), []).append(row)
    for rows in skill_rows_by_effect_id.values():
        rows.sort(key=lambda item: (_sort_value(item.get("sort")), str(item.get("id") or item.get("_row_key") or "")))

    details_by_id: dict[int, dict[str, Any]] = {}
    for effect_id, direct_jie_rows in jie_rows_by_effect_id.items():
        if not direct_jie_rows:
            continue
        gid = _as_int(direct_jie_rows[0].get("gid"))
        gongfa = gongfa_by_id.get(gid or -1)
        skills = skill_rows_by_effect_id.get(effect_id) or []
        if gongfa is None and not skills:
            continue

        gongfa_name = _text_value(gongfa or {}, "name")
        skill_name = _text_value(skills[0], "skillName") if skills else ""
        first_jie = direct_jie_rows[0]
        max_jie = direct_jie_rows[-1]
        first_attr_entries = _title_attr_entries(first_jie.get("attr"), attr_name_by_key)
        max_attr_entries = _title_attr_entries(max_jie.get("attr"), attr_name_by_key)
        first_attr_text = _format_title_attr_text("初始重数属性", first_attr_entries)
        max_attr_text = _format_title_attr_text("最高重数属性", max_attr_entries) if max_jie is not first_jie else ""
        jie_descriptions = [
            _rich_text_value(row, "describe")
            for row in direct_jie_rows
            if _rich_text_value(row, "describe")
        ]
        top_descriptions = []
        for row in direct_jie_rows:
            text = _rich_text_value(row, "topDescribe")
            if text and text not in top_descriptions:
                top_descriptions.append(text)
            if len(top_descriptions) >= 3:
                break
        consume_values = []
        for row in direct_jie_rows:
            consume = row.get("consume")
            if isinstance(consume, list):
                consume_values.extend(str(item) for item in consume if item not in (None, ""))
            elif consume not in (None, ""):
                consume_values.append(str(consume))
        consume_text = "；".join(dict.fromkeys(consume_values[:8]))
        first_skill = skills[0] if skills else None
        max_skill = skills[-1] if skills else None
        first_skill_text = ""
        max_skill_text = ""
        if first_skill:
            first_skill_text = "：".join(
                part
                for part in (
                    _text_value(first_skill, "effectDescribe"),
                    _rich_text_value(first_skill, "describe"),
                )
                if part
            )
        if max_skill and max_skill is not first_skill:
            max_skill_text = "：".join(
                part
                for part in (
                    _text_value(max_skill, "effectDescribe"),
                    _rich_text_value(max_skill, "describe"),
                )
                if part
            )
        origin_text = _rich_text_value(first_skill, "origin") if first_skill else ""
        additional_text = _rich_text_value(first_skill, "additionalDescribe") if first_skill else ""

        description_parts = [
            f"关联功法：{gongfa_name}（Gongfa {gid}）" if gongfa_name and gid is not None else "",
            f"战斗神通：{skill_name}" if skill_name else "",
            _rich_text_value(gongfa, "descript"),
            f"重数范围：{first_jie.get('name_plain') or first_jie.get('name') or first_jie.get('jie')} ~ {max_jie.get('name_plain') or max_jie.get('name') or max_jie.get('jie')}，共 {len(direct_jie_rows)} 重",
            first_attr_text,
            max_attr_text,
            f"重数效果：\n" + "\n".join(jie_descriptions[:12]) if jie_descriptions else "",
            f"激活提示：{'；'.join(top_descriptions)}" if top_descriptions else "",
            f"重数消耗：{consume_text}" if consume_text else "",
            f"初阶神通：{first_skill_text}" if first_skill_text else "",
            f"最高阶神通：{max_skill_text}" if max_skill_text else "",
            f"神通来源：{origin_text}" if origin_text else "",
            additional_text,
        ]
        description = "\n".join(part for part in description_parts if part)
        if not description:
            continue
        detail: dict[str, Any] = {
            "kind": "gongfa_jie_book",
            "title": gongfa_name or skill_name or f"功法重数 {effect_id}",
            "subtitle": " · ".join(part for part in (f"effectValue {effect_id}", skill_name) if part),
            "description": description,
            "plain_description": _plain_rich_text(description),
            "source": "Renjie-GongfaJie + GongfaSkill",
            "source_id": effect_id,
            "gongfa_jie_effect_id": effect_id,
            "gongfa_jie_gid": gid,
            "gongfa_jie_name": gongfa_name,
            "gongfa_jie_skill_name": skill_name,
            "gongfa_jie_count": len(direct_jie_rows),
            "gongfa_skill_stage_count": len(skills),
            "attr_entries": first_attr_entries,
            "max_attr_entries": max_attr_entries,
            "stage_text": "\n".join(jie_descriptions[:12]),
            "skill_text": "\n".join(part for part in (first_skill_text, max_skill_text) if part),
            "consume_text": consume_text,
        }
        details_by_id[effect_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_id, {
        "gongfa_jie_book_source": str(jie_path),
        "gongfa_jie_book_gongfa_source": str(gongfa_path),
        "gongfa_jie_book_skill_source": str(skill_path or ""),
        "gongfa_jie_book_attribute_source": str(attribute_path or ""),
        "gongfa_jie_book_gongfa_row_count": len(gongfa_rows),
        "gongfa_jie_book_jie_row_count": len(jie_rows),
        "gongfa_jie_book_skill_row_count": len(skill_rows),
        "gongfa_jie_book_detail_count": len(details_by_id),
    }


def _probe_tsv_values(value: Any, *, limit: int | None = None) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    values = [part.strip() for part in re.split(r"[、,，;；\n]+", text) if part.strip()]
    return values[:limit] if limit is not None else values


def _probe_tsv_int(value: Any) -> int:
    number = _as_int(value)
    return number if number is not None else 0


def _build_gongfa_feature_probe_book_details_by_id(root: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    family_path = root / DEFAULT_GONGFA_FEATURE_FAMILIES
    link_path = root / DEFAULT_GONGFA_FEATURE_LINKS
    if not family_path.is_file():
        return {}, {
            "gongfa_feature_probe_family_source": str(family_path),
            "gongfa_feature_probe_link_source": str(link_path if link_path.is_file() else ""),
            "gongfa_feature_probe_detail_count": 0,
            "gongfa_feature_probe_family_row_count": 0,
            "gongfa_feature_probe_link_row_count": 0,
        }

    with family_path.open("r", encoding="utf-8-sig", newline="") as file:
        family_rows = [dict(row) for row in csv.DictReader(file, delimiter="\t") if isinstance(row, dict)]

    link_rows: list[dict[str, str]] = []
    if link_path.is_file():
        with link_path.open("r", encoding="utf-8-sig", newline="") as file:
            link_rows = [dict(row) for row in csv.DictReader(file, delimiter="\t") if isinstance(row, dict)]

    links_by_gid: dict[int, list[dict[str, str]]] = {}
    for row in link_rows:
        gid = _as_int(row.get("source_gid"))
        feature = str(row.get("feature") or "").strip()
        if gid is None or not feature:
            continue
        links_by_gid.setdefault(gid, []).append(row)
    for rows in links_by_gid.values():
        rows.sort(key=lambda item: (_sort_value(item.get("source_jie")), _sort_value(item.get("feature")), str(item.get("feature") or "")))

    details_by_id: dict[int, dict[str, Any]] = {}
    for row in family_rows:
        source_gid = _as_int(row.get("source_gid"))
        if source_gid is None:
            continue
        linked_item_names = _probe_tsv_values(row.get("linked_item_names"), limit=3)
        linked_item_ids = _probe_tsv_values(row.get("linked_item_ids"), limit=5)
        feature_prefixes = _probe_tsv_values(row.get("feature_prefixes"), limit=8)
        feature_ids = _probe_tsv_values(row.get("features"), limit=12)
        source_names = _probe_tsv_values(row.get("source_names"), limit=8)
        candidate_ids = _probe_tsv_values(row.get("candidate_ids"), limit=12)
        status = str(row.get("status") or "").strip()
        source_describe = str(row.get("source_describe") or "").strip()

        stage_lines: list[str] = []
        seen_stage_lines: set[str] = set()
        feature_link_rows: list[dict[str, Any]] = []
        for link in links_by_gid.get(source_gid, []):
            describe = str(link.get("source_describe") or "").strip()
            if describe and describe not in seen_stage_lines:
                seen_stage_lines.add(describe)
                stage_lines.append(describe)
            compact_link = {
                "feature": str(link.get("feature") or "").strip(),
                "source_jie": str(link.get("source_jie") or "").strip(),
                "source_name": str(link.get("source_name") or "").strip(),
                "source_describe": describe,
                "match_kind": str(link.get("match_kind") or "").strip(),
                "direct_match_count": str(link.get("direct_match_count") or "").strip(),
                "family_match_count": str(link.get("family_match_count") or "").strip(),
                "config_ids": str(link.get("config_ids") or "").strip(),
                "config_descriptions": str(link.get("config_descriptions") or "").strip(),
                "timelines": str(link.get("timelines") or "").strip(),
                "effect_paths": str(link.get("effect_paths") or "").strip(),
                "sound_ids": str(link.get("sound_ids") or "").strip(),
            }
            feature_link_rows.append({key: value for key, value in compact_link.items() if value not in (None, "", [], {})})
        if source_describe and source_describe not in seen_stage_lines:
            stage_lines.insert(0, source_describe)

        asset_parts = []
        for label, field in (
            ("特效候选", "candidate_descriptions"),
            ("Timeline", "candidate_timelines"),
            ("资源路径", "candidate_effect_paths"),
            ("音效", "candidate_sound_ids"),
            ("消耗材料", "consume_item_names"),
        ):
            values = _probe_tsv_values(row.get(field), limit=8)
            if values:
                asset_parts.append(f"{label}：{'、'.join(values)}")
        feature_asset_text = "\n".join(asset_parts)
        feature_stage_text = "\n".join(stage_lines[:12])

        description_parts = [
            f"关联道具：{'、'.join(linked_item_names)}" if linked_item_names else "",
            f"effectValue：{source_gid}",
            f"探针状态：{status}" if status else "",
            f"Feature前缀：{'、'.join(feature_prefixes)}" if feature_prefixes else "",
            f"重数覆盖：{'、'.join(source_names)}" if source_names else "",
            f"核心描述：{source_describe}" if source_describe else "",
            f"重数/技能描述：\n{feature_stage_text}" if feature_stage_text else "",
            feature_asset_text,
            f"候选配置：{'、'.join(candidate_ids[:10])}" if candidate_ids else "",
        ]
        description = "\n".join(part for part in description_parts if part)
        if not description:
            continue

        detail: dict[str, Any] = {
            "kind": "gongfa_feature_probe_book",
            "title": linked_item_names[0] if linked_item_names else f"功法书探针 {source_gid}",
            "subtitle": " · ".join(part for part in (f"effectValue {source_gid}", status, f"feature {row.get('feature_count')}") if part),
            "description": description,
            "plain_description": _plain_rich_text(description),
            "source": "gongfa_feature_probe/feature_families.tsv + feature_links.tsv",
            "source_id": source_gid,
            "gongfa_feature_gid": source_gid,
            "gongfa_feature_status": status,
            "gongfa_feature_prefixes": feature_prefixes,
            "gongfa_feature_ids": feature_ids,
            "gongfa_feature_count": _probe_tsv_int(row.get("feature_count")),
            "gongfa_feature_source_jie": _probe_tsv_values(row.get("source_jie"), limit=12),
            "gongfa_feature_source_names": source_names,
            "gongfa_feature_candidate_count": _probe_tsv_int(row.get("candidate_count")),
            "gongfa_feature_candidate_ids": candidate_ids,
            "gongfa_feature_linked_item_ids": linked_item_ids,
            "feature_stage_text": feature_stage_text,
            "feature_asset_text": feature_asset_text,
            "feature_links": feature_link_rows[:12],
        }
        details_by_id[source_gid] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_id, {
        "gongfa_feature_probe_family_source": str(family_path),
        "gongfa_feature_probe_link_source": str(link_path if link_path.is_file() else ""),
        "gongfa_feature_probe_family_row_count": len(family_rows),
        "gongfa_feature_probe_link_row_count": len(link_rows),
        "gongfa_feature_probe_detail_count": len(details_by_id),
    }


def _build_gongfa_feature_probe_item_detail(
    row: dict[str, Any],
    details_by_id: dict[int, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if str(row.get("type")) != "3" or str(row.get("subType")) != "8":
        return None
    effect_id = _as_int(row.get("effectValue"))
    if effect_id is None:
        return None
    detail = (details_by_id or {}).get(effect_id)
    if not detail:
        return None
    result = dict(detail)
    item_name = _text_value(row, "name")
    if item_name:
        result["title"] = item_name
    return result


def _build_gongfa_local_description_detail(row: dict[str, Any]) -> dict[str, Any] | None:
    if str(row.get("type")) != "3" or str(row.get("subType")) != "8":
        return None
    item_name = _text_value(row, "name")
    plain_description = _text_value(row, "descript")
    rich_description = _rich_text_value(row, "descript")
    if not plain_description:
        return None
    skip_text = f"{item_name} {plain_description}"
    if any(word in skip_text for word in ("暂未投放", "废弃", "不应该在任何地方看到")):
        return None

    highlighted_terms: list[str] = []
    for match in re.finditer(r"<color=[^>]+>(.*?)</color>", rich_description, flags=re.IGNORECASE | re.DOTALL):
        term = _plain_rich_text(match.group(1))
        if term and term not in highlighted_terms:
            highlighted_terms.append(term)

    personality = ""
    match = re.search(r"适合性格([^之，。]+)之人修行", plain_description)
    if match:
        personality = match.group(1).strip()

    effect_id = _as_int(row.get("effectValue"))
    description_parts = [
        f"高亮术语：{'、'.join(highlighted_terms)}" if highlighted_terms else "",
        f"性格倾向：{personality}" if personality else "",
        plain_description,
        f"effectValue：{effect_id}" if effect_id is not None else "",
    ]
    description = "\n".join(part for part in description_parts if part)
    detail: dict[str, Any] = {
        "kind": "gongfa_local_description",
        "title": item_name or f"功法书 {row.get('id') or row.get('_row_key')}",
        "subtitle": " · ".join(part for part in ("Item.descript", f"性格 {personality}" if personality else "") if part),
        "description": description,
        "plain_description": description,
        "source": "Item.descript",
        "source_id": row.get("id") or row.get("_row_key"),
        "gongfa_local_effect_id": effect_id,
        "gongfa_local_terms": highlighted_terms,
        "gongfa_local_personality": personality,
    }
    return {key: value for key, value in detail.items() if value not in (None, "", [], {})}


def _build_physical_exercise_effect_details_by_id(
    root: Path,
    quality_by_id: dict[int, dict[str, Any]] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    physical_path = _find_physical_exercise_lua(root, "Physical.lua")
    if physical_path is None:
        return {}, {
            "physical_exercise_source": "",
            "physical_jie_source": "",
            "physical_comprehension_source": "",
            "physical_exercise_detail_count": 0,
        }

    jie_path = _find_physical_exercise_lua(root, "PhysicalJie.lua")
    comprehension_path = _find_physical_exercise_lua(root, "Comprehension.lua")
    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    physical_rows = list(parse_fanxiu_generated_lua_config(physical_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    jie_rows = (
        list(parse_fanxiu_generated_lua_config(jie_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if jie_path is not None
        else []
    )
    comprehension_rows = (
        list(parse_fanxiu_generated_lua_config(comprehension_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if comprehension_path is not None
        else []
    )
    attribute_rows = (
        list(parse_fanxiu_generated_lua_config(attribute_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if attribute_path is not None
        else []
    )
    attr_name_by_key = {
        str(row.get("id")): _text_value(row, "name")
        for row in attribute_rows
        if isinstance(row, dict) and row.get("id") not in (None, "") and _text_value(row, "name")
    }

    jie_by_physical: dict[int, list[dict[str, Any]]] = {}
    for row in jie_rows:
        if not isinstance(row, dict):
            continue
        physical_id = _as_int(row.get("gid"))
        if physical_id is None:
            continue
        jie_by_physical.setdefault(physical_id, []).append(row)
    for rows in jie_by_physical.values():
        rows.sort(key=lambda item: (_sort_value(item.get("jie")), _sort_value(item.get("id") or item.get("_row_key"))))

    comprehension_by_physical: dict[int, list[dict[str, Any]]] = {}
    for row in comprehension_rows:
        if not isinstance(row, dict):
            continue
        physical_id = _as_int(row.get("physicalId"))
        if physical_id is None:
            continue
        comprehension_by_physical.setdefault(physical_id, []).append(row)
    for rows in comprehension_by_physical.values():
        rows.sort(key=lambda item: (_sort_value(item.get("grade")), _sort_value(item.get("id") or item.get("_row_key"))))

    details_by_id: dict[int, dict[str, Any]] = {}
    for row in physical_rows:
        if not isinstance(row, dict):
            continue
        physical_id = _as_int(row.get("id") or row.get("_row_key"))
        if physical_id is None:
            continue

        stages = jie_by_physical.get(physical_id) or []
        first_stage = stages[0] if stages else None
        max_stage = stages[-1] if stages else None
        core_stages = [
            stage
            for stage in stages
            if isinstance(stage, dict) and "【" in (_rich_text_value(stage, "describe") or "")
        ][:12]
        if not core_stages and first_stage:
            core_stages = [first_stage]
        stage_lines = []
        for stage in core_stages:
            stage_description = _rich_text_value(stage, "describe")
            if not stage_description:
                continue
            stage_name = _text_value(stage, "name") or f"{stage.get('jie')}重"
            stage_lines.append(f"{stage_name}：{stage_description}")
        stage_text = "\n".join(stage_lines)

        max_attr_entries = _title_attr_entries(
            max_stage.get("attributes") if isinstance(max_stage, dict) else None,
            attr_name_by_key,
        )
        max_attr_text = _format_title_attr_text("满重属性", max_attr_entries)

        comprehension_rows_for_physical = comprehension_by_physical.get(physical_id) or []
        first_comprehension = comprehension_rows_for_physical[0] if comprehension_rows_for_physical else None
        max_comprehension = comprehension_rows_for_physical[-1] if comprehension_rows_for_physical else None
        comprehension_attr_entries = _title_attr_entries(
            max_comprehension.get("attributes") if isinstance(max_comprehension, dict) else None,
            attr_name_by_key,
        )
        comprehension_attr_text = _format_title_attr_text("领悟属性", comprehension_attr_entries)
        comprehension_summary = ""
        if first_comprehension:
            first_grade = first_comprehension.get("grade")
            last_grade = max_comprehension.get("maxGrade") if isinstance(max_comprehension, dict) else None
            if last_grade in (None, "", 0):
                last_grade = max_comprehension.get("grade") if isinstance(max_comprehension, dict) else None
            grade_text = f"等级 {first_grade}-{last_grade}" if last_grade and last_grade != first_grade else f"等级 {first_grade}"
            comprehension_summary = "；".join(
                part
                for part in (
                    grade_text,
                    f"本体加成 {first_comprehension.get('noumenonAddition')}" if first_comprehension.get("noumenonAddition") not in (None, "", 0) else "",
                    f"消耗 {first_comprehension.get('consume')}" if first_comprehension.get("consume") not in (None, "") else "",
                    comprehension_attr_text,
                )
                if part
            )

        quality_id = _as_int(row.get("quality"))
        quality_name = (quality_by_id or {}).get(quality_id or -1, {}).get("quality_name", "") if quality_id is not None else ""
        consume = row.get("consume")
        consume_text = ""
        if isinstance(consume, list):
            consume_text = "；".join(str(item) for item in consume if item not in (None, ""))
        elif consume not in (None, ""):
            consume_text = str(consume)

        description_parts = [
            f"炼体秘术品阶：{quality_name}" if quality_name else "",
            f"初始重数：{row.get('initJie')}" if row.get("initJie") not in (None, "", 0) else "",
            f"节点数：{row.get('nodeNum')}" if row.get("nodeNum") not in (None, "", 0) else "",
            f"学习消耗：{consume_text}" if consume_text else "",
            f"普通淬炼道具：{row.get('normalConsume')}" if row.get("normalConsume") not in (None, "", 0) else "",
            f"突破道具：{row.get('breakConsume')}" if row.get("breakConsume") not in (None, "", 0) else "",
            f"核心阶数效果：\n{stage_text}" if stage_text else "",
            max_attr_text,
            f"领悟：{comprehension_summary}" if comprehension_summary else "",
        ]
        description = "\n".join(part for part in description_parts if part)
        if not description:
            continue

        detail: dict[str, Any] = {
            "kind": "physical_exercise",
            "title": _text_value(row, "name") or f"炼体秘术 {physical_id}",
            "subtitle": " · ".join(part for part in (quality_name, f"{len(stages)}重" if stages else "") if part),
            "description": description,
            "plain_description": description,
            "attr_text": " ".join(part for part in (max_attr_text, comprehension_attr_text) if part),
            "attr_entries": max_attr_entries,
            "source": "PhysicalExercise.Physical",
            "source_id": physical_id,
            "quality": quality_id,
            "quality_name": quality_name,
            "stage_count": len(stages),
            "stage_text": stage_text,
            "consume_text": consume_text,
            "normal_consume": row.get("normalConsume"),
            "break_consume": row.get("breakConsume"),
            "node_num": row.get("nodeNum"),
            "init_jie": row.get("initJie"),
            "comprehension_count": len(comprehension_rows_for_physical),
            "raw_img": row.get("rawImg"),
            "effect_prefab": row.get("eff"),
        }
        details_by_id[physical_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_id, {
        "physical_exercise_source": str(physical_path),
        "physical_jie_source": str(jie_path or ""),
        "physical_comprehension_source": str(comprehension_path or ""),
        "physical_exercise_row_count": len(physical_rows),
        "physical_jie_row_count": len(jie_rows),
        "physical_comprehension_row_count": len(comprehension_rows),
        "physical_exercise_detail_count": len(details_by_id),
    }


def _build_partner_effect_details_by_id(
    root: Path,
    quality_by_id: dict[int, dict[str, Any]] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    partner_path = _find_partner_lua(root, "Partner.lua")
    if partner_path is None:
        return {}, {
            "partner_source": "",
            "partner_grade_source": "",
            "partner_detail_count": 0,
        }

    quality_path = _find_partner_lua(root, "PartnerQuality.lua")
    grade_path = _find_partner_lua(root, "PartnerGrade.lua")
    show_skill_path = _find_partner_lua(root, "PartnerShowSkill.lua")
    skill_level_path = _find_partner_lua(root, "PartnerSkillLevel.lua")
    active_skill_path = _find_partner_lua(root, "PartnerActiveSkill.lua")
    arane_path = _find_partner_lua(root, "PartnerAraneResource.lua")
    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None

    def parse_optional(path: Path | None) -> list[dict[str, Any]]:
        if path is None:
            return []
        return [
            row
            for row in parse_fanxiu_generated_lua_config(path, lang_path=lang_path, lang_map=lang_map).get("rows") or []
            if isinstance(row, dict)
        ]

    partner_rows = parse_optional(partner_path)
    quality_rows = parse_optional(quality_path)
    grade_rows = parse_optional(grade_path)
    show_skill_rows = parse_optional(show_skill_path)
    skill_level_rows = parse_optional(skill_level_path)
    active_skill_rows = parse_optional(active_skill_path)
    arane_rows = parse_optional(arane_path)
    attribute_rows = parse_optional(attribute_path)

    partner_quality_name_by_id = {
        quality_id: _text_value(row, "name")
        for row in quality_rows
        if (quality_id := _as_int(row.get("id") or row.get("_row_key"))) is not None and _text_value(row, "name")
    }
    attr_name_by_key = {
        str(row.get("id")): _text_value(row, "name")
        for row in attribute_rows
        if row.get("id") not in (None, "") and _text_value(row, "name")
    }

    grades_by_partner: dict[int, list[dict[str, Any]]] = {}
    for row in grade_rows:
        partner_id = _as_int(row.get("partnerid") or row.get("partnerId"))
        if partner_id is None:
            continue
        grades_by_partner.setdefault(partner_id, []).append(row)
    for rows in grades_by_partner.values():
        rows.sort(key=lambda item: (_sort_value(item.get("stage")), _sort_value(item.get("id") or item.get("_row_key"))))

    show_skills_by_partner: dict[int, list[dict[str, Any]]] = {}
    for row in show_skill_rows:
        partner_id = _as_int(row.get("partnerId") or row.get("partnerid"))
        if partner_id is None:
            continue
        show_skills_by_partner.setdefault(partner_id, []).append(row)
    for rows in show_skills_by_partner.values():
        rows.sort(key=lambda item: (_sort_value(item.get("sort")), _sort_value(item.get("id") or item.get("_row_key"))))

    skill_levels_by_group: dict[int, list[dict[str, Any]]] = {}
    for row in skill_level_rows:
        group_id = _as_int(row.get("skillGroup"))
        if group_id is None:
            continue
        skill_levels_by_group.setdefault(group_id, []).append(row)
    for rows in skill_levels_by_group.values():
        rows.sort(key=lambda item: (_sort_value(item.get("level")), _sort_value(item.get("star")), _sort_value(item.get("id") or item.get("_row_key"))))

    active_skills_by_partner: dict[int, list[dict[str, Any]]] = {}
    for row in active_skill_rows:
        partner_id = _as_int(row.get("partnerId") or row.get("partnerid"))
        if partner_id is None:
            continue
        active_skills_by_partner.setdefault(partner_id, []).append(row)
    for rows in active_skills_by_partner.values():
        rows.sort(key=lambda item: (_sort_value(item.get("sort")), _sort_value(item.get("id") or item.get("_row_key"))))

    arane_by_partner: dict[int, list[dict[str, Any]]] = {}
    for row in arane_rows:
        partner_id = _as_int(row.get("partnerId") or row.get("partnerid"))
        if partner_id is None:
            continue
        arane_by_partner.setdefault(partner_id, []).append(row)
    for rows in arane_by_partner.values():
        rows.sort(key=lambda item: (_sort_value(item.get("level")), _sort_value(item.get("id") or item.get("_row_key"))))

    def text(row: dict[str, Any] | None, field: str, *, rich: bool) -> str:
        return _rich_text_value(row, field) if rich else _text_value(row or {}, field)

    def build_description(partner: dict[str, Any], *, rich: bool) -> tuple[str, dict[str, str]]:
        partner_id = _as_int(partner.get("id") or partner.get("_row_key"))
        if partner_id is None:
            return "", {}
        grades = grades_by_partner.get(partner_id) or []
        init_stage = _as_int(partner.get("initStage"))
        first_grade = next((item for item in grades if _as_int(item.get("stage")) == init_stage), None) or (grades[0] if grades else None)
        max_grade = grades[-1] if grades else None
        show_skills = show_skills_by_partner.get(partner_id) or []
        active_skills = active_skills_by_partner.get(partner_id) or []
        arane_rows_for_partner = [row for row in arane_by_partner.get(partner_id) or [] if text(row, "describe", rich=rich)]
        first_arane = arane_rows_for_partner[0] if arane_rows_for_partner else None

        quality_id = _as_int(partner.get("quality"))
        quality_name = partner_quality_name_by_id.get(quality_id or -1)
        if not quality_name and quality_id is not None:
            quality_name = (quality_by_id or {}).get(quality_id, {}).get("quality_name", "")
        stage_name = text(first_grade, "stagename", rich=False) if first_grade else ""

        system_name = ""
        arane_plain = text(first_arane, "describe", rich=False)
        match = re.search(r"归属于【([^】]+)】体系", arane_plain)
        if match:
            system_name = match.group(1)

        base_parts = [
            f"品质：{quality_name}" if quality_name else "",
            f"初始阶级：{stage_name}" if stage_name else "",
            f"体系：{system_name}" if system_name else "",
            f"模型：{partner.get('model')}" if partner.get("model") not in (None, "", 0) else "",
            f"誓约道具：{partner.get('itemId')}" if partner.get("itemId") not in (None, "", 0) else "",
            f"碎片来源：{partner.get('obtainAgain')}" if partner.get("obtainAgain") not in (None, "") else "",
            f"重复转化：{partner.get('convertItem')}" if partner.get("convertItem") not in (None, "") else "",
            f"推荐法宝：{partner.get('recommendTalisman')}" if partner.get("recommendTalisman") not in (None, "") else "",
            f"推荐炼体：{partner.get('recommendPhysical')}" if partner.get("recommendPhysical") not in (None, "") else "",
        ]
        base_text = "；".join(part for part in base_parts if part)

        grade_desc = text(first_grade, "descript", rich=rich) or text(first_grade, "shortDesc", rich=rich)
        grade_skill_name = text(first_grade, "skillShowName", rich=False) or text(first_grade, "skillName", rich=False)
        grade_text = f"{grade_skill_name}：{grade_desc}" if grade_skill_name and grade_desc else grade_desc

        show_lines: list[str] = []
        for show_skill in show_skills[:8]:
            group_id = _as_int(show_skill.get("groupId"))
            level = (skill_levels_by_group.get(group_id or -1) or [None])[0]
            name = text(show_skill, "skillName", rich=False)
            level_name = text(level, "skillName", rich=False)
            desc = text(level, "skillDesc", rich=rich)
            unlock = text(show_skill, "unLockDesc", rich=False) or text(level, "unLockDesc", rich=False)
            label = " / ".join(part for part in (name, level_name) if part and part != name)
            suffix = f"（{unlock}）" if unlock else ""
            if desc:
                show_lines.append(f"{label or name}{suffix}：{desc}")
            elif label or name:
                show_lines.append(f"{label or name}{suffix}")
        show_skill_text = "\n".join(show_lines)

        active_lines = [
            " / ".join(
                part
                for part in (
                    text(row, "skillName", rich=False),
                    f"Skill {row.get('skillId')}" if row.get("skillId") not in (None, "", 0) else "",
                )
                if part
            )
            for row in active_skills[:10]
        ]
        active_skill_text = "；".join(part for part in active_lines if part)

        arane_name = text(first_arane, "skillName", rich=False) or text(first_arane, "Arane", rich=False)
        arane_desc = text(first_arane, "describe", rich=rich)
        arane_text = f"{arane_name}：{arane_desc}" if arane_name and arane_desc else arane_desc

        max_attr_entries = _title_attr_entries(max_grade.get("attr") if isinstance(max_grade, dict) else None, attr_name_by_key)
        max_attr_text = _format_title_attr_text("满阶属性", max_attr_entries)

        parts = [
            f"仙侣基础：{base_text}" if base_text else "",
            f"灵技：\n{grade_text}" if grade_text else "",
            f"洞府/展示技能：\n{show_skill_text}" if show_skill_text else "",
            f"可装配神通：{active_skill_text}" if active_skill_text else "",
            f"绝技：\n{arane_text}" if arane_text else "",
            max_attr_text,
        ]
        return "\n\n".join(part for part in parts if part), {
            "quality_name": quality_name or "",
            "stage_name": stage_name,
            "system_name": system_name,
            "partner_skill_text": " ".join(part for part in (grade_text, show_skill_text) if part),
            "partner_active_skill_text": active_skill_text,
            "partner_arane_text": arane_text,
            "attr_text": max_attr_text,
        }

    details_by_id: dict[int, dict[str, Any]] = {}
    for row in partner_rows:
        partner_id = _as_int(row.get("id") or row.get("_row_key"))
        if partner_id is None:
            continue
        rich_description, rich_meta = build_description(row, rich=True)
        plain_description, plain_meta = build_description(row, rich=False)
        if not rich_description and not plain_description:
            continue
        subtitle = " · ".join(part for part in (plain_meta.get("quality_name"), plain_meta.get("stage_name"), plain_meta.get("system_name")) if part)
        detail: dict[str, Any] = {
            "kind": "partner",
            "title": _text_value(row, "name") or f"仙侣 {partner_id}",
            "subtitle": subtitle,
            "description": rich_description or plain_description,
            "plain_description": plain_description or rich_description,
            "source": "Partner.Partner",
            "source_id": partner_id,
            "quality": row.get("quality"),
            "quality_name": plain_meta.get("quality_name"),
            "stage": row.get("initStage"),
            "stage_name": plain_meta.get("stage_name"),
            "type_label": plain_meta.get("system_name"),
            "partner_skill_text": plain_meta.get("partner_skill_text"),
            "partner_active_skill_text": plain_meta.get("partner_active_skill_text"),
            "partner_arane_text": plain_meta.get("partner_arane_text"),
            "attr_text": plain_meta.get("attr_text"),
            "item_id": row.get("itemId"),
            "fragment_item": row.get("obtainAgain"),
            "skill_item_id": row.get("skillItemId"),
            "model": row.get("model"),
            "head_icon": row.get("headIcon"),
            "icon": row.get("icon"),
            "show_skill_count": len(show_skills_by_partner.get(partner_id) or []),
            "active_skill_count": len(active_skills_by_partner.get(partner_id) or []),
            "arane_level_count": len(arane_by_partner.get(partner_id) or []),
            "grade_count": len(grades_by_partner.get(partner_id) or []),
        }
        details_by_id[partner_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_id, {
        "partner_source": str(partner_path),
        "partner_quality_source": str(quality_path or ""),
        "partner_grade_source": str(grade_path or ""),
        "partner_show_skill_source": str(show_skill_path or ""),
        "partner_skill_level_source": str(skill_level_path or ""),
        "partner_active_skill_source": str(active_skill_path or ""),
        "partner_arane_source": str(arane_path or ""),
        "partner_row_count": len(partner_rows),
        "partner_quality_row_count": len(quality_rows),
        "partner_grade_row_count": len(grade_rows),
        "partner_show_skill_row_count": len(show_skill_rows),
        "partner_skill_level_row_count": len(skill_level_rows),
        "partner_active_skill_row_count": len(active_skill_rows),
        "partner_arane_row_count": len(arane_rows),
        "partner_detail_count": len(details_by_id),
    }


def _extract_npc_gift_target_name(row: dict[str, Any], npc_id: int) -> str:
    candidates = [
        _plain_rich_text(row.get("des_plain") or row.get("des")),
        _plain_rich_text(row.get("effectInfoTxt")),
        _plain_rich_text(row.get("effectTips")),
    ]
    patterns = (
        r"向([^，。,、\s]{1,20}?)(?:赠礼|祈福)",
        r"朝([^，。,、\s]{1,20}?)祈福",
        r"前往([^，。,、\s]{1,20}?)处",
    )
    for text in candidates:
        if not text:
            continue
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
    return f"NPC {npc_id}"


def _summarize_names(names: list[str], *, limit: int = 24) -> str:
    if not names:
        return ""
    visible = names[:limit]
    suffix = f" 等 {len(names)} 个" if len(names) > limit else ""
    return "、".join(visible) + suffix


def _build_npc_gift_effect_details_by_item_id(
    root: Path,
    item_rows: list[dict[str, Any]],
    partner_details_by_id: dict[int, dict[str, Any]] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    item_by_id = {
        item_id: row
        for row in item_rows
        if isinstance(row, dict) and (item_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    details_by_item_id: dict[int, dict[str, Any]] = {}
    npc_path = _find_npc_lua(root, "NpcGift.lua")
    npc_name_path = _find_npc_lua(root, "Npc.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None

    npc_name_by_id: dict[int, str] = {}
    if npc_name_path is not None:
        for row in parse_fanxiu_generated_lua_config(npc_name_path, lang_path=lang_path, lang_map=lang_map).get("rows") or []:
            if not isinstance(row, dict):
                continue
            npc_id = _as_int(row.get("id") or row.get("_row_key"))
            name = _text_value(row, "name")
            if npc_id is not None and name:
                npc_name_by_id[npc_id] = name

    npc_rows: list[dict[str, Any]] = []
    if npc_path is not None:
        npc_rows = [
            row
            for row in parse_fanxiu_generated_lua_config(npc_path, lang_path=lang_path, lang_map=lang_map).get("rows") or []
            if isinstance(row, dict)
        ]
        for row in npc_rows:
            npc_id = _as_int(row.get("npcId"))
            if npc_id is None:
                continue
            gift_item_ids = _linked_partner_gift_target_ids(row.get("npcGiftId"))
            if not gift_item_ids:
                continue
            known_gift_item_ids = [gift_item_id for gift_item_id in gift_item_ids if gift_item_id in item_by_id]
            if not known_gift_item_ids:
                continue
            target_name = npc_name_by_id.get(npc_id) or _extract_npc_gift_target_name(row, npc_id)
            gift_names = [
                _text_value(item_by_id.get(gift_id, {"id": gift_id, "name": gift_id}), "name") or str(gift_id)
                for gift_id in known_gift_item_ids
            ]
            rich_description = "\n".join(
                part
                for part in (
                    f"目标NPC：{target_name}（Npc {npc_id}）",
                    f"活动说明：{_rich_text_value(row, 'des')}",
                    f"玩法提示：{_rich_text_value(row, 'effectTips')}",
                    f"提交收益：{row.get('textItem')}" if row.get("textItem") else "",
                    f"可提交道具：{_summarize_names(gift_names, limit=16)}",
                )
                if part
            )
            plain_description = "\n".join(
                part
                for part in (
                    f"目标NPC：{target_name}（Npc {npc_id}）",
                    f"活动说明：{_text_value(row, 'des')}",
                    f"玩法提示：{_plain_rich_text(row.get('effectTips'))}",
                    f"提交收益：{_plain_rich_text(row.get('textItem'))}" if row.get("textItem") else "",
                    f"可提交道具：{_summarize_names(gift_names, limit=16)}",
                )
                if part
            )
            for gift_item_id in known_gift_item_ids:
                detail = {
                    "kind": "npc_gift_activity",
                    "title": f"NPC赠礼：{target_name}",
                    "subtitle": _plain_rich_text(row.get("Text")) or "赠礼/祈福",
                    "description": rich_description or plain_description,
                    "plain_description": plain_description or rich_description,
                    "source": "Npc.NpcGift",
                    "source_id": row.get("id") or row.get("_row_key"),
                    "npc_id": npc_id,
                    "npc_name": target_name,
                    "npc_gift_item_ids": known_gift_item_ids,
                    "npc_gift_item_names": gift_names,
                    "activity_name": _plain_rich_text(row.get("des_plain") or row.get("des")),
                }
                details_by_item_id[gift_item_id] = {
                    key: value for key, value in detail.items() if value not in (None, "", [], {})
                }

    partner_detail_count = 0
    for row in item_rows:
        item_id = _as_int(row.get("id") or row.get("_row_key"))
        if item_id is None or item_id in details_by_item_id:
            continue
        if str(row.get("type")) != "20" or str(row.get("subType")) != "16":
            continue
        target_ids = _linked_partner_gift_target_ids(row.get("effectValue"))
        if not target_ids:
            continue
        target_names: list[str] = []
        unknown_target_ids: list[int] = []
        for target_id in target_ids:
            target_name = npc_name_by_id.get(target_id) or (partner_details_by_id or {}).get(target_id, {}).get("title")
            if target_name:
                target_names.append(str(target_name))
            else:
                target_names.append(str(target_id))
                unknown_target_ids.append(target_id)
        known_target_names = [
            name
            for target_id, name in zip(target_ids, target_names, strict=False)
            if target_id not in set(unknown_target_ids)
        ]
        rich_description = "\n".join(
            part
            for part in (
                f"赠礼对象：{_summarize_names(known_target_names)}" if known_target_names else "",
                f"未命名对象ID：{', '.join(str(item) for item in unknown_target_ids)}" if unknown_target_ids else "",
                f"对象ID：{', '.join(str(item) for item in target_ids)}",
                f"赠礼效果：{_rich_text_value(row, 'effDescript')}" if _rich_text_value(row, "effDescript") else "",
            )
            if part
        )
        plain_description = "\n".join(
            part
            for part in (
                f"赠礼对象：{_summarize_names(known_target_names)}" if known_target_names else "",
                f"未命名对象ID：{', '.join(str(item) for item in unknown_target_ids)}" if unknown_target_ids else "",
                f"对象ID：{', '.join(str(item) for item in target_ids)}",
                f"赠礼效果：{_text_value(row, 'effDescript')}" if _text_value(row, "effDescript") else "",
            )
            if part
        )
        detail = {
            "kind": "partner_gift_targets",
            "title": "仙缘赠礼对象",
            "subtitle": f"{len(target_ids)} 个对象",
            "description": rich_description or plain_description,
            "plain_description": plain_description or rich_description,
            "source": "Item.effectValue",
            "source_id": item_id,
            "target_partner_ids": target_ids,
            "target_partner_names": target_names,
            "target_partner_unknown_ids": unknown_target_ids,
        }
        details_by_item_id[item_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}
        partner_detail_count += 1

    return details_by_item_id, {
        "npc_gift_source": str(npc_path or ""),
        "npc_source": str(npc_name_path or ""),
        "npc_row_count": len(npc_name_by_id),
        "npc_gift_row_count": len(npc_rows),
        "npc_gift_activity_detail_count": sum(
            1
            for detail in details_by_item_id.values()
            if isinstance(detail, dict) and detail.get("kind") == "npc_gift_activity"
        ),
        "partner_gift_target_detail_count": partner_detail_count,
        "npc_gift_detail_count": len(details_by_item_id),
    }


_HIDDEN_WORLD_ITEM_TYPE_LABELS = {
    1: "身份卡",
    2: "秘技道具卡",
    3: "决战道具卡",
}


def _hidden_world_param_skill_entries(
    value: Any,
    camp_name_by_id: dict[int, str],
    skill_by_id: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[int], list[str]]:
    raw_values: list[Any]
    if isinstance(value, list):
        raw_values = value
    elif value in (None, ""):
        raw_values = []
    else:
        raw_values = [value]
    entries: list[dict[str, Any]] = []
    skill_ids: list[int] = []
    skill_names: list[str] = []
    seen_skill_ids: set[int] = set()
    for raw in raw_values:
        text = str(raw or "").strip()
        if not text:
            continue
        camp_text, sep, skill_text = text.partition(":")
        skill_id = _as_int(skill_text if sep else camp_text)
        if skill_id is None:
            continue
        camp_id = _as_int(camp_text) if sep else None
        skill = skill_by_id.get(skill_id, {})
        skill_name = _text_value(skill, "name") or f"技能 {skill_id}"
        camp_name = camp_name_by_id.get(camp_id or -1, f"阵营 {camp_id}") if camp_id is not None else ""
        if skill_id not in seen_skill_ids:
            skill_ids.append(skill_id)
            skill_names.append(skill_name)
            seen_skill_ids.add(skill_id)
        entries.append(
            {
                "camp_id": camp_id,
                "camp_name": camp_name,
                "skill_id": skill_id,
                "skill_name": skill_name,
                "skill_description": _rich_text_value(skill, "skillDesc"),
                "skill_description_plain": _text_value(skill, "skillDesc"),
                "cooldown": skill.get("cd"),
                "effect": skill.get("effect"),
                "conditions": skill.get("conditions"),
            }
        )
    return entries, skill_ids, skill_names


def _build_hidden_world_effect_details_by_item_id(root: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    item_path = _find_hidden_world_lua(root, "HiddenWorldItem.lua")
    skill_path = _find_hidden_world_lua(root, "HiddenWorldSkill.lua")
    if item_path is None or skill_path is None:
        return {}, {
            "hidden_world_item_source": str(item_path or ""),
            "hidden_world_skill_source": str(skill_path or ""),
            "hidden_world_detail_count": 0,
        }

    camp_path = _find_hidden_world_lua(root, "HiddenWorldCampBase.lua")
    career_path = _find_hidden_world_lua(root, "HiddenWorldCampCareer.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    item_rows = list(parse_fanxiu_generated_lua_config(item_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    skill_rows = list(parse_fanxiu_generated_lua_config(skill_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    camp_rows = (
        list(parse_fanxiu_generated_lua_config(camp_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if camp_path
        else []
    )
    career_rows = (
        list(parse_fanxiu_generated_lua_config(career_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if career_path
        else []
    )
    skill_by_id = {
        skill_id: row
        for row in skill_rows
        if isinstance(row, dict) and (skill_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    camp_name_by_id = {
        camp_id: _text_value(row, "name") or f"阵营 {camp_id}"
        for row in camp_rows
        if isinstance(row, dict) and (camp_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    career_names = [
        _text_value(row, "name")
        for row in sorted(career_rows, key=lambda row: _sort_value(row.get("sort"), _sort_value(row.get("id"))))
        if isinstance(row, dict) and _text_value(row, "name")
    ]
    details_by_item_id: dict[int, dict[str, Any]] = {}
    for row in item_rows:
        if not isinstance(row, dict):
            continue
        item_id = _as_int(row.get("itemId"))
        hidden_world_item_id = _as_int(row.get("id") or row.get("_row_key"))
        if item_id is None or hidden_world_item_id is None:
            continue
        item_type = _as_int(row.get("type"))
        item_type_label = _HIDDEN_WORLD_ITEM_TYPE_LABELS.get(item_type or -1, f"类型 {item_type}") if item_type is not None else ""
        skill_entries, skill_ids, skill_names = _hidden_world_param_skill_entries(
            row.get("param"), camp_name_by_id, skill_by_id
        )
        rich_lines = ["活动：秘境封魔杀"]
        plain_lines = ["活动：秘境封魔杀"]
        if item_type_label:
            rich_lines.append(f"道具类型：{item_type_label}")
            plain_lines.append(f"道具类型：{item_type_label}")
        allow_drop_ids = [_as_int(part) for part in re.split(r"[,，、\s]+", str(row.get("allowDrop") or "")) if part]
        allow_drop_names = [camp_name_by_id.get(camp_id or -1, str(camp_id)) for camp_id in allow_drop_ids if camp_id is not None]
        if allow_drop_names:
            line = "可掉落阵营：" + "、".join(allow_drop_names)
            rich_lines.append(line)
            plain_lines.append(line)
        if skill_entries:
            rich_lines.append("关联技能：")
            plain_lines.append("关联技能：")
            for entry in skill_entries:
                prefix = f"{entry['camp_name']}：" if entry.get("camp_name") else ""
                title = f"{prefix}{entry.get('skill_name')}（Skill {entry.get('skill_id')}）"
                rich_lines.append(title)
                plain_lines.append(title)
                desc = entry.get("skill_description")
                plain_desc = entry.get("skill_description_plain")
                if desc:
                    rich_lines.append(str(desc))
                if plain_desc:
                    plain_lines.append(str(plain_desc))
                if entry.get("cooldown") not in (None, ""):
                    cooldown_line = f"冷却：{entry.get('cooldown')} 秒"
                    rich_lines.append(cooldown_line)
                    plain_lines.append(cooldown_line)
                if entry.get("effect"):
                    effect_line = f"内部效果：{entry.get('effect')}"
                    rich_lines.append(effect_line)
                    plain_lines.append(effect_line)
        elif career_names:
            line = "可选身份：" + "、".join(career_names[:12])
            rich_lines.append(line)
            plain_lines.append(line)
        details_by_item_id[item_id] = {
            "kind": "hidden_world_item",
            "title": "秘境封魔杀道具",
            "subtitle": item_type_label,
            "description": "\n".join(line for line in rich_lines if line),
            "plain_description": "\n".join(line for line in plain_lines if line),
            "source": "HiddenWorld.HiddenWorldItem",
            "source_id": hidden_world_item_id,
            "hidden_world_item_id": hidden_world_item_id,
            "hidden_world_item_type": item_type,
            "hidden_world_item_type_label": item_type_label,
            "skill_ids": skill_ids,
            "skill_names": skill_names,
            "skill_entries": skill_entries,
        }
    return details_by_item_id, {
        "hidden_world_item_source": str(item_path),
        "hidden_world_skill_source": str(skill_path),
        "hidden_world_camp_source": str(camp_path or ""),
        "hidden_world_career_source": str(career_path or ""),
        "hidden_world_item_row_count": len(item_rows),
        "hidden_world_skill_row_count": len(skill_rows),
        "hidden_world_detail_count": len(details_by_item_id),
    }


def _build_pet_gift_effect_details_by_id(root: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    pet_gift_path = _find_pet_lua(root, "PetGift.lua")
    if pet_gift_path is None:
        return {}, {"pet_gift_source": "", "pet_gift_detail_count": 0}

    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    pet_gift_rows = list(parse_fanxiu_generated_lua_config(pet_gift_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    attribute_rows = (
        list(parse_fanxiu_generated_lua_config(attribute_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if attribute_path
        else []
    )
    attr_name_by_key = {
        str(row.get("id")): _text_value(row, "name")
        for row in attribute_rows
        if isinstance(row, dict) and row.get("id") not in (None, "") and _text_value(row, "name")
    }
    details_by_id: dict[int, dict[str, Any]] = {}
    for row in pet_gift_rows:
        if not isinstance(row, dict):
            continue
        gift_id = _as_int(row.get("id") or row.get("_row_key"))
        if gift_id is None:
            continue
        attr_entries = _title_attr_entries(row.get("attr"), attr_name_by_key)
        attr_text = _format_title_attr_text("部位属性", attr_entries)
        lines = [
            "系统：灵兽部位能力",
            f"部位：{_text_value(row, 'name')}" if _text_value(row, "name") else "",
            f"评分权重：{row.get('rate')}" if row.get("rate") not in (None, "") else "",
            attr_text,
            f"显示条件：{row.get('showCondition')}" if row.get("showCondition") else "",
        ]
        description = "\n".join(line for line in lines if line)
        details_by_id[gift_id] = {
            "kind": "pet_gift",
            "title": _text_value(row, "name") or f"灵兽部位 {gift_id}",
            "subtitle": "灵兽部位能力",
            "description": description,
            "plain_description": description,
            "source": "Pet.PetGift",
            "source_id": gift_id,
            "pet_gift_id": gift_id,
            "pet_gift_rate": row.get("rate"),
            "condition": str(row.get("showCondition") or ""),
            "attr_text": attr_text,
            "attr_entries": attr_entries,
        }
    return details_by_id, {
        "pet_gift_source": str(pet_gift_path),
        "pet_gift_detail_count": len(details_by_id),
        "pet_gift_row_count": len(pet_gift_rows),
    }


def _build_member_effect_details_by_id(
    root: Path,
    quality_by_id: dict[int, dict[str, Any]] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    member_path = _find_dragon_member_lua(root, "MemberBase.lua")
    if member_path is None:
        return {}, {"member_source": "", "member_star_source": "", "member_detail_count": 0}

    star_path = _find_dragon_member_lua(root, "MemberStar.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    member_rows = [
        row
        for row in parse_fanxiu_generated_lua_config(member_path, lang_path=lang_path, lang_map=lang_map).get("rows") or []
        if isinstance(row, dict)
    ]
    star_rows = (
        [
            row
            for row in parse_fanxiu_generated_lua_config(star_path, lang_path=lang_path, lang_map=lang_map).get("rows") or []
            if isinstance(row, dict)
        ]
        if star_path is not None
        else []
    )

    stars_by_member: dict[int, list[dict[str, Any]]] = {}
    for row in star_rows:
        member_id = _as_int(row.get("baseId"))
        if member_id is None:
            continue
        stars_by_member.setdefault(member_id, []).append(row)
    for rows in stars_by_member.values():
        rows.sort(key=lambda item: (_sort_value(item.get("grade")), _sort_value(item.get("stars"), 0), _sort_value(item.get("id") or item.get("_row_key"))))

    details_by_id: dict[int, dict[str, Any]] = {}
    for row in member_rows:
        member_id = _as_int(row.get("id") or row.get("_row_key"))
        if member_id is None:
            continue
        stars = stars_by_member.get(member_id) or []
        init_grade = _as_int(row.get("initGrade"))
        first_star = next((item for item in stars if _as_int(item.get("grade")) == init_grade and _sort_value(item.get("stars"), 0) == 0), None)
        if first_star is None and stars:
            first_star = stars[0]
        max_star = stars[-1] if stars else None
        quality_id = _as_int(row.get("quality"))
        quality_name = (quality_by_id or {}).get(quality_id or -1, {}).get("quality_name", "") if quality_id is not None else ""
        member_type = _text_value(row, "type")
        base_parts = [
            f"品质：{quality_name}" if quality_name else "",
            f"类型：{member_type}" if member_type else "",
            f"初始品阶：{row.get('initGrade')}" if row.get("initGrade") not in (None, "", 0) else "",
            f"初始星级：{row.get('initStar')}" if row.get("initStar") not in (None, "", 0) else "",
            f"模型：{row.get('model')}" if row.get("model") not in (None, "", 0) else "",
            f"信物道具：{row.get('itemId')}" if row.get("itemId") not in (None, "", 0) else "",
            f"隐藏条件：{row.get('hideCondition')}" if row.get("hideCondition") not in (None, "") else "",
        ]
        first_skill_name = _rich_text_value(first_star, "skillName")
        first_skill_desc = _rich_text_value(first_star, "skillDesNew") or _rich_text_value(first_star, "skillDes")
        first_speed = _rich_text_value(first_star, "addSpeedDes")
        max_skill_desc = _rich_text_value(max_star, "skillDesNew") if max_star is not first_star else ""
        plain_skill_name = _text_value(first_star or {}, "skillName")
        plain_skill_desc = _text_value(first_star or {}, "skillDesNew") or _text_value(first_star or {}, "skillDes")
        plain_max_skill_desc = _text_value(max_star or {}, "skillDesNew") if max_star is not first_star else ""
        skill_parts = [
            f"{first_skill_name}：{first_skill_desc}" if first_skill_name and first_skill_desc else first_skill_desc,
            f"速度：{first_speed}" if first_speed else "",
            f"最高星级效果：{max_skill_desc}" if max_skill_desc else "",
        ]
        plain_skill_parts = [
            f"{plain_skill_name}：{plain_skill_desc}" if plain_skill_name and plain_skill_desc else plain_skill_desc,
            f"速度：{_text_value(first_star or {}, 'addSpeedDes')}" if _text_value(first_star or {}, "addSpeedDes") else "",
            f"最高星级效果：{plain_max_skill_desc}" if plain_max_skill_desc else "",
        ]
        description = "\n\n".join(
            part
            for part in (
                f"伙伴基础：{'；'.join(part for part in base_parts if part)}",
                f"星级技能：\n{chr(10).join(part for part in skill_parts if part)}" if any(skill_parts) else "",
            )
            if part
        )
        plain_description = "\n\n".join(
            part
            for part in (
                f"伙伴基础：{'；'.join(part for part in base_parts if part)}",
                f"星级技能：\n{chr(10).join(part for part in plain_skill_parts if part)}" if any(plain_skill_parts) else "",
            )
            if part
        )
        if not description and not plain_description:
            continue
        detail = {
            "kind": "member",
            "title": _text_value(row, "name") or f"伙伴 {member_id}",
            "subtitle": " · ".join(part for part in (quality_name, member_type) if part),
            "description": description or plain_description,
            "plain_description": plain_description or description,
            "source": "DragonMember.MemberBase",
            "source_id": member_id,
            "quality": quality_id,
            "quality_name": quality_name,
            "type_label": member_type,
            "member_skill_text": " ".join(part for part in plain_skill_parts if part),
            "item_id": row.get("itemId"),
            "model": row.get("model"),
            "head_icon": row.get("headIcon"),
            "photo": row.get("photo"),
            "star_count": len(stars),
        }
        details_by_id[member_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_id, {
        "member_source": str(member_path),
        "member_star_source": str(star_path or ""),
        "member_row_count": len(member_rows),
        "member_star_row_count": len(star_rows),
        "member_detail_count": len(details_by_id),
    }


def _build_member_equipment_effect_details_by_group_id(
    root: Path,
    quality_by_id: dict[int, dict[str, Any]] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    equipment_path = _find_dragon_boat_festival_lua(root, "DragonBoatEquipment.lua")
    if equipment_path is None:
        return {}, {
            "member_equipment_source": "",
            "member_equipment_skill_source": "",
            "member_equipment_detail_count": 0,
        }

    skill_path = _find_dragon_boat_festival_lua(root, "BoatSkill.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    equipment_rows = [
        row
        for row in parse_fanxiu_generated_lua_config(equipment_path, lang_path=lang_path, lang_map=lang_map).get("rows") or []
        if isinstance(row, dict)
    ]
    skill_rows = (
        [
            row
            for row in parse_fanxiu_generated_lua_config(skill_path, lang_path=lang_path, lang_map=lang_map).get("rows") or []
            if isinstance(row, dict)
        ]
        if skill_path is not None
        else []
    )
    skills_by_id = {
        skill_id: row
        for row in skill_rows
        if (skill_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    rows_by_group: dict[int, list[dict[str, Any]]] = {}
    for row in equipment_rows:
        group_id = _as_int(row.get("groupId"))
        if group_id is None:
            continue
        rows_by_group.setdefault(group_id, []).append(row)
    for rows in rows_by_group.values():
        rows.sort(key=lambda item: (_sort_value(item.get("level")), _sort_value(item.get("grade")), _sort_value(item.get("id"))))

    details_by_group_id: dict[int, dict[str, Any]] = {}
    for group_id, rows in rows_by_group.items():
        first = rows[0] if rows else None
        if first is None:
            continue
        max_row = rows[-1]
        quality_id = _as_int(first.get("grade"))
        quality_name = (quality_by_id or {}).get(quality_id or -1, {}).get("quality_name", "") if quality_id is not None else ""
        first_skill = skills_by_id.get(_as_int(first.get("skill")) or -1, {})
        max_skill = skills_by_id.get(_as_int(max_row.get("skill")) or -1, {})
        side_skill_ids: list[int] = []
        for row in rows:
            raw_side_skills = row.get("sideSkill")
            if isinstance(raw_side_skills, list):
                for raw_skill_id in raw_side_skills:
                    skill_id = _as_int(raw_skill_id)
                    if skill_id is not None and skill_id not in side_skill_ids:
                        side_skill_ids.append(skill_id)
        side_skill_parts: list[str] = []
        plain_side_skill_parts: list[str] = []
        for skill_id in side_skill_ids:
            skill = skills_by_id.get(skill_id) or {}
            unlock = _rich_text_value(skill, "sideSkillUnlockDes")
            name = _rich_text_value(skill, "name")
            desc = _rich_text_value(skill, "currentSkillDes") or _rich_text_value(skill, "skillDes")
            plain_unlock = _text_value(skill, "sideSkillUnlockDes")
            plain_name = _text_value(skill, "name")
            plain_desc = _text_value(skill, "currentSkillDes") or _text_value(skill, "skillDes")
            side_skill_parts.append("：".join(part for part in (f"{name}{unlock}" if unlock else name, desc) if part))
            plain_side_skill_parts.append("：".join(part for part in (f"{plain_name}{plain_unlock}" if plain_unlock else plain_name, plain_desc) if part))

        first_skill_title = _rich_text_value(first_skill, "name") or _rich_text_value(first, "skillName")
        first_skill_desc = _rich_text_value(first_skill, "currentSkillDes") or _rich_text_value(first_skill, "skillDes")
        max_skill_title = _rich_text_value(max_skill, "name") or _rich_text_value(max_row, "skillName")
        max_skill_desc = _rich_text_value(max_skill, "currentSkillDes") or _rich_text_value(max_skill, "skillDes")
        plain_first_skill_title = _text_value(first_skill, "name") or _text_value(first, "skillName")
        plain_first_skill_desc = _text_value(first_skill, "currentSkillDes") or _text_value(first_skill, "skillDes")
        plain_max_skill_title = _text_value(max_skill, "name") or _text_value(max_row, "skillName")
        plain_max_skill_desc = _text_value(max_skill, "currentSkillDes") or _text_value(max_skill, "skillDes")
        base_parts = [
            f"初始档位：{_text_value(first, 'levelDes')}" if _text_value(first, "levelDes") else "",
            f"初始品质：{quality_name or quality_id}" if quality_name or quality_id is not None else "",
            f"最高档位：{_text_value(max_row, 'levelDes')}" if _text_value(max_row, "levelDes") else "",
            f"档位数：{len(rows)}",
            f"升级提示：{_text_value(first, 'upgradeTip')}" if _text_value(first, "upgradeTip") else "",
        ]
        plain_base_parts = base_parts
        recommendation = _rich_text_value(first, "des")
        plain_recommendation = _text_value(first, "des")
        skill_parts = [
            f"{first_skill_title}：{first_skill_desc}" if first_skill_title and first_skill_desc else first_skill_desc,
            (
                f"最高阶主技能：{max_skill_title}：{max_skill_desc}"
                if max_skill_title and max_skill_desc and max_skill_desc != first_skill_desc
                else ""
            ),
            f"解锁副技能：\n{chr(10).join(part for part in side_skill_parts if part)}" if any(side_skill_parts) else "",
        ]
        plain_skill_parts = [
            f"{plain_first_skill_title}：{plain_first_skill_desc}" if plain_first_skill_title and plain_first_skill_desc else plain_first_skill_desc,
            (
                f"最高阶主技能：{plain_max_skill_title}：{plain_max_skill_desc}"
                if plain_max_skill_title and plain_max_skill_desc and plain_max_skill_desc != plain_first_skill_desc
                else ""
            ),
            f"解锁副技能：\n{chr(10).join(part for part in plain_side_skill_parts if part)}" if any(plain_side_skill_parts) else "",
        ]
        description = "\n\n".join(
            part
            for part in (
                f"装备基础：{'；'.join(part for part in base_parts if part)}",
                recommendation,
                f"装备技能：\n{chr(10).join(part for part in skill_parts if part)}" if any(skill_parts) else "",
            )
            if part
        )
        plain_description = "\n\n".join(
            part
            for part in (
                f"装备基础：{'；'.join(part for part in plain_base_parts if part)}",
                plain_recommendation,
                f"装备技能：\n{chr(10).join(part for part in plain_skill_parts if part)}" if any(plain_skill_parts) else "",
            )
            if part
        )
        if not description and not plain_description:
            continue
        detail = {
            "kind": "member_equipment",
            "title": _text_value(first, "name") or f"仙舟伙伴装备 {group_id}",
            "subtitle": " · ".join(part for part in (_text_value(first, "skillName"), _text_value(first, "levelDes")) if part),
            "description": description or plain_description,
            "plain_description": plain_description or description,
            "source": "DragonBoatFestival.DragonBoatEquipment",
            "source_id": group_id,
            "equipment_group_id": group_id,
            "quality": quality_id,
            "quality_name": quality_name,
            "level_count": len(rows),
            "item_id": first.get("item"),
            "skill_id": first.get("skill"),
            "side_skill_ids": side_skill_ids,
            "skill_text": " ".join(part for part in plain_skill_parts if part),
        }
        details_by_group_id[group_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_group_id, {
        "member_equipment_source": str(equipment_path),
        "member_equipment_skill_source": str(skill_path or ""),
        "member_equipment_row_count": len(equipment_rows),
        "member_equipment_skill_row_count": len(skill_rows),
        "member_equipment_detail_count": len(details_by_group_id),
    }


def _build_member_equipment_item_detail(row: dict[str, Any]) -> dict[str, Any] | None:
    effect_value = row.get("effectValue")
    if not _is_member_equipment_marker(effect_value):
        return None
    description = _rich_text_value(row, "descript")
    plain_description = _text_value(row, "descript")
    if not description and not plain_description:
        return None
    source_id = _linked_member_equipment_item_id(effect_value)
    marker = "" if effect_value is None else str(effect_value).split("|", 1)[0]
    subtitle = "节日活动伙伴装备" if marker == "JIERIMEMBEREUIPMENT" else "仙舟伙伴装备"
    return {
        "kind": "member_equipment_item",
        "title": _text_value(row, "name") or "伙伴装备",
        "subtitle": subtitle,
        "description": description or plain_description,
        "plain_description": plain_description or description,
        "source": "Item.effectValue",
        "source_id": source_id if source_id is not None else effect_value,
    }


def _format_duration_ms(value: Any) -> str:
    milliseconds = _as_int(value)
    if milliseconds is None:
        return ""
    if milliseconds <= 0:
        return f"{milliseconds} 毫秒"
    if milliseconds % 60000 == 0:
        minutes = milliseconds // 60000
        return f"{minutes} 分钟"
    if milliseconds % 1000 == 0:
        seconds = milliseconds // 1000
        return f"{seconds} 秒"
    return f"{milliseconds} 毫秒"


def _format_medical_attr_value(attr_key: str, value: Any) -> str:
    number = _as_int(value)
    if number is None:
        return str(value)
    if attr_key.endswith("_RATE"):
        percent = number / 100
        percent_text = f"{percent:.2f}".rstrip("0").rstrip(".")
        return f"+{percent_text}%"
    prefix = "+" if number >= 0 else ""
    return f"{prefix}{number}"


def _format_medical_attr_entries(label: str, entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""
    parts = [
        f"{entry.get('label') or entry.get('key')}{_format_medical_attr_value(str(entry.get('key') or ''), entry.get('value'))}"
        for entry in entries
    ]
    return f"{label}：" + "；".join(parts)


def _parse_medical_item_token(token: Any, items_by_id: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    text = "" if token is None else str(token).strip()
    if not text:
        return None
    match = re.fullmatch(r"Item\|(-?\d+)_(-?\d+)", text)
    if not match:
        return None
    item_id = _as_int(match.group(1))
    count = _as_int(match.group(2))
    if item_id is None:
        return None
    item = _linked_item_from_row(items_by_id.get(item_id) or {"id": item_id, "name": item_id}, count)
    item["token"] = text
    return item


def _parse_medical_formula_items(value: Any, items_by_id: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    values = value if isinstance(value, list) else [value]
    items: list[dict[str, Any]] = []
    for token in values:
        item = _parse_medical_item_token(token, items_by_id)
        if item:
            items.append(item)
    return items


def _parse_medical_any_material_items(value: Any, items_by_id: dict[int, dict[str, Any]]) -> tuple[list[dict[str, Any]], int | None]:
    text = "" if value is None else str(value).strip()
    if not text.startswith("AnyItem|"):
        return [], None
    parts = [part.strip() for part in text.split("|", 1)[1].split("_") if part.strip()]
    if len(parts) < 2:
        return [], None
    count = _as_int(parts[0])
    items: list[dict[str, Any]] = []
    for raw_item_id in parts[1:]:
        item_id = _as_int(raw_item_id)
        if item_id is None:
            continue
        item = _linked_item_from_row(items_by_id.get(item_id) or {"id": item_id, "name": item_id}, 1)
        item["any_count"] = count
        items.append(item)
    return items, count


def _format_linked_item_list(items: list[dict[str, Any]], *, any_count: int | None = None) -> str:
    if not items:
        return ""
    if any_count is not None:
        names = " / ".join(str(item.get("name") or item.get("id")) for item in items)
        return f"任意材料：{names} x{any_count}"
    return "材料：" + "；".join(
        f"{item.get('name') or item.get('id')} x{item.get('count') or 1}" for item in items
    )


def _build_take_medicine_effect_details_by_item_id(root: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    medicine_path = _find_take_medicine_lua(root, "TakeMedicine.lua")
    if medicine_path is None:
        return {}, {
            "take_medicine_source": "",
            "take_medicine_type_source": "",
            "take_medicine_detail_count": 0,
        }

    type_path = _find_take_medicine_lua(root, "TakeMedicineType.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    medicine_rows = list(parse_fanxiu_generated_lua_config(medicine_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    type_rows = (
        list(parse_fanxiu_generated_lua_config(type_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if type_path is not None
        else []
    )
    type_by_id = {
        type_id: row
        for row in type_rows
        if isinstance(row, dict) and (type_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }

    details_by_item_id: dict[int, dict[str, Any]] = {}
    for row in medicine_rows:
        if not isinstance(row, dict):
            continue
        item_id = _as_int(row.get("itemId"))
        if item_id is None:
            continue
        type_id = _as_int(row.get("typeId"))
        type_row = type_by_id.get(type_id or -1)
        medicine_type = _text_value(type_row or {}, "name")
        max_times = _as_int(row.get("maxTimes"))
        max_times_text = "不限" if max_times == -1 else (f"{max_times} 次" if max_times is not None else "")
        cooldown_text = _format_duration_ms(row.get("time"))
        description_parts = [
            f"服药分类：{medicine_type}" if medicine_type else "",
            f"服用上限：{max_times_text}" if max_times_text else "",
            f"服用间隔：{cooldown_text}" if cooldown_text else "",
            f"开启条件：{type_row.get('condition')}" if isinstance(type_row, dict) and type_row.get("condition") else "",
            f"境界编号：{type_row.get('realm')}" if isinstance(type_row, dict) and type_row.get("realm") not in (None, "") else "",
        ]
        description = "\n".join(part for part in description_parts if part)
        if not description:
            continue
        detail = {
            "kind": "take_medicine",
            "title": "服药规则",
            "subtitle": medicine_type,
            "description": description,
            "plain_description": description,
            "source": "TakeMedicine.TakeMedicine",
            "source_id": item_id,
            "medicine_id": row.get("id") or row.get("_row_key"),
            "medicine_type": medicine_type,
            "max_times_text": max_times_text,
            "cooldown_text": cooldown_text,
        }
        details_by_item_id[item_id] = {key: value for key, value in detail.items() if value not in (None, "")}

    return details_by_item_id, {
        "take_medicine_source": str(medicine_path),
        "take_medicine_type_source": str(type_path or ""),
        "take_medicine_row_count": len(medicine_rows),
        "take_medicine_type_row_count": len(type_rows),
        "take_medicine_detail_count": len(details_by_item_id),
    }


def _build_medical_recipe_effect_details(
    root: Path,
    item_rows: list[dict[str, Any]],
    take_medicine_details_by_item_id: dict[int, dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]], dict[str, Any]]:
    medical_path = _find_medical_lua(root, "Medical.lua")
    if medical_path is None:
        return {}, {}, {
            "medical_source": "",
            "medical_type_source": "",
            "medical_effect_source": "",
            "medical_effect_index_source": "",
            "medical_detail_count": 0,
        }

    type_path = _find_medical_lua(root, "MedicalType.lua")
    effect_path = _find_medical_lua(root, "MedicalEffect.lua")
    effect_index_path = _find_medical_lua(root, "MedicalEffectIndex.lua")
    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    medical_rows = list(parse_fanxiu_generated_lua_config(medical_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    type_rows = (
        list(parse_fanxiu_generated_lua_config(type_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if type_path is not None
        else []
    )
    effect_rows = (
        list(parse_fanxiu_generated_lua_config(effect_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if effect_path is not None
        else []
    )
    effect_index_rows = (
        list(parse_fanxiu_generated_lua_config(effect_index_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if effect_index_path is not None
        else []
    )
    attribute_rows = (
        list(parse_fanxiu_generated_lua_config(attribute_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if attribute_path is not None
        else []
    )

    items_by_id = {
        item_id: row
        for row in item_rows
        if isinstance(row, dict) and (item_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    type_by_id = {
        type_id: row
        for row in type_rows
        if isinstance(row, dict) and (type_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    effect_by_id = {
        effect_id: row
        for row in effect_rows
        if isinstance(row, dict) and (effect_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    attr_sort_by_key = {
        str(row.get("id") or row.get("_row_key")): _sort_value(row.get("index"), 10**9)
        for row in effect_index_rows
        if isinstance(row, dict) and row.get("id") not in (None, "")
    }
    attr_name_by_key = {
        str(row.get("id")): _text_value(row, "name")
        for row in attribute_rows
        if isinstance(row, dict) and row.get("id") not in (None, "") and _text_value(row, "name")
    }

    details_by_id: dict[int, dict[str, Any]] = {}
    details_by_formula_item_id: dict[int, dict[str, Any]] = {}
    for row in medical_rows:
        if not isinstance(row, dict):
            continue
        medical_id = _as_int(row.get("id") or row.get("_row_key"))
        formula_item_id = _as_int(row.get("item1"))
        product_item_id = _as_int(row.get("item2"))
        if medical_id is None or product_item_id is None:
            continue
        product_item = _linked_item_from_row(
            items_by_id.get(product_item_id) or {"id": product_item_id, "name": product_item_id}
        )
        formula_items = _parse_medical_formula_items(row.get("formula"), items_by_id)
        any_items, any_count = _parse_medical_any_material_items(row.get("anyMaterial"), items_by_id)
        material_text = _format_linked_item_list(formula_items) or _format_linked_item_list(any_items, any_count=any_count)
        type_row = type_by_id.get(_as_int(row.get("typeId")) or -1)
        type_name = _text_value(type_row or {}, "name")
        waiting_time_text = _format_duration_ms(row.get("waitingTime"))
        product_medicine_detail = take_medicine_details_by_item_id.get(product_item_id)
        effect_row = effect_by_id.get(product_item_id)
        attr_entries: list[dict[str, Any]] = []
        attrs = effect_row.get("attributes") if isinstance(effect_row, dict) else None
        if isinstance(attrs, dict):
            for key, value in sorted(attrs.items(), key=lambda item: (attr_sort_by_key.get(str(item[0]), 10**9), str(item[0]))):
                if _as_int(value) == 0:
                    continue
                attr_entries.append(
                    {
                        "key": str(key),
                        "label": attr_name_by_key.get(str(key)) or str(key),
                        "value": value,
                    }
                )
        attr_text = _format_medical_attr_entries("服用效果", attr_entries)
        description_parts = [
            f"产物：{product_item.get('name') or product_item_id}（Item {product_item_id}）",
            f"丹药分类：{type_name}" if type_name else "",
            material_text,
            f"炼制时间：{waiting_time_text}" if waiting_time_text else "",
            f"熟练度：{row.get('proficiency')}" if row.get("proficiency") not in (None, "", 0) else "",
            f"炼制限制：{row.get('medicalLimit')}" if row.get("medicalLimit") not in (None, "", 0) else "",
            attr_text,
            product_medicine_detail.get("plain_description") if isinstance(product_medicine_detail, dict) else "",
        ]
        description = "\n".join(part for part in description_parts if part)
        if not description:
            continue
        detail = {
            "kind": "medical_recipe",
            "title": "炼丹配方",
            "subtitle": f"{product_item.get('name') or product_item_id} · {type_name}" if type_name else str(product_item.get("name") or product_item_id),
            "description": description,
            "plain_description": description,
            "source": "Medical.Medical",
            "source_id": medical_id,
            "medical_id": medical_id,
            "recipe_item_id": formula_item_id,
            "product_item_id": product_item_id,
            "product_item_name": product_item.get("name"),
            "medicine_type": type_name,
            "formula_items": formula_items,
            "any_material_items": any_items,
            "material_text": material_text,
            "waiting_time_text": waiting_time_text,
            "proficiency": row.get("proficiency"),
            "medical_limit": row.get("medicalLimit"),
            "attr_text": attr_text,
            "attr_entries": attr_entries,
        }
        compact_detail = {key: value for key, value in detail.items() if value not in (None, "", [], {})}
        details_by_id[medical_id] = compact_detail
        if formula_item_id is not None:
            details_by_formula_item_id[formula_item_id] = compact_detail

    return details_by_id, details_by_formula_item_id, {
        "medical_source": str(medical_path),
        "medical_type_source": str(type_path or ""),
        "medical_effect_source": str(effect_path or ""),
        "medical_effect_index_source": str(effect_index_path or ""),
        "medical_attribute_source": str(attribute_path or ""),
        "medical_row_count": len(medical_rows),
        "medical_type_row_count": len(type_rows),
        "medical_effect_row_count": len(effect_rows),
        "medical_effect_index_row_count": len(effect_index_rows),
        "medical_detail_count": len(details_by_id),
    }


def _build_wallet_resource_effect_details_by_id(
    root: Path,
    item_rows: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    resource_path = _find_resource_lua(root, "Resource.lua")
    if resource_path is None:
        return {}, {"wallet_resource_source": "", "wallet_resource_detail_count": 0}

    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    resource_rows = list(
        parse_fanxiu_generated_lua_config(resource_path, lang_path=lang_path, lang_map=lang_map).get("rows") or []
    )
    items_by_id = {
        item_id: row
        for row in item_rows
        if isinstance(row, dict) and (item_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }

    details_by_id: dict[int, dict[str, Any]] = {}
    for row in resource_rows:
        if not isinstance(row, dict):
            continue
        resource_id = _as_int(row.get("id") or row.get("_row_key"))
        if resource_id is None:
            continue
        item_id = _as_int(row.get("itemId"))
        item = _linked_item_from_row(items_by_id.get(item_id or -1) or {"id": item_id, "name": item_id}) if item_id else None
        resource_name = _text_value(row, "name")
        alias = _text_value(row, "alias")
        description_parts = [
            f"钱包资源：{resource_name}" if resource_name else "",
            f"资源别名：{alias}" if alias else "",
            f"映射道具：{item.get('name') or item_id}（Item {item_id}）" if item_id else "",
            f"排序：{row.get('sort')}" if row.get("sort") not in (None, "", 0) else "",
        ]
        description = "\n".join(part for part in description_parts if part)
        if not description:
            continue
        detail = {
            "kind": "wallet_resource",
            "title": "钱包资源",
            "subtitle": resource_name,
            "description": description,
            "plain_description": description,
            "source": "Resource.Resource",
            "source_id": resource_id,
            "wallet_resource_id": resource_id,
            "wallet_alias": alias,
            "product_item_id": item_id,
            "product_item_name": item.get("name") if item else "",
        }
        details_by_id[resource_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_id, {
        "wallet_resource_source": str(resource_path),
        "wallet_resource_row_count": len(resource_rows),
        "wallet_resource_detail_count": len(details_by_id),
    }


def _boss_kill_effect_attr_entries(value: Any, attr_name_by_key: dict[str, str]) -> list[dict[str, Any]]:
    if value in (None, "", []):
        return []
    if isinstance(value, dict):
        raw_entries = [f"{key}|{raw}" for key, raw in value.items()]
    elif isinstance(value, (list, tuple)):
        raw_entries = list(value)
    else:
        raw_entries = [value]
    entries: list[dict[str, Any]] = []
    for raw_entry in raw_entries:
        text = str(raw_entry or "").strip()
        if not text:
            continue
        key, sep, raw_value = text.partition("|")
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            continue
        parsed_value: int | float | str | None
        if not sep or raw_value == "":
            parsed_value = None
        elif re.fullmatch(r"-?\d+", raw_value):
            parsed_value = int(raw_value)
        elif re.fullmatch(r"-?\d+(?:\.\d+)?", raw_value):
            parsed_value = float(raw_value)
        else:
            parsed_value = raw_value
        entry = {
            "key": key,
            "label": attr_name_by_key.get(key, key),
        }
        if parsed_value is not None:
            entry["value"] = parsed_value
        entries.append(entry)
    return entries


def _format_boss_kill_effect_attr_text(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""
    parts: list[str] = []
    for entry in entries:
        label = entry.get("label") or entry.get("key")
        value = entry.get("value")
        if value in (None, ""):
            parts.append(str(label))
        else:
            prefix = "+" if isinstance(value, (int, float)) and value >= 0 else ""
            parts.append(f"{label} {prefix}{value}")
    return "累计属性：" + "；".join(parts)


def _render_boss_kill_effect_description(template: str, entries: list[dict[str, Any]]) -> str:
    if not template or "$" not in template:
        return template
    values_by_key: dict[str, list[Any]] = {}
    for entry in entries:
        key = str(entry.get("key") or "").strip()
        if not key or entry.get("value") in (None, ""):
            continue
        values_by_key.setdefault(key, []).append(entry.get("value"))

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        values = values_by_key.get(key) or []
        if not values:
            return match.group(0)
        return str(values.pop(0))

    return re.sub(r"\$([A-Za-z0-9_]+)\$", replace, template)


def _build_boss_kill_effect_details_by_id(root: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    boss_path = _find_boss_kill_effect_lua(root, "BossKillEffect.lua")
    if boss_path is None:
        return {}, {"boss_kill_effect_source": "", "boss_kill_effect_detail_count": 0}

    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    boss_rows = list(parse_fanxiu_generated_lua_config(boss_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
    attribute_rows = (
        list(parse_fanxiu_generated_lua_config(attribute_path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])
        if attribute_path is not None
        else []
    )
    attr_name_by_key: dict[str, str] = {}
    for row in attribute_rows:
        if not isinstance(row, dict):
            continue
        name = _text_value(row, "name")
        for key in (row.get("id"), row.get("code")):
            if key not in (None, "") and name:
                attr_name_by_key[str(key)] = name

    details_by_id: dict[int, dict[str, Any]] = {}
    for row in boss_rows:
        if not isinstance(row, dict):
            continue
        effect_id = _as_int(row.get("id") or row.get("_row_key"))
        if effect_id is None:
            continue
        title = _text_value(row, "titile") or _text_value(row, "title") or f"BossKillEffect {effect_id}"
        attr_entries = _boss_kill_effect_attr_entries(row.get("attr"), attr_name_by_key)
        desc = _render_boss_kill_effect_description(_plain_rich_text(_text_value(row, "desc")), attr_entries)
        attr_text = _format_boss_kill_effect_attr_text(attr_entries)
        effect_type = row.get("type")
        max_value = row.get("maxValue")
        param = row.get("param")
        subtitle = " · ".join(
            part
            for part in (
                "BossKillEffect",
                f"类型 {effect_type}" if effect_type not in (None, "") else "",
                f"上限 {max_value}" if max_value not in (None, "") else "",
            )
            if part
        )
        plain_parts = [part for part in (desc, attr_text) if part]
        if max_value not in (None, ""):
            plain_parts.append(f"最大值：{max_value}")
        if param not in (None, ""):
            plain_parts.append(f"参数：{param}")
        detail = {
            "kind": "boss_kill_effect",
            "title": title,
            "subtitle": subtitle,
            "description": "\n".join(plain_parts),
            "plain_description": "\n".join(plain_parts),
            "source": "BossKillEffect.BossKillEffect",
            "source_id": effect_id,
            "boss_kill_effect_id": effect_id,
            "effect_type": effect_type,
            "max_value": max_value,
            "param": param,
            "attr_text": attr_text,
            "attr_entries": attr_entries,
        }
        details_by_id[effect_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_id, {
        "boss_kill_effect_source": str(boss_path),
        "boss_kill_effect_attribute_source": str(attribute_path or ""),
        "boss_kill_effect_row_count": len(boss_rows),
        "boss_kill_effect_detail_count": len(details_by_id),
    }


def _build_equipment_material_effect_detail(
    row: dict[str, Any],
    boss_kill_effect_details_by_id: dict[int, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if str(row.get("type")) != "5" or str(row.get("subType")) != "30":
        return None
    effect_id = _as_int(row.get("effectValue"))
    if effect_id is None:
        return None
    boss_detail = (boss_kill_effect_details_by_id or {}).get(effect_id)
    if not boss_detail:
        return None

    item_id = _as_int(row.get("id") or row.get("_row_key"))
    item_name = _text_value(row, "name") or (f"道具 {item_id}" if item_id is not None else "装备材料")
    local_description = _plain_rich_text(_text_value(row, "descript"))
    effect_title = str(boss_detail.get("title") or f"BossKillEffect {effect_id}")
    boss_description = _plain_rich_text(
        str(boss_detail.get("plain_description") or boss_detail.get("description") or "")
    )
    description_parts = [
        f"用途：{local_description}" if local_description else "",
        f"关联效果：{effect_title}",
        boss_description,
    ]
    description = "\n".join(part for part in description_parts if part)
    detail = {
        "kind": "equipment_material_effect",
        "title": item_name,
        "subtitle": f"装备材料 · BossKillEffect {effect_id}",
        "description": description,
        "plain_description": description,
        "source": "Item.effectValue -> BossKillEffect.BossKillEffect",
        "source_id": effect_id,
        "equipment_material_item_id": item_id,
        "equipment_material_effect_id": effect_id,
        "equipment_material_effect_title": effect_title,
        "boss_kill_effect_id": effect_id,
    }
    return {key: value for key, value in detail.items() if value not in (None, "", [], {})}


def _build_faze_effect_details_by_id(root: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    faze_path = root / DEFAULT_FAZE_RESOURCE_ROWS
    rows = _load_optional_json_rows(faze_path)
    details_by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        faze_id = _as_int(row.get("id") or row.get("_row_key"))
        if faze_id is None:
            continue
        description_parts = [
            _rich_text_value(row, "describe"),
            _rich_text_value(row, "effectsDes"),
        ]
        plain_parts = [
            _text_value(row, "describe"),
            _text_value(row, "effectsDes"),
        ]
        description = "\n\n".join(part for part in description_parts if part)
        plain_description = "\n\n".join(part for part in plain_parts if part)
        if not description and not plain_description:
            continue
        effect_type_label = _text_value(row, "effectDescribe")
        activity_name = _text_value(row, "activityDescribe")
        max_number = row.get("maxNumber")
        subtitle = " · ".join(
            part
            for part in (
                effect_type_label,
                activity_name,
                f"上限 {max_number}" if max_number not in (None, "", 0) else "",
            )
            if part
        )
        detail = {
            "kind": "faze",
            "title": _text_value(row, "showName") or _text_value(row, "name") or f"法则 {faze_id}",
            "subtitle": subtitle,
            "description": description or plain_description,
            "plain_description": plain_description or description,
            "source": "FazeResource.FazeResource",
            "source_id": faze_id,
            "quality": row.get("quality"),
            "activity_name": activity_name,
            "effect_type_label": effect_type_label,
            "max_number": max_number,
            "condition": row.get("showCondition") or row.get("enterCondition"),
        }
        details_by_id[faze_id] = {key: value for key, value in detail.items() if value not in (None, "", [], {})}

    return details_by_id, {
        "faze_source": str(faze_path) if faze_path.is_file() else "",
        "faze_row_count": len(rows),
        "faze_detail_count": len(details_by_id),
    }


FIXED_ITEM_STONE_VALUES: dict[int, int] = {
    5030001: 10,
}


def _item_stone_value(item_id: int | None, effect_text: Any) -> int | float | None:
    if item_id in FIXED_ITEM_STONE_VALUES:
        return FIXED_ITEM_STONE_VALUES[item_id]
    text = str(effect_text or "")
    match = re.search(r"(?:提升|增加|获得)\s*([0-9]+(?:\.[0-9]+)?)\s*点?\s*(?:友好度|好感度)", text)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    if value <= 0:
        return None
    return int(value) if value.is_integer() else value


def _compact_item_row(
    row: dict[str, Any],
    quality_by_id: dict[int, dict[str, Any]],
    progression_counts_by_gid: dict[int, dict[str, int]] | None = None,
    time_hints_by_id: dict[str, list[dict[str, Any]]] | None = None,
    optional_gift_rewards_by_group: dict[str, list[dict[str, Any]]] | None = None,
    talisman_details_by_id: dict[int, dict[str, Any]] | None = None,
    spiritual_body_details_by_id: dict[int, dict[str, Any]] | None = None,
    title_details_by_id: dict[int, dict[str, Any]] | None = None,
    fashion_details_by_id: dict[int, dict[str, Any]] | None = None,
    gongfa_details_by_id: dict[int, dict[str, Any]] | None = None,
    gongfa_jie_book_details_by_id: dict[int, dict[str, Any]] | None = None,
    gongfa_feature_probe_book_details_by_id: dict[int, dict[str, Any]] | None = None,
    special_gongfa_jie_details_by_item_id: dict[int, dict[str, Any]] | None = None,
    physical_exercise_details_by_id: dict[int, dict[str, Any]] | None = None,
    partner_details_by_id: dict[int, dict[str, Any]] | None = None,
    npc_gift_details_by_item_id: dict[int, dict[str, Any]] | None = None,
    hidden_world_details_by_item_id: dict[int, dict[str, Any]] | None = None,
    pet_gift_details_by_id: dict[int, dict[str, Any]] | None = None,
    member_details_by_id: dict[int, dict[str, Any]] | None = None,
    member_equipment_details_by_group_id: dict[int, dict[str, Any]] | None = None,
    take_medicine_details_by_item_id: dict[int, dict[str, Any]] | None = None,
    medical_recipe_details_by_id: dict[int, dict[str, Any]] | None = None,
    medical_recipe_details_by_formula_item_id: dict[int, dict[str, Any]] | None = None,
    wallet_resource_details_by_id: dict[int, dict[str, Any]] | None = None,
    boss_kill_effect_details_by_id: dict[int, dict[str, Any]] | None = None,
    faze_details_by_id: dict[int, dict[str, Any]] | None = None,
    spiritware_details_by_item_id: dict[int, dict[str, Any]] | None = None,
    swordsoul_details_by_item_id: dict[int, dict[str, Any]] | None = None,
    swordsoul_line_details_by_item_id: dict[int, dict[str, Any]] | None = None,
    sword_base_details_by_item_id: dict[int, dict[str, Any]] | None = None,
    flame_square_details_by_item_id: dict[int, dict[str, Any]] | None = None,
    equipment_details_by_item_id: dict[int, dict[str, Any]] | None = None,
    coreware_details_by_item_id: dict[int, dict[str, Any]] | None = None,
    partner_weapon_stone_details_by_item_id: dict[int, dict[str, Any]] | None = None,
    redbag_details_by_id: dict[int, dict[str, Any]] | None = None,
    title_details_by_item_id: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    quality = row.get("quality")
    effect_text = _text_value(row, "effDescript")
    effect_value = row.get("effectValue")
    effect_gid = _as_int(effect_value)
    item_id = _as_int(row.get("id") or row.get("_row_key"))
    progression_counts = dict((progression_counts_by_gid or {}).get(effect_gid or -1, {}))
    optional_gift_group_id = _optional_gift_group_id(effect_value)
    optional_gift_rewards = list((optional_gift_rewards_by_group or {}).get(optional_gift_group_id) or [])
    optional_gift_detail = _build_optional_gift_effect_detail(optional_gift_group_id, optional_gift_rewards)
    linked_talisman_id = _linked_talisman_id(effect_value) if str(row.get("type")) == "5" and str(row.get("subType")) == "6" else None
    talisman_detail = (talisman_details_by_id or {}).get(linked_talisman_id) if linked_talisman_id is not None else None
    linked_spiritual_body_id = (
        _linked_spiritual_body_id(effect_value) if str(row.get("type")) == "5" and str(row.get("subType")) == "37" else None
    )
    spiritual_body_detail = (
        (spiritual_body_details_by_id or {}).get(linked_spiritual_body_id)
        if linked_spiritual_body_id is not None
        else None
    )
    linked_title_id = _linked_title_id(effect_value) if str(row.get("type")) == "15" or str(effect_value).startswith("TITLE|") else None
    title_detail = (title_details_by_id or {}).get(linked_title_id) if linked_title_id is not None else None
    if title_detail is None and item_id is not None and str(row.get("type")) == "15":
        title_detail = (title_details_by_item_id or {}).get(item_id)
        if title_detail and title_detail.get("source_id") not in (None, ""):
            linked_title_id = _as_int(title_detail.get("source_id"))
    title_local_detail = _build_title_local_fallback_detail(row, linked_title_id) if title_detail is None else None
    linked_fashion_id = _linked_fashion_id(effect_value) if str(row.get("type")) == "8" else None
    fashion_detail = (fashion_details_by_id or {}).get(linked_fashion_id) if linked_fashion_id is not None else None
    linked_gongfa_id = (
        _linked_gongfa_id(effect_value)
        if str(row.get("type")) == "3"
        or (str(row.get("type")) == "999" and str(row.get("subType")) in {"33", "87"})
        else None
    )
    gongfa_detail = (gongfa_details_by_id or {}).get(linked_gongfa_id) if linked_gongfa_id is not None else None
    gongfa_jie_book_detail = (
        (gongfa_jie_book_details_by_id or {}).get(linked_gongfa_id)
        if linked_gongfa_id is not None and str(row.get("type")) == "3" and str(row.get("subType")) == "8"
        else None
    )
    gongfa_feature_probe_book_detail = (
        None
        if gongfa_jie_book_detail
        else _build_gongfa_feature_probe_item_detail(row, gongfa_feature_probe_book_details_by_id)
    )
    gongfa_local_description_detail = (
        None
        if any((gongfa_detail, gongfa_jie_book_detail, gongfa_feature_probe_book_detail))
        else _build_gongfa_local_description_detail(row)
    )
    special_gongfa_jie_detail = (
        (special_gongfa_jie_details_by_item_id or {}).get(item_id) if item_id is not None else None
    )
    if special_gongfa_jie_detail:
        gongfa_local_description_detail = None
    linked_physical_exercise_id = (
        _linked_physical_exercise_id(effect_value)
        if str(row.get("type")) == "3" and str(row.get("subType")) == "9"
        else None
    )
    physical_exercise_detail = (
        (physical_exercise_details_by_id or {}).get(linked_physical_exercise_id)
        if linked_physical_exercise_id is not None
        else None
    )
    linked_partner_id = _linked_partner_id(effect_value)
    if linked_partner_id is None and str(row.get("type")) == "1" and str(row.get("subType")) == "66":
        candidate_partner_id = _as_int(effect_value)
        linked_partner_id = candidate_partner_id if candidate_partner_id is not None and candidate_partner_id > 0 else None
    partner_detail = (partner_details_by_id or {}).get(linked_partner_id) if linked_partner_id is not None else None
    linked_member_id = _linked_member_id(effect_value)
    member_detail = (member_details_by_id or {}).get(linked_member_id) if linked_member_id is not None else None
    linked_member_equipment_group_id = (
        _linked_member_equipment_group_id(effect_value)
        if str(row.get("type")) == "1" and str(row.get("subType")) == "72"
        else None
    )
    member_equipment_detail = (
        (member_equipment_details_by_group_id or {}).get(linked_member_equipment_group_id)
        if linked_member_equipment_group_id is not None
        else None
    )
    member_equipment_item_detail = _build_member_equipment_item_detail(row)
    linked_medical_id = _linked_medical_id(effect_value) if str(row.get("type")) == "6" else None
    medical_recipe_detail = (
        (medical_recipe_details_by_id or {}).get(linked_medical_id)
        if linked_medical_id is not None
        else None
    )
    npc_gift_detail = (npc_gift_details_by_item_id or {}).get(item_id) if item_id is not None else None
    linked_hidden_world_item_id = _linked_hidden_world_item_id(effect_value)
    hidden_world_detail = (
        (hidden_world_details_by_item_id or {}).get(linked_hidden_world_item_id)
        if linked_hidden_world_item_id is not None
        else None
    )
    linked_pet_gift_id = _linked_pet_gift_id(effect_value)
    pet_gift_detail = (pet_gift_details_by_id or {}).get(linked_pet_gift_id) if linked_pet_gift_id is not None else None
    if medical_recipe_detail is None and item_id is not None and str(row.get("type")) == "6":
        medical_recipe_detail = (medical_recipe_details_by_formula_item_id or {}).get(item_id)
    linked_wallet_resource_id = _linked_wallet_resource_id(effect_value)
    wallet_resource_detail = (
        (wallet_resource_details_by_id or {}).get(linked_wallet_resource_id)
        if linked_wallet_resource_id is not None
        else None
    )
    linked_boss_kill_effect_id = _linked_boss_kill_effect_id(effect_value)
    boss_kill_effect_detail = (
        (boss_kill_effect_details_by_id or {}).get(linked_boss_kill_effect_id)
        if linked_boss_kill_effect_id is not None
        else None
    )
    equipment_material_effect_detail = _build_equipment_material_effect_detail(row, boss_kill_effect_details_by_id)
    linked_faze_id = _linked_faze_id(effect_value) if str(row.get("type")) == "23" else None
    faze_detail = (faze_details_by_id or {}).get(linked_faze_id) if linked_faze_id is not None else None
    spiritware_detail = (spiritware_details_by_item_id or {}).get(item_id) if item_id is not None else None
    swordsoul_detail = (swordsoul_details_by_item_id or {}).get(item_id) if item_id is not None else None
    swordsoul_line_detail = (swordsoul_line_details_by_item_id or {}).get(item_id) if item_id is not None else None
    sword_base_detail = (sword_base_details_by_item_id or {}).get(item_id) if item_id is not None else None
    flame_square_detail = (flame_square_details_by_item_id or {}).get(item_id) if item_id is not None else None
    equipment_detail = (equipment_details_by_item_id or {}).get(item_id) if item_id is not None else None
    coreware_detail = (coreware_details_by_item_id or {}).get(item_id) if item_id is not None else None
    partner_weapon_stone_detail = (
        (partner_weapon_stone_details_by_item_id or {}).get(item_id) if item_id is not None else None
    )
    linked_redbag_id = _as_int(effect_value) if str(row.get("type")) == "45" else None
    redbag_detail = (redbag_details_by_id or {}).get(linked_redbag_id) if linked_redbag_id is not None else None
    talisman_refine_material_detail = _build_talisman_refine_material_detail(row, talisman_details_by_id)
    show_effect_detail = _build_show_effect_detail(row)
    prefixed_item_effect_detail = _build_prefixed_item_effect_detail(row)
    take_medicine_detail = (
        (take_medicine_details_by_item_id or {}).get(item_id)
        if item_id is not None and str(row.get("type")) == "22"
        else None
    )
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
        "source_table": "Item",
        "source_path": str(row.get("_source_path") or ""),
        "icon_source_table": "Item" if row.get("icon") else "",
        "icon_source_field": "icon" if row.get("icon") else "",
    }
    stone_value = _item_stone_value(item_id, "\n".join((_text_value(row, "descript"), effect_text)))
    if stone_value is not None:
        card["stone_value"] = stone_value
    if optional_gift_rewards:
        card["optional_gift_group_id"] = optional_gift_group_id
        card["optional_gift_rewards"] = optional_gift_rewards
    if linked_talisman_id is not None:
        card["linked_talisman_id"] = linked_talisman_id
    if linked_spiritual_body_id is not None:
        card["linked_spiritual_body_id"] = linked_spiritual_body_id
    if linked_title_id is not None:
        card["linked_title_id"] = linked_title_id
    if linked_fashion_id is not None:
        card["linked_fashion_id"] = linked_fashion_id
    if linked_gongfa_id is not None:
        card["linked_gongfa_id"] = linked_gongfa_id
    if gongfa_jie_book_detail and linked_gongfa_id is not None:
        card["linked_gongfa_jie_effect_id"] = linked_gongfa_id
        if gongfa_jie_book_detail.get("gongfa_jie_gid") not in (None, ""):
            card["linked_gongfa_jie_gid"] = gongfa_jie_book_detail.get("gongfa_jie_gid")
    if gongfa_feature_probe_book_detail and linked_gongfa_id is not None:
        card["linked_gongfa_feature_gid"] = linked_gongfa_id
        if gongfa_feature_probe_book_detail.get("gongfa_feature_prefixes"):
            card["linked_gongfa_feature_prefixes"] = gongfa_feature_probe_book_detail.get("gongfa_feature_prefixes")
    if special_gongfa_jie_detail and item_id is not None:
        card["linked_special_gongfa_item_id"] = item_id
        if special_gongfa_jie_detail.get("special_gongfa_gid") not in (None, ""):
            card["linked_special_gongfa_gid"] = special_gongfa_jie_detail.get("special_gongfa_gid")
    if linked_physical_exercise_id is not None:
        card["linked_physical_exercise_id"] = linked_physical_exercise_id
    if linked_partner_id is not None:
        card["linked_partner_id"] = linked_partner_id
    if linked_hidden_world_item_id is not None:
        card["linked_hidden_world_item_id"] = linked_hidden_world_item_id
    if linked_pet_gift_id is not None:
        card["linked_pet_gift_id"] = linked_pet_gift_id
    if linked_member_id is not None:
        card["linked_member_id"] = linked_member_id
    if linked_member_equipment_group_id is not None:
        card["linked_member_equipment_group_id"] = linked_member_equipment_group_id
    linked_member_equipment_item_id = _linked_member_equipment_item_id(effect_value)
    if linked_member_equipment_item_id is not None:
        card["linked_member_equipment_item_id"] = linked_member_equipment_item_id
    if linked_medical_id is not None:
        card["linked_medical_id"] = linked_medical_id
    if linked_wallet_resource_id is not None:
        card["linked_wallet_resource_id"] = linked_wallet_resource_id
    if linked_boss_kill_effect_id is not None:
        card["linked_boss_kill_effect_id"] = linked_boss_kill_effect_id
    if equipment_material_effect_detail and equipment_material_effect_detail.get("equipment_material_effect_id") is not None:
        card["linked_equipment_material_effect_id"] = equipment_material_effect_detail.get("equipment_material_effect_id")
    if linked_faze_id is not None:
        card["linked_faze_id"] = linked_faze_id
    if spiritware_detail:
        card["linked_spiritware_item_id"] = item_id
    if swordsoul_detail:
        card["linked_swordsoul_item_id"] = item_id
        if swordsoul_detail.get("swordsoul_id") not in (None, ""):
            card["linked_swordsoul_id"] = swordsoul_detail.get("swordsoul_id")
        if swordsoul_detail.get("swordsoul_part") not in (None, ""):
            card["linked_swordsoul_part"] = swordsoul_detail.get("swordsoul_part")
    if swordsoul_line_detail:
        card["linked_swordsoul_line_item_id"] = item_id
        if swordsoul_line_detail.get("swordsoul_id") not in (None, ""):
            card["linked_swordsoul_id"] = swordsoul_line_detail.get("swordsoul_id")
        if swordsoul_line_detail.get("swordsoul_part") not in (None, ""):
            card["linked_swordsoul_part"] = swordsoul_line_detail.get("swordsoul_part")
    if sword_base_detail:
        card["linked_sword_item_id"] = item_id
        if sword_base_detail.get("sword_id") not in (None, ""):
            card["linked_sword_id"] = sword_base_detail.get("sword_id")
    if flame_square_detail:
        card["linked_flame_item_id"] = item_id
        if flame_square_detail.get("flame_id") not in (None, ""):
            card["linked_flame_id"] = flame_square_detail.get("flame_id")
    if equipment_detail and item_id is not None:
        if equipment_detail.get("kind") == "equipment_gem":
            card["linked_equipment_gem_item_id"] = item_id
        else:
            card["linked_equipment_item_id"] = item_id
    if coreware_detail and item_id is not None:
        card["linked_coreware_item_id"] = item_id
    if partner_weapon_stone_detail and item_id is not None:
        card["linked_partner_weapon_stone_item_id"] = item_id
        if partner_weapon_stone_detail.get("partner_weapon_partner_id") not in (None, ""):
            card["linked_partner_weapon_partner_id"] = partner_weapon_stone_detail.get("partner_weapon_partner_id")
        if partner_weapon_stone_detail.get("partner_weapon_id") not in (None, ""):
            card["linked_partner_weapon_id"] = partner_weapon_stone_detail.get("partner_weapon_id")
    if linked_redbag_id is not None:
        card["linked_redbag_id"] = linked_redbag_id
    if talisman_refine_material_detail and talisman_refine_material_detail.get("target_talisman_id") not in (None, ""):
        card["linked_talisman_refine_target_id"] = talisman_refine_material_detail.get("target_talisman_id")
    effect_details = [
        dict(detail)
        for detail in (
            talisman_detail,
            optional_gift_detail,
            spiritual_body_detail,
            title_detail,
            title_local_detail,
            fashion_detail,
            gongfa_detail,
            gongfa_jie_book_detail,
            gongfa_feature_probe_book_detail,
            gongfa_local_description_detail,
            special_gongfa_jie_detail,
            physical_exercise_detail,
            partner_detail,
            npc_gift_detail,
            hidden_world_detail,
            pet_gift_detail,
            member_detail,
            member_equipment_detail,
            member_equipment_item_detail,
            medical_recipe_detail,
            wallet_resource_detail,
            boss_kill_effect_detail,
            equipment_material_effect_detail,
            faze_detail,
            spiritware_detail,
            swordsoul_detail,
            swordsoul_line_detail,
            sword_base_detail,
            flame_square_detail,
            equipment_detail,
            coreware_detail,
            partner_weapon_stone_detail,
            redbag_detail,
            talisman_refine_material_detail,
            show_effect_detail,
            prefixed_item_effect_detail,
            take_medicine_detail,
        )
        if detail
    ]
    if effect_details:
        card["effect_details"] = effect_details
        card["effect_detail_preview"] = _item_effect_detail_preview(card)
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


def _annotate_item_icon_reuse(cards: list[dict[str, Any]], *, primary_reuse_risk_threshold: int = 50) -> None:
    icon_counts: dict[str, int] = {}
    small_icon_counts: dict[str, int] = {}
    for card in cards:
        icon = str(card.get("icon") or "").strip()
        if icon:
            icon_counts[icon] = icon_counts.get(icon, 0) + 1
        small_icon = str(card.get("small_icon") or "").strip()
        if small_icon:
            small_icon_counts[small_icon] = small_icon_counts.get(small_icon, 0) + 1
    for card in cards:
        icon = str(card.get("icon") or "").strip()
        if icon:
            reuse_count = icon_counts.get(icon, 0)
            card["icon_reuse_count"] = reuse_count
            if reuse_count >= primary_reuse_risk_threshold:
                card["icon_quality_risk"] = "high_reuse_primary_icon"
                card["icon_quality_note"] = f"主图标被 {reuse_count} 个道具共用，可能是通用配置图标。"
        small_icon = str(card.get("small_icon") or "").strip()
        if small_icon:
            small_reuse_count = small_icon_counts.get(small_icon, 0)
            card["small_icon_reuse_count"] = small_reuse_count
            if small_reuse_count >= primary_reuse_risk_threshold:
                card["small_icon_quality_risk"] = "high_reuse_small_icon"
                card["small_icon_quality_note"] = f"小图标被 {small_reuse_count} 个道具共用，可能是通用角标或小图标。"


def _default_catalog_source_files(root: Path) -> list[Path]:
    optional_gift_path = _find_optional_gift_lua(root)
    talisman_path = _find_talisman_lua(root, "Talisman.lua")
    talisman_grade_path = _find_talisman_lua(root, "TalismanGrade.lua")
    spiritual_body_path = _find_spiritual_body_lua(root, "SpiritualBody.lua")
    spiritual_body_jie_path = _find_spiritual_body_lua(root, "SpiritualBodyJie.lua")
    spiritual_body_quality_path = _find_spiritual_body_lua(root, "SpiritualQuality.lua")
    title_path = _find_title_lua(root, "Title.lua")
    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    fashion_path = _find_fashion_lua(root, "Fashion.lua")
    fashion_level_path = _find_fashion_lua(root, "FashionLevel.lua")
    fashion_type_path = _find_fashion_lua(root, "FashionType.lua")
    gongfa_path = _find_gongfa_lua(root, "Gongfa.lua")
    gongfa_pin_path = _find_gongfa_lua(root, "GongfaPin.lua")
    gongfa_career_path = _find_gongfa_lua(root, "GongfaCareer.lua")
    gongfa_jie_book_path = _find_gongfa_lua(root, "Renjie-GongfaJie.lua")
    special_gongfa_jie_path = _find_gongfa_lua(root, "Special-GongfaJie.lua")
    gongfa_skill_path = _find_gongfa_lua(root, "GongfaSkill.lua")
    physical_path = _find_physical_exercise_lua(root, "Physical.lua")
    physical_jie_path = _find_physical_exercise_lua(root, "PhysicalJie.lua")
    physical_comprehension_path = _find_physical_exercise_lua(root, "Comprehension.lua")
    partner_path = _find_partner_lua(root, "Partner.lua")
    partner_quality_path = _find_partner_lua(root, "PartnerQuality.lua")
    partner_grade_path = _find_partner_lua(root, "PartnerGrade.lua")
    partner_show_skill_path = _find_partner_lua(root, "PartnerShowSkill.lua")
    partner_skill_level_path = _find_partner_lua(root, "PartnerSkillLevel.lua")
    partner_active_skill_path = _find_partner_lua(root, "PartnerActiveSkill.lua")
    partner_arane_path = _find_partner_lua(root, "PartnerAraneResource.lua")
    npc_gift_path = _find_npc_lua(root, "NpcGift.lua")
    npc_path = _find_npc_lua(root, "Npc.lua")
    hidden_world_item_path = _find_hidden_world_lua(root, "HiddenWorldItem.lua")
    hidden_world_skill_path = _find_hidden_world_lua(root, "HiddenWorldSkill.lua")
    hidden_world_camp_path = _find_hidden_world_lua(root, "HiddenWorldCampBase.lua")
    hidden_world_career_path = _find_hidden_world_lua(root, "HiddenWorldCampCareer.lua")
    pet_gift_path = _find_pet_lua(root, "PetGift.lua")
    member_path = _find_dragon_member_lua(root, "MemberBase.lua")
    member_star_path = _find_dragon_member_lua(root, "MemberStar.lua")
    member_equipment_path = _find_dragon_boat_festival_lua(root, "DragonBoatEquipment.lua")
    member_equipment_skill_path = _find_dragon_boat_festival_lua(root, "BoatSkill.lua")
    take_medicine_path = _find_take_medicine_lua(root, "TakeMedicine.lua")
    take_medicine_type_path = _find_take_medicine_lua(root, "TakeMedicineType.lua")
    medical_path = _find_medical_lua(root, "Medical.lua")
    medical_type_path = _find_medical_lua(root, "MedicalType.lua")
    medical_effect_path = _find_medical_lua(root, "MedicalEffect.lua")
    medical_effect_index_path = _find_medical_lua(root, "MedicalEffectIndex.lua")
    resource_path = _find_resource_lua(root, "Resource.lua")
    boss_kill_effect_path = _find_boss_kill_effect_lua(root, "BossKillEffect.lua")
    spiritware_path = _find_spiritware_lua(root, "SpiritWare.lua")
    spiritware_item_path = _find_spiritware_lua(root, "SpiritWareItem.lua")
    spiritware_base_path = _find_spiritware_lua(root, "SpiritWareBase.lua")
    spiritware_ultra_path = _find_spiritware_lua(root, "SpiritWareUltra.lua")
    spiritware_soul_path = _find_spiritware_lua(root, "SpiritWareSoul.lua")
    spiritware_cleanse_item_path = _find_spiritware_lua(root, "SpiritWareCleanseItem.lua")
    swordsoul_base_path = _find_swordsoul_lua(root, "SwordSoulBase.lua")
    swordsoul_awakening_path = _find_swordsoul_lua(root, "SwordSoulAwakening.lua")
    swordsoul_lines_path = _find_swordsoul_lua(root, "SwordSoulLines.lua")
    swordsoul_line_base_path = _find_swordsoul_lua(root, "SwordLinesBase.lua")
    swordsoul_line_level_path = _find_swordsoul_lua(root, "SwordLinesLevel.lua")
    swordsoul_line_attr_path = _find_swordsoul_lua(root, "SwordLinesAttr.lua")
    swordsoul_line_attr_quality_path = _find_swordsoul_lua(root, "SwordLinesAttrQuality.lua")
    swordsoul_eff_path = _find_swordsoul_lua(root, "SwordSoulEff.lua")
    swordsoul_line_wash_path = _find_swordsoul_lua(root, "SwordLinesWash.lua")
    sword_base_path = _find_swordsoul_lua(root, "SwordBase.lua")
    sword_level_up_path = _find_swordsoul_lua(root, "SwordLevelUp.lua")
    sword_key_point_path = _find_swordsoul_lua(root, "SwordKeyPoint.lua")
    flame_level_path = _find_flame_square_lua(root, "FlameLevel.lua")
    flame_square_build_path = _find_flame_square_lua(root, "FlameSquareBuild.lua")
    flame_square_level_path = _find_flame_square_lua(root, "FlameSquareLevel.lua")
    equipment_path = _find_equipment_lua(root, "Equipment.lua")
    equipment_item_path = _find_equipment_lua(root, "EquipmentItem.lua")
    equipment_tag_path = _find_equipment_lua(root, "EquipmentTag.lua")
    equipment_gem_path = _find_equipment_lua(root, "GemDevelop.lua")
    equipment_gem_suit_path = _find_equipment_lua(root, "GemSuit.lua")
    redbag_path = _find_redbag_lua(root, "RedBag.lua")
    lang_path = _find_default_lang_path(root)
    return [
        root / DEFAULT_ITEM_ROWS,
        root / DEFAULT_QUALITY_ROWS,
        *([optional_gift_path] if optional_gift_path else []),
        *([talisman_path] if talisman_path else []),
        *([talisman_grade_path] if talisman_grade_path else []),
        *([spiritual_body_path] if spiritual_body_path else []),
        *([spiritual_body_jie_path] if spiritual_body_jie_path else []),
        *([spiritual_body_quality_path] if spiritual_body_quality_path else []),
        *([title_path] if title_path else []),
        *([attribute_path] if attribute_path else []),
        *([fashion_path] if fashion_path else []),
        *([fashion_level_path] if fashion_level_path else []),
        *([fashion_type_path] if fashion_type_path else []),
        *([gongfa_path] if gongfa_path else []),
        *([gongfa_pin_path] if gongfa_pin_path else []),
        *([gongfa_career_path] if gongfa_career_path else []),
        *([gongfa_jie_book_path] if gongfa_jie_book_path else []),
        *([special_gongfa_jie_path] if special_gongfa_jie_path else []),
        *([gongfa_skill_path] if gongfa_skill_path else []),
        *([physical_path] if physical_path else []),
        *([physical_jie_path] if physical_jie_path else []),
        *([physical_comprehension_path] if physical_comprehension_path else []),
        *([partner_path] if partner_path else []),
        *([partner_quality_path] if partner_quality_path else []),
        *([partner_grade_path] if partner_grade_path else []),
        *([partner_show_skill_path] if partner_show_skill_path else []),
        *([partner_skill_level_path] if partner_skill_level_path else []),
        *([partner_active_skill_path] if partner_active_skill_path else []),
        *([partner_arane_path] if partner_arane_path else []),
        *([npc_gift_path] if npc_gift_path else []),
        *([npc_path] if npc_path else []),
        *([hidden_world_item_path] if hidden_world_item_path else []),
        *([hidden_world_skill_path] if hidden_world_skill_path else []),
        *([hidden_world_camp_path] if hidden_world_camp_path else []),
        *([hidden_world_career_path] if hidden_world_career_path else []),
        *([pet_gift_path] if pet_gift_path else []),
        *([member_path] if member_path else []),
        *([member_star_path] if member_star_path else []),
        *([member_equipment_path] if member_equipment_path else []),
        *([member_equipment_skill_path] if member_equipment_skill_path else []),
        *([take_medicine_path] if take_medicine_path else []),
        *([take_medicine_type_path] if take_medicine_type_path else []),
        *([medical_path] if medical_path else []),
        *([medical_type_path] if medical_type_path else []),
        *([medical_effect_path] if medical_effect_path else []),
        *([medical_effect_index_path] if medical_effect_index_path else []),
        *([resource_path] if resource_path else []),
        *([boss_kill_effect_path] if boss_kill_effect_path else []),
        *([spiritware_path] if spiritware_path else []),
        *([spiritware_item_path] if spiritware_item_path else []),
        *([spiritware_base_path] if spiritware_base_path else []),
        *([spiritware_ultra_path] if spiritware_ultra_path else []),
        *([spiritware_soul_path] if spiritware_soul_path else []),
        *([spiritware_cleanse_item_path] if spiritware_cleanse_item_path else []),
        *([swordsoul_base_path] if swordsoul_base_path else []),
        *([swordsoul_awakening_path] if swordsoul_awakening_path else []),
        *([swordsoul_lines_path] if swordsoul_lines_path else []),
        *([swordsoul_line_base_path] if swordsoul_line_base_path else []),
        *([swordsoul_line_level_path] if swordsoul_line_level_path else []),
        *([swordsoul_line_attr_path] if swordsoul_line_attr_path else []),
        *([swordsoul_line_attr_quality_path] if swordsoul_line_attr_quality_path else []),
        *([swordsoul_eff_path] if swordsoul_eff_path else []),
        *([swordsoul_line_wash_path] if swordsoul_line_wash_path else []),
        *([sword_base_path] if sword_base_path else []),
        *([sword_level_up_path] if sword_level_up_path else []),
        *([sword_key_point_path] if sword_key_point_path else []),
        *([flame_level_path] if flame_level_path else []),
        *([flame_square_build_path] if flame_square_build_path else []),
        *([flame_square_level_path] if flame_square_level_path else []),
        *([equipment_path] if equipment_path else []),
        *([equipment_item_path] if equipment_item_path else []),
        *([equipment_tag_path] if equipment_tag_path else []),
        *([equipment_gem_path] if equipment_gem_path else []),
        *([equipment_gem_suit_path] if equipment_gem_suit_path else []),
        *([redbag_path] if redbag_path else []),
        *([lang_path] if lang_path else []),
        *([root / DEFAULT_GONGFA_FEATURE_FAMILIES] if (root / DEFAULT_GONGFA_FEATURE_FAMILIES).is_file() else []),
        *([root / DEFAULT_GONGFA_FEATURE_LINKS] if (root / DEFAULT_GONGFA_FEATURE_LINKS).is_file() else []),
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
    small_icon = _normalize_search_text(card.get("small_icon"))
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
    effect_detail_text = _normalize_search_text(
        " ".join(
            _effect_detail_search_text(detail)
            for detail in card.get("effect_details") or []
            if isinstance(detail, dict)
        )
    )
    return {
        "index": index,
        "card": card,
        "item_id": item_id,
        "name": name,
        "icon": icon,
        "small_icon": small_icon,
        "description": description,
        "effect_description": effect_description,
        "effect_detail_text": effect_detail_text,
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
        "combined": " ".join(
            [
                item_id,
                name,
                icon,
                small_icon,
                description,
                effect_description,
                effect_detail_text,
                progression_text,
                optional_gift_text,
                *quality_texts,
                *type_texts,
            ]
        ),
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
        "icon_quality_options": _build_item_icon_quality_options(cards),
        "high_reuse_icon_options": _build_item_high_reuse_icon_options(cards),
        "small_icon_quality_options": _build_item_small_icon_quality_options(cards),
        "high_reuse_small_icon_options": _build_item_high_reuse_small_icon_options(cards),
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
        *(detail.get("description") for detail in card.get("effect_details") or [] if isinstance(detail, dict)),
        *(reward.get("name") for reward in card.get("optional_gift_rewards") or [] if isinstance(reward, dict)),
        limit=limit,
    )


def _item_effect_detail_preview(card: dict[str, Any], *, limit: int = 180) -> str:
    for detail in card.get("effect_details") or []:
        if not isinstance(detail, dict):
            continue
        preview = _preview(detail.get("plain_description") or detail.get("description"), limit)
        if preview:
            return preview
    return ""


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
        "source_table": card.get("source_table"),
        "source_path": card.get("source_path"),
        "source_row_key": card.get("source_row_key"),
        "icon_source_table": card.get("icon_source_table"),
        "icon_source_field": card.get("icon_source_field"),
        "icon_reuse_count": card.get("icon_reuse_count"),
        "icon_quality_risk": card.get("icon_quality_risk"),
        "icon_quality_note": card.get("icon_quality_note"),
        "small_icon_reuse_count": card.get("small_icon_reuse_count"),
        "small_icon_quality_risk": card.get("small_icon_quality_risk"),
        "small_icon_quality_note": card.get("small_icon_quality_note"),
        "terms": _card_terms(card),
        "score": score,
    }
    if card.get("first_time_hint"):
        item["first_time_hint"] = card.get("first_time_hint")
    effect_detail_preview = card.get("effect_detail_preview") or _item_effect_detail_preview(card)
    if effect_detail_preview:
        item["effect_detail_preview"] = effect_detail_preview
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


def _item_icon_quality_key(card: dict[str, Any]) -> str:
    return "high_reuse" if card.get("icon_quality_risk") else "normal"


def _item_small_icon_quality_key(card: dict[str, Any]) -> str:
    return "high_reuse" if card.get("small_icon_quality_risk") else "normal"


def _item_icon_quality_label(value: str) -> str:
    if value == "high_reuse":
        return "高复用"
    return "普通"


def _build_item_icon_quality_options(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {"high_reuse": 0, "normal": 1}
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        value = _item_icon_quality_key(card)
        item = grouped.setdefault(
            value,
            {
                "value": value,
                "label": _item_icon_quality_label(value),
                "count": 0,
            },
        )
        item["count"] += 1
    return sorted(grouped.values(), key=lambda item: (order.get(str(item.get("value")), 99), str(item.get("label") or "")))


def _build_item_small_icon_quality_options(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {"high_reuse": 0, "normal": 1}
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        value = _item_small_icon_quality_key(card)
        item = grouped.setdefault(
            value,
            {
                "value": value,
                "label": _item_icon_quality_label(value),
                "count": 0,
            },
        )
        item["count"] += 1
    return sorted(grouped.values(), key=lambda item: (order.get(str(item.get("value")), 99), str(item.get("label") or "")))


def _build_item_high_reuse_icon_group_options(
    cards: list[dict[str, Any]],
    *,
    icon_field: str,
    risk_field: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        if not card.get(risk_field):
            continue
        icon = str(card.get(icon_field) or "").strip()
        if not icon:
            continue
        item = grouped.setdefault(
            icon,
            {
                "value": icon,
                "label": icon,
                "count": 0,
                "type_counts": {},
                "samples": [],
            },
        )
        item["count"] += 1
        type_label = str(card.get("type_name") or card.get("type_key") or "类型未知").strip() or "类型未知"
        item["type_counts"][type_label] = int(item["type_counts"].get(type_label) or 0) + 1
        samples = item["samples"]
        if len(samples) < 8:
            samples.append(
                {
                    "id": card.get("id"),
                    "name": card.get("name"),
                    "type_name": card.get("type_name"),
                }
            )
    options = []
    for item in grouped.values():
        type_counts = item.pop("type_counts")
        total_count = int(item.get("count") or 0)
        sorted_type_counts = sorted(type_counts.items(), key=lambda entry: (-int(entry[1]), str(entry[0])))
        top_type_name, top_type_count = sorted_type_counts[0] if sorted_type_counts else ("类型未知", 0)
        top_type_ratio = (int(top_type_count) / total_count) if total_count else 0.0
        type_summary_parts = [
            f"{name}{count}"
            for name, count in sorted_type_counts[:6]
        ]
        item["type_summary"] = "；".join(type_summary_parts)
        item["dominant_type"] = top_type_name
        item["dominant_type_ratio"] = round(top_type_ratio, 4)
        if top_type_ratio >= 0.95:
            item["review_hint"] = "单一类型通用"
            item["review_priority"] = "medium"
        elif top_type_ratio >= 0.75:
            item["review_hint"] = "主类型为主"
            item["review_priority"] = "medium_high"
        else:
            item["review_hint"] = "跨类型混用"
            item["review_priority"] = "high"
        options.append(item)
    return sorted(options, key=lambda item: (-int(item.get("count") or 0), str(item.get("value") or "")))


def _build_item_high_reuse_icon_options(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _build_item_high_reuse_icon_group_options(cards, icon_field="icon", risk_field="icon_quality_risk")


def _build_item_high_reuse_small_icon_options(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _build_item_high_reuse_icon_group_options(cards, icon_field="small_icon", risk_field="small_icon_quality_risk")


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
        if term in doc["small_icon"]:
            score += 20
        if term in doc["description"]:
            score += 18
        if term in doc["effect_description"]:
            score += 28
        if term in doc["effect_detail_text"]:
            score += 32
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
        "icon_quality": {},
        "icon_name": {},
        "small_icon_quality": {},
        "small_icon_name": {},
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
        icon_quality = _item_icon_quality_key(card)
        rows["icon_quality"].setdefault(icon_quality, []).append(object_id)
        icon_name = str(card.get("icon") or "").strip()
        if icon_name:
            rows["icon_name"].setdefault(icon_name, []).append(object_id)
        small_icon_quality = _item_small_icon_quality_key(card)
        rows["small_icon_quality"].setdefault(small_icon_quality, []).append(object_id)
        small_icon_name = str(card.get("small_icon") or "").strip()
        if small_icon_name:
            rows["small_icon_name"].setdefault(small_icon_name, []).append(object_id)
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
    icon_quality: str = "",
    icon_name: str = "",
    small_icon_quality: str = "",
    small_icon_name: str = "",
    sort_by: str = "default",
    sort_order: str = "asc",
    limit: int = 80,
    offset: int = 0,
    include_facets: bool = True,
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
    icon_quality = str(icon_quality or "").strip()
    icon_name = str(icon_name or "").strip()
    small_icon_quality = str(small_icon_quality or "").strip()
    small_icon_name = str(small_icon_name or "").strip()
    if icon_quality not in {"", "high_reuse", "normal"}:
        icon_quality = ""
    if small_icon_quality not in {"", "high_reuse", "normal"}:
        small_icon_quality = ""
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
        if icon_quality and _item_icon_quality_key(card) != icon_quality:
            continue
        if icon_name and str(card.get("icon") or "").strip() != icon_name:
            continue
        if small_icon_quality and _item_small_icon_quality_key(card) != small_icon_quality:
            continue
        if small_icon_name and str(card.get("small_icon") or "").strip() != small_icon_name:
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
    response = {
        "query": query,
        "quality_name": quality_name,
        "type_key": type_key,
        "sub_type_key": sub_type_key,
        "icon_quality": icon_quality,
        "icon_name": icon_name,
        "small_icon_quality": small_icon_quality,
        "small_icon_name": small_icon_name,
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
        "icon_quality_options": runtime_index["icon_quality_options"],
        "high_reuse_icon_options": runtime_index["high_reuse_icon_options"],
        "small_icon_quality_options": runtime_index["small_icon_quality_options"],
        "high_reuse_small_icon_options": runtime_index["high_reuse_small_icon_options"],
        "items": [_format_item_search_item(card, score) for score, _index, card in page_rows],
    }
    if include_facets:
        response["facet_index"] = _build_item_facet_index(query_rows)
    return response


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
    for row in item_rows:
        row.setdefault("_source_path", str(item_path))
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
    talisman_details_by_id, talisman_stats = _build_talisman_effect_details_by_id(root)
    spiritual_body_details_by_id, spiritual_body_stats = _build_spiritual_body_effect_details_by_id(root)
    title_details_by_id, title_details_by_item_id, title_stats = _build_title_effect_details_by_id(root, quality_by_id)
    fashion_details_by_id, fashion_stats = _build_fashion_effect_details_by_id(root)
    gongfa_details_by_id, gongfa_stats = _build_gongfa_effect_details_by_id(root)
    gongfa_jie_book_details_by_id, gongfa_jie_book_stats = _build_gongfa_jie_book_effect_details_by_id(root)
    gongfa_feature_probe_book_details_by_id, gongfa_feature_probe_stats = _build_gongfa_feature_probe_book_details_by_id(root)
    special_gongfa_jie_details_by_item_id, special_gongfa_jie_stats = _build_special_gongfa_jie_effect_details_by_item_id(
        root, item_rows
    )
    physical_exercise_details_by_id, physical_exercise_stats = _build_physical_exercise_effect_details_by_id(root, quality_by_id)
    partner_details_by_id, partner_stats = _build_partner_effect_details_by_id(root, quality_by_id)
    npc_gift_details_by_item_id, npc_gift_stats = _build_npc_gift_effect_details_by_item_id(
        root, item_rows, partner_details_by_id
    )
    hidden_world_details_by_item_id, hidden_world_stats = _build_hidden_world_effect_details_by_item_id(root)
    pet_gift_details_by_id, pet_gift_stats = _build_pet_gift_effect_details_by_id(root)
    member_details_by_id, member_stats = _build_member_effect_details_by_id(root, quality_by_id)
    member_equipment_details_by_group_id, member_equipment_stats = _build_member_equipment_effect_details_by_group_id(
        root, quality_by_id
    )
    take_medicine_details_by_item_id, take_medicine_stats = _build_take_medicine_effect_details_by_item_id(root)
    medical_recipe_details_by_id, medical_recipe_details_by_formula_item_id, medical_stats = _build_medical_recipe_effect_details(
        root, item_rows, take_medicine_details_by_item_id
    )
    wallet_resource_details_by_id, wallet_resource_stats = _build_wallet_resource_effect_details_by_id(root, item_rows)
    boss_kill_effect_details_by_id, boss_kill_effect_stats = _build_boss_kill_effect_details_by_id(root)
    faze_details_by_id, faze_stats = _build_faze_effect_details_by_id(root)
    spiritware_details_by_item_id, spiritware_stats = _build_spiritware_effect_details_by_item_id(root, item_rows)
    swordsoul_details_by_item_id, swordsoul_stats = _build_swordsoul_awakening_effect_details_by_item_id(root)
    swordsoul_line_details_by_item_id, swordsoul_line_stats = _build_swordsoul_line_effect_details_by_item_id(
        root, item_rows, quality_by_id
    )
    sword_base_details_by_item_id, sword_base_stats = _build_sword_base_effect_details_by_item_id(root, item_rows)
    flame_square_details_by_item_id, flame_square_stats = _build_flame_square_effect_details_by_item_id(root, item_rows)
    equipment_details_by_item_id, equipment_stats = _build_equipment_effect_details_by_item_id(root, item_rows, quality_by_id)
    coreware_details_by_item_id, coreware_stats = _build_coreware_effect_details_by_item_id(root, item_rows, quality_by_id)
    partner_weapon_stone_details_by_item_id, partner_weapon_stone_stats = _build_partner_weapon_stone_effect_details_by_item_id(
        root, item_rows, quality_by_id, partner_details_by_id
    )
    redbag_details_by_id, redbag_stats = _build_redbag_effect_details_by_id(root, item_rows)

    cards = [
        _compact_item_row(
            row,
            quality_by_id,
            progression_counts_by_gid,
            time_hints_by_id,
            optional_gift_rewards_by_group,
            talisman_details_by_id,
            spiritual_body_details_by_id,
            title_details_by_id,
            fashion_details_by_id,
            gongfa_details_by_id,
            gongfa_jie_book_details_by_id,
            gongfa_feature_probe_book_details_by_id,
            special_gongfa_jie_details_by_item_id,
            physical_exercise_details_by_id,
            partner_details_by_id,
            npc_gift_details_by_item_id,
            hidden_world_details_by_item_id,
            pet_gift_details_by_id,
            member_details_by_id,
            member_equipment_details_by_group_id,
            take_medicine_details_by_item_id,
            medical_recipe_details_by_id,
            medical_recipe_details_by_formula_item_id,
            wallet_resource_details_by_id,
            boss_kill_effect_details_by_id,
            faze_details_by_id,
            spiritware_details_by_item_id,
            swordsoul_details_by_item_id,
            swordsoul_line_details_by_item_id,
            sword_base_details_by_item_id,
            flame_square_details_by_item_id,
            equipment_details_by_item_id,
            coreware_details_by_item_id,
            partner_weapon_stone_details_by_item_id,
            redbag_details_by_id,
            title_details_by_item_id,
        )
        for row in sorted(item_rows, key=_item_sort_key)
    ]
    _annotate_item_icon_reuse(cards)
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
        "item_with_effect_detail_count": sum(1 for card in cards if card.get("effect_details")),
        "primary_icon_reuse_risk_count": sum(1 for card in cards if card.get("icon_quality_risk")),
        "small_icon_reuse_risk_count": sum(1 for card in cards if card.get("small_icon_quality_risk")),
        "item_with_optional_gift_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "optional_gift_rewards" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_talisman_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "talisman" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_spiritual_body_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "spiritual_body" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_title_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "title" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_title_local_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "title_item_local" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_fashion_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "fashion" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_gongfa_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "gongfa_book" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_gongfa_jie_book_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "gongfa_jie_book" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_gongfa_feature_probe_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "gongfa_feature_probe_book" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_gongfa_local_description_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "gongfa_local_description" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_special_gongfa_jie_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "special_gongfa_jie_item" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_physical_exercise_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "physical_exercise" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_partner_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "partner" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_npc_gift_detail_count": sum(
            1
            for card in cards
            if any(
                detail.get("kind") in {"npc_gift_activity", "partner_gift_targets"}
                for detail in card.get("effect_details") or []
                if isinstance(detail, dict)
            )
        ),
        "item_with_hidden_world_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "hidden_world_item" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_pet_gift_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "pet_gift" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_member_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "member" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_member_equipment_detail_count": sum(
            1
            for card in cards
            if any(
                detail.get("kind") in {"member_equipment", "member_equipment_item"}
                for detail in card.get("effect_details") or []
                if isinstance(detail, dict)
            )
        ),
        "item_with_show_effect_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "item_show_effect" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_take_medicine_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "take_medicine" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_medical_recipe_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "medical_recipe" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_wallet_resource_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "wallet_resource" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_boss_kill_effect_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "boss_kill_effect" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_prefixed_effect_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "prefixed_item_effect" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_faze_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "faze" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_spiritware_detail_count": sum(
            1
            for card in cards
            if any(
                str(detail.get("kind") or "").startswith("spiritware_")
                for detail in card.get("effect_details") or []
                if isinstance(detail, dict)
            )
        ),
        "item_with_spiritware_part_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "spiritware_part" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_spiritware_soul_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "spiritware_soul" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_spiritware_cleanse_detail_count": sum(
            1
            for card in cards
            if any(
                detail.get("kind") in {"spiritware_cleanse_item", "spiritware_cleanse_material"}
                for detail in card.get("effect_details") or []
                if isinstance(detail, dict)
            )
        ),
        "item_with_spiritware_ultra_material_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "spiritware_ultra_material" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_talisman_refine_material_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "talisman_refine_material" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_swordsoul_detail_count": sum(
            1
            for card in cards
            if any(
                detail.get("kind") in {"swordsoul_awakening_material", "swordsoul_line", "swordsoul_line_wash_item"}
                for detail in card.get("effect_details") or []
                if isinstance(detail, dict)
            )
        ),
        "item_with_swordsoul_line_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "swordsoul_line" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_swordsoul_line_wash_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "swordsoul_line_wash_item" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_sword_base_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "sword_base_activation" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_flame_square_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "flame_square_flame" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_equipment_detail_count": sum(
            1
            for card in cards
            if any(
                detail.get("kind") in {"equipment_item", "equipment_gem"}
                for detail in card.get("effect_details") or []
                if isinstance(detail, dict)
            )
        ),
        "item_with_equipment_material_effect_detail_count": sum(
            1
            for card in cards
            if any(
                detail.get("kind") == "equipment_material_effect"
                for detail in card.get("effect_details") or []
                if isinstance(detail, dict)
            )
        ),
        "item_with_coreware_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "coreware_item" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_partner_weapon_stone_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "partner_weapon_stone" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        "item_with_redbag_detail_count": sum(
            1
            for card in cards
            if any(detail.get("kind") == "redbag" for detail in card.get("effect_details") or [] if isinstance(detail, dict))
        ),
        **optional_gift_stats,
        **talisman_stats,
        **spiritual_body_stats,
        **title_stats,
        **fashion_stats,
        **gongfa_stats,
        **gongfa_jie_book_stats,
        **gongfa_feature_probe_stats,
        **special_gongfa_jie_stats,
        **physical_exercise_stats,
        **partner_stats,
        **npc_gift_stats,
        **hidden_world_stats,
        **pet_gift_stats,
        **member_stats,
        **member_equipment_stats,
        **take_medicine_stats,
        **medical_stats,
        **wallet_resource_stats,
        **boss_kill_effect_stats,
        **faze_stats,
        **spiritware_stats,
        **swordsoul_stats,
        **swordsoul_line_stats,
        **sword_base_stats,
        **flame_square_stats,
        **equipment_stats,
        **coreware_stats,
        **partner_weapon_stone_stats,
        **redbag_stats,
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
                    "talisman": talisman_stats.get("talisman_source") or "",
                    "talisman_grade": talisman_stats.get("talisman_grade_source") or "",
                    "spiritual_body": spiritual_body_stats.get("spiritual_body_source") or "",
                    "spiritual_body_jie": spiritual_body_stats.get("spiritual_body_jie_source") or "",
                    "spiritual_body_quality": spiritual_body_stats.get("spiritual_body_quality_source") or "",
                    "title": title_stats.get("title_source") or "",
                    "attribute": title_stats.get("attribute_source") or "",
                    "fashion": fashion_stats.get("fashion_source") or "",
                    "fashion_level": fashion_stats.get("fashion_level_source") or "",
                    "fashion_type": fashion_stats.get("fashion_type_source") or "",
                    "gongfa": gongfa_stats.get("gongfa_source") or "",
                    "gongfa_pin": gongfa_stats.get("gongfa_pin_source") or "",
                    "gongfa_career": gongfa_stats.get("gongfa_career_source") or "",
                    "gongfa_jie_book": gongfa_jie_book_stats.get("gongfa_jie_book_source") or "",
                    "gongfa_jie_book_skill": gongfa_jie_book_stats.get("gongfa_jie_book_skill_source") or "",
                    "gongfa_feature_probe_family": gongfa_feature_probe_stats.get("gongfa_feature_probe_family_source") or "",
                    "gongfa_feature_probe_link": gongfa_feature_probe_stats.get("gongfa_feature_probe_link_source") or "",
                    "special_gongfa_jie": special_gongfa_jie_stats.get("special_gongfa_jie_source") or "",
                    "special_gongfa_skill": special_gongfa_jie_stats.get("special_gongfa_skill_source") or "",
                    "physical_exercise": physical_exercise_stats.get("physical_exercise_source") or "",
                    "physical_jie": physical_exercise_stats.get("physical_jie_source") or "",
                    "physical_comprehension": physical_exercise_stats.get("physical_comprehension_source") or "",
                    "partner": partner_stats.get("partner_source") or "",
                    "partner_quality": partner_stats.get("partner_quality_source") or "",
                    "partner_grade": partner_stats.get("partner_grade_source") or "",
                    "partner_show_skill": partner_stats.get("partner_show_skill_source") or "",
                    "partner_skill_level": partner_stats.get("partner_skill_level_source") or "",
                    "partner_active_skill": partner_stats.get("partner_active_skill_source") or "",
                    "partner_arane": partner_stats.get("partner_arane_source") or "",
                    "npc_gift": npc_gift_stats.get("npc_gift_source") or "",
                    "npc": npc_gift_stats.get("npc_source") or "",
                    "hidden_world_item": hidden_world_stats.get("hidden_world_item_source") or "",
                    "hidden_world_skill": hidden_world_stats.get("hidden_world_skill_source") or "",
                    "hidden_world_camp": hidden_world_stats.get("hidden_world_camp_source") or "",
                    "hidden_world_career": hidden_world_stats.get("hidden_world_career_source") or "",
                    "pet_gift": pet_gift_stats.get("pet_gift_source") or "",
                    "member": member_stats.get("member_source") or "",
                    "member_star": member_stats.get("member_star_source") or "",
                    "member_equipment": member_equipment_stats.get("member_equipment_source") or "",
                    "member_equipment_skill": member_equipment_stats.get("member_equipment_skill_source") or "",
                    "take_medicine": take_medicine_stats.get("take_medicine_source") or "",
                    "take_medicine_type": take_medicine_stats.get("take_medicine_type_source") or "",
                    "medical": medical_stats.get("medical_source") or "",
                    "medical_type": medical_stats.get("medical_type_source") or "",
                    "medical_effect": medical_stats.get("medical_effect_source") or "",
                    "medical_effect_index": medical_stats.get("medical_effect_index_source") or "",
                    "wallet_resource": wallet_resource_stats.get("wallet_resource_source") or "",
                    "boss_kill_effect": boss_kill_effect_stats.get("boss_kill_effect_source") or "",
                    "boss_kill_effect_attribute": boss_kill_effect_stats.get("boss_kill_effect_attribute_source") or "",
                    "faze": faze_stats.get("faze_source") or "",
                    "spiritware": spiritware_stats.get("spiritware_source") or "",
                    "spiritware_item": spiritware_stats.get("spiritware_item_source") or "",
                    "spiritware_base": spiritware_stats.get("spiritware_base_source") or "",
                    "spiritware_ultra": spiritware_stats.get("spiritware_ultra_source") or "",
                    "spiritware_soul": spiritware_stats.get("spiritware_soul_source") or "",
                    "spiritware_cleanse_item": spiritware_stats.get("spiritware_cleanse_item_source") or "",
                    "spiritware_attribute": spiritware_stats.get("spiritware_attribute_source") or "",
                    "swordsoul_base": swordsoul_stats.get("swordsoul_base_source") or "",
                    "swordsoul_awakening": swordsoul_stats.get("swordsoul_awakening_source") or "",
                    "swordsoul_lines": swordsoul_stats.get("swordsoul_lines_source") or "",
                    "swordsoul_line_base": swordsoul_line_stats.get("swordsoul_line_base_source") or "",
                    "swordsoul_line_level": swordsoul_line_stats.get("swordsoul_line_level_source") or "",
                    "swordsoul_line_attr": swordsoul_line_stats.get("swordsoul_line_attr_source") or "",
                    "swordsoul_line_attr_quality": swordsoul_line_stats.get("swordsoul_line_attr_quality_source") or "",
                    "swordsoul_eff": swordsoul_line_stats.get("swordsoul_eff_source") or "",
                    "swordsoul_line_wash": swordsoul_line_stats.get("swordsoul_line_wash_source") or "",
                    "sword_base": sword_base_stats.get("sword_base_source") or "",
                    "sword_level_up": sword_base_stats.get("sword_level_up_source") or "",
                    "sword_key_point": sword_base_stats.get("sword_key_point_source") or "",
                    "flame_level": flame_square_stats.get("flame_level_source") or "",
                    "flame_square_build": flame_square_stats.get("flame_square_build_source") or "",
                    "flame_square_level": flame_square_stats.get("flame_square_level_source") or "",
                    "flame_attribute": flame_square_stats.get("flame_attribute_source") or "",
                    "equipment": equipment_stats.get("equipment_source") or "",
                    "equipment_item": equipment_stats.get("equipment_item_source") or "",
                    "equipment_tag": equipment_stats.get("equipment_tag_source") or "",
                    "equipment_gem": equipment_stats.get("equipment_gem_source") or "",
                    "equipment_gem_suit": equipment_stats.get("equipment_gem_suit_source") or "",
                    "equipment_attribute": equipment_stats.get("equipment_attribute_source") or "",
                    "core_base": coreware_stats.get("core_base_source") or "",
                    "core_map": coreware_stats.get("core_map_source") or "",
                    "coreware_base": coreware_stats.get("coreware_base_source") or "",
                    "coreware_level": coreware_stats.get("coreware_level_source") or "",
                    "coreware_attribute": coreware_stats.get("coreware_attribute_source") or "",
                    "partner_weapon_stone_base": partner_weapon_stone_stats.get("partner_weapon_stone_base_source") or "",
                    "partner_weapon_stone_level": partner_weapon_stone_stats.get("partner_weapon_stone_level_source") or "",
                    "partner_weapon_stone_upgrade": partner_weapon_stone_stats.get("partner_weapon_stone_upgrade_source") or "",
                    "partner_weapon_base": partner_weapon_stone_stats.get("partner_weapon_base_source") or "",
                    "partner_weapon_stone_combination": partner_weapon_stone_stats.get("partner_weapon_stone_combination_source") or "",
                    "partner_weapon_attribute": partner_weapon_stone_stats.get("partner_weapon_attribute_source") or "",
                    "redbag": redbag_stats.get("redbag_source") or "",
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
