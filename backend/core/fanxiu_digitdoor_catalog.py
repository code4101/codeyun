from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.core.fanxiu_item_catalog import load_fanxiu_item_runtime_index
from backend.core.fanxiu_doupotd_catalog import (
    _collect_reward_result_resolution_flow,
    _extra_mark_label,
    _find_lua_asset_by_name,
    _load_item_corner_rows,
    _parse_reward_type_lua,
    _reward_type_from_token,
)
from backend.core.fanxiu_lua_config import load_fanxiu_lang_map, parse_fanxiu_generated_lua_config
from backend.core.fanxiu_resources import FanxiuResourceError, resolve_fanxiu_export_root
from backend.core.fanxiu_wiki import strip_fanxiu_rich_text


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


def _digitdoor_readyfight_runtime_sample_targets() -> list[dict[str, Any]]:
    return [
        {
            "pro_id": 91628,
            "packet": "CM_DigitDoorReadyFight",
            "expected_direction": "c2s",
            "sample_use": "ready-fight request trigger context",
        },
        {
            "pro_id": 91629,
            "packet": "SM_DigitDoorReadyFight",
            "expected_direction": "s2c",
            "sample_use": "skillList runtime wire-shape calibration",
        },
        {
            "pro_id": 91604,
            "packet": "DDSkillVo",
            "expected_direction": "embedded_or_unknown",
            "sample_use": "adjacent bean-shape reference if emitted as a top-level or nested decoded marker",
        },
    ]


def _digitdoor_capture_decoded_jsons(root: Path) -> list[Path]:
    capture_dir = root / "tcp_captures"
    if not capture_dir.is_dir():
        return []
    paths: list[Path] = []
    seen: set[str] = set()
    for pattern in ("*.codeyun_decoded.json", "*.decoded.json"):
        for path in sorted(capture_dir.glob(pattern), key=lambda item: item.name.lower()):
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
    return paths


