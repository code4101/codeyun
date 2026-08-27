from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.catalog.doupotd import (
    _collect_reward_result_resolution_flow,
    _extra_mark_label,
    _find_lua_asset_by_name,
    _load_item_corner_rows,
    _parse_reward_type_lua,
    _reward_type_from_token,
)
from backend.core.fanxiu.catalog.lua_config import load_fanxiu_lang_map, parse_fanxiu_generated_lua_config
from backend.core.fanxiu.catalog.resources import FanxiuResourceError, resolve_fanxiu_export_root
from backend.core.fanxiu.catalog.wiki import strip_fanxiu_rich_text


DIGITDOOR_CATALOG_SCHEMA_VERSION = 2
DEFAULT_CATALOG = Path("parsed_configs/digitdoor_catalog/digitdoor_catalog.json")
DEFAULT_DIGITDOOR_DIR_PATTERN = "by_source/lscripts/generate/cfg/digitdoor_*/text_assets"
DEFAULT_DIGITDOOR_LOGIC_DIR_PATTERN = "by_source/lscripts/gamesystem/game/digitdoor_*/text_assets"
DEFAULT_LANG_PATTERN = "by_source/lscripts/generate/localization/chinese/lang_*/text_assets/lang.lua"
DIGITDOOR_EFFECT_TYPE_REQUIRE = "GameSystem.Game.DigitDoor.Core.Fight.SkillEffect.Const.DigitDoorEffectType"

QUALITY_LABELS = {
    1: "白",
    2: "绿",
    3: "蓝",
    4: "紫",
    5: "橙",
    6: "红",
    7: "金",
}

ENHANCE_TYPE_LABELS = {
    1: "招募/基础",
    2: "技能强化",
}

DOOR_TYPE_LABELS = {
    1: "增益门",
    2: "资源门",
}

DOOR_REFRESH_SIDE_LABELS = {
    1: "左侧",
    2: "右侧",
}

MONSTER_SKILL_TYPE_LABELS = {
    1: "NormalAttack",
    2: "OtherSkill",
}

SKILL_RELEASE_TYPE_LABELS = {
    0: "Common",
    1: "MoveDistance",
    2: "HpPercent",
    3: "Death",
    4: "DistanceBetween",
    5: "DirectDoor",
}

SKILL_ENHANCE_EFFECT_FIELD_NOTES = {
    "buff_id": "进入 `DigitDoorBaseSkill:AddBuffData`，再按 `BuffEffect.type/targetType/passive` 决定是立即给自身加被动、增强技能数值，还是挂到技能命中/结束阶段。",
    "mutex_timeline": "进入 `DigitDoorBaseSkill:UpdateStrengthEffect`，大于 0 时清空原 `effectCfgDic` 并替换技能时间线效果类。",
    "ext_release_count": "进入 `DigitDoorSkillData/RoleBallData` 并由 `DigitDoorSkillActor:ReleaseSkill` 传给 `BaseSkill:Start`，释放结束后可递减重放。",
    "ext_penetrate": "进入 `DigitDoorSkillData:ModifyData`，累加到默认穿透数量。",
    "ext_hit_num": "进入 Focus/GushFire/PulseLaser 等技能数据类，并在对应 Effect 中增加命中/触发次数。",
    "ext_atk_distance": "进入 Parabola/Slash/GushFire/Scatter 等技能数据类，扩展攻击距离或判定范围。",
    "ext_cd": "由 `DigitDoorFightComponent:UpdateSkillCD` 读取，按技能组调整冷却。",
}

SKILL_ENHANCE_EFFECT_TOPIC_TERMS = {
    "door_skill_to_effect": ("SkillRefreshEffect", "SkillEnhanceEffect"),
    "role_effect_counter": ("UpdateRoleSkillAttrList", "GetRoleAllSkillEnhanceEffectList", "V_RoleSkillEnhanceEffList"),
    "battle_apply": ("UpdateDigitDoorSkillInBattle", "UpdateSkillEffect", "SkillActor:UpdateSkill"),
    "base_apply": ("UpdateStrengthEffect", "AddBuffData", "ModifyData"),
    "buff_id": ("buffId", "AddBuffData", "BuffEffect"),
    "mutex_timeline": ("mutexTimeline", "AddSkillEffectClassPath", "effectCfgDic"),
    "ext_release_count": ("extReleaseCount", "GetExtReleaseCount", "SetExtReleaseCount"),
    "ext_penetrate": ("extPenetrate", "SetDefaultPenetrateNum", "GetDefaultPenetrateNum"),
    "ext_hit_num": ("extHitNum", "GetExtHitNum", "SetExtHitNum"),
    "ext_atk_distance": ("extAtkDistance", "GetExtAtkDistance", "SetExtAtkDistance"),
    "ext_cd": ("extCd", "UpdateSkillCD", "skillGroup"),
}

SKILL_ENHANCE_APPLICATION_TOPIC_TERMS = {
    "ready_fight_skill_list": ("SM_DigitDoorReadyFight", "DigitDoorReadyFright", "skillList"),
    "server_level_update": ("CM_DigitDoorUpLevelFun", "SM_DigitDoorUpLevelFun", "UpdateDDPartnerVos"),
    "council_skill_cache": ("SetCouncilSkill2lvMap", "GetCouncilSkillList", "V_CouncilSkillList"),
    "skill_enhance_display_lookup": ("ConfigName.DigitDoor_SkillEnhance", "GetSkillName", "GetSkillNameList"),
    "council_skill_info_lookup": ("GetCouncilSkillById", "GetCouncilSkillCfgById", "DigitDoor_CharacterSkillInfo"),
    "skill_enhance_effect_apply": ("DigitDoor_SkillEnhanceEffect", "UpdateSkillEffect", "SkillActor:UpdateSkill", "UpdateStrengthEffect"),
    "door_refresh_effect_apply": ("SkillRefreshEffect", "UpdateRoleSkillAttrList", "UpdateDigitDoorSkillInBattle"),
    "skill_enhance_effect_id_direct_read": ("skillCfg.effectId", ".effectId"),
}

BUFF_EFFECT_FIELD_NOTES = {
    "type": "映射到 `DigitDoorType.SkillBuffType`，再通过 `DigitDoorType.BuffPath` 选择具体 Buff 类。",
    "target_type": "`DigitDoorFightComponent:GetBuffTargetList`、AddBuff 类和部分效果类按目标类型选择敌方/友方/自身。",
    "trigger_type": "`DigitDoorBuffBase:AnalysisTriggerType` 解析 ADD/HITTARGET/CALDAMAGE/DEAD 等触发时机，也可带技能组过滤。",
    "duration": "`DigitDoorBuffBase:Update` 以秒级生命周期移除非被动 Buff，负数一般表示不按普通时长结束。",
    "interval": "持续类 Buff 的周期字段，具体由对应 Buff 类决定是否消费。",
    "eff_type": "`DigitDoorPartnerView:AddBuff` 按 Add/Refresh 决定同 id Buff 叠层或刷新。",
    "plies_limit": "`DigitDoorBuffBase:InitData` 写入层数上限，未配置时默认 999。",
    "damage": "Burning/Shocking/Injure/DeadBoom 等伤害类 Buff 的数值源。",
    "add_attr": "`DigitDoorBuffAddAttr` 与属性汇总逻辑消费，常见格式如 `REDUCEDAMAGE:5000`。",
    "shield": "护盾类 Buff 的护盾比例/数值源，PartnerView 会按 MaxHP 汇总护盾。",
    "slow_down": "SlowDown/控制类 Buff 的减速比例源。",
    "buff_amplify": "`EffectStrength` / `SkillDamageStrength` 两类特殊 Buff 用于增强 buff 效果或技能伤害。",
    "timeline_id": "`DigitDoorBuffBase:Start` 播放 Buff 表现 timeline。",
    "passive": "`BaseSkill:AddBuffData` 对 self passive Buff 会立即加到施法者身上，BuffBase 更新也跳过普通生命周期。",
}

BUFF_EFFECT_TOPIC_TERMS = {
    "buff_config_lookup": ("DigitDoor_BuffEffect", "BuffEffect", "GetConfigTableByIdWithLog"),
    "buff_type_to_class": ("SkillBuffType", "BuffPath", "require(buffPath)"),
    "add_buff_application": ("AddBuff(", "AddBuffData", "buffList"),
    "layer_or_refresh": ("effType", "BuffLayerType", "AddBuffLayer", "OnRefreshBuff"),
    "buff_base_init": ("InitData(buffCfg", "V_ConfigId", "AnalysisTriggerType"),
    "trigger_type": ("triggerType", "BuffTriggerType", "TriggerSkillDic"),
    "target_type": ("targetType", "BuffTargetType", "GetBuffTargetList"),
    "duration_timeline": ("duration", "timelineId", "PlayElement"),
    "passive_branch": ("passive", "isPassive", "BuffTargetType.Self"),
    "amplify": ("buffAmplify", "SetBuffStrength", "SetDamageStrength"),
    "add_attr": ("addAttr", "BuffAddAttrType", "GetBuffListByType(DigitDoorType.SkillBuffType.AddAttr)"),
    "shield": ("shield", "SkillBuffType.Shield", "GetShieldRatio"),
    "slow_down": ("slowDown", "SlowDown", "GetSlowDown"),
    "damage": ("damage", "SkillBuffType.Injure", "AddDamageResult"),
}

BUFF_CLASS_FORMULA_TOPIC_TERMS = {
    "buff_data_init": ("function _M.InitData", "strengthVal", "cfg.addAttr", "SetShieldRatio", "SetDamage"),
    "add_attr_aggregation": ("GetAddExtBattleAttr", "BuffAddAttrType", "extAttack", "extSkillDamage"),
    "shield_formula": ("GetShieldRatio", "shieldValue", "maxHp*shieldRatio*0.0001"),
    "duration_and_layer": ("GetDuration", "AddBuffLayer", "OnRefreshBuff", "V_LayerLimit"),
    "burning_interval_damage": ("hitTimer", "GetHitInterval", "DoHit"),
    "slow_down_apply": ("GetSlowDown", "SetExtMoveSpeed", "OnSpeedChanged"),
    "reduce_cd": ("GetDecreaseCd", "GetSkillCDLeft", "RefreshCDTime"),
    "hit_add_buff": ("TriggerSkillDic", "BuffIdCheckList", "TriggerPercentBuff", "triggerBuffIds"),
    "be_hit_damage": ("CheckReboundDamage", "AddDamageResult"),
    "hp_judge": ("GetHpJudge", "currentHp<maxHp*hpJudge", "TriggerPercentBuff"),
}

MONSTER_REFRESH_TOPIC_TERMS = {
    "refresh_config_index": ("DigitDoor_MonsterRefreshPoint", "GetRefreshPointCfg", "refreshPointDict"),
    "monster_group_lookup": ("DigitDoor_MonsterGroup", "monsterdata", "cfg.monsterId"),
    "monster_info_lookup": ("DigitDoor_MonsterInfo", "monsterBaseCfg", "GetMonsterInfo"),
    "monster_skill_lookup": ("DigitDoor_MonsterSkill", "defaultSkill", "MonsterSkill"),
    "wave_progress": ("refreshWave", "CM_DigitDoorRefreshWaveFun", "GetCurTotalWave"),
    "spawn_monster": ("RefreshMonster", "refreshTotalNum", "refreshNum", "InitData(cfg"),
    "boss_summary": ("curWaveBossInfoDict", "bossVoList", "DDBossVo"),
}

DOOR_REFRESH_TOPIC_TERMS = {
    "door_config_index": ("DigitDoor_DoorRefreshPoint", "GetDoorRefreshPointCfg", "doorRefreshPointDict"),
    "pre_create_request": ("UpdatePreCreateDoor", "startRefreshTime", "sendDoorList"),
    "server_create_response": ("CreateDoor", "doorVOS", "resourceId"),
    "effect_lookup": ("DigitDoor_SkillRefreshEffect", "customizedType", "SkillRefreshEffect"),
    "door_position": ("GenerateDoorPosByType", "side", "refreshOffsetDis"),
    "door_entity_data": ("doorDamage", "SetDoorDamage", "cfg.hp", "cfg.attack", "cfg.volume"),
}

DOOR_GAIN_BUFF_TOPIC_TERMS = {
    "collision_gate": ("CheckMutualExclusion", "CheckPartnerCollision", "StartCheckTouch"),
    "collision_record": ("RecordCollisionDoor", "isTouchDead"),
    "local_counter": ("UpdateRoleSkillAttrList", "V_RoleSkillEnhanceEffList"),
    "battle_apply": ("UpdateDigitDoorSkillInBattle", "UpdateSkillEffect", "DigitDoor_SkillRefreshEffect"),
    "claim_send": ("CM_DigitDoorGainBuffFun", "buffList", "F_SendMsg"),
    "claim_ack": ("SM_DigitDoorGainBuffFun", "msg.code"),
}

DOOR_CUSTOMIZED_TYPE_TOPIC_TERMS = {
    "negative_visual": ("customizedType", "DoorBufferType.Negative"),
    "refresh_pool": ("DoorRefreshPoint", "customizedType", "GetDoorRefreshPointCfg"),
    "effect_pool": ("SkillRefreshEffect", "customizedType", "GetBuffCfgData"),
    "special_pool": ("spxDoorType", "rateList", "probability"),
    "debuff_pool": ("debuffDoorType", "DoorBufferType.Negative"),
}

DOOR_BROKEN_TYPE_LABELS = {
    1: "可击破门",
    2: "碰触门",
}

DOOR_BUFFER_TYPE_LABELS = {
    1: "低级门池",
    2: "中级门池",
    3: "高级门池",
    4: "负面门池",
}

DIGITDOOR_BUFF_ATTR_LABELS = {
    "ATTACK": "攻击",
    "ATKSPEED": "攻速",
    "CRIT": "暴击",
    "CRITDAMAGE": "暴伤",
    "ANTICIRT": "抗暴",
    "INCDAMAGE": "增伤",
    "REDUCEDAMAGE": "减伤",
    "MAXHP": "生命",
    "ADDDAMAGE": "附伤",
    "SKILL_DAMAGE": "技能伤害",
}

BUFF_CLASS_FORMULA_ROWS = [
    {
        "field": "addAttr",
        "runtime_slot": "DigitDoorBuffData.extAttr[key]",
        "consumer": "DigitDoorBuffData:InitData / DigitDoorPartnerView attribute aggregation",
        "formula": "value = cfg.addAttr.value * (1 + strengthVal * 0.0001); aggregate = sum(value * layer); percent display = aggregate * 0.01",
        "meaning": "属性百分比加成/减免，支持 ATTACK、ATKSPEED、CRIT、CRITDAMAGE、ANTICIRT、INCDAMAGE、REDUCEDAMAGE、MAXHP、ADDDAMAGE、SKILL_DAMAGE。",
        "topics": "buff_data_init,add_attr_aggregation",
    },
    {
        "field": "shield",
        "runtime_slot": "DigitDoorBuffData.shieldRatio",
        "consumer": "DigitDoorPartnerView shield aggregation",
        "formula": "shieldRatio = cfg.shield * (1 + strengthVal * 0.0001); shieldValue = maxHp * sum(shieldRatio) * 0.0001",
        "meaning": "护盾比例，按目标最大生命换算成当前护盾值。",
        "topics": "buff_data_init,shield_formula",
    },
    {
        "field": "damage",
        "runtime_slot": "DigitDoorBuffData.damage",
        "consumer": "DigitDoorBuffBase:DoHit / DigitDoorBuffBeHit:CheckReboundDamage / damage buff classes",
        "formula": "damage = cfg.damage * (1 + strengthVal * 0.0001); later passed to AddDamageResult through the buff instance",
        "meaning": "灼烧、反伤、易伤触发等伤害型 Buff 的基础数值源。",
        "topics": "buff_data_init,burning_interval_damage,be_hit_damage",
    },
    {
        "field": "duration",
        "runtime_slot": "DigitDoorBuffData.duration",
        "consumer": "DigitDoorBuffBase:Update",
        "formula": "duration = cfg.duration >= 0 ? cfg.duration * 0.001 : cfg.duration; lifeTime += extTime * 0.001 when present",
        "meaning": "持续时间，正数以毫秒配置并转秒，负数保留为特殊长驻/非普通时长。",
        "topics": "buff_data_init,duration_and_layer",
    },
    {
        "field": "interval",
        "runtime_slot": "DigitDoorBuffData.hitInterval",
        "consumer": "DigitDoorBuffBurning:Update",
        "formula": "hitInterval = cfg.interval * 0.001; hitTimer > hitInterval then DoHit()",
        "meaning": "周期伤害间隔，当前可见典型消费者是 Burning。",
        "topics": "buff_data_init,burning_interval_damage",
    },
    {
        "field": "slowDown",
        "runtime_slot": "DigitDoorBuffData.slowDown",
        "consumer": "DigitDoorBuffSlowDown:Start/RemoveSelf",
        "formula": "Start applies SetExtMoveSpeed(slowDown); RemoveSelf applies SetExtMoveSpeed(-slowDown)",
        "meaning": "移动速度修正，配置可为负值；移除时反向抵消。",
        "topics": "buff_data_init,slow_down_apply",
    },
    {
        "field": "decreaseCd",
        "runtime_slot": "DigitDoorBuffData.decreaseCd",
        "consumer": "DigitDoorBuffReduceCD:CheckReduceCD",
        "formula": "cdLeftMs = max(GetSkillCDLeft(ultraId) * 1000 - decreaseCd, 0); RefreshCDTime(ultraId, cdLeftMs)",
        "meaning": "立即减少大招/绝技剩余冷却。",
        "topics": "buff_data_init,reduce_cd",
    },
    {
        "field": "triggerPercent + triggerBuffId + targetBuffCheck",
        "runtime_slot": "DigitDoorBuffData.triggerPercent/triggerBuffIds + BuffBase.BuffIdCheckList",
        "consumer": "DigitDoorBuffBase:TriggerPercentBuff / DigitDoorBuffHitAddBuff",
        "formula": "roll 1..10000; trigger when roll <= triggerPercent; then add triggerBuffId list, optionally after skill-group and target-buff filters",
        "meaning": "命中/受击/血量等条件触发额外 Buff 的概率门。",
        "topics": "hit_add_buff,hp_judge",
    },
    {
        "field": "hpJudge",
        "runtime_slot": "DigitDoorBuffData.hpJudge",
        "consumer": "DigitDoorBuffHpJudge:CheckHp",
        "formula": "hpJudge = cfg.hpJudge * 0.0001; trigger when currentHp < maxHp * hpJudge",
        "meaning": "低血量阈值触发器。",
        "topics": "buff_data_init,hp_judge",
    },
    {
        "field": "effType + pliesLimit",
        "runtime_slot": "DigitDoorBuffBase layer state",
        "consumer": "DigitDoorPartnerView:AddBuff / DigitDoorBuffBase:AddBuffLayer/OnRefreshBuff",
        "formula": "effType Add increments layer until pliesLimit; effType Refresh resets StartTime",
        "meaning": "同 id Buff 重复获得时的叠层或刷新规则。",
        "topics": "duration_and_layer",
    },
]

BUFF_CLASS_CANONICAL_SOURCES = [
    {
        "source": "DigitDoorBuffData.lua",
        "role": "BuffEffect numeric conversion",
        "summary": "Converts cfg.duration/damage/shield/interval/slowDown/decreaseCd/addAttr/hpJudge into runtime slots; applies strengthVal to damage/shield/addAttr.",
    },
    {
        "source": "DigitDoorPartnerView.lua",
        "role": "Buff instance and aggregate stats",
        "summary": "Instantiates Buff classes, handles layer/refresh, stores BuffDic, aggregates AddAttr, Shield, and Injure values for display/runtime summaries.",
    },
    {
        "source": "DigitDoorBuffBase.lua",
        "role": "Common lifecycle and trigger",
        "summary": "Owns duration expiry, timeline playback, layer add, refresh, DoHit, and TriggerPercentBuff probability gate.",
    },
    {
        "source": "DigitDoorBuffBurning.lua",
        "role": "Periodic damage",
        "summary": "Uses hitInterval timer and calls DoHit periodically.",
    },
    {
        "source": "DigitDoorBuffSlowDown.lua",
        "role": "Move speed modifier",
        "summary": "Applies slowDown to bot move speed on start and applies the inverse value on removal.",
    },
    {
        "source": "DigitDoorBuffReduceCD.lua",
        "role": "Cooldown reduction",
        "summary": "Reads decreaseCd, subtracts it from ultra skill CD left in milliseconds, and refreshes the CD timer.",
    },
    {
        "source": "DigitDoorBuffHitAddBuff.lua",
        "role": "Hit-triggered extra buff",
        "summary": "Filters by skill group/type and optional target buff ids, then rolls triggerPercent and adds triggerBuffIds.",
    },
    {
        "source": "DigitDoorBuffHpJudge.lua",
        "role": "HP threshold trigger",
        "summary": "Converts hpJudge to a max-hp ratio and triggers extra buffs when current HP is below the threshold.",
    },
    {
        "source": "DigitDoorBuffBeHit.lua",
        "role": "Be-hit reaction damage",
        "summary": "Triggers extra buffs and can send rebound damage through AddDamageResult.",
    },
]

CONDITION_OP_LABELS = {
    "PR": "前置强化",
    "TCLV": "角色等级区间",
    "MU": "互斥/替换",
}

_WHITESPACE_RE = re.compile(r"\s+")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FanxiuResourceError(f"数字门图鉴格式不正确：{path}")
    return data


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _md_table_cell(value: Any) -> str:
    return str(value or "").replace("|", "<br>").replace("\n", " ")


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sort_value(value: Any, fallback: int = 10**12) -> int:
    parsed = _as_int(value)
    return parsed if parsed is not None else fallback


def _plain(value: Any) -> str:
    return strip_fanxiu_rich_text(str(value or "")).strip()


def _preview(value: Any, limit: int = 180) -> str:
    text = _WHITESPACE_RE.sub(" ", _plain(value))
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _normalize_search_text(value: Any) -> str:
    return str(value or "").lower()


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _parse_int_csv(value: Any) -> list[int]:
    result: list[int] = []
    for part in re.split(r"[,|]", str(value or "")):
        parsed = _as_int(part.strip())
        if parsed is not None:
            result.append(parsed)
    return result


def _resolve_export_dir(path: str | Path | None, *, export_root: str | Path | None = None) -> Path | None:
    if path is None:
        return None
    root = resolve_fanxiu_export_root(export_root)
    raw_path = Path(path)
    resolved = raw_path.expanduser().resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    if not _is_relative_to(resolved, root):
        raise FanxiuResourceError(f"目录必须位于导出根目录内：{root}")
    if not resolved.is_dir():
        raise FanxiuResourceError(f"目录不存在：{resolved}")
    return resolved


def _find_default_config_dir(root: Path) -> Path:
    candidates = [path for path in root.glob(DEFAULT_DIGITDOOR_DIR_PATTERN) if path.is_dir()]
    if not candidates:
        raise FanxiuResourceError(f"未找到 DigitDoor 配置目录：{DEFAULT_DIGITDOOR_DIR_PATTERN}")
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_default_logic_dir(root: Path) -> Path:
    candidates = [path for path in root.glob(DEFAULT_DIGITDOOR_LOGIC_DIR_PATTERN) if path.is_dir()]
    if not candidates:
        raise FanxiuResourceError(f"未找到 DigitDoor Lua 逻辑目录：{DEFAULT_DIGITDOOR_LOGIC_DIR_PATTERN}")
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_default_lang_path(root: Path) -> Path | None:
    candidates = [path for path in root.glob(DEFAULT_LANG_PATTERN) if path.is_file()]
    if not candidates:
        candidates = [path for path in root.glob("by_source/**/text_assets/lang.lua") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.stat().st_size, item.stat().st_mtime_ns))


def _resolve_lang_path(root: Path, lang_path: str | Path | None) -> Path | None:
    resolved_lang_path = Path(lang_path).expanduser().resolve() if lang_path else _find_default_lang_path(root)
    if resolved_lang_path and not _is_relative_to(resolved_lang_path, root):
        raise FanxiuResourceError(f"语言文件必须位于导出根目录内：{root}")
    if resolved_lang_path and not resolved_lang_path.is_file():
        raise FanxiuResourceError(f"语言文件不存在：{resolved_lang_path}")
    return resolved_lang_path


def _parse_config_rows(
    config_dir: Path,
    name: str,
    lang_path: Path | None,
    lang_map: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    path = config_dir / f"{name}.lua"
    if not path.is_file():
        return []
    return list(parse_fanxiu_generated_lua_config(path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])


def _group_by_int(rows: list[dict[str, Any]], field: str) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        value = _as_int(row.get(field))
        if value is None:
            continue
        grouped.setdefault(value, []).append(row)
    return grouped


def _dedupe_preserve(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        key = str(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _load_items_by_id(root: Path) -> dict[str, dict[str, Any]]:
    try:
        runtime = load_fanxiu_item_runtime_index(export_root=root, rebuild_missing=False)
    except FanxiuResourceError:
        return {}
    return {str(key): value for key, value in (runtime.get("cards_by_id") or {}).items() if isinstance(value, dict)}


def _compact_item(card: dict[str, Any] | None, fallback_id: Any = None) -> dict[str, Any] | None:
    if not card:
        if fallback_id is None:
            return None
        return {"id": fallback_id, "name": str(fallback_id), "icon": "", "quality_name": ""}
    return {
        "id": card.get("id"),
        "name": card.get("name") or str(card.get("id") or ""),
        "icon": card.get("icon") or "",
        "small_icon": card.get("small_icon") or "",
        "quality_name": card.get("quality_name") or "",
        "description": card.get("description") or "",
    }


def _iter_reward_parts(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        raw_parts = value
    else:
        raw_parts = str(value or "").split(",")
    return [str(part).strip() for part in raw_parts if str(part).strip()]


def _compact_reward_items(value: Any, item_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rewards: list[dict[str, Any]] = []
    for text in _iter_reward_parts(value):
        pieces = text.split("|", 1)
        reward_type = pieces[0].strip()
        item_text = pieces[1] if len(pieces) > 1 else pieces[0]
        item_bits = item_text.split("_")
        item_id_text = item_bits[0].strip() if item_bits else ""
        count_text = item_bits[1].strip() if len(item_bits) > 1 else ""
        extra_mark_text = item_bits[2].strip() if len(item_bits) > 2 else ""
        count = _as_int(count_text) if count_text else None
        extra_mark = _as_int(extra_mark_text) if extra_mark_text else None
        item = _compact_item(item_by_id.get(item_id_text), item_id_text)
        item_name = item.get("name") if item else item_id_text
        parsed_item_id = _as_int(item_id_text)
        rewards.append(
            {
                "type": reward_type,
                "id": parsed_item_id if parsed_item_id is not None else item_id_text,
                "count": count,
                "extra_mark": extra_mark,
                "item": item,
                "raw": text,
                "text": f"{item_name}x{count}" if item_name and count is not None and count >= 0 else (item_name or text),
            }
        )
    return rewards


def _format_ratio(value: Any) -> str:
    parsed = _as_int(value)
    if parsed is None:
        return ""
    if parsed == 0:
        return "0"
    if abs(parsed) >= 1000:
        number = parsed / 100
        return f"{number:g}%"
    return str(parsed)


def _format_signed_percent_basis(value: Any) -> str:
    parsed = _as_int(value)
    if parsed is None:
        text = str(value or "").strip()
        return text
    if parsed == 0:
        return "0%"
    number = parsed / 100
    return f"{number:+g}%"


def _digitdoor_add_attr_hints(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    hints: list[str] = []
    for part in re.split(r"[,;|]", text):
        raw = part.strip()
        if not raw:
            continue
        field, sep, raw_value = raw.partition(":")
        if not sep:
            hints.append(raw)
            continue
        attr_key = field.strip()
        label = DIGITDOOR_BUFF_ATTR_LABELS.get(attr_key, attr_key)
        hints.append(f"{label} {_format_signed_percent_basis(raw_value.strip())}")
    return _dedupe_preserve(hints)


def _digitdoor_effect_show_percent_values(value: Any) -> list[int]:
    return [_as_int(match.group(1)) for match in re.finditer(r"([+-]?\d+)%", str(value or "")) if _as_int(match.group(1)) is not None]


def _digitdoor_effect_add_attr_values(effect: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for skill in effect.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        enhance_effect = skill.get("enhance_effect")
        if not isinstance(enhance_effect, dict):
            continue
        buff = enhance_effect.get("buff")
        if not isinstance(buff, dict):
            continue
        for part in re.split(r"[,;|]", str(buff.get("add_attr") or "")):
            raw = part.strip()
            if not raw or ":" not in raw:
                continue
            attr_key, raw_value = (item.strip() for item in raw.split(":", 1))
            parsed = _as_int(raw_value)
            if parsed is None or (attr_key, parsed) in seen:
                continue
            seen.add((attr_key, parsed))
            values.append(
                {
                    "attr_key": attr_key,
                    "attr_label": DIGITDOOR_BUFF_ATTR_LABELS.get(attr_key, attr_key),
                    "raw_value": parsed,
                    "percent_value": parsed / 100,
                    "percent_text": _format_signed_percent_basis(parsed),
                }
            )
    return values


def _parse_condition_expr(value: Any) -> list[dict[str, Any]]:
    text = str(value or "").strip()
    if not text:
        return []
    alternatives: list[dict[str, Any]] = []
    for alt_index, alt in enumerate(part.strip() for part in text.split(";") if part.strip()):
        clauses: list[dict[str, Any]] = []
        for raw_clause in (part.strip() for part in alt.split(",") if part.strip()):
            op, sep, payload = raw_clause.partition("|")
            args = [item for item in payload.split("_") if item != ""] if sep else []
            int_args = [_as_int(item) for item in args]
            clause: dict[str, Any] = {
                "raw": raw_clause,
                "op": op,
                "label": CONDITION_OP_LABELS.get(op, op),
                "args": args,
            }
            if op in {"PR", "MU"} and int_args and int_args[0] is not None:
                clause["enhance_id"] = int_args[0]
                if len(int_args) > 1 and int_args[1] is not None:
                    clause["count"] = int_args[1]
            elif op == "TCLV" and len(int_args) >= 3:
                clause["char_id"] = int_args[0]
                clause["min_level"] = int_args[1]
                clause["max_level"] = int_args[2]
            clauses.append(clause)
        alternatives.append({"index": alt_index, "raw": alt, "clauses": clauses})
    return alternatives


def _condition_ref_ids(conditions: list[dict[str, Any]], op: str) -> list[int]:
    ids: list[int] = []
    for alternative in conditions:
        for clause in alternative.get("clauses") or []:
            if clause.get("op") == op and isinstance(clause.get("enhance_id"), int):
                ids.append(clause["enhance_id"])
    return sorted(set(ids))


def _condition_level_ranges(conditions: list[dict[str, Any]]) -> list[dict[str, int]]:
    ranges: list[dict[str, int]] = []
    seen: set[tuple[int, int, int]] = set()
    for alternative in conditions:
        for clause in alternative.get("clauses") or []:
            if clause.get("op") != "TCLV":
                continue
            char_id = clause.get("char_id")
            min_level = clause.get("min_level")
            max_level = clause.get("max_level")
            if not all(isinstance(item, int) for item in (char_id, min_level, max_level)):
                continue
            key = (char_id, min_level, max_level)
            if key in seen:
                continue
            seen.add(key)
            ranges.append({"char_id": char_id, "min_level": min_level, "max_level": max_level})
    return ranges


def _compact_enhance_ref(row: dict[str, Any] | None, enhance_id: Any) -> dict[str, Any]:
    if not row:
        return {"id": enhance_id, "name": str(enhance_id), "description": ""}
    quality = _as_int(row.get("quality"))
    enhance_type = _as_int(row.get("type"))
    return {
        "id": row.get("id"),
        "name": row.get("name") or str(row.get("id") or enhance_id),
        "description": row.get("des") or "",
        "description_plain": _plain(row.get("des")),
        "char_id": row.get("charId"),
        "type": enhance_type,
        "type_label": ENHANCE_TYPE_LABELS.get(enhance_type or 0, str(enhance_type or "")),
        "quality": quality,
        "quality_label": QUALITY_LABELS.get(quality or 0, str(quality or "")),
    }


def _compact_enhance(row: dict[str, Any], enhance_by_id: dict[int, dict[str, Any]]) -> dict[str, Any]:
    enhance_id = _as_int(row.get("id")) or row.get("id")
    quality = _as_int(row.get("quality"))
    enhance_type = _as_int(row.get("type"))
    conditions = _parse_condition_expr(row.get("condition"))
    prereq_ids = _condition_ref_ids(conditions, "PR")
    mutex_ids = _condition_ref_ids(conditions, "MU")
    unlock_show_ids = _parse_int_csv(row.get("unlockShow"))
    return {
        "id": enhance_id,
        "char_id": row.get("charId"),
        "name": row.get("name") or str(enhance_id),
        "type": enhance_type,
        "type_label": ENHANCE_TYPE_LABELS.get(enhance_type or 0, str(enhance_type or "")),
        "quality": quality,
        "quality_label": QUALITY_LABELS.get(quality or 0, str(quality or "")),
        "description": row.get("des") or "",
        "description_plain": _plain(row.get("des")),
        "effect_id": row.get("effectId"),
        "limit": row.get("limit"),
        "weight": row.get("weight"),
        "bg_patch": row.get("bgPatch") or "",
        "bg_icon": row.get("bgIcon") or "",
        "bg_effect": row.get("bgEffect") or "",
        "condition_raw": row.get("condition") or "",
        "conditions": conditions,
        "prereq_ids": prereq_ids,
        "prereqs": [_compact_enhance_ref(enhance_by_id.get(item), item) for item in prereq_ids],
        "mutex_ids": mutex_ids,
        "mutexes": [_compact_enhance_ref(enhance_by_id.get(item), item) for item in mutex_ids],
        "level_ranges": _condition_level_ranges(conditions),
        "unlock_show_ids": unlock_show_ids,
        "unlock_show": [_compact_enhance_ref(enhance_by_id.get(item), item) for item in unlock_show_ids],
    }


def _compact_skill_show(row: dict[str, Any], logic_by_id: dict[int, dict[str, Any]]) -> dict[str, Any]:
    skill_id = _as_int(row.get("id")) or row.get("id")
    logic = logic_by_id.get(skill_id) if isinstance(skill_id, int) else None
    runtime_fields = {}
    if logic:
        runtime_fields = {
            "skill_type": logic.get("skillType"),
            "skill_group": logic.get("skillGroup"),
            "timeline_id": logic.get("timeLineId"),
            "pvp_timeline_id": logic.get("pvpTimeLineId"),
            "cd_ms": logic.get("cd"),
            "damage_raw": logic.get("damage"),
            "damage_text": _format_ratio(logic.get("damage")),
            "duration_ms": logic.get("duration"),
            "range": logic.get("range"),
            "buff_ids": _as_list(logic.get("buffId")),
        }
    return {
        "id": skill_id,
        "partner_id": row.get("partnerId"),
        "belong_id": row.get("belongId"),
        "base_skill": row.get("baseSkill"),
        "level_show": row.get("levelShow"),
        "skill_title": row.get("skillTitle") or "",
        "skill_title_plain": _plain(row.get("skillTitle")),
        "skill_name": row.get("skillName") or str(skill_id),
        "skill_description": row.get("skillDes") or "",
        "skill_description_plain": _plain(row.get("skillDes")),
        "skill_icon": row.get("skillIcon") or "",
        "skill_patch": row.get("skillPatch") or "",
        "show_condition": row.get("showCondition") or "",
        "runtime": runtime_fields,
    }


def _compact_skill_ref(
    skill_id: int,
    skill_show_by_id: dict[int, dict[str, Any]],
    logic_by_id: dict[int, dict[str, Any]],
    enhance_effect_by_id: dict[int, dict[str, Any]],
    buff_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    show = skill_show_by_id.get(skill_id)
    logic = logic_by_id.get(skill_id)
    enhance_effect = enhance_effect_by_id.get(skill_id)
    if show:
        result = _compact_skill_show(show, logic_by_id)
        if enhance_effect:
            result["enhance_effect"] = _compact_skill_enhance_effect(enhance_effect, buff_by_id)
        return result
    if logic:
        result = {
            "id": skill_id,
            "partner_id": logic.get("charId"),
            "skill_name": str(skill_id),
            "skill_description": "",
            "skill_description_plain": "",
            "runtime": {
                "skill_type": logic.get("skillType"),
                "skill_group": logic.get("skillGroup"),
                "timeline_id": logic.get("timeLineId"),
                "pvp_timeline_id": logic.get("pvpTimeLineId"),
                "cd_ms": logic.get("cd"),
                "damage_raw": logic.get("damage"),
                "damage_text": _format_ratio(logic.get("damage")),
                "duration_ms": logic.get("duration"),
                "range": logic.get("range"),
                "buff_ids": _as_list(logic.get("buffId")),
            },
        }
        if enhance_effect:
            result["enhance_effect"] = _compact_skill_enhance_effect(enhance_effect, buff_by_id)
        return result
    if enhance_effect:
        effect = _compact_skill_enhance_effect(enhance_effect, buff_by_id)
        return {
            "id": skill_id,
            "partner_id": effect.get("char_id"),
            "skill_name": str(skill_id),
            "skill_description": "",
            "skill_description_plain": "",
            "runtime": {},
            "enhance_effect": effect,
        }
    return {
        "id": skill_id,
        "skill_name": str(skill_id),
        "skill_description": "",
        "skill_description_plain": "",
        "runtime": {},
    }


def _compact_logic_skill(row: dict[str, Any], buff_by_id: dict[int, dict[str, Any]]) -> dict[str, Any]:
    buff_ids = [_as_int(item) for item in _as_list(row.get("buffId"))]
    clean_buff_ids = [item for item in buff_ids if item is not None]
    return {
        "id": row.get("id"),
        "char_id": row.get("charId"),
        "skill_type": row.get("skillType"),
        "skill_group": row.get("skillGroup"),
        "level": row.get("level"),
        "timeline_id": row.get("timeLineId"),
        "pvp_timeline_id": row.get("pvpTimeLineId"),
        "cd_ms": row.get("cd"),
        "damage_raw": row.get("damage"),
        "damage_text": _format_ratio(row.get("damage")),
        "duration_ms": row.get("duration"),
        "range": row.get("range"),
        "bullet_count": row.get("bulletCount"),
        "hit_num": row.get("hitNum"),
        "buff_ids": clean_buff_ids,
        "buffs": [_compact_buff(buff_by_id[item]) for item in clean_buff_ids if item in buff_by_id],
    }


def _compact_buff(row: dict[str, Any]) -> dict[str, Any]:
    add_attr = row.get("addAttr")
    return {
        "id": row.get("id"),
        "type": row.get("type"),
        "target_type": row.get("targetType"),
        "trigger_type": row.get("triggerType"),
        "duration": row.get("duration"),
        "interval": row.get("interval"),
        "eff_type": row.get("effType"),
        "damage_raw": row.get("damage"),
        "damage_text": _format_ratio(row.get("damage")),
        "add_attr": add_attr,
        "shield": row.get("shield"),
        "slow_down": row.get("slowDown"),
        "timeline_id": row.get("timelineId"),
    }


def _compact_skill_enhance_effect(row: dict[str, Any], buff_by_id: dict[int, dict[str, Any]]) -> dict[str, Any]:
    buff_id = _as_int(row.get("buffId"))
    return {
        "id": row.get("id"),
        "char_id": row.get("charId"),
        "skill": row.get("skill"),
        "skill_type": row.get("skillType"),
        "buff_id": buff_id,
        "buff": _compact_buff(buff_by_id[buff_id]) if buff_id is not None and buff_id in buff_by_id else None,
        "ext_release_count": row.get("extReleaseCount"),
        "ext_hit_num": row.get("extHitNum"),
        "ext_penetrate": row.get("extPenetrate"),
        "ext_atk_distance": row.get("extAtkDistance"),
        "mutex_timeline": row.get("mutexTimeline"),
    }


def _compact_level_milestones(rows: list[dict[str, Any]], *, limit: int = 40) -> list[dict[str, Any]]:
    milestones: list[dict[str, Any]] = []
    last_skill_key: tuple[str, str] | None = None
    sorted_rows = sorted(rows, key=lambda item: _sort_value(item.get("level")))
    for index, row in enumerate(sorted_rows):
        skill_key = (json.dumps(row.get("defaultSkill"), ensure_ascii=False), json.dumps(row.get("defaultSkillEnhance"), ensure_ascii=False))
        should_keep = index == 0 or index == len(sorted_rows) - 1 or skill_key != last_skill_key
        if should_keep:
            milestones.append(
                {
                    "id": row.get("id"),
                    "level": row.get("level"),
                    "cost": row.get("cost") or "",
                    "return_reward": row.get("returnReward") or "",
                    "point_name": row.get("pointName") or "",
                    "default_skill": _as_list(row.get("defaultSkill")),
                    "default_skill_enhance": _as_list(row.get("defaultSkillEnhance")),
                    "attack": row.get("ATTACK"),
                    "pvp_attack": row.get("PVPATTACK"),
                    "max_hp": row.get("MAXHP"),
                    "skill_damage": row.get("SKILL_DAMAGE"),
                }
            )
        last_skill_key = skill_key
        if len(milestones) >= limit:
            break
    return milestones


def _compact_level_summary(
    row: dict[str, Any],
    *,
    door_rows: list[dict[str, Any]],
    item_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    door_type_counts = Counter(str(item.get("doorType") or "") for item in door_rows)
    customized_types = sorted(
        {
            str(value)
            for door in door_rows
            for value in _as_list(door.get("customizedType"))
            if value not in (None, "")
        },
        key=lambda item: _sort_value(item, 10**9),
    )
    return {
        "id": row.get("id"),
        "name": row.get("name") or str(row.get("id") or ""),
        "name_plain": _plain(row.get("name")),
        "stage": row.get("stage"),
        "group": row.get("group"),
        "layer": row.get("layer"),
        "sub_layer": row.get("subLayer"),
        "type": row.get("type"),
        "init_char": row.get("initChar"),
        "recommend_tips": row.get("recommendTips") or "",
        "recommend_tips_plain": _plain(row.get("recommendTips")),
        "monster": _as_list(row.get("monster")),
        "reward": _as_list(row.get("reward")),
        "reward_items": _compact_reward_items(row.get("reward"), item_by_id),
        "reward_show_title": row.get("rewardShowTitle") or "",
        "reward_show_title_plain": _plain(row.get("rewardShowTitle")),
        "scene_id": row.get("sceneId"),
        "show_img": row.get("showImg"),
        "door_count": len(door_rows),
        "door_type_counts": dict(door_type_counts),
        "customized_types": customized_types,
        "first_door_times": [door.get("startRefreshTime") for door in sorted(door_rows, key=lambda item: _sort_value(item.get("startRefreshTime")))[:8]],
    }


def _compact_door_effect(
    row: dict[str, Any],
    skill_show_by_id: dict[int, dict[str, Any]],
    logic_by_id: dict[int, dict[str, Any]],
    enhance_effect_by_id: dict[int, dict[str, Any]],
    buff_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    effect_id = _as_int(row.get("id")) or row.get("id")
    char_id = _as_int(row.get("charId")) or 0
    door_type = _as_int(row.get("doorType"))
    skill_ids = [_as_int(item) for item in _as_list(row.get("skill"))]
    clean_skill_ids = [item for item in skill_ids if item is not None]
    skills = [_compact_skill_ref(item, skill_show_by_id, logic_by_id, enhance_effect_by_id, buff_by_id) for item in clean_skill_ids]
    result = {
        "id": effect_id,
        "char_id": char_id,
        "customized_type": row.get("customizedType"),
        "door_type": door_type,
        "door_type_label": DOOR_TYPE_LABELS.get(door_type or 0, str(door_type or "")),
        "door_effect": row.get("doorEffect") or "",
        "effect_show": row.get("effectShow") or "",
        "effect_show_plain": _plain(row.get("effectShow")),
        "show_tips": row.get("showTips") or "",
        "show_tips_plain": _plain(row.get("showTips")),
        "refresh_weights": row.get("refreshWeights"),
        "put_back": row.get("putBack") or "",
        "skill_ids": clean_skill_ids,
        "skills": skills,
    }
    effect_hints = _digitdoor_door_effect_runtime_hints(result)
    result["effect_hints"] = effect_hints
    result["effect_hint_preview"] = " / ".join(effect_hints[:8])
    return result


def _compact_character_card(
    row: dict[str, Any],
    *,
    level_rows: list[dict[str, Any]],
    skill_show_rows: list[dict[str, Any]],
    logic_skill_rows: list[dict[str, Any]],
    skill_enhance_effect_rows: list[dict[str, Any]],
    door_effect_rows: list[dict[str, Any]],
    logic_by_id: dict[int, dict[str, Any]],
    skill_show_by_id: dict[int, dict[str, Any]],
    enhance_effect_by_id: dict[int, dict[str, Any]],
    buff_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    char_id = _as_int(row.get("id")) or 0
    levels = sorted(level_rows, key=lambda item: _sort_value(item.get("level")))
    skills = [_compact_skill_show(item, logic_by_id) for item in sorted(skill_show_rows, key=lambda item: (_sort_value(item.get("levelShow")), _sort_value(item.get("id"))))]
    logic_skills = [_compact_logic_skill(item, buff_by_id) for item in sorted(logic_skill_rows, key=lambda item: (_sort_value(item.get("skillType")), _sort_value(item.get("level")), _sort_value(item.get("id"))))]
    skill_enhance_effects = [
        _compact_skill_enhance_effect(item, buff_by_id)
        for item in sorted(skill_enhance_effect_rows, key=lambda item: (_sort_value(item.get("skillType")), _sort_value(item.get("id"))))
    ]
    door_effects = [
        _compact_door_effect(item, skill_show_by_id, logic_by_id, enhance_effect_by_id, buff_by_id)
        for item in sorted(door_effect_rows, key=lambda item: (_sort_value(item.get("doorType")), _sort_value(item.get("customizedType")), _sort_value(item.get("id"))))
    ]
    quality = _as_int(row.get("quality"))
    return {
        "id": char_id,
        "name": row.get("name") or str(char_id),
        "positioning": row.get("positioning") or "",
        "position_type": row.get("positionType"),
        "career_type": row.get("careerType"),
        "quality": quality,
        "quality_label": QUALITY_LABELS.get(quality or 0, str(quality or "")),
        "sort": row.get("sort"),
        "can_battle": row.get("canBattle"),
        "unlock_level": row.get("unlockLevel"),
        "model": row.get("model"),
        "icon": row.get("icon") or "",
        "big_icon": row.get("bigIcon") or "",
        "head_icon": row.get("headIcon") or "",
        "head_icon_alt": row.get("headIconAlta") or "",
        "bg_icon": row.get("bgIcon") or "",
        "skill_icon": row.get("skillIcon") or "",
        "skill_name": row.get("skillName") or "",
        "skill_description": row.get("skillDes") or "",
        "skill_description_plain": _plain(row.get("skillDes")),
        "level_count": len(levels),
        "min_level": min((_sort_value(item.get("level")) for item in levels), default=0),
        "max_level": max((_sort_value(item.get("level"), 0) for item in levels), default=0),
        "level_milestones": _compact_level_milestones(levels),
        "skill_count": len(skills),
        "skills": skills,
        "logic_skill_count": len(logic_skills),
        "logic_skills": logic_skills,
        "skill_enhance_effect_count": len(skill_enhance_effects),
        "skill_enhance_effects": skill_enhance_effects,
        "door_effect_count": len(door_effects),
        "door_effects": door_effects,
        "terms": sorted(
            {
                item
                for source in [row.get("skillName"), row.get("positioning"), row.get("skillDes")]
                for item in re.findall(r"【([^】]{1,30})】", str(source or ""))
            }
        ),
    }


def _build_search_doc(card: dict[str, Any], index: int) -> dict[str, Any]:
    text_parts: list[Any] = [
        card.get("id"),
        card.get("name"),
        card.get("positioning"),
        card.get("skill_name"),
        card.get("skill_description"),
        " ".join(card.get("terms") or []),
    ]
    for collection_name in ("skills", "logic_skills", "enhances", "door_effects"):
        for item in card.get(collection_name) or []:
            if not isinstance(item, dict):
                continue
            text_parts.extend(
                [
                    item.get("skill_name"),
                    item.get("skill_description"),
                    item.get("name"),
                    item.get("description"),
                    item.get("show_tips"),
                    item.get("effect_show"),
                ]
            )
    combined = _normalize_search_text(" ".join(str(item or "") for item in text_parts))
    return {
        "index": index,
        "card": card,
        "combined": combined,
        "name": _normalize_search_text(card.get("name")),
    }


def _compact_enhance_group(char_id: int, rows: list[dict[str, Any]], enhance_by_id: dict[int, dict[str, Any]]) -> dict[str, Any]:
    sorted_rows = sorted(rows, key=lambda item: (_sort_value(item.get("type")), _sort_value(item.get("quality")), _sort_value(item.get("id"))))
    enhances = [_compact_enhance(item, enhance_by_id) for item in sorted_rows]
    recruit = next((item for item in enhances if item.get("type") == 1), enhances[0] if enhances else {})
    return {
        "char_id": char_id,
        "name": recruit.get("name") or str(char_id),
        "description": recruit.get("description") or "",
        "description_plain": recruit.get("description_plain") or "",
        "enhance_count": len(enhances),
        "enhances": enhances,
    }


def _digitdoor_enhance_ref_text(refs: list[dict[str, Any]]) -> str:
    return " / ".join(
        f"{ref.get('id')}:{ref.get('name')}"
        for ref in refs
        if ref.get("id") not in (None, "")
    )


def _digitdoor_level_range_text(level_ranges: list[dict[str, Any]]) -> str:
    return " / ".join(
        f"char{row.get('char_id')} Lv{row.get('min_level')}-{row.get('max_level')}"
        for row in level_ranges
        if row.get("char_id") not in (None, "")
    )


def _digitdoor_skill_enhance_condition_graph_rows(
    groups: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    group_by_enhance_id: dict[str, dict[str, Any]] = {}
    enhance_by_id: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group.get("enhances") or []:
            key = str(item.get("id") or "")
            if not key:
                continue
            group_by_enhance_id[key] = group
            enhance_by_id[key] = item

    node_rows: list[dict[str, Any]] = []
    edge_rows: list[dict[str, Any]] = []

    def add_edge(source: dict[str, Any], target: dict[str, Any], relation: str, note: str) -> None:
        source_id = str(source.get("id") or "")
        target_id = str(target.get("id") or "")
        target_group = group_by_enhance_id.get(target_id) or {}
        source_group = group_by_enhance_id.get(source_id) or {}
        edge_rows.append(
            {
                "source_id": source_id,
                "source_name": source.get("name") or "",
                "source_group_char_id": source_group.get("char_id") or "",
                "source_group_name": source_group.get("name") or "",
                "relation": relation,
                "target_id": target_id,
                "target_name": target.get("name") or target_id,
                "target_group_char_id": target_group.get("char_id") or "",
                "target_group_name": target_group.get("name") or "",
                "note": note,
            }
        )

    for group in groups:
        group_char_id = group.get("char_id")
        group_name = group.get("name") or ""
        for item in group.get("enhances") or []:
            condition_raw = item.get("condition_raw") or ""
            condition_text = _digitdoor_condition_projection("condition", condition_raw)
            prereqs = item.get("prereqs") or []
            mutexes = item.get("mutexes") or []
            unlock_show = item.get("unlock_show") or []
            level_ranges = item.get("level_ranges") or []
            node_rows.append(
                {
                    "id": item.get("id"),
                    "group_char_id": group_char_id,
                    "group_name": group_name,
                    "name": item.get("name") or "",
                    "type_label": item.get("type_label") or "",
                    "quality_label": item.get("quality_label") or "",
                    "description": item.get("description_plain") or "",
                    "condition_raw": condition_raw,
                    "condition_text": condition_text,
                    "condition_alternative_count": len(item.get("conditions") or []),
                    "prereq_count": len(prereqs),
                    "prereqs": _digitdoor_enhance_ref_text(prereqs),
                    "mutex_count": len(mutexes),
                    "mutexes": _digitdoor_enhance_ref_text(mutexes),
                    "level_range_count": len(level_ranges),
                    "level_ranges": _digitdoor_level_range_text(level_ranges),
                    "unlock_show_count": len(unlock_show),
                    "unlock_show": _digitdoor_enhance_ref_text(unlock_show),
                    "limit": item.get("limit") or "",
                    "weight": item.get("weight") or "",
                }
            )
            for ref in prereqs:
                add_edge(item, ref, "requires", "PR prerequisite from SkillEnhance.condition")
            for ref in mutexes:
                add_edge(item, ref, "mutex_with", "MU mutex/replacement from SkillEnhance.condition")
            for ref in unlock_show:
                add_edge(item, ref, "unlock_show", "unlockShow display hint")

    stats = {
        "group_count": len(groups),
        "enhance_count": len(node_rows),
        "condition_non_empty_count": sum(1 for row in node_rows if row.get("condition_raw")),
        "prereq_edge_count": sum(1 for row in edge_rows if row.get("relation") == "requires"),
        "mutex_edge_count": sum(1 for row in edge_rows if row.get("relation") == "mutex_with"),
        "unlock_show_edge_count": sum(1 for row in edge_rows if row.get("relation") == "unlock_show"),
        "level_range_node_count": sum(1 for row in node_rows if _as_int(row.get("level_range_count"))),
        "alternative_condition_node_count": sum(1 for row in node_rows if (_as_int(row.get("condition_alternative_count")) or 0) > 1),
    }
    return node_rows, edge_rows, stats


def _write_digitdoor_skill_enhance_condition_graph_markdown(
    path: Path,
    *,
    node_rows: list[dict[str, Any]],
    edge_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    config_dir: Path,
) -> None:
    lines = [
        "# DigitDoor SkillEnhance condition graph",
        "",
        "Static read-only graph of `SkillEnhance.condition` and `unlockShow` relationships.",
        "",
        f"- Config dir: `{config_dir}`",
        "",
        "## Stats",
        "",
    ]
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Group Samples", ""])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in node_rows:
        grouped[str(row.get("group_name") or row.get("group_char_id") or "")].append(row)
    for group_name, rows in list(grouped.items())[:8]:
        lines.append(f"### {group_name or 'Unknown'}")
        for row in rows[:8]:
            condition = row.get("condition_text") or row.get("condition_raw") or "无条件"
            lines.append(
                f"- `{row.get('id')}` `{row.get('name')}` `{row.get('quality_label')}`: {condition}"
            )
    lines.extend(["", "## Edge Samples", ""])
    for row in edge_rows[:80]:
        lines.append(
            f"- `{row.get('source_id')}` `{row.get('source_name')}` --{row.get('relation')}--> `{row.get('target_id')}` `{row.get('target_name')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- `SkillEnhance.charId` is treated as this enhancement-tree grouping key. It must not be blindly joined to `CharacterMainInfo.id` as the same namespace.",
            "- `PR` edges are prerequisites, `MU` edges are mutex/replacement constraints, and `TCLV` clauses are preserved as level-range text on nodes.",
            "- This graph is for catalog/wiki reasoning only; it is not guidance for modifying gameplay state or bypassing server authority.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _score_doc(doc: dict[str, Any], terms: tuple[str, ...]) -> int:
    if not terms:
        return 1
    if not all(term in doc["combined"] for term in terms):
        return 0
    score = 0
    for term in terms:
        if doc["name"] == term:
            score += 100
        if term in doc["name"]:
            score += 60
        score += 8 + min(doc["combined"].count(term), 8)
    return score


def _format_search_item(card: dict[str, Any], score: int) -> dict[str, Any]:
    return {
        "id": card.get("id"),
        "name": card.get("name"),
        "icon": card.get("icon"),
        "head_icon": card.get("head_icon"),
        "big_icon": card.get("big_icon"),
        "positioning": card.get("positioning"),
        "quality": card.get("quality"),
        "quality_label": card.get("quality_label"),
        "skill_name": card.get("skill_name"),
        "skill_description_preview": _preview(card.get("skill_description")),
        "skill_count": card.get("skill_count") or 0,
        "logic_skill_count": card.get("logic_skill_count") or 0,
        "skill_enhance_effect_count": card.get("skill_enhance_effect_count") or 0,
        "enhance_count": card.get("enhance_count") or 0,
        "door_effect_count": card.get("door_effect_count") or 0,
        "terms": card.get("terms") or [],
        "score": score,
    }


def _build_enhance_group_search_doc(group: dict[str, Any], index: int) -> dict[str, Any]:
    text_parts: list[Any] = [
        group.get("char_id"),
        group.get("name"),
        group.get("description"),
    ]
    for item in group.get("enhances") or []:
        if not isinstance(item, dict):
            continue
        text_parts.extend(
            [
                item.get("id"),
                item.get("name"),
                item.get("description"),
                item.get("condition_raw"),
                _digitdoor_condition_projection("condition", item.get("condition_raw")),
                _digitdoor_enhance_ref_text(item.get("prereqs") or []),
                _digitdoor_enhance_ref_text(item.get("mutexes") or []),
                _digitdoor_level_range_text(item.get("level_ranges") or []),
                _digitdoor_enhance_ref_text(item.get("unlock_show") or []),
            ]
        )
    combined = _normalize_search_text(" ".join(str(item or "") for item in text_parts))
    return {
        "index": index,
        "group": group,
        "combined": combined,
        "name": _normalize_search_text(group.get("name")),
    }


def _format_enhance_group_search_item(group: dict[str, Any], score: int) -> dict[str, Any]:
    enhances = [item for item in group.get("enhances") or [] if isinstance(item, dict)]
    condition_count = sum(1 for item in enhances if item.get("condition_raw"))
    prereq_count = sum(len(item.get("prereqs") or []) for item in enhances)
    mutex_count = sum(len(item.get("mutexes") or []) for item in enhances)
    level_range_count = sum(len(item.get("level_ranges") or []) for item in enhances)
    preview_parts = [
        item.get("name")
        for item in enhances
        if item.get("type") != 1 and item.get("name")
    ]
    return {
        "id": group.get("char_id"),
        "char_id": group.get("char_id"),
        "name": group.get("name"),
        "description_preview": _preview(group.get("description")),
        "enhance_count": len(enhances),
        "condition_count": condition_count,
        "prereq_count": prereq_count,
        "mutex_count": mutex_count,
        "level_range_count": level_range_count,
        "enhance_preview": " / ".join(str(item) for item in preview_parts[:6]),
        "score": score,
    }


def _default_catalog_source_files(root: Path) -> list[Path]:
    try:
        config_dir = _find_default_config_dir(root)
    except FanxiuResourceError:
        return []
    names = [
        "CharacterMainInfo.lua",
        "CharacterLevel.lua",
        "CharacterSkillInfo.lua",
        "CharacterSkillShow.lua",
        "SkillEnhanceEffect.lua",
        "SkillEnhance.lua",
        "SkillRefreshEffect.lua",
        "BuffEffect.lua",
        "DoorRefreshPoint.lua",
        "Level.lua",
        "DigitDoorStage.lua",
        "DigitDoorPreLevelReward.lua",
    ]
    return [config_dir / name for name in names if (config_dir / name).is_file()]


def _is_default_catalog_stale(catalog_path: Path, root: Path) -> bool:
    if not catalog_path.is_file():
        return True
    try:
        data = _read_json(catalog_path)
    except Exception:
        return True
    if data.get("schema_version") != DIGITDOOR_CATALOG_SCHEMA_VERSION:
        return True
    catalog_mtime_ns = catalog_path.stat().st_mtime_ns
    return any(path.is_file() and path.stat().st_mtime_ns > catalog_mtime_ns for path in _default_catalog_source_files(root))


def _resolve_catalog_file(export_root: str | Path | None = None, *, rebuild_missing: bool = True) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    path = root / DEFAULT_CATALOG
    if rebuild_missing and _is_default_catalog_stale(path, root):
        build_fanxiu_digitdoor_catalog(export_root=export_root)
    if not path.is_file():
        raise FanxiuResourceError(f"数字门图鉴尚未生成：{path}")
    return path


@lru_cache(maxsize=8)
def _load_catalog_cached(path_text: str, mtime_ns: int, size: int, export_root_text: str) -> dict[str, Any]:
    catalog = _read_json(Path(path_text))
    cards = [card for card in catalog.get("cards") or [] if isinstance(card, dict)]
    enhance_groups = [group for group in catalog.get("custom_enhance_groups") or [] if isinstance(group, dict)]
    levels = [item for item in catalog.get("levels") or [] if isinstance(item, dict)]
    stages = [item for item in catalog.get("stages") or [] if isinstance(item, dict)]
    pre_level_rewards = [item for item in catalog.get("pre_level_rewards") or [] if isinstance(item, dict)]
    return {
        "catalog": {
            **catalog,
            "export_root": export_root_text,
            "catalog_path": path_text,
        },
        "cards": cards,
        "cards_by_id": {str(card.get("id")): card for card in cards},
        "search_docs": [_build_search_doc(card, index) for index, card in enumerate(cards)],
        "enhance_groups": enhance_groups,
        "enhance_groups_by_id": {str(group.get("char_id")): group for group in enhance_groups},
        "enhance_group_search_docs": [_build_enhance_group_search_doc(group, index) for index, group in enumerate(enhance_groups)],
        "levels": levels,
        "levels_by_id": {str(item.get("id")): item for item in levels},
        "level_search_docs": [_build_level_search_doc(item, index) for index, item in enumerate(levels)],
        "stages": stages,
        "pre_level_rewards": pre_level_rewards,
    }


def load_fanxiu_digitdoor_runtime_index(
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    catalog_path = _resolve_catalog_file(export_root, rebuild_missing=rebuild_missing)
    root = resolve_fanxiu_export_root(export_root)
    stat = catalog_path.stat()
    return _load_catalog_cached(str(catalog_path), stat.st_mtime_ns, stat.st_size, str(root))


def search_fanxiu_digitdoor_character_cards(
    *,
    query: str = "",
    limit: int = 80,
    offset: int = 0,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    runtime = load_fanxiu_digitdoor_runtime_index(export_root=export_root, rebuild_missing=rebuild_missing)
    catalog = runtime["catalog"]
    terms = tuple(item.strip().lower() for item in re.split(r"\s+", query or "") if item.strip())
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for doc in runtime["search_docs"]:
        score = _score_doc(doc, terms)
        if score <= 0:
            continue
        scored.append((score, int(doc["index"]), doc["card"]))
    if terms:
        scored.sort(key=lambda item: (-item[0], _sort_value(item[2].get("sort")), _sort_value(item[2].get("id"))))
    else:
        scored.sort(key=lambda item: (_sort_value(item[2].get("sort")), _sort_value(item[2].get("id")), item[1]))
    page_rows = scored[offset: offset + limit]
    return {
        "query": query,
        "limit": limit,
        "offset": offset,
        "total": len(scored),
        "stats": catalog.get("stats") or {},
        "catalog_path": catalog["catalog_path"],
        "items": [_format_search_item(card, score) for score, _index, card in page_rows],
    }


def get_fanxiu_digitdoor_character_card(
    character_id: str | int,
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    requested = str(character_id)
    runtime = load_fanxiu_digitdoor_runtime_index(export_root=export_root, rebuild_missing=rebuild_missing)
    card = runtime["cards_by_id"].get(requested)
    if not card:
        raise FanxiuResourceError(f"没有找到数字门角色：{character_id}")
    return {
        "catalog_path": runtime["catalog"]["catalog_path"],
        "card": card,
    }


def search_fanxiu_digitdoor_enhance_groups(
    *,
    query: str = "",
    limit: int = 80,
    offset: int = 0,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    runtime = load_fanxiu_digitdoor_runtime_index(export_root=export_root, rebuild_missing=rebuild_missing)
    catalog = runtime["catalog"]
    terms = tuple(item.strip().lower() for item in re.split(r"\s+", query or "") if item.strip())
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for doc in runtime["enhance_group_search_docs"]:
        score = _score_doc(doc, terms)
        if score <= 0:
            continue
        scored.append((score, int(doc["index"]), doc["group"]))
    if terms:
        scored.sort(key=lambda item: (-item[0], _sort_value(item[2].get("char_id")), item[1]))
    else:
        scored.sort(key=lambda item: (_sort_value(item[2].get("char_id")), item[1]))
    page_rows = scored[offset: offset + limit]
    return {
        "query": query,
        "limit": limit,
        "offset": offset,
        "total": len(scored),
        "stats": catalog.get("stats") or {},
        "catalog_path": catalog["catalog_path"],
        "items": [_format_enhance_group_search_item(group, score) for score, _index, group in page_rows],
    }


def get_fanxiu_digitdoor_enhance_group(
    group_id: str | int,
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    requested = str(group_id)
    runtime = load_fanxiu_digitdoor_runtime_index(export_root=export_root, rebuild_missing=rebuild_missing)
    group = runtime["enhance_groups_by_id"].get(requested)
    if not group:
        raise FanxiuResourceError(f"没有找到数字门强化组：{group_id}")
    return {
        "catalog_path": runtime["catalog"]["catalog_path"],
        "group": group,
    }


def _build_level_search_doc(item: dict[str, Any], index: int) -> dict[str, Any]:
    reward_text = " ".join(
        str(reward.get("text") or reward.get("raw") or reward.get("id") or "")
        for reward in item.get("reward_items") or []
        if isinstance(reward, dict)
    )
    text_parts = [
        item.get("id"),
        item.get("name"),
        item.get("name_plain"),
        item.get("stage"),
        item.get("layer"),
        item.get("sub_layer"),
        item.get("recommend_tips"),
        item.get("recommend_tips_plain"),
        item.get("reward_show_title"),
        item.get("reward_show_title_plain"),
        reward_text,
        " ".join(str(value) for value in item.get("customized_types") or []),
        " ".join(str(value) for value in item.get("monster") or []),
    ]
    combined = _normalize_search_text(" ".join(str(value or "") for value in text_parts))
    return {
        "index": index,
        "item": item,
        "combined": combined,
        "name": _normalize_search_text(item.get("name_plain") or item.get("name")),
    }


def _format_level_search_item(item: dict[str, Any], score: int) -> dict[str, Any]:
    rewards = [reward for reward in item.get("reward_items") or [] if isinstance(reward, dict)]
    return {
        "id": item.get("id"),
        "name": item.get("name_plain") or item.get("name") or f"关卡 {item.get('id')}",
        "stage": item.get("stage"),
        "group": item.get("group"),
        "layer": item.get("layer"),
        "sub_layer": item.get("sub_layer"),
        "type": item.get("type"),
        "init_char": item.get("init_char"),
        "recommend_tips": item.get("recommend_tips_plain") or _plain(item.get("recommend_tips")),
        "reward_show_title": item.get("reward_show_title_plain") or item.get("reward_show_title"),
        "reward_preview": " / ".join(str(reward.get("text") or reward.get("raw") or "") for reward in rewards[:6] if reward.get("text") or reward.get("raw")),
        "reward_count": len(rewards),
        "door_count": item.get("door_count") or 0,
        "customized_types": item.get("customized_types") or [],
        "monster_count": len(item.get("monster") or []),
        "score": score,
    }


def _format_digitdoor_stage_option(item: dict[str, Any], level_count: int) -> dict[str, Any]:
    title = item.get("title") or item.get("name_plain") or item.get("name") or item.get("id")
    return {
        "id": item.get("id"),
        "name": str(title or ""),
        "reward_count": len(_as_list(item.get("rewardShow"))),
        "level_count": level_count,
    }


def _enrich_digitdoor_stage_reward(
    stage: dict[str, Any] | None,
    *,
    export_root: str | Path,
) -> dict[str, Any] | None:
    if not stage:
        return None
    if stage.get("reward_items"):
        return stage
    item_by_id = _load_items_by_id(Path(export_root))
    return {
        **stage,
        "name_plain": _plain(stage.get("name")),
        "title_plain": _plain(stage.get("title")),
        "reward_items": _compact_reward_items(stage.get("rewardShow"), item_by_id),
    }


def _digitdoor_reward_item_source_rows(
    runtime: dict[str, Any],
    *,
    export_root: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add_rows(
        *,
        source_table: str,
        config_id: Any,
        stage: Any,
        layer: Any,
        reward_items: list[dict[str, Any]],
    ) -> None:
        for index, reward in enumerate(reward_items, start=1):
            item = reward.get("item") or {}
            rows.append(
                {
                    "source_table": source_table,
                    "config_id": config_id,
                    "stage": stage,
                    "layer": layer,
                    "reward_index": index,
                    "raw": reward.get("raw") or "",
                    "reward_type": reward.get("type") or "",
                    "item_id": reward.get("id") or "",
                    "item_name": item.get("name") or "",
                    "quality_name": item.get("quality_name") or "",
                    "count": reward.get("count") if reward.get("count") is not None else "",
                    "extra_mark": reward.get("extra_mark") if reward.get("extra_mark") is not None else "",
                }
            )

    for level in runtime["levels"]:
        add_rows(
            source_table="Level",
            config_id=level.get("id"),
            stage=level.get("stage"),
            layer=level.get("layer"),
            reward_items=[reward for reward in level.get("reward_items") or [] if isinstance(reward, dict)],
        )

    item_by_id = _load_items_by_id(export_root)
    for stage in runtime["pre_level_rewards"]:
        add_rows(
            source_table="DigitDoorPreLevelReward",
            config_id=stage.get("id"),
            stage=stage.get("id"),
            layer="",
            reward_items=_compact_reward_items(stage.get("rewardShow"), item_by_id),
        )
    return rows


def _digitdoor_reward_result_resolution_rows(
    reward_item_rows: list[dict[str, Any]],
    *,
    reward_types: dict[str, int],
    extra_marks: dict[str, int],
    item_corner_by_id: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in reward_item_rows:
        runtime_type, runtime_type_name = _reward_type_from_token(item.get("reward_type"), reward_types)
        amount = _as_int(item.get("count"))
        extra_mark = _as_int(item.get("extra_mark"))
        runtime_extra_mark = extra_mark if extra_mark is not None else 0
        corner = item_corner_by_id.get(runtime_extra_mark)
        note = ""
        if amount is not None and amount < 0:
            note = "Static preview uses a negative amount sentinel; server settlement amount still comes from RewardResult.amount."
        elif extra_mark is None:
            note = "Static reward omits extraMark; FormatStr2Reward defaults RewardResult.extraMark to 0."
        rows.append(
            {
                "source_table": item.get("source_table") or "",
                "config_id": item.get("config_id") or "",
                "stage": item.get("stage") or "",
                "layer": item.get("layer") or "",
                "reward_index": item.get("reward_index") or "",
                "raw": item.get("raw") or "",
                "static_reward_type_token": item.get("reward_type") or "",
                "runtime_reward_type": runtime_type if runtime_type is not None else "",
                "runtime_reward_type_name": runtime_type_name,
                "code": item.get("item_id") or "",
                "item_name": item.get("item_name") or "",
                "quality_name": item.get("quality_name") or "",
                "amount": amount if amount is not None else "",
                "extra_mark": runtime_extra_mark,
                "extra_mark_name": _extra_mark_label(runtime_extra_mark, extra_marks=extra_marks, item_corner=corner),
                "extra_mark_show_type": corner.get("showType") if corner else "",
                "extra_mark_eff_name": corner.get("effName") if corner else "",
                "resolution_rule": (
                    f"RewardType.{runtime_type_name}({runtime_type}) => ConfigName.Item_Item[code]"
                    if runtime_type_name == "ITEM" and runtime_type is not None
                    else "Non-ITEM reward type needs its own GameUtil.GetItemCfgByRewardTypeAndCode branch"
                ),
                "note": note,
            }
        )
    return rows


def _collect_digitdoor_reward_settlement_flow(root: Path) -> list[dict[str, Any]]:
    try:
        logic_dir = _find_default_logic_dir(root)
    except FanxiuResourceError:
        return []
    topic_terms = {
        "gameplayer_settlement": ("SM_DigitDoorGamePlayerFun", "rewardResults", "DigitDoorExitGame"),
        "result_view_display": ("DigitDoorResultInfoView", "rewardResults", "AddRewardResults"),
        "skip_reward_display": ("isSkipLevel", "rewardResults", "RewardAndCostPopType"),
    }
    rows: list[dict[str, Any]] = []
    for hit in _scan_lua_hits_for_topics(logic_dir, root, topic_terms):
        rows.append(
            {
                "source_file": hit.get("file") or "",
                "line": hit.get("line") or "",
                "category": f"digitdoor_{hit.get('topic') or ''}",
                "target": hit.get("function") or "",
                "snippet": hit.get("snippet") or "",
            }
        )
    return rows


def _write_digitdoor_reward_result_resolution_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    item_rows: list[dict[str, Any]],
    flow_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# digitdoor RewardResult resolution report",
        "",
        "Static read-only drilldown for how DigitDoor `Level.reward` and `DigitDoorPreLevelReward.rewardShow` strings map into the shared RewardResult shape.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Field Semantics",
            "",
            "- `type`: static `Item|...` maps to `RewardType.ITEM` (`0`).",
            "- `code`: item id when `type == RewardType.ITEM`; `GameUtil.GetItemCfgByRewardTypeAndCode` resolves it through `ConfigName.Item_Item`.",
            "- `amount`: reward quantity from the static string. Negative preview sentinels remain display hints; server settlement still owns final `RewardResult.amount`.",
            "- `extraMark`: optional corner/effect marker, defaulting to `0` when omitted.",
            "",
            "## Reward Item Samples",
            "",
        ]
    )
    for row in item_rows[:100]:
        lines.append(
            f"- `{row.get('source_table')}` `{row.get('config_id')}` #{row.get('reward_index')}: type `{row.get('runtime_reward_type_name')}` code `{row.get('code')}` `{row.get('item_name')}` amount `{row.get('amount')}` extra `{row.get('extra_mark')}` `{row.get('extra_mark_name')}`"
        )
    lines.extend(["", "## Evidence Samples", ""])
    for row in flow_rows[:100]:
        lines.append(f"- `{row.get('category')}` `{row.get('source_file')}:{row.get('line')}` `{row.get('snippet')}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "`Level.reward` and `DigitDoorPreLevelReward.rewardShow` are static display/preview configs. Final battle reward display is still driven by server-returned `SM_DigitDoorGamePlayer.rewardResults`; live calibration needs a privacy-filtered runtime sample.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_reward_result_resolution_probe(
    *,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None
    runtime = load_fanxiu_digitdoor_runtime_index(export_root=root)
    reward_item_rows = _digitdoor_reward_item_source_rows(runtime, export_root=root)
    reward_type_path = _find_lua_asset_by_name(root, "RewardType.lua")
    reward_types, extra_marks = _parse_reward_type_lua(reward_type_path)
    item_corner_path, item_corner_rows, item_corner_by_id = _load_item_corner_rows(
        root,
        lang_path=resolved_lang_path,
        lang_map=lang_map,
    )
    resolution_rows = _digitdoor_reward_result_resolution_rows(
        reward_item_rows,
        reward_types=reward_types,
        extra_marks=extra_marks,
        item_corner_by_id=item_corner_by_id,
    )
    flow_rows = _collect_reward_result_resolution_flow(root) + _collect_digitdoor_reward_settlement_flow(root)
    flow_text = "\n".join(str(row.get("snippet") or "") for row in flow_rows)
    settlement_flow_text = "\n".join(
        str(row.get("snippet") or "")
        for row in flow_rows
        if str(row.get("category") or "").startswith("digitdoor_")
    )
    stats = {
        "static_reward_item_count": len(reward_item_rows),
        "level_reward_item_count": sum(1 for row in resolution_rows if row.get("source_table") == "Level"),
        "prelevel_reward_item_count": sum(1 for row in resolution_rows if row.get("source_table") == "DigitDoorPreLevelReward"),
        "resolved_item_reward_count": sum(1 for row in resolution_rows if row.get("runtime_reward_type_name") == "ITEM"),
        "unique_item_count": len({str(row.get("code") or "") for row in resolution_rows if row.get("code") not in (None, "")}),
        "unique_runtime_extra_mark_count": len({str(row.get("extra_mark") or 0) for row in resolution_rows}),
        "nonzero_extra_mark_row_count": sum(1 for row in resolution_rows if _as_int(row.get("extra_mark")) not in (None, 0)),
        "negative_amount_row_count": sum(1 for row in resolution_rows if (_as_int(row.get("amount")) or 0) < 0),
        "reward_type_enum_count": len(reward_types),
        "extra_mark_enum_count": len(extra_marks),
        "item_corner_count": len(item_corner_rows),
        "flow_evidence_count": len(flow_rows),
        "digitdoor_settlement_evidence_count": sum(1 for row in flow_rows if str(row.get("category") or "").startswith("digitdoor_")),
    }
    verdict = {
        "static_reward_string_shape_matches_reward_result": bool(re.search(r"FormatStr2Reward|_RewardResult\.new|RewardResult read_fields", flow_text)),
        "item_reward_type_resolves_code_to_item_table": reward_types.get("ITEM") == 0 and bool(re.search(r"RewardType\.ITEM|ConfigName\.Item_Item", flow_text)),
        "amount_maps_to_reward_result_amount": bool(re.search(r"reward\.amount|amount:Long", flow_text)),
        "extra_mark_resolves_to_item_corner": bool(item_corner_rows) and bool(re.search(r"Item_ItemCorner|UpdateItemCornet", flow_text)),
        "server_result_boundary_remains_gameplayer_reward_results": bool(re.search(r"rewardResults|DigitDoorResultInfoView|AddRewardResults", settlement_flow_text)),
        "runtime_values_still_require_server_sample": True,
    }

    output_dir = root / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    items_tsv = output_dir / "lua_lscript_module_digitdoor_reward_result_resolution_items.tsv"
    flow_tsv = output_dir / "lua_lscript_module_digitdoor_reward_result_resolution_flow.tsv"
    report_path = output_dir / "lua_lscript_module_digitdoor_reward_result_resolution_report.md"
    json_path = output_dir / "lua_lscript_module_digitdoor_reward_result_resolution_report.json"
    item_fields = [
        "source_table",
        "config_id",
        "stage",
        "layer",
        "reward_index",
        "raw",
        "static_reward_type_token",
        "runtime_reward_type",
        "runtime_reward_type_name",
        "code",
        "item_name",
        "quality_name",
        "amount",
        "extra_mark",
        "extra_mark_name",
        "extra_mark_show_type",
        "extra_mark_eff_name",
        "resolution_rule",
        "note",
    ]
    _write_tsv(items_tsv, resolution_rows, item_fields)
    _write_tsv(flow_tsv, flow_rows, ["source_file", "line", "category", "target", "snippet"])
    _write_digitdoor_reward_result_resolution_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        item_rows=resolution_rows,
        flow_rows=flow_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "source": {
                    "catalog_path": runtime["catalog"]["catalog_path"],
                    "reward_type_lua": str(reward_type_path or ""),
                    "item_corner_lua": str(item_corner_path or ""),
                    "lang_path": str(resolved_lang_path or ""),
                },
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "items": resolution_rows[:120],
                    "flow": flow_rows[:120],
                },
                "files": {
                    "items": str(items_tsv),
                    "flow": str(flow_tsv),
                    "markdown": str(report_path),
                    "json": str(json_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "items": str(items_tsv),
            "flow": str(flow_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _digitdoor_reward_marker_summary_rows(resolution_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter: Counter[tuple[str, str, str, str, str, str]] = Counter()
    for row in resolution_rows:
        extra_mark = str(row.get("extra_mark") or "0").strip()
        if extra_mark in ("", "0"):
            continue
        counter[
            (
                str(row.get("code") or ""),
                str(row.get("item_name") or ""),
                str(row.get("amount") or ""),
                extra_mark,
                str(row.get("extra_mark_eff_name") or ""),
                str(row.get("source_table") or ""),
            )
        ] += 1
    summary_rows: list[dict[str, Any]] = []
    for (code, item_name, amount, extra_mark, eff_name, source_table), count in sorted(
        counter.items(),
        key=lambda item: (-item[1], item[0][0], item[0][2], item[0][5]),
    ):
        summary_rows.append(
            {
                "code": code,
                "item_name": item_name,
                "amount": amount,
                "extra_mark": extra_mark,
                "extra_mark_eff_name": eff_name,
                "source_table": source_table,
                "row_count": count,
                "has_negative_amount": str(amount).startswith("-"),
            }
        )
    return summary_rows


def _write_digitdoor_reward_marker_semantics_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    summary_rows: list[dict[str, Any]],
    negative_rows: list[dict[str, Any]],
    files: dict[str, str],
) -> None:
    lines = [
        "# digitdoor reward marker semantics report",
        "",
        "Static read-only summary of DigitDoor reward marker rows after RewardResult resolution. This report focuses on `extraMark` and negative static preview amounts.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## extraMark Item Summary", ""])
    for row in summary_rows[:100]:
        lines.append(
            f"- `{row.get('source_table')}` code `{row.get('code')}` `{row.get('item_name')}` amount `{row.get('amount')}` extra `{row.get('extra_mark')}` eff `{row.get('extra_mark_eff_name')}` rows `{row.get('row_count')}`"
        )
    lines.extend(["", "## Negative Preview Samples", ""])
    for row in negative_rows[:100]:
        lines.append(
            f"- `{row.get('source_table')}` `{row.get('config_id')}` stage `{row.get('stage')}` raw `{row.get('raw')}` item `{row.get('item_name')}`"
        )
    lines.extend(["", "## Files", ""])
    for label, file_path in files.items():
        lines.append(f"- `{label}`: `{file_path}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_reward_marker_semantics_probe(
    *,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    resolution = build_fanxiu_digitdoor_reward_result_resolution_probe(lang_path=lang_path, export_root=root)
    resolution_rows = _read_tsv_dicts(Path(resolution["files"]["items"]))
    extra_rows = [row for row in resolution_rows if str(row.get("extra_mark") or "0").strip() not in ("", "0")]
    negative_rows = [row for row in resolution_rows if str(row.get("amount") or "").strip().startswith("-")]
    summary_rows = _digitdoor_reward_marker_summary_rows(resolution_rows)
    source_counts = Counter(str(row.get("source_table") or "") for row in resolution_rows)
    extra_source_counts = Counter(str(row.get("source_table") or "") for row in extra_rows)
    negative_source_counts = Counter(str(row.get("source_table") or "") for row in negative_rows)
    negative_extra_marks = sorted({str(row.get("extra_mark") or "0") for row in negative_rows})
    stats = {
        "static_reward_rows": len(resolution_rows),
        "extra_mark_rows": len(extra_rows),
        "negative_amount_rows": len(negative_rows),
        "summary_row_count": len(summary_rows),
        "source_counts": dict(source_counts),
        "extra_mark_source_counts": dict(extra_source_counts),
        "negative_amount_source_counts": dict(negative_source_counts),
        "negative_amount_extra_marks": negative_extra_marks,
    }
    verdict = {
        "extra_mark_is_marker_not_count": bool(extra_rows),
        "negative_amount_rows_are_preview_rows": bool(negative_rows) and set(negative_source_counts) == {"DigitDoorPreLevelReward"},
        "negative_amount_rows_all_have_extra_mark": bool(negative_rows) and all(str(row.get("extra_mark") or "0") not in ("", "0") for row in negative_rows),
        "server_amount_still_requires_reward_results": bool(resolution.get("verdict", {}).get("runtime_values_still_require_server_sample")),
    }

    output_dir = root / "apk_static_index"
    summary_tsv = output_dir / "lua_lscript_module_digitdoor_reward_marker_semantics_summary.tsv"
    report_path = output_dir / "lua_lscript_module_digitdoor_reward_marker_semantics_report.md"
    json_path = output_dir / "lua_lscript_module_digitdoor_reward_marker_semantics_report.json"
    _write_tsv(
        summary_tsv,
        summary_rows,
        ["code", "item_name", "amount", "extra_mark", "extra_mark_eff_name", "source_table", "row_count", "has_negative_amount"],
    )
    files = {
        "summary": str(summary_tsv),
        "reward_result_items": resolution["files"]["items"],
        "markdown": str(report_path),
        "json": str(json_path),
    }
    _write_digitdoor_reward_marker_semantics_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        summary_rows=summary_rows,
        negative_rows=negative_rows,
        files=files,
    )
    json_path.write_text(
        json.dumps(
            {
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "summary": summary_rows[:120],
                    "negative_rows": negative_rows[:120],
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


def _collect_digitdoor_reward_marker_ui_evidence(
    root: Path,
    *,
    effect_name: str,
) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []

    def scan_file(path: Path | None, patterns: list[tuple[str, re.Pattern[str]]], *, target: str | None = None) -> None:
        if path is None or not path.is_file():
            return
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return
        counts: Counter[str] = Counter()
        for line_no, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            for category, pattern in patterns:
                if counts[category] >= 8 or not pattern.search(stripped):
                    continue
                counts[category] += 1
                rows.append(
                    {
                        "source_file": _path_display(path, root),
                        "line": line_no,
                        "category": category,
                        "target": target or path.name,
                        "snippet": _WHITESPACE_RE.sub(" ", stripped)[:360],
                    }
                )

    scan_file(
        _find_lua_asset_by_name(root, "CostAndRewardMgr.lua"),
        [
            ("format_reward_extra_mark", re.compile(r"function\s+_M\.FormatStr2Reward|reward\.extraMark\s*=", re.I)),
        ],
        target="CostAndRewardMgr.FormatStr2Reward",
    )
    scan_file(
        _find_lua_asset_by_name(root, "GameUtil.lua"),
        [
            ("get_item_icon_extra_mark", re.compile(r"function\s+_M\.GetItemIcon|extraMark\s*=|return\s+cfg\s*,\s*itemNum\s*,\s*extraMark", re.I)),
            ("get_reward_result_extra_mark", re.compile(r"function\s+_M\.GetRewardResult|Item\|%s_%s_%s|FormatStr2Reward\(str\)", re.I)),
            ("sort_or_merge_preserves_extra_mark", re.compile(r"v\.extraMark\s*==\s*j\.extraMark|RewardType\.ExtraMark\.FirstGet|ItemCornerType", re.I)),
            ("update_item_corner_lookup", re.compile(r"function\s+_M\.UpdateItemCornet|ConfigName\.Item_ItemCorner|itemCornerlo\.effName|GetEffect\(showEffName\)|effItem:Play", re.I)),
            ("corner_show_type", re.compile(r"function\s+_M\.GetCornetShowType|ItemCornerShowType\.OnlyEff|ItemCornerShowType\.CornerAndEff", re.I)),
        ],
        target="GameUtil",
    )
    scan_file(
        _find_lua_asset_by_name(root, "ItemType.lua"),
        [
            ("item_corner_show_type_enum", re.compile(r"ItemCornerShowType|OnlyCorner\s*=\s*1|OnlyEff\s*=\s*2|CornerAndEff\s*=\s*3", re.I)),
            ("item_corner_type_enum", re.compile(r"ItemCornerType|DuanWu|PartnerExploreExtra", re.I)),
            ("extra_mark_effect_slot", re.compile(r"ItemExtraMarkUseType|effNameExtraMark|effItemNameExtraMark", re.I)),
        ],
        target="ItemType",
    )
    for path in sorted(root.glob("by_source/**/text_assets/RewardItem*.lua"), key=lambda item: str(item).lower()):
        scan_file(
            path,
            [
                ("reward_item_consumes_extra_mark", re.compile(r"function\s+_M\.UpdateItem|data\.extraMark|UpdateItemCorner\(signText,data\.extraMark\)", re.I)),
                ("reward_item_calls_update_corner", re.compile(r"function\s+_M\.UpdateItemCorner|GameUtil\.UpdateItemCornet|UpdateNomalLimitType", re.I)),
            ],
            target=path.name,
        )

    effect_resource_present = False
    manifest_path = root / "apk_static_index" / "resource_manifest_diff.tsv"
    if manifest_path.is_file() and effect_name:
        for row in _read_tsv_dicts(manifest_path):
            resource_path = str(row.get("path") or row.get("resource_actual_path") or "")
            if f"{effect_name}.bytes" not in resource_path:
                continue
            effect_resource_present = True
            rows.append(
                {
                    "source_file": _path_display(manifest_path, root),
                    "line": "",
                    "category": "effect_asset_present",
                    "target": effect_name,
                    "snippet": f"{row.get('status') or ''} {resource_path} size={row.get('resource_size') or row.get('apk_size') or ''} md5={row.get('resource_md5') or row.get('apk_md5') or ''}".strip(),
                }
            )
            break

    return rows, effect_resource_present


def _write_digitdoor_reward_marker_ui_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    corner: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
    files: dict[str, str],
) -> None:
    lines = [
        "# digitdoor reward marker UI report",
        "",
        "Static read-only drilldown for how DigitDoor reward `extraMark` reaches the generic item corner/effect UI path.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## ItemCorner#7",
            "",
            f"- `id`: `{corner.get('id') or corner.get('_row_key') or ''}`",
            f"- `name`: `{_plain(corner.get('name') or '')}`",
            f"- `showType`: `{corner.get('showType') or ''}` (`OnlyEff` when value is `2`)",
            f"- `effName`: `{corner.get('effName') or ''}`",
            "",
            "## Static UI Chain",
            "",
            "1. Static reward strings can carry an optional third segment: `item|code_amount_extraMark`.",
            "2. `CostAndRewardMgr.FormatStr2Reward` writes that third segment into `RewardResult.extraMark`; omitted values default to `0`.",
            "3. `RewardItem.UpdateItem` keeps using `data.extraMark` when rendering an item.",
            "4. `GameUtil.UpdateItemCornet` looks up `ConfigName.Item_ItemCorner[extraMark]`.",
            "5. For `ItemCorner#7`, `showType=2` selects the effect-only branch and `effName` chooses the played UI effect.",
            "",
            "## Evidence Samples",
            "",
        ]
    )
    for row in evidence_rows[:120]:
        lines.append(f"- `{row.get('category')}` `{row.get('source_file')}:{row.get('line')}` `{row.get('snippet')}`")
    lines.extend(["", "## Files", ""])
    for label, file_path in files.items():
        lines.append(f"- `{label}`: `{file_path}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_reward_marker_ui_probe(
    *,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None
    marker_result = build_fanxiu_digitdoor_reward_marker_semantics_probe(lang_path=lang_path, export_root=root)
    _item_corner_path, item_corner_rows, item_corner_by_id = _load_item_corner_rows(
        root,
        lang_path=resolved_lang_path,
        lang_map=lang_map,
    )
    corner = item_corner_by_id.get(7, {})
    effect_name = str(corner.get("effName") or "")
    evidence_rows, effect_resource_present = _collect_digitdoor_reward_marker_ui_evidence(root, effect_name=effect_name)
    evidence_text = "\n".join(str(row.get("snippet") or "") for row in evidence_rows)
    categories = Counter(str(row.get("category") or "") for row in evidence_rows)
    stats = {
        "item_corner_count": len(item_corner_rows),
        "item_corner_7_show_type": corner.get("showType") if corner else "",
        "item_corner_7_eff_name": effect_name,
        "marker_extra_mark_rows": marker_result.get("stats", {}).get("extra_mark_rows", 0),
        "marker_negative_amount_rows": marker_result.get("stats", {}).get("negative_amount_rows", 0),
        "ui_evidence_count": len(evidence_rows),
        "ui_evidence_categories": dict(categories),
        "effect_resource_present": effect_resource_present,
    }
    verdict = {
        "item_corner_7_config_resolved": bool(corner) and effect_name == "pre_eff_ui_zongmenqifu_paomadeng",
        "item_corner_7_is_effect_only": _as_int(corner.get("showType")) == 2 if corner else False,
        "extra_mark_flows_from_reward_string_to_reward_result": bool(re.search(r"reward\.extraMark|GetItemIcon|extraMark\s*=", evidence_text)),
        "reward_item_consumes_data_extra_mark_for_display": bool(re.search(r"UpdateItemCorner\(signText,data\.extraMark\)|GameUtil\.UpdateItemCornet", evidence_text)),
        "display_uses_item_corner_config_not_quantity": bool(re.search(r"ConfigName\.Item_ItemCorner|ItemCornerShowType", evidence_text)),
        "effect_asset_present": effect_resource_present,
        "settlement_amount_still_requires_reward_results": bool(marker_result.get("verdict", {}).get("server_amount_still_requires_reward_results")),
    }

    output_dir = root / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    flow_tsv = output_dir / "lua_lscript_module_digitdoor_reward_marker_ui_flow.tsv"
    report_path = output_dir / "lua_lscript_module_digitdoor_reward_marker_ui_report.md"
    json_path = output_dir / "lua_lscript_module_digitdoor_reward_marker_ui_report.json"
    _write_tsv(flow_tsv, evidence_rows, ["source_file", "line", "category", "target", "snippet"])
    files = {
        "flow": str(flow_tsv),
        "marker_semantics": marker_result["files"]["markdown"],
        "markdown": str(report_path),
        "json": str(json_path),
    }
    _write_digitdoor_reward_marker_ui_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        corner=corner,
        evidence_rows=evidence_rows,
        files=files,
    )
    json_path.write_text(
        json.dumps(
            {
                "stats": stats,
                "verdict": verdict,
                "item_corner_7": corner,
                "samples": {
                    "evidence": evidence_rows[:160],
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


def _digitdoor_reward_result_resolution_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "apk_static_index"
    return {
        "items": output_dir / "lua_lscript_module_digitdoor_reward_result_resolution_items.tsv",
        "json": output_dir / "lua_lscript_module_digitdoor_reward_result_resolution_report.json",
    }


def _read_tsv_dicts(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _load_digitdoor_reward_result_resolution_files(root: Path) -> tuple[dict[str, Path], list[dict[str, str]], dict[str, Any]]:
    paths = _digitdoor_reward_result_resolution_file_paths(root)
    if not paths["items"].is_file() or not paths["json"].is_file():
        build_fanxiu_digitdoor_reward_result_resolution_probe(export_root=root)
    return paths, _read_tsv_dicts(paths["items"]), _read_json(paths["json"])


def _digitdoor_monster_refresh_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "parsed_configs" / "digitdoor_catalog"
    return {
        "levels": output_dir / "monster_refresh_levels.tsv",
        "refresh_points": output_dir / "monster_refresh_points.tsv",
        "monsters": output_dir / "monster_refresh_monsters.tsv",
        "skills": output_dir / "monster_refresh_skills.tsv",
        "json": output_dir / "monster_refresh_report.json",
    }


def _digitdoor_monster_refresh_point_value_projection_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "parsed_configs" / "digitdoor_catalog"
    return {
        "projections": output_dir / "monster_refresh_point_value_projection.tsv",
        "by_level": output_dir / "monster_refresh_point_value_projection_by_level.tsv",
        "json": output_dir / "monster_refresh_point_value_projection_report.json",
    }


def _digitdoor_monster_refresh_point_latent_field_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "parsed_configs" / "digitdoor_catalog"
    return {
        "fields": output_dir / "monster_refresh_point_latent_fields.tsv",
        "lua_hits": output_dir / "monster_refresh_point_latent_field_lua_hits.tsv",
        "json": output_dir / "monster_refresh_point_latent_field_report.json",
    }


def _digitdoor_monster_refresh_point_attribute_projection_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "parsed_configs" / "digitdoor_catalog"
    return {
        "projections": output_dir / "monster_refresh_point_attribute_projection.tsv",
        "field_summary": output_dir / "monster_refresh_point_attribute_projection_fields.tsv",
        "json": output_dir / "monster_refresh_point_attribute_projection_report.json",
    }


def _digitdoor_door_refresh_projection_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "parsed_configs" / "digitdoor_catalog"
    return {
        "projections": output_dir / "door_refresh_projection.tsv",
        "by_level": output_dir / "door_refresh_projection_by_level.tsv",
        "field_summary": output_dir / "door_refresh_projection_fields.tsv",
        "lua_hits": output_dir / "door_refresh_projection_lua_hits.tsv",
        "json": output_dir / "door_refresh_projection_report.json",
    }


def _digitdoor_door_gain_buff_flow_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "parsed_configs" / "digitdoor_catalog"
    return {
        "effects": output_dir / "door_gain_buff_effects.tsv",
        "flow_steps": output_dir / "door_gain_buff_flow_steps.tsv",
        "lua_hits": output_dir / "door_gain_buff_lua_hits.tsv",
        "json": output_dir / "door_gain_buff_flow_report.json",
    }


def _digitdoor_door_customized_type_semantics_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "parsed_configs" / "digitdoor_catalog"
    return {
        "types": output_dir / "door_customized_type_semantics.tsv",
        "lua_hits": output_dir / "door_customized_type_semantics_lua_hits.tsv",
        "json": output_dir / "door_customized_type_semantics_report.json",
    }


def _digitdoor_monster_skill_timeline_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "parsed_configs" / "digitdoor_catalog"
    return {
        "skill_links": output_dir / "monster_skill_timeline_links.tsv",
        "timelines": output_dir / "monster_skill_timeline_timelines.tsv",
        "effects": output_dir / "monster_skill_timeline_effects.tsv",
        "json": output_dir / "monster_skill_timeline_report.json",
    }


def _digitdoor_monster_effect_class_flow_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "parsed_configs" / "digitdoor_catalog"
    return {
        "classes": output_dir / "monster_effect_class_flow_classes.tsv",
        "functions": output_dir / "monster_effect_class_flow_functions.tsv",
        "steps": output_dir / "monster_effect_class_flow_steps.tsv",
        "json": output_dir / "monster_effect_class_flow_report.json",
    }


def _digitdoor_monster_skill_data_accessor_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "parsed_configs" / "digitdoor_catalog"
    return {
        "accessors": output_dir / "monster_skill_data_accessors.tsv",
        "effect_refs": output_dir / "monster_skill_data_accessor_effect_refs.tsv",
        "skill_values": output_dir / "monster_skill_data_accessor_skill_values.tsv",
        "json": output_dir / "monster_skill_data_accessor_report.json",
    }


def _digitdoor_monster_skill_value_projection_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "parsed_configs" / "digitdoor_catalog"
    return {
        "projections": output_dir / "monster_skill_value_projection.tsv",
        "by_skill": output_dir / "monster_skill_value_projection_by_skill.tsv",
        "json": output_dir / "monster_skill_value_projection_report.json",
    }


def _digitdoor_monster_skill_buff_link_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "parsed_configs" / "digitdoor_catalog"
    return {
        "links": output_dir / "monster_skill_buff_links.tsv",
        "types": output_dir / "monster_skill_buff_type_summary.tsv",
        "json": output_dir / "monster_skill_buff_link_report.json",
    }


def _digitdoor_monster_skill_buff_formula_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "parsed_configs" / "digitdoor_catalog"
    return {
        "projections": output_dir / "monster_skill_buff_formula_projection.tsv",
        "by_skill": output_dir / "monster_skill_buff_formula_by_skill.tsv",
        "json": output_dir / "monster_skill_buff_formula_projection_report.json",
    }


def _split_digitdoor_csv_text(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _split_digitdoor_pipe_text(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split("|") if item.strip()]


def _split_digitdoor_cell_values(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [part.strip() for part in re.split(r"[|,]", str(value)) if part.strip()]


def _join_digitdoor_unique_cell(values: list[Any]) -> str:
    return _pipe_join(_dedupe_preserve([value for value in values if value not in (None, "")]))


def _load_digitdoor_monster_skill_timeline_files(
    root: Path,
) -> tuple[dict[str, Path], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    paths = _digitdoor_monster_skill_timeline_file_paths(root)
    if (
        not paths["skill_links"].is_file()
        or not paths["timelines"].is_file()
        or not paths["effects"].is_file()
        or not paths["json"].is_file()
    ):
        build_fanxiu_digitdoor_monster_skill_timeline_probe(export_root=root)
    return (
        paths,
        _read_tsv_dicts(paths["skill_links"]),
        _read_tsv_dicts(paths["timelines"]),
        _read_tsv_dicts(paths["effects"]),
        _read_json(paths["json"]),
    )


def _load_digitdoor_monster_effect_class_flow_files(
    root: Path,
) -> tuple[dict[str, Path], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    paths = _digitdoor_monster_effect_class_flow_file_paths(root)
    if not paths["classes"].is_file() or not paths["functions"].is_file() or not paths["steps"].is_file() or not paths["json"].is_file():
        build_fanxiu_digitdoor_monster_effect_class_flow_probe(export_root=root)
    return (
        paths,
        _read_tsv_dicts(paths["classes"]),
        _read_tsv_dicts(paths["functions"]),
        _read_tsv_dicts(paths["steps"]),
        _read_json(paths["json"]),
    )


def _load_digitdoor_monster_skill_data_accessor_files(
    root: Path,
) -> tuple[dict[str, Path], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    paths = _digitdoor_monster_skill_data_accessor_file_paths(root)
    if not paths["accessors"].is_file() or not paths["effect_refs"].is_file() or not paths["skill_values"].is_file() or not paths["json"].is_file():
        build_fanxiu_digitdoor_monster_skill_data_accessor_probe(export_root=root)
    return (
        paths,
        _read_tsv_dicts(paths["accessors"]),
        _read_tsv_dicts(paths["effect_refs"]),
        _read_tsv_dicts(paths["skill_values"]),
        _read_json(paths["json"]),
    )


def _load_digitdoor_monster_skill_value_projection_files(
    root: Path,
) -> tuple[dict[str, Path], list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    paths = _digitdoor_monster_skill_value_projection_file_paths(root)
    if not paths["projections"].is_file() or not paths["by_skill"].is_file() or not paths["json"].is_file():
        build_fanxiu_digitdoor_monster_skill_value_projection_probe(export_root=root)
    return (
        paths,
        _read_tsv_dicts(paths["projections"]),
        _read_tsv_dicts(paths["by_skill"]),
        _read_json(paths["json"]),
    )


def _load_digitdoor_monster_skill_buff_link_files(
    root: Path,
) -> tuple[dict[str, Path], list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    paths = _digitdoor_monster_skill_buff_link_file_paths(root)
    if not paths["links"].is_file() or not paths["types"].is_file() or not paths["json"].is_file():
        build_fanxiu_digitdoor_monster_skill_buff_link_probe(export_root=root)
    return (
        paths,
        _read_tsv_dicts(paths["links"]),
        _read_tsv_dicts(paths["types"]),
        _read_json(paths["json"]),
    )


def _load_digitdoor_monster_skill_buff_formula_files(
    root: Path,
) -> tuple[dict[str, Path], list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    paths = _digitdoor_monster_skill_buff_formula_file_paths(root)
    if not paths["projections"].is_file() or not paths["by_skill"].is_file() or not paths["json"].is_file():
        build_fanxiu_digitdoor_monster_skill_buff_formula_probe(export_root=root)
    return (
        paths,
        _read_tsv_dicts(paths["projections"]),
        _read_tsv_dicts(paths["by_skill"]),
        _read_json(paths["json"]),
    )


def _compact_digitdoor_monster_effect_class_flow(row: dict[str, str] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "class_name": row.get("class_name") or "",
        "source_file": row.get("source_file") or "",
        "function_count": _as_int(row.get("function_count")) or 0,
        "flow_step_count": _as_int(row.get("flow_step_count")) or 0,
        "flow_categories": _split_digitdoor_pipe_text(row.get("flow_categories")),
        "flow_labels": _split_digitdoor_pipe_text(row.get("flow_labels")),
        "flow_hint": row.get("flow_hint") or "",
    }


def _compact_digitdoor_monster_skill_data_accessor(row: dict[str, str] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "class_name": row.get("class_name") or "",
        "function": row.get("function") or "",
        "accessor": row.get("accessor") or "",
        "config_field": row.get("config_field") or "",
        "source_data_class": row.get("source_data_class") or "",
        "transform": row.get("transform") or "",
    }


def _compact_digitdoor_monster_skill_value_projection(row: dict[str, str] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "field": row.get("field") or "",
        "raw_value": row.get("raw_value") or "",
        "projection": row.get("projection") or "",
        "formula": row.get("formula") or "",
        "meaning": row.get("meaning") or "",
        "runtime_slot": row.get("runtime_slot") or "",
    }


def _compact_digitdoor_monster_skill_buff_link(row: dict[str, str] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "skill_id": row.get("skill_id") or "",
        "buff_id": row.get("buff_id") or "",
        "buff_type": row.get("buff_type") or "",
        "buff_type_name": row.get("buff_type_name") or "",
        "buff_path": row.get("buff_path") or "",
        "target_type": row.get("target_type") or "",
        "target_type_name": row.get("target_type_name") or "",
        "trigger_type": row.get("trigger_type") or "",
        "trigger_type_name": row.get("trigger_type_name") or "",
        "duration": row.get("duration") or "",
        "interval": row.get("interval") or "",
        "eff_type": row.get("eff_type") or "",
        "plies_limit": row.get("plies_limit") or "",
        "damage": row.get("damage") or "",
        "add_attr": row.get("add_attr") or "",
        "shield": row.get("shield") or "",
        "slow_down": row.get("slow_down") or "",
        "passive": row.get("passive") or "",
        "buff_timeline_id": row.get("buff_timeline_id") or "",
        "runtime_hint": row.get("runtime_hint") or "",
    }


def _compact_digitdoor_monster_skill_buff_formula(row: dict[str, str] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "field": row.get("field") or "",
        "raw_value": row.get("raw_value") or "",
        "projection": row.get("projection") or "",
        "formula": row.get("formula") or "",
        "meaning": row.get("meaning") or "",
        "runtime_slot": row.get("runtime_slot") or "",
    }


def _compact_digitdoor_monster_skill_timeline_link(
    row: dict[str, str] | None,
    effect_flow_by_class: dict[str, dict[str, str]] | None = None,
    accessors_by_class: dict[str, list[dict[str, str]]] | None = None,
) -> dict[str, Any] | None:
    if not row:
        return None
    effect_classes = _split_digitdoor_pipe_text(row.get("effect_classes"))
    class_flows = [
        compact
        for class_name in effect_classes
        if (compact := _compact_digitdoor_monster_effect_class_flow((effect_flow_by_class or {}).get(class_name)))
    ]
    skill_data_accessors = [
        compact
        for class_name in effect_classes
        for accessor_row in (accessors_by_class or {}).get(class_name, [])
        if (compact := _compact_digitdoor_monster_skill_data_accessor(accessor_row))
    ]
    return {
        "skill_id": row.get("skill_id") or "",
        "timeline_id": row.get("timeline_id") or "",
        "missing_timeline_id": row.get("missing_timeline_id") or "",
        "sections": _split_digitdoor_pipe_text(row.get("sections")),
        "effect_classes": effect_classes,
        "effect_class_count": row.get("effect_class_count") or "",
        "timeline_files": _split_digitdoor_pipe_text(row.get("timeline_files")),
        "class_flows": class_flows,
        "skill_data_accessors": skill_data_accessors,
    }


def _load_digitdoor_monster_refresh_files(
    root: Path,
) -> tuple[dict[str, Path], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    paths = _digitdoor_monster_refresh_file_paths(root)
    needs_rebuild = (
        not paths["levels"].is_file()
        or not paths["refresh_points"].is_file()
        or not paths["monsters"].is_file()
        or not paths["skills"].is_file()
        or not paths["json"].is_file()
    )
    skill_rows = _read_tsv_dicts(paths["skills"]) if paths["skills"].is_file() else []
    if skill_rows and "type_name" not in skill_rows[0]:
        needs_rebuild = True
    if needs_rebuild:
        build_fanxiu_digitdoor_monster_refresh_probe(export_root=root)
        skill_rows = _read_tsv_dicts(paths["skills"])
    return (
        paths,
        _read_tsv_dicts(paths["levels"]),
        _read_tsv_dicts(paths["refresh_points"]),
        _read_tsv_dicts(paths["monsters"]),
        skill_rows,
        _read_json(paths["json"]),
    )


def _load_digitdoor_monster_refresh_point_value_projection_files(
    root: Path,
) -> tuple[dict[str, Path], list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    paths = _digitdoor_monster_refresh_point_value_projection_file_paths(root)
    if not paths["projections"].is_file() or not paths["by_level"].is_file() or not paths["json"].is_file():
        build_fanxiu_digitdoor_monster_refresh_point_value_projection_probe(export_root=root)
    return (
        paths,
        _read_tsv_dicts(paths["projections"]),
        _read_tsv_dicts(paths["by_level"]),
        _read_json(paths["json"]),
    )


def _load_digitdoor_monster_refresh_point_attribute_projection_files(
    root: Path,
) -> tuple[dict[str, Path], list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    paths = _digitdoor_monster_refresh_point_attribute_projection_file_paths(root)
    if not paths["projections"].is_file() or not paths["field_summary"].is_file() or not paths["json"].is_file():
        build_fanxiu_digitdoor_monster_refresh_point_attribute_projection_probe(export_root=root)
    return (
        paths,
        _read_tsv_dicts(paths["projections"]),
        _read_tsv_dicts(paths["field_summary"]),
        _read_json(paths["json"]),
    )


def _load_digitdoor_door_refresh_projection_files(
    root: Path,
) -> tuple[dict[str, Path], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    paths = _digitdoor_door_refresh_projection_file_paths(root)
    needs_rebuild = (
        not paths["projections"].is_file()
        or not paths["by_level"].is_file()
        or not paths["field_summary"].is_file()
        or not paths["json"].is_file()
    )
    projection_rows = _read_tsv_dicts(paths["projections"]) if paths["projections"].is_file() else []
    if projection_rows and "effect_pool_preview" not in projection_rows[0]:
        needs_rebuild = True
    if needs_rebuild:
        build_fanxiu_digitdoor_door_refresh_projection_probe(export_root=root)
        projection_rows = _read_tsv_dicts(paths["projections"])
    return (
        paths,
        projection_rows,
        _read_tsv_dicts(paths["by_level"]),
        _read_tsv_dicts(paths["field_summary"]),
        _read_json(paths["json"]),
    )


def _load_digitdoor_door_customized_type_semantics_files(
    root: Path,
) -> tuple[dict[str, Path], list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    paths = _digitdoor_door_customized_type_semantics_file_paths(root)
    needs_rebuild = not paths["types"].is_file() or not paths["lua_hits"].is_file() or not paths["json"].is_file()
    type_rows = _read_tsv_dicts(paths["types"]) if paths["types"].is_file() else []
    if type_rows and "semantic_label" not in type_rows[0]:
        needs_rebuild = True
    if needs_rebuild:
        build_fanxiu_digitdoor_door_customized_type_semantics_probe(export_root=root)
        type_rows = _read_tsv_dicts(paths["types"])
    return (
        paths,
        type_rows,
        _read_tsv_dicts(paths["lua_hits"]),
        _read_json(paths["json"]),
    )


def _compact_digitdoor_monster_refresh_point_value_projection(row: dict[str, str] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "field": row.get("field") or "",
        "raw_value": row.get("raw_value") or "",
        "projection": row.get("projection") or "",
        "formula": row.get("formula") or "",
        "meaning": row.get("meaning") or "",
        "runtime_slot": row.get("runtime_slot") or "",
    }


def _compact_digitdoor_monster_refresh_point_attribute_projection(row: dict[str, str] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "field": row.get("field") or "",
        "raw_value": row.get("raw_value") or "",
        "projection": row.get("projection") or "",
        "formula": row.get("formula") or "",
        "meaning": row.get("meaning") or "",
        "runtime_slot": row.get("runtime_slot") or "",
    }


def _compact_digitdoor_door_refresh_projection(row: dict[str, str] | None) -> dict[str, Any] | None:
    if not row:
        return None
    customized_values = _split_digitdoor_pipe_text(row.get("customized_type_values"))
    return {
        "point_id": row.get("point_id") or "",
        "level": row.get("level") or "",
        "name": row.get("name") or "",
        "side": row.get("side") or "",
        "side_label": row.get("side_label") or "",
        "start_refresh_time": row.get("start_refresh_time") or "",
        "timing_projection": row.get("timing_projection") or "",
        "door_type": row.get("door_type") or "",
        "customized_type_values": customized_values,
        "effect_pool_count": _as_int(row.get("effect_pool_count")) or 0,
        "effect_pool_ids": _split_digitdoor_pipe_text(row.get("effect_pool_ids")),
        "effect_pool_preview": row.get("effect_pool_preview") or "",
        "positive_effect_count": _as_int(row.get("positive_effect_count")) or 0,
        "negative_effect_count": _as_int(row.get("negative_effect_count")) or 0,
        "debuff_door_type": row.get("debuff_door_type") or "",
        "probability": row.get("probability") or "",
        "rate_list": _split_digitdoor_pipe_text(row.get("rate_list")),
        "spx_door_type": _split_digitdoor_pipe_text(row.get("spx_door_type")),
        "special_rule_projection": row.get("special_rule_projection") or "",
        "door_damage": row.get("door_damage") or "",
        "attack": row.get("attack") or "",
        "volume": row.get("volume") or "",
        "hp": row.get("hp") or "",
        "refresh_offset_dis": row.get("refresh_offset_dis") or "",
        "position_projection": row.get("position_projection") or "",
        "server_boundary": row.get("server_boundary") or "",
    }


def _compact_digitdoor_door_pool_semantic(
    row: dict[str, str] | None,
    *,
    source_field: str,
) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "customized_type": row.get("customized_type") or "",
        "semantic_label": row.get("semantic_label") or "",
        "static_role": row.get("static_role") or "",
        "source_field": source_field,
        "effect_count": _as_int(row.get("effect_count")) or 0,
        "effect_ids": _split_digitdoor_pipe_text(row.get("effect_ids")),
        "effect_shows": row.get("effect_shows") or "",
        "refresh_weight_summary": row.get("refresh_weight_summary") or "",
        "put_back_summary": row.get("put_back_summary") or "",
        "weighted_effect_count": _as_int(row.get("weighted_effect_count")) or 0,
        "put_back_reusable_count": _as_int(row.get("put_back_reusable_count")) or 0,
        "character_count": _as_int(row.get("character_count")) or 0,
        "character_ids": _split_digitdoor_pipe_text(row.get("character_ids")),
        "character_names": row.get("character_names") or "",
    }


def _digitdoor_door_pool_semantics_for_values(
    values: list[Any],
    semantic_by_type: dict[str, dict[str, str]],
    *,
    source_field: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        key = str(value or "").strip()
        if not key or (source_field, key) in seen:
            continue
        seen.add((source_field, key))
        compact = _compact_digitdoor_door_pool_semantic(semantic_by_type.get(key), source_field=source_field)
        if compact:
            result.append(compact)
    return result


def _digitdoor_door_pool_semantic_text(semantics: list[dict[str, Any]], *, include_source: bool = False) -> str:
    parts: list[str] = []
    for item in semantics:
        label = str(item.get("semantic_label") or "").strip()
        if not label:
            continue
        if include_source:
            source = str(item.get("source_field") or "").strip()
            label = f"{source}:{label}" if source else label
        parts.append(label)
    return " / ".join(_dedupe_preserve(parts))


def _digitdoor_value_count_summary(rows: list[dict[str, Any]], field: str, *, blank_label: str = "未填") -> str:
    counts: Counter[str] = Counter()
    for row in rows:
        raw = row.get(field)
        text = str(raw if raw not in (None, "") else blank_label).strip()
        if text:
            counts[text] += 1

    def sort_key(item: tuple[str, int]) -> tuple[int, int, str]:
        key, _count = item
        if key == blank_label:
            return (-1, -1, key)
        return (0, _sort_value(key), key)

    return " / ".join(f"{key}x{count}" for key, count in sorted(counts.items(), key=sort_key))


def _digitdoor_door_effect_runtime_hints(effect: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for skill in effect.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        enhance_effect = skill.get("enhance_effect")
        if not isinstance(enhance_effect, dict):
            continue
        buff = enhance_effect.get("buff")
        if isinstance(buff, dict):
            hints.extend(_digitdoor_add_attr_hints(buff.get("add_attr")))
            if _is_nonzero_effect_value(buff.get("shield")):
                hints.append(f"护盾 {_format_signed_percent_basis(buff.get('shield'))}")
            if _is_nonzero_effect_value(buff.get("slow_down")):
                hints.append(f"减速 {_format_signed_percent_basis(buff.get('slow_down'))}")
            if _is_nonzero_effect_value(buff.get("damage_raw")):
                hints.append(f"伤害 {_format_ratio(buff.get('damage_raw'))}")
        ext_release_count = _as_int(enhance_effect.get("ext_release_count"))
        if ext_release_count:
            hints.append(f"额外释放 {ext_release_count:+d}")
        ext_hit_num = _as_int(enhance_effect.get("ext_hit_num"))
        if ext_hit_num:
            hints.append(f"额外命中 {ext_hit_num:+d}")
        ext_penetrate = _as_int(enhance_effect.get("ext_penetrate"))
        if ext_penetrate:
            hints.append(f"穿透 {ext_penetrate:+d}")
        ext_atk_distance = _as_int(enhance_effect.get("ext_atk_distance"))
        if ext_atk_distance:
            hints.append(f"距离 {ext_atk_distance:+d}")
        mutex_timeline = _as_int(enhance_effect.get("mutex_timeline"))
        if mutex_timeline:
            hints.append(f"替换时间线 {mutex_timeline}")
    return _dedupe_preserve(hints)


def _compact_digitdoor_door_effect_option(
    effect: dict[str, Any] | None,
    *,
    char_card: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not effect:
        return None
    effect_id = effect.get("id") if effect.get("id") is not None else effect.get("effect_id")
    if effect_id in (None, ""):
        return None
    char_id = effect.get("char_id") if effect.get("char_id") is not None else effect.get("charId")
    char_name = str(effect.get("char_name") or (char_card or {}).get("name") or "").strip()
    effect_show = str(effect.get("effect_show") or effect.get("effectShow") or "").strip()
    show_tips = str(effect.get("show_tips") or effect.get("showTips") or "").strip()
    skill_ids = [item for item in _as_list(effect.get("skill_ids") or effect.get("skill")) if item not in (None, "")]
    skills = [item for item in effect.get("skills") or [] if isinstance(item, dict)]
    skill_names = _dedupe_preserve(
        [
            item.get("skill_name") or item.get("name") or item.get("id")
            for item in skills
            if item.get("skill_name") or item.get("name") or item.get("id")
        ]
    )
    effect_hints = _dedupe_preserve([str(item) for item in _as_list(effect.get("effect_hints")) if str(item or "").strip()])
    if not effect_hints:
        effect_hints = _digitdoor_door_effect_runtime_hints(effect)
    customized_type = effect.get("customized_type") if effect.get("customized_type") is not None else effect.get("customizedType")
    door_type = effect.get("door_type") if effect.get("door_type") is not None else effect.get("doorType")
    display_parts = [
        char_name,
        effect_show,
        f"{len(skill_ids)} 技能" if skill_ids else "",
    ]
    return {
        "effect_id": effect_id,
        "customized_type": customized_type,
        "door_type": door_type,
        "door_type_label": effect.get("door_type_label") or DOOR_TYPE_LABELS.get(_as_int(door_type) or 0, ""),
        "refresh_weights": effect.get("refresh_weights") if effect.get("refresh_weights") is not None else effect.get("refreshWeights"),
        "put_back": effect.get("put_back") if effect.get("put_back") is not None else effect.get("putBack"),
        "char_id": char_id,
        "char_name": char_name,
        "effect_show": effect_show,
        "show_tips": show_tips,
        "skill_ids": skill_ids,
        "skill_count": len(skill_ids),
        "skill_names": skill_names,
        "effect_hints": effect_hints,
        "effect_hint_preview": " / ".join(effect_hints[:8]),
        "display_text": " · ".join(part for part in display_parts if part),
    }


def _digitdoor_door_effect_options_by_id(
    *,
    cards_by_id: dict[str, dict[str, Any]] | None = None,
    global_door_effects: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for card in (cards_by_id or {}).values():
        if not isinstance(card, dict):
            continue
        for effect in card.get("door_effects") or []:
            if not isinstance(effect, dict):
                continue
            compact = _compact_digitdoor_door_effect_option(effect, char_card=card)
            if not compact:
                continue
            result.setdefault(str(compact["effect_id"]), compact)
    for effect in global_door_effects or []:
        if not isinstance(effect, dict):
            continue
        compact = _compact_digitdoor_door_effect_option(effect)
        if not compact:
            continue
        result.setdefault(str(compact["effect_id"]), compact)
    return result


def _digitdoor_door_effect_options_from_config(
    root: Path,
    *,
    cards_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    try:
        config_dir = _find_default_config_dir(root)
    except FanxiuResourceError:
        return {}
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    result: dict[str, dict[str, Any]] = {}
    for row in _parse_config_rows(config_dir, "SkillRefreshEffect", lang_path, lang_map):
        char_id = row.get("charId")
        compact = _compact_digitdoor_door_effect_option(row, char_card=(cards_by_id or {}).get(str(char_id)))
        if not compact:
            continue
        result[str(compact["effect_id"])] = compact
    return result


def _digitdoor_door_effect_options_for_point(
    compact: dict[str, Any],
    effect_options_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for effect_id in compact.get("effect_pool_ids") or []:
        key = str(effect_id or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        option = effect_options_by_id.get(key)
        if option:
            result.append(option)
    return result


def _digitdoor_door_effect_options_for_customized_type(
    customized_type: Any,
    semantic_by_type: dict[str, dict[str, str]],
    effect_options_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    key = str(customized_type or "").strip()
    if not key:
        return []
    semantic = semantic_by_type.get(key) or {}
    effect_ids = _split_digitdoor_pipe_text(semantic.get("effect_ids"))
    if not effect_ids:
        effect_ids = [
            str(option.get("effect_id"))
            for option in effect_options_by_id.values()
            if str(option.get("customized_type") or "").strip() == key
        ]
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for effect_id in effect_ids:
        effect_key = str(effect_id or "").strip()
        if not effect_key or effect_key in seen:
            continue
        seen.add(effect_key)
        option = effect_options_by_id.get(effect_key)
        if option:
            result.append(option)
    return result


def _digitdoor_door_effect_option_preview(options: list[dict[str, Any]], *, limit: int = 8) -> str:
    labels = _dedupe_preserve([item.get("display_text") or item.get("effect_show") for item in options if item.get("display_text") or item.get("effect_show")])
    return " / ".join(str(item) for item in labels[:limit])


def _digitdoor_door_merge_effect_options(*option_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for options in option_groups:
        for option in options:
            key = str(option.get("effect_id") or option.get("display_text") or option.get("effect_show") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(option)
    return result


def _digitdoor_door_effect_pool_summary(
    points: list[dict[str, Any]],
    semantic_by_type: dict[str, dict[str, str]],
    effect_options_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    pool_map: dict[str, dict[str, Any]] = {}

    def add_pool(
        customized_type: Any,
        *,
        source_field: str,
        source_label: str,
        point: dict[str, Any],
        effect_options: list[dict[str, Any]] | None = None,
        rate_text: str = "",
    ) -> None:
        key = str(customized_type or "").strip()
        if not key:
            return
        semantic = _compact_digitdoor_door_pool_semantic(semantic_by_type.get(key), source_field=source_field) or {}
        label = semantic.get("semantic_label") or f"customizedType {key}"
        item = pool_map.setdefault(
            key,
            {
                "customized_type": key,
                "semantic_label": label,
                "static_role": semantic.get("static_role") or "",
                "refresh_weight_summary": semantic.get("refresh_weight_summary") or "",
                "put_back_summary": semantic.get("put_back_summary") or "",
                "weighted_effect_count": semantic.get("weighted_effect_count") or 0,
                "put_back_reusable_count": semantic.get("put_back_reusable_count") or 0,
                "effect_count": 0,
                "effect_options": [],
                "source_fields": [],
                "source_labels": [],
                "rate_texts": [],
                "points": [],
                "point_count": 0,
                "point_time_preview": "",
                "effect_option_preview": "",
            },
        )
        if source_field and source_field not in item["source_fields"]:
            item["source_fields"].append(source_field)
        if source_label and source_label not in item["source_labels"]:
            item["source_labels"].append(source_label)
        if rate_text and rate_text not in item["rate_texts"]:
            item["rate_texts"].append(rate_text)
        point_id = str(point.get("point_id") or "")
        if point_id and all(str(row.get("point_id") or "") != point_id for row in item["points"]):
            item["points"].append(
                {
                    "point_id": point.get("point_id") or "",
                    "start_refresh_time": point.get("start_refresh_time") or "",
                    "timing_projection": point.get("timing_projection") or "",
                    "position_projection": point.get("position_projection") or "",
                }
            )
        options = effect_options if effect_options is not None else _digitdoor_door_effect_options_for_customized_type(key, semantic_by_type, effect_options_by_id)
        item["effect_options"] = _digitdoor_door_merge_effect_options(item["effect_options"], options)

    for point in points:
        for semantic in point.get("pool_semantics") or []:
            add_pool(
                semantic.get("customized_type"),
                source_field=semantic.get("source_field") or "customizedType",
                source_label="直接候选池",
                point=point,
            )
        for rule in point.get("special_rules") or []:
            if rule.get("kind") == "debuff_pool":
                add_pool(
                    rule.get("customized_type"),
                    source_field=rule.get("source_field") or "debuffDoorType",
                    source_label="负面替换池",
                    point=point,
                    effect_options=rule.get("effect_options") or [],
                )
            for option in rule.get("options") or []:
                add_pool(
                    option.get("customized_type"),
                    source_field=option.get("source_field") or "spxDoorType",
                    source_label="特殊替换池",
                    point=point,
                    effect_options=option.get("effect_options") or [],
                    rate_text=option.get("rate_text") or "",
                )

    result = []
    for item in pool_map.values():
        item["effect_count"] = len(item["effect_options"])
        item["point_count"] = len(item["points"])
        item["point_time_preview"] = " / ".join(
            _dedupe_preserve([f"{row.get('start_refresh_time')}s" for row in item["points"] if row.get("start_refresh_time")])[:12]
        )
        item["effect_option_preview"] = _digitdoor_door_effect_option_preview(item["effect_options"])
        result.append(item)
    result.sort(key=lambda row: (_sort_value(row.get("customized_type")), str(row.get("semantic_label") or "")))
    return result


def _digitdoor_door_special_rules(
    compact: dict[str, Any],
    semantic_by_type: dict[str, dict[str, str]],
    effect_options_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    rules: list[dict[str, Any]] = []
    parts: list[str] = []
    debuff_type = str(compact.get("debuff_door_type") or "").strip()
    if debuff_type:
        debuff_semantic = _compact_digitdoor_door_pool_semantic(
            semantic_by_type.get(debuff_type),
            source_field="debuffDoorType",
        )
        rule = {
            "kind": "debuff_pool",
            "customized_type": debuff_type,
            "semantic_label": (debuff_semantic or {}).get("semantic_label") or f"customizedType {debuff_type}",
            "source_field": "debuffDoorType",
        }
        effect_options = _digitdoor_door_effect_options_for_customized_type(debuff_type, semantic_by_type, effect_options_by_id)
        rule["effect_options"] = effect_options
        rule["effect_option_preview"] = _digitdoor_door_effect_option_preview(effect_options)
        rules.append(rule)
        parts.append(f"负面替换池：{rule['semantic_label']}")

    probability = _as_int(compact.get("probability"))
    spx_types = [str(item) for item in compact.get("spx_door_type") or [] if str(item or "").strip()]
    rates = [str(item) for item in compact.get("rate_list") or [] if str(item or "").strip()]
    if probability is not None and probability != 0:
        options: list[dict[str, Any]] = []
        option_parts: list[str] = []
        for index, spx_type in enumerate(spx_types):
            rate = rates[index] if index < len(rates) else ""
            semantic = _compact_digitdoor_door_pool_semantic(
                semantic_by_type.get(spx_type),
                source_field="spxDoorType",
            )
            label = (semantic or {}).get("semantic_label") or f"customizedType {spx_type}"
            option = {
                "customized_type": spx_type,
                "semantic_label": label,
                "rate": rate,
                "rate_text": _format_ratio(rate),
                "source_field": "spxDoorType",
            }
            effect_options = _digitdoor_door_effect_options_for_customized_type(spx_type, semantic_by_type, effect_options_by_id)
            option["effect_options"] = effect_options
            option["effect_option_preview"] = _digitdoor_door_effect_option_preview(effect_options)
            options.append(option)
            option_parts.append(f"{label} {option['rate_text']}".strip())
        rule = {
            "kind": "special_roll",
            "trigger_probability": probability,
            "trigger_probability_text": _format_ratio(probability),
            "options": options,
        }
        rules.append(rule)
        option_text = " / ".join(option_parts) if option_parts else "未列出替换池"
        parts.append(f"特殊池触发 {rule['trigger_probability_text']}：{option_text}")
    return rules, "；".join(parts)


def _digitdoor_door_refresh_detail(
    level_id: Any,
    root: Path,
    *,
    cards_by_id: dict[str, dict[str, Any]] | None = None,
    global_door_effects: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    paths, projection_rows, by_level_rows, _field_rows, report = _load_digitdoor_door_refresh_projection_files(root)
    _semantic_paths, semantic_rows, _semantic_hit_rows, semantic_report = _load_digitdoor_door_customized_type_semantics_files(root)
    semantic_by_type = {str(row.get("customized_type") or ""): row for row in semantic_rows if row.get("customized_type")}
    effect_options_by_id = _digitdoor_door_effect_options_by_id(cards_by_id=cards_by_id, global_door_effects=global_door_effects)
    for key, option in _digitdoor_door_effect_options_from_config(root, cards_by_id=cards_by_id).items():
        effect_options_by_id.setdefault(key, option)
    requested = str(level_id)
    points: list[dict[str, Any]] = []
    for row in projection_rows:
        if str(row.get("level") or "") != requested:
            continue
        compact = _compact_digitdoor_door_refresh_projection(row)
        if not compact:
            continue
        pool_semantics = _digitdoor_door_pool_semantics_for_values(
            compact.get("customized_type_values") or [],
            semantic_by_type,
            source_field="customizedType",
        )
        replacement_semantics = []
        replacement_semantics.extend(
            _digitdoor_door_pool_semantics_for_values(
                [compact.get("debuff_door_type")],
                semantic_by_type,
                source_field="debuffDoorType",
            )
        )
        replacement_semantics.extend(
            _digitdoor_door_pool_semantics_for_values(
                compact.get("spx_door_type") or [],
                semantic_by_type,
                source_field="spxDoorType",
            )
        )
        compact["pool_semantics"] = pool_semantics
        compact["pool_semantic_text"] = _digitdoor_door_pool_semantic_text(pool_semantics)
        compact["replacement_pool_semantics"] = replacement_semantics
        compact["replacement_pool_semantic_text"] = _digitdoor_door_pool_semantic_text(replacement_semantics, include_source=True)
        special_rules, special_rule_text = _digitdoor_door_special_rules(compact, semantic_by_type, effect_options_by_id)
        compact["special_rules"] = special_rules
        compact["special_rule_text"] = special_rule_text
        effect_options = _digitdoor_door_effect_options_for_point(compact, effect_options_by_id)
        compact["effect_options"] = effect_options
        compact["effect_option_preview"] = _digitdoor_door_effect_option_preview(effect_options)
        points.append(compact)
    points.sort(key=lambda row: (_sort_value(row.get("start_refresh_time")), _sort_value(row.get("point_id"))))
    level_row = next((row for row in by_level_rows if str(row.get("level") or "") == requested), None)
    if not points and not level_row:
        return None
    pool_semantic_preview = " / ".join(
        _dedupe_preserve([row.get("pool_semantic_text") for row in points if row.get("pool_semantic_text")])[:10]
    )
    replacement_pool_preview = " / ".join(
        _dedupe_preserve([row.get("replacement_pool_semantic_text") for row in points if row.get("replacement_pool_semantic_text")])[:10]
    )
    effect_option_preview = " / ".join(
        _dedupe_preserve([row.get("effect_option_preview") for row in points if row.get("effect_option_preview")])[:8]
    )
    effect_pools = _digitdoor_door_effect_pool_summary(points, semantic_by_type, effect_options_by_id)
    return {
        "summary": {
            "level": requested,
            "point_count": _as_int((level_row or {}).get("point_count")) or len(points),
            "first_refresh_time": (level_row or {}).get("first_refresh_time") or "",
            "last_refresh_time": (level_row or {}).get("last_refresh_time") or "",
            "side_counts": (level_row or {}).get("side_counts") or "",
            "customized_types": _split_digitdoor_pipe_text((level_row or {}).get("customized_types")),
            "effect_pool_preview": (level_row or {}).get("effect_pool_preview") or "",
            "pool_semantic_preview": pool_semantic_preview,
            "replacement_pool_preview": replacement_pool_preview,
            "effect_option_preview": effect_option_preview,
            "effect_pool_count": len(effect_pools),
            "special_rule_count": _as_int((level_row or {}).get("special_rule_count")) or 0,
            "max_hp": (level_row or {}).get("max_hp") or "",
            "confirmed": bool(report.get("confirmed")) and bool(semantic_report.get("confirmed", True)),
            "report_path": str(paths["json"]),
        },
        "effect_pools": effect_pools,
        "points": points,
    }


def _digitdoor_monster_refresh_detail(level_id: Any, root: Path) -> dict[str, Any] | None:
    paths, level_rows, point_rows, monster_rows, skill_rows, report = _load_digitdoor_monster_refresh_files(root)
    _point_projection_paths, point_projection_rows, _point_projection_level_rows, point_projection_report = _load_digitdoor_monster_refresh_point_value_projection_files(root)
    _point_attribute_paths, point_attribute_rows, _point_attribute_field_rows, point_attribute_report = _load_digitdoor_monster_refresh_point_attribute_projection_files(root)
    timeline_paths, skill_timeline_rows, _timeline_rows, _effect_rows, timeline_report = _load_digitdoor_monster_skill_timeline_files(root)
    _flow_paths, flow_class_rows, _flow_function_rows, _flow_step_rows, flow_report = _load_digitdoor_monster_effect_class_flow_files(root)
    _accessor_paths, _accessor_rows, accessor_effect_rows, _accessor_value_rows, accessor_report = _load_digitdoor_monster_skill_data_accessor_files(root)
    _value_projection_paths, value_projection_rows, _value_projection_skill_rows, value_projection_report = _load_digitdoor_monster_skill_value_projection_files(root)
    _buff_paths, skill_buff_rows, _buff_type_rows, buff_link_report = _load_digitdoor_monster_skill_buff_link_files(root)
    _buff_formula_paths, buff_formula_rows, _buff_formula_skill_rows, buff_formula_report = _load_digitdoor_monster_skill_buff_formula_files(root)
    requested = str(level_id)
    level_row = next((row for row in level_rows if str(row.get("level") or "") == requested), None)
    if not level_row:
        return None

    points = [row for row in point_rows if str(row.get("level") or "") == requested]
    points.sort(key=lambda row: (_sort_value(row.get("refresh_wave")), _sort_value(row.get("id"))))
    point_projections_by_id: dict[str, list[dict[str, str]]] = {}
    for row in point_projection_rows:
        point_id = row.get("point_id") or ""
        if point_id:
            point_projections_by_id.setdefault(point_id, []).append(row)
    point_attributes_by_id: dict[str, list[dict[str, str]]] = {}
    for row in point_attribute_rows:
        point_id = row.get("point_id") or ""
        if point_id:
            point_attributes_by_id.setdefault(point_id, []).append(row)
    enriched_points = [
        {
            **point,
            "value_projections": [
                projection
                for projection_row in point_projections_by_id.get(str(point.get("id") or ""), [])
                if (projection := _compact_digitdoor_monster_refresh_point_value_projection(projection_row))
            ],
            "attribute_projections": [
                projection
                for projection_row in point_attributes_by_id.get(str(point.get("id") or ""), [])
                if (projection := _compact_digitdoor_monster_refresh_point_attribute_projection(projection_row))
            ],
        }
        for point in points
    ]
    declared_ids = _split_digitdoor_csv_text(level_row.get("declared_monster_ids"))
    refresh_ids = _split_digitdoor_csv_text(level_row.get("refresh_monster_ids"))
    monster_ids = {item for item in [*declared_ids, *refresh_ids] if item}
    monster_by_id = {str(row.get("monster_id") or ""): row for row in monster_rows}
    skill_by_id = {str(row.get("id") or ""): row for row in skill_rows}
    skill_timeline_by_id = {str(row.get("skill_id") or ""): row for row in skill_timeline_rows}
    effect_flow_by_class = {str(row.get("class_name") or ""): row for row in flow_class_rows}
    accessors_by_class: dict[str, list[dict[str, str]]] = {}
    for row in accessor_effect_rows:
        class_name = row.get("class_name") or ""
        if class_name:
            accessors_by_class.setdefault(class_name, []).append(row)
    value_projections_by_skill: dict[str, list[dict[str, str]]] = {}
    for row in value_projection_rows:
        skill_id = row.get("skill_id") or ""
        if skill_id:
            value_projections_by_skill.setdefault(skill_id, []).append(row)
    buff_links_by_skill: dict[str, list[dict[str, str]]] = {}
    for row in skill_buff_rows:
        skill_id = row.get("skill_id") or ""
        if skill_id:
            buff_links_by_skill.setdefault(skill_id, []).append(row)
    buff_formula_by_skill_buff: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in buff_formula_rows:
        skill_id = row.get("skill_id") or ""
        buff_id = row.get("buff_id") or ""
        if skill_id and buff_id:
            buff_formula_by_skill_buff.setdefault((skill_id, buff_id), []).append(row)
    monsters: list[dict[str, Any]] = []
    for monster_id in sorted(monster_ids, key=_sort_value):
        monster = monster_by_id.get(monster_id)
        if not monster:
            continue
        skill_ids = _split_digitdoor_csv_text(monster.get("default_skill_ids"))
        default_skills = []
        for skill_id in skill_ids:
            skill = skill_by_id.get(skill_id)
            if not skill:
                continue
            default_skills.append(
                {
                    **skill,
                    "timeline_effect": _compact_digitdoor_monster_skill_timeline_link(
                        skill_timeline_by_id.get(skill_id),
                        effect_flow_by_class,
                        accessors_by_class,
                    ),
                    "value_projections": [
                        projection
                        for projection_row in value_projections_by_skill.get(skill_id, [])
                        if (projection := _compact_digitdoor_monster_skill_value_projection(projection_row))
                    ],
                    "buff_effects": [
                        {
                            **compact,
                            "formula_projections": [
                                formula
                                for formula_row in buff_formula_by_skill_buff.get((skill_id, str(compact.get("buff_id") or "")), [])
                                if (formula := _compact_digitdoor_monster_skill_buff_formula(formula_row))
                            ],
                        }
                        for buff_row in buff_links_by_skill.get(skill_id, [])
                        if (compact := _compact_digitdoor_monster_skill_buff_link(buff_row))
                    ],
                }
            )
        monsters.append(
            {
                **monster,
                "default_skills": default_skills,
            }
        )
    skill_ids = {
        skill_id
        for monster in monsters
        for skill_id in _split_digitdoor_csv_text(monster.get("default_skill_ids"))
    }
    skills = []
    for skill_id in sorted(skill_ids, key=_sort_value):
        skill = skill_by_id.get(skill_id)
        if not skill:
            continue
        skills.append(
            {
                **skill,
                "timeline_effect": _compact_digitdoor_monster_skill_timeline_link(
                    skill_timeline_by_id.get(skill_id),
                    effect_flow_by_class,
                    accessors_by_class,
                ),
                "value_projections": [
                    projection
                    for projection_row in value_projections_by_skill.get(skill_id, [])
                    if (projection := _compact_digitdoor_monster_skill_value_projection(projection_row))
                ],
                "buff_effects": [
                    {
                        **compact,
                        "formula_projections": [
                            formula
                            for formula_row in buff_formula_by_skill_buff.get((skill_id, str(compact.get("buff_id") or "")), [])
                            if (formula := _compact_digitdoor_monster_skill_buff_formula(formula_row))
                        ],
                    }
                    for buff_row in buff_links_by_skill.get(skill_id, [])
                    if (compact := _compact_digitdoor_monster_skill_buff_link(buff_row))
                ],
            }
        )
    return {
        "summary": {
            "level": level_row.get("level") or "",
            "name": level_row.get("name") or "",
            "stage": level_row.get("stage") or "",
            "layer": level_row.get("layer") or "",
            "sub_layer": level_row.get("sub_layer") or "",
            "declared_monster_ids": declared_ids,
            "declared_monster_names": _split_digitdoor_csv_text(level_row.get("declared_monster_names")),
            "declared_monster_unresolved_ids": _split_digitdoor_csv_text(level_row.get("declared_monster_unresolved_ids")),
            "refresh_point_count": level_row.get("refresh_point_count") or "",
            "wave_count": level_row.get("wave_count") or "",
            "first_wave": level_row.get("first_wave") or "",
            "last_wave": level_row.get("last_wave") or "",
            "refresh_monster_ids": refresh_ids,
            "refresh_monster_count": level_row.get("refresh_monster_count") or "",
            "max_attack": level_row.get("max_attack") or "",
            "max_hp": level_row.get("max_hp") or "",
            "confirmed": bool(report.get("confirmed")),
            "report_path": str(paths["json"]),
            "timeline_confirmed": bool(timeline_report.get("confirmed")),
            "timeline_report_path": str(timeline_paths["json"]),
            "effect_flow_confirmed": bool(flow_report.get("confirmed")),
            "skill_data_accessor_confirmed": bool(accessor_report.get("confirmed")),
            "refresh_point_value_projection_confirmed": bool(point_projection_report.get("confirmed")),
            "refresh_point_attribute_projection_confirmed": bool(point_attribute_report.get("confirmed")),
            "skill_buff_link_confirmed": bool(buff_link_report.get("confirmed")),
            "skill_buff_formula_confirmed": bool(buff_formula_report.get("confirmed")),
        },
        "points": enriched_points,
        "monsters": monsters,
        "skills": skills,
    }


def _digitdoor_reward_result_resolution_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("source_table") or ""),
        str(row.get("config_id") or ""),
        str(row.get("reward_index") or ""),
    )


def _attach_digitdoor_reward_result_resolution(
    reward_items: list[dict[str, Any]],
    *,
    source_table: str,
    config_id: Any,
    resolution_by_key: dict[tuple[str, str, str], dict[str, str]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for index, reward in enumerate(reward_items, start=1):
        resolution = resolution_by_key.get((source_table, str(config_id or ""), str(index)))
        if not resolution:
            enriched.append(reward)
            continue
        enriched.append(
            {
                **reward,
                "reward_result": {
                    "runtime_reward_type": resolution.get("runtime_reward_type") or "",
                    "runtime_reward_type_name": resolution.get("runtime_reward_type_name") or "",
                    "code": resolution.get("code") or "",
                    "amount": resolution.get("amount") or "",
                    "extra_mark": resolution.get("extra_mark") or "",
                    "extra_mark_name": resolution.get("extra_mark_name") or "",
                    "extra_mark_show_type": resolution.get("extra_mark_show_type") or "",
                    "extra_mark_eff_name": resolution.get("extra_mark_eff_name") or "",
                    "resolution_rule": resolution.get("resolution_rule") or "",
                    "note": resolution.get("note") or "",
                },
            }
        )
    return enriched


def search_fanxiu_digitdoor_level_configs(
    *,
    query: str = "",
    stage: str | int | None = "",
    limit: int = 80,
    offset: int = 0,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    runtime = load_fanxiu_digitdoor_runtime_index(export_root=export_root, rebuild_missing=rebuild_missing)
    catalog = runtime["catalog"]
    terms = tuple(item.strip().lower() for item in re.split(r"\s+", query or "") if item.strip())
    stage_text = str(stage or "").strip()
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for doc in runtime["level_search_docs"]:
        item = doc["item"]
        if stage_text and str(item.get("stage")) != stage_text:
            continue
        score = _score_doc(doc, terms)
        if score <= 0:
            continue
        scored.append((score, int(doc["index"]), item))
    if terms:
        scored.sort(key=lambda row: (-row[0], _sort_value(row[2].get("stage")), _sort_value(row[2].get("layer")), _sort_value(row[2].get("id"))))
    else:
        scored.sort(key=lambda row: (_sort_value(row[2].get("stage")), _sort_value(row[2].get("layer")), _sort_value(row[2].get("sub_layer")), _sort_value(row[2].get("id")), row[1]))
    page_rows = scored[offset: offset + limit]
    return {
        "query": query,
        "stage": stage_text,
        "limit": limit,
        "offset": offset,
        "total": len(scored),
        "stats": catalog.get("stats") or {},
        "catalog_path": catalog["catalog_path"],
        "stage_options": [
            _format_digitdoor_stage_option(
                item,
                sum(1 for level in runtime["levels"] if str(level.get("stage")) == str(item.get("id"))),
            )
            for item in runtime["pre_level_rewards"]
        ],
        "items": [_format_level_search_item(item, score) for score, _index, item in page_rows],
    }


def get_fanxiu_digitdoor_level_config(
    level_id: str | int,
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    requested = str(level_id)
    runtime = load_fanxiu_digitdoor_runtime_index(export_root=export_root, rebuild_missing=rebuild_missing)
    item = runtime["levels_by_id"].get(requested)
    if not item:
        raise FanxiuResourceError(f"没有找到数字门关卡：{level_id}")
    stage = next((stage for stage in runtime["pre_level_rewards"] if str(stage.get("id")) == str(item.get("stage"))), None)
    root = Path(runtime["catalog"]["export_root"])
    _paths, resolution_items, _report = _load_digitdoor_reward_result_resolution_files(root)
    resolution_by_key = {_digitdoor_reward_result_resolution_key(row): row for row in resolution_items}
    enriched_item = {
        **item,
        "reward_items": _attach_digitdoor_reward_result_resolution(
            [reward for reward in item.get("reward_items") or [] if isinstance(reward, dict)],
            source_table="Level",
            config_id=item.get("id"),
            resolution_by_key=resolution_by_key,
        ),
        "door_refresh": _digitdoor_door_refresh_detail(
            item.get("id"),
            root,
            cards_by_id=runtime["cards_by_id"],
            global_door_effects=runtime["catalog"].get("global_door_effects") or [],
        ),
        "monster_refresh": _digitdoor_monster_refresh_detail(item.get("id"), root),
    }
    enriched_stage = _enrich_digitdoor_stage_reward(stage, export_root=runtime["catalog"]["export_root"])
    if enriched_stage:
        enriched_stage = {
            **enriched_stage,
            "reward_items": _attach_digitdoor_reward_result_resolution(
                [reward for reward in enriched_stage.get("reward_items") or [] if isinstance(reward, dict)],
                source_table="DigitDoorPreLevelReward",
                config_id=enriched_stage.get("id"),
                resolution_by_key=resolution_by_key,
            ),
        }
    return {
        "catalog_path": runtime["catalog"]["catalog_path"],
        "stats": runtime["catalog"].get("stats") or {},
        "stage": enriched_stage,
        "item": enriched_item,
    }


_LUA_FUNCTION_RE = re.compile(r"^\s*function\s+([^\(]+)")


def _path_display(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _scan_lua_hits_for_topics(
    logic_dir: Path,
    root: Path,
    topic_terms: dict[str, tuple[str, ...]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(logic_dir.glob("*.lua"), key=lambda item: item.name.lower()):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            matched_topics = [
                topic
                for topic, terms in topic_terms.items()
                if any(term in line for term in terms)
            ]
            if not matched_topics:
                continue
            snippet = _WHITESPACE_RE.sub(" ", line.strip())
            for topic in matched_topics:
                rows.append(
                    {
                        "topic": topic,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "matched_terms": ",".join(term for term in topic_terms[topic] if term in line),
                        "snippet": snippet,
                    }
                )
    return rows


def _scan_lua_file_list_for_topics(
    files: list[Path],
    root: Path,
    topic_terms: dict[str, tuple[str, ...]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for path in sorted(files, key=lambda item: str(item).lower()):
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            matched_topics = [
                topic
                for topic, terms in topic_terms.items()
                if any(term in line for term in terms)
            ]
            if not matched_topics:
                continue
            snippet = _WHITESPACE_RE.sub(" ", line.strip())
            for topic in matched_topics:
                rows.append(
                    {
                        "topic": topic,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "matched_terms": ",".join(term for term in topic_terms[topic] if term in line),
                        "snippet": snippet,
                    }
                )
    return rows


def _scan_skill_enhance_effect_lua_hits(logic_dir: Path, root: Path) -> list[dict[str, Any]]:
    return _scan_lua_hits_for_topics(logic_dir, root, SKILL_ENHANCE_EFFECT_TOPIC_TERMS)


def _summarize_skill_enhance_effect_lua_hits(hit_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary_rows: list[dict[str, Any]] = []
    for topic, terms in SKILL_ENHANCE_EFFECT_TOPIC_TERMS.items():
        topic_hits = [row for row in hit_rows if row.get("topic") == topic]
        files = sorted({str(row.get("file") or "") for row in topic_hits if row.get("file")})
        summary_rows.append(
            {
                "topic": topic,
                "terms": ",".join(terms),
                "hit_count": len(topic_hits),
                "file_count": len(files),
                "sample_files": ", ".join(files[:6]),
                "note": SKILL_ENHANCE_EFFECT_FIELD_NOTES.get(topic, ""),
            }
        )
    return summary_rows


def _is_nonzero_effect_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_is_nonzero_effect_value(item) for item in value)
    parsed = _as_int(value)
    if parsed is not None:
        return parsed != 0
    return bool(value)


def _collect_skill_enhance_effect_config_rows(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in catalog.get("cards") or []:
        if not isinstance(card, dict):
            continue
        for effect in card.get("skill_enhance_effects") or []:
            if not isinstance(effect, dict):
                continue
            effect_key = str(effect.get("id") or "")
            if effect_key and effect_key in seen:
                continue
            if effect_key:
                seen.add(effect_key)
            rows.append(
                {
                    "id": effect.get("id"),
                    "char_id": effect.get("char_id"),
                    "char_name": card.get("name") or "",
                    "skill": effect.get("skill"),
                    "skill_type": effect.get("skill_type"),
                    "buff_id": effect.get("buff_id"),
                    "buff_type": (effect.get("buff") or {}).get("type") if isinstance(effect.get("buff"), dict) else "",
                    "ext_release_count": effect.get("ext_release_count"),
                    "ext_hit_num": effect.get("ext_hit_num"),
                    "ext_penetrate": effect.get("ext_penetrate"),
                    "ext_atk_distance": effect.get("ext_atk_distance"),
                    "mutex_timeline": effect.get("mutex_timeline"),
                }
            )
    return sorted(rows, key=lambda item: (_sort_value(item.get("char_id")), _sort_value(item.get("id"))))


def _summarize_skill_enhance_effect_config_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_names = ["buff_id", "mutex_timeline", "ext_release_count", "ext_penetrate", "ext_hit_num", "ext_atk_distance"]
    summary_rows: list[dict[str, Any]] = []
    for field in field_names:
        nonzero_rows = [row for row in rows if _is_nonzero_effect_value(row.get(field))]
        examples = [
            f"{row.get('id')}:{row.get('char_name')}={row.get(field)}"
            for row in nonzero_rows[:8]
        ]
        summary_rows.append(
            {
                "field": field,
                "nonzero_count": len(nonzero_rows),
                "example_count": len(examples),
                "examples": "; ".join(examples),
                "note": SKILL_ENHANCE_EFFECT_FIELD_NOTES.get(field, ""),
            }
        )
    return summary_rows


def _door_effect_skill_ref_stats(catalog: dict[str, Any]) -> dict[str, int]:
    all_effects: list[dict[str, Any]] = []
    all_effects.extend(item for item in catalog.get("global_door_effects") or [] if isinstance(item, dict))
    for card in catalog.get("cards") or []:
        if isinstance(card, dict):
            all_effects.extend(item for item in card.get("door_effects") or [] if isinstance(item, dict))
    ref_count = 0
    enhance_effect_ref_count = 0
    unique_skill_ids: set[str] = set()
    unique_enhance_effect_ids: set[str] = set()
    for door_effect in all_effects:
        for skill in door_effect.get("skills") or []:
            if not isinstance(skill, dict):
                continue
            ref_count += 1
            if skill.get("id") is not None:
                unique_skill_ids.add(str(skill["id"]))
            enhance_effect = skill.get("enhance_effect")
            if isinstance(enhance_effect, dict):
                enhance_effect_ref_count += 1
                if enhance_effect.get("id") is not None:
                    unique_enhance_effect_ids.add(str(enhance_effect["id"]))
    return {
        "door_skill_ref_count": ref_count,
        "door_skill_ref_unique_count": len(unique_skill_ids),
        "door_skill_enhance_effect_ref_count": enhance_effect_ref_count,
        "door_skill_enhance_effect_unique_count": len(unique_enhance_effect_ids),
    }


def build_fanxiu_digitdoor_skill_enhance_effect_usage_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    runtime = load_fanxiu_digitdoor_runtime_index(export_root=root, rebuild_missing=True)
    catalog = runtime["catalog"]
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    hit_tsv = out_dir / "skill_enhance_effect_usage_lua_hits.tsv"
    topic_tsv = out_dir / "skill_enhance_effect_usage_topics.tsv"
    config_tsv = out_dir / "skill_enhance_effect_usage_config_examples.tsv"
    field_tsv = out_dir / "skill_enhance_effect_usage_fields.tsv"
    report_path = out_dir / "skill_enhance_effect_usage_report.md"

    hit_rows = _scan_skill_enhance_effect_lua_hits(logic_dir, root)
    topic_rows = _summarize_skill_enhance_effect_lua_hits(hit_rows)
    config_rows = _collect_skill_enhance_effect_config_rows(catalog)
    field_rows = _summarize_skill_enhance_effect_config_rows(config_rows)
    door_stats = _door_effect_skill_ref_stats(catalog)
    topic_counts = {row["topic"]: row["hit_count"] for row in topic_rows}
    field_counts = {row["field"]: row["nonzero_count"] for row in field_rows}
    required_topics = ["door_skill_to_effect", "role_effect_counter", "battle_apply", "base_apply"]
    confirmed = all(topic_counts.get(topic, 0) > 0 for topic in required_topics) and bool(config_rows)

    _write_tsv(
        hit_tsv,
        hit_rows,
        ["topic", "file", "line", "function", "matched_terms", "snippet"],
    )
    _write_tsv(
        topic_tsv,
        topic_rows,
        ["topic", "terms", "hit_count", "file_count", "sample_files", "note"],
    )
    _write_tsv(
        config_tsv,
        config_rows,
        [
            "id",
            "char_id",
            "char_name",
            "skill",
            "skill_type",
            "buff_id",
            "buff_type",
            "ext_release_count",
            "ext_hit_num",
            "ext_penetrate",
            "ext_atk_distance",
            "mutex_timeline",
        ],
    )
    _write_tsv(
        field_tsv,
        field_rows,
        ["field", "nonzero_count", "example_count", "examples", "note"],
    )

    report_lines = [
        "# DigitDoor SkillEnhanceEffect 使用链路",
        "",
        f"- Lua 逻辑目录：`{logic_dir}`",
        f"- Catalog：`{runtime['catalog']['catalog_path']}`",
        f"- SkillEnhanceEffect 配置行：{len(config_rows)}",
        f"- Lua 命中行：{len(hit_rows)}",
        f"- 门效果技能引用：{door_stats['door_skill_ref_unique_count']} unique；其中增强效果引用 {door_stats['door_skill_enhance_effect_unique_count']} unique。",
        "",
        "## 当前结论",
        "",
        "- `SkillRefreshEffect.skill` 和 `SkillEnhanceEffect.id` 是同一命名空间；门效果不是直接指向展示技能，而是投放一个或多个技能增强效果。",
        "- `DigitDoorData:UpdateRoleSkillAttrList(id)` 维护门效果触发次数；`DigitDoorFightComponent:UpdateDigitDoorSkillInBattle` 读取次数增量后展开 `SkillRefreshEffect.skill`。",
        "- 展开后的 `SkillEnhanceEffect` 会进入 `SkillActor:UpdateSkill` / `DigitDoorBaseSkill:UpdateStrengthEffect`，再落到技能数据类和效果类。",
        "- 这条链仍属于客户端表现和战斗模拟逻辑。结算/奖励边界仍以已整理的 socket 协议报告为准，不把它理解成可本地改数值的服务器事实。",
        "",
        "## 字段语义",
        "",
        "| 字段 | 非零配置数 | 逆向语义 |",
        "| --- | ---: | --- |",
    ]
    for row in field_rows:
        report_lines.append(f"| `{row['field']}` | {row['nonzero_count']} | {row['note']} |")
    report_lines.extend(
        [
            "",
            "## Lua 证据主题",
            "",
            "| 主题 | 命中行 | 文件数 | 关键词 |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    for row in topic_rows:
        report_lines.append(f"| `{row['topic']}` | {row['hit_count']} | {row['file_count']} | `{row['terms']}` |")
    report_lines.extend(
        [
            "",
            "## 输出文件",
            "",
            f"- `skill_enhance_effect_usage_lua_hits.tsv`：逐行 Lua 证据。",
            f"- `skill_enhance_effect_usage_topics.tsv`：按主题聚合的 Lua 命中。",
            f"- `skill_enhance_effect_usage_config_examples.tsv`：每个 SkillEnhanceEffect 的关键字段样例。",
            f"- `skill_enhance_effect_usage_fields.tsv`：按字段统计的非零配置和语义说明。",
        ]
    )
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    return {
        "confirmed": confirmed,
        "output_dir": str(out_dir),
        "counts": {
            "config_effect_rows": len(config_rows),
            "lua_hit_rows": len(hit_rows),
            "topics": topic_counts,
            "fields": field_counts,
            **door_stats,
        },
        "files": {
            "report": str(report_path),
            "lua_hits_tsv": str(hit_tsv),
            "topics_tsv": str(topic_tsv),
            "config_examples_tsv": str(config_tsv),
            "fields_tsv": str(field_tsv),
        },
    }


def _digitdoor_skill_enhance_application_scan_files(root: Path, logic_dir: Path) -> list[Path]:
    files = list(logic_dir.glob("*.lua"))
    patterns = [
        "by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorReadyFight.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorUpLevel.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorUpLevel.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/DDPartnerVO.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/DDFightPartnerVO.lua",
        "by_source/lscripts/gamesystem/game/headui_*/text_assets/DigitDoorPartnerHeadUI.lua",
    ]
    for pattern in patterns:
        files.extend(path for path in root.glob(pattern) if path.is_file())
    return files


def _skill_enhance_effect_runtime_fields(row: dict[str, Any] | None) -> list[str]:
    if not row:
        return []
    fields = []
    for raw, label in [
        ("buffId", "buffId"),
        ("mutexTimeline", "mutexTimeline"),
        ("extReleaseCount", "extReleaseCount"),
        ("extPenetrate", "extPenetrate"),
        ("extHitNum", "extHitNum"),
        ("extAtkDistance", "extAtkDistance"),
        ("extCd", "extCd"),
        ("relatedSkillId", "relatedSkillId"),
        ("buffEffect", "buffEffect"),
    ]:
        if _is_nonzero_effect_value(row.get(raw)):
            fields.append(label)
    return fields


def _skill_enhance_application_hint(enhance: dict[str, Any], linked_effect: dict[str, Any] | None) -> str:
    effect_id = enhance.get("effect_id")
    if not effect_id:
        return "招募/基础节点；当前配置无 effectId。"
    if linked_effect:
        fields = ", ".join(_skill_enhance_effect_runtime_fields(linked_effect)) or "no nonzero runtime field"
        return f"effectId 直接命中 SkillEnhanceEffect；运行字段：{fields}。"
    return "effectId 未直接命中 SkillEnhanceEffect；当前静态证据不足以把它当作战斗效果配置 id。"


def _digitdoor_skill_enhance_application_rows(
    groups: list[dict[str, Any]],
    effect_by_id: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    duplicate_counter: Counter[str] = Counter()
    for group in groups:
        for enhance in group.get("enhances") or []:
            effect_id = _as_int(enhance.get("effect_id"))
            if effect_id is not None:
                duplicate_counter[str(effect_id)] += 1
    for group in groups:
        for enhance in group.get("enhances") or []:
            enhance_id = _as_int(enhance.get("id"))
            effect_id = _as_int(enhance.get("effect_id"))
            linked_effect = effect_by_id.get(effect_id) if effect_id is not None else None
            fields = _skill_enhance_effect_runtime_fields(linked_effect)
            rows.append(
                {
                    "enhance_id": enhance.get("id"),
                    "group_char_id": group.get("char_id"),
                    "group_name": group.get("name") or "",
                    "enhance_name": enhance.get("name") or "",
                    "type_label": enhance.get("type_label") or "",
                    "quality_label": enhance.get("quality_label") or "",
                    "description": enhance.get("description_plain") or "",
                    "condition_text": _digitdoor_condition_projection("condition", enhance.get("condition_raw")),
                    "effect_id": effect_id if effect_id is not None else "",
                    "effect_id_same_as_enhance_id": "yes" if effect_id is not None and enhance_id == effect_id else "no",
                    "effect_id_duplicate_count": duplicate_counter.get(str(effect_id), 0) if effect_id is not None else 0,
                    "effect_id_matches_skill_enhance_effect": "yes" if linked_effect else "no",
                    "linked_effect_char_id": linked_effect.get("charId") if linked_effect else "",
                    "linked_effect_skill": linked_effect.get("skill") if linked_effect else "",
                    "linked_effect_skill_type": linked_effect.get("skillType") if linked_effect else "",
                    "linked_effect_buff_id": linked_effect.get("buffId") if linked_effect else "",
                    "linked_effect_runtime_fields": ",".join(fields),
                    "application_hint": _skill_enhance_application_hint(enhance, linked_effect),
                }
            )
    stats = {
        "enhance_count": len(rows),
        "effect_id_non_empty_count": sum(1 for row in rows if row.get("effect_id") not in ("", None)),
        "effect_id_same_as_enhance_id_count": sum(1 for row in rows if row.get("effect_id_same_as_enhance_id") == "yes"),
        "effect_id_direct_effect_match_count": sum(1 for row in rows if row.get("effect_id_matches_skill_enhance_effect") == "yes"),
        "effect_id_no_direct_effect_match_count": sum(
            1
            for row in rows
            if row.get("effect_id") not in ("", None) and row.get("effect_id_matches_skill_enhance_effect") != "yes"
        ),
        "recruit_or_empty_effect_id_count": sum(1 for row in rows if row.get("effect_id") in ("", None)),
        "duplicated_effect_id_count": sum(1 for _, count in duplicate_counter.items() if count > 1),
    }
    return rows, stats


def _write_digitdoor_skill_enhance_application_markdown(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    hit_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    topic_counts: dict[str, int],
    config_dir: Path,
    logic_dir: Path,
) -> None:
    direct_rows = [row for row in rows if row.get("effect_id_matches_skill_enhance_effect") == "yes"]
    no_direct_rows = [
        row
        for row in rows
        if row.get("effect_id") not in ("", None) and row.get("effect_id_matches_skill_enhance_effect") != "yes"
    ]
    lines = [
        "# DigitDoor SkillEnhance application boundary",
        "",
        "Static read-only audit for how `SkillEnhance` rows relate to visible runtime application paths.",
        "",
        f"- Config dir: `{config_dir}`",
        f"- Logic dir: `{logic_dir}`",
        "",
        "## Stats",
        "",
    ]
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            "- `SkillEnhance.condition` is the visible rogue-enhance prerequisite/mutex/level graph.",
            "- `SkillEnhance` is visibly used for names/description display through `DigitDoor_SkillEnhance` lookups.",
            "- Most non-empty `SkillEnhance.effectId` values do not directly resolve to `SkillEnhanceEffect.id`; only direct matches are listed below.",
            "- The visible battle-effect path remains `SkillRefreshEffect.skill -> SkillEnhanceEffect -> SkillActor:UpdateSkill -> DigitDoorBaseSkill:UpdateStrengthEffect`.",
            "- Treat unmatched `effectId` values as unproven static ids until a runtime sample or deeper server/native evidence explains the mapping.",
            "",
            "## Lua Topics",
            "",
            "| Topic | Hits |",
            "| --- | ---: |",
        ]
    )
    for topic in SKILL_ENHANCE_APPLICATION_TOPIC_TERMS:
        lines.append(f"| `{topic}` | {topic_counts.get(topic, 0)} |")
    lines.extend(["", "## Direct `effectId -> SkillEnhanceEffect` Samples", ""])
    for row in direct_rows[:40]:
        lines.append(
            f"- `{row.get('enhance_id')}` `{row.get('enhance_name')}` effectId `{row.get('effect_id')}` -> char `{row.get('linked_effect_char_id')}`, skill `{row.get('linked_effect_skill')}`, fields `{row.get('linked_effect_runtime_fields') or '-'}`"
        )
    if not direct_rows:
        lines.append("- No direct matches.")
    lines.extend(["", "## Non-direct `effectId` Samples", ""])
    for row in no_direct_rows[:40]:
        lines.append(
            f"- `{row.get('enhance_id')}` `{row.get('enhance_name')}` effectId `{row.get('effect_id')}`: {row.get('application_hint')}"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This report documents static client/resource evidence only.",
            "- It must not be used as guidance for injection, APK modification, or bypassing server authority.",
            "- If exact live selection/application is required, collect a privacy-filtered `SM_DigitDoorReadyFight.skillList` sample and compare it with this TSV.",
            "",
            "## Outputs",
            "",
            "- `skill_enhance_application_nodes.tsv`: per `SkillEnhance` row mapping/audit.",
            "- `skill_enhance_application_lua_hits.tsv`: visible Lua evidence lines.",
            "- `skill_enhance_application_report.md`: this summary.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_skill_enhance_application_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    digitdoor_logic_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None
    runtime = load_fanxiu_digitdoor_runtime_index(export_root=root, rebuild_missing=True)
    effect_rows = _parse_config_rows(config_dir, "SkillEnhanceEffect", resolved_lang_path, lang_map)
    effect_by_id = {
        parsed: row
        for row in effect_rows
        if (parsed := _as_int(row.get("id"))) is not None
    }
    groups = [group for group in runtime["catalog"].get("custom_enhance_groups") or [] if isinstance(group, dict)]
    app_rows, stats = _digitdoor_skill_enhance_application_rows(groups, effect_by_id)

    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    nodes_tsv = out_dir / "skill_enhance_application_nodes.tsv"
    hits_tsv = out_dir / "skill_enhance_application_lua_hits.tsv"
    report_path = out_dir / "skill_enhance_application_report.md"

    scan_files = _digitdoor_skill_enhance_application_scan_files(root, logic_dir)
    hit_rows = _scan_lua_file_list_for_topics(scan_files, root, SKILL_ENHANCE_APPLICATION_TOPIC_TERMS)
    topic_counts = Counter(str(row.get("topic") or "") for row in hit_rows)
    visible_direct_effect_id_reader_found = topic_counts.get("skill_enhance_effect_id_direct_read", 0) > 0
    confirmed = bool(app_rows) and topic_counts.get("skill_enhance_display_lookup", 0) > 0 and topic_counts.get("skill_enhance_effect_apply", 0) > 0

    _write_tsv(
        nodes_tsv,
        app_rows,
        [
            "enhance_id",
            "group_char_id",
            "group_name",
            "enhance_name",
            "type_label",
            "quality_label",
            "description",
            "condition_text",
            "effect_id",
            "effect_id_same_as_enhance_id",
            "effect_id_duplicate_count",
            "effect_id_matches_skill_enhance_effect",
            "linked_effect_char_id",
            "linked_effect_skill",
            "linked_effect_skill_type",
            "linked_effect_buff_id",
            "linked_effect_runtime_fields",
            "application_hint",
        ],
    )
    _write_tsv(
        hits_tsv,
        hit_rows,
        ["topic", "file", "line", "function", "matched_terms", "snippet"],
    )
    _write_digitdoor_skill_enhance_application_markdown(
        report_path,
        rows=app_rows,
        hit_rows=hit_rows,
        stats=stats,
        topic_counts=dict(topic_counts),
        config_dir=config_dir,
        logic_dir=logic_dir,
    )

    return {
        "confirmed": confirmed,
        "output_dir": str(out_dir),
        "stats": stats,
        "topic_counts": dict(topic_counts),
        "verdict": {
            "most_effect_ids_are_not_direct_skill_enhance_effect_refs": stats["effect_id_no_direct_effect_match_count"] > stats["effect_id_direct_effect_match_count"],
            "visible_skill_enhance_effect_id_reader_found": visible_direct_effect_id_reader_found,
            "visible_skill_enhance_display_lookup_found": topic_counts.get("skill_enhance_display_lookup", 0) > 0,
            "visible_skill_enhance_effect_apply_found": topic_counts.get("skill_enhance_effect_apply", 0) > 0,
        },
        "files": {
            "markdown": str(report_path),
            "nodes": str(nodes_tsv),
            "lua_hits": str(hits_tsv),
        },
    }


def _skill_enhance_effect_id_namespace_label(
    *,
    has_skill_enhance_effect: bool,
    has_character_skill: bool,
    has_skill_enhance: bool,
    has_any_config: bool,
) -> str:
    if has_skill_enhance_effect:
        return "direct_skill_enhance_effect"
    if has_character_skill:
        return "character_skill_candidate"
    if has_skill_enhance:
        return "skill_enhance_alias_or_self"
    if has_any_config:
        return "other_config_collision"
    return "unresolved"


def _skill_enhance_effect_id_namespace_note(label: str) -> str:
    notes = {
        "direct_skill_enhance_effect": "effectId directly resolves to SkillEnhanceEffect.id; this is a plausible runtime effect config candidate.",
        "character_skill_candidate": "effectId resolves to CharacterSkillInfo/CharacterSkillShow; treat as skill config/display id evidence, not direct SkillEnhanceEffect.",
        "skill_enhance_alias_or_self": "effectId only resolves to SkillEnhance rows among trusted namespaces; likely alias/self/branch id, not proven runtime effect config.",
        "other_config_collision": "effectId collides with other config ids only; this may be an id-range collision and needs more evidence.",
        "unresolved": "effectId does not resolve in trusted DigitDoor namespaces scanned here.",
    }
    return notes.get(label, "")


def _digitdoor_name_from_row(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    return str(row.get("name") or row.get("skillName") or row.get("effectShow") or row.get("des") or "")


def _digitdoor_skill_enhance_effect_id_namespace_rows(
    groups: list[dict[str, Any]],
    *,
    config_index: dict[int, list[dict[str, Any]]],
    skill_info_by_id: dict[int, dict[str, Any]],
    skill_show_by_id: dict[int, dict[str, Any]],
    skill_enhance_effect_by_id: dict[int, dict[str, Any]],
    skill_enhance_by_id: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    namespace_counter: Counter[str] = Counter()
    label_counter: Counter[str] = Counter()
    for group in groups:
        for enhance in group.get("enhances") or []:
            effect_id = _as_int(enhance.get("effect_id"))
            if effect_id is None:
                continue
            config_hits = config_index.get(effect_id) or []
            namespace_hits = sorted({str(hit.get("table") or "") for hit in config_hits if hit.get("table")})
            for namespace in namespace_hits:
                namespace_counter[namespace] += 1
            skill_info = skill_info_by_id.get(effect_id)
            skill_show = skill_show_by_id.get(effect_id)
            skill_enhance_effect = skill_enhance_effect_by_id.get(effect_id)
            skill_enhance = skill_enhance_by_id.get(effect_id)
            label = _skill_enhance_effect_id_namespace_label(
                has_skill_enhance_effect=bool(skill_enhance_effect),
                has_character_skill=bool(skill_info or skill_show),
                has_skill_enhance=bool(skill_enhance),
                has_any_config=bool(config_hits),
            )
            label_counter[label] += 1
            rows.append(
                {
                    "enhance_id": enhance.get("id"),
                    "group_char_id": group.get("char_id"),
                    "group_name": group.get("name") or "",
                    "enhance_name": enhance.get("name") or "",
                    "description": enhance.get("description_plain") or "",
                    "condition_text": _digitdoor_condition_projection("condition", enhance.get("condition_raw")),
                    "effect_id": effect_id,
                    "primary_namespace": label,
                    "namespace_note": _skill_enhance_effect_id_namespace_note(label),
                    "namespace_hits": ",".join(namespace_hits),
                    "character_skill_info_hit": "yes" if skill_info else "no",
                    "character_skill_info_char_id": skill_info.get("charId") if skill_info else "",
                    "character_skill_show_hit": "yes" if skill_show else "no",
                    "character_skill_show_partner_id": skill_show.get("partnerId") if skill_show else "",
                    "character_skill_show_name": _digitdoor_name_from_row(skill_show),
                    "skill_enhance_effect_hit": "yes" if skill_enhance_effect else "no",
                    "skill_enhance_effect_char_id": skill_enhance_effect.get("charId") if skill_enhance_effect else "",
                    "skill_enhance_effect_skill": skill_enhance_effect.get("skill") if skill_enhance_effect else "",
                    "skill_enhance_effect_buff_id": skill_enhance_effect.get("buffId") if skill_enhance_effect else "",
                    "skill_enhance_hit": "yes" if skill_enhance else "no",
                    "skill_enhance_hit_name": _digitdoor_name_from_row(skill_enhance),
                    "same_as_enhance_id": "yes" if effect_id == _as_int(enhance.get("id")) else "no",
                    "config_hit_count": len(config_hits),
                    "sample_config_hits": " / ".join(
                        f"{hit.get('table')}:{hit.get('name') or hit.get('id')}"
                        for hit in config_hits[:10]
                    ),
                }
            )
    summary_rows = [
        {"kind": "primary_namespace", "key": key, "count": value}
        for key, value in sorted(label_counter.items(), key=lambda item: (-item[1], item[0]))
    ]
    summary_rows.extend(
        {"kind": "config_table_hit", "key": key, "count": value}
        for key, value in sorted(namespace_counter.items(), key=lambda item: (-item[1], item[0]))[:40]
    )
    stats = {
        "effect_id_row_count": len(rows),
        "unique_effect_id_count": len({row.get("effect_id") for row in rows}),
        "character_skill_candidate_count": label_counter.get("character_skill_candidate", 0),
        "direct_skill_enhance_effect_count": label_counter.get("direct_skill_enhance_effect", 0),
        "skill_enhance_alias_or_self_count": label_counter.get("skill_enhance_alias_or_self", 0),
        "other_config_collision_count": label_counter.get("other_config_collision", 0),
        "unresolved_count": label_counter.get("unresolved", 0),
        "character_skill_info_hit_count": sum(1 for row in rows if row.get("character_skill_info_hit") == "yes"),
        "character_skill_show_hit_count": sum(1 for row in rows if row.get("character_skill_show_hit") == "yes"),
        "skill_enhance_hit_count": sum(1 for row in rows if row.get("skill_enhance_hit") == "yes"),
        "same_as_enhance_id_count": sum(1 for row in rows if row.get("same_as_enhance_id") == "yes"),
    }
    return rows, summary_rows, stats


def _write_digitdoor_skill_enhance_effect_id_namespace_markdown(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    config_dir: Path,
) -> None:
    lines = [
        "# DigitDoor SkillEnhance effectId namespace audit",
        "",
        "Static read-only namespace audit for `SkillEnhance.effectId`.",
        "",
        f"- Config dir: `{config_dir}`",
        "",
        "## Stats",
        "",
    ]
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            "- `SkillEnhance.effectId` is not a single clean namespace in the visible static surface.",
            "- Only rows classified as `direct_skill_enhance_effect` resolve directly to `SkillEnhanceEffect.id`.",
            "- The dominant useful hit is `CharacterSkillInfo/CharacterSkillShow`, which suggests many `effectId` values are skill-config/display ids or branch markers rather than runtime effect rows.",
            "- Rows classified as `skill_enhance_alias_or_self` resolve only to other `SkillEnhance` ids among trusted namespaces.",
            "- This audit refines the earlier application-boundary report and should be read together with `skill_enhance_application_report.md`.",
            "",
            "## Primary Namespace Summary",
            "",
            "| Key | Count |",
            "| --- | ---: |",
        ]
    )
    for row in summary_rows:
        if row.get("kind") == "primary_namespace":
            lines.append(f"| `{row.get('key')}` | {row.get('count')} |")
    lines.extend(["", "## Direct SkillEnhanceEffect Rows", ""])
    for row in [item for item in rows if item.get("primary_namespace") == "direct_skill_enhance_effect"][:80]:
        lines.append(
            f"- `{row.get('enhance_id')}` `{row.get('enhance_name')}` -> effectId `{row.get('effect_id')}` / skill `{row.get('skill_enhance_effect_skill')}` / buff `{row.get('skill_enhance_effect_buff_id')}` / show `{row.get('character_skill_show_name')}`"
        )
    lines.extend(["", "## Character Skill Candidate Samples", ""])
    for row in [item for item in rows if item.get("primary_namespace") == "character_skill_candidate"][:40]:
        lines.append(
            f"- `{row.get('enhance_id')}` `{row.get('enhance_name')}` -> effectId `{row.get('effect_id')}` / show `{row.get('character_skill_show_name')}` / hits `{row.get('namespace_hits')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Do not use same-number collisions with broad tables such as `CharacterLevel` or `MonsterRefreshPoint` as proof of semantic ownership.",
            "- Treat `effectId` interpretation as static evidence only until calibrated by a privacy-filtered runtime `SM_DigitDoorReadyFight.skillList` sample.",
            "- This report is not guidance for patching, injection, or bypassing server authority.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_skill_enhance_effect_id_namespace_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None
    runtime = load_fanxiu_digitdoor_runtime_index(export_root=root, rebuild_missing=True)

    all_config_rows: dict[str, list[dict[str, Any]]] = {}
    config_index: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(config_dir.glob("*.lua"), key=lambda item: item.name.lower()):
        table = path.stem.split("__")[0]
        rows = _parse_config_rows(config_dir, table, resolved_lang_path, lang_map)
        all_config_rows[table] = rows
        for row in rows:
            row_id = _as_int(row.get("id"))
            if row_id is None:
                continue
            config_index[row_id].append(
                {
                    "table": table,
                    "id": row_id,
                    "name": _digitdoor_name_from_row(row),
                }
            )

    def by_id(table: str) -> dict[int, dict[str, Any]]:
        return {
            parsed: row
            for row in all_config_rows.get(table, [])
            if (parsed := _as_int(row.get("id"))) is not None
        }

    rows, summary_rows, stats = _digitdoor_skill_enhance_effect_id_namespace_rows(
        [group for group in runtime["catalog"].get("custom_enhance_groups") or [] if isinstance(group, dict)],
        config_index=config_index,
        skill_info_by_id=by_id("CharacterSkillInfo"),
        skill_show_by_id=by_id("CharacterSkillShow"),
        skill_enhance_effect_by_id=by_id("SkillEnhanceEffect"),
        skill_enhance_by_id=by_id("SkillEnhance"),
    )
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_tsv = out_dir / "skill_enhance_effect_id_namespace.tsv"
    summary_tsv = out_dir / "skill_enhance_effect_id_namespace_summary.tsv"
    report_path = out_dir / "skill_enhance_effect_id_namespace_report.md"
    _write_tsv(
        rows_tsv,
        rows,
        [
            "enhance_id",
            "group_char_id",
            "group_name",
            "enhance_name",
            "description",
            "condition_text",
            "effect_id",
            "primary_namespace",
            "namespace_note",
            "namespace_hits",
            "character_skill_info_hit",
            "character_skill_info_char_id",
            "character_skill_show_hit",
            "character_skill_show_partner_id",
            "character_skill_show_name",
            "skill_enhance_effect_hit",
            "skill_enhance_effect_char_id",
            "skill_enhance_effect_skill",
            "skill_enhance_effect_buff_id",
            "skill_enhance_hit",
            "skill_enhance_hit_name",
            "same_as_enhance_id",
            "config_hit_count",
            "sample_config_hits",
        ],
    )
    _write_tsv(summary_tsv, summary_rows, ["kind", "key", "count"])
    _write_digitdoor_skill_enhance_effect_id_namespace_markdown(
        report_path,
        rows=rows,
        summary_rows=summary_rows,
        stats=stats,
        config_dir=config_dir,
    )
    return {
        "confirmed": bool(rows),
        "output_dir": str(out_dir),
        "stats": stats,
        "files": {
            "markdown": str(report_path),
            "rows": str(rows_tsv),
            "summary": str(summary_tsv),
        },
    }


def _digitdoor_readyfight_skilllist_scan_files(root: Path, logic_dir: Path) -> list[Path]:
    files = list(logic_dir.glob("*.lua"))
    patterns = [
        "by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorReadyFight.lua",
        "by_source/lscripts/gamesystem/game/headui_*/text_assets/DigitDoorPartnerHeadUI.lua",
    ]
    for pattern in patterns:
        files.extend(path for path in root.glob(pattern) if path.is_file())
    return files


def _classify_digitdoor_readyfight_skilllist_line(path: Path, line: str) -> list[str]:
    stripped = line.strip()
    categories: list[str] = []
    if path.name.startswith("SM_DigitDoorReadyFight") and (
        "self.skillList" in line or "readMessageList2List(self.skillList)" in line or "writeIntList(self.skillList)" in line
    ):
        categories.append("packet_skill_list_schema")
    if "SetCouncilSkill2lvMap(msg.skillList)" in line:
        categories.append("readyfight_msg_to_cache")
    if "function _M.SetCouncilSkill2lvMap" in line or "V_CouncilSkillList=skillList" in line:
        categories.append("cache_setter")
    if "function _M.GetCouncilSkillList" in line or "return self.V_CouncilSkillList" in line:
        categories.append("cache_getter_definition")
    if "GetCouncilSkillList(" in line:
        is_definition = stripped.startswith("function _M.GetCouncilSkillList")
        is_wrapper = "return self.DigitDoorData:GetCouncilSkillList()" in stripped
        if not is_definition and not is_wrapper:
            categories.append("external_cache_consumer")
    if "GetCouncilSkillById" in line:
        if path.name == "DigitDoorFightComponent.lua":
            categories.append("fight_component_character_skill_lookup")
        elif path.name == "DigitDoorPartnerHeadUI.lua":
            categories.append("headui_character_skill_lookup")
        else:
            categories.append("character_skill_lookup_call")
    if "ConfigName.DigitDoor_CharacterSkillInfo" in line:
        categories.append("character_skill_info_config_lookup")
    if "ConfigName.DigitDoor_SkillEnhance" in line:
        categories.append("skill_enhance_display_lookup")
    if "local skillList={1001,2001,3001,4001,5001}" in line:
        categories.append("hardcoded_base_skill_seed")
    return categories


def _digitdoor_readyfight_skilllist_consumer_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for path in sorted(_digitdoor_readyfight_skilllist_scan_files(root, logic_dir), key=lambda item: str(item).lower()):
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            for category in _classify_digitdoor_readyfight_skilllist_line(path, line):
                rows.append(
                    {
                        "category": category,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": _WHITESPACE_RE.sub(" ", line.strip()),
                    }
                )
    return rows


def _write_digitdoor_readyfight_skilllist_consumer_markdown(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    stats: dict[str, Any],
    logic_dir: Path,
) -> None:
    lines = [
        "# DigitDoor ReadyFight skillList consumer audit",
        "",
        "Static read-only audit for `SM_DigitDoorReadyFight.skillList` and visible Lua consumers.",
        "",
        f"- Logic dir: `{logic_dir}`",
        "",
        "## Stats",
        "",
    ]
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            "- `SM_DigitDoorReadyFight.skillList` is read as a message/int list and cached through `DigitDoorData:SetCouncilSkill2lvMap(msg.skillList)` into `V_CouncilSkillList`.",
            "- Current visible DigitDoor Lua has no external `DigitDoorMgr.Model:GetCouncilSkillList()` consumer beyond wrapper/getter definitions.",
            "- Visible skill detail lookups use `GetCouncilSkillById(skillId) -> DigitDoor_CharacterSkillInfo`, while strengthen-name formatting uses `DigitDoor_SkillEnhance`.",
            "- Therefore the cached ReadyFight `skillList` is a server snapshot surface, but its exact live values still need a privacy-filtered runtime packet sample before treating it as the authoritative enhance-selection list.",
            "",
            "## Category Summary",
            "",
            "| Category | Count |",
            "| --- | ---: |",
        ]
    )
    for key in sorted(k for k in stats if k.endswith("_rows")):
        lines.append(f"| `{key.removesuffix('_rows')}` | {stats[key]} |")
    lines.extend(["", "## Evidence Samples", ""])
    for row in rows[:80]:
        lines.append(
            f"- `{row.get('category')}` {row.get('file')}:{row.get('line')} `{row.get('function')}` - `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This report documents static client/resource evidence only.",
            "- It is a calibration aid for wiki/protocol understanding, not guidance for patching, injection, or bypassing server authority.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_readyfight_skilllist_consumer_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    rows = _digitdoor_readyfight_skilllist_consumer_rows(root, logic_dir)
    category_counts = Counter(str(row.get("category") or "") for row in rows)
    stats = {
        "evidence_row_count": len(rows),
        "packet_skill_list_schema_rows": category_counts.get("packet_skill_list_schema", 0),
        "readyfight_msg_to_cache_rows": category_counts.get("readyfight_msg_to_cache", 0),
        "cache_setter_rows": category_counts.get("cache_setter", 0),
        "cache_getter_definition_rows": category_counts.get("cache_getter_definition", 0),
        "external_cache_consumer_rows": category_counts.get("external_cache_consumer", 0),
        "character_skill_info_config_lookup_rows": category_counts.get("character_skill_info_config_lookup", 0),
        "fight_component_character_skill_lookup_rows": category_counts.get("fight_component_character_skill_lookup", 0),
        "headui_character_skill_lookup_rows": category_counts.get("headui_character_skill_lookup", 0),
        "skill_enhance_display_lookup_rows": category_counts.get("skill_enhance_display_lookup", 0),
        "hardcoded_base_skill_seed_rows": category_counts.get("hardcoded_base_skill_seed", 0),
    }
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_tsv = out_dir / "readyfight_skilllist_consumers.tsv"
    report_path = out_dir / "readyfight_skilllist_consumer_report.md"
    _write_tsv(rows_tsv, rows, ["category", "file", "line", "function", "snippet"])
    _write_digitdoor_readyfight_skilllist_consumer_markdown(report_path, rows=rows, stats=stats, logic_dir=logic_dir)
    confirmed = (
        stats["packet_skill_list_schema_rows"] > 0
        and stats["readyfight_msg_to_cache_rows"] > 0
        and stats["character_skill_info_config_lookup_rows"] > 0
    )
    return {
        "confirmed": confirmed,
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": {
            "readyfight_skilllist_is_cached": stats["readyfight_msg_to_cache_rows"] > 0 and stats["cache_setter_rows"] > 0,
            "visible_external_cache_consumer_found": stats["external_cache_consumer_rows"] > 0,
            "visible_character_skill_info_lookup_found": stats["character_skill_info_config_lookup_rows"] > 0,
            "visible_skill_enhance_display_lookup_found": stats["skill_enhance_display_lookup_rows"] > 0,
        },
        "files": {
            "markdown": str(report_path),
            "rows": str(rows_tsv),
        },
    }


def _digitdoor_readyfight_skilllist_shape_files(root: Path, logic_dir: Path) -> list[Path]:
    files = [
        logic_dir / "DigitDoorData.lua",
        *root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorReadyFight.lua"),
        *root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/DDSkillVo.lua"),
        *root.glob("by_source/lscripts/core_*/text_assets/BaseMessage.lua"),
    ]
    unique: dict[str, Path] = {}
    for path in files:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _classify_digitdoor_readyfight_skilllist_shape_line(path: Path, line: str) -> list[str]:
    categories: list[str] = []
    if path.name == "SM_DigitDoorReadyFight.lua":
        if "readMessageList2List(self.skillList)" in line:
            categories.append("readyfight_read_message_list2list")
        if "writeIntList(self.skillList)" in line:
            categories.append("readyfight_write_int_list")
        if "self.skillList" in line and "CList.new" in line:
            categories.append("readyfight_skilllist_ctor")
    elif path.name == "BaseMessage.lua":
        if re.search(r"function\s+_M[:.]readMessageList2List\b", line):
            categories.append("base_read_message_list2list_definition")
        if "readBaseByType(proId)" in line or "readBaseByType" in line and "proId" in line:
            categories.append("base_primitive_list_branch")
        if "F_GetMessage(proId)" in line:
            categories.append("base_bean_list_branch")
        if re.search(r"function\s+_M[:.]writeIntList\b", line):
            categories.append("base_write_int_list_definition")
        if "SerializerType.INT_JAVA" in line:
            categories.append("base_write_int_list_serializer")
        if "message:getId()" in line:
            categories.append("base_write_list_message_id")
    elif path.name == "DDSkillVo.lua":
        if "91604" in line:
            categories.append("dds_skill_vo_id")
        if re.search(r"\bself\.(id|num)\b", line):
            categories.append("dds_skill_vo_field")
    elif path.name == "DigitDoorData.lua":
        if "info.id" in line:
            categories.append("get_skill_name_list_info_id")
        if "info.value" in line:
            categories.append("get_skill_name_list_info_value")
    return categories


def _digitdoor_readyfight_skilllist_shape_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_readyfight_skilllist_shape_files(root, logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            for category in _classify_digitdoor_readyfight_skilllist_shape_line(path, line):
                rows.append(
                    {
                        "category": category,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": _WHITESPACE_RE.sub(" ", line.strip()),
                    }
                )
    return rows


def _digitdoor_readyfight_ddskillvo_direct_usage_rows(root: Path) -> list[dict[str, Any]]:
    usage_rows: list[dict[str, Any]] = []
    lscript_root = root / "by_source" / "lscripts"
    if not lscript_root.is_dir():
        return usage_rows
    for path in sorted(lscript_root.rglob("*.lua"), key=lambda item: str(item).lower()):
        if path.name.startswith("DDSkillVo") or path.name.startswith("VO_URL"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        current_function = ""
        for line_no, line in enumerate(text.splitlines(), start=1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            if "DDSkillVo" in line:
                usage_rows.append(
                    {
                        "category": "direct_ddskillvo_logic_usage",
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": _WHITESPACE_RE.sub(" ", line.strip()),
                    }
                )
    return usage_rows


def _digitdoor_readyfight_ddskillvo_fields(rows: list[dict[str, Any]]) -> list[str]:
    fields: set[str] = set()
    for row in rows:
        if row.get("category") != "dds_skill_vo_field":
            continue
        for field in re.findall(r"\bself\.(id|num)\b", str(row.get("snippet") or "")):
            fields.add(field)
    return sorted(fields)


def _write_digitdoor_readyfight_skilllist_shape_markdown(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
    logic_dir: Path,
) -> None:
    lines = [
        "# DigitDoor ReadyFight skillList wire-shape audit",
        "",
        "Static read-only audit for the ambiguous wire shape of `SM_DigitDoorReadyFight.skillList`.",
        "",
        f"- Logic dir: `{logic_dir}`",
        "",
        "## Stats",
        "",
    ]
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Verdict", ""])
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            "- `SM_DigitDoorReadyFight` reads `skillList` with `readMessageList2List`, a helper that can decode both primitive typed lists and bean/message lists.",
            "- The same generated message writes `skillList` with `writeIntList`, which is strong static evidence for a primitive int-list client write shape.",
            "- `DDSkillVo(91604)` exists in the same message family with `id,num`, but visible DigitDoor Lua has no direct `DDSkillVo` consumer.",
            "- `DigitDoorData:GetSkillNameList` works with `info.id` / `info.value`; that does not exactly match `DDSkillVo.id` / `DDSkillVo.num` by field name.",
            "- Current static evidence therefore keeps the runtime shape as ambiguous. A privacy-filtered captured `91629` packet is the clean way to close this gap.",
            "",
            "## Evidence Samples",
            "",
        ]
    )
    for row in rows[:100]:
        lines.append(
            f"- `{row.get('category')}` {row.get('file')}:{row.get('line')} `{row.get('function')}` - `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This probe is for protocol/catalog understanding and wiki rendering.",
            "- It does not patch, inject, bypass authority, or alter runtime traffic.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_readyfight_skilllist_shape_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    rows = _digitdoor_readyfight_skilllist_shape_rows(root, logic_dir)
    usage_rows = _digitdoor_readyfight_ddskillvo_direct_usage_rows(root)
    all_rows = rows + usage_rows
    category_counts = Counter(str(row.get("category") or "") for row in all_rows)
    dds_fields = _digitdoor_readyfight_ddskillvo_fields(rows)
    stats = {
        "evidence_row_count": len(all_rows),
        "readyfight_read_message_list_rows": category_counts.get("readyfight_read_message_list2list", 0),
        "readyfight_write_int_list_rows": category_counts.get("readyfight_write_int_list", 0),
        "readyfight_skilllist_ctor_rows": category_counts.get("readyfight_skilllist_ctor", 0),
        "base_read_message_list2list_rows": category_counts.get("base_read_message_list2list_definition", 0),
        "base_primitive_list_branch_rows": category_counts.get("base_primitive_list_branch", 0),
        "base_bean_list_branch_rows": category_counts.get("base_bean_list_branch", 0),
        "base_write_int_list_rows": category_counts.get("base_write_int_list_definition", 0),
        "base_write_int_serializer_rows": category_counts.get("base_write_int_list_serializer", 0),
        "base_write_list_message_id_rows": category_counts.get("base_write_list_message_id", 0),
        "dds_skill_vo_id_rows": category_counts.get("dds_skill_vo_id", 0),
        "dds_skill_vo_field_count": len(dds_fields),
        "direct_ddskillvo_logic_usage_rows": category_counts.get("direct_ddskillvo_logic_usage", 0),
        "get_skill_name_list_info_id_rows": category_counts.get("get_skill_name_list_info_id", 0),
        "get_skill_name_list_info_value_rows": category_counts.get("get_skill_name_list_info_value", 0),
    }
    supports_primitive = stats["base_primitive_list_branch_rows"] > 0 and stats["base_write_int_serializer_rows"] > 0
    supports_bean = stats["base_bean_list_branch_rows"] > 0 and stats["base_write_list_message_id_rows"] > 0
    mismatch = "num" in dds_fields and stats["get_skill_name_list_info_value_rows"] > 0
    if stats["readyfight_read_message_list_rows"] and stats["readyfight_write_int_list_rows"] and supports_primitive and supports_bean:
        shape_verdict = "ambiguous_runtime_wire_shape"
    elif stats["readyfight_write_int_list_rows"] and supports_primitive:
        shape_verdict = "primitive_int_list_likely"
    elif stats["dds_skill_vo_id_rows"] and supports_bean:
        shape_verdict = "bean_list_possible"
    else:
        shape_verdict = "insufficient_static_evidence"
    verdict = {
        "readyfight_skilllist_read_method": "readMessageList2List" if stats["readyfight_read_message_list_rows"] else "",
        "readyfight_skilllist_write_method": "writeIntList" if stats["readyfight_write_int_list_rows"] else "",
        "dds_skill_vo_found": stats["dds_skill_vo_id_rows"] > 0,
        "dds_skill_vo_fields": ",".join(dds_fields),
        "base_message_supports_primitive_list": supports_primitive,
        "base_message_supports_bean_list": supports_bean,
        "visible_ddskillvo_logic_usage_found": stats["direct_ddskillvo_logic_usage_rows"] > 0,
        "get_skill_name_list_uses_info_id": stats["get_skill_name_list_info_id_rows"] > 0,
        "get_skill_name_list_uses_info_value": stats["get_skill_name_list_info_value_rows"] > 0,
        "dds_skill_vo_num_value_name_mismatch": mismatch,
        "shape_verdict": shape_verdict,
    }
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_tsv = out_dir / "readyfight_skilllist_shape_evidence.tsv"
    report_path = out_dir / "readyfight_skilllist_shape_report.md"
    _write_tsv(rows_tsv, all_rows, ["category", "file", "line", "function", "snippet"])
    _write_digitdoor_readyfight_skilllist_shape_markdown(
        report_path,
        rows=all_rows,
        stats=stats,
        verdict=verdict,
        logic_dir=logic_dir,
    )
    return {
        "confirmed": stats["readyfight_read_message_list_rows"] > 0 and stats["readyfight_write_int_list_rows"] > 0,
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "rows": str(rows_tsv),
        },
    }


def _digitdoor_startgame_cpp2il_surfaces(root: Path) -> list[dict[str, Any]]:
    index_dir = root / "apk_static_index"
    return [
        {
            "surface": "cpp2il_diffable_cs",
            "path": index_dir / "cpp2il_2022_1_pre21_arm64_diffable_cs",
            "suffixes": {".cs"},
        },
        {
            "surface": "cpp2il_isil",
            "path": index_dir / "cpp2il_2022_1_pre21_arm64_isil",
            "suffixes": {".txt"},
        },
        {
            "surface": "il2cpp_metadata_tsv",
            "path": index_dir,
            "suffixes": {".tsv"},
            "name_prefix": "il2cpp_",
        },
    ]


def _iter_digitdoor_startgame_cpp2il_files(surface: dict[str, Any]) -> list[Path]:
    path = Path(surface["path"])
    suffixes = set(surface.get("suffixes") or ())
    name_prefix = str(surface.get("name_prefix") or "")
    if not path.exists():
        return []
    if path.is_file():
        return [path] if path.suffix in suffixes else []
    files: list[Path] = []
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        if suffixes and item.suffix not in suffixes:
            continue
        if name_prefix and not item.name.startswith(name_prefix):
            continue
        files.append(item)
    return sorted(files, key=lambda item: str(item).lower())


def _classify_digitdoor_startgame_cpp2il_hit(category: str, term: str) -> str:
    if category.startswith("direct_startgame"):
        return "direct packet/field symbol candidate; would need line context before treating it as a consumer"
    if term == "GetDigitDoorPartnerAttributes":
        return "native-to-Lua helper surface for DigitDoor partner attributes, not a StartGame skillVos consumer by itself"
    return "generic DigitDoor native-readable bridge surface"


def _write_digitdoor_startgame_cpp2il_consumer_markdown(
    path: Path,
    *,
    export_root: Path,
    surface_rows: list[dict[str, Any]],
    hit_rows: list[dict[str, Any]],
    bridge_lua_rows: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor StartGame Cpp2IL consumer surface",
        "",
        f"- Export root: `{export_root}`",
        f"- Surfaces scanned: {len(surface_rows)}",
        f"- Evidence rows: {len(hit_rows)}",
        f"- Lua bridge target rows: {len(bridge_lua_rows)}",
        "- Scope: static Cpp2IL/diffable C#/ISIL/metadata search for StartGame packet and `skillVos` consumer symbols. It does not hook, patch, replay, or modify the client.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Surface Summary",
            "",
            "| Surface | Exists | Files Scanned | Hit Rows |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for row in surface_rows:
        lines.append(
            "| "
            f"{row.get('surface', '')} | "
            f"{row.get('exists', '')} | "
            f"{row.get('files_scanned', '')} | "
            f"{row.get('hit_rows', '')} |"
        )
    lines.extend(
        [
            "",
            "## Term Summary",
            "",
            "| Term | Category | Hits |",
            "| --- | --- | ---: |",
        ]
    )
    for row in summary_rows:
        lines.append(f"| {row.get('term', '')} | {row.get('category', '')} | {row.get('hit_count', '')} |")
    if bridge_lua_rows:
        lines.extend(
            [
                "",
                "## Lua Bridge Target",
                "",
                "| File | Line | Term | Category |",
                "| --- | ---: | --- | --- |",
            ]
        )
        for row in bridge_lua_rows:
            lines.append(
                "| "
                f"{row.get('file', '')} | "
                f"{row.get('line', '')} | "
                f"{row.get('term', '')} | "
                f"{row.get('category', '')} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- No direct `SM_DigitDoorStartGame` / `skillVos` / `DDSkillVo` Cpp2IL hit means the native-readable surface does not currently close the StartGame response-field semantics.",
            "- `CsCallLuaMgr.GetDigitDoorPartnerAttributes()` maps to `Core.Battle.Skill.Editor.SkillEditorBridge.GetDigitDoorPartnerAttributes`, which formats current `DigitDoorPartnerView`/`DigitDoorBotView` attributes for an editor/console surface.",
            "- That Lua bridge reads runtime entity views and formatted attributes; it does not mention `SM_DigitDoorStartGame`, `skillVos`, or `DDSkillVo`.",
            "- Keep the prior boundary: `skillVos` remains a server-returned schema candidate until a privacy-filtered `91623` runtime sample or a stronger native symbol appears.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_DIGITDOOR_STARTGAME_CPP2IL_TERMS: dict[str, str] = {
    "CM_DigitDoorStartGame": "direct_startgame_packet_symbol",
    "SM_DigitDoorStartGame": "direct_startgame_packet_symbol",
    "DDFightPartnerVO": "direct_startgame_partner_vo_symbol",
    "DDSkillVo": "direct_startgame_skill_vo_symbol",
    "skillVos": "direct_startgame_skillvos_field",
    "indexList": "direct_startgame_indexlist_field",
    "GetDigitDoorPartnerAttributes": "digitdoor_partner_attribute_bridge",
    "DigitDoor": "digitdoor_generic_bridge_surface",
}


def _collect_digitdoor_startgame_cpp2il_bridge_lua_rows(root: Path) -> list[dict[str, Any]]:
    terms = {
        "GetDigitDoorPartnerAttributes": "bridge_lua_function",
        "DigitDoorFightMgr.inst": "bridge_runtime_guard",
        "GetDigitDoorPartnerViewList": "bridge_partner_view_list",
        "GetDigitDoorBotViewList": "bridge_bot_view_list",
        "FormatBaseAttr4ConsoleOutput": "bridge_base_attr_formatter",
        "FormatBuffAddAttrOutput": "bridge_buff_attr_formatter",
        "SM_DigitDoorStartGame": "unexpected_startgame_packet_reference",
        "skillVos": "unexpected_skillvos_reference",
        "DDSkillVo": "unexpected_ddskillvo_reference",
    }
    rows: list[dict[str, Any]] = []
    candidates = sorted((root / "by_source" / "lscripts").glob("**/SkillEditorBridge*.lua"), key=lambda item: str(item).lower())
    for path in candidates:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        rel_path = path.relative_to(root) if path.is_relative_to(root) else path
        for line_no, line in enumerate(lines, 1):
            for term, category in terms.items():
                if term not in line:
                    continue
                rows.append(
                    {
                        "file": str(rel_path),
                        "line": line_no,
                        "term": term,
                        "category": category,
                        "snippet": line.strip()[:240],
                    }
                )
    return rows


def build_fanxiu_digitdoor_startgame_cpp2il_consumer_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    terms = {term.lower(): (term, category) for term, category in _DIGITDOOR_STARTGAME_CPP2IL_TERMS.items()}
    hit_rows: list[dict[str, Any]] = []
    surface_rows: list[dict[str, Any]] = []
    term_counts: Counter[tuple[str, str]] = Counter()
    for surface in _digitdoor_startgame_cpp2il_surfaces(root):
        surface_name = str(surface["surface"])
        surface_path = Path(surface["path"])
        files = _iter_digitdoor_startgame_cpp2il_files(surface)
        surface_hit_count = 0
        for file_path in files:
            try:
                lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            rel_path = file_path.relative_to(root) if file_path.is_relative_to(root) else file_path
            for line_no, line in enumerate(lines, 1):
                lowered = line.lower()
                for lowered_term, (term, category) in terms.items():
                    if lowered_term not in lowered:
                        continue
                    term_counts[(term, category)] += 1
                    surface_hit_count += 1
                    hit_rows.append(
                        {
                            "surface": surface_name,
                            "file": str(rel_path),
                            "line": line_no,
                            "term": term,
                            "category": category,
                            "snippet": line.strip()[:240],
                            "interpretation": _classify_digitdoor_startgame_cpp2il_hit(category, term),
                        }
                    )
        surface_rows.append(
            {
                "surface": surface_name,
                "path": str(surface_path),
                "exists": surface_path.exists(),
                "files_scanned": len(files),
                "hit_rows": surface_hit_count,
            }
        )
    direct_hits = [row for row in hit_rows if str(row.get("category", "")).startswith("direct_startgame")]
    skillvos_hits = [row for row in hit_rows if row.get("term") == "skillVos"]
    bridge_hits = [row for row in hit_rows if row.get("term") == "GetDigitDoorPartnerAttributes"]
    bridge_lua_rows = _collect_digitdoor_startgame_cpp2il_bridge_lua_rows(root)
    bridge_lua_terms = {str(row.get("term") or "") for row in bridge_lua_rows}
    unexpected_bridge_lua_terms = {"SM_DigitDoorStartGame", "skillVos", "DDSkillVo"} & bridge_lua_terms
    summary_rows = [
        {
            "term": term,
            "category": category,
            "hit_count": term_counts.get((term, category), 0),
        }
        for term, category in _DIGITDOOR_STARTGAME_CPP2IL_TERMS.items()
    ]
    verdict = {
        "cpp2il_surfaces_found": any(bool(row["exists"]) for row in surface_rows),
        "cpp2il_has_startgame_packet_or_field_symbols": bool(direct_hits),
        "cpp2il_has_skillvos_symbol": bool(skillvos_hits),
        "cpp2il_has_digitdoor_partner_attribute_bridge": bool(bridge_hits),
        "cpp2il_bridge_is_not_skillvos_consumer": bool(bridge_hits) and not skillvos_hits,
        "lua_bridge_target_found": "GetDigitDoorPartnerAttributes" in bridge_lua_terms,
        "lua_bridge_reads_runtime_partner_and_bot_views": {"GetDigitDoorPartnerViewList", "GetDigitDoorBotViewList"}.issubset(
            bridge_lua_terms
        ),
        "lua_bridge_mentions_startgame_skillvos_or_ddskillvo": bool(unexpected_bridge_lua_terms),
        "native_readable_surface_closes_startgame_skillvos": bool(skillvos_hits),
    }
    _write_tsv(
        out_dir / "startgame_cpp2il_consumer_surfaces.tsv",
        surface_rows,
        ["surface", "path", "exists", "files_scanned", "hit_rows"],
    )
    _write_tsv(
        out_dir / "startgame_cpp2il_consumer_hits.tsv",
        hit_rows,
        ["surface", "file", "line", "term", "category", "snippet", "interpretation"],
    )
    _write_tsv(
        out_dir / "startgame_cpp2il_consumer_summary.tsv",
        summary_rows,
        ["term", "category", "hit_count"],
    )
    _write_tsv(
        out_dir / "startgame_cpp2il_bridge_lua_hits.tsv",
        bridge_lua_rows,
        ["file", "line", "term", "category", "snippet"],
    )
    report_path = out_dir / "startgame_cpp2il_consumer_report.md"
    _write_digitdoor_startgame_cpp2il_consumer_markdown(
        report_path,
        export_root=root,
        surface_rows=surface_rows,
        hit_rows=hit_rows,
        bridge_lua_rows=bridge_lua_rows,
        summary_rows=summary_rows,
        verdict=verdict,
    )
    json_path = out_dir / "startgame_cpp2il_consumer_report.json"
    json_path.write_text(
        json.dumps(
            {
                "stats": {
                    "surface_count": len(surface_rows),
                    "files_scanned": sum(int(row["files_scanned"]) for row in surface_rows),
                    "hit_count": len(hit_rows),
                    "direct_hit_count": len(direct_hits),
                    "skillvos_hit_count": len(skillvos_hits),
                    "bridge_hit_count": len(bridge_hits),
                    "bridge_lua_hit_count": len(bridge_lua_rows),
                },
                "verdict": verdict,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": verdict["cpp2il_surfaces_found"],
        "output_dir": str(out_dir),
        "stats": {
            "surface_count": len(surface_rows),
            "files_scanned": sum(int(row["files_scanned"]) for row in surface_rows),
            "hit_count": len(hit_rows),
            "direct_hit_count": len(direct_hits),
            "skillvos_hit_count": len(skillvos_hits),
            "bridge_hit_count": len(bridge_hits),
            "bridge_lua_hit_count": len(bridge_lua_rows),
        },
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "surfaces": str(out_dir / "startgame_cpp2il_consumer_surfaces.tsv"),
            "hits": str(out_dir / "startgame_cpp2il_consumer_hits.tsv"),
            "summary": str(out_dir / "startgame_cpp2il_consumer_summary.tsv"),
            "bridge_lua_hits": str(out_dir / "startgame_cpp2il_bridge_lua_hits.tsv"),
            "json": str(json_path),
        },
    }


_DIGITDOOR_READYFIGHT_CPP2IL_TERMS: dict[str, str] = {
    "CM_DigitDoorReadyFight": "direct_readyfight_packet_symbol",
    "SM_DigitDoorReadyFight": "direct_readyfight_packet_symbol",
    "DDSkillVo": "direct_readyfight_skill_vo_symbol",
    "skillList": "broad_skilllist_identifier",
    "SetCouncilSkill2lvMap": "readyfight_lua_cache_symbol",
    "GetCouncilSkillList": "readyfight_lua_cache_symbol",
    "GetCouncilSkillById": "readyfight_lua_lookup_symbol",
    "DigitDoor_CharacterSkillInfo": "readyfight_character_skill_config_symbol",
    "DigitDoor_SkillEnhance": "readyfight_skill_enhance_config_symbol",
    "DigitDoor": "digitdoor_generic_bridge_surface",
}


def _classify_digitdoor_readyfight_cpp2il_hit(category: str, term: str) -> str:
    if category == "broad_skilllist_identifier":
        return "broad identifier only; not enough to identify a ReadyFight consumer without packet or class context"
    if category.startswith("direct_readyfight"):
        return "direct ReadyFight packet/field symbol candidate; line context determines whether it is a real consumer"
    if category.startswith("readyfight_lua"):
        return "Lua-side cache or lookup symbol; useful if exported into a native-readable bridge"
    return "generic DigitDoor native-readable surface"


def _collect_digitdoor_readyfight_cpp2il_lua_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, str, str]] = set()
    for source, source_rows in [
        ("consumer_probe", _digitdoor_readyfight_skilllist_consumer_rows(root, logic_dir)),
        ("shape_probe", _digitdoor_readyfight_skilllist_shape_rows(root, logic_dir)),
    ]:
        for row in source_rows:
            key = (
                source,
                str(row.get("file") or ""),
                int(row.get("line") or 0),
                str(row.get("category") or ""),
                str(row.get("snippet") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "source": source,
                    "category": row.get("category") or "",
                    "file": row.get("file") or "",
                    "line": row.get("line") or "",
                    "function": row.get("function") or "",
                    "snippet": row.get("snippet") or "",
                }
            )
    return rows


def _write_digitdoor_readyfight_cpp2il_consumer_markdown(
    path: Path,
    *,
    export_root: Path,
    surface_rows: list[dict[str, Any]],
    hit_rows: list[dict[str, Any]],
    lua_rows: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor ReadyFight Cpp2IL consumer surface",
        "",
        f"- Export root: `{export_root}`",
        f"- Surfaces scanned: {len(surface_rows)}",
        f"- Cpp2IL/native-readable evidence rows: {len(hit_rows)}",
        f"- Lua reference rows: {len(lua_rows)}",
        "- Scope: static Cpp2IL/diffable C#/ISIL/metadata search for ReadyFight packet and `skillList` consumer symbols. It does not hook, patch, replay, or modify the client.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Surface Summary",
            "",
            "| Surface | Exists | Files Scanned | Hit Rows |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for row in surface_rows:
        lines.append(
            "| "
            f"{row.get('surface', '')} | "
            f"{row.get('exists', '')} | "
            f"{row.get('files_scanned', '')} | "
            f"{row.get('hit_rows', '')} |"
        )
    lines.extend(
        [
            "",
            "## Term Summary",
            "",
            "| Term | Category | Hits |",
            "| --- | --- | ---: |",
        ]
    )
    for row in summary_rows:
        lines.append(f"| {row.get('term', '')} | {row.get('category', '')} | {row.get('hit_count', '')} |")
    if lua_rows:
        lines.extend(
            [
                "",
                "## Lua Reference Boundary",
                "",
                "| Source | Category | File | Line | Function |",
                "| --- | --- | --- | ---: | --- |",
            ]
        )
        for row in lua_rows[:80]:
            lines.append(
                "| "
                f"{row.get('source', '')} | "
                f"{row.get('category', '')} | "
                f"{row.get('file', '')} | "
                f"{row.get('line', '')} | "
                f"{row.get('function', '')} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Lua evidence already shows `SM_DigitDoorReadyFight.skillList` is cached through `DigitDoorData:SetCouncilSkill2lvMap(msg.skillList)` and later read through council-skill helper functions.",
            "- This report asks a narrower question: whether the Cpp2IL/native-readable surface contains direct ReadyFight packet, `DDSkillVo`, or cache-consumer symbols that close the static boundary.",
            "- A standalone `skillList` identifier is deliberately treated as broad evidence only; without ReadyFight packet/class context it is not enough to prove the `91629` response shape.",
            "- If direct ReadyFight Cpp2IL symbols stay absent, the remaining clean closure path is still a privacy-filtered `SM_DigitDoorReadyFight(91629)` runtime sample.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_readyfight_cpp2il_consumer_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    terms = {term.lower(): (term, category) for term, category in _DIGITDOOR_READYFIGHT_CPP2IL_TERMS.items()}
    hit_rows: list[dict[str, Any]] = []
    surface_rows: list[dict[str, Any]] = []
    term_counts: Counter[tuple[str, str]] = Counter()
    for surface in _digitdoor_startgame_cpp2il_surfaces(root):
        surface_name = str(surface["surface"])
        surface_path = Path(surface["path"])
        files = _iter_digitdoor_startgame_cpp2il_files(surface)
        surface_hit_count = 0
        for file_path in files:
            try:
                lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            rel_path = file_path.relative_to(root) if file_path.is_relative_to(root) else file_path
            for line_no, line in enumerate(lines, 1):
                lowered = line.lower()
                for lowered_term, (term, category) in terms.items():
                    if lowered_term not in lowered:
                        continue
                    term_counts[(term, category)] += 1
                    surface_hit_count += 1
                    hit_rows.append(
                        {
                            "surface": surface_name,
                            "file": str(rel_path),
                            "line": line_no,
                            "term": term,
                            "category": category,
                            "snippet": line.strip()[:240],
                            "interpretation": _classify_digitdoor_readyfight_cpp2il_hit(category, term),
                        }
                    )
        surface_rows.append(
            {
                "surface": surface_name,
                "path": str(surface_path),
                "exists": surface_path.exists(),
                "files_scanned": len(files),
                "hit_rows": surface_hit_count,
            }
        )
    lua_rows = _collect_digitdoor_readyfight_cpp2il_lua_rows(root, logic_dir)
    direct_packet_hits = [row for row in hit_rows if row.get("category") == "direct_readyfight_packet_symbol"]
    direct_skillvo_hits = [row for row in hit_rows if row.get("category") == "direct_readyfight_skill_vo_symbol"]
    broad_skilllist_hits = [row for row in hit_rows if row.get("category") == "broad_skilllist_identifier"]
    cache_symbol_hits = [row for row in hit_rows if str(row.get("category") or "").startswith("readyfight_lua")]
    lua_categories = {str(row.get("category") or "") for row in lua_rows}
    summary_rows = [
        {
            "term": term,
            "category": category,
            "hit_count": term_counts.get((term, category), 0),
        }
        for term, category in _DIGITDOOR_READYFIGHT_CPP2IL_TERMS.items()
    ]
    verdict = {
        "cpp2il_surfaces_found": any(bool(row["exists"]) for row in surface_rows),
        "cpp2il_has_readyfight_packet_symbol": bool(direct_packet_hits),
        "cpp2il_has_ddskillvo_symbol": bool(direct_skillvo_hits),
        "cpp2il_has_broad_skilllist_identifier": bool(broad_skilllist_hits),
        "cpp2il_has_readyfight_cache_or_lookup_symbol": bool(cache_symbol_hits),
        "lua_readyfight_skilllist_cache_found": "readyfight_msg_to_cache" in lua_categories,
        "lua_readyfight_shape_still_ambiguous": "readyfight_read_message_list2list" in lua_categories
        and "readyfight_write_int_list" in lua_categories,
        "native_readable_surface_closes_readyfight_skilllist": bool(direct_packet_hits or direct_skillvo_hits),
    }
    _write_tsv(
        out_dir / "readyfight_cpp2il_consumer_surfaces.tsv",
        surface_rows,
        ["surface", "path", "exists", "files_scanned", "hit_rows"],
    )
    _write_tsv(
        out_dir / "readyfight_cpp2il_consumer_hits.tsv",
        hit_rows,
        ["surface", "file", "line", "term", "category", "snippet", "interpretation"],
    )
    _write_tsv(
        out_dir / "readyfight_cpp2il_consumer_summary.tsv",
        summary_rows,
        ["term", "category", "hit_count"],
    )
    _write_tsv(
        out_dir / "readyfight_cpp2il_lua_reference_hits.tsv",
        lua_rows,
        ["source", "category", "file", "line", "function", "snippet"],
    )
    report_path = out_dir / "readyfight_cpp2il_consumer_report.md"
    _write_digitdoor_readyfight_cpp2il_consumer_markdown(
        report_path,
        export_root=root,
        surface_rows=surface_rows,
        hit_rows=hit_rows,
        lua_rows=lua_rows,
        summary_rows=summary_rows,
        verdict=verdict,
    )
    json_path = out_dir / "readyfight_cpp2il_consumer_report.json"
    stats = {
        "surface_count": len(surface_rows),
        "files_scanned": sum(int(row["files_scanned"]) for row in surface_rows),
        "hit_count": len(hit_rows),
        "direct_packet_hit_count": len(direct_packet_hits),
        "direct_ddskillvo_hit_count": len(direct_skillvo_hits),
        "broad_skilllist_hit_count": len(broad_skilllist_hits),
        "cache_symbol_hit_count": len(cache_symbol_hits),
        "lua_reference_hit_count": len(lua_rows),
    }
    json_path.write_text(
        json.dumps({"stats": stats, "verdict": verdict}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "confirmed": verdict["cpp2il_surfaces_found"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "surfaces": str(out_dir / "readyfight_cpp2il_consumer_surfaces.tsv"),
            "hits": str(out_dir / "readyfight_cpp2il_consumer_hits.tsv"),
            "summary": str(out_dir / "readyfight_cpp2il_consumer_summary.tsv"),
            "lua_reference_hits": str(out_dir / "readyfight_cpp2il_lua_reference_hits.tsv"),
            "json": str(json_path),
        },
    }


def _parse_digitdoor_named_string_lua_table(text: str, table_name: str) -> dict[str, str]:
    match = re.search(rf"_M\.{re.escape(table_name)}\s*=\s*\{{(?P<body>.*?)\}}", text, flags=re.S)
    if not match:
        return {}
    return {
        name: value
        for name, value in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"([^\"]+)\"", match.group("body"))
    }


def _digitdoor_partner_attribute_formatter_files(root: Path, logic_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for name in [
        "DigitDoorSceneView.lua",
        "DigitDoorPartnerView.lua",
        "DigitDoorEntityData.lua",
        "DigitDoorBuffData.lua",
        "DigitDoorBuffAddAttr.lua",
        "DigitDoorFightComponent.lua",
        "DigitDoorBaseSkill.lua",
        "DigitDoorType.lua",
    ]:
        candidates.append(logic_dir / name)
    candidates.extend((root / "by_source" / "lscripts").glob("**/SkillEditorBridge*.lua"))
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _classify_digitdoor_partner_attribute_formatter_line(file_name: str, line: str) -> list[str]:
    categories: list[str] = []
    if "GetDigitDoorPartnerAttributes" in line:
        categories.append("editor_bridge_entry")
    if "LuaGlobal.IsDebugBuild" in line:
        categories.append("debug_build_guard")
    if "DigitDoorMgr.Inst_get():IsStartGame()" in line:
        categories.append("startgame_state_guard")
    if "GetDigitDoorPartnerViewList" in line:
        categories.append("partner_view_source")
    if "GetDigitDoorBotViewList" in line:
        categories.append("bot_view_source")
    if "FormatBaseAttr4ConsoleOutput" in line:
        categories.append("base_attr_formatter")
    if "FormatBuffAddAttrOutput" in line:
        categories.append("buff_attr_formatter")
    if "self.Entity.EntityData:FormatBaseAttr4ConsoleOutput()" in line:
        categories.append("partner_delegates_to_entity_data")
    if ":GetCurrentHp()" in line or ":GetMaxHp()" in line or ":GetAttack()" in line or ":GetSkillDamage()" in line:
        categories.append("base_attr_runtime_getter")
    if "DigitDoorType.BuffAddAttrType" in line:
        categories.append("buff_add_attr_type_loop")
    if "GetAddExtBattleAttr" in line and "GetLayer" in line:
        categories.append("buff_add_attr_layer_aggregation")
    elif "GetAddExtBattleAttr" in line:
        categories.append("buff_add_attr_read")
    if "SetAddExtBattleAttr" in line and "strengthVal*ratio" in line:
        categories.append("buff_add_attr_strength_formula")
    elif "SetAddExtBattleAttr" in line:
        categories.append("buff_add_attr_store")
    if "shieldValue=maxHp*shieldRatio*0.0001" in line:
        categories.append("shield_value_formula")
    if "injuredValue=injuredValue+value*layer" in line:
        categories.append("injure_layer_aggregation")
    if "local ratio=0.01" in line:
        categories.append("percent_display_scale")
    if "TestTimeTF:SetText" in line:
        categories.append("debug_ui_output")
    if any(token in line for token in ["SM_DigitDoorStartGame", "skillVos", "DDSkillVo"]):
        categories.append("unexpected_startgame_skillvos_reference")
    if file_name == "DigitDoorFightComponent.lua" and "GetAddExtBattleAttr" in line:
        categories.append("combat_formula_consumer")
    if file_name == "DigitDoorBaseSkill.lua" and "GetAddExtBattleAttr" in line:
        categories.append("skill_cd_consumer")
    return categories


def _digitdoor_partner_attribute_formatter_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_partner_attribute_formatter_files(root, logic_dir):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        rel_path = path.relative_to(root) if path.is_relative_to(root) else path
        for line_no, line in enumerate(lines, 1):
            categories = _classify_digitdoor_partner_attribute_formatter_line(path.name, line)
            for category in categories:
                rows.append(
                    {
                        "file": path.name,
                        "path": str(rel_path),
                        "line": line_no,
                        "category": category,
                        "snippet": line.strip()[:260],
                    }
                )
    return rows


def _digitdoor_partner_attribute_field_rows(logic_dir: Path) -> list[dict[str, Any]]:
    type_path = logic_dir / "DigitDoorType.lua"
    buff_add_attr_types: dict[str, str] = {}
    if type_path.is_file():
        buff_add_attr_types = _parse_digitdoor_named_string_lua_table(
            type_path.read_text(encoding="utf-8", errors="ignore"),
            "BuffAddAttrType",
        )
    base_fields = [
        ("currentHp", "当前血量", "self.Entity.EntityData:GetCurrentHp()", "raw integer"),
        ("maxHp", "基础血量", "self.Entity.EntityData:GetMaxHp()", "raw integer"),
        ("attack", "基础攻击", "self.Entity.EntityData:GetAttack()", "raw integer"),
        ("pvpAttack", "pvp基础攻击", "self.Entity.EntityData:GetPVPAttack()", "raw integer"),
        ("addDamage", "基础伤害加成", "self.Entity.EntityData:GetAddDamage()", "raw integer"),
        ("attackSpeed", "基础攻速", "self.Entity.EntityData:GetAttackSpeed()", "raw integer"),
        ("critical", "基础暴击", "self.Entity.EntityData:GetCritical()", "raw integer"),
        ("criticalDamage", "基础暴击增伤", "self.Entity.EntityData:GetCriticalDamage()", "raw integer"),
        ("antiCritical", "基础抗暴", "self.Entity.EntityData:GetAntiCritical()", "raw integer"),
        ("increaseDamage", "基础伤害加深", "self.Entity.EntityData:GetIncreaseDamage()", "raw integer"),
        ("reduceDamage", "基础伤害减免", "self.Entity.EntityData:GetReduceDamage()", "raw integer"),
        ("skillDamage", "基础技能增伤", "self.Entity.EntityData:GetSkillDamage()", "raw integer"),
        ("pvpIncreaseDamage", "pvp增伤", "self.Entity.EntityData:GetPVPIncreaseDamage()", "raw integer"),
        ("pvpReduceDamage", "pvp减伤", "self.Entity.EntityData:GetPVPReduceDamage()", "raw integer"),
        ("pvpWinnerReduceDamage", "pvp胜者减伤加成", "self.Entity.EntityData:GetPVPWinnerReduceDamage()", "raw integer"),
    ]
    buff_fields = [
        ("Attack", "额外攻击", "extAttack", "percent display = sum(GetAddExtBattleAttr(ATTACK) * layer) * 0.01"),
        ("AttackSpeed", "额外攻速", "extAttackSpeed", "percent display = sum(GetAddExtBattleAttr(ATKSPEED) * layer) * 0.01"),
        ("CriticalRate", "额外暴击", "extCritical", "percent display = sum(GetAddExtBattleAttr(CRIT) * layer) * 0.01"),
        ("CriticalDamage", "额外暴击增伤", "extCriticalDamage", "percent display = sum(GetAddExtBattleAttr(CRITDAMAGE) * layer) * 0.01"),
        ("AntiCritical", "额外抗暴", "extAntiCritical", "percent display = sum(GetAddExtBattleAttr(ANTICIRT) * layer) * 0.01"),
        ("IncreaseDamage", "额外伤害加深", "extIncreaseDamage", "percent display = sum(GetAddExtBattleAttr(INCDAMAGE) * layer) * 0.01"),
        ("ReduceDamage", "额外伤害减免", "extReduceDamage", "percent display = sum(GetAddExtBattleAttr(REDUCEDAMAGE) * layer) * 0.01"),
        ("AddDamage", "额外伤害加成", "extAddDamage", "percent display = sum(GetAddExtBattleAttr(ADDDAMAGE) * layer) * 0.01"),
        ("SkillDamage", "额外技能增伤", "extSkillDamage", "percent display = sum(GetAddExtBattleAttr(SKILL_DAMAGE) * layer) * 0.01"),
        ("Shield", "当前护盾值", "shieldValue", "maxHp * sum(GetShieldRatio()) * 0.0001"),
        ("Injure", "易伤", "injuredValue", "percent display = sum(GetInjuredValue() * layer) * 0.01"),
    ]
    rows: list[dict[str, Any]] = []
    for key, label, source, formula in base_fields:
        rows.append(
            {
                "group": "base_attr",
                "key": key,
                "type_value": "",
                "display_label": label,
                "runtime_source": source,
                "formula": formula,
                "source_file": "DigitDoorEntityData.lua",
            }
        )
    for key, label, source, formula in buff_fields:
        rows.append(
            {
                "group": "buff_attr",
                "key": key,
                "type_value": buff_add_attr_types.get(key, ""),
                "display_label": label,
                "runtime_source": source,
                "formula": formula,
                "source_file": "DigitDoorPartnerView.lua",
            }
        )
    return rows


def _write_digitdoor_partner_attribute_formatter_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    field_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor partner attribute formatter",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Field rows: {len(field_rows)}",
        f"- Evidence rows: {len(evidence_rows)}",
        "- Scope: static Lua evidence for the debug/editor attribute formatter. It does not read live account state, memory, or network payload values.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Counts",
            "",
        ]
    )
    for key, value in stats.items():
        if isinstance(value, (str, int, bool)):
            lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Field Projection",
            "",
            "| Group | Key | Type Value | Display | Formula |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in field_rows:
        lines.append(
            "| "
            f"{row.get('group', '')} | "
            f"{row.get('key', '')} | "
            f"{row.get('type_value', '')} | "
            f"{row.get('display_label', '')} | "
            f"{row.get('formula', '')} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `SkillEditorBridge.GetDigitDoorPartnerAttributes` and `DigitDoorSceneView` both format current entity views, not packet payload fields.",
            "- `DigitDoorSceneView` is guarded by `LuaGlobal.IsDebugBuild` and writes to `TestTimeTF`, so this is a debug/editor display surface.",
            "- The displayed numbers are still useful: base values come from `DigitDoorEntityData` getters, while buff values aggregate live `BuffDic` AddAttr/Shield/Injure buff data.",
            "- Buff add-attr config values are scaled by `1 + strengthVal * 0.0001`, aggregated by buff layer, and displayed as percent with `* 0.01`; shield value is `maxHp * shieldRatio * 0.0001`.",
            "- This surface does not close `SM_DigitDoorStartGame.skillVos`; it only explains how current local entity/buff attributes are formatted for inspection.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_partner_attribute_formatter_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_rows = _digitdoor_partner_attribute_formatter_rows(root, logic_dir)
    field_rows = _digitdoor_partner_attribute_field_rows(logic_dir)
    category_counts = Counter(str(row.get("category") or "") for row in evidence_rows)
    source_rows = [
        {
            "file": path.name,
            "path": str(path.relative_to(root) if path.is_relative_to(root) else path),
            "role": "logic_source" if path.parent == logic_dir else "core_bridge_source",
        }
        for path in _digitdoor_partner_attribute_formatter_files(root, logic_dir)
    ]
    stats: dict[str, Any] = {
        "source_file_count": len(source_rows),
        "field_row_count": len(field_rows),
        "evidence_row_count": len(evidence_rows),
        "base_attr_field_count": sum(1 for row in field_rows if row.get("group") == "base_attr"),
        "buff_attr_field_count": sum(1 for row in field_rows if row.get("group") == "buff_attr"),
        "debug_guard_rows": category_counts.get("debug_build_guard", 0),
        "debug_ui_output_rows": category_counts.get("debug_ui_output", 0),
        "editor_bridge_entry_rows": category_counts.get("editor_bridge_entry", 0),
        "base_attr_formatter_rows": category_counts.get("base_attr_formatter", 0),
        "buff_attr_formatter_rows": category_counts.get("buff_attr_formatter", 0),
        "buff_add_attr_layer_aggregation_rows": category_counts.get("buff_add_attr_layer_aggregation", 0),
        "buff_add_attr_strength_formula_rows": category_counts.get("buff_add_attr_strength_formula", 0),
        "shield_value_formula_rows": category_counts.get("shield_value_formula", 0),
        "injure_layer_aggregation_rows": category_counts.get("injure_layer_aggregation", 0),
        "unexpected_startgame_skillvos_reference_rows": category_counts.get("unexpected_startgame_skillvos_reference", 0),
    }
    verdict = {
        "formatter_surface_found": stats["base_attr_formatter_rows"] > 0 and stats["buff_attr_formatter_rows"] > 0,
        "debug_or_editor_display_surface": stats["debug_guard_rows"] > 0 and stats["editor_bridge_entry_rows"] > 0,
        "reads_runtime_entity_views": category_counts.get("partner_view_source", 0) > 0
        and category_counts.get("bot_view_source", 0) > 0,
        "buff_add_attr_formula_confirmed": stats["buff_add_attr_layer_aggregation_rows"] > 0
        and stats["buff_add_attr_strength_formula_rows"] > 0,
        "shield_and_injure_formula_confirmed": stats["shield_value_formula_rows"] > 0
        and stats["injure_layer_aggregation_rows"] > 0,
        "mentions_startgame_skillvos_or_ddskillvo": stats["unexpected_startgame_skillvos_reference_rows"] > 0,
        "safe_static_debug_formatter_boundary": stats["unexpected_startgame_skillvos_reference_rows"] == 0,
    }
    _write_tsv(
        out_dir / "partner_attribute_formatter_sources.tsv",
        source_rows,
        ["file", "path", "role"],
    )
    _write_tsv(
        out_dir / "partner_attribute_formatter_fields.tsv",
        field_rows,
        ["group", "key", "type_value", "display_label", "runtime_source", "formula", "source_file"],
    )
    _write_tsv(
        out_dir / "partner_attribute_formatter_evidence.tsv",
        evidence_rows,
        ["file", "path", "line", "category", "snippet"],
    )
    report_path = out_dir / "partner_attribute_formatter_report.md"
    _write_digitdoor_partner_attribute_formatter_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        field_rows=field_rows,
        evidence_rows=evidence_rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["formatter_surface_found"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "sources": str(out_dir / "partner_attribute_formatter_sources.tsv"),
            "fields": str(out_dir / "partner_attribute_formatter_fields.tsv"),
            "evidence": str(out_dir / "partner_attribute_formatter_evidence.tsv"),
        },
    }


def _digitdoor_combat_attribute_consumer_files(logic_dir: Path) -> list[Path]:
    names = [
        "DigitDoorFightComponent.lua",
        "DigitDoorBaseSkill.lua",
        "DigitDoorBuffAddAttr.lua",
        "DigitDoorBuffData.lua",
        "DigitDoorPartnerView.lua",
    ]
    return [path for path in (logic_dir / name for name in names) if path.is_file()]


def _digitdoor_buff_attr_type_from_line(line: str) -> str:
    match = re.search(r"BuffAddAttrType\.([A-Za-z_][A-Za-z0-9_]*)", line)
    return match.group(1) if match else ""


def _digitdoor_combat_attribute_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_combat_attribute_consumer_files(logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            categories: list[tuple[str, str, str]] = []
            field = _digitdoor_buff_attr_type_from_line(line)
            if "function _M.AddDamageResult" in line:
                categories.append(("damage_result_entry", "", "combat_damage_pipeline"))
            if current_function == "_M.AddDamageResult":
                if "GetAttackerFinalAttr(casterView)" in line:
                    categories.append(("attacker_final_attr_entry", "", "combat_damage_pipeline"))
                if "GetDefenseFinalAttr(targetView)" in line:
                    categories.append(("defense_final_attr_entry", "", "combat_damage_pipeline"))
                if "realIncreaseDamage" in line or "damage=(" in line or "pvpDamage=(" in line:
                    categories.append(("damage_formula", "", "combat_damage_pipeline"))
                if "GetBuffListByType(DigitDoorType.SkillBuffType.Injure)" in line:
                    categories.append(("combat_injure_buff_lookup", "Injure", "combat_damage_pipeline"))
                if "GetInjuredValue()" in line:
                    categories.append(("combat_injure_value_read", "Injure", "combat_damage_pipeline"))
                if "CalculateCurrentHp" in line:
                    categories.append(("hp_apply", "", "combat_damage_pipeline"))
                if "DigitDoorHurtDataExecute" in line:
                    categories.append(("hurt_projection", "", "combat_damage_pipeline"))
            if current_function == "_M.GetShieldCost":
                if "GetBuffListByType(DigitDoorType.SkillBuffType.Shield)" in line:
                    categories.append(("combat_shield_buff_lookup", "Shield", "combat_damage_pipeline"))
                if "GetShieldRatio()" in line:
                    categories.append(("combat_shield_ratio_read", "Shield", "combat_damage_pipeline"))
                if "SetShieldRatio" in line:
                    categories.append(("combat_shield_ratio_mutation", "Shield", "combat_damage_pipeline"))
            if current_function == "_M.GetAttackerFinalAttr" and "GetAddExtBattleAttr" in line:
                categories.append(("combat_attacker_addattr_read", field, "combat_attr_consumer"))
            if current_function == "_M.GetDefenseFinalAttr" and "GetAddExtBattleAttr" in line:
                categories.append(("combat_defense_addattr_read", field, "combat_attr_consumer"))
            if current_function == "_M.CalculateSkillSpeed":
                if "GetBuffListByType(DigitDoorType.SkillBuffType.AddAttr)" in line:
                    categories.append(("combat_skill_speed_addattr_lookup", "AttackSpeed", "combat_skill_timing"))
                if "GetAddExtBattleAttr" in line:
                    categories.append(("combat_skill_speed_addattr_read", field, "combat_skill_timing"))
                if "self.speed=atkSpeed*(1/(1+extAtkSpeed*0.0001))" in line:
                    categories.append(("combat_skill_speed_formula", "AttackSpeed", "combat_skill_timing"))
            if current_function == "_M.FormatBuffAddAttrOutput":
                if "GetAddExtBattleAttr" in line:
                    categories.append(("debug_formatter_addattr_read", field, "debug_formatter_reference"))
                if "GetShieldRatio()" in line:
                    categories.append(("debug_formatter_shield_read", "Shield", "debug_formatter_reference"))
                if "GetInjuredValue()" in line:
                    categories.append(("debug_formatter_injure_read", "Injure", "debug_formatter_reference"))
            if current_function == "_M.Start" and path.name == "DigitDoorBuffAddAttr.lua":
                if "LuaGlobal.IsUnityEditor" in line:
                    categories.append(("unity_editor_guard", "", "editor_only_mutation"))
                if field == "MaxHp":
                    categories.append(("editor_maxhp_addattr_read", "MaxHp", "editor_only_mutation"))
                if "SetMaxHp" in line or "SetCurrentHp" in line:
                    categories.append(("editor_maxhp_mutation", "MaxHp", "editor_only_mutation"))
            for category, category_field, role in categories:
                rows.append(
                    {
                        "category": category,
                        "role": role,
                        "field": category_field,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": stripped,
                    }
                )
    return rows


def _digitdoor_combat_attribute_field_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_roles: dict[str, set[str]] = defaultdict(set)
    field_categories: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        field = str(row.get("field") or "")
        if not field:
            continue
        field_roles[field].add(str(row.get("role") or ""))
        field_categories[field].add(str(row.get("category") or ""))
    field_notes = {
        "Attack": "attacker damage formula extAttack",
        "AttackSpeed": "normal skill timing speed modifier",
        "CriticalRate": "attacker critical chance after anti-critical",
        "CriticalDamage": "attacker critical damage multiplier",
        "AntiCritical": "defender anti-critical",
        "IncreaseDamage": "attacker increase damage term",
        "ReduceDamage": "defender reduce damage term",
        "AddDamage": "attacker additive damage multiplier",
        "SkillDamage": "attacker skill damage multiplier",
        "Shield": "target shield cost before HP apply",
        "Injure": "target injure multiplier against bots",
        "MaxHp": "UnityEditor-only max HP mutation in DigitDoorBuffAddAttr",
    }
    return [
        {
            "field": field,
            "roles": " | ".join(sorted(role for role in field_roles[field] if role)),
            "categories": " | ".join(sorted(field_categories[field])),
            "note": field_notes.get(field, ""),
        }
        for field in sorted(field_roles)
    ]


def _write_digitdoor_combat_attribute_consumer_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    field_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor combat attribute consumer",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Field rows: {len(field_rows)}",
        f"- Evidence rows: {len(evidence_rows)}",
        "- Scope: static Lua evidence for local DigitDoor combat calculation consumers. It does not patch, inject, replay packets, or assert server authority.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Field Usage",
            "",
            "| Field | Roles | Categories | Note |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in field_rows:
        lines.append(
            "| "
            f"{_md_table_cell(row.get('field', ''))} | "
            f"{_md_table_cell(row.get('roles', ''))} | "
            f"{_md_table_cell(row.get('categories', ''))} | "
            f"{_md_table_cell(row.get('note', ''))} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `DigitDoorFightComponent:AddDamageResult` is the local damage aggregation path and consumes `GetAttackerFinalAttr`, `GetDefenseFinalAttr`, Injure, Shield, damage share, HP apply, and hurt-data projection.",
            "- `GetAttackerFinalAttr` consumes AddAttr fields `Attack/CriticalRate/CriticalDamage/IncreaseDamage/AddDamage/SkillDamage`; `GetDefenseFinalAttr` consumes `AntiCritical/ReduceDamage`.",
            "- `DigitDoorBaseSkill:CalculateSkillSpeed` consumes AddAttr `AttackSpeed` and applies `atkSpeed * (1 / (1 + extAtkSpeed * 0.0001))` to normal-skill timing.",
            "- `GetShieldCost` consumes and mutates Shield ratio before HP damage is applied; Injure is read from target buffs and multiplies damage against DigitDoor bots.",
            "- `MaxHp` appears under `LuaGlobal.IsUnityEditor` in `DigitDoorBuffAddAttr`, so treat it as an editor-only mutation on the visible Lua surface, not the normal runtime combat path.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_combat_attribute_consumer_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_rows = _digitdoor_combat_attribute_rows(root, logic_dir)
    field_rows = _digitdoor_combat_attribute_field_rows(evidence_rows)
    category_counts = Counter(str(row.get("category") or "") for row in evidence_rows)
    combat_fields = {
        str(row.get("field") or "")
        for row in evidence_rows
        if str(row.get("role") or "") in {"combat_attr_consumer", "combat_skill_timing", "combat_damage_pipeline"}
        and str(row.get("field") or "")
    }
    display_fields = {
        str(row.get("field") or "")
        for row in evidence_rows
        if str(row.get("role") or "") == "debug_formatter_reference" and str(row.get("field") or "")
    }
    expected_combat_fields = {
        "Attack",
        "AttackSpeed",
        "CriticalRate",
        "CriticalDamage",
        "AntiCritical",
        "IncreaseDamage",
        "ReduceDamage",
        "AddDamage",
        "SkillDamage",
        "Shield",
        "Injure",
    }
    stats = {
        "source_file_count": len(_digitdoor_combat_attribute_consumer_files(logic_dir)),
        "evidence_row_count": len(evidence_rows),
        "field_row_count": len(field_rows),
        "combat_field_count": len(combat_fields),
        "debug_formatter_field_count": len(display_fields),
        "combat_attacker_addattr_rows": category_counts.get("combat_attacker_addattr_read", 0),
        "combat_defense_addattr_rows": category_counts.get("combat_defense_addattr_read", 0),
        "combat_skill_speed_rows": category_counts.get("combat_skill_speed_addattr_read", 0),
        "combat_shield_rows": category_counts.get("combat_shield_ratio_read", 0),
        "combat_injure_rows": category_counts.get("combat_injure_value_read", 0),
        "damage_formula_rows": category_counts.get("damage_formula", 0),
        "hp_apply_rows": category_counts.get("hp_apply", 0),
        "hurt_projection_rows": category_counts.get("hurt_projection", 0),
        "editor_maxhp_rows": category_counts.get("editor_maxhp_addattr_read", 0)
        + category_counts.get("editor_maxhp_mutation", 0),
    }
    verdict = {
        "combat_damage_pipeline_found": category_counts.get("damage_result_entry", 0) > 0
        and stats["hp_apply_rows"] > 0,
        "addattr_fields_consumed_by_combat": expected_combat_fields.issubset(combat_fields),
        "debug_formatter_fields_overlap_combat": bool(display_fields) and display_fields.issubset(combat_fields | {"MaxHp"}),
        "attack_speed_affects_skill_timing": category_counts.get("combat_skill_speed_formula", 0) > 0,
        "shield_consumed_before_hp_apply": stats["combat_shield_rows"] > 0 and stats["hp_apply_rows"] > 0,
        "injure_consumed_in_damage_formula": stats["combat_injure_rows"] > 0 and stats["damage_formula_rows"] > 0,
        "maxhp_visible_surface_is_editor_only": category_counts.get("unity_editor_guard", 0) > 0
        and stats["editor_maxhp_rows"] > 0,
    }
    _write_tsv(
        out_dir / "combat_attribute_consumer_fields.tsv",
        field_rows,
        ["field", "roles", "categories", "note"],
    )
    _write_tsv(
        out_dir / "combat_attribute_consumer_evidence.tsv",
        evidence_rows,
        ["category", "role", "field", "file", "line", "function", "snippet"],
    )
    report_path = out_dir / "combat_attribute_consumer_report.md"
    _write_digitdoor_combat_attribute_consumer_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        field_rows=field_rows,
        evidence_rows=evidence_rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["combat_damage_pipeline_found"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "fields": str(out_dir / "combat_attribute_consumer_fields.tsv"),
            "evidence": str(out_dir / "combat_attribute_consumer_evidence.tsv"),
        },
    }


def _digitdoor_gameplayer_settlement_files(root: Path, logic_dir: Path) -> list[Path]:
    names = [
        "DigitDoorNetLogic.lua",
        "DigitDoorMgr.lua",
        "DigitDoorData.lua",
        "DigitDoorEntityMgr.lua",
        "DigitDoorFightComponent.lua",
        "DigitDoorResultInfoView.lua",
        "DigitDoorSceneView.lua",
        "DigitDoorPauseView.lua",
    ]
    candidates = [logic_dir / name for name in names]
    patterns = [
        "by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorGamePlayer.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorGamePlayer.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/DDBossVo.lua",
    ]
    for pattern in patterns:
        candidates.extend(root.glob(pattern))
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _digitdoor_gameplayer_settlement_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_gameplayer_settlement_files(root, logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            categories: list[tuple[str, str, str, str]] = []
            if path.name == "CM_DigitDoorGamePlayer.lua":
                for field in ["currWave", "wavePercent", "killNum", "bossVoList"]:
                    if f"self.{field}" in line:
                        categories.append(("request_packet_schema", "request", field, "CM_DigitDoorGamePlayer"))
                if "writeList(self.bossVoList)" in line or "readMessageList2List(self.bossVoList)" in line:
                    categories.append(("request_boss_vo_list_wire", "request", "bossVoList", "CM_DigitDoorGamePlayer"))
            elif path.name == "SM_DigitDoorGamePlayer.lua":
                for field in ["finishWave", "rewardResults", "passLevelVOS", "levelId", "gameType", "isSkipLevel"]:
                    if f"self.{field}" in line:
                        categories.append(("response_packet_schema", "response", field, "SM_DigitDoorGamePlayer"))
                if "writeIntList(self.passLevelVOS)" in line:
                    categories.append(("response_pass_level_int_list_wire", "response", "passLevelVOS", "SM_DigitDoorGamePlayer"))
                if "writeList(self.rewardResults)" in line or "readMessageList2List(self.rewardResults)" in line:
                    categories.append(("response_reward_result_list_wire", "response", "rewardResults", "SM_DigitDoorGamePlayer"))
            elif path.name == "DDBossVo.lua":
                for field in ["id", "hp"]:
                    if f"self.{field}" in line:
                        categories.append(("boss_vo_schema", "request", field, "DDBossVo"))
                if "writeDouble(self.hp)" in line or "readDouble()" in line:
                    categories.append(("boss_hp_double_wire", "request", "hp", "DDBossVo"))

            if path.name == "DigitDoorNetLogic.lua":
                if current_function == "_M.CM_DigitDoorGamePlayerFun":
                    if "GetMessageFromPools(_CM_DigitDoorGamePlayer)" in line:
                        categories.append(("request_pool_message", "request", "", "DigitDoorNetLogic"))
                    if "CM_DigitDoorGamePlayer.currWave" in line:
                        categories.append(("request_fill_curr_wave", "request", "currWave", "DigitDoorNetLogic"))
                    if "CM_DigitDoorGamePlayer.wavePercent" in line:
                        categories.append(("request_fill_wave_percent", "request", "wavePercent", "DigitDoorNetLogic"))
                    if "CM_DigitDoorGamePlayer.killNum" in line:
                        categories.append(("request_fill_kill_num", "request", "killNum", "DigitDoorNetLogic"))
                    if "GetTotalKillSmallMonsterNum()" in line:
                        categories.append(("request_kill_num_from_local_counter", "request", "killNum", "DigitDoorNetLogic"))
                    if "CM_DigitDoorGamePlayer.bossVoList" in line:
                        categories.append(("request_fill_boss_vo_list", "request", "bossVoList", "DigitDoorNetLogic"))
                    if "GetTotalBossDamageList()" in line:
                        categories.append(("request_boss_vo_from_local_hp_percent", "request", "bossVoList", "DigitDoorNetLogic"))
                    if "F_SendMsg(CM_DigitDoorGamePlayer)" in line:
                        categories.append(("request_send", "request", "", "DigitDoorNetLogic"))
                if current_function == "_M.SM_DigitDoorGamePlayerFun":
                    if "DigitDoorExitGame(msg)" in line:
                        categories.append(("response_handler_exit_game", "response", "", "DigitDoorNetLogic"))
                    if "msg.isSkipLevel" in line:
                        categories.append(("response_skip_branch", "response", "isSkipLevel", "DigitDoorNetLogic"))
                    if "msg.rewardResults" in line:
                        categories.append(("response_reward_results_popup", "response", "rewardResults", "DigitDoorNetLogic"))

            if path.name == "DigitDoorMgr.lua":
                if "function _M.DigitDoorExitGame" in line:
                    categories.append(("exit_game_entry", "response", "", "DigitDoorMgr"))
                if current_function == "_M.DigitDoorExitGame":
                    if "SetIsSkipLevel(msg.isSkipLevel)" in line:
                        categories.append(("exit_game_store_skip_state", "response", "isSkipLevel", "DigitDoorMgr"))
                    if "SetFinishLevelInfo(msg)" in line:
                        categories.append(("exit_game_store_finish_level_info", "response", "passLevelVOS", "DigitDoorMgr"))
                if current_function == "_M.ReqFinishGame":
                    if "CM_DigitDoorGamePlayerFun(wave,wavePercent)" in line:
                        categories.append(("finish_request_send_entry", "request", "currWave|wavePercent", "DigitDoorMgr"))
                    if "V_IsReqFinishGame" in line:
                        categories.append(("finish_request_duplicate_guard", "request", "", "DigitDoorMgr"))

            if path.name == "DigitDoorData.lua":
                if "function _M.SetFinishLevelInfo" in line:
                    categories.append(("finish_level_info_entry", "response", "", "DigitDoorData"))
                if current_function == "_M.SetFinishLevelInfo" and "InitNewLevelDic(msg.passLevelVOS)" in line:
                    categories.append(("pass_level_state_from_response", "response", "passLevelVOS", "DigitDoorData"))
                if "GetDamageCache()" in line:
                    categories.append(("local_damage_cache_read_for_display", "local", "DamageCache", "DigitDoorData"))

            if path.name == "DigitDoorEntityMgr.lua":
                if "function _M.GetTotalBossDamageList" in line:
                    categories.append(("boss_damage_list_entry", "request", "bossVoList", "DigitDoorEntityMgr"))
                if current_function == "_M.GetTotalBossDamageList":
                    if "DDBossVo" in line or "DDBossVo.new" in line:
                        categories.append(("boss_vo_construct", "request", "bossVoList", "DigitDoorEntityMgr"))
                    if "GetMaxHp()" in line:
                        categories.append(("boss_max_hp_read", "request", "bossVoList", "DigitDoorEntityMgr"))
                    if "GetCurrentHp()/maxHp*10000" in line:
                        categories.append(("boss_hp_percent_formula", "request", "bossVoList", "DigitDoorEntityMgr"))
                    if "bossVoList:Add(vo)" in line:
                        categories.append(("boss_vo_list_add", "request", "bossVoList", "DigitDoorEntityMgr"))
                if "function _M.GetTotalKillSmallMonsterNum" in line:
                    categories.append(("kill_small_monster_entry", "request", "killNum", "DigitDoorEntityMgr"))
                if current_function == "_M.GetTotalKillSmallMonsterNum" and (
                    "totalKillMonsterNum" in line and "skillMonsterNum" in line and "totalKillBossNum" in line
                ):
                    categories.append(("kill_small_monster_formula", "request", "killNum", "DigitDoorEntityMgr"))
                if "totalKillMonsterNum=self.totalKillMonsterNum+1" in line:
                    categories.append(("kill_total_monster_increment", "request", "killNum", "DigitDoorEntityMgr"))
                if "skillMonsterNum=self.skillMonsterNum+1" in line:
                    categories.append(("kill_skill_monster_increment", "request", "killNum", "DigitDoorEntityMgr"))
                if "totalKillBossNum=self.totalKillBossNum+1" in line:
                    categories.append(("kill_boss_increment", "request", "killNum", "DigitDoorEntityMgr"))
                if "ReqFinishGame()" in line:
                    categories.append(("finish_request_trigger", "request", "", "DigitDoorEntityMgr"))

            if path.name == "DigitDoorFightComponent.lua":
                if "self.DamageCache=Dictionary.new()" in line:
                    categories.append(("damage_cache_init", "local", "DamageCache", "DigitDoorFightComponent"))
                if "DamageCache" in line and "(pvpDamage>0 and pvpDamage or damage)" in line:
                    categories.append(("damage_cache_accumulate", "local", "DamageCache", "DigitDoorFightComponent"))
                if "function _M.GetDamageCache" in line:
                    categories.append(("damage_cache_getter", "local", "DamageCache", "DigitDoorFightComponent"))

            if path.name == "DigitDoorResultInfoView.lua":
                if "msg.passLevelVOS" in line and "Contains(msg.levelId)" in line:
                    categories.append(("result_win_state_from_pass_level", "response", "passLevelVOS|levelId", "DigitDoorResultInfoView"))
                if "msg.rewardResults" in line:
                    categories.append(("result_reward_display_from_response", "response", "rewardResults", "DigitDoorResultInfoView"))
                if "finishWave=msg.finishWave" in line:
                    categories.append(("result_finish_wave_from_response", "response", "finishWave", "DigitDoorResultInfoView"))

            if path.name in {"DigitDoorSceneView.lua", "DigitDoorPauseView.lua"} and "ReqFinishGame" in line:
                categories.append(("finish_request_trigger", "request", "", path.stem))

            for category, direction, field, source in categories:
                rows.append(
                    {
                        "category": category,
                        "direction": direction,
                        "field": field,
                        "source": source,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": stripped,
                    }
                )
    return rows


def _digitdoor_gameplayer_settlement_field_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_notes = {
        "currWave": "request progress snapshot wave",
        "wavePercent": "request progress snapshot percent",
        "killNum": "request local small-monster kill summary",
        "bossVoList": "request local boss hp-percent summary",
        "finishWave": "response result display wave",
        "rewardResults": "response final reward display list",
        "passLevelVOS": "response passed-level state/list used for win state",
        "levelId": "response level id used with passLevelVOS",
        "gameType": "response game type for result display",
        "isSkipLevel": "response skip branch flag",
        "id": "DDBossVo boss id",
        "hp": "DDBossVo boss hp percentage as double",
        "DamageCache": "local damage ranking/display cache; not part of CM_DigitDoorGamePlayer schema",
    }
    by_field: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"directions": set(), "categories": set(), "sources": set()})
    for row in rows:
        fields = [item for item in str(row.get("field") or "").split("|") if item]
        for field in fields:
            by_field[field]["directions"].add(str(row.get("direction") or ""))
            by_field[field]["categories"].add(str(row.get("category") or ""))
            by_field[field]["sources"].add(str(row.get("source") or ""))
    return [
        {
            "field": field,
            "directions": " | ".join(sorted(item for item in values["directions"] if item)),
            "sources": " | ".join(sorted(item for item in values["sources"] if item)),
            "categories": " | ".join(sorted(values["categories"])),
            "note": field_notes.get(field, ""),
        }
        for field, values in sorted(by_field.items())
    ]


def _write_digitdoor_gameplayer_settlement_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    field_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor GamePlayer settlement boundary",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Field rows: {len(field_rows)}",
        f"- Evidence rows: {len(evidence_rows)}",
        "- Scope: static Lua evidence for the DigitDoor battle-settlement request/response boundary. It does not modify traffic, replay packets, or assert server behavior beyond visible client code.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Field Boundary",
            "",
            "| Field | Direction | Sources | Note |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in field_rows:
        lines.append(
            "| "
            f"{_md_table_cell(row.get('field', ''))} | "
            f"{_md_table_cell(row.get('directions', ''))} | "
            f"{_md_table_cell(row.get('sources', ''))} | "
            f"{_md_table_cell(row.get('note', ''))} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `CM_DigitDoorGamePlayer` sends a local progress snapshot: `currWave`, `wavePercent`, `killNum`, and `bossVoList`.",
            "- `killNum` is derived from local counters as `totalKillMonsterNum - skillMonsterNum - totalKillBossNum`; `bossVoList` uses `DDBossVo(id,hp)` where `hp` is `Floor(currentHp / maxHp * 10000)` for visible boss entities.",
            "- `SM_DigitDoorGamePlayer` returns final settlement/display state: `finishWave`, `rewardResults`, `passLevelVOS`, `levelId`, `gameType`, and `isSkipLevel`.",
            "- `DigitDoorData:SetFinishLevelInfo` rebuilds passed-level state only from `msg.passLevelVOS`; `DigitDoorResultInfoView` uses `passLevelVOS:Contains(levelId)` for win/fail display and `msg.rewardResults` for rewards.",
            "- `DigitDoorFightComponent.DamageCache` is local display/ranking data and has no visible field in `CM_DigitDoorGamePlayer`.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_gameplayer_settlement_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_rows = _digitdoor_gameplayer_settlement_rows(root, logic_dir)
    field_rows = _digitdoor_gameplayer_settlement_field_rows(evidence_rows)
    category_counts = Counter(str(row.get("category") or "") for row in evidence_rows)
    stats = {
        "source_file_count": len(_digitdoor_gameplayer_settlement_files(root, logic_dir)),
        "evidence_row_count": len(evidence_rows),
        "field_row_count": len(field_rows),
        "request_schema_rows": category_counts.get("request_packet_schema", 0),
        "response_schema_rows": category_counts.get("response_packet_schema", 0),
        "request_fill_rows": sum(
            category_counts.get(category, 0)
            for category in [
                "request_fill_curr_wave",
                "request_fill_wave_percent",
                "request_fill_kill_num",
                "request_fill_boss_vo_list",
            ]
        ),
        "local_kill_formula_rows": category_counts.get("kill_small_monster_formula", 0),
        "boss_hp_percent_formula_rows": category_counts.get("boss_hp_percent_formula", 0),
        "response_reward_display_rows": category_counts.get("response_reward_results_popup", 0)
        + category_counts.get("result_reward_display_from_response", 0),
        "pass_level_state_rows": category_counts.get("pass_level_state_from_response", 0)
        + category_counts.get("result_win_state_from_pass_level", 0),
        "damage_cache_rows": sum(count for category, count in category_counts.items() if "damage_cache" in category),
    }
    verdict = {
        "request_progress_snapshot_confirmed": stats["request_schema_rows"] >= 4 and stats["request_fill_rows"] >= 4,
        "request_uses_local_kill_and_boss_hp_summary": stats["local_kill_formula_rows"] > 0
        and stats["boss_hp_percent_formula_rows"] > 0,
        "response_settlement_fields_confirmed": stats["response_schema_rows"] >= 6,
        "response_rewards_drive_display": stats["response_reward_display_rows"] > 0,
        "response_pass_level_drives_state_and_win_display": stats["pass_level_state_rows"] >= 2,
        "local_damage_cache_not_sent_in_gameplayer_schema": stats["damage_cache_rows"] > 0
        and not any(row.get("source") == "CM_DigitDoorGamePlayer" and row.get("field") == "DamageCache" for row in evidence_rows),
    }
    _write_tsv(
        out_dir / "gameplayer_settlement_fields.tsv",
        field_rows,
        ["field", "directions", "sources", "categories", "note"],
    )
    _write_tsv(
        out_dir / "gameplayer_settlement_evidence.tsv",
        evidence_rows,
        ["category", "direction", "field", "source", "file", "line", "function", "snippet"],
    )
    report_path = out_dir / "gameplayer_settlement_report.md"
    _write_digitdoor_gameplayer_settlement_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        field_rows=field_rows,
        evidence_rows=evidence_rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["request_progress_snapshot_confirmed"] and verdict["response_settlement_fields_confirmed"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "fields": str(out_dir / "gameplayer_settlement_fields.tsv"),
            "evidence": str(out_dir / "gameplayer_settlement_evidence.tsv"),
        },
    }


def _digitdoor_info_snapshot_files(root: Path, logic_dir: Path) -> list[Path]:
    names = [
        "DigitDoorNetLogic.lua",
        "DigitDoorMgr.lua",
        "DigitDoorData.lua",
        "DigitDoorInfoPanel.lua",
        "DigitDoorType.lua",
    ]
    candidates = [logic_dir / name for name in names]
    patterns = [
        "by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorInfo.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorInfo.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/DDPartnerVO.lua",
    ]
    for pattern in patterns:
        candidates.extend(root.glob(pattern))
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _digitdoor_info_snapshot_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_info_snapshot_files(root, logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            categories: list[tuple[str, str, str, str]] = []

            if path.name == "CM_DigitDoorInfo.lua":
                if "return 91620" in line:
                    categories.append(("request_packet_id", "request", "", "CM_DigitDoorInfo"))
                if current_function == "_M._init_" and "_super_._init_" in line:
                    categories.append(("request_empty_init_super_only", "request", "", "CM_DigitDoorInfo"))
                if current_function == "_M.reading" and "_super_.reading" in line:
                    categories.append(("request_empty_reading_super_only", "request", "", "CM_DigitDoorInfo"))
                if current_function == "_M.writing" and "_super_.writing" in line:
                    categories.append(("request_empty_writing_super_only", "request", "", "CM_DigitDoorInfo"))

            elif path.name == "SM_DigitDoorInfo.lua":
                for field in ["passList", "ddPartnerVOList"]:
                    if f"self.{field}" in line:
                        categories.append(("response_packet_schema", "response", field, "SM_DigitDoorInfo"))
                if "readMessageList2List(self.passList)" in line:
                    categories.append(("response_pass_list_read_helper", "response", "passList", "SM_DigitDoorInfo"))
                if "writeIntList(self.passList)" in line:
                    categories.append(("response_pass_list_int_wire", "response", "passList", "SM_DigitDoorInfo"))
                if "readMessageList2List(self.ddPartnerVOList)" in line:
                    categories.append(("response_partner_vo_list_read_helper", "response", "ddPartnerVOList", "SM_DigitDoorInfo"))
                if "writeList(self.ddPartnerVOList)" in line:
                    categories.append(("response_partner_vo_list_wire", "response", "ddPartnerVOList", "SM_DigitDoorInfo"))
                if "return 91621" in line:
                    categories.append(("response_packet_id", "response", "", "SM_DigitDoorInfo"))

            elif path.name == "DDPartnerVO.lua":
                for field in ["id", "lv"]:
                    if f"self.{field}" in line:
                        categories.append(("partner_vo_schema", "response", field, "DDPartnerVO"))
                if "readInt()" in line and "self.id" in line:
                    categories.append(("partner_vo_id_int_wire", "response", "id", "DDPartnerVO"))
                if "readInt()" in line and "self.lv" in line:
                    categories.append(("partner_vo_lv_int_wire", "response", "lv", "DDPartnerVO"))
                if "writeInt(self.id)" in line:
                    categories.append(("partner_vo_id_int_wire", "response", "id", "DDPartnerVO"))
                if "writeInt(self.lv)" in line:
                    categories.append(("partner_vo_lv_int_wire", "response", "lv", "DDPartnerVO"))
                if "return 91600" in line:
                    categories.append(("partner_vo_packet_id", "response", "", "DDPartnerVO"))

            if path.name == "DigitDoorNetLogic.lua":
                if "F_Register(_CM_DigitDoorInfo:getId()" in line:
                    categories.append(("request_register", "request", "", "DigitDoorNetLogic"))
                if "F_Register(_SM_DigitDoorInfo:getId()" in line:
                    categories.append(("response_register", "response", "", "DigitDoorNetLogic"))
                if current_function == "_M.CM_DigitDoorInfoFun":
                    if "GetMessageFromPools(_CM_DigitDoorInfo)" in line:
                        categories.append(("request_pool_message", "request", "", "DigitDoorNetLogic"))
                    if "F_SendMsg(CM_DigitDoorInfo)" in line:
                        categories.append(("request_send", "request", "", "DigitDoorNetLogic"))
                if current_function == "_M.SM_DigitDoorInfoFun":
                    if "msg.code==0" in line:
                        categories.append(("response_success_guard", "response", "code", "DigitDoorNetLogic"))
                    if "SeDigitDoorInfoFun(msg)" in line:
                        categories.append(("response_store_info_snapshot", "response", "V_DigitDoorInfo", "DigitDoorNetLogic"))
                    if "DigitDoorInfoUpdate" in line and "RaiseEvent" in line:
                        categories.append(("response_raise_info_update", "response", "DigitDoorInfoUpdate", "DigitDoorNetLogic"))
                    if "UpdateRedDot()" in line:
                        categories.append(("response_update_red_dot", "response", "", "DigitDoorNetLogic"))

            if path.name == "DigitDoorMgr.lua":
                if "CM_DigitDoorInfoFun()" in line:
                    field = "activity_refresh" if current_function == "_M.UpdateActivationActivityVO" else "activity_state_change"
                    categories.append(("request_trigger_activity_info", "request", field, "DigitDoorMgr"))
                if "RaiseRedDotEvent" in line and "DigitDoor_Rank_Reward" in line:
                    categories.append(("request_trigger_rank_red_dot", "request", "DigitDoor_Rank_Reward", "DigitDoorMgr"))

            if path.name == "DigitDoorData.lua":
                if "function _M.SeDigitDoorInfoFun" in line:
                    categories.append(("info_snapshot_store_entry", "response", "V_DigitDoorInfo", "DigitDoorData"))
                if current_function == "_M.SeDigitDoorInfoFun":
                    if "ClearActivityData()" in line:
                        categories.append(("info_clear_previous_activity_data", "response", "V_NewLevelIdDic|serverData", "DigitDoorData"))
                    if "V_DigitDoorInfo=msg" in line:
                        categories.append(("info_snapshot_store_msg", "response", "V_DigitDoorInfo", "DigitDoorData"))
                    if "UpdateDDPartnerVos(msg.ddPartnerVOList)" in line:
                        categories.append(("info_partner_list_to_state", "response", "ddPartnerVOList", "DigitDoorData"))
                    if "InitNewLevelDic(msg.passList)" in line:
                        categories.append(("info_pass_list_to_state", "response", "passList", "DigitDoorData"))
                if current_function == "_M.ClearActivityData":
                    if "V_NewLevelIdDic=nil" in line:
                        categories.append(("info_clear_pass_level_state", "response", "V_NewLevelIdDic", "DigitDoorData"))
                    if "SetServerData(nil)" in line:
                        categories.append(("info_clear_partner_server_state", "response", "serverData", "DigitDoorData"))
                if current_function == "_M.GeDigitDoorInfo" and "return self.V_DigitDoorInfo" in line:
                    categories.append(("info_snapshot_getter", "response", "V_DigitDoorInfo", "DigitDoorData"))
                if "function _M.UpdateDDPartnerVos" in line:
                    categories.append(("partner_state_update_entry", "response", "ddPartnerVOList", "DigitDoorData"))
                if current_function == "_M.UpdateDDPartnerVos" and "UpdateOneDigitDoorCharacterVo(v)" in line:
                    categories.append(("partner_state_iterate_list", "response", "ddPartnerVOList", "DigitDoorData"))
                if current_function == "_M.UpdateOneDigitDoorCharacterVo":
                    if "GetDigitDoorCharacterVoById(msgVo.id)" in line:
                        categories.append(("partner_state_lookup_by_id", "response", "id", "DigitDoorData"))
                    if "SetServerData(msgVo)" in line:
                        categories.append(("partner_state_store_server_vo", "response", "serverData", "DigitDoorData"))
                if "function _M.InitNewLevelDic" in line:
                    categories.append(("pass_level_state_update_entry", "response", "passList", "DigitDoorData"))
                if current_function == "_M.InitNewLevelDic":
                    if "Cipairs(passList)" in line:
                        categories.append(("pass_level_state_iterate_pass_list", "response", "passList", "DigitDoorData"))
                    if "ConfigName.DigitDoor_Level" in line:
                        categories.append(("pass_level_state_lookup_level_cfg", "response", "passList", "DigitDoorData"))
                    if "V_NewLevelIdDic[levelCfg.type][levelCfg.layer]=true" in line:
                        categories.append(("pass_level_state_store_type_layer", "response", "V_NewLevelIdDic", "DigitDoorData"))

            if path.name == "DigitDoorInfoPanel.lua":
                if "onUpdateViewFunc=function" in line:
                    categories.append(("panel_info_update_callback_entry", "ui", "DigitDoorInfoUpdate", "DigitDoorInfoPanel"))
                if current_function == "" and "UpdateViewData()" in line:
                    categories.append(("panel_refresh_view_data", "ui", "DigitDoorInfoUpdate", "DigitDoorInfoPanel"))
                if "AddEventHandler(DigitDoorType.EventType.DigitDoorInfoUpdate" in line:
                    categories.append(("panel_listen_info_update", "ui", "DigitDoorInfoUpdate", "DigitDoorInfoPanel"))
                if "RemoveEventHandler(DigitDoorType.EventType.DigitDoorInfoUpdate" in line:
                    categories.append(("panel_remove_info_update", "ui", "DigitDoorInfoUpdate", "DigitDoorInfoPanel"))

            if path.name == "DigitDoorType.lua" and 'DigitDoorInfoUpdate="DigitDoorInfoUpdate"' in line:
                categories.append(("event_constant_defined", "shared", "DigitDoorInfoUpdate", "DigitDoorType"))

            for category, direction, field, source in categories:
                rows.append(
                    {
                        "category": category,
                        "direction": direction,
                        "field": field,
                        "source": source,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": stripped,
                    }
                )
    return rows


def _digitdoor_info_snapshot_field_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_notes = {
        "passList": "SM_DigitDoorInfo passed-level id list; `InitNewLevelDic` maps level config type/layer to finished state.",
        "ddPartnerVOList": "SM_DigitDoorInfo partner server-state list; each entry is a DDPartnerVO.",
        "id": "DDPartnerVO partner id used to locate DigitDoorCharacterVo.",
        "lv": "DDPartnerVO partner level.",
        "V_DigitDoorInfo": "cached whole info snapshot message.",
        "V_NewLevelIdDic": "client-side finished-level lookup keyed by level type/layer.",
        "serverData": "per-character server state set from DDPartnerVO and cleared before fresh info snapshots.",
        "DigitDoorInfoUpdate": "model event raised after successful info response and listened to by DigitDoorInfoPanel.",
        "DigitDoor_Rank_Reward": "red-dot refresh side effect around activity/info updates.",
        "code": "response success guard before applying snapshot.",
        "activity_refresh": "activity refresh trigger for CM_DigitDoorInfoFun.",
        "activity_state_change": "activity state-change trigger for CM_DigitDoorInfoFun.",
    }
    by_field: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"directions": set(), "categories": set(), "sources": set()})
    for row in rows:
        for field in [item for item in str(row.get("field") or "").split("|") if item]:
            by_field[field]["directions"].add(str(row.get("direction") or ""))
            by_field[field]["categories"].add(str(row.get("category") or ""))
            by_field[field]["sources"].add(str(row.get("source") or ""))
    return [
        {
            "field": field,
            "directions": " | ".join(sorted(item for item in values["directions"] if item)),
            "sources": " | ".join(sorted(item for item in values["sources"] if item)),
            "categories": " | ".join(sorted(values["categories"])),
            "note": field_notes.get(field, ""),
        }
        for field, values in sorted(by_field.items())
    ]


def _write_digitdoor_info_snapshot_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    field_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor Info snapshot boundary",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Field rows: {len(field_rows)}",
        f"- Evidence rows: {len(evidence_rows)}",
        "- Scope: static Lua evidence for the DigitDoor activity-info request/response boundary. It does not modify traffic, replay packets, or assert server behavior beyond visible client code.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Field Boundary",
            "",
            "| Field | Direction | Sources | Note |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in field_rows:
        lines.append(
            "| "
            f"{_md_table_cell(row.get('field', ''))} | "
            f"{_md_table_cell(row.get('directions', ''))} | "
            f"{_md_table_cell(row.get('sources', ''))} | "
            f"{_md_table_cell(row.get('note', ''))} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `CM_DigitDoorInfo` is a no-payload activity info request: the client allocates it from the message pool and immediately sends it.",
            "- `SM_DigitDoorInfo` is the authoritative snapshot carrier for this entry point: `passList` rebuilds passed-level lookup state, and `ddPartnerVOList` updates each DigitDoor character's server data.",
            "- `DDPartnerVO` is only `id` and `lv` in this static schema, so richer character display data comes from local config plus this server level overlay.",
            "- A successful response raises `DigitDoorInfoUpdate`; `DigitDoorInfoPanel` listens to that event and refreshes view data.",
            "- This complements the GamePlayer settlement probe: Info is the activity/home-screen snapshot, while GamePlayer is the battle-settlement boundary.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_info_snapshot_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_rows = _digitdoor_info_snapshot_rows(root, logic_dir)
    field_rows = _digitdoor_info_snapshot_field_rows(evidence_rows)
    category_counts = Counter(str(row.get("category") or "") for row in evidence_rows)
    stats = {
        "source_file_count": len(_digitdoor_info_snapshot_files(root, logic_dir)),
        "evidence_row_count": len(evidence_rows),
        "field_row_count": len(field_rows),
        "request_schema_field_rows": category_counts.get("request_packet_schema", 0),
        "request_empty_super_only_rows": sum(
            category_counts.get(category, 0)
            for category in [
                "request_empty_init_super_only",
                "request_empty_reading_super_only",
                "request_empty_writing_super_only",
            ]
        ),
        "request_packet_id_rows": category_counts.get("request_packet_id", 0),
        "request_send_rows": category_counts.get("request_send", 0),
        "activity_trigger_rows": category_counts.get("request_trigger_activity_info", 0),
        "response_schema_rows": category_counts.get("response_packet_schema", 0),
        "response_pass_list_wire_rows": category_counts.get("response_pass_list_int_wire", 0),
        "response_partner_list_wire_rows": category_counts.get("response_partner_vo_list_wire", 0),
        "partner_vo_schema_rows": category_counts.get("partner_vo_schema", 0),
        "snapshot_store_rows": category_counts.get("info_snapshot_store_msg", 0)
        + category_counts.get("response_store_info_snapshot", 0),
        "partner_state_rows": sum(count for category, count in category_counts.items() if category.startswith("partner_state_"))
        + category_counts.get("info_partner_list_to_state", 0),
        "pass_level_state_rows": sum(
            count
            for category, count in category_counts.items()
            if category.startswith("pass_level_state_") or category == "info_pass_list_to_state"
        ),
        "info_update_event_rows": category_counts.get("response_raise_info_update", 0)
        + category_counts.get("event_constant_defined", 0),
        "panel_listener_rows": category_counts.get("panel_listen_info_update", 0),
    }
    verdict = {
        "request_has_no_payload_fields": stats["request_schema_field_rows"] == 0
        and stats["request_empty_super_only_rows"] >= 3
        and stats["request_send_rows"] > 0,
        "response_snapshot_fields_confirmed": stats["response_schema_rows"] >= 2
        and stats["response_pass_list_wire_rows"] > 0
        and stats["response_partner_list_wire_rows"] > 0,
        "partner_vo_shape_confirmed": stats["partner_vo_schema_rows"] >= 2,
        "response_updates_partner_state": stats["partner_state_rows"] >= 3,
        "response_updates_pass_level_state": stats["pass_level_state_rows"] >= 4,
        "info_update_event_raised": stats["info_update_event_rows"] > 0,
        "info_panel_listens_to_info_update": stats["panel_listener_rows"] > 0,
        "activity_state_triggers_info_request": stats["activity_trigger_rows"] > 0,
    }
    _write_tsv(
        out_dir / "info_snapshot_fields.tsv",
        field_rows,
        ["field", "directions", "sources", "categories", "note"],
    )
    _write_tsv(
        out_dir / "info_snapshot_evidence.tsv",
        evidence_rows,
        ["category", "direction", "field", "source", "file", "line", "function", "snippet"],
    )
    report_path = out_dir / "info_snapshot_report.md"
    _write_digitdoor_info_snapshot_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        field_rows=field_rows,
        evidence_rows=evidence_rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["request_has_no_payload_fields"] and verdict["response_snapshot_fields_confirmed"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "fields": str(out_dir / "info_snapshot_fields.tsv"),
            "evidence": str(out_dir / "info_snapshot_evidence.tsv"),
        },
    }


def _digitdoor_uplevel_state_files(root: Path, logic_dir: Path) -> list[Path]:
    names = [
        "DigitDoorNetLogic.lua",
        "DigitDoorMgr.lua",
        "DigitDoorData.lua",
        "DigitDoorPartnerPanel.lua",
        "DigitDoorPlayerView.lua",
        "DigitDoorCharacterVo.lua",
        "DigitDoorType.lua",
    ]
    candidates = [logic_dir / name for name in names]
    patterns = [
        "by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorUpLevel.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorUpLevel.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/DDPartnerVO.lua",
    ]
    for pattern in patterns:
        candidates.extend(root.glob(pattern))
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _digitdoor_uplevel_state_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_uplevel_state_files(root, logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            categories: list[tuple[str, str, str, str]] = []

            if path.name == "CM_DigitDoorUpLevel.lua":
                if "self.id" in line:
                    categories.append(("request_packet_schema", "request", "id", "CM_DigitDoorUpLevel"))
                if "readInt()" in line and "self.id" in line:
                    categories.append(("request_id_int_wire", "request", "id", "CM_DigitDoorUpLevel"))
                if "writeInt(self.id)" in line:
                    categories.append(("request_id_int_wire", "request", "id", "CM_DigitDoorUpLevel"))
                if "return 91637" in line:
                    categories.append(("request_packet_id", "request", "", "CM_DigitDoorUpLevel"))

            elif path.name == "SM_DigitDoorUpLevel.lua":
                if "self.partnerList" in line:
                    categories.append(("response_packet_schema", "response", "partnerList", "SM_DigitDoorUpLevel"))
                if "readMessageList2List(self.partnerList)" in line:
                    categories.append(("response_partner_list_read_helper", "response", "partnerList", "SM_DigitDoorUpLevel"))
                if "writeList(self.partnerList)" in line:
                    categories.append(("response_partner_list_wire", "response", "partnerList", "SM_DigitDoorUpLevel"))
                if "return 91638" in line:
                    categories.append(("response_packet_id", "response", "", "SM_DigitDoorUpLevel"))

            elif path.name == "DDPartnerVO.lua":
                for field in ["id", "lv"]:
                    if f"self.{field}" in line:
                        categories.append(("partner_vo_schema", "response", field, "DDPartnerVO"))
                if "writeInt(self.id)" in line or ("readInt()" in line and "self.id" in line):
                    categories.append(("partner_vo_id_int_wire", "response", "id", "DDPartnerVO"))
                if "writeInt(self.lv)" in line or ("readInt()" in line and "self.lv" in line):
                    categories.append(("partner_vo_lv_int_wire", "response", "lv", "DDPartnerVO"))

            if path.name == "DigitDoorNetLogic.lua":
                if "F_Register(_CM_DigitDoorUpLevel:getId()" in line:
                    categories.append(("request_register", "request", "", "DigitDoorNetLogic"))
                if "F_Register(_SM_DigitDoorUpLevel:getId()" in line:
                    categories.append(("response_register", "response", "", "DigitDoorNetLogic"))
                if current_function == "_M.CM_DigitDoorUpLevelFun":
                    if "GetMessageFromPools(_CM_DigitDoorUpLevel)" in line:
                        categories.append(("request_pool_message", "request", "", "DigitDoorNetLogic"))
                    if "CM_DigitDoorUpLevel.id=id" in line:
                        categories.append(("request_fill_id", "request", "id", "DigitDoorNetLogic"))
                    if "F_SendMsg(CM_DigitDoorUpLevel)" in line:
                        categories.append(("request_send", "request", "", "DigitDoorNetLogic"))
                if current_function == "_M.SM_DigitDoorUpLevelFun":
                    if "msg.code==0" in line:
                        categories.append(("response_success_guard", "response", "code", "DigitDoorNetLogic"))
                    if "UpdateDDPartnerVos(msg.partnerList)" in line:
                        categories.append(("response_partner_list_to_state", "response", "partnerList", "DigitDoorNetLogic"))
                    if "Update_Character_Info" in line and "RaiseEvent" in line:
                        field = "partnerVO" if "msg.partnerVO" in line else "Update_Character_Info"
                        categories.append(("response_raise_character_update", "response", field, "DigitDoorNetLogic"))
                    if "msg.partnerVO" in line:
                        categories.append(("response_event_partner_vo_reference", "response", "partnerVO", "DigitDoorNetLogic"))
                    if "Digit_Door_Tips_27" in line:
                        categories.append(("response_success_tip", "ui", "Digit_Door_Tips_27", "DigitDoorNetLogic"))
                    if "UpdateRedDot()" in line:
                        categories.append(("response_update_red_dot", "response", "", "DigitDoorNetLogic"))

            if path.name == "DigitDoorPartnerPanel.lua":
                if "AddEventHandler(DigitDoorType.EventType.Update_Character_Info" in line:
                    categories.append(("panel_listen_character_update", "ui", "Update_Character_Info", "DigitDoorPartnerPanel"))
                if "RemoveEventHandler(DigitDoorType.EventType.Update_Character_Info" in line:
                    categories.append(("panel_remove_character_update", "ui", "Update_Character_Info", "DigitDoorPartnerPanel"))
                if current_function == "_M.OnUpdateLevelClick":
                    if "self.isMax" in line:
                        categories.append(("panel_guard_max_level", "request", "isMax", "DigitDoorPartnerPanel"))
                    if "not self.isEnough" in line:
                        categories.append(("panel_guard_cost_enough", "request", "isEnough", "DigitDoorPartnerPanel"))
                    if "CM_DigitDoorUpLevelFun(0)" in line:
                        categories.append(("panel_request_global_id_zero", "request", "id", "DigitDoorPartnerPanel"))
                if "ConfigName.DigitDoor_CharacterLevelCost" in line:
                    categories.append(("panel_cost_config_lookup", "local", "DigitDoor_CharacterLevelCost", "DigitDoorPartnerPanel"))
                if "GetCostInfo(levelCfg.cost,true)" in line:
                    categories.append(("panel_cost_item_lookup", "local", "cost", "DigitDoorPartnerPanel"))

            if path.name == "DigitDoorPlayerView.lua":
                if current_function == "_M.OnUpdateLevelClick":
                    if "GetIsMaxLevel()" in line:
                        categories.append(("player_guard_max_level", "request", "GetIsMaxLevel", "DigitDoorPlayerView"))
                    if "GetIsActive()" in line:
                        categories.append(("player_guard_active", "request", "GetIsActive", "DigitDoorPlayerView"))
                    if "not self.isEnough" in line:
                        categories.append(("player_guard_cost_enough", "request", "isEnough", "DigitDoorPlayerView"))
                    if "CM_DigitDoorUpLevelFun(self.defenseVo.id)" in line:
                        categories.append(("player_request_selected_partner_id", "request", "id", "DigitDoorPlayerView"))

            if path.name == "DigitDoorMgr.lua":
                if "function _M.CheckCanLevelUp" in line:
                    categories.append(("mgr_check_can_level_up_entry", "local", "cost", "DigitDoorMgr"))
                if current_function == "_M.CheckCanLevelUp":
                    if "GetCurCostLevel()" in line:
                        categories.append(("mgr_current_cost_level_lookup", "local", "CurCostLevel", "DigitDoorMgr"))
                    if "ConfigName.DigitDoor_CharacterLevelCost" in line:
                        categories.append(("mgr_cost_config_lookup", "local", "DigitDoor_CharacterLevelCost", "DigitDoorMgr"))
                    if "GetCostInfo(levelCfg.cost,true)" in line:
                        categories.append(("mgr_cost_item_lookup", "local", "cost", "DigitDoorMgr"))
                    if "hadNum>=costInfo.itemNum" in line:
                        categories.append(("mgr_cost_enough_compare", "local", "isEnough", "DigitDoorMgr"))
                if current_function == "_M.UpdateRedDot" and "DigitDoor_Character_Level" in line:
                    categories.append(("mgr_character_level_red_dot", "ui", "DigitDoor_Character_Level", "DigitDoorMgr"))

            if path.name == "DigitDoorData.lua":
                if "function _M.UpdateDDPartnerVos" in line:
                    categories.append(("partner_state_update_entry", "response", "partnerList", "DigitDoorData"))
                if current_function == "_M.UpdateDDPartnerVos" and "UpdateOneDigitDoorCharacterVo(v)" in line:
                    categories.append(("partner_state_iterate_list", "response", "partnerList", "DigitDoorData"))
                if current_function == "_M.UpdateOneDigitDoorCharacterVo":
                    if "GetDigitDoorCharacterVoById(msgVo.id)" in line:
                        categories.append(("partner_state_lookup_by_id", "response", "id", "DigitDoorData"))
                    if "SetServerData(msgVo)" in line:
                        categories.append(("partner_state_store_server_vo", "response", "serverData", "DigitDoorData"))
                if "function _M.GetCurCostLevel" in line:
                    categories.append(("data_current_cost_level_entry", "local", "CurCostLevel", "DigitDoorData"))
                if current_function == "_M.GetCurCostLevel":
                    if "vo:GetIsActive()" in line:
                        categories.append(("data_current_cost_level_active_filter", "local", "CurCostLevel", "DigitDoorData"))
                    if "vo:GetCurLevel()" in line:
                        categories.append(("data_current_cost_level_from_active_vo", "local", "CurCostLevel", "DigitDoorData"))

            if path.name == "DigitDoorCharacterVo.lua":
                if "function _M.GetIsMaxLevel" in line:
                    categories.append(("character_max_level_guard_entry", "local", "maxLevel", "DigitDoorCharacterVo"))
                if current_function == "_M.GetIsMaxLevel" and "curLevel>=maxLvl" in line:
                    categories.append(("character_max_level_compare", "local", "maxLevel", "DigitDoorCharacterVo"))
                if "function _M.CheckCanLevelUp" in line:
                    categories.append(("character_check_can_level_up_entry", "local", "CheckCanLevelUp", "DigitDoorCharacterVo"))
                if current_function == "_M.CheckCanLevelUp" and "return false" in line:
                    categories.append(("character_check_can_level_up_stub_false", "local", "CheckCanLevelUp", "DigitDoorCharacterVo"))

            if path.name == "DigitDoorType.lua" and 'Update_Character_Info="Update_Character_Info"' in line:
                categories.append(("event_constant_defined", "shared", "Update_Character_Info", "DigitDoorType"))

            for category, direction, field, source in categories:
                rows.append(
                    {
                        "category": category,
                        "direction": direction,
                        "field": field,
                        "source": source,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": stripped,
                    }
                )
    return rows


def _digitdoor_uplevel_state_field_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_notes = {
        "id": "CM_DigitDoorUpLevel request target id; visible UI sends either `0` from the partner panel or `defenseVo.id` from player view.",
        "partnerList": "SM_DigitDoorUpLevel server-returned DDPartnerVO list used to refresh local partner server state.",
        "partnerVO": "Referenced in the visible event payload but absent from the visible SM_DigitDoorUpLevel schema.",
        "lv": "DDPartnerVO partner level.",
        "code": "success guard before applying partner state.",
        "serverData": "per-character server state set from returned DDPartnerVO rows.",
        "Update_Character_Info": "model event used by partner UI to refresh after unlock/upgrade.",
        "DigitDoor_CharacterLevelCost": "local config used for upgrade cost display/red-dot checks.",
        "DigitDoor_Character_Level": "red-dot id raised after successful upgrade.",
        "cost": "local cost lookup and enough-item comparison before sending upgrade request.",
        "isEnough": "UI-side item sufficiency guard.",
        "isMax": "partner panel max-level guard.",
        "GetIsMaxLevel": "selected-player max-level guard.",
        "GetIsActive": "selected-player active-state guard.",
        "CurCostLevel": "aggregate current active-character level used to pick the upgrade cost row.",
    }
    by_field: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"directions": set(), "categories": set(), "sources": set()})
    for row in rows:
        for field in [item for item in str(row.get("field") or "").split("|") if item]:
            by_field[field]["directions"].add(str(row.get("direction") or ""))
            by_field[field]["categories"].add(str(row.get("category") or ""))
            by_field[field]["sources"].add(str(row.get("source") or ""))
    return [
        {
            "field": field,
            "directions": " | ".join(sorted(item for item in values["directions"] if item)),
            "sources": " | ".join(sorted(item for item in values["sources"] if item)),
            "categories": " | ".join(sorted(values["categories"])),
            "note": field_notes.get(field, ""),
        }
        for field, values in sorted(by_field.items())
    ]


def _write_digitdoor_uplevel_state_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    field_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor UpLevel state boundary",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Field rows: {len(field_rows)}",
        f"- Evidence rows: {len(evidence_rows)}",
        "- Scope: static Lua evidence for the DigitDoor partner-level request/response boundary. It does not modify traffic, replay packets, or assert server acceptance beyond visible client code.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Field Boundary",
            "",
            "| Field | Direction | Sources | Note |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in field_rows:
        lines.append(
            "| "
            f"{_md_table_cell(row.get('field', ''))} | "
            f"{_md_table_cell(row.get('directions', ''))} | "
            f"{_md_table_cell(row.get('sources', ''))} | "
            f"{_md_table_cell(row.get('note', ''))} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `CM_DigitDoorUpLevel` sends one integer `id`; visible UI can send `0` from the partner panel or a selected `defenseVo.id` from player view.",
            "- `SM_DigitDoorUpLevel` visibly returns `partnerList`, a list of `DDPartnerVO(id,lv)`, and the handler applies it through `UpdateDDPartnerVos`.",
            "- The visible handler raises `Update_Character_Info(msg.partnerVO,true)`, but `partnerVO` is not in the visible `SM_DigitDoorUpLevel` schema. Treat that event payload as suspicious/loose UI context until runtime evidence proves otherwise.",
            "- Cost/max/active checks are UI/local guards; they are useful for understanding intended flow but not proof of server-side acceptance rules.",
            "- This boundary updates the same partner server-data layer established by the Info snapshot probe.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_uplevel_state_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_rows = _digitdoor_uplevel_state_rows(root, logic_dir)
    field_rows = _digitdoor_uplevel_state_field_rows(evidence_rows)
    category_counts = Counter(str(row.get("category") or "") for row in evidence_rows)
    sm_schema_fields = {
        str(row.get("field") or "")
        for row in evidence_rows
        if row.get("source") == "SM_DigitDoorUpLevel" and row.get("category") == "response_packet_schema"
    }
    stats = {
        "source_file_count": len(_digitdoor_uplevel_state_files(root, logic_dir)),
        "evidence_row_count": len(evidence_rows),
        "field_row_count": len(field_rows),
        "request_schema_rows": category_counts.get("request_packet_schema", 0),
        "request_send_rows": category_counts.get("request_send", 0),
        "ui_global_zero_request_rows": category_counts.get("panel_request_global_id_zero", 0),
        "ui_selected_partner_request_rows": category_counts.get("player_request_selected_partner_id", 0),
        "cost_guard_rows": sum(count for category, count in category_counts.items() if "cost" in category or category.endswith("_enough")),
        "max_or_active_guard_rows": sum(
            count
            for category, count in category_counts.items()
            if "max_level" in category or category in {"panel_guard_max_level", "player_guard_active"}
        ),
        "response_schema_rows": category_counts.get("response_packet_schema", 0),
        "response_partner_list_wire_rows": category_counts.get("response_partner_list_wire", 0),
        "partner_vo_schema_rows": category_counts.get("partner_vo_schema", 0),
        "partner_state_rows": sum(count for category, count in category_counts.items() if category.startswith("partner_state_"))
        + category_counts.get("response_partner_list_to_state", 0),
        "character_update_event_rows": category_counts.get("response_raise_character_update", 0)
        + category_counts.get("event_constant_defined", 0),
        "panel_listener_rows": category_counts.get("panel_listen_character_update", 0),
        "event_partner_vo_reference_rows": category_counts.get("response_event_partner_vo_reference", 0),
        "sm_schema_partner_vo_fields": 1 if "partnerVO" in sm_schema_fields else 0,
    }
    verdict = {
        "request_id_schema_confirmed": stats["request_schema_rows"] > 0 and stats["request_send_rows"] > 0,
        "ui_sends_zero_or_selected_partner_id": stats["ui_global_zero_request_rows"] > 0
        and stats["ui_selected_partner_request_rows"] > 0,
        "ui_cost_and_max_guards_visible": stats["cost_guard_rows"] > 0 and stats["max_or_active_guard_rows"] > 0,
        "response_partner_list_confirmed": stats["response_schema_rows"] > 0
        and stats["response_partner_list_wire_rows"] > 0,
        "partner_vo_shape_confirmed": stats["partner_vo_schema_rows"] >= 2,
        "response_updates_partner_state": stats["partner_state_rows"] >= 3,
        "character_update_event_raised_and_listened": stats["character_update_event_rows"] > 0
        and stats["panel_listener_rows"] > 0,
        "event_partner_vo_reference_not_in_sm_schema": stats["event_partner_vo_reference_rows"] > 0
        and stats["sm_schema_partner_vo_fields"] == 0,
    }
    _write_tsv(
        out_dir / "uplevel_state_fields.tsv",
        field_rows,
        ["field", "directions", "sources", "categories", "note"],
    )
    _write_tsv(
        out_dir / "uplevel_state_evidence.tsv",
        evidence_rows,
        ["category", "direction", "field", "source", "file", "line", "function", "snippet"],
    )
    report_path = out_dir / "uplevel_state_report.md"
    _write_digitdoor_uplevel_state_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        field_rows=field_rows,
        evidence_rows=evidence_rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["request_id_schema_confirmed"] and verdict["response_partner_list_confirmed"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "fields": str(out_dir / "uplevel_state_fields.tsv"),
            "evidence": str(out_dir / "uplevel_state_evidence.tsv"),
        },
    }


def _digitdoor_unlock_state_files(root: Path, logic_dir: Path) -> list[Path]:
    names = [
        "DigitDoorNetLogic.lua",
        "DigitDoorMgr.lua",
        "DigitDoorData.lua",
        "DigitDoorPartnerPanel.lua",
        "DigitDoorType.lua",
    ]
    candidates = [logic_dir / name for name in names]
    patterns = [
        "by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorUnlock.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorUnlock.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/DDPartnerVO.lua",
    ]
    for pattern in patterns:
        candidates.extend(root.glob(pattern))
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _digitdoor_unlock_state_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_unlock_state_files(root, logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            categories: list[tuple[str, str, str, str]] = []

            if path.name == "CM_DigitDoorUnlock.lua":
                if "self.Id" in line:
                    categories.append(("request_packet_schema", "request", "Id", "CM_DigitDoorUnlock"))
                if "readInt()" in line and "self.Id" in line:
                    categories.append(("request_id_int_wire", "request", "Id", "CM_DigitDoorUnlock"))
                if "writeInt(self.Id)" in line:
                    categories.append(("request_id_int_wire", "request", "Id", "CM_DigitDoorUnlock"))
                if "return 91630" in line:
                    categories.append(("request_packet_id", "request", "", "CM_DigitDoorUnlock"))

            elif path.name == "SM_DigitDoorUnlock.lua":
                if "self.list" in line:
                    categories.append(("response_packet_schema", "response", "list", "SM_DigitDoorUnlock"))
                if "readMessageList2List(self.list)" in line:
                    categories.append(("response_list_read_helper", "response", "list", "SM_DigitDoorUnlock"))
                if "writeList(self.list)" in line:
                    categories.append(("response_list_wire", "response", "list", "SM_DigitDoorUnlock"))
                if "return 91631" in line:
                    categories.append(("response_packet_id", "response", "", "SM_DigitDoorUnlock"))

            elif path.name == "DDPartnerVO.lua":
                for field in ["id", "lv"]:
                    if f"self.{field}" in line:
                        categories.append(("partner_vo_schema", "response", field, "DDPartnerVO"))
                if "writeInt(self.id)" in line or ("readInt()" in line and "self.id" in line):
                    categories.append(("partner_vo_id_int_wire", "response", "id", "DDPartnerVO"))
                if "writeInt(self.lv)" in line or ("readInt()" in line and "self.lv" in line):
                    categories.append(("partner_vo_lv_int_wire", "response", "lv", "DDPartnerVO"))

            if path.name == "DigitDoorNetLogic.lua":
                if "F_Register(_CM_DigitDoorUnlock:getId()" in line:
                    categories.append(("request_register", "request", "", "DigitDoorNetLogic"))
                if "F_Register(_SM_DigitDoorUnlock:getId()" in line:
                    categories.append(("response_register", "response", "", "DigitDoorNetLogic"))
                if "function _M.CM_DigitDoorUnlockFun" in line:
                    categories.append(("request_handler_entry", "request", "", "DigitDoorNetLogic"))
                elif "CM_DigitDoorUnlockFun(" in line:
                    categories.append(("request_visible_callsite", "request", "", "DigitDoorNetLogic"))
                if current_function == "_M.CM_DigitDoorUnlockFun":
                    if "GetMessageFromPools(_CM_DigitDoorUnlock)" in line:
                        categories.append(("request_pool_message", "request", "", "DigitDoorNetLogic"))
                    if "CM_DigitDoorUnlock.Id" in line:
                        categories.append(("request_visible_id_assignment", "request", "Id", "DigitDoorNetLogic"))
                    if "F_SendMsg(CM_DigitDoorUnlock)" in line:
                        categories.append(("request_send", "request", "", "DigitDoorNetLogic"))
                if current_function == "_M.SM_DigitDoorUnlockFun":
                    if "msg.code==0" in line:
                        categories.append(("response_success_guard", "response", "code", "DigitDoorNetLogic"))
                    if "UpdateDDPartnerVos(msg.list)" in line:
                        categories.append(("response_list_to_partner_state", "response", "list", "DigitDoorNetLogic"))
                    if "OpenDigitDoorNewCharacterView(msg.list,true)" in line:
                        categories.append(("response_open_new_character_view", "ui", "list", "DigitDoorNetLogic"))
                    if "Update_Character_Info" in line and "RaiseEvent" in line:
                        categories.append(("response_raise_character_update", "response", "Update_Character_Info", "DigitDoorNetLogic"))

            if path.name == "DigitDoorMgr.lua":
                if "function _M.OpenDigitDoorNewCharacterView" in line:
                    categories.append(("new_character_view_entry", "ui", "recordOpenCharacterIdList", "DigitDoorMgr"))
                if current_function == "_M.OpenDigitDoorNewCharacterView":
                    if "recordOpenCharacterIdList=CList.new()" in line:
                        categories.append(("new_character_record_list_init", "ui", "recordOpenCharacterIdList", "DigitDoorMgr"))
                    if "for _,v in Cipairs(list)" in line:
                        categories.append(("new_character_iterate_msg_list", "ui", "list", "DigitDoorMgr"))
                    if "recordOpenCharacterIdList:Add(v.id)" in line:
                        categories.append(("new_character_record_id", "ui", "id", "DigitDoorMgr"))
                    if "IsInDigitDoorPveScene()" in line or "GetIsSkipLevel()" in line:
                        categories.append(("new_character_scene_or_skip_guard", "ui", "recordOpenCharacterIdList", "DigitDoorMgr"))
                    if "F_ShowWin(Window.DigitDoorNewCharacterView" in line:
                        categories.append(("new_character_popup_show", "ui", "Window.DigitDoorNewCharacterView", "DigitDoorMgr"))
                    if "view:UpdateView(characterInfoVo)" in line:
                        categories.append(("new_character_popup_update_view", "ui", "characterInfoVo", "DigitDoorMgr"))

            if path.name == "DigitDoorData.lua":
                if "function _M.UpdateDDPartnerVos" in line:
                    categories.append(("partner_state_update_entry", "response", "list", "DigitDoorData"))
                if current_function == "_M.UpdateDDPartnerVos" and "UpdateOneDigitDoorCharacterVo(v)" in line:
                    categories.append(("partner_state_iterate_list", "response", "list", "DigitDoorData"))
                if current_function == "_M.UpdateOneDigitDoorCharacterVo":
                    if "GetDigitDoorCharacterVoById(msgVo.id)" in line:
                        categories.append(("partner_state_lookup_by_id", "response", "id", "DigitDoorData"))
                    if "SetServerData(msgVo)" in line:
                        categories.append(("partner_state_store_server_vo", "response", "serverData", "DigitDoorData"))

            if path.name == "DigitDoorPartnerPanel.lua":
                if "AddEventHandler(DigitDoorType.EventType.Update_Character_Info" in line:
                    categories.append(("panel_listen_character_update", "ui", "Update_Character_Info", "DigitDoorPartnerPanel"))
                if "RemoveEventHandler(DigitDoorType.EventType.Update_Character_Info" in line:
                    categories.append(("panel_remove_character_update", "ui", "Update_Character_Info", "DigitDoorPartnerPanel"))

            if path.name == "DigitDoorType.lua" and 'Update_Character_Info="Update_Character_Info"' in line:
                categories.append(("event_constant_defined", "shared", "Update_Character_Info", "DigitDoorType"))

            for category, direction, field, source in categories:
                rows.append(
                    {
                        "category": category,
                        "direction": direction,
                        "field": field,
                        "source": source,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": stripped,
                    }
                )
    return rows


def _digitdoor_unlock_state_field_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_notes = {
        "Id": "CM_DigitDoorUnlock request field. Visible NetLogic sends the packet without assigning it.",
        "list": "SM_DigitDoorUnlock returned DDPartnerVO list; used both for partner state and new-character popup recording.",
        "id": "DDPartnerVO partner id; used to locate character state and record newly opened characters.",
        "lv": "DDPartnerVO partner level.",
        "code": "success guard before applying unlock state.",
        "serverData": "per-character server state set from returned DDPartnerVO rows.",
        "Update_Character_Info": "model event listened to by partner UI after unlock/upgrade.",
        "recordOpenCharacterIdList": "manager-side list of newly opened character ids used for delayed popup display.",
        "Window.DigitDoorNewCharacterView": "popup opened after unlocked character ids are recorded outside battle/skip flow.",
        "characterInfoVo": "local character config/state passed into the new-character view.",
    }
    by_field: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"directions": set(), "categories": set(), "sources": set()})
    for row in rows:
        for field in [item for item in str(row.get("field") or "").split("|") if item]:
            by_field[field]["directions"].add(str(row.get("direction") or ""))
            by_field[field]["categories"].add(str(row.get("category") or ""))
            by_field[field]["sources"].add(str(row.get("source") or ""))
    return [
        {
            "field": field,
            "directions": " | ".join(sorted(item for item in values["directions"] if item)),
            "sources": " | ".join(sorted(item for item in values["sources"] if item)),
            "categories": " | ".join(sorted(values["categories"])),
            "note": field_notes.get(field, ""),
        }
        for field, values in sorted(by_field.items())
    ]


def _write_digitdoor_unlock_state_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    field_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor Unlock state boundary",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Field rows: {len(field_rows)}",
        f"- Evidence rows: {len(evidence_rows)}",
        "- Scope: static Lua evidence for the DigitDoor unlock request/response boundary. It does not modify traffic, replay packets, or assert server acceptance beyond visible client code.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Field Boundary",
            "",
            "| Field | Direction | Sources | Note |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in field_rows:
        lines.append(
            "| "
            f"{_md_table_cell(row.get('field', ''))} | "
            f"{_md_table_cell(row.get('directions', ''))} | "
            f"{_md_table_cell(row.get('sources', ''))} | "
            f"{_md_table_cell(row.get('note', ''))} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `CM_DigitDoorUnlock` declares an integer `Id`, but visible `DigitDoorNetLogic.CM_DigitDoorUnlockFun` does not assign it before sending.",
            "- No visible business Lua callsite for `CM_DigitDoorUnlockFun` is present in the selected DigitDoor logic surface; the caller may be native, unexported, unused, or left to defaults.",
            "- `SM_DigitDoorUnlock` returns `list`, a `DDPartnerVO(id,lv)` list. The handler applies it to partner server state, opens/records newly unlocked characters, and raises `Update_Character_Info`.",
            "- `OpenDigitDoorNewCharacterView` records ids from `msg.list`, skips popup while in PVE/skip flow, and eventually opens `Window.DigitDoorNewCharacterView` with local character info.",
            "- This complements Info and UpLevel: all three update the same partner server-data layer, but Unlock has a visible request-field/callsite gap.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_unlock_state_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_rows = _digitdoor_unlock_state_rows(root, logic_dir)
    field_rows = _digitdoor_unlock_state_field_rows(evidence_rows)
    category_counts = Counter(str(row.get("category") or "") for row in evidence_rows)
    stats = {
        "source_file_count": len(_digitdoor_unlock_state_files(root, logic_dir)),
        "evidence_row_count": len(evidence_rows),
        "field_row_count": len(field_rows),
        "request_schema_rows": category_counts.get("request_packet_schema", 0),
        "request_send_rows": category_counts.get("request_send", 0),
        "request_visible_id_assignment_rows": category_counts.get("request_visible_id_assignment", 0),
        "request_visible_callsite_rows": category_counts.get("request_visible_callsite", 0),
        "response_schema_rows": category_counts.get("response_packet_schema", 0),
        "response_list_wire_rows": category_counts.get("response_list_wire", 0),
        "partner_vo_schema_rows": category_counts.get("partner_vo_schema", 0),
        "partner_state_rows": sum(count for category, count in category_counts.items() if category.startswith("partner_state_"))
        + category_counts.get("response_list_to_partner_state", 0),
        "new_character_popup_rows": sum(count for category, count in category_counts.items() if category.startswith("new_character_"))
        + category_counts.get("response_open_new_character_view", 0),
        "character_update_event_rows": category_counts.get("response_raise_character_update", 0)
        + category_counts.get("event_constant_defined", 0),
        "panel_listener_rows": category_counts.get("panel_listen_character_update", 0),
    }
    verdict = {
        "request_id_schema_confirmed": stats["request_schema_rows"] > 0 and stats["request_send_rows"] > 0,
        "visible_request_id_assignment_missing": stats["request_schema_rows"] > 0
        and stats["request_visible_id_assignment_rows"] == 0,
        "visible_unlock_callsite_missing": stats["request_visible_callsite_rows"] == 0,
        "response_list_confirmed": stats["response_schema_rows"] > 0 and stats["response_list_wire_rows"] > 0,
        "partner_vo_shape_confirmed": stats["partner_vo_schema_rows"] >= 2,
        "response_updates_partner_state": stats["partner_state_rows"] >= 3,
        "new_character_popup_path_visible": stats["new_character_popup_rows"] >= 4,
        "character_update_event_raised_and_listened": stats["character_update_event_rows"] > 0
        and stats["panel_listener_rows"] > 0,
    }
    _write_tsv(
        out_dir / "unlock_state_fields.tsv",
        field_rows,
        ["field", "directions", "sources", "categories", "note"],
    )
    _write_tsv(
        out_dir / "unlock_state_evidence.tsv",
        evidence_rows,
        ["category", "direction", "field", "source", "file", "line", "function", "snippet"],
    )
    report_path = out_dir / "unlock_state_report.md"
    _write_digitdoor_unlock_state_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        field_rows=field_rows,
        evidence_rows=evidence_rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["request_id_schema_confirmed"] and verdict["response_list_confirmed"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "fields": str(out_dir / "unlock_state_fields.tsv"),
            "evidence": str(out_dir / "unlock_state_evidence.tsv"),
        },
    }


def _digitdoor_skip_level_files(root: Path, logic_dir: Path) -> list[Path]:
    names = [
        "DigitDoorNetLogic.lua",
        "DigitDoorInfoPanel.lua",
        "DigitDoorMgr.lua",
        "DigitDoorData.lua",
        "DigitDoorResultInfoView.lua",
    ]
    candidates = [logic_dir / name for name in names]
    patterns = [
        "by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorSkipLevel.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorSkipLevel.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorGamePlayer.lua",
    ]
    for pattern in patterns:
        candidates.extend(root.glob(pattern))
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _digitdoor_skip_level_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_skip_level_files(root, logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            categories: list[tuple[str, str, str, str]] = []

            if path.name == "CM_DigitDoorSkipLevel.lua":
                if "return 91624" in line:
                    categories.append(("request_packet_id", "request", "", "CM_DigitDoorSkipLevel"))
                if current_function == "_M._init_" and "_super_._init_" in line:
                    categories.append(("request_empty_init_super_only", "request", "", "CM_DigitDoorSkipLevel"))
                if current_function == "_M.reading" and "_super_.reading" in line:
                    categories.append(("request_empty_reading_super_only", "request", "", "CM_DigitDoorSkipLevel"))
                if current_function == "_M.writing" and "_super_.writing" in line:
                    categories.append(("request_empty_writing_super_only", "request", "", "CM_DigitDoorSkipLevel"))
                if "self." in line:
                    categories.append(("request_packet_schema", "request", "unknown", "CM_DigitDoorSkipLevel"))

            elif path.name == "SM_DigitDoorSkipLevel.lua":
                categories.append(("dedicated_response_packet_visible", "response", "", "SM_DigitDoorSkipLevel"))

            elif path.name == "SM_DigitDoorGamePlayer.lua":
                if "self.isSkipLevel" in line:
                    categories.append(("gameplayer_response_schema", "response", "isSkipLevel", "SM_DigitDoorGamePlayer"))
                if "readBool()" in line and "self.isSkipLevel" in line:
                    categories.append(("gameplayer_skip_bool_wire", "response", "isSkipLevel", "SM_DigitDoorGamePlayer"))
                if "writeBool(self.isSkipLevel)" in line:
                    categories.append(("gameplayer_skip_bool_wire", "response", "isSkipLevel", "SM_DigitDoorGamePlayer"))

            if path.name == "DigitDoorInfoPanel.lua":
                if "minimumLevel" in line and "totalLevel" in line:
                    categories.append(("ui_minimum_level_guard", "ui", "minimumLevel|totalLevel", "DigitDoorInfoPanel"))
                if "allowSkipLevel" in line and "totalLevel" in line:
                    categories.append(("ui_allow_skip_level_guard", "ui", "allowSkipLevel|totalLevel", "DigitDoorInfoPanel"))
                if "ShowCommonAlertTip(true" in line:
                    categories.append(("ui_skip_confirm_dialog", "ui", "Digit_Door_Tips_7", "DigitDoorInfoPanel"))
                if "CM_DigitDoorSkipLevelFun()" in line:
                    categories.append(("ui_confirm_sends_skip_request", "request", "", "DigitDoorInfoPanel"))
                if "ReqChangeMap(self.showLevelCfg.sceneId)" in line:
                    categories.append(("ui_cancel_or_normal_enters_scene", "ui", "sceneId", "DigitDoorInfoPanel"))
                if "IsFinishLevel" in line:
                    categories.append(("ui_already_finished_guard", "ui", "IsFinishLevel", "DigitDoorInfoPanel"))

            if path.name == "DigitDoorNetLogic.lua":
                if "F_Register(_CM_DigitDoorSkipLevel:getId()" in line:
                    categories.append(("request_register", "request", "", "DigitDoorNetLogic"))
                if "function _M.CM_DigitDoorSkipLevelFun" in line:
                    categories.append(("request_handler_entry", "request", "", "DigitDoorNetLogic"))
                if current_function == "_M.CM_DigitDoorSkipLevelFun":
                    if "GetMessageFromPools(_CM_DigitDoorSkipLevel)" in line:
                        categories.append(("request_pool_message", "request", "", "DigitDoorNetLogic"))
                    if "F_SendMsg(CM_DigitDoorSkipLevel)" in line:
                        categories.append(("request_send", "request", "", "DigitDoorNetLogic"))
                if "function _M.SM_DigitDoorActivityEndFun" in line:
                    categories.append(("activity_end_handler_entry", "response", "", "DigitDoorNetLogic"))
                if current_function == "_M.SM_DigitDoorActivityEndFun" and "ReqFinishGame()" in line:
                    categories.append(("activity_end_triggers_finish_request", "response", "ReqFinishGame", "DigitDoorNetLogic"))
                if current_function == "_M.SM_DigitDoorGamePlayerFun":
                    if "DigitDoorExitGame(msg)" in line:
                        categories.append(("gameplayer_response_exit_game", "response", "isSkipLevel", "DigitDoorNetLogic"))
                    if "msg.isSkipLevel==false" in line:
                        categories.append(("gameplayer_normal_result_branch", "response", "isSkipLevel", "DigitDoorNetLogic"))
                    if "msg.rewardResults" in line:
                        categories.append(("gameplayer_skip_reward_branch", "response", "rewardResults", "DigitDoorNetLogic"))
                    if "SetIsSkipLevel(false)" in line:
                        categories.append(("gameplayer_skip_reset_after_rewards", "response", "isSkipLevel", "DigitDoorNetLogic"))
                    if "OpenDigitDoorNewCharacterView(recordIdList)" in line:
                        categories.append(("gameplayer_skip_new_character_popup", "ui", "recordIdList", "DigitDoorNetLogic"))
                    if "DigitDoorInfoUpdate" in line and "RaiseEvent" in line:
                        categories.append(("gameplayer_raise_info_update", "response", "DigitDoorInfoUpdate", "DigitDoorNetLogic"))

            if path.name == "DigitDoorMgr.lua":
                if "function _M.DigitDoorExitGame" in line:
                    categories.append(("exit_game_entry", "response", "isSkipLevel", "DigitDoorMgr"))
                if current_function == "_M.DigitDoorExitGame":
                    if "SetIsSkipLevel(msg.isSkipLevel)" in line:
                        categories.append(("exit_game_store_skip_state", "response", "isSkipLevel", "DigitDoorMgr"))
                    if "if not msg.isSkipLevel" in line:
                        categories.append(("exit_game_normal_sets_finish_flag", "response", "isSkipLevel", "DigitDoorMgr"))
                    if "SetFinishLevelInfo(msg)" in line:
                        categories.append(("exit_game_store_finish_level_info", "response", "passLevelVOS", "DigitDoorMgr"))
                if "function _M.SetIsSkipLevel" in line:
                    categories.append(("skip_state_setter", "response", "isSkipLevel", "DigitDoorMgr"))
                if "function _M.GetIsSkipLevel" in line:
                    categories.append(("skip_state_getter", "response", "isSkipLevel", "DigitDoorMgr"))
                if current_function == "_M.ReqFinishGame" and "CM_DigitDoorGamePlayerFun(wave,wavePercent)" in line:
                    categories.append(("finish_request_sends_gameplayer", "request", "CM_DigitDoorGamePlayer", "DigitDoorMgr"))

            if path.name == "DigitDoorData.lua":
                if current_function == "_M.SetFinishLevelInfo" and "InitNewLevelDic(msg.passLevelVOS)" in line:
                    categories.append(("finish_level_info_from_gameplayer", "response", "passLevelVOS", "DigitDoorData"))

            if path.name == "DigitDoorResultInfoView.lua":
                if "msg.isSkipLevel" in line:
                    categories.append(("result_view_reads_skip_flag", "response", "isSkipLevel", "DigitDoorResultInfoView"))
                if "msg.rewardResults" in line:
                    categories.append(("result_view_reward_results", "response", "rewardResults", "DigitDoorResultInfoView"))

            for category, direction, field, source in categories:
                rows.append(
                    {
                        "category": category,
                        "direction": direction,
                        "field": field,
                        "source": source,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": stripped,
                    }
                )
    return rows


def _digitdoor_skip_level_field_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_notes = {
        "allowSkipLevel": "DigitDoor level config threshold; skip prompt is enabled when not 999 and total level reaches it.",
        "minimumLevel": "normal challenge level threshold checked before skip logic.",
        "totalLevel": "local aggregate level used by challenge/skip guards.",
        "sceneId": "normal/cancel path enters the configured level scene instead of sending skip intent.",
        "isSkipLevel": "SM_DigitDoorGamePlayer result flag that carries the skip outcome; no visible dedicated SM_DigitDoorSkipLevel exists.",
        "rewardResults": "server-returned reward list shown directly on skip branch.",
        "recordIdList": "new-character ids shown after skip rewards are acknowledged.",
        "DigitDoorInfoUpdate": "model event raised after GamePlayer settlement, including skip branch.",
        "ReqFinishGame": "activity-end handler requests GamePlayer settlement.",
        "CM_DigitDoorGamePlayer": "settlement request used after activity end / finish flow.",
        "passLevelVOS": "GamePlayer response pass-level list stored by SetFinishLevelInfo.",
    }
    by_field: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"directions": set(), "categories": set(), "sources": set()})
    for row in rows:
        for field in [item for item in str(row.get("field") or "").split("|") if item]:
            by_field[field]["directions"].add(str(row.get("direction") or ""))
            by_field[field]["categories"].add(str(row.get("category") or ""))
            by_field[field]["sources"].add(str(row.get("source") or ""))
    return [
        {
            "field": field,
            "directions": " | ".join(sorted(item for item in values["directions"] if item)),
            "sources": " | ".join(sorted(item for item in values["sources"] if item)),
            "categories": " | ".join(sorted(values["categories"])),
            "note": field_notes.get(field, ""),
        }
        for field, values in sorted(by_field.items())
    ]


def _write_digitdoor_skip_level_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    field_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor SkipLevel boundary",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Field rows: {len(field_rows)}",
        f"- Evidence rows: {len(evidence_rows)}",
        "- Scope: static Lua evidence for the DigitDoor skip-level request/result boundary. It does not modify traffic, replay packets, or assert server acceptance beyond visible client code.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Field Boundary",
            "",
            "| Field | Direction | Sources | Note |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in field_rows:
        lines.append(
            "| "
            f"{_md_table_cell(row.get('field', ''))} | "
            f"{_md_table_cell(row.get('directions', ''))} | "
            f"{_md_table_cell(row.get('sources', ''))} | "
            f"{_md_table_cell(row.get('note', ''))} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `CM_DigitDoorSkipLevel` is a no-payload skip intent request.",
            "- Visible UI sends it only after the challenge is not already finished, activity is open, `minimumLevel` is satisfied, and `allowSkipLevel != 999` with total level high enough.",
            "- The cancel/normal path uses `ReqChangeMap(sceneId)` rather than sending skip.",
            "- There is no visible `SM_DigitDoorSkipLevel`; skip outcome is folded into `SM_DigitDoorGamePlayer.isSkipLevel` and the GamePlayer settlement flow.",
            "- On the skip branch, rewards can be shown directly, skip state is reset after the reward callback, and newly opened characters are shown from the recorded id list.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_skip_level_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_rows = _digitdoor_skip_level_rows(root, logic_dir)
    field_rows = _digitdoor_skip_level_field_rows(evidence_rows)
    category_counts = Counter(str(row.get("category") or "") for row in evidence_rows)
    visible_sm_skip_files = [path for path in _digitdoor_skip_level_files(root, logic_dir) if path.name == "SM_DigitDoorSkipLevel.lua"]
    stats = {
        "source_file_count": len(_digitdoor_skip_level_files(root, logic_dir)),
        "evidence_row_count": len(evidence_rows),
        "field_row_count": len(field_rows),
        "request_schema_rows": category_counts.get("request_packet_schema", 0),
        "request_empty_super_only_rows": sum(
            category_counts.get(category, 0)
            for category in [
                "request_empty_init_super_only",
                "request_empty_reading_super_only",
                "request_empty_writing_super_only",
            ]
        ),
        "request_send_rows": category_counts.get("request_send", 0),
        "visible_sm_skip_file_count": len(visible_sm_skip_files),
        "ui_guard_rows": category_counts.get("ui_already_finished_guard", 0)
        + category_counts.get("ui_minimum_level_guard", 0)
        + category_counts.get("ui_allow_skip_level_guard", 0),
        "ui_confirm_send_rows": category_counts.get("ui_confirm_sends_skip_request", 0),
        "ui_cancel_or_normal_scene_rows": category_counts.get("ui_cancel_or_normal_enters_scene", 0),
        "gameplayer_skip_schema_rows": category_counts.get("gameplayer_response_schema", 0),
        "gameplayer_skip_branch_rows": category_counts.get("gameplayer_normal_result_branch", 0)
        + category_counts.get("gameplayer_skip_reward_branch", 0),
        "exit_game_skip_state_rows": category_counts.get("exit_game_store_skip_state", 0)
        + category_counts.get("skip_state_setter", 0),
        "finish_request_rows": category_counts.get("activity_end_triggers_finish_request", 0)
        + category_counts.get("finish_request_sends_gameplayer", 0),
        "skip_reward_and_new_character_rows": category_counts.get("gameplayer_skip_reward_branch", 0)
        + category_counts.get("gameplayer_skip_new_character_popup", 0)
        + category_counts.get("gameplayer_skip_reset_after_rewards", 0),
    }
    verdict = {
        "request_has_no_payload_fields": stats["request_schema_rows"] == 0
        and stats["request_empty_super_only_rows"] >= 3
        and stats["request_send_rows"] > 0,
        "ui_skip_conditions_visible": stats["ui_guard_rows"] >= 3 and stats["ui_confirm_send_rows"] > 0,
        "ui_cancel_or_normal_enters_scene": stats["ui_cancel_or_normal_scene_rows"] > 0,
        "no_visible_dedicated_sm_skip_packet": stats["visible_sm_skip_file_count"] == 0,
        "skip_outcome_folded_into_gameplayer": stats["gameplayer_skip_schema_rows"] > 0
        and stats["gameplayer_skip_branch_rows"] > 0,
        "exit_game_stores_skip_state": stats["exit_game_skip_state_rows"] > 0,
        "activity_end_to_gameplayer_finish_path_visible": stats["finish_request_rows"] >= 2,
        "skip_rewards_and_new_character_popup_visible": stats["skip_reward_and_new_character_rows"] >= 3,
    }
    _write_tsv(
        out_dir / "skip_level_fields.tsv",
        field_rows,
        ["field", "directions", "sources", "categories", "note"],
    )
    _write_tsv(
        out_dir / "skip_level_evidence.tsv",
        evidence_rows,
        ["category", "direction", "field", "source", "file", "line", "function", "snippet"],
    )
    report_path = out_dir / "skip_level_report.md"
    _write_digitdoor_skip_level_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        field_rows=field_rows,
        evidence_rows=evidence_rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["request_has_no_payload_fields"] and verdict["skip_outcome_folded_into_gameplayer"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "fields": str(out_dir / "skip_level_fields.tsv"),
            "evidence": str(out_dir / "skip_level_evidence.tsv"),
        },
    }


def _digitdoor_activity_end_files(root: Path, logic_dir: Path) -> list[Path]:
    names = [
        "DigitDoorNetLogic.lua",
        "DigitDoorMgr.lua",
        "DigitDoorSceneView.lua",
        "DigitDoorEntityMgr.lua",
    ]
    candidates = [logic_dir / name for name in names]
    patterns = [
        "by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorActivityEnd*.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorActivityEnd*.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorGamePlayer.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorGamePlayer.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/VO_URL.lua",
    ]
    for pattern in patterns:
        candidates.extend(root.glob(pattern))
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _digitdoor_activity_end_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_activity_end_files(root, logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            categories: list[tuple[str, str, str, str]] = []
            name = path.name

            if name.startswith("SM_DigitDoorActivityEnd"):
                if "_M=class(ClientResult,_M)" in line:
                    categories.append(("activity_end_clientresult_super", "response", "code", "SM_DigitDoorActivityEnd"))
                if "return 91632" in line:
                    categories.append(("activity_end_packet_id", "response", "", "SM_DigitDoorActivityEnd"))
                if current_function == "_M._init_" and "_super_._init_" in line:
                    categories.append(("activity_end_no_payload_init_super_only", "response", "", "SM_DigitDoorActivityEnd"))
                if current_function == "_M.reading" and "_super_.reading" in line:
                    categories.append(("activity_end_no_payload_reading_super_only", "response", "", "SM_DigitDoorActivityEnd"))
                if current_function == "_M.writing" and "_super_.writing" in line:
                    categories.append(("activity_end_no_payload_writing_super_only", "response", "", "SM_DigitDoorActivityEnd"))
                if re.search(r"\bself\.[A-Za-z_]\w*\s*=", line):
                    categories.append(("activity_end_response_schema", "response", "unknown", "SM_DigitDoorActivityEnd"))

            elif name.startswith("CM_DigitDoorActivityEnd"):
                categories.append(("visible_cm_activity_end_packet", "request", "", "CM_DigitDoorActivityEnd"))

            elif name == "VO_URL.lua":
                if "91632" in line and "SM_DigitDoorActivityEnd" in line:
                    categories.append(("activity_end_vo_url_map", "response", "", "VO_URL"))
                if "91626" in line and "CM_DigitDoorGamePlayer" in line:
                    categories.append(("gameplayer_request_vo_url_map", "request", "", "VO_URL"))
                if "91627" in line and "SM_DigitDoorGamePlayer" in line:
                    categories.append(("gameplayer_response_vo_url_map", "response", "", "VO_URL"))

            elif name == "CM_DigitDoorGamePlayer.lua":
                for field in ["currWave", "wavePercent", "killNum", "bossVoList"]:
                    if f"self.{field}" in line:
                        categories.append(("gameplayer_request_schema", "request", field, "CM_DigitDoorGamePlayer"))
                if "readInt()" in line:
                    for field in ["currWave", "wavePercent", "killNum"]:
                        if f"self.{field}" in line:
                            categories.append(("gameplayer_request_int_wire", "request", field, "CM_DigitDoorGamePlayer"))
                if "writeInt(self." in line:
                    for field in ["currWave", "wavePercent", "killNum"]:
                        if f"writeInt(self.{field})" in line:
                            categories.append(("gameplayer_request_int_wire", "request", field, "CM_DigitDoorGamePlayer"))
                if "readMessageList2List(self.bossVoList)" in line or "writeList(self.bossVoList)" in line:
                    categories.append(("gameplayer_request_boss_list_wire", "request", "bossVoList", "CM_DigitDoorGamePlayer"))
                if "return 91626" in line:
                    categories.append(("gameplayer_request_packet_id", "request", "", "CM_DigitDoorGamePlayer"))

            elif name == "SM_DigitDoorGamePlayer.lua":
                for field in ["finishWave", "rewardResults", "passLevelVOS", "levelId", "gameType", "isSkipLevel"]:
                    if f"self.{field}" in line:
                        categories.append(("gameplayer_response_schema", "response", field, "SM_DigitDoorGamePlayer"))
                if "readInt()" in line or "writeInt(self." in line:
                    for field in ["finishWave", "levelId", "gameType"]:
                        if f"self.{field}" in line:
                            categories.append(("gameplayer_response_int_wire", "response", field, "SM_DigitDoorGamePlayer"))
                if "readBool()" in line and "self.isSkipLevel" in line:
                    categories.append(("gameplayer_response_bool_wire", "response", "isSkipLevel", "SM_DigitDoorGamePlayer"))
                if "writeBool(self.isSkipLevel)" in line:
                    categories.append(("gameplayer_response_bool_wire", "response", "isSkipLevel", "SM_DigitDoorGamePlayer"))
                if "readMessageList2List(self.rewardResults)" in line or "writeList(self.rewardResults)" in line:
                    categories.append(("gameplayer_response_reward_list_wire", "response", "rewardResults", "SM_DigitDoorGamePlayer"))
                if "readMessageList2List(self.passLevelVOS)" in line or "writeIntList(self.passLevelVOS)" in line:
                    categories.append(("gameplayer_response_pass_level_list_wire", "response", "passLevelVOS", "SM_DigitDoorGamePlayer"))
                if "return 91627" in line:
                    categories.append(("gameplayer_response_packet_id", "response", "", "SM_DigitDoorGamePlayer"))

            if name == "DigitDoorNetLogic.lua":
                if "_SM_DigitDoorActivityEnd" in line and "require" in line:
                    categories.append(("activity_end_import", "response", "", "DigitDoorNetLogic"))
                if "F_Register(_SM_DigitDoorActivityEnd:getId()" in line:
                    categories.append(("activity_end_register", "response", "", "DigitDoorNetLogic"))
                if "F_Unregister(_SM_DigitDoorActivityEnd:getId()" in line:
                    categories.append(("activity_end_unregister", "response", "", "DigitDoorNetLogic"))
                if "function _M.SM_DigitDoorActivityEndFun" in line:
                    categories.append(("activity_end_handler_entry", "response", "", "DigitDoorNetLogic"))
                if current_function == "_M.SM_DigitDoorActivityEndFun":
                    if "CheckCodeMessage(msg,3,true)" in line:
                        categories.append(("activity_end_success_guard", "response", "code", "DigitDoorNetLogic"))
                    if "ReqFinishGame()" in line:
                        categories.append(("activity_end_triggers_req_finish_game", "response", "ReqFinishGame", "DigitDoorNetLogic"))
                if current_function == "_M.CM_DigitDoorGamePlayerFun":
                    if "GetMessageFromPools(_CM_DigitDoorGamePlayer)" in line:
                        categories.append(("gameplayer_request_pool_message", "request", "", "DigitDoorNetLogic"))
                    for field in ["currWave", "wavePercent"]:
                        if f"CM_DigitDoorGamePlayer.{field}" in line:
                            categories.append(("gameplayer_request_fill_arg", "request", field, "DigitDoorNetLogic"))
                    if "GetTotalKillSmallMonsterNum()" in line:
                        categories.append(("gameplayer_request_fill_runtime_summary", "request", "killNum", "DigitDoorNetLogic"))
                    if "GetTotalBossDamageList()" in line:
                        categories.append(("gameplayer_request_fill_runtime_summary", "request", "bossVoList", "DigitDoorNetLogic"))
                    if "F_SendMsg(CM_DigitDoorGamePlayer)" in line:
                        categories.append(("gameplayer_request_send", "request", "", "DigitDoorNetLogic"))
                if current_function == "_M.SM_DigitDoorGamePlayerFun":
                    if "CheckCodeMessage(msg,3,true)" in line:
                        categories.append(("gameplayer_response_success_guard", "response", "code", "DigitDoorNetLogic"))
                    if "DigitDoorExitGame(msg)" in line:
                        categories.append(("gameplayer_response_exit_game", "response", "SM_DigitDoorGamePlayer", "DigitDoorNetLogic"))
                    if "OpenDigitDoorResultInfoView(msg)" in line:
                        categories.append(("gameplayer_response_open_result_view", "response", "SM_DigitDoorGamePlayer", "DigitDoorNetLogic"))
                    if "msg.rewardResults" in line:
                        categories.append(("gameplayer_response_reward_branch", "response", "rewardResults", "DigitDoorNetLogic"))
                    if "DigitDoorInfoUpdate" in line and "RaiseEvent" in line:
                        categories.append(("gameplayer_response_raise_info_update", "response", "DigitDoorInfoUpdate", "DigitDoorNetLogic"))

            if name == "DigitDoorMgr.lua":
                if "self:ReqFinishGame(true,true)" in line:
                    categories.append(("finish_trigger_finish_level_event", "local", "FinishLevel", "DigitDoorMgr"))
                if "function _M.OpenDigitDoorResultInfoView" in line:
                    categories.append(("open_result_view_entry", "ui", "DigitDoorResultInfoView", "DigitDoorMgr"))
                if current_function == "_M.OpenDigitDoorResultInfoView" and "IsInDigitDoorPveScene()" in line:
                    categories.append(("open_result_view_scene_guard", "ui", "DigitDoorResultInfoView", "DigitDoorMgr"))
                if "function _M.DigitDoorExitGame" in line:
                    categories.append(("exit_game_entry", "response", "SM_DigitDoorGamePlayer", "DigitDoorMgr"))
                if current_function == "_M.DigitDoorExitGame":
                    if "SetIsSkipLevel(msg.isSkipLevel)" in line:
                        categories.append(("exit_game_store_skip_flag", "response", "isSkipLevel", "DigitDoorMgr"))
                    if "V_IsReqFinishGame=true" in line:
                        categories.append(("exit_game_set_finish_flag", "response", "V_IsReqFinishGame", "DigitDoorMgr"))
                    if "SetFinishLevelInfo(msg)" in line:
                        categories.append(("exit_game_store_finish_info", "response", "passLevelVOS", "DigitDoorMgr"))
                if "function _M.ReqFinishGame" in line:
                    categories.append(("req_finish_game_entry", "request", "ReqFinishGame", "DigitDoorMgr"))
                if current_function == "_M.ReqFinishGame":
                    if "IsInDigitDoorPveScene()" in line:
                        categories.append(("req_finish_game_scene_guard", "request", "ReqFinishGame", "DigitDoorMgr"))
                    if "self.V_IsReqFinishGame then" in line:
                        categories.append(("req_finish_game_duplicate_guard", "request", "V_IsReqFinishGame", "DigitDoorMgr"))
                    if "V_ReqFinishSaveTime" in line:
                        categories.append(("req_finish_game_time_throttle", "request", "V_ReqFinishSaveTime", "DigitDoorMgr"))
                    if "Model:GetWave()" in line:
                        categories.append(("req_finish_game_wave_snapshot", "request", "currWave", "DigitDoorMgr"))
                    if "OpenDigitDoorResultInfoView()" in line:
                        categories.append(("req_finish_game_open_pending_result_view", "ui", "DigitDoorResultInfoView", "DigitDoorMgr"))
                    if "CM_DigitDoorGamePlayerFun(wave,wavePercent)" in line:
                        categories.append(("req_finish_game_sends_gameplayer", "request", "CM_DigitDoorGamePlayer", "DigitDoorMgr"))
                    if "UpdateDigitDoorThinkingData(result)" in line:
                        categories.append(("req_finish_game_upload_thinking_data", "local", "activity_stage", "DigitDoorMgr"))
                if current_function == "_M.UpdateDigitDoorThinkingData":
                    if "GetCurLevelTime()" in line:
                        categories.append(("thinking_data_uses_level_time", "local", "used_time", "DigitDoorMgr"))
                    if "UploadThinkingDatas(\"activity_stage\"" in line:
                        categories.append(("thinking_data_upload_activity_stage", "local", "activity_stage", "DigitDoorMgr"))

            if name == "DigitDoorSceneView.lua":
                if "isNeedFinish" in line:
                    categories.append(("finish_trigger_scene_need_finish_flag", "local", "isNeedFinish", "DigitDoorSceneView"))
                if "IsStartGame()" in line and "V_IsReqFinishGame" in line:
                    categories.append(("finish_trigger_scene_guard", "local", "IsStartGame|V_IsReqFinishGame", "DigitDoorSceneView"))
                if "ReqFinishGame(true)" in line:
                    categories.append(("finish_trigger_scene_req_finish_game", "local", "ReqFinishGame", "DigitDoorSceneView"))

            if name == "DigitDoorEntityMgr.lua":
                if "function _M.CheckAutoLose" in line:
                    categories.append(("finish_trigger_auto_lose_entry", "local", "CheckAutoLose", "DigitDoorEntityMgr"))
                if current_function == "_M.CheckAutoLose":
                    if "GetAutoLostTimeLimit()" in line:
                        categories.append(("finish_trigger_auto_lose_time_limit", "local", "GetAutoLostTimeLimit", "DigitDoorEntityMgr"))
                    if "ReqFinishGame()" in line:
                        categories.append(("finish_trigger_auto_lose_req_finish_game", "local", "ReqFinishGame", "DigitDoorEntityMgr"))

            for category, direction, field, source in categories:
                rows.append(
                    {
                        "category": category,
                        "direction": direction,
                        "field": field,
                        "source": source,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": stripped,
                    }
                )
    return rows


def _digitdoor_activity_end_field_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_notes = {
        "code": "ClientResult success/error status checked before applying ActivityEnd or GamePlayer response.",
        "ReqFinishGame": "local finish-settlement entry reused by server ActivityEnd signal and local finish/loss triggers.",
        "CM_DigitDoorGamePlayer": "settlement request sent after finish flow; carries local progress summary.",
        "SM_DigitDoorGamePlayer": "settlement response that drives exit-game state, rewards, level progress, and result UI.",
        "V_IsReqFinishGame": "client duplicate guard / finish-in-progress flag.",
        "V_ReqFinishSaveTime": "client throttle used to avoid repeated finish requests within roughly one second.",
        "currWave": "GamePlayer request wave snapshot, filled from DigitDoor model wave.",
        "wavePercent": "GamePlayer request progress percent; visible finish path currently sends zero.",
        "killNum": "GamePlayer request runtime kill summary from DigitDoorEntityMgr.",
        "bossVoList": "GamePlayer request runtime boss-damage summary from DigitDoorEntityMgr.",
        "finishWave": "GamePlayer response finished wave count.",
        "rewardResults": "GamePlayer response reward list used by settlement/skip reward UI.",
        "passLevelVOS": "GamePlayer response pass-level list persisted into DigitDoorData finish state.",
        "levelId": "GamePlayer response level id.",
        "gameType": "GamePlayer response game type.",
        "isSkipLevel": "GamePlayer response branch flag for skip-level settlement handling.",
        "DigitDoorInfoUpdate": "model event raised after GamePlayer settlement.",
        "DigitDoorResultInfoView": "pending/final settlement UI view opened around finish/result flow.",
        "activity_stage": "SDK thinking-data analytics event uploaded after finish request.",
        "FinishLevel": "local model event that can also trigger ReqFinishGame(true,true).",
        "isNeedFinish": "scene-side local finish flag that can trigger delayed ReqFinishGame(true).",
        "CheckAutoLose": "entity-manager auto-loss timer entry.",
        "GetAutoLostTimeLimit": "auto-loss threshold used before calling ReqFinishGame.",
        "used_time": "thinking-data field filled from current level time.",
    }
    by_field: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"directions": set(), "categories": set(), "sources": set()})
    for row in rows:
        for field in [item for item in str(row.get("field") or "").split("|") if item]:
            by_field[field]["directions"].add(str(row.get("direction") or ""))
            by_field[field]["categories"].add(str(row.get("category") or ""))
            by_field[field]["sources"].add(str(row.get("source") or ""))
    return [
        {
            "field": field,
            "directions": " | ".join(sorted(item for item in values["directions"] if item)),
            "sources": " | ".join(sorted(item for item in values["sources"] if item)),
            "categories": " | ".join(sorted(values["categories"])),
            "note": field_notes.get(field, ""),
        }
        for field, values in sorted(by_field.items())
    ]


def _write_digitdoor_activity_end_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    field_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor ActivityEnd finish boundary",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Field rows: {len(field_rows)}",
        f"- Evidence rows: {len(evidence_rows)}",
        "- Scope: static Lua evidence for the DigitDoor ActivityEnd-to-finish-settlement boundary. It does not modify traffic, replay packets, or assert server behavior beyond visible client code.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Field Boundary",
            "",
            "| Field | Direction | Sources | Note |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in field_rows:
        lines.append(
            "| "
            f"{_md_table_cell(row.get('field', ''))} | "
            f"{_md_table_cell(row.get('directions', ''))} | "
            f"{_md_table_cell(row.get('sources', ''))} | "
            f"{_md_table_cell(row.get('note', ''))} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `SM_DigitDoorActivityEnd(91632)` is a server-to-client `ClientResult` signal with no visible business payload fields.",
            "- The successful handler does not settle rewards directly; it calls `DigitDoorMgr:ReqFinishGame()`.",
            "- `ReqFinishGame` is guarded by PVE-scene state, a duplicate flag, and a short time throttle before sending `CM_DigitDoorGamePlayer(91626)`.",
            "- `CM_DigitDoorGamePlayer` carries a local progress snapshot: `currWave`, `wavePercent`, `killNum`, and `bossVoList`.",
            "- The authoritative settlement surface remains `SM_DigitDoorGamePlayer(91627)`, which drives `DigitDoorExitGame`, reward/result UI, pass-level state, and `DigitDoorInfoUpdate`.",
            "- Local finish triggers (`FinishLevel`, scene finish flag, auto-loss timer) reuse the same `ReqFinishGame` path, so ActivityEnd is best understood as one trigger into the shared finish pipeline.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_activity_end_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_rows = _digitdoor_activity_end_rows(root, logic_dir)
    field_rows = _digitdoor_activity_end_field_rows(evidence_rows)
    category_counts = Counter(str(row.get("category") or "") for row in evidence_rows)
    visible_cm_activity_end_files = [
        path for path in _digitdoor_activity_end_files(root, logic_dir) if path.name.startswith("CM_DigitDoorActivityEnd")
    ]
    stats = {
        "source_file_count": len(_digitdoor_activity_end_files(root, logic_dir)),
        "evidence_row_count": len(evidence_rows),
        "field_row_count": len(field_rows),
        "activity_end_packet_id_rows": category_counts.get("activity_end_packet_id", 0)
        + category_counts.get("activity_end_vo_url_map", 0),
        "activity_end_schema_rows": category_counts.get("activity_end_response_schema", 0),
        "activity_end_no_payload_super_rows": sum(
            category_counts.get(category, 0)
            for category in [
                "activity_end_no_payload_init_super_only",
                "activity_end_no_payload_reading_super_only",
                "activity_end_no_payload_writing_super_only",
            ]
        ),
        "visible_cm_activity_end_file_count": len(visible_cm_activity_end_files),
        "activity_end_register_rows": category_counts.get("activity_end_register", 0),
        "activity_end_success_guard_rows": category_counts.get("activity_end_success_guard", 0),
        "activity_end_trigger_rows": category_counts.get("activity_end_triggers_req_finish_game", 0),
        "req_finish_guard_rows": category_counts.get("req_finish_game_scene_guard", 0)
        + category_counts.get("req_finish_game_duplicate_guard", 0)
        + category_counts.get("req_finish_game_time_throttle", 0),
        "req_finish_sends_gameplayer_rows": category_counts.get("req_finish_game_sends_gameplayer", 0),
        "gameplayer_request_schema_rows": category_counts.get("gameplayer_request_schema", 0),
        "gameplayer_request_fill_rows": category_counts.get("gameplayer_request_fill_arg", 0)
        + category_counts.get("gameplayer_request_fill_runtime_summary", 0),
        "gameplayer_request_send_rows": category_counts.get("gameplayer_request_send", 0),
        "gameplayer_response_schema_rows": category_counts.get("gameplayer_response_schema", 0),
        "gameplayer_response_settlement_rows": category_counts.get("gameplayer_response_exit_game", 0)
        + category_counts.get("exit_game_store_finish_info", 0)
        + category_counts.get("gameplayer_response_raise_info_update", 0),
        "other_req_finish_trigger_rows": category_counts.get("finish_trigger_finish_level_event", 0)
        + category_counts.get("finish_trigger_scene_req_finish_game", 0)
        + category_counts.get("finish_trigger_auto_lose_req_finish_game", 0),
        "thinking_data_rows": category_counts.get("req_finish_game_upload_thinking_data", 0)
        + category_counts.get("thinking_data_upload_activity_stage", 0),
    }
    verdict = {
        "activity_end_is_server_only_no_payload_response": stats["activity_end_packet_id_rows"] > 0
        and stats["activity_end_schema_rows"] == 0
        and stats["activity_end_no_payload_super_rows"] >= 3
        and stats["visible_cm_activity_end_file_count"] == 0,
        "activity_end_registered_and_guarded": stats["activity_end_register_rows"] > 0
        and stats["activity_end_success_guard_rows"] > 0,
        "activity_end_triggers_finish_request": stats["activity_end_trigger_rows"] > 0,
        "finish_request_guarded_by_scene_duplicate_and_time": stats["req_finish_guard_rows"] >= 3,
        "finish_request_sends_gameplayer_snapshot": stats["req_finish_sends_gameplayer_rows"] > 0
        and stats["gameplayer_request_schema_rows"] >= 4
        and stats["gameplayer_request_fill_rows"] >= 4
        and stats["gameplayer_request_send_rows"] > 0,
        "gameplayer_response_remains_settlement_authority": stats["gameplayer_response_schema_rows"] >= 6
        and stats["gameplayer_response_settlement_rows"] >= 3,
        "other_finish_triggers_share_same_req_finish": stats["other_req_finish_trigger_rows"] > 0,
        "finish_flow_uploads_thinking_data": stats["thinking_data_rows"] > 0,
    }
    _write_tsv(
        out_dir / "activity_end_fields.tsv",
        field_rows,
        ["field", "directions", "sources", "categories", "note"],
    )
    _write_tsv(
        out_dir / "activity_end_evidence.tsv",
        evidence_rows,
        ["category", "direction", "field", "source", "file", "line", "function", "snippet"],
    )
    report_path = out_dir / "activity_end_report.md"
    _write_digitdoor_activity_end_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        field_rows=field_rows,
        evidence_rows=evidence_rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["activity_end_is_server_only_no_payload_response"]
        and verdict["activity_end_triggers_finish_request"]
        and verdict["finish_request_sends_gameplayer_snapshot"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "fields": str(out_dir / "activity_end_fields.tsv"),
            "evidence": str(out_dir / "activity_end_evidence.tsv"),
        },
    }


def _digitdoor_report_gmbattle_files(root: Path, logic_dir: Path) -> list[Path]:
    names = [
        "DigitDoorNetLogic.lua",
        "DigitDoorPVPSceneView.lua",
        "DigitDoorMgr.lua",
        "DigitDoorFightComponent.lua",
    ]
    candidates = [logic_dir / name for name in names]
    patterns = [
        "by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorReport*.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorReport*.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorGMBattle*.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorGMBattle*.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/DigitDoorSimpleVO*.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/ImmortalBattleVO*.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/VO_URL.lua",
    ]
    for pattern in patterns:
        candidates.extend(root.glob(pattern))
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _digitdoor_report_gmbattle_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    report_fields = [
        "replayId",
        "type",
        "round",
        "pkStage",
        "zone",
        "pkStep",
        "time",
        "atkVoList",
        "defVoList",
        "clientWinnerId",
        "serverWinnerId",
    ]
    simple_vo_fields = ["ownerId", "resourceId", "index", "lv", "attrVOList"]
    battle_vo_fields = ["id", "round", "winnerId", "startTime", "overTime", "sortTime", "replayId", "joiners", "statList"]
    for path in _digitdoor_report_gmbattle_files(root, logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        is_digitaldoor_battle_vo = "module.digitaldoor.immortaldigital.packet.bean.ImmortalBattleVO" in text
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            categories: list[tuple[str, str, str, str]] = []
            name = path.name

            if name.startswith("CM_DigitDoorReport"):
                for field in report_fields:
                    if f"self.{field}" in line:
                        categories.append(("report_request_schema", "request", field, "CM_DigitDoorReport"))
                for field in ["replayId", "time", "clientWinnerId", "serverWinnerId"]:
                    if (f"self.{field}=self:readLong()" in line) or (f"writeLong(self.{field})" in line):
                        categories.append(("report_request_long_wire", "request", field, "CM_DigitDoorReport"))
                for field in ["type", "round", "pkStage", "zone", "pkStep"]:
                    if (f"self.{field}=self:readInt()" in line) or (f"writeInt(self.{field})" in line):
                        categories.append(("report_request_int_wire", "request", field, "CM_DigitDoorReport"))
                for field in ["atkVoList", "defVoList"]:
                    if f"readMessageList2List(self.{field})" in line or f"writeList(self.{field})" in line:
                        categories.append(("report_request_digitdoor_simple_list_wire", "request", field, "CM_DigitDoorReport"))
                if "return 91644" in line:
                    categories.append(("report_request_packet_id", "request", "", "CM_DigitDoorReport"))

            elif name.startswith("SM_DigitDoorReport"):
                categories.append(("visible_sm_report_packet", "response", "", "SM_DigitDoorReport"))

            elif name.startswith("SM_DigitDoorGMBattle"):
                if "_M=class(ClientResult,_M)" in line:
                    categories.append(("gmbattle_clientresult_super", "response", "code", "SM_DigitDoorGMBattle"))
                if "self.battleVO" in line:
                    categories.append(("gmbattle_response_schema", "response", "battleVO", "SM_DigitDoorGMBattle"))
                if "self.type" in line:
                    categories.append(("gmbattle_response_schema", "response", "type", "SM_DigitDoorGMBattle"))
                if "readBean(typeof(ImmortalBattleVO))" in line or "writeBean(self.battleVO)" in line:
                    categories.append(("gmbattle_battle_vo_wire", "response", "battleVO", "SM_DigitDoorGMBattle"))
                if "self.type=self:readInt()" in line or "writeInt(self.type)" in line:
                    categories.append(("gmbattle_type_int_wire", "response", "type", "SM_DigitDoorGMBattle"))
                if "return 91643" in line:
                    categories.append(("gmbattle_packet_id", "response", "", "SM_DigitDoorGMBattle"))

            elif name.startswith("CM_DigitDoorGMBattle"):
                categories.append(("visible_cm_gmbattle_packet", "request", "", "CM_DigitDoorGMBattle"))

            elif name.startswith("DigitDoorSimpleVO"):
                for field in simple_vo_fields:
                    if f"self.{field}" in line:
                        categories.append(("simple_vo_schema", "request", field, "DigitDoorSimpleVO"))
                for field in ["ownerId"]:
                    if f"self.{field}=self:readLong()" in line or f"writeLong(self.{field})" in line:
                        categories.append(("simple_vo_long_wire", "request", field, "DigitDoorSimpleVO"))
                for field in ["resourceId", "index", "lv"]:
                    if f"self.{field}=self:readInt()" in line or f"writeInt(self.{field})" in line:
                        categories.append(("simple_vo_int_wire", "request", field, "DigitDoorSimpleVO"))
                if "readMessageList2List(self.attrVOList)" in line or "writeList(self.attrVOList)" in line:
                    categories.append(("simple_vo_attr_list_wire", "request", "attrVOList", "DigitDoorSimpleVO"))
                if "return 91605" in line:
                    categories.append(("simple_vo_packet_id", "request", "", "DigitDoorSimpleVO"))

            elif name.startswith("ImmortalBattleVO") and is_digitaldoor_battle_vo:
                for field in battle_vo_fields:
                    if f"self.{field}" in line:
                        categories.append(("digitaldoor_battle_vo_schema", "response", field, "ImmortalBattleVO"))
                for field in ["id", "winnerId", "startTime", "overTime", "sortTime", "replayId"]:
                    if f"self.{field}=self:readLong()" in line or f"writeLong(self.{field})" in line:
                        categories.append(("digitaldoor_battle_vo_long_wire", "response", field, "ImmortalBattleVO"))
                if "self.round=self:readInt()" in line or "writeInt(self.round)" in line:
                    categories.append(("digitaldoor_battle_vo_int_wire", "response", "round", "ImmortalBattleVO"))
                for field in ["joiners", "statList"]:
                    if f"readMessageList2List(self.{field})" in line or f"writeList(self.{field})" in line:
                        categories.append(("digitaldoor_battle_vo_list_wire", "response", field, "ImmortalBattleVO"))
                if "return 92034" in line:
                    categories.append(("digitaldoor_battle_vo_packet_id", "response", "", "ImmortalBattleVO"))

            elif name == "VO_URL.lua":
                if "91644" in line and "CM_DigitDoorReport" in line:
                    categories.append(("report_request_vo_url_map", "request", "", "VO_URL"))
                if "91643" in line and "SM_DigitDoorGMBattle" in line:
                    categories.append(("gmbattle_vo_url_map", "response", "", "VO_URL"))
                if "91605" in line and "DigitDoorSimpleVO" in line:
                    categories.append(("simple_vo_url_map", "request", "", "VO_URL"))
                if "92034" in line and "module.digitaldoor.immortaldigital.packet.bean.ImmortalBattleVO" in line:
                    categories.append(("digitaldoor_battle_vo_url_map", "response", "", "VO_URL"))

            if name == "DigitDoorNetLogic.lua":
                if "_SM_DigitDoorGMBattle" in line and "require" in line:
                    categories.append(("gmbattle_import", "response", "", "DigitDoorNetLogic"))
                if "_CM_DigitDoorReport" in line and "require" in line:
                    categories.append(("report_import", "request", "", "DigitDoorNetLogic"))
                if "F_Register(_SM_DigitDoorGMBattle:getId()" in line:
                    categories.append(("gmbattle_register", "response", "", "DigitDoorNetLogic"))
                if "F_Register(_CM_DigitDoorReport:getId()" in line:
                    categories.append(("report_request_register", "request", "", "DigitDoorNetLogic"))
                if "F_Unregister(_SM_DigitDoorGMBattle:getId()" in line:
                    categories.append(("gmbattle_unregister", "response", "", "DigitDoorNetLogic"))
                if "F_Unregister(_CM_DigitDoorReport:getId()" in line:
                    categories.append(("report_request_unregister", "request", "", "DigitDoorNetLogic"))
                if "function _M.CM_DigitDoorReportFun" in line:
                    categories.append(("report_request_handler_entry", "request", "", "DigitDoorNetLogic"))
                if current_function == "_M.CM_DigitDoorReportFun":
                    if "GetMessageFromPools(_CM_DigitDoorReport)" in line:
                        categories.append(("report_request_pool_message", "request", "", "DigitDoorNetLogic"))
                    for field in report_fields:
                        if f"CM_DigitDoorReport.{field}={field}" in line:
                            categories.append(("report_request_fill_field", "request", field, "DigitDoorNetLogic"))
                    if "F_SendMsg(CM_DigitDoorReport)" in line:
                        categories.append(("report_request_send", "request", "", "DigitDoorNetLogic"))
                if "function _M.SM_DigitDoorGMBattleFun" in line:
                    categories.append(("gmbattle_handler_entry", "response", "", "DigitDoorNetLogic"))
                if current_function == "_M.SM_DigitDoorGMBattleFun":
                    if "EntityMgr.Inst_get().UserView" in line:
                        categories.append(("gmbattle_user_view_guard", "response", "UserView", "DigitDoorNetLogic"))
                    if "UpdateFinishVo(msg.battleVO)" in line:
                        categories.append(("gmbattle_update_finish_vo", "response", "battleVO", "DigitDoorNetLogic"))
                    if "ReqReplay" in line:
                        categories.append(("gmbattle_req_replay", "response", "battleVO|type", "DigitDoorNetLogic"))
                    if "msg.battleVO.replayId" in line:
                        categories.append(("gmbattle_replay_id_to_req_replay", "response", "replayId", "DigitDoorNetLogic"))
                    if "msg.type==1" in line and ("89504" in line or "87006" in line):
                        categories.append(("gmbattle_type_selects_map_id", "response", "type", "DigitDoorNetLogic"))
                    if "MapType.ReplayType.DigitDoorPVP" in line or "MapType.ReplayType.TowerDefensePVP" in line:
                        categories.append(("gmbattle_type_selects_replay_type", "response", "type", "DigitDoorNetLogic"))

            if name == "DigitDoorPVPSceneView.lua":
                if current_function == "_M.CheckList":
                    if "self.curFinishVo" in line:
                        categories.append(("report_source_requires_finish_vo", "request", "curFinishVo", "DigitDoorPVPSceneView"))
                    if "GetDefenseViewList()" in line:
                        categories.append(("report_collect_defense_views", "request", "defVoList", "DigitDoorPVPSceneView"))
                    if "GetAttackViewList()" in line:
                        categories.append(("report_collect_attack_views", "request", "atkVoList", "DigitDoorPVPSceneView"))
                    if "Scene_DigitDoorPVPIT" in line:
                        categories.append(("report_source_pvpit_branch", "request", "type|round|time", "DigitDoorPVPSceneView"))
                    if "Scene_DigitDoorPVP" in line:
                        categories.append(("report_source_pvp_branch", "request", "type|pkStage|zone|pkStep|time", "DigitDoorPVPSceneView"))
                    for field in ["replayId", "round", "pkStage", "zone", "pkStep", "startTime", "createTime"]:
                        if f"curFinishVo.{field}" in line:
                            categories.append(("report_source_finish_vo_field", "request", field, "DigitDoorPVPSceneView"))
                    if "atkVoList=self.attackList" in line:
                        categories.append(("report_attack_list_to_request", "request", "atkVoList", "DigitDoorPVPSceneView"))
                    if "defVoList=self.defenseList" in line:
                        categories.append(("report_defense_list_to_request", "request", "defVoList", "DigitDoorPVPSceneView"))
                    if "clientWinnerId" in line and "winnerId" in line:
                        categories.append(("report_client_winner_projection", "request", "clientWinnerId", "DigitDoorPVPSceneView"))
                    if "serverWinnerId=self.winnerId" in line:
                        categories.append(("report_server_winner_projection", "request", "serverWinnerId", "DigitDoorPVPSceneView"))
                    if "CM_DigitDoorReportFun(" in line:
                        categories.append(("report_pvp_scene_sends_report", "request", "", "DigitDoorPVPSceneView"))
                if current_function == "_M.CreateEntityData":
                    if "DigitDoorSimpleVO.new()" in line:
                        categories.append(("report_create_simple_vo", "request", "DigitDoorSimpleVO", "DigitDoorPVPSceneView"))
                    for field in ["ownerId", "resourceId", "index", "lv"]:
                        if f"data.{field}" in line:
                            categories.append(("report_fill_simple_vo_field", "request", field, "DigitDoorPVPSceneView"))
                    if "self:GetAttr(entityView.Entity.EntityData,data.attrVOList)" in line:
                        categories.append(("report_fill_simple_vo_attr_list", "request", "attrVOList", "DigitDoorPVPSceneView"))
                if current_function == "_M.GetAttrCode":
                    if "attrVo.type=id" in line:
                        categories.append(("report_fill_attr_vo_type", "request", "attrVOList", "DigitDoorPVPSceneView"))
                    if "attrVo.value=value" in line:
                        categories.append(("report_fill_attr_vo_value", "request", "attrVOList", "DigitDoorPVPSceneView"))
                if current_function == "_M.GetAttr" and "GetAttrCode(" in line:
                    categories.append(("report_attr_code_emitted", "request", "attrVOList", "DigitDoorPVPSceneView"))

            if name == "DigitDoorMgr.lua":
                if current_function == "_M.OnEnterReplayScene":
                    if "self.DigitDoorPVPReplayMsg=msg" in line:
                        categories.append(("replay_scene_store_msg", "response", "DigitDoorPVPReplayMsg", "DigitDoorMgr"))
                    if "DigitDoorPvpEntityMgr.Inst_get():SceneInfoSync()" in line:
                        categories.append(("replay_scene_sync_pvp_entity_mgr", "response", "DigitDoorPVPReplayMsg", "DigitDoorMgr"))
                    if "DigitDoorFightMgr.Inst_get():SceneInfoSync(msg)" in line:
                        categories.append(("replay_scene_sync_fight_mgr", "response", "DigitDoorPVPReplayMsg", "DigitDoorMgr"))
                if current_function == "_M.LeaveScene" and "DigitDoorPVPReplayMsg=nil" in line:
                    categories.append(("replay_scene_clear_msg", "response", "DigitDoorPVPReplayMsg", "DigitDoorMgr"))

            if name == "DigitDoorFightComponent.lua":
                if current_function == "_M.GM_GetImmortalDigitPartnerVO":
                    if "ImmortalBattleVO.new()" in line:
                        categories.append(("gm_helper_builds_immortal_battle_vo", "local", "ImmortalBattleVO", "DigitDoorFightComponent"))
                    if "data.winnerId" in line:
                        categories.append(("gm_helper_sets_winner_id", "local", "winnerId", "DigitDoorFightComponent"))
                    if "data.joiners:Add" in line:
                        categories.append(("gm_helper_adds_joiners", "local", "joiners", "DigitDoorFightComponent"))

            for category, direction, field, source in categories:
                rows.append(
                    {
                        "category": category,
                        "direction": direction,
                        "field": field,
                        "source": source,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": stripped,
                    }
                )
    return rows


def _digitdoor_report_gmbattle_field_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_notes = {
        "replayId": "PVP report/replay identity; request-side comes from curFinishVo, GMBattle replay uses battleVO.replayId.",
        "type": "PVP mode discriminator: visible code uses 1 for Scene_DigitDoorPVPIT and 2 for Scene_DigitDoorPVP; GMBattle type selects replay map/type.",
        "round": "PVPIT round value copied from curFinishVo.round.",
        "pkStage": "PVP stage copied from curFinishVo.pkStage.",
        "zone": "PVP zone copied from curFinishVo.zone.",
        "pkStep": "PVP step copied from curFinishVo.pkStep.",
        "time": "Report timestamp/source time; PVPIT uses startTime and PVP uses createTime.",
        "atkVoList": "Client-built attacker snapshot list of DigitDoorSimpleVO entries.",
        "defVoList": "Client-built defender snapshot list of DigitDoorSimpleVO entries.",
        "clientWinnerId": "Client-side winner projection, inverted against user id in visible PVPSceneView logic.",
        "serverWinnerId": "Server/source winner id copied from self.winnerId.",
        "ownerId": "DigitDoorSimpleVO owner id copied from entity data.",
        "resourceId": "DigitDoorSimpleVO role/resource id.",
        "index": "DigitDoorSimpleVO position index.",
        "lv": "DigitDoorSimpleVO level.",
        "attrVOList": "DigitDoorSimpleVO attribute snapshot list filled by GetAttr/GetAttrCode.",
        "battleVO": "SM_DigitDoorGMBattle battle object; visible handler updates ImmortalData finish VO and requests replay.",
        "UserView": "Handler guard: replay request only runs if a current user view exists.",
        "DigitDoorPVPReplayMsg": "Mgr stores replay scene message and passes it into entity/fight scene sync.",
        "ImmortalBattleVO": "DigitalDoor immortal battle bean used by SM_DigitDoorGMBattle and GM helper.",
        "winnerId": "ImmortalBattleVO/server winner field and GM helper local winner setup.",
        "joiners": "ImmortalBattleVO participant list; also built by GM helper.",
        "statList": "ImmortalBattleVO stats list.",
        "code": "SM_DigitDoorGMBattle inherits ClientResult, but visible handler has no CheckCodeMessage guard.",
        "curFinishVo": "PVPSceneView finish-state source object used to build CM_DigitDoorReport.",
    }
    by_field: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"directions": set(), "categories": set(), "sources": set()})
    for row in rows:
        for field in [item for item in str(row.get("field") or "").split("|") if item]:
            by_field[field]["directions"].add(str(row.get("direction") or ""))
            by_field[field]["categories"].add(str(row.get("category") or ""))
            by_field[field]["sources"].add(str(row.get("source") or ""))
    return [
        {
            "field": field,
            "directions": " | ".join(sorted(item for item in values["directions"] if item)),
            "sources": " | ".join(sorted(item for item in values["sources"] if item)),
            "categories": " | ".join(sorted(values["categories"])),
            "note": field_notes.get(field, ""),
        }
        for field, values in sorted(by_field.items())
    ]


def _write_digitdoor_report_gmbattle_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    field_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor Report/GMBattle boundary",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Field rows: {len(field_rows)}",
        f"- Evidence rows: {len(evidence_rows)}",
        "- Scope: static Lua evidence for the DigitDoor PVP report upload and server-pushed replay boundary. It does not modify traffic, replay packets, or assert server acceptance beyond visible client code.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Field Boundary",
            "",
            "| Field | Direction | Sources | Note |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in field_rows:
        lines.append(
            "| "
            f"{_md_table_cell(row.get('field', ''))} | "
            f"{_md_table_cell(row.get('directions', ''))} | "
            f"{_md_table_cell(row.get('sources', ''))} | "
            f"{_md_table_cell(row.get('note', ''))} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `CM_DigitDoorReport(91644)` is a client-to-server PVP report upload, not a reward/settlement response.",
            "- `DigitDoorPVPSceneView:CheckList` builds the request from `curFinishVo`, current attacker/defender entity snapshots, and visible winner ids.",
            "- Entity snapshots are `DigitDoorSimpleVO(91605)` rows with owner/resource/index/lv plus an attribute list filled by `GetAttr/GetAttrCode`.",
            "- There is no visible `SM_DigitDoorReport`; treat server acceptance/validation as outside this visible Lua surface.",
            "- `SM_DigitDoorGMBattle(91643)` is a server-to-client `ClientResult` carrying `battleVO` and `type`; the handler updates ImmortalData finish VO and calls `ReqReplay` using `battleVO.replayId`.",
            "- `SM_DigitDoorGMBattle.type` selects replay map/replay type (`DigitDoorPVP` versus `TowerDefensePVP` in visible code); it is a replay trigger, not the GamePlayer settlement authority.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_report_gmbattle_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_rows = _digitdoor_report_gmbattle_rows(root, logic_dir)
    field_rows = _digitdoor_report_gmbattle_field_rows(evidence_rows)
    category_counts = Counter(str(row.get("category") or "") for row in evidence_rows)
    visible_sm_report_files = [
        path for path in _digitdoor_report_gmbattle_files(root, logic_dir) if path.name.startswith("SM_DigitDoorReport")
    ]
    visible_cm_gmbattle_files = [
        path for path in _digitdoor_report_gmbattle_files(root, logic_dir) if path.name.startswith("CM_DigitDoorGMBattle")
    ]
    stats = {
        "source_file_count": len(_digitdoor_report_gmbattle_files(root, logic_dir)),
        "evidence_row_count": len(evidence_rows),
        "field_row_count": len(field_rows),
        "report_request_packet_id_rows": category_counts.get("report_request_packet_id", 0)
        + category_counts.get("report_request_vo_url_map", 0),
        "report_request_schema_rows": category_counts.get("report_request_schema", 0),
        "report_request_fill_rows": category_counts.get("report_request_fill_field", 0),
        "report_request_send_rows": category_counts.get("report_request_send", 0),
        "pvp_scene_report_call_rows": category_counts.get("report_pvp_scene_sends_report", 0),
        "pvp_scene_source_rows": category_counts.get("report_source_finish_vo_field", 0)
        + category_counts.get("report_source_pvpit_branch", 0)
        + category_counts.get("report_source_pvp_branch", 0),
        "simple_vo_schema_rows": category_counts.get("simple_vo_schema", 0),
        "simple_vo_attr_projection_rows": category_counts.get("report_fill_simple_vo_attr_list", 0)
        + category_counts.get("report_fill_attr_vo_type", 0)
        + category_counts.get("report_fill_attr_vo_value", 0)
        + category_counts.get("report_attr_code_emitted", 0),
        "visible_sm_report_file_count": len(visible_sm_report_files),
        "gmbattle_packet_id_rows": category_counts.get("gmbattle_packet_id", 0) + category_counts.get("gmbattle_vo_url_map", 0),
        "gmbattle_schema_rows": category_counts.get("gmbattle_response_schema", 0),
        "gmbattle_replay_rows": category_counts.get("gmbattle_update_finish_vo", 0)
        + category_counts.get("gmbattle_req_replay", 0)
        + category_counts.get("gmbattle_replay_id_to_req_replay", 0),
        "gmbattle_type_branch_rows": category_counts.get("gmbattle_type_selects_map_id", 0)
        + category_counts.get("gmbattle_type_selects_replay_type", 0),
        "visible_cm_gmbattle_file_count": len(visible_cm_gmbattle_files),
        "digitaldoor_battle_vo_schema_rows": category_counts.get("digitaldoor_battle_vo_schema", 0),
        "replay_scene_sync_rows": category_counts.get("replay_scene_store_msg", 0)
        + category_counts.get("replay_scene_sync_pvp_entity_mgr", 0)
        + category_counts.get("replay_scene_sync_fight_mgr", 0),
    }
    verdict = {
        "report_request_schema_confirmed": stats["report_request_packet_id_rows"] > 0
        and stats["report_request_schema_rows"] >= 11
        and stats["report_request_fill_rows"] >= 11
        and stats["report_request_send_rows"] > 0,
        "pvp_scene_builds_report_from_finish_state": stats["pvp_scene_report_call_rows"] > 0
        and stats["pvp_scene_source_rows"] >= 6,
        "report_entity_snapshot_shape_confirmed": stats["simple_vo_schema_rows"] >= 5
        and stats["simple_vo_attr_projection_rows"] >= 3,
        "no_visible_dedicated_sm_report_packet": stats["visible_sm_report_file_count"] == 0,
        "gmbattle_response_schema_confirmed": stats["gmbattle_packet_id_rows"] > 0
        and stats["gmbattle_schema_rows"] >= 2
        and stats["digitaldoor_battle_vo_schema_rows"] >= 9,
        "gmbattle_is_server_only_replay_trigger": stats["visible_cm_gmbattle_file_count"] == 0
        and stats["gmbattle_replay_rows"] >= 3,
        "gmbattle_type_selects_replay_context": stats["gmbattle_type_branch_rows"] >= 2,
        "replay_scene_sync_path_visible": stats["replay_scene_sync_rows"] >= 3,
    }
    _write_tsv(
        out_dir / "report_gmbattle_fields.tsv",
        field_rows,
        ["field", "directions", "sources", "categories", "note"],
    )
    _write_tsv(
        out_dir / "report_gmbattle_evidence.tsv",
        evidence_rows,
        ["category", "direction", "field", "source", "file", "line", "function", "snippet"],
    )
    report_path = out_dir / "report_gmbattle_report.md"
    _write_digitdoor_report_gmbattle_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        field_rows=field_rows,
        evidence_rows=evidence_rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["report_request_schema_confirmed"] and verdict["gmbattle_is_server_only_replay_trigger"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "fields": str(out_dir / "report_gmbattle_fields.tsv"),
            "evidence": str(out_dir / "report_gmbattle_evidence.tsv"),
        },
    }


_DIGITDOOR_PVP_BALANCE_CONFIG_KEYS: dict[str, str] = {
    "PVP_TIMELIMIT": "PVP scene time limit; visible PVP scene init reads it and falls back to 120 when absent.",
    "FAKEPVP_REGULATION": "Comma pair consumed as PVP winner-side HP extension and reduce-damage extension rates.",
    "FAKEPVP_REGULATION_WINNER": "Winner-side dynamic damage reduction rate used by UpdatePVPWinnerProtection.",
    "WIN_TEAM_LOSTHP_LIMIT": "Winner-side per-period HP loss limit pair, split by front/back partner position.",
    "FAIL_TEAM_LOSTHP_LIMIT": "Loser-side per-period HP loss limit pair, split by front/back partner position.",
    "WIN_TEAM_attack": "Winner-side delayed attack extension added after the PVP winner max-time check.",
}


def _digitdoor_pvp_balance_files(root: Path, logic_dir: Path, config_dir: Path) -> list[Path]:
    names = [
        "DigitDoorFightComponent.lua",
        "DigitDoorEntityData.lua",
        "DigitDoorPartner.lua",
        "DigitDoorPartnerView.lua",
        "DigitDoorPVPSceneView.lua",
    ]
    candidates = [logic_dir / name for name in names]
    candidates.extend(
        [
            config_dir / "ConfigValue.lua",
            config_dir / "CharacterLevel.lua",
            config_dir / "AttrName.lua",
        ]
    )
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _digitdoor_pvp_balance_config_rows(root: Path, config_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    config_path = config_dir / "ConfigValue.lua"
    config_rows = _parse_config_rows(config_dir, "ConfigValue", None)
    by_id = {str(row.get("id") or ""): row for row in config_rows}
    for key, note in _DIGITDOOR_PVP_BALANCE_CONFIG_KEYS.items():
        row = by_id.get(key)
        if row is None:
            continue
        rows.append(
            {
                "category": "pvp_config_value",
                "role": "config",
                "field": key,
                "source": "ConfigValue",
                "file": _path_display(config_path, root),
                "line": "",
                "function": "",
                "value": row.get("value", ""),
                "snippet": f"{key}={row.get('value', '')}",
                "note": note,
            }
        )

    character_path = config_dir / "CharacterLevel.lua"
    character_rows = _parse_config_rows(config_dir, "CharacterLevel", None)
    if character_path.is_file() and character_rows:
        for field in ["PVPATTACK", "PVPINCREASE", "PVPREDUCE", "PVP_WINREDUCE"]:
            values = [str(row.get(field) or "") for row in character_rows if str(row.get(field) or "") != ""]
            nonzero_values = [value for value in values if value not in {"0", "0.0"}]
            if not values:
                continue
            sample_values = _dedupe_preserve(nonzero_values[:8] or values[:8])
            rows.append(
                {
                    "category": "character_level_pvp_field_summary",
                    "role": "config",
                    "field": field,
                    "source": "CharacterLevel",
                    "file": _path_display(character_path, root),
                    "line": "",
                    "function": "",
                    "value": ",".join(sample_values),
                    "snippet": f"{field}: rows={len(values)}, nonzero={len(nonzero_values)}, samples={','.join(sample_values)}",
                    "note": "CharacterLevel defines this PVP-related column; visible Lua currently consumes PVPATTACK/PVPINCREASE/PVPREDUCE directly.",
                }
            )
    return rows


def _digitdoor_pvp_balance_rows(root: Path, logic_dir: Path, config_dir: Path) -> list[dict[str, Any]]:
    rows = _digitdoor_pvp_balance_config_rows(root, config_dir)
    for path in _digitdoor_pvp_balance_files(root, logic_dir, config_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            categories: list[tuple[str, str, str, str, str]] = []
            name = path.name

            if name == "ConfigValue.lua":
                for key in _DIGITDOOR_PVP_BALANCE_CONFIG_KEYS:
                    if key in line:
                        categories.append(("pvp_config_key_map", "config", key, "ConfigValue", "Generated config key/value mapping."))

            elif name == "CharacterLevel.lua":
                for field in ["PVPATTACK", "PVPINCREASE", "PVPREDUCE", "PVP_WINREDUCE"]:
                    if field in line:
                        categories.append(
                            (
                                "character_level_pvp_field_defined",
                                "config",
                                field,
                                "CharacterLevel",
                                "Character-level table exposes this PVP-related column.",
                            )
                        )

            elif name == "AttrName.lua" and "PVPATTACK" in line:
                categories.append(("pvp_attr_name_defined", "config", "PVPATTACK", "AttrName", "PVP attack has a visible display-name entry."))

            elif name == "DigitDoorFightComponent.lua":
                if "PVP_BALANCE_DURATION" in line:
                    categories.append(
                        (
                            "pvp_balance_timer_const",
                            "runtime",
                            "PVP_BALANCE_DURATION",
                            "DigitDoorFightComponent",
                            "Winner-protection balance update cadence.",
                        )
                    )
                if "PVP_WINNER_MAXTIME_CHECK" in line:
                    categories.append(
                        (
                            "pvp_winner_attack_time_const",
                            "runtime",
                            "PVP_WINNER_MAXTIME_CHECK",
                            "DigitDoorFightComponent",
                            "Elapsed-time threshold before winner attack extension starts.",
                        )
                    )
                if "PVP_WINNER_ATTACK_DURATION" in line:
                    categories.append(
                        (
                            "pvp_winner_attack_duration_const",
                            "runtime",
                            "PVP_WINNER_ATTACK_DURATION",
                            "DigitDoorFightComponent",
                            "Interval for repeated winner attack extension.",
                        )
                    )
                if current_function == "_M.InitBalanceValue":
                    for key in _DIGITDOOR_PVP_BALANCE_CONFIG_KEYS:
                        if key in line:
                            categories.append(
                                (
                                    "init_balance_reads_config",
                                    "runtime",
                                    key,
                                    "DigitDoorFightComponent",
                                    "InitBalanceValue reads this ConfigValue key in PVP scenes.",
                                )
                            )
                    init_terms = {
                        "pvpHpExtRate": "Winner-side max-HP extension rate derived from FAKEPVP_REGULATION.",
                        "pvpReduceDamageExtRate": "Winner-side reduce-damage extension rate derived from FAKEPVP_REGULATION.",
                        "pvpWinnerReduceRate": "Dynamic winner-side reduction rate derived from FAKEPVP_REGULATION_WINNER.",
                        "pvpWinnerDamageLimit": "Winner-side HP loss limit derived from WIN_TEAM_LOSTHP_LIMIT.",
                        "pvpLoserDamageLimit": "Loser-side HP loss limit derived from FAIL_TEAM_LOSTHP_LIMIT.",
                        "pvpWinnerExtAttack": "Delayed winner-side attack extension derived from WIN_TEAM_attack.",
                    }
                    for field, note in init_terms.items():
                        if field in line:
                            categories.append(("init_balance_runtime_field", "runtime", field, "DigitDoorFightComponent", note))
                if current_function == "_M.CreateAttackerPartner":
                    create_terms = {
                        "pvpHpRate": "Winner-side HP rate is assigned before partner InitData.",
                        "pvpReduceDamageRate": "Winner-side reduce-damage rate is assigned before partner InitData.",
                        "pvpWinnerDamageLimit": "Winner team receives the winner HP-loss limit pair.",
                        "pvpLoserDamageLimit": "Non-winner team receives the loser HP-loss limit pair.",
                        "_Partner:InitData(partnerData,isPVE,pvpHpRate,pvpReduceDamageRate,damageLimit)": "PVP rates and damage limit are passed into partner initialization.",
                    }
                    for field, note in create_terms.items():
                        if field in line:
                            categories.append(("create_attacker_partner_pvp_init", "runtime", field, "DigitDoorFightComponent", note))
                if current_function == "_M.AddDamageResult":
                    read_terms = {
                        "GetPVPWinnerExtAttack": "Caster winner attack extension is folded into extAttack before damage.",
                        "GetPVPIncreaseDamage": "Caster PVP increase is read for the PVP damage multiplier.",
                        "GetPVPReduceDamage": "Target PVP reduce is read for the PVP damage multiplier.",
                        "GetPVPWinnerReduceDamage": "Target winner-protection reduce is read for the PVP damage multiplier.",
                    }
                    for field, note in read_terms.items():
                        if field in line:
                            categories.append(("pvp_damage_reads_runtime_attr", "runtime", field, "DigitDoorFightComponent", note))
                    formula_terms = {
                        "pvpIncreaseDamage=1+": "pvpIncreaseDamage = 1 + PVPINCREASE * Damage_Ratio.",
                        "pvpReduceDamage=Mathf.Max": "pvpReduceDamage has a floor before multiplication.",
                        "pvpWinnerReduceDamage=Mathf.Max": "pvpWinnerReduceDamage has a floor before multiplication.",
                        "pvpDamage=": "Separate pvpDamage branch applies PVP multipliers before current-HP calculation.",
                        "CalculateCurrentHp(pvpDamage)": "PVP branch applies pvpDamage into EntityData:CalculateCurrentHp.",
                    }
                    for field, note in formula_terms.items():
                        if field in line:
                            categories.append(("pvp_damage_formula", "runtime", field, "DigitDoorFightComponent", note))
                if current_function == "_M.GetAttackerFinalAttr" and "GetPVPAttack()" in line:
                    categories.append(
                        (
                            "pvp_attack_overrides_base_attack",
                            "runtime",
                            "PVPAttack",
                            "DigitDoorFightComponent",
                            "PVP partner attack uses EntityData:GetPVPAttack() instead of base attack.",
                        )
                    )
                if current_function == "_M.UpdatePVPWinnerProtection":
                    if "curWinnerHp/curLoserHp" in line:
                        categories.append(
                            (
                                "winner_protection_hp_ratio_branch",
                                "runtime",
                                "curWinnerHp/curLoserHp",
                                "DigitDoorFightComponent",
                                "Winner protection branches on winner/loser current HP ratio.",
                            )
                        )
                    if "SetPVPWinnerReduceDamage" in line:
                        categories.append(
                            (
                                "winner_protection_reduce_update",
                                "runtime",
                                "PVPWinnerReduceDamage",
                                "DigitDoorFightComponent",
                                "Winner side receives dynamic PVP damage reduction.",
                            )
                        )
                    if "PVP_WINNER_MAXTIME_CHECK" in line:
                        categories.append(
                            (
                                "winner_protection_attack_after_time",
                                "runtime",
                                "PVP_WINNER_MAXTIME_CHECK",
                                "DigitDoorFightComponent",
                                "Winner attack extension only starts after the max-time check threshold.",
                            )
                        )
                    if "SetPVPWinnerExtAttack" in line:
                        categories.append(
                            (
                                "winner_protection_attack_update",
                                "runtime",
                                "PVPWinnerExtAttack",
                                "DigitDoorFightComponent",
                                "Winner side receives repeated PVP attack extension.",
                            )
                        )

            elif name == "DigitDoorEntityData.lua":
                if current_function == "_M.InitData":
                    entity_terms = {
                        "pvpHpRate": "PVP HP rate inflates CurrentHp and MaxHp from cfg.MAXHP.",
                        "pvpReduceDamageRate": "PVP reduce-damage rate inflates REDUCEDAMAGE.",
                        "cfg.PVPATTACK": "CharacterLevel.PVPATTACK is copied into EntityData.PVPAttack.",
                        "cfg.PVPINCREASE": "CharacterLevel.PVPINCREASE is copied into EntityData.PVPIncreaseDamage.",
                        "cfg.PVPREDUCE": "CharacterLevel.PVPREDUCE is copied into EntityData.PVPReduceDamage.",
                    }
                    for field, note in entity_terms.items():
                        if field in line:
                            categories.append(("entitydata_init_pvp_field", "runtime", field, "DigitDoorEntityData", note))
                accessor_terms = {
                    "SetPVPIncreaseDamage": "Encrypted state accessor for PVP increase.",
                    "GetPVPIncreaseDamage": "Encrypted state accessor for PVP increase.",
                    "SetPVPReduceDamage": "Encrypted state accessor for PVP reduce.",
                    "GetPVPReduceDamage": "Encrypted state accessor for PVP reduce.",
                    "SetPVPWinnerReduceDamage": "Encrypted state accessor for winner protection reduce.",
                    "GetPVPWinnerReduceDamage": "Encrypted state accessor for winner protection reduce.",
                    "SetPVPWinnerExtAttack": "Encrypted state accessor for winner attack extension.",
                    "GetPVPWinnerExtAttack": "Encrypted state accessor for winner attack extension.",
                    "SetPVPDamageLimit": "Encrypted state accessor for per-period PVP damage limit.",
                    "GetPVPDamageLimit": "Encrypted state accessor for per-period PVP damage limit.",
                    "SetDefaultPVPDamageLimit": "Encrypted state accessor for default per-period PVP damage limit.",
                    "GetDefaultPVPDamageLimit": "Encrypted state accessor for default per-period PVP damage limit.",
                    "SetPVPAttack": "Encrypted state accessor for PVP attack.",
                    "GetPVPAttack": "Encrypted state accessor for PVP attack.",
                }
                for field, note in accessor_terms.items():
                    if field in line:
                        categories.append(("entitydata_pvp_accessor", "runtime", field, "DigitDoorEntityData", note))
                if current_function == "_M.CalculateCurrentHp":
                    for field in ["GetDefaultPVPDamageLimit", "GetPVPDamageLimit", "SetPVPDamageLimit"]:
                        if field in line:
                            categories.append(
                                (
                                    "entitydata_damage_limit_consumed",
                                    "runtime",
                                    field,
                                    "DigitDoorEntityData",
                                    "CalculateCurrentHp caps current damage by the remaining PVP damage-limit budget.",
                                )
                            )

            elif name == "DigitDoorPartner.lua" and current_function == "_M.InitData":
                partner_terms = {
                    "limitRate=self.isFront": "Damage-limit pair selects front/back partner limit rate.",
                    "limitVal=maxHp*limitRate*Ratio": "Configured limit rate is converted to a max-HP-scaled value.",
                    "SetDefaultPVPDamageLimit": "Default PVP damage limit is initialized on EntityData.",
                    "SetPVPDamageLimit": "Current PVP damage limit is initialized on EntityData.",
                }
                for field, note in partner_terms.items():
                    if field in line:
                        categories.append(("partner_damage_limit_init", "runtime", field, "DigitDoorPartner", note))

            elif name == "DigitDoorPartnerView.lua":
                if current_function == "_M.UpdatePVPDamageLimit":
                    for field in ["GetDefaultPVPDamageLimit", "SetPVPDamageLimit", "damageLimit"]:
                        if field in line:
                            categories.append(
                                (
                                    "partnerview_damage_limit_periodic_reset",
                                    "runtime",
                                    field,
                                    "DigitDoorPartnerView",
                                    "PVP damage-limit budget is restored periodically from the default value.",
                                )
                            )

            elif name == "DigitDoorPVPSceneView.lua":
                if "PVP_TIMELIMIT" in line:
                    categories.append(
                        (
                            "pvp_scene_reads_time_limit",
                            "runtime",
                            "PVP_TIMELIMIT",
                            "DigitDoorPVPSceneView",
                            "PVP scene init reads the configured time limit.",
                        )
                    )
                if current_function == "_M.GetAttr":
                    for field in ["PVPATTACK", "PVPINCREASE", "PVPREDUCE"]:
                        if field in line:
                            categories.append(
                                (
                                    "pvp_report_attr_code_emitted",
                                    "request",
                                    field,
                                    "DigitDoorPVPSceneView",
                                    "PVP report snapshot emits this attr code into attrVOList.",
                                )
                            )

            for category, role, field, source, note in categories:
                rows.append(
                    {
                        "category": category,
                        "role": role,
                        "field": field,
                        "source": source,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "value": "",
                        "snippet": stripped,
                        "note": note,
                    }
                )
    return rows


def _digitdoor_pvp_balance_field_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_notes = {
        **_DIGITDOOR_PVP_BALANCE_CONFIG_KEYS,
        "PVPATTACK": "CharacterLevel PVP attack field; EntityData stores it and PVP damage uses GetPVPAttack for partners.",
        "PVPINCREASE": "CharacterLevel PVP increase field; damage formula multiplies by 1 + PVPINCREASE * Damage_Ratio.",
        "PVPREDUCE": "CharacterLevel PVP reduce field; target-side damage formula multiplies by a floored reduce factor.",
        "PVP_WINREDUCE": "CharacterLevel column is visible, but current Lua evidence does not show direct cfg.PVP_WINREDUCE consumption in EntityData.InitData.",
        "PVPAttack": "Runtime PVP attack slot used instead of base attack in PVP partner damage.",
        "PVPWinnerReduceDamage": "Runtime winner-protection reduce slot, dynamically adjusted by UpdatePVPWinnerProtection.",
        "PVPWinnerExtAttack": "Runtime winner attack-extension slot, dynamically increased after the PVP time threshold.",
        "PVP_BALANCE_DURATION": "Winner-protection update cadence.",
        "PVP_WINNER_MAXTIME_CHECK": "Elapsed-time threshold before winner attack extension starts.",
        "PVP_WINNER_ATTACK_DURATION": "Repeated winner attack-extension cadence.",
        "curWinnerHp/curLoserHp": "Winner-protection branch compares total winner HP and loser HP.",
    }
    by_field: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"roles": set(), "categories": set(), "sources": set(), "values": set()})
    for row in rows:
        for field in [item for item in str(row.get("field") or "").split("|") if item]:
            by_field[field]["roles"].add(str(row.get("role") or ""))
            by_field[field]["categories"].add(str(row.get("category") or ""))
            by_field[field]["sources"].add(str(row.get("source") or ""))
            if row.get("value") not in (None, ""):
                by_field[field]["values"].add(str(row.get("value")))
    return [
        {
            "field": field,
            "roles": " | ".join(sorted(item for item in values["roles"] if item)),
            "sources": " | ".join(sorted(item for item in values["sources"] if item)),
            "categories": " | ".join(sorted(values["categories"])),
            "values": " | ".join(sorted(values["values"])),
            "note": field_notes.get(field, ""),
        }
        for field, values in sorted(by_field.items())
    ]


def _write_digitdoor_pvp_balance_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    config_dir: Path,
    field_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    config_rows = [row for row in evidence_rows if row.get("category") == "pvp_config_value"]
    lines = [
        "# DigitDoor PVP balance/formula probe",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Config dir: `{config_dir}`",
        f"- Field rows: {len(field_rows)}",
        f"- Evidence rows: {len(evidence_rows)}",
        "- Scope: static Lua/config evidence for DigitDoor PVP attribute initialization, local damage formula, and PVP report snapshot fields. It does not modify packets, memory, APKs, or assert server acceptance beyond visible client code.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Config Values", "", "| Key | Value | Note |", "| --- | --- | --- |"])
    for row in config_rows:
        lines.append(
            "| "
            f"{_md_table_cell(row.get('field', ''))} | "
            f"{_md_table_cell(row.get('value', ''))} | "
            f"{_md_table_cell(row.get('note', ''))} |"
        )
    lines.extend(
        [
            "",
            "## Field Boundary",
            "",
            "| Field | Role | Source | Values | Note |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in field_rows:
        lines.append(
            "| "
            f"{_md_table_cell(row.get('field', ''))} | "
            f"{_md_table_cell(row.get('roles', ''))} | "
            f"{_md_table_cell(row.get('sources', ''))} | "
            f"{_md_table_cell(row.get('values', ''))} | "
            f"{_md_table_cell(row.get('note', ''))} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- PVP-specific config is visible in `DigitDoor_ConfigValue` and consumed in `DigitDoorFightComponent:InitBalanceValue` only when the current scene is PVP.",
            "- `FAKEPVP_REGULATION` feeds winner-side HP and reduce-damage extension rates; `WIN_TEAM_LOSTHP_LIMIT` and `FAIL_TEAM_LOSTHP_LIMIT` feed per-period HP loss caps.",
            "- `CharacterLevel.PVPATTACK/PVPINCREASE/PVPREDUCE` are copied into `DigitDoorEntityData`; PVP partner attack uses `GetPVPAttack()` instead of base `GetAttack()`.",
            "- `DigitDoorFightComponent:AddDamageResult` has a visible PVP branch: it reads PVP increase/reduce/winner-reduce/winner-attack slots, computes `pvpDamage`, and applies it through `CalculateCurrentHp(pvpDamage)`.",
            "- `CalculateCurrentHp` consumes the remaining PVP damage-limit budget; `DigitDoorPartnerView:UpdatePVPDamageLimit` periodically restores that budget from the default limit.",
            "- Winner protection is local and dynamic in visible Lua: it compares winner/loser current HP totals, adjusts `PVPWinnerReduceDamage`, and after the time threshold repeatedly adds `PVPWinnerExtAttack`.",
            "- PVP report snapshots emit `PVPATTACK`, `PVPINCREASE`, and `PVPREDUCE` into `attrVOList`; whether a server trusts or rejects these snapshots remains outside this static Lua evidence.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_pvp_balance_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    digitdoor_config_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_rows = _digitdoor_pvp_balance_rows(root, logic_dir, config_dir)
    field_rows = _digitdoor_pvp_balance_field_rows(evidence_rows)
    category_counts = Counter(str(row.get("category") or "") for row in evidence_rows)
    stats = {
        "source_file_count": len(_digitdoor_pvp_balance_files(root, logic_dir, config_dir)),
        "evidence_row_count": len(evidence_rows),
        "field_row_count": len(field_rows),
        "config_value_rows": category_counts.get("pvp_config_value", 0),
        "config_consumer_rows": category_counts.get("init_balance_reads_config", 0),
        "character_level_pvp_field_rows": category_counts.get("character_level_pvp_field_defined", 0)
        + category_counts.get("character_level_pvp_field_summary", 0),
        "entitydata_pvp_init_rows": category_counts.get("entitydata_init_pvp_field", 0),
        "create_attacker_pvp_init_rows": category_counts.get("create_attacker_partner_pvp_init", 0),
        "pvp_attack_override_rows": category_counts.get("pvp_attack_overrides_base_attack", 0),
        "pvp_damage_attr_read_rows": category_counts.get("pvp_damage_reads_runtime_attr", 0),
        "pvp_damage_formula_rows": category_counts.get("pvp_damage_formula", 0),
        "damage_limit_init_rows": category_counts.get("partner_damage_limit_init", 0),
        "damage_limit_consume_rows": category_counts.get("entitydata_damage_limit_consumed", 0),
        "damage_limit_reset_rows": category_counts.get("partnerview_damage_limit_periodic_reset", 0),
        "winner_protection_rows": category_counts.get("winner_protection_hp_ratio_branch", 0)
        + category_counts.get("winner_protection_reduce_update", 0)
        + category_counts.get("winner_protection_attack_after_time", 0)
        + category_counts.get("winner_protection_attack_update", 0),
        "pvp_report_attr_rows": category_counts.get("pvp_report_attr_code_emitted", 0),
        "pvp_scene_time_limit_rows": category_counts.get("pvp_scene_reads_time_limit", 0),
    }
    verdict = {
        "pvp_config_values_confirmed": stats["config_value_rows"] >= len(_DIGITDOOR_PVP_BALANCE_CONFIG_KEYS)
        and stats["config_consumer_rows"] >= len(_DIGITDOOR_PVP_BALANCE_CONFIG_KEYS) - 1,
        "character_level_pvp_fields_visible": stats["character_level_pvp_field_rows"] >= 3,
        "pvp_hp_reduce_init_chain_visible": stats["entitydata_pvp_init_rows"] >= 5
        and stats["create_attacker_pvp_init_rows"] >= 4,
        "pvp_attack_overrides_base_attack": stats["pvp_attack_override_rows"] > 0,
        "pvp_damage_formula_visible": stats["pvp_damage_attr_read_rows"] >= 4 and stats["pvp_damage_formula_rows"] >= 5,
        "pvp_damage_limit_chain_visible": stats["damage_limit_init_rows"] >= 3
        and stats["damage_limit_consume_rows"] >= 3
        and stats["damage_limit_reset_rows"] >= 2,
        "pvp_winner_protection_visible": stats["winner_protection_rows"] >= 4,
        "pvp_report_attr_codes_visible": stats["pvp_report_attr_rows"] >= 3,
        "pvp_scene_time_limit_visible": stats["pvp_scene_time_limit_rows"] > 0,
    }
    verdict["pvp_balance_is_client_visible_local_formula"] = (
        verdict["pvp_config_values_confirmed"]
        and verdict["pvp_hp_reduce_init_chain_visible"]
        and verdict["pvp_attack_overrides_base_attack"]
        and verdict["pvp_damage_formula_visible"]
        and verdict["pvp_damage_limit_chain_visible"]
        and verdict["pvp_winner_protection_visible"]
    )
    _write_tsv(
        out_dir / "pvp_balance_fields.tsv",
        field_rows,
        ["field", "roles", "sources", "categories", "values", "note"],
    )
    _write_tsv(
        out_dir / "pvp_balance_evidence.tsv",
        evidence_rows,
        ["category", "role", "field", "source", "file", "line", "function", "value", "snippet", "note"],
    )
    report_path = out_dir / "pvp_balance_report.md"
    _write_digitdoor_pvp_balance_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        config_dir=config_dir,
        field_rows=field_rows,
        evidence_rows=evidence_rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["pvp_balance_is_client_visible_local_formula"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "fields": str(out_dir / "pvp_balance_fields.tsv"),
            "evidence": str(out_dir / "pvp_balance_evidence.tsv"),
        },
    }


_DIGITDOOR_PVP_WINREDUCE_CPP2IL_TERMS: dict[str, str] = {
    "PVP_WINREDUCE": "direct_character_level_column",
    "PVPWINREDUCE": "direct_character_level_column_variant",
    "PVPWinnerReduceDamage": "runtime_winner_reduce_slot",
    "pvpWinnerReduceDamage": "runtime_winner_reduce_slot",
    "PVP_WINNER_DAMAGE_REDUCE": "adjacent_towerdefense_style_config",
    "pvpDamageReduce": "adjacent_towerdefense_style_runtime",
}


def _digitdoor_pvp_winreduce_files(root: Path, logic_dir: Path, config_dir: Path) -> list[Path]:
    candidates = [config_dir / "CharacterLevel.lua", config_dir / "ConfigValue.lua"]
    candidates.extend(sorted(logic_dir.glob("*.lua"), key=lambda item: item.name.lower()))
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _digitdoor_pvp_winreduce_rows(root: Path, logic_dir: Path, config_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    character_path = config_dir / "CharacterLevel.lua"
    character_rows = _parse_config_rows(config_dir, "CharacterLevel", None)
    if character_rows:
        values = [str(row.get("PVP_WINREDUCE") or "") for row in character_rows]
        nonzero_values = [value for value in values if value not in {"", "0", "0.0"}]
        samples = _dedupe_preserve(nonzero_values[:8] or values[:8])
        rows.append(
            {
                "category": "character_level_pvp_winreduce_value_summary",
                "surface": "lua_config",
                "field": "PVP_WINREDUCE",
                "source": "CharacterLevel",
                "file": _path_display(character_path, root),
                "line": "",
                "function": "",
                "value": ",".join(samples),
                "row_count": len(values),
                "nonzero_count": len(nonzero_values),
                "snippet": f"PVP_WINREDUCE rows={len(values)}, nonzero={len(nonzero_values)}, samples={','.join(samples)}",
                "note": "CharacterLevel declares the column; current values are summarized here before consumer checks.",
            }
        )

    for path in _digitdoor_pvp_winreduce_files(root, logic_dir, config_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            categories: list[tuple[str, str, str, str]] = []
            if "PVP_WINREDUCE" in line:
                categories.append(
                    (
                        "direct_pvp_winreduce_symbol",
                        "PVP_WINREDUCE",
                        "Exact `PVP_WINREDUCE` symbol occurrence in visible Lua/config.",
                        "lua_config" if path.name == "CharacterLevel.lua" else "lua_logic",
                    )
                )
            if "cfg.PVP_WINREDUCE" in line:
                categories.append(
                    (
                        "direct_pvp_winreduce_cfg_consumer",
                        "cfg.PVP_WINREDUCE",
                        "Direct runtime read of CharacterLevel.PVP_WINREDUCE.",
                        "lua_logic",
                    )
                )
            if "FAKEPVP_REGULATION_WINNER" in line or "pvpWinnerReduceRate" in line:
                categories.append(
                    (
                        "dynamic_winner_reduce_config_source",
                        "FAKEPVP_REGULATION_WINNER|pvpWinnerReduceRate",
                        "Visible DigitDoor winner-reduce logic is sourced from ConfigValue, not CharacterLevel.PVP_WINREDUCE.",
                        "lua_logic",
                    )
                )
            if "SetPVPWinnerReduceDamage" in line or "GetPVPWinnerReduceDamage" in line or "pvpWinnerReduceDamage" in line:
                categories.append(
                    (
                        "runtime_winner_reduce_slot",
                        "PVPWinnerReduceDamage",
                        "Runtime winner-reduce slot used by protection/damage formula.",
                        "lua_logic",
                    )
                )
            for category, field, note, surface in categories:
                rows.append(
                    {
                        "category": category,
                        "surface": surface,
                        "field": field,
                        "source": path.stem,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "value": "",
                        "row_count": "",
                        "nonzero_count": "",
                        "snippet": stripped,
                        "note": note,
                    }
                )
    return rows


def _digitdoor_pvp_winreduce_cpp2il_rows(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    hit_rows: list[dict[str, Any]] = []
    surface_rows: list[dict[str, Any]] = []
    terms = {term.lower(): (term, category) for term, category in _DIGITDOOR_PVP_WINREDUCE_CPP2IL_TERMS.items()}
    for surface in _digitdoor_startgame_cpp2il_surfaces(root):
        files = _iter_digitdoor_startgame_cpp2il_files(surface)
        hit_count = 0
        for file_path in files:
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lower_text = text.lower()
            if not any(term_lower in lower_text for term_lower in terms):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                lower_line = line.lower()
                matched = [(term, category) for term_lower, (term, category) in terms.items() if term_lower in lower_line]
                if not matched:
                    continue
                hit_count += len(matched)
                for term, category in matched:
                    hit_rows.append(
                        {
                            "category": category,
                            "surface": surface["surface"],
                            "field": term,
                            "source": "Cpp2IL",
                            "file": _path_display(file_path, root),
                            "line": line_no,
                            "function": "",
                            "value": "",
                            "row_count": "",
                            "nonzero_count": "",
                            "snippet": _WHITESPACE_RE.sub(" ", line.strip()),
                            "note": "Native-readable Cpp2IL/metadata hit; line context must be reviewed before assigning runtime ownership.",
                        }
                    )
        surface_rows.append(
            {
                "surface": surface["surface"],
                "path": str(surface["path"]),
                "file_count": len(files),
                "hit_count": hit_count,
                "terms": ",".join(_DIGITDOOR_PVP_WINREDUCE_CPP2IL_TERMS),
            }
        )
    return hit_rows, surface_rows


def _write_digitdoor_pvp_winreduce_gap_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    config_dir: Path,
    evidence_rows: list[dict[str, Any]],
    surface_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor PVP_WINREDUCE gap probe",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Config dir: `{config_dir}`",
        f"- Evidence rows: {len(evidence_rows)}",
        f"- Cpp2IL surfaces: {len(surface_rows)}",
        "- Scope: static Lua/config plus already-exported Cpp2IL-readable surface check for the `CharacterLevel.PVP_WINREDUCE` column. It does not modify APKs, memory, traffic, or infer server-side acceptance.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    summary_rows = [row for row in evidence_rows if row.get("category") == "character_level_pvp_winreduce_value_summary"]
    lines.extend(["", "## CharacterLevel Summary", "", "| Field | Rows | Nonzero | Samples |", "| --- | ---: | ---: | --- |"])
    for row in summary_rows:
        lines.append(
            "| "
            f"{_md_table_cell(row.get('field', ''))} | "
            f"{row.get('row_count', '')} | "
            f"{row.get('nonzero_count', '')} | "
            f"{_md_table_cell(row.get('value', ''))} |"
        )
    lines.extend(["", "## Cpp2IL Surface Check", "", "| Surface | Files | Hits |", "| --- | ---: | ---: |"])
    for row in surface_rows:
        lines.append(f"| {_md_table_cell(row.get('surface', ''))} | {row.get('file_count', '')} | {row.get('hit_count', '')} |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `CharacterLevel.PVP_WINREDUCE` is declared in the current DigitDoor config schema, but the true resource rows have no nonzero values for that column.",
            "- The visible DigitDoor Lua runtime does not read `cfg.PVP_WINREDUCE`; `DigitDoorEntityData:InitData` copies only `cfg.PVPATTACK`, `cfg.PVPINCREASE`, and `cfg.PVPREDUCE` into PVP-specific runtime slots.",
            "- The winner-reduction slot used by the formula is `PVPWinnerReduceDamage`, but visible code drives it through `FAKEPVP_REGULATION_WINNER` and `UpdatePVPWinnerProtection`, not through the CharacterLevel column.",
            "- The already-exported Cpp2IL-readable surfaces have no matching PVP win-reduce symbol hits in this probe. That does not prove the server has no concept of it; it only closes the current local readable surfaces.",
            "- Practical next step: treat `PVP_WINREDUCE` as a declared-but-currently-inactive/legacy column until a future resource update gives it nonzero values or a focused runtime/native sample shows a consumer.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_pvp_winreduce_gap_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    digitdoor_config_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    lua_rows = _digitdoor_pvp_winreduce_rows(root, logic_dir, config_dir)
    cpp2il_rows, surface_rows = _digitdoor_pvp_winreduce_cpp2il_rows(root)
    evidence_rows = lua_rows + cpp2il_rows
    category_counts = Counter(str(row.get("category") or "") for row in evidence_rows)
    summary = next(
        (row for row in lua_rows if row.get("category") == "character_level_pvp_winreduce_value_summary"),
        {},
    )
    stats = {
        "source_file_count": len(_digitdoor_pvp_winreduce_files(root, logic_dir, config_dir)),
        "evidence_row_count": len(evidence_rows),
        "character_level_row_count": int(summary.get("row_count") or 0),
        "pvp_winreduce_nonzero_row_count": int(summary.get("nonzero_count") or 0),
        "direct_pvp_winreduce_symbol_rows": category_counts.get("direct_pvp_winreduce_symbol", 0),
        "direct_pvp_winreduce_cfg_consumer_rows": category_counts.get("direct_pvp_winreduce_cfg_consumer", 0),
        "dynamic_winner_reduce_config_rows": category_counts.get("dynamic_winner_reduce_config_source", 0),
        "runtime_winner_reduce_slot_rows": category_counts.get("runtime_winner_reduce_slot", 0),
        "cpp2il_surface_count": len(surface_rows),
        "cpp2il_scanned_file_count": sum(int(row.get("file_count") or 0) for row in surface_rows),
        "cpp2il_hit_count": len(cpp2il_rows),
    }
    verdict = {
        "pvp_winreduce_column_declared": stats["direct_pvp_winreduce_symbol_rows"] > 0
        and stats["character_level_row_count"] > 0,
        "pvp_winreduce_values_all_zero": stats["character_level_row_count"] > 0
        and stats["pvp_winreduce_nonzero_row_count"] == 0,
        "no_visible_lua_direct_cfg_consumer": stats["direct_pvp_winreduce_cfg_consumer_rows"] == 0,
        "winner_reduce_runtime_slot_uses_dynamic_config": stats["runtime_winner_reduce_slot_rows"] > 0
        and stats["dynamic_winner_reduce_config_rows"] > 0,
        "no_cpp2il_readable_symbol_hit": stats["cpp2il_hit_count"] == 0,
    }
    verdict["treat_pvp_winreduce_as_currently_inactive_column"] = (
        verdict["pvp_winreduce_column_declared"]
        and verdict["pvp_winreduce_values_all_zero"]
        and verdict["no_visible_lua_direct_cfg_consumer"]
        and verdict["winner_reduce_runtime_slot_uses_dynamic_config"]
        and verdict["no_cpp2il_readable_symbol_hit"]
    )
    _write_tsv(
        out_dir / "pvp_winreduce_gap_evidence.tsv",
        evidence_rows,
        ["category", "surface", "field", "source", "file", "line", "function", "value", "row_count", "nonzero_count", "snippet", "note"],
    )
    _write_tsv(
        out_dir / "pvp_winreduce_gap_surfaces.tsv",
        surface_rows,
        ["surface", "path", "file_count", "hit_count", "terms"],
    )
    report_path = out_dir / "pvp_winreduce_gap_report.md"
    _write_digitdoor_pvp_winreduce_gap_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        config_dir=config_dir,
        evidence_rows=evidence_rows,
        surface_rows=surface_rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["treat_pvp_winreduce_as_currently_inactive_column"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "evidence": str(out_dir / "pvp_winreduce_gap_evidence.tsv"),
            "surfaces": str(out_dir / "pvp_winreduce_gap_surfaces.tsv"),
        },
    }


_DIGITDOOR_PVP_REPORT_ATTR_NOTES: dict[str, str] = {
    "HP": "Current HP from EntityData:GetCurrentHp().",
    "MAXHP": "Max HP from EntityData:GetMaxHp().",
    "ATTACK": "Base attack from EntityData:GetAttack().",
    "PVPATTACK": "PVP attack from EntityData:GetPVPAttack(); PVP damage uses this for partners.",
    "ATKSPEED": "Attack speed from EntityData:GetAttackSpeed().",
    "CRITICAL": "Critical rate from EntityData:GetCritical().",
    "ANTICRITICAL": "Anti-critical rate from EntityData:GetAntiCritical().",
    "INCREASEDAMAGE": "General increase-damage slot from EntityData:GetIncreaseDamage().",
    "REDUCEDAMAGE": "General reduce-damage slot from EntityData:GetReduceDamage().",
    "PVPINCREASE": "PVP increase slot from EntityData:GetPVPIncreaseDamage().",
    "PVPREDUCE": "PVP reduce slot from EntityData:GetPVPReduceDamage().",
    "ADDDAMAGE": "Additional damage slot from EntityData:GetAddDamage().",
}


def _digitdoor_pvp_report_attr_files(root: Path, logic_dir: Path) -> list[Path]:
    candidates = [logic_dir / "DigitDoorPVPSceneView.lua"]
    patterns = [
        "by_source/lscripts/gamesystem/game/message_*/text_assets/DigitDoorAttrVO.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/DigitDoorSimpleVO.lua",
    ]
    for pattern in patterns:
        candidates.extend(root.glob(pattern))
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _digitdoor_pvp_report_attr_rows(root: Path, logic_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    attr_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for path in _digitdoor_pvp_report_attr_files(root, logic_dir):
        current_function = ""
        getter_by_var: dict[str, str] = {}
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            name = path.name

            if name == "DigitDoorPVPSceneView.lua" and current_function == "_M.GetAttr":
                if match := re.search(r"local\s+([A-Za-z_]\w*)\s*=\s*EntityData:(Get[A-Za-z_]\w*)\(\)", line):
                    local_var, getter = match.groups()
                    getter_by_var[local_var] = getter
                    evidence_rows.append(
                        {
                            "category": "getattr_local_getter",
                            "attr_code": "",
                            "local_var": local_var,
                            "getter": getter,
                            "source": "DigitDoorPVPSceneView",
                            "file": _path_display(path, root),
                            "line": line_no,
                            "function": current_function,
                            "wire_field": "",
                            "snippet": stripped,
                            "note": "Local value source used before GetAttrCode emits report attrVOList.",
                        }
                    )
                if match := re.search(r'GetAttrCode\("([^"]+)"\s*,\s*([A-Za-z_]\w*)\s*,\s*clist\)', line):
                    attr_code, local_var = match.groups()
                    attr_rows.append(
                        {
                            "order": len(attr_rows) + 1,
                            "attr_code": attr_code,
                            "local_var": local_var,
                            "getter": getter_by_var.get(local_var, ""),
                            "role": "pvp_specific" if attr_code.startswith("PVP") else "general_snapshot",
                            "note": _DIGITDOOR_PVP_REPORT_ATTR_NOTES.get(attr_code, ""),
                            "file": _path_display(path, root),
                            "line": line_no,
                        }
                    )
                    evidence_rows.append(
                        {
                            "category": "report_attr_code_emitted",
                            "attr_code": attr_code,
                            "local_var": local_var,
                            "getter": getter_by_var.get(local_var, ""),
                            "source": "DigitDoorPVPSceneView",
                            "file": _path_display(path, root),
                            "line": line_no,
                            "function": current_function,
                            "wire_field": "attrVOList",
                            "snippet": stripped,
                            "note": "GetAttr emits this code/value pair into attrVOList.",
                        }
                    )

            if name == "DigitDoorPVPSceneView.lua" and current_function == "_M.GetAttrCode":
                if "DigitDoorAttrVO.new()" in line:
                    evidence_rows.append(
                        {
                            "category": "attrvo_created",
                            "attr_code": "",
                            "local_var": "",
                            "getter": "",
                            "source": "DigitDoorPVPSceneView",
                            "file": _path_display(path, root),
                            "line": line_no,
                            "function": current_function,
                            "wire_field": "DigitDoorAttrVO",
                            "snippet": stripped,
                            "note": "Each report attr entry is a DigitDoorAttrVO.",
                        }
                    )
                for field in ["type", "value"]:
                    if f"attrVo.{field}" in line:
                        evidence_rows.append(
                            {
                                "category": "attrvo_field_assigned",
                                "attr_code": "",
                                "local_var": "",
                                "getter": "",
                                "source": "DigitDoorPVPSceneView",
                                "file": _path_display(path, root),
                                "line": line_no,
                                "function": current_function,
                                "wire_field": field,
                                "snippet": stripped,
                                "note": "GetAttrCode assigns this DigitDoorAttrVO field.",
                            }
                        )
                if "clist:Add(attrVo)" in line:
                    evidence_rows.append(
                        {
                            "category": "attrvo_added_to_list",
                            "attr_code": "",
                            "local_var": "",
                            "getter": "",
                            "source": "DigitDoorPVPSceneView",
                            "file": _path_display(path, root),
                            "line": line_no,
                            "function": current_function,
                            "wire_field": "attrVOList",
                            "snippet": stripped,
                            "note": "GetAttrCode appends the built DigitDoorAttrVO to the list.",
                        }
                    )

            if name == "DigitDoorAttrVO.lua":
                if "self.type" in line:
                    evidence_rows.append(
                        {
                            "category": "digitdoor_attrvo_schema",
                            "attr_code": "",
                            "local_var": "",
                            "getter": "",
                            "source": "DigitDoorAttrVO",
                            "file": _path_display(path, root),
                            "line": line_no,
                            "function": current_function,
                            "wire_field": "type",
                            "snippet": stripped,
                            "note": "DigitDoorAttrVO.type is the attr code string.",
                        }
                    )
                if "self.value" in line:
                    evidence_rows.append(
                        {
                            "category": "digitdoor_attrvo_schema",
                            "attr_code": "",
                            "local_var": "",
                            "getter": "",
                            "source": "DigitDoorAttrVO",
                            "file": _path_display(path, root),
                            "line": line_no,
                            "function": current_function,
                            "wire_field": "value",
                            "snippet": stripped,
                            "note": "DigitDoorAttrVO.value is the numeric attr value.",
                        }
                    )
                if "readString()" in line or "writeString(self.type)" in line:
                    evidence_rows.append(
                        {
                            "category": "digitdoor_attrvo_wire_type_string",
                            "attr_code": "",
                            "local_var": "",
                            "getter": "",
                            "source": "DigitDoorAttrVO",
                            "file": _path_display(path, root),
                            "line": line_no,
                            "function": current_function,
                            "wire_field": "type",
                            "snippet": stripped,
                            "note": "Wire type for attr code is string.",
                        }
                    )
                if "readDouble()" in line or "writeDouble(self.value)" in line:
                    evidence_rows.append(
                        {
                            "category": "digitdoor_attrvo_wire_value_double",
                            "attr_code": "",
                            "local_var": "",
                            "getter": "",
                            "source": "DigitDoorAttrVO",
                            "file": _path_display(path, root),
                            "line": line_no,
                            "function": current_function,
                            "wire_field": "value",
                            "snippet": stripped,
                            "note": "Wire type for attr value is double.",
                        }
                    )
                if "return 91606" in line:
                    evidence_rows.append(
                        {
                            "category": "digitdoor_attrvo_packet_id",
                            "attr_code": "",
                            "local_var": "",
                            "getter": "",
                            "source": "DigitDoorAttrVO",
                            "file": _path_display(path, root),
                            "line": line_no,
                            "function": current_function,
                            "wire_field": "91606",
                            "snippet": stripped,
                            "note": "DigitDoorAttrVO packet/bean id.",
                        }
                    )

            if name == "DigitDoorSimpleVO.lua" and "attrVOList" in line:
                evidence_rows.append(
                    {
                        "category": "simple_vo_attr_list_schema",
                        "attr_code": "",
                        "local_var": "",
                        "getter": "",
                        "source": "DigitDoorSimpleVO",
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "wire_field": "attrVOList",
                        "snippet": stripped,
                        "note": "DigitDoorSimpleVO embeds the attrVOList snapshot.",
                    }
                )
    return attr_rows, evidence_rows


def _write_digitdoor_pvp_report_attr_snapshot_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    attr_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor PVP report attr snapshot",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Attr rows: {len(attr_rows)}",
        f"- Evidence rows: {len(evidence_rows)}",
        "- Scope: static Lua evidence for the `DigitDoorPVPSceneView:GetAttr -> DigitDoorAttrVO -> DigitDoorSimpleVO.attrVOList` report snapshot. It does not assert server trust or replay acceptance.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Attr Order", "", "| Order | Code | Getter | Role | Note |", "| ---: | --- | --- | --- | --- |"])
    for row in attr_rows:
        lines.append(
            "| "
            f"{row.get('order', '')} | "
            f"{_md_table_cell(row.get('attr_code', ''))} | "
            f"{_md_table_cell(row.get('getter', ''))} | "
            f"{_md_table_cell(row.get('role', ''))} | "
            f"{_md_table_cell(row.get('note', ''))} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `DigitDoorPVPSceneView:GetAttr` emits 12 attr codes in a fixed visible order: HP, MAXHP, ATTACK, PVPATTACK, ATKSPEED, CRITICAL, ANTICRITICAL, INCREASEDAMAGE, REDUCEDAMAGE, PVPINCREASE, PVPREDUCE, and ADDDAMAGE.",
            "- Each entry is a `DigitDoorAttrVO(91606)` with `type` as a string and `value` as a double, appended to `DigitDoorSimpleVO.attrVOList`.",
            "- The PVP-specific snapshot fields are `PVPATTACK`, `PVPINCREASE`, and `PVPREDUCE`. `PVP_WINREDUCE` is not emitted in the report snapshot.",
            "- This describes the client-built report payload shape only. Report validation/acceptance remains outside the visible Lua surface because no visible `SM_DigitDoorReport` exists.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_pvp_report_attr_snapshot_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    attr_rows, evidence_rows = _digitdoor_pvp_report_attr_rows(root, logic_dir)
    category_counts = Counter(str(row.get("category") or "") for row in evidence_rows)
    attr_codes = [str(row.get("attr_code") or "") for row in attr_rows]
    stats = {
        "source_file_count": len(_digitdoor_pvp_report_attr_files(root, logic_dir)),
        "attr_row_count": len(attr_rows),
        "evidence_row_count": len(evidence_rows),
        "pvp_specific_attr_count": sum(1 for code in attr_codes if code.startswith("PVP")),
        "getattr_local_getter_rows": category_counts.get("getattr_local_getter", 0),
        "attrvo_schema_rows": category_counts.get("digitdoor_attrvo_schema", 0),
        "attrvo_type_string_rows": category_counts.get("digitdoor_attrvo_wire_type_string", 0),
        "attrvo_value_double_rows": category_counts.get("digitdoor_attrvo_wire_value_double", 0),
        "simple_vo_attr_list_rows": category_counts.get("simple_vo_attr_list_schema", 0),
        "contains_pvp_winreduce_attr": "PVP_WINREDUCE" in attr_codes,
    }
    expected_order = [
        "HP",
        "MAXHP",
        "ATTACK",
        "PVPATTACK",
        "ATKSPEED",
        "CRITICAL",
        "ANTICRITICAL",
        "INCREASEDAMAGE",
        "REDUCEDAMAGE",
        "PVPINCREASE",
        "PVPREDUCE",
        "ADDDAMAGE",
    ]
    verdict = {
        "attr_order_confirmed": attr_codes == expected_order,
        "attr_getters_mapped": stats["getattr_local_getter_rows"] >= len(expected_order),
        "pvp_specific_attrs_present": {"PVPATTACK", "PVPINCREASE", "PVPREDUCE"}.issubset(set(attr_codes)),
        "pvp_winreduce_not_emitted": not stats["contains_pvp_winreduce_attr"],
        "digitdoor_attrvo_wire_shape_confirmed": stats["attrvo_type_string_rows"] >= 2
        and stats["attrvo_value_double_rows"] >= 2,
        "simple_vo_embeds_attr_list": stats["simple_vo_attr_list_rows"] >= 3,
    }
    verdict["pvp_report_attr_snapshot_confirmed"] = (
        verdict["attr_order_confirmed"]
        and verdict["pvp_specific_attrs_present"]
        and verdict["pvp_winreduce_not_emitted"]
        and verdict["digitdoor_attrvo_wire_shape_confirmed"]
        and verdict["simple_vo_embeds_attr_list"]
    )
    _write_tsv(
        out_dir / "pvp_report_attr_snapshot_attrs.tsv",
        attr_rows,
        ["order", "attr_code", "local_var", "getter", "role", "note", "file", "line"],
    )
    _write_tsv(
        out_dir / "pvp_report_attr_snapshot_evidence.tsv",
        evidence_rows,
        ["category", "attr_code", "local_var", "getter", "source", "file", "line", "function", "wire_field", "snippet", "note"],
    )
    report_path = out_dir / "pvp_report_attr_snapshot_report.md"
    _write_digitdoor_pvp_report_attr_snapshot_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        attr_rows=attr_rows,
        evidence_rows=evidence_rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["pvp_report_attr_snapshot_confirmed"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "attrs": str(out_dir / "pvp_report_attr_snapshot_attrs.tsv"),
            "evidence": str(out_dir / "pvp_report_attr_snapshot_evidence.tsv"),
        },
    }


def _digitdoor_pvp_winner_projection_files(root: Path, logic_dir: Path) -> list[Path]:
    candidates = [
        logic_dir / "DigitDoorPVPSceneView.lua",
        logic_dir / "DigitDoorNetLogic.lua",
        logic_dir / "DigitDoorFightComponent.lua",
    ]
    candidates.extend(root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorReport.lua"))
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _digitdoor_pvp_winner_projection_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_pvp_winner_projection_files(root, logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            name = path.name
            categories: list[tuple[str, str, str]] = []
            if name == "DigitDoorPVPSceneView.lua":
                if current_function == "_M.InitDataInfo":
                    if "Scene_DigitDoorPVPIT" in line:
                        categories.append(("pvp_mode_pvpit_branch", "curMapType", "PVPIT branch uses ImmortalDigital finish VO."))
                    if "Scene_DigitDoorPVP" in line:
                        categories.append(("pvp_mode_club_branch", "curMapType", "Club PVP branch uses ClubPkDigital finish VO."))
                    if "self.curFinishVo=ImmortalDigitalMgr" in line:
                        categories.append(("pvpit_finish_vo_source", "curFinishVo", "PVPIT finish VO source."))
                    if "self.curFinishVo=ClubPkDigitalMgr" in line:
                        categories.append(("club_finish_vo_source", "curFinishVo", "Club PVP finish VO source."))
                    if "self.winnerId=self.curFinishVo.winnerId" in line:
                        categories.append(("pvpit_winner_from_finish_vo", "winnerId", "PVPIT winner id is copied directly from curFinishVo.winnerId."))
                    if "self.curFinishVo.victory" in line:
                        categories.append(("club_winner_victory_branch", "victory", "Club PVP winner derives from curFinishVo.victory."))
                    if "self.winnerId=self.curFinishVo.attacker.id" in line:
                        categories.append(("club_winner_attacker_when_victory", "winnerId", "Club PVP victory=true selects attacker id."))
                    if "self.winnerId=self.curFinishVo.defender.id" in line:
                        categories.append(("club_winner_defender_when_not_victory", "winnerId", "Club PVP victory=false selects defender id."))
                    if "self.otherId=" in line:
                        categories.append(("other_id_projection", "otherId", "Other participant id is projected from finish VO."))
                if current_function == "_M.UpdateHp":
                    if "GetDefenseHPMsg()" in line:
                        categories.append(("updatehp_defense_hp_source", "defenseHp", "UpdateHp watches defense-side HP."))
                    if "GetAttackHPMsg()" in line:
                        categories.append(("updatehp_attack_hp_source", "attackHp", "UpdateHp watches attack-side HP."))
                    if "curHp==0" in line:
                        categories.append(("updatehp_zero_hp_gate", "curHp", "CheckList can be triggered after one side reaches zero HP."))
                    if "not self.winnerId:Equal(userVID)" in line:
                        categories.append(("defense_dead_requires_user_not_winner", "winnerId", "Defense-side death path reports only when winnerId is not the user id."))
                    if "self.winnerId:Equal(userVID)" in line:
                        categories.append(("attack_dead_requires_user_winner", "winnerId", "Attack-side death path reports only when winnerId equals the user id."))
                    if "self:CheckList(fightComponent)" in line:
                        categories.append(("updatehp_triggers_checklist", "CheckList", "UpdateHp calls CheckList when the visible winner/death guard matches."))
                if current_function == "_M.CheckList":
                    if "clientWinnerId=self.winnerId:Equal(userId)and self.otherId or userId" in line:
                        categories.append(("client_winner_id_inverts_visible_winner", "clientWinnerId", "Despite the name, this expression selects the non-winner in the two-player local view."))
                    if "serverWinnerId=self.winnerId" in line:
                        categories.append(("server_winner_id_uses_finish_winner", "serverWinnerId", "serverWinnerId is the finish-VO-derived winnerId."))
                    if "CM_DigitDoorReportFun(" in line:
                        categories.append(("checklist_sends_report", "CM_DigitDoorReport", "CheckList sends the built report fields."))
                    if "atkVoList=self.attackList" in line:
                        categories.append(("checklist_attack_list_snapshot", "atkVoList", "Report uses accumulated attackList snapshot."))
                    if "defVoList=self.defenseList" in line:
                        categories.append(("checklist_defense_list_snapshot", "defVoList", "Report uses accumulated defenseList snapshot."))
            elif name == "DigitDoorNetLogic.lua" and current_function == "_M.CM_DigitDoorReportFun":
                for field in ["clientWinnerId", "serverWinnerId"]:
                    if f"CM_DigitDoorReport.{field}={field}" in line:
                        categories.append(("netlogic_report_winner_field_assignment", field, "NetLogic assigns this winner field into CM_DigitDoorReport."))
                if "F_SendMsg(CM_DigitDoorReport)" in line:
                    categories.append(("netlogic_sends_report", "CM_DigitDoorReport", "NetLogic sends CM_DigitDoorReport."))
            elif name == "CM_DigitDoorReport.lua":
                for field in ["clientWinnerId", "serverWinnerId"]:
                    if f"self.{field}" in line:
                        categories.append(("report_schema_winner_field", field, "CM_DigitDoorReport declares/serializes this winner field."))
                    if f"self.{field}=self:readLong()" in line or f"writeLong(self.{field})" in line:
                        categories.append(("report_wire_winner_long", field, "Winner field wire type is long."))
            elif name == "DigitDoorFightComponent.lua":
                if "self.winnerId=vo.winnerId" in line:
                    categories.append(("fight_component_winner_from_scene_info", "winnerId", "FightComponent also receives winnerId from scene info for PVP balancing."))
                if "return self.winnerId" in line:
                    categories.append(("fight_component_winner_getter", "winnerId", "FightComponent exposes winnerId through getter."))
            for category, field, note in categories:
                rows.append(
                    {
                        "category": category,
                        "field": field,
                        "source": path.stem,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": stripped,
                        "note": note,
                    }
                )
    return rows


def _write_digitdoor_pvp_winner_projection_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor PVP winner projection",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Evidence rows: {len(rows)}",
        "- Scope: static Lua evidence for how `winnerId`, `clientWinnerId`, and `serverWinnerId` are projected into `CM_DigitDoorReport`. It does not assert server acceptance or replay truth.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Key Evidence",
            "",
            "| Category | Field | File | Line | Snippet |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in rows:
        if row.get("category") in {
            "pvpit_winner_from_finish_vo",
            "club_winner_attacker_when_victory",
            "club_winner_defender_when_not_victory",
            "client_winner_id_inverts_visible_winner",
            "server_winner_id_uses_finish_winner",
            "report_wire_winner_long",
        }:
            lines.append(
                "| "
                f"{_md_table_cell(row.get('category', ''))} | "
                f"{_md_table_cell(row.get('field', ''))} | "
                f"{_md_table_cell(row.get('file', ''))} | "
                f"{row.get('line', '')} | "
                f"{_md_table_cell(row.get('snippet', ''))} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- PVPIT mode copies `winnerId` directly from `ImmortalDigital` finish VO; Club PVP derives winner from `curFinishVo.victory`, selecting attacker id when true and defender id when false.",
            "- `serverWinnerId` in the report is the finish-VO-derived `self.winnerId`.",
            "- `clientWinnerId` is misleadingly named in visible Lua: `self.winnerId:Equal(userId) and self.otherId or userId` selects the opposite participant from the visible winner in the two-player local view.",
            "- `UpdateHp` gates `CheckList` on zero HP plus a winner/user-id condition; it does not compute the winner from local damage totals.",
            "- The report packet serializes both winner fields as long values. No visible `SM_DigitDoorReport` closes server-side acceptance.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_pvp_winner_projection_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _digitdoor_pvp_winner_projection_rows(root, logic_dir)
    category_counts = Counter(str(row.get("category") or "") for row in rows)
    stats = {
        "source_file_count": len(_digitdoor_pvp_winner_projection_files(root, logic_dir)),
        "evidence_row_count": len(rows),
        "finish_vo_winner_source_rows": category_counts.get("pvpit_winner_from_finish_vo", 0)
        + category_counts.get("club_winner_attacker_when_victory", 0)
        + category_counts.get("club_winner_defender_when_not_victory", 0),
        "other_id_projection_rows": category_counts.get("other_id_projection", 0),
        "updatehp_checklist_trigger_rows": category_counts.get("updatehp_triggers_checklist", 0),
        "client_winner_projection_rows": category_counts.get("client_winner_id_inverts_visible_winner", 0),
        "server_winner_projection_rows": category_counts.get("server_winner_id_uses_finish_winner", 0),
        "netlogic_winner_assignment_rows": category_counts.get("netlogic_report_winner_field_assignment", 0),
        "report_schema_winner_rows": category_counts.get("report_schema_winner_field", 0),
        "report_wire_winner_long_rows": category_counts.get("report_wire_winner_long", 0),
    }
    verdict = {
        "winner_source_from_finish_vo_visible": stats["finish_vo_winner_source_rows"] >= 3,
        "checklist_trigger_guard_visible": stats["updatehp_checklist_trigger_rows"] >= 2,
        "client_winner_id_inverts_visible_winner": stats["client_winner_projection_rows"] > 0,
        "server_winner_id_uses_finish_winner": stats["server_winner_projection_rows"] > 0,
        "report_winner_fields_assigned_and_serialized": stats["netlogic_winner_assignment_rows"] >= 2
        and stats["report_schema_winner_rows"] >= 2
        and stats["report_wire_winner_long_rows"] >= 2,
    }
    verdict["pvp_winner_projection_confirmed"] = all(verdict.values())
    _write_tsv(
        out_dir / "pvp_winner_projection_evidence.tsv",
        rows,
        ["category", "field", "source", "file", "line", "function", "snippet", "note"],
    )
    report_path = out_dir / "pvp_winner_projection_report.md"
    _write_digitdoor_pvp_winner_projection_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        rows=rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["pvp_winner_projection_confirmed"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "evidence": str(out_dir / "pvp_winner_projection_evidence.tsv"),
        },
    }


def _digitdoor_pvp_report_list_lifecycle_files(root: Path, logic_dir: Path) -> list[Path]:
    candidates = [
        logic_dir / "DigitDoorPVPSceneView.lua",
        logic_dir / "DigitDoorNetLogic.lua",
    ]
    candidates.extend(root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorReport.lua"))
    candidates.extend(root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/DigitDoorSimpleVO.lua"))
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _digitdoor_pvp_report_list_lifecycle_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_pvp_report_list_lifecycle_files(root, logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            compact = _WHITESPACE_RE.sub("", line)
            name = path.name
            categories: list[tuple[str, str, str]] = []
            if name == "DigitDoorPVPSceneView.lua":
                if current_function == "_M._init_":
                    if "self.tbAttack={}" in compact:
                        categories.append(("dedupe_map_initialized", "tbAttack", "Attack-side role-id dedupe map is initialized."))
                    if "self.tbDefense={}" in compact:
                        categories.append(("dedupe_map_initialized", "tbDefense", "Defense-side role-id dedupe map is initialized."))
                    if "self.attackList=CList.new()" in compact:
                        categories.append(("snapshot_list_initialized", "attackList", "Attack snapshot list is initialized."))
                    if "self.defenseList=CList.new()" in compact:
                        categories.append(("snapshot_list_initialized", "defenseList", "Defense snapshot list is initialized."))
                if current_function == "_M.AddEvent":
                    if "self.entityDead=function" in compact:
                        categories.append(("dead_event_handler_defined", "entityDead", "Shared entity-death handler is defined."))
                    if "self:SaveEntityData(entityView)" in compact:
                        categories.append(("dead_event_saves_entity_data", "SaveEntityData", "Death handler snapshots the dead entity."))
                    if "self:UpdateHp()" in compact:
                        categories.append(("dead_event_updates_hp", "UpdateHp", "Death handler re-checks HP/report conditions."))
                    if "ENTITY_ENTER_DEAD" in line or "AFTER_ENTITY_DEAD_ANIM" in line:
                        categories.append(("dead_event_registered", "entityDead", "Entity death event feeds the snapshot path."))
                if current_function == "_M.SaveEntityData":
                    if "V_EntityType==LuaEntityType.DigitDoorPartner" in compact:
                        categories.append(("save_entity_partner_guard", "DigitDoorPartner", "Only DigitDoor partner entities are snapshot candidates."))
                    if "campGroup==DigitDoorType.CampGroup.Attack" in compact:
                        categories.append(("save_entity_attack_branch", "attackList", "Attack-side dead entity branch."))
                    if "notself.tbAttack[entityView.Entity.V_RoleId]" in compact:
                        categories.append(("save_entity_dedupe_guard", "tbAttack", "Attack-side snapshot is deduped by role id."))
                    if "notself.tbDefense[entityView.Entity.V_RoleId]" in compact:
                        categories.append(("save_entity_dedupe_guard", "tbDefense", "Defense-side snapshot is deduped by role id."))
                    if "self.attackList:Add(data)" in compact:
                        categories.append(("save_entity_adds_snapshot", "attackList", "Dead attack-side entity snapshot is appended."))
                    if "self.defenseList:Add(data)" in compact:
                        categories.append(("save_entity_adds_snapshot", "defenseList", "Dead defense-side entity snapshot is appended."))
                    if "self:CreateEntityData(entityView)" in compact:
                        categories.append(("save_entity_creates_simple_vo", "DigitDoorSimpleVO", "Dead entity is projected through CreateEntityData."))
                if current_function == "_M.CheckList":
                    if "ifnotself.curFinishVothen" in compact:
                        categories.append(("checklist_requires_finish_vo", "curFinishVo", "Report build exits when finish VO is absent."))
                    if "fightComponent:GetDefenseViewList()" in compact:
                        categories.append(("checklist_view_source", "defenseList", "CheckList reads remaining defense-side live views."))
                    if "fightComponent:GetAttackViewList()" in compact:
                        categories.append(("checklist_view_source", "attackList", "CheckList reads remaining attack-side live views."))
                    if "notself.tbDefense[entityView.Entity.V_RoleId]" in compact:
                        categories.append(("checklist_dedupe_guard", "tbDefense", "CheckList backfill is deduped for defense role ids."))
                    if "notself.tbAttack[entityView.Entity.V_RoleId]" in compact:
                        categories.append(("checklist_dedupe_guard", "tbAttack", "CheckList backfill is deduped for attack role ids."))
                    if "self.defenseList:Add(data)" in compact:
                        categories.append(("checklist_backfills_snapshot", "defenseList", "CheckList appends remaining defense-side snapshot."))
                    if "self.attackList:Add(data)" in compact:
                        categories.append(("checklist_backfills_snapshot", "attackList", "CheckList appends remaining attack-side snapshot."))
                    if "atkVoList=self.attackList" in compact:
                        categories.append(("checklist_assigns_request_list", "atkVoList", "Report request uses accumulated attackList."))
                    if "defVoList=self.defenseList" in compact:
                        categories.append(("checklist_assigns_request_list", "defVoList", "Report request uses accumulated defenseList."))
                    if "CM_DigitDoorReportFun(" in line:
                        categories.append(("checklist_sends_report", "CM_DigitDoorReport", "CheckList sends the completed report request."))
                if current_function == "_M.CreateEntityData":
                    for field in ["ownerId", "resourceId", "index", "lv"]:
                        if f"data.{field}" in line:
                            categories.append(("simple_vo_field_projected", field, "CreateEntityData fills this DigitDoorSimpleVO field."))
                    if "self:GetAttr(entityView.Entity.EntityData,data.attrVOList)" in compact:
                        categories.append(("simple_vo_attr_list_filled", "attrVOList", "CreateEntityData fills attrVOList via GetAttr."))
                    if "DigitDoorSimpleVO.new()" in compact:
                        categories.append(("simple_vo_created", "DigitDoorSimpleVO", "CreateEntityData instantiates DigitDoorSimpleVO."))
                if current_function == "_M.Destroy":
                    if "self.attackList=CList:Recyle(self.attackList)" in compact:
                        categories.append(("snapshot_list_recycled", "attackList", "Attack snapshot list is recycled on destroy."))
                    if "self.defenseList=CList:Recyle(self.defenseList)" in compact:
                        categories.append(("snapshot_list_recycled", "defenseList", "Defense snapshot list is recycled on destroy."))
            elif name == "DigitDoorNetLogic.lua" and current_function == "_M.CM_DigitDoorReportFun":
                for field in ["atkVoList", "defVoList"]:
                    if f"CM_DigitDoorReport.{field}={field}" in compact:
                        categories.append(("netlogic_assigns_request_list", field, "NetLogic assigns this list into CM_DigitDoorReport."))
                if "F_SendMsg(CM_DigitDoorReport)" in line:
                    categories.append(("netlogic_sends_report", "CM_DigitDoorReport", "NetLogic sends CM_DigitDoorReport."))
            elif name == "CM_DigitDoorReport.lua":
                for field in ["atkVoList", "defVoList"]:
                    if f"self.{field}=CList.new()" in compact:
                        categories.append(("request_list_schema_declared", field, "CM_DigitDoorReport declares this list field."))
                    if f"readMessageList2List(self.{field})" in compact:
                        categories.append(("request_list_read_wire", field, "CM_DigitDoorReport reads this list as a message list."))
                    if f"writeList(self.{field})" in compact:
                        categories.append(("request_list_write_wire", field, "CM_DigitDoorReport writes this list."))
            elif name == "DigitDoorSimpleVO.lua":
                for field in ["ownerId", "resourceId", "index", "lv", "attrVOList"]:
                    if f"self.{field}" in line:
                        categories.append(("simple_vo_schema_field", field, "DigitDoorSimpleVO exposes this snapshot field."))
                if "readLong()" in line or "writeLong(" in line:
                    categories.append(("simple_vo_long_wire", "ownerId", "DigitDoorSimpleVO owner id is long-shaped on wire."))
                if "readInt()" in line or "writeInt(" in line:
                    categories.append(("simple_vo_int_wire", "resourceId/index/lv", "DigitDoorSimpleVO numeric fields are int-shaped on wire."))
                if "readMessageList2List(self.attrVOList)" in compact or "writeList(self.attrVOList)" in compact:
                    categories.append(("simple_vo_attr_list_wire", "attrVOList", "DigitDoorSimpleVO attrVOList is a nested message list."))
            for category, field, note in categories:
                rows.append(
                    {
                        "category": category,
                        "field": field,
                        "source": path.stem,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": stripped,
                        "note": note,
                    }
                )
    return rows


def _write_digitdoor_pvp_report_list_lifecycle_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor PVP report list lifecycle",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Evidence rows: {len(rows)}",
        "- Scope: static Lua evidence for how `attackList`/`defenseList` snapshots are collected into `CM_DigitDoorReport`. It does not assert server acceptance or replay truth.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Lifecycle Evidence",
            "",
            "| Category | Field | File | Line | Snippet |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    priority_categories = {
        "snapshot_list_initialized",
        "dead_event_registered",
        "save_entity_dedupe_guard",
        "save_entity_adds_snapshot",
        "checklist_view_source",
        "checklist_backfills_snapshot",
        "checklist_assigns_request_list",
        "simple_vo_field_projected",
        "simple_vo_attr_list_filled",
        "request_list_read_wire",
        "request_list_write_wire",
        "snapshot_list_recycled",
    }
    for row in rows:
        if row.get("category") in priority_categories:
            lines.append(
                "| "
                f"{_md_table_cell(row.get('category', ''))} | "
                f"{_md_table_cell(row.get('field', ''))} | "
                f"{_md_table_cell(row.get('file', ''))} | "
                f"{row.get('line', '')} | "
                f"{_md_table_cell(row.get('snippet', ''))} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `DigitDoorPVPSceneView` initializes `tbAttack/tbDefense` dedupe maps and `attackList/defenseList` snapshot lists when the PVP scene view is created.",
            "- Entity-death events first call `SaveEntityData`, which snapshots dead DigitDoor partner entities once per `V_RoleId` into the side-specific list.",
            "- When `UpdateHp` decides the report should be built, `CheckList` backfills any still-live attack/defense views that were not already captured by the death-event path.",
            "- Each entry is created by `CreateEntityData` as `DigitDoorSimpleVO(ownerId, resourceId, index, lv, attrVOList)`, with `attrVOList` filled by `GetAttr`.",
            "- `CM_DigitDoorReport` carries `atkVoList` and `defVoList` as message lists; `DigitDoorNetLogic` assigns the accumulated lists and sends the request.",
            "- The local lists are recycled in `Destroy`; there is no visible dedicated `SM_DigitDoorReport` in this static surface.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_pvp_report_list_lifecycle_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _digitdoor_pvp_report_list_lifecycle_rows(root, logic_dir)
    category_counts = Counter(str(row.get("category") or "") for row in rows)
    simple_vo_fields = {
        str(row.get("field"))
        for row in rows
        if row.get("category") == "simple_vo_field_projected"
    }
    stats = {
        "source_file_count": len(_digitdoor_pvp_report_list_lifecycle_files(root, logic_dir)),
        "evidence_row_count": len(rows),
        "dedupe_map_initialized_rows": category_counts.get("dedupe_map_initialized", 0),
        "snapshot_list_initialized_rows": category_counts.get("snapshot_list_initialized", 0),
        "dead_event_registered_rows": category_counts.get("dead_event_registered", 0),
        "dead_event_saves_entity_data_rows": category_counts.get("dead_event_saves_entity_data", 0),
        "save_entity_dedupe_guard_rows": category_counts.get("save_entity_dedupe_guard", 0),
        "save_entity_adds_snapshot_rows": category_counts.get("save_entity_adds_snapshot", 0),
        "checklist_view_source_rows": category_counts.get("checklist_view_source", 0),
        "checklist_backfills_snapshot_rows": category_counts.get("checklist_backfills_snapshot", 0),
        "checklist_assigns_request_list_rows": category_counts.get("checklist_assigns_request_list", 0),
        "simple_vo_projected_field_count": len(simple_vo_fields),
        "simple_vo_attr_list_filled_rows": category_counts.get("simple_vo_attr_list_filled", 0),
        "netlogic_assigns_request_list_rows": category_counts.get("netlogic_assigns_request_list", 0),
        "request_list_read_wire_rows": category_counts.get("request_list_read_wire", 0),
        "request_list_write_wire_rows": category_counts.get("request_list_write_wire", 0),
        "snapshot_list_recycled_rows": category_counts.get("snapshot_list_recycled", 0),
    }
    verdict = {
        "lists_initialized_with_dedupe_maps": stats["dedupe_map_initialized_rows"] >= 2
        and stats["snapshot_list_initialized_rows"] >= 2,
        "dead_events_feed_snapshot_path": stats["dead_event_registered_rows"] >= 2
        and stats["dead_event_saves_entity_data_rows"] > 0,
        "save_entity_data_dedupes_dead_partner_snapshots": stats["save_entity_dedupe_guard_rows"] >= 2
        and stats["save_entity_adds_snapshot_rows"] >= 2,
        "checklist_backfills_live_views_before_report": stats["checklist_view_source_rows"] >= 2
        and stats["checklist_backfills_snapshot_rows"] >= 2,
        "simple_vo_projection_shape_visible": stats["simple_vo_projected_field_count"] >= 4
        and stats["simple_vo_attr_list_filled_rows"] > 0,
        "request_list_wire_shape_confirmed": stats["netlogic_assigns_request_list_rows"] >= 2
        and stats["request_list_read_wire_rows"] >= 2
        and stats["request_list_write_wire_rows"] >= 2,
        "lists_recycled_on_destroy": stats["snapshot_list_recycled_rows"] >= 2,
    }
    verdict["pvp_report_list_lifecycle_confirmed"] = all(verdict.values())
    _write_tsv(
        out_dir / "pvp_report_list_lifecycle_evidence.tsv",
        rows,
        ["category", "field", "source", "file", "line", "function", "snippet", "note"],
    )
    report_path = out_dir / "pvp_report_list_lifecycle_report.md"
    _write_digitdoor_pvp_report_list_lifecycle_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        rows=rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["pvp_report_list_lifecycle_confirmed"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "evidence": str(out_dir / "pvp_report_list_lifecycle_evidence.tsv"),
        },
    }


def _digitdoor_pvp_report_acceptance_gap_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add_row(
        category: str,
        field: str,
        source: str,
        path: Path | None,
        line: int | str,
        snippet: str,
        note: str,
    ) -> None:
        rows.append(
            {
                "category": category,
                "field": field,
                "source": source,
                "file": _path_display(path, root) if path else "",
                "line": line,
                "snippet": _WHITESPACE_RE.sub(" ", snippet.strip()),
                "note": note,
            }
        )

    cm_files = sorted(
        root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorReport*.lua"),
        key=lambda item: str(item).lower(),
    )
    sm_files = sorted(
        root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorReport*.lua"),
        key=lambda item: str(item).lower(),
    )
    for path in cm_files:
        add_row("lua_request_packet_file", "CM_DigitDoorReport", "LuaMessage", path, "", path.name, "Visible CM request packet file.")
    for path in sm_files:
        add_row("lua_response_packet_file", "SM_DigitDoorReport", "LuaMessage", path, "", path.name, "Visible SM response packet file.")
    if not sm_files:
        add_row("lua_response_packet_missing", "SM_DigitDoorReport", "LuaMessage", None, "", "", "No visible SM_DigitDoorReport Lua packet file found.")

    netlogic_path = logic_dir / "DigitDoorNetLogic.lua"
    if netlogic_path.is_file():
        for line_no, line in enumerate(netlogic_path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            compact = _WHITESPACE_RE.sub("", line)
            if "_CM_DigitDoorReport" in line and "F_Register" in line:
                add_row("netlogic_request_registered", "CM_DigitDoorReport", "DigitDoorNetLogic", netlogic_path, line_no, line, "Request packet is registered.")
            if "_CM_DigitDoorReport" in line and "F_Unregister" in line:
                add_row("netlogic_request_unregistered", "CM_DigitDoorReport", "DigitDoorNetLogic", netlogic_path, line_no, line, "Request packet is unregistered on destroy.")
            if "CM_DigitDoorReportFun" in line:
                add_row("netlogic_request_send_function", "CM_DigitDoorReportFun", "DigitDoorNetLogic", netlogic_path, line_no, line, "Request send function exists.")
            if "F_SendMsg(CM_DigitDoorReport)" in compact:
                add_row("netlogic_request_sent", "CM_DigitDoorReport", "DigitDoorNetLogic", netlogic_path, line_no, line, "Request packet is sent.")
            if "SM_DigitDoorReport" in line:
                add_row("netlogic_response_symbol", "SM_DigitDoorReport", "DigitDoorNetLogic", netlogic_path, line_no, line, "Potential visible response symbol.")

    index_dir = root / "apk_static_index"
    indexed_files = [
        index_dir / "lua_lscript_module_digitdoor_protocol_schemas.tsv",
        index_dir / "lua_lscript_module_digitdoor_protocol_fields.tsv",
        index_dir / "lua_lscript_module_digitdoor_netlogic_flow_edges.tsv",
        index_dir / "lua_lscript_module_digitdoor_surface_protocol_refs.tsv",
    ]
    protocol_response_rows = 0
    for path in indexed_files:
        if not path.is_file():
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if "CM_DigitDoorReport" in line:
                add_row("index_request_report_row", "CM_DigitDoorReport", path.stem, path, line_no, line, "Existing generated index confirms request-side report surface.")
            if "SM_DigitDoorReport" in line:
                protocol_response_rows += 1
                add_row("index_response_report_row", "SM_DigitDoorReport", path.stem, path, line_no, line, "Generated index surfaced a response-side report row.")
    if protocol_response_rows == 0:
        add_row("index_response_report_missing", "SM_DigitDoorReport", "apk_static_index", None, "", "", "Generated DigitDoor indexes contain no SM_DigitDoorReport row.")

    cpp2il_terms = ["CM_DigitDoorReport", "SM_DigitDoorReport"]
    for surface in _digitdoor_startgame_cpp2il_surfaces(root):
        files = _iter_digitdoor_startgame_cpp2il_files(surface)
        surface_hit_count = 0
        for file_path in files:
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if not any(term in text for term in cpp2il_terms):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                for term in cpp2il_terms:
                    if term in line:
                        surface_hit_count += 1
                        add_row(
                            "cpp2il_exact_report_symbol_hit",
                            term,
                            str(surface["surface"]),
                            file_path,
                            line_no,
                            line,
                            "Exact report packet symbol surfaced in native-readable output; line context must be reviewed.",
                        )
        add_row(
            "cpp2il_surface_summary",
            str(surface["surface"]),
            "Cpp2IL",
            Path(surface["path"]),
            "",
            f"files={len(files)} hits={surface_hit_count}",
            "Cpp2IL/metadata surface exact-symbol scan summary.",
        )
    return rows


def _write_digitdoor_pvp_report_acceptance_gap_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor PVP report acceptance gap",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Evidence rows: {len(rows)}",
        "- Scope: static/index evidence for whether the visible client surface closes `CM_DigitDoorReport` acceptance. Runtime truth must come from the read-only Runtime layer.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            "| Category | Field | Source | File | Line | Snippet |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    priority_categories = {
        "lua_request_packet_file",
        "lua_response_packet_missing",
        "netlogic_request_registered",
        "netlogic_request_sent",
        "index_request_report_row",
        "index_response_report_missing",
        "cpp2il_exact_report_symbol_hit",
        "cpp2il_surface_summary",
    }
    for row in rows:
        if row.get("category") in priority_categories:
            lines.append(
                "| "
                f"{_md_table_cell(row.get('category', ''))} | "
                f"{_md_table_cell(row.get('field', ''))} | "
                f"{_md_table_cell(row.get('source', ''))} | "
                f"{_md_table_cell(row.get('file', ''))} | "
                f"{row.get('line', '')} | "
                f"{_md_table_cell(row.get('snippet', ''))} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- The request side is visible and indexed: `CM_DigitDoorReport(91644)` exists, is registered by `DigitDoorNetLogic`, and is sent by `CM_DigitDoorReportFun`.",
            "- The response/acceptance side is not visible in current Lua/index surfaces: no `SM_DigitDoorReport` packet file, schema row, or NetLogic handler was found.",
            "- Exact-symbol Cpp2IL/metadata scan does not expose a readable native `SM_DigitDoorReport` consumer in the current surfaces.",
            "- Treat server acceptance and validation truth as unresolved until the read-only Runtime layer exposes the relevant state or stronger native evidence is added.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_pvp_report_acceptance_gap_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _digitdoor_pvp_report_acceptance_gap_rows(root, logic_dir)
    category_counts = Counter(str(row.get("category") or "") for row in rows)
    cpp2il_file_count = 0
    cpp2il_surface_count = category_counts.get("cpp2il_surface_summary", 0)
    for row in rows:
        if row.get("category") != "cpp2il_surface_summary":
            continue
        match = re.search(r"files=(\d+)", str(row.get("snippet") or ""))
        if match:
            cpp2il_file_count += int(match.group(1))
    stats = {
        "evidence_row_count": len(rows),
        "cm_lua_packet_file_count": category_counts.get("lua_request_packet_file", 0),
        "sm_lua_packet_file_count": category_counts.get("lua_response_packet_file", 0),
        "netlogic_request_registered_rows": category_counts.get("netlogic_request_registered", 0),
        "netlogic_request_sent_rows": category_counts.get("netlogic_request_sent", 0),
        "netlogic_response_symbol_rows": category_counts.get("netlogic_response_symbol", 0),
        "index_request_report_rows": category_counts.get("index_request_report_row", 0),
        "index_response_report_rows": category_counts.get("index_response_report_row", 0),
        "cpp2il_surface_count": cpp2il_surface_count,
        "cpp2il_scanned_file_count": cpp2il_file_count,
        "cpp2il_exact_report_symbol_hit_count": category_counts.get("cpp2il_exact_report_symbol_hit", 0),
    }
    verdict = {
        "request_report_surface_visible": stats["cm_lua_packet_file_count"] > 0
        and stats["netlogic_request_registered_rows"] > 0
        and stats["netlogic_request_sent_rows"] > 0
        and stats["index_request_report_rows"] > 0,
        "no_visible_sm_report_packet_or_handler": stats["sm_lua_packet_file_count"] == 0
        and stats["netlogic_response_symbol_rows"] == 0
        and stats["index_response_report_rows"] == 0,
        "native_readable_surfaces_have_no_exact_report_symbol": stats["cpp2il_exact_report_symbol_hit_count"] == 0,
    }
    verdict["pvp_report_acceptance_gap_confirmed"] = all(verdict.values())
    _write_tsv(
        out_dir / "pvp_report_acceptance_gap_evidence.tsv",
        rows,
        ["category", "field", "source", "file", "line", "snippet", "note"],
    )
    report_path = out_dir / "pvp_report_acceptance_gap_report.md"
    _write_digitdoor_pvp_report_acceptance_gap_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        rows=rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["pvp_report_acceptance_gap_confirmed"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "evidence": str(out_dir / "pvp_report_acceptance_gap_evidence.tsv"),
        },
    }


def _pvp_report_family_scene_module(path: Path) -> str:
    name = path.name
    if name == "DigitDoorPVPSceneView.lua":
        return "digitdoor"
    if name == "TowerDefensePVPSceneView.lua":
        return "towerdefense"
    if name == "DoupoTDPVPSceneView.lua":
        return "doupotd"
    return path.stem


def _pvp_report_family_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    scene_names = {"DigitDoorPVPSceneView.lua", "TowerDefensePVPSceneView.lua", "DoupoTDPVPSceneView.lua"}
    for path in root.glob("by_source/lscripts/gamesystem/game/*/text_assets/*PVPSceneView.lua"):
        if path.name in scene_names:
            candidates.append(path)
    candidates.extend(root.glob("by_source/lscripts/gamesystem/game/*/text_assets/*NetLogic.lua"))
    candidates.extend(root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorReport*.lua"))
    candidates.extend(root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorReport*.lua"))
    candidates.extend(root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DoupoTDReport*.lua"))
    candidates.extend(root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DoupoTDReport*.lua"))
    index_dir = root / "apk_static_index"
    candidates.extend(
        [
            index_dir / "lua_lscript_module_digitdoor_protocol_schemas.tsv",
            index_dir / "lua_lscript_module_doupotd_protocol_schemas.tsv",
            index_dir / "lua_lscript_module_digitdoor_netlogic_flow_edges.tsv",
            index_dir / "lua_lscript_module_doupotd_netlogic_flow_edges.tsv",
        ]
    )
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _pvp_report_family_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add_row(
        category: str,
        module: str,
        field: str,
        path: Path | None,
        line: int | str,
        snippet: str,
        note: str,
    ) -> None:
        rows.append(
            {
                "category": category,
                "module": module,
                "field": field,
                "file": _path_display(path, root) if path else "",
                "line": line,
                "snippet": _WHITESPACE_RE.sub(" ", snippet.strip()),
                "note": note,
            }
        )

    files = _pvp_report_family_files(root)
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        name = path.name
        if name.endswith("PVPSceneView.lua"):
            module = _pvp_report_family_scene_module(path)
            current_function = ""
            for line_no, line in enumerate(text.splitlines(), 1):
                if match := _LUA_FUNCTION_RE.search(line):
                    current_function = match.group(1).strip()
                compact = _WHITESPACE_RE.sub("", line)
                if current_function == "_M.CheckList":
                    if "CM_DigitDoorReportFun(" in line:
                        add_row(
                            "pvp_scene_uses_digitdoor_report_fun",
                            module,
                            "CM_DigitDoorReportFun",
                            path,
                            line_no,
                            line,
                            "PVP scene sends through DigitDoor report function.",
                        )
                    if "CM_DoupoTDReportFun(" in line:
                        add_row(
                            "pvp_scene_uses_doupotd_report_fun",
                            module,
                            "CM_DoupoTDReportFun",
                            path,
                            line_no,
                            line,
                            "PVP scene sends through DoupoTD report function.",
                        )
                    for field in ["atkVoList", "defVoList", "clientWinnerId", "serverWinnerId"]:
                        if f"{field}=" in compact:
                            add_row(
                                "pvp_scene_common_report_field",
                                module,
                                field,
                                path,
                                line_no,
                                line,
                                "Scene fills a common PVP report argument.",
                            )
                    for scene_type in [
                        "Scene_DigitDoorPVPIT",
                        "Scene_DigitDoorPVP",
                        "Scene_TowerDefensePVPIT",
                        "Scene_TowerDefensePVP",
                        "Scene_DoupoTDPVPIT",
                        "Scene_DoupoTDPVP",
                    ]:
                        if scene_type in line:
                            add_row(
                                "pvp_scene_mode_branch",
                                module,
                                scene_type,
                                path,
                                line_no,
                                line,
                                "Scene branches report payload by PVP map type.",
                            )
                if current_function == "_M.CreateEntityData" and "DigitDoorSimpleVO.new()" in compact:
                    add_row(
                        "pvp_scene_uses_digitdoor_simple_vo",
                        module,
                        "DigitDoorSimpleVO",
                        path,
                        line_no,
                        line,
                        "Scene report snapshot row uses DigitDoorSimpleVO.",
                    )
        elif name.endswith("NetLogic.lua"):
            module = "digitdoor" if name == "DigitDoorNetLogic.lua" else "doupotd" if name == "DoupoTDNetLogic.lua" else path.parent.parent.name
            current_function = ""
            for line_no, line in enumerate(text.splitlines(), 1):
                if match := _LUA_FUNCTION_RE.search(line):
                    current_function = match.group(1).strip()
                if "CM_DigitDoorReport" in line:
                    add_row(
                        "netlogic_digitdoor_report_symbol",
                        module,
                        "CM_DigitDoorReport",
                        path,
                        line_no,
                        line,
                        "NetLogic has DigitDoor report symbol evidence.",
                    )
                if "CM_DoupoTDReport" in line:
                    add_row(
                        "netlogic_doupotd_report_symbol",
                        module,
                        "CM_DoupoTDReport",
                        path,
                        line_no,
                        line,
                        "NetLogic has DoupoTD report symbol evidence.",
                    )
                if "function _M.CM_DigitDoorReportFun" in line:
                    add_row(
                        "netlogic_digitdoor_report_fun",
                        module,
                        "CM_DigitDoorReportFun",
                        path,
                        line_no,
                        line,
                        "DigitDoor report send function is visible.",
                    )
                if "function _M.CM_DoupoTDReportFun" in line:
                    add_row(
                        "netlogic_doupotd_report_fun",
                        module,
                        "CM_DoupoTDReportFun",
                        path,
                        line_no,
                        line,
                        "DoupoTD report send function is visible.",
                    )
                if current_function in {"_M.CM_DigitDoorReportFun", "_M.CM_DoupoTDReportFun"} and "F_SendMsg" in line:
                    add_row(
                        "netlogic_report_send",
                        module,
                        current_function,
                        path,
                        line_no,
                        line,
                        "Report send function sends a packet.",
                    )
        elif name.startswith(("CM_DigitDoorReport", "SM_DigitDoorReport", "CM_DoupoTDReport", "SM_DoupoTDReport")):
            field = name.split("__", 1)[0].removesuffix(".lua")
            module = "digitdoor" if "DigitDoor" in field else "doupotd"
            add_row(
                "report_packet_file",
                module,
                field,
                path,
                "",
                name,
                "Visible report packet file.",
            )
            for line_no, line in enumerate(text.splitlines(), 1):
                if "return" in line and ("91644" in line or "936" in line):
                    add_row(
                        "report_packet_id",
                        module,
                        field,
                        path,
                        line_no,
                        line,
                        "Report packet id candidate.",
                    )
        elif path.suffix == ".tsv":
            for line_no, line in enumerate(text.splitlines(), 1):
                if "CM_DigitDoorReport" in line or "SM_DigitDoorReport" in line:
                    add_row(
                        "index_digitdoor_report_row",
                        "digitdoor",
                        "DigitDoorReport",
                        path,
                        line_no,
                        line,
                        "Generated index row for DigitDoor report family.",
                    )
                if "CM_DoupoTDReport" in line or "SM_DoupoTDReport" in line:
                    add_row(
                        "index_doupotd_report_row",
                        "doupotd",
                        "DoupoTDReport",
                        path,
                        line_no,
                        line,
                        "Generated index row for DoupoTD report family.",
                    )
    if not any(row["category"] == "report_packet_file" and row["field"] == "CM_DoupoTDReport" for row in rows):
        add_row(
            "doupotd_report_packet_missing",
            "doupotd",
            "CM_DoupoTDReport",
            None,
            "",
            "",
            "No visible CM_DoupoTDReport packet file found.",
        )
    if not any(row["category"] == "netlogic_doupotd_report_fun" for row in rows):
        add_row(
            "doupotd_report_netlogic_fun_missing",
            "doupotd",
            "CM_DoupoTDReportFun",
            None,
            "",
            "",
            "No visible CM_DoupoTDReportFun implementation found.",
        )
    return rows


def _write_pvp_report_family_reuse_markdown(
    path: Path,
    *,
    export_root: Path,
    rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# PVP report family reuse",
        "",
        f"- Export root: `{export_root}`",
        f"- Evidence rows: {len(rows)}",
        "- Scope: static Lua/index evidence for PVP report reuse across DigitDoor, TowerDefense, and DoupoTD scene views. It does not send, replay, or modify packets.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            "| Category | Module | Field | File | Line | Snippet |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    priority_categories = {
        "pvp_scene_uses_digitdoor_report_fun",
        "pvp_scene_uses_doupotd_report_fun",
        "pvp_scene_uses_digitdoor_simple_vo",
        "netlogic_digitdoor_report_fun",
        "netlogic_doupotd_report_fun",
        "report_packet_file",
        "doupotd_report_packet_missing",
        "doupotd_report_netlogic_fun_missing",
        "index_digitdoor_report_row",
        "index_doupotd_report_row",
    }
    for row in rows:
        if row.get("category") not in priority_categories:
            continue
        lines.append(
            "| "
            f"{_md_table_cell(row.get('category', ''))} | "
            f"{_md_table_cell(row.get('module', ''))} | "
            f"{_md_table_cell(row.get('field', ''))} | "
            f"{_md_table_cell(row.get('file', ''))} | "
            f"{row.get('line', '')} | "
            f"{_md_table_cell(row.get('snippet', ''))} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `DigitDoorPVPSceneView` builds a `CM_DigitDoorReport` request through `DigitDoorMgr.NetLogic:CM_DigitDoorReportFun`.",
            "- `TowerDefensePVPSceneView` also calls the same `DigitDoorMgr.NetLogic:CM_DigitDoorReportFun`, so TowerDefense PVP reuses the DigitDoor report packet/path rather than a separate TowerDefense report packet in the visible Lua surface.",
            "- `DoupoTDPVPSceneView` has a parallel call shape through `DoupoTDMgr.NetLogic:CM_DoupoTDReportFun`, but the current visible export lacks both `CM_DoupoTDReport.lua` and a `CM_DoupoTDReportFun` implementation/index row.",
            "- All three scene views use the same visible argument shape (`replayId/type/round/pkStage/zone/pkStep/time/atkVoList/defVoList/clientWinnerId/serverWinnerId`) and `DigitDoorSimpleVO` snapshot rows.",
            "- Treat the family as partially reused, not fully symmetric: DigitDoor/TowerDefense report path is closed on the request side; DoupoTD report path remains a source/export gap until a packet or runtime sample appears.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_pvp_report_family_reuse_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _pvp_report_family_rows(root)
    category_counts = Counter(str(row.get("category") or "") for row in rows)
    scene_digitdoor_calls = sum(
        1
        for row in rows
        if row.get("category") == "pvp_scene_uses_digitdoor_report_fun" and row.get("module") == "digitdoor"
    )
    scene_towerdefense_digitdoor_calls = sum(
        1
        for row in rows
        if row.get("category") == "pvp_scene_uses_digitdoor_report_fun" and row.get("module") == "towerdefense"
    )
    scene_doupotd_calls = sum(
        1
        for row in rows
        if row.get("category") == "pvp_scene_uses_doupotd_report_fun" and row.get("module") == "doupotd"
    )
    doupotd_packet_files = sum(
        1
        for row in rows
        if row.get("category") == "report_packet_file" and row.get("field") == "CM_DoupoTDReport"
    )
    digitdoor_packet_files = sum(
        1
        for row in rows
        if row.get("category") == "report_packet_file" and row.get("field") == "CM_DigitDoorReport"
    )
    stats = {
        "source_file_count": len(_pvp_report_family_files(root)),
        "evidence_row_count": len(rows),
        "digitdoor_scene_digitdoor_report_call_count": scene_digitdoor_calls,
        "towerdefense_scene_digitdoor_report_call_count": scene_towerdefense_digitdoor_calls,
        "doupotd_scene_doupotd_report_call_count": scene_doupotd_calls,
        "digitdoor_cm_report_packet_file_count": digitdoor_packet_files,
        "doupotd_cm_report_packet_file_count": doupotd_packet_files,
        "digitdoor_netlogic_report_fun_rows": category_counts.get("netlogic_digitdoor_report_fun", 0),
        "doupotd_netlogic_report_fun_rows": category_counts.get("netlogic_doupotd_report_fun", 0),
        "digitdoor_index_report_rows": category_counts.get("index_digitdoor_report_row", 0),
        "doupotd_index_report_rows": category_counts.get("index_doupotd_report_row", 0),
        "digitdoor_simple_vo_scene_rows": category_counts.get("pvp_scene_uses_digitdoor_simple_vo", 0),
    }
    verdict = {
        "digitdoor_scene_uses_digitdoor_report": scene_digitdoor_calls > 0,
        "towerdefense_scene_reuses_digitdoor_report": scene_towerdefense_digitdoor_calls > 0,
        "doupotd_scene_has_parallel_report_call": scene_doupotd_calls > 0,
        "digitdoor_report_request_surface_indexed": digitdoor_packet_files > 0
        and stats["digitdoor_netlogic_report_fun_rows"] > 0
        and stats["digitdoor_index_report_rows"] > 0,
        "doupotd_report_request_surface_missing": doupotd_packet_files == 0
        and stats["doupotd_netlogic_report_fun_rows"] == 0
        and stats["doupotd_index_report_rows"] == 0,
        "family_uses_common_digitdoor_simple_vo_shape": stats["digitdoor_simple_vo_scene_rows"] >= 3,
    }
    verdict["pvp_report_family_reuse_confirmed"] = all(verdict.values())
    _write_tsv(
        out_dir / "pvp_report_family_reuse_evidence.tsv",
        rows,
        ["category", "module", "field", "file", "line", "snippet", "note"],
    )
    report_path = out_dir / "pvp_report_family_reuse_report.md"
    _write_pvp_report_family_reuse_markdown(
        report_path,
        export_root=root,
        rows=rows,
        stats=stats,
        verdict=verdict,
    )
    return {
        "confirmed": verdict["pvp_report_family_reuse_confirmed"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "evidence": str(out_dir / "pvp_report_family_reuse_evidence.tsv"),
        },
    }


_DIGITDOOR_GAMEPLAYER_CPP2IL_TERMS: dict[str, str] = {
    "CM_DigitDoorGamePlayer": "direct_gameplayer_packet_symbol",
    "SM_DigitDoorGamePlayer": "direct_gameplayer_packet_symbol",
    "DDBossVo": "direct_gameplayer_boss_vo_symbol",
    "bossVoList": "gameplayer_request_field_symbol",
    "passLevelVOS": "gameplayer_response_field_symbol",
    "DigitDoorExitGame": "gameplayer_settlement_boundary_symbol",
    "ReqFinishGame": "gameplayer_settlement_boundary_symbol",
    "SetFinishLevelInfo": "gameplayer_settlement_boundary_symbol",
    "GetTotalBossDamageList": "gameplayer_request_summary_symbol",
    "GetTotalKillSmallMonsterNum": "gameplayer_request_summary_symbol",
}


def _classify_digitdoor_gameplayer_cpp2il_hit(category: str, term: str) -> str:
    if category.startswith("direct_gameplayer"):
        return "direct GamePlayer packet/bean symbol candidate; line context determines whether it is a real consumer"
    if category.endswith("_field_symbol"):
        return "GamePlayer request/response field-name candidate; useful only with adjacent packet or method context"
    if category.endswith("_boundary_symbol"):
        return "settlement request/response boundary helper candidate"
    if category.endswith("_summary_symbol"):
        return "local request-summary helper candidate"
    return f"broad GamePlayer-related symbol candidate: {term}"


def _write_digitdoor_gameplayer_cpp2il_consumer_markdown(
    path: Path,
    *,
    export_root: Path,
    logic_dir: Path,
    surface_rows: list[dict[str, Any]],
    hit_rows: list[dict[str, Any]],
    lua_rows: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
    verdict: dict[str, Any],
) -> None:
    lines = [
        "# DigitDoor GamePlayer Cpp2IL consumer surface",
        "",
        f"- Export root: `{export_root}`",
        f"- Logic dir: `{logic_dir}`",
        f"- Surfaces scanned: {len(surface_rows)}",
        f"- Cpp2IL/native-readable evidence rows: {len(hit_rows)}",
        f"- Lua settlement reference rows: {len(lua_rows)}",
        "- Scope: static Cpp2IL/diffable C#/ISIL/metadata search for GamePlayer settlement packet and boundary symbols. It does not hook, patch, replay, or modify the client.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Surface Summary",
            "",
            "| Surface | Exists | Files Scanned | Hit Rows |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for row in surface_rows:
        lines.append(
            "| "
            f"{row.get('surface', '')} | "
            f"{row.get('exists', '')} | "
            f"{row.get('files_scanned', '')} | "
            f"{row.get('hit_rows', '')} |"
        )
    lines.extend(
        [
            "",
            "## Term Summary",
            "",
            "| Term | Category | Hits |",
            "| --- | --- | ---: |",
        ]
    )
    for row in summary_rows:
        lines.append(
            "| "
            f"{_md_table_cell(row.get('term', ''))} | "
            f"{_md_table_cell(row.get('category', ''))} | "
            f"{row.get('hit_count', '')} |"
        )
    if lua_rows:
        lines.extend(
            [
                "",
                "## Lua Reference Boundary",
                "",
                "| Category | Direction | Field | File | Line | Function |",
                "| --- | --- | --- | --- | ---: | --- |",
            ]
        )
        for row in lua_rows[:80]:
            lines.append(
                "| "
                f"{_md_table_cell(row.get('category', ''))} | "
                f"{_md_table_cell(row.get('direction', ''))} | "
                f"{_md_table_cell(row.get('field', ''))} | "
                f"{_md_table_cell(row.get('file', ''))} | "
                f"{row.get('line', '')} | "
                f"{_md_table_cell(row.get('function', ''))} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Lua evidence already maps the visible settlement boundary: `CM_DigitDoorGamePlayer` submits local progress summary and `SM_DigitDoorGamePlayer` drives final result/reward/pass-level display state.",
            "- This report asks whether the Cpp2IL/native-readable surface contains direct GamePlayer packet, `DDBossVo`, request-summary, or settlement-boundary symbols that add another consumer surface.",
            "- Field-name hits without adjacent packet or method context are supporting evidence only; they do not override the Lua boundary or prove server behavior.",
            "- If direct native-readable symbols stay absent, the remaining clean closure path is still a privacy-filtered `91626/91627` runtime sample.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_gameplayer_cpp2il_consumer_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    terms = {term.lower(): (term, category) for term, category in _DIGITDOOR_GAMEPLAYER_CPP2IL_TERMS.items()}
    hit_rows: list[dict[str, Any]] = []
    surface_rows: list[dict[str, Any]] = []
    term_counts: Counter[tuple[str, str]] = Counter()
    for surface in _digitdoor_startgame_cpp2il_surfaces(root):
        surface_name = str(surface["surface"])
        surface_path = Path(surface["path"])
        files = _iter_digitdoor_startgame_cpp2il_files(surface)
        surface_hit_count = 0
        for file_path in files:
            try:
                lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            rel_path = file_path.relative_to(root) if file_path.is_relative_to(root) else file_path
            for line_no, line in enumerate(lines, 1):
                lowered = line.lower()
                for lowered_term, (term, category) in terms.items():
                    if lowered_term not in lowered:
                        continue
                    term_counts[(term, category)] += 1
                    surface_hit_count += 1
                    hit_rows.append(
                        {
                            "surface": surface_name,
                            "file": str(rel_path),
                            "line": line_no,
                            "term": term,
                            "category": category,
                            "snippet": line.strip()[:240],
                            "interpretation": _classify_digitdoor_gameplayer_cpp2il_hit(category, term),
                        }
                    )
        surface_rows.append(
            {
                "surface": surface_name,
                "path": str(surface_path),
                "exists": surface_path.exists(),
                "files_scanned": len(files),
                "hit_rows": surface_hit_count,
            }
        )
    direct_packet_hits = [row for row in hit_rows if row.get("category") == "direct_gameplayer_packet_symbol"]
    boss_vo_hits = [row for row in hit_rows if row.get("category") == "direct_gameplayer_boss_vo_symbol"]
    field_hits = [row for row in hit_rows if str(row.get("category") or "").endswith("_field_symbol")]
    boundary_hits = [row for row in hit_rows if row.get("category") == "gameplayer_settlement_boundary_symbol"]
    request_summary_hits = [row for row in hit_rows if row.get("category") == "gameplayer_request_summary_symbol"]
    lua_rows = _digitdoor_gameplayer_settlement_rows(root, logic_dir)
    summary_rows = [
        {
            "term": term,
            "category": category,
            "hit_count": term_counts.get((term, category), 0),
        }
        for term, category in _DIGITDOOR_GAMEPLAYER_CPP2IL_TERMS.items()
    ]
    verdict = {
        "cpp2il_surfaces_found": any(bool(row["exists"]) for row in surface_rows),
        "cpp2il_has_gameplayer_packet_symbols": bool(direct_packet_hits),
        "cpp2il_has_ddbossvo_symbol": bool(boss_vo_hits),
        "cpp2il_has_gameplayer_field_symbols": bool(field_hits),
        "cpp2il_has_settlement_boundary_symbols": bool(boundary_hits),
        "cpp2il_has_request_summary_symbols": bool(request_summary_hits),
        "lua_settlement_boundary_found": bool(lua_rows),
        "native_readable_surface_closes_gameplayer_settlement": bool(direct_packet_hits)
        and (bool(boundary_hits) or bool(field_hits) or bool(request_summary_hits)),
    }
    _write_tsv(
        out_dir / "gameplayer_cpp2il_consumer_surfaces.tsv",
        surface_rows,
        ["surface", "path", "exists", "files_scanned", "hit_rows"],
    )
    _write_tsv(
        out_dir / "gameplayer_cpp2il_consumer_hits.tsv",
        hit_rows,
        ["surface", "file", "line", "term", "category", "snippet", "interpretation"],
    )
    _write_tsv(
        out_dir / "gameplayer_cpp2il_consumer_summary.tsv",
        summary_rows,
        ["term", "category", "hit_count"],
    )
    _write_tsv(
        out_dir / "gameplayer_cpp2il_lua_reference_hits.tsv",
        lua_rows,
        ["category", "direction", "field", "source", "file", "line", "function", "snippet"],
    )
    report_path = out_dir / "gameplayer_cpp2il_consumer_report.md"
    _write_digitdoor_gameplayer_cpp2il_consumer_markdown(
        report_path,
        export_root=root,
        logic_dir=logic_dir,
        surface_rows=surface_rows,
        hit_rows=hit_rows,
        lua_rows=lua_rows,
        summary_rows=summary_rows,
        verdict=verdict,
    )
    json_path = out_dir / "gameplayer_cpp2il_consumer_report.json"
    json_path.write_text(
        json.dumps(
            {
                "stats": {
                    "surface_count": len(surface_rows),
                    "files_scanned": sum(int(row["files_scanned"]) for row in surface_rows),
                    "hit_count": len(hit_rows),
                    "direct_packet_hit_count": len(direct_packet_hits),
                    "boss_vo_hit_count": len(boss_vo_hits),
                    "field_hit_count": len(field_hits),
                    "boundary_hit_count": len(boundary_hits),
                    "request_summary_hit_count": len(request_summary_hits),
                    "lua_reference_hit_count": len(lua_rows),
                },
                "verdict": verdict,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": verdict["cpp2il_surfaces_found"],
        "output_dir": str(out_dir),
        "stats": {
            "surface_count": len(surface_rows),
            "files_scanned": sum(int(row["files_scanned"]) for row in surface_rows),
            "hit_count": len(hit_rows),
            "direct_packet_hit_count": len(direct_packet_hits),
            "boss_vo_hit_count": len(boss_vo_hits),
            "field_hit_count": len(field_hits),
            "boundary_hit_count": len(boundary_hits),
            "request_summary_hit_count": len(request_summary_hits),
            "lua_reference_hit_count": len(lua_rows),
        },
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "surfaces": str(out_dir / "gameplayer_cpp2il_consumer_surfaces.tsv"),
            "hits": str(out_dir / "gameplayer_cpp2il_consumer_hits.tsv"),
            "summary": str(out_dir / "gameplayer_cpp2il_consumer_summary.tsv"),
            "lua_reference_hits": str(out_dir / "gameplayer_cpp2il_lua_reference_hits.tsv"),
            "json": str(json_path),
        },
    }


def _digitdoor_readyfight_request_levelid_files(root: Path, logic_dir: Path) -> list[Path]:
    files = list(logic_dir.glob("*.lua"))
    files.extend(root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorReadyFight.lua"))
    unique: dict[str, Path] = {}
    for path in files:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _classify_digitdoor_readyfight_request_levelid_line(path: Path, line: str) -> list[str]:
    stripped = line.strip()
    categories: list[str] = []
    if path.name == "CM_DigitDoorReadyFight.lua":
        if "self.levelId=0" in line:
            categories.append("request_packet_levelid_default")
        if "self.levelId=self:readInt()" in line:
            categories.append("request_packet_levelid_read")
        if "writeInt(self.levelId)" in line:
            categories.append("request_packet_levelid_write")
    if "GetMessageFromPools(_CM_DigitDoorReadyFight)" in line:
        categories.append("netlogic_request_pool")
    if "F_SendMsg(CM_DigitDoorReadyFight)" in line:
        categories.append("netlogic_request_send")
    if re.search(r"\bCM_DigitDoorReadyFight\.levelId\s*=", line):
        categories.append("request_levelid_assignment")
    if "CM_DigitDoorReadyFightFun" in line:
        if not stripped.startswith("function "):
            categories.append("readyfight_request_callsite")
    if "SetGameLevel(msg.levelId)" in line:
        categories.append("response_levelid_state_set")
    if "function _M.SetGameLevel" in line or "function _M.GetGameLevel" in line or "V_GameLevelId" in line:
        categories.append("game_level_state_accessor")
    return categories


def _digitdoor_readyfight_request_levelid_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_readyfight_request_levelid_files(root, logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            for category in _classify_digitdoor_readyfight_request_levelid_line(path, line):
                rows.append(
                    {
                        "category": category,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": _WHITESPACE_RE.sub(" ", line.strip()),
                    }
                )
    return rows


def _write_digitdoor_readyfight_request_levelid_markdown(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
    logic_dir: Path,
) -> None:
    lines = [
        "# DigitDoor ReadyFight request levelId audit",
        "",
        "Static read-only audit for where `CM_DigitDoorReadyFight.levelId` is populated before send.",
        "",
        f"- Logic dir: `{logic_dir}`",
        "",
        "## Stats",
        "",
    ]
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Verdict", ""])
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            "- The request packet declares and writes `levelId`, but the visible Lua send function only gets a pooled `CM_DigitDoorReadyFight` and sends it.",
            "- No visible Lua assignment to `CM_DigitDoorReadyFight.levelId` was found in the current static surface.",
            "- `SM_DigitDoorReadyFight.levelId` is visibly used to update local game-level state after the server response.",
            "- Treat the request `levelId` as a static placement gap: it may be filled by native code/object-pool state, left at default, or hidden in an unexported caller. Do not infer client authority from the field declaration alone.",
            "",
            "## Evidence Samples",
            "",
        ]
    )
    for row in rows[:100]:
        lines.append(
            f"- `{row.get('category')}` {row.get('file')}:{row.get('line')} `{row.get('function')}` - `{row.get('snippet')}`"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_readyfight_request_levelid_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    rows = _digitdoor_readyfight_request_levelid_rows(root, logic_dir)
    category_counts = Counter(str(row.get("category") or "") for row in rows)
    stats = {
        "evidence_row_count": len(rows),
        "request_packet_levelid_default_rows": category_counts.get("request_packet_levelid_default", 0),
        "request_packet_levelid_read_rows": category_counts.get("request_packet_levelid_read", 0),
        "request_packet_levelid_write_rows": category_counts.get("request_packet_levelid_write", 0),
        "netlogic_request_pool_rows": category_counts.get("netlogic_request_pool", 0),
        "netlogic_request_send_rows": category_counts.get("netlogic_request_send", 0),
        "request_levelid_assignment_rows": category_counts.get("request_levelid_assignment", 0),
        "readyfight_request_callsite_rows": category_counts.get("readyfight_request_callsite", 0),
        "response_levelid_state_set_rows": category_counts.get("response_levelid_state_set", 0),
        "game_level_state_accessor_rows": category_counts.get("game_level_state_accessor", 0),
    }
    verdict = {
        "request_packet_has_levelid_field": stats["request_packet_levelid_write_rows"] > 0,
        "visible_netlogic_sends_readyfight_request": stats["netlogic_request_pool_rows"] > 0 and stats["netlogic_request_send_rows"] > 0,
        "visible_levelid_assignment_found": stats["request_levelid_assignment_rows"] > 0,
        "visible_lua_callsite_found": stats["readyfight_request_callsite_rows"] > 0,
        "response_sets_game_level_state": stats["response_levelid_state_set_rows"] > 0,
        "authority_interpretation": "request_levelid_static_assignment_gap",
    }
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_tsv = out_dir / "readyfight_request_levelid_evidence.tsv"
    report_path = out_dir / "readyfight_request_levelid_report.md"
    _write_tsv(rows_tsv, rows, ["category", "file", "line", "function", "snippet"])
    _write_digitdoor_readyfight_request_levelid_markdown(
        report_path,
        rows=rows,
        stats=stats,
        verdict=verdict,
        logic_dir=logic_dir,
    )
    return {
        "confirmed": stats["request_packet_levelid_write_rows"] > 0 and stats["netlogic_request_send_rows"] > 0,
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "rows": str(rows_tsv),
        },
    }


def _digitdoor_readyfight_partnerlist_files(root: Path, logic_dir: Path) -> list[Path]:
    files = list(logic_dir.glob("*.lua"))
    patterns = [
        "by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorReadyFight.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorStartGame.lua",
        "by_source/lscripts/gamesystem/game/message_*/text_assets/DDFightPartnerVO.lua",
    ]
    for pattern in patterns:
        files.extend(root.glob(pattern))
    unique: dict[str, Path] = {}
    for path in files:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _classify_digitdoor_readyfight_partnerlist_line(path: Path, line: str) -> list[str]:
    categories: list[str] = []
    if path.name == "SM_DigitDoorReadyFight.lua":
        if "self.indexList=CList.new()" in line:
            categories.append("readyfight_indexlist_ctor")
        if "readMessageList2List(self.indexList)" in line:
            categories.append("readyfight_indexlist_read")
        if "writeList(self.indexList)" in line:
            categories.append("readyfight_indexlist_write_list")
    if path.name == "CM_DigitDoorStartGame.lua":
        if "self.indexList=CList.new()" in line:
            categories.append("startgame_indexlist_ctor")
        if "readMessageList2List(self.indexList)" in line:
            categories.append("startgame_indexlist_read")
        if "writeList(self.indexList)" in line:
            categories.append("startgame_indexlist_write_list")
    if path.name == "DDFightPartnerVO.lua":
        if "91601" in line:
            categories.append("fight_partner_vo_id")
        if re.search(r"\bself\.(id|index)\b", line):
            categories.append("fight_partner_vo_field")
    if "SetFightPartnerVoList(msg.indexList)" in line:
        categories.append("readyfight_indexlist_to_cache")
    if "function _M.SetFightPartnerVoList" in line:
        categories.append("fight_partner_cache_setter")
    if "gameFightPartnerVoList:Count()==0" in line and "GetGameLevel()==1" in line:
        categories.append("first_level_default_partner_fallback")
    if "self.gameFightPartnerVoList:LuaDic_AddOrSetItem(v.index,v)" in line:
        categories.append("partner_vo_indexed_by_position")
    if "function _M.UpdateFightPartnerVoList" in line:
        categories.append("local_partner_selection_mutator")
    if 'require"GameSystem.Game.Message.module.mini.digitdoor.packet.vo.DDFightPartnerVO"' in line:
        categories.append("local_partner_vo_constructor_require")
    if "DDFightPartnerVO.new()" in line:
        categories.append("local_partner_vo_constructor")
    if re.search(r"\bvo\.(id|index)\s*=", line):
        categories.append("local_partner_vo_field_assignment")
    if "GetFightPartnerVoList()" in line:
        categories.append("fight_partner_cache_getter_use")
    if "CM_DigitDoorStartGame.indexList:Add(v)" in line:
        categories.append("startgame_submit_partner_vo")
    if "UpdateFightPartnerVoList(" in line and not line.strip().startswith("function "):
        categories.append("local_partner_selection_update_call")
    return categories


def _digitdoor_readyfight_partnerlist_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_readyfight_partnerlist_files(root, logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            for category in _classify_digitdoor_readyfight_partnerlist_line(path, line):
                rows.append(
                    {
                        "category": category,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": _WHITESPACE_RE.sub(" ", line.strip()),
                    }
                )
    return rows


def _digitdoor_fight_partner_fields(rows: list[dict[str, Any]]) -> list[str]:
    fields: set[str] = set()
    for row in rows:
        if row.get("category") != "fight_partner_vo_field":
            continue
        for field in re.findall(r"\bself\.(id|index)\b", str(row.get("snippet") or "")):
            fields.add(field)
    return sorted(fields)


def _write_digitdoor_readyfight_partnerlist_markdown(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
    logic_dir: Path,
) -> None:
    lines = [
        "# DigitDoor ReadyFight indexList partner-list audit",
        "",
        "Static read-only audit for `SM_DigitDoorReadyFight.indexList` and the partner selection list reused by `CM_DigitDoorStartGame`.",
        "",
        f"- Logic dir: `{logic_dir}`",
        "",
        "## Stats",
        "",
    ]
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Verdict", ""])
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            "- `SM_DigitDoorReadyFight.indexList` is a bean list and resolves cleanly to the adjacent `DDFightPartnerVO(id,index)` shape.",
            "- The response list is cached by `SetFightPartnerVoList(msg.indexList)` and keyed by `v.index` in `gameFightPartnerVoList`.",
            "- Local drag/scene code mutates the same selection cache through `UpdateFightPartnerVoList`, constructing `DDFightPartnerVO` rows with `id/index`.",
            "- `CM_DigitDoorStartGame.indexList` then submits the current cached partner VO list through `writeList`.",
            "- This is a local selection/snapshot flow. It documents client-side list construction but does not imply the server accepts invalid partner ids or positions.",
            "",
            "## Evidence Samples",
            "",
        ]
    )
    for row in rows[:120]:
        lines.append(
            f"- `{row.get('category')}` {row.get('file')}:{row.get('line')} `{row.get('function')}` - `{row.get('snippet')}`"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_readyfight_partnerlist_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    rows = _digitdoor_readyfight_partnerlist_rows(root, logic_dir)
    category_counts = Counter(str(row.get("category") or "") for row in rows)
    partner_fields = _digitdoor_fight_partner_fields(rows)
    stats = {
        "evidence_row_count": len(rows),
        "readyfight_indexlist_read_rows": category_counts.get("readyfight_indexlist_read", 0),
        "readyfight_indexlist_write_list_rows": category_counts.get("readyfight_indexlist_write_list", 0),
        "startgame_indexlist_write_list_rows": category_counts.get("startgame_indexlist_write_list", 0),
        "fight_partner_vo_id_rows": category_counts.get("fight_partner_vo_id", 0),
        "fight_partner_vo_field_count": len(partner_fields),
        "readyfight_indexlist_to_cache_rows": category_counts.get("readyfight_indexlist_to_cache", 0),
        "partner_vo_indexed_by_position_rows": category_counts.get("partner_vo_indexed_by_position", 0),
        "local_partner_selection_mutator_rows": category_counts.get("local_partner_selection_mutator", 0),
        "local_partner_selection_update_call_rows": category_counts.get("local_partner_selection_update_call", 0),
        "startgame_submit_partner_vo_rows": category_counts.get("startgame_submit_partner_vo", 0),
        "first_level_default_partner_fallback_rows": category_counts.get("first_level_default_partner_fallback", 0),
    }
    verdict = {
        "readyfight_indexlist_is_bean_list": stats["readyfight_indexlist_write_list_rows"] > 0,
        "fight_partner_vo_shape": ",".join(partner_fields),
        "readyfight_indexlist_cached": stats["readyfight_indexlist_to_cache_rows"] > 0,
        "partner_cache_keyed_by_index": stats["partner_vo_indexed_by_position_rows"] > 0,
        "local_partner_selection_mutation_found": stats["local_partner_selection_mutator_rows"] > 0
        and stats["local_partner_selection_update_call_rows"] > 0,
        "startgame_submits_partner_vo_list": stats["startgame_submit_partner_vo_rows"] > 0
        and stats["startgame_indexlist_write_list_rows"] > 0,
        "authority_interpretation": "server_snapshot_then_local_selection_submit",
    }
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_tsv = out_dir / "readyfight_partnerlist_evidence.tsv"
    report_path = out_dir / "readyfight_partnerlist_report.md"
    _write_tsv(rows_tsv, rows, ["category", "file", "line", "function", "snippet"])
    _write_digitdoor_readyfight_partnerlist_markdown(
        report_path,
        rows=rows,
        stats=stats,
        verdict=verdict,
        logic_dir=logic_dir,
    )
    return {
        "confirmed": stats["readyfight_indexlist_to_cache_rows"] > 0 and stats["fight_partner_vo_field_count"] == 2,
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "rows": str(rows_tsv),
        },
    }


def _digitdoor_startgame_response_boundary_files(root: Path, logic_dir: Path) -> list[Path]:
    files = list(logic_dir.glob("*.lua"))
    files.extend(root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorStartGame.lua"))
    files.extend(root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/CM_DigitDoorStartGame.lua"))
    unique: dict[str, Path] = {}
    for path in files:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _classify_digitdoor_startgame_response_boundary_line(path: Path, line: str, current_function: str) -> list[str]:
    categories: list[str] = []
    if path.name == "SM_DigitDoorStartGame.lua":
        if "self.indexList=CList.new()" in line:
            categories.append("sm_startgame_indexlist_ctor")
        if "self.skillVos=CList.new()" in line:
            categories.append("sm_startgame_skillvos_ctor")
        if "readMessageList2List(self.indexList)" in line:
            categories.append("sm_startgame_indexlist_read")
        if "readMessageList2List(self.skillVos)" in line:
            categories.append("sm_startgame_skillvos_read")
        if "writeList(self.indexList)" in line:
            categories.append("sm_startgame_indexlist_write")
        if "writeList(self.skillVos)" in line:
            categories.append("sm_startgame_skillvos_write")
    if path.name == "CM_DigitDoorStartGame.lua":
        if "writeList(self.indexList)" in line:
            categories.append("cm_startgame_indexlist_write")
    if "function _M.SM_DigitDoorStartGameFun" in line:
        categories.append("sm_startgame_handler_definition")
    if "DigitDoorMgr.Inst_get():DigitDoorStartGame(msg)" in line:
        categories.append("sm_startgame_handler_to_mgr")
    if current_function in {"_M.SM_DigitDoorStartGameFun", "_M.DigitDoorStartGame"}:
        if "msg.indexList" in line:
            categories.append("sm_startgame_indexlist_consumer")
        if "msg.skillVos" in line:
            categories.append("sm_startgame_skillvos_consumer")
    if "function _M.DigitDoorStartGame" in line:
        categories.append("mgr_startgame_definition")
    if "IsInDigitDoorPveScene()or self.V_StartGame" in line:
        categories.append("mgr_startgame_scene_or_duplicate_guard")
    if "self.V_StartGame=true" in line:
        categories.append("mgr_startgame_state_set")
    if "RaiseEvent(DigitDoorType.EventType.OnStartGame)" in line:
        categories.append("mgr_onstartgame_event_raise")
    if "EventType.OnStartGame" in line and ("BinderEvent" in line or "AddEventHandler" in line):
        categories.append("onstartgame_listener_bind")
    if "_OnDigitDoorStartGame=function" in line:
        categories.append("onstartgame_callback_decl")
    if "DigitDoorEntityMgr.Inst_get():StartGame()" in line:
        categories.append("scene_entity_start_on_event")
    if "UpdateDigitDoorSkillInBattle()" in line:
        categories.append("fight_skill_update_on_event")
    if "ClearSkillCasterEmptyEff()" in line:
        categories.append("fight_clear_empty_caster_on_event")
    if "ReqStartGame()" in line:
        categories.append("ui_startgame_request_call")
    if "IsStartGame()" in line:
        categories.append("startgame_state_ui_guard")
    return categories


def _digitdoor_startgame_response_boundary_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_startgame_response_boundary_files(root, logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            for category in _classify_digitdoor_startgame_response_boundary_line(path, line, current_function):
                rows.append(
                    {
                        "category": category,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": _WHITESPACE_RE.sub(" ", line.strip()),
                    }
                )
    return rows


def _write_digitdoor_startgame_response_boundary_markdown(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
    logic_dir: Path,
) -> None:
    lines = [
        "# DigitDoor StartGame response boundary audit",
        "",
        "Static read-only audit for `SM_DigitDoorStartGame` field consumption and the local start-state/event boundary.",
        "",
        f"- Logic dir: `{logic_dir}`",
        "",
        "## Stats",
        "",
    ]
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Verdict", ""])
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            "- `SM_DigitDoorStartGame` declares `indexList` and `skillVos` as bean lists, but visible handler/downstream code does not read those fields.",
            "- The visible response boundary is `SM_DigitDoorStartGameFun -> DigitDoorMgr:DigitDoorStartGame(msg)`.",
            "- `DigitDoorMgr:DigitDoorStartGame` guards scene/duplicate state, sets `V_StartGame=true`, and raises `DigitDoorType.EventType.OnStartGame`.",
            "- Scene and fight components consume `OnStartGame` to hide lineup UI, start entities, clear/update skill casters, and enable drag/fight behavior.",
            "- Treat `SM_DigitDoorStartGame.indexList/skillVos` as server-returned schema fields with no visible static consumer on this Lua surface; use runtime samples or deeper native evidence before assigning semantics to them.",
            "",
            "## Evidence Samples",
            "",
        ]
    )
    for row in rows[:120]:
        lines.append(
            f"- `{row.get('category')}` {row.get('file')}:{row.get('line')} `{row.get('function')}` - `{row.get('snippet')}`"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_startgame_response_boundary_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    rows = _digitdoor_startgame_response_boundary_rows(root, logic_dir)
    category_counts = Counter(str(row.get("category") or "") for row in rows)
    stats = {
        "evidence_row_count": len(rows),
        "sm_startgame_indexlist_read_rows": category_counts.get("sm_startgame_indexlist_read", 0),
        "sm_startgame_skillvos_read_rows": category_counts.get("sm_startgame_skillvos_read", 0),
        "sm_startgame_indexlist_write_rows": category_counts.get("sm_startgame_indexlist_write", 0),
        "sm_startgame_skillvos_write_rows": category_counts.get("sm_startgame_skillvos_write", 0),
        "sm_startgame_handler_to_mgr_rows": category_counts.get("sm_startgame_handler_to_mgr", 0),
        "sm_startgame_indexlist_consumer_rows": category_counts.get("sm_startgame_indexlist_consumer", 0),
        "sm_startgame_skillvos_consumer_rows": category_counts.get("sm_startgame_skillvos_consumer", 0),
        "mgr_startgame_state_set_rows": category_counts.get("mgr_startgame_state_set", 0),
        "mgr_onstartgame_event_raise_rows": category_counts.get("mgr_onstartgame_event_raise", 0),
        "onstartgame_listener_bind_rows": category_counts.get("onstartgame_listener_bind", 0),
        "scene_entity_start_on_event_rows": category_counts.get("scene_entity_start_on_event", 0),
        "fight_skill_update_on_event_rows": category_counts.get("fight_skill_update_on_event", 0),
        "ui_startgame_request_call_rows": category_counts.get("ui_startgame_request_call", 0),
        "startgame_state_ui_guard_rows": category_counts.get("startgame_state_ui_guard", 0),
    }
    verdict = {
        "sm_startgame_declares_indexlist_and_skillvos": stats["sm_startgame_indexlist_read_rows"] > 0
        and stats["sm_startgame_skillvos_read_rows"] > 0,
        "visible_response_field_consumer_found": stats["sm_startgame_indexlist_consumer_rows"] > 0
        or stats["sm_startgame_skillvos_consumer_rows"] > 0,
        "visible_start_state_boundary_found": stats["mgr_startgame_state_set_rows"] > 0
        and stats["mgr_onstartgame_event_raise_rows"] > 0,
        "onstartgame_event_has_visible_consumers": stats["onstartgame_listener_bind_rows"] > 0,
        "scene_start_on_event_found": stats["scene_entity_start_on_event_rows"] > 0,
        "fight_update_on_event_found": stats["fight_skill_update_on_event_rows"] > 0,
        "authority_interpretation": "server_ack_gates_local_start_event",
    }
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_tsv = out_dir / "startgame_response_boundary_evidence.tsv"
    report_path = out_dir / "startgame_response_boundary_report.md"
    _write_tsv(rows_tsv, rows, ["category", "file", "line", "function", "snippet"])
    _write_digitdoor_startgame_response_boundary_markdown(
        report_path,
        rows=rows,
        stats=stats,
        verdict=verdict,
        logic_dir=logic_dir,
    )
    return {
        "confirmed": verdict["sm_startgame_declares_indexlist_and_skillvos"] and verdict["visible_start_state_boundary_found"],
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "rows": str(rows_tsv),
        },
    }


def _digitdoor_startgame_skillvos_shape_files(root: Path, logic_dir: Path) -> list[Path]:
    files = [
        logic_dir / "DigitDoorData.lua",
        *root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/SM_DigitDoorStartGame.lua"),
        *root.glob("by_source/lscripts/gamesystem/game/message_*/text_assets/DDSkillVo.lua"),
    ]
    unique: dict[str, Path] = {}
    for path in files:
        if path.is_file():
            unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _classify_digitdoor_startgame_skillvos_shape_line(path: Path, line: str) -> list[str]:
    categories: list[str] = []
    if path.name == "SM_DigitDoorStartGame.lua":
        if "self.skillVos=CList.new()" in line:
            categories.append("sm_startgame_skillvos_ctor")
        if "readMessageList2List(self.skillVos)" in line:
            categories.append("sm_startgame_skillvos_read")
        if "writeList(self.skillVos)" in line:
            categories.append("sm_startgame_skillvos_write_list")
    elif path.name == "DDSkillVo.lua":
        if "91604" in line:
            categories.append("dds_skill_vo_id")
        if re.search(r"\bself\.(id|num)\b", line):
            categories.append("dds_skill_vo_field")
    elif path.name == "DigitDoorData.lua":
        if "info.id" in line:
            categories.append("get_skill_name_list_info_id")
        if "info.value" in line:
            categories.append("get_skill_name_list_info_value")
        if "info.num" in line:
            categories.append("get_skill_name_list_info_num")
    if "msg.skillVos" in line:
        categories.append("msg_skillvos_consumer")
    return categories


def _digitdoor_startgame_skillvos_shape_rows(root: Path, logic_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _digitdoor_startgame_skillvos_shape_files(root, logic_dir):
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            for category in _classify_digitdoor_startgame_skillvos_shape_line(path, line):
                rows.append(
                    {
                        "category": category,
                        "file": _path_display(path, root),
                        "line": line_no,
                        "function": current_function,
                        "snippet": _WHITESPACE_RE.sub(" ", line.strip()),
                    }
                )
    return rows


def _write_digitdoor_startgame_skillvos_shape_markdown(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    stats: dict[str, Any],
    verdict: dict[str, Any],
    logic_dir: Path,
) -> None:
    lines = [
        "# DigitDoor StartGame skillVos shape audit",
        "",
        "Static read-only audit for `SM_DigitDoorStartGame.skillVos` and the adjacent `DDSkillVo` bean shape.",
        "",
        f"- Logic dir: `{logic_dir}`",
        "",
        "## Stats",
        "",
    ]
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Verdict", ""])
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            "- `SM_DigitDoorStartGame.skillVos` is serialized with `writeList`, so it is a bean-list field on the generated message class.",
            "- The adjacent DigitDoor VO candidate is `DDSkillVo(91604)` with fields `id,num`.",
            "- Visible business Lua has no direct `DDSkillVo` consumer and no visible `msg.skillVos` read in the StartGame response path.",
            "- `DigitDoorData:GetSkillNameList` uses `info.id/info.value`, not `info.num`, so the visible formatter shape does not exactly prove it consumes `DDSkillVo` rows.",
            "- Treat `skillVos` as a plausible server-returned skill-count bean list whose current Lua surface is unconsumed; close it with a privacy-filtered `SM_DigitDoorStartGame(91623)` runtime sample or deeper native evidence.",
            "",
            "## Evidence Samples",
            "",
        ]
    )
    for row in rows[:100]:
        lines.append(
            f"- `{row.get('category')}` {row.get('file')}:{row.get('line')} `{row.get('function')}` - `{row.get('snippet')}`"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_startgame_skillvos_shape_probe(
    *,
    digitdoor_logic_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    rows = _digitdoor_startgame_skillvos_shape_rows(root, logic_dir)
    usage_rows = _digitdoor_readyfight_ddskillvo_direct_usage_rows(root)
    all_rows = rows + usage_rows
    category_counts = Counter(str(row.get("category") or "") for row in all_rows)
    dds_fields = _digitdoor_readyfight_ddskillvo_fields(rows)
    stats = {
        "evidence_row_count": len(all_rows),
        "sm_startgame_skillvos_read_rows": category_counts.get("sm_startgame_skillvos_read", 0),
        "sm_startgame_skillvos_write_list_rows": category_counts.get("sm_startgame_skillvos_write_list", 0),
        "dds_skill_vo_id_rows": category_counts.get("dds_skill_vo_id", 0),
        "dds_skill_vo_field_count": len(dds_fields),
        "direct_ddskillvo_logic_usage_rows": category_counts.get("direct_ddskillvo_logic_usage", 0),
        "msg_skillvos_consumer_rows": category_counts.get("msg_skillvos_consumer", 0),
        "get_skill_name_list_info_id_rows": category_counts.get("get_skill_name_list_info_id", 0),
        "get_skill_name_list_info_value_rows": category_counts.get("get_skill_name_list_info_value", 0),
        "get_skill_name_list_info_num_rows": category_counts.get("get_skill_name_list_info_num", 0),
    }
    mismatch = "num" in dds_fields and stats["get_skill_name_list_info_value_rows"] > 0 and stats["get_skill_name_list_info_num_rows"] == 0
    verdict = {
        "sm_startgame_skillvos_is_bean_list": stats["sm_startgame_skillvos_write_list_rows"] > 0,
        "dds_skill_vo_found": stats["dds_skill_vo_id_rows"] > 0,
        "dds_skill_vo_fields": ",".join(dds_fields),
        "visible_ddskillvo_logic_usage_found": stats["direct_ddskillvo_logic_usage_rows"] > 0,
        "visible_msg_skillvos_consumer_found": stats["msg_skillvos_consumer_rows"] > 0,
        "get_skill_name_list_uses_info_value_not_num": mismatch,
        "shape_interpretation": "server_returned_ddskillvo_candidate_unconsumed",
    }
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_tsv = out_dir / "startgame_skillvos_shape_evidence.tsv"
    report_path = out_dir / "startgame_skillvos_shape_report.md"
    _write_tsv(rows_tsv, all_rows, ["category", "file", "line", "function", "snippet"])
    _write_digitdoor_startgame_skillvos_shape_markdown(
        report_path,
        rows=all_rows,
        stats=stats,
        verdict=verdict,
        logic_dir=logic_dir,
    )
    return {
        "confirmed": stats["sm_startgame_skillvos_write_list_rows"] > 0 and stats["dds_skill_vo_field_count"] == 2,
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "rows": str(rows_tsv),
        },
    }


def _parse_named_numeric_lua_table(text: str, table_name: str) -> dict[str, int]:
    match = re.search(rf"_M\.{re.escape(table_name)}\s*=\s*\{{(?P<body>.*?)\}}", text, flags=re.S)
    if not match:
        return {}
    values: dict[str, int] = {}
    for name, value in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?\d+)", match.group("body")):
        values[name] = int(value)
    return values


def _parse_digitdoor_buff_type_metadata(logic_dir: Path) -> dict[str, Any]:
    type_path = logic_dir / "DigitDoorType.lua"
    if not type_path.is_file():
        return {"types_by_id": {}, "targets_by_id": {}, "triggers_by_value": {}, "paths_by_id": {}, "source_file": ""}
    text = type_path.read_text(encoding="utf-8", errors="ignore")
    skill_buff_type = _parse_named_numeric_lua_table(text, "SkillBuffType")
    target_type = _parse_named_numeric_lua_table(text, "BuffTargetType")
    trigger_values = {
        name: value
        for name, value in re.findall(
            r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"([A-Z]+)\"",
            re.search(r"_M\.BuffTriggerType\s*=\s*\{(?P<body>.*?)\}", text, flags=re.S).group("body")
            if re.search(r"_M\.BuffTriggerType\s*=\s*\{(?P<body>.*?)\}", text, flags=re.S)
            else "",
        )
    }
    paths_by_id: dict[int, str] = {}
    for name, path in re.findall(r"\[_M\.SkillBuffType\.([A-Za-z_][A-Za-z0-9_]*)\]\s*=\s*\"([^\"]+)\"", text):
        type_id = skill_buff_type.get(name)
        if type_id is not None:
            paths_by_id[type_id] = path
    return {
        "types_by_id": {value: name for name, value in skill_buff_type.items()},
        "targets_by_id": {value: name for name, value in target_type.items()},
        "triggers_by_value": {value: name for name, value in trigger_values.items()},
        "paths_by_id": paths_by_id,
        "source_file": str(type_path),
    }


def _find_lscript_files_by_asset(root: Path) -> dict[str, list[Path]]:
    lscript_root = root / "by_source" / "lscripts"
    files_by_name: dict[str, list[Path]] = {}
    if not lscript_root.is_dir():
        return files_by_name
    for path in lscript_root.rglob("*.lua"):
        files_by_name.setdefault(path.name, []).append(path)
    for paths in files_by_name.values():
        paths.sort(key=lambda item: str(item))
    return files_by_name


def _find_digitdoor_timeline_lua_files(root: Path) -> tuple[dict[int, list[Path]], dict[int, list[Path]]]:
    lscript_root = root / "by_source" / "lscripts"
    all_named: dict[int, list[Path]] = {}
    digitdoor_named: dict[int, list[Path]] = {}
    if not lscript_root.is_dir():
        return all_named, digitdoor_named
    pattern = re.compile(r"^(?P<id>\d+)(?:__[-\d]+)?\.lua$")
    for path in lscript_root.rglob("*.lua"):
        match = pattern.match(path.name)
        if not match:
            continue
        timeline_id = int(match.group("id"))
        all_named.setdefault(timeline_id, []).append(path)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if DIGITDOOR_EFFECT_TYPE_REQUIRE in text:
            digitdoor_named.setdefault(timeline_id, []).append(path)
    for paths in all_named.values():
        paths.sort(key=lambda item: ("__" in item.name, str(item)))
    for paths in digitdoor_named.values():
        paths.sort(key=lambda item: ("__" in item.name, str(item)))
    return all_named, digitdoor_named


def _find_digitdoor_effect_class_map(root: Path) -> dict[str, str]:
    lscript_root = root / "by_source" / "lscripts"
    if not lscript_root.is_dir():
        return {}
    for path in lscript_root.rglob("DigitDoorEffectType*.lua"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = re.search(r"_M\.EffectClass\s*=\s*\{(?P<body>.*?)\}\s*_M\.", text, flags=re.S)
        if not match:
            continue
        return {
            key: value
            for key, value in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"([^\"]+)\"", match.group("body"))
        }
    return {}


def _parse_digitdoor_timeline_file(path: Path, effect_class_map: dict[str, str]) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    sections: list[str] = []
    effect_entries: list[dict[str, Any]] = []
    current_section = ""
    for line_no, line in enumerate(text.splitlines(), start=1):
        section_match = re.search(r"\[EffectType\.SkillEffect\.([A-Za-z_][A-Za-z0-9_]*)\]", line)
        if section_match:
            current_section = section_match.group(1)
            sections.append(current_section)
        class_match = re.search(r"class\s*=\s*EffectType\.EffectClass\.([A-Za-z_][A-Za-z0-9_]*)", line)
        if not class_match:
            continue
        class_key = class_match.group(1)
        effect_entries.append(
            {
                "line": line_no,
                "section": current_section,
                "class_key": class_key,
                "class_name": effect_class_map.get(class_key, class_key),
            }
        )
    return {
        "timeline_file": path,
        "sections": _dedupe_preserve(sections),
        "res_paths": _dedupe_preserve(re.findall(r"resPath\s*=\s*\"([^\"]+)\"", text)),
        "audios": _dedupe_preserve(re.findall(r"\baudio\s*=\s*(-?\d+)", text)),
        "effect_entries": effect_entries,
        "class_keys": _dedupe_preserve([entry["class_key"] for entry in effect_entries]),
        "class_names": _dedupe_preserve([entry["class_name"] for entry in effect_entries]),
    }


def _timeline_ids_for_monster_skill(row: dict[str, Any]) -> list[int]:
    timeline_id = _as_int(row.get("timeLineId"))
    return [timeline_id] if timeline_id is not None else []


def _pipe_join(values: list[Any]) -> str:
    return "|".join(str(item) for item in values if str(item or "").strip())


def _write_digitdoor_monster_skill_timeline_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    sample_skill_rows: list[dict[str, Any]],
    sample_timeline_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DigitDoor monster skill timeline link report",
        "",
        "Static read-only link map from `MonsterSkill.timeLineId` to `Generate.Timeline.DigitDoor.Config.<timelineId>` Lua configs and runtime effect classes.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Key Runtime Chain",
            "",
            "- `DigitDoorBotSkillActor:SkillCreator` reads `skillCfg.timeLineId` and calls `skill:AddSkillEffectClassPath(skillCfg.timeLineId)`.",
            "- `DigitDoorBaseSkill:AddSkillEffectClassPath` requires `Generate.Timeline.DigitDoor.Config.<timelineId>`, reads `EffectType.SkillEffect.*` buckets, and stores effect configs by phase.",
            "- Each timeline `class=EffectType.EffectClass.*` is expanded to `GameSystem.Game.DigitDoor.Core.Fight.SkillEffect.Effect.<class>` through `DigitDoorEffectType.EffectClass`.",
            "",
            "## Sample Skill Rows",
            "",
        ]
    )
    for row in sample_skill_rows[:20]:
        lines.append(
            f"- skill `{row.get('skill_id')}` timeline `{row.get('timeline_id')}` sections `{row.get('sections')}` classes `{row.get('effect_classes')}`"
        )
    lines.extend(["", "## Sample Timeline Rows", ""])
    for row in sample_timeline_rows[:20]:
        lines.append(
            f"- timeline `{row.get('timeline_id')}` files `{row.get('timeline_file_count')}` sections `{row.get('sections')}` classes `{row.get('effect_classes')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This report proves static client wiring from monster skill config to client-side visual/effect classes. It does not prove server-side authority or a modifiable runtime boundary.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


DIGITDOOR_EFFECT_FLOW_LABELS = {
    "entry": "入口",
    "guard": "条件/退出",
    "super_call": "父类调用",
    "damage_result": "伤害结算",
    "add_buff": "加Buff",
    "summon_monster": "召唤怪物",
    "move_speed": "移动/加速",
    "effect_create": "表现/特效",
    "targeting": "目标选择",
    "lifecycle": "生命周期",
    "data_access": "配置/数据",
}


DIGITDOOR_MONSTER_SKILL_ACCESSOR_META = {
    "GetHitTime": {
        "config_field": "hitTime",
        "source_data_class": "DigitDoorBotSkillData",
        "transform": "raw milliseconds; AttackAnim multiplies by 0.001 before V_Data:SetHitTime",
    },
    "GetDuration": {
        "config_field": "duration",
        "source_data_class": "DigitDoorBotSkillData",
        "transform": "cfg.duration >= 0 becomes seconds in InitData; negative values are preserved",
    },
    "GetDamage": {
        "config_field": "damage",
        "source_data_class": "DigitDoorBotSkillData",
        "transform": "raw config value",
    },
    "GetExtMoveSpeed": {
        "config_field": "speedUpValue",
        "source_data_class": "DigitDoorBotSkillData",
        "transform": "raw speed delta",
    },
    "GetSkillCD": {
        "config_field": "cd",
        "source_data_class": "DigitDoorBotSkillData",
        "transform": "raw milliseconds",
    },
    "GetMaxHitCount": {
        "config_field": "maxHit",
        "source_data_class": "DigitDoorBotSkillData",
        "transform": "raw count; defaults to 999",
    },
    "GetInterval": {
        "config_field": "timeDuration",
        "source_data_class": "DigitDoorBotSkillData",
        "transform": "cfg.timeDuration * 0.001 seconds",
    },
    "GetBulletSpeed": {
        "config_field": "bulletSpeed",
        "source_data_class": "DigitDoorBotSkillData",
        "transform": "cfg.bulletSpeed * 0.0001",
    },
    "GetBulletDuration": {
        "config_field": "bulletDuration",
        "source_data_class": "DigitDoorBotSkillData",
        "transform": "cfg.bulletDuration * 0.001 seconds",
    },
    "GetSummonId": {
        "config_field": "summonMonsterId",
        "source_data_class": "DigitDoorBotSkillData",
        "transform": "raw monster group id",
    },
    "GetSummonAttackPercent": {
        "config_field": "summonAttack",
        "source_data_class": "DigitDoorBotSkillData",
        "transform": "cfg.summonAttack * 0.0001",
    },
    "GetSummonHpPercent": {
        "config_field": "summonHp",
        "source_data_class": "DigitDoorBotSkillData",
        "transform": "cfg.summonHp * 0.0001",
    },
    "GetHpRecoverPercent": {
        "config_field": "hpRecover",
        "source_data_class": "DigitDoorBotSkillData",
        "transform": "cfg.hpRecover * 0.0001",
    },
    "GetExtCondition": {
        "config_field": "extCondition",
        "source_data_class": "DigitDoorAddBuffData",
        "transform": "direct value on AddBuffData; DigitDoorBotSkillData does not define this accessor",
    },
    "GetExtBuffValue": {
        "config_field": "extBuffValue",
        "source_data_class": "DigitDoorAddBuffData",
        "transform": "direct value on AddBuffData; DigitDoorBotSkillData does not define this accessor",
    },
}


def _extract_digitdoor_lua_function_blocks(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    starts: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        match = re.search(r"\bfunction\s+_M[.:]([A-Za-z0-9_]+)\s*\(([^)]*)\)", stripped)
        if not match:
            match = re.search(r"_M[.:]([A-Za-z0-9_]+)\s*=\s*function\s*\(([^)]*)\)", stripped)
        if not match:
            continue
        starts.append(
            {
                "name": match.group(1),
                "params": match.group(2),
                "start_index": index,
                "start_line": index + 1,
            }
        )
    blocks: list[dict[str, Any]] = []
    for i, start in enumerate(starts):
        end_index = starts[i + 1]["start_index"] if i + 1 < len(starts) else len(lines)
        block_lines = [
            {"line": line_no + 1, "code": lines[line_no].strip()}
            for line_no in range(start["start_index"], end_index)
            if lines[line_no].strip() and not lines[line_no].strip().startswith("--")
        ]
        blocks.append(
            {
                "function": start["name"],
                "params": start["params"],
                "start_line": start["start_line"],
                "end_line": end_index,
                "lines": block_lines,
            }
        )
    return blocks


def _classify_digitdoor_effect_flow_line(code: str) -> str | None:
    checks = [
        ("entry", r"^function\s+_M[.:]"),
        ("guard", r"\bif\s+not\b|\breturn\b"),
        ("super_call", r"_super_"),
        ("damage_result", r"AddDamageResult|DamageResult|DoHit|\bdamage\b|GetDamage", re.I),
        ("add_buff", r"AddBuff|buffId|buffList|BuffEffect|GetExtBuffValue|CheckHasBuffById"),
        ("summon_monster", r"Summon|CreateDigitDoorBotView|InitSummonData|AddSkillMonster|InsertObject|DeleteDigitDoorBotView"),
        ("move_speed", r"ExtMoveSpeed|SetRushMode|MoveSpeed|SpeedUp|RushMode"),
        ("effect_create", r"LoadEffect|CreateEffect|AdditionalEffect|V_ResPath|PlayElement|PlayAnimation|SetPauseAnimation"),
        ("targeting", r"targetView|targetId|GetDigitDoorPartnerView|GetDigitDoorBotView|casterView|EntityMgr"),
        ("lifecycle", r"DoStart|DoEnd|EffectUpdate|Destroy|startUpdate|SetLifeTime|GetDuration|GetHitTime|SetHitTime|hitTimer|bHit"),
        ("data_access", r"V_Data|V_Skill|skillData|DBMgr|ConfigName"),
    ]
    for item in checks:
        if len(item) == 2:
            category, pattern = item
            flags = 0
        else:
            category, pattern, flags = item
        if re.search(pattern, code, flags):
            return category
    return None


def _summarize_digitdoor_effect_flow_function(block: dict[str, Any]) -> dict[str, Any]:
    step_rows = [
        row
        for row in block.get("lines") or []
        if _classify_digitdoor_effect_flow_line(str(row.get("code") or "")) is not None
    ]
    text = "\n".join(str(row.get("code") or "") for row in block.get("lines") or [])
    calls = re.findall(r"\bself:([A-Za-z0-9_]+)\s*\(", text)
    calls.extend(re.findall(r"\b[A-Za-z0-9_]+:([A-Za-z0-9_]+)\s*\(", text))
    categories = [_classify_digitdoor_effect_flow_line(str(row.get("code") or "")) for row in step_rows]
    return {
        "function": block.get("function") or "",
        "params": block.get("params") or "",
        "start_line": block.get("start_line") or "",
        "end_line": block.get("end_line") or "",
        "step_count": len(step_rows),
        "categories": _join_digitdoor_unique_cell([category for category in categories if category]),
        "calls": _join_digitdoor_unique_cell(calls),
        "adds_damage_result": "AddDamageResult" in text,
        "adds_buff": "AddBuff" in text,
        "summons_monster": "Summon" in text or "CreateDigitDoorBotView" in text or "InitSummonData" in text,
        "changes_move_speed": "SetExtMoveSpeed" in text or "SetRushMode" in text,
        "creates_effect": "LoadEffect" in text or "CreateEffect" in text or "PlayAnimation" in text,
        "targets_entity": "targetView" in text or "targetId" in text or "GetDigitDoor" in text,
        "uses_timeline_data": "V_Data" in text or "SetLifeTime" in text or "GetHitTime" in text,
    }


def _digitdoor_effect_class_flow_hint(class_name: str, function_rows: list[dict[str, Any]]) -> str:
    function_by_name = {str(row.get("function") or ""): row for row in function_rows}
    if class_name == "DigitDoorAttackAnimEffect":
        return "命中伤害型：DoStart 设置 hitTime/lifeTime 并播放攻击动作；EffectUpdate 到命中点后按 targetId 找目标并调用 AddDamageResult。"
    if class_name == "DigitDoorSummonEffect":
        return "召唤型：DoStart 进入 SummonMonster，按施法者最大生命/攻击和技能百分比构造 DigitDoorBot，再创建 BotView 并加入战斗对象。"
    if class_name == "DigitDoorAddBuffEffect":
        return "加 Buff 型：DoStart 选择施法者或传入目标，遍历 skill.buffList，查 DigitDoor_BuffEffect 后调用 targetView:AddBuff。"
    if class_name == "DigitDoorSpeedUpEffect":
        return "移动强化型：DoStart 增加 ExtMoveSpeed 并开启 RushMode；DoEnd 用相反速度值恢复并关闭 RushMode。"
    if any(row.get("adds_damage_result") for row in function_rows):
        return "伤害结算型：类内存在 AddDamageResult 路径，需要结合 timeline 阶段和 skillData 判断命中时机。"
    if any(row.get("adds_buff") for row in function_rows):
        return "加 Buff 型：类内存在 AddBuff 路径，通常由 timeline 触发后把 BuffEffect 挂到目标。"
    if any(row.get("summons_monster") for row in function_rows):
        return "召唤型：类内存在召唤/创建 DigitDoorBotView 路径。"
    if any(row.get("changes_move_speed") for row in function_rows):
        return "移动状态型：类内会改变移动速度或 rush 状态。"
    if function_by_name.get("DoStart") or function_by_name.get("EffectUpdate"):
        return "timeline 效果型：类内有启动或更新逻辑，但当前静态片段未归类出伤害/Buff/召唤等强语义。"
    return "基础效果型：当前静态片段没有发现可归类的运行时动作。"


def _write_digitdoor_monster_effect_class_flow_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    class_rows: list[dict[str, Any]],
    function_rows: list[dict[str, Any]],
    step_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DigitDoor monster effect class flow report",
        "",
        "Static read-only control-flow summary for `DigitDoor*Effect` Lua classes referenced by monster skill timelines.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Class Flow Hints", ""])
    for row in class_rows:
        lines.append(
            f"- `{row.get('class_name')}` timelines `{row.get('timeline_ids')}` labels `{row.get('flow_labels')}`: {row.get('flow_hint')}"
        )
    lines.extend(["", "## Key Functions", ""])
    for row in function_rows:
        if not row.get("step_count"):
            continue
        lines.append(
            f"- `{row.get('class_name')}.{row.get('function')}` lines `{row.get('start_line')}-{row.get('end_line')}` "
            f"categories `{row.get('categories')}` calls `{row.get('calls')}`"
        )
    lines.extend(["", "## Step Samples", ""])
    for row in step_rows[:80]:
        lines.append(
            f"- `{row.get('class_name')}.{row.get('function')}` line `{row.get('line')}` `{row.get('category')}`: `{row.get('code')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This report describes visible client Lua effect flow only. It does not modify runtime state, prove server-side authority, or validate live combat outcomes.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _collect_buff_effect_rows(rows: list[dict[str, Any]], type_meta: dict[str, Any]) -> list[dict[str, Any]]:
    types_by_id = type_meta.get("types_by_id") or {}
    targets_by_id = type_meta.get("targets_by_id") or {}
    triggers_by_value = type_meta.get("triggers_by_value") or {}
    paths_by_id = type_meta.get("paths_by_id") or {}
    result: list[dict[str, Any]] = []
    for row in rows:
        type_id = _as_int(row.get("type"))
        target_type = _as_int(row.get("targetType"))
        trigger_type = str(row.get("triggerType") or "")
        result.append(
            {
                "id": row.get("id"),
                "type": type_id,
                "type_name": types_by_id.get(type_id, str(type_id or "")),
                "buff_path": paths_by_id.get(type_id, ""),
                "target_type": target_type,
                "target_type_name": targets_by_id.get(target_type, str(target_type or "")),
                "trigger_type": trigger_type,
                "trigger_type_name": triggers_by_value.get(trigger_type.split("_", 1)[0], trigger_type),
                "trigger_percent": row.get("triggerPercent"),
                "duration": row.get("duration"),
                "interval": row.get("interval"),
                "eff_type": row.get("effType"),
                "plies_limit": row.get("pliesLimit"),
                "damage": row.get("damage"),
                "add_attr": row.get("addAttr") or "",
                "shield": row.get("shield"),
                "slow_down": row.get("slowDown"),
                "buff_amplify": row.get("buffAmplify"),
                "timeline_id": row.get("timelineId"),
                "passive": row.get("passive"),
                "dead_boom": row.get("deadBoom"),
                "target_buff_check": ",".join(str(item) for item in _as_list(row.get("targetBuffCheck"))),
            }
        )
    return sorted(result, key=lambda item: _sort_value(item.get("id")))


def _summarize_buff_effect_types(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        type_id = _as_int(row.get("type"))
        if type_id is None:
            continue
        grouped.setdefault(type_id, []).append(row)
    result: list[dict[str, Any]] = []
    for type_id, type_rows in sorted(grouped.items(), key=lambda item: _sort_value(item[0])):
        result.append(
            {
                "type": type_id,
                "type_name": type_rows[0].get("type_name") or str(type_id),
                "count": len(type_rows),
                "buff_path": type_rows[0].get("buff_path") or "",
                "sample_ids": ",".join(str(row.get("id")) for row in type_rows[:10]),
            }
        )
    return result


def _summarize_buff_effect_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_names = [
        "target_type",
        "trigger_type",
        "duration",
        "interval",
        "eff_type",
        "plies_limit",
        "damage",
        "add_attr",
        "shield",
        "slow_down",
        "buff_amplify",
        "timeline_id",
        "passive",
        "dead_boom",
        "target_buff_check",
    ]
    result: list[dict[str, Any]] = []
    for field in field_names:
        nonzero_rows = [row for row in rows if _is_nonzero_effect_value(row.get(field))]
        examples = [f"{row.get('id')}:{row.get('type_name')}={row.get(field)}" for row in nonzero_rows[:8]]
        result.append(
            {
                "field": field,
                "nonzero_count": len(nonzero_rows),
                "example_count": len(examples),
                "examples": "; ".join(examples),
                "note": BUFF_EFFECT_FIELD_NOTES.get(field, ""),
            }
        )
    return result


def _summarize_buff_effect_lua_hits(hit_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary_rows: list[dict[str, Any]] = []
    for topic, terms in BUFF_EFFECT_TOPIC_TERMS.items():
        topic_hits = [row for row in hit_rows if row.get("topic") == topic]
        files = sorted({str(row.get("file") or "") for row in topic_hits if row.get("file")})
        summary_rows.append(
            {
                "topic": topic,
                "terms": ",".join(terms),
                "hit_count": len(topic_hits),
                "file_count": len(files),
                "sample_files": ", ".join(files[:6]),
            }
        )
    return summary_rows


def build_fanxiu_digitdoor_buff_effect_usage_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    digitdoor_logic_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None
    buff_rows_raw = _parse_config_rows(config_dir, "BuffEffect", resolved_lang_path, lang_map)
    type_meta = _parse_digitdoor_buff_type_metadata(logic_dir)
    buff_rows = _collect_buff_effect_rows(buff_rows_raw, type_meta)
    type_rows = _summarize_buff_effect_types(buff_rows)
    field_rows = _summarize_buff_effect_fields(buff_rows)
    hit_rows = _scan_lua_hits_for_topics(logic_dir, root, BUFF_EFFECT_TOPIC_TERMS)
    topic_rows = _summarize_buff_effect_lua_hits(hit_rows)
    topic_counts = {row["topic"]: row["hit_count"] for row in topic_rows}
    required_topics = ["buff_config_lookup", "buff_type_to_class", "add_buff_application", "buff_base_init"]
    confirmed = bool(buff_rows) and all(topic_counts.get(topic, 0) > 0 for topic in required_topics)

    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    config_tsv = out_dir / "buff_effect_usage_config.tsv"
    type_tsv = out_dir / "buff_effect_usage_types.tsv"
    field_tsv = out_dir / "buff_effect_usage_fields.tsv"
    hit_tsv = out_dir / "buff_effect_usage_lua_hits.tsv"
    topic_tsv = out_dir / "buff_effect_usage_topics.tsv"
    report_path = out_dir / "buff_effect_usage_report.md"

    _write_tsv(
        config_tsv,
        buff_rows,
        [
            "id",
            "type",
            "type_name",
            "buff_path",
            "target_type",
            "target_type_name",
            "trigger_type",
            "trigger_type_name",
            "trigger_percent",
            "duration",
            "interval",
            "eff_type",
            "plies_limit",
            "damage",
            "add_attr",
            "shield",
            "slow_down",
            "buff_amplify",
            "timeline_id",
            "passive",
            "dead_boom",
            "target_buff_check",
        ],
    )
    _write_tsv(type_tsv, type_rows, ["type", "type_name", "count", "buff_path", "sample_ids"])
    _write_tsv(field_tsv, field_rows, ["field", "nonzero_count", "example_count", "examples", "note"])
    _write_tsv(hit_tsv, hit_rows, ["topic", "file", "line", "function", "matched_terms", "snippet"])
    _write_tsv(topic_tsv, topic_rows, ["topic", "terms", "hit_count", "file_count", "sample_files"])

    top_types = sorted(type_rows, key=lambda item: (-int(item.get("count") or 0), _sort_value(item.get("type"))))[:8]
    report_lines = [
        "# DigitDoor BuffEffect 使用链路",
        "",
        f"- 配置目录：`{config_dir}`",
        f"- Lua 逻辑目录：`{logic_dir}`",
        f"- BuffEffect 配置行：{len(buff_rows)}",
        f"- Buff 类型数：{len(type_rows)}",
        f"- Lua 命中行：{len(hit_rows)}",
        "",
        "## 当前结论",
        "",
        "- `BuffEffect.type` 先映射到 `DigitDoorType.SkillBuffType`，再通过 `DigitDoorType.BuffPath` 选择具体 Buff 类；找不到路径时 `DigitDoorPartnerView:AddBuff` 回退到基础 Buff。",
        "- `DigitDoorPartnerView:AddBuff` 负责同 id Buff 的叠层/刷新、实例化 Buff 类、调用 `InitData/Start`，并把 Buff 按 type 存进 `BuffDic`。",
        "- `DigitDoorBuffBase:InitData` 把 `type/targetType/triggerType/duration/timelineId/passive/pliesLimit` 等基础字段写入运行态；`Start/Update` 处理表现 timeline、立即触发和生命周期移除。",
        "- `DigitDoorBaseSkill:AddBuffData` 对 `EffectStrength` / `SkillDamageStrength` 两类 Buff 做特殊处理，分别写入 buff 效果增强和技能伤害增强；self passive Buff 会立即加到施法者身上。",
        "",
        "## 类型分布",
        "",
        "| type | 名称 | 行数 | Buff 类 |",
        "| ---: | --- | ---: | --- |",
    ]
    for row in top_types:
        report_lines.append(f"| {row['type']} | `{row['type_name']}` | {row['count']} | `{row['buff_path']}` |")
    report_lines.extend(
        [
            "",
            "## 字段分布",
            "",
            "| 字段 | 非零配置数 | 逆向语义 |",
            "| --- | ---: | --- |",
        ]
    )
    for row in field_rows:
        report_lines.append(f"| `{row['field']}` | {row['nonzero_count']} | {row['note']} |")
    report_lines.extend(
        [
            "",
            "## Lua 证据主题",
            "",
            "| 主题 | 命中行 | 文件数 | 关键词 |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    for row in topic_rows:
        report_lines.append(f"| `{row['topic']}` | {row['hit_count']} | {row['file_count']} | `{row['terms']}` |")
    report_lines.extend(
        [
            "",
            "## 输出文件",
            "",
            "- `buff_effect_usage_config.tsv`：BuffEffect 配置行和枚举名称。",
            "- `buff_effect_usage_types.tsv`：按 SkillBuffType 聚合。",
            "- `buff_effect_usage_fields.tsv`：按字段统计非零配置和语义。",
            "- `buff_effect_usage_lua_hits.tsv`：逐行 Lua 证据。",
            "- `buff_effect_usage_topics.tsv`：按主题聚合的 Lua 命中。",
        ]
    )
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    return {
        "confirmed": confirmed,
        "output_dir": str(out_dir),
        "counts": {
            "buff_effect_rows": len(buff_rows),
            "buff_type_rows": len(type_rows),
            "lua_hit_rows": len(hit_rows),
            "topics": topic_counts,
            "types": {str(row["type"]): row["count"] for row in type_rows},
            "fields": {row["field"]: row["nonzero_count"] for row in field_rows},
        },
        "files": {
            "report": str(report_path),
            "config_tsv": str(config_tsv),
            "types_tsv": str(type_tsv),
            "fields_tsv": str(field_tsv),
            "lua_hits_tsv": str(hit_tsv),
            "topics_tsv": str(topic_tsv),
        },
    }


def _count_nonzero_raw_config(rows: list[dict[str, Any]], field: str) -> int:
    if field == "triggerPercent + triggerBuffId + targetBuffCheck":
        return sum(
            1
            for row in rows
            if _is_nonzero_effect_value(row.get("triggerPercent"))
            or _is_nonzero_effect_value(row.get("triggerBuffId"))
            or _is_nonzero_effect_value(row.get("targetBuffCheck"))
        )
    if field == "effType + pliesLimit":
        return sum(
            1
            for row in rows
            if _is_nonzero_effect_value(row.get("effType")) or _is_nonzero_effect_value(row.get("pliesLimit"))
        )
    return sum(1 for row in rows if _is_nonzero_effect_value(row.get(field)))


def _format_config_examples(rows: list[dict[str, Any]], field: str, type_meta: dict[str, Any], limit: int = 8) -> str:
    types_by_id = type_meta.get("types_by_id") or {}
    examples: list[str] = []
    for row in sorted(rows, key=lambda item: _sort_value(item.get("id"))):
        values: list[str] = []
        if field == "triggerPercent + triggerBuffId + targetBuffCheck":
            for source_field in ("triggerPercent", "triggerBuffId", "targetBuffCheck"):
                if _is_nonzero_effect_value(row.get(source_field)):
                    values.append(f"{source_field}={row.get(source_field)}")
        elif field == "effType + pliesLimit":
            for source_field in ("effType", "pliesLimit"):
                if _is_nonzero_effect_value(row.get(source_field)):
                    values.append(f"{source_field}={row.get(source_field)}")
        elif _is_nonzero_effect_value(row.get(field)):
            values.append(f"{field}={row.get(field)}")
        if not values:
            continue
        type_name = types_by_id.get(_as_int(row.get("type")), str(row.get("type") or ""))
        examples.append(f"{row.get('id')}:{type_name}({', '.join(values)})")
        if len(examples) >= limit:
            break
    return "; ".join(examples)


def _summarize_buff_class_formula_topics(hit_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary_rows: list[dict[str, Any]] = []
    for topic, terms in BUFF_CLASS_FORMULA_TOPIC_TERMS.items():
        topic_hits = [row for row in hit_rows if row.get("topic") == topic]
        files = sorted({str(row.get("file") or "") for row in topic_hits if row.get("file")})
        summary_rows.append(
            {
                "topic": topic,
                "terms": ",".join(terms),
                "hit_count": len(topic_hits),
                "file_count": len(files),
                "sample_files": ", ".join(files[:6]),
            }
        )
    return summary_rows


def _build_buff_class_formula_rows(rows: list[dict[str, Any]], type_meta: dict[str, Any]) -> list[dict[str, Any]]:
    formula_rows: list[dict[str, Any]] = []
    for item in BUFF_CLASS_FORMULA_ROWS:
        formula_rows.append(
            {
                **item,
                "nonzero_config_count": _count_nonzero_raw_config(rows, item["field"]),
                "examples": _format_config_examples(rows, item["field"], type_meta),
            }
        )
    return formula_rows


def build_fanxiu_digitdoor_buff_class_formula_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    digitdoor_logic_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None
    buff_rows_raw = _parse_config_rows(config_dir, "BuffEffect", resolved_lang_path, lang_map)
    type_meta = _parse_digitdoor_buff_type_metadata(logic_dir)
    formula_rows = _build_buff_class_formula_rows(buff_rows_raw, type_meta)
    hit_rows = _scan_lua_hits_for_topics(logic_dir, root, BUFF_CLASS_FORMULA_TOPIC_TERMS)
    topic_rows = _summarize_buff_class_formula_topics(hit_rows)
    topic_counts = {row["topic"]: row["hit_count"] for row in topic_rows}
    confirmed = bool(buff_rows_raw) and all(topic_counts.get(topic, 0) > 0 for topic in ["buff_data_init", "add_attr_aggregation", "shield_formula"])

    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    formula_tsv = out_dir / "buff_class_formula_fields.tsv"
    source_tsv = out_dir / "buff_class_formula_sources.tsv"
    hit_tsv = out_dir / "buff_class_formula_lua_hits.tsv"
    topic_tsv = out_dir / "buff_class_formula_topics.tsv"
    report_path = out_dir / "buff_class_formula_report.md"

    _write_tsv(
        formula_tsv,
        formula_rows,
        ["field", "runtime_slot", "consumer", "formula", "meaning", "topics", "nonzero_config_count", "examples"],
    )
    _write_tsv(source_tsv, BUFF_CLASS_CANONICAL_SOURCES, ["source", "role", "summary"])
    _write_tsv(hit_tsv, hit_rows, ["topic", "file", "line", "function", "matched_terms", "snippet"])
    _write_tsv(topic_tsv, topic_rows, ["topic", "terms", "hit_count", "file_count", "sample_files"])

    report_lines = [
        "# DigitDoor Buff 类字段公式",
        "",
        f"- 配置目录：`{config_dir}`",
        f"- Lua 逻辑目录：`{logic_dir}`",
        f"- BuffEffect 配置行：{len(buff_rows_raw)}",
        f"- 公式字段：{len(formula_rows)}",
        f"- Lua 公式证据命中：{len(hit_rows)}",
        "",
        "## 当前结论",
        "",
        "- `DigitDoorBuffData:InitData` 是 BuffEffect 数值转换中心：持续时间、伤害、护盾、周期、减速、触发概率、触发 Buff、低血线和 addAttr 都先写入加密运行槽。",
        "- `strengthVal` 来自技能增强链路，可按 `1 + strengthVal * 0.0001` 放大 `damage/shield/addAttr` 这几类效果。",
        "- `DigitDoorPartnerView` 汇总 `AddAttr/Shield/Injure`：AddAttr 会按 Buff 层数相乘后求和，Shield 会按 `maxHp * shieldRatio * 0.0001` 得到当前护盾值。",
        "- `TriggerPercentBuff` 使用 `1..10000` 概率门，配合 `triggerBuffId/targetBuffCheck/TriggerSkillDic` 实现命中、受击或低血触发额外 Buff。",
        "",
        "## 字段公式",
        "",
        "| 字段 | 非零配置数 | 运行槽 | 公式/规则 |",
        "| --- | ---: | --- | --- |",
    ]
    for row in formula_rows:
        report_lines.append(f"| `{row['field']}` | {row['nonzero_config_count']} | `{row['runtime_slot']}` | {row['formula']} |")
    report_lines.extend(
        [
            "",
            "## 关键源码入口",
            "",
            "| 文件 | 角色 | 摘要 |",
            "| --- | --- | --- |",
        ]
    )
    for row in BUFF_CLASS_CANONICAL_SOURCES:
        report_lines.append(f"| `{row['source']}` | {row['role']} | {row['summary']} |")
    report_lines.extend(
        [
            "",
            "## Lua 证据主题",
            "",
            "| 主题 | 命中行 | 文件数 | 关键词 |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    for row in topic_rows:
        report_lines.append(f"| `{row['topic']}` | {row['hit_count']} | {row['file_count']} | `{row['terms']}` |")
    report_lines.extend(
        [
            "",
            "## 输出文件",
            "",
            "- `buff_class_formula_fields.tsv`：字段、运行槽、公式、配置例子。",
            "- `buff_class_formula_sources.tsv`：关键源码入口说明。",
            "- `buff_class_formula_lua_hits.tsv`：逐行 Lua 公式证据。",
            "- `buff_class_formula_topics.tsv`：按公式主题聚合的 Lua 证据。",
        ]
    )
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    return {
        "confirmed": confirmed,
        "output_dir": str(out_dir),
        "counts": {
            "buff_effect_rows": len(buff_rows_raw),
            "formula_rows": len(formula_rows),
            "lua_hit_rows": len(hit_rows),
            "topics": topic_counts,
            "fields": {row["field"]: row["nonzero_config_count"] for row in formula_rows},
        },
        "files": {
            "report": str(report_path),
            "formula_tsv": str(formula_tsv),
            "sources_tsv": str(source_tsv),
            "lua_hits_tsv": str(hit_tsv),
            "topics_tsv": str(topic_tsv),
        },
    }


def build_fanxiu_digitdoor_monster_skill_timeline_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None

    monster_skill_rows = _parse_config_rows(config_dir, "MonsterSkill", resolved_lang_path, lang_map)
    all_timeline_files, digitdoor_timeline_files = _find_digitdoor_timeline_lua_files(root)
    effect_class_map = _find_digitdoor_effect_class_map(root)
    files_by_asset = _find_lscript_files_by_asset(root)

    requested_timeline_ids = sorted({_as_int(row.get("timeLineId")) for row in monster_skill_rows} - {None})
    timeline_rows: list[dict[str, Any]] = []
    effect_rows: list[dict[str, Any]] = []
    timeline_summary_by_id: dict[int, dict[str, Any]] = {}
    exact_collision_ids: set[int] = set()
    for timeline_id in requested_timeline_ids:
        files = digitdoor_timeline_files.get(int(timeline_id), [])
        if any(path.name == f"{timeline_id}.lua" for path in all_timeline_files.get(int(timeline_id), [])) and not any(
            path.name == f"{timeline_id}.lua" for path in files
        ):
            exact_collision_ids.add(int(timeline_id))
        parsed_files = [_parse_digitdoor_timeline_file(path, effect_class_map) for path in files]
        class_names = _dedupe_preserve([class_name for item in parsed_files for class_name in item["class_names"]])
        sections = _dedupe_preserve([section for item in parsed_files for section in item["sections"]])
        res_paths = _dedupe_preserve([res_path for item in parsed_files for res_path in item["res_paths"]])
        audios = _dedupe_preserve([audio for item in parsed_files for audio in item["audios"]])
        timeline_row = {
            "timeline_id": timeline_id,
            "timeline_file_count": len(files),
            "timeline_files": _pipe_join([path.relative_to(root) for path in files]),
            "has_exact_digitdoor_file": any(path.name == f"{timeline_id}.lua" for path in files),
            "has_exact_non_digitdoor_collision": int(timeline_id) in exact_collision_ids,
            "sections": _pipe_join(sections),
            "effect_classes": _pipe_join(class_names),
            "effect_class_file_count": sum(len(files_by_asset.get(f"{class_name}.lua", [])) for class_name in class_names),
            "res_paths": _pipe_join(res_paths[:12]),
            "audios": _pipe_join(audios[:12]),
        }
        timeline_rows.append(timeline_row)
        timeline_summary_by_id[int(timeline_id)] = timeline_row
        for parsed in parsed_files:
            for entry in parsed["effect_entries"]:
                class_name = entry["class_name"]
                effect_files = files_by_asset.get(f"{class_name}.lua", [])
                effect_rows.append(
                    {
                        "timeline_id": timeline_id,
                        "timeline_file": str(parsed["timeline_file"].relative_to(root)),
                        "line": entry["line"],
                        "section": entry["section"],
                        "class_key": entry["class_key"],
                        "class_name": class_name,
                        "effect_files": _pipe_join([path.relative_to(root) for path in effect_files[:6]]),
                        "effect_file_count": len(effect_files),
                    }
                )

    skill_link_rows: list[dict[str, Any]] = []
    for row in sorted(monster_skill_rows, key=lambda item: _sort_value(item.get("id"))):
        timeline_ids = _timeline_ids_for_monster_skill(row)
        linked_timeline_rows = [timeline_summary_by_id.get(timeline_id, {}) for timeline_id in timeline_ids]
        effect_classes = _dedupe_preserve(
            [
                class_name
                for timeline_row in linked_timeline_rows
                for class_name in str(timeline_row.get("effect_classes") or "").split("|")
                if class_name
            ]
        )
        sections = _dedupe_preserve(
            [
                section
                for timeline_row in linked_timeline_rows
                for section in str(timeline_row.get("sections") or "").split("|")
                if section
            ]
        )
        skill_type = _as_int(row.get("type"))
        trigger_type = _as_int(row.get("trigger"))
        skill_link_rows.append(
            {
                "skill_id": row.get("id"),
                "timeline_id": row.get("timeLineId") or "",
                "missing_timeline_id": "" if not timeline_ids or timeline_ids[0] in timeline_summary_by_id else row.get("timeLineId"),
                "type": row.get("type") or "",
                "type_name": MONSTER_SKILL_TYPE_LABELS.get(skill_type or -1, ""),
                "trigger": row.get("trigger") or "",
                "trigger_name": SKILL_RELEASE_TYPE_LABELS.get(trigger_type or -1, ""),
                "sections": _pipe_join(sections),
                "effect_classes": _pipe_join(effect_classes),
                "effect_class_count": len(effect_classes),
                "timeline_files": _pipe_join([timeline_row.get("timeline_files") for timeline_row in linked_timeline_rows]),
                "buff_id": row.get("buffId") or "",
                "damage": row.get("damage") or "",
                "cd": row.get("cd") or "",
                "duration": row.get("duration") or "",
                "hit_time": row.get("hitTime") or "",
                "distance": row.get("distance") or "",
            }
        )

    stats = {
        "monster_skill_row_count": len(monster_skill_rows),
        "requested_timeline_count": len(requested_timeline_ids),
        "timeline_found_count": sum(1 for row in timeline_rows if row["timeline_file_count"]),
        "timeline_missing_count": sum(1 for row in timeline_rows if not row["timeline_file_count"]),
        "effect_entry_count": len(effect_rows),
        "effect_class_count": len({row["class_name"] for row in effect_rows}),
        "effect_class_map_count": len(effect_class_map),
        "exact_non_digitdoor_collision_count": len(exact_collision_ids),
        "skills_with_buff_id": sum(1 for row in monster_skill_rows if row.get("buffId")),
    }
    verdict = {
        "all_monster_skill_timelines_have_digitdoor_config": stats["timeline_missing_count"] == 0,
        "effect_class_map_found": bool(effect_class_map),
        "timeline_effect_entries_parsed": bool(effect_rows) if requested_timeline_ids else True,
        "static_client_effect_wiring_only": True,
    }

    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    skill_tsv = out_dir / "monster_skill_timeline_links.tsv"
    timeline_tsv = out_dir / "monster_skill_timeline_timelines.tsv"
    effect_tsv = out_dir / "monster_skill_timeline_effects.tsv"
    report_path = out_dir / "monster_skill_timeline_report.md"
    json_path = out_dir / "monster_skill_timeline_report.json"
    _write_tsv(
        skill_tsv,
        skill_link_rows,
        [
            "skill_id",
            "timeline_id",
            "missing_timeline_id",
            "type",
            "type_name",
            "trigger",
            "trigger_name",
            "sections",
            "effect_classes",
            "effect_class_count",
            "timeline_files",
            "buff_id",
            "damage",
            "cd",
            "duration",
            "hit_time",
            "distance",
        ],
    )
    _write_tsv(
        timeline_tsv,
        timeline_rows,
        [
            "timeline_id",
            "timeline_file_count",
            "timeline_files",
            "has_exact_digitdoor_file",
            "has_exact_non_digitdoor_collision",
            "sections",
            "effect_classes",
            "effect_class_file_count",
            "res_paths",
            "audios",
        ],
    )
    _write_tsv(
        effect_tsv,
        effect_rows,
        ["timeline_id", "timeline_file", "line", "section", "class_key", "class_name", "effect_files", "effect_file_count"],
    )
    _write_digitdoor_monster_skill_timeline_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        sample_skill_rows=skill_link_rows,
        sample_timeline_rows=timeline_rows,
    )
    files = {
        "skill_links": str(skill_tsv),
        "timelines": str(timeline_tsv),
        "effects": str(effect_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    json_path.write_text(
        json.dumps(
            {
                "confirmed": all(verdict.values()),
                "source": {
                    "digitdoor_config_dir": str(config_dir),
                    "lang_path": str(resolved_lang_path or ""),
                },
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "skill_links": skill_link_rows[:160],
                    "timelines": timeline_rows[:160],
                    "effects": effect_rows[:240],
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": all(verdict.values()),
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


def build_fanxiu_digitdoor_monster_effect_class_flow_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
    effect_classes: list[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    timeline_result = build_fanxiu_digitdoor_monster_skill_timeline_probe(
        digitdoor_config_dir=digitdoor_config_dir,
        lang_path=lang_path,
        export_root=root,
    )
    effect_rows = _read_tsv_dicts(Path(timeline_result["files"]["effects"]))
    requested = {item.strip() for item in effect_classes or [] if item and item.strip()}
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in effect_rows:
        class_name = row.get("class_name") or ""
        if not class_name or (requested and class_name not in requested):
            continue
        grouped.setdefault(class_name, []).append(row)

    files_by_asset = _find_lscript_files_by_asset(root)
    class_rows: list[dict[str, Any]] = []
    function_rows: list[dict[str, Any]] = []
    step_rows: list[dict[str, Any]] = []
    missing_sources: list[str] = []
    for class_name, rows in sorted(grouped.items()):
        candidate_paths: list[Path] = []
        for row in rows:
            for rel_path in _split_digitdoor_pipe_text(row.get("effect_files")):
                candidate = root / rel_path
                if candidate.is_file():
                    candidate_paths.append(candidate)
        if not candidate_paths:
            candidate_paths.extend(files_by_asset.get(f"{class_name}.lua", []))
        source_path = _dedupe_preserve(candidate_paths)[0] if candidate_paths else None
        timeline_ids = _join_digitdoor_unique_cell([row.get("timeline_id") for row in rows])
        sections = _join_digitdoor_unique_cell([row.get("section") for row in rows])
        class_keys = _join_digitdoor_unique_cell([row.get("class_key") for row in rows])
        if not source_path or not source_path.is_file():
            missing_sources.append(class_name)
            class_rows.append(
                {
                    "class_name": class_name,
                    "timeline_ids": timeline_ids,
                    "sections": sections,
                    "class_keys": class_keys,
                    "source_file": "",
                    "function_count": 0,
                    "flow_step_count": 0,
                    "flow_categories": "",
                    "flow_labels": "",
                    "flow_hint": "source file missing",
                }
            )
            continue

        text = source_path.read_text(encoding="utf-8", errors="ignore")
        blocks = _extract_digitdoor_lua_function_blocks(text)
        current_function_rows: list[dict[str, Any]] = []
        current_step_rows: list[dict[str, Any]] = []
        source_rel = str(source_path.relative_to(root))
        for block in blocks:
            summary = _summarize_digitdoor_effect_flow_function(block)
            summary.update(
                {
                    "class_name": class_name,
                    "timeline_ids": timeline_ids,
                    "sections": sections,
                    "class_keys": class_keys,
                    "source_file": source_rel,
                }
            )
            current_function_rows.append(summary)
            order = 0
            for line_row in block.get("lines") or []:
                code = str(line_row.get("code") or "")
                category = _classify_digitdoor_effect_flow_line(code)
                if not category:
                    continue
                order += 1
                current_step_rows.append(
                    {
                        "class_name": class_name,
                        "function": block.get("function") or "",
                        "line": line_row.get("line") or "",
                        "step_order": order,
                        "category": category,
                        "label": DIGITDOOR_EFFECT_FLOW_LABELS.get(category, category),
                        "code": code[:260],
                        "source_file": source_rel,
                    }
                )
        function_rows.extend(current_function_rows)
        step_rows.extend(current_step_rows)
        categories = _join_digitdoor_unique_cell([step.get("category") for step in current_step_rows])
        class_rows.append(
            {
                "class_name": class_name,
                "timeline_ids": timeline_ids,
                "sections": sections,
                "class_keys": class_keys,
                "source_file": source_rel,
                "function_count": len(blocks),
                "flow_step_count": len(current_step_rows),
                "flow_categories": categories,
                "flow_labels": _join_digitdoor_unique_cell(
                    [DIGITDOOR_EFFECT_FLOW_LABELS.get(category, category) for category in _split_digitdoor_pipe_text(categories)]
                ),
                "flow_hint": _digitdoor_effect_class_flow_hint(class_name, current_function_rows),
            }
        )

    category_counts = Counter(str(row.get("category") or "") for row in step_rows if row.get("category"))
    stats = {
        "effect_entry_count": len(effect_rows),
        "requested_class_count": len(requested),
        "selected_class_count": len(grouped),
        "class_file_missing_count": len(missing_sources),
        "function_count": len(function_rows),
        "flow_step_count": len(step_rows),
        "classes_with_damage_result": sum(
            1 for row in class_rows if "damage_result" in _split_digitdoor_cell_values(row.get("flow_categories"))
        ),
        "classes_with_add_buff": sum(1 for row in class_rows if "add_buff" in _split_digitdoor_cell_values(row.get("flow_categories"))),
        "classes_with_summon": sum(
            1 for row in class_rows if "summon_monster" in _split_digitdoor_cell_values(row.get("flow_categories"))
        ),
        "classes_with_move_speed": sum(1 for row in class_rows if "move_speed" in _split_digitdoor_cell_values(row.get("flow_categories"))),
        "step_category_counts": dict(sorted(category_counts.items())),
    }
    verdict = {
        "all_selected_effect_classes_have_source": stats["class_file_missing_count"] == 0,
        "has_effect_class_flow": stats["flow_step_count"] > 0,
        "has_damage_or_mutation_flow": stats["classes_with_damage_result"] > 0
        or stats["classes_with_add_buff"] > 0
        or stats["classes_with_summon"] > 0
        or stats["classes_with_move_speed"] > 0,
        "static_client_effect_flow_only": True,
    }

    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    class_tsv = out_dir / "monster_effect_class_flow_classes.tsv"
    function_tsv = out_dir / "monster_effect_class_flow_functions.tsv"
    step_tsv = out_dir / "monster_effect_class_flow_steps.tsv"
    report_path = out_dir / "monster_effect_class_flow_report.md"
    json_path = out_dir / "monster_effect_class_flow_report.json"
    _write_tsv(
        class_tsv,
        class_rows,
        [
            "class_name",
            "timeline_ids",
            "sections",
            "class_keys",
            "source_file",
            "function_count",
            "flow_step_count",
            "flow_categories",
            "flow_labels",
            "flow_hint",
        ],
    )
    _write_tsv(
        function_tsv,
        function_rows,
        [
            "class_name",
            "timeline_ids",
            "sections",
            "class_keys",
            "function",
            "params",
            "start_line",
            "end_line",
            "step_count",
            "categories",
            "calls",
            "adds_damage_result",
            "adds_buff",
            "summons_monster",
            "changes_move_speed",
            "creates_effect",
            "targets_entity",
            "uses_timeline_data",
            "source_file",
        ],
    )
    _write_tsv(step_tsv, step_rows, ["class_name", "function", "line", "step_order", "category", "label", "code", "source_file"])
    _write_digitdoor_monster_effect_class_flow_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        class_rows=class_rows,
        function_rows=function_rows,
        step_rows=step_rows,
    )
    files = {
        "classes": str(class_tsv),
        "functions": str(function_tsv),
        "steps": str(step_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    json_path.write_text(
        json.dumps(
            {
                "confirmed": all(verdict.values()),
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "classes": class_rows[:80],
                    "functions": function_rows[:160],
                    "steps": step_rows[:240],
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": all(verdict.values()),
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


def _digitdoor_accessor_runtime_preview(accessor: str, raw_value: Any) -> str:
    parsed = _as_int(raw_value)
    if raw_value in (None, ""):
        return ""
    if accessor in {"GetSummonAttackPercent", "GetSummonHpPercent", "GetHpRecoverPercent", "GetBulletSpeed"} and parsed is not None:
        return f"{parsed * 0.0001:g}"
    if accessor in {"GetInterval", "GetBulletDuration"} and parsed is not None:
        return f"{parsed * 0.001:g}s"
    if accessor == "GetDuration" and parsed is not None:
        return f"{parsed * 0.001:g}s" if parsed >= 0 else str(parsed)
    return str(raw_value)


def _find_digitdoor_skill_data_source_files(root: Path) -> dict[str, Path]:
    files_by_asset = _find_lscript_files_by_asset(root)
    result: dict[str, Path] = {}
    for class_name in {str(meta["source_data_class"]) for meta in DIGITDOOR_MONSTER_SKILL_ACCESSOR_META.values()}:
        candidates = files_by_asset.get(f"{class_name}.lua", [])
        if candidates:
            result[class_name] = candidates[0]
    return result


def _find_digitdoor_effect_skill_data_refs(root: Path, effect_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    files_by_asset = _find_lscript_files_by_asset(root)
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in effect_rows:
        class_name = row.get("class_name") or ""
        if class_name:
            grouped.setdefault(class_name, []).append(row)
    refs: list[dict[str, Any]] = []
    for class_name, rows in sorted(grouped.items()):
        candidate_paths: list[Path] = []
        for row in rows:
            for rel_path in _split_digitdoor_pipe_text(row.get("effect_files")):
                candidate = root / rel_path
                if candidate.is_file():
                    candidate_paths.append(candidate)
        if not candidate_paths:
            candidate_paths.extend(files_by_asset.get(f"{class_name}.lua", []))
        source_path = _dedupe_preserve(candidate_paths)[0] if candidate_paths else None
        if not source_path or not source_path.is_file():
            continue
        source_rel = str(source_path.relative_to(root))
        current_function = ""
        for line_no, line in enumerate(source_path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            if match := re.search(r"\bfunction\s+_M[.:]([A-Za-z0-9_]+)\s*\(", line.strip()):
                current_function = match.group(1)
            for accessor in re.findall(r"skillData:([A-Za-z0-9_]+)\s*\(", line):
                meta = DIGITDOOR_MONSTER_SKILL_ACCESSOR_META.get(accessor, {})
                refs.append(
                    {
                        "class_name": class_name,
                        "function": current_function,
                        "line": line_no,
                        "accessor": accessor,
                        "config_field": meta.get("config_field", ""),
                        "source_data_class": meta.get("source_data_class", ""),
                        "transform": meta.get("transform", ""),
                        "source_file": source_rel,
                        "code": line.strip()[:260],
                    }
                )
            for accessor in re.findall(r"skillData\.([A-Za-z0-9_]+)\b", line):
                if not accessor.startswith("Get"):
                    continue
                meta = DIGITDOOR_MONSTER_SKILL_ACCESSOR_META.get(accessor, {})
                refs.append(
                    {
                        "class_name": class_name,
                        "function": current_function,
                        "line": line_no,
                        "accessor": accessor,
                        "config_field": meta.get("config_field", ""),
                        "source_data_class": meta.get("source_data_class", ""),
                        "transform": meta.get("transform", ""),
                        "source_file": source_rel,
                        "code": line.strip()[:260],
                    }
                )
    return refs


def _write_digitdoor_monster_skill_data_accessor_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    accessor_rows: list[dict[str, Any]],
    effect_ref_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DigitDoor monster skill data accessor report",
        "",
        "Static read-only mapping from `DigitDoor*Effect` `skillData:Get*` calls back to `MonsterSkill` config fields and runtime conversions.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Accessor Map", ""])
    for row in accessor_rows:
        lines.append(
            f"- `{row.get('accessor')}` -> `{row.get('config_field')}` via `{row.get('source_data_class')}`; "
            f"transform: {row.get('transform')}; nonzero skills `{row.get('nonzero_skill_count')}`"
        )
    lines.extend(["", "## Effect References", ""])
    for row in effect_ref_rows[:60]:
        lines.append(
            f"- `{row.get('class_name')}.{row.get('function')}` line `{row.get('line')}` "
            f"`{row.get('accessor')}` -> `{row.get('config_field')}`: `{row.get('code')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "`DigitDoorBotSkillData` uses a per-instance `MagicalNum` add/sub obfuscation for in-memory values, but the mapping here is static config-to-client-runtime only. It does not imply server authority or a modifiable live boundary.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _digitdoor_monster_skill_buff_ids(row: dict[str, Any]) -> list[int]:
    buff_ids: list[int] = []
    for raw in _as_list(row.get("buffId")):
        if isinstance(raw, str) and re.search(r"[,|]", raw):
            buff_ids.extend(_parse_int_csv(raw))
            continue
        parsed = _as_int(raw)
        if parsed is not None:
            buff_ids.append(parsed)
    return _dedupe_preserve(buff_ids)


def _digitdoor_monster_skill_buff_hint(row: dict[str, Any]) -> str:
    type_name = str(row.get("buff_type_name") or row.get("buff_type") or "Buff")
    target = str(row.get("target_type_name") or row.get("target_type") or "")
    trigger = str(row.get("trigger_type_name") or row.get("trigger_type") or "")
    prefix = f"{type_name} Buff"
    if row.get("add_attr"):
        prefix = f"属性 Buff：{row.get('add_attr')}"
    elif _is_nonzero_effect_value(row.get("shield")):
        prefix = f"护盾 Buff：shield {row.get('shield')}"
    elif _is_nonzero_effect_value(row.get("damage")):
        prefix = f"伤害/触发 Buff：damage {row.get('damage')}"
    elif _is_nonzero_effect_value(row.get("slow_down")):
        prefix = f"移速 Buff：slowDown {row.get('slow_down')}"
    tail = [item for item in [target and f"目标 {target}", trigger and f"触发 {trigger}"] if item]
    if _is_nonzero_effect_value(row.get("duration")):
        tail.append(f"持续 {row.get('duration')}")
    if str(row.get("passive") or "").strip() not in {"", "0", "False", "false"}:
        tail.append("passive")
    return "；".join([prefix, *tail])


def _write_digitdoor_monster_skill_buff_link_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    link_rows: list[dict[str, Any]],
    type_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DigitDoor monster skill buff link report",
        "",
        "Static read-only join from `MonsterSkill.buffId` to `BuffEffect` config rows and their Lua Buff class paths.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Link Rules", ""])
    lines.extend(
        [
            "- `MonsterSkill.buffId` may be a scalar or list; each id resolves through `DigitDoor_BuffEffect[id]`.",
            "- `BuffEffect.type` maps to `DigitDoorType.SkillBuffType`, then `DigitDoorType.BuffPath[type]` chooses the Lua buff class.",
            "- Runtime application is still controlled by effect classes such as `DigitDoorAddBuffEffect` and `DigitDoorBaseSkill:AddBuffData`; this report only joins static config.",
            "",
            "## Linked Buff Types",
            "",
        ]
    )
    for row in type_rows:
        lines.append(
            f"- `{row.get('buff_type_name')}` type `{row.get('buff_type')}` links `{row.get('link_count')}` "
            f"skills `{row.get('sample_skill_ids')}` buffs `{row.get('sample_buff_ids')}` path `{row.get('buff_path')}`"
        )
    lines.extend(["", "## Skill Buff Samples", ""])
    for row in link_rows[:80]:
        lines.append(
            f"- skill `{row.get('skill_id')}` timeline `{row.get('skill_timeline_id')}` buff `{row.get('buff_id')}` "
            f"`{row.get('buff_type_name')}`: {row.get('runtime_hint')}"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This report is a static config-to-client-class map. It does not prove server-side authority, alter runtime state, or provide live patching guidance.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_monster_skill_buff_link_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    digitdoor_logic_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None

    monster_skill_rows = _parse_config_rows(config_dir, "MonsterSkill", resolved_lang_path, lang_map)
    buff_rows_raw = _parse_config_rows(config_dir, "BuffEffect", resolved_lang_path, lang_map)
    type_meta = _parse_digitdoor_buff_type_metadata(logic_dir)
    buff_rows = _collect_buff_effect_rows(buff_rows_raw, type_meta)
    buff_by_id = {_as_int(row.get("id")) or 0: row for row in buff_rows if _as_int(row.get("id")) is not None}

    link_rows: list[dict[str, Any]] = []
    missing_rows: list[dict[str, Any]] = []
    for skill in sorted(monster_skill_rows, key=lambda item: _sort_value(item.get("id"))):
        skill_id = skill.get("id")
        buff_ids = _digitdoor_monster_skill_buff_ids(skill)
        for buff_id in buff_ids:
            buff = buff_by_id.get(buff_id)
            if not buff:
                missing_rows.append(
                    {
                        "skill_id": skill_id,
                        "skill_timeline_id": skill.get("timeLineId") or "",
                        "buff_id": buff_id,
                    }
                )
                continue
            skill_type = _as_int(skill.get("type"))
            trigger_type = _as_int(skill.get("trigger"))
            row = {
                "skill_id": skill_id,
                "skill_timeline_id": skill.get("timeLineId") or "",
                "skill_type": skill.get("type") or "",
                "skill_type_name": MONSTER_SKILL_TYPE_LABELS.get(skill_type or -1, ""),
                "skill_trigger": skill.get("trigger") or "",
                "skill_trigger_name": SKILL_RELEASE_TYPE_LABELS.get(trigger_type or -1, ""),
                "buff_id": buff_id,
                "buff_type": buff.get("type") or "",
                "buff_type_name": buff.get("type_name") or "",
                "buff_path": buff.get("buff_path") or "",
                "target_type": buff.get("target_type") or "",
                "target_type_name": buff.get("target_type_name") or "",
                "trigger_type": buff.get("trigger_type") or "",
                "trigger_type_name": buff.get("trigger_type_name") or "",
                "trigger_percent": buff.get("trigger_percent") or "",
                "duration": buff.get("duration") or "",
                "interval": buff.get("interval") or "",
                "eff_type": buff.get("eff_type") or "",
                "plies_limit": buff.get("plies_limit") or "",
                "damage": buff.get("damage") or "",
                "add_attr": buff.get("add_attr") or "",
                "shield": buff.get("shield") or "",
                "slow_down": buff.get("slow_down") or "",
                "buff_amplify": buff.get("buff_amplify") or "",
                "passive": buff.get("passive") or "",
                "buff_timeline_id": buff.get("timeline_id") or "",
                "buff_class_resolved": bool(buff.get("buff_path")),
            }
            row["runtime_hint"] = _digitdoor_monster_skill_buff_hint(row)
            link_rows.append(row)

    type_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in link_rows:
        type_groups[str(row.get("buff_type") or "")].append(row)
    type_rows: list[dict[str, Any]] = []
    for buff_type, rows in sorted(type_groups.items(), key=lambda item: _sort_value(item[0])):
        type_rows.append(
            {
                "buff_type": buff_type,
                "buff_type_name": rows[0].get("buff_type_name") or "",
                "link_count": len(rows),
                "skill_count": len({str(row.get("skill_id") or "") for row in rows}),
                "buff_count": len({str(row.get("buff_id") or "") for row in rows}),
                "buff_path": rows[0].get("buff_path") or "",
                "sample_skill_ids": ",".join(str(item) for item in _dedupe_preserve([row.get("skill_id") for row in rows[:12]])),
                "sample_buff_ids": ",".join(str(item) for item in _dedupe_preserve([row.get("buff_id") for row in rows[:12]])),
                "sample_hints": " | ".join(_dedupe_preserve([row.get("runtime_hint") for row in rows[:6]])),
            }
        )

    stats = {
        "monster_skill_row_count": len(monster_skill_rows),
        "buff_effect_row_count": len(buff_rows_raw),
        "skills_with_buff_id": sum(1 for row in monster_skill_rows if _digitdoor_monster_skill_buff_ids(row)),
        "skill_buff_ref_count": len(link_rows) + len(missing_rows),
        "resolved_skill_buff_ref_count": len(link_rows),
        "unresolved_skill_buff_ref_count": len(missing_rows),
        "unique_buff_ref_count": len({str(row.get("buff_id") or "") for row in link_rows}),
        "linked_buff_type_count": len(type_rows),
        "linked_buff_class_path_count": len({str(row.get("buff_path") or "") for row in link_rows if row.get("buff_path")}),
        "linked_buff_class_path_missing_count": sum(1 for row in link_rows if not row.get("buff_path")),
    }
    verdict = {
        "all_monster_skill_buff_ids_resolve_buff_effect": stats["unresolved_skill_buff_ref_count"] == 0,
        "buff_type_metadata_found": bool(type_meta.get("types_by_id")) and bool(type_meta.get("paths_by_id")),
        "buff_class_resolution_surface_found": bool(type_meta.get("paths_by_id")),
        "static_client_buff_mapping_only": True,
    }

    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    link_tsv = out_dir / "monster_skill_buff_links.tsv"
    type_tsv = out_dir / "monster_skill_buff_type_summary.tsv"
    report_path = out_dir / "monster_skill_buff_link_report.md"
    json_path = out_dir / "monster_skill_buff_link_report.json"
    _write_tsv(
        link_tsv,
        link_rows,
        [
            "skill_id",
            "skill_timeline_id",
            "skill_type",
            "skill_type_name",
            "skill_trigger",
            "skill_trigger_name",
            "buff_id",
            "buff_type",
            "buff_type_name",
            "buff_path",
            "target_type",
            "target_type_name",
            "trigger_type",
            "trigger_type_name",
            "trigger_percent",
            "duration",
            "interval",
            "eff_type",
            "plies_limit",
            "damage",
            "add_attr",
            "shield",
            "slow_down",
            "buff_amplify",
            "passive",
            "buff_timeline_id",
            "buff_class_resolved",
            "runtime_hint",
        ],
    )
    _write_tsv(
        type_tsv,
        type_rows,
        ["buff_type", "buff_type_name", "link_count", "skill_count", "buff_count", "buff_path", "sample_skill_ids", "sample_buff_ids", "sample_hints"],
    )
    _write_digitdoor_monster_skill_buff_link_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        link_rows=link_rows,
        type_rows=type_rows,
    )
    files = {
        "links": str(link_tsv),
        "types": str(type_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    json_path.write_text(
        json.dumps(
            {
                "confirmed": all(verdict.values()),
                "source": {
                    "digitdoor_config_dir": str(config_dir),
                    "digitdoor_logic_dir": str(logic_dir),
                    "lang_path": str(resolved_lang_path or ""),
                },
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "links": link_rows[:160],
                    "types": type_rows,
                    "missing": missing_rows[:80],
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": all(verdict.values()),
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


def _digitdoor_basis_point_percent(value: Any) -> str:
    parsed = _as_int(value)
    if parsed is None:
        return ""
    return f"{parsed / 100:g}%"


def _digitdoor_duration_projection(value: Any) -> str:
    parsed = _as_int(value)
    if parsed is None:
        return ""
    if parsed < 0:
        return f"特殊/常驻时长 `{parsed}`，不按普通秒数自然结束"
    return f"{parsed * 0.001:g} 秒"


def _digitdoor_eff_type_projection(value: Any) -> str:
    parsed = _as_int(value)
    if parsed == 1:
        return "重复获得时叠层，受 pliesLimit 限制"
    if parsed == 2:
        return "重复获得时刷新/重置开始时间"
    if parsed is None:
        return ""
    return f"未知 effType `{parsed}`，保留原值"


def _digitdoor_add_attr_projection(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parts: list[str] = []
    for item in re.split(r"[,|;]", text):
        if not item.strip():
            continue
        key, sep, raw = item.strip().partition(":")
        percent = _digitdoor_basis_point_percent(raw) if sep else ""
        parts.append(f"{key} +{percent}" if percent else item.strip())
    return "；".join(parts)


def _digitdoor_monster_skill_buff_formula_rows(link_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    formula_meta = {row["field"]: row for row in BUFF_CLASS_FORMULA_ROWS}

    def meta_for(field: str) -> dict[str, Any]:
        return formula_meta.get(field, {})

    rows: list[dict[str, Any]] = []

    def add_projection(link: dict[str, str], field: str, raw_value: Any, projection: str, formula_field: str, confidence: str = "direct") -> None:
        if raw_value in (None, "") or projection == "":
            return
        meta = meta_for(formula_field)
        rows.append(
            {
                "skill_id": link.get("skill_id") or "",
                "skill_timeline_id": link.get("skill_timeline_id") or "",
                "buff_id": link.get("buff_id") or "",
                "buff_type_name": link.get("buff_type_name") or "",
                "field": field,
                "raw_value": raw_value,
                "projection": projection,
                "formula_field": formula_field,
                "runtime_slot": meta.get("runtime_slot", ""),
                "formula": meta.get("formula", ""),
                "meaning": meta.get("meaning", ""),
                "confidence": confidence,
            }
        )

    for link in link_rows:
        add_projection(
            link,
            "addAttr",
            link.get("add_attr"),
            _digitdoor_add_attr_projection(link.get("add_attr")),
            "addAttr",
        )
        add_projection(
            link,
            "shield",
            link.get("shield"),
            f"{_digitdoor_basis_point_percent(link.get('shield'))} 最大生命护盾比例",
            "shield",
        )
        add_projection(
            link,
            "damage",
            link.get("damage"),
            f"{_format_ratio(link.get('damage'))} 基础伤害/触发数值",
            "damage",
        )
        add_projection(
            link,
            "duration",
            link.get("duration"),
            _digitdoor_duration_projection(link.get("duration")),
            "duration",
        )
        add_projection(
            link,
            "triggerPercent",
            link.get("trigger_percent"),
            f"{_digitdoor_basis_point_percent(link.get('trigger_percent'))} 触发概率",
            "triggerPercent + triggerBuffId + targetBuffCheck",
        )
        add_projection(
            link,
            "effType",
            link.get("eff_type"),
            _digitdoor_eff_type_projection(link.get("eff_type")),
            "effType + pliesLimit",
        )
        add_projection(
            link,
            "buffTimeline",
            link.get("buff_timeline_id"),
            f"播放 Buff 表现 timeline `{link.get('buff_timeline_id')}`",
            "duration",
            confidence="cosmetic_timeline",
        )
    return rows


def _write_digitdoor_monster_skill_buff_formula_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    projection_rows: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DigitDoor monster skill buff formula projection report",
        "",
        "Static read-only projection from monster-skill BuffEffect rows to human-readable runtime formula hints.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Skill Summary", ""])
    for row in summary_rows:
        lines.append(
            f"- skill `{row.get('skill_id')}` buffs `{row.get('buff_ids')}`: {row.get('projection_summary')}"
        )
    lines.extend(["", "## Projection Rows", ""])
    for row in projection_rows[:120]:
        lines.append(
            f"- skill `{row.get('skill_id')}` buff `{row.get('buff_id')}` `{row.get('field')}` `{row.get('raw_value')}` -> "
            f"{row.get('projection')}; formula `{row.get('formula')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "These are static client-side formula projections from config and visible Lua formula semantics. Strength amplification, layers, target max HP, and final combat authority may change the live numeric result.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_monster_skill_buff_formula_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    digitdoor_logic_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    link_result = build_fanxiu_digitdoor_monster_skill_buff_link_probe(
        digitdoor_config_dir=digitdoor_config_dir,
        digitdoor_logic_dir=digitdoor_logic_dir,
        lang_path=lang_path,
        export_root=root,
    )
    link_rows = _read_tsv_dicts(Path(link_result["files"]["links"]))
    projection_rows = _digitdoor_monster_skill_buff_formula_rows(link_rows)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in projection_rows:
        grouped[str(row.get("skill_id") or "")].append(row)
    summary_rows: list[dict[str, Any]] = []
    for skill_id, rows in sorted(grouped.items(), key=lambda item: _sort_value(item[0])):
        summary_rows.append(
            {
                "skill_id": skill_id,
                "buff_ids": ",".join(str(item) for item in _dedupe_preserve([row.get("buff_id") for row in rows])),
                "projection_count": len(rows),
                "projection_summary": "；".join(_dedupe_preserve([row.get("projection") for row in rows if row.get("projection")])),
            }
        )

    field_counts = dict(Counter(str(row.get("field") or "") for row in projection_rows))
    stats = {
        "linked_buff_count": len(link_rows),
        "projection_row_count": len(projection_rows),
        "skills_with_projection": len(summary_rows),
        "projection_field_counts": field_counts,
        "linked_buffs_with_projection": len({(row.get("skill_id"), row.get("buff_id")) for row in projection_rows}),
    }
    verdict = {
        "link_probe_confirmed": bool(link_result.get("confirmed")),
        "all_linked_buffs_have_formula_projection": stats["linked_buffs_with_projection"] == stats["linked_buff_count"],
        "formula_semantics_available": bool(BUFF_CLASS_FORMULA_ROWS),
        "static_client_formula_projection_only": True,
    }

    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    projection_tsv = out_dir / "monster_skill_buff_formula_projection.tsv"
    by_skill_tsv = out_dir / "monster_skill_buff_formula_by_skill.tsv"
    report_path = out_dir / "monster_skill_buff_formula_projection_report.md"
    json_path = out_dir / "monster_skill_buff_formula_projection_report.json"
    _write_tsv(
        projection_tsv,
        projection_rows,
        [
            "skill_id",
            "skill_timeline_id",
            "buff_id",
            "buff_type_name",
            "field",
            "raw_value",
            "projection",
            "formula_field",
            "runtime_slot",
            "formula",
            "meaning",
            "confidence",
        ],
    )
    _write_tsv(by_skill_tsv, summary_rows, ["skill_id", "buff_ids", "projection_count", "projection_summary"])
    _write_digitdoor_monster_skill_buff_formula_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        projection_rows=projection_rows,
        summary_rows=summary_rows,
    )
    files = {
        "projections": str(projection_tsv),
        "by_skill": str(by_skill_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    json_path.write_text(
        json.dumps(
            {
                "confirmed": all(verdict.values()),
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "projections": projection_rows[:200],
                    "by_skill": summary_rows,
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": all(verdict.values()),
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


def build_fanxiu_digitdoor_monster_skill_data_accessor_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None
    monster_skill_rows = _parse_config_rows(config_dir, "MonsterSkill", resolved_lang_path, lang_map)
    timeline_result = build_fanxiu_digitdoor_monster_skill_timeline_probe(
        digitdoor_config_dir=digitdoor_config_dir,
        lang_path=lang_path,
        export_root=root,
    )
    effect_rows = _read_tsv_dicts(Path(timeline_result["files"]["effects"]))
    effect_ref_rows = _find_digitdoor_effect_skill_data_refs(root, effect_rows)
    data_source_files = _find_digitdoor_skill_data_source_files(root)

    used_accessors = sorted({row.get("accessor") or "" for row in effect_ref_rows if row.get("accessor")})
    accessor_rows: list[dict[str, Any]] = []
    for accessor in used_accessors:
        meta = DIGITDOOR_MONSTER_SKILL_ACCESSOR_META.get(accessor, {})
        config_field = meta.get("config_field", "")
        nonzero_rows = [row for row in monster_skill_rows if config_field and _is_nonzero_effect_value(row.get(config_field))]
        examples = [
            f"{row.get('id')}={row.get(config_field)}->{_digitdoor_accessor_runtime_preview(accessor, row.get(config_field))}"
            for row in nonzero_rows[:10]
        ]
        source_class = str(meta.get("source_data_class") or "")
        source_path = data_source_files.get(source_class)
        source_text = source_path.read_text(encoding="utf-8", errors="ignore") if source_path else ""
        accessor_rows.append(
            {
                "accessor": accessor,
                "config_field": config_field,
                "source_data_class": source_class,
                "source_file": str(source_path.relative_to(root)) if source_path else "",
                "getter_found": f"function _M.{accessor}" in source_text,
                "transform": meta.get("transform", ""),
                "nonzero_skill_count": len(nonzero_rows),
                "example_skill_values": "; ".join(examples),
                "bot_skill_data_has_accessor": source_class == "DigitDoorBotSkillData",
            }
        )

    skill_value_rows: list[dict[str, Any]] = []
    for row in sorted(monster_skill_rows, key=lambda item: _sort_value(item.get("id"))):
        for accessor_row in accessor_rows:
            config_field = accessor_row.get("config_field") or ""
            raw_value = row.get(config_field)
            if raw_value in (None, ""):
                continue
            skill_value_rows.append(
                {
                    "skill_id": row.get("id"),
                    "timeline_id": row.get("timeLineId") or "",
                    "type": row.get("type") or "",
                    "trigger": row.get("trigger") or "",
                    "accessor": accessor_row.get("accessor") or "",
                    "config_field": config_field,
                    "raw_value": raw_value,
                    "runtime_preview": _digitdoor_accessor_runtime_preview(str(accessor_row.get("accessor") or ""), raw_value),
                }
            )

    stats = {
        "monster_skill_row_count": len(monster_skill_rows),
        "effect_accessor_ref_count": len(effect_ref_rows),
        "used_accessor_count": len(used_accessors),
        "mapped_accessor_count": sum(1 for accessor in used_accessors if accessor in DIGITDOOR_MONSTER_SKILL_ACCESSOR_META),
        "bot_skill_data_source_found": "DigitDoorBotSkillData" in data_source_files,
        "add_buff_data_source_found": "DigitDoorAddBuffData" in data_source_files,
        "accessors_not_on_bot_skill_data": [
            row["accessor"]
            for row in accessor_rows
            if row.get("source_data_class") and row.get("source_data_class") != "DigitDoorBotSkillData"
        ],
        "skill_value_row_count": len(skill_value_rows),
    }
    verdict = {
        "all_effect_accessors_are_mapped": stats["mapped_accessor_count"] == stats["used_accessor_count"],
        "bot_skill_data_source_found": bool(stats["bot_skill_data_source_found"]),
        "add_buff_ext_accessors_are_guarded_non_bot_data": set(stats["accessors_not_on_bot_skill_data"]).issubset(
            {"GetExtCondition", "GetExtBuffValue"}
        ),
        "static_client_accessor_mapping_only": True,
    }

    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    accessor_tsv = out_dir / "monster_skill_data_accessors.tsv"
    effect_ref_tsv = out_dir / "monster_skill_data_accessor_effect_refs.tsv"
    value_tsv = out_dir / "monster_skill_data_accessor_skill_values.tsv"
    report_path = out_dir / "monster_skill_data_accessor_report.md"
    json_path = out_dir / "monster_skill_data_accessor_report.json"
    _write_tsv(
        accessor_tsv,
        accessor_rows,
        [
            "accessor",
            "config_field",
            "source_data_class",
            "source_file",
            "getter_found",
            "transform",
            "nonzero_skill_count",
            "example_skill_values",
            "bot_skill_data_has_accessor",
        ],
    )
    _write_tsv(
        effect_ref_tsv,
        effect_ref_rows,
        ["class_name", "function", "line", "accessor", "config_field", "source_data_class", "transform", "source_file", "code"],
    )
    _write_tsv(
        value_tsv,
        skill_value_rows,
        ["skill_id", "timeline_id", "type", "trigger", "accessor", "config_field", "raw_value", "runtime_preview"],
    )
    _write_digitdoor_monster_skill_data_accessor_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        accessor_rows=accessor_rows,
        effect_ref_rows=effect_ref_rows,
    )
    files = {
        "accessors": str(accessor_tsv),
        "effect_refs": str(effect_ref_tsv),
        "skill_values": str(value_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    json_path.write_text(
        json.dumps(
            {
                "confirmed": all(verdict.values()),
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "accessors": accessor_rows,
                    "effect_refs": effect_ref_rows[:160],
                    "skill_values": skill_value_rows[:240],
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": all(verdict.values()),
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


def _compact_monster_skill(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    skill_type = _as_int(row.get("type"))
    trigger_type = _as_int(row.get("trigger"))
    return {
        "id": row.get("id"),
        "type": row.get("type"),
        "type_name": MONSTER_SKILL_TYPE_LABELS.get(skill_type or -1, ""),
        "trigger": row.get("trigger"),
        "trigger_name": SKILL_RELEASE_TYPE_LABELS.get(trigger_type or -1, ""),
        "timeline_id": row.get("timeLineId"),
        "cd": row.get("cd"),
        "damage": row.get("damage"),
        "buff_id": row.get("buffId"),
        "release_count": row.get("releaseCount"),
        "duration": row.get("duration"),
        "hit_time": row.get("hitTime"),
        "distance": row.get("distance"),
        "hp_limit": row.get("hpLimit"),
        "summon_monster_id": row.get("summonMonsterId"),
        "summon_hp": row.get("summonHp"),
        "summon_attack": row.get("summonAttack"),
        "runtime_hint": _monster_skill_runtime_hint(row),
    }


def _monster_skill_runtime_hint(row: dict[str, Any]) -> str:
    trigger_type = _as_int(row.get("trigger"))
    if trigger_type == 0:
        return "Common trigger sets auto-release immediately."
    if trigger_type == 1:
        return f"Release when moved distance reaches cfg.distance `{row.get('distance') or 0}`."
    if trigger_type == 2:
        return f"Release when current HP <= max HP * hpLimit * 0.0001; default is 80%, cfg hpLimit `{row.get('hpLimit') or ''}`."
    if trigger_type == 3:
        return "Death trigger is released by explicit ReleaseSkillByType(Death) callers."
    if trigger_type == 4:
        return f"Release when distance to a live partner <= cfg.distance `{row.get('distance') or 0}` and target that partner."
    if trigger_type == 5:
        return "DirectDoor trigger sets auto-release during condition checks."
    return ""


def _digitdoor_monster_skill_rows(monster_skill_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in sorted(monster_skill_rows, key=lambda item: _sort_value(item.get("id"))):
        compact = _compact_monster_skill(row) or {}
        rows.append({key: ("" if value is None else value) for key, value in compact.items()})
    return rows


def _digitdoor_millisecond_projection(value: Any) -> str:
    parsed = _as_int(value)
    if parsed is None:
        return ""
    if parsed < 0:
        return f"特殊毫秒值 `{parsed}`"
    return f"{parsed * 0.001:g} 秒"


def _digitdoor_monster_skill_value_projection_rows(skill_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(
        skill: dict[str, Any],
        field: str,
        raw_value: Any,
        projection: str,
        formula: str,
        meaning: str,
        runtime_slot: str,
    ) -> None:
        if not _is_nonzero_effect_value(raw_value) or not projection:
            return
        rows.append(
            {
                "skill_id": skill.get("id") or "",
                "timeline_id": skill.get("timeline_id") or "",
                "type": skill.get("type") or "",
                "type_name": skill.get("type_name") or "",
                "trigger": skill.get("trigger") or "",
                "trigger_name": skill.get("trigger_name") or "",
                "field": field,
                "raw_value": raw_value,
                "projection": projection,
                "formula": formula,
                "meaning": meaning,
                "runtime_slot": runtime_slot,
            }
        )

    for skill in skill_rows:
        cd = _as_int(skill.get("cd"))
        if cd is not None:
            add(
                skill,
                "cd",
                skill.get("cd"),
                f"冷却 {_digitdoor_millisecond_projection(cd)}",
                "cd_seconds = cfg.cd * 0.001; negative values are special/non-standard cooldown markers",
                "技能冷却时间",
                "DigitDoorBotSkillData.cd",
            )
        add(
            skill,
            "damage",
            skill.get("damage"),
            f"{_format_ratio(skill.get('damage'))} 基础伤害/技能伤害系数",
            "damage_percent = cfg.damage / 100",
            "技能基础伤害系数",
            "DigitDoorBotSkillData.damage",
        )
        add(
            skill,
            "duration",
            skill.get("duration"),
            _digitdoor_duration_projection(skill.get("duration")),
            "duration_seconds = cfg.duration * 0.001; -1 is a special persistent marker",
            "技能持续时间参数",
            "DigitDoorBotSkillData.duration",
        )
        add(
            skill,
            "hitTime",
            skill.get("hit_time"),
            f"命中/触发时间 {_digitdoor_millisecond_projection(skill.get('hit_time'))}",
            "hit_time_seconds = cfg.hitTime * 0.001",
            "命中/触发时间参数",
            "DigitDoorBotSkillData.hitTime",
        )
        add(
            skill,
            "distance",
            skill.get("distance"),
            f"{skill.get('distance')} 距离单位",
            "distance = cfg.distance",
            "移动距离或对目标触发距离",
            "DigitDoorBotSkillData.distance",
        )
        add(
            skill,
            "hpLimit",
            skill.get("hp_limit"),
            f"{_format_ratio(skill.get('hp_limit'))} 血线触发阈值",
            "hp_limit_ratio = cfg.hpLimit * 0.0001",
            "按自身生命比例触发技能",
            "DigitDoorBotSkillData.hpLimit",
        )
        add(
            skill,
            "releaseCount",
            skill.get("release_count"),
            (
                f"特殊释放计数 `{skill.get('release_count')}`（重复/不限次标记）"
                if (_as_int(skill.get("release_count")) or 0) < 0
                else f"{skill.get('release_count')} 次释放/触发计数"
            ),
            "release_count = cfg.releaseCount; negative values are special repeat markers",
            "释放次数或特殊重复标记",
            "DigitDoorBotSkillData.releaseCount",
        )
        add(
            skill,
            "summonMonsterId",
            skill.get("summon_monster_id"),
            f"召唤怪物组 {skill.get('summon_monster_id')}",
            "summon_monster_id = cfg.summonMonsterId",
            "召唤怪物组引用",
            "DigitDoorBotSkillData.summonMonsterId",
        )
        add(
            skill,
            "summonHp",
            skill.get("summon_hp"),
            f"{_format_ratio(skill.get('summon_hp'))} 召唤物生命继承倍率",
            "summon_hp_ratio = cfg.summonHp * 0.0001",
            "召唤物生命继承倍率",
            "DigitDoorBotSkillData.summonHp",
        )
        add(
            skill,
            "summonAttack",
            skill.get("summon_attack"),
            f"{_format_ratio(skill.get('summon_attack'))} 召唤物攻击继承倍率",
            "summon_attack_ratio = cfg.summonAttack * 0.0001",
            "召唤物攻击继承倍率",
            "DigitDoorBotSkillData.summonAttack",
        )
    return rows


def _write_digitdoor_monster_skill_value_projection_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    projection_rows: list[dict[str, Any]],
    files: dict[str, str],
) -> None:
    lines = [
        "# DigitDoor monster skill value projection report",
        "",
        "Static read-only projection of raw `MonsterSkill` numeric fields into readable units.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Projection Samples", ""])
    for row in projection_rows[:80]:
        lines.append(
            f"- skill `{row.get('skill_id')}` field `{row.get('field')}` raw `{row.get('raw_value')}` => `{row.get('projection')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This is a client-side static projection. Runtime values can still be affected by layers, strength amplification, target stats, combat timing, and server/combat authority.",
            "",
            "## Files",
            "",
        ]
    )
    for label, file_path in files.items():
        lines.append(f"- `{label}`: `{file_path}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_monster_skill_value_projection_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None

    monster_skill_rows = _parse_config_rows(config_dir, "MonsterSkill", resolved_lang_path, lang_map)
    skill_rows = _digitdoor_monster_skill_rows(monster_skill_rows)
    projection_rows = _digitdoor_monster_skill_value_projection_rows(skill_rows)
    rows_by_skill: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in projection_rows:
        rows_by_skill[str(row.get("skill_id") or "")].append(row)
    by_skill_rows = [
        {
            "skill_id": skill_id,
            "projection_count": len(rows),
            "projection_fields": _pipe_join([row.get("field") for row in rows]),
            "projection_summary": " | ".join(_dedupe_preserve([row.get("projection") for row in rows[:12]])),
        }
        for skill_id, rows in sorted(rows_by_skill.items(), key=lambda item: _sort_value(item[0]))
    ]
    field_counts = Counter(str(row.get("field") or "") for row in projection_rows)
    stats = {
        "monster_skill_row_count": len(skill_rows),
        "projection_row_count": len(projection_rows),
        "skills_with_projection": len(rows_by_skill),
        "projection_field_counts": dict(field_counts),
    }
    verdict = {
        "monster_skill_rows_available": len(skill_rows) > 0,
        "numeric_value_projection_available": len(projection_rows) > 0,
        "damage_ratio_projected": field_counts.get("damage", 0) > 0,
        "cooldown_seconds_projected": field_counts.get("cd", 0) > 0,
    }
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    projection_tsv = out_dir / "monster_skill_value_projection.tsv"
    by_skill_tsv = out_dir / "monster_skill_value_projection_by_skill.tsv"
    report_path = out_dir / "monster_skill_value_projection_report.md"
    json_path = out_dir / "monster_skill_value_projection_report.json"
    _write_tsv(
        projection_tsv,
        projection_rows,
        [
            "skill_id",
            "timeline_id",
            "type",
            "type_name",
            "trigger",
            "trigger_name",
            "field",
            "raw_value",
            "projection",
            "formula",
            "meaning",
            "runtime_slot",
        ],
    )
    _write_tsv(by_skill_tsv, by_skill_rows, ["skill_id", "projection_count", "projection_fields", "projection_summary"])
    files = {
        "projections": str(projection_tsv),
        "by_skill": str(by_skill_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    _write_digitdoor_monster_skill_value_projection_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        projection_rows=projection_rows,
        files=files,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": all(verdict.values()),
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "projections": projection_rows[:240],
                    "by_skill": by_skill_rows[:120],
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": all(verdict.values()),
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


def _monster_name(group: dict[str, Any] | None, info: dict[str, Any] | None) -> str:
    return str((info or {}).get("name") or (group or {}).get("textName") or (group or {}).get("id") or "")


def _digitdoor_monster_rows(
    monster_rows: list[dict[str, Any]],
    *,
    monster_info_by_id: dict[int, dict[str, Any]],
    monster_skill_by_id: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in sorted(monster_rows, key=lambda item: _sort_value(item.get("id"))):
        monster_id = _as_int(row.get("id"))
        base_id = _as_int(row.get("baseId"))
        info = monster_info_by_id.get(base_id or -1)
        skill_ids = _parse_int_csv(row.get("defaultSkill"))
        unresolved_skill_ids = [skill_id for skill_id in skill_ids if skill_id not in monster_skill_by_id]
        rows.append(
            {
                "monster_id": monster_id if monster_id is not None else "",
                "name": _monster_name(row, info),
                "text_name": row.get("textName") or "",
                "base_id": base_id if base_id is not None else "",
                "info_name": info.get("name") if info else "",
                "type": row.get("type") or "",
                "info_type": info.get("type") if info else "",
                "model_id": row.get("modelId") or (info.get("modelId") if info else ""),
                "speed": row.get("speed") or "",
                "move_stop_distance": row.get("moveStopDistance") or "",
                "default_skill_ids": ",".join(str(skill_id) for skill_id in skill_ids),
                "default_skill_count": len(skill_ids),
                "unresolved_skill_ids": ",".join(str(skill_id) for skill_id in unresolved_skill_ids),
                "restrained_count": len(_as_list(row.get("restrained"))),
                "drops": row.get("drops") or "",
                "weight": row.get("weight") or "",
                "reduce_damage": row.get("reduceDamage") or "",
                "evasion": row.get("evasion") or "",
                "repel": row.get("repel") or "",
                "description": _plain(info.get("newDes") or info.get("des")) if info else "",
                "unlock_level": info.get("unlockLevel") if info else "",
                "sort": info.get("sort") if info else "",
            }
        )
    return rows


def _digitdoor_monster_refresh_rows(
    refresh_rows: list[dict[str, Any]],
    *,
    monster_by_id: dict[int, dict[str, Any]],
    monster_info_by_id: dict[int, dict[str, Any]],
    monster_skill_by_id: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in sorted(refresh_rows, key=lambda item: (_sort_value(item.get("level")), _sort_value(item.get("refreshWave")), _sort_value(item.get("id")))):
        monster_id = _as_int(row.get("monsterId"))
        group = monster_by_id.get(monster_id or -1)
        base_id = _as_int(group.get("baseId")) if group else None
        info = monster_info_by_id.get(base_id or -1)
        skill_ids = _parse_int_csv(group.get("defaultSkill") if group else "")
        unresolved_skill_ids = [skill_id for skill_id in skill_ids if skill_id not in monster_skill_by_id]
        rows.append(
            {
                "id": row.get("id") or "",
                "level": row.get("level") or "",
                "refresh_wave": row.get("refreshWave") or "",
                "game_type": row.get("gameType") or "",
                "object_type": row.get("objectType") or "",
                "monster_id": monster_id if monster_id is not None else "",
                "monster_name": _monster_name(group, info) if group or info else "",
                "base_id": base_id if base_id is not None else "",
                "monster_type": group.get("type") if group else "",
                "attack": row.get("ATTACK") or "",
                "hp": row.get("HP") or "",
                "critical": row.get("CRITICAL") or "",
                "anti_critical": row.get("ANTICRITICAL") or "",
                "atk_speed": row.get("ATKSPEED") or "",
                "increase_damage": row.get("INCREASEDAMAGE") or "",
                "reduce_damage": row.get("REDUCEDAMAGE") or "",
                "kill_exp": row.get("killExp") or "",
                "wave_time": row.get("waveTime") or "",
                "refresh_total_num": row.get("refreshTotalNum") or "",
                "refresh_time": row.get("refreshTime") or "",
                "refresh_num": row.get("refreshNum") or "",
                "refresh_offset_dis": row.get("refreshOffsetDis") or "",
                "refresh_type": row.get("refreshType") or "",
                "refresh_pos": row.get("refreshPos") or "",
                "next_wave_condition": row.get("nextWaveCondition") or "",
                "default_skill_ids": ",".join(str(skill_id) for skill_id in skill_ids),
                "unresolved_skill_ids": ",".join(str(skill_id) for skill_id in unresolved_skill_ids),
            }
        )
    return rows


DIGITDOOR_REFRESH_TYPE_LABELS = {
    0: "Normal",
    1: "TargetPos",
    2: "TargetArea",
}

DIGITDOOR_MONSTER_TYPE_LABELS = {
    1: "SmallMonster",
    2: "Elite",
    3: "Boss",
}

DIGITDOOR_REFRESH_POINT_LATENT_FIELDS = [
    {
        "field": "frontRow",
        "meaning": "前后排目标优先级标记",
        "runtime_slot": "DigitDoorBot.InitData -> self.isFront",
        "lua_topic": "front_row",
    },
    {
        "field": "moveType",
        "meaning": "移动模式枚举",
        "runtime_slot": "DigitDoorBot.InitMoveData -> self.moveType",
        "lua_topic": "broken_move",
    },
    {
        "field": "moveAngle",
        "meaning": "折线移动角度权重列表",
        "runtime_slot": "DigitDoorBot.InitMoveData -> startMoveAngle/curMoveAngle",
        "lua_topic": "broken_move",
    },
    {
        "field": "startBubble",
        "meaning": "波次开始剧情/气泡",
        "runtime_slot": "DigitDoorEntityMgr.CheckStartBubble -> TalkMgr.AddTalkFromPlot",
        "lua_topic": "bubble_start",
    },
    {
        "field": "endBubble",
        "meaning": "波次结束剧情/气泡",
        "runtime_slot": "DigitDoorEntityMgr.CheckEndBubble -> TalkMgr.AddTalkFromPlot",
        "lua_topic": "bubble_end",
    },
    {
        "field": "bubbleCondition",
        "meaning": "气泡播放条件",
        "runtime_slot": "GameUtil.CheckCondition(cfg.bubbleCondition)",
        "lua_topic": "bubble_condition",
    },
]

DIGITDOOR_REFRESH_POINT_LATENT_TOPIC_TERMS = {
    "front_row": ("frontRow", "self.isFront"),
    "broken_move": ("moveType", "moveAngle", "BrokenLine", "startMoveAngle", "curMoveAngle"),
    "bubble_start": ("CheckStartBubble", "startBubble"),
    "bubble_end": ("CheckEndBubble", "endBubble"),
    "bubble_condition": ("bubbleCondition", "GameUtil.CheckCondition"),
}


def _digitdoor_number_tokens(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return re.findall(r"-?\d+(?:\.\d+)?", str(value))


def _digitdoor_next_wave_condition_projection(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    clauses = []
    for clause in [part.strip() for part in text.split(";") if part.strip()]:
        pieces = [part.strip() for part in clause.split("|")]
        ctype = pieces[0] if pieces else ""
        param = pieces[1] if len(pieces) > 1 else ""
        if ctype == "CM":
            clauses.append(f"波长倒计时剩余 <= {param or 0} 秒")
        elif ctype == "CR":
            clauses.append(f"本波剩余怪物 <= {param or 0} 个")
        else:
            clauses.append(f"{clause}（未命名条件）")
    if not clauses:
        return text
    suffix = "；多条条件按 OR 判断" if len(clauses) > 1 else ""
    return "；".join(clauses) + suffix


DIGITDOOR_CONDITION_FIELD_AUDIT_TARGETS = [
    {
        "config": "SkillRefreshEffect",
        "field": "condition",
        "meaning": "门效果候选条件预留字段",
        "runtime_slot": "当前可见 Lua 未发现直接消费；门实体由 SM_DigitDoorRefDoor.doorVOS.id 指向 SkillRefreshEffect",
        "active_note": "若非空，应优先核对服务端回包/选择逻辑；当前不能把它当成本地候选过滤器。",
    },
    {
        "config": "SkillEnhanceEffect",
        "field": "condition",
        "meaning": "技能增强效果条件预留字段",
        "runtime_slot": "门效果通过 SkillRefreshEffect.skill -> SkillEnhanceEffect 展开；当前静态效果 hint 未依赖该字段",
        "active_note": "若非空，需结合具体 Effect 类再判断触发分支。",
    },
    {
        "config": "CharacterSkillInfo",
        "field": "extCondition",
        "meaning": "技能额外参数条件",
        "runtime_slot": "DigitDoorAddBuffEffect/GetExtParam 与 DigitDoorAddBuffInEndEffect/GetExtParam",
        "active_note": "Lua 会在条件满足时使用 extBuffValue；当前如果全空，则相关分支是预留能力。",
    },
    {
        "config": "CharacterSkillShow",
        "field": "showCondition",
        "meaning": "展示技能说明条件",
        "runtime_slot": "DigitDoorData 拼接 showCondition + skillDes",
        "active_note": "偏展示层，不等价于战斗触发条件。",
    },
    {
        "config": "SkillEnhance",
        "field": "condition",
        "meaning": "肉鸽技能强化前置/互斥/等级区间条件",
        "runtime_slot": "强化池选择逻辑；catalog 已解析 PR/MU/TCLV 引用",
        "active_note": "这是当前最主要的强化树条件字段。",
    },
    {
        "config": "MonsterRefreshPoint",
        "field": "nextWaveCondition",
        "meaning": "进入下一波条件",
        "runtime_slot": "DigitDoorEntityMgr.CheckNextWaveCondition，CM=剩余波长时间，CR=剩余怪物数",
        "active_note": "控制刷怪波次推进节奏。",
    },
    {
        "config": "MonsterRefreshPoint",
        "field": "bubbleCondition",
        "meaning": "波次剧情/气泡播放条件",
        "runtime_slot": "DigitDoorEntityMgr.CheckStartBubble/CheckEndBubble -> GameUtil.CheckCondition(cfg.bubbleCondition)",
        "active_note": "只影响波次气泡/剧情展示。",
    },
    {
        "config": "DigitDoorStage",
        "field": "showCondition",
        "meaning": "数字门入口显示条件",
        "runtime_slot": "DigitDoorMgr/DigitDoorPopView 调用 GameUtil.CheckCondition",
        "active_note": "入口可见性，不是关内战斗逻辑。",
    },
    {
        "config": "DigitDoorStage",
        "field": "endCondition",
        "meaning": "数字门入口结束/关闭条件",
        "runtime_slot": "DigitDoorPopItem/DigitDoorActivityEnterItem 调用 GameUtil.CheckCondition",
        "active_note": "活动/入口生命周期条件。",
    },
    {
        "config": "DigitDoorStage",
        "field": "autoCondition",
        "meaning": "自动条件提示/入口辅助条件",
        "runtime_slot": "DigitDoorStage 配置字段；具体消费需按入口视图继续追踪",
        "active_note": "当前只做静态审计，不推断战斗含义。",
    },
    {
        "config": "DigitDoorActivity",
        "field": "showCondition",
        "meaning": "活动入口显示条件",
        "runtime_slot": "DigitDoorActivityEnterView/DigitDoorPopView -> GameUtil.CheckCondition",
        "active_note": "活动入口可见性。",
    },
    {
        "config": "DigitDoorActivity",
        "field": "endCondition",
        "meaning": "活动入口结束条件",
        "runtime_slot": "DigitDoorActivityEnterItem/DigitDoorActivityEnterView -> GameUtil.CheckCondition",
        "active_note": "活动入口生命周期条件。",
    },
    {
        "config": "DigitDoorPreLevelReward",
        "field": "openCondition",
        "meaning": "章节预览奖励解锁条件",
        "runtime_slot": "DigitDoorInfoPanel/DigitDoorPreRewardView 展示 openConditionDesc",
        "active_note": "奖励预览层条件。",
    },
]


def _digitdoor_condition_value_has_content(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_digitdoor_condition_value_has_content(item) for item in value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip()
    return text not in {"", "0", "nil", "None", "none", "null"}


def _digitdoor_condition_projection(field: str, value: Any) -> str:
    if field == "nextWaveCondition":
        return _digitdoor_next_wave_condition_projection(value)
    if field == "condition":
        parsed = _parse_condition_expr(value)
        parts: list[str] = []
        for alternative in parsed:
            clauses = []
            for clause in alternative.get("clauses") or []:
                op = str(clause.get("op") or "").strip()
                label = CONDITION_OP_LABELS.get(op, op)
                if op == "PR":
                    clauses.append(f"{label} {clause.get('enhance_id')} x{clause.get('count')}")
                elif op == "MU":
                    clauses.append(f"{label} {clause.get('enhance_id')}")
                elif op == "TCLV":
                    clauses.append(f"{label} 角色{clause.get('char_id')} {clause.get('min_level')}-{clause.get('max_level')}")
                else:
                    args = clause.get("args") or []
                    clauses.append(f"{label}:{'/'.join(str(item) for item in args)}")
            if clauses:
                parts.append(" + ".join(clauses))
        return "；".join(parts)
    return ""


def _digitdoor_condition_field_audit_rows(
    config_rows_by_name: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in DIGITDOOR_CONDITION_FIELD_AUDIT_TARGETS:
        config_name = str(target["config"])
        field = str(target["field"])
        config_rows = config_rows_by_name.get(config_name) or []
        values = [row.get(field) for row in config_rows]
        non_empty_values = [value for value in values if _digitdoor_condition_value_has_content(value)]
        sample_values = _dedupe_preserve([str(value) for value in non_empty_values if str(value).strip()])[:8]
        sample_projection = ""
        for sample in sample_values:
            sample_projection = _digitdoor_condition_projection(field, sample)
            if sample_projection:
                break
        rows.append(
            {
                "config": config_name,
                "field": field,
                "row_count": len(config_rows),
                "non_empty_count": len(non_empty_values),
                "empty_count": len(config_rows) - len(non_empty_values),
                "unique_non_empty_count": len({str(value) for value in non_empty_values}),
                "sample_values": " | ".join(sample_values),
                "sample_projection": sample_projection,
                "meaning": target.get("meaning") or "",
                "runtime_slot": target.get("runtime_slot") or "",
                "active_note": target.get("active_note") or "",
                "current_boundary": "当前真实配置全空，先视为预留/非激活字段" if config_rows and not non_empty_values else "当前真实配置有值，可继续按运行时消费点解释" if non_empty_values else "当前配置表不存在或无行",
            }
        )
    return rows


def _write_digitdoor_condition_field_audit_markdown(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    config_dir: Path,
) -> None:
    lines = [
        "# DigitDoor condition field audit",
        "",
        "Static read-only audit of condition-like fields in DigitDoor config tables. This report separates currently active config semantics from schema placeholders.",
        "",
        f"- Config dir: `{config_dir}`",
        "",
        "## Findings",
        "",
    ]
    for row in rows:
        lines.append(
            f"- `{row.get('config')}.{row.get('field')}`: `{row.get('non_empty_count')}/{row.get('row_count')}` non-empty; "
            f"{row.get('current_boundary')}; runtime `{row.get('runtime_slot')}`"
        )
        if row.get("sample_values"):
            lines.append(f"  - sample: `{row.get('sample_values')}`")
        if row.get("sample_projection"):
            lines.append(f"  - projection: `{row.get('sample_projection')}`")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Empty condition columns are still useful schema evidence, but should not be treated as current gameplay selectors.",
            "- `MonsterRefreshPoint.nextWaveCondition` is a real runtime gate; visible Lua supports `CM` and `CR` conditions.",
            "- `SkillRefreshEffect.condition`, `SkillEnhanceEffect.condition`, and `CharacterSkillInfo.extCondition` being empty means the current true resource surface does not use those fields for door-effect availability or buff parameter branching.",
            "- This is documentation/indexing only; it is not guidance for patching, injection, or bypassing server choice.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _digitdoor_monster_refresh_point_value_projection_rows(point_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(
        point: dict[str, Any],
        field: str,
        raw_value: Any,
        projection: str,
        formula: str,
        meaning: str,
        runtime_slot: str,
    ) -> None:
        if not _is_nonzero_effect_value(raw_value) or not projection:
            return
        rows.append(
            {
                "point_id": point.get("id") or "",
                "level": point.get("level") or "",
                "refresh_wave": point.get("refresh_wave") or "",
                "monster_id": point.get("monster_id") or "",
                "monster_name": point.get("monster_name") or "",
                "field": field,
                "raw_value": raw_value,
                "projection": projection,
                "formula": formula,
                "meaning": meaning,
                "runtime_slot": runtime_slot,
            }
        )

    for point in point_rows:
        object_type = _as_int(point.get("object_type"))
        if object_type is not None:
            label = DIGITDOOR_MONSTER_TYPE_LABELS.get(object_type, "")
            add(
                point,
                "objectType",
                point.get("object_type"),
                f"{label or 'MonsterType'}({object_type})",
                "SmallMonster/Elite use batched refresh intervals; Boss is spawned as a boss wave and recorded in curWaveBossInfoDict",
                "刷新对象类型",
                "MonsterRefreshPoint.objectType",
            )
        add(
            point,
            "refreshTotalNum",
            point.get("refresh_total_num"),
            f"总计 {point.get('refresh_total_num')} 个",
            "total_spawn_count = cfg.refreshTotalNum",
            "当前刷新点总刷怪数量",
            "MonsterRefreshPoint.refreshTotalNum",
        )
        add(
            point,
            "refreshNum",
            point.get("refresh_num"),
            f"每批 {point.get('refresh_num')} 个",
            "batch_spawn_count = cfg.refreshNum",
            "每次刷新批量",
            "MonsterRefreshPoint.refreshNum",
        )
        add(
            point,
            "refreshTime",
            point.get("refresh_time"),
            f"刷新间隔 {_digitdoor_millisecond_projection(point.get('refresh_time'))}",
            "refresh_interval_seconds = cfg.refreshTime / 1000; used by GetCurWaveMonsterRefreshInterval for small/elite monsters",
            "同一刷新点分批刷新间隔",
            "MonsterRefreshPoint.refreshTime",
        )
        add(
            point,
            "waveTime",
            point.get("wave_time"),
            f"波长 {_digitdoor_millisecond_projection(point.get('wave_time'))}",
            "wave_seconds = cfg.waveTime / 1000; used by GetCurWaveRefreshInterval",
            "当前波次默认持续时间",
            "MonsterRefreshPoint.waveTime",
        )
        refresh_type = _as_int(point.get("refresh_type"))
        if refresh_type is not None:
            label = DIGITDOOR_REFRESH_TYPE_LABELS.get(refresh_type, "")
            add(
                point,
                "refreshType",
                point.get("refresh_type"),
                f"{label or 'RefreshType'}({refresh_type}) 生成位置规则",
                "GenerateMonsterPosByType checks RefreshMonsterType.TargetPos/TargetArea",
                "怪物生成位置类型",
                "MonsterRefreshPoint.refreshType",
            )
            pos_tokens = _digitdoor_number_tokens(point.get("refresh_pos"))
            if refresh_type == 1 and len(pos_tokens) >= 2:
                add(
                    point,
                    "refreshPos",
                    point.get("refresh_pos"),
                    f"固定坐标 x={pos_tokens[0]}, z={pos_tokens[1]}",
                    "IsFixedCreatePos uses refreshPos[1]/refreshPos[2] as x/z and snaps to the closest grid position",
                    "TargetPos 固定刷怪坐标",
                    "MonsterRefreshPoint.refreshPos",
                )
            elif refresh_type == 2 and len(pos_tokens) >= 4:
                add(
                    point,
                    "refreshPos",
                    point.get("refresh_pos"),
                    f"区域参数 minX={pos_tokens[0]}, minZ={pos_tokens[1]}, maxX={pos_tokens[2]}, maxZ={pos_tokens[3]}",
                    "GenerateMonsterPosByType reads refreshPos as minX/minZ/maxX/maxZ, then randomizes offsetX and disZ before snapping to a valid spawn position",
                    "TargetArea 区域刷怪参数",
                    "MonsterRefreshPoint.refreshPos",
                )
        add(
            point,
            "refreshOffsetDis",
            point.get("refresh_offset_dis"),
            f"备用 Z 偏移上限 {point.get('refresh_offset_dis')}",
            "Fallback GenerateMonsterPosByType uses min(cfg.refreshOffsetDis, sceneBounds.maxZ-minZ) when TargetArea is not providing a valid area",
            "非固定区域时的随机 Z 偏移上限",
            "MonsterRefreshPoint.refreshOffsetDis",
        )
        condition = str(point.get("next_wave_condition") or "").strip()
        if condition:
            add(
                point,
                "nextWaveCondition",
                condition,
                _digitdoor_next_wave_condition_projection(condition),
                "CheckNextWaveCondition splits nextWaveCondition by `;` then `|`; CM checks time remaining, CR checks remaining monster count",
                "进入下一波条件",
                "DigitDoorEntityMgr.nextWaveConditionList",
            )
    return rows


def _write_digitdoor_monster_refresh_point_value_projection_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    projection_rows: list[dict[str, Any]],
    files: dict[str, str],
) -> None:
    lines = [
        "# DigitDoor monster refresh point value projection report",
        "",
        "Static read-only projection of `MonsterRefreshPoint` wave rhythm fields into readable timing/count semantics.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Projection Samples", ""])
    for row in projection_rows[:80]:
        lines.append(
            f"- level `{row.get('level')}` wave `{row.get('refresh_wave')}` point `{row.get('point_id')}` field `{row.get('field')}` raw `{row.get('raw_value')}` => `{row.get('projection')}`"
        )
    lines.extend(
        [
            "",
            "## Runtime Evidence",
            "",
            "- `DigitDoorEntityMgr:GetCurWaveMonsterRefreshInterval` returns `cfg.refreshTime / 1000` for small/elite monsters.",
            "- `DigitDoorEntityMgr:GetCurWaveRefreshInterval` returns `cfg.waveTime / 1000`.",
            "- `DigitDoorEntityMgr:CheckNextWaveCondition` dispatches `CM` to remaining wave time and `CR` to remaining monster count.",
            "",
            "## Boundary",
            "",
            "- This is a static config-unit projection. Runtime spawning still depends on frame timing, object type, wave state, entity limits, and server/combat synchronization.",
            "",
            "## Files",
            "",
        ]
    )
    for label, file_path in files.items():
        lines.append(f"- `{label}`: `{file_path}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _digitdoor_monster_refresh_point_latent_field_rows(
    refresh_rows_raw: list[dict[str, Any]],
    lua_hits: list[dict[str, Any]],
    declared_fields: set[str],
) -> list[dict[str, Any]]:
    hit_topics = Counter(str(row.get("topic") or "") for row in lua_hits)
    rows: list[dict[str, Any]] = []
    for field_meta in DIGITDOOR_REFRESH_POINT_LATENT_FIELDS:
        field = str(field_meta["field"])
        values = [row.get(field) for row in refresh_rows_raw]
        non_empty_values = [value for value in values if _is_nonzero_effect_value(value)]
        declared = field in declared_fields or any(field in row for row in refresh_rows_raw)
        samples = _dedupe_preserve([str(value) for value in non_empty_values if str(value or "").strip()])[:8]
        rows.append(
            {
                "field": field,
                "declared_in_config": declared,
                "row_count": len(refresh_rows_raw),
                "non_empty_count": len(non_empty_values),
                "sample_values": " | ".join(samples),
                "meaning": field_meta["meaning"],
                "runtime_slot": field_meta["runtime_slot"],
                "lua_topic": field_meta["lua_topic"],
                "lua_hit_count": hit_topics.get(str(field_meta["lua_topic"]), 0),
                "current_boundary": "当前真实资源为空，暂不参与当前波次图鉴展示" if not non_empty_values else "当前资源已有值，可继续做投影",
            }
        )
    return rows


def _digitdoor_declared_config_fields(config_dir: Path, config_name: str) -> set[str]:
    path = config_dir / f"{config_name}.lua"
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"local\s+_key2index\s*=\s*\{(?P<body>.*?)\}", text, flags=re.S)
    if not match:
        return set()
    return {
        field.strip()
        for field in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", match.group("body"))
        if field.strip()
    }


def _write_digitdoor_monster_refresh_point_latent_field_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    field_rows: list[dict[str, Any]],
    lua_hits: list[dict[str, Any]],
    files: dict[str, str],
) -> None:
    lines = [
        "# DigitDoor monster refresh point latent field report",
        "",
        "Static read-only report for `MonsterRefreshPoint` fields that have visible Lua consumers but are empty in the current true config surface.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Field Boundary", ""])
    for row in field_rows:
        lines.append(
            f"- `{row.get('field')}` declared `{row.get('declared_in_config')}` non-empty `{row.get('non_empty_count')}`; runtime `{row.get('runtime_slot')}`; note `{row.get('current_boundary')}`"
        )
    lines.extend(["", "## Lua Evidence Samples", ""])
    for row in lua_hits[:80]:
        lines.append(
            f"- `{row.get('topic')}` `{row.get('file')}:{row.get('line')}` `{row.get('function')}` => `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Do not spend more current static time projecting these fields into the wiki until a future resource update gives them non-empty values.",
            "- The Lua consumers are still useful as schema notes: they explain how future `frontRow`, broken-line movement, and wave bubble fields would be consumed if enabled.",
            "",
            "## Files",
            "",
        ]
    )
    for label, file_path in files.items():
        lines.append(f"- `{label}`: `{file_path}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_monster_refresh_point_latent_field_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    digitdoor_logic_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None

    refresh_rows_raw = _parse_config_rows(config_dir, "MonsterRefreshPoint", resolved_lang_path, lang_map)
    lua_hits = _scan_lua_hits_for_topics(logic_dir, root, DIGITDOOR_REFRESH_POINT_LATENT_TOPIC_TERMS)
    declared_fields = _digitdoor_declared_config_fields(config_dir, "MonsterRefreshPoint")
    field_rows = _digitdoor_monster_refresh_point_latent_field_rows(refresh_rows_raw, lua_hits, declared_fields)
    non_empty_fields = [str(row.get("field") or "") for row in field_rows if _as_int(row.get("non_empty_count"))]
    stats = {
        "monster_refresh_point_count": len(refresh_rows_raw),
        "latent_field_count": len(field_rows),
        "latent_fields_with_values": len(non_empty_fields),
        "latent_fields_empty": len(field_rows) - len(non_empty_fields),
        "lua_hit_count": len(lua_hits),
        "lua_topic_counts": dict(Counter(str(row.get("topic") or "") for row in lua_hits)),
        "non_empty_fields": non_empty_fields,
    }
    required_topics = {str(field["lua_topic"]) for field in DIGITDOOR_REFRESH_POINT_LATENT_FIELDS}
    hit_topics = {str(row.get("topic") or "") for row in lua_hits}
    verdict = {
        "monster_refresh_points_available": len(refresh_rows_raw) > 0,
        "latent_fields_declared": all(bool(row.get("declared_in_config")) for row in field_rows),
        "latent_fields_empty_in_current_surface": not non_empty_fields,
        "lua_consumers_found_for_latent_fields": required_topics <= hit_topics,
    }

    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    fields_tsv = out_dir / "monster_refresh_point_latent_fields.tsv"
    hits_tsv = out_dir / "monster_refresh_point_latent_field_lua_hits.tsv"
    report_path = out_dir / "monster_refresh_point_latent_field_report.md"
    json_path = out_dir / "monster_refresh_point_latent_field_report.json"
    _write_tsv(
        fields_tsv,
        field_rows,
        [
            "field",
            "declared_in_config",
            "row_count",
            "non_empty_count",
            "sample_values",
            "meaning",
            "runtime_slot",
            "lua_topic",
            "lua_hit_count",
            "current_boundary",
        ],
    )
    _write_tsv(hits_tsv, lua_hits, ["topic", "file", "line", "function", "matched_terms", "snippet"])
    files = {
        "fields": str(fields_tsv),
        "lua_hits": str(hits_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    confirmed = all(verdict.values())
    _write_digitdoor_monster_refresh_point_latent_field_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        field_rows=field_rows,
        lua_hits=lua_hits,
        files=files,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": confirmed,
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "fields": field_rows,
                    "lua_hits": lua_hits[:160],
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": confirmed,
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


def build_fanxiu_digitdoor_monster_refresh_point_value_projection_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None

    refresh_rows_raw = _parse_config_rows(config_dir, "MonsterRefreshPoint", resolved_lang_path, lang_map)
    monster_rows_raw = _parse_config_rows(config_dir, "MonsterGroup", resolved_lang_path, lang_map)
    monster_info_rows = _parse_config_rows(config_dir, "MonsterInfo", resolved_lang_path, lang_map)
    monster_skill_rows = _parse_config_rows(config_dir, "MonsterSkill", resolved_lang_path, lang_map)
    monster_by_id = {_as_int(row.get("id")) or 0: row for row in monster_rows_raw if _as_int(row.get("id")) is not None}
    monster_info_by_id = {_as_int(row.get("id")) or 0: row for row in monster_info_rows if _as_int(row.get("id")) is not None}
    monster_skill_by_id = {_as_int(row.get("id")) or 0: row for row in monster_skill_rows if _as_int(row.get("id")) is not None}
    point_rows = _digitdoor_monster_refresh_rows(
        refresh_rows_raw,
        monster_by_id=monster_by_id,
        monster_info_by_id=monster_info_by_id,
        monster_skill_by_id=monster_skill_by_id,
    )
    projection_rows = _digitdoor_monster_refresh_point_value_projection_rows(point_rows)
    rows_by_level: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in projection_rows:
        rows_by_level[str(row.get("level") or "")].append(row)
    by_level_rows = [
        {
            "level": level,
            "projection_count": len(rows),
            "point_count": len({str(row.get("point_id") or "") for row in rows}),
            "projection_fields": _pipe_join(_dedupe_preserve([row.get("field") for row in rows])),
            "projection_summary": " | ".join(_dedupe_preserve([row.get("projection") for row in rows[:12]])),
        }
        for level, rows in sorted(rows_by_level.items(), key=lambda item: _sort_value(item[0]))
    ]
    field_counts = Counter(str(row.get("field") or "") for row in projection_rows)
    stats = {
        "monster_refresh_point_count": len(point_rows),
        "projection_row_count": len(projection_rows),
        "levels_with_projection": len(rows_by_level),
        "projection_field_counts": dict(field_counts),
        "next_wave_condition_count": field_counts.get("nextWaveCondition", 0),
    }
    verdict = {
        "monster_refresh_points_available": len(point_rows) > 0,
        "rhythm_projection_available": len(projection_rows) > 0,
        "refresh_time_seconds_projected": field_counts.get("refreshTime", 0) > 0,
        "wave_time_seconds_projected": field_counts.get("waveTime", 0) > 0,
        "next_wave_condition_projected": field_counts.get("nextWaveCondition", 0) > 0,
    }
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    projection_tsv = out_dir / "monster_refresh_point_value_projection.tsv"
    by_level_tsv = out_dir / "monster_refresh_point_value_projection_by_level.tsv"
    report_path = out_dir / "monster_refresh_point_value_projection_report.md"
    json_path = out_dir / "monster_refresh_point_value_projection_report.json"
    _write_tsv(
        projection_tsv,
        projection_rows,
        [
            "point_id",
            "level",
            "refresh_wave",
            "monster_id",
            "monster_name",
            "field",
            "raw_value",
            "projection",
            "formula",
            "meaning",
            "runtime_slot",
        ],
    )
    _write_tsv(by_level_tsv, by_level_rows, ["level", "projection_count", "point_count", "projection_fields", "projection_summary"])
    files = {
        "projections": str(projection_tsv),
        "by_level": str(by_level_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    _write_digitdoor_monster_refresh_point_value_projection_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        projection_rows=projection_rows,
        files=files,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": all(verdict.values()),
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "projections": projection_rows[:240],
                    "by_level": by_level_rows[:120],
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": all(verdict.values()),
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


DIGITDOOR_REFRESH_ATTRIBUTE_FIELDS = [
    ("ATTACK", "attack", "攻击", "DigitDoorBot.InitBaseData -> EntityData.attack", "SetAttack(refreshPoint.ATTACK) overrides MonsterGroup base attack when nonzero"),
    ("HP", "hp", "生命", "DigitDoorBot.InitBaseData -> EntityData.maxHp/currentHp", "SetMaxHp/SetCurrentHp(refreshPoint.HP) overrides MonsterGroup base HP when nonzero"),
    ("CRITICAL", "critical", "暴击", "DigitDoorMonsterData.critical", "SetCritical(cfg.CRITICAL * SkillRatio)"),
    ("ANTICRITICAL", "anti_critical", "抗暴", "DigitDoorMonsterData.antiCritical", "SetAntiCritical(cfg.ANTICRITICAL * SkillRatio)"),
    ("ATKSPEED", "atk_speed", "攻速", "DigitDoorMonsterData.attackSpeed", "SetAttackSpeed(cfg.ATKSPEED * SkillRatio)"),
    ("INCREASEDAMAGE", "increase_damage", "增伤", "DigitDoorMonsterData.increaseDamage", "SetIncreaseDamage(cfg.INCREASEDAMAGE * SkillRatio)"),
    ("REDUCEDAMAGE", "reduce_damage", "减伤", "DigitDoorMonsterData.reduceDamage", "SetReduceDamage(cfg.REDUCEDAMAGE * SkillRatio)"),
    ("killExp", "kill_exp", "击杀经验", "MonsterRefreshPoint.killExp", "static refresh-point reward/progress field"),
]


def _digitdoor_refresh_attribute_projection(config_field: str, api_field: str, label: str, value: Any) -> str:
    parsed = _as_int(value)
    if parsed is None:
        return ""
    if config_field in {"ATTACK", "HP", "killExp"}:
        return f"{label} {parsed}"
    if config_field in {"CRITICAL", "ANTICRITICAL", "INCREASEDAMAGE", "REDUCEDAMAGE"}:
        return f"{label} {_format_ratio(parsed)}"
    return f"{label} {parsed}"


def _digitdoor_monster_refresh_point_attribute_projection_rows(point_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    projection_rows: list[dict[str, Any]] = []
    field_summary: list[dict[str, Any]] = []
    for config_field, api_field, label, runtime_slot, formula in DIGITDOOR_REFRESH_ATTRIBUTE_FIELDS:
        nonzero_rows = [row for row in point_rows if _is_nonzero_effect_value(row.get(api_field))]
        sample_values = _dedupe_preserve([row.get(api_field) for row in nonzero_rows[:12]])
        field_summary.append(
            {
                "config_field": config_field,
                "api_field": api_field,
                "label": label,
                "nonzero_count": len(nonzero_rows),
                "sample_values": _pipe_join(sample_values),
                "runtime_slot": runtime_slot,
                "formula": formula,
                "note": "当前真实资源中有值" if nonzero_rows else "当前真实 MonsterRefreshPoint 导出面为空/零",
            }
        )
        for point in nonzero_rows:
            projection_rows.append(
                {
                    "point_id": point.get("id") or "",
                    "level": point.get("level") or "",
                    "refresh_wave": point.get("refresh_wave") or "",
                    "monster_id": point.get("monster_id") or "",
                    "monster_name": point.get("monster_name") or "",
                    "field": config_field,
                    "api_field": api_field,
                    "raw_value": point.get(api_field),
                    "projection": _digitdoor_refresh_attribute_projection(config_field, api_field, label, point.get(api_field)),
                    "formula": formula,
                    "meaning": f"刷新点怪物{label}配置",
                    "runtime_slot": runtime_slot,
                }
            )
    return projection_rows, field_summary


def _write_digitdoor_monster_refresh_point_attribute_projection_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    field_summary: list[dict[str, Any]],
    projection_rows: list[dict[str, Any]],
    files: dict[str, str],
) -> None:
    lines = [
        "# DigitDoor monster refresh point attribute projection report",
        "",
        "Static read-only projection of `MonsterRefreshPoint` monster battle attributes into readable labels, plus a field-presence boundary.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Field Summary", ""])
    for row in field_summary:
        lines.append(
            f"- `{row.get('config_field')}` -> `{row.get('label')}` nonzero `{row.get('nonzero_count')}` samples `{row.get('sample_values')}` note `{row.get('note')}`"
        )
    lines.extend(["", "## Projection Samples", ""])
    for row in projection_rows[:80]:
        lines.append(
            f"- level `{row.get('level')}` wave `{row.get('refresh_wave')}` point `{row.get('point_id')}` `{row.get('monster_name')}` `{row.get('field')}` raw `{row.get('raw_value')}` => `{row.get('projection')}`"
        )
    lines.extend(
        [
            "",
            "## Runtime Evidence",
            "",
            "- `DigitDoorBot:InitData` calls `self:InitBaseData(cfg.HP,cfg.ATTACK)` for refresh-point monster rows, then `InitBaseData` overrides runtime HP/Attack when these values are nonzero.",
            "- `DigitDoorMonsterData:InitData` maps MonsterGroup-style base stats (`MAXHP/ATTACK/ATKSPEED/CRITICAL/ANTICRITICAL/INCREASEDAMAGE/REDUCEDAMAGE`) into runtime entity properties before refresh-point HP/Attack overrides.",
            "- Current true `MonsterRefreshPoint` config has nonzero values only for `ATTACK` and `HP`; the other battle-stat columns are absent/zero in this resource surface.",
            "",
            "## Boundary",
            "",
            "- This is a static config projection. Runtime HP can still be affected by suppress/scaling logic and combat synchronization.",
            "",
            "## Files",
            "",
        ]
    )
    for label, file_path in files.items():
        lines.append(f"- `{label}`: `{file_path}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_monster_refresh_point_attribute_projection_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None

    refresh_rows_raw = _parse_config_rows(config_dir, "MonsterRefreshPoint", resolved_lang_path, lang_map)
    monster_rows_raw = _parse_config_rows(config_dir, "MonsterGroup", resolved_lang_path, lang_map)
    monster_info_rows = _parse_config_rows(config_dir, "MonsterInfo", resolved_lang_path, lang_map)
    monster_skill_rows = _parse_config_rows(config_dir, "MonsterSkill", resolved_lang_path, lang_map)
    monster_by_id = {_as_int(row.get("id")) or 0: row for row in monster_rows_raw if _as_int(row.get("id")) is not None}
    monster_info_by_id = {_as_int(row.get("id")) or 0: row for row in monster_info_rows if _as_int(row.get("id")) is not None}
    monster_skill_by_id = {_as_int(row.get("id")) or 0: row for row in monster_skill_rows if _as_int(row.get("id")) is not None}
    point_rows = _digitdoor_monster_refresh_rows(
        refresh_rows_raw,
        monster_by_id=monster_by_id,
        monster_info_by_id=monster_info_by_id,
        monster_skill_by_id=monster_skill_by_id,
    )
    projection_rows, field_summary = _digitdoor_monster_refresh_point_attribute_projection_rows(point_rows)
    field_counts = Counter(str(row.get("field") or "") for row in projection_rows)
    stats = {
        "monster_refresh_point_count": len(point_rows),
        "projection_row_count": len(projection_rows),
        "projection_field_counts": dict(field_counts),
        "nonzero_field_count": sum(1 for row in field_summary if _as_int(row.get("nonzero_count"))),
        "zero_or_missing_field_count": sum(1 for row in field_summary if not _as_int(row.get("nonzero_count"))),
    }
    verdict = {
        "monster_refresh_points_available": len(point_rows) > 0,
        "attribute_projection_available": len(projection_rows) > 0,
        "attack_projected": field_counts.get("ATTACK", 0) > 0,
        "hp_projected": field_counts.get("HP", 0) > 0,
        "only_attack_hp_nonzero_in_current_surface": {field for field, count in field_counts.items() if count > 0} <= {"ATTACK", "HP"},
    }
    confirmed = all(
        verdict[key]
        for key in [
            "monster_refresh_points_available",
            "attribute_projection_available",
            "attack_projected",
            "hp_projected",
        ]
    )
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    projection_tsv = out_dir / "monster_refresh_point_attribute_projection.tsv"
    field_tsv = out_dir / "monster_refresh_point_attribute_projection_fields.tsv"
    report_path = out_dir / "monster_refresh_point_attribute_projection_report.md"
    json_path = out_dir / "monster_refresh_point_attribute_projection_report.json"
    _write_tsv(
        projection_tsv,
        projection_rows,
        [
            "point_id",
            "level",
            "refresh_wave",
            "monster_id",
            "monster_name",
            "field",
            "api_field",
            "raw_value",
            "projection",
            "formula",
            "meaning",
            "runtime_slot",
        ],
    )
    _write_tsv(field_tsv, field_summary, ["config_field", "api_field", "label", "nonzero_count", "sample_values", "runtime_slot", "formula", "note"])
    files = {
        "projections": str(projection_tsv),
        "field_summary": str(field_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    _write_digitdoor_monster_refresh_point_attribute_projection_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        field_summary=field_summary,
        projection_rows=projection_rows,
        files=files,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": confirmed,
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "projections": projection_rows[:240],
                    "field_summary": field_summary,
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": confirmed,
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


def _digitdoor_probability_text(value: Any) -> str:
    parsed = _as_int(value)
    if parsed is None:
        return ""
    return f"{parsed}/10000 ({_format_ratio(parsed)})"


def _digitdoor_door_refresh_effect_pool(
    row: dict[str, Any],
    effect_by_customized_type: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    customized_values = [str(item) for item in _as_list(row.get("customizedType")) if str(item or "").strip()]
    effects: list[dict[str, Any]] = []
    for value in customized_values:
        effects.extend(effect_by_customized_type.get(value, []))
    return effects, customized_values


def _digitdoor_door_refresh_special_projection(row: dict[str, Any]) -> str:
    parts: list[str] = []
    debuff_type = _as_int(row.get("debuffDoorType"))
    if debuff_type is not None and debuff_type != 0:
        parts.append(f"负面门池 customizedType={debuff_type}")
    probability = _as_int(row.get("probability"))
    spx_types = [str(item) for item in _as_list(row.get("spxDoorType")) if str(item or "").strip()]
    rates = [str(item) for item in _as_list(row.get("rateList")) if str(item or "").strip()]
    if probability is not None and probability != 0:
        weighted: list[str] = []
        for index, spx_type in enumerate(spx_types):
            rate = rates[index] if index < len(rates) else ""
            rate_text = _digitdoor_probability_text(rate) if rate else ""
            weighted.append(f"{spx_type}{f'@{rate_text}' if rate_text else ''}")
        weighted_text = " / ".join(weighted) if weighted else "-"
        parts.append(f"特殊池触发字段 probability={_digitdoor_probability_text(probability)}，spxDoorType={weighted_text}")
    return "；".join(parts)


def _digitdoor_door_refresh_projection_rows(
    door_rows: list[dict[str, Any]],
    effect_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    effect_by_customized_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for effect in effect_rows:
        customized_type = str(effect.get("customizedType") or "")
        if customized_type:
            effect_by_customized_type[customized_type].append(effect)

    projection_rows: list[dict[str, Any]] = []
    for row in sorted(door_rows, key=lambda item: (_sort_value(item.get("level")), _sort_value(item.get("startRefreshTime")), _sort_value(item.get("id")))):
        side = _as_int(row.get("side"))
        side_label = DOOR_REFRESH_SIDE_LABELS.get(side or 0, str(side or ""))
        start_time = row.get("startRefreshTime") or ""
        effects, customized_values = _digitdoor_door_refresh_effect_pool(row, effect_by_customized_type)
        effect_ids = [effect.get("id") for effect in effects if effect.get("id") not in (None, "")]
        effect_previews = _dedupe_preserve([_plain(effect.get("effectShow")) or _plain(effect.get("showTips")) for effect in effects])[:8]
        effect_door_types = Counter(str(effect.get("doorType") or "") for effect in effects)
        refresh_offset_dis = row.get("refreshOffsetDis")
        refresh_offset_text = str(refresh_offset_dis if refresh_offset_dis not in (None, "") else 0)
        projection_rows.append(
            {
                "point_id": row.get("id") or "",
                "level": row.get("level") or "",
                "name": _plain(row.get("name")) or row.get("name") or "",
                "side": side if side is not None else "",
                "side_label": side_label,
                "start_refresh_time": start_time,
                "timing_projection": f"开局 {start_time} 秒后进入候选刷门列表" if start_time != "" else "",
                "door_type": row.get("doorType") or "",
                "customized_type_values": _pipe_join(customized_values),
                "effect_pool_count": len(effects),
                "effect_pool_ids": _pipe_join(effect_ids),
                "effect_pool_preview": " / ".join(effect_previews),
                "positive_effect_count": effect_door_types.get("1", 0),
                "negative_effect_count": effect_door_types.get("2", 0),
                "debuff_door_type": row.get("debuffDoorType") or "",
                "probability": row.get("probability") or "",
                "rate_list": _pipe_join(_as_list(row.get("rateList"))),
                "spx_door_type": _pipe_join(_as_list(row.get("spxDoorType"))),
                "special_rule_projection": _digitdoor_door_refresh_special_projection(row),
                "door_damage": row.get("doorDamage") or "",
                "attack": row.get("attack") or "",
                "volume": row.get("volume") or "",
                "hp": row.get("hp") or "",
                "refresh_offset_dis": refresh_offset_text,
                "position_projection": f"{side_label or '未知侧'}，场景起点 Z+{refresh_offset_text}",
                "server_boundary": "客户端按 startRefreshTime 上报 DoorRefreshPoint.id；服务端返回 doorVOS 后才确定 SkillRefreshEffect.id/resourceId/side。",
            }
        )

    rows_by_level: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in projection_rows:
        rows_by_level[str(row.get("level") or "")].append(row)
    by_level_rows = [
        {
            "level": level,
            "point_count": len(rows),
            "first_refresh_time": min((_sort_value(row.get("start_refresh_time"), 0) for row in rows), default=0),
            "last_refresh_time": max((_sort_value(row.get("start_refresh_time"), 0) for row in rows), default=0),
            "side_counts": " / ".join(
                f"{side}:{count}"
                for side, count in Counter(str(row.get("side_label") or row.get("side") or "") for row in rows).items()
                if side
            ),
            "customized_types": _pipe_join(_dedupe_preserve([value for row in rows for value in _split_digitdoor_pipe_text(row.get("customized_type_values"))])),
            "effect_pool_preview": " / ".join(_dedupe_preserve([row.get("effect_pool_preview") for row in rows if row.get("effect_pool_preview")])[:10]),
            "special_rule_count": sum(1 for row in rows if row.get("special_rule_projection")),
            "max_hp": max((_sort_value(row.get("hp"), 0) for row in rows), default=0),
        }
        for level, rows in sorted(rows_by_level.items(), key=lambda item: _sort_value(item[0]))
    ]

    declared_fields = [
        "id",
        "level",
        "name",
        "side",
        "startRefreshTime",
        "doorType",
        "customizedType",
        "probabilityList",
        "debuffDoorType",
        "probability",
        "rateList",
        "spxDoorType",
        "doorDamage",
        "attack",
        "volume",
        "hp",
        "refreshOffsetDis",
    ]
    field_summary: list[dict[str, Any]] = []
    for field in declared_fields:
        values = [row.get(field) for row in door_rows]
        non_empty_values = [value for value in values if _is_nonzero_effect_value(value)]
        samples = _dedupe_preserve([
            _pipe_join(value) if isinstance(value, list) else value
            for value in non_empty_values[:16]
        ])[:8]
        field_summary.append(
            {
                "field": field,
                "row_count": len(door_rows),
                "non_empty_count": len(non_empty_values),
                "unique_count": len({json.dumps(value, ensure_ascii=False, sort_keys=True) for value in non_empty_values}),
                "sample_values": _pipe_join(samples),
                "note": "当前真实资源有值" if non_empty_values else "当前真实资源为空/默认零",
            }
        )
    return projection_rows, by_level_rows, field_summary


def _write_digitdoor_door_refresh_projection_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    projection_rows: list[dict[str, Any]],
    lua_hits: list[dict[str, Any]],
    files: dict[str, str],
) -> None:
    lines = [
        "# DigitDoor door refresh projection report",
        "",
        "Static read-only projection of `DoorRefreshPoint` into a readable door timeline and candidate effect-pool surface.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Projection Samples", ""])
    for row in projection_rows[:80]:
        lines.append(
            f"- level `{row.get('level')}` point `{row.get('point_id')}` time `{row.get('start_refresh_time')}` side `{row.get('side_label')}` customized `{row.get('customized_type_values')}` effects `{row.get('effect_pool_preview')}` special `{row.get('special_rule_projection')}`"
        )
    lines.extend(["", "## Lua Evidence Samples", ""])
    for row in lua_hits[:80]:
        lines.append(
            f"- `{row.get('topic')}` `{row.get('file')}:{row.get('line')}` `{row.get('function')}` => `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Runtime Boundary",
            "",
            "- `UpdatePreCreateDoor(levelTime)` sends eligible `DoorRefreshPoint.id` values to `CM_DigitDoorRefDoorFun`; static config only exposes candidate refresh points.",
            "- `SM_DigitDoorRefDoorFun` raises `CreateDoor`; `CheckCreateDoor` then maps `doorVOS.resourceId` back to `DoorRefreshPoint` and `doorVOS.id` to `SkillRefreshEffect`.",
            "- Therefore the final door effect is server-confirmed. This report does not attempt to force or predict a live server choice beyond the static candidate pool.",
            "",
            "## Files",
            "",
        ]
    )
    for label, file_path in files.items():
        lines.append(f"- `{label}`: `{file_path}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_door_refresh_projection_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    digitdoor_logic_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None

    door_rows = _parse_config_rows(config_dir, "DoorRefreshPoint", resolved_lang_path, lang_map)
    effect_rows = _parse_config_rows(config_dir, "SkillRefreshEffect", resolved_lang_path, lang_map)
    projection_rows, by_level_rows, field_summary = _digitdoor_door_refresh_projection_rows(door_rows, effect_rows)
    lua_hits = _scan_lua_hits_for_topics(logic_dir, root, DOOR_REFRESH_TOPIC_TERMS)
    unresolved_pool_rows = [row for row in projection_rows if row.get("customized_type_values") and not _as_int(row.get("effect_pool_count"))]
    special_rows = [row for row in projection_rows if row.get("special_rule_projection")]
    stats = {
        "door_refresh_point_count": len(door_rows),
        "level_count": len(by_level_rows),
        "projection_row_count": len(projection_rows),
        "effect_pool_linked_row_count": len(projection_rows) - len(unresolved_pool_rows),
        "unresolved_effect_pool_row_count": len(unresolved_pool_rows),
        "special_rule_row_count": len(special_rows),
        "debuff_door_type_row_count": sum(1 for row in projection_rows if row.get("debuff_door_type")),
        "probability_row_count": sum(1 for row in projection_rows if row.get("probability")),
        "side_counts": dict(Counter(str(row.get("side_label") or row.get("side") or "") for row in projection_rows)),
        "customized_type_counts": dict(Counter(value for row in projection_rows for value in _split_digitdoor_pipe_text(row.get("customized_type_values")))),
        "lua_hit_count": len(lua_hits),
        "lua_topic_counts": dict(Counter(str(row.get("topic") or "") for row in lua_hits)),
    }
    hit_topics = {str(row.get("topic") or "") for row in lua_hits}
    verdict = {
        "door_refresh_points_available": len(door_rows) > 0,
        "levels_available": len(by_level_rows) > 0,
        "effect_pools_linked": not unresolved_pool_rows,
        "pre_create_request_found": "pre_create_request" in hit_topics,
        "server_create_response_found": "server_create_response" in hit_topics,
        "door_position_found": "door_position" in hit_topics,
        "door_entity_data_found": "door_entity_data" in hit_topics,
    }

    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    projection_tsv = out_dir / "door_refresh_projection.tsv"
    by_level_tsv = out_dir / "door_refresh_projection_by_level.tsv"
    field_tsv = out_dir / "door_refresh_projection_fields.tsv"
    hit_tsv = out_dir / "door_refresh_projection_lua_hits.tsv"
    report_path = out_dir / "door_refresh_projection_report.md"
    json_path = out_dir / "door_refresh_projection_report.json"
    _write_tsv(
        projection_tsv,
        projection_rows,
        [
            "point_id",
            "level",
            "name",
            "side",
            "side_label",
            "start_refresh_time",
            "timing_projection",
            "door_type",
            "customized_type_values",
            "effect_pool_count",
            "effect_pool_ids",
            "effect_pool_preview",
            "positive_effect_count",
            "negative_effect_count",
            "debuff_door_type",
            "probability",
            "rate_list",
            "spx_door_type",
            "special_rule_projection",
            "door_damage",
            "attack",
            "volume",
            "hp",
            "refresh_offset_dis",
            "position_projection",
            "server_boundary",
        ],
    )
    _write_tsv(
        by_level_tsv,
        by_level_rows,
        [
            "level",
            "point_count",
            "first_refresh_time",
            "last_refresh_time",
            "side_counts",
            "customized_types",
            "effect_pool_preview",
            "special_rule_count",
            "max_hp",
        ],
    )
    _write_tsv(field_tsv, field_summary, ["field", "row_count", "non_empty_count", "unique_count", "sample_values", "note"])
    _write_tsv(hit_tsv, lua_hits, ["topic", "file", "line", "function", "matched_terms", "snippet"])
    files = {
        "projections": str(projection_tsv),
        "by_level": str(by_level_tsv),
        "field_summary": str(field_tsv),
        "lua_hits": str(hit_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    confirmed = all(verdict.values())
    _write_digitdoor_door_refresh_projection_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        projection_rows=projection_rows,
        lua_hits=lua_hits,
        files=files,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": confirmed,
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "projections": projection_rows[:240],
                    "by_level": by_level_rows[:160],
                    "field_summary": field_summary,
                    "lua_hits": lua_hits[:160],
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": confirmed,
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


def _digitdoor_gain_buff_ack_msg_fields(logic_dir: Path) -> list[str]:
    path = logic_dir / "DigitDoorNetLogic.lua"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"function\s+_M\.SM_DigitDoorGainBuffFun\s*\([^)]*\)(?P<body>.*?)(?:\nfunction\s+_M\.|\Z)", text, flags=re.S)
    if not match:
        return []
    return sorted(set(re.findall(r"\bmsg\.([A-Za-z_][A-Za-z0-9_]*)", match.group("body"))))


def _digitdoor_door_gain_buff_effect_rows(
    effect_rows: list[dict[str, Any]],
    door_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    door_count_by_customized_type: Counter[str] = Counter()
    for row in door_rows:
        for customized_type in _as_list(row.get("customizedType")):
            text = str(customized_type or "").strip()
            if text:
                door_count_by_customized_type[text] += 1

    rows: list[dict[str, Any]] = []
    for effect in sorted(effect_rows, key=lambda item: (_sort_value(item.get("customizedType")), _sort_value(item.get("charId")), _sort_value(item.get("id")))):
        effect_id = _as_int(effect.get("id"))
        customized_type = str(effect.get("customizedType") or "")
        door_type = _as_int(effect.get("doorType"))
        skill_ids = [str(item) for item in _as_list(effect.get("skill")) if str(item or "").strip()]
        effect_show = _plain(effect.get("effectShow"))
        show_tips = _plain(effect.get("showTips"))
        rows.append(
            {
                "effect_id": effect_id if effect_id is not None else effect.get("id") or "",
                "customized_type": customized_type,
                "customized_type_label": DOOR_BUFFER_TYPE_LABELS.get(_as_int(customized_type) or 0, ""),
                "door_type": door_type if door_type is not None else effect.get("doorType") or "",
                "door_type_label": DOOR_BROKEN_TYPE_LABELS.get(door_type or 0, ""),
                "char_id": effect.get("charId") or "",
                "effect_show": effect_show,
                "show_tips": show_tips,
                "skill_ids": _pipe_join(skill_ids),
                "skill_count": len(skill_ids),
                "refresh_point_count": door_count_by_customized_type.get(customized_type, 0),
                "local_apply_hint": "StartDead -> UpdateRoleSkillAttrList(effect_id) -> UpdateDigitDoorSkillInBattle -> SkillEnhanceEffect",
                "claim_packet_hint": "DeadEnd -> CM_DigitDoorGainBuff.buffList includes this effect_id; visible SM handler only checks code.",
            }
        )
    return rows


def _digitdoor_door_gain_buff_flow_rows() -> list[dict[str, Any]]:
    return [
        {
            "order": 1,
            "phase": "spawn_confirm",
            "source": "DigitDoorEntityMgr.CheckCreateDoor",
            "summary": "SM_DigitDoorRefDoor returns doorVOS; each VO maps resourceId to DoorRefreshPoint and id to SkillRefreshEffect, then creates DigitDoorBuffEffectView.",
            "authority_boundary": "server_confirmed_spawn",
        },
        {
            "order": 2,
            "phase": "touch_or_break",
            "source": "DigitDoorBuffEffectView.UpdateMove / OnHit",
            "summary": "Non-breakable doors move into collision checks; breakable doors enter Dead via hit/death logic.",
            "authority_boundary": "client_runtime_collision_or_combat",
        },
        {
            "order": 3,
            "phase": "mutual_exclusion",
            "source": "DigitDoorEntityMgr.CheckMutualExclusion / RecordCollisionDoor",
            "summary": "Touched doors record startRefreshTime so another door at the same refresh time is ignored locally.",
            "authority_boundary": "client_runtime_state",
        },
        {
            "order": 4,
            "phase": "local_counter",
            "source": "DigitDoorBuffEffectView.StartDead",
            "summary": "StartDead calls DigitDoorMgr.Model.UpdateRoleSkillAttrList(buffCfg.id), incrementing a local effect counter before the GainBuff ack path.",
            "authority_boundary": "client_runtime_state",
        },
        {
            "order": 5,
            "phase": "local_apply",
            "source": "DigitDoorFightComponent.UpdateDigitDoorSkillInBattle",
            "summary": "The fight component reads RoleSkillEnhanceEffList, resolves SkillRefreshEffect.skill to SkillEnhanceEffect rows, and applies them to matching role SkillActor instances.",
            "authority_boundary": "client_runtime_skill_update",
        },
        {
            "order": 6,
            "phase": "claim_send",
            "source": "DigitDoorBuffEffectView.DeadEnd / DigitDoorNetLogic.CM_DigitDoorGainBuffFun",
            "summary": "DeadEnd sends CM_DigitDoorGainBuff.buffList containing the gained SkillRefreshEffect id.",
            "authority_boundary": "client_to_server_claim_notification",
        },
        {
            "order": 7,
            "phase": "claim_ack",
            "source": "DigitDoorNetLogic.SM_DigitDoorGainBuffFun",
            "summary": "The visible response handler only checks msg.code == 0 and has no visible local buff application body.",
            "authority_boundary": "thin_server_ack",
        },
    ]


def _write_digitdoor_door_gain_buff_flow_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    flow_rows: list[dict[str, Any]],
    effect_rows: list[dict[str, Any]],
    lua_hits: list[dict[str, Any]],
    files: dict[str, str],
) -> None:
    lines = [
        "# DigitDoor door gain-buff flow report",
        "",
        "Static read-only boundary report for the flow after a spawned door is touched/broken and converted into local skill-enhance effects plus a thin GainBuff server acknowledgement.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Flow", ""])
    for row in flow_rows:
        lines.append(f"{row.get('order')}. `{row.get('phase')}` `{row.get('source')}`: {row.get('summary')}")
    lines.extend(["", "## Effect Samples", ""])
    for row in effect_rows[:40]:
        lines.append(
            f"- effect `{row.get('effect_id')}` customized `{row.get('customized_type')}` `{row.get('effect_show')}` skills `{row.get('skill_ids')}` refresh points `{row.get('refresh_point_count')}`"
        )
    lines.extend(["", "## Lua Evidence Samples", ""])
    for row in lua_hits[:80]:
        lines.append(
            f"- `{row.get('topic')}` `{row.get('file')}:{row.get('line')}` `{row.get('function')}` => `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Local visible application is `StartDead -> UpdateRoleSkillAttrList -> UpdateDigitDoorSkillInBattle`, not the `SM_DigitDoorGainBuff` body.",
            "- `CM_DigitDoorGainBuff.buffList` is still important as a server claim/ack boundary, but current visible Lua does not consume `SM_DigitDoorGainBuff.buffList` to apply buffs.",
            "- This report is for static understanding and wiki/API context only; it is not guidance for patching, injection, or live server-choice control.",
            "",
            "## Files",
            "",
        ]
    )
    for label, file_path in files.items():
        lines.append(f"- `{label}`: `{file_path}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_door_gain_buff_flow_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    digitdoor_logic_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None

    door_rows = _parse_config_rows(config_dir, "DoorRefreshPoint", resolved_lang_path, lang_map)
    effect_rows_raw = _parse_config_rows(config_dir, "SkillRefreshEffect", resolved_lang_path, lang_map)
    effect_rows = _digitdoor_door_gain_buff_effect_rows(effect_rows_raw, door_rows)
    flow_rows = _digitdoor_door_gain_buff_flow_rows()
    lua_hits = _scan_lua_hits_for_topics(logic_dir, root, DOOR_GAIN_BUFF_TOPIC_TERMS)
    ack_msg_fields = _digitdoor_gain_buff_ack_msg_fields(logic_dir)
    hit_topics = {str(row.get("topic") or "") for row in lua_hits}
    door_type_counts = Counter(str(row.get("door_type") or "") for row in effect_rows)
    customized_type_counts = Counter(str(row.get("customized_type") or "") for row in effect_rows)
    stats = {
        "skill_refresh_effect_count": len(effect_rows),
        "door_refresh_point_count": len(door_rows),
        "effects_with_skill_count": sum(1 for row in effect_rows if _as_int(row.get("skill_count"))),
        "breakable_effect_count": door_type_counts.get("1", 0),
        "touch_effect_count": door_type_counts.get("2", 0),
        "negative_effect_count": customized_type_counts.get("4", 0),
        "door_type_counts": dict(door_type_counts),
        "customized_type_counts": dict(customized_type_counts),
        "lua_hit_count": len(lua_hits),
        "lua_topic_counts": dict(Counter(str(row.get("topic") or "") for row in lua_hits)),
        "sm_gain_buff_msg_fields": ack_msg_fields,
    }
    verdict = {
        "skill_refresh_effects_available": len(effect_rows) > 0,
        "collision_or_break_source_found": bool({"collision_gate", "collision_record"} & hit_topics),
        "local_counter_found": "local_counter" in hit_topics,
        "battle_apply_found": "battle_apply" in hit_topics,
        "gain_buff_claim_send_found": "claim_send" in hit_topics,
        "gain_buff_ack_found": "claim_ack" in hit_topics,
        "sm_gain_buff_ack_is_thin": set(ack_msg_fields) <= {"code"},
    }

    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    effect_tsv = out_dir / "door_gain_buff_effects.tsv"
    flow_tsv = out_dir / "door_gain_buff_flow_steps.tsv"
    hit_tsv = out_dir / "door_gain_buff_lua_hits.tsv"
    report_path = out_dir / "door_gain_buff_flow_report.md"
    json_path = out_dir / "door_gain_buff_flow_report.json"
    _write_tsv(
        effect_tsv,
        effect_rows,
        [
            "effect_id",
            "customized_type",
            "customized_type_label",
            "door_type",
            "door_type_label",
            "char_id",
            "effect_show",
            "show_tips",
            "skill_ids",
            "skill_count",
            "refresh_point_count",
            "local_apply_hint",
            "claim_packet_hint",
        ],
    )
    _write_tsv(flow_tsv, flow_rows, ["order", "phase", "source", "summary", "authority_boundary"])
    _write_tsv(hit_tsv, lua_hits, ["topic", "file", "line", "function", "matched_terms", "snippet"])
    files = {
        "effects": str(effect_tsv),
        "flow_steps": str(flow_tsv),
        "lua_hits": str(hit_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    confirmed = all(verdict.values())
    _write_digitdoor_door_gain_buff_flow_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        flow_rows=flow_rows,
        effect_rows=effect_rows,
        lua_hits=lua_hits,
        files=files,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": confirmed,
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "effects": effect_rows[:160],
                    "flow_steps": flow_rows,
                    "lua_hits": lua_hits[:160],
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": confirmed,
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


def _digitdoor_door_customized_type_semantic_label(
    customized_type: str,
    effect_rows: list[dict[str, Any]],
) -> str:
    parsed = _as_int(customized_type)
    enum_label = DOOR_BUFFER_TYPE_LABELS.get(parsed or 0)
    if enum_label:
        return enum_label
    shows = _dedupe_preserve([_plain(row.get("effectShow")) for row in effect_rows if _plain(row.get("effectShow"))])
    char_ids = [_as_int(row.get("charId")) for row in effect_rows]
    char_ids = [item for item in char_ids if item is not None and item > 0]
    if char_ids and len(char_ids) == len(effect_rows) and len(shows) == 1:
        if shows[0] == "重置绝招":
            return "特殊替换：重置绝招"
        return f"角色专属：{shows[0]}"
    if shows:
        return f"全体增益池：{' / '.join(shows[:4])}"
    return "候选门池"


def _digitdoor_door_customized_type_semantic_role(
    *,
    effect_rows: list[dict[str, Any]],
    direct_count: int,
    debuff_count: int,
    spx_count: int,
) -> str:
    char_ids = [_as_int(row.get("charId")) for row in effect_rows]
    char_ids = [item for item in char_ids if item is not None and item > 0]
    if debuff_count:
        return "negative_replacement_pool"
    if spx_count and not direct_count:
        return "special_replacement_pool"
    if direct_count and char_ids and len(char_ids) == len(effect_rows):
        return "direct_character_pool"
    if direct_count:
        return "direct_global_pool"
    return "effect_pool_only"


def _digitdoor_door_customized_type_semantics_rows(
    *,
    door_rows: list[dict[str, Any]],
    effect_rows: list[dict[str, Any]],
    character_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    effect_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    direct_rows_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    debuff_rows_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    spx_rows_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in effect_rows:
        value = str(row.get("customizedType") or "").strip()
        if value:
            effect_by_type[value].append(row)
    for row in door_rows:
        for value in _as_list(row.get("customizedType")):
            text = str(value or "").strip()
            if text:
                direct_rows_by_type[text].append(row)
        debuff_type = str(row.get("debuffDoorType") or "").strip()
        if debuff_type:
            debuff_rows_by_type[debuff_type].append(row)
        for value in _as_list(row.get("spxDoorType")):
            text = str(value or "").strip()
            if text:
                spx_rows_by_type[text].append(row)

    char_name_by_id = {
        _as_int(row.get("id")): _plain(row.get("name"))
        for row in character_rows
        if _as_int(row.get("id")) is not None
    }
    all_types = set(effect_by_type) | set(direct_rows_by_type) | set(debuff_rows_by_type) | set(spx_rows_by_type)
    rows: list[dict[str, Any]] = []
    for customized_type in sorted(all_types, key=_sort_value):
        effects = sorted(effect_by_type.get(customized_type, []), key=lambda item: (_sort_value(item.get("charId")), _sort_value(item.get("id"))))
        direct_rows = direct_rows_by_type.get(customized_type, [])
        debuff_rows = debuff_rows_by_type.get(customized_type, [])
        spx_rows = spx_rows_by_type.get(customized_type, [])
        effect_ids = [row.get("id") for row in effects if row.get("id") not in (None, "")]
        effect_shows = _dedupe_preserve([_plain(row.get("effectShow")) for row in effects if _plain(row.get("effectShow"))])
        refresh_weight_values = _dedupe_preserve([
            row.get("refreshWeights")
            for row in effects
            if row.get("refreshWeights") not in (None, "")
        ])
        put_back_values = _dedupe_preserve([
            row.get("putBack")
            for row in effects
            if row.get("putBack") not in (None, "")
        ])
        char_ids = _dedupe_preserve([
            _as_int(row.get("charId"))
            for row in effects
            if (_as_int(row.get("charId")) is not None and (_as_int(row.get("charId")) or 0) > 0)
        ])
        char_names = [char_name_by_id.get(char_id, str(char_id)) for char_id in char_ids]
        source_fields: list[str] = []
        if effects:
            source_fields.append("SkillRefreshEffect.customizedType")
        if direct_rows:
            source_fields.append("DoorRefreshPoint.customizedType")
        if debuff_rows:
            source_fields.append("DoorRefreshPoint.debuffDoorType")
        if spx_rows:
            source_fields.append("DoorRefreshPoint.spxDoorType")
        direct_levels = {_as_int(row.get("level")) for row in direct_rows if _as_int(row.get("level")) is not None}
        debuff_levels = {_as_int(row.get("level")) for row in debuff_rows if _as_int(row.get("level")) is not None}
        spx_levels = {_as_int(row.get("level")) for row in spx_rows if _as_int(row.get("level")) is not None}
        direct_times = sorted(
            {_as_int(row.get("startRefreshTime")) for row in direct_rows if _as_int(row.get("startRefreshTime")) is not None},
            key=_sort_value,
        )
        label = _digitdoor_door_customized_type_semantic_label(customized_type, effects)
        role = _digitdoor_door_customized_type_semantic_role(
            effect_rows=effects,
            direct_count=len(direct_rows),
            debuff_count=len(debuff_rows),
            spx_count=len(spx_rows),
        )
        rows.append(
            {
                "customized_type": customized_type,
                "semantic_label": label,
                "static_role": role,
                "effect_count": len(effects),
                "effect_ids": _pipe_join(effect_ids),
                "effect_shows": " / ".join(effect_shows),
                "refresh_weight_values": _pipe_join(refresh_weight_values),
                "refresh_weight_summary": _digitdoor_value_count_summary(effects, "refreshWeights"),
                "weighted_effect_count": sum(1 for row in effects if row.get("refreshWeights") not in (None, "")),
                "put_back_values": _pipe_join(put_back_values),
                "put_back_summary": _digitdoor_value_count_summary(effects, "putBack"),
                "put_back_reusable_count": sum(1 for row in effects if str(row.get("putBack") or "") == "1"),
                "character_count": len(char_ids),
                "character_ids": _pipe_join(char_ids),
                "character_names": " / ".join(char_names),
                "direct_refresh_point_count": len(direct_rows),
                "direct_level_count": len(direct_levels),
                "direct_level_range": f"{min(direct_levels)}-{max(direct_levels)}" if direct_levels else "",
                "direct_time_values": _pipe_join(direct_times[:12]),
                "debuff_door_count": len(debuff_rows),
                "debuff_level_count": len(debuff_levels),
                "spx_count": len(spx_rows),
                "spx_level_count": len(spx_levels),
                "source_fields": _pipe_join(source_fields),
                "notes": "Lua DoorBufferType only names 1-4; higher values are inferred from config distribution and effect payloads.",
            }
        )
    return rows


def _write_digitdoor_door_customized_type_semantics_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    rows: list[dict[str, Any]],
    lua_hits: list[dict[str, Any]],
    files: dict[str, str],
) -> None:
    lines = [
        "# DigitDoor door customizedType semantics report",
        "",
        "Static read-only classification of `SkillRefreshEffect.customizedType` and the matching `DoorRefreshPoint` direct/debuff/special-pool fields.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Type Summary", ""])
    for row in rows:
        lines.append(
            f"- type `{row.get('customized_type')}` `{row.get('semantic_label')}` role `{row.get('static_role')}` effects `{row.get('effect_count')}` weights `{row.get('refresh_weight_summary')}` putBack `{row.get('put_back_summary')}` direct `{row.get('direct_refresh_point_count')}` debuff `{row.get('debuff_door_count')}` spx `{row.get('spx_count')}` shows `{row.get('effect_shows')}` chars `{row.get('character_names')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- `DoorBufferType` in visible Lua only names `Low/Middle/High/Negative = 1/2/3/4`; current resources use higher `customizedType` values as config-level pool ids.",
            "- `DoorRefreshPoint.customizedType` is the direct candidate pool; `debuffDoorType` and `spxDoorType` are replacement/special-pool fields on the refresh point.",
            "- This report labels those pools from static config shape and visible Lua consumers; it does not claim live server selection authority.",
            "",
            "## Lua Evidence Samples",
            "",
        ]
    )
    for row in lua_hits[:80]:
        lines.append(
            f"- `{row.get('topic')}` `{row.get('file')}:{row.get('line')}` `{row.get('function')}` => `{row.get('snippet')}`"
        )
    lines.extend(["", "## Files", ""])
    for label, file_path in files.items():
        lines.append(f"- `{label}`: `{file_path}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_door_customized_type_semantics_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    digitdoor_logic_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None
    door_rows = _parse_config_rows(config_dir, "DoorRefreshPoint", resolved_lang_path, lang_map)
    effect_rows = _parse_config_rows(config_dir, "SkillRefreshEffect", resolved_lang_path, lang_map)
    character_rows = _parse_config_rows(config_dir, "CharacterMainInfo", resolved_lang_path, lang_map)
    rows = _digitdoor_door_customized_type_semantics_rows(
        door_rows=door_rows,
        effect_rows=effect_rows,
        character_rows=character_rows,
    )
    lua_hits = _scan_lua_hits_for_topics(logic_dir, root, DOOR_CUSTOMIZED_TYPE_TOPIC_TERMS)
    role_counts = Counter(str(row.get("static_role") or "") for row in rows)
    source_type_counts = Counter()
    for row in rows:
        for field in _split_digitdoor_pipe_text(row.get("source_fields")):
            source_type_counts[field] += 1
    known_lua_enum_values = {str(key) for key in DOOR_BUFFER_TYPE_LABELS}
    current_type_values = {str(row.get("customized_type") or "") for row in rows}
    stats = {
        "customized_type_count": len(rows),
        "skill_refresh_effect_count": len(effect_rows),
        "door_refresh_point_count": len(door_rows),
        "direct_type_count": source_type_counts.get("DoorRefreshPoint.customizedType", 0),
        "debuff_type_count": source_type_counts.get("DoorRefreshPoint.debuffDoorType", 0),
        "spx_type_count": source_type_counts.get("DoorRefreshPoint.spxDoorType", 0),
        "role_counts": dict(role_counts),
        "known_lua_enum_values": sorted(known_lua_enum_values, key=_sort_value),
        "config_values_not_named_by_lua_enum": sorted(current_type_values - known_lua_enum_values, key=_sort_value),
        "lua_hit_count": len(lua_hits),
        "lua_topic_counts": dict(Counter(str(row.get("topic") or "") for row in lua_hits)),
    }
    verdict = {
        "customized_types_available": len(rows) > 0,
        "direct_pools_found": source_type_counts.get("DoorRefreshPoint.customizedType", 0) > 0,
        "special_replacement_pool_found": role_counts.get("special_replacement_pool", 0) > 0,
        "negative_replacement_pool_found": role_counts.get("negative_replacement_pool", 0) > 0,
        "character_pools_identified": role_counts.get("direct_character_pool", 0) > 0,
        "lua_enum_is_partial": bool(current_type_values - known_lua_enum_values),
    }
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    type_tsv = out_dir / "door_customized_type_semantics.tsv"
    hit_tsv = out_dir / "door_customized_type_semantics_lua_hits.tsv"
    report_path = out_dir / "door_customized_type_semantics_report.md"
    json_path = out_dir / "door_customized_type_semantics_report.json"
    _write_tsv(
        type_tsv,
        rows,
        [
            "customized_type",
            "semantic_label",
            "static_role",
            "effect_count",
            "effect_ids",
            "effect_shows",
            "refresh_weight_values",
            "refresh_weight_summary",
            "weighted_effect_count",
            "put_back_values",
            "put_back_summary",
            "put_back_reusable_count",
            "character_count",
            "character_ids",
            "character_names",
            "direct_refresh_point_count",
            "direct_level_count",
            "direct_level_range",
            "direct_time_values",
            "debuff_door_count",
            "debuff_level_count",
            "spx_count",
            "spx_level_count",
            "source_fields",
            "notes",
        ],
    )
    _write_tsv(hit_tsv, lua_hits, ["topic", "file", "line", "function", "matched_terms", "snippet"])
    files = {
        "types": str(type_tsv),
        "lua_hits": str(hit_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    confirmed = all(verdict.values())
    _write_digitdoor_door_customized_type_semantics_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        rows=rows,
        lua_hits=lua_hits,
        files=files,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": confirmed,
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "types": rows,
                    "lua_hits": lua_hits[:160],
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": confirmed,
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


def _digitdoor_level_monster_rows(
    level_rows: list[dict[str, Any]],
    refresh_rows: list[dict[str, Any]],
    *,
    monster_info_by_id: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_level: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in refresh_rows:
        level = _as_int(row.get("level"))
        if level is not None:
            by_level[level].append(row)
    rows: list[dict[str, Any]] = []
    for level in sorted(level_rows, key=lambda item: _sort_value(item.get("id"))):
        level_id = _as_int(level.get("id"))
        level_refresh = by_level.get(level_id or -1, [])
        declared_monster_ids = [_as_int(item) for item in _as_list(level.get("monster"))]
        clean_declared_ids = [item for item in declared_monster_ids if item is not None]
        refresh_monster_ids = sorted(
            {
                parsed
                for row in level_refresh
                if (parsed := _as_int(row.get("monster_id"))) is not None
            },
            key=_sort_value,
        )
        wave_ids = sorted(
            {
                parsed
                for row in level_refresh
                if (parsed := _as_int(row.get("refresh_wave"))) is not None
            },
            key=_sort_value,
        )
        rows.append(
            {
                "level": level_id if level_id is not None else "",
                "name": _plain(level.get("name")),
                "stage": level.get("stage") or "",
                "layer": level.get("layer") or "",
                "sub_layer": level.get("subLayer") or "",
                "declared_monster_ids": ",".join(str(item) for item in clean_declared_ids),
                "declared_monster_names": ",".join(_plain(monster_info_by_id.get(item, {}).get("name")) for item in clean_declared_ids),
                "declared_monster_unresolved_ids": ",".join(str(item) for item in clean_declared_ids if item not in monster_info_by_id),
                "refresh_point_count": len(level_refresh),
                "wave_count": len(wave_ids),
                "first_wave": wave_ids[0] if wave_ids else "",
                "last_wave": wave_ids[-1] if wave_ids else "",
                "refresh_monster_ids": ",".join(str(item) for item in refresh_monster_ids),
                "refresh_monster_count": len(refresh_monster_ids),
                "max_attack": max((_sort_value(row.get("attack"), 0) for row in level_refresh), default=0),
                "max_hp": max((_sort_value(row.get("hp"), 0) for row in level_refresh), default=0),
            }
        )
    return rows


def _write_digitdoor_monster_refresh_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    level_rows: list[dict[str, Any]],
    monster_rows: list[dict[str, Any]],
    hit_rows: list[dict[str, Any]],
    files: dict[str, str],
) -> None:
    lines = [
        "# DigitDoor monster refresh report",
        "",
        "Static read-only join for DigitDoor level monster declarations, monster refresh waves, monster groups, monster info, and monster skills.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Join Rules", ""])
    lines.extend(
        [
            "- `Level.monster` is a display/declaration list and resolves through `DigitDoor_MonsterInfo[id]`.",
            "- `MonsterRefreshPoint.level + refreshWave` is the runtime wave schedule source used by `DigitDoorEntityMgr:GetRefreshPointCfg`.",
            "- `MonsterRefreshPoint.monsterId` resolves to `MonsterGroup.id`; `MonsterGroup.baseId` resolves to `MonsterInfo.id`.",
            "- `MonsterGroup.defaultSkill` is a comma-separated list of `MonsterSkill.id` values, consumed by `DigitDoorBotView`.",
            "",
            "## Level Samples",
            "",
        ]
    )
    for row in level_rows[:40]:
        lines.append(
            f"- level `{row.get('level')}` `{row.get('name')}` waves `{row.get('wave_count')}` refresh `{row.get('refresh_point_count')}` declared `{row.get('declared_monster_names')}` refresh_monsters `{row.get('refresh_monster_ids')}`"
        )
    lines.extend(["", "## Monster Samples", ""])
    for row in monster_rows[:60]:
        lines.append(
            f"- monster `{row.get('monster_id')}` `{row.get('name')}` base `{row.get('base_id')}` skills `{row.get('default_skill_ids')}` desc `{row.get('description')}`"
        )
    lines.extend(["", "## Evidence Samples", ""])
    for row in hit_rows[:80]:
        lines.append(f"- `{row.get('topic')}` `{row.get('file')}:{row.get('line')}` `{row.get('snippet')}`")
    lines.extend(["", "## Files", ""])
    for label, file_path in files.items():
        lines.append(f"- `{label}`: `{file_path}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_monster_refresh_probe(
    *,
    digitdoor_config_dir: str | Path | None = None,
    digitdoor_logic_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    logic_dir = _resolve_export_dir(digitdoor_logic_dir, export_root=export_root) or _find_default_logic_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None

    level_rows_raw = _parse_config_rows(config_dir, "Level", resolved_lang_path, lang_map)
    refresh_rows_raw = _parse_config_rows(config_dir, "MonsterRefreshPoint", resolved_lang_path, lang_map)
    monster_rows_raw = _parse_config_rows(config_dir, "MonsterGroup", resolved_lang_path, lang_map)
    monster_info_rows = _parse_config_rows(config_dir, "MonsterInfo", resolved_lang_path, lang_map)
    monster_skill_rows = _parse_config_rows(config_dir, "MonsterSkill", resolved_lang_path, lang_map)

    monster_by_id = {_as_int(row.get("id")) or 0: row for row in monster_rows_raw if _as_int(row.get("id")) is not None}
    monster_info_by_id = {_as_int(row.get("id")) or 0: row for row in monster_info_rows if _as_int(row.get("id")) is not None}
    monster_skill_by_id = {_as_int(row.get("id")) or 0: row for row in monster_skill_rows if _as_int(row.get("id")) is not None}

    monster_rows = _digitdoor_monster_rows(
        monster_rows_raw,
        monster_info_by_id=monster_info_by_id,
        monster_skill_by_id=monster_skill_by_id,
    )
    skill_rows = _digitdoor_monster_skill_rows(monster_skill_rows)
    refresh_rows = _digitdoor_monster_refresh_rows(
        refresh_rows_raw,
        monster_by_id=monster_by_id,
        monster_info_by_id=monster_info_by_id,
        monster_skill_by_id=monster_skill_by_id,
    )
    level_rows = _digitdoor_level_monster_rows(
        level_rows_raw,
        refresh_rows,
        monster_info_by_id=monster_info_by_id,
    )
    hit_rows = _scan_lua_hits_for_topics(logic_dir, root, MONSTER_REFRESH_TOPIC_TERMS)

    refresh_rows_with_monster = [row for row in refresh_rows if str(row.get("monster_id") or "").strip()]
    default_skill_refs = [
        skill_id
        for row in monster_rows_raw
        for skill_id in _parse_int_csv(row.get("defaultSkill"))
    ]
    level_declared_refs = [
        monster_id
        for row in level_rows_raw
        for value in _as_list(row.get("monster"))
        if (monster_id := _as_int(value)) is not None
    ]
    stats = {
        "level_count": len(level_rows_raw),
        "monster_refresh_point_count": len(refresh_rows_raw),
        "monster_refresh_rows_with_monster_id": len(refresh_rows_with_monster),
        "monster_group_count": len(monster_rows_raw),
        "monster_info_count": len(monster_info_rows),
        "monster_skill_count": len(monster_skill_rows),
        "monster_skill_export_count": len(skill_rows),
        "refresh_level_wave_count": len({(row.get("level"), row.get("refresh_wave")) for row in refresh_rows}),
        "refresh_monster_unique_count": len({str(row.get("monster_id") or "") for row in refresh_rows_with_monster}),
        "default_skill_ref_count": len(default_skill_refs),
        "default_skill_ref_unique_count": len(set(default_skill_refs)),
        "level_declared_monster_ref_count": len(level_declared_refs),
        "level_declared_monster_unique_count": len(set(level_declared_refs)),
        "lua_hit_count": len(hit_rows),
        "lua_topic_counts": dict(Counter(str(row.get("topic") or "") for row in hit_rows)),
    }
    verdict = {
        "refresh_monster_ids_resolve_monster_group": all(_as_int(row.get("monster_id")) in monster_by_id for row in refresh_rows_with_monster),
        "monster_group_base_ids_resolve_monster_info": all((_as_int(row.get("baseId")) or 0) in monster_info_by_id for row in monster_rows_raw),
        "monster_default_skills_resolve_monster_skill": all(skill_id in monster_skill_by_id for skill_id in default_skill_refs),
        "level_declared_monsters_resolve_monster_info": all(monster_id in monster_info_by_id for monster_id in level_declared_refs),
        "runtime_uses_monster_refresh_point_by_level_wave": any(row.get("topic") == "refresh_config_index" for row in hit_rows),
        "runtime_uses_monster_group_and_skill_tables": any(row.get("topic") == "monster_group_lookup" for row in hit_rows)
        and any(row.get("topic") == "monster_skill_lookup" for row in hit_rows),
    }

    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    level_tsv = out_dir / "monster_refresh_levels.tsv"
    refresh_tsv = out_dir / "monster_refresh_points.tsv"
    monster_tsv = out_dir / "monster_refresh_monsters.tsv"
    skill_tsv = out_dir / "monster_refresh_skills.tsv"
    hit_tsv = out_dir / "monster_refresh_lua_hits.tsv"
    report_path = out_dir / "monster_refresh_report.md"
    json_path = out_dir / "monster_refresh_report.json"

    _write_tsv(
        level_tsv,
        level_rows,
        [
            "level",
            "name",
            "stage",
            "layer",
            "sub_layer",
            "declared_monster_ids",
            "declared_monster_names",
            "declared_monster_unresolved_ids",
            "refresh_point_count",
            "wave_count",
            "first_wave",
            "last_wave",
            "refresh_monster_ids",
            "refresh_monster_count",
            "max_attack",
            "max_hp",
        ],
    )
    _write_tsv(
        refresh_tsv,
        refresh_rows,
        [
            "id",
            "level",
            "refresh_wave",
            "game_type",
            "object_type",
            "monster_id",
            "monster_name",
            "base_id",
            "monster_type",
            "attack",
            "hp",
            "critical",
            "anti_critical",
            "atk_speed",
            "increase_damage",
            "reduce_damage",
            "kill_exp",
            "wave_time",
            "refresh_total_num",
            "refresh_time",
            "refresh_num",
            "refresh_offset_dis",
            "refresh_type",
            "refresh_pos",
            "next_wave_condition",
            "default_skill_ids",
            "unresolved_skill_ids",
        ],
    )
    _write_tsv(
        monster_tsv,
        monster_rows,
        [
            "monster_id",
            "name",
            "text_name",
            "base_id",
            "info_name",
            "type",
            "info_type",
            "model_id",
            "speed",
            "move_stop_distance",
            "default_skill_ids",
            "default_skill_count",
            "unresolved_skill_ids",
            "restrained_count",
            "drops",
            "weight",
            "reduce_damage",
            "evasion",
            "repel",
            "description",
            "unlock_level",
            "sort",
        ],
    )
    _write_tsv(
        skill_tsv,
        skill_rows,
        [
            "id",
            "type",
            "type_name",
            "trigger",
            "trigger_name",
            "timeline_id",
            "cd",
            "damage",
            "buff_id",
            "release_count",
            "duration",
            "hit_time",
            "distance",
            "hp_limit",
            "summon_monster_id",
            "summon_hp",
            "summon_attack",
            "runtime_hint",
        ],
    )
    _write_tsv(hit_tsv, hit_rows, ["topic", "file", "line", "function", "matched_terms", "snippet"])
    files = {
        "levels": str(level_tsv),
        "refresh_points": str(refresh_tsv),
        "monsters": str(monster_tsv),
        "skills": str(skill_tsv),
        "lua_hits": str(hit_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    _write_digitdoor_monster_refresh_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        level_rows=level_rows,
        monster_rows=monster_rows,
        hit_rows=hit_rows,
        files=files,
    )
    json_path.write_text(
        json.dumps(
            {
                "source": {
                    "digitdoor_config_dir": str(config_dir),
                    "digitdoor_logic_dir": str(logic_dir),
                    "lang_path": str(resolved_lang_path or ""),
                },
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "levels": level_rows[:120],
                    "refresh_points": refresh_rows[:120],
                    "monsters": monster_rows[:120],
                    "lua_hits": hit_rows[:160],
                },
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "confirmed": all(verdict.values()),
        "output_dir": str(out_dir),
        "stats": stats,
        "verdict": verdict,
        "files": files,
    }


def _digitdoor_door_effect_hint_calibration_rows(effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for effect in effects:
        show_percent_values = _digitdoor_effect_show_percent_values(effect.get("effect_show_plain") or effect.get("effect_show"))
        add_attr_values = _digitdoor_effect_add_attr_values(effect)
        add_attr_percent_values = [item["percent_value"] for item in add_attr_values]
        matched = ""
        note = ""
        if show_percent_values and add_attr_percent_values:
            matched = "yes" if any(float(show_value) in add_attr_percent_values for show_value in show_percent_values) else "no"
            if matched == "no":
                note = "effectShow percentage differs from linked BuffEffect.addAttr; treat hint as runtime-config chain, not display replacement."
        rows.append(
            {
                "id": effect.get("id"),
                "char_id": effect.get("char_id"),
                "customized_type": effect.get("customized_type"),
                "door_type_label": effect.get("door_type_label"),
                "effect_show": effect.get("effect_show_plain") or effect.get("effect_show") or "",
                "show_percent_values": ",".join(f"{value:g}%" for value in show_percent_values),
                "effect_hint_preview": effect.get("effect_hint_preview") or "",
                "add_attr_values": " / ".join(
                    f"{item['attr_key']}:{item['raw_value']} ({item['attr_label']} {item['percent_text']})" for item in add_attr_values
                ),
                "matched_show_percent": matched,
                "note": note,
            }
        )
    return rows


def _write_digitdoor_door_effect_hint_calibration_report(path: Path, rows: list[dict[str, Any]], *, config_dir: Path) -> None:
    counts = Counter(str(row.get("matched_show_percent") or "blank") for row in rows)
    mismatches = [row for row in rows if row.get("matched_show_percent") == "no"]
    lines = [
        "# DigitDoor 门效果数值提示校准",
        "",
        f"- 配置目录：`{config_dir}`",
        f"- 门效果行：{len(rows)}",
        f"- 文案百分比与 linked `BuffEffect.addAttr` 匹配：{counts.get('yes', 0)}",
        f"- 无直接 addAttr 百分比可比：{counts.get('blank', 0)}",
        f"- 不一致：{counts.get('no', 0)}",
        "",
        "## 结论",
        "",
        "- `effect_hint_preview` 是从 `SkillRefreshEffect.skill -> SkillEnhanceEffect -> BuffEffect` 反连出来的运行时配置链提示。",
        "- 它可以帮助理解实际会挂到角色/技能上的 buff 字段，但不能无条件替代 `SkillRefreshEffect.effectShow/showTips` 文案。",
        "- 当前只有 `customizedType=1` 的 3 条基础全局增益行出现文案百分比与 linked addAttr 不一致；其余可比较行均一致。",
        "- 可见 Lua 客户端只按时间向 `CM_DigitDoorRefDoor.resourceList` 上报 `DoorRefreshPoint.id`，具体 `SkillRefreshEffect.id` 由 `SM_DigitDoorRefDoor.doorVOS.id` 回包后创建门实体；静态客户端侧不能证明服务端如何从池内随机/加权选择。",
        "",
        "## 不一致行",
        "",
    ]
    if mismatches:
        lines.extend(
            [
                "| SkillRefreshEffect | customizedType | effectShow | linked addAttr | hint | note |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in mismatches:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(row.get("id") or ""),
                        str(row.get("customized_type") or ""),
                        str(row.get("effect_show") or ""),
                        str(row.get("add_attr_values") or ""),
                        str(row.get("effect_hint_preview") or ""),
                        str(row.get("note") or ""),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- 当前没有不一致行。")
    lines.extend(
        [
            "",
            "## 使用边界",
            "",
            "- 图鉴 UI 可以同时显示文案和 hint；如果两者不同，应优先把 hint 标注为 linked runtime-config chain，而不是更正文案。",
            "- 若未来要解释这 3 条为什么展示 `+30%` 但 linked addAttr 是 `+10%`，下一步应查服务端或运行时样本，而不是重复全量扫描 `SkillRefreshEffect`。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_catalog(
    *,
    digitdoor_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    config_dir = _resolve_export_dir(digitdoor_config_dir, export_root=export_root) or _find_default_config_dir(root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None

    character_rows = _parse_config_rows(config_dir, "CharacterMainInfo", resolved_lang_path, lang_map)
    level_rows = _parse_config_rows(config_dir, "CharacterLevel", resolved_lang_path, lang_map)
    skill_info_rows = _parse_config_rows(config_dir, "CharacterSkillInfo", resolved_lang_path, lang_map)
    skill_show_rows = _parse_config_rows(config_dir, "CharacterSkillShow", resolved_lang_path, lang_map)
    skill_enhance_effect_rows = _parse_config_rows(config_dir, "SkillEnhanceEffect", resolved_lang_path, lang_map)
    enhance_rows = _parse_config_rows(config_dir, "SkillEnhance", resolved_lang_path, lang_map)
    door_effect_rows = _parse_config_rows(config_dir, "SkillRefreshEffect", resolved_lang_path, lang_map)
    buff_rows = _parse_config_rows(config_dir, "BuffEffect", resolved_lang_path, lang_map)
    level_config_rows = _parse_config_rows(config_dir, "Level", resolved_lang_path, lang_map)
    door_refresh_rows = _parse_config_rows(config_dir, "DoorRefreshPoint", resolved_lang_path, lang_map)
    stage_rows = _parse_config_rows(config_dir, "DigitDoorStage", resolved_lang_path, lang_map)
    activity_rows = _parse_config_rows(config_dir, "DigitDoorActivity", resolved_lang_path, lang_map)
    pre_level_reward_rows = _parse_config_rows(config_dir, "DigitDoorPreLevelReward", resolved_lang_path, lang_map)

    level_by_character = _group_by_int(level_rows, "charId")
    skill_show_by_character = _group_by_int(skill_show_rows, "partnerId")
    logic_skill_by_character = _group_by_int(skill_info_rows, "charId")
    skill_enhance_effect_by_character = _group_by_int(skill_enhance_effect_rows, "charId")
    enhance_by_character = _group_by_int(enhance_rows, "charId")
    door_effect_by_character = _group_by_int(door_effect_rows, "charId")
    door_refresh_by_level = _group_by_int(door_refresh_rows, "level")
    character_ids = {_as_int(row.get("id")) for row in character_rows if _as_int(row.get("id")) is not None}
    skill_info_by_id = {_as_int(row.get("id")) or 0: row for row in skill_info_rows if _as_int(row.get("id")) is not None}
    skill_show_by_id = {_as_int(row.get("id")) or 0: row for row in skill_show_rows if _as_int(row.get("id")) is not None}
    skill_enhance_effect_by_id = {_as_int(row.get("id")) or 0: row for row in skill_enhance_effect_rows if _as_int(row.get("id")) is not None}
    buff_by_id = {_as_int(row.get("id")) or 0: row for row in buff_rows if _as_int(row.get("id")) is not None}
    enhance_by_id = {_as_int(row.get("id")) or 0: row for row in enhance_rows if _as_int(row.get("id")) is not None}
    item_by_id = _load_items_by_id(root)
    door_skill_ref_ids = [
        parsed
        for row in door_effect_rows
        for raw in _as_list(row.get("skill"))
        if (parsed := _as_int(raw)) is not None
    ]
    unresolved_door_skill_ids = sorted(
        {
            skill_id
            for skill_id in door_skill_ref_ids
            if skill_id not in skill_show_by_id and skill_id not in skill_info_by_id and skill_id not in skill_enhance_effect_by_id
        }
    )

    cards = [
        _compact_character_card(
            row,
            level_rows=level_by_character.get(_as_int(row.get("id")) or 0, []),
            skill_show_rows=skill_show_by_character.get(_as_int(row.get("id")) or 0, []),
            logic_skill_rows=logic_skill_by_character.get(_as_int(row.get("id")) or 0, []),
            skill_enhance_effect_rows=skill_enhance_effect_by_character.get(_as_int(row.get("id")) or 0, []),
            door_effect_rows=door_effect_by_character.get(_as_int(row.get("id")) or 0, []),
            logic_by_id=skill_info_by_id,
            skill_show_by_id=skill_show_by_id,
            enhance_effect_by_id=skill_enhance_effect_by_id,
            buff_by_id=buff_by_id,
        )
        for row in sorted(character_rows, key=lambda item: (_sort_value(item.get("sort")), _sort_value(item.get("id"))))
    ]
    global_door_effect_rows = []
    for row in door_effect_rows:
        char_id = _as_int(row.get("charId"))
        if char_id is None or char_id not in character_ids:
            global_door_effect_rows.append(row)
    global_door_effects = [
        _compact_door_effect(row, skill_show_by_id, skill_info_by_id, skill_enhance_effect_by_id, buff_by_id)
        for row in sorted(global_door_effect_rows, key=lambda item: (_sort_value(item.get("doorType")), _sort_value(item.get("customizedType")), _sort_value(item.get("id"))))
    ]
    custom_enhance_groups = [
        _compact_enhance_group(char_id, rows, enhance_by_id)
        for char_id, rows in sorted(enhance_by_character.items(), key=lambda item: _sort_value(item[0]))
    ]
    level_summaries = [
        _compact_level_summary(row, door_rows=door_refresh_by_level.get(_as_int(row.get("id")) or 0, []), item_by_id=item_by_id)
        for row in sorted(level_config_rows, key=lambda item: (_sort_value(item.get("stage")), _sort_value(item.get("layer")), _sort_value(item.get("id"))))
    ]
    stage_summaries = [
        {
            **row,
            "name_plain": _plain(row.get("name")),
            "level_count": sum(1 for level in level_summaries if str(level.get("stage")) == str(row.get("id"))),
        }
        for row in sorted(stage_rows, key=lambda item: (_sort_value(item.get("type")), _sort_value(item.get("id"))))
    ]
    stats = {
        "character_count": len(cards),
        "level_row_count": len(level_rows),
        "skill_show_count": len(skill_show_rows),
        "skill_logic_count": len(skill_info_rows),
        "skill_enhance_effect_count": len(skill_enhance_effect_rows),
        "enhance_count": len(enhance_rows),
        "door_effect_count": len(door_effect_rows),
        "character_door_effect_count": sum(len(card.get("door_effects") or []) for card in cards),
        "global_door_effect_count": len(global_door_effects),
        "buff_count": len(buff_rows),
        "level_config_count": len(level_config_rows),
        "door_refresh_count": len(door_refresh_rows),
        "stage_count": len(stage_rows),
        "pre_level_reward_count": len(pre_level_reward_rows),
        "skill_enhance_group_count": len(custom_enhance_groups),
        "door_skill_ref_count": len(door_skill_ref_ids),
        "door_skill_ref_unique_count": len(set(door_skill_ref_ids)),
        "door_skill_ref_show_resolved_count": len({skill_id for skill_id in door_skill_ref_ids if skill_id in skill_show_by_id}),
        "door_skill_ref_logic_resolved_count": len({skill_id for skill_id in door_skill_ref_ids if skill_id in skill_info_by_id}),
        "door_skill_ref_enhance_effect_resolved_count": len({skill_id for skill_id in door_skill_ref_ids if skill_id in skill_enhance_effect_by_id}),
        "door_skill_ref_unresolved_count": len(unresolved_door_skill_ids),
        "enhance_type_counts": dict(Counter(str(row.get("type") or "") for row in enhance_rows)),
        "door_type_counts": dict(Counter(str(row.get("doorType") or "") for row in door_effect_rows)),
    }

    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = out_dir / "digitdoor_catalog.json"
    character_summary_tsv = out_dir / "character_summary.tsv"
    enhance_summary_tsv = out_dir / "enhance_summary.tsv"
    door_effect_summary_tsv = out_dir / "door_effect_summary.tsv"
    door_effect_hint_calibration_tsv = out_dir / "door_effect_hint_calibration.tsv"
    door_effect_hint_calibration_report = out_dir / "door_effect_hint_calibration_report.md"
    condition_field_audit_tsv = out_dir / "condition_field_audit.tsv"
    condition_field_audit_report = out_dir / "condition_field_audit_report.md"
    skill_enhance_condition_nodes_tsv = out_dir / "skill_enhance_condition_nodes.tsv"
    skill_enhance_condition_edges_tsv = out_dir / "skill_enhance_condition_edges.tsv"
    skill_enhance_condition_report = out_dir / "skill_enhance_condition_report.md"
    level_summary_tsv = out_dir / "level_summary.tsv"
    report_path = out_dir / "digitdoor_catalog_report.md"
    all_door_effects = [*global_door_effects, *(effect for card in cards for effect in card.get("door_effects") or [])]
    door_effect_hint_calibration_rows = _digitdoor_door_effect_hint_calibration_rows(all_door_effects)
    condition_field_audit_rows = _digitdoor_condition_field_audit_rows(
        {
            "SkillRefreshEffect": door_effect_rows,
            "SkillEnhanceEffect": skill_enhance_effect_rows,
            "CharacterSkillInfo": skill_info_rows,
            "CharacterSkillShow": skill_show_rows,
            "SkillEnhance": enhance_rows,
            "MonsterRefreshPoint": _parse_config_rows(config_dir, "MonsterRefreshPoint", resolved_lang_path, lang_map),
            "DigitDoorStage": stage_rows,
            "DigitDoorActivity": activity_rows,
            "DigitDoorPreLevelReward": pre_level_reward_rows,
        }
    )
    skill_enhance_condition_nodes, skill_enhance_condition_edges, skill_enhance_condition_stats = _digitdoor_skill_enhance_condition_graph_rows(custom_enhance_groups)

    catalog = {
        "schema_version": DIGITDOOR_CATALOG_SCHEMA_VERSION,
        "source": {
            "digitdoor_config_dir": str(config_dir),
            "lang_path": str(resolved_lang_path or ""),
        },
        "stats": stats,
        "cards": cards,
        "custom_enhance_groups": custom_enhance_groups,
        "global_door_effects": global_door_effects,
        "levels": level_summaries,
        "stages": stage_summaries,
        "pre_level_rewards": pre_level_reward_rows,
        "unresolved_door_skill_ids": unresolved_door_skill_ids,
    }
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_tsv(
        character_summary_tsv,
        [
            {
                "id": card.get("id"),
                "name": card.get("name"),
                "positioning": card.get("positioning"),
                "skill_name": card.get("skill_name"),
                "max_level": card.get("max_level"),
                "skill_count": card.get("skill_count"),
                "logic_skill_count": card.get("logic_skill_count"),
                "skill_enhance_effect_count": card.get("skill_enhance_effect_count"),
                "door_effect_count": card.get("door_effect_count"),
            }
            for card in cards
        ],
        ["id", "name", "positioning", "skill_name", "max_level", "skill_count", "logic_skill_count", "skill_enhance_effect_count", "door_effect_count"],
    )
    _write_tsv(
        enhance_summary_tsv,
        [
            {
                "id": item.get("id"),
                "char_id": item.get("char_id"),
                "group_name": group.get("name"),
                "name": item.get("name"),
                "type_label": item.get("type_label"),
                "quality_label": item.get("quality_label"),
                "description": item.get("description_plain"),
                "condition_raw": item.get("condition_raw"),
                "prereq_ids": ",".join(str(value) for value in item.get("prereq_ids") or []),
                "unlock_show_ids": ",".join(str(value) for value in item.get("unlock_show_ids") or []),
            }
            for group in custom_enhance_groups
            for item in group.get("enhances") or []
        ],
        ["id", "char_id", "group_name", "name", "type_label", "quality_label", "description", "condition_raw", "prereq_ids", "unlock_show_ids"],
    )
    _write_tsv(
        door_effect_summary_tsv,
        [
            {
                "id": item.get("id"),
                "char_id": item.get("char_id"),
                "door_type_label": item.get("door_type_label"),
                "customized_type": item.get("customized_type"),
                "effect_show": item.get("effect_show_plain"),
                "show_tips": item.get("show_tips_plain"),
                "skill_ids": ",".join(str(value) for value in item.get("skill_ids") or []),
                "effect_hint_preview": item.get("effect_hint_preview") or "",
                "effect_hints": " / ".join(str(value) for value in item.get("effect_hints") or []),
            }
            for item in all_door_effects
        ],
        ["id", "char_id", "door_type_label", "customized_type", "effect_show", "show_tips", "skill_ids", "effect_hint_preview", "effect_hints"],
    )
    _write_tsv(
        door_effect_hint_calibration_tsv,
        door_effect_hint_calibration_rows,
        [
            "id",
            "char_id",
            "customized_type",
            "door_type_label",
            "effect_show",
            "show_percent_values",
            "effect_hint_preview",
            "add_attr_values",
            "matched_show_percent",
            "note",
        ],
    )
    _write_digitdoor_door_effect_hint_calibration_report(door_effect_hint_calibration_report, door_effect_hint_calibration_rows, config_dir=config_dir)
    _write_tsv(
        condition_field_audit_tsv,
        condition_field_audit_rows,
        [
            "config",
            "field",
            "row_count",
            "non_empty_count",
            "empty_count",
            "unique_non_empty_count",
            "sample_values",
            "sample_projection",
            "meaning",
            "runtime_slot",
            "active_note",
            "current_boundary",
        ],
    )
    _write_digitdoor_condition_field_audit_markdown(condition_field_audit_report, condition_field_audit_rows, config_dir=config_dir)
    _write_tsv(
        skill_enhance_condition_nodes_tsv,
        skill_enhance_condition_nodes,
        [
            "id",
            "group_char_id",
            "group_name",
            "name",
            "type_label",
            "quality_label",
            "description",
            "condition_raw",
            "condition_text",
            "condition_alternative_count",
            "prereq_count",
            "prereqs",
            "mutex_count",
            "mutexes",
            "level_range_count",
            "level_ranges",
            "unlock_show_count",
            "unlock_show",
            "limit",
            "weight",
        ],
    )
    _write_tsv(
        skill_enhance_condition_edges_tsv,
        skill_enhance_condition_edges,
        [
            "source_id",
            "source_name",
            "source_group_char_id",
            "source_group_name",
            "relation",
            "target_id",
            "target_name",
            "target_group_char_id",
            "target_group_name",
            "note",
        ],
    )
    _write_digitdoor_skill_enhance_condition_graph_markdown(
        skill_enhance_condition_report,
        node_rows=skill_enhance_condition_nodes,
        edge_rows=skill_enhance_condition_edges,
        stats=skill_enhance_condition_stats,
        config_dir=config_dir,
    )
    _write_tsv(
        level_summary_tsv,
        [
            {
                "id": item.get("id"),
                "name": item.get("name_plain") or item.get("name"),
                "stage": item.get("stage"),
                "group": item.get("group"),
                "layer": item.get("layer"),
                "sub_layer": item.get("sub_layer"),
                "init_char": item.get("init_char"),
                "door_count": item.get("door_count"),
                "customized_types": ",".join(item.get("customized_types") or []),
                "reward_show_title": item.get("reward_show_title_plain"),
                "recommend_tips": item.get("recommend_tips_plain"),
            }
            for item in level_summaries
        ],
        ["id", "name", "stage", "group", "layer", "sub_layer", "init_char", "door_count", "customized_types", "reward_show_title", "recommend_tips"],
    )
    report_path.write_text(
        "\n".join(
            [
                "# DigitDoor 配置图鉴目录",
                "",
                f"- 配置目录：`{config_dir}`",
                f"- 角色：{stats['character_count']}",
                f"- 角色等级行：{stats['level_row_count']}",
                f"- 展示技能：{stats['skill_show_count']}",
                f"- 逻辑技能：{stats['skill_logic_count']}",
                f"- 技能增强效果：{stats['skill_enhance_effect_count']}",
                f"- 技能强化：{stats['enhance_count']}",
                f"- 技能强化组：{stats['skill_enhance_group_count']}",
                f"- 门效果：{stats['door_effect_count']}",
                f"- 角色门效果：{stats['character_door_effect_count']}，全局/负面门效果：{stats['global_door_effect_count']}",
                f"- 门效果技能引用：{stats['door_skill_ref_unique_count']} unique，增强效果解析 {stats['door_skill_ref_enhance_effect_resolved_count']} 个，未解析 {stats['door_skill_ref_unresolved_count']} 个",
                f"- 关卡：{stats['level_config_count']}",
                f"- 刷门点：{stats['door_refresh_count']}",
                "",
                "## 重点 join",
                "",
                "- `CharacterMainInfo.id` -> `CharacterLevel.charId` / `CharacterSkillShow.partnerId` / `CharacterSkillInfo.charId` / `SkillRefreshEffect.charId`。",
                "- `SkillRefreshEffect.skill` 已反连到 `SkillEnhanceEffect.id`，并尽量补充同 id 的 `CharacterSkillShow.id` / `CharacterSkillInfo.id` 信息，能看到门效果会投放哪些技能增强效果。",
                "- `SkillRefreshEffect.skill -> SkillEnhanceEffect -> BuffEffect` 也会提炼 `effect_hints`，例如 `ATTACK:-500` 显示为 `攻击 -5%`，`mutexTimeline` 显示为替换时间线。",
                "- `SkillEnhance` 是另一套自定义/肉鸽强化树；当前只在组内解析 `PR|id_count`、`MU|id` 强化依赖/互斥引用，以及 `TCLV|char_min_max` 等级区间，不强行并入 `CharacterMainInfo` 角色卡。",
                "- `Level.id` -> `DoorRefreshPoint.level`，可按关卡查看刷门时间、门类型、customizedType 和奖励摘要。",
                "",
                "## 输出文件",
                "",
                f"- `digitdoor_catalog.json`：完整结构化图鉴。",
                f"- `character_summary.tsv`：角色摘要。",
                f"- `enhance_summary.tsv`：技能强化摘要。",
                f"- `door_effect_summary.tsv`：门效果摘要，含 `effect_hints` 可读运行时提示。",
                f"- `door_effect_hint_calibration.tsv`：门效果文案百分比与 linked BuffEffect.addAttr 的静态对照。",
                f"- `door_effect_hint_calibration_report.md`：门效果数值提示校准报告。",
                f"- `condition_field_audit.tsv` / `condition_field_audit_report.md`：数字门条件字段活跃度审计。",
                f"- `skill_enhance_condition_nodes.tsv` / `skill_enhance_condition_edges.tsv` / `skill_enhance_condition_report.md`：SkillEnhance 强化树条件图。",
                f"- `level_summary.tsv`：关卡/刷门摘要。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    return {
        "output_dir": str(out_dir),
        "stats": stats,
        "files": {
            "catalog": str(catalog_path),
            "character_summary_tsv": str(character_summary_tsv),
            "enhance_summary_tsv": str(enhance_summary_tsv),
            "door_effect_summary_tsv": str(door_effect_summary_tsv),
            "door_effect_hint_calibration_tsv": str(door_effect_hint_calibration_tsv),
            "door_effect_hint_calibration_report": str(door_effect_hint_calibration_report),
            "condition_field_audit_tsv": str(condition_field_audit_tsv),
            "condition_field_audit_report": str(condition_field_audit_report),
            "skill_enhance_condition_nodes_tsv": str(skill_enhance_condition_nodes_tsv),
            "skill_enhance_condition_edges_tsv": str(skill_enhance_condition_edges_tsv),
            "skill_enhance_condition_report": str(skill_enhance_condition_report),
            "level_summary_tsv": str(level_summary_tsv),
            "report": str(report_path),
        },
    }