def _load_digitdoor_decoded_frames(path: Path) -> tuple[list[dict[str, Any]], str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"{type(exc).__name__}: {exc}"
    raw_frames = data.get("frames", []) if isinstance(data, dict) else []
    if not isinstance(raw_frames, list):
        return [], "frames field is not a list"
    return [frame for frame in raw_frames if isinstance(frame, dict)], ""


def _collect_json_keys(value: Any, *, limit: int = 200) -> set[str]:
    keys: set[str] = set()

    def visit(node: Any) -> None:
        if len(keys) >= limit:
            return
        if isinstance(node, dict):
            for key, child in node.items():
                keys.add(str(key))
                visit(child)
        elif isinstance(node, list):
            for child in node[:80]:
                visit(child)

    visit(value)
    return keys


def _write_digitdoor_readyfight_runtime_sample_markdown(
    path: Path,
    *,
    export_root: Path,
    decoded_paths: list[Path],
    target_rows: list[dict[str, Any]],
    fixture_rows: list[dict[str, Any]],
    hit_rows: list[dict[str, Any]],
    verdict: dict[str, Any],
    errors: list[str],
) -> None:
    lines = [
        "# DigitDoor ReadyFight runtime sample coverage",
        "",
        f"- Export root: `{export_root}`",
        f"- Decoded fixtures scanned: {len(decoded_paths)}",
        f"- Fixture rows: {len(fixture_rows)}",
        f"- Target hit rows: {len(hit_rows)}",
        "- Scope: scans existing decoded TCP fixture metadata for ReadyFight packet ids. It does not read live traffic, hook the client, or export parsed payload values.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    if errors:
        lines.extend(["", "## Fixture Errors", ""])
        for error in errors[:12]:
            lines.append(f"- `{error}`")
    lines.extend(
        [
            "",
            "## Target Coverage",
            "",
            "| ProId | Packet | Expected Direction | Frames | Parsed | Status |",
            "| ---: | --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in target_rows:
        lines.append(
            "| "
            f"{row.get('pro_id', '')} | "
            f"{row.get('packet', '')} | "
            f"{row.get('expected_direction', '')} | "
            f"{row.get('frame_count', '')} | "
            f"{row.get('parsed_count', '')} | "
            f"{row.get('coverage_status', '')} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- If `SM_DigitDoorReadyFight(91629)` is absent, existing captures cannot close the `skillList` wire-shape ambiguity.",
            "- If it is present, the next step is a focused, privacy-filtered decoder pass over that frame only, not a broad live capture dump.",
            "- `DDSkillVo(91604)` may not appear as a top-level frame even if a bean list exists; this probe treats it as secondary supporting evidence only.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_digitdoor_readyfight_runtime_sample_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    out_dir = root / "parsed_configs" / "digitdoor_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    decoded_paths = _digitdoor_capture_decoded_jsons(root)
    targets = _digitdoor_readyfight_runtime_sample_targets()
    target_ids = {int(row["pro_id"]) for row in targets}
    target_by_id = {int(row["pro_id"]): row for row in targets}
    aggregate_counts: Counter[int] = Counter()
    aggregate_parsed_counts: Counter[int] = Counter()
    aggregate_files: dict[int, set[str]] = {int(row["pro_id"]): set() for row in targets}
    aggregate_offsets: dict[int, list[str]] = {int(row["pro_id"]): [] for row in targets}
    fixture_rows: list[dict[str, Any]] = []
    hit_rows: list[dict[str, Any]] = []
    protocol_counts: Counter[tuple[str, int, str]] = Counter()
    errors: list[str] = []
    total_frames = 0
    for decoded_path in decoded_paths:
        frames, error = _load_digitdoor_decoded_frames(decoded_path)
        if error:
            errors.append(f"{decoded_path.name}: {error}")
        total_frames += len(frames)
        fixture_target_ids: set[int] = set()
        for index, frame in enumerate(frames):
            pro_id = _as_int(frame.get("pro_id"))
            direction = str(frame.get("direction") or "")
            name = str(frame.get("name") or "")
            if pro_id is not None:
                protocol_counts[(direction, pro_id, name)] += 1
            if pro_id not in target_ids:
                continue
            fixture_target_ids.add(pro_id)
            aggregate_counts[pro_id] += 1
            if frame.get("parsed"):
                aggregate_parsed_counts[pro_id] += 1
            aggregate_files.setdefault(pro_id, set()).add(decoded_path.name)
            if len(aggregate_offsets.setdefault(pro_id, [])) < 8:
                aggregate_offsets[pro_id].append(str(frame.get("offset", "")))
            parsed_keys = _collect_json_keys(frame.get("parsed", {}))
            spec = target_by_id[pro_id]
            hit_rows.append(
                {
                    "fixture": decoded_path.name,
                    "index": index,
                    "direction": direction,
                    "offset": frame.get("offset", ""),
                    "pro_id": pro_id,
                    "packet": spec["packet"],
                    "name": name,
                    "frame_len": frame.get("frame_len", ""),
                    "payload_len": frame.get("payload_len", ""),
                    "zlib": frame.get("zlib", ""),
                    "parsed": bool(frame.get("parsed")),
                    "parsed_key_tokens": " | ".join(sorted(key for key in parsed_keys if key in {"skillList", "indexList", "levelId"})),
                    "privacy_note": "metadata_and_key_names_only_no_payload_values",
                }
            )
        fixture_rows.append(
            {
                "fixture": decoded_path.name,
                "path": str(decoded_path),
                "frame_count": len(frames),
                "c2s_frames": sum(1 for frame in frames if frame.get("direction") == "c2s"),
                "s2c_frames": sum(1 for frame in frames if frame.get("direction") == "s2c"),
                "parsed_frames": sum(1 for frame in frames if frame.get("parsed")),
                "readyfight_target_frames": sum(1 for frame in frames if _as_int(frame.get("pro_id")) in target_ids),
                "target_ids_present": " | ".join(str(item) for item in sorted(fixture_target_ids)),
                "error": error,
            }
        )
    target_rows: list[dict[str, Any]] = []
    for spec in targets:
        pro_id = int(spec["pro_id"])
        count = aggregate_counts.get(pro_id, 0)
        target_rows.append(
            {
                "pro_id": pro_id,
                "packet": spec["packet"],
                "expected_direction": spec["expected_direction"],
                "sample_use": spec["sample_use"],
                "frame_count": count,
                "parsed_count": aggregate_parsed_counts.get(pro_id, 0),
                "fixtures": " | ".join(sorted(aggregate_files.get(pro_id, set()))),
                "first_offsets": " | ".join(aggregate_offsets.get(pro_id, [])),
                "coverage_status": "present" if count else "absent",
                "interpretation": "usable existing sample candidate" if count else "no existing decoded sample for this packet id",
            }
        )
    protocol_rows = [
        {
            "direction": direction,
            "pro_id": pro_id,
            "name": name,
            "count": count,
            "is_readyfight_target": pro_id in target_ids,
        }
        for (direction, pro_id, name), count in sorted(
            protocol_counts.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1], item[0][2]),
        )
    ]
    _write_tsv(
        out_dir / "readyfight_runtime_sample_targets.tsv",
        target_rows,
        [
            "pro_id",
            "packet",
            "expected_direction",
            "sample_use",
            "frame_count",
            "parsed_count",
            "fixtures",
            "first_offsets",
            "coverage_status",
            "interpretation",
        ],
    )
    _write_tsv(
        out_dir / "readyfight_runtime_sample_fixtures.tsv",
        fixture_rows,
        [
            "fixture",
            "path",
            "frame_count",
            "c2s_frames",
            "s2c_frames",
            "parsed_frames",
            "readyfight_target_frames",
            "target_ids_present",
            "error",
        ],
    )
    _write_tsv(
        out_dir / "readyfight_runtime_sample_hits.tsv",
        hit_rows,
        [
            "fixture",
            "index",
            "direction",
            "offset",
            "pro_id",
            "packet",
            "name",
            "frame_len",
            "payload_len",
            "zlib",
            "parsed",
            "parsed_key_tokens",
            "privacy_note",
        ],
    )
    _write_tsv(
        out_dir / "readyfight_runtime_sample_protocol_counts.tsv",
        protocol_rows,
        ["direction", "pro_id", "name", "count", "is_readyfight_target"],
    )
    verdict = {
        "decoded_fixtures_found": bool(decoded_paths),
        "readyfight_request_sample_present": aggregate_counts.get(91628, 0) > 0,
        "readyfight_response_sample_present": aggregate_counts.get(91629, 0) > 0,
        "ddskillvo_top_level_sample_present": aggregate_counts.get(91604, 0) > 0,
        "existing_captures_cover_readyfight_shape": aggregate_counts.get(91629, 0) > 0,
        "safe_to_skip_existing_fixtures_for_readyfight_shape": aggregate_counts.get(91629, 0) == 0,
        "metadata_only_no_payload_values_exported": True,
    }
    report_path = out_dir / "readyfight_runtime_sample_coverage_report.md"
    _write_digitdoor_readyfight_runtime_sample_markdown(
        report_path,
        export_root=root,
        decoded_paths=decoded_paths,
        target_rows=target_rows,
        fixture_rows=fixture_rows,
        hit_rows=hit_rows,
        verdict=verdict,
        errors=errors,
    )
    return {
        "confirmed": bool(decoded_paths),
        "output_dir": str(out_dir),
        "stats": {
            "decoded_fixture_count": len(decoded_paths),
            "total_frame_count": total_frames,
            "target_hit_count": len(hit_rows),
            "readyfight_request_frame_count": aggregate_counts.get(91628, 0),
            "readyfight_response_frame_count": aggregate_counts.get(91629, 0),
            "ddskillvo_top_level_frame_count": aggregate_counts.get(91604, 0),
            "protocol_count_rows": len(protocol_rows),
        },
        "verdict": verdict,
        "files": {
            "markdown": str(report_path),
            "targets": str(out_dir / "readyfight_runtime_sample_targets.tsv"),
            "fixtures": str(out_dir / "readyfight_runtime_sample_fixtures.tsv"),
            "hits": str(out_dir / "readyfight_runtime_sample_hits.tsv"),
            "protocol_counts": str(out_dir / "readyfight_runtime_sample_protocol_counts.tsv"),
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
