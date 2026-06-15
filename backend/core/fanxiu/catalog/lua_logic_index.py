from __future__ import annotations

import csv
import json
import re
import struct
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from backend.core.fanxiu.catalog.apk_static import resolve_fanxiu_apk_unpacked_root
from backend.core.fanxiu.catalog.lua_config import parse_fanxiu_generated_lua_config
from backend.core.fanxiu.catalog.resources import (
    FanxiuResourceError,
    export_fanxiu_unity_text_assets,
    inspect_fanxiu_unity_bundle,
    resolve_fanxiu_export_root,
    resolve_fanxiu_resource_root,
)
from backend.core.fanxiu.catalog.wiki import _unescape_lua_string, strip_fanxiu_rich_text


DEFAULT_LUA_LOGIC_DIR = Path("by_source/lscripts/gamesystem/game")
DEFAULT_LINGJIE_RUNTIME_OUT_DIR = Path("parsed_configs/lingjie_feature_catalog")
_CONFIG_REF_RE = re.compile(r"ConfigName\.([A-Za-z0-9_\-]+)")
_FUNCTION_RE = re.compile(r"function\s+_M[:.]([A-Za-z0-9_]+)\s*\(")
_REQUIRE_RE = re.compile(r"require\s*(?:\(?\s*)[\"']([^\"']+)[\"']")
_PACKAGE_RE = re.compile(r"package\.loaded\[[\"']([^\"']+)[\"']\]")
_NETLOGIC_FUN_RE = re.compile(r"\bNetLogic[.:]([A-Za-z0-9_]+Fun)\s*\(")

_LINGJIE_CONFIG_ROLES = {
    "LingjieGongfa_XianjieGongfaStar": "按 featureGroup/star 索引仙界功法星级品质",
    "LingjieGongfa_LingjieGongfaStar": "按 gongfaId/star 索引灵界功法星级",
    "LingjieGongfa_LingjieGongfaJie": "按 gongfaId/jie 索引灵界功法阶数，并计算最大阶",
    "LingjieGongfa_MainFeaturePin": "按 gongfaId/pin 索引主词条品阶配置",
    "LingjieGongfa_MainFeature": "按 gongfaId/group 或 featureType/gongfaId 索引主词条组",
    "LingjieGongfa_SideFeatureJie": "按 featureGroup/jie 索引副词条阶数，并计算最大阶",
    "LingjieGongfa_SideFeaturePin": "按 featureGroup/pin 索引副词条品阶，并计算最大品",
    "LingjieGongfa_FeatureBase": "按词条 id 解析 group、featureGroup、keyFeature 等基础信息",
    "LingjieGongfa_LearnCost": "按品质和专属词条状态计算学习/请教成本",
    "LingjieGongfa_ConfigValue": "读取开启条件、消耗上下限、渠道等常量",
    "LingjieGongfa_LingjieGongfaIcon": "读取自创功法图标和图标背景池",
    "LingjieGongfa_LingJieGrid": "读取灵界飞升/格子相关配置",
    "LingjieGongfa_XianjieGrid": "读取仙界飞升/格子相关配置",
    "LingjieGongfa_SkillRandomName": "读取功法随机名称池",
    "LingjieGongfa_CornerRandomName": "读取角标/别名随机名称池",
}

_GONGFAHOMEMAKE_VO_FIELD_ROLES = {
    ("CreateSkillCommonVO", "id"): ("runtime_id", "自创功法实例 id / make id"),
    ("CreateSkillCommonVO", "mainId"): ("main_skill_id", "主技能 id，普通 effectMap 中也作为主词条 key"),
    ("CreateSkillCommonVO", "skillName"): ("display_name", "自创功法名称"),
    ("CreateSkillCommonVO", "mark"): ("display_note", "玩家备注/描述"),
    ("CreateSkillCommonVO", "icon"): ("display_icon", "自创功法图标 id"),
    ("CreateSkillCommonVO", "iconBg"): ("display_icon_bg", "自创功法图标背景 id"),
    ("CreateSkillCommonVO", "effectMap"): (
        "lingjie_effect_map",
        "灵界自创词条映射；客户端按 key=skillId、value=FeatureBase.id 解析主/侧词条",
    ),
    ("CreateSkillCommonVO", "xianEffectMap"): (
        "xianjie_effect_map",
        "仙界/飞升自创词条映射；客户端按 key=FeatureBase.id、value=skillId 解析主/侧词条",
    ),
    ("CreateSkillCommonVO", "fromLingName"): ("origin_name", "仙界自创时展示来源灵界功法名"),
    ("CreateSkillCommonVO", "career"): ("career", "职业/流派选择"),
    ("GongFaHomeMakeVO", "skillCommonVO"): ("common_payload", "自创功法核心字段"),
    ("GongFaHomeMakeVO", "fromPlayerName"): ("owner_name", "创建/上传玩家名"),
    ("GongFaHomeMakeVO", "fromPlayerId"): ("owner_id", "创建/上传玩家 id"),
    ("GongFaHomeMakeVO", "fromPlayerServer"): ("owner_server", "创建/上传玩家服务器"),
    ("GongFaHomeMakeVO", "isLight"): ("unlocked", "是否已点亮/可用于显示或上阵"),
    ("GongFaHomeMakeVO", "isItemCreate"): ("item_created", "是否由道具或特殊入口生成"),
    ("GongFaHomeMakeVO", "isCheck"): ("selected", "是否收藏/勾选"),
    ("GongFaHomeMakeVO", "scopeType"): ("scope", "功法域：灵界或仙界"),
    ("GongFaHomeMakeVO", "skillType"): ("battle_type", "战斗类型：神通/心法等"),
    ("GongFaHomeMakeVO", "isNoneCreate"): ("non_created_xinfa", "非自创灵界心法标记"),
    ("HMFilterVO", "startIdx"): ("page_start", "分页起点"),
    ("HMFilterVO", "endIdx"): ("page_end", "分页终点"),
    ("HMFilterVO", "type"): ("scope_filter", "列表筛选的功法域"),
    ("HMFilterVO", "skillType"): ("battle_type_filter", "列表筛选的战斗类型"),
    ("HMFilterVO", "isNotLightUp"): ("unlocked_filter", "是否只请求未点亮/可请教项"),
    ("HMFilterVO", "careers"): ("career_filter", "职业/流派过滤列表"),
    ("HMFilterVO", "mainSkills"): ("main_skill_filter", "灵界主技能过滤列表"),
    ("HMFilterVO", "assistSkills"): ("assist_skill_filter", "灵界副技能过滤列表"),
    ("HMFilterVO", "threeSet"): ("assist_set_filter", "灵界三件套/副词条组合过滤列表"),
    ("HMFilterVO", "mainXianSkills"): ("xian_main_filter", "仙界主技能过滤列表"),
    ("HMFilterVO", "xianThreeSet"): ("xian_assist_set_filter", "仙界副词条组合过滤列表"),
}

_GONGFAHOMEMAKE_LIST_FIELD_TARGETS = {
    ("SM_GongFaHomeMakePageList", "homeMakeVOS"): "GongFaHomeMakeVO",
    ("SM_GongFaHomeMakeList", "homeMakeVOS"): "GongFaHomeMakeScopeListVO",
    ("GongFaHomeMakeListVO", "homeMakeVOList"): "GongFaHomeMakeVO",
    ("SM_GongFaHomeMakeLearnList", "itemVOS"): "GongFaLearnItemVO",
    ("SM_GongFaHomeMakeTeachList", "itemVOS"): "GongFaTeachItemVO",
    ("SM_GongFaHomeMakeRecordList", "recordVOS"): "GongFaHomeMakeRecordVO",
    ("GongFaTeachItemVO", "playerVOS"): "GongFaTeachPlayerVO",
    ("SM_GongFaHomeMakeCombineList", "combineList"): "SM_GongFaHomeMakeCombine",
    ("SM_GongFaHomeMakeChangeNameList", "changeNameList"): "SM_GongFaHomeMakeChangeName",
}

_GONGFAHOMEMAKE_INTEGRATION_PATTERNS = {
    "GongFaHomeMakeVO": re.compile(r"\bGongFaHomeMakeVO\b"),
    "homeMakeVO": re.compile(r"\bhomeMakeVO\b"),
    "gongFaHomeMakeVO": re.compile(r"\bgongFaHomeMakeVO\b"),
    "makeId": re.compile(r"\bmakeId\b"),
    "SkillProgramVO": re.compile(r"\bSkillProgramVO\b"),
    "GetGongFaHomeMakeVoById": re.compile(r"\bGetGongFaHomeMakeVoById\b"),
    "GetCreateSkillParamData": re.compile(r"\bGetCreateSkillParamData\b"),
    "GetGongFaIdArrCompare": re.compile(r"\bGetGongFaIdArrCompare\b"),
}

_LINGJIE_EQUIP_PACKET_NAMES = {
    "CM_ReplaceSkill",
    "SM_ReplaceSkill",
    "SkillInfoVO",
    "ShowSkillVO",
    "SkillProgramVO",
    "CM_GongFaSaveProgram",
    "SM_GongFaSaveProgram",
    "GongFaProgramVO",
    "CM_XinFaPutUp",
    "SM_XinFaPutUp",
    "XinFaVO",
    "HomeMakeXinFaVO",
}

_LINGJIE_EQUIP_PACKET_NOTES = {
    "CM_ReplaceSkill": "神通/绝招等直接替换请求；自创项通过 makeId 引用实例。",
    "SM_ReplaceSkill": "替换响应；返回 groupId、skills、cds 等服务端确认状态。",
    "SkillInfoVO": "通用技能槽信息；skillId/jie/star/type/makeId 组合承载普通或自创技能引用。",
    "ShowSkillVO": "展示用技能信息；同样带 makeId。",
    "SkillProgramVO": "功法方案中的单个技能对象；同时包含 homeMakeVO 和 skillInfoVO。",
    "CM_GongFaSaveProgram": "保存功法方案请求；写入 GongFaProgramVO。",
    "SM_GongFaSaveProgram": "保存功法方案响应；返回 GongFaProgramVO。",
    "GongFaProgramVO": "功法方案对象；skillList 是 SkillProgramVO 列表。",
    "CM_XinFaPutUp": "心法上阵请求；写入 XinFaVO 列表。",
    "SM_XinFaPutUp": "心法上阵响应；返回 XinFaVO 列表。",
    "XinFaVO": "心法槽对象；idx + SkillInfoVO，其中 xinFaId.makeId 指向自创实例。",
    "HomeMakeXinFaVO": "心法展示/引用对象；effectMap/xianEffectMap 客户端只读不写，写出侧只带 makeId 等展示字段。",
}

_SKILL_EQUIP_PACKET_NAMES = {
    "CM_ShowSkill",
    "SM_ShowSkill",
    "CM_AutoReplace",
    "SM_AutoReplace",
    "CM_ChangeGroup",
    "SM_ChangeGroup",
    "SkillPosVo",
    "SM_ShowReplaceSkill",
    "SkillInfoVO",
    "CM_ReplaceSkill",
    "SM_ReplaceSkill",
}

_SKILL_EQUIP_PACKET_NOTES = {
    "CM_ShowSkill": "请求当前技能/装备组展示数据。",
    "SM_ShowSkill": "返回 groups 和 currentGroupId；groups 是 SkillPosVo 列表。",
    "CM_AutoReplace": "提交整组自动替换；字段 groupId + skills。",
    "SM_AutoReplace": "自动替换响应；返回 systemTime/groupId/skills/cds。",
    "CM_ChangeGroup": "提交当前使用组切换；字段 groupId + skills。",
    "SM_ChangeGroup": "切换组响应；返回 systemTime/groupId/skills/cds。",
    "SkillPosVo": "技能组位置对象；字段 groupId + skills。",
    "SM_ShowReplaceSkill": "返回可替换技能组展示数据；结构与 SM_ShowSkill 相近。",
    "SkillInfoVO": "通用技能槽信息；自创项通过 type + makeId 指向已有实例。",
    "CM_ReplaceSkill": "单槽替换请求；字段 skillId/type/makeId/groupId/index。",
    "SM_ReplaceSkill": "单槽替换响应；返回 systemTime/groupId/skills/cds。",
}

_FIGHT_RESULT_PACKET_NAMES = {
    "SM_FightResult",
    "SM_FightResultTalisman",
    "SM_FightResultPet",
    "SM_FightResultFunnel",
    "FightResultVO",
}

_FIGHT_RESULT_PACKET_NOTES = {
    "SM_FightResult": "普通技能战斗结果回包；FightNetLogic 收到后转入 caster 的 SkillActor。",
    "SM_FightResultTalisman": "法宝战斗结果回包；继承 SM_FightResult 结构。",
    "SM_FightResultPet": "灵兽/宠物战斗结果回包；继承 SM_FightResult 结构。",
    "SM_FightResultFunnel": "漏斗/召唤物战斗结果回包；继承 SM_FightResult 结构。",
    "FightResultVO": "单个目标的服务端伤害结果；SkillBase:SetSM_FightResult 按 hurt_event 百分比分摊显示。",
}

_FIGHT_RESULT_FIELD_SEMANTICS = {
    ("SM_FightResult", "casterId"): "施法实体 id，FightNetLogic 用它定位 caster 的 SkillActor。",
    ("SM_FightResult", "lockId"): "锁定目标 id 或本次技能锁定对象。",
    ("SM_FightResult", "skillId"): "本次回包对应的战斗技能 id。",
    ("SM_FightResult", "results"): "FightResultVO 列表；每个元素是一名目标的服务端结果。",
    ("SM_FightResult", "delayTime"): "服务端给出的延迟播放/处理时间。",
    ("FightResultVO", "targetId"): "目标实体 id，客户端用它定位飘字和受击表现对象。",
    ("FightResultVO", "fightEffect"): "战斗效果位标记，例如暴击、格挡、主目标等表现分支。",
    ("FightResultVO", "damage"): "服务端下发的真实伤害总值；客户端按 hurt_event 百分比分段显示。",
    ("FightResultVO", "damageView"): "服务端下发的展示伤害值；客户端同样按段分摊。",
    ("FightResultVO", "mpAddDamage"): "附加法力/特殊伤害数值，客户端按段分摊。",
    ("FightResultVO", "mpAddDamageView"): "附加法力/特殊伤害展示值，客户端按段分摊。",
    ("FightResultVO", "damageTimes"): "伤害次数/倍数相关字段，当前分段逻辑未直接用它计算 hurt_event 百分比。",
    ("FightResultVO", "recoverHp"): "回血数值，客户端按段分摊后生成恢复飘字。",
    ("FightResultVO", "damageReflect"): "反射伤害数值，客户端按段分摊。",
    ("FightResultVO", "mpDamageAbsorb"): "法力/特殊伤害吸收数值，客户端按段分摊。",
}

_FIGHT_EFFECT_SEMANTICS = {
    "NORMAL": "普通伤害/默认表现。",
    "LAST_HIT": "最后一击标记。",
    "SKILL_MAIN_TARGET": "技能主目标标记；SkillBase 用它决定是否播放目标受击 timeline。",
    "DODGE": "闪避/未命中标记；HurtData 会转为 miss 飘字。",
    "CRIT": "暴击标记；HurtData 转为暴击血条/飘字表现。",
    "LUCKY": "幸运相关战斗效果标记，当前已读表现分支未直接解释。",
    "DEADLY": "致命/特殊伤害标记；HurtData 转为特殊飘字。",
    "VIOLENT": "强击/猛烈类特殊伤害标记；HurtData 转为特殊飘字。",
    "IGNORE": "忽视类特殊伤害标记；HurtData 转为特殊飘字。",
    "IGNORE_HIT_EFFECT": "忽略命中特效标记。",
    "IMMUNITY": "免疫标记；HurtData 会忽略伤害数值并显示免疫类飘字。",
    "BLOCK": "格挡标记；HurtData 转为格挡表现。",
    "HOLY": "神圣类特殊伤害标记；HurtData 转为特殊飘字。",
    "SMITE": "重击类特殊伤害标记；HurtData 转为特殊飘字。",
    "SPELL_CRIT": "法术暴击标记；HurtData 与 CRIT 走同一暴击表现分支。",
    "THEURGY": "神通相关战斗效果标记，当前已读表现分支未直接解释。",
    "SWORDKEE": "剑气相关战斗效果标记，当前已读表现分支未直接解释。",
    "SHADOW": "影/暗类特殊表现标记。",
    "FIRE": "火焰类特殊表现标记。",
    "CHARM": "魅惑类特殊表现标记。",
    "VIOLENT_XIAN": "仙界强击类特殊表现标记。",
    "DODGE_DAMAGE": "闪避伤害/未命中伤害标记；HurtData 会转为 miss 飘字。",
    "LINGYUN": "凌云类特殊伤害标记；HurtData 转为特殊飘字。",
}

_BLOOD_TYPE_SEMANTICS = {
    "NORMAL": "普通扣血飘字。",
    "SPECIAL": "特殊伤害/免疫/格挡等非普通伤害飘字。",
    "POISON": "中毒扣血飘字。",
    "SELF_HURT": "自身受伤/反噬类飘字。",
    "CURE": "回血飘字。",
    "MP": "法力/灵力变化飘字。",
    "BURNING": "燃烧类飘字枚举；当前 UI 映射未直接使用。",
    "TALISMAN": "法宝伤害飘字。",
    "PET": "灵兽/宠物伤害飘字。",
    "SUMMON": "召唤物伤害飘字。",
    "REFLECT": "反射伤害飘字。",
    "MP_DAMAGE": "法力/灵力附加伤害飘字。",
    "MP_DAMAGE_ABSORB": "法力/灵力伤害吸收飘字。",
    "ROSE_HURT": "玫瑰/特殊活动伤害飘字。",
    "CRIT_HURT": "暴击/未命中等醒目战斗飘字。",
    "SHIELD_ABSORB": "护盾吸收飘字。",
    "OUTCAST_DAMAGE": "放逐/特殊玩法普通伤害飘字。",
    "OUTCAST_CRIT": "放逐/特殊玩法暴击飘字。",
    "OUTCAST_SELF_HURT": "放逐/特殊玩法自身受伤飘字。",
    "CHOPPINGTREE_DAMAGE": "砍树玩法普通伤害飘字。",
    "CHOPPINGTREE_CIRT": "砍树玩法暴击飘字。",
    "CHOPPINGTREE_SELF_HURT": "砍树玩法自身受伤飘字。",
    "TD_DAMAGE": "塔防玩法普通伤害飘字。",
    "TD_SELF_HURT": "塔防玩法自身受伤飘字。",
    "TD_CIRT": "塔防玩法暴击飘字。",
    "DIGIT_DOOR_DAMAGE": "数字门玩法普通伤害飘字。",
    "DIGIT_DOOR_SELF_HURT": "数字门玩法自身受伤飘字。",
    "DIGIT_DOOR_CIRT": "数字门玩法暴击飘字。",
    "XZ_DAMAGE": "仙战/特殊玩法普通伤害飘字。",
    "XZ_CIRT": "仙战/特殊玩法暴击飘字。",
    "XZ_SELF_HURT": "仙战/特殊玩法自身受伤飘字。",
    "XZ_CURE": "仙战/特殊玩法回血飘字。",
    "DOUPOPVP_DAMAGE": "斗法 PVP 普通伤害飘字。",
    "DOUPOPVP_CRIT": "斗法 PVP 暴击飘字。",
    "DOUPOPVP_SELF_HURT": "斗法 PVP 自身受伤飘字。",
}

_HURT_SOURCE_FIELD_HINTS = {
    "damage_num": "FightResultVO.damage / damageView 分段后的普通伤害表现值",
    "recoverHp_num": "FightResultVO.recoverHp 分段后的回血表现值",
    "recoverMp_num": "回蓝/灵力恢复表现值，来自运行态附加字段",
    "reducedMp_num": "扣蓝/灵力减少表现值，来自运行态附加字段",
    "mp_damage": "FightResultVO.mpAddDamage / mpAddDamageView 分段后的附加法力伤害表现值",
    "mp_damage_num": "FightResultVO.mpAddDamage / mpAddDamageView 分段后的附加法力伤害表现值",
    "reflect_damage": "FightResultVO.damageReflect 分段后的反射伤害表现值",
    "reflect_num": "FightResultVO.damageReflect 分段后的反射伤害表现值",
    "mpDamageAbsorb_num": "FightResultVO.mpDamageAbsorb 分段后的法力伤害吸收表现值",
    "shieldAbsorb_num": "护盾吸收表现值，来自运行态吸收字段",
    "shield_num": "特殊玩法护盾吸收表现值",
    "healAmount": "外部玩法传入的回血表现值",
    "totalNum": "HurtTipsMgr 聚合后的同类飘字总值",
}

_HURT_TIPS_TYPE_BLOOD_CANDIDATES = {
    "NormalDamage": "NORMAL、CRIT_HURT、SPECIAL、SELF_HURT、ROSE_HURT、PET、TALISMAN、SUMMON",
    "SelfHurtDamage": "SELF_HURT、ROSE_HURT",
    "MpDamage": "MP_DAMAGE",
    "ReflectDamage": "REFLECT",
    "PetDamage": "PET",
    "TalismanDamage": "TALISMAN",
    "HpRecover": "CURE",
    "MpRecover": "MP",
    "MpReduced": "MP",
    "ShieldAbsorb": "SHIELD_ABSORB",
    "MpAbsorb": "MP_DAMAGE_ABSORB",
}

_HURT_FORMAT_BLOOD_CANDIDATES = {
    "FormatHurtTipsAndType": _HURT_TIPS_TYPE_BLOOD_CANDIDATES["NormalDamage"],
    "FormatXZHurtTipsAndType": "XZ_DAMAGE、XZ_SELF_HURT、XZ_CIRT",
    "FormatDoupoPvpHurtTipsAndType": "DOUPOPVP_DAMAGE、DOUPOPVP_SELF_HURT、DOUPOPVP_CRIT",
}

_HURT_DATA_SETDATA_PARAMS = [
    ("casterId", "caster_entity_id", "施法实体 id；SkillBase 中通常是当前实体或宠物/法宝 owner id。"),
    ("targetId", "target_entity_id", "目标实体 id；来自 FightResultVO.targetId。"),
    ("fightEffect", "fight_effect_flags", "战斗效果位标记；来自 FightResultVO.fightEffect:ToNum()。"),
    ("damage_num", "display_damage", "HurtData 普通伤害显示值；SkillBase 主要传入 damage_view。"),
    ("reflect_damage", "reflect_damage", "HurtData 反射伤害显示值；来自 FightResultVO.damageReflect 分段。"),
    ("mp_damage", "mp_damage", "HurtData 附加法力/特殊伤害显示值；来自 FightResultVO.mpAddDamageView 分段。"),
    ("recoverHp_num", "hp_recover", "HurtData 回血显示值；来自 FightResultVO.recoverHp 分段。"),
    ("recoverMp_num", "mp_recover", "HurtData 回蓝显示值；SkillBase 普通回包中固定传 0。"),
    ("reducedMp_num", "mp_reduce", "HurtData 扣蓝显示值；SkillBase 普通回包中固定传 0。"),
    ("total_damage", "accumulated_damage", "同目标累计真实伤害；SkillBase 累加 resultVo.damage 与 resultVo.mpAddDamage 分段。"),
    ("total_recover", "accumulated_recover", "同目标累计回血；SkillBase 累加 resultVo.recoverHp 分段。"),
    ("mpDamageAbsorb_num", "mp_damage_absorb", "HurtData 法力/特殊伤害吸收值；来自 FightResultVO.mpDamageAbsorb 分段。"),
    ("shieldAbsorb_num", "shield_absorb", "HurtData 护盾吸收值；SkillBase 普通 FightResult 中当前固定为 0。"),
    ("raise_event", "hp_change_event", "是否触发 HP 变化事件；SkillBase 当前局部初始化为 false。"),
    ("entityType", "caster_entity_type", "施法实体类型；影响宠物/法宝/召唤物飘字分支。"),
    ("skillId", "skill_id", "当前战斗技能 id。"),
]

_HURT_DATA_INTERNAL_FIELDS = {
    "damage_num": "damage_num",
    "reflect_damage": "reflect_num",
    "mp_damage": "mp_damage_num",
    "recoverHp_num": "recoverHp_num",
    "recoverMp_num": "recoverMp_num",
    "reducedMp_num": "reducedMp_num",
    "total_damage": "total_damage",
    "total_recover": "total_recover",
    "mpDamageAbsorb_num": "mpDamageAbsorb_num",
    "shieldAbsorb_num": "shieldAbsorb_num",
}

_FIGHT_RESULT_FIELD_FROM_EXPR = {
    "targetId": "FightResultVO.targetId",
    "fightEffect": "FightResultVO.fightEffect",
    "damage": "FightResultVO.damage",
    "damageView": "FightResultVO.damageView",
    "mpAddDamage": "FightResultVO.mpAddDamage",
    "mpAddDamageView": "FightResultVO.mpAddDamageView",
    "damageTimes": "FightResultVO.damageTimes",
    "recoverHp": "FightResultVO.recoverHp",
    "damageReflect": "FightResultVO.damageReflect",
    "mpDamageAbsorb": "FightResultVO.mpDamageAbsorb",
}

_FIGHT_RESULT_BOUNDARY_FILE_NAMES = {
    "FightResultVO.lua",
    "SM_FightResult.lua",
    "SM_FightResultTalisman.lua",
    "SM_FightResultPet.lua",
    "SM_FightResultFunnel.lua",
    "FightNetLogic.lua",
    "SkillActor.lua",
    "UserSkillActor.lua",
    "TalismanSkillActor.lua",
    "FunnelSkillActor.lua",
    "SkillBase.lua",
}

_FIGHT_RESULT_BOUNDARY_TERMS = (
    "FightResultVO",
    "SM_FightResult",
    "SetSM_FightResult",
    "msg.results",
    "resultVo.",
)

_HP_SIDE_PATH_PACKET_NAMES = {
    "SM_UnitHpUpdate",
    "SM_UnitMpUpdate",
    "SM_BuffChangeHpAndMp",
    "BuffResultVO",
}

_UNIT_HP_PARAM_SOURCES = {
    "targetId": "SM_UnitHpUpdate.id",
    "damage": "SM_UnitHpUpdate.damage",
    "recoverHp": "SM_UnitHpUpdate.recoverHp",
    "fightEffect": "SM_UnitHpUpdate.fightEffect",
    "mpDamageAbsorb": "SM_UnitHpUpdate.mpDamageAbsorb",
    "shieldAbsorb": "SM_UnitHpUpdate.shieldAbsorb",
}

_UNIT_MP_PARAM_SOURCES = {
    "targetId": "SM_UnitMpUpdate.id",
    "recoverMp": "SM_UnitMpUpdate.recoverMp",
    "reducedMp": "SM_UnitMpUpdate.changeMp",
}

_BUFF_RESULT_FIELD_FROM_EXPR = {
    "id": "BuffResultVO.id",
    "ownerId": "BuffResultVO.ownerId",
    "casterId": "BuffResultVO.casterId",
    "targetId": "BuffResultVO.targetId",
    "modelId": "BuffResultVO.modelId",
    "damage": "BuffResultVO.damage",
    "damageView": "BuffResultVO.damageView",
    "recoverHp": "BuffResultVO.recoverHp",
    "recoverMp": "BuffResultVO.recoverMp",
    "fightEffect": "BuffResultVO.fightEffect",
}

_FIGHT_STATE_SYNC_PACKET_NAMES = {
    "SM_HpChange",
    "SM_MpChange",
    "SM_FixDamage",
    "SM_ShadowHpChange",
    "SM_ShadowInfo",
    "SM_UnitMaxHpUpdate",
}

_FIGHT_STATE_SYNC_HANDLERS = {
    "SM_HpChangeFun": "SM_HpChange",
    "SM_MpChangeFun": "SM_MpChange",
    "SM_FixDamageFun": "SM_FixDamage",
    "SM_ShadowHpChangeFun": "SM_ShadowHpChange",
    "SM_ShadowInfoFun": "SM_ShadowInfo",
    "SM_UnitMaxHpUpdateFun": "SM_UnitMaxHpUpdate",
}

_FIGHT_REQUEST_PACKET_NAMES = {
    "CM_FightByTarget",
    "CM_FightByTargets",
    "CM_FightByDir",
    "CM_FightByPosition",
    "CM_FightFinishCharge",
    "CM_FightInterrupt",
}

_FIGHT_CAST_BROADCAST_PACKET_NAMES = {
    "SM_FightCast",
    "SM_FightCastTalisman",
    "SM_FightCastPet",
    "SM_FightCastPassive",
    "SM_FightCastFunnel",
    "FightCastVO",
    "FightCastMultiVO",
}

_FIGHT_REQUEST_PACKET_VAR_MAP = {
    "cm_FightByTarget": "CM_FightByTarget",
    "_CM_FightByTargets": "CM_FightByTargets",
    "_CM_FightByDir": "CM_FightByDir",
    "_CM_FightByPosition": "CM_FightByPosition",
}

_FIGHT_REQUEST_FUNCTION_PACKETS = {
    "CM_FightByTarget": "CM_FightByTarget",
    "CM_FightByTargets": "CM_FightByTargets",
    "CM_FightByDir": "CM_FightByDir",
    "CM_FightByPosition": "CM_FightByPosition",
}

_FIGHT_REQUEST_FUNCTION_FIELDS = {
    "CM_FightByTarget": ["skillId", "casterId", "targetId", "movePos", "currPos"],
    "CM_FightByTargets": ["skillId", "casterId", "selectTargetIds", "selectDir", "selectPos", "movePos", "currPos"],
    "CM_FightByDir": ["skillId", "casterId", "selectDir", "movePos", "currPos"],
    "CM_FightByPosition": ["skillId", "casterId", "selectPos", "movePos", "currPos"],
}

_FIGHT_CAST_HANDLERS = {
    "SM_FightCastFun": "SM_FightCast",
    "SM_FightCastTalismanFun": "SM_FightCastTalisman",
    "SM_FightCastPetFun": "SM_FightCastPet",
    "SM_FightCastPassiveFun": "SM_FightCastPassive",
    "SM_FightCastFunnelFun": "SM_FightCastFunnel",
}

_FIGHT_CAST_FLOW_FILE_NAMES = {
    "EntityView.lua",
    "FightNetLogic.lua",
    "FightMgr.lua",
    "SkillActor.lua",
    "StateBase.lua",
    "StateMachine.lua",
    "StateSkill.lua",
}

_SKILL_INSTANCE_LIFECYCLE_FILE_NAMES = {
    "SkillActor.lua",
    "SkillBase.lua",
    "HurtEvent.lua",
    "HurtFrameVo.lua",
    "BulletMgr.lua",
    "Bullet.lua",
    "HurtData.lua",
}

_FIGHT_AUTHORITY_BOUNDARY_PATTERNS = [
    {
        "phase_order": 10,
        "phase_id": "client_local_precheck",
        "authority": "client_prediction",
        "packet_or_event": "CM_FightBySkill",
        "file_name": "FightNetLogic.lua",
        "function_names": {"CM_FightBySkill"},
        "pattern": re.compile(r"\bReleaseSkillExecute\b"),
        "local_effect": "发送请求前先尝试本地释放判定/预表现。",
        "server_authority_note": "这一步不产生权威伤害，只是客户端释放意图前置检查。",
        "max_hits": 4,
    },
    {
        "phase_order": 20,
        "phase_id": "client_send_intent",
        "authority": "client_intent",
        "packet_or_event": "CM_FightBy*",
        "file_name": "FightNetLogic.lua",
        "function_names": {"CM_FightByTarget", "CM_FightByTargets", "CM_FightByDir", "CM_FightByPosition"},
        "pattern": re.compile(r"\bF_SendMsg\b"),
        "local_effect": "发送 caster/skill/target/dir/pos/movePos 等释放意图。",
        "server_authority_note": "请求字段未携带 damage/recover/hp/mp；最终数值等待服务端回包。",
        "max_hits": 8,
    },
    {
        "phase_order": 30,
        "phase_id": "server_cast_broadcast",
        "authority": "server_cast_confirmation",
        "packet_or_event": "SM_FightCast*",
        "file_name": "FightNetLogic.lua",
        "function_names": {"SM_FightCastFun", "SM_FightCastTalismanFun", "SM_FightCastPassiveFun", "SM_FightCastFunnelFun"},
        "pattern": re.compile(r"\bEntityFightCast\b|\bReleaseSkillExecute\b|\bEntityCastPassiveSkill\b"),
        "local_effect": "服务端确认释放后广播目标、方向、位置、CD、阶星等表现参数。",
        "server_authority_note": "这是释放确认，不是伤害结果；伤害仍看 SM_FightResult 或 HP/MP 旁路。",
        "max_hits": 12,
    },
    {
        "phase_order": 40,
        "phase_id": "local_skill_state_enter",
        "authority": "client_local_presentation",
        "packet_or_event": "StateType.Skill",
        "file_name": "FightMgr.lua",
        "function_names": {"ReleaseSkillExecute"},
        "pattern": re.compile(r"\bSetState\(StateType\.Skill"),
        "local_effect": "进入本地技能状态机。",
        "server_authority_note": "只改变客户端状态和表现驱动，不写最终 HP/MP。",
        "max_hits": 4,
    },
    {
        "phase_order": 50,
        "phase_id": "local_skill_instance_start",
        "authority": "client_local_presentation",
        "packet_or_event": "SkillBase:Start",
        "file_name": "SkillActor.lua",
        "function_names": {"ReleaseSkill", "ReleaseMagicSkill", "ReleasePassiveSkill"},
        "pattern": re.compile(r"\b(?:runtimeSkill|skillInfo):Start\("),
        "local_effect": "启动本地 SkillBase 实例和 timeline。",
        "server_authority_note": "timeline 决定播放时点和表现分段，不决定总伤害。",
        "max_hits": 8,
    },
    {
        "phase_order": 60,
        "phase_id": "server_result_dispatch",
        "authority": "server_result",
        "packet_or_event": "SM_FightResult*",
        "file_name": "FightNetLogic.lua",
        "function_names": {"SM_FightResultFun", "SM_FightResultTalismanFun", "SM_FightResultPetFun", "SM_FightResultFunnelFun"},
        "pattern": re.compile(r"\bSetSM_FightResult4RunTimeSkill\b"),
        "local_effect": "按 caster/talisman/pet/funnel 找到对应 SkillActor。",
        "server_authority_note": "SM_FightResult.results 是服务端下发的目标级结果列表。",
        "max_hits": 8,
    },
    {
        "phase_order": 70,
        "phase_id": "server_result_to_hurtdata",
        "authority": "server_result_to_presentation",
        "packet_or_event": "FightResultVO -> HurtData",
        "file_name": "SkillBase.lua",
        "function_names": {"SetSM_FightResult"},
        "pattern": re.compile(r"\bhurtData:SetData\b"),
        "local_effect": "把服务端总数值按 q_hurt_events 百分比分摊成 HurtData。",
        "server_authority_note": "HurtData 是表现层字段落点，仍以服务端 FightResultVO 字段为来源。",
        "max_hits": 4,
    },
    {
        "phase_order": 80,
        "phase_id": "timeline_hurt_trigger",
        "authority": "client_local_presentation",
        "packet_or_event": "HurtEvent",
        "file_name": "HurtEvent.lua",
        "function_names": {"OnStart"},
        "pattern": re.compile(r"\bUpdate4Hurt\b"),
        "local_effect": "timeline 命中帧触发已排队 HurtData。",
        "server_authority_note": "这里只控制展示时机，不产生新的权威伤害。",
        "max_hits": 4,
    },
    {
        "phase_order": 90,
        "phase_id": "hurtdata_execute_display",
        "authority": "client_local_presentation",
        "packet_or_event": "HurtData:Execute",
        "file_name": "HurtFrameVo.lua",
        "function_names": {"ExecuteHurtDataList", "ExecuteDelayHurtData"},
        "pattern": re.compile(r"\bhurtData:Execute\b"),
        "local_effect": "执行 HurtData，进入飘字/血条表现分支。",
        "server_authority_note": "表现执行不等于属性最终同步；属性仍看后续状态同步包。",
        "max_hits": 4,
    },
    {
        "phase_order": 100,
        "phase_id": "server_hp_property_sync",
        "authority": "server_state_sync",
        "packet_or_event": "SM_HpChange",
        "file_name": "FightNetLogic.lua",
        "function_names": {"SM_HpChangeFun"},
        "pattern": re.compile(r"\bSetProperty\(LuaEntityPropertyType\.(?:HP|VIRTUAL)"),
        "local_effect": "把服务端 HP/VIRTUAL 变化写入实体属性。",
        "server_authority_note": "这是实际属性同步路径，不经过 SkillBase hurt_event 分段。",
        "max_hits": 4,
    },
    {
        "phase_order": 110,
        "phase_id": "server_mp_property_sync",
        "authority": "server_state_sync",
        "packet_or_event": "SM_MpChange",
        "file_name": "FightNetLogic.lua",
        "function_names": {"SM_MpChangeFun"},
        "pattern": re.compile(r"\bSetProperty\(LuaEntityPropertyType\.MP"),
        "local_effect": "把服务端 MP 变化写入实体属性。",
        "server_authority_note": "这是实际 MP 属性同步路径。",
        "max_hits": 4,
    },
    {
        "phase_order": 120,
        "phase_id": "direct_unit_hp_tip_side_path",
        "authority": "server_result_side_path",
        "packet_or_event": "SM_UnitHpUpdate",
        "file_name": "FightNetLogic.lua",
        "function_names": {"SM_UnitHpUpdateFun"},
        "pattern": re.compile(r"\bUpdateHpChange\b"),
        "local_effect": "直接 HP 变化旁路，生成 HurtData/飘字表现。",
        "server_authority_note": "字段 damage/recoverHp 来自 SM_UnitHpUpdate，不经过 SkillBase timeline 分段。",
        "max_hits": 4,
    },
    {
        "phase_order": 130,
        "phase_id": "fixed_damage_hp_smoothing",
        "authority": "server_state_sync",
        "packet_or_event": "SM_FixDamage",
        "file_name": "FightNetLogic.lua",
        "function_names": {"SM_FixDamageFun"},
        "pattern": re.compile(r"\bHURT_HP_CHANGE\b|\bSetProperty\(LuaEntityPropertyType\.HP"),
        "local_effect": "Boss 固伤/限伤场景用定时器平滑血条，最后写回服务端 HP。",
        "server_authority_note": "平滑曲线是本地表现；最终 HP 仍用 msg.hp。",
        "max_hits": 8,
    },
]

_FIGHT_DAMAGE_FIELD_RE = re.compile(r"(damage|hurt|recover|hp|mp|absorb|shield)", re.IGNORECASE)

_HURT_FUNCTION_CONTEXT = {
    "NormalExecute": "普通战斗飘字入口；按回血、蓝量、伤害、反射、吸收等字段逐段展示。",
    "ExecutePerSeconds": "HurtTipsMgr 聚合后的简单战斗飘字入口；复用普通字段结构。",
    "XZHurtDataExecute": "仙战/探索回放场景飘字入口。",
    "DoupoPvpHurtDataExecute": "斗破/大话 PVP 场景飘字入口。",
    "OutcastHurtDataExecute": "放逐玩法飘字入口。",
    "TDHurtDataExecute": "塔防玩法伤害飘字入口。",
    "TDResistanceExecute": "塔防玩法抗性/控制提示飘字入口。",
    "DigitDoorHurtDataExecute": "数字门玩法伤害飘字入口。",
    "DigitDoorResistanceExecute": "数字门玩法抗性/控制提示飘字入口。",
    "DoupoTDHurtDataExecute": "斗破塔防玩法伤害飘字入口。",
    "BLLDHurtDataExecute": "特殊 PVP/玩法伤害飘字入口。",
    "BLLDRecoverHpExecute": "特殊 PVP/玩法回血飘字入口。",
    "BLLDPlayerHurtExecute": "特殊 PVP/玩法玩家受伤飘字入口。",
    "DoupoTDResistanceExecute": "斗破塔防抗性/控制提示飘字入口。",
    "ShowHurtTipsByType": "HurtTipsMgr 将聚合类型还原成 HurtData 字段。",
}

_LINGJIE_EQUIP_FLOW_FILES = {
    "GongFaBattleMainPanel.lua",
    "GongFaBattleCustomView.lua",
    "GongFaNewMgr.lua",
    "GongFaNewNetLogic.lua",
    "ChangeContentClass.lua",
    "CM_ReplaceSkill.lua",
    "SM_ReplaceSkill.lua",
    "SkillInfoVO.lua",
    "ShowSkillVO.lua",
    "SkillProgramVO.lua",
    "CM_GongFaSaveProgram.lua",
    "SM_GongFaSaveProgram.lua",
    "GongFaProgramVO.lua",
    "CM_XinFaPutUp.lua",
    "SM_XinFaPutUp.lua",
    "XinFaVO.lua",
    "HomeMakeXinFaVO.lua",
}

_LINGJIE_EQUIP_FLOW_PATTERNS = {
    "direct_replace_packet": re.compile(r"\bCM_ReplaceSkill(?:Fun)?\b|\bSM_ReplaceSkill\b"),
    "program_save_packet": re.compile(r"\bCM_GongFaSaveProgramFun\b|\bCM_GongFaSaveProgram\b|\bGongFaProgramVO\b"),
    "xinfa_putup_packet": re.compile(r"\bCM_XinFaPutUpFun\b|\bCM_XinFaPutUp\b|\bAutoEquipAllXinFa\b|\bXinFaVO\b"),
    "self_make_id": re.compile(r"\bmakeId\b|\bskillCommonVO\.id\b|\bxinFaId\.makeId\b"),
    "home_make_vo": re.compile(r"\bGongFaHomeMakeVO\b|\bgongFaHomeMakeVO\b|\bhomeMakeVO\b|\bHomeMakeXinFaVO\b"),
    "skill_id_projection": re.compile(r"\bGetLingjieGongfaStarCfgBySkillId\b|\bcfg\.skill\b"),
}

_LINGJIE_STATE_FLOW_FILES = {
    "GongFaNewModel.lua",
    "GongFaNewData.lua",
    "GongFaNewNetLogic.lua",
    "GongfahomemakeModel.lua",
    "GongfahomemakeData.lua",
    "GongfahomemakeNetLogic.lua",
}

_LINGJIE_STATE_FLOW_PATTERNS = {
    "xinfa_state": re.compile(r"\bSetXinFaInfo\b|\bxinFaPutUpList\b|\bCHANGE_BATTLE_XIN_FA\b"),
    "program_state": re.compile(r"\bGongFaSaveProgram\b|\bSetGongFaProgram\b|\bAddGongFaProgram\b|\bprogramVO\b"),
    "home_make_cache": re.compile(r"\bSetGongFaHomeMakeList\b|\bhomeMakeDic\b|\bGetGongFaHomeMakeVoById\b"),
    "home_make_instance_update": re.compile(
        r"\bGongFaHomeMakeCombine(?:Update|List)?\b|\bUpdateGongFaHomeMakeLearn\b|\bGongFaHomeMakeChangeName\b"
    ),
    "event_refresh": re.compile(
        r"\bCHANGE_BATTLE_XIN_FA\b|\bGongFaSaveProgram(?:_PVP)?\b|\bGongFaHomeMakeList\b|"
        r"\bUpdateGongFaHomeMakeLearn\b|\bCREATING_SKILL_UPDATE\b|\bTenCreate\b"
    ),
}

_SKILL_CORE_FLOW_FILES = {
    "SkillNetLogic.lua",
    "SkillMgr.lua",
    "SkillModel.lua",
    "SkillData.lua",
    "SkillBase.lua",
    "SkillConfig.lua",
}

_SKILL_CORE_FLOW_PATTERNS = {
    "packet_register": re.compile(r"\bF_Register\b|\bF_Unregister\b"),
    "replace_request": re.compile(r"\bCM_ReplaceSkillFun\b|\bCM_ReplaceSkill\."),
    "replace_response": re.compile(r"\bSM_ReplaceSkillFun\b|\bSetChangeSkillGroupData\b"),
    "auto_replace": re.compile(r"\bAutoReplaceUpSkill\b|\bCM_AutoReplaceFun\b|\bSM_AutoReplaceFun\b|\bSetChangeNoUpGroupData\b"),
    "change_group": re.compile(r"\bCM_ChangeGroupFun\b|\bSM_ChangeGroupFun\b|\bSetChangeGroupData\b"),
    "show_group": re.compile(r"\bSM_ShowSkillFun\b|\bSM_ShowReplaceSkillFun\b|\bSetShowSkillGroupData\b"),
    "group_cache": re.compile(r"\bUpdateGroupSkills\b|\bSetGroupSkillInfo\b|\bGetDefaultSkillGroupData\b|\bGetShowSkillGroupData\b"),
    "cd_cache": re.compile(r"\bSetSkillCD\b|\bcds\b|\bcdDic\b|\bsystemTime\b"),
    "battle_apply": re.compile(r"\bChangeBattleGroupSkills\b|\bChangeBattleSkills\b|\bUpdateBattleGroupSkill\b|\bLoadSkills\b"),
    "equip_check": re.compile(r"\bCheckGongFaIsEquipById\b|\bGongfahomemakeType\.SkillType\.Create\b|\bcreateId:Equal\b"),
    "event_refresh": re.compile(r"\bReFreshSkillGroupData\b|\bCHANGE_BATTLE_SKILL\b|\bChangeGongFa\b"),
    "timeline_resolve": re.compile(
        r"\bGetTimelineIdBySkillId\b|\bjian_timelineId\b|\bmo_timelineId\b|\bsha_timelineId\b|\bxian_timelineId\b"
    ),
    "timeline_update": re.compile(r"\bUpdateTimelineData\b|\btimeline_id\b|\bGetSkillInfo\b"),
    "skill_ex_params": re.compile(r"\bGetSkillExParams\b|\bSkill_SkillExParams\b|\bexParamCfg\.channel\b|\breal_section_dmg\b"),
}

_SKILL_MGR_REF_RE = re.compile(
    r"\bSkillMgr\.Inst_get\(\)(?:\.[A-Za-z0-9_]+)*(?::|\.)([A-Za-z0-9_]+)"
    r"|\bSkillData(?::|\.)([A-Za-z0-9_]+)"
)

_BATTLE_DAMAGE_FLOW_FILES = {
    "SkillBase.lua",
    "SkillConfig.lua",
    "BulletMgr.lua",
}

_BATTLE_DAMAGE_FLOW_PATTERNS = {
    "timeline_id": re.compile(
        r"\bGetTimelineIdBySkillId\b|\bjian_timelineId\b|\bmo_timelineId\b|\bsha_timelineId\b|\bxian_timelineId\b"
    ),
    "skill_ex_params": re.compile(r"\bGetSkillExParams\b|\bSkill_SkillExParams\b|\bexParamCfg\.channel\b"),
    "timeline_data": re.compile(r"\bUpdateTimelineData\b|\bCfg_Hurts\b|\bCfg_keyFrames\b|\bGetTimeLineAllData\b"),
    "section_damage": re.compile(r"\breal_section_dmg\b|\bhurt_index\b|\bbIgnore\b"),
    "damage_split": re.compile(
        r"\bhurt_event\[2\]\b|\bpercent\b|\bdamage_num\b|\bdamage_view\b|\brecover_num\b|"
        r"\bdamage_reflect\b|\bmpDamage_num\b|\bmpDamage_view\b"
    ),
    "hurt_schedule": re.compile(r"\bAdd4HurtDataListDic\b|\btrajectoryCachedHurtVo\b|\bFindBulletByHurtIndex\b|\bAddHurtData\b"),
    "presentation": re.compile(r"\bPlayBattleSkillSuffer\b|\bPlayBattleSkill\b|\bnot self\.real_section_dmg\b"),
    "range_check": re.compile(r"\bIsInSkillCastArea\b|\bDamageCenterType\b|\bScopeType\b|\bscope_param\b"),
}

_APK_RUNTIME_SYMBOL_TERMS = (
    "SkillMgr",
    "SkillNetLogic",
    "SkillData",
    "CM_ReplaceSkillFun",
    "SM_ReplaceSkillFun",
    "CM_ReplaceSkill",
    "SM_ReplaceSkill",
    "AutoReplaceUpSkill",
    "CheckGongFaIsEquipById",
    "GetDefaultSkillGroupData",
    "GetShowSkillGroupData",
    "GetChangeGroupData",
    "GongFaBattleMainPanel",
    "GongFaNewMgr",
    "GongFaNewNetLogic",
    "GongfahomemakeMgr",
    "GongfahomemakeModel",
    "GongfahomemakeNetLogic",
    "GongFaHomeMakeVO",
    "CreateSkillCommonVO",
    "SkillInfoVO",
    "SkillProgramVO",
    "CM_XinFaPutUp",
    "CM_GongFaSaveProgram",
)

_APK_SYMBOL_FIXED_TARGETS = {
    Path("assets/bin/Data/Managed/Metadata/global-metadata.dat"): "il2cpp_metadata",
    Path("lib/arm64-v8a/libil2cpp.so"): "native_il2cpp_arm64",
    Path("lib/armeabi-v7a/libil2cpp.so"): "native_il2cpp_armv7",
}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_export_dir(path: str | Path | None, default: Path, *, export_root: str | Path | None = None) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    raw_path = Path(path) if path else default
    resolved = raw_path.expanduser().resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    if not _is_relative_to(resolved, root):
        raise FanxiuResourceError(f"目录必须位于导出根目录内：{root}")
    if not resolved.is_dir():
        raise FanxiuResourceError(f"目录不存在：{resolved}")
    return resolved


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _join_unique(values: list[Any], *, limit: int = 20) -> str:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        text = "" if value is None else str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= limit:
            break
    return "、".join(items)


def _join_items(values: list[Any], *, limit: int = 80) -> str:
    return "、".join("" if value is None else str(value).strip() for value in values[:limit])


def _safe_export_part(value: str, fallback: str = "asset") -> str:
    text = re.sub(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+", "_", str(value or "").strip()).strip("._")
    return text[:80] if text else fallback


def _count_map_text(counts: Any) -> str:
    if not isinstance(counts, dict):
        return ""
    items = sorted(counts.items(), key=lambda item: (-int(item[1] or 0), str(item[0])))
    return "、".join(f"{key}:{value}" for key, value in items)


def _unity_object_names(objects: Any, type_names: set[str] | None = None, *, limit: int = 30) -> str:
    names: list[str] = []
    for obj in objects if isinstance(objects, list) else []:
        if not isinstance(obj, dict):
            continue
        type_name = str(obj.get("type_name") or "")
        if type_names is not None and type_name not in type_names:
            continue
        name = str(obj.get("name") or "").strip()
        if name:
            names.append(name)
    return _join_unique(names, limit=limit)


def _line_numbers_for(pattern: re.Pattern[str], lines: list[str], group: int | str | None = None) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for line_no, line in enumerate(lines, start=1):
        for match in pattern.finditer(line):
            hits.append((line_no, match.group(group) if group else match.group(0)))
    return hits


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _function_spans(lines: list[str]) -> list[dict[str, Any]]:
    starts: list[dict[str, Any]] = []
    for line_no, line in enumerate(lines, start=1):
        match = _FUNCTION_RE.search(line)
        if match:
            starts.append({"name": match.group(1), "start": line_no, "end": len(lines)})
    for index, item in enumerate(starts[:-1]):
        item["end"] = starts[index + 1]["start"] - 1
    return starts


def _function_for_line(spans: list[dict[str, Any]], line_no: int) -> str:
    for span in reversed(spans):
        if int(span["start"]) <= line_no <= int(span["end"]):
            return str(span["name"])
    return ""


def _runtime_scope_for_file(file_name: str) -> str:
    if file_name == "GongfahomemakeData.lua":
        return "data_index"
    if file_name == "GongfahomemakeModel.lua":
        return "model_query"
    if file_name == "GongfahomemakeMgr.lua":
        return "manager_logic"
    if file_name == "GongfahomemakeNetLogic.lua":
        return "net_logic"
    if file_name.endswith("View.lua") or file_name.endswith("Item.lua"):
        return "ui_view"
    return "other"


def _packet_role_for_name(name: str) -> str:
    if name == "SkillProgramVO":
        return "equip_program"
    if name in {"SkillInfoVO", "ShowSkillVO"}:
        return "skill_reference"
    if "ReplaceSkill" in name:
        return "equip_replace"
    if "SaveProgram" in name or name == "GongFaProgramVO":
        return "equip_program"
    if "XinFaPutUp" in name or name in {"XinFaVO", "HomeMakeXinFaVO"}:
        return "xinfa_equip"
    if "Combine" in name:
        return "compose"
    if "ChangeName" in name or "CheckName" in name:
        return "name"
    if "LightUp" in name:
        return "light_up"
    if "Learn" in name:
        return "learn"
    if "Teach" in name:
        return "teach"
    if "Upload" in name:
        return "upload"
    if "Exchange" in name:
        return "exchange"
    if "Grid" in name:
        return "ascension_grid"
    if "Record" in name:
        return "record"
    if "PageList" in name or name.endswith("List") or "ListVO" in name:
        return "list_filter"
    if "Check" in name:
        return "check"
    if "SelectCareer" in name:
        return "career"
    return "other"


def _direction_for_packet_name(name: str) -> str:
    if name.startswith("CM_"):
        return "client_to_server"
    if name.startswith("SM_"):
        return "server_to_client"
    if name.endswith("VO") or name.endswith("DTO"):
        return "value_object"
    return "other"


def _packet_field_signature(fields: list[dict[str, str]], *, limit: int = 30) -> str:
    parts: list[str] = []
    for field in fields[:limit]:
        read_method = field.get("read_method") or ""
        type_hint = field.get("type_hint") or ""
        suffix = f"<{type_hint}>" if type_hint else ""
        parts.append(f"{field.get('field_name')}:{read_method}{suffix}")
    if len(fields) > limit:
        parts.append(f"...+{len(fields) - limit}")
    return ", ".join(parts)


def _fight_result_schema_role(name: str) -> str:
    if name == "SM_FightResult":
        return "result_packet"
    if name == "FightResultVO":
        return "result_vo"
    if name.startswith("SM_FightResult"):
        return "special_result_packet"
    return "other"


def _fight_result_field_semantics(schema_name: str, field_name: str) -> str:
    if (schema_name, field_name) in _FIGHT_RESULT_FIELD_SEMANTICS:
        return _FIGHT_RESULT_FIELD_SEMANTICS[(schema_name, field_name)]
    if schema_name.startswith("SM_FightResult") and ("SM_FightResult", field_name) in _FIGHT_RESULT_FIELD_SEMANTICS:
        return _FIGHT_RESULT_FIELD_SEMANTICS[("SM_FightResult", field_name)]
    return ""


def _extract_lua_number_table(lines: list[str], table_name: str) -> list[dict[str, Any]]:
    marker = f"_M.{table_name}"
    rows: list[dict[str, Any]] = []
    waiting_for_open = False
    in_table = False
    for line_no, line in enumerate(lines, start=1):
        if not in_table and marker in line:
            waiting_for_open = True
        if waiting_for_open and "{" in line:
            in_table = True
            waiting_for_open = False
            continue
        if in_table and "}" in line:
            break
        if not in_table:
            continue
        match = re.search(r"\b([A-Za-z0-9_]+)\s*=\s*(-?\d+)\s*,?", line)
        if not match:
            continue
        rows.append(
            {
                "name": match.group(1),
                "value": int(match.group(2)),
                "line": line_no,
            }
        )
    return rows


def _bit_index(value: int) -> str:
    if value <= 0 or value & (value - 1):
        return ""
    return str(value.bit_length() - 1)


def _extract_hurt_data_effect_format(lines: list[str]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    spans = _function_spans(lines)
    span = next((item for item in spans if item.get("name") == "FormatHurtTipsAndType"), None)
    if not span:
        return rows
    start = int(span["start"]) - 1
    end = int(span["end"])
    function_lines = lines[start:end]
    for offset, line in enumerate(function_lines):
        if "HasEffect(" not in line:
            continue
        effect_names = re.findall(r"SkillDefine\.FightCastEffect\.([A-Z0-9_]+)", line)
        if not effect_names:
            continue
        block_lines: list[str] = []
        for next_line in function_lines[offset : min(len(function_lines), offset + 12)]:
            stripped = next_line.strip()
            if block_lines and (
                stripped.startswith("elseif ")
                or stripped.startswith("else")
                or stripped == "end"
            ):
                break
            block_lines.append(next_line)
        block = "\n".join(block_lines)
        prefix_match = re.search(r"hurt_tips:Append\([\"']([^\"']*)[\"']\)", block)
        blood_match = re.search(r"bloodType\s*=\s*BloodType\.([A-Za-z0-9_]+)", block)
        resolved_match = re.search(r"fightEffect\s*=\s*SkillDefine\.FightCastEffect\.([A-Z0-9_]+)", block)
        for effect_name in effect_names:
            rows.setdefault(
                effect_name,
                {
                    "format_line": start + offset + 1,
                    "hurt_tip_prefix": prefix_match.group(1).strip() if prefix_match else "",
                    "blood_type": blood_match.group(1) if blood_match else "",
                    "resolved_fight_effect": resolved_match.group(1) if resolved_match else "",
                    "ignore_damage": "1" if "ignoreDmg=true" in block else "0",
                    "special_cast": "1" if "isSpecialCast=true" in block else "0",
                },
            )
    if "DODGE" not in rows:
        rows["DODGE"] = {
            "format_line": "",
            "hurt_tip_prefix": "m",
            "blood_type": "CRIT_HURT",
            "resolved_fight_effect": "",
            "ignore_damage": "1",
            "special_cast": "1",
        }
    if "DODGE_DAMAGE" not in rows:
        rows["DODGE_DAMAGE"] = {
            "format_line": "",
            "hurt_tip_prefix": "m",
            "blood_type": "CRIT_HURT",
            "resolved_fight_effect": "",
            "ignore_damage": "1",
            "special_cast": "1",
        }
    return rows


def _find_fight_config_value_source(root: Path) -> Path | None:
    candidates = [
        path
        for path in root.glob("by_source/lscripts/generate/cfg/fight_*/text_assets/ConfigValue.lua")
        if path.is_file()
    ]
    if not candidates:
        return None
    with_font_keys = [
        path
        for path in candidates
        if "font_NormalDamage" in path.read_text(encoding="utf-8-sig", errors="replace")
    ]
    return max(with_font_keys or candidates, key=lambda item: item.stat().st_mtime_ns)


def _duration_pair_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_pair in str(value or "").split(","):
        raw_pair = raw_pair.strip()
        if not raw_pair:
            continue
        left, sep, right = raw_pair.partition("_")
        if not sep:
            rows.append(
                {
                    "raw_pair": raw_pair,
                    "input_index": "",
                    "duration_ms": "",
                    "parse_status": "missing_separator",
                }
            )
            continue
        try:
            input_index: int | str = int(left)
        except ValueError:
            input_index = left
        try:
            duration_ms: int | str = int(right)
        except ValueError:
            duration_ms = right
        rows.append(
            {
                "raw_pair": raw_pair,
                "input_index": input_index,
                "duration_ms": duration_ms,
                "parse_status": "ok",
            }
        )
    return rows


def _build_fight_config_value_rows(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_path = _find_fight_config_value_source(root)
    if not source_path:
        return [], {"status": "missing_source", "source_path": ""}
    parsed = parse_fanxiu_generated_lua_config(source_path)
    rows = [
        {
            "config_key": row.get("id", row.get("_row_key", "")),
            "value": row.get("value", ""),
            "source_file": source_path.relative_to(root).as_posix(),
        }
        for row in parsed.get("rows", [])
    ]
    rows.sort(key=lambda item: str(item["config_key"]))
    return rows, {
        "status": "ok",
        "source_path": source_path.relative_to(root).as_posix(),
        "row_count": len(rows),
    }


def _build_hurt_tips_config_rows(
    fight_config_value_rows: list[dict[str, Any]],
    fight_effect_flag_rows: list[dict[str, Any]],
    hurt_tips_type_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    values_by_key = {str(row.get("config_key") or ""): row.get("value", "") for row in fight_config_value_rows}
    effect_by_value = {int(row["value"]): str(row["effect_name"]) for row in fight_effect_flag_rows}
    effect_semantics_by_value = {int(row["value"]): str(row.get("semantics") or "") for row in fight_effect_flag_rows}
    tips_by_value = {int(row["value"]): str(row["tips_type_name"]) for row in hurt_tips_type_rows}
    rows: list[dict[str, Any]] = []

    for pair in _duration_pair_rows(values_by_key.get("font_NormalDamage")):
        input_index = pair.get("input_index")
        mapped_value: int | str = ""
        mapped_name = ""
        semantics = ""
        if isinstance(input_index, int):
            mapped_value = 2 ** input_index if input_index > 0 else input_index
            mapped_name = effect_by_value.get(mapped_value, "")
            semantics = effect_semantics_by_value.get(mapped_value, "")
        duration_ms = pair.get("duration_ms", "")
        duration_seconds = float(duration_ms) * 0.001 if isinstance(duration_ms, int) else ""
        rows.append(
            {
                "config_key": "font_NormalDamage",
                "config_kind": "normal_damage_by_fight_effect",
                "raw_value": values_by_key.get("font_NormalDamage", ""),
                "raw_pair": pair.get("raw_pair", ""),
                "input_index": input_index,
                "mapping_rule": "index>0 maps to FightCastEffect value 2^index; index<=0 keeps raw value",
                "mapped_value": mapped_value,
                "mapped_name": mapped_name,
                "configured_duration_ms": duration_ms,
                "configured_duration_seconds": duration_seconds,
                "runtime_timer_seconds": duration_seconds,
                "runtime_note": "HurtTipsMgr.NormalDamageDurationCfg uses configured ms * 0.001.",
                "semantics": semantics,
                "parse_status": pair.get("parse_status", ""),
            }
        )

    for pair in _duration_pair_rows(values_by_key.get("font_OtherNormalFont")):
        input_index = pair.get("input_index")
        mapped_name = tips_by_value.get(input_index, "") if isinstance(input_index, int) else ""
        duration_ms = pair.get("duration_ms", "")
        duration_seconds = float(duration_ms) * 0.001 if isinstance(duration_ms, int) else ""
        rows.append(
            {
                "config_key": "font_OtherNormalFont",
                "config_kind": "other_damage_by_hurt_tips_type",
                "raw_value": values_by_key.get("font_OtherNormalFont", ""),
                "raw_pair": pair.get("raw_pair", ""),
                "input_index": input_index,
                "mapping_rule": "direct HurtTipsType value",
                "mapped_value": input_index,
                "mapped_name": mapped_name,
                "configured_duration_ms": duration_ms,
                "configured_duration_seconds": duration_seconds,
                "runtime_timer_seconds": 1,
                "runtime_note": "Config is parsed into OtherDamageDurationCfg, but current AddTipsNum sets non-normal timer duration to 1 second.",
                "semantics": "",
                "parse_status": pair.get("parse_status", ""),
            }
        )

    rose_value = values_by_key.get("font_special_rose", "")
    if rose_value != "":
        rows.append(
            {
                "config_key": "font_special_rose",
                "config_kind": "rose_damage_throttle",
                "raw_value": rose_value,
                "raw_pair": rose_value,
                "input_index": "",
                "mapping_rule": "assigned directly to HurtTipsMgr.RoseDamageDuration",
                "mapped_value": "",
                "mapped_name": "",
                "configured_duration_ms": rose_value,
                "configured_duration_seconds": "",
                "runtime_timer_seconds": "",
                "runtime_note": "HurtTipsMgr compares Time:GetTimestamp delta with RoseDamageDuration directly.",
                "semantics": "特殊玫瑰伤害飘字节流时间。",
                "parse_status": "ok",
            }
        )

    return rows


def _find_lua_text_asset(root: Path, file_name: str, *, must_contain: str | None = None) -> Path | None:
    candidates = sorted(root.glob(f"by_source/lscripts/**/text_assets/{file_name}"))
    if must_contain:
        candidates = [
            path
            for path in candidates
            if must_contain in path.read_text(encoding="utf-8-sig", errors="replace")
        ]
    return candidates[0] if candidates else None


def _relative_to_root(path: Path | None, root: Path) -> str:
    if not path:
        return ""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _extract_lua_number_assignments(lines: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(lines, start=1):
        match = re.search(r"\b_M\.([A-Za-z0-9_]+)\s*=\s*(-?\d+)\b", line)
        if match:
            rows.append({"name": match.group(1), "value": int(match.group(2)), "line": line_no})
    return rows


def _extract_panel_blood_tip_rows(lines: list[str]) -> tuple[str, dict[str, dict[str, Any]]]:
    ui_asset = ""
    components: dict[str, str] = {}
    rows: dict[str, dict[str, Any]] = {}
    for line_no, line in enumerate(lines, start=1):
        asset_match = re.search(r"Tips3DUIShowComponent\.new\([\"']([^\"']+)[\"']", line)
        if asset_match:
            ui_asset = asset_match.group(1)
        component_match = re.search(r"\blocal\s+([A-Za-z0-9_]+)\s*=\s*self:SetComponent\(LuaGameObject,\s*(\d+)\)", line)
        if component_match:
            components[component_match.group(1)] = component_match.group(2)
        map_match = re.search(r"\[BloodType\.([A-Z0-9_]+)\]\s*=\s*([A-Za-z0-9_]+)", line)
        if not map_match:
            continue
        blood_type = map_match.group(1)
        prefab_var = map_match.group(2)
        rows[blood_type] = {
            "prefab_var": prefab_var,
            "prefab_component_id": components.get(prefab_var, ""),
            "panel_line": line_no,
        }
    return ui_asset, rows


def _extract_blood_tip_item_animation_rows(lines: list[str]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    current_blood_type = ""
    current_line: int | str = ""
    for line_no, line in enumerate(lines, start=1):
        if "bloodType" in line and "BloodType." in line:
            match = re.search(r"BloodType\.([A-Z0-9_]+)", line)
            if match:
                current_blood_type = match.group(1)
                current_line = line_no
        anim_match = re.search(r"PlayAnim\([\"']([^\"']+)[\"']", line)
        if current_blood_type and anim_match:
            rows[current_blood_type] = {
                "animation_name": anim_match.group(1),
                "animation_line": line_no,
                "branch_line": current_line,
            }
            current_blood_type = ""
            current_line = ""
    return rows


def _blood_type_position_rule(name: str) -> str:
    if name in {"TD_SELF_HURT", "DIGIT_DOOR_SELF_HURT"}:
        return "self_hurt_high_offset_then_default_random"
    if name in {"TD_DAMAGE", "TD_CIRT", "DIGIT_DOOR_DAMAGE", "DIGIT_DOOR_CIRT"}:
        return "tower_defense_cluster_random"
    if name.startswith("OUTCAST_") or name.startswith("XZ_") or name.startswith("DOUPOPVP_"):
        return "fixed_entity_position_no_random_offset"
    return "default_random_offset"


def _build_blood_type_ui_rows(root: Path, fight_effect_flag_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blood_type_path = _find_lua_text_asset(root, "BloodType.lua", must_contain="Core.Battle.Entity.Const.BloodType")
    panel_path = _find_lua_text_asset(root, "PanelBloodTips.lua", must_contain="PanelBloodTips_1")
    item_path = _find_lua_text_asset(root, "BloodTipItem.lua", must_contain="PlayAnim")

    blood_defs: dict[str, dict[str, Any]] = {}
    if blood_type_path:
        blood_lines = blood_type_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        for row in _extract_lua_number_assignments(blood_lines):
            blood_defs[row["name"]] = row

    ui_asset = ""
    panel_rows: dict[str, dict[str, Any]] = {}
    if panel_path:
        panel_lines = panel_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        ui_asset, panel_rows = _extract_panel_blood_tip_rows(panel_lines)

    animation_rows: dict[str, dict[str, Any]] = {}
    if item_path:
        item_lines = item_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        animation_rows = _extract_blood_tip_item_animation_rows(item_lines)

    effects_by_blood_type: dict[str, list[str]] = defaultdict(list)
    prefixes_by_blood_type: dict[str, list[str]] = defaultdict(list)
    for row in fight_effect_flag_rows:
        blood_type = str(row.get("blood_type") or "")
        if not blood_type:
            continue
        effects_by_blood_type[blood_type].append(str(row.get("effect_name") or ""))
        prefix = str(row.get("hurt_tip_prefix") or "")
        if prefix:
            prefixes_by_blood_type[blood_type].append(f"{row.get('effect_name')}:{prefix}")

    rows: list[dict[str, Any]] = []
    names = sorted(set(blood_defs) | set(panel_rows) | set(animation_rows))
    for name in names:
        definition = blood_defs.get(name, {})
        panel = panel_rows.get(name, {})
        animation = animation_rows.get(name, {})
        rows.append(
            {
                "blood_type_name": name,
                "value": definition.get("value", ""),
                "semantics": _BLOOD_TYPE_SEMANTICS.get(name, ""),
                "source_file": _relative_to_root(blood_type_path, root),
                "source_line": definition.get("line", ""),
                "ui_prefab_path": ui_asset,
                "panel_source_file": _relative_to_root(panel_path, root),
                "prefab_var": panel.get("prefab_var", ""),
                "prefab_component_id": panel.get("prefab_component_id", ""),
                "panel_line": panel.get("panel_line", ""),
                "animation_source_file": _relative_to_root(item_path, root),
                "animation_name": animation.get("animation_name", ""),
                "animation_line": animation.get("animation_line", ""),
                "produced_by_fight_effects": _join_unique(effects_by_blood_type.get(name, []), limit=30),
                "effect_prefixes": _join_unique(prefixes_by_blood_type.get(name, []), limit=30),
                "panel_position_rule": _blood_type_position_rule(name),
            }
        )
    rows.sort(key=lambda row: int(row["value"]) if isinstance(row.get("value"), int) else 10**9)
    return rows


def _split_lua_args(arg_text: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    depth = 0
    quote = ""
    escape = False
    for char in arg_text:
        if quote:
            current.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}" and depth > 0:
            depth -= 1
        if char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        args.append(tail)
    return args


def _collect_lua_call(lines: list[str], start_index: int, function_name: str) -> tuple[str, int]:
    marker = f"{function_name}("
    collected: list[str] = []
    depth = 0
    quote = ""
    escape = False
    started = False
    for index in range(start_index, len(lines)):
        line = lines[index]
        collected.append(line.strip())
        scan_start = 0
        if not started:
            marker_index = line.find(marker)
            if marker_index < 0:
                continue
            started = True
            scan_start = marker_index + len(function_name)
        for char in line[scan_start:]:
            if quote:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == quote:
                    quote = ""
                continue
            if char in {"'", '"'}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth <= 0:
                    return " ".join(collected), index
    return " ".join(collected), start_index


def _extract_call_args(line: str, function_name: str) -> list[str]:
    marker = f"{function_name}("
    index = line.find(marker)
    if index < 0:
        return []
    start = index + len(marker)
    depth = 1
    quote = ""
    escape = False
    for pos in range(start, len(line)):
        char = line[pos]
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return _split_lua_args(line[start:pos])
    return _split_lua_args(line[start:])


def _blood_type_names_from_text(text: str) -> list[str]:
    return re.findall(r"BloodType\.([A-Z0-9_]+)", text)


def _blood_type_candidates_for_expr(expr: str, function_lines: list[str]) -> str:
    direct = _blood_type_names_from_text(expr)
    if direct:
        return _join_unique(direct, limit=20)
    if expr.strip() != "bloodType":
        return ""
    context = "\n".join(function_lines)
    for format_name, candidates in _HURT_FORMAT_BLOOD_CANDIDATES.items():
        if format_name in context:
            return candidates
    names = _blood_type_names_from_text(context)
    return _join_unique(names, limit=40)


def _blood_type_context_for_call(function_lines: list[str], line_index: int) -> list[str]:
    start = max(0, line_index - 35)
    for index in range(line_index, start - 1, -1):
        line = function_lines[index]
        if re.search(r"\blocal\s+bloodType\b", line):
            return function_lines[index : line_index + 1]
    for index in range(line_index, start - 1, -1):
        line = function_lines[index]
        if re.search(r"\blocal\s+[^=\n]*\bbloodType\b", line):
            return function_lines[index : line_index + 1]
    return function_lines[start : line_index + 1]


def _infer_hurt_source_metric(function_lines: list[str], line_index: int, call_args: list[str]) -> str:
    known = sorted(_HURT_SOURCE_FIELD_HINTS, key=len, reverse=True)
    joined_args = ",".join(call_args)
    for name in known:
        if re.search(rf"\b{re.escape(name)}\b", joined_args):
            return name
    start = max(0, line_index - 45)
    for line in reversed(function_lines[start:line_index]):
        for name in known:
            if re.search(rf"\b(?:self\.)?{re.escape(name)}\s*~=|local\s+{re.escape(name)}\s*=|\b{re.escape(name)}\s*>", line):
                return name
            if re.search(rf"ConvertBigDouble\(\s*{re.escape(name)}\s*\)", line):
                return name
        match = re.search(r"Format(?:[A-Za-z0-9_]+)?HurtTipsAndType\([^)]*,\s*([A-Za-z0-9_]+)\)", line)
        if match and match.group(1) in _HURT_SOURCE_FIELD_HINTS:
            return match.group(1)
    return ""


def _source_field_hint(metric: str) -> str:
    return _HURT_SOURCE_FIELD_HINTS.get(metric, "")


def _scene_gate_for_function(function_name: str) -> str:
    if function_name == "XZHurtDataExecute":
        return "Scene_XZExploreReplay"
    if function_name == "DoupoPvpHurtDataExecute":
        return "DoupoPvp / DaHuaPvp / DaHuaES scene"
    if function_name.startswith("TD") or function_name.startswith("DoupoTD"):
        return "TowerDefense / DoupoTD"
    if function_name.startswith("DigitDoor"):
        return "DigitDoor"
    if function_name.startswith("Outcast"):
        return "Outcast"
    if function_name.startswith("BLLD"):
        return "BLLD special scene"
    if function_name == "NormalExecute":
        return "default battle scene"
    if function_name == "ExecutePerSeconds":
        return "simple fight aggregated display"
    return ""


def _extract_hurt_data_blood_source_rows(root: Path) -> list[dict[str, Any]]:
    hurt_data_path = _find_lua_text_asset(root, "HurtData.lua", must_contain="FormatHurtTipsAndType")
    rows: list[dict[str, Any]] = []
    if hurt_data_path:
        lines = hurt_data_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        spans = _function_spans(lines)
        for span in spans:
            function_name = str(span["name"])
            function_lines = lines[int(span["start"]) - 1 : int(span["end"])]
            for offset, line in enumerate(function_lines):
                line_no = int(span["start"]) + offset
                if "ShowBloodTips(" in line:
                    args = _extract_call_args(line, "ShowBloodTips")
                    blood_expr = args[1] if len(args) > 1 else ""
                    metric = _infer_hurt_source_metric(function_lines, offset, args)
                    local_context = _blood_type_context_for_call(function_lines, offset)
                    rows.append(
                        {
                            "source_file": _relative_to_root(hurt_data_path, root),
                            "function_name": function_name,
                            "line": line_no,
                            "call_kind": "direct_show_blood_tips",
                            "scene_gate": _scene_gate_for_function(function_name),
                            "runtime_context": _HURT_FUNCTION_CONTEXT.get(function_name, ""),
                            "source_metric": metric,
                            "source_field_hint": _source_field_hint(metric),
                            "tips_type": "",
                            "fight_effect_expr": "",
                            "blood_type_expr": blood_expr,
                            "blood_type_candidates": _blood_type_candidates_for_expr(blood_expr, local_context),
                            "target_expr": args[0] if args else "",
                            "amount_arg": "",
                            "tip_expr": args[2] if len(args) > 2 else "",
                            "code": line.strip()[:240],
                        }
                    )
                if "AddTipsNum(" in line:
                    args = _extract_call_args(line, "AddTipsNum")
                    tips_type_match = re.search(r"HurtTipsType\.([A-Za-z0-9_]+)", args[1] if len(args) > 1 else "")
                    tips_type = tips_type_match.group(1) if tips_type_match else ""
                    metric = _infer_hurt_source_metric(function_lines, offset, args)
                    rows.append(
                        {
                            "source_file": _relative_to_root(hurt_data_path, root),
                            "function_name": function_name,
                            "line": line_no,
                            "call_kind": "simple_fight_aggregate_add",
                            "scene_gate": "FightMgr:IsInSimpleFight",
                            "runtime_context": _HURT_FUNCTION_CONTEXT.get(function_name, ""),
                            "source_metric": metric,
                            "source_field_hint": _source_field_hint(metric),
                            "tips_type": tips_type,
                            "fight_effect_expr": args[2] if len(args) > 3 else "",
                            "blood_type_expr": "",
                            "blood_type_candidates": _HURT_TIPS_TYPE_BLOOD_CANDIDATES.get(tips_type, ""),
                            "target_expr": args[0] if args else "",
                            "amount_arg": args[-1] if len(args) >= 3 else "",
                            "tip_expr": "",
                            "code": line.strip()[:240],
                        }
                    )

    tips_mgr_path = _find_lua_text_asset(root, "HurtTipsMgr.lua", must_contain="ShowHurtTipsByType")
    if tips_mgr_path:
        lines = tips_mgr_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        span = next((item for item in _function_spans(lines) if item.get("name") == "ShowHurtTipsByType"), None)
        if span:
            function_lines = lines[int(span["start"]) - 1 : int(span["end"])]
            for offset, line in enumerate(function_lines):
                match = re.search(r"tipsType==SkillDefine\.HurtTipsType\.([A-Za-z0-9_]+)", line)
                if not match:
                    continue
                tips_type = match.group(1)
                block_lines: list[str] = [line]
                for next_line in function_lines[offset + 1 : min(len(function_lines), offset + 12)]:
                    stripped = next_line.strip()
                    if stripped.startswith("elseif ") or stripped == "end":
                        break
                    block_lines.append(next_line)
                block = "\n".join(block_lines)
                assigned_metrics = [
                    name
                    for name in _HURT_SOURCE_FIELD_HINTS
                    if re.search(rf"\b{re.escape(name)}\s*=\s*totalNum\b", block)
                ]
                rows.append(
                    {
                        "source_file": _relative_to_root(tips_mgr_path, root),
                        "function_name": "ShowHurtTipsByType",
                        "line": int(span["start"]) + offset,
                        "call_kind": "simple_fight_type_decode",
                        "scene_gate": "HurtTipsMgr.ExecuteTips",
                        "runtime_context": _HURT_FUNCTION_CONTEXT["ShowHurtTipsByType"],
                        "source_metric": _join_unique(assigned_metrics or ["totalNum"], limit=10),
                        "source_field_hint": _join_unique([_source_field_hint(item) for item in assigned_metrics], limit=10),
                        "tips_type": tips_type,
                        "fight_effect_expr": "fightEffect",
                        "blood_type_expr": "via HurtData.ExecutePerSeconds",
                        "blood_type_candidates": _HURT_TIPS_TYPE_BLOOD_CANDIDATES.get(tips_type, ""),
                        "target_expr": "targetId",
                        "amount_arg": "totalNum",
                        "tip_expr": "",
                        "code": " ".join(item.strip() for item in block_lines)[:240],
                    }
                )

    rows.sort(key=lambda row: (str(row["source_file"]), str(row["function_name"]), int(row["line"] or 0), str(row["call_kind"])))
    return rows


def _lua_assignments_before(function_lines: list[str], line_index: int) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for line in function_lines[:line_index]:
        stripped = line.strip()
        if stripped.startswith(("if ", "elseif ", "for ", "while ", "function ")):
            continue
        match = re.match(r"(?:local\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$", stripped)
        if not match:
            continue
        name = match.group(1)
        expr = match.group(2).strip()
        if name in {"self"} or expr.startswith("="):
            continue
        old_expr = assignments.get(name)
        if old_expr and old_expr != expr:
            if "resultVo." in old_expr and "resultVo." not in expr:
                continue
            if "resultVo." in expr and "resultVo." not in old_expr:
                assignments[name] = expr[:240]
                continue
            assignments[name] = f"{old_expr} | {expr}"[:240]
            continue
        assignments[name] = expr[:240]
    return assignments


def _resolve_lua_expr(expr: str, assignments: dict[str, str]) -> str:
    text = expr.strip()
    seen: set[str] = set()
    while re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text) and text in assignments and text not in seen:
        seen.add(text)
        text = assignments[text].strip()
    return text[:240]


def _fight_result_fields_in_expr(expr: str) -> str:
    fields = [
        _FIGHT_RESULT_FIELD_FROM_EXPR.get(match.group(1), f"FightResultVO.{match.group(1)}")
        for match in re.finditer(r"\bresultVo\.([A-Za-z0-9_]+)", expr)
    ]
    if "temp_cur_damage" in expr:
        fields.extend(["FightResultVO.damage", "FightResultVO.mpAddDamage"])
    if "temp_cur_recover" in expr:
        fields.append("FightResultVO.recoverHp")
    return _join_unique(fields, limit=20)


def _fight_result_fields_in_line(line: str) -> str:
    fields: list[str] = []
    for match in re.finditer(r"\b(?:self|resultVo|msg|v)\.([A-Za-z0-9_]+)", line):
        field_name = match.group(1)
        if field_name in _FIGHT_RESULT_FIELD_FROM_EXPR:
            fields.append(_FIGHT_RESULT_FIELD_FROM_EXPR[field_name])
        elif field_name in {"results", "casterId", "skillId", "lockId", "delayTime"}:
            fields.append(f"SM_FightResult.{field_name}")
    if "readMessageList2List(self.results)" in line or "writeList(self.results)" in line:
        fields.append("SM_FightResult.results")
    return _join_unique(fields, limit=20)


def _fight_result_boundary_kind(file_name: str, function_name: str, line: str) -> tuple[str, str]:
    stripped = line.strip()
    if file_name == "FightResultVO.lua":
        if re.search(r"\bself\.[A-Za-z0-9_]+\s*=\s*self:read", stripped):
            return "packet_read_field", "FightResultVO 字段从服务端字节流反序列化进入客户端。"
        if re.search(r"\bself:write[A-Za-z0-9_]+\(self\.", stripped):
            return "packet_write_serializer", "生成协议类自带写出方法；本轮未发现客户端把 FightResultVO 作为请求发出。"
        if re.search(r"\bself\.[A-Za-z0-9_]+\s*=\s*0\b", stripped):
            return "packet_default", "对象初始化默认值，不是伤害计算来源。"
    if file_name.startswith("SM_FightResult"):
        if "class(SM_FightResult" in stripped:
            return "packet_inherit", "特殊战斗结果回包继承 SM_FightResult 基础结构。"
        if "readMessageList2List(self.results)" in stripped:
            return "packet_read_results", "回包读取 FightResultVO 列表。"
        if re.search(r"\bself\.[A-Za-z0-9_]+\s*=\s*self:read", stripped):
            return "packet_read_field", "SM_FightResult 顶层字段从服务端回包读取。"
        if re.search(r"\bself:write[A-Za-z0-9_]+\(self\.|writeList\(self\.results", stripped):
            return "packet_write_serializer", "生成协议类自带写出方法；SM_ 前缀表示服务端到客户端消息。"
    if file_name == "FightNetLogic.lua":
        if "F_Register" in stripped and "SM_FightResult" in stripped:
            return "net_register", "网络层注册 SM_FightResult 及其派生回包处理函数。"
        if function_name.startswith("SM_FightResult") and stripped.startswith("function "):
            return "net_handler_decl", "网络层回包处理入口。"
        if "SetSM_FightResult4RunTimeSkill" in stripped:
            return "net_dispatch_to_actor", "按 casterId / 派生实体定位 SkillActor，再转交运行中技能。"
    if file_name in {"SkillActor.lua", "UserSkillActor.lua", "TalismanSkillActor.lua", "FunnelSkillActor.lua"}:
        if function_name == "SetSM_FightResult4RunTimeSkill" and stripped.startswith("function "):
            return "actor_handler_decl", "SkillActor 接收 FightNetLogic 转交的战斗结果。"
        if "SetSM_FightResult(msg)" in stripped:
            return "actor_dispatch_to_skill", "SkillActor 把回包交给运行中技能或被动/法宝技能实例。"
    if file_name == "SkillBase.lua":
        if "msg.results" in stripped:
            return "skillbase_iterate_results", "技能实例遍历服务端 FightResultVO 列表。"
        if "resultVo." in stripped:
            return "skillbase_consume_field", "技能实例消费 FightResultVO 字段，生成表现层 HurtData/受击表现。"
    return "reference", "FightResult 相关普通引用。"


def _build_fight_result_boundary_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    search_root = root / "by_source" / "lscripts"
    if not search_root.is_dir():
        return rows
    for path in sorted(search_root.glob("**/text_assets/*.lua")):
        file_name = path.name
        if file_name not in _FIGHT_RESULT_BOUNDARY_FILE_NAMES:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        lines = text.splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            if file_name in {"FightResultVO.lua", "SM_FightResult.lua"}:
                interesting = any(
                    token in line
                    for token in (
                        "self:read",
                        "self:write",
                        "self.results",
                        "self.damage",
                        "self.recoverHp",
                        "self.fightEffect",
                    )
                )
            else:
                interesting = any(term in line for term in _FIGHT_RESULT_BOUNDARY_TERMS)
            if not interesting:
                continue
            function_name = _function_for_line(spans, line_no)
            kind, note = _fight_result_boundary_kind(file_name, function_name, line)
            rows.append(
                {
                    "source_file": _relative_to_root(path, root),
                    "file_name": file_name,
                    "function_name": function_name,
                    "line": line_no,
                    "boundary_kind": kind,
                    "field_refs": _fight_result_fields_in_line(line),
                    "direction": "server_to_client" if kind.startswith(("packet_read", "net_", "actor_", "skillbase_")) else "",
                    "note": note,
                    "code": line.strip()[:300],
                }
            )
    rows.sort(key=lambda row: (str(row["source_file"]), int(row["line"] or 0), str(row["boundary_kind"])))
    return rows


def _hurt_data_setdata_note(param_name: str, resolved_expr: str) -> str:
    if "percent" in resolved_expr or "tmpPercent" in resolved_expr:
        return "按 timeline hurt_event 百分比分摊。"
    if "temp_cur_damage" in resolved_expr or "temp_cur_recover" in resolved_expr:
        return "按 targetId 在本次技能结果内累计，用于 HP 事件表现。"
    if param_name in {"recoverMp_num", "reducedMp_num", "shieldAbsorb_num"} and resolved_expr == "0":
        return "当前 SkillBase 普通 FightResult 路径固定传 0。"
    if param_name == "damage_num" and "useFakeInjury" in resolved_expr:
        return "可能被 useFakeInjury 分支替换为客户端假伤显示。"
    return ""


def _build_fight_result_to_hurt_data_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_candidates = [
        ("SkillBase.lua", _find_lua_text_asset(root, "SkillBase.lua", must_contain="SetSM_FightResult")),
        ("HurtFrameVo.lua", _find_lua_text_asset(root, "HurtFrameVo.lua", must_contain="SeparateHurtData")),
    ]
    for file_name, source_path in source_candidates:
        if not source_path:
            continue
        lines = source_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        spans = _function_spans(lines)
        for span in spans:
            function_name = str(span["name"])
            function_lines = lines[int(span["start"]) - 1 : int(span["end"])]
            for offset, line in enumerate(function_lines):
                if "SetData(" not in line:
                    continue
                call_text, end_offset = _collect_lua_call(function_lines, offset, "SetData")
                args = _extract_call_args(call_text, "SetData")
                assignments = _lua_assignments_before(function_lines, offset)
                arg_count = len(args)
                expected_arg_count = len(_HURT_DATA_SETDATA_PARAMS)
                for arg_index, (param_name, param_role, semantics) in enumerate(_HURT_DATA_SETDATA_PARAMS):
                    arg_expr = args[arg_index] if arg_index < len(args) else ""
                    resolved_expr = _resolve_lua_expr(arg_expr, assignments)
                    arg_status = "ok" if arg_index < arg_count else "missing_nil"
                    transform_note = _hurt_data_setdata_note(param_name, resolved_expr)
                    if arg_status == "missing_nil":
                        transform_note = _join_unique(
                            [transform_note, "该调用未传此参数，Lua 运行时为 nil，HurtData:SetData 内部按默认值处理。"],
                            limit=5,
                        )
                    elif arg_count != expected_arg_count:
                        transform_note = _join_unique(
                            [
                                transform_note,
                                f"该调用实际传 {arg_count} 个参数，少于 SetData 当前签名 {expected_arg_count} 个参数。",
                            ],
                            limit=5,
                        )
                    rows.append(
                        {
                            "source_file": _relative_to_root(source_path, root),
                            "function_name": function_name,
                            "line": int(span["start"]) + offset,
                            "line_end": int(span["start"]) + end_offset,
                            "call_kind": "skillbase_fight_result_to_hurt_data"
                            if file_name == "SkillBase.lua"
                            else "hurt_frame_multi_hit_split",
                            "call_arg_count": arg_count,
                            "expected_arg_count": expected_arg_count,
                            "arg_status": arg_status,
                            "param_index": arg_index + 1,
                            "hurt_data_param": param_name,
                            "hurt_data_field": _HURT_DATA_INTERNAL_FIELDS.get(param_name, ""),
                            "param_role": param_role,
                            "arg_expr": arg_expr,
                            "resolved_expr": resolved_expr,
                            "fight_result_fields": _fight_result_fields_in_expr(resolved_expr or arg_expr),
                            "semantics": semantics,
                            "transform_note": transform_note,
                            "code": call_text[:300],
                        }
                    )
    rows.sort(
        key=lambda row: (
            str(row["source_file"]),
            str(row["function_name"]),
            int(row["line"] or 0),
            int(row["param_index"] or 0),
        )
    )
    return rows


def _hp_side_path_for_packet(packet_name: str) -> str:
    if packet_name == "SM_UnitHpUpdate":
        return "direct_unit_hp_update"
    if packet_name == "SM_UnitMpUpdate":
        return "direct_unit_mp_update"
    if packet_name in {"SM_BuffChangeHpAndMp", "BuffResultVO"}:
        return "buff_change_hp_mp"
    return ""


def _hp_side_path_field_semantics(packet_name: str, field_name: str) -> str:
    semantics = {
        ("SM_UnitHpUpdate", "id"): "被更新血量的目标实体 id，后续传给 EntityFightView:UpdateHpChange 的 targetId。",
        ("SM_UnitHpUpdate", "casterId"): "触发本次直接血量变化的实体 id，FightNetLogic 用它找到 EntityFightView。",
        ("SM_UnitHpUpdate", "damage"): "服务端直接下发的扣血数值；不经过 SkillBase timeline 分段。",
        ("SM_UnitHpUpdate", "recoverHp"): "服务端直接下发的回血数值；不经过 SkillBase timeline 分段。",
        ("SM_UnitHpUpdate", "fightEffect"): "直接血量变化的 FightCastEffect 位标记。",
        ("SM_UnitHpUpdate", "mpDamageAbsorb"): "直接血量变化携带的法力/特殊伤害吸收数值。",
        ("SM_UnitHpUpdate", "shieldAbsorb"): "直接血量变化携带的护盾吸收数值。",
        ("SM_UnitMpUpdate", "id"): "蓝量变化的目标实体 id，后续传给 UserView:UpdateMpChange 的 targetId。",
        ("SM_UnitMpUpdate", "recoverMp"): "服务端直接下发的回蓝/灵力恢复数值。",
        ("SM_UnitMpUpdate", "changeMp"): "服务端直接下发的扣蓝/灵力减少数值，客户端函数参数名为 reducedMp。",
        ("SM_BuffChangeHpAndMp", "resultVOs"): "BuffResultVO 列表；每个元素是一条 Buff/持续伤害或恢复结果。",
        ("BuffResultVO", "ownerId"): "Buff 归属实体 id；BuffMgr 用它定位 EntityFightView。",
        ("BuffResultVO", "casterId"): "Buff 结果的施法/来源实体 id。",
        ("BuffResultVO", "targetId"): "Buff 结果的目标实体 id。",
        ("BuffResultVO", "damage"): "Buff 结果真实扣血数值；进入 HurtData.total_damage。",
        ("BuffResultVO", "damageView"): "Buff 结果显示扣血数值；进入 HurtData.damage_num。",
        ("BuffResultVO", "recoverHp"): "Buff 结果回血数值。",
        ("BuffResultVO", "recoverMp"): "Buff 结果回蓝/灵力恢复数值。",
        ("BuffResultVO", "fightEffect"): "Buff 结果的 FightCastEffect 位标记。",
    }
    return semantics.get((packet_name, field_name), "")


def _hp_side_path_fields_in_expr(path_kind: str, expr: str) -> str:
    fields: list[str] = []
    text = expr or ""
    for match in re.finditer(r"\bbuffResultVO\.([A-Za-z0-9_]+)", text):
        fields.append(_BUFF_RESULT_FIELD_FROM_EXPR.get(match.group(1), f"BuffResultVO.{match.group(1)}"))
    if path_kind == "direct_unit_hp_update":
        if "self.Entity.V_ID" in text:
            fields.append("SM_UnitHpUpdate.casterId")
        for token, source in _UNIT_HP_PARAM_SOURCES.items():
            if re.search(rf"\b{re.escape(token)}\b", text):
                fields.append(source)
    if path_kind == "direct_unit_mp_update":
        if "self.Entity.V_ID" in text:
            fields.append("UserView.self")
        for token, source in _UNIT_MP_PARAM_SOURCES.items():
            if re.search(rf"\b{re.escape(token)}\b", text):
                fields.append(source)
    if "msg.resultVOs" in text:
        fields.append("SM_BuffChangeHpAndMp.resultVOs")
    for match in re.finditer(r"\bmsg\.([A-Za-z0-9_]+)", text):
        field_name = match.group(1)
        if field_name in {"id", "casterId", "damage", "recoverHp", "fightEffect", "mpDamageAbsorb", "shieldAbsorb"}:
            fields.append(f"SM_UnitHpUpdate.{field_name}")
        if field_name in {"recoverMp", "changeMp"}:
            fields.append(f"SM_UnitMpUpdate.{field_name}")
    return _join_unique(fields, limit=20)


def _hp_side_path_line_kind(file_name: str, function_name: str, line: str) -> tuple[str, str, str]:
    stripped = line.strip()
    if file_name == "FightNetLogic.lua" and "SM_UnitHpUpdate" in stripped:
        if "F_Register" in stripped:
            return "direct_unit_hp_update", "net_register", "网络层注册直接血量更新回包。"
        if function_name == "SM_UnitHpUpdateFun" and stripped.startswith("function "):
            return "direct_unit_hp_update", "net_handler_decl", "网络层直接血量更新处理入口。"
    if file_name == "FightNetLogic.lua" and "UpdateHpChange" in stripped:
        return "direct_unit_hp_update", "net_dispatch_to_entity", "按 casterId 找到 EntityFightView，再把直接血量变化字段传给 UpdateHpChange。"
    if file_name == "FightNetLogic.lua" and "SM_UnitMpUpdate" in stripped:
        if "F_Register" in stripped:
            return "direct_unit_mp_update", "net_register", "网络层注册直接蓝量更新回包。"
        if function_name == "SM_UnitMpUpdateFun" and stripped.startswith("function "):
            return "direct_unit_mp_update", "net_handler_decl", "网络层直接蓝量更新处理入口。"
    if file_name == "FightNetLogic.lua" and "UpdateMpChange" in stripped:
        return "direct_unit_mp_update", "net_dispatch_to_user_view", "把直接蓝量变化字段传给 UserView:UpdateMpChange。"
    if file_name == "BuffNetLogic.lua" and "SM_BuffChangeHpAndMp" in stripped:
        if "F_Register" in stripped:
            return "buff_change_hp_mp", "net_register", "网络层注册 Buff 血蓝变化回包。"
        if function_name == "SM_BuffChangeHpAndMpFunc" and stripped.startswith("function "):
            return "buff_change_hp_mp", "net_handler_decl", "网络层 Buff 血蓝变化处理入口。"
    if file_name == "BuffNetLogic.lua" and "UpdateBuffResult" in stripped:
        return "buff_change_hp_mp", "net_dispatch_to_buff_mgr", "把 BuffResultVO 列表交给 BuffMgr。"
    if file_name == "BuffMgr.lua" and function_name == "UpdateBuffResult":
        if stripped.startswith("function "):
            return "buff_change_hp_mp", "manager_handler_decl", "BuffMgr 接收 BuffResultVO 列表。"
        if "AddBuffResult" in stripped:
            return "buff_change_hp_mp", "manager_dispatch_to_entity", "按 BuffResultVO.ownerId 找到 EntityFightView，再执行 AddBuffResult。"
        if "buffResultVO.ownerId" in stripped:
            return "buff_change_hp_mp", "manager_owner_lookup", "使用 BuffResultVO.ownerId 定位 Buff 所属实体。"
    if file_name == "EntityFightView.lua" and function_name == "UpdateHpChange":
        if stripped.startswith("function "):
            return "direct_unit_hp_update", "entity_handler_decl", "EntityFightView 接收直接血量更新字段。"
    if file_name == "EntityFightView.lua" and function_name == "AddBuffResult":
        if stripped.startswith("function "):
            return "buff_change_hp_mp", "entity_handler_decl", "EntityFightView 接收单条 BuffResultVO。"
    if file_name == "UserView.lua" and function_name == "UpdateMpChange":
        if stripped.startswith("function "):
            return "direct_unit_mp_update", "entity_handler_decl", "UserView 接收直接蓝量更新字段。"
    return "", "", ""


def _hp_side_path_setdata_note(path_kind: str, param_name: str, resolved_expr: str) -> str:
    if path_kind == "direct_unit_hp_update":
        if param_name in {"damage_num", "total_damage"}:
            return "来自 SM_UnitHpUpdate.damage；这是直接血量更新，不经过 SkillBase timeline 百分比分段。"
        if param_name in {"recoverHp_num", "total_recover"}:
            return "来自 SM_UnitHpUpdate.recoverHp；这是直接血量更新，不经过 SkillBase timeline 百分比分段。"
        if param_name == "mpDamageAbsorb_num":
            return "来自 SM_UnitHpUpdate.mpDamageAbsorb。"
        if param_name == "shieldAbsorb_num":
            return "来自 SM_UnitHpUpdate.shieldAbsorb。"
    if path_kind == "buff_change_hp_mp":
        if param_name == "damage_num":
            return "来自 BuffResultVO.damageView；Buff/持续伤害走独立回包，不经过 SkillBase timeline 百分比分段。"
        if param_name == "total_damage":
            return "来自 BuffResultVO.damage；作为真实扣血总值传入 HurtData。"
        if param_name in {"recoverHp_num", "total_recover"}:
            return "来自 BuffResultVO.recoverHp。"
        if param_name == "recoverMp_num":
            return "来自 BuffResultVO.recoverMp。"
    if path_kind == "direct_unit_mp_update":
        if param_name == "recoverMp_num":
            return "来自 SM_UnitMpUpdate.recoverMp；直接蓝量变化不经过 SkillBase timeline 百分比分段。"
        if param_name == "reducedMp_num":
            return "来自 SM_UnitMpUpdate.changeMp；客户端函数参数名为 reducedMp。"
    if resolved_expr == "0":
        return "该旁路固定传 0。"
    return ""


def _build_hp_update_side_path_rows(
    root: Path,
    all_fields_by_packet_name: dict[str, list[dict[str, str]]],
    all_packet_by_name: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for packet_name in sorted(_HP_SIDE_PATH_PACKET_NAMES):
        packet = all_packet_by_name.get(packet_name, {})
        path_kind = _hp_side_path_for_packet(packet_name)
        for field in all_fields_by_packet_name.get(packet_name, []):
            field_name = field.get("field_name") or ""
            rows.append(
                {
                    "path_kind": path_kind,
                    "row_kind": "packet_field",
                    "source_file": packet.get("relative_path") or field.get("relative_path") or "",
                    "file_name": packet.get("file") or field.get("file") or "",
                    "function_name": "reading",
                    "line": field.get("line") or "",
                    "line_end": field.get("line") or "",
                    "packet_or_vo": packet_name,
                    "field_refs": f"{packet_name}.{field_name}" if field_name else "",
                    "call_arg_count": "",
                    "expected_arg_count": "",
                    "arg_status": "",
                    "param_index": "",
                    "hurt_data_param": "",
                    "hurt_data_field": "",
                    "arg_expr": "",
                    "resolved_expr": "",
                    "semantics": _hp_side_path_field_semantics(packet_name, field_name),
                    "note": "协议字段定义；字段值由服务端回包 reading() 进入客户端。",
                    "code": f"{field_name}:{field.get('read_method') or ''}"
                    + (f"<{field.get('type_hint')}>" if field.get("type_hint") else ""),
                }
            )

    source_candidates = [
        _find_lua_text_asset(root, "FightNetLogic.lua", must_contain="SM_UnitHpUpdateFun"),
        _find_lua_text_asset(root, "BuffNetLogic.lua", must_contain="SM_BuffChangeHpAndMpFunc"),
        _find_lua_text_asset(root, "BuffMgr.lua", must_contain="UpdateBuffResult"),
        _find_lua_text_asset(root, "EntityFightView.lua", must_contain="UpdateHpChange"),
        _find_lua_text_asset(root, "UserView.lua", must_contain="UpdateMpChange"),
    ]
    for source_path in [path for path in source_candidates if path]:
        lines = source_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            path_kind, row_kind, note = _hp_side_path_line_kind(source_path.name, function_name, line)
            if not row_kind:
                continue
            rows.append(
                {
                    "path_kind": path_kind,
                    "row_kind": row_kind,
                    "source_file": _relative_to_root(source_path, root),
                    "file_name": source_path.name,
                    "function_name": function_name,
                    "line": line_no,
                    "line_end": line_no,
                    "packet_or_vo": "SM_UnitHpUpdate"
                    if path_kind == "direct_unit_hp_update"
                    else "SM_UnitMpUpdate"
                    if path_kind == "direct_unit_mp_update"
                    else "SM_BuffChangeHpAndMp / BuffResultVO",
                    "field_refs": _hp_side_path_fields_in_expr(path_kind, line),
                    "call_arg_count": "",
                    "expected_arg_count": "",
                    "arg_status": "",
                    "param_index": "",
                    "hurt_data_param": "",
                    "hurt_data_field": "",
                    "arg_expr": "",
                    "resolved_expr": "",
                    "semantics": note,
                    "note": "",
                    "code": line.strip()[:300],
                }
            )

        for span in spans:
            function_name = str(span["name"])
            if function_name not in {"UpdateHpChange", "UpdateMpChange", "AddBuffResult"}:
                continue
            if function_name == "UpdateHpChange":
                path_kind = "direct_unit_hp_update"
            elif function_name == "UpdateMpChange":
                path_kind = "direct_unit_mp_update"
            else:
                path_kind = "buff_change_hp_mp"
            function_lines = lines[int(span["start"]) - 1 : int(span["end"])]
            for offset, line in enumerate(function_lines):
                if "SetData(" not in line:
                    continue
                call_text, end_offset = _collect_lua_call(function_lines, offset, "SetData")
                args = _extract_call_args(call_text, "SetData")
                assignments = _lua_assignments_before(function_lines, offset)
                arg_count = len(args)
                expected_arg_count = len(_HURT_DATA_SETDATA_PARAMS)
                for arg_index, (param_name, param_role, semantics) in enumerate(_HURT_DATA_SETDATA_PARAMS):
                    arg_expr = args[arg_index] if arg_index < len(args) else ""
                    resolved_expr = _resolve_lua_expr(arg_expr, assignments)
                    arg_status = "ok" if arg_index < arg_count else "missing_nil"
                    note = _hp_side_path_setdata_note(path_kind, param_name, resolved_expr)
                    if arg_status == "missing_nil":
                        note = _join_unique(
                            [note, "该调用未传此参数，Lua 运行时为 nil，HurtData:SetData 内部按默认值处理。"],
                            limit=5,
                        )
                    elif arg_count != expected_arg_count:
                        note = _join_unique(
                            [note, f"该调用实际传 {arg_count} 个参数，少于 SetData 当前签名 {expected_arg_count} 个参数。"],
                            limit=5,
                        )
                    rows.append(
                        {
                            "path_kind": path_kind,
                            "row_kind": "hurtdata_setdata_param",
                            "source_file": _relative_to_root(source_path, root),
                            "file_name": source_path.name,
                            "function_name": function_name,
                            "line": int(span["start"]) + offset,
                            "line_end": int(span["start"]) + end_offset,
                            "packet_or_vo": "SM_UnitHpUpdate"
                            if path_kind == "direct_unit_hp_update"
                            else "SM_UnitMpUpdate"
                            if path_kind == "direct_unit_mp_update"
                            else "BuffResultVO",
                            "field_refs": _hp_side_path_fields_in_expr(path_kind, resolved_expr or arg_expr),
                            "call_arg_count": arg_count,
                            "expected_arg_count": expected_arg_count,
                            "arg_status": arg_status,
                            "param_index": arg_index + 1,
                            "hurt_data_param": param_name,
                            "hurt_data_field": _HURT_DATA_INTERNAL_FIELDS.get(param_name, ""),
                            "arg_expr": arg_expr,
                            "resolved_expr": resolved_expr,
                            "semantics": semantics,
                            "note": note,
                            "code": call_text[:300],
                        }
                    )
    rows.sort(
        key=lambda row: (
            str(row["path_kind"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
            int(row["param_index"] or 0),
        )
    )
    return rows


def _fight_state_sync_path_kind(packet_name: str) -> str:
    if packet_name == "SM_HpChange":
        return "hp_property_sync"
    if packet_name == "SM_MpChange":
        return "mp_property_sync"
    if packet_name == "SM_FixDamage":
        return "fixed_damage_hp_event_smoothing"
    if packet_name in {"SM_ShadowHpChange", "SM_ShadowInfo"}:
        return "shadow_hp_property_sync"
    if packet_name == "SM_UnitMaxHpUpdate":
        return "max_hp_property_sync"
    return ""


def _fight_state_sync_field_semantics(packet_name: str, field_name: str) -> str:
    semantics = {
        ("SM_HpChange", "changeHpMap"): "实体 id -> 当前 HP；FightNetLogic 写入 LuaEntityPropertyType.HP。",
        ("SM_HpChange", "changeVirtualHpMap"): "实体 id -> 虚拟血量；FightNetLogic 写入 LuaEntityPropertyType.VIRTUAL。",
        ("SM_MpChange", "changeMpMap"): "实体 id -> 当前 MP；FightNetLogic 写入 LuaEntityPropertyType.MP。",
        ("SM_FixDamage", "unitId"): "固定伤害 Boss 实体 id。",
        ("SM_FixDamage", "hp"): "服务端最终 HP；平滑事件结束后写回 LuaEntityPropertyType.HP。",
        ("SM_FixDamage", "totalDamage"): "固定伤害平滑展示使用的总伤害。",
        ("SM_FixDamage", "maxDamage"): "固定伤害平滑展示使用的最大单段伤害。",
        ("SM_FixDamage", "attackTime"): "固定伤害平滑展示估算分段次数的攻击时长。",
        ("SM_ShadowHpChange", "changeHpMap"): "实体 id -> 影子血量；写入 SHADOWHP 特殊属性。",
        ("SM_ShadowHpChange", "recoverHpLock"): "回放/影子血量恢复锁；交给 FightModel 缓存。",
        ("SM_ShadowInfo", "currHp"): "用户影子当前 HP；写入 SHADOWHP 特殊属性。",
        ("SM_ShadowInfo", "maxHp"): "用户影子最大 HP；写入 SHADOWMAXHP 特殊属性。",
        ("SM_UnitMaxHpUpdate", "id"): "最大血量更新目标实体 id。",
        ("SM_UnitMaxHpUpdate", "maxHp"): "新的最大 HP；写入 LuaEntityPropertyType.MAXHP。",
        ("SM_UnitMaxHpUpdate", "currHp"): "新的当前 HP；写入 LuaEntityPropertyType.HP。",
    }
    return semantics.get((packet_name, field_name), "")


def _fight_state_sync_packet_for_line(function_name: str, line: str) -> str:
    if function_name in _FIGHT_STATE_SYNC_HANDLERS:
        return _FIGHT_STATE_SYNC_HANDLERS[function_name]
    for packet_name in _FIGHT_STATE_SYNC_PACKET_NAMES:
        if packet_name in line:
            return packet_name
    return ""


def _fight_state_sync_line_kind(function_name: str, line: str) -> tuple[str, str]:
    stripped = line.strip()
    packet_name = _fight_state_sync_packet_for_line(function_name, stripped)
    if not packet_name:
        return "", ""
    if "F_Register" in stripped:
        return "net_register", "网络层注册状态同步回包。"
    if stripped.startswith("function ") and function_name in _FIGHT_STATE_SYNC_HANDLERS:
        return "net_handler_decl", "状态同步回包处理入口。"
    if "SetProperty(LuaEntityPropertyType.HP" in stripped:
        return "set_property_hp", "直接写入实体 HP 属性；不创建 HurtData。"
    if "SetProperty(LuaEntityPropertyType.MAXHP" in stripped:
        return "set_property_max_hp", "直接写入实体最大 HP 属性；不创建 HurtData。"
    if "SetProperty(LuaEntityPropertyType.MP" in stripped:
        return "set_property_mp", "直接写入实体 MP 属性；不创建 HurtData。"
    if "SetProperty(LuaEntityPropertyType.VIRTUAL" in stripped:
        return "set_property_virtual_hp", "直接写入实体虚拟血量属性；不创建 HurtData。"
    if "SetSepcialProperty(GameDefine.Dic_SepcialPropertyKey.SHADOWHP" in stripped:
        return "set_shadow_hp", "写入影子 HP 特殊属性；不创建 HurtData。"
    if "SetSepcialProperty(GameDefine.Dic_SepcialPropertyKey.SHADOWMAXHP" in stripped:
        return "set_shadow_max_hp", "写入影子最大 HP 特殊属性；不创建 HurtData。"
    if "RaiseEvent(CommonEventType.HURT_HP_CHANGE" in stripped:
        return "raise_hurt_hp_change_event", "触发血条变化事件用于平滑表现；没有创建 HurtData 飘字。"
    if "SetReplayRecoverHpLock" in stripped:
        return "shadow_recover_lock_cache", "缓存影子血量恢复锁。"
    if "LastRealHp" in stripped:
        return "fixed_damage_cache", "固定伤害平滑展示临时缓存真实 HP。"
    return "", ""


def _fight_state_sync_field_refs(packet_name: str, line: str) -> str:
    refs: list[str] = []
    for match in re.finditer(r"\bmsg\.([A-Za-z0-9_]+)", line):
        refs.append(f"{packet_name}.{match.group(1)}")
    for field_name in (
        "changeHpMap",
        "changeVirtualHpMap",
        "changeMpMap",
        "recoverHpLock",
        "hp",
        "maxHp",
        "currHp",
        "totalDamage",
        "maxDamage",
        "attackTime",
        "unitId",
    ):
        if re.search(rf"\b{re.escape(field_name)}\b", line):
            refs.append(f"{packet_name}.{field_name}")
    return _join_unique(refs, limit=20)


def _build_fight_state_sync_rows(
    root: Path,
    all_fields_by_packet_name: dict[str, list[dict[str, str]]],
    all_packet_by_name: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for packet_name in sorted(_FIGHT_STATE_SYNC_PACKET_NAMES):
        packet = all_packet_by_name.get(packet_name, {})
        for field in all_fields_by_packet_name.get(packet_name, []):
            field_name = field.get("field_name") or ""
            rows.append(
                {
                    "path_kind": _fight_state_sync_path_kind(packet_name),
                    "row_kind": "packet_field",
                    "packet_name": packet_name,
                    "source_file": packet.get("relative_path") or field.get("relative_path") or "",
                    "file_name": packet.get("file") or field.get("file") or "",
                    "function_name": "reading",
                    "line": field.get("line") or "",
                    "field_refs": f"{packet_name}.{field_name}" if field_name else "",
                    "state_target": "",
                    "creates_hurt_data": "no",
                    "shows_blood_tip": "no",
                    "semantics": _fight_state_sync_field_semantics(packet_name, field_name),
                    "note": "协议字段定义；字段值由服务端回包 reading() 进入客户端。",
                    "code": f"{field_name}:{field.get('read_method') or ''}"
                    + (f"<{field.get('type_hint')}>" if field.get("type_hint") else ""),
                }
            )

    fight_net_logic = _find_lua_text_asset(root, "FightNetLogic.lua", must_contain="SM_HpChangeFun")
    if fight_net_logic:
        lines = fight_net_logic.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            packet_name = _fight_state_sync_packet_for_line(function_name, line)
            if not packet_name:
                continue
            row_kind, note = _fight_state_sync_line_kind(function_name, line)
            if not row_kind:
                continue
            state_target = ""
            if "LuaEntityPropertyType.HP" in line:
                state_target = "LuaEntityPropertyType.HP"
            elif "LuaEntityPropertyType.MAXHP" in line:
                state_target = "LuaEntityPropertyType.MAXHP"
            elif "LuaEntityPropertyType.MP" in line:
                state_target = "LuaEntityPropertyType.MP"
            elif "LuaEntityPropertyType.VIRTUAL" in line:
                state_target = "LuaEntityPropertyType.VIRTUAL"
            elif "SHADOWMAXHP" in line:
                state_target = "SHADOWMAXHP"
            elif "SHADOWHP" in line:
                state_target = "SHADOWHP"
            elif "HURT_HP_CHANGE" in line:
                state_target = "CommonEventType.HURT_HP_CHANGE"
            rows.append(
                {
                    "path_kind": _fight_state_sync_path_kind(packet_name),
                    "row_kind": row_kind,
                    "packet_name": packet_name,
                    "source_file": _relative_to_root(fight_net_logic, root),
                    "file_name": fight_net_logic.name,
                    "function_name": function_name,
                    "line": line_no,
                    "field_refs": _fight_state_sync_field_refs(packet_name, line),
                    "state_target": state_target,
                    "creates_hurt_data": "no",
                    "shows_blood_tip": "no",
                    "semantics": note,
                    "note": "状态同步/血条事件路径；与 HurtData 飘字路径分开看。",
                    "code": line.strip()[:300],
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["path_kind"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    return rows


def _fight_request_flow_phase(packet_name: str) -> str:
    if packet_name.startswith("CM_"):
        return "client_request_intent"
    if packet_name.startswith("SM_"):
        return "server_cast_broadcast"
    if packet_name.endswith("VO"):
        return "cast_intent_value_object"
    return ""


def _fight_request_field_semantics(packet_name: str, field_name: str) -> str:
    semantics = {
        ("CM_FightByTarget", "casterId"): "客户端声明施法实体 id。不是伤害数值。",
        ("CM_FightByTarget", "skillId"): "客户端声明释放的技能 id。不是伤害数值。",
        ("CM_FightByTarget", "targetId"): "客户端声明单个目标实体 id。不是伤害数值。",
        ("CM_FightByTarget", "movePos"): "可选移动目标位置；用于技能移动/位移意图。",
        ("CM_FightByTarget", "currPos"): "打断移动施法时附带的当前格子位置。",
        ("CM_FightByTargets", "casterId"): "客户端声明施法实体 id。不是伤害数值。",
        ("CM_FightByTargets", "skillId"): "客户端声明释放的技能 id。不是伤害数值。",
        ("CM_FightByTargets", "selectTargetIds"): "客户端本地范围判定出的候选目标列表；服务端仍可校验。",
        ("CM_FightByTargets", "selectDir"): "客户端声明释放方向。",
        ("CM_FightByTargets", "selectPos"): "客户端声明释放位置。",
        ("CM_FightByTargets", "movePos"): "可选移动目标位置；用于技能移动/位移意图。",
        ("CM_FightByTargets", "currPos"): "打断移动施法时附带的当前格子位置。",
        ("CM_FightByDir", "casterId"): "客户端声明施法实体 id。不是伤害数值。",
        ("CM_FightByDir", "skillId"): "客户端声明释放的技能 id。不是伤害数值。",
        ("CM_FightByDir", "selectDir"): "客户端声明释放方向。",
        ("CM_FightByDir", "movePos"): "可选移动目标位置；用于技能移动/位移意图。",
        ("CM_FightByDir", "currPos"): "打断移动施法时附带的当前格子位置。",
        ("CM_FightByPosition", "casterId"): "客户端声明施法实体 id。不是伤害数值。",
        ("CM_FightByPosition", "skillId"): "客户端声明释放的技能 id。不是伤害数值。",
        ("CM_FightByPosition", "selectPos"): "客户端声明释放位置。",
        ("CM_FightByPosition", "movePos"): "可选移动目标位置；用于技能移动/位移意图。",
        ("CM_FightByPosition", "currPos"): "打断移动施法时附带的当前格子位置。",
        ("CM_FightFinishCharge", "casterId"): "客户端通知某实体完成蓄力。",
        ("CM_FightFinishCharge", "skillId"): "客户端通知完成蓄力的技能 id。",
        ("SM_FightCast", "id"): "服务端广播的释放事件 id。不是伤害数值。",
        ("SM_FightCast", "casterId"): "服务端确认的施法实体 id。",
        ("SM_FightCast", "skillId"): "服务端确认的释放技能 id。",
        ("SM_FightCast", "jie"): "服务端广播的技能阶数。",
        ("SM_FightCast", "star"): "服务端广播的技能星级。",
        ("SM_FightCast", "cdTime"): "服务端广播的冷却时间。",
        ("SM_FightCast", "attackPerSecond"): "服务端广播的攻击速度/频率参数。",
        ("SM_FightCast", "fightCastVO"): "服务端广播的释放意图 VO，嵌套目标/位置/方向。",
        ("SM_FightCast", "currPos"): "服务端确认的当前格子位置。",
        ("SM_FightCast", "castingSpeed"): "服务端广播的施法速度。",
        ("SM_FightCastTalisman", "talismanId"): "法宝释放广播的法宝 id。",
        ("SM_FightCastPet", "location"): "灵兽/宠物释放广播的位置槽位。",
        ("SM_FightCastPassive", "casterId"): "被动技能释放广播的施法实体 id。",
        ("SM_FightCastPassive", "skillId"): "被动技能释放广播的技能 id。",
        ("SM_FightCastPassive", "fightCastVO"): "被动技能释放广播的释放意图 VO。",
        ("SM_FightCastFunnel", "buffId"): "漏斗/召唤物释放广播的 buff/实体 id。",
        ("FightCastVO", "selectTargetId"): "服务端广播的单目标选择。",
        ("FightCastVO", "selectPos"): "服务端广播的位置选择。",
        ("FightCastVO", "selectDir"): "服务端广播的方向选择。",
        ("FightCastVO", "castType"): "服务端广播的释放类型。",
        ("FightCastMultiVO", "selectTargetIds"): "服务端广播的多目标选择列表。",
        ("FightCastMultiVO", "selectPoses"): "服务端广播的多位置选择列表。",
        ("FightCastMultiVO", "selectDir"): "服务端广播的方向选择。",
        ("FightCastMultiVO", "castType"): "服务端广播的释放类型。",
    }
    return semantics.get((packet_name, field_name), "")


def _fight_request_packet_from_line(function_name: str, line: str) -> str:
    if function_name in _FIGHT_REQUEST_FUNCTION_PACKETS:
        return _FIGHT_REQUEST_FUNCTION_PACKETS[function_name]
    if function_name in _FIGHT_CAST_HANDLERS:
        return _FIGHT_CAST_HANDLERS[function_name]
    for var_name, packet_name in _FIGHT_REQUEST_PACKET_VAR_MAP.items():
        if var_name in line:
            return packet_name
    for packet_name in sorted(_FIGHT_REQUEST_PACKET_NAMES | _FIGHT_CAST_BROADCAST_PACKET_NAMES):
        if packet_name in line:
            return packet_name
    return ""


def _fight_request_line_kind(function_name: str, line: str) -> tuple[str, str]:
    stripped = line.strip()
    if "_MessagePool.Inst_get():F_Register" in stripped and (
        "_CM_Fight" in stripped or "_SM_FightCast" in stripped
    ):
        return "net_register", "网络层注册请求包或释放广播包。"
    if stripped.startswith("function ") and function_name in _FIGHT_REQUEST_FUNCTION_PACKETS:
        return "request_builder_decl", "客户端构造并发送战斗释放请求的函数入口。"
    if stripped.startswith("function ") and function_name in _FIGHT_CAST_HANDLERS:
        return "cast_broadcast_handler_decl", "服务端释放广播的客户端处理入口。"
    if "ReleaseSkillExecute(" in stripped and function_name == "CM_FightBySkill":
        return "client_local_release_precheck", "发送请求前先在客户端做释放判定/表现预执行；这不是最终伤害计算。"
    if "_M.SendFightMessage(" in stripped:
        return "route_to_request_dispatch", "本地释放判定通过后进入按点选类型分发的请求发送函数。"
    if function_name == "SendFightMessage" and re.search(r"_M\.CM_FightBy(Targets|Target|Dir|Position)\(", stripped):
        return "request_route", "按技能 point_type / scope 分支选择目标、方向、位置或多目标请求。"
    if "GetMessageFromPools(_CM_Fight" in stripped:
        return "request_pool_get", "从消息池取出客户端请求对象。"
    if any(re.search(rf"\b{re.escape(var_name)}\.[A-Za-z0-9_]+", stripped) for var_name in _FIGHT_REQUEST_PACKET_VAR_MAP):
        if ":Clear()" in stripped:
            return "request_list_clear", "发送前清空复用消息对象里的目标列表。"
        if ":AddRange(" in stripped:
            return "request_list_write", "写入客户端本地选择出的目标列表。"
        return "request_field_write", "写入客户端请求字段。"
    if "F_SendMsg(" in stripped and any(var_name in stripped for var_name in _FIGHT_REQUEST_PACKET_VAR_MAP):
        return "request_send", "发送客户端战斗释放请求。"
    if function_name in _FIGHT_CAST_HANDLERS and any(
        token in stripped for token in ("msg.", "fightCastVO.", "EntityFightCast", "ReleaseSkillExecute", "ReleaseBeastSkill")
    ):
        if "EntityFightCast" in stripped or "ReleaseSkillExecute" in stripped or "ReleaseBeastSkill" in stripped:
            return "cast_broadcast_dispatch", "消费服务端释放广播，驱动客户端表现/本地技能实例。"
        return "cast_broadcast_field_use", "读取服务端释放广播中的目标/方向/位置等字段。"
    return "", ""


def _fight_request_line_field_refs(function_name: str, line: str) -> str:
    refs: list[str] = []
    for var_name, packet_name in _FIGHT_REQUEST_PACKET_VAR_MAP.items():
        if var_name not in line:
            continue
        for field_match in re.finditer(rf"\b{re.escape(var_name)}\.([A-Za-z0-9_]+)", line):
            refs.append(f"{packet_name}.{field_match.group(1)}")
    for packet_name, fields in _FIGHT_REQUEST_FUNCTION_FIELDS.items():
        if re.search(rf"_M\.{re.escape(packet_name)}\(", line):
            refs.extend(f"{packet_name}.{field}" for field in fields)
    packet_name = _fight_request_packet_from_line(function_name, line)
    if packet_name.startswith("SM_"):
        for match in re.finditer(r"\bmsg\.([A-Za-z0-9_]+)", line):
            refs.append(f"{packet_name}.{match.group(1)}")
    if "fightCastVO." in line:
        for match in re.finditer(r"\bfightCastVO\.([A-Za-z0-9_]+)", line):
            refs.append(f"FightCastVO.{match.group(1)}")
    return _join_unique(refs, limit=30)


def _has_damage_like_field(field_refs: str) -> str:
    if not field_refs:
        return "no"
    for item in re.split(r"[、,;\s]+", field_refs):
        field_name = item.rsplit(".", 1)[-1]
        if _FIGHT_DAMAGE_FIELD_RE.search(field_name):
            return "yes"
    return "no"


def _fight_request_fallback_phase(row_kind: str) -> str:
    if row_kind.startswith("request_") or row_kind in {"client_local_release_precheck", "route_to_request_dispatch"}:
        return "client_request_intent"
    return "server_cast_broadcast"


def _build_fight_request_intent_rows(
    root: Path,
    all_fields_by_packet_name: dict[str, list[dict[str, str]]],
    all_packet_by_name: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    packet_names = _FIGHT_REQUEST_PACKET_NAMES | _FIGHT_CAST_BROADCAST_PACKET_NAMES
    for packet_name in sorted(packet_names):
        packet = all_packet_by_name.get(packet_name, {})
        fields = all_fields_by_packet_name.get(packet_name, [])
        if not packet and not fields:
            continue
        if not fields:
            rows.append(
                {
                    "flow_phase": _fight_request_flow_phase(packet_name),
                    "row_kind": "packet_no_fields",
                    "packet_name": packet_name,
                    "direction": packet.get("direction") or _direction_for_packet_name(packet_name),
                    "source_file": packet.get("relative_path") or "",
                    "file_name": packet.get("file") or "",
                    "function_name": "reading",
                    "line": "",
                    "field_refs": "",
                    "has_damage_like_field": "no",
                    "intent_role": "no_payload",
                    "semantics": "协议类无独立字段；仅表示一个动作信号。",
                    "note": "字段签名来自 packet 索引。",
                    "code": "",
                }
            )
            continue
        for field in fields:
            field_name = field.get("field_name") or ""
            field_ref = f"{packet_name}.{field_name}" if field_name else ""
            rows.append(
                {
                    "flow_phase": _fight_request_flow_phase(packet_name),
                    "row_kind": "packet_field",
                    "packet_name": packet_name,
                    "direction": packet.get("direction") or field.get("direction") or _direction_for_packet_name(packet_name),
                    "source_file": packet.get("relative_path") or field.get("relative_path") or "",
                    "file_name": packet.get("file") or field.get("file") or "",
                    "function_name": "reading",
                    "line": field.get("line") or "",
                    "field_refs": field_ref,
                    "has_damage_like_field": _has_damage_like_field(field_ref),
                    "intent_role": "request_payload" if packet_name.startswith("CM_") else "cast_broadcast_payload",
                    "semantics": _fight_request_field_semantics(packet_name, field_name),
                    "note": "协议字段签名；CM_ 是客户端请求意图，SM_/VO 是服务端释放广播或其嵌套意图对象。",
                    "code": f"{field_name}:{field.get('read_method') or ''}"
                    + (f"<{field.get('type_hint')}>" if field.get("type_hint") else ""),
                }
            )

    fight_net_logic = _find_lua_text_asset(root, "FightNetLogic.lua", must_contain="CM_FightBySkill")
    if fight_net_logic:
        lines = fight_net_logic.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            row_kind, note = _fight_request_line_kind(function_name, line)
            if not row_kind:
                continue
            packet_name = _fight_request_packet_from_line(function_name, line)
            field_refs = _fight_request_line_field_refs(function_name, line)
            rows.append(
                {
                    "flow_phase": _fight_request_flow_phase(packet_name)
                    or _fight_request_fallback_phase(row_kind),
                    "row_kind": row_kind,
                    "packet_name": packet_name,
                    "direction": _direction_for_packet_name(packet_name) if packet_name else "",
                    "source_file": _relative_to_root(fight_net_logic, root),
                    "file_name": fight_net_logic.name,
                    "function_name": function_name,
                    "line": line_no,
                    "field_refs": field_refs,
                    "has_damage_like_field": _has_damage_like_field(field_refs),
                    "intent_role": "request_sender" if row_kind.startswith("request_") else "cast_broadcast_consumer",
                    "semantics": note,
                    "note": "本表只记录字段/发送/广播证据，不构造或修改任何请求。",
                    "code": line.strip()[:300],
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["flow_phase"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    return rows


def _fight_cast_flow_stage(file_name: str, function_name: str) -> str:
    if file_name == "EntityView.lua":
        return "entity_view_set_state"
    if file_name == "StateMachine.lua":
        return "state_machine_transition"
    if file_name == "StateSkill.lua":
        return "state_skill_enter"
    if file_name == "StateBase.lua":
        return "state_change_rule"
    if file_name == "FightNetLogic.lua":
        return "net_broadcast_dispatch"
    if file_name == "FightMgr.lua":
        if function_name == "ReleaseSkillExecute":
            return "local_skill_state_enter"
        if function_name in {"EntityFightCast", "EntityCastPassiveSkill"}:
            return "cast_broadcast_entry"
        if function_name in {"OnEntityCast", "EntityReleaseSkill", "OnUserCast", "ReleaseMagicSkill"}:
            return "cast_route_to_skill_actor"
    if file_name == "SkillActor.lua":
        return "skill_actor_start"
    return "other"


def _fight_cast_flow_line_kind(file_name: str, function_name: str, line: str) -> tuple[str, str]:
    stripped = line.strip()
    if file_name == "FightNetLogic.lua" and stripped.startswith("function ") and function_name in _FIGHT_CAST_HANDLERS:
        return "net_handler_decl", "服务端释放广播的网络入口。"
    if file_name == "FightNetLogic.lua":
        if "EntityFightCast(msg)" in stripped:
            return "dispatch_normal_cast", "普通释放广播交给 FightMgr:EntityFightCast。"
        if "EntityCastPassiveSkill(msg)" in stripped:
            return "dispatch_passive_cast", "被动释放广播交给 FightMgr:EntityCastPassiveSkill。"
        if "ReleaseSkillExecute(" in stripped:
            return "dispatch_special_cast", "法宝/召唤物等特殊释放广播直接触发本地释放执行。"
        if "ReleaseBeastSkill(" in stripped:
            return "dispatch_pet_cast", "灵兽释放广播交给 BeastView。"
    if file_name == "FightMgr.lua":
        if stripped.startswith("function ") and function_name in {
            "EntityFightCast",
            "EntityCastPassiveSkill",
            "OnEntityCast",
            "EntityReleaseSkill",
            "OnUserCast",
            "ReleaseMagicSkill",
            "ReleaseSkillExecute",
        }:
            return "fightmgr_handler_decl", "FightMgr 释放广播/本地技能状态处理函数。"
        if "local fightCastVO=msg.fightCastVO" in stripped:
            return "decode_fight_cast_vo", "取出服务端广播里的释放意图 VO。"
        if "attackSpeed=msg.attackPerSecond*0.0001" in stripped:
            return "decode_attack_speed", "把服务端广播的 attackPerSecond 转为本地播放速度。"
        if "GetEntityFightInBattleView" in stripped and ("msg.casterId" in stripped or "fightCastVO.selectTargetId" in stripped):
            return "resolve_cast_entities", "按服务端广播的 caster/target id 定位本地实体视图。"
        if "self:OnEntityCast(" in stripped:
            return "route_other_entity_cast", "非本机用户释放，进入 OnEntityCast 路径。"
        if "self:OnUserCast(" in stripped:
            return "route_user_cast", "本机用户释放，进入 OnUserCast 路径并处理 CD/速度/位移。"
        if "self:EntityReleaseSkill(" in stripped:
            return "route_entity_release_skill", "OnEntityCast 判定后进入实体释放技能。"
        if "self:ReleaseMagicSkill(" in stripped:
            return "route_magic_skill", "尝试按神通/主动技能路径释放。"
        if "self:ReleaseSkillExecute(" in stripped and function_name != "ReleaseSkill4Preview":
            return "route_release_execute", "最终进入 ReleaseSkillExecute，让实体进入技能状态。"
        if "ToCDStart(" in stripped:
            return "user_cd_start", "本机用户收到释放确认后启动技能 CD。"
        if "ResetAttackPlayableData" in stripped:
            return "reset_runtime_playable", "根据服务端确认的 movePos 修正本地 playable 位移轨道。"
        if "_TempSkillParam." in stripped:
            return "fill_temp_skill_param", "把技能 id、目标、方向、位置、速度、阶星等写入临时释放参数。"
        if "SetState(StateType.Skill" in stripped:
            return "enter_skill_state", "实体进入技能状态；后续由 SkillActor/SkillBase 播放表现。"
        if "SetTargetId(" in stripped:
            return "set_entity_target", "把服务端确认/本地整理出的目标 id 写到实体。"
    if file_name == "SkillActor.lua":
        if stripped.startswith("function ") and function_name in {"ReleaseMagicSkill", "ReleasePassiveSkill"}:
            return "skillactor_handler_decl", "SkillActor 接收释放请求并准备 tParam。"
        if "local tParam={" in stripped:
            return "build_skill_tparam", "构造传入 SkillBase:Start 的方向、位置、移动、阶星参数。"
        if "skillInfo:Start(" in stripped:
            return "skill_start", "启动技能实例；后续 timeline 和 FightResult 回包都会落在该技能实例上。"
        if "magicSkills[skillId]" in stripped or "passiveSkills[skillId]" in stripped:
            return "cache_runtime_skill", "缓存当前运行中的神通/被动技能实例。"
    if file_name == "EntityView.lua":
        if stripped.startswith("function ") and function_name in {"SetState", "ForceSetState"}:
            return "entityview_set_state_decl", "实体视图状态切换入口。"
        if "StateMachine:ChangeState" in stripped:
            return "entityview_change_state", "把状态切换交给 StateMachine。"
        if "curState~=StateType.Skill" in stripped:
            return "allow_skill_restart", "普通相同状态会拒绝重复切换，但 Skill 状态允许重新进入。"
        if "StateType.Skill" in stripped and "IsInSkillState" in function_name:
            return "skill_state_predicate", "把 Skill/SkillMove/SkillMoveStop 视为技能中。"
    if file_name == "StateMachine.lua":
        if "self.m_dicState[StateType.Skill]=StateSkill.new()" in stripped:
            return "register_state_skill", "状态机注册 StateType.Skill 到 StateSkill。"
        if stripped.startswith("function ") and function_name == "ChangeState":
            return "state_machine_change_decl", "状态机统一切换入口。"
        if "CanChangeTo(nextState)" in stripped:
            return "state_machine_check_change", "进入下个状态前询问当前状态是否允许切换。"
        if ":Exit(tParam,nextState)" in stripped:
            return "state_exit_previous", "退出旧状态。"
        if ":Enter(tParam)" in stripped:
            return "state_enter_next", "进入新状态，并把 tParam 传给 StateSkill:Enter。"
    if file_name == "StateSkill.lua":
        if stripped.startswith("function ") and function_name == "Enter":
            return "state_skill_enter_decl", "StateType.Skill 的 Enter 入口。"
        if re.match(r"local\s+(skillId|targetId|pos|dir|movePos|attack_speed|skill_move_speed|stage|star|makeId)\s*=", stripped):
            return "state_skill_decode_param", "从 FightMgr 填入的 tParam 取出技能释放参数。"
        if "local tParam={" in stripped:
            return "state_skill_build_tparam", "构造传给 SkillActor:ReleaseSkill 的二级 tParam。"
        if "skillActor:GetSkill(skillId)" in stripped:
            return "state_skill_get_skill", "用 skillId 取本地 SkillBase/技能实例。"
        if "EntityMgr.Inst_get():GetEntityFightInBattleView(targetId)" in stripped:
            return "state_skill_resolve_target", "按目标 id 找本地目标视图。"
        if "LookAt(" in stripped or "RotateTowards(" in stripped or "LookAtPos(" in stripped:
            return "state_skill_face_target", "按目标/方向/位置修正朝向。"
        if "skillActor:ReleaseSkill(skillId,targetId,tParam)" in stripped:
            return "state_skill_release", "StateSkill 把技能真正交给 SkillActor:ReleaseSkill。"
        if "RaiseEvent(FightEventType.ENTER_STATESKILL_USER)" in stripped:
            return "state_skill_enter_user_event", "本机用户进入技能状态事件。"
        if "skillActor:StopSkill()" in stripped:
            return "state_skill_exit_stop", "退出技能状态时停止当前技能。"
        if "skillActor:IsCurSkillCanMove()" in stripped or "skillActor:IsInAfterCastingState()" in stripped:
            return "state_skill_can_change", "技能状态迁移条件由当前技能是否可移动/后摇状态决定。"
    if file_name == "StateBase.lua":
        if "(state==StateType.Skill or state==StateType.SkillNavigation)" in stripped:
            return "base_allow_skill_change", "基础状态规则允许非死亡状态进入 Skill/SkillNavigation。"
    return "", ""


def _fight_cast_flow_field_refs(line: str) -> str:
    refs: list[str] = []
    for match in re.finditer(r"\bmsg\.([A-Za-z0-9_]+)", line):
        field_name = match.group(1)
        refs.append(f"SM_FightCast.{field_name}")
    for match in re.finditer(r"\bfightCastVO\.([A-Za-z0-9_]+)", line):
        refs.append(f"FightCastVO.{match.group(1)}")
    for match in re.finditer(r"\b_TempSkillParam\.([A-Za-z0-9_]+)", line):
        refs.append(f"TempSkillParam.{match.group(1)}")
    for name in ("skillId", "targetId", "selectDir", "selectPos", "movePos", "attackSpeed", "skillMoveSpeed", "jie", "star"):
        if re.search(rf"\b{re.escape(name)}\b", line):
            refs.append(name)
    return _join_unique(refs, limit=30)


def _build_fight_cast_broadcast_flow_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    search_root = root / "by_source" / "lscripts"
    if not search_root.is_dir():
        return rows
    for path in sorted(search_root.glob("**/text_assets/*.lua")):
        if path.name not in _FIGHT_CAST_FLOW_FILE_NAMES:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if (
            "FightCast" not in text
            and "ReleaseMagicSkill" not in text
            and "ReleaseSkill(" not in text
            and "ReleaseSkillExecute" not in text
            and "StateType.Skill" not in text
            and "StateSkill" not in text
        ):
            continue
        lines = text.splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            row_kind, semantics = _fight_cast_flow_line_kind(path.name, function_name, line)
            if not row_kind:
                continue
            rows.append(
                {
                    "flow_stage": _fight_cast_flow_stage(path.name, function_name),
                    "row_kind": row_kind,
                    "source_file": _relative_to_root(path, root),
                    "file_name": path.name,
                    "function_name": function_name,
                    "line": line_no,
                    "field_refs": _fight_cast_flow_field_refs(line),
                    "semantics": semantics,
                    "note": "释放广播链路只驱动本地表现/技能状态，伤害数值仍看后续 SM_FightResult 或 HP/MP 旁路回包。",
                    "code": line.strip()[:300],
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    return rows


def _skill_instance_lifecycle_stage(file_name: str, function_name: str) -> str:
    if file_name == "SkillActor.lua":
        if function_name == "SetSM_FightResult4RunTimeSkill":
            return "actor_result_dispatch"
        if function_name in {"ReleaseSkill", "ReleaseMagicSkill", "ReleasePassiveSkill"}:
            return "actor_release_runtime"
        if function_name in {"StopSkill", "OnStartSkill", "OnStopCast", "OnStopSkill"}:
            return "actor_lifecycle_callbacks"
    if file_name == "SkillBase.lua":
        if function_name in {"_init_", "LuaSKillBase", "InitConfig"}:
            return "skillbase_init"
        if function_name == "UpdateTimelineData":
            return "timeline_config_load"
        if function_name == "Start":
            return "skillbase_start"
        if function_name in {"PlaySkillTimeline", "PlaySkillTargetTimeline4Other"}:
            return "timeline_playback"
        if function_name == "SetSM_FightResult":
            return "fight_result_consume"
        if function_name == "Update4Hurt":
            return "hurt_event_tick"
        if function_name in {"Update", "OnCaseBefore", "OnCaseAfter", "OnCaseCasting"}:
            return "skill_cast_timer"
        if function_name in {"Stop", "StopSkillTimeline", "ClearHurtFrameVO"}:
            return "skill_stop_cleanup"
    if file_name == "HurtEvent.lua":
        return "timeline_hurt_event"
    if file_name == "HurtFrameVo.lua":
        return "hurt_frame_schedule"
    if file_name in {"Bullet.lua", "BulletMgr.lua"}:
        return "trajectory_hurt_route"
    if file_name == "HurtData.lua":
        return "hurtdata_display"
    return "other"


def _skill_instance_lifecycle_line_kind(file_name: str, function_name: str, line: str) -> tuple[str, str]:
    stripped = line.strip()
    if file_name == "SkillActor.lua":
        if stripped.startswith("function ") and function_name == "SetSM_FightResult4RunTimeSkill":
            return "actor_result_dispatch_decl", "SkillActor 接收 FightResult 回包并按 skillId 找到技能实例。"
        if "skillInfo:SetSM_FightResult(msg)" in stripped:
            return "actor_result_to_skillbase", "把整包 FightResult 交给对应 SkillBase 实例消费。"
        if stripped.startswith("function ") and function_name == "ReleaseSkill":
            return "actor_release_skill_decl", "SkillActor 普通释放入口，负责当前运行技能切换和启动 SkillBase。"
        if stripped.startswith("function ") and function_name in {"ReleaseMagicSkill", "ReleasePassiveSkill"}:
            return "actor_special_release_decl", "SkillActor 主动/被动释放包装入口。"
        if "local runtimeSkill=self:GetRuntimeSkill()" in stripped:
            return "actor_get_runtime_skill", "取得当前运行中的技能实例，决定是否需要先停止。"
        if "self:StopSkill(false,skillId)" in stripped:
            return "actor_stop_previous_skill", "新技能启动前停止旧运行实例。"
        if "self:SetRuntimeSkillId(0)" in stripped or "self:SetRuntimeSkillId(skillId)" in stripped:
            return "actor_set_runtime_skill_id", "维护当前运行技能 id。"
        if "runtimeSkill=self:GetSkill(skillId)" in stripped or "skillActor:GetSkill(skillId)" in stripped:
            return "actor_get_skill_instance", "按 skillId 取得本地 SkillBase 实例。"
        if "runtimeSkill:Start(" in stripped or "skillInfo:Start(" in stripped:
            return "actor_start_skillbase", "调用 SkillBase:Start，后续 timeline 播放和 FightResult 消费都落在该实例。"
        if stripped.startswith("function ") and function_name in {"OnStartSkill", "OnStopCast", "OnStopSkill"}:
            return "actor_lifecycle_callback_decl", "SkillBase 回调 SkillActor，用于移动、CD、状态收尾。"
        if stripped.startswith("function ") and function_name == "StopSkill":
            return "actor_stop_skill_decl", "停止当前运行技能实例。"
        if "runtimeSkill:Stop(" in stripped:
            return "actor_stop_skillbase", "把停止动作下发给 SkillBase:Stop。"
    if file_name == "SkillBase.lua":
        if stripped.startswith("function ") and function_name == "_init_":
            return "skillbase_init_decl", "SkillBase 初始化入口。"
        if "CreateHurtFrameVO" in stripped:
            return "skillbase_create_hurt_frame", "创建普通伤害帧缓存和弹道缓存。"
        if stripped.startswith("function ") and function_name == "UpdateTimelineData":
            return "timeline_update_decl", "按技能 id、阶/星重新加载 timeline 运行数据。"
        if "GetTimelineIdBySkillId" in stripped:
            return "timeline_resolve_id", "用 skillId、性别、阶/星选择 timeline id。"
        if "GetSkillInfo(self.timeline_id)" in stripped:
            return "timeline_load_config", "读取 timeline 配置对象。"
        if "self.Cfg_keyFrames" in stripped or "self.Cfg_CastBefore" in stripped or "self.Cfg_CastAfter" in stripped:
            return "timeline_keyframe_load", "读取起手、后摇、蓄力等关键帧。"
        if "self.Cfg_Hurts" in stripped or "q_hurt_events" in stripped:
            return "timeline_hurt_events_load", "读取 q_hurt_events，后续用作伤害显示分段时点。"
        if "GetSkillExParams(self.timeline_id)" in stripped:
            return "timeline_ex_params_load", "读取 SkillExParams，判断是否使用真实分段伤害。"
        if "self.real_section_dmg" in stripped:
            return "timeline_real_section_flag", "设置 real_section_dmg；有 channel 时按服务端回包逐段消费。"
        if stripped.startswith("function ") and function_name == "Start":
            return "skillbase_start_decl", "SkillBase:Start 是技能实例启动入口。"
        if "self:UpdateTimelineData(" in stripped and function_name == "Start":
            return "start_refresh_timeline", "根据 tParam.stage/star 刷新 timeline。"
        if "not self.timeline_id" in stripped or "self:Stop()" in stripped and function_name == "Start":
            return "start_no_timeline_stop", "缺 timeline 时直接走停止/完成回调。"
        if "self:SetStageState(SkillDefine.Stage.Before)" in stripped:
            return "start_set_before_stage", "进入技能 Before 阶段。"
        if "fun_skillStart(self.skillId)" in stripped:
            return "start_callback_skill_start", "触发 SkillActor 的开始回调。"
        if "self.isRunning=true" in stripped:
            return "start_mark_running", "标记技能实例运行中。"
        if "self:PlaySkillTimeline(" in stripped:
            return "start_play_timeline", "启动攻击 timeline 播放。"
        if "self.updateStart=true" in stripped or "self.updateStart=false" in stripped:
            return "start_update_gate", "决定 Update/伤害帧计时何时开始推进。"
        if stripped.startswith("function ") and function_name == "PlaySkillTimeline":
            return "timeline_play_decl", "播放攻击 timeline 的入口。"
        if "self.Cfg_CastTotal=totalTime*0.001" in stripped:
            return "timeline_total_time", "把 timeline 总时长从毫秒转成秒。"
        if "GetTimeLineAllData(self.timeline_id)" in stripped:
            return "timeline_load_all_data", "读取攻击/受击轨道等 timeline 合并数据。"
        if "PlayBattleSkillSuffer" in stripped:
            return "timeline_play_suffer", "播放目标受击 timeline。"
        if "PlayBattleSkill(" in stripped:
            return "timeline_play_attack", "播放施法者攻击 timeline。"
        if stripped.startswith("function ") and function_name == "SetSM_FightResult":
            return "fight_result_consume_decl", "SkillBase 消费 SM_FightResult 的入口。"
        if "self.temp_cur_damage={}" in stripped or "self.temp_cur_recover={}" in stripped:
            return "fight_result_reset_accumulator", "重置本次回包的目标累计伤害/恢复缓存。"
        if "self.hurt_index" in stripped:
            return "fight_result_section_index", "real_section_dmg 时每次回包推进一个伤害段。"
        if "ipairs(self.Cfg_Hurts)" in stripped:
            return "fight_result_iter_hurt_events", "按 timeline q_hurt_events 遍历伤害分段。"
        if "percent=hurt_event[2]" in stripped:
            return "fight_result_hurt_percent", "读取当前分段百分比。"
        if "Cipairs(msg.results)" in stripped or "local resultVo=v" in stripped:
            return "fight_result_iter_results", "遍历服务端下发的 FightResultVO 列表。"
        if "CreateHurtData" in stripped:
            return "fight_result_create_hurtdata", "为每个目标创建表现层 HurtData。"
        if "hurtData:SetData(" in stripped:
            return "fight_result_to_hurtdata", "把分摊后的字段写入 HurtData:SetData。"
        if any(name in stripped for name in ("damage_num", "damage_view", "recover_num", "damage_reflect", "mpDamage_num", "mpDamage_view", "mpDamageAbsorb_num")):
            return "fight_result_split_values", "把服务端结果数值按 hurt_event 百分比分摊到当前表现段。"
        if "self.temp_cur_damage" in stripped or "self.temp_cur_recover" in stripped:
            return "fight_result_accumulate_target", "累计同目标已展示的真实伤害/恢复。"
        if "self.hurtFrameVo:Add4HurtDataListDic" in stripped:
            return "fight_result_schedule_hurt_frame", "按 hurt_event 时间点把 HurtData 放入普通伤害帧缓存。"
        if "trajectoryCachedHurtVo" in stripped or "bullet:AddHurtData" in stripped or "FindBulletByHurtIndex" in stripped:
            return "fight_result_schedule_trajectory", "弹道命中类伤害先挂到弹道 hurt index。"
        if "SKILL_MAIN_TARGET" in stripped or "PlaySkillTargetTimeline4Other" in stripped:
            return "fight_result_play_main_target_suffer", "主目标标记会触发目标受击 timeline。"
        if "FightTrigger(" in stripped:
            return "fight_result_trigger_event", "通知战斗触发器本次 caster/target 产生结果。"
        if "ResetAttackPlayableData" in stripped:
            return "fight_result_reset_playable", "按 lockId 修正朝向/目标 playable 数据。"
        if stripped.startswith("function ") and function_name == "Update4Hurt":
            return "hurt_update_decl", "timeline HurtEvent 调用这里检查并执行已排队 HurtData。"
        if "CheckMultiHurt" in stripped:
            return "hurt_update_multi_check", "多跳/多段表现会把已排队 HurtData 再拆成多次执行。"
        if "CheckHurt" in stripped:
            return "hurt_update_check", "普通伤害帧到点后执行 HurtData。"
        if stripped.startswith("function ") and function_name == "Update":
            return "skill_update_timer_decl", "技能实例的 Update 推进起手、后摇和停止。"
        if "self:OnCaseBefore()" in stripped or "self:OnCaseAfter()" in stripped or "self:Stop(true)" in stripped:
            return "skill_update_stage_tick", "按 elapseTimer 推进阶段或自然结束。"
        if stripped.startswith("function ") and function_name == "Stop":
            return "skill_stop_decl", "技能实例停止入口。"
        if "self:StopSkillTimeline()" in stripped or "AbortSkillByUseSkillPlayerId" in stripped or "AbortSkillSufferByUseSkillPlayerId" in stripped:
            return "skill_stop_abort_timeline", "停止攻击/受击 timeline。"
        if "self:ClearHurtFrameVO()" in stripped or "Clear4HurtDataListDic" in stripped:
            return "skill_stop_clear_hurt_frame", "清理还未执行的伤害帧缓存。"
    if file_name == "HurtEvent.lua":
        if stripped.startswith("function ") and function_name == "OnStart":
            return "timeline_hurt_event_start_decl", "timeline 伤害事件开始入口。"
        if "GetHurtDataDetail" in stripped:
            return "timeline_query_hurtdata_detail", "先读取当前时点是否命中、是否有伤害/恢复数据。"
        if "self.skillObj:Update4Hurt" in stripped:
            return "timeline_fire_skill_hurt", "timeline 命中帧回调 SkillBase:Update4Hurt。"
        if "IsInSkillCastArea" in stripped:
            return "timeline_range_check", "没有服务端 miss 标记时，再按本地范围判断是否播放命中表现。"
    if file_name == "HurtFrameVo.lua":
        if stripped.startswith("function ") and function_name == "Add4HurtDataListDic":
            return "hurt_frame_store_decl", "保存某个时间点或弹道 index 对应的 HurtData 列表。"
        if "LuaDic_AddOrSetItem" in stripped:
            return "hurt_frame_store_list", "把 HurtData 列表写入伤害帧字典。"
        if stripped.startswith("function ") and function_name == "CheckHurt":
            return "hurt_frame_check_decl", "普通伤害帧检查入口。"
        if "ExecuteHurtDataList" in stripped:
            return "hurt_frame_execute_due_list", "到点后执行 HurtData 列表。"
        if stripped.startswith("function ") and function_name == "ExecuteHurtDataList":
            return "hurt_frame_execute_decl", "执行并回收 HurtData。"
        if "hurtData:Execute()" in stripped:
            return "hurt_frame_execute_hurtdata", "HurtData 最终进入飘字/血条表现。"
        if stripped.startswith("function ") and function_name in {"CheckMultiHurt", "SeparateHurtData"}:
            return "hurt_frame_multi_split_decl", "多跳表现的二次拆分入口。"
        if "separateHurtData:SetData" in stripped:
            return "hurt_frame_secondary_setdata", "把已有 HurtData 均分成多次表现。"
    if file_name == "BulletMgr.lua":
        if "GetBulletHurtVo" in stripped:
            return "trajectory_fetch_cached_hurt", "创建弹道时取出 SkillBase 暂存的弹道伤害列表。"
        if "bullet:AddHurtData" in stripped:
            return "trajectory_attach_hurtdata", "把弹道伤害列表挂到 Bullet。"
    if file_name == "Bullet.lua":
        if stripped.startswith("function ") and function_name == "AddHurtData":
            return "trajectory_add_hurtdata_decl", "弹道接收某个 hurt index 的 HurtData 列表。"
        if "CheckBulletHit" in stripped:
            return "trajectory_hit_check", "弹道命中目标时触发伤害事件。"
        if "self.hurt_event:OnStart" in stripped or "self.hurtFrameVo:CheckHurt" in stripped:
            return "trajectory_fire_hurt", "弹道命中后执行对应 HurtEvent 和 HurtFrameVo。"
    if file_name == "HurtData.lua":
        if stripped.startswith("function ") and function_name == "SetData":
            return "hurtdata_setdata_decl", "HurtData 字段落点，承接 SkillBase 或旁路更新传入的表现数值。"
        if re.match(r"self\.(damage_num|recoverHp_num|mp_damage_num|reflect_num|total_damage|total_recover|mpDamageAbsorb_num|shieldAbsorb_num|fightEffect|casterId|targetId)=", stripped):
            return "hurtdata_store_field", "保存表现层伤害、恢复、效果标志和目标字段。"
        if stripped.startswith("function ") and function_name == "Execute":
            return "hurtdata_execute_decl", "执行 HurtData，分发到具体场景表现。"
        if stripped.startswith("function ") and function_name == "NormalExecute":
            return "hurtdata_normal_execute_decl", "普通场景飘字/血条表现入口。"
        if "ShowBloodTips" in stripped or "AddTipsNum" in stripped:
            return "hurtdata_show_blood_tip", "展示飘字或进入 HurtTips 聚合。"
    return "", ""


def _skill_instance_lifecycle_refs(line: str) -> str:
    refs: list[str] = []
    for match in re.finditer(r"\bmsg\.([A-Za-z0-9_]+)", line):
        refs.append(f"SM_FightResult.{match.group(1)}")
    for match in re.finditer(r"\bresultVo\.([A-Za-z0-9_]+)", line):
        refs.append(f"FightResultVO.{match.group(1)}")
    for match in re.finditer(r"\btParam\.([A-Za-z0-9_]+)", line):
        refs.append(f"tParam.{match.group(1)}")
    for match in re.finditer(r"\bhurtData\.([A-Za-z0-9_]+)", line):
        refs.append(f"HurtData.{match.group(1)}")
    for match in re.finditer(r"\bhurt_event\[(\d+)\]", line):
        index_name = {"1": "time_ms", "2": "percent", "3": "isTrajectoryHit", "4": "trajectoryIndex"}.get(
            match.group(1),
            match.group(1),
        )
        refs.append(f"q_hurt_events[{match.group(1)}:{index_name}]")
    for name in (
        "skillId",
        "targetId",
        "timeline_id",
        "Cfg_Hurts",
        "Cfg_keyFrames",
        "Cfg_CastTotal",
        "real_section_dmg",
        "hurt_index",
        "damage_num",
        "damage_view",
        "recover_num",
        "mpDamage_num",
        "mpDamage_view",
        "mpDamageAbsorb_num",
    ):
        if re.search(rf"\b{re.escape(name)}\b", line):
            refs.append(name)
    return _join_unique(refs, limit=36)


def _build_skill_instance_lifecycle_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    search_root = root / "by_source" / "lscripts"
    if not search_root.is_dir():
        return rows
    terms = (
        "SetSM_FightResult",
        "PlaySkillTimeline",
        "Update4Hurt",
        "HurtFrameVo",
        "HurtData",
        "ReleaseSkill",
        "GetBulletHurtVo",
        "AddHurtData",
        "CheckHurt",
        "ShowBloodTips",
    )
    for path in sorted(search_root.glob("**/text_assets/*.lua")):
        if path.name not in _SKILL_INSTANCE_LIFECYCLE_FILE_NAMES:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if not any(term in text for term in terms):
            continue
        lines = text.splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            row_kind, semantics = _skill_instance_lifecycle_line_kind(path.name, function_name, line)
            if not row_kind:
                continue
            rows.append(
                {
                    "flow_stage": _skill_instance_lifecycle_stage(path.name, function_name),
                    "row_kind": row_kind,
                    "source_file": _relative_to_root(path, root),
                    "file_name": path.name,
                    "function_name": function_name,
                    "line": line_no,
                    "field_refs": _skill_instance_lifecycle_refs(line),
                    "semantics": semantics,
                    "note": "这张表描述本地技能实例、timeline 和 HurtData 表现生命周期；最终数值总量仍来自服务端 FightResult/HP/MP 回包。",
                    "code": line.strip()[:300],
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    return rows


def _fight_authority_boundary_refs(function_name: str, line: str) -> str:
    return _join_unique(
        [
            ref
            for ref in (
                _fight_request_line_field_refs(function_name, line),
                _fight_cast_flow_field_refs(line),
                _skill_instance_lifecycle_refs(line),
            )
            if ref
        ],
        limit=40,
    )


def _build_fight_authority_boundary_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    search_root = root / "by_source" / "lscripts"
    if not search_root.is_dir():
        return rows
    paths_by_name: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(search_root.glob("**/text_assets/*.lua")):
        paths_by_name[path.name].append(path)

    for spec in _FIGHT_AUTHORITY_BOUNDARY_PATTERNS:
        hit_count = 0
        function_names = set(spec.get("function_names") or [])
        for path in paths_by_name.get(str(spec["file_name"]), []):
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            lines = text.splitlines()
            spans = _function_spans(lines)
            for line_no, line in enumerate(lines, start=1):
                function_name = _function_for_line(spans, line_no)
                if function_names and function_name not in function_names:
                    continue
                if not spec["pattern"].search(line):
                    continue
                rows.append(
                    {
                        "phase_order": spec["phase_order"],
                        "phase_id": spec["phase_id"],
                        "authority": spec["authority"],
                        "packet_or_event": spec["packet_or_event"],
                        "source_file": _relative_to_root(path, root),
                        "file_name": path.name,
                        "function_name": function_name,
                        "line": line_no,
                        "field_refs": _fight_authority_boundary_refs(function_name, line),
                        "local_effect": spec["local_effect"],
                        "server_authority_note": spec["server_authority_note"],
                        "code": line.strip()[:300],
                    }
                )
                hit_count += 1
                if hit_count >= int(spec.get("max_hits") or 1):
                    break
            if hit_count >= int(spec.get("max_hits") or 1):
                break
    rows.sort(
        key=lambda row: (
            int(row["phase_order"] or 0),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["phase_id"]),
        )
    )
    return rows


_FIGHT_SIDE_CHANNEL_PACKET_NAMES = {
    "SM_QiChange",
    "SM_FightFail",
    "SM_FightInterrupt",
    "SM_RestrictStatus",
    "SM_FightTimeLine",
    "SkillEffectVO",
    "MoveSkillEffectVO",
    "SM_SkillEffect",
    "SM_UpdateCd",
    "SM_UpdateSelect",
    "SM_SyncUnit",
    "SM_TestAdjustDirect",
    "SM_TestShape",
    "SM_FightChannel",
    "SM_UnitState",
}

_FIGHT_SIDE_CHANNEL_FUNCTION_PACKETS = {
    "SM_QiChangeFun": "SM_QiChange",
    "SM_FightFailFun": "SM_FightFail",
    "SM_FightInterruptFun": "SM_FightInterrupt",
    "SM_RestrictStatusFun": "SM_RestrictStatus",
    "SM_FightTimeLineFun": "SM_FightTimeLine",
    "SM_SkillEffectFun": "SM_SkillEffect",
    "SM_UpdateCdFun": "SM_UpdateCd",
    "SM_UpdateSelect": "SM_UpdateSelect",
    "SM_UpdateSelectFun": "SM_UpdateSelect",
    "SM_SyncUnitFun": "SM_SyncUnit",
    "SM_TestAdjustDirectFun": "SM_TestAdjustDirect",
    "SM_TestShapeFun": "SM_TestShape",
    "SM_FightChannelFun": "SM_FightChannel",
    "SM_UnitStateFun": "SM_UnitState",
}

_FIGHT_SIDE_CHANNEL_FIELD_SEMANTICS = {
    ("SM_QiChange", "changeQiMap"): "实体 id -> 当前气值；FightNetLogic 写入 LuaEntityPropertyType.QI。",
    ("SM_FightFail", "casterId"): "释放失败的施法实体。",
    ("SM_FightFail", "skillId"): "释放失败的技能 id。",
    ("SM_FightFail", "currPos"): "失败后服务端校正当前位置。",
    ("SM_FightInterrupt", "casterId"): "被打断的施法实体。",
    ("SM_FightInterrupt", "skillId"): "被打断的技能 id。",
    ("SM_FightInterrupt", "targetPos"): "打断后服务端指定的校正位置。",
    ("SM_RestrictStatus", "unitId"): "状态限制目标实体 id。",
    ("SM_RestrictStatus", "restrictCode"): "服务端下发的限制状态码。",
    ("SM_FightTimeLine", "casterId"): "服务端指定播放 timeline 的施法者。",
    ("SM_FightTimeLine", "targetId"): "服务端指定播放 timeline 的目标。",
    ("SM_FightTimeLine", "timeLine"): "直接指定的 timeline 配置 id。",
    ("SM_FightTimeLine", "timeLineType"): "timeline 播放类型；客户端按类型分成 buff element 或 battle skill 路径。",
    ("SM_FightTimeLine", "skillId"): "关联的技能 id。",
    ("SM_FightTimeLine", "buffId"): "关联的 buff id。",
    ("SkillEffectVO", ""): "技能效果 VO 基类，本身无字段；具体效果由派生 VO 承载。",
    ("MoveSkillEffectVO", "forceMoveVOs"): "强制位移效果列表；客户端按目标实体修正受击 playable。",
    ("SM_SkillEffect", "casterId"): "效果来源实体。",
    ("SM_SkillEffect", "skillId"): "效果所属技能。",
    ("SM_SkillEffect", "skillEffectVO"): "服务端下发的技能效果 VO，可指向 MoveSkillEffectVO 等派生结构。",
    ("SM_UpdateCd", "skill2cd"): "服务端下发的技能 id -> CD 时间。",
    ("SM_UpdateSelect", "id"): "选择状态变化目标实体。",
    ("SM_UpdateSelect", "canSelect"): "该实体是否可被选择。",
    ("SM_SyncUnit", "unitIds"): "需要同步/复活/刷新 CD 的单位集合。",
    ("SM_TestAdjustDirect", "pos"): "调试校正点；客户端转给 PresentationBridge。",
    ("SM_TestShape", "casterId"): "调试范围检测施法者。",
    ("SM_TestShape", "center"): "调试范围中心点。",
    ("SM_TestShape", "dir"): "调试范围方向。",
    ("SM_TestShape", "shapeType"): "调试范围形状类型。",
    ("SM_TestShape", "width"): "调试范围宽。",
    ("SM_TestShape", "height"): "调试范围高。",
    ("SM_TestShape", "angle"): "调试范围角度。",
    ("SM_TestShape", "toCheck"): "调试范围待检测目标。",
    ("SM_FightChannel", "casterId"): "持续施法/引导施法实体。",
    ("SM_FightChannel", "skillId"): "持续施法/引导技能 id。",
    ("SM_FightChannel", "channellingCount"): "第几段引导，用于修正指定轨道 index。",
    ("SM_FightChannel", "fightCastVO"): "引导中的释放参数，包含 movePos 等表现修正字段。",
    ("SM_UnitState", "id"): "单位状态同步目标实体。",
    ("SM_UnitState", "state"): "服务端单位状态值。",
}


def _fight_side_channel_packet_for_line(function_name: str, line: str) -> str:
    if function_name in _FIGHT_SIDE_CHANNEL_FUNCTION_PACKETS:
        return _FIGHT_SIDE_CHANNEL_FUNCTION_PACKETS[function_name]
    for packet_name in sorted(_FIGHT_SIDE_CHANNEL_PACKET_NAMES, key=len, reverse=True):
        if packet_name in line or f"_{packet_name}" in line:
            return packet_name
    return ""


def _fight_side_channel_group(packet_name: str, function_name: str = "") -> str:
    packet_name = packet_name or _FIGHT_SIDE_CHANNEL_FUNCTION_PACKETS.get(function_name, "")
    if packet_name in {"SM_FightFail", "SM_FightInterrupt"}:
        return "failure_interrupt"
    if packet_name in {"SM_RestrictStatus", "SM_UnitState"}:
        return "restrict_unit_state"
    if packet_name == "SM_FightTimeLine":
        return "timeline_playable"
    if packet_name in {"SkillEffectVO", "MoveSkillEffectVO", "SM_SkillEffect"}:
        return "skill_effect_force_move"
    if packet_name in {"SM_UpdateCd", "SM_UpdateSelect", "SM_SyncUnit"}:
        return "cd_select_sync"
    if packet_name in {"SM_TestAdjustDirect", "SM_TestShape"}:
        return "debug_probe"
    if packet_name == "SM_FightChannel":
        return "fight_channel"
    if packet_name == "SM_QiChange":
        return "qi_property_sync"
    return "other"


def _fight_side_channel_field_semantics(packet_name: str, field_name: str) -> str:
    return _FIGHT_SIDE_CHANNEL_FIELD_SEMANTICS.get((packet_name, field_name), "")


def _fight_side_channel_authority_note(row_kind: str) -> str:
    if row_kind in {"packet_field", "packet_no_fields"}:
        return "协议字段定义；字段值从服务端回包或 VO reading() 进入客户端。"
    if row_kind in {"net_register", "net_handler_decl"}:
        return "网络入口证据；说明客户端存在对应回包处理函数。"
    if row_kind in {
        "qi_property_sync",
        "restrict_add_code",
        "unit_state_set",
        "cd_refresh",
        "cd_start",
        "select_update",
        "sync_unit_revive_info",
        "sync_unit_refresh_cd",
    }:
        return "服务端状态/冷却/选择性同步；客户端按回包刷新本地状态。"
    if row_kind in {
        "skill_effect_reset_suffer_playable",
        "timeline_play_element",
        "timeline_play_attack",
        "timeline_play_suffer",
        "fight_channel_reset_attack_playable",
        "fight_channel_set_position",
    }:
        return "服务端驱动的表现修正；不等同于客户端计算最终伤害。"
    if row_kind in {"skill_failed_reset_position", "interrupt_stop_runtime", "interrupt_remove_end_action", "interrupt_reset_position"}:
        return "服务端失败/打断校正；用于回滚或终止本地释放表现。"
    if row_kind.startswith("test_"):
        return "调试/可视化通道；用于校正或范围展示。"
    return "side-channel 处理证据；需与 FightResult/HP/MP 数值通道分开看。"


def _fight_side_channel_line_kind(function_name: str, line: str) -> tuple[str, str]:
    stripped = line.strip()
    packet_name = _fight_side_channel_packet_for_line(function_name, stripped)
    if not packet_name:
        return "", ""
    if "F_Register" in stripped and f"_{packet_name}" in stripped:
        return "net_register", "网络层注册该 fight side-channel 回包。"
    if stripped.startswith("function ") and function_name in _FIGHT_SIDE_CHANNEL_FUNCTION_PACKETS:
        return "net_handler_decl", "fight side-channel 回包处理入口。"
    if function_name == "SM_QiChangeFun" and "SetProperty(LuaEntityPropertyType.QI" in stripped:
        return "qi_property_sync", "写入实体 QI 属性。"
    if function_name == "SM_FightFailFun":
        if "OnSkillFailed" in stripped:
            return "skill_failed_actor_callback", "通知本地 SkillActor 技能释放失败。"
        if "MapPositionReset" in stripped:
            return "skill_failed_reset_position", "按服务端 currPos 校正当前位置。"
        if "FightEventType.SKILL_FAILED" in stripped:
            return "skill_failed_raise_event", "触发技能失败事件。"
    if function_name == "SM_FightInterruptFun":
        if "StopSkill(true" in stripped:
            return "interrupt_stop_runtime", "打断当前 runtime skill。"
        if "RemoveSkillEndAction" in stripped:
            return "interrupt_remove_end_action", "移除技能结束动作。"
        if "CheckAutoFightReleaseQueue" in stripped:
            return "interrupt_check_auto_queue", "打断后检查自动战斗释放队列。"
        if "SetEntityPosition(targetPos" in stripped or "SetEntityPosition(pos" in stripped:
            return "interrupt_reset_position", "按服务端目标点校正实体位置。"
    if function_name == "SM_RestrictStatusFun" and "AddRestrictCode" in stripped:
        return "restrict_add_code", "给目标实体追加服务端限制状态码。"
    if function_name == "SM_FightTimeLineFun":
        if "PlayElement" in stripped:
            return "timeline_play_element", "按 buff element 播放表现。"
        if "PlayBattleSkillSuffer" in stripped:
            return "timeline_play_suffer", "播放受击 timeline。"
        if "PlayBattleSkill" in stripped:
            return "timeline_play_attack", "播放攻击 timeline。"
        if "CommonEventType.FIGHT_TIMELINE" in stripped:
            return "timeline_raise_event", "触发 fight timeline 事件。"
    if function_name == "SM_SkillEffectFun":
        if "forceMoveVOs" in stripped:
            return "skill_effect_force_move_list", "读取 MoveSkillEffectVO.forceMoveVOs 强制位移列表。"
        if "moveSkillVo.unitId" in stripped or "moveSkillVo.finalGrid" in stripped:
            return "skill_effect_force_move_field", "读取单个强制位移目标和最终格点。"
        if "in_special_hit" in stripped:
            return "skill_effect_mark_special_hit", "把目标标记为特殊受击。"
        if "ResetSufferPlayableData" in stripped:
            return "skill_effect_reset_suffer_playable", "按服务端最终位置修正受击 playable 位移轨。"
    if function_name == "SM_UpdateCdFun":
        if "RefreshCDTime" in stripped:
            return "cd_refresh", "刷新已在 CD 中的技能剩余时间。"
        if "ToCDStart" in stripped:
            return "cd_start", "启动技能 CD。"
    if function_name in {"SM_UpdateSelect", "SM_UpdateSelectFun"}:
        if "V_CanSelect" in stripped:
            return "select_update", "更新实体是否可选择。"
        if "CLICK_ENTITYVIEW" in stripped:
            return "select_clear_current", "不可选时清掉当前选择。"
    if function_name == "SM_SyncUnitFun":
        if "ReviveInfo" in stripped:
            return "sync_unit_revive_info", "同步复活/单位信息。"
        if "RefreshUserSkillCD" in stripped:
            return "sync_unit_refresh_cd", "同步后刷新用户技能 CD。"
    if function_name == "SM_TestAdjustDirectFun" and "TestAdjustDirect4Server" in stripped:
        return "test_adjust_direct", "把服务端调试校正点传给表现桥。"
    if function_name == "SM_TestShapeFun" and "ShowSkillDamageRange" in stripped:
        return "test_shape_show_range", "展示服务端调试范围。"
    if function_name == "SM_FightChannelFun":
        if "fightCastVO.movePos" in stripped or "movePos" in stripped and "fightCastVO" in stripped:
            return "fight_channel_move_pos", "读取引导施法期间的 movePos。"
        if "SetEntityPosition(pos" in stripped:
            return "fight_channel_set_position", "本地 runtime skill 不匹配时直接校正实体位置。"
        if "ResetAttackPlayableData" in stripped:
            return "fight_channel_reset_attack_playable", "按 channellingCount 修正攻击 playable 的移动轨道。"
    if function_name == "SM_UnitStateFun" and "SetServerUnitState" in stripped:
        return "unit_state_set", "写入服务端单位状态。"
    return "", ""


def _fight_side_channel_field_refs(packet_name: str, line: str) -> str:
    refs: list[str] = []
    if packet_name:
        for match in re.finditer(r"\bmsg\.([A-Za-z0-9_]+)", line):
            refs.append(f"{packet_name}.{match.group(1)}")
    for match in re.finditer(r"\bskillEffectVo\.([A-Za-z0-9_]+)", line):
        refs.append(f"SkillEffectVO.{match.group(1)}")
    for match in re.finditer(r"\bmoveSkillVo\.([A-Za-z0-9_]+)", line):
        refs.append(f"MoveSkillEffectVO.forceMoveVOs[].{match.group(1)}")
    for match in re.finditer(r"\bfightCastVO\.([A-Za-z0-9_]+)", line):
        refs.append(f"FightCastVO.{match.group(1)}")
    for name in (
        "changeQiMap",
        "restrictCode",
        "skill2cd",
        "canSelect",
        "channellingCount",
        "timeLineType",
        "timeLine",
        "buffId",
        "state",
        "targetPos",
        "currPos",
    ):
        if packet_name and re.search(rf"\b{re.escape(name)}\b", line):
            refs.append(f"{packet_name}.{name}")
    return _join_unique(refs, limit=40)


def _build_fight_side_channel_rows(
    root: Path,
    all_fields_by_packet_name: dict[str, list[dict[str, str]]],
    all_packet_by_name: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for packet_name in sorted(_FIGHT_SIDE_CHANNEL_PACKET_NAMES):
        packet = all_packet_by_name.get(packet_name, {})
        fields = all_fields_by_packet_name.get(packet_name, [])
        if not packet and not fields:
            continue
        base_data = {
            "channel_group": _fight_side_channel_group(packet_name),
            "packet_name": packet_name,
            "direction": packet.get("direction") or _direction_for_packet_name(packet_name),
            "source_file": packet.get("relative_path") or "",
            "file_name": packet.get("file") or "",
            "function_name": "reading",
        }
        if not fields:
            rows.append(
                {
                    **base_data,
                    "row_kind": "packet_no_fields",
                    "line": "",
                    "field_refs": "",
                    "runtime_effect": _fight_side_channel_field_semantics(packet_name, ""),
                    "authority_note": _fight_side_channel_authority_note("packet_no_fields"),
                    "code": "",
                }
            )
            continue
        for field in fields:
            field_name = field.get("field_name") or ""
            rows.append(
                {
                    **base_data,
                    "row_kind": "packet_field",
                    "source_file": packet.get("relative_path") or field.get("relative_path") or "",
                    "file_name": packet.get("file") or field.get("file") or "",
                    "line": field.get("line") or "",
                    "field_refs": f"{packet_name}.{field_name}" if field_name else "",
                    "runtime_effect": _fight_side_channel_field_semantics(packet_name, field_name),
                    "authority_note": _fight_side_channel_authority_note("packet_field"),
                    "code": f"{field_name}:{field.get('read_method') or ''}"
                    + (f"<{field.get('type_hint')}>" if field.get("type_hint") else ""),
                }
            )

    fight_net_logic = _find_lua_text_asset(root, "FightNetLogic.lua", must_contain="SM_SkillEffectFun")
    if fight_net_logic:
        lines = fight_net_logic.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            packet_name = _fight_side_channel_packet_for_line(function_name, line)
            row_kind, runtime_effect = _fight_side_channel_line_kind(function_name, line)
            if not row_kind:
                continue
            rows.append(
                {
                    "channel_group": _fight_side_channel_group(packet_name, function_name),
                    "row_kind": row_kind,
                    "packet_name": packet_name,
                    "direction": _direction_for_packet_name(packet_name) if packet_name else "",
                    "source_file": _relative_to_root(fight_net_logic, root),
                    "file_name": fight_net_logic.name,
                    "function_name": function_name,
                    "line": line_no,
                    "field_refs": _fight_side_channel_field_refs(packet_name, line),
                    "runtime_effect": runtime_effect,
                    "authority_note": _fight_side_channel_authority_note(row_kind),
                    "code": line.strip()[:300],
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["channel_group"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    return rows


_RESTRICT_STATUS_SEMANTICS = {
    "FORBID_MOVE": "禁止移动；如果当前在移动状态，AddRestrictCode 会 StopMove。",
    "CANNOT_SELECT_AS_TARGET": "不能被选为目标；IsCanSelectAsTarget 会直接返回 false。",
    "USE_DEFAULT_SKILL_ONLY": "只能使用默认技能；UserNormalAttackOnly 返回 true。",
    "FORBID_USE_SKILL": "禁止使用技能；IsCanCastSkill 返回 false。",
    "NO_HP_CHANGE": "禁止 HP 变化；当前 Lua 只见枚举定义，未见消费点。",
    "FORBID_REDUCE_HP": "禁止扣 HP；当前 Lua 只见枚举定义，未见消费点。",
    "FORBID_RECOVER_HP": "禁止恢复 HP；当前 Lua 只见枚举定义，未见消费点。",
    "FORBID_REDUCE_MP": "禁止扣 MP；当前 Lua 只见枚举定义，未见消费点。",
    "FORBID_RECOVER_MP": "禁止恢复 MP；当前 Lua 只见枚举定义，未见消费点。",
    "AI_DISABLE": "禁用 AI；自动战斗组件会响应该状态变化。",
    "CAN_NOT_MISS": "不能 miss；当前 Lua 只见枚举定义，未见消费点。",
    "FORBID_ADD_BUFF": "禁止添加 Buff；当前 Lua 只见枚举定义，未见消费点。",
    "FIGHT_MOTION": "战斗动作限制；当前 Lua 只见枚举定义，未见消费点。",
    "GATHER": "采集状态限制；当前 Lua 只见枚举定义，未见消费点。",
    "FORBID_USE_SKILL_GONGFA": "禁止使用功法技能；UserView.ForbidUseGongFa 直接检查。",
    "FORBID_USE_SKILL_DODGE": "禁止使用闪避技能；UserView.ForbidUseDodge 直接检查。",
    "FORBID_USE_SKILL_XINFA": "禁止使用心法技能；当前 Lua 只见枚举定义，未见消费点。",
    "FORBID_USE_SKILL_NORMAL": "禁止普通攻击；UserView.ForbidUseNormalAttack 直接检查。",
}

_UNIT_STATE_SEMANTICS = {
    "idle": "空闲状态；UserView 初始化时写入默认 serverUnitState。",
    "fight": "战斗状态位；PlayerView 用它切换环绕部件显示。",
    "fight_pvp": "PVP 战斗状态位；Player:IsInServerFightState 使用该位判断。",
    "horse": "骑乘状态位；PlayerView 用它切换 EasyFly/Run/Idle。",
}


def _fight_status_usage_row_kind(line: str, code_group: str) -> str:
    stripped = line.strip()
    if code_group == "restrict_status":
        if "AddRestrictCode" in stripped:
            return "restrict_code_set"
        if "RESTRICT_STATUS_CHANGED" in stripped:
            return "restrict_change_event"
        if "IsInRestrictStatus" in stripped or "bit.band" in stripped:
            return "restrict_bit_check"
        return "restrict_reference"
    if "SetServerUnitState" in stripped:
        return "unit_state_set"
    if "bit.band" in stripped or "IsHasState" in stripped:
        return "unit_state_bit_check"
    return "unit_state_reference"


def _fight_status_runtime_effect(code_group: str, code_name: str, line: str) -> str:
    if code_group == "restrict_status":
        if "StopMove" in line:
            return "命中 FORBID_MOVE 后停止移动。"
        if "return false" in line:
            return "限制状态导致能力判定返回 false。"
        return _RESTRICT_STATUS_SEMANTICS.get(code_name, "")
    return _UNIT_STATE_SEMANTICS.get(code_name, "")


def _build_fight_status_code_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    search_root = root / "by_source" / "lscripts"
    restrict_values: dict[str, dict[str, Any]] = {}
    unit_state_values: dict[str, dict[str, Any]] = {}

    skill_define = _find_lua_text_asset(root, "SkillDefine.lua", must_contain="RestrictStatus")
    if skill_define:
        lines = skill_define.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        for item in _extract_lua_number_table(lines, "RestrictStatus"):
            code_name = str(item.get("name") or "")
            value = int(item.get("value") or 0)
            restrict_values[code_name] = item
            rows.append(
                {
                    "code_group": "restrict_status",
                    "row_kind": "enum_value",
                    "code_name": code_name,
                    "value": value,
                    "hex_value": hex(value),
                    "bit_index": _bit_index(value),
                    "source_file": _relative_to_root(skill_define, root),
                    "file_name": skill_define.name,
                    "function_name": "",
                    "line": item.get("line") or "",
                    "runtime_effect": _RESTRICT_STATUS_SEMANTICS.get(code_name, ""),
                    "evidence": "SkillDefine.RestrictStatus 枚举定义。",
                    "code": f"{code_name}={value}",
                }
            )

    player_type = _find_lua_text_asset(root, "PlayerType.lua", must_contain="UnitState")
    if player_type:
        lines = player_type.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        for item in _extract_lua_number_table(lines, "UnitState"):
            code_name = str(item.get("name") or "")
            value = int(item.get("value") or 0)
            unit_state_values[code_name] = item
            rows.append(
                {
                    "code_group": "unit_state",
                    "row_kind": "enum_value",
                    "code_name": code_name,
                    "value": value,
                    "hex_value": hex(value),
                    "bit_index": _bit_index(value),
                    "source_file": _relative_to_root(player_type, root),
                    "file_name": player_type.name,
                    "function_name": "",
                    "line": item.get("line") or "",
                    "runtime_effect": _UNIT_STATE_SEMANTICS.get(code_name, ""),
                    "evidence": "PlayerType.UnitState 枚举定义。",
                    "code": f"{code_name}={value}",
                }
            )

    if not search_root.is_dir():
        return rows
    for path in sorted(search_root.glob("**/text_assets/*.lua")):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if "RestrictStatus." not in text and "PlayerType.UnitState." not in text and "AddRestrictCode" not in text:
            continue
        lines = text.splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            if "AddRestrictCode" in line or "RESTRICT_STATUS_CHANGED" in line:
                rows.append(
                    {
                        "code_group": "restrict_status",
                        "row_kind": _fight_status_usage_row_kind(line, "restrict_status"),
                        "code_name": "",
                        "value": "",
                        "hex_value": "",
                        "bit_index": "",
                        "source_file": _relative_to_root(path, root),
                        "file_name": path.name,
                        "function_name": function_name,
                        "line": line_no,
                        "runtime_effect": "restrictCode 被整体写入或触发状态变化事件。",
                        "evidence": "客户端保存服务端 restrictCode，并通知监听者重新判断 bitmask。",
                        "code": line.strip()[:300],
                    }
                )
            for code_name in re.findall(r"SkillDefine\.RestrictStatus\.([A-Z0-9_]+)", line):
                value = restrict_values.get(code_name, {}).get("value", "")
                rows.append(
                    {
                        "code_group": "restrict_status",
                        "row_kind": _fight_status_usage_row_kind(line, "restrict_status"),
                        "code_name": code_name,
                        "value": value,
                        "hex_value": hex(int(value)) if value != "" else "",
                        "bit_index": _bit_index(int(value)) if value != "" else "",
                        "source_file": _relative_to_root(path, root),
                        "file_name": path.name,
                        "function_name": function_name,
                        "line": line_no,
                        "runtime_effect": _fight_status_runtime_effect("restrict_status", code_name, line),
                        "evidence": "客户端按 bitmask 检查 RestrictStatus。",
                        "code": line.strip()[:300],
                    }
                )
            for code_name in re.findall(r"PlayerType\.UnitState\.([A-Za-z0-9_]+)", line):
                value = unit_state_values.get(code_name, {}).get("value", "")
                rows.append(
                    {
                        "code_group": "unit_state",
                        "row_kind": _fight_status_usage_row_kind(line, "unit_state"),
                        "code_name": code_name,
                        "value": value,
                        "hex_value": hex(int(value)) if value != "" else "",
                        "bit_index": _bit_index(int(value)) if value != "" else "",
                        "source_file": _relative_to_root(path, root),
                        "file_name": path.name,
                        "function_name": function_name,
                        "line": line_no,
                        "runtime_effect": _fight_status_runtime_effect("unit_state", code_name, line),
                        "evidence": "客户端按 PlayerType.UnitState 位检查或写入 serverUnitState。",
                        "code": line.strip()[:300],
                    }
                )
    rows.sort(
        key=lambda row: (
            str(row["code_group"]),
            str(row["code_name"]),
            str(row["row_kind"]),
            str(row["source_file"]),
            int(row["line"] or 0),
        )
    )
    return rows


_SYNC_UNIT_SKILL_CD_PACKET_NAMES = {
    "SM_SyncUnit",
    "SM_ReplaceSkill",
    "SM_ChangeGroup",
    "SM_AutoReplace",
    "SkillInfoVO",
}

_SYNC_UNIT_SKILL_CD_FILE_NAMES = {
    "FightNetLogic.lua",
    "SkillMgr.lua",
    "SkillData.lua",
    "SkillNetLogic.lua",
}

_SYNC_UNIT_SKILL_CD_FIELD_SEMANTICS = {
    ("SM_SyncUnit", "currHp"): "复活/同步时的当前 HP；同包还带技能组/CD 数据。",
    ("SM_SyncUnit", "maxHp"): "复活/同步时的最大 HP。",
    ("SM_SyncUnit", "currMp"): "复活/同步时的当前 MP。",
    ("SM_SyncUnit", "maxMp"): "复活/同步时的最大 MP。",
    ("SM_SyncUnit", "runSpeed"): "复活/同步时的移动速度。",
    ("SM_SyncUnit", "systemTime"): "服务端时间戳；客户端用它计算剩余 CD。",
    ("SM_SyncUnit", "groupId"): "当前技能组 id；作为 SkillData.cdDic 的一级 key。",
    ("SM_SyncUnit", "skills"): "SkillInfoVO 列表；按索引与 cds 列表对齐。",
    ("SM_SyncUnit", "cds"): "CD 结束时间列表；SkillData 用 cds[index-1]-systemTime 转为剩余 CD。",
    ("SM_SyncUnit", "chargeLv"): "蓄力等级/充能等级；当前链路未见 SkillData 消费。",
    ("SM_ReplaceSkill", "systemTime"): "单槽替换响应服务端时间戳；用于换算剩余 CD。",
    ("SM_ReplaceSkill", "groupId"): "单槽替换后的技能组 id。",
    ("SM_ReplaceSkill", "skills"): "替换后服务端确认的完整 SkillInfoVO 列表。",
    ("SM_ReplaceSkill", "cds"): "替换后技能组 CD 结束时间列表。",
    ("SM_ChangeGroup", "systemTime"): "切组响应服务端时间戳；用于换算剩余 CD。",
    ("SM_ChangeGroup", "groupId"): "服务端确认切到的技能组 id。",
    ("SM_ChangeGroup", "skills"): "切组后服务端确认的 SkillInfoVO 列表。",
    ("SM_ChangeGroup", "cds"): "切组后技能组 CD 结束时间列表。",
    ("SM_AutoReplace", "systemTime"): "自动替换响应服务端时间戳；用于换算剩余 CD。",
    ("SM_AutoReplace", "groupId"): "自动替换的技能组 id。",
    ("SM_AutoReplace", "skills"): "自动替换后服务端确认的 SkillInfoVO 列表。",
    ("SM_AutoReplace", "cds"): "自动替换后技能组 CD 结束时间列表。",
    ("SkillInfoVO", "skillId"): "技能槽中的实际技能 id；CD 字典最终以它为 key。",
    ("SkillInfoVO", "jie"): "技能阶数。",
    ("SkillInfoVO", "star"): "技能星级。",
    ("SkillInfoVO", "type"): "技能类型；自创功法通过 Create 类型区分。",
    ("SkillInfoVO", "makeId"): "自创功法实例 id；普通技能可为 0。",
}


_SYNC_UNIT_STATE_PACKET_NAMES = {
    "SM_SyncUnit",
    "SM_Revive",
    "SM_ShadowInfo",
    "SM_UnitMaxHpUpdate",
}

_SYNC_UNIT_STATE_FILE_NAMES = {
    "FightNetLogic.lua",
    "RoleMgr.lua",
    "Player.lua",
    "Monster.lua",
    "EntityFight.lua",
}

_SYNC_UNIT_STATE_FIELD_SEMANTICS = {
    ("SM_SyncUnit", "currHp"): "同步/复活时的当前 HP；可见入口交给 RoleMgr:ReviveInfo。",
    ("SM_SyncUnit", "maxHp"): "同步/复活时的最大 HP；可见入口交给 RoleMgr:ReviveInfo。",
    ("SM_SyncUnit", "currMp"): "同步/复活时的当前 MP；可见入口交给 RoleMgr:ReviveInfo。",
    ("SM_SyncUnit", "maxMp"): "同步/复活时的最大 MP；可见入口交给 RoleMgr:ReviveInfo。",
    ("SM_SyncUnit", "runSpeed"): "同步/复活时的移动速度；当前导出 Lua 未见直接消费点。",
    ("SM_SyncUnit", "chargeLv"): "同步/复活时的蓄力等级；Player.InitData 也会从角色 VO 写入 chargeLv。",
    ("SM_Revive", "maxHp"): "复活包里的 HP map；SM_ReviveFun 中按 unit id 写入 LuaEntityPropertyType.HP。",
    ("SM_Revive", "maxMp"): "复活包里的 MP map；SM_ReviveFun 中按 unit id 写入 LuaEntityPropertyType.MP。",
    ("SM_Revive", "costResults"): "复活消耗结果；用于飘字/奖励消耗展示。",
    ("SM_Revive", "reviveType"): "复活类型；BornRevive 分支会刷新自动战斗按钮并重置相机。",
    ("SM_ShadowInfo", "currHp"): "影子/回放 HP 当前值；写入 SHADOWHP special property。",
    ("SM_ShadowInfo", "maxHp"): "影子/回放 HP 最大值；写入 SHADOWMAXHP special property。",
    ("SM_UnitMaxHpUpdate", "id"): "需要同步最大 HP 的实体 id。",
    ("SM_UnitMaxHpUpdate", "maxHp"): "实体最大 HP 更新值；直接写入 LuaEntityPropertyType.MAXHP。",
    ("SM_UnitMaxHpUpdate", "currHp"): "实体当前 HP 更新值；直接写入 LuaEntityPropertyType.HP。",
}


_ROLE_ATTRIBUTE_SYNC_PACKET_NAMES = {
    "ChangedAttrsVo",
    "SM_RoleChangedAttrs",
    "SM_RealmUpRewardAttr",
    "SM_ChangedPlayerAttribute",
    "SM_FightScore",
    "SM_ModuleFightScore",
    "SM_TakeMedicineAttributeSync",
}

_ROLE_ATTRIBUTE_SYNC_FILE_NAMES = {
    "RoleNetLogic.lua",
    "RoleMgr.lua",
    "GameUtil.lua",
    "EntityFight.lua",
    "RoleInfoPanel.lua",
}

_ROLE_ATTRIBUTE_SYNC_FIELD_SEMANTICS = {
    ("ChangedAttrsVo", "addAttrs"): "属性增量 map；主要用于属性变化飘字和提示。",
    ("ChangedAttrsVo", "subAttrs"): "属性减少 map；当前已读 GameUtil 链路未直接消费。",
    ("ChangedAttrsVo", "finalAttrs"): "最终属性 map；GameUtil:DealAttrChangeByModule 逐项写入 UserView.Entity:SetProperty(key, finalValue)。",
    ("SM_RoleChangedAttrs", "attrs"): "通用角色属性变化 VO；RoleNetLogic:SM_ChangeAttrsVoFun 转给 GameUtil:DealAttrChangeByModule。",
    ("SM_RealmUpRewardAttr", "attrs"): "境界/奖励相关属性变化 VO；RoleNetLogic:SM_RealmUpRewardAttr 转给 GameUtil:DealAttrChangeByModule。",
    ("SM_ChangedPlayerAttribute", "attributes"): "单位属性 map；RoleNetLogic 按 key/value 直接写入目标 entity 的 SetProperty。",
    ("SM_ChangedPlayerAttribute", "unitId"): "需要更新属性的单位 id；RoleNetLogic 用它定位 EntityFightInBattleView。",
    ("SM_FightScore", "score"): "服务端下发总战力；RoleMgr:UpdateFightScore 写入 LuaEntityPropertyType.FIGHT_POWER。",
    ("SM_ModuleFightScore", "moduleName2FightScore"): "分模块战力 map；当前 RoleNetLogic 只交给 GM 战力面板展示。",
    ("SM_TakeMedicineAttributeSync", "attrs"): "丹药/服药同步属性 VO；同样使用 ChangedAttrsVo 结构。",
    ("SM_TakeMedicineAttributeSync", "exp"): "丹药/服药属性同步附带经验值。",
}


_GONGFA_ATTR_CHANGE_PACKET_NAMES = {
    "ChangedAttrsVo",
    "CM_GongFaLearn",
    "SM_GongFaLearn",
    "CM_GongFaUpgrade",
    "SM_GongFaUpgrade",
    "CM_GongFaUpgradeTimes",
    "SM_GongFaUpgradeTimes",
}

_GONGFA_ATTR_CHANGE_FILE_NAMES = {
    "GongFaNewNetLogic.lua",
    "GongFaNewModel.lua",
}

_GONGFA_ATTR_CHANGE_FIELD_SEMANTICS = {
    ("CM_GongFaLearn", "baseId"): "请求学习的功法 baseId。",
    ("SM_GongFaLearn", "gongfa"): "学习后服务端确认的 GongFaItemVO。",
    ("SM_GongFaLearn", "results"): "学习结果列表。",
    ("SM_GongFaLearn", "rewardResults"): "学习附带奖励/经验结果，Model 中会提取 EXP 传给属性提示。",
    ("SM_GongFaLearn", "attrs"): "学习后属性变化 VO；Model:GongFaLearn 转给 GameUtil:DealAttrChangeByModule。",
    ("CM_GongFaUpgrade", "type"): "升级类型；客户端调用时来自 GongFaNewType.UpType。",
    ("CM_GongFaUpgrade", "baseId"): "要升级的功法 baseId。",
    ("CM_GongFaUpgrade", "times"): "升级次数。",
    ("SM_GongFaUpgrade", "gongfa"): "升级后服务端确认的 GongFaItemVO。",
    ("SM_GongFaUpgrade", "results"): "升级结果列表。",
    ("SM_GongFaUpgrade", "rewardResults"): "升级奖励/经验结果，Model 中会提取 EXP。",
    ("SM_GongFaUpgrade", "attrs"): "升级后属性变化 VO；Model:UpgradeRefresh 转给 GameUtil:DealAttrChangeByModule。",
    ("SM_GongFaUpgrade", "upgradeQuality"): "升品/升阶特殊确认标记；部分分支先缓存再弹确认页。",
    ("CM_GongFaUpgradeTimes", "upgradeList"): "一键/批量升级请求列表。",
    ("SM_GongFaUpgradeTimes", "upgradeList"): "批量升级结果列表；Model 聚合 attrs.addAttrs 并取最后一次 finalAttrs。",
    ("ChangedAttrsVo", "addAttrs"): "属性增量 map；批量升级时会累加多次 addAttrs。",
    ("ChangedAttrsVo", "finalAttrs"): "最终属性 map；批量升级取最后一次结果作为最终属性写入。",
}

_GONGFA_STATE_PACKET_NAMES = {
    "CM_GongFaView",
    "SM_GongFaView",
    "SimpleItemVO",
    "GongFaItemVO",
    "SM_GongFaLearn",
    "SM_GongFaUpgrade",
    "SM_GongFaUpgradeTimes",
}

_GONGFA_STATE_FILE_NAMES = {
    "CM_GongFaView.lua",
    "SM_GongFaView.lua",
    "SimpleItemVO.lua",
    "GongFaItemVO.lua",
    "GongFaVo.lua",
    "GongFaNewNetLogic.lua",
    "GongFaNewModel.lua",
    "GongFaNewData.lua",
}

_GONGFA_STATE_FIELD_SEMANTICS = {
    ("SM_GongFaView", "actives"): "功法相关已激活字典；Model:SetGongFaInfo 直接保存到 GongFaNewData.actives。",
    ("SM_GongFaView", "xinFaPutUpList"): "心法上阵槽列表；GongFaNewData:SetXinFaInfo 合并进本地槽位。",
    ("SM_GongFaView", "fazePutUpList"): "法则/法则上阵列表；转给 FazeMgr 保存面板数据。",
    ("SM_GongFaView", "skillList"): "已学习功法技能 id 列表；GongFaNewData:SetLearnedSkillList 维护 Old/NewLearnedSkill。",
    ("SM_GongFaView", "programVOList"): "功法方案列表；GongFaNewData:SetGongFaProgram 建 programDic。",
    ("SimpleItemVO", "baseId"): "所有 SimpleItemVO 子类继承的静态配置 id；GongFaItemVO 用它作为功法 baseId。",
    ("SimpleItemVO", "id"): "背包/实例层长整型 id。",
    ("SimpleItemVO", "num"): "数量字段。",
    ("GongFaItemVO", "grade"): "功法等级。",
    ("GongFaItemVO", "jie"): "功法阶数。",
    ("GongFaItemVO", "star"): "功法星级。",
    ("GongFaItemVO", "pin"): "功法品/品阶。",
    ("GongFaItemVO", "tongxuan"): "通玄进度/状态。",
    ("GongFaItemVO", "quality"): "服务端确认的当前品质。",
    ("GongFaItemVO", "totalExp"): "功法累计经验。",
    ("GongFaItemVO", "qualityNum"): "品质相关计数字典。",
    ("SM_GongFaLearn", "gongfa"): "学习后服务端确认的 GongFaItemVO；UpdateGongFaVo 覆盖本地状态。",
    ("SM_GongFaUpgrade", "gongfa"): "升级后服务端确认的 GongFaItemVO；UpdateGongFaVo 覆盖本地状态。",
    ("SM_GongFaUpgradeTimes", "upgradeList"): "批量升级结果列表；每个元素包含 gongfa，用于逐个 UpdateGongFaVo。",
}

_GONGFA_ATTR_DISPLAY_FILE_NAMES = {
    "DetailPanel.lua",
    "GongFaAttrItem.lua",
    "GongFaChangeQualityView.lua",
    "GongFaJieFinishView.lua",
    "GongFaNewData.lua",
    "GongFaNewMgr.lua",
    "GongFaNewModel.lua",
    "GongFaTongXuanView.lua",
    "GongFaUpJieView.lua",
    "GongFaUpLevelView.lua",
    "GongFaUpPinView.lua",
    "GongFaUpStarView.lua",
    "PracticeTogetherGongFaView.lua",
}

_GONGFA_RICH_TEXT_FILE_NAMES = {
    "DesItem.lua",
    "DetailPanel.lua",
    "GongFaAttrItem.lua",
    "XianShuCreateItem.lua",
    "XianShuCreateSkillDetailView.lua",
}

_GONGFA_DESCRIPTION_COMPOSITION_FILE_NAMES = {
    "GongfahomemakeMgr.lua",
    "GongFaNewData.lua",
    "XianShuCreateSkillDetailView.lua",
}


def _sync_unit_state_stage(file_name: str, function_name: str) -> str:
    if file_name == "FightNetLogic.lua" and function_name == "SM_SyncUnitFun":
        return "sync_unit_entry"
    if file_name == "RoleMgr.lua" and function_name == "ReviveInfo":
        return "rolemgr_revive_info"
    if file_name == "FightNetLogic.lua" and function_name == "SM_ReviveFun":
        return "visible_revive_packet"
    if file_name == "FightNetLogic.lua" and function_name in {"SM_ShadowHpChangeFun", "SM_ShadowInfoFun"}:
        return "shadow_state_sync"
    if file_name == "FightNetLogic.lua" and function_name == "SM_UnitMaxHpUpdateFun":
        return "max_hp_sync"
    if file_name in {"Player.lua", "Monster.lua"} and function_name == "InitData":
        return "entity_init_state"
    if file_name == "EntityFight.lua":
        return "entity_property_runtime"
    return "other"


def _sync_unit_state_field_semantics(packet_name: str, field_name: str) -> str:
    return _SYNC_UNIT_STATE_FIELD_SEMANTICS.get((packet_name, field_name), "")


def _sync_unit_state_line_kind(file_name: str, function_name: str, line: str) -> tuple[str, str]:
    stripped = line.strip()
    if file_name == "FightNetLogic.lua" and function_name == "SM_SyncUnitFun":
        if stripped.startswith("function "):
            return "sync_unit_handler_decl", "SM_SyncUnit 网络处理入口。"
        if "RoleMgr.Inst_get():ReviveInfo(msg)" in stripped:
            return "dispatch_rolemgr_revive_info", "把 currHp/maxHp/currMp/maxMp/runSpeed/chargeLv 交给 RoleMgr:ReviveInfo。"
        if "SkillMgr.Inst_get():RefreshUserSkillCD(msg)" in stripped:
            return "dispatch_skill_cd_sync", "同一回包的技能/CD 分支交给 SkillMgr。"
    if file_name == "RoleMgr.lua" and function_name == "ReviveInfo":
        if stripped.startswith("function "):
            return "rolemgr_revive_info_decl", "RoleMgr 接收 SM_SyncUnit 的状态分支。"
        if "userView==nil or msg==nil" in stripped:
            return "guard_user_view_or_msg", "没有 UserView 或 msg 时直接返回。"
        if "IsInState(StateType.Dead)" in stripped and "msg.currHp>0" in stripped:
            return "dead_state_revive_check", "用户处于死亡状态且 currHp>0 时触发复活。"
        if "userView:Revive()" in stripped:
            return "revive_user_view", "本地 UserView 执行复活。"
        if "SetChargeLv(msg.chargeLv)" in stripped:
            return "set_charge_level", "把 SM_SyncUnit.chargeLv 写入实体蓄力等级。"
        if "SetProperty(LuaEntityPropertyType.HP,msg.currHp)" in stripped:
            return "rolemgr_set_hp", "把 SM_SyncUnit.currHp 写入实体 HP。"
        if "SetProperty(LuaEntityPropertyType.MAXHP,msg.maxHp)" in stripped:
            return "rolemgr_set_max_hp", "把 SM_SyncUnit.maxHp 写入实体 MAXHP。"
        if "SetProperty(LuaEntityPropertyType.MP,msg.currMp)" in stripped:
            return "rolemgr_set_mp", "把 SM_SyncUnit.currMp 写入实体 MP。"
        if "SetProperty(LuaEntityPropertyType.MAXMP,msg.maxMp)" in stripped:
            return "rolemgr_set_max_mp", "把 SM_SyncUnit.maxMp 写入实体 MAXMP。"
        if "SetProperty(LuaEntityPropertyType.RUNSPEED,msg.runSpeed)" in stripped:
            return "rolemgr_set_runspeed", "把 SM_SyncUnit.runSpeed 写入实体 RUNSPEED。"
    if file_name == "FightNetLogic.lua" and function_name == "SM_ReviveFun":
        if stripped.startswith("function "):
            return "revive_handler_decl", "SM_Revive 可见复活处理入口。"
        if "Kpairs(msg.maxHp)" in stripped:
            return "iterate_revive_hp_map", "遍历复活包中的 HP map。"
        if "SetProperty(LuaEntityPropertyType.HP" in stripped:
            return "set_revive_hp", "把复活 HP 写入实体 LuaEntityPropertyType.HP。"
        if "entityView:Revive()" in stripped:
            return "revive_entity_view", "触发实体复活动画/状态恢复。"
        if "Kpairs(msg.maxMp)" in stripped:
            return "iterate_revive_mp_map", "遍历复活包中的 MP map。"
        if "SetProperty(LuaEntityPropertyType.MP" in stripped:
            return "set_revive_mp", "把复活 MP 写入实体 LuaEntityPropertyType.MP。"
        if "msg.reviveType" in stripped and "BornRevive" in stripped:
            return "born_revive_branch", "出生点复活分支。"
        if "UpdateAutoFightBtnStatus" in stripped:
            return "raise_auto_fight_status", "自身出生点复活后刷新自动战斗按钮状态。"
        if "ResetUserSceneCamera" in stripped:
            return "reset_revive_camera", "自身出生点复活后按最近出生点重置相机。"
    if file_name == "FightNetLogic.lua" and function_name == "SM_ShadowHpChangeFun":
        if stripped.startswith("function "):
            return "shadow_hp_change_decl", "影子/回放 HP 变化入口。"
        if "Kpairs(msg.changeHpMap)" in stripped:
            return "iterate_shadow_hp_map", "遍历影子 HP map。"
        if "Dic_SepcialPropertyKey.SHADOWHP" in stripped:
            return "set_shadow_hp", "把影子 HP 写入 SHADOWHP special property。"
    if file_name == "FightNetLogic.lua" and function_name == "SM_ShadowInfoFun":
        if stripped.startswith("function "):
            return "shadow_info_decl", "影子/回放 HP 完整信息入口。"
        if "Dic_SepcialPropertyKey.SHADOWHP" in stripped:
            return "set_shadow_hp", "写入 SHADOWHP special property。"
        if "Dic_SepcialPropertyKey.SHADOWMAXHP" in stripped:
            return "set_shadow_max_hp", "写入 SHADOWMAXHP special property。"
    if file_name == "FightNetLogic.lua" and function_name == "SM_UnitMaxHpUpdateFun":
        if stripped.startswith("function "):
            return "unit_max_hp_update_decl", "单位最大 HP/当前 HP 同步入口。"
        if "SetProperty(LuaEntityPropertyType.MAXHP" in stripped:
            return "set_unit_max_hp", "把服务端 maxHp 写入实体 MAXHP。"
        if "SetProperty(LuaEntityPropertyType.HP" in stripped:
            return "set_unit_current_hp", "把服务端 currHp 写入实体 HP。"
    if file_name == "Player.lua" and function_name == "InitData":
        if "self.serverUnitState=msg.state" in stripped:
            return "init_server_unit_state", "玩家实体初始化时写入服务端单位状态。"
        if "self.chargeLv=msg.chargeLv" in stripped:
            return "init_player_charge_level", "玩家实体初始化时写入 chargeLv。"
    if file_name == "Monster.lua" and function_name == "InitData":
        if "self.currentHp=msg.currentHp" in stripped:
            return "init_monster_current_hp", "怪物实体初始化时保存 currentHp。"
        if "self.maxHp=msg.maxHp" in stripped:
            return "init_monster_max_hp", "怪物实体初始化时保存 maxHp。"
    if file_name == "EntityFight.lua":
        if stripped.startswith("function ") and function_name == "SetProperty":
            return "property_setter_decl", "实体普通属性写入入口。"
        if function_name == "SetProperty" and "self.PropertyDic:LuaDic_AddOrSetItem(protype,num)" in stripped:
            return "store_entity_property", "把 HP/MP/MAXHP/MAXMP 等普通属性写入 PropertyDic。"
        if function_name == "SetProperty" and "CommonEventType.PROPERTY_CHANGE" in stripped:
            return "raise_property_change", "普通属性变化后抛事件给显示层。"
        if stripped.startswith("function ") and function_name == "SetSepcialProperty":
            return "special_property_setter_decl", "special property 写入入口。"
        if function_name == "SetSepcialProperty" and "LuaDic_AddOrSetItem(protype,num)" in stripped:
            return "store_special_property", "把 SHADOWHP/SHADOWMAXHP 等 special property 存入字典。"
        if function_name == "SetSepcialProperty" and "SEPCIAL_PROPERTY_CHANGE" in stripped:
            return "raise_special_property_change", "special property 变化后抛事件给显示层。"
        if stripped.startswith("function ") and function_name == "TakeNumDamage":
            return "local_damage_helper_decl", "本地数值扣血 helper。"
        if "SetProperty(LuaEntityPropertyType.HP,num)" in stripped:
            return "local_set_hp_after_damage", "本地 helper 最终仍写入 HP 属性。"
    return "", ""


def _sync_unit_state_packet_for_function(function_name: str) -> str:
    if function_name == "SM_SyncUnitFun":
        return "SM_SyncUnit"
    if function_name == "ReviveInfo":
        return "SM_SyncUnit"
    if function_name == "SM_ReviveFun":
        return "SM_Revive"
    if function_name in {"SM_ShadowHpChangeFun", "SM_ShadowInfoFun"}:
        return "SM_ShadowInfo"
    if function_name == "SM_UnitMaxHpUpdateFun":
        return "SM_UnitMaxHpUpdate"
    return ""


def _sync_unit_state_field_refs(file_name: str, function_name: str, line: str) -> str:
    refs: list[str] = []
    packet_name = _sync_unit_state_packet_for_function(function_name)
    for match in re.finditer(r"\bmsg\.([A-Za-z0-9_]+)", line):
        refs.append(f"{packet_name}.{match.group(1)}" if packet_name else f"msg.{match.group(1)}")
    for match in re.finditer(r"LuaEntityPropertyType\.([A-Z0-9_]+)", line):
        refs.append(f"LuaEntityPropertyType.{match.group(1)}")
    for match in re.finditer(r"Dic_SepcialPropertyKey\.([A-Z0-9_]+)", line):
        refs.append(f"Dic_SepcialPropertyKey.{match.group(1)}")
    if "RoleMgr.Inst_get():ReviveInfo" in line:
        refs.append("RoleMgr.ReviveInfo")
    if "entityView:Revive()" in line:
        refs.append("EntityView.Revive")
    return _join_unique(refs, limit=40)


def _build_sync_unit_state_rows(
    root: Path,
    all_fields_by_packet_name: dict[str, list[dict[str, str]]],
    all_packet_by_name: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for packet_name in sorted(_SYNC_UNIT_STATE_PACKET_NAMES):
        packet = all_packet_by_name.get(packet_name, {})
        fields = all_fields_by_packet_name.get(packet_name, [])
        if not packet and not fields:
            continue
        for field in fields:
            field_name = field.get("field_name") or ""
            rows.append(
                {
                    "flow_stage": "packet_schema",
                    "row_kind": "packet_field",
                    "packet_name": packet_name,
                    "source_file": packet.get("relative_path") or field.get("relative_path") or "",
                    "file_name": packet.get("file") or field.get("file") or "",
                    "function_name": "reading",
                    "line": field.get("line") or "",
                    "field_refs": f"{packet_name}.{field_name}" if field_name else "",
                    "runtime_effect": _sync_unit_state_field_semantics(packet_name, field_name),
                    "visibility_note": "协议字段定义；SM_SyncUnit 的状态落点优先看 RoleMgr:ReviveInfo，若 RoleMgr.lua 未导出会额外标记缺口。",
                    "code": f"{field_name}:{field.get('read_method') or ''}"
                    + (f"<{field.get('type_hint')}>" if field.get("type_hint") else ""),
                }
            )

    search_root = root / "by_source" / "lscripts"
    if not search_root.is_dir():
        return rows

    role_mgr_paths = list(search_root.glob("**/text_assets/RoleMgr.lua"))
    if not role_mgr_paths:
        rows.append(
            {
                "flow_stage": "visibility_gap",
                "row_kind": "missing_rolemgr_source",
                "packet_name": "SM_SyncUnit",
                "source_file": "",
                "file_name": "RoleMgr.lua",
                "function_name": "ReviveInfo",
                "line": "",
                "field_refs": "SM_SyncUnit.currHp、SM_SyncUnit.maxHp、SM_SyncUnit.currMp、SM_SyncUnit.maxMp、SM_SyncUnit.runSpeed、SM_SyncUnit.chargeLv",
                "runtime_effect": "FightNetLogic 明确调用 RoleMgr:ReviveInfo(msg)，但当前 text_assets 未导出 RoleMgr.lua，无法继续确认该函数内部写入细节。",
                "visibility_note": "这是当前静态导出的缺口，不等于客户端没有该逻辑。",
                "code": 'require("GameSystem.Game.Role.Mgr.RoleMgr") is visible, RoleMgr.lua is not in exported text_assets',
            }
        )

    for path in sorted(search_root.glob("**/text_assets/*.lua")):
        if path.name not in _SYNC_UNIT_STATE_FILE_NAMES:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if not any(
            term in text
            for term in (
                "SM_SyncUnitFun",
                "SM_ReviveFun",
                "SM_ShadowInfoFun",
                "SM_UnitMaxHpUpdateFun",
                "chargeLv",
                "SetSepcialProperty",
                "currentHp",
            )
        ):
            continue
        lines = text.splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            row_kind, runtime_effect = _sync_unit_state_line_kind(path.name, function_name, line)
            if not row_kind:
                continue
            rows.append(
                {
                    "flow_stage": _sync_unit_state_stage(path.name, function_name),
                    "row_kind": row_kind,
                    "packet_name": _sync_unit_state_packet_for_function(function_name),
                    "source_file": _relative_to_root(path, root),
                    "file_name": path.name,
                    "function_name": function_name,
                    "line": line_no,
                    "field_refs": _sync_unit_state_field_refs(path.name, function_name, line),
                    "runtime_effect": runtime_effect,
                    "visibility_note": "可见 Lua 证据；与 RoleMgr:ReviveInfo 缺口分开记录。",
                    "code": line.strip()[:300],
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    return rows


def _role_attribute_sync_stage(file_name: str, function_name: str) -> str:
    if file_name == "RoleNetLogic.lua" and function_name == "LuaRoleNetLogic":
        return "role_packet_registration"
    if file_name == "RoleNetLogic.lua" and function_name in {"SM_ChangeAttrsVoFun", "SM_RealmUpRewardAttr"}:
        return "changed_attrs_dispatch"
    if file_name == "GameUtil.lua" and function_name == "DealAttrChangeByModule":
        return "changed_attrs_apply"
    if file_name == "RoleNetLogic.lua" and function_name == "SM_ChangedPlayerAttributeFun":
        return "unit_attribute_map_apply"
    if file_name == "RoleNetLogic.lua" and function_name in {"SM_FightScoreFun", "SM_ModuleFightScoreFun"}:
        return "fight_score_packet"
    if file_name == "RoleMgr.lua" and function_name == "UpdateFightScore":
        return "fight_score_apply"
    if file_name == "EntityFight.lua" and function_name == "SetProperty":
        return "entity_property_runtime"
    if file_name == "RoleInfoPanel.lua":
        return "fight_score_ui_read"
    return "other"


def _role_attribute_sync_packet_for_function(function_name: str) -> str:
    if function_name == "SM_ChangeAttrsVoFun":
        return "SM_RoleChangedAttrs"
    if function_name == "SM_RealmUpRewardAttr":
        return "SM_RealmUpRewardAttr"
    if function_name == "SM_ChangedPlayerAttributeFun":
        return "SM_ChangedPlayerAttribute"
    if function_name == "SM_FightScoreFun":
        return "SM_FightScore"
    if function_name == "SM_ModuleFightScoreFun":
        return "SM_ModuleFightScore"
    if function_name == "DealAttrChangeByModule":
        return "ChangedAttrsVo"
    if function_name == "UpdateFightScore":
        return "SM_FightScore"
    return ""


def _role_attribute_sync_field_semantics(packet_name: str, field_name: str) -> str:
    return _ROLE_ATTRIBUTE_SYNC_FIELD_SEMANTICS.get((packet_name, field_name), "")


def _role_attribute_sync_registration_kind(line: str) -> tuple[str, str]:
    stripped = line.strip()
    registrations = {
        "_SM_RoleChangedAttrs": ("register_role_changed_attrs", "注册通用角色属性变化包 SM_RoleChangedAttrs。"),
        "_SM_RealmUpRewardAttr": ("register_realm_reward_attr", "注册境界奖励属性包 SM_RealmUpRewardAttr。"),
        "_SM_ChangedPlayerAttribute": ("register_changed_player_attribute", "注册单位属性 map 同步包 SM_ChangedPlayerAttribute。"),
        "_SM_FightScore": ("register_fight_score", "注册总战力同步包 SM_FightScore。"),
        "_SM_ModuleFightScore": ("register_module_fight_score", "注册分模块战力展示包 SM_ModuleFightScore。"),
    }
    if "F_Register" not in stripped:
        return "", ""
    for token, result in registrations.items():
        if token in stripped:
            return result
    return "", ""


def _role_attribute_sync_line_kind(file_name: str, function_name: str, line: str) -> tuple[str, str]:
    stripped = line.strip()
    if file_name == "RoleNetLogic.lua" and function_name == "LuaRoleNetLogic":
        row_kind, effect = _role_attribute_sync_registration_kind(line)
        if row_kind:
            return row_kind, effect
    if file_name == "RoleNetLogic.lua" and function_name == "SM_ChangeAttrsVoFun":
        if stripped.startswith("function "):
            return "role_changed_attrs_handler_decl", "SM_RoleChangedAttrs 的客户端处理入口。"
        if "msg.code==0" in stripped:
            return "client_result_success_guard", "只在 code==0 时处理属性变化。"
        if "msg.attrs" in stripped and "if" in stripped:
            return "attrs_guard", "确认回包带有 ChangedAttrsVo。"
        if "GameUtil.DealAttrChangeByModule(msg.attrs" in stripped:
            return "dispatch_changed_attrs_vo", "把 SM_RoleChangedAttrs.attrs 交给 GameUtil 统一写属性和飘字。"
    if file_name == "RoleNetLogic.lua" and function_name == "SM_RealmUpRewardAttr":
        if stripped.startswith("function "):
            return "realm_reward_attr_handler_decl", "SM_RealmUpRewardAttr 的客户端处理入口。"
        if "GameUtil.DealAttrChangeByModule(msg.attrs" in stripped:
            return "dispatch_realm_reward_attrs", "境界奖励属性也复用 ChangedAttrsVo 统一处理。"
    if file_name == "RoleNetLogic.lua" and function_name == "SM_ChangedPlayerAttributeFun":
        if stripped.startswith("function "):
            return "changed_player_attribute_handler_decl", "SM_ChangedPlayerAttribute 的客户端处理入口。"
        if "GetEntityFightInBattleView(msg.unitId)" in stripped:
            return "locate_entity_by_unit_id", "按 SM_ChangedPlayerAttribute.unitId 定位战斗实体。"
        if "Kpairs(msg.attributes)" in stripped:
            return "iterate_attribute_map", "遍历服务端下发的 attributes map。"
        if "entityView.Entity:SetProperty(k,v)" in stripped:
            return "set_attribute_map_property", "把 attributes map 的 key/value 直接写入 Entity:SetProperty。"
        if "没有协议需要的EntityView" in stripped:
            return "missing_entity_debug", "找不到 unitId 对应实体时只记 debug 日志。"
    if file_name == "RoleNetLogic.lua" and function_name == "SM_FightScoreFun":
        if stripped.startswith("function "):
            return "fight_score_handler_decl", "SM_FightScore 的客户端处理入口。"
        if "RoleMgr.Inst_get():UpdateFightScore(msg.score)" in stripped:
            return "dispatch_fight_score", "把服务端 score 交给 RoleMgr:UpdateFightScore。"
    if file_name == "RoleNetLogic.lua" and function_name == "SM_ModuleFightScoreFun":
        if stripped.startswith("function "):
            return "module_fight_score_handler_decl", "SM_ModuleFightScore 的客户端处理入口。"
        if "msg.code==0" in stripped:
            return "module_fight_score_success_guard", "只在 code==0 时展示分模块战力。"
        if "ShowGmPowerView(msg)" in stripped:
            return "show_module_fight_score_debug", "分模块战力 map 进入 GM 战力面板展示。"
    if file_name == "GameUtil.lua" and function_name == "DealAttrChangeByModule":
        if stripped.startswith("function "):
            return "deal_attr_change_decl", "ChangedAttrsVo 的通用消费入口。"
        if "local addAttrs=attributes.addAttrs" in stripped:
            return "read_add_attrs", "读取 ChangedAttrsVo.addAttrs。"
        if "local subAttrs=attributes.subAttrs" in stripped:
            return "read_sub_attrs", "读取 ChangedAttrsVo.subAttrs。"
        if "local finalAttrs=attributes.finalAttrs" in stripped:
            return "read_final_attrs", "读取 ChangedAttrsVo.finalAttrs。"
        if "local userView=EntityMgr.Inst_get().UserView" in stripped:
            return "locate_user_view", "属性变化默认落到当前 UserView。"
        if "Kpairs(finalAttrs)" in stripped:
            return "iterate_final_attrs", "遍历 finalAttrs，准备写最终属性值。"
        if "finalValue>0 or finalValue==0" in stripped:
            return "accept_non_negative_final_attr", "只接受非负最终属性值写入。"
        if "userView.Entity:SetProperty(key,finalValue)" in stripped:
            return "set_final_attr_property", "把 ChangedAttrsVo.finalAttrs[key] 写入 UserView.Entity:SetProperty。"
        if "Kpairs(addAttrs)" in stripped:
            return "iterate_add_attrs_for_tips", "遍历 addAttrs 用于系统属性变化提示。"
        if "propCfg.showTips==1" in stripped:
            return "filter_attr_tip_config", "按 Attribute_Attribute.showTips 决定是否飘提示。"
        if "ShowAttrSystemTips2" in stripped:
            return "show_exp_attr_tips", "展示经验类属性变化提示。"
        if "ShowAttrSystemTips(list1)" in stripped:
            return "show_attr_tips", "展示普通属性变化提示。"
    if file_name == "RoleMgr.lua" and function_name == "UpdateFightScore":
        if stripped.startswith("function "):
            return "update_fight_score_decl", "总战力写入入口。"
        if 'type(score)=="table"and score._IS_LUSUOLONG' in stripped:
            return "convert_lusuo_long_score", "LusuoLong 形式的 score 转成本地数字。"
        if "GetProperty(LuaEntityPropertyType.FIGHT_POWER)" in stripped:
            return "read_current_fight_power", "读取当前 FIGHT_POWER 用于计算变化量。"
        if "changeValue=newValue-curNum" in stripped:
            return "compute_fight_power_delta", "计算战力变化值。"
        if "SetProperty(LuaEntityPropertyType.FIGHT_POWER,newValue)" in stripped:
            return "set_fight_power_property", "把服务端 score 写入 LuaEntityPropertyType.FIGHT_POWER。"
        if "PlayerType.REFRESH_FIGHT_SCORE" in stripped:
            return "raise_fight_score_event", "战力上涨且满足等级条件时抛刷新/飘字事件。"
    if file_name == "EntityFight.lua" and function_name == "SetProperty":
        if stripped.startswith("function "):
            return "entity_set_property_decl", "实体普通属性统一写入入口。"
        if "self.PropertyDic:LuaDic_AddOrSetItem(protype,num)" in stripped:
            return "store_entity_property", "最终写入 EntityFight.PropertyDic。"
        if "CommonEventType.PROPERTY_CHANGE" in stripped:
            return "raise_property_change", "普通属性变化后抛事件给显示层。"
    if file_name == "RoleInfoPanel.lua":
        if "GetProperty(LuaEntityPropertyType.FIGHT_POWER)" in stripped:
            return "read_fight_power_for_ui", "角色面板从 Model 读取 FIGHT_POWER 用于显示。"
    return "", ""


def _role_attribute_sync_field_refs(file_name: str, function_name: str, line: str) -> str:
    refs: list[str] = []
    packet_name = _role_attribute_sync_packet_for_function(function_name)
    for match in re.finditer(r"\bmsg\.([A-Za-z0-9_]+)", line):
        refs.append(f"{packet_name}.{match.group(1)}" if packet_name else f"msg.{match.group(1)}")
    for token in ("addAttrs", "subAttrs", "finalAttrs"):
        if re.search(rf"\b{token}\b", line):
            refs.append(f"ChangedAttrsVo.{token}")
    for match in re.finditer(r"LuaEntityPropertyType\.([A-Z0-9_]+)", line):
        refs.append(f"LuaEntityPropertyType.{match.group(1)}")
    if "GameUtil.DealAttrChangeByModule" in line:
        refs.append("GameUtil.DealAttrChangeByModule")
    if "RoleMgr.Inst_get():UpdateFightScore" in line:
        refs.append("RoleMgr.UpdateFightScore")
    if "SetProperty" in line:
        refs.append("Entity.SetProperty")
    if "ShowGmPowerView" in line:
        refs.append("GmMgr.ShowGmPowerView")
    return _join_unique(refs, limit=40)


def _build_role_attribute_sync_rows(
    root: Path,
    all_fields_by_packet_name: dict[str, list[dict[str, str]]],
    all_packet_by_name: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for packet_name in sorted(_ROLE_ATTRIBUTE_SYNC_PACKET_NAMES):
        packet = all_packet_by_name.get(packet_name, {})
        fields = all_fields_by_packet_name.get(packet_name, [])
        if not packet and not fields:
            continue
        for field in fields:
            field_name = field.get("field_name") or ""
            rows.append(
                {
                    "flow_stage": "packet_schema" if packet_name != "ChangedAttrsVo" else "changed_attrs_vo_schema",
                    "row_kind": "packet_field",
                    "packet_name": packet_name,
                    "source_file": packet.get("relative_path") or field.get("relative_path") or "",
                    "file_name": packet.get("file") or field.get("file") or "",
                    "function_name": "reading",
                    "line": field.get("line") or "",
                    "field_refs": f"{packet_name}.{field_name}" if field_name else "",
                    "runtime_effect": _role_attribute_sync_field_semantics(packet_name, field_name),
                    "authority_note": "协议字段定义；角色属性和战力字段均以服务端回包为输入，本表只记录客户端读写落点。",
                    "code": f"{field_name}:{field.get('read_method') or ''}"
                    + (f"<{field.get('type_hint')}>" if field.get("type_hint") else ""),
                }
            )

    search_root = root / "by_source" / "lscripts"
    if not search_root.is_dir():
        return rows
    for path in sorted(search_root.glob("**/text_assets/*.lua")):
        if path.name not in _ROLE_ATTRIBUTE_SYNC_FILE_NAMES:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if not any(
            term in text
            for term in (
                "SM_ChangedPlayerAttribute",
                "SM_RoleChangedAttrs",
                "SM_FightScore",
                "SM_ModuleFightScore",
                "SM_RealmUpRewardAttr",
                "DealAttrChangeByModule",
                "FIGHT_POWER",
                "PropertyDic:LuaDic_AddOrSetItem",
            )
        ):
            continue
        lines = text.splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            row_kind, runtime_effect = _role_attribute_sync_line_kind(path.name, function_name, line)
            if not row_kind:
                continue
            rows.append(
                {
                    "flow_stage": _role_attribute_sync_stage(path.name, function_name),
                    "row_kind": row_kind,
                    "packet_name": _role_attribute_sync_packet_for_function(function_name),
                    "source_file": _relative_to_root(path, root),
                    "file_name": path.name,
                    "function_name": function_name,
                    "line": line_no,
                    "field_refs": _role_attribute_sync_field_refs(path.name, function_name, line),
                    "runtime_effect": runtime_effect,
                    "authority_note": "可见 Lua 证据；当前只说明客户端如何应用服务端下发的属性/战力数据。",
                    "code": line.strip()[:300],
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    return rows


def _find_runtime_lang_path(root: Path) -> Path | None:
    candidates = sorted(root.glob("by_source/lscripts/generate/localization/chinese/lang_*/text_assets/lang.lua"))
    if not candidates:
        candidates = sorted(root.glob("**/text_assets/lang.lua"))
    return candidates[0] if candidates else None


def _find_attribute_config_source(root: Path) -> Path | None:
    candidates = sorted(root.glob("by_source/lscripts/generate/cfg/attribute_*/text_assets/Attribute.lua"))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _ensure_attribute_config_rows(
    root: Path,
    *,
    export_root: str | Path | None = None,
    resource_root: str | Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_path = _find_attribute_config_source(root)
    source_status = "exported_source"
    if not source_path:
        resolved_resource_root = resolve_fanxiu_resource_root(resource_root)
        attribute_packages = sorted((resolved_resource_root / "lscripts" / "generate" / "cfg").glob("attribute_*.bytes"))
        if not attribute_packages:
            return [], {
                "status": "missing_attribute_package",
                "source": "",
                "row_count": 0,
                "resource_root": str(resolved_resource_root),
            }
        exported = export_fanxiu_unity_text_assets(
            attribute_packages[0].relative_to(resolved_resource_root).as_posix(),
            resource_root=resolved_resource_root,
            export_root=export_root,
        )
        output_dir = Path(exported["output_dir"])
        candidate = output_dir / "Attribute.lua"
        if not candidate.is_file():
            return [], {
                "status": "missing_attribute_lua_after_export",
                "source": str(candidate),
                "row_count": 0,
                "exported_count": len(exported.get("items", [])),
            }
        source_path = candidate
    else:
        source_status = "existing_source"

    lang_path = _find_runtime_lang_path(root)
    parsed = parse_fanxiu_generated_lua_config(source_path, lang_path=lang_path)
    rows: list[dict[str, Any]] = []
    for row in parsed.get("rows", []):
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "property_code": row.get("code", row.get("_row_key", "")),
                "lua_symbol": row.get("id", ""),
                "display_name": row.get("name_plain", row.get("name", "")),
                "name_lang_id": row.get("name_lang_id", ""),
                "group": row.get("group", ""),
                "exp_show": row.get("expShow", ""),
                "display_roles": row.get("display_roles", ""),
                "show_condition": row.get("showCondition", ""),
                "description": row.get("descript_plain", row.get("descript", "")),
                "details": row.get("details", ""),
                "sort": row.get("sort", ""),
                "show_tips": row.get("showTips", ""),
                "icon_patch": row.get("iconPatch", ""),
                "icon": row.get("icon", ""),
                "source_file": source_path.relative_to(root).as_posix(),
            }
        )
    rows.sort(key=lambda item: (str(item["group"]), int(item["sort"] or 0), int(item["property_code"] or 0)))

    parsed_dir = root / "parsed_configs" / "Attribute"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    (parsed_dir / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    return rows, {
        "status": "ok",
        "source_status": source_status,
        "source": source_path.relative_to(root).as_posix(),
        "lang_path": lang_path.relative_to(root).as_posix() if lang_path else "",
        "row_count": len(rows),
    }


def _gongfa_attr_change_stage(file_name: str, function_name: str) -> str:
    if file_name == "GongFaNewNetLogic.lua" and function_name == "LuaGongFaNewNetLogic":
        return "gongfa_packet_registration"
    if file_name == "GongFaNewNetLogic.lua" and function_name.startswith("CM_GongFa"):
        return "client_request"
    if file_name == "GongFaNewNetLogic.lua" and function_name.startswith("SM_GongFa"):
        return "netlogic_response_dispatch"
    if file_name == "GongFaNewModel.lua" and function_name in {"GongFaLearn", "UpgradeRefresh"}:
        return "single_gongfa_attr_apply"
    if file_name == "GongFaNewModel.lua" and function_name == "GongFaUpgradeTimes":
        return "batch_gongfa_attr_apply"
    if file_name == "GongFaNewModel.lua" and function_name == "GongFaUpgrade":
        return "upgrade_response_router"
    return "other"


def _gongfa_attr_change_packet_for_function(function_name: str) -> str:
    if function_name == "CM_GongFaLearnFun":
        return "CM_GongFaLearn"
    if function_name == "SM_GongFaLearnFun" or function_name == "GongFaLearn":
        return "SM_GongFaLearn"
    if function_name == "CM_GongFaUpgrade":
        return "CM_GongFaUpgrade"
    if function_name in {"SM_GongFaUpgradeFun", "GongFaUpgrade", "UpgradeRefresh"}:
        return "SM_GongFaUpgrade"
    if function_name == "CM_GongFaUpgradeTimesFun":
        return "CM_GongFaUpgradeTimes"
    if function_name in {"SM_GongFaUpgradeTimesFun", "GongFaUpgradeTimes"}:
        return "SM_GongFaUpgradeTimes"
    return ""


def _gongfa_attr_change_line_kind(file_name: str, function_name: str, line: str) -> tuple[str, str]:
    stripped = line.strip()
    if file_name == "GongFaNewNetLogic.lua" and function_name == "LuaGongFaNewNetLogic":
        registrations = {
            "_SM_GongFaLearn": ("register_gongfa_learn_response", "注册功法学习响应包。"),
            "_SM_GongFaUpgradeTimes": ("register_gongfa_upgrade_times_response", "注册批量升级响应包。"),
            "_SM_GongFaUpgrade": ("register_gongfa_upgrade_response", "注册功法升级响应包。"),
            "_CM_GongFaLearn": ("register_gongfa_learn_request", "注册功法学习请求包。"),
            "_CM_GongFaUpgradeTimes": ("register_gongfa_upgrade_times_request", "注册批量升级请求包。"),
            "_CM_GongFaUpgrade": ("register_gongfa_upgrade_request", "注册功法升级请求包。"),
        }
        if "F_Register" in stripped:
            for token, result in registrations.items():
                if token in stripped:
                    return result
    if file_name == "GongFaNewNetLogic.lua" and function_name in {"CM_GongFaLearnFun", "CM_GongFaUpgrade", "CM_GongFaUpgradeTimesFun"}:
        if stripped.startswith("function "):
            return "client_request_decl", "客户端请求函数。"
        if "SocketManager.Inst_get():GetMessageFromPools" in stripped:
            return "create_request_message", "从消息池创建请求对象。"
        if re.search(r"CM_GongFa(?:Learn|Upgrade|UpgradeTimes)\.", stripped):
            return "fill_request_field", "写入请求字段。"
        if "SocketManager.Inst_get():F_SendMsg" in stripped:
            return "send_request", "发送功法请求到服务端。"
    if file_name == "GongFaNewNetLogic.lua" and function_name in {
        "SM_GongFaLearnFun",
        "SM_GongFaUpgradeFun",
        "SM_GongFaUpgradeTimesFun",
    }:
        if stripped.startswith("function "):
            return "response_handler_decl", "服务端响应入口。"
        if "Model:GongFaLearn(msg)" in stripped:
            return "dispatch_learn_to_model", "把学习回包交给 GongFaNewModel:GongFaLearn。"
        if "Model:GongFaUpgrade(msg)" in stripped:
            return "dispatch_upgrade_to_model", "把升级回包交给 GongFaNewModel:GongFaUpgrade。"
        if "Model:GongFaUpgradeTimes(msg)" in stripped:
            return "dispatch_upgrade_times_to_model", "把批量升级回包交给 GongFaNewModel:GongFaUpgradeTimes。"
    if file_name == "GongFaNewModel.lua" and function_name == "GongFaUpgrade":
        if stripped.startswith("function "):
            return "upgrade_router_decl", "升级回包总入口。"
        if "SaveUpgradeQualityData(msg)" in stripped:
            return "save_upgrade_quality_data", "升品确认分支先缓存服务端回包。"
        if "self:UpgradeRefresh(msg)" in stripped:
            return "route_upgrade_refresh", "普通升级分支进入 UpgradeRefresh 应用属性变化。"
    if file_name == "GongFaNewModel.lua" and function_name == "UpgradeRefresh":
        if stripped.startswith("function "):
            return "upgrade_refresh_decl", "单次升级应用入口。"
        if "UpdateGongFaVo(msg.gongfa)" in stripped:
            return "update_gongfa_vo", "写入升级后的 GongFaItemVO。"
        if "Cipairs(msg.rewardResults)" in stripped:
            return "scan_reward_results_for_exp", "扫描 rewardResults 提取 EXP。"
        if "GameUtil.DealAttrChangeByModule(msg.attrs,exp)" in stripped:
            return "apply_upgrade_attrs", "把 SM_GongFaUpgrade.attrs 交给角色属性通用写入链。"
        if "RaiseEvent(GongFaNewType.ChangeGongFa" in stripped:
            return "raise_gongfa_change_event", "属性/功法数据更新后刷新功法 UI。"
    if file_name == "GongFaNewModel.lua" and function_name == "GongFaLearn":
        if stripped.startswith("function "):
            return "gongfa_learn_decl", "功法学习应用入口。"
        if "UpdateGongFaVo(msg.gongfa)" in stripped:
            return "update_learned_gongfa_vo", "写入学习后的 GongFaItemVO。"
        if "Cipairs(msg.rewardResults)" in stripped:
            return "scan_learn_reward_results_for_exp", "扫描学习奖励提取 EXP。"
        if "GameUtil.DealAttrChangeByModule(msg.attrs,exp)" in stripped:
            return "apply_learn_attrs", "把 SM_GongFaLearn.attrs 交给角色属性通用写入链。"
        if "RaiseEvent(GongFaNewType.ChangeGongFa" in stripped:
            return "raise_learn_change_event", "学习成功后刷新功法 UI。"
    if file_name == "GongFaNewModel.lua" and function_name == "GongFaUpgradeTimes":
        if stripped.startswith("function "):
            return "upgrade_times_decl", "批量升级应用入口。"
        if "ChangedAttrsVo.new()" in stripped:
            return "create_aggregate_attrs_vo", "创建聚合 ChangedAttrsVo。"
        if "Cipairs(msg.upgradeList)" in stripped:
            return "iterate_upgrade_result_list", "遍历服务端批量升级结果列表。"
        if "UpdateGongFaVo(v.gongfa)" in stripped:
            return "update_batch_gongfa_vo", "逐个写入升级后的 GongFaItemVO。"
        if "Cipairs(v.rewardResults)" in stripped:
            return "scan_batch_reward_results_for_exp", "聚合批量升级经验奖励。"
        if "Kpairs(v.attrs.addAttrs)" in stripped:
            return "iterate_batch_add_attrs", "遍历单次升级的 addAttrs。"
        if "allAttrs.addAttrs:LuaDic_AddOrSetItem" in stripped:
            return "merge_batch_add_attr", "把多次 addAttrs 累加到聚合 VO。"
        if "allAttrs.finalAttrs=v.attrs.finalAttrs" in stripped:
            return "take_last_final_attrs", "把最后一次升级的 finalAttrs 作为最终属性值。"
        if "GameUtil.DealAttrChangeByModule(allAttrs" in stripped:
            return "apply_batch_upgrade_attrs", "把聚合 ChangedAttrsVo 交给角色属性通用写入链。"
    return "", ""


def _gongfa_attr_change_field_refs(function_name: str, line: str) -> str:
    refs: list[str] = []
    packet_name = _gongfa_attr_change_packet_for_function(function_name)
    for match in re.finditer(r"\bmsg\.([A-Za-z0-9_]+)", line):
        refs.append(f"{packet_name}.{match.group(1)}" if packet_name else f"msg.{match.group(1)}")
    for match in re.finditer(r"\bv\.([A-Za-z0-9_]+)", line):
        if function_name == "GongFaUpgradeTimes":
            refs.append(f"SM_GongFaUpgradeTimes.upgradeList[].{match.group(1)}")
    for token in ("addAttrs", "finalAttrs", "ChangedAttrsVo"):
        if token in line:
            refs.append(f"ChangedAttrsVo.{token}" if token != "ChangedAttrsVo" else "ChangedAttrsVo")
    if "GameUtil.DealAttrChangeByModule" in line:
        refs.append("GameUtil.DealAttrChangeByModule")
    return _join_unique(refs, limit=40)


def _build_gongfa_attr_change_rows(
    root: Path,
    all_fields_by_packet_name: dict[str, list[dict[str, str]]],
    all_packet_by_name: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for packet_name in sorted(_GONGFA_ATTR_CHANGE_PACKET_NAMES):
        packet = all_packet_by_name.get(packet_name, {})
        fields = all_fields_by_packet_name.get(packet_name, [])
        if not packet and not fields:
            continue
        for field in fields:
            field_name = field.get("field_name") or ""
            rows.append(
                {
                    "flow_stage": "packet_schema" if packet_name != "ChangedAttrsVo" else "changed_attrs_vo_schema",
                    "row_kind": "packet_field",
                    "packet_name": packet_name,
                    "source_file": packet.get("relative_path") or field.get("relative_path") or "",
                    "file_name": packet.get("file") or field.get("file") or "",
                    "function_name": "reading",
                    "line": field.get("line") or "",
                    "field_refs": f"{packet_name}.{field_name}" if field_name else "",
                    "runtime_effect": _GONGFA_ATTR_CHANGE_FIELD_SEMANTICS.get((packet_name, field_name), ""),
                    "authority_note": "协议字段定义；功法学习/升级的属性变化最终复用 ChangedAttrsVo -> GameUtil。",
                    "code": f"{field_name}:{field.get('read_method') or ''}"
                    + (f"<{field.get('type_hint')}>" if field.get("type_hint") else ""),
                }
            )

    search_root = root / "by_source" / "lscripts"
    if not search_root.is_dir():
        return rows
    for path in sorted(search_root.glob("**/text_assets/*.lua")):
        if path.name not in _GONGFA_ATTR_CHANGE_FILE_NAMES:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if not any(term in text for term in ("SM_GongFaLearn", "SM_GongFaUpgrade", "DealAttrChangeByModule")):
            continue
        lines = text.splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            row_kind, runtime_effect = _gongfa_attr_change_line_kind(path.name, function_name, line)
            if not row_kind:
                continue
            rows.append(
                {
                    "flow_stage": _gongfa_attr_change_stage(path.name, function_name),
                    "row_kind": row_kind,
                    "packet_name": _gongfa_attr_change_packet_for_function(function_name),
                    "source_file": _relative_to_root(path, root),
                    "file_name": path.name,
                    "function_name": function_name,
                    "line": line_no,
                    "field_refs": _gongfa_attr_change_field_refs(function_name, line),
                    "runtime_effect": runtime_effect,
                    "authority_note": "可见 Lua 证据；这里记录功法回包如何进入角色属性写入链。",
                    "code": line.strip()[:300],
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    return rows


def _gongfa_state_stage(file_name: str, function_name: str) -> str:
    if file_name in {"CM_GongFaView.lua", "SM_GongFaView.lua", "SimpleItemVO.lua", "GongFaItemVO.lua"}:
        return "packet_schema_source"
    if file_name == "GongFaNewNetLogic.lua":
        if function_name == "LuaGongFaNewNetLogic":
            return "netlogic_registration"
        if function_name == "CM_GongFaView":
            return "view_request"
        if function_name == "SM_GongFaViewFun":
            return "view_response_dispatch"
    if file_name == "GongFaNewModel.lua":
        if function_name == "SetGongFaInfo":
            return "model_view_apply"
        if function_name == "SetGongFaVo":
            return "model_vo_list_apply"
        if function_name in {"GongFaLearn", "GongFaUpgrade", "UpgradeRefresh", "GongFaUpgradeTimes"}:
            return "model_incremental_update"
    if file_name == "GongFaNewData.lua":
        if function_name in {"LuaGongFaNewData", "SetGongFaDic"}:
            return "data_static_catalog_init"
        if function_name in {"SetGongFaInfo", "SetXinFaInfo", "SetLearnedSkillList", "SetGongFaProgram"}:
            return "data_view_state_store"
        if function_name in {"SetGongFaVo", "UpdateGongFaVo", "UpdateGongFaVoEx"}:
            return "data_vo_overlay"
        if function_name in {"GetGongFaById", "GetAllGongFa", "GetTypeGongFa"}:
            return "data_query_api"
        if function_name == "UpdateActiveId":
            return "data_active_update"
    if file_name == "GongFaVo.lua":
        return "client_vo_wrapper"
    return "other"


def _gongfa_state_packet_for_function(function_name: str) -> str:
    if function_name == "CM_GongFaView":
        return "CM_GongFaView"
    if function_name == "SM_GongFaViewFun":
        return "SM_GongFaView"
    if function_name in {"GongFaLearn"}:
        return "SM_GongFaLearn"
    if function_name in {"GongFaUpgrade", "UpgradeRefresh"}:
        return "SM_GongFaUpgrade"
    if function_name == "GongFaUpgradeTimes":
        return "SM_GongFaUpgradeTimes"
    if function_name in {"SetGongFaVo", "UpdateGongFaVo", "UpdateGongFaVoEx"}:
        return "GongFaItemVO"
    return ""


def _gongfa_state_line_kind(file_name: str, function_name: str, line: str) -> tuple[str, str]:
    stripped = line.strip()
    if file_name == "GongFaNewNetLogic.lua" and function_name == "LuaGongFaNewNetLogic":
        if "_CM_GongFaView:getId()" in stripped and "F_Register" in stripped:
            return "register_view_request", "注册 CM_GongFaView 请求包。"
        if "_SM_GongFaView:getId()" in stripped and "F_Register" in stripped:
            return "register_view_response", "注册 SM_GongFaView 回包处理函数。"
        if "self.SM_GongFaViewFun(msg)" in stripped:
            return "route_view_response_handler", "SM_GongFaView 回包进入 SM_GongFaViewFun。"
    if file_name == "GongFaNewNetLogic.lua" and function_name == "CM_GongFaView":
        if stripped.startswith("function "):
            return "request_view_decl", "功法页状态请求入口；客户端只发空请求。"
        if "GetMessageFromPools(_CM_GongFaView)" in stripped:
            return "build_view_request", "从消息池取 CM_GongFaView。"
        if "F_SendMsg(CM_GongFaView" in stripped:
            return "send_view_request", "发送 CM_GongFaView。"
    if file_name == "GongFaNewNetLogic.lua" and function_name == "SM_GongFaViewFun":
        if stripped.startswith("function "):
            return "view_response_handler_decl", "功法页状态回包入口。"
        if "msg.code==0" in stripped:
            return "guard_success_code", "仅在服务端成功码下应用状态。"
        if "Model:SetGongFaInfo(msg)" in stripped:
            return "dispatch_view_to_model", "把 SM_GongFaView 交给 GongFaNewModel:SetGongFaInfo。"
        if "AddEventListeners()" in stripped:
            return "attach_runtime_listeners", "状态初始化后挂载事件监听。"

    if file_name == "GongFaNewModel.lua" and function_name == "SetGongFaInfo":
        if stripped.startswith("function "):
            return "model_set_view_info_decl", "Model 接收 SM_GongFaView 的整体状态。"
        if "SetGongFaInfo(info.actives)" in stripped:
            return "store_active_map", "保存 SM_GongFaView.actives 到 GongFaNewData。"
        if "SetXinFaInfo(info.xinFaPutUpList)" in stripped:
            return "store_xinfa_putup_list", "保存心法上阵列表。"
        if "SetLearnedSkillList(info.skillList" in stripped:
            return "store_learned_skill_list", "保存已学习功法技能列表。"
        if "SetGongFaProgram(info.programVOList)" in stripped:
            return "store_program_list", "保存功法方案列表。"
        if "SaveFazePutUpPanelData(info.fazePutUpList)" in stripped:
            return "store_faze_putup_list", "保存法则/法则上阵列表。"
        if "RefreshAllRed()" in stripped or "RefreshNewRed()" in stripped or "RefreshGongFaDataEx" in stripped:
            return "refresh_after_view_state", "状态落地后刷新红点/界面事件。"
    if file_name == "GongFaNewModel.lua" and function_name == "SetGongFaVo":
        if stripped.startswith("function "):
            return "model_set_vo_list_decl", "Model 提供批量 GongFaItemVO 覆盖入口。"
        if "GongFaNewData:SetGongFaVo(infoList)" in stripped:
            return "dispatch_vo_list_to_data", "把 GongFaItemVO 列表交给 Data 层覆盖。"
    if file_name == "GongFaNewModel.lua" and function_name in {"GongFaLearn", "UpgradeRefresh", "GongFaUpgradeTimes"}:
        if "UpdateGongFaVo(msg.gongfa)" in stripped:
            return "update_single_gongfa_vo", "学习/单次升级回包用 msg.gongfa 覆盖单个功法状态。"
        if "UpdateGongFaVo(v.gongfa)" in stripped:
            return "update_batch_gongfa_vo", "批量升级逐个用 v.gongfa 覆盖功法状态。"
        if "SetUpGongFaId(msg.gongfa.baseId)" in stripped:
            return "remember_updated_gongfa_id", "记录刚更新的功法 baseId。"

    if file_name == "GongFaNewData.lua" and function_name == "LuaGongFaNewData":
        if "self.gongFaDic=Dictionary.new()" in stripped:
            return "init_gongfa_dictionary", "初始化功法图鉴运行字典。"
        if "ConfigName.Gongfa_Gongfa)" in stripped:
            return "load_gongfa_config", "读取 Gongfa_Gongfa 静态功法表。"
        if "ConfigName.Gongfa_GongfaPin)" in stripped:
            return "load_gongfa_quality_config", "读取 Gongfa_GongfaPin 品质/类型表。"
        if "self:SetGongFaDic()" in stripped:
            return "build_static_catalog_call", "启动静态功法图鉴字典构建。"
    if file_name == "GongFaNewData.lua" and function_name == "SetGongFaDic":
        if stripped.startswith("function "):
            return "build_static_catalog_decl", "从本地配置构造完整功法图鉴底座。"
        if "for k,v in pairs(self.gongFaCfg)" in stripped:
            return "iterate_static_gongfa_config", "遍历 Gongfa_Gongfa 静态配置。"
        if "GongFaVo.new(v)" in stripped:
            return "create_gongfa_vo_from_config", "每条静态配置创建一个 GongFaVo。"
        if "LuaDic_AddOrSetItem(v.id,gongFaVo)" in stripped:
            return "store_static_gongfa_vo", "按 Gongfa_Gongfa.id 存入 gongFaDic。"
        if "table.insert(self.tbTypeGongFa" in stripped:
            return "index_gongfa_by_type_quality", "按类型/品质建立图鉴分类索引。"
    if file_name == "GongFaNewData.lua" and function_name == "SetGongFaInfo":
        if stripped.startswith("function "):
            return "data_store_actives_decl", "Data 层保存 SM_GongFaView.actives。"
        if "self.actives=actives" in stripped:
            return "store_actives_direct", "直接保存服务端 active 字典。"
    if file_name == "GongFaNewData.lua" and function_name == "SetGongFaProgram":
        if "programDic:LuaDic_Clear()" in stripped:
            return "clear_program_dict", "重建功法方案字典前清空旧数据。"
        if "programDic:LuaDic_AddOrSetItem(v.id,v)" in stripped:
            return "store_program_by_id", "按方案 id 存储 programVO。"
    if file_name == "GongFaNewData.lua" and function_name == "SetXinFaInfo":
        if "self.xinFaPutUpList==nil" in stripped:
            return "init_xinfa_slot_list", "首次初始化心法槽位列表。"
        if "GongFaItemDataList" in stripped:
            return "build_default_xinfa_slots", "按本地战斗槽配置补齐默认心法槽。"
        if "value.xinFaId=v.xinFaId" in stripped:
            return "merge_server_xinfa_slot", "用服务端 xinFaPutUpList 覆盖对应槽位。"
    if file_name == "GongFaNewData.lua" and function_name == "SetLearnedSkillList":
        if "self.NewLearnedSkill:Add(cfg.id)" in stripped:
            return "record_new_learned_skill", "记录新增学习技能。"
        if "self.OldLearnedSkill:Add(cfg.id)" in stripped:
            return "store_learned_skill_id", "保存服务端 skillList 对应的配置技能 id。"
    if file_name == "GongFaNewData.lua" and function_name == "SetGongFaVo":
        if stripped.startswith("function "):
            return "data_set_vo_list_decl", "Data 层批量覆盖已学习功法 VO。"
        if "v:SetVo(nil)" in stripped:
            return "clear_old_gongfa_vo_overlay", "批量覆盖前清掉旧的已学习状态。"
        if "self:UpdateGongFaVo(v)" in stripped:
            return "apply_vo_list_item", "逐个应用服务端 GongFaItemVO。"
    if file_name == "GongFaNewData.lua" and function_name == "UpdateGongFaVo":
        if stripped.startswith("function "):
            return "data_update_vo_decl", "Data 层覆盖单个 GongFaItemVO。"
        if "LuaDic_GetItem(data.baseId)" in stripped:
            return "lookup_gongfa_by_base_id", "按 GongFaItemVO.baseId 定位本地图鉴项。"
        if "gongFaVo:SetVo(data)" in stripped:
            return "overlay_server_vo", "把服务端 GongFaItemVO 存入 GongFaVo.vo。"
        if "GongFaVo.new(cfg)" in stripped:
            return "create_missing_vo_from_config", "本地缺项时用静态配置补建 GongFaVo。"
    if file_name == "GongFaNewData.lua" and function_name in {"GetGongFaById", "GetAllGongFa", "GetTypeGongFa"}:
        if stripped.startswith("function "):
            return "query_api_decl", "对外提供图鉴字典查询入口。"
        if "return self.gongFaDic" in stripped:
            return "return_all_gongfa_dictionary", "返回完整功法字典。"
        if "LuaDic_GetItem(id)" in stripped:
            return "return_gongfa_by_id", "按 id 返回单个 GongFaVo。"
    if file_name == "GongFaNewData.lua" and function_name == "UpdateActiveId":
        if "self.actives=Dictionary.new()" in stripped:
            return "ensure_active_dictionary", "没有 active 字典时创建。"
        if "LuaDic_AddOrSetItem(msg.starId,msg.jie)" in stripped:
            return "update_active_jie_by_star_id", "把 starId -> jie 写入 active 字典。"

    if file_name == "GongFaVo.lua":
        if stripped.startswith("function _M._init_"):
            return "gongfa_vo_init_decl", "客户端包装对象持有静态 cfg 与服务端 vo。"
        if "self.cfg=cfg" in stripped:
            return "store_static_cfg_in_vo", "保存静态 Gongfa_Gongfa 配置。"
        if "self.vo=nil" in stripped:
            return "init_server_vo_empty", "未学习时服务端状态为空。"
        if stripped.startswith("function _M.SetVo"):
            return "set_server_vo_decl", "写入服务端 GongFaItemVO。"
        if "self.vo=vo" in stripped:
            return "store_server_vo_in_wrapper", "把服务端 GongFaItemVO 保存到 wrapper。"
    if file_name == "SimpleItemVO.lua":
        if re.search(r"self\.(baseId|id|num)", stripped):
            return "simple_item_vo_field", "GongFaItemVO 继承的 SimpleItemVO 字段；baseId 是功法状态覆盖主键。"
    if file_name == "GongFaItemVO.lua":
        if "class(SimpleItemVO" in stripped:
            return "inherit_simple_item_vo", "GongFaItemVO 继承 SimpleItemVO，因此额外带 baseId/id/num。"
        if "_super_.reading(self)" in stripped:
            return "read_inherited_simple_item_fields", "读取 GongFaItemVO 自有字段后继续读取 SimpleItemVO.baseId/id/num。"
        if "_super_.writing(self)" in stripped:
            return "write_inherited_simple_item_fields", "写出 GongFaItemVO 自有字段后继续写出 SimpleItemVO.baseId/id/num。"
        if re.search(r"self\.(grade|jie|star|pin|tongxuan|quality|totalExp|qualityNum)", stripped):
            return "gongfa_item_vo_field", "GongFaItemVO 可见状态字段定义/读写。"
    return "", ""


def _gongfa_state_field_refs(function_name: str, line: str) -> str:
    refs: list[str] = []
    packet_name = _gongfa_state_packet_for_function(function_name)
    for field in (
        "actives",
        "xinFaPutUpList",
        "fazePutUpList",
        "skillList",
        "programVOList",
        "gongfa",
        "upgradeList",
        "baseId",
    ):
        if field in line:
            if packet_name:
                refs.append(f"{packet_name}.{field}")
            else:
                refs.append(field)
    if "Gongfa_Gongfa)" in line:
        refs.append("ConfigName.Gongfa_Gongfa")
    if "Gongfa_GongfaPin)" in line:
        refs.append("ConfigName.Gongfa_GongfaPin")
    if "GongFaItemVO" in line:
        refs.append("GongFaItemVO")
    if "GongFaVo" in line:
        refs.append("GongFaVo")
    if "SimpleItemVO" in line or "_super_.reading" in line or "_super_.writing" in line:
        refs.append("SimpleItemVO.baseId/id/num")
    for field in ("baseId", "id", "num"):
        if f"self.{field}" in line and function_name in {"_init_", "reading", "writing"}:
            refs.append(f"SimpleItemVO.{field}")
    return _join_unique(refs, limit=40)


def _build_gongfa_state_rows(
    root: Path,
    all_fields_by_packet_name: dict[str, list[dict[str, str]]],
    all_packet_by_name: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for packet_name in sorted(_GONGFA_STATE_PACKET_NAMES):
        packet = all_packet_by_name.get(packet_name, {})
        fields = all_fields_by_packet_name.get(packet_name, [])
        if not packet and not fields:
            continue
        if not fields:
            rows.append(
                {
                    "flow_stage": "packet_schema",
                    "row_kind": "packet_no_field",
                    "packet_name": packet_name,
                    "source_file": packet.get("relative_path") or "",
                    "file_name": packet.get("file") or "",
                    "function_name": "reading",
                    "line": "",
                    "field_refs": packet_name,
                    "runtime_effect": "无自有字段；用于触发服务端下发功法页状态。",
                    "authority_note": "协议定义；功法图鉴状态的请求/回包入口。",
                    "code": "",
                }
            )
            continue
        for field in fields:
            field_name = field.get("field_name") or ""
            rows.append(
                {
                    "flow_stage": (
                        "simple_item_vo_schema"
                        if packet_name == "SimpleItemVO"
                        else "gongfa_item_vo_schema"
                        if packet_name == "GongFaItemVO"
                        else "packet_schema"
                    ),
                    "row_kind": "packet_field",
                    "packet_name": packet_name,
                    "source_file": packet.get("relative_path") or field.get("relative_path") or "",
                    "file_name": packet.get("file") or field.get("file") or "",
                    "function_name": "reading",
                    "line": field.get("line") or "",
                    "field_refs": f"{packet_name}.{field_name}" if field_name else "",
                    "runtime_effect": _GONGFA_STATE_FIELD_SEMANTICS.get((packet_name, field_name), ""),
                    "authority_note": "协议字段定义；用于理解功法图鉴状态、已学习 VO 和增量更新。",
                    "code": f"{field_name}:{field.get('read_method') or ''}"
                    + (f"<{field.get('type_hint')}>" if field.get("type_hint") else ""),
                }
            )

    search_root = root / "by_source" / "lscripts"
    if not search_root.is_dir():
        return rows
    for path in sorted(search_root.glob("**/text_assets/*.lua")):
        if path.name not in _GONGFA_STATE_FILE_NAMES:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if not any(
            term in text
            for term in (
                "GongFaView",
                "SimpleItemVO",
                "GongFaItemVO",
                "GongFaVo",
                "Gongfa_Gongfa",
                "SetGongFaInfo",
                "SetGongFaVo",
                "UpdateGongFaVo",
            )
        ):
            continue
        lines = text.splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            row_kind, runtime_effect = _gongfa_state_line_kind(path.name, function_name, line)
            if not row_kind:
                continue
            rows.append(
                {
                    "flow_stage": _gongfa_state_stage(path.name, function_name),
                    "row_kind": row_kind,
                    "packet_name": _gongfa_state_packet_for_function(function_name),
                    "source_file": _relative_to_root(path, root),
                    "file_name": path.name,
                    "function_name": function_name,
                    "line": line_no,
                    "field_refs": _gongfa_state_field_refs(function_name, line),
                    "runtime_effect": runtime_effect,
                    "authority_note": "可见 Lua 证据；这里记录功法图鉴静态配置和服务端状态如何合并。",
                    "code": line.strip()[:300],
                }
            )
    set_vo_callsite_count = 0
    for path in sorted(search_root.glob("**/text_assets/*.lua")):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if "SetGongFaVo(" not in text:
            continue
        lines = text.splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            stripped = line.strip()
            if "SetGongFaVo(" not in stripped:
                continue
            function_name = _function_for_line(spans, line_no)
            if stripped.startswith("function ") or function_name == "SetGongFaVo":
                continue
            set_vo_callsite_count += 1
            rows.append(
                {
                    "flow_stage": "visible_callsite_probe",
                    "row_kind": "set_gongfa_vo_callsite",
                    "packet_name": "GongFaItemVO",
                    "source_file": _relative_to_root(path, root),
                    "file_name": path.name,
                    "function_name": function_name,
                    "line": line_no,
                    "field_refs": "GongFaItemVO",
                    "runtime_effect": "可见 Lua 中调用批量 GongFaItemVO 覆盖入口。",
                    "authority_note": "全量 Lua 文本探查；用于确认已学习功法批量状态是否有 Lua 调用点。",
                    "code": stripped[:300],
                }
            )
    if set_vo_callsite_count == 0:
        rows.append(
            {
                "flow_stage": "visible_callsite_probe",
                "row_kind": "visible_gap_no_set_gongfa_vo_caller",
                "packet_name": "GongFaItemVO",
                "source_file": "",
                "file_name": "",
                "function_name": "",
                "line": "",
                "field_refs": "GongFaItemVO.baseId;GongFaNewModel:SetGongFaVo",
                "runtime_effect": "当前可读 Lua 未发现 Model:SetGongFaVo(infoList) 的外部调用点；批量初始化来源可能在原生/背包系统或未导出脚本，已确认的 Lua 增量路径是学习/升级回包 UpdateGongFaVo。",
                "authority_note": "缺口行；表示本轮静态导出未覆盖该调用来源，不代表运行时不存在。",
                "code": "",
            }
        )
    rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    return rows


def _gongfa_attr_display_stage(file_name: str, function_name: str) -> str:
    if file_name == "GongFaNewModel.lua" and function_name in {
        "GetAllAddAttrTb",
        "GetLevelAndStarAttr",
        "GetIngoreSpecialAttrNextAdd",
        "GetAllAttrNextAttr",
        "GetAllAttrNextAdd",
        "FormatSpAttr",
    }:
        return "model_attr_display_helpers"
    if file_name == "GongFaNewData.lua" and function_name in {"GetViewAttrListShow", "GetTongXuanDesInfo"}:
        return "data_attr_display_helpers"
    if file_name == "GongFaNewMgr.lua" and function_name in {
        "FormatAttrNum",
        "GetGongFaAttrListByAttr",
        "GetGongFaAttrShow",
    }:
        return "mgr_attr_display_helpers"
    if file_name == "DetailPanel.lua":
        return "ui_detail_static_attr"
    if file_name == "GongFaAttrItem.lua":
        return "ui_attr_item_format"
    if file_name == "GongFaUpLevelView.lua":
        return "ui_level_preview"
    if file_name in {
        "GongFaChangeQualityView.lua",
        "GongFaJieFinishView.lua",
        "GongFaTongXuanView.lua",
        "GongFaUpJieView.lua",
        "GongFaUpPinView.lua",
        "GongFaUpStarView.lua",
        "PracticeTogetherGongFaView.lua",
    }:
        return "ui_other_preview"
    return "other"


def _gongfa_attr_display_line_kind(file_name: str, function_name: str, line: str) -> tuple[str, str]:
    stripped = line.strip()
    if file_name == "DetailPanel.lua":
        if "GetAllAddAttrTb(gongFaVo.cfg.attr" in stripped or "GetAllAddAttrTb(self.gongFaVo.cfg.attr" in stripped:
            return "detail_static_attr_entry", "详情面板直接把静态 Gongfa_Gongfa.attr 转为可展示属性列表。"

    if file_name == "GongFaNewModel.lua" and function_name == "GetAllAddAttrTb":
        if stripped.startswith("function "):
            return "attr_list_helper_decl", "把属性 map 转成按配置排序的展示项。"
        if "GetConfigTableByKeyAndIdWithLog(ConfigName.Attribute_Attribute" in stripped:
            return "filter_attr_map_by_attribute_config", "逐项用 Attribute_Attribute 查属性元数据。"
        if "attrCfg.group" in stripped and "GameDefine.AttrType.Indirect" in stripped:
            return "filter_indirect_attr_group", "默认只保留间接属性组，除非调用方要求不忽略。"
        if "param.cfg=attrCfg" in stripped or "param.num=v" in stripped:
            return "current_attr_to_display_param", "把当前属性值和属性配置包装成 UI 展示参数。"
        if "table.insert(addAttr,param)" in stripped:
            return "append_attr_display_param", "把属性展示参数加入结果列表。"
        if "table.sort(addAttr" in stripped:
            return "sort_attr_by_config_sort", "按 Attribute_Attribute.sort 排序展示。"

    if file_name == "GongFaNewModel.lua" and function_name == "GetLevelAndStarAttr":
        if stripped.startswith("function "):
            return "merge_level_star_attr_decl", "合并等级属性和星级属性。"
        if "retTb[k]=v" in stripped:
            return "copy_level_attr", "先拷贝等级属性到临时属性 map。"
        if "retTb[k]=retTb[k]+v" in stripped or "retTb[k]=v" in stripped:
            return "merge_star_attr", "把星级属性累加到同一属性 key 上。"

    if file_name == "GongFaNewModel.lua" and function_name == "GetIngoreSpecialAttrNextAdd":
        if stripped.startswith("function "):
            return "ignore_special_next_add_decl", "下一阶预览入口，可屏蔽特殊属性。"
        if "GetSpecialAttrTypeEx" in stripped or "GetSpecialAttrType" in stripped:
            return "choose_special_ignore_set", "按调用场景选择要忽略的特殊属性集合。"
        if "GetAllAttrNextAdd" in stripped:
            return "preview_next_attr_dispatch", "把当前/下一阶属性 map 交给差值计算函数。"

    if file_name == "GongFaNewModel.lua" and function_name == "GetAllAttrNextAttr":
        if stripped.startswith("function "):
            return "next_attr_filter_decl", "构造仅展示增长项的下一阶属性预览。"
        if "newIgnore[k]=true" in stripped:
            return "ignore_non_increasing_next_attr", "下一阶没有增长的属性会被忽略。"
        if "GetAllAttrNextAdd" in stripped:
            return "preview_next_attr_dispatch", "进入统一的当前/下一阶差值计算。"

    if file_name == "GongFaNewModel.lua" and function_name == "GetAllAttrNextAdd":
        if stripped.startswith("function "):
            return "next_attr_delta_decl", "核心属性预览函数：比较当前值和下一阶值。"
        if "nextAttr==nil" in stripped or "not isLearn" in stripped:
            return "preview_without_next_branch", "未学习或没有下一阶配置时只展示当前属性。"
        if "GetConfigTableByKeyAndIdWithLog(ConfigName.Attribute_Attribute" in stripped:
            return "preview_lookup_attribute_config", "差值预览同样依赖 Attribute_Attribute 元数据。"
        if "local addNum=" in stripped or ".addNum=addNum" in stripped or "data.addNum" in stripped:
            return "compute_attr_add_num", "计算下一阶相对当前阶的增长值。"
        if ".isNew=true" in stripped or "isNew=true" in stripped:
            return "mark_new_attr", "下一阶新增属性会标记 isNew。"
        if "param.cfg=attrCfg" in stripped or "param.num=v" in stripped:
            return "current_attr_to_display_param", "包装当前属性值和属性配置。"
        if "table.sort(addAttr" in stripped:
            return "sort_attr_by_config_sort", "按 Attribute_Attribute.sort 排序展示。"

    if file_name == "GongFaNewData.lua" and function_name == "GetViewAttrListShow":
        if stripped.startswith("function "):
            return "view_attr_union_decl", "组合当前属性和下一阶属性供界面展示。"
        if "curAttr" in stripped and "nextAttr" in stripped and ("showAttrList" in stripped or "attrList" in stripped):
            return "view_attr_union_keys", "取当前/下一阶属性 key 的并集作为展示候选。"
        if "GetConfigTableByKeyAndIdWithLog(ConfigName.Attribute_Attribute" in stripped:
            return "view_attr_lookup_config", "展示列表逐项查 Attribute_Attribute 配置。"
        if "nextValue" in stripped or "addNum" in stripped:
            return "view_attr_compute_delta", "记录下一阶值与增长值。"
        if "isNew" in stripped:
            return "view_attr_mark_new", "记录下一阶新增属性标记。"
    if file_name == "GongFaNewData.lua" and function_name == "GetTongXuanDesInfo":
        if "Gongfa_GongfaJie_Simple" in stripped or "mainDescribe" in stripped:
            return "tongxuan_description_config", "通玄描述来自 Gongfa_GongfaJie_Simple.mainDescribe。"

    if file_name == "GongFaNewMgr.lua" and function_name == "FormatAttrNum":
        if stripped.startswith("function "):
            return "format_attr_num_decl", "属性数值格式化入口。"
        if "ConvertBigDouble" in stripped or any(token in stripped for token in ("Value", "Indirect", "IndirectFormula")):
            return "format_attr_value_number", "Value/Indirect/IndirectFormula 类属性按普通数值显示。"
        if "%" in stripped or any(token in stripped for token in ("RatioAttribute", "Ratio")):
            return "format_attr_value_ratio", "Ratio/RatioAttribute 类属性按百分比显示。"
    if file_name == "GongFaNewMgr.lua" and function_name == "GetGongFaAttrListByAttr":
        if "Attribute_Attribute" in stripped or "LuaEntityPropertyType" in stripped:
            return "mgr_attr_list_lookup_config", "把属性 key 映射到 Attribute_Attribute 配置并排序。"
        if "table.sort" in stripped:
            return "mgr_attr_list_sort", "按配置排序功法属性列表。"
    if file_name == "GongFaNewMgr.lua" and function_name == "GetGongFaAttrShow":
        if "addValue" in stripped or "nextAttr" in stripped or "ShowAttrList" in stripped:
            return "mgr_show_attr_delta", "构造当前/下一阶展示项和增长值。"

    if file_name == "GongFaUpLevelView.lua":
        if "GetLevelAndStarAttr" in stripped:
            return "ui_level_preview_merge", "升级面板先合并等级/星级属性。"
        if "GetIngoreSpecialAttrNextAdd" in stripped:
            return "ui_level_preview_display", "升级面板调用下一阶差值预览。"
    if file_name in {
        "GongFaChangeQualityView.lua",
        "GongFaJieFinishView.lua",
        "GongFaTongXuanView.lua",
        "GongFaUpJieView.lua",
        "GongFaUpPinView.lua",
        "GongFaUpStarView.lua",
        "PracticeTogetherGongFaView.lua",
    } and any(
        token in stripped
        for token in ("GetIngoreSpecialAttrNextAdd", "GetAllAttrNextAttr", "GetAllAddAttrTb", "GetViewAttrListShow")
    ):
        return "ui_other_preview_display", "其他功法界面复用属性展示/预览 helper。"

    if file_name == "GongFaAttrItem.lua" and "FormatAttrNum" in stripped:
        if "addNum" in stripped or "nextValue" in stripped:
            return "ui_attr_item_format_add", "属性条目格式化增长值/下一阶值。"
        return "ui_attr_item_format_current", "属性条目格式化当前值。"
    return "", ""


def _gongfa_attr_display_field_refs(line: str) -> str:
    refs: list[str] = []
    if "ConfigName.Attribute_Attribute" in line:
        refs.append("ConfigName.Attribute_Attribute")
    for config_name in (
        "Gongfa_GongfaJie_Simple",
        "Gongfa_GongfaStar_Simple",
        "Gongfa_GongfaUpgrade",
        "Gongfa_GongfaTongxuanUpgrade",
    ):
        if config_name in line:
            refs.append(f"ConfigName.{config_name}")
    if "LuaEntityPropertyType" in line:
        refs.append("LuaEntityPropertyType")
    if "GameDefine.AttrType.Indirect" in line:
        refs.append("GameDefine.AttrType.Indirect")
    if "attrCfg.group" in line:
        refs.append("Attribute_Attribute.group")
    if "attrCfg.sort" in line or ".sort" in line and "attrCfg" in line:
        refs.append("Attribute_Attribute.sort")
    for token in ("curAttr", "nextAttr", "addNum", "isNew", "nextValue"):
        if token in line:
            refs.append(token)
    for helper in (
        "GetAllAddAttrTb",
        "GetLevelAndStarAttr",
        "GetIngoreSpecialAttrNextAdd",
        "GetAllAttrNextAdd",
        "GetAllAttrNextAttr",
        "GetViewAttrListShow",
        "FormatAttrNum",
        "ConvertBigDouble",
    ):
        if helper in line:
            refs.append(helper)
    return _join_unique(refs, limit=40)


def _build_gongfa_attr_display_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    search_root = root / "by_source" / "lscripts"
    if not search_root.is_dir():
        return rows
    for path in sorted(search_root.glob("**/text_assets/*.lua")):
        if path.name not in _GONGFA_ATTR_DISPLAY_FILE_NAMES:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if not any(
            term in text
            for term in (
                "Attribute_Attribute",
                "GetAllAddAttrTb",
                "GetAllAttrNextAdd",
                "GetIngoreSpecialAttrNextAdd",
                "FormatAttrNum",
                "Gongfa_GongfaJie_Simple",
            )
        ):
            continue
        lines = text.splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            row_kind, runtime_effect = _gongfa_attr_display_line_kind(path.name, function_name, line)
            if not row_kind:
                continue
            rows.append(
                {
                    "flow_stage": _gongfa_attr_display_stage(path.name, function_name),
                    "row_kind": row_kind,
                    "packet_name": "",
                    "source_file": _relative_to_root(path, root),
                    "file_name": path.name,
                    "function_name": function_name,
                    "line": line_no,
                    "field_refs": _gongfa_attr_display_field_refs(line),
                    "runtime_effect": runtime_effect,
                    "authority_note": "可见 Lua 证据；这里记录功法属性从静态配置到 UI 展示/下一阶预览的转换链。",
                    "code": line.strip()[:300],
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    return rows


def _gongfa_rich_text_stage(file_name: str, function_name: str) -> str:
    if file_name == "DetailPanel.lua":
        return "gongfa_static_detail_panel"
    if file_name == "GongFaAttrItem.lua":
        return "gongfa_attr_text_item"
    if file_name == "XianShuCreateSkillDetailView.lua":
        return "xianshu_detail_compose"
    if file_name == "XianShuCreateItem.lua":
        return "xianshu_detail_render_item"
    if file_name == "DesItem.lua":
        return "generic_description_item"
    return "other"


def _gongfa_rich_text_line_kind(file_name: str, function_name: str, line: str) -> tuple[str, str]:
    stripped = line.strip()
    if file_name == "DetailPanel.lua":
        if "SetComponent(LuaTextGamma" in stripped:
            return "bind_rich_text_component", "详情面板使用 LuaTextGamma 渲染可带富文本标签的文本。"
        if "DescTxt:SetText(gongFaVo.cfg.descript)" in stripped:
            return "render_static_gongfa_description", "静态功法描述直接来自 Gongfa_Gongfa.descript。"
        if "DescTxt:preferredHeight" in stripped:
            return "measure_static_description_height", "描述文本渲染后按 preferredHeight 调整面板高度。"

    if file_name == "GongFaAttrItem.lua":
        if "SetComponent(LuaTextGamma" in stripped:
            return "bind_attr_text_component", "属性条目使用 LuaTextGamma 渲染属性名、当前值和加值。"
        if "LuaLocalization.Get(" in stripped:
            return "load_attr_text_template", "从语言表读取属性数值格式模板。"
        if "LuaLocalization.Format(" in stripped:
            return "format_attr_text_template", "从语言表格式化属性名或状态颜色模板。"
        if "<color=" in stripped:
            return "inline_color_template", "属性加值行包含内联 color 富文本标签。"
        if ":SetText(" in stripped:
            return "render_attr_text", "把属性名、数值或加值写入文本组件。"
        if ":SetColor3(" in stripped:
            return "set_attr_runtime_color", "部分属性文本通过组件颜色设置而不是内联 color 标签上色。"

    if file_name == "XianShuCreateSkillDetailView.lua":
        if "SetComponent(LuaTextEx" in stripped or "SetComponent(LuaTextGamma" in stripped:
            return "bind_xianshu_text_component", "仙术详情页混用 LuaTextEx/LuaTextGamma 展示富文本和标题。"
        if "GetMainDes(" in stripped:
            return "compose_main_description", "主描述由 GongfahomemakeMgr:GetMainDes 根据品阶/星级/阶数配置拼装。"
        if "mainDesTxt:SetText(desStr)" in stripped:
            return "render_main_description", "把主描述写入详情页文本组件。"
        if 'LuaLocalization.Format("GongFa_LingJie_100"' in stripped:
            return "format_active_rich_description", "已激活效果使用 GongFa_LingJie_100 模板和彩色名称。"
        if 'LuaLocalization.Format("GongFa_LingJie_101"' in stripped:
            return "format_locked_rich_description", "未激活效果使用 GongFa_LingJie_101 模板并带跳转/灰色样式参数。"
        if 'LuaLocalization.Format("GongFa_LingJie_102"' in stripped:
            return "format_locked_side_description", "未激活的副词条/通玄说明使用 GongFa_LingJie_102 模板。"
        if 'LuaLocalization.Format("GongFa_LingJie_106"' in stripped:
            return "format_active_side_description", "已激活的副词条说明使用 GongFa_LingJie_106 模板。"
        if 'LuaLocalization.Format("GongFa_LingJie_131"' in stripped:
            return "format_tongxuan_description", "通玄二段描述使用 GongFa_LingJie_131 模板。"
        if "describe=describe.." in stripped:
            return "append_multiline_description", "多段效果文案用换行拼接成同一详情块。"
        if "showList:Add" in stripped and "describe" in stripped:
            return "push_description_row", "把拼好的 describe 放入详情列表数据源。"
        if "desTxt:SetText(v.describe)" in stripped or "activeTxt:SetText(str)" in stripped:
            return "render_xianshu_detail_text", "把已组装的详情文案写入文本组件。"

    if file_name == "XianShuCreateItem.lua":
        if "SetComponent(LuaTextEx" in stripped or "SetComponent(LuaTextGamma" in stripped:
            return "bind_xianshu_item_text_component", "仙术详情列表项使用 LuaTextEx 渲染 describe。"
        if "desTxt:SetText(data.describe)" in stripped:
            return "render_xianshu_description_row", "详情列表项直接渲染 data.describe。"
        if 'desTxt:SetColor3("322722")' in stripped:
            return "set_active_description_color", "已激活详情项使用深色正文颜色。"
        if 'desTxt:SetColor3("74746c")' in stripped:
            return "set_locked_description_color", "未激活详情项使用灰紫色正文颜色。"

    if file_name == "DesItem.lua":
        if "SetComponent(LuaTextGamma" in stripped:
            return "bind_description_item_component", "通用描述项使用 LuaTextGamma。"
        if "desTxt:SetText(data.desStr)" in stripped:
            return "render_description_item_text", "通用描述项渲染 data.desStr。"
        if "desTxt:SetColor3(data.color)" in stripped:
            return "set_description_item_color", "通用描述项可由数据传入颜色。"
    return "", ""


def _gongfa_rich_text_field_refs(line: str) -> str:
    refs: list[str] = []
    for match in re.finditer(r'LuaLocalization\.(Get|Format)\("([^"]+)"', line):
        refs.append(f"LuaLocalization.{match.group(1)}:{match.group(2)}")
    for match in re.finditer(r'["\'](#?[0-9a-fA-F]{6,8})["\']', line):
        value = match.group(1)
        refs.append(f"color:{value if value.startswith('#') else '#' + value}")
    if "<color=" in line:
        refs.append("rich_tag:<color>")
    for token, ref in (
        ("LuaTextGamma", "LuaTextGamma"),
        ("LuaTextEx", "LuaTextEx"),
        ("gongFaVo.cfg.descript", "Gongfa_Gongfa.descript"),
        ("gongFaVo.cfg.name", "Gongfa_Gongfa.name"),
        ("pinCfg.describe", "Gongfa_GongfaPin.describe"),
        ("sidePinCfg.describe", "Gongfa_GongfaPin.describe"),
        ("sideCfg.describe", "Gongfa_GongfaJie.describe"),
        ("sideJieCfg.name", "Gongfa_GongfaJie.name"),
        ("qualityCfg.color", "Quality_Quality.color"),
        ("qualityCfgSide.color", "Quality_Quality.color"),
        ("secDescribe", "tongxuan_sec_describe"),
        ("GetMainDes", "GongfahomemakeMgr:GetMainDes"),
    ):
        if token in line:
            refs.append(ref)
    return _join_unique(refs, limit=50)


def _build_gongfa_rich_text_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    search_root = root / "by_source" / "lscripts"
    if not search_root.is_dir():
        return rows
    for path in sorted(search_root.glob("**/text_assets/*.lua")):
        if path.name not in _GONGFA_RICH_TEXT_FILE_NAMES:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if not any(term in text for term in ("LuaText", "LuaLocalization", "SetText", "<color=", "describe")):
            continue
        lines = text.splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            row_kind, runtime_effect = _gongfa_rich_text_line_kind(path.name, function_name, line)
            if not row_kind:
                continue
            rows.append(
                {
                    "flow_stage": _gongfa_rich_text_stage(path.name, function_name),
                    "row_kind": row_kind,
                    "packet_name": "",
                    "source_file": _relative_to_root(path, root),
                    "file_name": path.name,
                    "function_name": function_name,
                    "line": line_no,
                    "field_refs": _gongfa_rich_text_field_refs(line),
                    "runtime_effect": runtime_effect,
                    "authority_note": "可见 Lua 证据；这里记录功法详情文案、语言表模板和富文本/颜色渲染链路。",
                    "code": line.strip()[:300],
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    return rows


def _localization_keys_from_gongfa_rich_text(rows: list[dict[str, Any]]) -> tuple[Counter[str], dict[str, set[str]]]:
    key_counts: Counter[str] = Counter()
    method_refs: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        for ref in str(row.get("field_refs") or "").split("、"):
            if not ref.startswith("LuaLocalization."):
                continue
            method, _, key = ref.partition(":")
            if not key:
                continue
            key_counts[key] += 1
            method_refs[key].add(method)
    return key_counts, method_refs


def _find_runtime_localization_paths(root: Path) -> list[Path]:
    preferred = sorted(root.glob("by_source/lscripts/generate/localization/chinese/**/text_assets/localization.lua"))
    if preferred:
        return preferred
    return sorted(root.glob("by_source/lscripts/**/text_assets/localization.lua"))


def _parse_lua_localization_entries(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    entry_re = re.compile(r"^\['(?P<key>(?:\\'|[^'])+)'\]\s*=\s*'(?P<text>.*)',?\s*$")
    with path.open("r", encoding="utf-8-sig", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            match = entry_re.match(line.rstrip("\r\n"))
            if not match:
                continue
            key = _unescape_lua_string(match.group("key"))
            rich_text = _unescape_lua_string(match.group("text"))
            rows[key] = {
                "key": key,
                "rich_text": rich_text,
                "plain_text": strip_fanxiu_rich_text(rich_text),
                "line": line_no,
                "path": path,
            }
    return rows


def _build_gongfa_localization_template_rows(root: Path, rich_text_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    key_counts, method_refs = _localization_keys_from_gongfa_rich_text(rich_text_rows)
    if not key_counts:
        return []
    localization_entries: dict[str, dict[str, Any]] = {}
    for path in _find_runtime_localization_paths(root):
        localization_entries.update(_parse_lua_localization_entries(path))
    rows: list[dict[str, Any]] = []
    for key in sorted(key_counts):
        entry = localization_entries.get(key, {})
        rich_text = str(entry.get("rich_text") or "")
        plain_text = str(entry.get("plain_text") or "")
        color_refs = _join_unique(
            [match.group(1) for match in re.finditer(r"<color=(#[0-9a-fA-F]{6,8}|#%s)>", rich_text)],
            limit=20,
        )
        rows.append(
            {
                "localization_key": key,
                "usage_count": key_counts[key],
                "method_refs": _join_unique(sorted(method_refs.get(key, set())), limit=10),
                "source_file": _relative_to_root(entry["path"], root) if entry.get("path") else "",
                "file_name": entry["path"].name if entry.get("path") else "",
                "line": entry.get("line", ""),
                "placeholder_count": len(re.findall(r"%(?:\.\d+)?[sdif]", rich_text)),
                "color_refs": color_refs,
                "has_href": "1" if "<href=" in rich_text else "0",
                "rich_text": rich_text,
                "plain_text": plain_text,
                "status": "ok" if entry else "missing",
            }
        )
    return rows


def _gongfa_description_composition_stage(file_name: str, function_name: str) -> str:
    if file_name == "GongfahomemakeMgr.lua" and function_name == "GetMainDes":
        return "main_description_formatter"
    if file_name == "GongfahomemakeMgr.lua" and function_name in {
        "GetOneTongXuanMainDesc",
        "GetOneTongXuanSecDesc",
    }:
        return "tongxuan_description_lookup"
    if file_name == "GongFaNewData.lua" and function_name == "GetTongXuanDesInfo":
        return "tongxuan_gallery_split"
    if file_name == "XianShuCreateSkillDetailView.lua":
        return "xianshu_detail_list_composer"
    return "other"


def _gongfa_description_composition_line_kind(
    file_name: str, function_name: str, line: str
) -> tuple[str, str]:
    stripped = line.strip()
    if file_name == "GongfahomemakeMgr.lua" and function_name == "GetMainDes":
        if "ConfigName.Quality_Quality" in stripped:
            return "resolve_description_quality", "按功法或通玄品质读取 Quality_Quality.color。"
        if "starCfg and starCfg.param" in stripped:
            return "read_star_description_params", "读取星级配置参数，准备填充 starCfg.describe 的占位符。"
        if "jieCfg and jieCfg.param" in stripped:
            return "read_jie_description_params", "读取阶数配置参数，和星级参数合并后填充主描述。"
        if "TabelAddTabel" in stripped:
            return "merge_star_jie_params", "按客户端逻辑把 starCfg.param 与 jieCfg.param 顺序相加。"
        if "string.format(starCfg.describe" in stripped:
            return "format_star_jie_description", "用合并后的参数格式化 starCfg.describe。"
        if "GetOneTongXuanMainDesc" in stripped:
            return "lookup_tongxuan_main_description", "读取通玄主描述并追加到主描述。"
        if "showColor=" in stripped and "tongxuan" in stripped:
            return "select_active_color", "已通玄时使用通玄品质颜色，否则使用功法品质颜色。"
        if 'LuaLocalization.Format("GongFa_LingJie_60"' in stripped or 'LuaLocalization.Format("GongFa_LingJie_58"' in stripped:
            return "append_main_activation_link", "可激活主功法时追加功法名链接或不可点击高亮。"
        if 'LuaLocalization.Format("GongFa_LingJie_129"' in stripped or 'LuaLocalization.Format("GongFa_LingJie_128"' in stripped:
            return "append_tongxuan_activation_link", "可激活通玄时追加通玄功法名链接或不可点击高亮。"
        if 'LuaLocalization.Format("GongFa_LingJie_132"' in stripped:
            return "wrap_locked_tongxuan_description", "未激活通玄时用语言模板包一层未激活样式。"
        if "desStr=desStr.." in stripped or 'string.format("\\n\\n%s' in stripped:
            return "append_description_block", "把激活提示或通玄描述按多段换行追加到主描述。"
        if "StringFormatColorType" in stripped:
            return "normalize_description_color_type", "主描述最终按当前界面颜色模式转换富文本颜色。"

    if file_name == "GongfahomemakeMgr.lua" and function_name in {
        "GetOneTongXuanMainDesc",
        "GetOneTongXuanSecDesc",
    }:
        if "CheckGongFaBookTongXuanIsShow" in stripped:
            return "check_tongxuan_visibility", "先判断该功法或词条是否显示通玄说明。"
        if "GetGongfaTongXuanCfgByIdTongXuan" in stripped:
            return "resolve_tongxuan_level_config", "按 originId 和 tongxuan 等级取通玄配置，0 级预览取 1 级配置。"
        if "mainDescribe" in stripped:
            return "read_tongxuan_main_description", "通玄主描述来自 TongXuan 配置的 mainDescribe。"
        if "secDescribe" in stripped:
            return "read_tongxuan_secondary_description", "通玄副词条描述来自 TongXuan 配置的 secDescribe。"
        if "StringFormatColorType" in stripped:
            return "normalize_tongxuan_color_type", "通玄描述也会按界面颜色模式转换。"

    if file_name == "GongFaNewData.lua" and function_name == "GetTongXuanDesInfo":
        if "GetGongfaTongXuanCfgEx" in stripped:
            return "load_tongxuan_config_index", "读取按功法 id 聚合的通玄配置索引。"
        if "tbGongFa[id]" in stripped:
            return "iterate_tongxuan_config_rows", "遍历指定功法的所有通玄配置行。"
        if "mainDescribe" in stripped:
            return "filter_tongxuan_rows_with_main_description", "只保留有 mainDescribe 的通玄配置用于展示。"
        if "cfg.pin>pin" in stripped:
            return "split_tongxuan_by_current_pin", "按当前品阶把通玄说明拆成已满足和后续可见两组。"
        if "tbBeforeJie" in stripped or "tbNextJie" in stripped:
            return "return_tongxuan_before_after_lists", "返回当前品阶前后的通玄说明列表。"

    if file_name == "XianShuCreateSkillDetailView.lua":
        if "GetMainDes(" in stripped:
            return "compose_main_description_call", "详情页调用 GetMainDes 生成主描述。"
        if "mainDesTxt:SetText" in stripped:
            return "render_main_description", "把主描述写入详情页主文本组件。"
        if "showList:Add" in stripped and "itemType=1" in stripped:
            return "push_section_header", "向详情列表插入章节标题。"
        if "xianEffectMap" in stripped:
            return "iterate_xian_effect_map", "遍历仙界/飞升自创词条映射。"
        if "effectMap" in stripped:
            return "iterate_lingjie_effect_map", "遍历灵界自创词条映射。"
        if "GetMainFeatureCfgById" in stripped:
            return "resolve_feature_type", "按 skillId/featureId 读取 MainFeature 并判断主副词条。"
        if "GetSideFeatureJieCfgById" in stripped:
            return "resolve_side_jie_description", "副词条阶数描述来自 SideFeatureJie。"
        if "GetSideFeaturePinCfgById" in stripped:
            return "resolve_side_pin_description", "副词条品阶描述来自 SideFeaturePin。"
        if "GetXianjieGongfaStarCfgEx" in stripped:
            return "resolve_xianjie_star_description", "仙界主词条用 featureGroup/star 反查星级描述上下文。"
        if "IsActiveEffect" in stripped or "GetGongFaIsLearn" in stripped or "GetSideFeaturePinIsActive" in stripped:
            return "evaluate_effect_active_state", "根据学习状态、品阶或条件判断效果是否激活。"
        if 'LuaLocalization.Format("GongFa_LingJie_100"' in stripped:
            return "format_active_effect_description", "已激活词条用 GongFa_LingJie_100 格式化彩色标题和正文。"
        if 'LuaLocalization.Format("GongFa_LingJie_101"' in stripped:
            return "format_locked_effect_description", "未激活词条用 GongFa_LingJie_101 格式化灰色正文和跳转链接。"
        if 'LuaLocalization.Format("GongFa_LingJie_102"' in stripped:
            return "format_locked_nested_description", "未激活的品阶或通玄子描述用 GongFa_LingJie_102。"
        if 'LuaLocalization.Format("GongFa_LingJie_106"' in stripped:
            return "format_active_pin_description", "已激活品阶描述用 GongFa_LingJie_106 标注悟境。"
        if 'LuaLocalization.Format("GongFa_LingJie_131"' in stripped:
            return "format_active_tongxuan_secondary", "已激活通玄副描述用 GongFa_LingJie_131。"
        if "describe=describe.." in stripped:
            return "append_nested_description_line", "同一效果块内把品阶、通玄等子描述按换行拼接。"
        if ("showList:Add" in stripped or "assistList:Add" in stripped) and "describe" in stripped:
            return "push_effect_description_row", "把拼好的 describe 放入详情列表。"
        if "assistList:Sort" in stripped:
            return "sort_assist_description_rows", "副词条效果按 sort 字段排序后展示。"

    return "", ""


def _gongfa_description_composition_refs(line: str) -> tuple[str, str, str]:
    config_refs = _join_unique(_CONFIG_REF_RE.findall(line), limit=20)
    localization_keys = _join_unique(
        [match.group(2) for match in re.finditer(r'LuaLocalization\.(Get|Format)\("([^"]+)"', line)],
        limit=20,
    )
    data_refs: list[str] = []
    for token, ref in (
        ("starCfg.describe", "LingjieGongfa_LingjieGongfaStar.describe"),
        ("starCfg.param", "LingjieGongfa_LingjieGongfaStar.param"),
        ("jieCfg.param", "LingjieGongfa_LingjieGongfaJie.param"),
        ("pinCfg.describe", "LingjieGongfa_MainFeaturePin.describe"),
        ("sidePinCfg.describe", "LingjieGongfa_SideFeaturePin.describe"),
        ("sideCfg.describe", "LingjieGongfa_SideFeatureJie.describe"),
        ("sideJieCfg.name", "LingjieGongfa_SideFeatureJie.name"),
        ("featureBaseCfg.featureGroup", "LingjieGongfa_FeatureBase.featureGroup"),
        ("mainFeatureCfg.condition", "LingjieGongfa_MainFeature.condition"),
        ("skillCommonVO.xianEffectMap", "CreateSkillCommonVO.xianEffectMap"),
        ("skillCommonVO.effectMap", "CreateSkillCommonVO.effectMap"),
        ("skillCommonVO.mainId", "CreateSkillCommonVO.mainId"),
        ("qualityCfg.color", "Quality_Quality.color"),
        ("qualityCfgSide.color", "Quality_Quality.color"),
        ("tongxuanQualityCfg.color", "Quality_Quality.color"),
        ("mainDescribe", "GongfaTongXuan.mainDescribe"),
        ("secDescribe", "GongfaTongXuan.secDescribe"),
        ("GetMainDes", "GongfahomemakeMgr:GetMainDes"),
        ("GetOneTongXuanMainDesc", "GongfahomemakeMgr:GetOneTongXuanMainDesc"),
        ("GetOneTongXuanSecDesc", "GongfahomemakeMgr:GetOneTongXuanSecDesc"),
        ("GetGongfaTongXuanCfgByIdTongXuan", "GongFaNewModel:GetGongfaTongXuanCfgByIdTongXuan"),
        ("CheckGongFaBookTongXuanIsShow", "GongFaNewModel:CheckGongFaBookTongXuanIsShow"),
    ):
        if token in line:
            data_refs.append(ref)
    return config_refs, localization_keys, _join_unique(data_refs, limit=50)


def _build_gongfa_description_composition_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    search_root = root / "by_source" / "lscripts"
    if not search_root.is_dir():
        return rows
    for path in sorted(search_root.glob("**/text_assets/*.lua")):
        if path.name not in _GONGFA_DESCRIPTION_COMPOSITION_FILE_NAMES:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if not any(term in text for term in ("GetMainDes", "TongXuan", "describe", "LuaLocalization")):
            continue
        lines = text.splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            row_kind, composition_role = _gongfa_description_composition_line_kind(path.name, function_name, line)
            if not row_kind:
                continue
            config_refs, localization_keys, data_refs = _gongfa_description_composition_refs(line)
            rows.append(
                {
                    "flow_stage": _gongfa_description_composition_stage(path.name, function_name),
                    "row_kind": row_kind,
                    "source_file": _relative_to_root(path, root),
                    "file_name": path.name,
                    "function_name": function_name,
                    "line": line_no,
                    "config_refs": config_refs,
                    "localization_keys": localization_keys,
                    "data_refs": data_refs,
                    "composition_role": composition_role,
                    "code": line.strip()[:300],
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    return rows


def _sync_unit_skill_cd_stage(file_name: str, function_name: str) -> str:
    if file_name == "FightNetLogic.lua":
        return "fight_sync_entry"
    if file_name == "SkillMgr.lua" and function_name == "RefreshUserSkillCD":
        return "skillmgr_sync_cd"
    if file_name == "SkillMgr.lua" and function_name == "ChangeBattleGroupSkills":
        return "battle_group_apply"
    if file_name == "SkillData.lua" and function_name in {
        "SetSkillCD",
        "GetCDBySkillId",
    }:
        return "skilldata_cd_cache"
    if file_name == "SkillData.lua" and function_name in {
        "UpdateGroupSkills",
        "ChangeCurrentGroupData",
        "SetChangeGroupData",
        "SetChangeNoUpGroupData",
        "SetChangeSkillGroupData",
        "SetShowSkillGroupData",
        "SetGroupSkillInfo",
    }:
        return "skilldata_group_cache"
    if file_name == "SkillNetLogic.lua":
        return "skill_group_response"
    return "other"


def _sync_unit_skill_cd_field_semantics(packet_name: str, field_name: str) -> str:
    return _SYNC_UNIT_SKILL_CD_FIELD_SEMANTICS.get((packet_name, field_name), "")


def _sync_unit_skill_cd_line_kind(file_name: str, function_name: str, line: str) -> tuple[str, str]:
    stripped = line.strip()
    if file_name == "FightNetLogic.lua" and function_name == "SM_SyncUnitFun":
        if stripped.startswith("function "):
            return "sync_unit_handler_decl", "SM_SyncUnit 网络处理入口。"
        if "RoleMgr.Inst_get():ReviveInfo(msg)" in stripped:
            return "dispatch_revival_info", "同一回包先交给 RoleMgr 处理 HP/MP/复活信息。"
        if "SkillMgr.Inst_get():RefreshUserSkillCD(msg)" in stripped:
            return "dispatch_skill_cd_sync", "把 groupId/skills/cds/systemTime 交给 SkillMgr 刷新技能 CD。"
    if file_name == "SkillMgr.lua" and function_name == "RefreshUserSkillCD":
        if stripped.startswith("function "):
            return "refresh_user_skill_cd_decl", "SkillMgr 接收 SM_SyncUnit 或同构消息刷新 CD。"
        if "SetSkillCD(msg.groupId,msg.skills,msg.cds,msg.systemTime:ToNum())" in stripped:
            return "set_skill_cd_from_sync_unit", "用服务端 groupId、skills、cds、systemTime 进入 SkillData:SetSkillCD。"
        if "Kpairs(msg.skills)" in stripped:
            return "iterate_synced_skill_list", "遍历服务端下发的 SkillInfoVO 列表。"
        if "RefreshSkillCD(skillVo.skillId)" in stripped:
            return "refresh_loaded_actor_skill_cd", "按 SkillInfoVO.skillId 通知 SkillActor 刷新本地已加载技能 CD。"
    if file_name == "SkillData.lua":
        if stripped.startswith("function ") and function_name == "SetSkillCD":
            return "set_skill_cd_decl", "SkillData 的 CD 缓存写入入口。"
        if stripped.startswith("function ") and function_name in {"SetChangeGroupData", "SetChangeNoUpGroupData", "SetChangeSkillGroupData"}:
            return "group_response_decl", "切组/替换回包写入技能组缓存的入口。"
        if stripped.startswith("function ") and function_name == "UpdateGroupSkills":
            return "update_group_skills_decl", "更新 groups[groupId].skills。"
        if "self.groups[groupId].skills=skills" in stripped or "self.groups[data.groupId].skills" in stripped:
            return "store_group_skills", "把服务端确认的 skills 列表写入技能组缓存。"
        if "self.groups[data.groupId]={" in stripped or "self.groups[groupId]={" in stripped:
            return "ensure_group_cache", "没有技能组缓存时创建 groupId/skills/cds 容器。"
        if "self.cdDic:LuaDic_AddOrSetItem(groupId,Dictionary.new())" in stripped:
            return "ensure_cd_group_cache", "为 groupId 创建 CD 字典。"
        if "self.cdDic[groupId]:LuaDic_Clear()" in stripped:
            return "clear_cd_group_cache", "刷新前清空该技能组旧 CD。"
        if "for index,skillVo in Kpairs(skillList)" in stripped:
            return "iterate_skill_cd_pairs", "按 skillList 索引遍历 SkillInfoVO，与 cdList[index-1] 对齐。"
        if "cdList[index-1]" in stripped and "systemTime" in stripped:
            return "compute_remaining_cd", "用 cdList[index-1]-systemTime 计算剩余 CD。"
        if "groupCDDic:LuaDic_AddOrSetItem(skillVo.skillId" in stripped:
            return "store_cd_by_skill_id", "以 SkillInfoVO.skillId 为 key 保存剩余 CD。"
        if stripped.startswith("function ") and function_name == "GetCDBySkillId":
            return "get_cd_by_skill_decl", "当前组按 skillId 读取 CD。"
        if "currentCDGroup[skillId]" in stripped:
            return "read_cd_by_skill_id", "从 cdDic[currentGroupId][skillId] 读取 CD。"
        if "self:SetSkillCD(data.groupId" in stripped:
            return "set_skill_cd_from_group_response", "切组/替换回包也复用 SetSkillCD。"
    if file_name == "SkillMgr.lua" and function_name == "ChangeBattleGroupSkills":
        if stripped.startswith("function "):
            return "change_battle_group_decl", "把已缓存的技能组应用到当前战斗。"
        if "GetShowSkillGroupData(groupId)" in stripped:
            return "load_cached_group", "按 groupId 读取 SkillData.groups 中的技能组。"
        if "local allSkillList=skillBattleGroupDic.skills" in stripped:
            return "read_cached_group_skills", "取出服务端确认的 SkillInfoVO 列表用于加载。"
        if "UpdateBattleGroupSkill(v,k)" in stripped:
            return "apply_battle_group_skill", "逐槽更新战斗技能。"
        if "SkillActor:LoadSkills()" in stripped:
            return "reload_user_skill_actor", "技能组更新后重新加载 UserView.SkillActor。"
    if file_name == "SkillNetLogic.lua" and function_name in {"SM_ReplaceSkillFun", "SM_ChangeGroupFun", "SM_AutoReplaceFun"}:
        if stripped.startswith("function "):
            return "skill_group_response_decl", "player.skill 服务端技能组响应入口。"
        if "SetChangeSkillGroupData(msg)" in stripped:
            return "replace_group_cache_update", "单槽替换响应写入 SkillData group/cache。"
        if "SetChangeGroupData(msg)" in stripped:
            return "change_group_cache_update", "切组响应写入 SkillData group/cache。"
        if "SetChangeNoUpGroupData(msg)" in stripped:
            return "auto_replace_group_cache_update", "自动替换响应写入 SkillData group/cache。"
        if "ChangeBattleGroupSkills(msg.groupId)" in stripped:
            return "apply_group_after_response", "服务端确认后将技能组应用到当前战斗。"
        if "ReFreshSkillGroupData" in stripped or "CHANGE_BATTLE_SKILL" in stripped or "ChangeGongFa" in stripped:
            return "raise_skill_group_ui_event", "技能组变化后刷新设置/功法 UI 事件。"
    return "", ""


def _sync_unit_skill_cd_field_refs(file_name: str, function_name: str, line: str) -> str:
    refs: list[str] = []
    if file_name == "FightNetLogic.lua" or function_name == "RefreshUserSkillCD":
        for match in re.finditer(r"\bmsg\.([A-Za-z0-9_]+)", line):
            refs.append(f"SM_SyncUnit.{match.group(1)}")
    elif file_name == "SkillNetLogic.lua":
        for match in re.finditer(r"\bmsg\.([A-Za-z0-9_]+)", line):
            refs.append(f"SM_ChangeGroup/SM_ReplaceSkill.{match.group(1)}")
    for token in ("groupId", "skillList", "cdList", "systemTime", "currentGroupId", "skills", "cds"):
        if re.search(rf"\b{re.escape(token)}\b", line):
            refs.append(token)
    for match in re.finditer(r"\bskillVo\.([A-Za-z0-9_]+)", line):
        refs.append(f"SkillInfoVO.{match.group(1)}")
    if "cdList[index-1]" in line:
        refs.append("cdList[index-1]")
    if "groupCDDic" in line or "cdDic" in line:
        refs.append("SkillData.cdDic[groupId][skillId]")
    return _join_unique(refs, limit=40)


def _build_sync_unit_skill_cd_rows(
    root: Path,
    all_fields_by_packet_name: dict[str, list[dict[str, str]]],
    all_packet_by_name: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for packet_name in sorted(_SYNC_UNIT_SKILL_CD_PACKET_NAMES):
        packet = all_packet_by_name.get(packet_name, {})
        fields = all_fields_by_packet_name.get(packet_name, [])
        if not packet and not fields:
            continue
        for field in fields:
            field_name = field.get("field_name") or ""
            rows.append(
                {
                    "flow_stage": "packet_schema" if packet_name != "SkillInfoVO" else "skill_vo_schema",
                    "row_kind": "packet_field",
                    "packet_name": packet_name,
                    "source_file": packet.get("relative_path") or field.get("relative_path") or "",
                    "file_name": packet.get("file") or field.get("file") or "",
                    "function_name": "reading",
                    "line": field.get("line") or "",
                    "field_refs": f"{packet_name}.{field_name}" if field_name else "",
                    "runtime_effect": _sync_unit_skill_cd_field_semantics(packet_name, field_name),
                    "authority_note": "协议字段定义；服务端返回的 skills/cds 是客户端技能组和 CD 缓存的输入。",
                    "code": f"{field_name}:{field.get('read_method') or ''}"
                    + (f"<{field.get('type_hint')}>" if field.get("type_hint") else ""),
                }
            )

    search_root = root / "by_source" / "lscripts"
    if not search_root.is_dir():
        return rows
    for path in sorted(search_root.glob("**/text_assets/*.lua")):
        if path.name not in _SYNC_UNIT_SKILL_CD_FILE_NAMES:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if not any(term in text for term in ("SM_SyncUnitFun", "RefreshUserSkillCD", "SetSkillCD", "SM_ChangeGroupFun")):
            continue
        lines = text.splitlines()
        spans = _function_spans(lines)
        for line_no, line in enumerate(lines, start=1):
            function_name = _function_for_line(spans, line_no)
            row_kind, runtime_effect = _sync_unit_skill_cd_line_kind(path.name, function_name, line)
            if not row_kind:
                continue
            rows.append(
                {
                    "flow_stage": _sync_unit_skill_cd_stage(path.name, function_name),
                    "row_kind": row_kind,
                    "packet_name": "SM_SyncUnit"
                    if path.name == "FightNetLogic.lua" or function_name == "RefreshUserSkillCD"
                    else "SM_ChangeGroup/SM_ReplaceSkill"
                    if path.name == "SkillNetLogic.lua"
                    else "",
                    "source_file": _relative_to_root(path, root),
                    "file_name": path.name,
                    "function_name": function_name,
                    "line": line_no,
                    "field_refs": _sync_unit_skill_cd_field_refs(path.name, function_name, line),
                    "runtime_effect": runtime_effect,
                    "authority_note": "服务端确认的技能组/CD 数据落到 SkillData；客户端主要负责缓存、换算剩余 CD 和刷新表现。",
                    "code": line.strip()[:300],
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    return rows


def _vo_field_role(vo_name: str, field_name: str) -> tuple[str, str]:
    return _GONGFAHOMEMAKE_VO_FIELD_ROLES.get((vo_name, field_name), ("", ""))


def _integration_category(file_name: str, relative_path: str) -> str:
    if file_name in {"SkillProgramVO.lua", "SkillInfoVO.lua", "ShowSkillVO.lua", "CM_ReplaceSkill.lua"}:
        return "gongfa_packet_bridge"
    if "message_" in relative_path:
        return "message_schema"
    if "gongfahomemake_" in relative_path:
        return "home_make_runtime"
    if "GongFaBattle" in file_name or file_name == "SelfGongFaBattleItem.lua":
        return "battle_equip_ui"
    if "CreateSkillDetail" in file_name or file_name in {"LingJieSkillItem.lua", "XianFaComposeSuccessView.lua"}:
        return "detail_display_ui"
    if file_name.endswith("Model.lua") or file_name.endswith("Mgr.lua"):
        return "model_runtime"
    return "other"


def _equip_flow_stage(file_name: str, line: str) -> str:
    if file_name in {
        "CM_ReplaceSkill.lua",
        "SM_ReplaceSkill.lua",
        "SkillInfoVO.lua",
        "ShowSkillVO.lua",
        "SkillProgramVO.lua",
        "CM_GongFaSaveProgram.lua",
        "SM_GongFaSaveProgram.lua",
        "GongFaProgramVO.lua",
        "CM_XinFaPutUp.lua",
        "SM_XinFaPutUp.lua",
        "XinFaVO.lua",
        "HomeMakeXinFaVO.lua",
    }:
        return "packet_or_vo_schema"
    if "EquipShenTongJueZhao" in line or "CM_ReplaceSkillFun" in line:
        return "direct_replace_send"
    if "EquipXinFa" in line or "AutoEquipAllXinFa" in line or "CM_XinFaPutUpFun" in line:
        return "xinfa_putup_send"
    if "CM_GongFaSaveProgramFun" in line or "CM_GongFaSaveProgram" in line:
        return "program_save_send"
    if "GongFaProgramVO" in line or "SkillProgramVO" in line:
        return "program_object_bridge"
    if "GetLingjieGongfaStarCfgBySkillId" in line or "cfg.skill" in line:
        return "self_skill_id_projection"
    if "skillCommonVO.id" in line or "xinFaId.makeId" in line or re.search(r"\bmakeId\b", line):
        return "self_make_id_bridge"
    if "gongFaHomeMakeVO" in line or "homeMakeVO" in line or "GongFaHomeMakeVO" in line:
        return "home_make_vo_bridge"
    return ""


def _state_update_stage(file_name: str, line: str) -> str:
    if "SM_XinFaPutUpFun" in line or "SetXinFaInfo" in line or "xinFaPutUpList" in line:
        return "xinfa_equip_state"
    if "GongFaSaveProgram" in line or "SetGongFaProgram" in line or "AddGongFaProgram" in line:
        return "program_state"
    if "SetGongFaHomeMakeList" in line or "homeMakeDic" in line or "GetGongFaHomeMakeVoById" in line:
        return "home_make_cache"
    if (
        "GongFaHomeMakeCombine" in line
        or "UpdateGongFaHomeMakeLearn" in line
        or "GongFaHomeMakeChangeName" in line
    ):
        return "home_make_instance_update"
    if "CHANGE_BATTLE_XIN_FA" in line:
        return "xinfa_refresh_event"
    if "RaiseEvent" in line and (
        "GongFaSaveProgram" in line
        or "GongFaHomeMakeList" in line
        or "UpdateGongFaHomeMakeLearn" in line
        or "CREATING_SKILL_UPDATE" in line
        or "TenCreate" in line
    ):
        return "ui_refresh_event"
    return ""


def _skill_core_flow_stage(file_name: str, function_name: str, line: str) -> str:
    if file_name == "SkillNetLogic.lua":
        if "CM_ReplaceSkillFun" in function_name or "CM_ReplaceSkill" in line:
            return "replace_request_send"
        if "SM_ReplaceSkillFun" in function_name or "SetChangeSkillGroupData" in line:
            return "replace_response_handler"
        if "SM_ShowSkillFun" in function_name or "SetShowSkillGroupData" in line:
            return "show_group_response"
        if "CM_AutoReplaceFun" in function_name:
            return "auto_replace_send"
        if "SM_AutoReplaceFun" in function_name or "SetChangeNoUpGroupData" in line:
            return "auto_replace_response"
        if "CM_ChangeGroupFun" in function_name:
            return "change_group_send"
        if "SM_ChangeGroupFun" in function_name or "SetChangeGroupData" in line:
            return "change_group_response"
        if "SM_ShowReplaceSkillFun" in function_name:
            return "plot_replace_response"
        if "F_Register" in line or "F_Unregister" in line:
            return "packet_lifecycle"
    if file_name == "SkillMgr.lua":
        if "AutoReplaceUpSkill" in function_name or "CM_ChangeGroupFun" in line:
            return "auto_replace_facade"
        if "ChangeBattleGroupSkills" in function_name or "ChangeBattleSkills" in function_name:
            return "battle_group_apply"
        if "UpdateBattleGroupSkill" in function_name or "SetBattleGroup" in function_name:
            return "battle_group_mutation"
        if "CheckGongFaIsEquipById" in function_name:
            return "equip_check_facade"
        if "CtorSkillVo" in function_name:
            return "skill_vo_clone"
        if "IsSkillConflict" in function_name:
            return "self_make_conflict_check"
    if file_name == "SkillModel.lua":
        if function_name:
            return "model_facade"
    if file_name == "SkillData.lua":
        if "SetChangeSkillGroupData" in function_name:
            return "replace_group_cache"
        if "SetChangeGroupData" in function_name:
            return "change_group_cache"
        if "SetChangeNoUpGroupData" in function_name:
            return "auto_replace_cache"
        if "SetShowSkillGroupData" in function_name or "SetGroupSkillInfo" in function_name:
            return "show_group_cache"
        if "SetSkillCD" in function_name:
            return "cd_cache"
        if "CheckGongFaIsEquipById" in function_name:
            return "equip_check_cache"
        if "GetDefaultSkillGroupData" in function_name or "GetShowSkillGroupData" in function_name:
            return "group_read"
    if file_name == "SkillConfig.lua":
        if "GetTimelineIdBySkillId" in function_name or "timelineId" in line or "_timelineId" in line:
            return "timeline_id_resolver"
        if "GetSkillExParams" in function_name or "Skill_SkillExParams" in line:
            return "skill_ex_params_lookup"
        if "GetSkillInfo" in function_name:
            return "skill_config_lookup"
    if file_name == "SkillBase.lua":
        if "UpdateTimelineData" in function_name or "GetTimelineIdBySkillId" in line:
            return "timeline_runtime_update"
        if "GetSkillExParams" in line or "exParamCfg.channel" in line or "real_section_dmg" in line:
            return "skill_ex_params_runtime"
        if "CheckBuffChangeTimeline" in function_name or "CHANGETIMELINE" in line:
            return "buff_change_timeline"
    return ""


def _skill_mgr_ref_role(symbol: str) -> str:
    if symbol in {"CM_ReplaceSkillFun"}:
        return "replace_request"
    if symbol in {"AutoReplaceUpSkill"}:
        return "auto_replace_request"
    if symbol in {"CheckGongFaIsEquipById"}:
        return "equip_check"
    if symbol in {"GetDefaultSkillGroupData", "GetShowSkillGroupData", "GetChangeGroupData"}:
        return "skill_group_read"
    return "other"


def _battle_damage_flow_stage(file_name: str, function_name: str, line: str) -> str:
    if file_name == "SkillConfig.lua":
        if "GetTimelineIdBySkillId" in function_name or "_timelineId" in line or "timelineId" in line:
            return "timeline_id_resolver"
        if "GetSkillExParams" in function_name or "Skill_SkillExParams" in line:
            return "skill_ex_params_lookup"
    if file_name == "SkillBase.lua":
        if "UpdateTimelineData" in function_name or "Cfg_Hurts" in line or "Cfg_keyFrames" in line:
            return "timeline_runtime_load"
        if "real_section_dmg" in line or "exParamCfg.channel" in line:
            return "section_damage_flag"
        if "SetSM_FightResult" in function_name and (
            "hurt_event" in line
            or "percent" in line
            or "damage_num" in line
            or "damage_view" in line
            or "recover_num" in line
            or "mpDamage" in line
        ):
            return "server_result_damage_split"
        if "SetSM_FightResult" in function_name and (
            "hurt_index" in line
            or "bIgnore" in line
            or "index=index+1" in line
        ):
            return "section_hit_gate"
        if "Add4HurtDataListDic" in line or "FindBulletByHurtIndex" in line or "trajectoryCachedHurtVo" in line:
            return "hurt_data_schedule"
        if "PlaySkillTimeline" in function_name and (
            "not self.real_section_dmg" in line
            or "PlayBattleSkillSuffer" in line
            or "PlayBattleSkill(" in line
        ):
            return "presentation_gate"
        if "IsInSkillCastArea" in function_name or "DamageCenterType" in line or "ScopeType" in line:
            return "client_range_check"
    if file_name == "BulletMgr.lua":
        if "hurtEvent" in line or "timelineId" in line:
            return "trajectory_bullet_bind"
    return ""


def _battle_damage_flow_semantics(stage: str) -> str:
    return {
        "timeline_id_resolver": "按 jian/xian/mo/sha 或默认 timelineId 选择技能 timeline。",
        "skill_ex_params_lookup": "从 SkillExParams 读取 timeline 附加参数，channel 在这里进入运行态。",
        "timeline_runtime_load": "SkillBase 加载 q_keyframe_events/q_hurt_events 并排序，为后续分段伤害准备数据。",
        "section_damage_flag": "channel 非空时设置 real_section_dmg，表示按服务端多次回包/分段处理。",
        "server_result_damage_split": "服务端 resultVo.damage/damageView/recover 等数值按 hurt_event[2] 百分比分摊到当前段。",
        "section_hit_gate": "real_section_dmg 模式下 hurt_index 每次递增，只处理当前段 hurt_event。",
        "hurt_data_schedule": "把本段 HurtData 放入普通时间轴或飞行轨迹缓存，等待表现层按时间/轨迹播放。",
        "presentation_gate": "real_section_dmg 会避开用户端一次性播放 suffer timeline 的分支，改由分段回包驱动。",
        "client_range_check": "客户端有范围判定/调试逻辑，但最终伤害目标和数值仍来自服务端回包。",
        "trajectory_bullet_bind": "飞行轨迹命中通过 bullet/hurtEvent 绑定 timeline 和伤害数据。",
    }.get(stage, "")


def _apk_symbol_target_files(apk_root: Path) -> list[tuple[Path, str]]:
    targets: list[tuple[Path, str]] = []
    for relative_path, role in _APK_SYMBOL_FIXED_TARGETS.items():
        path = apk_root / relative_path
        if path.is_file():
            targets.append((path, role))
    for path in sorted(apk_root.glob("classes*.dex"), key=lambda item: (len(item.name), item.name)):
        if path.is_file():
            targets.append((path, "dex"))
    return targets


def _binary_snippet(data: bytes, offset: int, length: int, *, radius: int = 96) -> str:
    left = max(0, offset - radius)
    right = min(len(data), offset + length + radius)
    text = data[left:right].replace(b"\x00", b" ").decode("utf-8", errors="replace")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:260]


def _scan_apk_runtime_symbol_hits(
    *,
    apk_root: str | Path | None,
    max_offsets_per_term: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not apk_root:
        return [], {"status": "not_requested", "apk_root": "", "target_file_count": 0}

    resolved_apk_root = resolve_fanxiu_apk_unpacked_root(apk_root)
    rows: list[dict[str, Any]] = []
    missing_targets = [
        relative_path.as_posix()
        for relative_path in _APK_SYMBOL_FIXED_TARGETS
        if not (resolved_apk_root / relative_path).is_file()
    ]
    target_files = _apk_symbol_target_files(resolved_apk_root)

    for path, file_role in target_files:
        data = path.read_bytes()
        relative_path = path.relative_to(resolved_apk_root).as_posix()
        for term in _APK_RUNTIME_SYMBOL_TERMS:
            needle = term.encode("utf-8")
            offsets: list[int] = []
            hit_count = 0
            pos = data.find(needle)
            while pos >= 0:
                hit_count += 1
                if len(offsets) < max_offsets_per_term:
                    offsets.append(pos)
                pos = data.find(needle, pos + 1)
            if not hit_count:
                continue
            first_offset = offsets[0]
            rows.append(
                {
                    "file_role": file_role,
                    "relative_path": relative_path,
                    "size_bytes": len(data),
                    "term": term,
                    "hit_count": hit_count,
                    "first_offset_hex": f"0x{first_offset:X}",
                    "sample_offsets_hex": _join_unique([f"0x{offset:X}" for offset in offsets], limit=max_offsets_per_term),
                    "snippet": _binary_snippet(data, first_offset, len(needle)),
                }
            )

    rows.sort(key=lambda row: (str(row["file_role"]), str(row["relative_path"]), str(row["term"])))
    return rows, {
        "status": "ok",
        "apk_root": str(resolved_apk_root),
        "target_file_count": len(target_files),
        "missing_targets": missing_targets,
    }


def _load_parsed_config_rows(root: Path, config_name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows_path = root / "parsed_configs" / config_name / "rows.json"
    if not rows_path.is_file():
        return [], {"status": "missing", "config_name": config_name, "path": str(rows_path)}
    data = json.loads(rows_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise FanxiuResourceError(f"配置解析结果不是列表：{rows_path}")
    rows = [row for row in data if isinstance(row, dict)]
    return rows, {"status": "ok", "config_name": config_name, "path": str(rows_path), "row_count": len(rows)}


def _cell_json(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _build_projected_skill_rows(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    star_rows, star_status = _load_parsed_config_rows(root, "LingjieGongfaStar")
    skill_rows, skill_status = _load_parsed_config_rows(root, "Skill")
    if not star_rows or not skill_rows:
        return [], {
            "status": "missing_source",
            "LingjieGongfaStar": star_status,
            "Skill": skill_status,
        }

    skills_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in skill_rows:
        skill_id = row.get("id")
        if skill_id is None:
            continue
        skills_by_id[str(skill_id)].append(row)

    rows: list[dict[str, Any]] = []
    for star_row in star_rows:
        skill_id = star_row.get("skill")
        matches = skills_by_id.get(str(skill_id), []) if skill_id is not None else []
        skill = matches[0] if matches else {}
        rows.append(
            {
                "gongfa_id": star_row.get("gongfaId", ""),
                "star": star_row.get("star", ""),
                "lingjie_star_id": star_row.get("id", star_row.get("_row_key", "")),
                "projected_skill_id": skill_id if skill_id is not None else "",
                "match_status": "matched" if skill else ("missing_skill_id" if skill_id is None else "missing_skill"),
                "skill_row_count": len(matches),
                "skill_name": skill.get("name_plain", skill.get("name", "")),
                "skill_type": skill.get("skillType", ""),
                "is_active_skill": skill.get("isActiveSkill", ""),
                "timeline_id": skill.get("timelineId", ""),
                "cd_time": skill.get("cdTime", ""),
                "public_cd_group": skill.get("publicCdGroup", ""),
                "public_cd": skill.get("publicCd", ""),
                "pre_skill": _cell_json(skill.get("preSkill", "")),
                "stat_skill_group": skill.get("statSkillGroup", ""),
                "stat_skill": _cell_json(skill.get("statSkill", "")),
                "icon": skill.get("icon", ""),
                "scope": _cell_json(skill.get("scope", "")),
                "target_type": skill.get("targetType", ""),
                "target_max": skill.get("targetMax", ""),
                "condition": _cell_json(skill.get("condition", "")),
                "power": skill.get("power", ""),
                "fight_score": skill.get("fightScore", ""),
                "lingjie_star_cd": star_row.get("cd_plain", star_row.get("cd", "")),
                "lingjie_star_describe": star_row.get("describe_plain", star_row.get("describe", "")),
                "lingjie_star_param": _cell_json(star_row.get("param", "")),
            }
        )

    rows.sort(
        key=lambda row: (
            str(row["gongfa_id"]),
            str(row["star"]).zfill(8) if str(row["star"]).isdigit() else str(row["star"]),
            str(row["projected_skill_id"]),
        )
    )
    duplicate_skill_count = sum(1 for matches in skills_by_id.values() if len(matches) > 1)
    return rows, {
        "status": "ok",
        "LingjieGongfaStar": star_status,
        "Skill": skill_status,
        "skill_id_count": len(skills_by_id),
        "duplicate_skill_id_count": duplicate_skill_count,
    }


def _id_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item) != ""]
    return [str(value)]


def _index_rows_by_id(rows: list[dict[str, Any]], field: str = "id") -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for value in _id_list(row.get(field)):
            indexed[value].append(row)
    return indexed


def _build_projected_skill_next_hop_rows(
    root: Path,
    projected_skill_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    skill_rows, skill_status = _load_parsed_config_rows(root, "Skill")
    skill_ex_rows, skill_ex_status = _load_parsed_config_rows(root, "SkillExParams")
    lingjie_jie_rows, lingjie_jie_status = _load_parsed_config_rows(root, "LingjieGongfaJie")
    main_feature_pin_rows, main_feature_pin_status = _load_parsed_config_rows(root, "MainFeaturePin")

    if not projected_skill_rows or not skill_rows:
        return [], {
            "status": "missing_source",
            "Skill": skill_status,
            "SkillExParams": skill_ex_status,
            "LingjieGongfaJie": lingjie_jie_status,
            "MainFeaturePin": main_feature_pin_status,
        }

    skill_by_id = {str(row.get("id")): row for row in skill_rows if row.get("id") is not None}
    skill_ex_by_id = {str(row.get("id")): row for row in skill_ex_rows if row.get("id") is not None}
    lingjie_jie_by_feature = _index_rows_by_id(lingjie_jie_rows, "feature")
    main_feature_pin_by_feature = _index_rows_by_id(main_feature_pin_rows, "feature")

    timeline_fields = [
        ("jian", "jian_timelineId"),
        ("mo", "mo_timelineId"),
        ("sha", "sha_timelineId"),
        ("xian", "xian_timelineId"),
        ("default", "timelineId"),
    ]

    rows: list[dict[str, Any]] = []
    unique_timeline_ids: set[str] = set()
    matched_timeline_ids: set[str] = set()
    for projected in projected_skill_rows:
        skill_id = str(projected.get("projected_skill_id") or "")
        skill = skill_by_id.get(skill_id, {})
        timeline_ids: list[str] = []
        timeline_channels: list[str] = []
        for career, field in timeline_fields:
            for timeline_id in _id_list(skill.get(field)):
                unique_timeline_ids.add(timeline_id)
                timeline_ids.append(f"{career}:{timeline_id}")
                channel = skill_ex_by_id.get(timeline_id, {}).get("channel", "")
                if channel:
                    matched_timeline_ids.add(timeline_id)
                timeline_channels.append(f"{career}:{timeline_id}:{channel}")

        lingjie_jie_refs = [
            f"id={row.get('id', '')}:jie={row.get('jie', '')}:param={_cell_json(row.get('param', ''))}"
            for row in lingjie_jie_by_feature.get(skill_id, [])
        ]
        main_feature_pin_refs = [
            f"id={row.get('id', '')}:pin={row.get('pin', '')}:quality={row.get('quality', '')}:name={row.get('name_plain', row.get('name', ''))}"
            for row in main_feature_pin_by_feature.get(skill_id, [])
        ]
        if timeline_ids:
            next_hop_kind = "timeline_channel"
        elif lingjie_jie_refs or main_feature_pin_refs:
            next_hop_kind = "lingjie_feature_reuse"
        else:
            next_hop_kind = "no_static_next_hop"

        rows.append(
            {
                "gongfa_id": projected.get("gongfa_id", ""),
                "star": projected.get("star", ""),
                "lingjie_star_id": projected.get("lingjie_star_id", ""),
                "projected_skill_id": skill_id,
                "skill_name": projected.get("skill_name", skill.get("name_plain", skill.get("name", ""))),
                "skill_type": projected.get("skill_type", skill.get("skillType", "")),
                "next_hop_kind": next_hop_kind,
                "timeline_ids": _join_unique(timeline_ids, limit=20),
                "timeline_channels": _join_unique(timeline_channels, limit=20),
                "timeline_channel_match_count": sum(
                    1 for item in timeline_ids if item.split(":", 1)[-1] in skill_ex_by_id
                ),
                "lingjie_jie_ref_count": len(lingjie_jie_refs),
                "lingjie_jie_refs": _join_unique(lingjie_jie_refs, limit=20),
                "main_feature_pin_ref_count": len(main_feature_pin_refs),
                "main_feature_pin_refs": _join_unique(main_feature_pin_refs, limit=20),
                "cd_time": skill.get("cdTime", projected.get("cd_time", "")),
                "public_cd_group": skill.get("publicCdGroup", projected.get("public_cd_group", "")),
                "public_cd": skill.get("publicCd", projected.get("public_cd", "")),
                "scope": _cell_json(skill.get("scope", projected.get("scope", ""))),
                "target_type": skill.get("targetType", projected.get("target_type", "")),
                "target_max": skill.get("targetMax", projected.get("target_max", "")),
                "fight_score": skill.get("fightScore", projected.get("fight_score", "")),
            }
        )

    rows.sort(
        key=lambda row: (
            str(row["next_hop_kind"]),
            str(row["gongfa_id"]),
            str(row["star"]).zfill(8) if str(row["star"]).isdigit() else str(row["star"]),
        )
    )
    return rows, {
        "status": "ok",
        "Skill": skill_status,
        "SkillExParams": skill_ex_status,
        "LingjieGongfaJie": lingjie_jie_status,
        "MainFeaturePin": main_feature_pin_status,
        "unique_timeline_id_count": len(unique_timeline_ids),
        "timeline_channel_matched_id_count": len(matched_timeline_ids),
    }


def _parse_timeline_track_json(value: Any) -> dict[str, Any]:
    if not value:
        return {
            "track_count": 0,
            "clip_count": 0,
            "clip_type_counts": "",
            "effect_resources": "",
            "action_names": "",
            "sound_ids": "",
        }
    try:
        track_items = json.loads(str(value))
    except json.JSONDecodeError:
        return {
            "track_count": 0,
            "clip_count": 0,
            "clip_type_counts": "",
            "effect_resources": "",
            "action_names": "",
            "sound_ids": "",
        }
    clip_type_counts: Counter[str] = Counter()
    effect_resources: list[str] = []
    action_names: list[str] = []
    sound_ids: list[str] = []
    clip_count = 0
    for item in track_items if isinstance(track_items, list) else []:
        try:
            track = json.loads(item) if isinstance(item, str) else item
            track_value = track.get("TrackValue", {}) if isinstance(track, dict) else {}
            payload = json.loads(track_value) if isinstance(track_value, str) else track_value
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
        clips = payload.get("ClipDataList", []) if isinstance(payload, dict) else []
        for clip in clips if isinstance(clips, list) else []:
            if not isinstance(clip, dict):
                continue
            clip_count += 1
            clip_type_counts[str(clip.get("ClipType", ""))] += 1
            args = clip.get("args", {})
            if not isinstance(args, dict):
                continue
            res_name = str(args.get("res_Name", "") or "")
            if res_name and res_name != "None":
                effect_resources.append(res_name)
            action_name = str(args.get("action_Name", "") or "")
            if action_name and action_name != "None":
                action_names.append(action_name)
            sound_id = args.get("Sound_Id")
            if sound_id not in (None, "", 0):
                sound_ids.append(str(sound_id))
    clip_type_text = "、".join(f"{kind}:{count}" for kind, count in clip_type_counts.most_common())
    return {
        "track_count": len(track_items) if isinstance(track_items, list) else 0,
        "clip_count": clip_count,
        "clip_type_counts": clip_type_text,
        "effect_resources": _join_unique(effect_resources, limit=30),
        "action_names": _join_unique(action_names, limit=20),
        "sound_ids": _join_unique(sound_ids, limit=20),
    }


def _timeline_clip_role(clip_type: Any) -> str:
    return {
        "0": "action",
        "1": "sound",
        "2": "effect",
        "3": "trajectory",
        "4": "movement",
        "5": "camera_shake",
        "6": "post_effect",
        "7": "hit_frame",
        "8": "keyframe",
        "13": "action_speed",
        "23": "character_display",
        "26": "move_to_target",
        "27": "camera_control",
    }.get(str(clip_type), f"clip_type_{clip_type}")


def _iter_timeline_track_clips(value: Any, *, track_side: str) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        track_items = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    rows: list[dict[str, Any]] = []
    for track_index, item in enumerate(track_items if isinstance(track_items, list) else [], start=1):
        try:
            track = json.loads(item) if isinstance(item, str) else item
            track_value = track.get("TrackValue", {}) if isinstance(track, dict) else {}
            payload = json.loads(track_value) if isinstance(track_value, str) else track_value
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        clips = payload.get("ClipDataList", [])
        for clip_index, clip in enumerate(clips if isinstance(clips, list) else [], start=1):
            if not isinstance(clip, dict):
                continue
            args = clip.get("args", {})
            if not isinstance(args, dict):
                args = {}
            clip_type = clip.get("ClipType", "")
            rows.append(
                {
                    "track_side": track_side,
                    "track_index": track_index,
                    "track_name": track.get("TrackName", "") if isinstance(track, dict) else "",
                    "parent_name": payload.get("ParentName", ""),
                    "track_sub_name": payload.get("SubName", ""),
                    "track_payload_name": payload.get("Name", ""),
                    "track_type": payload.get("TracType", payload.get("TrackType", "")),
                    "track_frame_count": payload.get("FrameCount", ""),
                    "track_total_time": payload.get("TotalTime", ""),
                    "clip_index": clip_index,
                    "clip_type": clip_type,
                    "clip_role": _timeline_clip_role(clip_type),
                    "start_time": clip.get("Start_Time", ""),
                    "start_frame": clip.get("Start_Frame", ""),
                    "end_time": clip.get("End_Time", ""),
                    "end_frame": clip.get("End_Frame", ""),
                    "duration": clip.get("Duration", ""),
                    "loop": clip.get("Loop", ""),
                    "loop_count": clip.get("LoopCount", ""),
                    "end_play": clip.get("EndPlay", ""),
                    "clip_id": args.get("Clip_ID", ""),
                    "link_id": args.get("Link_ID", ""),
                    "res_name": args.get("res_Name", ""),
                    "random_res_names": args.get("random_res_names", ""),
                    "action_name": args.get("action_Name", ""),
                    "sound_id": args.get("Sound_Id", ""),
                    "hit_effect_sound": args.get("Hit_Effect_Sound", ""),
                    "frame": args.get("Frame", ""),
                    "hurt_index": args.get("Hurt_Index", ""),
                    "hurt_percent": args.get("Hurt_Precent", ""),
                    "hurt_multi_count": args.get("Hurt_Multi_Count", ""),
                    "hurt_multi_duration": args.get("Hurt_Multi_Duration", ""),
                    "real_multi_hurt": args.get("Real_Multi_Hurt", ""),
                    "damage_center_type": args.get("Damage_Center_Type", ""),
                    "damage_scope_type": args.get("Damage_Scope_Type", ""),
                    "scope_param1": args.get("Scope_Param1", ""),
                    "scope_param2": args.get("Scope_Param2", ""),
                    "trajectory_index": args.get("Trajectory_Index", ""),
                    "trajectory_type": args.get("Trajectory_Type", ""),
                    "fly_speed": args.get("Fly_Speed", ""),
                    "bind_target": args.get("bind_Target", ""),
                    "end_bind_target": args.get("End_Bind_Target", ""),
                    "is_bind": args.get("isBind", ""),
                    "is_hit_effect": args.get("Is_Hit_Effect", ""),
                    "effect_start_type": args.get("Effect_Start_Type", ""),
                    "effect_last_time": args.get("Effect_Last_Time", ""),
                    "args_json": _cell_json(args),
                }
            )
    return rows


def _timeline_source_path(root: Path, timeline_id: str) -> Path | None:
    matches = sorted((root / "by_source" / "lscripts" / "gamesystem" / "game").glob(f"*/text_assets/{timeline_id}.lua"))
    return matches[0] if matches else None


def _timeline_playable_asset_path(timeline_id: str, resource_root: str | Path | None = None) -> str:
    resource_root = resolve_fanxiu_resource_root(resource_root)
    if not resource_root.is_dir():
        return ""
    matches = sorted((resource_root / "playable" / "skill").glob(f"timeline{timeline_id}_*.bytes"))
    if not matches:
        return ""
    return matches[0].relative_to(resource_root).as_posix()


def _split_joined_items(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split("、") if item.strip()]


def _effect_asset_matches(resource_root: Path, effect_resource: str) -> list[Path]:
    normalized = str(effect_resource or "").replace("\\", "/").strip("/")
    if not normalized:
        return []
    parts = [part for part in normalized.split("/") if part and part not in {".", ".."}]
    if not parts:
        return []
    if parts[0].lower() == "effect":
        parts = parts[1:]
    rel_parent = Path(*parts[:-1]) if len(parts) > 1 else Path()
    stem = parts[-1]
    base_dir = resource_root / "effect" / rel_parent
    if not base_dir.is_dir():
        return []
    matches = sorted(base_dir.glob(f"{stem}_*.bytes"))
    exact = base_dir / f"{stem}.bytes"
    if exact.is_file():
        matches.append(exact)
    return matches


def _build_projected_timeline_detail_rows(
    root: Path,
    skill_next_hop_rows: list[dict[str, Any]],
    *,
    resource_root: str | Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    timeline_to_skills: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in skill_next_hop_rows:
        for item in str(row.get("timeline_ids") or "").split("、"):
            if ":" not in item:
                continue
            career, timeline_id = item.split(":", 1)
            if not timeline_id:
                continue
            timeline_to_skills[timeline_id].append(
                {
                    "career": career,
                    "projected_skill_id": row.get("projected_skill_id", ""),
                    "skill_name": row.get("skill_name", ""),
                    "gongfa_id": row.get("gongfa_id", ""),
                    "star": row.get("star", ""),
                }
            )

    rows: list[dict[str, Any]] = []
    missing_lua_count = 0
    playable_asset_count = 0
    for timeline_id, refs in sorted(timeline_to_skills.items(), key=lambda item: item[0]):
        source_path = _timeline_source_path(root, timeline_id)
        if not source_path:
            missing_lua_count += 1
            rows.append(
                {
                    "timeline_id": timeline_id,
                    "status": "missing_lua",
                    "projected_skill_count": len({str(ref["projected_skill_id"]) for ref in refs}),
                    "careers": _join_unique([ref["career"] for ref in refs], limit=10),
                    "sample_skills": _join_unique([f"{ref['projected_skill_id']}:{ref['skill_name']}" for ref in refs], limit=8),
                }
            )
            continue
        parsed = parse_fanxiu_generated_lua_config(source_path)
        timeline_row = parsed["rows"][0] if parsed.get("rows") else {}
        attack_track = _parse_timeline_track_json(timeline_row.get("q_timeline_attacktrack"))
        suffer_track = _parse_timeline_track_json(timeline_row.get("q_timeline_suffertrack"))
        playable_asset = _timeline_playable_asset_path(timeline_id, resource_root=resource_root)
        if playable_asset:
            playable_asset_count += 1
        effect_resources = _join_unique(
            [
                item
                for item in [
                    attack_track.get("effect_resources", ""),
                    suffer_track.get("effect_resources", ""),
                ]
                if item
            ],
            limit=60,
        )
        rows.append(
            {
                "timeline_id": timeline_id,
                "status": "ok",
                "projected_skill_count": len({str(ref["projected_skill_id"]) for ref in refs}),
                "careers": _join_unique([ref["career"] for ref in refs], limit=10),
                "sample_skills": _join_unique([f"{ref['projected_skill_id']}:{ref['skill_name']}" for ref in refs], limit=8),
                "source_lua": source_path.relative_to(root).as_posix(),
                "playable_asset": playable_asset,
                "q_type": timeline_row.get("q_type", ""),
                "q_desc": timeline_row.get("q_desc", ""),
                "q_track_time": timeline_row.get("q_track_time", ""),
                "q_keyframe_events": _cell_json(timeline_row.get("q_keyframe_events", "")),
                "q_hurt_events": _cell_json(timeline_row.get("q_hurt_events", "")),
                "hurt_event_count": len(timeline_row.get("q_hurt_events", []) or []),
                "display_name": timeline_row.get("q_timeline_displayName", ""),
                "attack_track_count": attack_track.get("track_count", 0),
                "attack_clip_count": attack_track.get("clip_count", 0),
                "attack_clip_types": attack_track.get("clip_type_counts", ""),
                "suffer_track_count": suffer_track.get("track_count", 0),
                "suffer_clip_count": suffer_track.get("clip_count", 0),
                "suffer_clip_types": suffer_track.get("clip_type_counts", ""),
                "effect_resources": effect_resources,
                "action_names": _join_unique(
                    [
                        item
                        for item in [
                            attack_track.get("action_names", ""),
                            suffer_track.get("action_names", ""),
                        ]
                        if item
                    ],
                    limit=30,
                ),
                "sound_ids": _join_unique(
                    [
                        item
                        for item in [
                            attack_track.get("sound_ids", ""),
                            suffer_track.get("sound_ids", ""),
                        ]
                        if item
                    ],
                    limit=30,
                ),
            }
        )
    return rows, {
        "status": "ok",
        "timeline_id_count": len(timeline_to_skills),
        "missing_lua_count": missing_lua_count,
        "playable_asset_count": playable_asset_count,
    }


def _build_timeline_clip_event_rows(
    root: Path,
    timeline_detail_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    missing_source_count = 0
    for timeline_detail in timeline_detail_rows:
        if timeline_detail.get("status") != "ok":
            continue
        timeline_id = str(timeline_detail.get("timeline_id") or "")
        source_lua = str(timeline_detail.get("source_lua") or "")
        source_path = (root / source_lua).resolve() if source_lua else None
        if not source_path or not _is_relative_to(source_path, root) or not source_path.is_file():
            missing_source_count += 1
            continue
        parsed = parse_fanxiu_generated_lua_config(source_path)
        timeline_row = parsed["rows"][0] if parsed.get("rows") else {}
        clip_rows = [
            *_iter_timeline_track_clips(timeline_row.get("q_timeline_attacktrack"), track_side="attack"),
            *_iter_timeline_track_clips(timeline_row.get("q_timeline_suffertrack"), track_side="suffer"),
        ]
        for clip_row in clip_rows:
            rows.append(
                {
                    "timeline_id": timeline_id,
                    "careers": timeline_detail.get("careers", ""),
                    "q_desc": timeline_detail.get("q_desc", ""),
                    "sample_skills": timeline_detail.get("sample_skills", ""),
                    "source_lua": source_lua,
                    **clip_row,
                }
            )
    return rows, {
        "status": "ok",
        "clip_count": len(rows),
        "missing_source_count": missing_source_count,
    }


def _build_timeline_clip_type_summary_rows(timeline_clip_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in timeline_clip_rows:
        clip_type = row.get("clip_type")
        grouped["" if clip_type is None else str(clip_type)].append(row)

    def sort_key(item: tuple[str, list[dict[str, Any]]]) -> tuple[int, str]:
        clip_type = item[0]
        if clip_type.isdigit():
            return (int(clip_type), "")
        return (999999, clip_type)

    rows: list[dict[str, Any]] = []
    for clip_type, clip_rows in sorted(grouped.items(), key=sort_key):
        args_keys: set[str] = set()
        for row in clip_rows:
            try:
                args = json.loads(str(row.get("args_json") or "{}"))
            except json.JSONDecodeError:
                args = {}
            if isinstance(args, dict):
                args_keys.update(str(key) for key in args)
        sample = clip_rows[0] if clip_rows else {}
        rows.append(
            {
                "clip_type": clip_type,
                "clip_role": sample.get("clip_role", _timeline_clip_role(clip_type)),
                "clip_count": len(clip_rows),
                "track_sides": _join_unique([row.get("track_side", "") for row in clip_rows], limit=10),
                "track_names": _join_unique([row.get("track_name", "") for row in clip_rows], limit=20),
                "track_payload_names": _join_unique([row.get("track_payload_name", "") for row in clip_rows], limit=20),
                "args_keys": _join_unique(sorted(args_keys), limit=120),
                "sample_timeline_id": sample.get("timeline_id", ""),
                "sample_track_side": sample.get("track_side", ""),
                "sample_track_name": sample.get("track_name", ""),
                "sample_start_frame": sample.get("start_frame", ""),
                "sample_end_frame": sample.get("end_frame", ""),
                "sample_res_name": sample.get("res_name", ""),
                "sample_action_name": sample.get("action_name", ""),
                "sample_sound_id": sample.get("sound_id", ""),
                "sample_hurt_percent": sample.get("hurt_percent", ""),
                "sample_args_json": sample.get("args_json", ""),
            }
        )
    return rows


def _parse_json_list_cell(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        if parsed and not isinstance(parsed[0], (list, dict)):
            return [parsed]
        return parsed
    if isinstance(parsed, dict):
        return [
            item[1]
            for item in sorted(
                parsed.items(),
                key=lambda item: (0, int(str(item[0]))) if str(item[0]).isdigit() else (1, str(item[0])),
            )
        ]
    return []


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_timeline_hit_frame_rows(
    timeline_detail_rows: list[dict[str, Any]],
    timeline_clip_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    detail_by_timeline = {str(row.get("timeline_id") or ""): row for row in timeline_detail_rows}
    rows: list[dict[str, Any]] = []
    matched_count = 0
    total_hurt_event_count = 0
    for detail in timeline_detail_rows:
        total_hurt_event_count += len(_parse_json_list_cell(detail.get("q_hurt_events")))

    for hit_index, clip in enumerate((row for row in timeline_clip_rows if row.get("clip_role") == "hit_frame"), start=1):
        timeline_id = str(clip.get("timeline_id") or "")
        detail = detail_by_timeline.get(timeline_id, {})
        hurt_events = _parse_json_list_cell(detail.get("q_hurt_events"))
        frame = _safe_float(clip.get("frame") or clip.get("start_frame"))
        frame_time_ms = round(frame * 1000 / 30, 3) if frame is not None else ""
        nearest_index = ""
        nearest_event: Any = ""
        nearest_time_ms: Any = ""
        nearest_frame: Any = ""
        nearest_delta_ms: Any = ""
        if frame_time_ms != "":
            candidates: list[tuple[float, int, Any, float]] = []
            for event_index, event in enumerate(hurt_events, start=1):
                if not isinstance(event, list) or not event:
                    continue
                event_time = _safe_float(event[0])
                if event_time is None:
                    continue
                candidates.append((abs(event_time - float(frame_time_ms)), event_index, event, event_time))
            if candidates:
                delta, nearest_index, nearest_event, nearest_time = min(candidates, key=lambda item: item[0])
                nearest_time_ms = nearest_time
                nearest_frame = round(nearest_time * 30 / 1000, 3)
                nearest_delta_ms = round(delta, 3)
                if delta <= 34:
                    matched_count += 1
        rows.append(
            {
                "timeline_id": timeline_id,
                "careers": clip.get("careers", ""),
                "q_desc": clip.get("q_desc", ""),
                "sample_skills": clip.get("sample_skills", ""),
                "source_lua": clip.get("source_lua", ""),
                "hit_index": hit_index,
                "track_side": clip.get("track_side", ""),
                "track_name": clip.get("track_name", ""),
                "start_frame": clip.get("start_frame", ""),
                "end_frame": clip.get("end_frame", ""),
                "frame": clip.get("frame", ""),
                "frame_time_ms": frame_time_ms,
                "hurt_percent": clip.get("hurt_percent", ""),
                "hurt_multi_count": clip.get("hurt_multi_count", ""),
                "hurt_multi_duration": clip.get("hurt_multi_duration", ""),
                "real_multi_hurt": clip.get("real_multi_hurt", ""),
                "damage_center_type": clip.get("damage_center_type", ""),
                "damage_scope_type": clip.get("damage_scope_type", ""),
                "scope_param1": clip.get("scope_param1", ""),
                "scope_param2": clip.get("scope_param2", ""),
                "trajectory_index": clip.get("trajectory_index", ""),
                "hit_effect_sound": clip.get("hit_effect_sound", ""),
                "hurt_event_index": nearest_index,
                "hurt_event_time_ms": nearest_time_ms,
                "hurt_event_frame": nearest_frame,
                "hurt_event_delta_ms": nearest_delta_ms,
                "hurt_event_values": _cell_json(nearest_event),
                "args_json": clip.get("args_json", ""),
            }
        )
    return rows, {
        "status": "ok",
        "hit_frame_count": len(rows),
        "matched_hurt_event_count": matched_count,
        "total_hurt_event_count": total_hurt_event_count,
        "timeline_count": len({row.get("timeline_id") for row in rows}),
    }


def _expected_times_from_channel(channel: str, *, count: int) -> list[float]:
    if not channel.startswith("BYPERIOD|"):
        return []
    number_text = channel.split("|", 1)[1]
    numbers = [_safe_float(part.strip()) for part in number_text.split(",") if part.strip()]
    values = [number for number in numbers if number is not None]
    if not values:
        return []
    times = [values[0]]
    if len(values) == 1:
        return times
    current = values[0]
    deltas = values[1:]
    while len(times) < count:
        delta = deltas[min(len(times) - 1, len(deltas) - 1)]
        current += delta
        times.append(current)
    return times


def _build_timeline_channel_alignment_rows(
    timeline_detail_rows: list[dict[str, Any]],
    skill_next_hop_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    channel_refs: dict[str, dict[str, Any]] = defaultdict(lambda: {"channels": set(), "refs": []})
    for row in skill_next_hop_rows:
        for item in _split_joined_items(row.get("timeline_channels")):
            parts = item.split(":", 2)
            if len(parts) != 3:
                continue
            career, timeline_id, channel = parts
            channel_refs[timeline_id]["channels"].add(channel)
            channel_refs[timeline_id]["refs"].append(
                f"{career}:{row.get('projected_skill_id', '')}:{row.get('skill_name', '')}:star={row.get('star', '')}"
            )

    rows: list[dict[str, Any]] = []
    matched_count = 0
    for detail in timeline_detail_rows:
        if detail.get("status") != "ok":
            continue
        timeline_id = str(detail.get("timeline_id") or "")
        hurt_events = _parse_json_list_cell(detail.get("q_hurt_events"))
        hurt_times = [
            _safe_float(event[0])
            for event in hurt_events
            if isinstance(event, list) and event and _safe_float(event[0]) is not None
        ]
        channels = sorted(str(channel) for channel in channel_refs[timeline_id]["channels"])
        if not channels:
            rows.append(
                {
                    "timeline_id": timeline_id,
                    "status": "missing_channel",
                    "careers": detail.get("careers", ""),
                    "q_desc": detail.get("q_desc", ""),
                    "hurt_event_count": len(hurt_times),
                    "hurt_event_times_ms": _join_items(hurt_times, limit=80),
                    "channels": "",
                    "expected_times_ms": "",
                    "delta_ms": "",
                    "source_refs": "",
                }
            )
            continue

        for channel in channels:
            expected = _expected_times_from_channel(channel, count=len(hurt_times))
            deltas = [
                round(abs(float(hurt_time) - float(expected_time)), 3)
                for hurt_time, expected_time in zip(hurt_times, expected)
            ]
            count_matches = len(expected) == len(hurt_times)
            time_matches = bool(deltas) and all(delta <= 34 for delta in deltas)
            status = "matched" if count_matches and time_matches else ("count_mismatch" if not count_matches else "time_mismatch")
            if status == "matched":
                matched_count += 1
            rows.append(
                {
                    "timeline_id": timeline_id,
                    "status": status,
                    "careers": detail.get("careers", ""),
                    "q_desc": detail.get("q_desc", ""),
                    "hurt_event_count": len(hurt_times),
                    "hurt_event_times_ms": _join_items(hurt_times, limit=80),
                    "channels": channel,
                    "expected_times_ms": _join_items([round(value, 3) for value in expected], limit=80),
                    "delta_ms": _join_items(deltas, limit=80),
                    "source_refs": _join_unique(channel_refs[timeline_id]["refs"], limit=20),
                }
            )
    return rows, {
        "status": "ok",
        "row_count": len(rows),
        "matched_count": matched_count,
        "missing_channel_count": sum(1 for row in rows if row.get("status") == "missing_channel"),
    }


def _build_projected_skill_damage_profile_rows(
    skill_next_hop_rows: list[dict[str, Any]],
    timeline_detail_rows: list[dict[str, Any]],
    timeline_hit_frame_rows: list[dict[str, Any]],
    timeline_channel_alignment_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    detail_by_timeline = {str(row.get("timeline_id") or ""): row for row in timeline_detail_rows}
    hits_by_timeline: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in timeline_hit_frame_rows:
        hits_by_timeline[str(row.get("timeline_id") or "")].append(row)
    for rows in hits_by_timeline.values():
        rows.sort(
            key=lambda row: (
                _safe_float(row.get("hurt_event_index")) or 999999,
                _safe_float(row.get("frame")) or 999999,
            )
        )
    alignment_by_timeline_channel = {
        (str(row.get("timeline_id") or ""), str(row.get("channels") or "")): row
        for row in timeline_channel_alignment_rows
    }

    rows: list[dict[str, Any]] = []
    missing_timeline_count = 0
    missing_channel_count = 0
    for skill_row in skill_next_hop_rows:
        if skill_row.get("next_hop_kind") != "timeline_channel":
            continue
        channel_by_ref: dict[tuple[str, str], str] = {}
        for channel_item in _split_joined_items(skill_row.get("timeline_channels")):
            parts = channel_item.split(":", 2)
            if len(parts) == 3:
                channel_by_ref[(parts[0], parts[1])] = parts[2]

        for timeline_item in _split_joined_items(skill_row.get("timeline_ids")):
            parts = timeline_item.split(":", 1)
            if len(parts) != 2:
                continue
            career, timeline_id = parts
            detail = detail_by_timeline.get(timeline_id, {})
            if detail.get("status") != "ok":
                missing_timeline_count += 1
            channel = channel_by_ref.get((career, timeline_id), "")
            if not channel:
                missing_channel_count += 1
            alignment = alignment_by_timeline_channel.get((timeline_id, channel), {})
            hits = hits_by_timeline.get(timeline_id, [])
            hurt_percents = [
                value
                for value in (_safe_float(hit.get("hurt_percent")) for hit in hits)
                if value is not None
            ]
            hit_times = [
                value
                for value in (_safe_float(hit.get("hurt_event_time_ms") or hit.get("frame_time_ms")) for hit in hits)
                if value is not None
            ]
            hit_frames = [
                value
                for value in (_safe_float(hit.get("frame")) for hit in hits)
                if value is not None
            ]
            multi_counts = [
                value
                for value in (_safe_float(hit.get("hurt_multi_count")) for hit in hits)
                if value is not None
            ]
            rows.append(
                {
                    "gongfa_id": skill_row.get("gongfa_id", ""),
                    "star": skill_row.get("star", ""),
                    "lingjie_star_id": skill_row.get("lingjie_star_id", ""),
                    "projected_skill_id": skill_row.get("projected_skill_id", ""),
                    "skill_name": skill_row.get("skill_name", ""),
                    "skill_type": skill_row.get("skill_type", ""),
                    "career": career,
                    "timeline_id": timeline_id,
                    "timeline_status": detail.get("status", "missing_timeline"),
                    "q_desc": detail.get("q_desc", ""),
                    "channel": channel,
                    "channel_alignment_status": alignment.get("status", "missing_channel"),
                    "hit_count": len(hits),
                    "first_hit_ms": min(hit_times) if hit_times else "",
                    "last_hit_ms": max(hit_times) if hit_times else "",
                    "hit_times_ms": _join_items([round(value, 3) for value in hit_times], limit=80),
                    "hit_frames": _join_items([round(value, 3) for value in hit_frames], limit=80),
                    "hurt_percents": _join_items([int(value) if value.is_integer() else value for value in hurt_percents], limit=80),
                    "total_hurt_percent": round(sum(hurt_percents), 3) if hurt_percents else "",
                    "multi_hit_counts": _join_items([int(value) if value.is_integer() else value for value in multi_counts], limit=80),
                    "damage_scope_types": _join_unique([hit.get("damage_scope_type", "") for hit in hits], limit=20),
                    "scope_params": _join_unique(
                        [
                            f"{hit.get('scope_param1', '')},{hit.get('scope_param2', '')}"
                            for hit in hits
                            if hit.get("scope_param1", "") != "" or hit.get("scope_param2", "") != ""
                        ],
                        limit=20,
                    ),
                    "trajectory_indexes": _join_unique([hit.get("trajectory_index", "") for hit in hits], limit=20),
                    "hit_effect_sounds": _join_unique([hit.get("hit_effect_sound", "") for hit in hits], limit=20),
                    "expected_times_ms": alignment.get("expected_times_ms", ""),
                    "channel_delta_ms": alignment.get("delta_ms", ""),
                    "effect_resources": detail.get("effect_resources", ""),
                    "sound_ids": detail.get("sound_ids", ""),
                    "cd_time": skill_row.get("cd_time", ""),
                    "public_cd_group": skill_row.get("public_cd_group", ""),
                    "public_cd": skill_row.get("public_cd", ""),
                    "scope": skill_row.get("scope", ""),
                    "target_type": skill_row.get("target_type", ""),
                    "target_max": skill_row.get("target_max", ""),
                    "fight_score": skill_row.get("fight_score", ""),
                }
            )

    rows.sort(
        key=lambda row: (
            str(row["gongfa_id"]),
            str(row["star"]).zfill(8) if str(row["star"]).isdigit() else str(row["star"]),
            str(row["projected_skill_id"]),
            str(row["career"]),
            str(row["timeline_id"]),
        )
    )
    return rows, {
        "status": "ok",
        "profile_count": len(rows),
        "timeline_count": len({row.get("timeline_id") for row in rows}),
        "skill_count": len({row.get("projected_skill_id") for row in rows}),
        "missing_timeline_count": missing_timeline_count,
        "missing_channel_count": missing_channel_count,
    }


def _build_projected_skill_damage_family_rows(
    projected_skill_damage_profile_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    family_fields = [
        "channel",
        "hit_count",
        "hit_times_ms",
        "hurt_percents",
        "total_hurt_percent",
        "multi_hit_counts",
        "damage_scope_types",
        "scope_params",
        "scope",
        "target_type",
        "target_max",
    ]
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    skipped_count = 0
    for row in projected_skill_damage_profile_rows:
        if row.get("timeline_status") != "ok" or not row.get("hit_count"):
            skipped_count += 1
            continue
        grouped[tuple(str(row.get(field) or "") for field in family_fields)].append(row)

    rows: list[dict[str, Any]] = []
    for family_index, (key, family_rows) in enumerate(
        sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])),
        start=1,
    ):
        first = family_rows[0]
        rows.append(
            {
                "family_id": f"damage_family_{family_index:03d}",
                "profile_count": len(family_rows),
                "skill_count": len({str(row.get("projected_skill_id") or "") for row in family_rows}),
                "timeline_count": len({str(row.get("timeline_id") or "") for row in family_rows}),
                "gongfa_count": len({str(row.get("gongfa_id") or "") for row in family_rows}),
                "careers": _join_unique([row.get("career", "") for row in family_rows], limit=10),
                "sample_gongfas": _join_unique([row.get("gongfa_id", "") for row in family_rows], limit=20),
                "sample_skills": _join_unique(
                    [
                        f"{row.get('projected_skill_id', '')}:{row.get('skill_name', '')}:star={row.get('star', '')}"
                        for row in family_rows
                    ],
                    limit=20,
                ),
                "sample_timelines": _join_unique(
                    [f"{row.get('career', '')}:{row.get('timeline_id', '')}:{row.get('q_desc', '')}" for row in family_rows],
                    limit=20,
                ),
                "channel": first.get("channel", ""),
                "hit_count": first.get("hit_count", ""),
                "first_hit_ms": first.get("first_hit_ms", ""),
                "last_hit_ms": first.get("last_hit_ms", ""),
                "hit_times_ms": first.get("hit_times_ms", ""),
                "hit_frames": first.get("hit_frames", ""),
                "hurt_percents": first.get("hurt_percents", ""),
                "total_hurt_percent": first.get("total_hurt_percent", ""),
                "multi_hit_counts": first.get("multi_hit_counts", ""),
                "damage_scope_types": first.get("damage_scope_types", ""),
                "scope_params": first.get("scope_params", ""),
                "scope": first.get("scope", ""),
                "target_type": first.get("target_type", ""),
                "target_max": first.get("target_max", ""),
                "cd_times": _join_unique([row.get("cd_time", "") for row in family_rows], limit=20),
                "fight_scores": _join_unique([row.get("fight_score", "") for row in family_rows], limit=20),
            }
        )
    return rows, {
        "status": "ok",
        "family_count": len(rows),
        "skipped_profile_count": skipped_count,
    }


def _build_timeline_effect_asset_rows(
    timeline_detail_rows: list[dict[str, Any]],
    *,
    resource_root: str | Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved_root = resolve_fanxiu_resource_root(resource_root)
    root_exists = resolved_root.is_dir()
    rows: list[dict[str, Any]] = []
    unique_effects: set[str] = set()
    unique_assets: dict[str, int] = {}
    matched_ref_count = 0
    missing_ref_count = 0

    for timeline_row in timeline_detail_rows:
        timeline_id = str(timeline_row.get("timeline_id") or "")
        for effect_resource in _split_joined_items(timeline_row.get("effect_resources")):
            unique_effects.add(effect_resource)
            base_row = {
                "timeline_id": timeline_id,
                "careers": timeline_row.get("careers", ""),
                "q_desc": timeline_row.get("q_desc", ""),
                "sample_skills": timeline_row.get("sample_skills", ""),
                "effect_resource": effect_resource,
            }
            if not root_exists:
                missing_ref_count += 1
                rows.append(
                    {
                        **base_row,
                        "status": "missing_resource_root",
                        "asset_count": 0,
                        "asset_paths": "",
                        "asset_size_bytes": 0,
                    }
                )
                continue

            matches = _effect_asset_matches(resolved_root, effect_resource)
            if not matches:
                missing_ref_count += 1
                rows.append(
                    {
                        **base_row,
                        "status": "missing_asset",
                        "asset_count": 0,
                        "asset_paths": "",
                        "asset_size_bytes": 0,
                    }
                )
                continue

            matched_ref_count += 1
            asset_paths: list[str] = []
            asset_size_bytes = 0
            for match in matches:
                rel_path = match.relative_to(resolved_root).as_posix()
                try:
                    size = match.stat().st_size
                except OSError:
                    size = 0
                asset_paths.append(rel_path)
                asset_size_bytes += size
                unique_assets.setdefault(rel_path, size)
            rows.append(
                {
                    **base_row,
                    "status": "ok",
                    "asset_count": len(matches),
                    "asset_paths": _join_unique(asset_paths, limit=10),
                    "asset_size_bytes": asset_size_bytes,
                }
            )

    return rows, {
        "status": "ok" if root_exists else "missing_resource_root",
        "resource_root": str(resolved_root),
        "resource_root_exists": root_exists,
        "effect_ref_count": len(rows),
        "unique_effect_count": len(unique_effects),
        "matched_ref_count": matched_ref_count,
        "missing_ref_count": missing_ref_count,
        "unique_asset_count": len(unique_assets),
        "unique_asset_total_bytes": sum(unique_assets.values()),
    }


def _build_effect_bundle_object_rows(
    effect_asset_rows: list[dict[str, Any]],
    *,
    resource_root: str | Path | None = None,
    max_objects: int = 120,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved_root = resolve_fanxiu_resource_root(resource_root)
    root_exists = resolved_root.is_dir()
    asset_refs: dict[str, dict[str, Any]] = {}
    for row in effect_asset_rows:
        if row.get("status") != "ok":
            continue
        for asset_path in _split_joined_items(row.get("asset_paths")):
            ref = asset_refs.setdefault(
                asset_path,
                {
                    "asset_path": asset_path,
                    "timeline_ids": [],
                    "effect_resources": [],
                    "careers": [],
                    "q_descs": [],
                },
            )
            ref["timeline_ids"].append(row.get("timeline_id", ""))
            ref["effect_resources"].append(row.get("effect_resource", ""))
            ref["careers"].append(row.get("careers", ""))
            ref["q_descs"].append(row.get("q_desc", ""))

    rows: list[dict[str, Any]] = []
    ok_count = 0
    error_count = 0
    object_type_counts: Counter[str] = Counter()
    read_error_object_count = 0
    for asset_path, ref in sorted(asset_refs.items()):
        base_row = {
            "asset_path": asset_path,
            "timeline_ids": _join_unique(ref["timeline_ids"], limit=20),
            "effect_resources": _join_unique(ref["effect_resources"], limit=20),
            "careers": _join_unique(ref["careers"], limit=10),
            "q_descs": _join_unique(ref["q_descs"], limit=20),
        }
        if not root_exists:
            error_count += 1
            rows.append(
                {
                    **base_row,
                    "status": "missing_resource_root",
                    "size_bytes": 0,
                    "magic": "",
                    "offset": "",
                    "error": "",
                    "object_total": 0,
                    "object_counts": "",
                    "root_names": "",
                    "gameobject_names": "",
                    "material_names": "",
                    "texture_names": "",
                    "monoscript_names": "",
                    "read_error_object_count": 0,
                }
            )
            continue
        try:
            summary = inspect_fanxiu_unity_bundle(asset_path, resource_root=resolved_root, max_objects=max_objects)
        except Exception as exc:  # pragma: no cover - defensive around third-party bundle readers
            error_count += 1
            rows.append(
                {
                    **base_row,
                    "status": "inspect_exception",
                    "size_bytes": 0,
                    "magic": "",
                    "offset": "",
                    "error": f"{type(exc).__name__}: {exc}",
                    "object_total": 0,
                    "object_counts": "",
                    "root_names": "",
                    "gameobject_names": "",
                    "material_names": "",
                    "texture_names": "",
                    "monoscript_names": "",
                    "read_error_object_count": 0,
                }
            )
            continue

        error = str(summary.get("error") or "")
        status = "ok" if not error else "inspect_error"
        if status == "ok":
            ok_count += 1
        else:
            error_count += 1
        object_counts = summary.get("object_counts") if isinstance(summary, dict) else {}
        if isinstance(object_counts, dict):
            for type_name, count in object_counts.items():
                object_type_counts[str(type_name)] += int(count or 0)
        objects = summary.get("objects") if isinstance(summary, dict) else []
        row_read_errors = sum(1 for obj in objects if isinstance(obj, dict) and obj.get("read_error"))
        read_error_object_count += row_read_errors
        rows.append(
            {
                **base_row,
                "status": status,
                "size_bytes": summary.get("size", 0),
                "magic": summary.get("magic", ""),
                "offset": summary.get("offset", ""),
                "error": error,
                "object_total": sum(int(count or 0) for count in object_counts.values()) if isinstance(object_counts, dict) else 0,
                "object_counts": _count_map_text(object_counts),
                "root_names": _unity_object_names(objects, {"AssetBundle", "GameObject"}, limit=12),
                "gameobject_names": _unity_object_names(objects, {"GameObject"}, limit=30),
                "material_names": _unity_object_names(objects, {"Material"}, limit=30),
                "texture_names": _unity_object_names(objects, {"Texture2D"}, limit=30),
                "monoscript_names": _unity_object_names(objects, {"MonoScript"}, limit=10),
                "read_error_object_count": row_read_errors,
            }
        )

    return rows, {
        "status": "ok" if root_exists else "missing_resource_root",
        "resource_root": str(resolved_root),
        "resource_root_exists": root_exists,
        "asset_count": len(asset_refs),
        "inspected_asset_count": len(rows),
        "ok_count": ok_count,
        "error_count": error_count,
        "read_error_object_count": read_error_object_count,
        "object_type_counts": dict(object_type_counts.most_common()),
    }


def _build_playable_bundle_object_rows(
    timeline_detail_rows: list[dict[str, Any]],
    *,
    resource_root: str | Path | None = None,
    max_objects: int = 60,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved_root = resolve_fanxiu_resource_root(resource_root)
    root_exists = resolved_root.is_dir()
    rows: list[dict[str, Any]] = []
    ok_count = 0
    error_count = 0
    object_type_counts: Counter[str] = Counter()
    read_error_object_count = 0

    for timeline_row in timeline_detail_rows:
        playable_asset = str(timeline_row.get("playable_asset") or "")
        if not playable_asset:
            continue
        base_row = {
            "timeline_id": timeline_row.get("timeline_id", ""),
            "careers": timeline_row.get("careers", ""),
            "q_desc": timeline_row.get("q_desc", ""),
            "sample_skills": timeline_row.get("sample_skills", ""),
            "playable_asset": playable_asset,
        }
        if not root_exists:
            error_count += 1
            rows.append(
                {
                    **base_row,
                    "status": "missing_resource_root",
                    "size_bytes": 0,
                    "magic": "",
                    "offset": "",
                    "error": "",
                    "object_total": 0,
                    "object_counts": "",
                    "object_names": "",
                    "monoscript_names": "",
                    "read_error_object_count": 0,
                }
            )
            continue
        try:
            summary = inspect_fanxiu_unity_bundle(playable_asset, resource_root=resolved_root, max_objects=max_objects)
        except Exception as exc:  # pragma: no cover - defensive around third-party bundle readers
            error_count += 1
            rows.append(
                {
                    **base_row,
                    "status": "inspect_exception",
                    "size_bytes": 0,
                    "magic": "",
                    "offset": "",
                    "error": f"{type(exc).__name__}: {exc}",
                    "object_total": 0,
                    "object_counts": "",
                    "object_names": "",
                    "monoscript_names": "",
                    "read_error_object_count": 0,
                }
            )
            continue

        error = str(summary.get("error") or "")
        status = "ok" if not error else "inspect_error"
        if status == "ok":
            ok_count += 1
        else:
            error_count += 1
        object_counts = summary.get("object_counts") if isinstance(summary, dict) else {}
        if isinstance(object_counts, dict):
            for type_name, count in object_counts.items():
                object_type_counts[str(type_name)] += int(count or 0)
        objects = summary.get("objects") if isinstance(summary, dict) else []
        row_read_errors = sum(1 for obj in objects if isinstance(obj, dict) and obj.get("read_error"))
        read_error_object_count += row_read_errors
        rows.append(
            {
                **base_row,
                "status": status,
                "size_bytes": summary.get("size", 0),
                "magic": summary.get("magic", ""),
                "offset": summary.get("offset", ""),
                "error": error,
                "object_total": sum(int(count or 0) for count in object_counts.values()) if isinstance(object_counts, dict) else 0,
                "object_counts": _count_map_text(object_counts),
                "object_names": _unity_object_names(objects, None, limit=20),
                "monoscript_names": _unity_object_names(objects, {"MonoScript"}, limit=10),
                "read_error_object_count": row_read_errors,
            }
        )

    return rows, {
        "status": "ok" if root_exists else "missing_resource_root",
        "resource_root": str(resolved_root),
        "resource_root_exists": root_exists,
        "playable_asset_count": len(rows),
        "ok_count": ok_count,
        "error_count": error_count,
        "read_error_object_count": read_error_object_count,
        "object_type_counts": dict(object_type_counts.most_common()),
    }


def _ensure_sound_config_rows(
    root: Path,
    *,
    export_root: str | Path | None = None,
    resource_root: str | Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, parsed_status = _load_parsed_config_rows(root, "Sound")
    if rows:
        return rows, {"status": "ok", "source": "parsed_configs", "row_count": len(rows), "parsed": parsed_status}

    resolved_resource_root = resolve_fanxiu_resource_root(resource_root)
    sound_packages = sorted((resolved_resource_root / "lscripts" / "generate" / "cfg").glob("sound_*.bytes"))
    if not sound_packages:
        return [], {
            "status": "missing_sound_package",
            "source": "",
            "row_count": 0,
            "resource_root": str(resolved_resource_root),
        }

    try:
        exported = export_fanxiu_unity_text_assets(
            sound_packages[0].relative_to(resolved_resource_root).as_posix(),
            resource_root=resolved_resource_root,
            export_root=export_root,
        )
        from backend.core.fanxiu.catalog.lua_config import build_fanxiu_lua_config_batch_report

        build_fanxiu_lua_config_batch_report(config_dir=exported["output_dir"], export_root=export_root)
    except Exception as exc:
        return [], {
            "status": "parse_failed",
            "source": str(sound_packages[0]),
            "row_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    rows, parsed_status = _load_parsed_config_rows(root, "Sound")
    return rows, {
        "status": "ok" if rows else "missing_parsed_rows",
        "source": str(sound_packages[0]),
        "row_count": len(rows),
        "parsed": parsed_status,
    }


def _sound_bank_path(resource_root: Path, bank_name: str) -> Path | None:
    if not bank_name:
        return None
    bank_root = resource_root / "Audio" / "GeneratedSoundBanks" / "Android"
    direct = bank_root / f"{bank_name}.bnk"
    if direct.is_file():
        return direct
    matches = sorted(bank_root.rglob(f"{bank_name}.bnk")) if bank_root.is_dir() else []
    return matches[0] if matches else None


def _parse_wwise_hirc_links(bank_path: Path) -> dict[str, Any]:
    try:
        data = bank_path.read_bytes()
    except OSError as exc:
        return {"status": "read_failed", "error": f"{type(exc).__name__}: {exc}", "events": {}}
    hirc_pos = data.find(b"HIRC")
    didx_pos = data.find(b"DIDX")
    if hirc_pos < 0 or hirc_pos + 8 > len(data):
        return {"status": "missing_hirc", "error": "", "events": {}}

    didx_wems: dict[int, dict[str, int]] = {}
    if didx_pos >= 0 and didx_pos + 8 <= len(data):
        didx_size = struct.unpack_from("<I", data, didx_pos + 4)[0]
        didx = data[didx_pos + 8 : didx_pos + 8 + didx_size]
        for offset in range(0, len(didx) - 11, 12):
            wem_id, wem_offset, wem_size = struct.unpack_from("<III", didx, offset)
            didx_wems[wem_id] = {"offset": wem_offset, "size": wem_size}

    hirc_size = struct.unpack_from("<I", data, hirc_pos + 4)[0]
    hirc = data[hirc_pos + 8 : hirc_pos + 8 + hirc_size]
    if len(hirc) < 4:
        return {"status": "bad_hirc", "error": "HIRC payload too short", "events": {}}

    object_count = struct.unpack_from("<I", hirc, 0)[0]
    offset = 4
    actions: dict[int, int] = {}
    events: dict[int, list[int]] = {}
    sounds: dict[int, int] = {}
    objects: dict[int, tuple[int, bytes]] = {}
    for _index in range(object_count):
        if offset + 9 > len(hirc):
            break
        object_type = hirc[offset]
        object_len = struct.unpack_from("<I", hirc, offset + 1)[0]
        object_id = struct.unpack_from("<I", hirc, offset + 5)[0]
        payload_start = offset + 9
        payload_end = offset + 1 + 4 + object_len
        payload = hirc[payload_start:payload_end]
        objects[object_id] = (object_type, payload)
        if object_type == 2 and len(payload) >= 9:
            sounds[object_id] = struct.unpack_from("<I", payload, 5)[0]
        elif object_type == 3 and len(payload) >= 6:
            actions[object_id] = struct.unpack_from("<I", payload, 2)[0]
        elif object_type == 4 and len(payload) >= 1:
            action_count = payload[0]
            if 1 + action_count * 4 <= len(payload):
                events[object_id] = [
                    struct.unpack_from("<I", payload, 1 + index * 4)[0]
                    for index in range(action_count)
                ]
        offset = payload_end

    def collect_sound_object_ids(object_id: int, seen: set[int] | None = None) -> list[int]:
        seen = seen or set()
        if object_id in seen:
            return []
        seen.add(object_id)
        item = objects.get(object_id)
        if not item:
            return []
        object_type, payload = item
        if object_type == 2:
            return [object_id]
        direct_sound_refs: list[int] = []
        container_refs: list[int] = []
        for index in range(0, max(0, len(payload) - 3)):
            ref_id = struct.unpack_from("<I", payload, index)[0]
            if ref_id in objects and ref_id != object_id:
                ref_type = objects[ref_id][0]
                if ref_type == 2:
                    direct_sound_refs.append(ref_id)
                elif ref_type in {5, 6, 8, 9, 10, 11}:
                    container_refs.append(ref_id)
        if direct_sound_refs:
            return list(dict.fromkeys(direct_sound_refs))
        refs: list[int] = []
        for ref_id in dict.fromkeys(container_refs):
            refs.extend(collect_sound_object_ids(ref_id, seen))
        return list(dict.fromkeys(refs))

    event_links: dict[int, dict[str, Any]] = {}
    for event_id, action_ids in events.items():
        sound_object_ids: list[int] = []
        for action_id in action_ids:
            if action_id in actions:
                sound_object_ids.extend(collect_sound_object_ids(actions[action_id]))
        sound_object_ids = list(dict.fromkeys(sound_object_ids))
        wem_ids = [sounds[sound_object_id] for sound_object_id in sound_object_ids if sound_object_id in sounds]
        event_links[event_id] = {
            "action_ids": action_ids,
            "sound_object_ids": sound_object_ids,
            "wem_ids": wem_ids,
            "wem_ids_in_didx": [wem_id for wem_id in wem_ids if wem_id in didx_wems],
            "wem_details": [
                f"{wem_id}:offset={didx_wems[wem_id]['offset']}:size={didx_wems[wem_id]['size']}"
                for wem_id in wem_ids
                if wem_id in didx_wems
            ],
            "wem_extracts": [
                {"wem_id": wem_id, "offset": didx_wems[wem_id]["offset"], "size": didx_wems[wem_id]["size"]}
                for wem_id in wem_ids
                if wem_id in didx_wems
            ],
        }
    return {
        "status": "ok",
        "error": "",
        "events": event_links,
        "event_count": len(events),
        "action_count": len(actions),
        "sound_count": len(sounds),
        "didx_wem_count": len(didx_wems),
    }


def _wwise_data_chunk_payload(data: bytes) -> bytes:
    offset = 0
    while offset + 8 <= len(data):
        fourcc = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        payload_start = offset + 8
        payload_end = payload_start + size
        if fourcc == b"DATA":
            return data[payload_start:payload_end]
        offset = payload_end
    return b""


def _export_raw_wem_from_bank(
    bank_path: Path,
    *,
    bank_relative_path: str,
    wem_id: int,
    wem_offset: int,
    wem_size: int,
    output_dir: Path,
    export_root: Path,
) -> tuple[str, int]:
    try:
        bank_data = bank_path.read_bytes()
    except OSError:
        return "", 0
    data_payload = _wwise_data_chunk_payload(bank_data)
    if not data_payload:
        return "", 0
    wem_bytes = data_payload[int(wem_offset) : int(wem_offset) + int(wem_size)]
    if len(wem_bytes) != int(wem_size):
        return "", 0
    bank_stem = _safe_export_part(Path(bank_relative_path).stem, fallback="bank")
    target_dir = output_dir / bank_stem
    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = target_dir / f"{int(wem_id)}.wem"
    if not output_path.is_file() or output_path.stat().st_size != len(wem_bytes):
        output_path.write_bytes(wem_bytes)
    return output_path.relative_to(export_root).as_posix(), len(wem_bytes)


def _build_timeline_sound_ref_rows(
    root: Path,
    timeline_detail_rows: list[dict[str, Any]],
    *,
    export_root: str | Path | None = None,
    resource_root: str | Path | None = None,
    wem_export_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sound_rows, sound_scan = _ensure_sound_config_rows(root, export_root=export_root, resource_root=resource_root)
    sound_by_id = {str(row.get("id") or row.get("_row_key") or ""): row for row in sound_rows}
    resolved_resource_root = resolve_fanxiu_resource_root(resource_root)
    rows: list[dict[str, Any]] = []
    unique_sound_ids: set[str] = set()
    matched_count = 0
    bank_hit_count = 0
    hirc_wem_hit_count = 0
    bank_cache: dict[str, bytes | None] = {}
    hirc_cache: dict[str, dict[str, Any]] = {}
    raw_wem_cache: dict[tuple[str, int], tuple[str, int]] = {}

    for timeline_row in timeline_detail_rows:
        timeline_id = str(timeline_row.get("timeline_id") or "")
        for sound_id in _split_joined_items(timeline_row.get("sound_ids")):
            unique_sound_ids.add(sound_id)
            sound_cfg = sound_by_id.get(sound_id)
            base_row = {
                "timeline_id": timeline_id,
                "careers": timeline_row.get("careers", ""),
                "q_desc": timeline_row.get("q_desc", ""),
                "sample_skills": timeline_row.get("sample_skills", ""),
                "sound_id": sound_id,
            }
            if not sound_cfg:
                rows.append(
                    {
                        **base_row,
                        "match_status": "missing_sound_config",
                        "sound_type": "",
                        "loop": "",
                        "sound_event_name": "",
                        "sound_event_id": "",
                        "sound_bank": "",
                        "bank_path": "",
                        "bank_exists": 0,
                        "bank_size_bytes": 0,
                        "event_id_hit_in_bank": 0,
                        "hirc_status": "",
                        "hirc_action_ids": "",
                        "hirc_sound_object_ids": "",
                        "wem_ids": "",
                        "wem_ids_in_didx": "",
                        "wem_details": "",
                        "raw_wem_paths": "",
                        "raw_wem_bytes": 0,
                    }
                )
                continue

            matched_count += 1
            event_id = str(sound_cfg.get("soundEventId") or "")
            bank_name = str(sound_cfg.get("soundBank") or "")
            bank_path = _sound_bank_path(resolved_resource_root, bank_name)
            bank_exists = bank_path is not None and bank_path.is_file()
            bank_rel = bank_path.relative_to(resolved_resource_root).as_posix() if bank_exists and bank_path else ""
            bank_size = bank_path.stat().st_size if bank_exists and bank_path else 0
            event_hit = 0
            if bank_exists and bank_path and event_id.isdigit() and int(event_id) > 0:
                bank_key = str(bank_path)
                if bank_key not in bank_cache:
                    try:
                        bank_cache[bank_key] = bank_path.read_bytes()
                    except OSError:
                        bank_cache[bank_key] = None
                data = bank_cache.get(bank_key)
                if data and data.find(struct.pack("<I", int(event_id))) >= 0:
                    event_hit = 1
                    bank_hit_count += 1
            hirc_status = ""
            action_ids = ""
            sound_object_ids = ""
            wem_ids = ""
            wem_ids_in_didx = ""
            wem_details = ""
            raw_wem_paths = ""
            raw_wem_bytes = 0
            if bank_exists and bank_path and event_id.isdigit() and int(event_id) > 0:
                bank_key = str(bank_path)
                if bank_key not in hirc_cache:
                    hirc_cache[bank_key] = _parse_wwise_hirc_links(bank_path)
                hirc = hirc_cache[bank_key]
                hirc_status = str(hirc.get("status") or "")
                link = hirc.get("events", {}).get(int(event_id)) if isinstance(hirc.get("events"), dict) else None
                if isinstance(link, dict):
                    action_ids = _join_unique(link.get("action_ids", []), limit=20)
                    sound_object_ids = _join_unique(link.get("sound_object_ids", []), limit=20)
                    wem_ids = _join_unique(link.get("wem_ids", []), limit=20)
                    wem_ids_in_didx = _join_unique(link.get("wem_ids_in_didx", []), limit=20)
                    wem_details = _join_unique(link.get("wem_details", []), limit=20)
                    raw_paths: list[str] = []
                    for wem_item in link.get("wem_extracts", []):
                        if not isinstance(wem_item, dict):
                            continue
                        try:
                            wem_id = int(wem_item["wem_id"])
                            wem_offset = int(wem_item["offset"])
                            wem_size = int(wem_item["size"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        cache_key = (str(bank_path), wem_id)
                        if cache_key not in raw_wem_cache:
                            if wem_export_dir is not None:
                                raw_wem_cache[cache_key] = _export_raw_wem_from_bank(
                                    bank_path,
                                    bank_relative_path=bank_rel,
                                    wem_id=wem_id,
                                    wem_offset=wem_offset,
                                    wem_size=wem_size,
                                    output_dir=wem_export_dir,
                                    export_root=root,
                                )
                            else:
                                raw_wem_cache[cache_key] = ("", 0)
                        raw_path, raw_size = raw_wem_cache[cache_key]
                        if raw_path:
                            raw_paths.append(raw_path)
                            raw_wem_bytes += raw_size
                    if wem_ids:
                        hirc_wem_hit_count += 1
                    raw_wem_paths = _join_unique(raw_paths, limit=20)
            rows.append(
                {
                    **base_row,
                    "match_status": "matched",
                    "sound_type": sound_cfg.get("type", ""),
                    "loop": sound_cfg.get("loop", ""),
                    "sound_event_name": sound_cfg.get("soundEventName", ""),
                    "sound_event_id": event_id,
                    "sound_bank": bank_name,
                    "bank_path": bank_rel,
                    "bank_exists": 1 if bank_exists else 0,
                    "bank_size_bytes": bank_size,
                    "event_id_hit_in_bank": event_hit,
                    "hirc_status": hirc_status,
                    "hirc_action_ids": action_ids,
                    "hirc_sound_object_ids": sound_object_ids,
                    "wem_ids": wem_ids,
                    "wem_ids_in_didx": wem_ids_in_didx,
                    "wem_details": wem_details,
                    "raw_wem_paths": raw_wem_paths,
                    "raw_wem_bytes": raw_wem_bytes,
                }
            )

    exported_wems = {path: size for path, size in raw_wem_cache.values() if path}
    return rows, {
        "status": sound_scan.get("status", ""),
        "sound_config_scan": sound_scan,
        "sound_ref_count": len(rows),
        "unique_sound_id_count": len(unique_sound_ids),
        "matched_count": matched_count,
        "missing_count": len(rows) - matched_count,
        "event_id_bank_hit_count": bank_hit_count,
        "hirc_wem_hit_count": hirc_wem_hit_count,
        "raw_wem_export_count": len(exported_wems),
        "raw_wem_export_bytes": sum(exported_wems.values()),
    }


def _is_written_field(text: str, field_name: str) -> bool:
    return re.search(rf"self:write[A-Za-z0-9_]+\(\s*self\.{re.escape(field_name)}\b", text) is not None


def _load_packet_source(root: Path, relative_path: str) -> str:
    if not relative_path:
        return ""
    path = (root / relative_path).resolve()
    if not _is_relative_to(path, root) or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _resolve_packet_index_dir(
    packet_index_dir: str | Path | None,
    *,
    export_root: str | Path | None,
    source_dir: str | Path | None,
) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    raw_path = Path(packet_index_dir) if packet_index_dir else Path("parsed_configs/lua_packet_index")
    resolved = raw_path.expanduser().resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    if not _is_relative_to(resolved, root):
        raise FanxiuResourceError(f"目录必须位于导出根目录内：{root}")
    if not (resolved / "packets.tsv").is_file() or not (resolved / "packet_fields.tsv").is_file():
        from backend.core.fanxiu.catalog.lua_packet_index import build_fanxiu_lua_packet_index

        build_fanxiu_lua_packet_index(source_dir=source_dir, export_root=export_root)
    if not resolved.is_dir():
        raise FanxiuResourceError(f"目录不存在：{resolved}")
    return resolved


def build_fanxiu_lua_logic_index(
    *,
    source_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    resolved_source_dir = _resolve_export_dir(source_dir, DEFAULT_LUA_LOGIC_DIR, export_root=export_root)
    out_dir = root / "parsed_configs" / "lua_logic_index"
    out_dir.mkdir(parents=True, exist_ok=True)

    lua_files = sorted(resolved_source_dir.glob("*/text_assets/*.lua"))
    file_rows: list[dict[str, Any]] = []
    config_ref_rows: list[dict[str, Any]] = []
    function_rows: list[dict[str, Any]] = []
    config_summary: dict[str, dict[str, Any]] = defaultdict(lambda: {"ref_count": 0, "files": [], "bundles": []})

    for path in lua_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        rel_path = path.relative_to(root).as_posix()
        bundle = path.parent.parent.name if path.parent.name == "text_assets" else path.parent.name
        package_name = next((match.group(1) for match in _PACKAGE_RE.finditer(text)), "")

        config_hits = _line_numbers_for(_CONFIG_REF_RE, lines, 1)
        function_hits = _line_numbers_for(_FUNCTION_RE, lines, 1)
        require_hits = _line_numbers_for(_REQUIRE_RE, lines, 1)
        configs_by_name: dict[str, list[int]] = defaultdict(list)
        for line_no, config_name in config_hits:
            configs_by_name[config_name].append(line_no)
        for config_name, line_numbers in configs_by_name.items():
            config_ref_rows.append(
                {
                    "config_name": config_name,
                    "bundle": bundle,
                    "file": path.name,
                    "relative_path": rel_path,
                    "ref_count": len(line_numbers),
                    "lines": _join_unique(line_numbers, limit=30),
                }
            )
            summary = config_summary[config_name]
            summary["ref_count"] += len(line_numbers)
            summary["files"].append(path.name)
            summary["bundles"].append(bundle)

        for line_no, function_name in function_hits:
            function_rows.append(
                {
                    "function_name": function_name,
                    "bundle": bundle,
                    "file": path.name,
                    "relative_path": rel_path,
                    "line": line_no,
                }
            )

        file_rows.append(
            {
                "bundle": bundle,
                "file": path.name,
                "relative_path": rel_path,
                "package": package_name,
                "line_count": len(lines),
                "config_ref_count": len(config_hits),
                "config_names": _join_unique([name for _line, name in config_hits], limit=80),
                "function_count": len(function_hits),
                "functions": _join_unique([name for _line, name in function_hits], limit=80),
                "require_count": len(require_hits),
                "requires": _join_unique([name for _line, name in require_hits], limit=80),
            }
        )

    summary_rows = [
        {
            "config_name": config_name,
            "ref_count": data["ref_count"],
            "file_count": len(set(data["files"])),
            "bundles": _join_unique(data["bundles"], limit=50),
            "files": _join_unique(data["files"], limit=80),
        }
        for config_name, data in config_summary.items()
    ]
    summary_rows.sort(key=lambda row: (-int(row["ref_count"]), str(row["config_name"])))
    config_ref_rows.sort(key=lambda row: (str(row["config_name"]), str(row["bundle"]), str(row["file"])))
    function_rows.sort(key=lambda row: (str(row["bundle"]), str(row["file"]), int(row["line"])))

    bundle_counts = Counter(row["bundle"] for row in file_rows)
    stats = {
        "lua_file_count": len(file_rows),
        "bundle_count": len(bundle_counts),
        "config_name_count": len(summary_rows),
        "config_ref_count": sum(int(row["ref_count"]) for row in summary_rows),
        "function_count": len(function_rows),
        "top_bundles": dict(bundle_counts.most_common(20)),
    }

    files_path = out_dir / "lua_files.tsv"
    refs_path = out_dir / "config_refs.tsv"
    summary_path = out_dir / "config_summary.tsv"
    functions_path = out_dir / "functions.tsv"
    json_path = out_dir / "lua_logic_index.json"
    report_path = out_dir / "lua_logic_index_report.md"

    _write_tsv(
        files_path,
        file_rows,
        [
            "bundle",
            "file",
            "relative_path",
            "package",
            "line_count",
            "config_ref_count",
            "config_names",
            "function_count",
            "functions",
            "require_count",
            "requires",
        ],
    )
    _write_tsv(refs_path, config_ref_rows, ["config_name", "bundle", "file", "relative_path", "ref_count", "lines"])
    _write_tsv(summary_path, summary_rows, ["config_name", "ref_count", "file_count", "bundles", "files"])
    _write_tsv(functions_path, function_rows, ["function_name", "bundle", "file", "relative_path", "line"])
    json_path.write_text(
        json.dumps(
            {
                "source_dir": str(resolved_source_dir),
                "stats": stats,
                "top_config_refs": summary_rows[:100],
                "files": file_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    report_path.write_text(
        "\n".join(
            [
                "# 凡修 Lua 运行逻辑静态索引",
                "",
                f"- Lua 文件：{stats['lua_file_count']}",
                f"- 包目录：{stats['bundle_count']}",
                f"- 配置表引用种类：{stats['config_name_count']}",
                f"- 配置表引用次数：{stats['config_ref_count']}",
                f"- `_M` 函数定义：{stats['function_count']}",
                "",
                "## 高频配置表",
                "",
                *[
                    f"- `{row['config_name']}`：{row['ref_count']} 次，{row['file_count']} 个文件"
                    for row in summary_rows[:20]
                ],
            ]
        ),
        encoding="utf-8",
    )

    return {
        "output_dir": str(out_dir),
        "source_dir": str(resolved_source_dir),
        "stats": stats,
        "files": {
            "index_json": str(json_path),
            "lua_files_tsv": str(files_path),
            "config_refs_tsv": str(refs_path),
            "config_summary_tsv": str(summary_path),
            "functions_tsv": str(functions_path),
            "report": str(report_path),
        },
    }


def build_fanxiu_lingjie_gongfa_runtime_report(
    *,
    source_dir: str | Path | None = None,
    packet_index_dir: str | Path | None = None,
    apk_root: str | Path | None = None,
    export_root: str | Path | None = None,
    resource_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    resolved_source_dir = _resolve_export_dir(source_dir, DEFAULT_LUA_LOGIC_DIR, export_root=export_root)
    resolved_packet_index_dir = _resolve_packet_index_dir(
        packet_index_dir,
        export_root=export_root,
        source_dir=source_dir,
    )
    out_dir = root / DEFAULT_LINGJIE_RUNTIME_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    config_ref_rows: list[dict[str, Any]] = []
    function_ref_counts: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"ref_count": 0, "configs": [], "lines": []}
    )
    net_function_rows: list[dict[str, Any]] = []
    net_call_rows: list[dict[str, Any]] = []
    integration_ref_rows: list[dict[str, Any]] = []
    equip_flow_rows: list[dict[str, Any]] = []
    state_update_rows: list[dict[str, Any]] = []
    skill_core_flow_rows: list[dict[str, Any]] = []
    battle_damage_flow_rows: list[dict[str, Any]] = []
    skill_mgr_ref_rows: list[dict[str, Any]] = []
    fight_result_boundary_rows: list[dict[str, Any]] = []
    hp_update_side_path_rows: list[dict[str, Any]] = []
    fight_state_sync_rows: list[dict[str, Any]] = []
    fight_request_intent_rows: list[dict[str, Any]] = []
    fight_cast_broadcast_flow_rows: list[dict[str, Any]] = []
    skill_instance_lifecycle_rows: list[dict[str, Any]] = []
    fight_authority_boundary_rows: list[dict[str, Any]] = []
    fight_side_channel_rows: list[dict[str, Any]] = []
    fight_status_code_rows: list[dict[str, Any]] = []
    sync_unit_skill_cd_rows: list[dict[str, Any]] = []
    sync_unit_state_rows: list[dict[str, Any]] = []
    role_attribute_sync_rows: list[dict[str, Any]] = []
    attribute_definition_rows: list[dict[str, Any]] = []
    gongfa_attr_change_rows: list[dict[str, Any]] = []
    gongfa_state_rows: list[dict[str, Any]] = []
    gongfa_attr_display_rows: list[dict[str, Any]] = []
    gongfa_rich_text_rows: list[dict[str, Any]] = []
    gongfa_localization_template_rows: list[dict[str, Any]] = []
    gongfa_description_composition_rows: list[dict[str, Any]] = []
    fight_effect_defs: list[dict[str, Any]] = []
    hurt_tips_type_defs: list[dict[str, Any]] = []
    hurt_data_effect_format: dict[str, dict[str, Any]] = {}
    fight_effect_usage: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "files": [], "lines": []})

    for path in sorted(resolved_source_dir.glob("*/text_assets/*.lua")):
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        spans = _function_spans(lines)
        rel_path = path.relative_to(root).as_posix()
        bundle = path.parent.parent.name if path.parent.name == "text_assets" else path.parent.name
        file_scope = _runtime_scope_for_file(path.name)

        if path.name == "SkillDefine.lua":
            fight_effect_defs.extend(
                {
                    **row,
                    "bundle": bundle,
                    "file": path.name,
                    "relative_path": rel_path,
                }
                for row in _extract_lua_number_table(lines, "FightCastEffect")
            )
            hurt_tips_type_defs.extend(
                {
                    **row,
                    "bundle": bundle,
                    "file": path.name,
                    "relative_path": rel_path,
                }
                for row in _extract_lua_number_table(lines, "HurtTipsType")
            )
        if path.name == "HurtData.lua":
            hurt_data_effect_format.update(_extract_hurt_data_effect_format(lines))

        for line_no, line in enumerate(lines, start=1):
            for effect_name in re.findall(r"SkillDefine\.FightCastEffect\.([A-Z0-9_]+)", line):
                usage = fight_effect_usage[effect_name]
                usage["count"] += 1
                usage["files"].append(path.name)
                usage["lines"].append(f"{path.name}:{line_no}")

            for match in _CONFIG_REF_RE.finditer(line):
                config_name = match.group(1)
                if not config_name.startswith("LingjieGongfa_"):
                    continue
                function_name = _function_for_line(spans, line_no)
                config_ref_rows.append(
                    {
                        "config_name": config_name,
                        "config_role": _LINGJIE_CONFIG_ROLES.get(config_name, ""),
                        "file_scope": file_scope,
                        "bundle": bundle,
                        "file": path.name,
                        "relative_path": rel_path,
                        "function_name": function_name,
                        "line": line_no,
                        "code": line.strip()[:240],
                    }
                )
                key = (config_name, file_scope, path.name, function_name)
                row = function_ref_counts[key]
                row["ref_count"] += 1
                row["configs"].append(config_name)
                row["lines"].append(line_no)

            for match in _NETLOGIC_FUN_RE.finditer(line):
                net_function = match.group(1)
                packet_name = net_function[:-3] if net_function.endswith("Fun") else net_function
                net_call_rows.append(
                    {
                        "packet_name": packet_name,
                        "packet_role": _packet_role_for_name(packet_name),
                        "net_function": net_function,
                        "file_scope": file_scope,
                        "bundle": bundle,
                        "file": path.name,
                        "relative_path": rel_path,
                        "function_name": _function_for_line(spans, line_no),
                        "line": line_no,
                        "code": line.strip()[:240],
                    }
                )

            for term, pattern in _GONGFAHOMEMAKE_INTEGRATION_PATTERNS.items():
                if not pattern.search(line):
                    continue
                function_name = _function_for_line(spans, line_no)
                integration_ref_rows.append(
                    {
                        "term": term,
                        "category": _integration_category(path.name, rel_path),
                        "bundle": bundle,
                        "file": path.name,
                        "relative_path": rel_path,
                        "function_name": function_name,
                        "line": line_no,
                        "code": line.strip()[:240],
                    }
                )

            if path.name in _LINGJIE_EQUIP_FLOW_FILES:
                stage = _equip_flow_stage(path.name, line)
                terms = [
                    name
                    for name, pattern in _LINGJIE_EQUIP_FLOW_PATTERNS.items()
                    if pattern.search(line)
                ]
                if stage and terms:
                    equip_flow_rows.append(
                        {
                            "stage": stage,
                            "terms": _join_unique(terms, limit=20),
                            "bundle": bundle,
                            "file": path.name,
                            "relative_path": rel_path,
                            "function_name": _function_for_line(spans, line_no),
                            "line": line_no,
                            "code": line.strip()[:240],
                        }
                    )

            if path.name in _LINGJIE_STATE_FLOW_FILES:
                stage = _state_update_stage(path.name, line)
                terms = [
                    name
                    for name, pattern in _LINGJIE_STATE_FLOW_PATTERNS.items()
                    if pattern.search(line)
                ]
                if stage and terms:
                    state_update_rows.append(
                        {
                            "stage": stage,
                            "terms": _join_unique(terms, limit=20),
                            "bundle": bundle,
                            "file": path.name,
                            "relative_path": rel_path,
                            "function_name": _function_for_line(spans, line_no),
                            "line": line_no,
                            "code": line.strip()[:240],
                        }
                    )

            if path.name in _SKILL_CORE_FLOW_FILES:
                function_name = _function_for_line(spans, line_no)
                stage = _skill_core_flow_stage(path.name, function_name, line)
                terms = [
                    name
                    for name, pattern in _SKILL_CORE_FLOW_PATTERNS.items()
                    if pattern.search(line)
                ]
                if stage and terms:
                    skill_core_flow_rows.append(
                        {
                            "stage": stage,
                            "terms": _join_unique(terms, limit=20),
                            "bundle": bundle,
                            "file": path.name,
                            "relative_path": rel_path,
                            "function_name": function_name,
                            "line": line_no,
                            "code": line.strip()[:240],
                        }
                    )

            if path.name in _BATTLE_DAMAGE_FLOW_FILES:
                function_name = _function_for_line(spans, line_no)
                stage = _battle_damage_flow_stage(path.name, function_name, line)
                terms = [
                    name
                    for name, pattern in _BATTLE_DAMAGE_FLOW_PATTERNS.items()
                    if pattern.search(line)
                ]
                if stage and terms:
                    battle_damage_flow_rows.append(
                        {
                            "stage": stage,
                            "terms": _join_unique(terms, limit=20),
                            "semantics": _battle_damage_flow_semantics(stage),
                            "bundle": bundle,
                            "file": path.name,
                            "relative_path": rel_path,
                            "function_name": function_name,
                            "line": line_no,
                            "code": line.strip()[:240],
                        }
                    )

            for match in _SKILL_MGR_REF_RE.finditer(line):
                symbol = match.group(1) or match.group(2) or ""
                skill_mgr_ref_rows.append(
                    {
                        "symbol": symbol,
                        "role": _skill_mgr_ref_role(symbol),
                        "bundle": bundle,
                        "file": path.name,
                        "relative_path": rel_path,
                        "function_name": _function_for_line(spans, line_no),
                        "line": line_no,
                        "code": line.strip()[:240],
                    }
                )

        if path.name == "GongfahomemakeNetLogic.lua":
            for span in spans:
                function_name = str(span["name"])
                if not function_name.endswith("Fun"):
                    continue
                packet_name = function_name[:-3]
                body = lines[int(span["start"]) - 1 : int(span["end"])]
                field_re = re.compile(rf"\b{re.escape(packet_name)}\.([A-Za-z0-9_\.]+)\s*=")
                fields_written = _join_unique(
                    [match.group(1) for body_line in body for match in field_re.finditer(body_line)],
                    limit=80,
                )
                send_count = sum("F_SendMsg" in body_line for body_line in body)
                net_function_rows.append(
                    {
                        "packet_name": packet_name,
                        "packet_role": _packet_role_for_name(packet_name),
                        "direction": _direction_for_packet_name(packet_name),
                        "net_function": function_name,
                        "line": span["start"],
                        "line_end": span["end"],
                        "fields_written": fields_written,
                        "send_count": send_count,
                    }
                )

    all_packet_rows = _read_tsv(resolved_packet_index_dir / "packets.tsv")
    all_packet_field_rows = _read_tsv(resolved_packet_index_dir / "packet_fields.tsv")
    packet_rows = [row for row in all_packet_rows if row.get("module") == "player.gongfahomemake"]
    packet_field_rows = [row for row in all_packet_field_rows if row.get("module") == "player.gongfahomemake"]
    fields_by_packet_name: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in packet_field_rows:
        fields_by_packet_name[row.get("packet_name") or ""].append(row)
    packet_by_name = {row.get("name") or "": row for row in packet_rows}
    all_fields_by_packet_name: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in all_packet_field_rows:
        all_fields_by_packet_name[row.get("packet_name") or ""].append(row)
    all_packet_by_name = {row.get("name") or "": row for row in all_packet_rows}

    runtime_packet_rows: list[dict[str, Any]] = []
    for row in packet_rows:
        name = row.get("name") or ""
        fields = fields_by_packet_name.get(name, [])
        runtime_packet_rows.append(
            {
                "id": row.get("id") or "",
                "name": name,
                "packet_role": _packet_role_for_name(name),
                "direction": row.get("direction") or _direction_for_packet_name(name),
                "field_count": row.get("field_count") or len(fields),
                "fields": _packet_field_signature(fields),
                "file": row.get("file") or "",
                "relative_path": row.get("relative_path") or "",
            }
        )

    equip_packet_rows: list[dict[str, Any]] = []
    for name in sorted(_LINGJIE_EQUIP_PACKET_NAMES):
        row = all_packet_by_name.get(name, {})
        fields = all_fields_by_packet_name.get(name, [])
        if not row and not fields:
            continue
        source_text = _load_packet_source(root, row.get("relative_path") or "")
        read_only_maps = [
            field.get("field_name") or ""
            for field in fields
            if (field.get("field_name") or "") in {"effectMap", "xianEffectMap"}
            and not _is_written_field(source_text, field.get("field_name") or "")
        ]
        equip_packet_rows.append(
            {
                "id": row.get("id") or "",
                "name": name,
                "module": row.get("module") or "",
                "direction": row.get("direction") or _direction_for_packet_name(name),
                "packet_role": _packet_role_for_name(name),
                "field_count": row.get("field_count") or len(fields),
                "fields": _packet_field_signature(fields),
                "client_read_only_maps": _join_unique(read_only_maps, limit=10),
                "note": _LINGJIE_EQUIP_PACKET_NOTES.get(name, ""),
                "file": row.get("file") or "",
                "relative_path": row.get("relative_path") or "",
            }
        )

    skill_packet_rows: list[dict[str, Any]] = []
    for name in sorted(_SKILL_EQUIP_PACKET_NAMES):
        row = all_packet_by_name.get(name, {})
        fields = all_fields_by_packet_name.get(name, [])
        if not row and not fields:
            continue
        skill_packet_rows.append(
            {
                "id": row.get("id") or "",
                "name": name,
                "module": row.get("module") or "",
                "direction": row.get("direction") or _direction_for_packet_name(name),
                "packet_role": _packet_role_for_name(name),
                "field_count": row.get("field_count") or len(fields),
                "fields": _packet_field_signature(fields),
                "note": _SKILL_EQUIP_PACKET_NOTES.get(name, ""),
                "file": row.get("file") or "",
                "relative_path": row.get("relative_path") or "",
            }
        )

    fight_result_schema_rows: list[dict[str, Any]] = []
    for name in sorted(_FIGHT_RESULT_PACKET_NAMES):
        row = all_packet_by_name.get(name, {})
        fields = all_fields_by_packet_name.get(name, [])
        if not row and not fields:
            continue
        schema_role = _fight_result_schema_role(name)
        base_data = {
            "schema_name": name,
            "id": row.get("id") or "",
            "module": row.get("module") or "",
            "direction": row.get("direction") or _direction_for_packet_name(name),
            "schema_role": schema_role,
            "field_count": row.get("field_count") or len(fields),
            "fields": _packet_field_signature(fields),
            "note": _FIGHT_RESULT_PACKET_NOTES.get(name, ""),
            "file": row.get("file") or "",
            "relative_path": row.get("relative_path") or "",
        }
        if not fields:
            fight_result_schema_rows.append(
                {
                    **base_data,
                    "field_index": "",
                    "field_name": "",
                    "read_method": "",
                    "type_hint": "",
                    "semantics": "未在 packet 字段索引中读到独立字段，可能继承基础 SM_FightResult 结构。",
                }
            )
            continue
        for field in fields:
            field_name = field.get("field_name") or ""
            fight_result_schema_rows.append(
                {
                    **base_data,
                    "field_index": field.get("field_index") or "",
                    "field_name": field_name,
                    "read_method": field.get("read_method") or "",
                    "type_hint": field.get("type_hint") or "",
                    "semantics": _fight_result_field_semantics(name, field_name),
                }
            )

    fight_effect_flag_rows: list[dict[str, Any]] = []
    for item in fight_effect_defs:
        name = str(item.get("name") or "")
        value = int(item.get("value") or 0)
        usage = fight_effect_usage.get(name, {})
        format_row = hurt_data_effect_format.get(name, {})
        fight_effect_flag_rows.append(
            {
                "effect_name": name,
                "value": value,
                "hex_value": hex(value),
                "bit_index": _bit_index(value),
                "source_file": item.get("file") or "",
                "source_line": item.get("line") or "",
                "hurt_tip_prefix": format_row.get("hurt_tip_prefix", ""),
                "blood_type": format_row.get("blood_type", ""),
                "resolved_fight_effect": format_row.get("resolved_fight_effect", ""),
                "ignore_damage": format_row.get("ignore_damage", ""),
                "special_cast": format_row.get("special_cast", ""),
                "format_line": format_row.get("format_line", ""),
                "usage_count": usage.get("count", 0),
                "usage_files": _join_unique(list(usage.get("files", [])), limit=20),
                "usage_lines": _join_unique(list(usage.get("lines", [])), limit=30),
                "semantics": _FIGHT_EFFECT_SEMANTICS.get(name, ""),
            }
        )

    hurt_tips_type_rows: list[dict[str, Any]] = []
    for item in hurt_tips_type_defs:
        hurt_tips_type_rows.append(
            {
                "tips_type_name": item.get("name") or "",
                "value": item.get("value") or 0,
                "source_file": item.get("file") or "",
                "source_line": item.get("line") or "",
            }
        )

    fight_config_value_rows, fight_config_value_scan = _build_fight_config_value_rows(root)
    hurt_tips_config_rows = _build_hurt_tips_config_rows(
        fight_config_value_rows,
        fight_effect_flag_rows,
        hurt_tips_type_rows,
    )
    blood_type_ui_rows = _build_blood_type_ui_rows(root, fight_effect_flag_rows)
    hurt_data_blood_source_rows = _extract_hurt_data_blood_source_rows(root)
    fight_result_to_hurt_data_rows = _build_fight_result_to_hurt_data_rows(root)
    fight_result_boundary_rows = _build_fight_result_boundary_rows(root)
    hp_update_side_path_rows = _build_hp_update_side_path_rows(root, all_fields_by_packet_name, all_packet_by_name)
    fight_state_sync_rows = _build_fight_state_sync_rows(root, all_fields_by_packet_name, all_packet_by_name)
    fight_request_intent_rows = _build_fight_request_intent_rows(root, all_fields_by_packet_name, all_packet_by_name)
    fight_cast_broadcast_flow_rows = _build_fight_cast_broadcast_flow_rows(root)
    skill_instance_lifecycle_rows = _build_skill_instance_lifecycle_rows(root)
    fight_authority_boundary_rows = _build_fight_authority_boundary_rows(root)
    fight_side_channel_rows = _build_fight_side_channel_rows(root, all_fields_by_packet_name, all_packet_by_name)
    fight_status_code_rows = _build_fight_status_code_rows(root)
    sync_unit_skill_cd_rows = _build_sync_unit_skill_cd_rows(root, all_fields_by_packet_name, all_packet_by_name)
    sync_unit_state_rows = _build_sync_unit_state_rows(root, all_fields_by_packet_name, all_packet_by_name)
    role_attribute_sync_rows = _build_role_attribute_sync_rows(root, all_fields_by_packet_name, all_packet_by_name)
    attribute_definition_rows, attribute_definition_scan = _ensure_attribute_config_rows(
        root,
        export_root=export_root,
        resource_root=resource_root,
    )
    gongfa_attr_change_rows = _build_gongfa_attr_change_rows(root, all_fields_by_packet_name, all_packet_by_name)
    gongfa_state_rows = _build_gongfa_state_rows(root, all_fields_by_packet_name, all_packet_by_name)
    gongfa_attr_display_rows = _build_gongfa_attr_display_rows(root)
    gongfa_rich_text_rows = _build_gongfa_rich_text_rows(root)
    gongfa_localization_template_rows = _build_gongfa_localization_template_rows(root, gongfa_rich_text_rows)
    gongfa_description_composition_rows = _build_gongfa_description_composition_rows(root)

    value_object_names = {row["name"] for row in runtime_packet_rows if row["direction"] == "value_object"}
    source_text_by_name = {
        name: _load_packet_source(root, packet_by_name.get(name, {}).get("relative_path") or "")
        for name in value_object_names
    }
    vo_field_rows: list[dict[str, Any]] = []
    for name in sorted(value_object_names):
        packet = packet_by_name.get(name, {})
        source_text = source_text_by_name.get(name, "")
        for field in fields_by_packet_name.get(name, []):
            field_name = field.get("field_name") or ""
            role, semantics = _vo_field_role(name, field_name)
            is_written = _is_written_field(source_text, field_name)
            read_method = field.get("read_method") or ""
            vo_field_rows.append(
                {
                    "vo_name": name,
                    "field_index": field.get("field_index") or "",
                    "field_name": field_name,
                    "field_role": role,
                    "read_method": read_method,
                    "type_hint": field.get("type_hint") or "",
                    "client_writes": "1" if is_written else "0",
                    "wire_note": "server_read_only_in_client_class" if read_method and not is_written else "",
                    "semantics": semantics,
                    "line": field.get("line") or "",
                    "file": packet.get("file") or field.get("file") or "",
                }
            )

    vo_usage_rows: list[dict[str, Any]] = []
    for field in packet_field_rows:
        source_name = field.get("packet_name") or ""
        field_name = field.get("field_name") or ""
        target_vo = field.get("type_hint") or ""
        confidence = "exact_bean" if target_vo in value_object_names else ""
        if not target_vo:
            inferred = _GONGFAHOMEMAKE_LIST_FIELD_TARGETS.get((source_name, field_name), "")
            if inferred:
                target_vo = inferred
                confidence = "inferred_list"
        if not target_vo:
            continue
        packet = packet_by_name.get(source_name, {})
        vo_usage_rows.append(
            {
                "source_name": source_name,
                "source_direction": packet.get("direction") or field.get("direction") or "",
                "source_role": _packet_role_for_name(source_name),
                "field_name": field_name,
                "read_method": field.get("read_method") or "",
                "target_vo": target_vo,
                "confidence": confidence or "external_or_unknown",
                "source_file": packet.get("file") or field.get("file") or "",
            }
        )

    function_summary_rows = [
        {
            "config_name": config_name,
            "config_role": _LINGJIE_CONFIG_ROLES.get(config_name, ""),
            "file_scope": file_scope,
            "file": file,
            "function_name": function_name,
            "ref_count": data["ref_count"],
            "lines": _join_unique(data["lines"], limit=30),
        }
        for (config_name, file_scope, file, function_name), data in function_ref_counts.items()
    ]

    config_ref_rows.sort(key=lambda row: (str(row["config_name"]), str(row["file"]), int(row["line"])))
    function_summary_rows.sort(key=lambda row: (str(row["file_scope"]), str(row["file"]), str(row["function_name"])))
    runtime_packet_rows.sort(key=lambda row: (str(row["direction"]), str(row["packet_role"]), str(row["name"])))
    net_function_rows.sort(key=lambda row: (str(row["direction"]), str(row["packet_name"])))
    net_call_rows.sort(key=lambda row: (str(row["packet_name"]), str(row["file"]), int(row["line"])))
    vo_field_rows.sort(key=lambda row: (str(row["vo_name"]), int(row["field_index"] or 0)))
    vo_usage_rows.sort(key=lambda row: (str(row["target_vo"]), str(row["source_name"]), str(row["field_name"])))
    integration_ref_rows.sort(key=lambda row: (str(row["category"]), str(row["file"]), int(row["line"]), str(row["term"])))
    equip_packet_rows.sort(key=lambda row: (str(row["packet_role"]), str(row["name"])))
    equip_flow_rows.sort(key=lambda row: (str(row["stage"]), str(row["file"]), int(row["line"])))
    state_update_rows.sort(key=lambda row: (str(row["stage"]), str(row["file"]), int(row["line"])))
    skill_core_flow_rows.sort(key=lambda row: (str(row["stage"]), str(row["file"]), int(row["line"])))
    battle_damage_flow_rows.sort(key=lambda row: (str(row["stage"]), str(row["file"]), int(row["line"])))
    skill_packet_rows.sort(key=lambda row: (str(row["id"]), str(row["name"])))
    fight_result_schema_rows.sort(
        key=lambda row: (
            str(row["schema_role"]),
            str(row["id"]),
            str(row["schema_name"]),
            int(row["field_index"] or 0),
        )
    )
    fight_effect_flag_rows.sort(key=lambda row: int(row["value"]))
    hurt_tips_type_rows.sort(key=lambda row: int(row["value"]))
    hurt_tips_config_rows.sort(
        key=lambda row: (
            str(row["config_key"]),
            int(row["input_index"]) if isinstance(row.get("input_index"), int) else 10**9,
        )
    )
    blood_type_ui_rows.sort(key=lambda row: int(row["value"]) if isinstance(row.get("value"), int) else 10**9)
    hurt_data_blood_source_rows.sort(
        key=lambda row: (str(row["source_file"]), str(row["function_name"]), int(row["line"] or 0), str(row["call_kind"]))
    )
    fight_result_to_hurt_data_rows.sort(
        key=lambda row: (
            str(row["source_file"]),
            str(row["function_name"]),
            int(row["line"] or 0),
            int(row["param_index"] or 0),
        )
    )
    fight_result_boundary_rows.sort(
        key=lambda row: (str(row["source_file"]), int(row["line"] or 0), str(row["boundary_kind"]))
    )
    hp_update_side_path_rows.sort(
        key=lambda row: (
            str(row["path_kind"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
            int(row["param_index"] or 0),
        )
    )
    fight_state_sync_rows.sort(
        key=lambda row: (
            str(row["path_kind"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    fight_request_intent_rows.sort(
        key=lambda row: (
            str(row["flow_phase"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    fight_cast_broadcast_flow_rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    skill_instance_lifecycle_rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    fight_authority_boundary_rows.sort(
        key=lambda row: (
            int(row["phase_order"] or 0),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["phase_id"]),
        )
    )
    fight_side_channel_rows.sort(
        key=lambda row: (
            str(row["channel_group"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    fight_status_code_rows.sort(
        key=lambda row: (
            str(row["code_group"]),
            str(row["code_name"]),
            str(row["row_kind"]),
            str(row["source_file"]),
            int(row["line"] or 0),
        )
    )
    sync_unit_skill_cd_rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    sync_unit_state_rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    role_attribute_sync_rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    gongfa_attr_change_rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    gongfa_state_rows.sort(
        key=lambda row: (
            str(row["flow_stage"]),
            str(row["packet_name"]),
            str(row["source_file"]),
            int(row["line"] or 0),
            str(row["row_kind"]),
        )
    )
    skill_mgr_ref_rows.sort(key=lambda row: (str(row["role"]), str(row["file"]), int(row["line"]), str(row["symbol"])))
    apk_symbol_rows, apk_symbol_scan = _scan_apk_runtime_symbol_hits(apk_root=apk_root)
    projected_skill_rows, projected_skill_scan = _build_projected_skill_rows(root)
    skill_next_hop_rows, skill_next_hop_scan = _build_projected_skill_next_hop_rows(root, projected_skill_rows)
    timeline_detail_rows, timeline_detail_scan = _build_projected_timeline_detail_rows(
        root,
        skill_next_hop_rows,
        resource_root=resource_root,
    )
    timeline_clip_rows, timeline_clip_scan = _build_timeline_clip_event_rows(root, timeline_detail_rows)
    timeline_clip_type_summary_rows = _build_timeline_clip_type_summary_rows(timeline_clip_rows)
    timeline_hit_frame_rows, timeline_hit_frame_scan = _build_timeline_hit_frame_rows(timeline_detail_rows, timeline_clip_rows)
    timeline_channel_alignment_rows, timeline_channel_alignment_scan = _build_timeline_channel_alignment_rows(
        timeline_detail_rows,
        skill_next_hop_rows,
    )
    projected_skill_damage_profile_rows, projected_skill_damage_profile_scan = _build_projected_skill_damage_profile_rows(
        skill_next_hop_rows,
        timeline_detail_rows,
        timeline_hit_frame_rows,
        timeline_channel_alignment_rows,
    )
    projected_skill_damage_family_rows, projected_skill_damage_family_scan = _build_projected_skill_damage_family_rows(
        projected_skill_damage_profile_rows
    )
    effect_asset_rows, effect_asset_scan = _build_timeline_effect_asset_rows(
        timeline_detail_rows,
        resource_root=resource_root,
    )
    effect_bundle_object_rows, effect_bundle_object_scan = _build_effect_bundle_object_rows(
        effect_asset_rows,
        resource_root=resource_root,
    )
    playable_bundle_object_rows, playable_bundle_object_scan = _build_playable_bundle_object_rows(
        timeline_detail_rows,
        resource_root=resource_root,
    )
    timeline_sound_ref_rows, timeline_sound_ref_scan = _build_timeline_sound_ref_rows(
        root,
        timeline_detail_rows,
        export_root=export_root,
        resource_root=resource_root,
        wem_export_dir=out_dir / "raw_wems",
    )

    config_counts = Counter(str(row["config_name"]) for row in config_ref_rows)
    packet_direction_counts = Counter(str(row["direction"]) for row in runtime_packet_rows)
    packet_role_counts = Counter(str(row["packet_role"]) for row in runtime_packet_rows)
    vo_usage_counts = Counter(str(row["target_vo"]) for row in vo_usage_rows)
    integration_category_counts = Counter(str(row["category"]) for row in integration_ref_rows)
    integration_term_counts = Counter(str(row["term"]) for row in integration_ref_rows)
    equip_flow_stage_counts = Counter(str(row["stage"]) for row in equip_flow_rows)
    state_update_stage_counts = Counter(str(row["stage"]) for row in state_update_rows)
    skill_core_stage_counts = Counter(str(row["stage"]) for row in skill_core_flow_rows)
    battle_damage_stage_counts = Counter(str(row["stage"]) for row in battle_damage_flow_rows)
    skill_mgr_role_counts = Counter(str(row["role"]) for row in skill_mgr_ref_rows)
    fight_result_boundary_kind_counts = Counter(str(row["boundary_kind"]) for row in fight_result_boundary_rows)
    hp_update_side_path_kind_counts = Counter(str(row["row_kind"]) for row in hp_update_side_path_rows)
    hp_update_side_path_counts = Counter(str(row["path_kind"]) for row in hp_update_side_path_rows)
    fight_state_sync_kind_counts = Counter(str(row["row_kind"]) for row in fight_state_sync_rows)
    fight_state_sync_path_counts = Counter(str(row["path_kind"]) for row in fight_state_sync_rows)
    fight_request_intent_kind_counts = Counter(str(row["row_kind"]) for row in fight_request_intent_rows)
    fight_request_intent_phase_counts = Counter(str(row["flow_phase"]) for row in fight_request_intent_rows)
    fight_cast_broadcast_flow_stage_counts = Counter(str(row["flow_stage"]) for row in fight_cast_broadcast_flow_rows)
    fight_cast_broadcast_flow_kind_counts = Counter(str(row["row_kind"]) for row in fight_cast_broadcast_flow_rows)
    skill_instance_lifecycle_stage_counts = Counter(str(row["flow_stage"]) for row in skill_instance_lifecycle_rows)
    skill_instance_lifecycle_kind_counts = Counter(str(row["row_kind"]) for row in skill_instance_lifecycle_rows)
    fight_authority_boundary_authority_counts = Counter(str(row["authority"]) for row in fight_authority_boundary_rows)
    fight_authority_boundary_phase_counts = Counter(str(row["phase_id"]) for row in fight_authority_boundary_rows)
    fight_side_channel_group_counts = Counter(str(row["channel_group"]) for row in fight_side_channel_rows)
    fight_side_channel_kind_counts = Counter(str(row["row_kind"]) for row in fight_side_channel_rows)
    fight_status_code_group_counts = Counter(str(row["code_group"]) for row in fight_status_code_rows)
    fight_status_code_kind_counts = Counter(str(row["row_kind"]) for row in fight_status_code_rows)
    sync_unit_skill_cd_stage_counts = Counter(str(row["flow_stage"]) for row in sync_unit_skill_cd_rows)
    sync_unit_skill_cd_kind_counts = Counter(str(row["row_kind"]) for row in sync_unit_skill_cd_rows)
    sync_unit_state_stage_counts = Counter(str(row["flow_stage"]) for row in sync_unit_state_rows)
    sync_unit_state_kind_counts = Counter(str(row["row_kind"]) for row in sync_unit_state_rows)
    role_attribute_sync_stage_counts = Counter(str(row["flow_stage"]) for row in role_attribute_sync_rows)
    role_attribute_sync_kind_counts = Counter(str(row["row_kind"]) for row in role_attribute_sync_rows)
    attribute_definition_group_counts = Counter(str(row.get("group", "")) for row in attribute_definition_rows)
    gongfa_attr_change_stage_counts = Counter(str(row["flow_stage"]) for row in gongfa_attr_change_rows)
    gongfa_attr_change_kind_counts = Counter(str(row["row_kind"]) for row in gongfa_attr_change_rows)
    gongfa_state_stage_counts = Counter(str(row["flow_stage"]) for row in gongfa_state_rows)
    gongfa_state_kind_counts = Counter(str(row["row_kind"]) for row in gongfa_state_rows)
    gongfa_attr_display_stage_counts = Counter(str(row["flow_stage"]) for row in gongfa_attr_display_rows)
    gongfa_attr_display_kind_counts = Counter(str(row["row_kind"]) for row in gongfa_attr_display_rows)
    gongfa_rich_text_stage_counts = Counter(str(row["flow_stage"]) for row in gongfa_rich_text_rows)
    gongfa_rich_text_kind_counts = Counter(str(row["row_kind"]) for row in gongfa_rich_text_rows)
    gongfa_localization_template_status_counts = Counter(
        str(row["status"]) for row in gongfa_localization_template_rows
    )
    gongfa_description_composition_stage_counts = Counter(
        str(row["flow_stage"]) for row in gongfa_description_composition_rows
    )
    gongfa_description_composition_kind_counts = Counter(
        str(row["row_kind"]) for row in gongfa_description_composition_rows
    )
    projected_skill_status_counts = Counter(str(row["match_status"]) for row in projected_skill_rows)
    projected_skill_type_counts = Counter(str(row["skill_type"]) for row in projected_skill_rows if row.get("skill_type") != "")
    skill_next_hop_kind_counts = Counter(str(row["next_hop_kind"]) for row in skill_next_hop_rows)
    timeline_detail_status_counts = Counter(str(row["status"]) for row in timeline_detail_rows)
    timeline_clip_role_counts = Counter(str(row["clip_role"]) for row in timeline_clip_rows)
    timeline_clip_type_counts = Counter(str(row["clip_type"]) for row in timeline_clip_rows)
    timeline_clip_track_side_counts = Counter(str(row["track_side"]) for row in timeline_clip_rows)
    effect_asset_status_counts = Counter(str(row["status"]) for row in effect_asset_rows)
    effect_bundle_object_status_counts = Counter(str(row["status"]) for row in effect_bundle_object_rows)
    playable_bundle_object_status_counts = Counter(str(row["status"]) for row in playable_bundle_object_rows)
    timeline_sound_ref_status_counts = Counter(str(row["match_status"]) for row in timeline_sound_ref_rows)
    apk_symbol_term_counts = Counter()
    for row in apk_symbol_rows:
        apk_symbol_term_counts[str(row["term"])] += int(row["hit_count"])
    stats = {
        "config_ref_count": len(config_ref_rows),
        "config_name_count": len(config_counts),
        "config_file_count": len({row["relative_path"] for row in config_ref_rows}),
        "config_function_count": len({(row["file"], row["function_name"]) for row in config_ref_rows}),
        "packet_count": len(runtime_packet_rows),
        "packet_field_count": len(packet_field_rows),
        "value_object_count": len(value_object_names),
        "value_object_field_count": len(vo_field_rows),
        "value_object_usage_count": len(vo_usage_rows),
        "net_function_count": len(net_function_rows),
        "net_call_site_count": len(net_call_rows),
        "battle_integration_ref_count": len(integration_ref_rows),
        "battle_integration_file_count": len({row["relative_path"] for row in integration_ref_rows}),
        "equip_packet_count": len(equip_packet_rows),
        "equip_flow_ref_count": len(equip_flow_rows),
        "state_update_ref_count": len(state_update_rows),
        "skill_core_flow_ref_count": len(skill_core_flow_rows),
        "skill_core_file_count": len({row["relative_path"] for row in skill_core_flow_rows}),
        "battle_damage_flow_ref_count": len(battle_damage_flow_rows),
        "battle_damage_flow_file_count": len({row["relative_path"] for row in battle_damage_flow_rows}),
        "skill_packet_count": len(skill_packet_rows),
        "fight_result_schema_count": len({row["schema_name"] for row in fight_result_schema_rows}),
        "fight_result_schema_field_count": sum(1 for row in fight_result_schema_rows if row.get("field_name")),
        "fight_effect_flag_count": len(fight_effect_flag_rows),
        "fight_effect_formatted_flag_count": sum(1 for row in fight_effect_flag_rows if row.get("hurt_tip_prefix") or row.get("blood_type")),
        "hurt_tips_type_count": len(hurt_tips_type_rows),
        "fight_config_value_count": len(fight_config_value_rows),
        "hurt_tips_config_row_count": len(hurt_tips_config_rows),
        "blood_type_count": len(blood_type_ui_rows),
        "blood_type_ui_count": sum(1 for row in blood_type_ui_rows if row.get("prefab_var")),
        "blood_type_animation_count": sum(1 for row in blood_type_ui_rows if row.get("animation_name")),
        "hurt_data_blood_source_count": len(hurt_data_blood_source_rows),
        "hurt_data_direct_show_count": sum(1 for row in hurt_data_blood_source_rows if row.get("call_kind") == "direct_show_blood_tips"),
        "hurt_data_simple_aggregate_count": sum(1 for row in hurt_data_blood_source_rows if row.get("call_kind") == "simple_fight_aggregate_add"),
        "hurt_tips_type_decode_count": sum(1 for row in hurt_data_blood_source_rows if row.get("call_kind") == "simple_fight_type_decode"),
        "fight_result_to_hurt_data_count": len(fight_result_to_hurt_data_rows),
        "fight_result_to_hurt_data_field_count": len(
            {field for row in fight_result_to_hurt_data_rows for field in str(row.get("fight_result_fields") or "").split("、") if field}
        ),
        "fight_result_boundary_count": len(fight_result_boundary_rows),
        "fight_result_boundary_file_count": len({row["source_file"] for row in fight_result_boundary_rows}),
        "fight_result_boundary_kind_count": len({row["boundary_kind"] for row in fight_result_boundary_rows}),
        "hp_update_side_path_count": len(hp_update_side_path_rows),
        "hp_update_side_path_kind_count": len({row["row_kind"] for row in hp_update_side_path_rows}),
        "hp_update_side_path_param_count": sum(1 for row in hp_update_side_path_rows if row.get("row_kind") == "hurtdata_setdata_param"),
        "hp_update_side_path_field_count": sum(1 for row in hp_update_side_path_rows if row.get("row_kind") == "packet_field"),
        "fight_state_sync_count": len(fight_state_sync_rows),
        "fight_state_sync_field_count": sum(1 for row in fight_state_sync_rows if row.get("row_kind") == "packet_field"),
        "fight_state_sync_property_write_count": sum(1 for row in fight_state_sync_rows if str(row.get("row_kind") or "").startswith("set_")),
        "fight_state_sync_hurtdata_count": sum(1 for row in fight_state_sync_rows if row.get("creates_hurt_data") == "yes"),
        "fight_request_intent_count": len(fight_request_intent_rows),
        "fight_request_intent_packet_count": len({row["packet_name"] for row in fight_request_intent_rows if row.get("packet_name")}),
        "fight_request_intent_request_field_count": sum(
            1
            for row in fight_request_intent_rows
            if row.get("row_kind") == "packet_field" and str(row.get("packet_name") or "").startswith("CM_")
        ),
        "fight_request_intent_damage_field_count": sum(
            1
            for row in fight_request_intent_rows
            if row.get("row_kind") == "packet_field"
            and str(row.get("packet_name") or "").startswith("CM_")
            and row.get("has_damage_like_field") == "yes"
        ),
        "fight_request_intent_send_count": fight_request_intent_kind_counts.get("request_send", 0),
        "fight_cast_broadcast_flow_count": len(fight_cast_broadcast_flow_rows),
        "fight_cast_broadcast_flow_file_count": len({row["source_file"] for row in fight_cast_broadcast_flow_rows}),
        "fight_cast_broadcast_flow_stage_count": len({row["flow_stage"] for row in fight_cast_broadcast_flow_rows}),
        "fight_cast_broadcast_flow_skill_start_count": fight_cast_broadcast_flow_kind_counts.get("skill_start", 0),
        "skill_instance_lifecycle_count": len(skill_instance_lifecycle_rows),
        "skill_instance_lifecycle_file_count": len({row["source_file"] for row in skill_instance_lifecycle_rows}),
        "skill_instance_lifecycle_stage_count": len({row["flow_stage"] for row in skill_instance_lifecycle_rows}),
        "skill_instance_lifecycle_result_to_hurtdata_count": skill_instance_lifecycle_kind_counts.get(
            "fight_result_to_hurtdata",
            0,
        ),
        "skill_instance_lifecycle_hurt_execute_count": skill_instance_lifecycle_kind_counts.get(
            "hurt_frame_execute_hurtdata",
            0,
        ),
        "fight_authority_boundary_count": len(fight_authority_boundary_rows),
        "fight_authority_boundary_phase_count": len({row["phase_id"] for row in fight_authority_boundary_rows}),
        "fight_authority_boundary_server_authority_count": sum(
            1
            for row in fight_authority_boundary_rows
            if str(row.get("authority") or "").startswith("server")
        ),
        "fight_side_channel_count": len(fight_side_channel_rows),
        "fight_side_channel_packet_count": len(
            {row["packet_name"] for row in fight_side_channel_rows if row.get("packet_name")}
        ),
        "fight_side_channel_runtime_count": sum(
            1 for row in fight_side_channel_rows if row.get("row_kind") not in {"packet_field", "packet_no_fields"}
        ),
        "fight_side_channel_group_count": len({row["channel_group"] for row in fight_side_channel_rows}),
        "fight_side_channel_field_count": fight_side_channel_kind_counts.get("packet_field", 0),
        "fight_status_code_count": len(fight_status_code_rows),
        "fight_restrict_status_enum_count": sum(
            1
            for row in fight_status_code_rows
            if row.get("code_group") == "restrict_status" and row.get("row_kind") == "enum_value"
        ),
        "fight_restrict_status_usage_count": sum(
            1
            for row in fight_status_code_rows
            if row.get("code_group") == "restrict_status" and row.get("row_kind") != "enum_value"
        ),
        "fight_unit_state_enum_count": sum(
            1
            for row in fight_status_code_rows
            if row.get("code_group") == "unit_state" and row.get("row_kind") == "enum_value"
        ),
        "fight_unit_state_usage_count": sum(
            1
            for row in fight_status_code_rows
            if row.get("code_group") == "unit_state" and row.get("row_kind") != "enum_value"
        ),
        "sync_unit_skill_cd_count": len(sync_unit_skill_cd_rows),
        "sync_unit_skill_cd_packet_field_count": sync_unit_skill_cd_kind_counts.get("packet_field", 0),
        "sync_unit_skill_cd_runtime_count": sum(
            1 for row in sync_unit_skill_cd_rows if row.get("row_kind") != "packet_field"
        ),
        "sync_unit_skill_cd_stage_count": len({row["flow_stage"] for row in sync_unit_skill_cd_rows}),
        "sync_unit_skill_cd_skillinfo_field_count": sum(
            1
            for row in sync_unit_skill_cd_rows
            if row.get("packet_name") == "SkillInfoVO" and row.get("row_kind") == "packet_field"
        ),
        "sync_unit_state_count": len(sync_unit_state_rows),
        "sync_unit_state_packet_field_count": sync_unit_state_kind_counts.get("packet_field", 0),
        "sync_unit_state_runtime_count": sum(
            1
            for row in sync_unit_state_rows
            if row.get("row_kind") not in {"packet_field", "missing_rolemgr_source"}
        ),
        "sync_unit_state_gap_count": sync_unit_state_kind_counts.get("missing_rolemgr_source", 0),
        "sync_unit_state_stage_count": len({row["flow_stage"] for row in sync_unit_state_rows}),
        "role_attribute_sync_count": len(role_attribute_sync_rows),
        "role_attribute_sync_packet_field_count": role_attribute_sync_kind_counts.get("packet_field", 0),
        "role_attribute_sync_runtime_count": sum(
            1 for row in role_attribute_sync_rows if row.get("row_kind") != "packet_field"
        ),
        "role_attribute_sync_stage_count": len({row["flow_stage"] for row in role_attribute_sync_rows}),
        "role_attribute_sync_changed_attrs_field_count": sum(
            1
            for row in role_attribute_sync_rows
            if row.get("packet_name") == "ChangedAttrsVo" and row.get("row_kind") == "packet_field"
        ),
        "role_attribute_sync_property_write_count": sum(
            1
            for row in role_attribute_sync_rows
            if row.get("row_kind")
            in {"set_final_attr_property", "set_attribute_map_property", "set_fight_power_property"}
        ),
        "attribute_definition_count": len(attribute_definition_rows),
        "attribute_definition_show_tips_count": sum(
            1 for row in attribute_definition_rows if str(row.get("show_tips", "")) == "1"
        ),
        "attribute_definition_ratio_group_count": sum(
            1 for row in attribute_definition_rows if "Ratio" in str(row.get("group", ""))
        ),
        "attribute_definition_fight_power_count": sum(
            1 for row in attribute_definition_rows if row.get("lua_symbol") == "FIGHT_POWER"
        ),
        "gongfa_attr_change_count": len(gongfa_attr_change_rows),
        "gongfa_attr_change_packet_field_count": gongfa_attr_change_kind_counts.get("packet_field", 0),
        "gongfa_attr_change_runtime_count": sum(
            1 for row in gongfa_attr_change_rows if row.get("row_kind") != "packet_field"
        ),
        "gongfa_attr_change_stage_count": len({row["flow_stage"] for row in gongfa_attr_change_rows}),
        "gongfa_attr_change_apply_count": sum(
            1
            for row in gongfa_attr_change_rows
            if row.get("row_kind")
            in {"apply_learn_attrs", "apply_upgrade_attrs", "apply_batch_upgrade_attrs"}
        ),
        "gongfa_state_count": len(gongfa_state_rows),
        "gongfa_state_packet_field_count": gongfa_state_kind_counts.get("packet_field", 0),
        "gongfa_state_packet_no_field_count": gongfa_state_kind_counts.get("packet_no_field", 0),
        "gongfa_state_runtime_count": sum(
            1 for row in gongfa_state_rows if row.get("row_kind") not in {"packet_field", "packet_no_field"}
        ),
        "gongfa_state_stage_count": len({row["flow_stage"] for row in gongfa_state_rows}),
        "gongfa_state_inherited_simple_item_field_count": sum(
            1
            for row in gongfa_state_rows
            if row.get("packet_name") == "SimpleItemVO" and row.get("row_kind") == "packet_field"
        ),
        "gongfa_state_set_vo_callsite_count": gongfa_state_kind_counts.get("set_gongfa_vo_callsite", 0),
        "gongfa_state_gap_count": gongfa_state_kind_counts.get("visible_gap_no_set_gongfa_vo_caller", 0),
        "gongfa_state_static_catalog_count": sum(
            1
            for row in gongfa_state_rows
            if row.get("row_kind") in {"load_gongfa_config", "create_gongfa_vo_from_config", "store_static_gongfa_vo"}
        ),
        "gongfa_state_vo_overlay_count": sum(
            1
            for row in gongfa_state_rows
            if row.get("row_kind") in {"overlay_server_vo", "update_single_gongfa_vo", "update_batch_gongfa_vo"}
        ),
        "gongfa_attr_display_count": len(gongfa_attr_display_rows),
        "gongfa_attr_display_stage_count": len({row["flow_stage"] for row in gongfa_attr_display_rows}),
        "gongfa_attr_display_attribute_config_ref_count": sum(
            1
            for row in gongfa_attr_display_rows
            if "ConfigName.Attribute_Attribute" in str(row.get("field_refs") or "")
        ),
        "gongfa_attr_display_preview_call_count": sum(
            1
            for row in gongfa_attr_display_rows
            if row.get("row_kind")
            in {
                "preview_next_attr_dispatch",
                "ui_level_preview_display",
                "ui_other_preview_display",
                "detail_static_attr_entry",
            }
        ),
        "gongfa_attr_display_format_count": sum(
            1
            for row in gongfa_attr_display_rows
            if row.get("row_kind")
            in {
                "format_attr_value_number",
                "format_attr_value_ratio",
                "ui_attr_item_format_current",
                "ui_attr_item_format_add",
            }
        ),
        "gongfa_rich_text_count": len(gongfa_rich_text_rows),
        "gongfa_rich_text_stage_count": len({row["flow_stage"] for row in gongfa_rich_text_rows}),
        "gongfa_rich_text_localization_key_count": len(
            {
                ref
                for row in gongfa_rich_text_rows
                for ref in str(row.get("field_refs") or "").split("、")
                if ref.startswith("LuaLocalization.")
            }
        ),
        "gongfa_rich_text_color_ref_count": sum(
            1 for row in gongfa_rich_text_rows if "color:" in str(row.get("field_refs") or "")
        ),
        "gongfa_rich_text_config_description_count": sum(
            1
            for row in gongfa_rich_text_rows
            if any(
                token in str(row.get("field_refs") or "")
                for token in (
                    "Gongfa_Gongfa.descript",
                    "Gongfa_GongfaPin.describe",
                    "Gongfa_GongfaJie.describe",
                    "tongxuan_sec_describe",
                )
            )
        ),
        "gongfa_rich_text_render_count": sum(
            1
            for row in gongfa_rich_text_rows
            if str(row.get("row_kind") or "").startswith("render_")
        ),
        "gongfa_localization_template_count": len(gongfa_localization_template_rows),
        "gongfa_localization_template_ok_count": gongfa_localization_template_status_counts.get("ok", 0),
        "gongfa_localization_template_missing_count": gongfa_localization_template_status_counts.get("missing", 0),
        "gongfa_localization_template_color_count": sum(
            1 for row in gongfa_localization_template_rows if row.get("color_refs")
        ),
        "gongfa_localization_template_href_count": sum(
            1 for row in gongfa_localization_template_rows if row.get("has_href") == "1"
        ),
        "gongfa_localization_template_placeholder_count": sum(
            int(row.get("placeholder_count") or 0) for row in gongfa_localization_template_rows
        ),
        "gongfa_description_composition_count": len(gongfa_description_composition_rows),
        "gongfa_description_composition_stage_count": len(
            {row["flow_stage"] for row in gongfa_description_composition_rows}
        ),
        "gongfa_description_composition_localization_key_count": len(
            {
                key
                for row in gongfa_description_composition_rows
                for key in str(row.get("localization_keys") or "").split("、")
                if key
            }
        ),
        "gongfa_description_composition_tongxuan_count": sum(
            1
            for row in gongfa_description_composition_rows
            if "tongxuan" in str(row.get("row_kind") or "") or "TongXuan" in str(row.get("code") or "")
        ),
        "gongfa_description_composition_effect_template_count": sum(
            1
            for row in gongfa_description_composition_rows
            if row.get("row_kind")
            in {
                "format_active_effect_description",
                "format_locked_effect_description",
                "format_locked_nested_description",
                "format_active_pin_description",
                "format_active_tongxuan_secondary",
            }
        ),
        "skill_mgr_ref_count": len(skill_mgr_ref_rows),
        "projected_skill_count": len(projected_skill_rows),
        "projected_skill_matched_count": projected_skill_status_counts.get("matched", 0),
        "projected_skill_missing_count": len(projected_skill_rows) - projected_skill_status_counts.get("matched", 0),
        "skill_next_hop_count": len(skill_next_hop_rows),
        "skill_next_hop_timeline_skill_count": skill_next_hop_kind_counts.get("timeline_channel", 0),
        "skill_next_hop_feature_reuse_count": skill_next_hop_kind_counts.get("lingjie_feature_reuse", 0),
        "skill_next_hop_no_static_count": skill_next_hop_kind_counts.get("no_static_next_hop", 0),
        "timeline_detail_count": len(timeline_detail_rows),
        "timeline_detail_ok_count": timeline_detail_status_counts.get("ok", 0),
        "timeline_detail_missing_lua_count": timeline_detail_status_counts.get("missing_lua", 0),
        "timeline_playable_asset_count": timeline_detail_scan.get("playable_asset_count", 0),
        "timeline_clip_event_count": len(timeline_clip_rows),
        "timeline_clip_action_count": timeline_clip_role_counts.get("action", 0),
        "timeline_clip_sound_count": timeline_clip_role_counts.get("sound", 0),
        "timeline_clip_effect_count": timeline_clip_role_counts.get("effect", 0),
        "timeline_clip_hit_frame_count": timeline_clip_role_counts.get("hit_frame", 0),
        "timeline_clip_sound_id_ref_count": sum(1 for row in timeline_clip_rows if str(row.get("sound_id") or "") not in {"", "0", "None"}),
        "timeline_clip_type_summary_count": len(timeline_clip_type_summary_rows),
        "timeline_hit_frame_count": len(timeline_hit_frame_rows),
        "timeline_hit_frame_hurt_event_match_count": timeline_hit_frame_scan.get("matched_hurt_event_count", 0),
        "timeline_hit_frame_total_hurt_event_count": timeline_hit_frame_scan.get("total_hurt_event_count", 0),
        "timeline_hit_frame_timeline_count": timeline_hit_frame_scan.get("timeline_count", 0),
        "timeline_channel_alignment_count": len(timeline_channel_alignment_rows),
        "timeline_channel_alignment_matched_count": timeline_channel_alignment_scan.get("matched_count", 0),
        "timeline_channel_alignment_missing_count": timeline_channel_alignment_scan.get("missing_channel_count", 0),
        "projected_skill_damage_profile_count": len(projected_skill_damage_profile_rows),
        "projected_skill_damage_profile_skill_count": projected_skill_damage_profile_scan.get("skill_count", 0),
        "projected_skill_damage_profile_timeline_count": projected_skill_damage_profile_scan.get("timeline_count", 0),
        "projected_skill_damage_profile_missing_timeline_count": projected_skill_damage_profile_scan.get("missing_timeline_count", 0),
        "projected_skill_damage_profile_missing_channel_count": projected_skill_damage_profile_scan.get("missing_channel_count", 0),
        "projected_skill_damage_family_count": len(projected_skill_damage_family_rows),
        "projected_skill_damage_family_skipped_profile_count": projected_skill_damage_family_scan.get("skipped_profile_count", 0),
        "effect_asset_ref_count": len(effect_asset_rows),
        "effect_asset_ok_count": effect_asset_status_counts.get("ok", 0),
        "effect_asset_missing_count": len(effect_asset_rows) - effect_asset_status_counts.get("ok", 0),
        "effect_asset_unique_effect_count": effect_asset_scan.get("unique_effect_count", 0),
        "effect_asset_unique_asset_count": effect_asset_scan.get("unique_asset_count", 0),
        "effect_asset_unique_asset_total_bytes": effect_asset_scan.get("unique_asset_total_bytes", 0),
        "effect_bundle_object_asset_count": len(effect_bundle_object_rows),
        "effect_bundle_object_ok_count": effect_bundle_object_status_counts.get("ok", 0),
        "effect_bundle_object_error_count": len(effect_bundle_object_rows)
        - effect_bundle_object_status_counts.get("ok", 0),
        "effect_bundle_read_error_object_count": effect_bundle_object_scan.get("read_error_object_count", 0),
        "playable_bundle_object_asset_count": len(playable_bundle_object_rows),
        "playable_bundle_object_ok_count": playable_bundle_object_status_counts.get("ok", 0),
        "playable_bundle_object_error_count": len(playable_bundle_object_rows)
        - playable_bundle_object_status_counts.get("ok", 0),
        "playable_bundle_read_error_object_count": playable_bundle_object_scan.get("read_error_object_count", 0),
        "timeline_sound_ref_count": len(timeline_sound_ref_rows),
        "timeline_sound_unique_id_count": timeline_sound_ref_scan.get("unique_sound_id_count", 0),
        "timeline_sound_matched_count": timeline_sound_ref_status_counts.get("matched", 0),
        "timeline_sound_missing_count": len(timeline_sound_ref_rows) - timeline_sound_ref_status_counts.get("matched", 0),
        "timeline_sound_event_bank_hit_count": timeline_sound_ref_scan.get("event_id_bank_hit_count", 0),
        "timeline_sound_hirc_wem_hit_count": timeline_sound_ref_scan.get("hirc_wem_hit_count", 0),
        "timeline_sound_raw_wem_export_count": timeline_sound_ref_scan.get("raw_wem_export_count", 0),
        "timeline_sound_raw_wem_export_bytes": timeline_sound_ref_scan.get("raw_wem_export_bytes", 0),
        "apk_symbol_hit_count": sum(int(row["hit_count"]) for row in apk_symbol_rows),
        "apk_symbol_row_count": len(apk_symbol_rows),
        "apk_symbol_file_count": len({row["relative_path"] for row in apk_symbol_rows}),
        "packet_direction_counts": dict(packet_direction_counts),
        "packet_role_counts": dict(packet_role_counts),
        "top_value_object_usages": dict(vo_usage_counts.most_common(20)),
        "battle_integration_category_counts": dict(integration_category_counts),
        "battle_integration_term_counts": dict(integration_term_counts),
        "equip_flow_stage_counts": dict(equip_flow_stage_counts),
        "state_update_stage_counts": dict(state_update_stage_counts),
        "skill_core_stage_counts": dict(skill_core_stage_counts),
        "battle_damage_stage_counts": dict(battle_damage_stage_counts),
        "skill_mgr_role_counts": dict(skill_mgr_role_counts),
        "projected_skill_status_counts": dict(projected_skill_status_counts),
        "projected_skill_type_counts": dict(projected_skill_type_counts.most_common()),
        "projected_skill_scan": projected_skill_scan,
        "skill_next_hop_kind_counts": dict(skill_next_hop_kind_counts),
        "skill_next_hop_scan": skill_next_hop_scan,
        "timeline_detail_status_counts": dict(timeline_detail_status_counts),
        "timeline_detail_scan": timeline_detail_scan,
        "timeline_clip_role_counts": dict(timeline_clip_role_counts.most_common()),
        "timeline_clip_type_counts": dict(timeline_clip_type_counts.most_common()),
        "timeline_clip_track_side_counts": dict(timeline_clip_track_side_counts.most_common()),
        "timeline_clip_scan": timeline_clip_scan,
        "timeline_hit_frame_scan": timeline_hit_frame_scan,
        "timeline_channel_alignment_scan": timeline_channel_alignment_scan,
        "projected_skill_damage_profile_scan": projected_skill_damage_profile_scan,
        "projected_skill_damage_family_scan": projected_skill_damage_family_scan,
        "effect_asset_status_counts": dict(effect_asset_status_counts),
        "effect_asset_scan": effect_asset_scan,
        "effect_bundle_object_status_counts": dict(effect_bundle_object_status_counts),
        "effect_bundle_object_scan": effect_bundle_object_scan,
        "playable_bundle_object_status_counts": dict(playable_bundle_object_status_counts),
        "playable_bundle_object_scan": playable_bundle_object_scan,
        "timeline_sound_ref_status_counts": dict(timeline_sound_ref_status_counts),
        "timeline_sound_ref_scan": timeline_sound_ref_scan,
        "fight_config_value_scan": fight_config_value_scan,
        "fight_result_boundary_kind_counts": dict(fight_result_boundary_kind_counts.most_common()),
        "hp_update_side_path_kind_counts": dict(hp_update_side_path_kind_counts.most_common()),
        "hp_update_side_path_counts": dict(hp_update_side_path_counts.most_common()),
        "fight_state_sync_kind_counts": dict(fight_state_sync_kind_counts.most_common()),
        "fight_state_sync_path_counts": dict(fight_state_sync_path_counts.most_common()),
        "fight_request_intent_kind_counts": dict(fight_request_intent_kind_counts.most_common()),
        "fight_request_intent_phase_counts": dict(fight_request_intent_phase_counts.most_common()),
        "fight_cast_broadcast_flow_stage_counts": dict(fight_cast_broadcast_flow_stage_counts.most_common()),
        "fight_cast_broadcast_flow_kind_counts": dict(fight_cast_broadcast_flow_kind_counts.most_common()),
        "skill_instance_lifecycle_stage_counts": dict(skill_instance_lifecycle_stage_counts.most_common()),
        "skill_instance_lifecycle_kind_counts": dict(skill_instance_lifecycle_kind_counts.most_common()),
        "fight_authority_boundary_authority_counts": dict(fight_authority_boundary_authority_counts.most_common()),
        "fight_authority_boundary_phase_counts": dict(fight_authority_boundary_phase_counts.most_common()),
        "fight_side_channel_group_counts": dict(fight_side_channel_group_counts.most_common()),
        "fight_side_channel_kind_counts": dict(fight_side_channel_kind_counts.most_common()),
        "fight_status_code_group_counts": dict(fight_status_code_group_counts.most_common()),
        "fight_status_code_kind_counts": dict(fight_status_code_kind_counts.most_common()),
        "sync_unit_skill_cd_stage_counts": dict(sync_unit_skill_cd_stage_counts.most_common()),
        "sync_unit_skill_cd_kind_counts": dict(sync_unit_skill_cd_kind_counts.most_common()),
        "sync_unit_state_stage_counts": dict(sync_unit_state_stage_counts.most_common()),
        "sync_unit_state_kind_counts": dict(sync_unit_state_kind_counts.most_common()),
        "role_attribute_sync_stage_counts": dict(role_attribute_sync_stage_counts.most_common()),
        "role_attribute_sync_kind_counts": dict(role_attribute_sync_kind_counts.most_common()),
        "attribute_definition_group_counts": dict(attribute_definition_group_counts.most_common()),
        "attribute_definition_scan": attribute_definition_scan,
        "gongfa_attr_change_stage_counts": dict(gongfa_attr_change_stage_counts.most_common()),
        "gongfa_attr_change_kind_counts": dict(gongfa_attr_change_kind_counts.most_common()),
        "gongfa_state_stage_counts": dict(gongfa_state_stage_counts.most_common()),
        "gongfa_state_kind_counts": dict(gongfa_state_kind_counts.most_common()),
        "gongfa_attr_display_stage_counts": dict(gongfa_attr_display_stage_counts.most_common()),
        "gongfa_attr_display_kind_counts": dict(gongfa_attr_display_kind_counts.most_common()),
        "gongfa_rich_text_stage_counts": dict(gongfa_rich_text_stage_counts.most_common()),
        "gongfa_rich_text_kind_counts": dict(gongfa_rich_text_kind_counts.most_common()),
        "gongfa_localization_template_status_counts": dict(gongfa_localization_template_status_counts.most_common()),
        "gongfa_description_composition_stage_counts": dict(
            gongfa_description_composition_stage_counts.most_common()
        ),
        "gongfa_description_composition_kind_counts": dict(gongfa_description_composition_kind_counts.most_common()),
        "apk_symbol_term_counts": dict(apk_symbol_term_counts.most_common()),
        "apk_symbol_scan": apk_symbol_scan,
    }

    config_refs_path = out_dir / "lingjie_runtime_config_refs.tsv"
    function_refs_path = out_dir / "lingjie_runtime_function_refs.tsv"
    packets_path = out_dir / "lingjie_runtime_packets.tsv"
    vo_fields_path = out_dir / "lingjie_runtime_vo_fields.tsv"
    vo_usage_path = out_dir / "lingjie_runtime_vo_usage.tsv"
    net_functions_path = out_dir / "lingjie_runtime_net_functions.tsv"
    net_call_sites_path = out_dir / "lingjie_runtime_net_call_sites.tsv"
    battle_refs_path = out_dir / "lingjie_runtime_battle_refs.tsv"
    equip_packets_path = out_dir / "lingjie_runtime_equip_packets.tsv"
    equip_flow_path = out_dir / "lingjie_runtime_equip_flow.tsv"
    state_updates_path = out_dir / "lingjie_runtime_state_updates.tsv"
    skill_core_flow_path = out_dir / "lingjie_runtime_skill_core_flow.tsv"
    battle_damage_flow_path = out_dir / "lingjie_runtime_battle_damage_flow.tsv"
    skill_packets_path = out_dir / "lingjie_runtime_skill_packets.tsv"
    fight_result_schema_path = out_dir / "lingjie_runtime_fight_result_schema.tsv"
    fight_effect_flags_path = out_dir / "lingjie_runtime_fight_effect_flags.tsv"
    hurt_tips_types_path = out_dir / "lingjie_runtime_hurt_tips_types.tsv"
    fight_config_values_path = out_dir / "lingjie_runtime_fight_config_values.tsv"
    hurt_tips_config_path = out_dir / "lingjie_runtime_hurt_tips_config.tsv"
    blood_type_ui_path = out_dir / "lingjie_runtime_blood_type_ui.tsv"
    hurt_data_blood_sources_path = out_dir / "lingjie_runtime_hurt_data_blood_sources.tsv"
    fight_result_to_hurt_data_path = out_dir / "lingjie_runtime_fight_result_to_hurt_data.tsv"
    fight_result_boundary_path = out_dir / "lingjie_runtime_fight_result_boundary.tsv"
    hp_update_side_paths_path = out_dir / "lingjie_runtime_hp_update_side_paths.tsv"
    fight_state_sync_paths_path = out_dir / "lingjie_runtime_fight_state_sync_paths.tsv"
    fight_request_intents_path = out_dir / "lingjie_runtime_fight_request_intents.tsv"
    fight_cast_broadcast_flow_path = out_dir / "lingjie_runtime_fight_cast_broadcast_flow.tsv"
    skill_instance_lifecycle_path = out_dir / "lingjie_runtime_skill_instance_lifecycle.tsv"
    fight_authority_boundary_path = out_dir / "lingjie_runtime_fight_authority_boundaries.tsv"
    fight_side_channel_path = out_dir / "lingjie_runtime_fight_side_channels.tsv"
    fight_status_codes_path = out_dir / "lingjie_runtime_fight_status_codes.tsv"
    sync_unit_skill_cd_path = out_dir / "lingjie_runtime_sync_unit_skill_cd.tsv"
    sync_unit_state_path = out_dir / "lingjie_runtime_sync_unit_state.tsv"
    role_attribute_sync_path = out_dir / "lingjie_runtime_role_attribute_sync.tsv"
    attribute_definitions_path = out_dir / "lingjie_runtime_attribute_defs.tsv"
    gongfa_attr_change_path = out_dir / "lingjie_runtime_gongfa_attr_change.tsv"
    gongfa_state_path = out_dir / "lingjie_runtime_gongfa_state.tsv"
    gongfa_attr_display_path = out_dir / "lingjie_runtime_gongfa_attr_display.tsv"
    gongfa_rich_text_path = out_dir / "lingjie_runtime_gongfa_rich_text.tsv"
    gongfa_localization_templates_path = out_dir / "lingjie_runtime_gongfa_localization_templates.tsv"
    gongfa_description_composition_path = out_dir / "lingjie_runtime_gongfa_description_composition.tsv"
    skill_mgr_refs_path = out_dir / "lingjie_runtime_skillmgr_refs.tsv"
    projected_skills_path = out_dir / "lingjie_runtime_projected_skills.tsv"
    skill_next_hops_path = out_dir / "lingjie_runtime_skill_next_hops.tsv"
    projected_skill_damage_profiles_path = out_dir / "lingjie_runtime_projected_skill_damage_profiles.tsv"
    projected_skill_damage_families_path = out_dir / "lingjie_runtime_projected_skill_damage_families.tsv"
    timeline_details_path = out_dir / "lingjie_runtime_timeline_details.tsv"
    timeline_clips_path = out_dir / "lingjie_runtime_timeline_clips.tsv"
    timeline_clip_types_path = out_dir / "lingjie_runtime_timeline_clip_types.tsv"
    timeline_hit_frames_path = out_dir / "lingjie_runtime_timeline_hit_frames.tsv"
    timeline_channel_alignment_path = out_dir / "lingjie_runtime_timeline_channel_alignment.tsv"
    effect_assets_path = out_dir / "lingjie_runtime_effect_assets.tsv"
    effect_bundle_objects_path = out_dir / "lingjie_runtime_effect_bundle_objects.tsv"
    playable_bundle_objects_path = out_dir / "lingjie_runtime_playable_bundle_objects.tsv"
    timeline_sound_refs_path = out_dir / "lingjie_runtime_sound_refs.tsv"
    apk_symbol_hits_path = out_dir / "lingjie_runtime_apk_symbol_hits.tsv"
    json_path = out_dir / "lingjie_runtime_index.json"
    report_path = out_dir / "lingjie_runtime_report.md"

    _write_tsv(
        config_refs_path,
        config_ref_rows,
        [
            "config_name",
            "config_role",
            "file_scope",
            "bundle",
            "file",
            "relative_path",
            "function_name",
            "line",
            "code",
        ],
    )
    _write_tsv(
        function_refs_path,
        function_summary_rows,
        ["config_name", "config_role", "file_scope", "file", "function_name", "ref_count", "lines"],
    )
    _write_tsv(
        packets_path,
        runtime_packet_rows,
        ["id", "name", "packet_role", "direction", "field_count", "fields", "file", "relative_path"],
    )
    _write_tsv(
        vo_fields_path,
        vo_field_rows,
        [
            "vo_name",
            "field_index",
            "field_name",
            "field_role",
            "read_method",
            "type_hint",
            "client_writes",
            "wire_note",
            "semantics",
            "line",
            "file",
        ],
    )
    _write_tsv(
        vo_usage_path,
        vo_usage_rows,
        [
            "source_name",
            "source_direction",
            "source_role",
            "field_name",
            "read_method",
            "target_vo",
            "confidence",
            "source_file",
        ],
    )
    _write_tsv(
        net_functions_path,
        net_function_rows,
        ["packet_name", "packet_role", "direction", "net_function", "line", "line_end", "fields_written", "send_count"],
    )
    _write_tsv(
        net_call_sites_path,
        net_call_rows,
        [
            "packet_name",
            "packet_role",
            "net_function",
            "file_scope",
            "bundle",
            "file",
            "relative_path",
            "function_name",
            "line",
            "code",
        ],
    )
    _write_tsv(
        battle_refs_path,
        integration_ref_rows,
        ["term", "category", "bundle", "file", "relative_path", "function_name", "line", "code"],
    )
    _write_tsv(
        equip_packets_path,
        equip_packet_rows,
        [
            "id",
            "name",
            "module",
            "direction",
            "packet_role",
            "field_count",
            "fields",
            "client_read_only_maps",
            "note",
            "file",
            "relative_path",
        ],
    )
    _write_tsv(
        equip_flow_path,
        equip_flow_rows,
        ["stage", "terms", "bundle", "file", "relative_path", "function_name", "line", "code"],
    )
    _write_tsv(
        state_updates_path,
        state_update_rows,
        ["stage", "terms", "bundle", "file", "relative_path", "function_name", "line", "code"],
    )
    _write_tsv(
        skill_core_flow_path,
        skill_core_flow_rows,
        ["stage", "terms", "bundle", "file", "relative_path", "function_name", "line", "code"],
    )
    _write_tsv(
        battle_damage_flow_path,
        battle_damage_flow_rows,
        ["stage", "terms", "semantics", "bundle", "file", "relative_path", "function_name", "line", "code"],
    )
    _write_tsv(
        skill_packets_path,
        skill_packet_rows,
        ["id", "name", "module", "direction", "packet_role", "field_count", "fields", "note", "file", "relative_path"],
    )
    _write_tsv(
        fight_result_schema_path,
        fight_result_schema_rows,
        [
            "schema_name",
            "id",
            "module",
            "direction",
            "schema_role",
            "field_count",
            "fields",
            "field_index",
            "field_name",
            "read_method",
            "type_hint",
            "semantics",
            "note",
            "file",
            "relative_path",
        ],
    )
    _write_tsv(
        fight_effect_flags_path,
        fight_effect_flag_rows,
        [
            "effect_name",
            "value",
            "hex_value",
            "bit_index",
            "source_file",
            "source_line",
            "hurt_tip_prefix",
            "blood_type",
            "resolved_fight_effect",
            "ignore_damage",
            "special_cast",
            "format_line",
            "usage_count",
            "usage_files",
            "usage_lines",
            "semantics",
        ],
    )
    _write_tsv(
        hurt_tips_types_path,
        hurt_tips_type_rows,
        ["tips_type_name", "value", "source_file", "source_line"],
    )
    _write_tsv(
        fight_config_values_path,
        fight_config_value_rows,
        ["config_key", "value", "source_file"],
    )
    _write_tsv(
        hurt_tips_config_path,
        hurt_tips_config_rows,
        [
            "config_key",
            "config_kind",
            "raw_value",
            "raw_pair",
            "input_index",
            "mapping_rule",
            "mapped_value",
            "mapped_name",
            "configured_duration_ms",
            "configured_duration_seconds",
            "runtime_timer_seconds",
            "runtime_note",
            "semantics",
            "parse_status",
        ],
    )
    _write_tsv(
        blood_type_ui_path,
        blood_type_ui_rows,
        [
            "blood_type_name",
            "value",
            "semantics",
            "source_file",
            "source_line",
            "ui_prefab_path",
            "panel_source_file",
            "prefab_var",
            "prefab_component_id",
            "panel_line",
            "animation_source_file",
            "animation_name",
            "animation_line",
            "produced_by_fight_effects",
            "effect_prefixes",
            "panel_position_rule",
        ],
    )
    _write_tsv(
        hurt_data_blood_sources_path,
        hurt_data_blood_source_rows,
        [
            "source_file",
            "function_name",
            "line",
            "call_kind",
            "scene_gate",
            "runtime_context",
            "source_metric",
            "source_field_hint",
            "tips_type",
            "fight_effect_expr",
            "blood_type_expr",
            "blood_type_candidates",
            "target_expr",
            "amount_arg",
            "tip_expr",
            "code",
        ],
    )
    _write_tsv(
        fight_result_to_hurt_data_path,
        fight_result_to_hurt_data_rows,
        [
            "source_file",
            "function_name",
            "line",
            "line_end",
            "call_kind",
            "call_arg_count",
            "expected_arg_count",
            "arg_status",
            "param_index",
            "hurt_data_param",
            "hurt_data_field",
            "param_role",
            "arg_expr",
            "resolved_expr",
            "fight_result_fields",
            "semantics",
            "transform_note",
            "code",
        ],
    )
    _write_tsv(
        fight_result_boundary_path,
        fight_result_boundary_rows,
        [
            "source_file",
            "file_name",
            "function_name",
            "line",
            "boundary_kind",
            "field_refs",
            "direction",
            "note",
            "code",
        ],
    )
    _write_tsv(
        hp_update_side_paths_path,
        hp_update_side_path_rows,
        [
            "path_kind",
            "row_kind",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "line_end",
            "packet_or_vo",
            "field_refs",
            "call_arg_count",
            "expected_arg_count",
            "arg_status",
            "param_index",
            "hurt_data_param",
            "hurt_data_field",
            "arg_expr",
            "resolved_expr",
            "semantics",
            "note",
            "code",
        ],
    )
    _write_tsv(
        fight_state_sync_paths_path,
        fight_state_sync_rows,
        [
            "path_kind",
            "row_kind",
            "packet_name",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "field_refs",
            "state_target",
            "creates_hurt_data",
            "shows_blood_tip",
            "semantics",
            "note",
            "code",
        ],
    )
    _write_tsv(
        fight_request_intents_path,
        fight_request_intent_rows,
        [
            "flow_phase",
            "row_kind",
            "packet_name",
            "direction",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "field_refs",
            "has_damage_like_field",
            "intent_role",
            "semantics",
            "note",
            "code",
        ],
    )
    _write_tsv(
        fight_cast_broadcast_flow_path,
        fight_cast_broadcast_flow_rows,
        [
            "flow_stage",
            "row_kind",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "field_refs",
            "semantics",
            "note",
            "code",
        ],
    )
    _write_tsv(
        skill_instance_lifecycle_path,
        skill_instance_lifecycle_rows,
        [
            "flow_stage",
            "row_kind",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "field_refs",
            "semantics",
            "note",
            "code",
        ],
    )
    _write_tsv(
        fight_authority_boundary_path,
        fight_authority_boundary_rows,
        [
            "phase_order",
            "phase_id",
            "authority",
            "packet_or_event",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "field_refs",
            "local_effect",
            "server_authority_note",
            "code",
        ],
    )
    _write_tsv(
        fight_side_channel_path,
        fight_side_channel_rows,
        [
            "channel_group",
            "row_kind",
            "packet_name",
            "direction",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "field_refs",
            "runtime_effect",
            "authority_note",
            "code",
        ],
    )
    _write_tsv(
        fight_status_codes_path,
        fight_status_code_rows,
        [
            "code_group",
            "row_kind",
            "code_name",
            "value",
            "hex_value",
            "bit_index",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "runtime_effect",
            "evidence",
            "code",
        ],
    )
    _write_tsv(
        sync_unit_skill_cd_path,
        sync_unit_skill_cd_rows,
        [
            "flow_stage",
            "row_kind",
            "packet_name",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "field_refs",
            "runtime_effect",
            "authority_note",
            "code",
        ],
    )
    _write_tsv(
        sync_unit_state_path,
        sync_unit_state_rows,
        [
            "flow_stage",
            "row_kind",
            "packet_name",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "field_refs",
            "runtime_effect",
            "visibility_note",
            "code",
        ],
    )
    _write_tsv(
        role_attribute_sync_path,
        role_attribute_sync_rows,
        [
            "flow_stage",
            "row_kind",
            "packet_name",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "field_refs",
            "runtime_effect",
            "authority_note",
            "code",
        ],
    )
    _write_tsv(
        attribute_definitions_path,
        attribute_definition_rows,
        [
            "property_code",
            "lua_symbol",
            "display_name",
            "name_lang_id",
            "group",
            "exp_show",
            "display_roles",
            "show_condition",
            "description",
            "details",
            "sort",
            "show_tips",
            "icon_patch",
            "icon",
            "source_file",
        ],
    )
    _write_tsv(
        gongfa_attr_change_path,
        gongfa_attr_change_rows,
        [
            "flow_stage",
            "row_kind",
            "packet_name",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "field_refs",
            "runtime_effect",
            "authority_note",
            "code",
        ],
    )
    _write_tsv(
        gongfa_state_path,
        gongfa_state_rows,
        [
            "flow_stage",
            "row_kind",
            "packet_name",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "field_refs",
            "runtime_effect",
            "authority_note",
            "code",
        ],
    )
    _write_tsv(
        gongfa_attr_display_path,
        gongfa_attr_display_rows,
        [
            "flow_stage",
            "row_kind",
            "packet_name",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "field_refs",
            "runtime_effect",
            "authority_note",
            "code",
        ],
    )
    _write_tsv(
        gongfa_rich_text_path,
        gongfa_rich_text_rows,
        [
            "flow_stage",
            "row_kind",
            "packet_name",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "field_refs",
            "runtime_effect",
            "authority_note",
            "code",
        ],
    )
    _write_tsv(
        gongfa_localization_templates_path,
        gongfa_localization_template_rows,
        [
            "localization_key",
            "usage_count",
            "method_refs",
            "source_file",
            "file_name",
            "line",
            "placeholder_count",
            "color_refs",
            "has_href",
            "rich_text",
            "plain_text",
            "status",
        ],
    )
    _write_tsv(
        gongfa_description_composition_path,
        gongfa_description_composition_rows,
        [
            "flow_stage",
            "row_kind",
            "source_file",
            "file_name",
            "function_name",
            "line",
            "config_refs",
            "localization_keys",
            "data_refs",
            "composition_role",
            "code",
        ],
    )
    _write_tsv(
        skill_mgr_refs_path,
        skill_mgr_ref_rows,
        ["symbol", "role", "bundle", "file", "relative_path", "function_name", "line", "code"],
    )
    _write_tsv(
        projected_skills_path,
        projected_skill_rows,
        [
            "gongfa_id",
            "star",
            "lingjie_star_id",
            "projected_skill_id",
            "match_status",
            "skill_row_count",
            "skill_name",
            "skill_type",
            "is_active_skill",
            "timeline_id",
            "cd_time",
            "public_cd_group",
            "public_cd",
            "pre_skill",
            "stat_skill_group",
            "stat_skill",
            "icon",
            "scope",
            "target_type",
            "target_max",
            "condition",
            "power",
            "fight_score",
            "lingjie_star_cd",
            "lingjie_star_describe",
            "lingjie_star_param",
        ],
    )
    _write_tsv(
        skill_next_hops_path,
        skill_next_hop_rows,
        [
            "gongfa_id",
            "star",
            "lingjie_star_id",
            "projected_skill_id",
            "skill_name",
            "skill_type",
            "next_hop_kind",
            "timeline_ids",
            "timeline_channels",
            "timeline_channel_match_count",
            "lingjie_jie_ref_count",
            "lingjie_jie_refs",
            "main_feature_pin_ref_count",
            "main_feature_pin_refs",
            "cd_time",
            "public_cd_group",
            "public_cd",
            "scope",
            "target_type",
            "target_max",
            "fight_score",
        ],
    )
    _write_tsv(
        projected_skill_damage_profiles_path,
        projected_skill_damage_profile_rows,
        [
            "gongfa_id",
            "star",
            "lingjie_star_id",
            "projected_skill_id",
            "skill_name",
            "skill_type",
            "career",
            "timeline_id",
            "timeline_status",
            "q_desc",
            "channel",
            "channel_alignment_status",
            "hit_count",
            "first_hit_ms",
            "last_hit_ms",
            "hit_times_ms",
            "hit_frames",
            "hurt_percents",
            "total_hurt_percent",
            "multi_hit_counts",
            "damage_scope_types",
            "scope_params",
            "trajectory_indexes",
            "hit_effect_sounds",
            "expected_times_ms",
            "channel_delta_ms",
            "effect_resources",
            "sound_ids",
            "cd_time",
            "public_cd_group",
            "public_cd",
            "scope",
            "target_type",
            "target_max",
            "fight_score",
        ],
    )
    _write_tsv(
        projected_skill_damage_families_path,
        projected_skill_damage_family_rows,
        [
            "family_id",
            "profile_count",
            "skill_count",
            "timeline_count",
            "gongfa_count",
            "careers",
            "sample_gongfas",
            "sample_skills",
            "sample_timelines",
            "channel",
            "hit_count",
            "first_hit_ms",
            "last_hit_ms",
            "hit_times_ms",
            "hit_frames",
            "hurt_percents",
            "total_hurt_percent",
            "multi_hit_counts",
            "damage_scope_types",
            "scope_params",
            "scope",
            "target_type",
            "target_max",
            "cd_times",
            "fight_scores",
        ],
    )
    _write_tsv(
        timeline_details_path,
        timeline_detail_rows,
        [
            "timeline_id",
            "status",
            "projected_skill_count",
            "careers",
            "sample_skills",
            "source_lua",
            "playable_asset",
            "q_type",
            "q_desc",
            "q_track_time",
            "q_keyframe_events",
            "q_hurt_events",
            "hurt_event_count",
            "display_name",
            "attack_track_count",
            "attack_clip_count",
            "attack_clip_types",
            "suffer_track_count",
            "suffer_clip_count",
            "suffer_clip_types",
            "effect_resources",
            "action_names",
            "sound_ids",
        ],
    )
    _write_tsv(
        timeline_clips_path,
        timeline_clip_rows,
        [
            "timeline_id",
            "careers",
            "q_desc",
            "sample_skills",
            "source_lua",
            "track_side",
            "track_index",
            "track_name",
            "parent_name",
            "track_sub_name",
            "track_payload_name",
            "track_type",
            "track_frame_count",
            "track_total_time",
            "clip_index",
            "clip_type",
            "clip_role",
            "start_time",
            "start_frame",
            "end_time",
            "end_frame",
            "duration",
            "loop",
            "loop_count",
            "end_play",
            "clip_id",
            "link_id",
            "res_name",
            "random_res_names",
            "action_name",
            "sound_id",
            "hit_effect_sound",
            "frame",
            "hurt_index",
            "hurt_percent",
            "hurt_multi_count",
            "hurt_multi_duration",
            "real_multi_hurt",
            "damage_center_type",
            "damage_scope_type",
            "scope_param1",
            "scope_param2",
            "trajectory_index",
            "trajectory_type",
            "fly_speed",
            "bind_target",
            "end_bind_target",
            "is_bind",
            "is_hit_effect",
            "effect_start_type",
            "effect_last_time",
            "args_json",
        ],
    )
    _write_tsv(
        timeline_clip_types_path,
        timeline_clip_type_summary_rows,
        [
            "clip_type",
            "clip_role",
            "clip_count",
            "track_sides",
            "track_names",
            "track_payload_names",
            "args_keys",
            "sample_timeline_id",
            "sample_track_side",
            "sample_track_name",
            "sample_start_frame",
            "sample_end_frame",
            "sample_res_name",
            "sample_action_name",
            "sample_sound_id",
            "sample_hurt_percent",
            "sample_args_json",
        ],
    )
    _write_tsv(
        timeline_hit_frames_path,
        timeline_hit_frame_rows,
        [
            "timeline_id",
            "careers",
            "q_desc",
            "sample_skills",
            "source_lua",
            "hit_index",
            "track_side",
            "track_name",
            "start_frame",
            "end_frame",
            "frame",
            "frame_time_ms",
            "hurt_percent",
            "hurt_multi_count",
            "hurt_multi_duration",
            "real_multi_hurt",
            "damage_center_type",
            "damage_scope_type",
            "scope_param1",
            "scope_param2",
            "trajectory_index",
            "hit_effect_sound",
            "hurt_event_index",
            "hurt_event_time_ms",
            "hurt_event_frame",
            "hurt_event_delta_ms",
            "hurt_event_values",
            "args_json",
        ],
    )
    _write_tsv(
        timeline_channel_alignment_path,
        timeline_channel_alignment_rows,
        [
            "timeline_id",
            "status",
            "careers",
            "q_desc",
            "hurt_event_count",
            "hurt_event_times_ms",
            "channels",
            "expected_times_ms",
            "delta_ms",
            "source_refs",
        ],
    )
    _write_tsv(
        effect_assets_path,
        effect_asset_rows,
        [
            "timeline_id",
            "careers",
            "q_desc",
            "sample_skills",
            "effect_resource",
            "status",
            "asset_count",
            "asset_paths",
            "asset_size_bytes",
        ],
    )
    _write_tsv(
        effect_bundle_objects_path,
        effect_bundle_object_rows,
        [
            "asset_path",
            "status",
            "size_bytes",
            "magic",
            "offset",
            "error",
            "object_total",
            "object_counts",
            "root_names",
            "gameobject_names",
            "material_names",
            "texture_names",
            "monoscript_names",
            "read_error_object_count",
            "timeline_ids",
            "effect_resources",
            "careers",
            "q_descs",
        ],
    )
    _write_tsv(
        playable_bundle_objects_path,
        playable_bundle_object_rows,
        [
            "timeline_id",
            "status",
            "size_bytes",
            "magic",
            "offset",
            "error",
            "object_total",
            "object_counts",
            "object_names",
            "monoscript_names",
            "read_error_object_count",
            "careers",
            "q_desc",
            "sample_skills",
            "playable_asset",
        ],
    )
    _write_tsv(
        timeline_sound_refs_path,
        timeline_sound_ref_rows,
        [
            "timeline_id",
            "sound_id",
            "match_status",
            "sound_type",
            "loop",
            "sound_event_name",
            "sound_event_id",
            "sound_bank",
            "bank_path",
            "bank_exists",
            "bank_size_bytes",
            "event_id_hit_in_bank",
            "hirc_status",
            "hirc_action_ids",
            "hirc_sound_object_ids",
            "wem_ids",
            "wem_ids_in_didx",
            "wem_details",
            "raw_wem_paths",
            "raw_wem_bytes",
            "careers",
            "q_desc",
            "sample_skills",
        ],
    )
    _write_tsv(
        apk_symbol_hits_path,
        apk_symbol_rows,
        [
            "file_role",
            "relative_path",
            "size_bytes",
            "term",
            "hit_count",
            "first_offset_hex",
            "sample_offsets_hex",
            "snippet",
        ],
    )

    top_config_lines = [
        f"- `{name}`：{count} 次；{_LINGJIE_CONFIG_ROLES.get(name, '未归类')}"
        for name, count in config_counts.most_common()
    ]
    key_packet_lines = [
        f"- `{row['id']}` `{row['name']}`：{row['direction']}，{row['packet_role']}，字段 `{row['fields']}`"
        for row in runtime_packet_rows
        if str(row["name"]) in {
            "CM_GongFaHomeMakeCombine",
            "SM_GongFaHomeMakeCombine",
            "CM_GongFaHomeMakeCombineList",
            "SM_GongFaHomeMakeCombineList",
            "CM_GongFaHomeMakeLearn",
            "SM_GongFaHomeMakeLearn",
            "CM_GongFaHomeMakePageList",
            "SM_GongFaHomeMakePageList",
            "CM_GongFaTenCreateCheck",
            "SM_GongFaTenCreateCheck",
        }
    ]
    key_vo_lines = [
        f"- `{vo}`：{sum(1 for row in vo_field_rows if row['vo_name'] == vo)} 个字段；被引用 {vo_usage_counts.get(vo, 0)} 次"
        for vo in ["GongFaHomeMakeVO", "CreateSkillCommonVO", "HMFilterVO", "GongFaLearnItemVO", "GongFaTeachItemVO"]
        if vo in value_object_names
    ]
    integration_lines = [
        f"- `{category}`：{count} 次"
        for category, count in integration_category_counts.most_common()
    ]
    key_equip_packet_lines = [
        f"- `{row['id']}` `{row['name']}`：{row['direction']}，{row['packet_role']}，字段 `{row['fields']}`；{row['note']}"
        for row in equip_packet_rows
        if str(row["name"])
        in {
            "CM_ReplaceSkill",
            "SkillInfoVO",
            "SkillProgramVO",
            "CM_GongFaSaveProgram",
            "GongFaProgramVO",
            "CM_XinFaPutUp",
            "XinFaVO",
            "HomeMakeXinFaVO",
        }
    ]
    equip_flow_lines = [
        f"- `{stage}`：{count} 次"
        for stage, count in equip_flow_stage_counts.most_common()
    ]
    state_update_lines = [
        f"- `{stage}`：{count} 次"
        for stage, count in state_update_stage_counts.most_common()
    ]
    skill_core_lines = [
        f"- `{stage}`：{count} 次"
        for stage, count in skill_core_stage_counts.most_common()
    ]
    battle_damage_flow_lines = [
        f"- `{stage}`：{count} 次；{_battle_damage_flow_semantics(stage)}"
        for stage, count in battle_damage_stage_counts.most_common()
    ]
    skill_packet_lines = [
        f"- `{row['id']}` `{row['name']}`：{row['direction']}，字段 `{row['fields']}`；{row['note']}"
        for row in skill_packet_rows
    ]
    fight_result_schema_lines = [
        f"- `{row['id']}` `{row['schema_name']}`：{row['direction']}，{row['schema_role']}，字段 `{row['fields']}`；{row['note']}"
        for row in {
            str(item["schema_name"]): item
            for item in fight_result_schema_rows
        }.values()
    ]
    fight_effect_flag_lines = [
        f"- `{row['effect_name']}` = {row['value']} ({row['hex_value']})："
        f"{row['semantics'] or '未补充语义'}；飘字前缀 `{row['hurt_tip_prefix']}`，血条 `{row['blood_type']}`"
        for row in fight_effect_flag_rows
        if row.get("usage_count") or row.get("hurt_tip_prefix") or row.get("blood_type")
    ]
    hurt_tips_config_lines = [
        f"- `{row['config_key']}` `{row['raw_pair']}` -> `{row['mapped_name'] or row['mapped_value']}`："
        f"配置 {row['configured_duration_ms']}ms，运行计时 {row['runtime_timer_seconds']}"
        for row in hurt_tips_config_rows
    ]
    blood_type_ui_lines = [
        f"- `{row['blood_type_name']}`={row['value']}：prefab `{row['prefab_var'] or '未映射'}`"
        f"#{row['prefab_component_id'] or '-'}，动画 `{row['animation_name'] or '未映射'}`；"
        f"来源 FightCastEffect `{row['produced_by_fight_effects'] or '-'}`"
        for row in blood_type_ui_rows
        if row.get("prefab_var") or row.get("animation_name") or row.get("produced_by_fight_effects")
    ][:40]
    hurt_data_blood_source_lines = [
        f"- `{row['function_name']}:{row['line']}` `{row['call_kind']}`："
        f"`{row['source_metric'] or '-'}` -> `{row['blood_type_candidates'] or row['blood_type_expr'] or row['tips_type'] or '-'}`；"
        f"{row['source_field_hint'] or row['runtime_context']}"
        for row in hurt_data_blood_source_rows
        if row.get("call_kind") in {"direct_show_blood_tips", "simple_fight_type_decode"}
    ][:50]
    fight_result_to_hurt_data_lines = [
        f"- `{row['hurt_data_param']}` <- `{row['resolved_expr'] or row['arg_expr']}`："
        f"{row['fight_result_fields'] or '无直接 FightResultVO 字段'}；{row['transform_note'] or row['semantics']}"
        for row in fight_result_to_hurt_data_rows
        if row.get("call_kind") == "skillbase_fight_result_to_hurt_data"
    ]
    short_setdata_call_lines = [
        f"- `{function_name}:{line}` 调用传 {arg_count}/{expected_arg_count} 个参数；缺失参数按 Lua `nil` 和 `HurtData:SetData` 默认值处理。"
        for source_file, function_name, line, arg_count, expected_arg_count in sorted(
            {
                (
                    str(row.get("source_file") or ""),
                    str(row.get("function_name") or ""),
                    int(row.get("line") or 0),
                    int(row.get("call_arg_count") or 0),
                    int(row.get("expected_arg_count") or 0),
                )
                for row in fight_result_to_hurt_data_rows
                if int(row.get("call_arg_count") or 0)
                and int(row.get("expected_arg_count") or 0)
                and int(row.get("call_arg_count") or 0) != int(row.get("expected_arg_count") or 0)
            }
        )
    ]
    fight_result_boundary_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in fight_result_boundary_kind_counts.most_common()
    ]
    hp_update_side_path_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in hp_update_side_path_kind_counts.most_common()
    ]
    hp_update_side_path_summary_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in hp_update_side_path_counts.most_common()
    ]
    fight_state_sync_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in fight_state_sync_path_counts.most_common()
    ]
    fight_state_sync_kind_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in fight_state_sync_kind_counts.most_common()
    ]
    fight_request_intent_phase_lines = [
        f"- `{phase}`：{count} 行"
        for phase, count in fight_request_intent_phase_counts.most_common()
    ]
    fight_request_intent_kind_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in fight_request_intent_kind_counts.most_common()
    ]
    fight_cast_broadcast_flow_stage_lines = [
        f"- `{stage}`：{count} 行"
        for stage, count in fight_cast_broadcast_flow_stage_counts.most_common()
    ]
    fight_cast_broadcast_flow_kind_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in fight_cast_broadcast_flow_kind_counts.most_common()
    ]
    skill_instance_lifecycle_stage_lines = [
        f"- `{stage}`：{count} 行"
        for stage, count in skill_instance_lifecycle_stage_counts.most_common()
    ]
    skill_instance_lifecycle_kind_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in skill_instance_lifecycle_kind_counts.most_common()
    ]
    fight_authority_boundary_lines = [
        f"- `{authority}`：{count} 行"
        for authority, count in fight_authority_boundary_authority_counts.most_common()
    ]
    fight_authority_boundary_phase_lines = [
        f"- `{phase}`：{count} 行"
        for phase, count in fight_authority_boundary_phase_counts.most_common()
    ]
    fight_side_channel_group_lines = [
        f"- `{group}`：{count} 行"
        for group, count in fight_side_channel_group_counts.most_common()
    ]
    fight_side_channel_kind_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in fight_side_channel_kind_counts.most_common()
    ]
    fight_status_code_group_lines = [
        f"- `{group}`：{count} 行"
        for group, count in fight_status_code_group_counts.most_common()
    ]
    fight_status_code_kind_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in fight_status_code_kind_counts.most_common()
    ]
    sync_unit_skill_cd_stage_lines = [
        f"- `{stage}`：{count} 行"
        for stage, count in sync_unit_skill_cd_stage_counts.most_common()
    ]
    sync_unit_skill_cd_kind_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in sync_unit_skill_cd_kind_counts.most_common()
    ]
    sync_unit_state_stage_lines = [
        f"- `{stage}`：{count} 行"
        for stage, count in sync_unit_state_stage_counts.most_common()
    ]
    sync_unit_state_kind_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in sync_unit_state_kind_counts.most_common()
    ]
    role_attribute_sync_stage_lines = [
        f"- `{stage}`：{count} 行"
        for stage, count in role_attribute_sync_stage_counts.most_common()
    ]
    role_attribute_sync_kind_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in role_attribute_sync_kind_counts.most_common()
    ]
    attribute_definition_group_lines = [
        f"- `{group}`：{count} 条"
        for group, count in attribute_definition_group_counts.most_common(20)
        if group
    ]
    gongfa_attr_change_stage_lines = [
        f"- `{stage}`：{count} 行"
        for stage, count in gongfa_attr_change_stage_counts.most_common()
    ]
    gongfa_attr_change_kind_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in gongfa_attr_change_kind_counts.most_common()
    ]
    gongfa_state_stage_lines = [
        f"- `{stage}`：{count} 行"
        for stage, count in gongfa_state_stage_counts.most_common()
    ]
    gongfa_state_kind_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in gongfa_state_kind_counts.most_common()
    ]
    gongfa_attr_display_stage_lines = [
        f"- `{stage}`：{count} 行"
        for stage, count in gongfa_attr_display_stage_counts.most_common()
    ]
    gongfa_attr_display_kind_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in gongfa_attr_display_kind_counts.most_common()
    ]
    gongfa_rich_text_stage_lines = [
        f"- `{stage}`：{count} 行"
        for stage, count in gongfa_rich_text_stage_counts.most_common()
    ]
    gongfa_rich_text_kind_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in gongfa_rich_text_kind_counts.most_common()
    ]
    gongfa_description_composition_stage_lines = [
        f"- `{stage}`：{count} 行"
        for stage, count in gongfa_description_composition_stage_counts.most_common()
    ]
    gongfa_description_composition_kind_lines = [
        f"- `{kind}`：{count} 行"
        for kind, count in gongfa_description_composition_kind_counts.most_common(20)
    ]
    skill_mgr_ref_lines = [
        f"- `{role}`：{count} 次"
        for role, count in skill_mgr_role_counts.most_common()
    ]
    projected_skill_lines = [
        f"- `skillType={skill_type}`：{count} 条"
        for skill_type, count in projected_skill_type_counts.most_common(20)
    ]
    if projected_skill_scan.get("status") != "ok":
        projected_skill_lines = ["- 未同时找到 `LingjieGongfaStar` 和 `Skill` 的解析结果，本轮未生成投影关联。"]
    skill_next_hop_lines = [
        f"- `{kind}`：{count} 条"
        for kind, count in skill_next_hop_kind_counts.most_common()
    ]
    if skill_next_hop_scan.get("status") != "ok":
        skill_next_hop_lines = ["- 缺少 `Skill / SkillExParams` 等解析结果，本轮未生成技能下一跳关联。"]
    projected_skill_damage_profile_lines = [
        f"- 画像行：{stats['projected_skill_damage_profile_count']} 条，覆盖 {stats['projected_skill_damage_profile_skill_count']} 个投影技能、{stats['projected_skill_damage_profile_timeline_count']} 条 timeline，缺 timeline {stats['projected_skill_damage_profile_missing_timeline_count']}，缺 channel {stats['projected_skill_damage_profile_missing_channel_count']}"
    ]
    projected_skill_damage_family_lines = [
        f"- 伤害模式 family：{stats['projected_skill_damage_family_count']} 套；跳过无可读 timeline/hit_frame 的画像 {stats['projected_skill_damage_family_skipped_profile_count']} 条"
    ]
    timeline_detail_lines = [
        f"- `{status}`：{count} 条"
        for status, count in timeline_detail_status_counts.most_common()
    ]
    timeline_clip_role_lines = [
        f"- `{role}`：{count} 条"
        for role, count in timeline_clip_role_counts.most_common()
    ]
    if not timeline_clip_role_lines:
        timeline_clip_role_lines = ["- 未从 timeline 轨道中展开出 clip 事件。"]
    timeline_clip_type_lines = [
        f"- `ClipType={row['clip_type']}` `{row['clip_role']}`：{row['clip_count']} 条；轨道 `{row['track_names']}`"
        for row in timeline_clip_type_summary_rows
    ]
    if not timeline_clip_type_lines:
        timeline_clip_type_lines = ["- 未从 timeline 轨道中归纳出 ClipType。"]
    timeline_hit_frame_lines = [
        f"- hit_frame：{stats['timeline_hit_frame_count']} 条；q_hurt_events 对齐 {stats['timeline_hit_frame_hurt_event_match_count']} / {stats['timeline_hit_frame_total_hurt_event_count']} 条"
    ]
    timeline_channel_alignment_lines = [
        f"- SkillExParams.channel：{stats['timeline_channel_alignment_matched_count']} / {stats['timeline_channel_alignment_count']} 条对齐，缺失 {stats['timeline_channel_alignment_missing_count']} 条"
    ]
    effect_asset_lines = [
        f"- `{status}`：{count} 条"
        for status, count in effect_asset_status_counts.most_common()
    ]
    if not effect_asset_lines:
        effect_asset_lines = ["- 未从 timeline 轨道中提取到特效资源引用。"]
    effect_bundle_object_lines = [
        f"- `{status}`：{count} 个资源"
        for status, count in effect_bundle_object_status_counts.most_common()
    ]
    if not effect_bundle_object_lines:
        effect_bundle_object_lines = ["- 未找到可检查的特效 bundle 文件。"]
    effect_object_type_lines = [
        f"- `{type_name}`：{count} 个对象"
        for type_name, count in Counter(effect_bundle_object_scan.get("object_type_counts", {})).most_common(20)
    ]
    playable_bundle_object_lines = [
        f"- `{status}`：{count} 个资源"
        for status, count in playable_bundle_object_status_counts.most_common()
    ]
    if not playable_bundle_object_lines:
        playable_bundle_object_lines = ["- 未找到可检查的 playable bundle 文件。"]
    playable_object_type_lines = [
        f"- `{type_name}`：{count} 个对象"
        for type_name, count in Counter(playable_bundle_object_scan.get("object_type_counts", {})).most_common(20)
    ]
    timeline_sound_ref_lines = [
        f"- `{status}`：{count} 条"
        for status, count in timeline_sound_ref_status_counts.most_common()
    ]
    if not timeline_sound_ref_lines:
        timeline_sound_ref_lines = ["- 未从 timeline 中提取到 sound id。"]
    apk_symbol_lines = [
        f"- `{term}`：{count} 次"
        for term, count in apk_symbol_term_counts.most_common(30)
    ]
    if apk_symbol_scan.get("status") == "not_requested":
        apk_symbol_lines = ["- 未传入 APK 解包目录，本轮未扫描 APK 二进制符号。"]
    elif not apk_symbol_lines:
        apk_symbol_lines = ["- 已扫描 APK 目标文件，但未命中 SkillMgr/功法装备相关符号。"]
    report_path.write_text(
        "\n".join(
            [
                "# LingjieGongfa 客户端运行链路静态报告",
                "",
                "## 观察结论",
                "",
                "- 词条、阶数、星级、成本等展示和计算入口主要来自 `LingjieGongfa_*` 静态配置表。",
                "- `GongfahomemakeData.lua` 负责把配置表缓存成按 `gongfaId`、`featureGroup`、`jie`、`star`、`pin` 访问的索引。",
                "- `GongfahomemakeModel.lua` 负责把技能 id / 功法 id 映射回词条组，再取主词条和副词条配置。",
                "- 合成、点亮、学习、请教、上架、筛选列表等状态通过 `player.gongfahomemake` packet 与服务端同步；静态配置只能解释展示逻辑，不能单独代表最终运行状态。",
                "- `CreateSkillCommonVO.effectMap / xianEffectMap` 在客户端类里只读不写，词条集合更像是服务端下发给客户端展示、排序、消耗计算的结果字段。",
                "",
                "## 统计",
                "",
                f"- LingjieGongfa 配置引用：{stats['config_ref_count']} 次，{stats['config_name_count']} 张表，{stats['config_file_count']} 个 Lua 文件",
                f"- GongFaHomeMake packet：{stats['packet_count']} 个，字段：{stats['packet_field_count']} 个",
                f"- 核心 VO：{stats['value_object_count']} 个，VO 字段：{stats['value_object_field_count']} 个，VO 引用：{stats['value_object_usage_count']} 次",
                f"- NetLogic 函数：{stats['net_function_count']} 个，调用点：{stats['net_call_site_count']} 个",
                f"- 上阵/战斗集成引用：{stats['battle_integration_ref_count']} 次，{stats['battle_integration_file_count']} 个文件",
                f"- 装备链路 packet/VO：{stats['equip_packet_count']} 个，证据行：{stats['equip_flow_ref_count']} 次",
                f"- 状态落点证据行：{stats['state_update_ref_count']} 次",
                f"- SkillMgr 核心实现证据行：{stats['skill_core_flow_ref_count']} 次，{stats['skill_core_file_count']} 个文件",
                f"- 战斗伤害分段证据行：{stats['battle_damage_flow_ref_count']} 次，{stats['battle_damage_flow_file_count']} 个文件",
                f"- FightResult 回包 schema：{stats['fight_result_schema_count']} 个 packet/VO，字段 {stats['fight_result_schema_field_count']} 个",
                f"- FightCastEffect 标志：{stats['fight_effect_flag_count']} 个，HurtData 可直接解释 {stats['fight_effect_formatted_flag_count']} 个",
                f"- Fight.ConfigValue：{stats['fight_config_value_count']} 行，HurtTips 专项配置 {stats['hurt_tips_config_row_count']} 行",
                f"- BloodType/UI 飘字：{stats['blood_type_count']} 个枚举，UI 映射 {stats['blood_type_ui_count']} 个，动画映射 {stats['blood_type_animation_count']} 个",
                f"- HurtData 飘字来源：{stats['hurt_data_blood_source_count']} 行，直接 ShowBloodTips {stats['hurt_data_direct_show_count']} 行，简单战斗聚合 {stats['hurt_data_simple_aggregate_count']} 行，HurtTipsType 解码 {stats['hurt_tips_type_decode_count']} 行",
                f"- FightResult -> HurtData：{stats['fight_result_to_hurt_data_count']} 个参数映射，覆盖 {stats['fight_result_to_hurt_data_field_count']} 个 FightResultVO 字段",
                f"- FightResult 回包边界：{stats['fight_result_boundary_count']} 行，{stats['fight_result_boundary_file_count']} 个文件，{stats['fight_result_boundary_kind_count']} 类节点",
                f"- HP/MP/Buff 旁路更新：{stats['hp_update_side_path_count']} 行，协议字段 {stats['hp_update_side_path_field_count']} 行，HurtData 参数映射 {stats['hp_update_side_path_param_count']} 行",
                f"- Fight 状态同步：{stats['fight_state_sync_count']} 行，协议字段 {stats['fight_state_sync_field_count']} 行，属性写入 {stats['fight_state_sync_property_write_count']} 行，HurtData 创建 {stats['fight_state_sync_hurtdata_count']} 行",
                f"- Fight 请求意图：{stats['fight_request_intent_count']} 行，客户端请求字段 {stats['fight_request_intent_request_field_count']} 个，请求侧伤害字段 {stats['fight_request_intent_damage_field_count']} 个，发送点 {stats['fight_request_intent_send_count']} 个",
                f"- Fight 释放广播链路：{stats['fight_cast_broadcast_flow_count']} 行，覆盖 {stats['fight_cast_broadcast_flow_file_count']} 个文件、{stats['fight_cast_broadcast_flow_stage_count']} 个阶段，技能启动点 {stats['fight_cast_broadcast_flow_skill_start_count']} 个",
                f"- 技能实例生命周期：{stats['skill_instance_lifecycle_count']} 行，覆盖 {stats['skill_instance_lifecycle_file_count']} 个文件、{stats['skill_instance_lifecycle_stage_count']} 个阶段，FightResult 写入 HurtData {stats['skill_instance_lifecycle_result_to_hurtdata_count']} 行",
                f"- Fight 权威边界：{stats['fight_authority_boundary_count']} 行，{stats['fight_authority_boundary_phase_count']} 个阶段，服务端权威/同步行 {stats['fight_authority_boundary_server_authority_count']} 行",
                f"- Fight side-channel：{stats['fight_side_channel_count']} 行，{stats['fight_side_channel_packet_count']} 个 packet/VO，运行证据 {stats['fight_side_channel_runtime_count']} 行，{stats['fight_side_channel_group_count']} 类通道",
                f"- Fight 状态码：{stats['fight_status_code_count']} 行，RestrictStatus 枚举 {stats['fight_restrict_status_enum_count']} 个、消费点 {stats['fight_restrict_status_usage_count']} 个，UnitState 消费点 {stats['fight_unit_state_usage_count']} 个",
                f"- SyncUnit 技能/CD 同步：{stats['sync_unit_skill_cd_count']} 行，协议字段 {stats['sync_unit_skill_cd_packet_field_count']} 行，运行证据 {stats['sync_unit_skill_cd_runtime_count']} 行，覆盖 {stats['sync_unit_skill_cd_stage_count']} 个阶段",
                f"- SyncUnit 状态同步：{stats['sync_unit_state_count']} 行，协议字段 {stats['sync_unit_state_packet_field_count']} 行，运行证据 {stats['sync_unit_state_runtime_count']} 行，源码缺口 {stats['sync_unit_state_gap_count']} 行",
                f"- 角色属性/战力同步：{stats['role_attribute_sync_count']} 行，协议字段 {stats['role_attribute_sync_packet_field_count']} 行，运行证据 {stats['role_attribute_sync_runtime_count']} 行，属性写入 {stats['role_attribute_sync_property_write_count']} 行",
                f"- 属性定义字典：{stats['attribute_definition_count']} 条，showTips 属性 {stats['attribute_definition_show_tips_count']} 条，Ratio 类属性 {stats['attribute_definition_ratio_group_count']} 条",
                f"- 功法学习/升级属性链：{stats['gongfa_attr_change_count']} 行，协议字段 {stats['gongfa_attr_change_packet_field_count']} 行，运行证据 {stats['gongfa_attr_change_runtime_count']} 行，属性应用 {stats['gongfa_attr_change_apply_count']} 处",
                f"- 功法图鉴/状态初始化：{stats['gongfa_state_count']} 行，协议字段 {stats['gongfa_state_packet_field_count']} 行，运行证据 {stats['gongfa_state_runtime_count']} 行，继承 SimpleItemVO 字段 {stats['gongfa_state_inherited_simple_item_field_count']} 个，静态图鉴构建 {stats['gongfa_state_static_catalog_count']} 行，VO 覆盖 {stats['gongfa_state_vo_overlay_count']} 行，源码缺口 {stats['gongfa_state_gap_count']} 行",
                f"- 功法属性展示/预览：{stats['gongfa_attr_display_count']} 行，覆盖 {stats['gongfa_attr_display_stage_count']} 个阶段，Attribute 配置引用 {stats['gongfa_attr_display_attribute_config_ref_count']} 行，预览入口 {stats['gongfa_attr_display_preview_call_count']} 行，格式化 {stats['gongfa_attr_display_format_count']} 行",
                f"- 功法详情富文本：{stats['gongfa_rich_text_count']} 行，覆盖 {stats['gongfa_rich_text_stage_count']} 个阶段，语言模板 {stats['gongfa_rich_text_localization_key_count']} 个，颜色引用 {stats['gongfa_rich_text_color_ref_count']} 行，配置描述引用 {stats['gongfa_rich_text_config_description_count']} 行",
                f"- 功法语言模板：{stats['gongfa_localization_template_count']} 个引用模板，已解析 {stats['gongfa_localization_template_ok_count']} 个，缺失 {stats['gongfa_localization_template_missing_count']} 个，含颜色 {stats['gongfa_localization_template_color_count']} 个，含 href {stats['gongfa_localization_template_href_count']} 个",
                f"- 功法详情文案拼装：{stats['gongfa_description_composition_count']} 行，覆盖 {stats['gongfa_description_composition_stage_count']} 个阶段，语言 key {stats['gongfa_description_composition_localization_key_count']} 个，通玄相关 {stats['gongfa_description_composition_tongxuan_count']} 行",
                f"- SkillMgr 可见调用：{stats['skill_mgr_ref_count']} 次，player.skill 协议：{stats['skill_packet_count']} 个",
                f"- LingjieGongfaStar -> Skill 投影：{stats['projected_skill_count']} 条，匹配 {stats['projected_skill_matched_count']} 条，缺失 {stats['projected_skill_missing_count']} 条",
                f"- Skill 下一跳：时间线 {stats['skill_next_hop_timeline_skill_count']} 条，feature 复用 {stats['skill_next_hop_feature_reuse_count']} 条，未继续命中 {stats['skill_next_hop_no_static_count']} 条",
                f"- 投影技能伤害画像：{stats['projected_skill_damage_profile_count']} 条，覆盖 {stats['projected_skill_damage_profile_skill_count']} 个技能、{stats['projected_skill_damage_profile_timeline_count']} 条 timeline",
                f"- 投影技能伤害模式：{stats['projected_skill_damage_family_count']} 套 family",
                f"- Timeline 详情：{stats['timeline_detail_ok_count']} 条可读 LuaConfig，{stats['timeline_playable_asset_count']} 条命中 playable 资源",
                f"- Timeline clip 事件：{stats['timeline_clip_event_count']} 条，effect {stats['timeline_clip_effect_count']} 条，sound {stats['timeline_clip_sound_count']} 条，action {stats['timeline_clip_action_count']} 条，hit frame {stats['timeline_clip_hit_frame_count']} 条，含 Sound_Id {stats['timeline_clip_sound_id_ref_count']} 条",
                f"- Timeline 伤害时点：{stats['timeline_hit_frame_count']} 条 hit_frame，q_hurt_events 对齐 {stats['timeline_hit_frame_hurt_event_match_count']} / {stats['timeline_hit_frame_total_hurt_event_count']} 条",
                f"- Timeline channel 对齐：{stats['timeline_channel_alignment_matched_count']} / {stats['timeline_channel_alignment_count']} 条 SkillExParams.channel 命中 hurt_events 时间序列",
                f"- Timeline 特效资源：{stats['effect_asset_ref_count']} 条引用，{stats['effect_asset_unique_effect_count']} 个唯一资源名，命中 {stats['effect_asset_ok_count']} 条",
                f"- 特效 bundle 对象摘要：{stats['effect_bundle_object_ok_count']} / {stats['effect_bundle_object_asset_count']} 个资源可读",
                f"- Playable bundle 对象摘要：{stats['playable_bundle_object_ok_count']} / {stats['playable_bundle_object_asset_count']} 个资源可读",
                f"- Timeline 音效映射：{stats['timeline_sound_ref_count']} 条引用，{stats['timeline_sound_unique_id_count']} 个唯一 sound id，配置命中 {stats['timeline_sound_matched_count']} 条，WEM 链命中 {stats['timeline_sound_hirc_wem_hit_count']} 条，已导出 raw WEM {stats['timeline_sound_raw_wem_export_count']} 个",
                f"- APK 二进制符号命中：{stats['apk_symbol_hit_count']} 次，{stats['apk_symbol_file_count']} 个文件",
                "",
                "## 配置表角色",
                "",
                *top_config_lines,
                "",
                "## 关键 packet",
                "",
                *(key_packet_lines or ["- 未从 packet 索引中找到核心 packet。"]),
                "",
                "## 核心 VO",
                "",
                *(key_vo_lines or ["- 未从 packet 索引中找到核心 VO。"]),
                "",
                "### effectMap / xianEffectMap 方向",
                "",
                "- `effectMap`：普通灵界自创词条映射，客户端按 `key=skillId, value=FeatureBase.id` 解析；主词条分支满足 `key == mainId`。",
                "- `xianEffectMap`：仙界/飞升自创词条映射，客户端按 `key=FeatureBase.id, value=skillId` 解析；`GetScopeType` 用它是否为空判断是否仙界域。",
                "- 两个 map 在 `CreateSkillCommonVO.reading()` 中通过 `readMessageMap2Dic` 接收，但 `writing()` 未写出，说明静态客户端不掌握完整结果生成逻辑。",
                "",
                "## 上阵/战斗集成",
                "",
                "- `SkillProgramVO` 属于 `player.gongfa` 模块，但直接嵌入 `GongFaHomeMakeVO`，说明自创功法对象会进入通用功法方案/上阵协议对象。",
                "- `SkillInfoVO / ShowSkillVO / CM_ReplaceSkill` 等对象使用 `makeId` 承载自创实例 id；客户端再通过 `GongfahomemakeModel:GetGongFaHomeMakeVoById(makeId)` 找回完整 `GongFaHomeMakeVO`。",
                "- `GongFaBattle*` 界面会读取 `gongFaHomeMakeVO.skillCommonVO.effectMap / xianEffectMap` 做图标、品质、重复效果判断和上阵比较。",
                "",
                *(integration_lines or ["- 未找到上阵/战斗集成引用。"]),
                "",
                "### 装备请求链路",
                "",
                "- 神通/绝招自创功法装备时，`GongFaBattleMainPanel:EquipShenTongJueZhao` 把 `skillCommonVO.id` 放进 `CM_ReplaceSkill.makeId`，同时用 `LingjieGongfaStar.skill` 作为实际 `skillId`。",
                "- 心法自创功法装备时，`GongFaBattleMainPanel:EquipXinFa` 构造 `XinFaVO`，其中 `xinFaId` 是 `SkillInfoVO`，`xinFaId.makeId` 指向自创实例，再通过 `CM_XinFaPutUp` 提交整组心法槽。",
                "- 保存方案路径会构造 `GongFaProgramVO.skillList`；其中 `SkillProgramVO` 同时带 `homeMakeVO` 和 `skillInfoVO`，用于把完整展示对象和轻量引用一起保存/回显。",
                "- `HomeMakeXinFaVO.effectMap / xianEffectMap` 也只在 reading 中接收，writing 不写出；装备请求侧仍主要传 `makeId`，不是传完整词条结果。",
                "",
                *(key_equip_packet_lines or ["- 未找到装备相关 packet/VO。"]),
                "",
                *(equip_flow_lines or ["- 未找到装备链路证据行。"]),
                "",
                "### 状态落点",
                "",
                "- 心法上阵响应 `SM_XinFaPutUpFun` 会把 `msg.putUpList` 写入 `GongFaNewModel:SetXinFaInfo`，再落到 `GongFaNewData:SetXinFaInfo` 的 `xinFaPutUpList`。",
                "- 方案保存响应 `SM_GongFaSaveProgramFun` 会调用 `GongFaNewModel:GongFaSaveProgram`，最终用 `GongFaNewData:AddGongFaProgram` 更新 `programDic`。",
                "- 自创功法完整对象列表由 `GongfahomemakeData:SetGongFaHomeMakeList` 缓存在 `homeMakeDic`；`GetGongFaHomeMakeVoById(id)` 会遍历该字典，用 `skillCommonVO.id` 找回完整 `GongFaHomeMakeVO`。",
                "- 单槽替换响应已在 `SkillNetLogic:SM_ReplaceSkillFun` 中找到；它会把 `SM_ReplaceSkill` 写入 `SkillData:SetChangeSkillGroupData`，并刷新当前战斗技能组。",
                "",
                *(state_update_lines or ["- 未找到状态落点证据行。"]),
                "",
                "### SkillMgr 核心实现",
                "",
                "- `battle_*.bytes` 导出后已能看到 `SkillMgr / SkillNetLogic / SkillData / SkillModel` 的类实现；单槽替换不再是黑盒边界。",
                "- `SkillNetLogic:CM_ReplaceSkillFun(skillId, groupId, index, type, makeId, clientData)` 会写入 `CM_ReplaceSkill.skillId/groupId/index/type/makeId` 并发送；`makeId` 缺省时置为 0。",
                "- `SkillNetLogic:SM_ReplaceSkillFun(msg)` 在 `msg.code==0` 时执行回调、调用 `SkillMgr:SetChangeSkillGroupData(msg)`、刷新设置页、`ChangeBattleGroupSkills(msg.groupId)`，再触发功法战斗刷新事件。",
                "- `SkillData:SetChangeSkillGroupData(data)` 会维护 `groups[data.groupId].skills` 并通过 `SetSkillCD` 写入 CD 缓存；`GetDefaultSkillGroupData()` 固定读取默认组。",
                "- `SkillData:CheckGongFaIsEquipById(skillId, createId)` 从默认组扫描装备位；普通技能比对 `skillId`，自创功法比对 `v.type == Create` 且 `createId:Equal(v.makeId)`。",
                "- `player.skill` 协议族显示单槽替换、整组自动替换、切换组和拉取展示组共用 `groupId + skills + cds` 结构；自创功法仍通过 `SkillInfoVO.type/makeId` 进入这套结构。",
                "- `SkillConfig:GetTimelineIdBySkillId` 会按 `jian/mo/sha/xian_timelineId` 选择时间线；`SkillBase:UpdateTimelineData` 再用 timeline id 读取 `SkillExParams.channel`，有 channel 时设置 `real_section_dmg=true`。",
                "",
                *(skill_core_lines or ["- 未找到 SkillMgr 核心实现证据行。"]),
                "",
                "### 战斗伤害分段逻辑",
                "",
                "- `SkillBase:SetSM_FightResult` 显示客户端不是直接用 timeline 算最终伤害，而是接收服务端 `msg.results`，再按 `q_hurt_events` 里的百分比分摊到各段表现。",
                "- `SkillExParams.channel` 非空会让 `real_section_dmg=true`；此时 `hurt_index` 每次递增，只处理当前段 `hurt_event`，解释了为什么主动技能的多段伤害能和服务端分段回包对齐。",
                "- `damage_num = floor(resultVo.damage) * percent * 0.01`，`damage_view / recover / reflect / mpDamage` 也使用同一段百分比；因此当前静态画像里的 `total_hurt_percent` 是客户端分段比例，不是完整伤害公式。",
                "- `Damage_Scope_Type / Scope_Param` 在客户端主要有范围判定和调试表现逻辑；真正命中的目标、数值仍以后端回包为准。",
                "",
                *(battle_damage_flow_lines or ["- 未找到战斗伤害分段证据行。"]),
                "",
                "#### FightResult 回包 schema",
                "",
                "- `FightNetLogic:SM_FightResultFun` 按 `msg.casterId` 找到施法者视图，再把整包交给 `SkillActor:SetSM_FightResult4RunTimeSkill`，最终进入 `SkillBase:SetSM_FightResult`。",
                "- `SM_FightResult.results` 是 `FightResultVO` 列表；`SkillBase` 遍历列表时把每个目标的服务端总数值按当前 `hurt_event` 百分比分摊到表现层。",
                "",
                *(fight_result_schema_lines or ["- 未从 packet 索引中找到 FightResult schema。"]),
                "",
                "#### FightResult 回包边界",
                "",
                "- `FightResultVO.lua` 中伤害字段通过 `reading()` 从回包读取；`writing()` 是生成协议类的序列化对称方法，本轮未发现客户端把 `FightResultVO` 作为请求发出。",
                "- `FightNetLogic` 注册 `SM_FightResult*`，收到后按 `casterId / talismanId / location / buffId` 找到对应 `SkillActor`，再交给 `SkillBase:SetSM_FightResult` 消费。",
                "",
                *(fight_result_boundary_lines or ["- 未解析到 FightResult 回包边界证据。"]),
                "",
                "#### FightResult -> HurtData 参数映射",
                "",
                "- `SkillBase:SetSM_FightResult` 遍历 `msg.results`，将每个 `FightResultVO` 按 `hurt_event[2]` 百分比分摊后传给 `HurtData:SetData`；这些参数随后成为 `HurtData` 的 `damage_num / recoverHp_num / reflect_num / mp_damage_num / mpDamageAbsorb_num` 等字段。",
                "- `HurtFrameVo:SeparateHurtData` 还会在多段/多跳表现中把已经生成的 `HurtData` 再除以 `hurtCount`，这是表现层二次拆分，不是新的服务端回包字段。",
                *(short_setdata_call_lines or []),
                "",
                *(fight_result_to_hurt_data_lines or ["- 未解析到 FightResult 到 HurtData 的参数映射。"]),
                "",
                "#### HP/MP/Buff 旁路更新",
                "",
                "- `SM_UnitHpUpdate(60039)` 是普通 `SM_FightResult` 之外的直接血量更新路径：`FightNetLogic:SM_UnitHpUpdateFun -> EntityFightView:UpdateHpChange -> HurtData:SetData`。",
                "- `SM_UnitMpUpdate(60040)` 是直接蓝量更新路径：`FightNetLogic:SM_UnitMpUpdateFun -> UserView:UpdateMpChange -> HurtData:SetData`，会产生回蓝/扣蓝飘字。",
                "- `SM_BuffChangeHpAndMp` 承载 `BuffResultVO` 列表：`BuffNetLogic -> BuffMgr:UpdateBuffResult -> EntityFightView:AddBuffResult -> HurtData:SetData`，用于 Buff/持续伤害或恢复表现。",
                "- 这三条旁路都不经过 `SkillBase` 的 timeline `hurt_event` 百分比分段。",
                "",
                *(hp_update_side_path_summary_lines or ["- 未解析到 HP/Buff 旁路路径。"]),
                "",
                *(hp_update_side_path_lines or []),
                "",
                "#### Fight 状态同步",
                "",
                "- `SM_HpChange / SM_MpChange / SM_ShadowHpChange / SM_ShadowInfo / SM_UnitMaxHpUpdate` 直接写实体属性或特殊属性；这些路径不创建 `HurtData`，也不负责普通飘字。",
                "- `SM_FixDamage` 用 `CommonEventType.HURT_HP_CHANGE` 平滑 Boss 血条并最终写回 HP；它是血条事件表现，不是 `HurtData` 飘字路径。",
                "",
                *(fight_state_sync_lines or ["- 未解析到 Fight 状态同步路径。"]),
                "",
                *(fight_state_sync_kind_lines or []),
                "",
                "#### Fight 请求意图",
                "",
                "- `CM_FightByTarget / Dir / Position / Targets` 请求侧字段主要是 `casterId / skillId / targetId(s) / selectDir / selectPos / movePos / currPos`。本轮请求字段扫描未发现 `damage/recover/hp/mp` 一类数值字段。",
                "- `FightNetLogic:CM_FightBySkill` 会先走 `ReleaseSkillExecute` 做客户端释放判定/表现预执行，再由 `SendFightMessage` 按点选类型构造 CM 请求并 `F_SendMsg`。",
                "- `SM_FightCast* / FightCastVO` 是服务端释放广播，携带服务端确认后的释放目标、方向、位置、CD、阶星等表现字段；它仍不同于后续 `SM_FightResult` 伤害结果回包。",
                "",
                *(fight_request_intent_phase_lines or ["- 未解析到 Fight 请求意图路径。"]),
                "",
                *(fight_request_intent_kind_lines or []),
                "",
                "#### Fight 释放广播链路",
                "",
                "- `SM_FightCast*` 是服务端确认释放后的广播入口。普通释放先到 `FightMgr:EntityFightCast`，再分成本机用户 `OnUserCast` 和其他实体 `OnEntityCast` 两条表现路径。",
                "- `OnUserCast` 会启动 CD、修正速度和位移轨道；`OnEntityCast / EntityReleaseSkill` 会处理加载、排队、移动修正，最后也落到 `ReleaseSkillExecute` 或 `ReleaseMagicSkill`。",
                "- `ReleaseSkillExecute` 的普通路径会把 `_TempSkillParam` 传入 `SetState(StateType.Skill)`；随后 `StateMachine:ChangeState -> StateSkill:Enter -> SkillActor:ReleaseSkill` 才真正启动技能状态机。",
                "- `SkillActor:ReleaseMagicSkill / ReleasePassiveSkill / ReleaseSkill` 会构造或接收 `tParam` 并调用 `skillInfo:Start(...)`；这一步是表现/技能实例启动，不包含最终伤害数值。",
                "",
                *(fight_cast_broadcast_flow_stage_lines or ["- 未解析到 Fight 释放广播链路。"]),
                "",
                *(fight_cast_broadcast_flow_kind_lines or []),
                "",
                "#### 技能实例生命周期",
                "",
                "- `SkillActor:ReleaseSkill` 只负责选中/停止/启动本地 `SkillBase` 实例；真正的攻击 timeline、受击 timeline、伤害分段缓存都在 `SkillBase` 里推进。",
                "- `SkillBase:Start -> PlaySkillTimeline` 会播放攻击表现；`HurtEvent:OnStart -> SkillBase:Update4Hurt -> HurtFrameVo:CheckHurt` 才把已排队的 `HurtData` 执行成飘字/血条表现。",
                "- `SkillBase:SetSM_FightResult` 会把服务端 `msg.results` 分摊成 `HurtData` 并放进 `HurtFrameVo`；如果是弹道命中，则先经 `trajectoryCachedHurtVo / Bullet` 按弹道命中时机触发。",
                "",
                *(skill_instance_lifecycle_stage_lines or ["- 未解析到技能实例生命周期。"]),
                "",
                *(skill_instance_lifecycle_kind_lines or []),
                "",
                "#### Fight 权威边界",
                "",
                "- 端到端顺序可以概括为：`CM_FightBy*` 客户端意图 -> `SM_FightCast*` 服务端释放确认 -> 本地 `SkillBase` timeline 表现 -> `SM_FightResult*` 服务端目标结果 -> `HurtData` 飘字表现 -> `SM_HpChange/SM_MpChange` 等服务端属性同步。",
                "- 这张表把每一段标成 `client_intent / client_local_presentation / server_result / server_state_sync`，用于避免把本地 timeline 或 HurtData 表现误读成服务端伤害公式。",
                "",
                *(fight_authority_boundary_lines or ["- 未解析到 Fight 权威边界。"]),
                "",
                *(fight_authority_boundary_phase_lines or []),
                "",
                "#### Fight Side-Channel",
                "",
                "- 除 `SM_FightResult / HP / MP` 数值结果外，`FightNetLogic` 还处理一组服务端 side-channel：失败、打断、限制状态、timeline 播放、强制位移、CD、选择状态、引导施法和调试范围。",
                "- 这些通道主要用于状态同步和表现校正；它们能解释技能为什么被终止、位置为什么被修正、timeline 为什么被强制播放，但不能单独还原最终伤害公式。",
                "",
                *(fight_side_channel_group_lines or ["- 未解析到 Fight side-channel。"]),
                "",
                *(fight_side_channel_kind_lines or []),
                "",
                "#### Fight 状态码",
                "",
                "- `SM_RestrictStatus.restrictCode` 对应 `SkillDefine.RestrictStatus` bitmask；客户端用 `bit.band` 检查禁移动、不可选中、禁技能、只允许普攻、禁功法/闪避/普通攻击等状态。",
                "- `SM_UnitState.state` 写入 `serverUnitState`，当前真实 Lua 导出能看到 `idle / fight / fight_pvp / horse` 的消费点；但 `PlayerType.lua` 定义文件未出现在本轮导出里，因此真实枚举值暂以消费证据为主。",
                "",
                *(fight_status_code_group_lines or ["- 未解析到 Fight 状态码。"]),
                "",
                *(fight_status_code_kind_lines or []),
                "",
                "#### SyncUnit 技能/CD 同步",
                "",
                "- `SM_SyncUnit` 同包携带 `currHp/maxHp/currMp/maxMp/runSpeed` 和 `groupId/skills/cds/systemTime`；`FightNetLogic:SM_SyncUnitFun` 分别交给 `RoleMgr:ReviveInfo` 和 `SkillMgr:RefreshUserSkillCD`。",
                "- `SkillMgr:RefreshUserSkillCD` 用 `msg.groupId/msg.skills/msg.cds/msg.systemTime` 调 `SkillData:SetSkillCD`，再遍历 `msg.skills` 按 `skillVo.skillId` 刷新 `UserView.SkillActor` 中已加载技能的 CD。",
                "- `SkillData:SetSkillCD` 以 `groupId` 为一级 key，按 `skillList` 索引与 `cdList[index-1]` 对齐，用 `cdList[index-1] - systemTime` 换算剩余 CD，最终存入 `cdDic[groupId][skillId]`。",
                "- `SM_ReplaceSkill / SM_ChangeGroup / SM_AutoReplace` 也返回 `systemTime/groupId/skills/cds`，说明替换、切组、复活同步复用同构的服务端确认结构。",
                "",
                *(sync_unit_skill_cd_stage_lines or ["- 未解析到 SyncUnit 技能/CD 同步链路。"]),
                "",
                *(sync_unit_skill_cd_kind_lines or []),
                "",
                "#### SyncUnit 状态同步",
                "",
                "- `SM_SyncUnit` 的 HP/MP/速度/蓄力状态入口在 `FightNetLogic:SM_SyncUnitFun` 可见；它把同一个 msg 交给 `RoleMgr:ReviveInfo`。",
                "- `RoleMgr:ReviveInfo` 会在用户死亡且 `msg.currHp>0` 时触发 `userView:Revive()`，并把 `msg.chargeLv/currHp/maxHp/currMp/maxMp/runSpeed` 分别写入实体 `chargeLv / HP / MAXHP / MP / MAXMP / RUNSPEED`。",
                "- 可见的同类状态写入路径包括 `SM_ReviveFun` 直接写 `HP/MP` 并触发 `entityView:Revive()`，`SM_UnitMaxHpUpdateFun` 写 `MAXHP/HP`，`SM_ShadowInfoFun` 写 `SHADOWHP/SHADOWMAXHP`。",
                "- `Player.InitData` 也可见 `chargeLv` 和 `serverUnitState` 初始化，说明 `chargeLv` 是实体状态字段，不只是协议临时字段。",
                "",
                *(sync_unit_state_stage_lines or ["- 未解析到 SyncUnit 状态同步链路。"]),
                "",
                *(sync_unit_state_kind_lines or []),
                "",
                "#### 角色属性/战力同步",
                "",
                "- `SM_RoleChangedAttrs` 和 `SM_RealmUpRewardAttr` 都把 `ChangedAttrsVo` 交给 `GameUtil:DealAttrChangeByModule`；该函数遍历 `finalAttrs`，把最终属性值写入 `UserView.Entity:SetProperty(key, finalValue)`。",
                "- `SM_ChangedPlayerAttribute` 是另一条更直接的单位属性同步：按 `unitId` 定位战斗实体后遍历 `msg.attributes`，逐项 `SetProperty(k, v)`。",
                "- `SM_FightScore` 只下发 `score`，`RoleMgr:UpdateFightScore` 转为本地数字后写入 `LuaEntityPropertyType.FIGHT_POWER`，再按条件抛 `REFRESH_FIGHT_SCORE` 事件。",
                "- `SM_ModuleFightScore` 当前可见落点是 `GmMgr:ShowGmPowerView(msg)`，更像分模块战力调试/展示视图，不是总战力属性写入入口。",
                "",
                *(role_attribute_sync_stage_lines or ["- 未解析到角色属性/战力同步链路。"]),
                "",
                *(role_attribute_sync_kind_lines or []),
                "",
                "#### 属性定义字典",
                "",
                "- `LuaEntityPropertyType.InitPropertyTable` 会从 `Attribute.Attribute` 构建双向映射：`LuaEntityPropertyType[attribute.id] = attribute.code`，`PropertyNames[attribute.code] = attribute.id`。",
                "- `Attribute.Attribute` 已解析为 `property_code / lua_symbol / display_name / group / showTips / expShow / icon`；其中 `group` 决定 `GameUtil:GetPropertyTips` 是否按百分比展示。",
                "- 这张表是把协议 map 的数值 key 翻译成中文属性名的基础字典，例如 `FIGHT_POWER=战斗力`、`MAXHP=气血`、`ATTACK=攻击`。",
                "",
                *(attribute_definition_group_lines or ["- 未解析到 Attribute.Attribute 属性定义。"]),
                "",
                "#### 功法学习/升级属性链",
                "",
                "- `CM_GongFaLearn / CM_GongFaUpgrade` 只提交功法 id、升级类型和次数；属性增量不在客户端请求里计算。",
                "- `SM_GongFaLearn / SM_GongFaUpgrade` 返回 `gongfa / rewardResults / attrs`，`GongFaNewModel:GongFaLearn` 与 `UpgradeRefresh` 先更新 `GongFaItemVO`，再把 `msg.attrs` 交给 `GameUtil:DealAttrChangeByModule`。",
                "- `SM_GongFaUpgradeTimes` 批量升级会遍历 `upgradeList`，累加每次 `attrs.addAttrs`，并取最后一次 `attrs.finalAttrs` 作为最终属性值，再统一进入 `GameUtil`。",
                "- 因此功法学习/升级的属性落点与角色通用属性链相同，区别只在于 `ChangedAttrsVo` 的来源是功法回包。",
                "",
                *(gongfa_attr_change_stage_lines or ["- 未解析到功法属性变化链路。"]),
                "",
                *(gongfa_attr_change_kind_lines or []),
                "",
                "#### 功法图鉴/状态初始化",
                "",
                "- `GongFaNewData:LuaGongFaNewData` 从 `Gongfa_Gongfa / Gongfa_GongfaPin` 构建完整本地图鉴底座，`GongFaVo.cfg` 保存静态配置，未学习时 `GongFaVo.vo` 为空。",
                "- `CM_GongFaView` 是空请求；`SM_GongFaView` 下发 `actives / xinFaPutUpList / fazePutUpList / skillList / programVOList`，由 `GongFaNewModel:SetGongFaInfo` 分发到 Data 层和 FazeMgr。",
                "- 已学习或升级后的单个功法状态使用 `GongFaItemVO` 表达，自有字段是 `grade/jie/star/pin/tongxuan/quality/totalExp/qualityNum`；它继承 `SimpleItemVO.baseId/id/num`，其中 `baseId` 是 `UpdateGongFaVo` 覆盖本地 `gongFaDic` 的主键。",
                "- 当前可读 Lua 没有发现 `GongFaNewModel:SetGongFaVo(infoList)` 的外部调用点；这条批量初始化可能来自原生/背包系统或未导出脚本，Lua 内已确认的状态变更主要是学习/升级回包的 `UpdateGongFaVo`。",
                "- 因此客户端图鉴是“静态全量配置 + 服务端 VO 覆盖”的结构，不是服务端一次性下发全部功法配置。",
                "",
                *(gongfa_state_stage_lines or ["- 未解析到功法图鉴/状态初始化链路。"]),
                "",
                *(gongfa_state_kind_lines or []),
                "",
                "#### 功法属性展示/预览",
                "",
                "- 静态详情通常从 `GongFaVo.cfg.attr` 出发，经 `GongFaNewModel:GetAllAddAttrTb` 查 `Attribute_Attribute`，再按 `Attribute_Attribute.sort` 排序成 UI 属性条目。",
                "- 等级/星级预览会先用 `GetLevelAndStarAttr` 合并当前阶属性，再由 `GetIngoreSpecialAttrNextAdd / GetAllAttrNextAdd` 对比下一阶属性，写出 `addNum / isNew / nextValue` 这类展示字段。",
                "- `GongFaNewMgr:FormatAttrNum` 根据 `Attribute_Attribute.group` 决定普通数值还是百分比显示；这解释了游戏里属性文案的绿色加值和百分比格式，不代表客户端拥有最终属性结算权威。",
                "",
                *(gongfa_attr_display_stage_lines or ["- 未解析到功法属性展示/预览链路。"]),
                "",
                *(gongfa_attr_display_kind_lines or []),
                "",
                "#### 功法详情富文本",
                "",
                "- 普通功法详情面板直接把 `GongFaVo.cfg.descript` 写入 `LuaTextGamma`，所以配置/语言表中的 `<color>` 标签会原样进入 UI 渲染。",
                "- 属性条目通过 `LuaLocalization.Get/Format` 和 `GongFaNewMgr:FormatAttrNum` 生成属性名、当前值和绿色加值；部分颜色来自内联 `<color=#...>`，部分来自 `SetColor3`。",
                "- 仙术详情页会用 `GetMainDes` 和 `GongFa_LingJie_100/101/102/106/131` 这类语言模板拼出主/副效果、多段换行和激活/未激活颜色，再交给 `XianShuCreateItem` 渲染。",
                "",
                *(gongfa_rich_text_stage_lines or ["- 未解析到功法详情富文本链路。"]),
                "",
                *(gongfa_rich_text_kind_lines or []),
                "",
                "#### 功法语言模板",
                "",
                f"- 富文本链路共引用 {stats['gongfa_localization_template_count']} 个 `LuaLocalization` key，已从 `localization.lua` 解析 {stats['gongfa_localization_template_ok_count']} 个；其中 {stats['gongfa_localization_template_color_count']} 个模板含 `<color>`，{stats['gongfa_localization_template_href_count']} 个模板含 `<href>`。",
                f"- 这些模板的原始富文本已导出到 `{gongfa_localization_templates_path.name}`，可直接给前端图鉴做高亮预览。",
                "",
                "#### 功法详情文案拼装",
                "",
                "- `GongfahomemakeMgr:GetMainDes` 是主描述拼装入口：先把 `starCfg.param` 与 `jieCfg.param` 合并填入 `starCfg.describe`，再按通玄状态追加通玄描述和激活提示。",
                "- `XianShuCreateSkillDetailView` 负责把 `effectMap / xianEffectMap` 拆成主效果、副效果和仙界效果列表，并用 `GongFa_LingJie_100/101/102/106/131` 标记激活、未激活、悟境和通玄子描述。",
                "- 这张表记录的是 UI 文案结构，不表示效果数值由客户端结算；实际是否激活仍看服务端状态、VO 和本地学习/品阶状态。",
                "",
                *(gongfa_description_composition_stage_lines or ["- 未解析到功法详情文案拼装链路。"]),
                "",
                *(gongfa_description_composition_kind_lines or []),
                "",
                "#### FightCastEffect 标志",
                "",
                "- `FightResultVO.fightEffect` 是位标记；`HurtData:FormatHurtTipsAndType` 用 `bit.band` 选择免疫、闪避、格挡、暴击、特殊伤害等飘字分支。",
                "- `SKILL_MAIN_TARGET=2` 在 `SkillBase:SetSM_FightResult` 中单独用于决定是否播放目标受击 timeline；它不是伤害数值公式本身。",
                "",
                *(fight_effect_flag_lines or ["- 未从 SkillDefine/HurtData 中解析到 FightCastEffect 标志。"]),
                "",
                "#### HurtTips 聚合配置",
                "",
                "- `HurtTipsMgr` 从 `Fight.ConfigValue` 读取 `font_NormalDamage / font_OtherNormalFont / font_special_rose`。由于多个模块都有 `ConfigValue.lua`，这里按 `generate/cfg/fight_*/text_assets/ConfigValue.lua` 源路径解析，避免短表名覆盖。",
                "- `font_NormalDamage` 的左值 `index>0` 会转成 `2^index` 后对应 `FightCastEffect`；例如 `3_500` 对应 `CRIT=8`，表示暴击普通伤害聚合 0.5 秒。",
                "- `font_OtherNormalFont` 左值直接对应 `HurtTipsType`；当前 Lua 虽解析了配置毫秒值，但非普通伤害分支在 `AddTipsNum` 中把运行计时设为 1 秒。",
                "",
                *(hurt_tips_config_lines or ["- 未解析到 HurtTips 聚合配置。"]),
                "",
                "#### BloodType/UI 飘字表现",
                "",
                "- `BloodType.lua` 给出飘字类型枚举；`PanelBloodTips.lua` 把类型映射到 `UI/FightMainUI/PanelBloodTips_1` 的子节点；`BloodTipItem.lua` 决定每类飘字播放的动画名。",
                "- 这张表只描述客户端表现层：哪种服务端/表现分支会走哪套飘字 prefab 和动画，不代表数值公式本身。",
                "",
                *(blood_type_ui_lines or ["- 未解析到 BloodType/UI 飘字映射。"]),
                "",
                "#### HurtData 飘字来源",
                "",
                "- `HurtData` 是服务端战斗结果进入飘字表现层的主要落点：普通场景直接 `ShowBloodTips`，简单战斗先 `AddTipsNum` 聚合同类数值，再由 `HurtTipsMgr:ShowHurtTipsByType` 还原成字段结构。",
                "- 表中 `source_metric` 对应 `HurtData:SetData/SetPerSecondsData` 内部字段，可反查到 `FightResultVO.damage / recoverHp / damageReflect / mpAddDamage / mpDamageAbsorb` 等上游回包字段。",
                "",
                *(hurt_data_blood_source_lines or ["- 未解析到 HurtData 飘字来源。"]),
                "",
                *(skill_packet_lines or ["- 未找到 player.skill 协议。"]),
                "",
                *(skill_mgr_ref_lines or ["- 未找到 SkillMgr 调用点。"]),
                "",
                "### LingjieGongfaStar -> Skill 投影",
                "",
                "- `LingjieGongfaStar.skill` 是灵界功法不同星级投射到战斗技能表的关键字段；把它和 `Skill.id` 关联后，可以看到真实技能名、类型、目标、CD、战力等战斗侧属性。",
                "- 这一步仍是静态表关联，只说明客户端可读的配置关系；最终可装备/生效状态仍以后端下发的 `SkillInfoVO.type/makeId` 和当前技能组为准。",
                "",
                *(projected_skill_lines or ["- 未找到投影技能记录。"]),
                "",
                "### Skill 下一跳",
                "",
                "- `skillType=2` 的灵界主动技能主要通过 `jian/mo/sha/xian_timelineId` 指向四套时间线 id；这些 id 可继续命中 `SkillExParams.id`，其 `channel` 字段保存分段时序参数。",
                "- `skillType=5` 的灵界技能通常没有时间线字段；其中一部分同一个 skill id 会在 `LingjieGongfaJie.feature` 或 `MainFeaturePin.feature` 中复用，表示它同时承担展示/阶数/品阶特征 id 的角色。",
                f"- 本轮共发现 {skill_next_hop_scan.get('unique_timeline_id_count', 0)} 个唯一 timeline id，全部能命中 `SkillExParams` 的 channel 配置。",
                "",
                *(skill_next_hop_lines or ["- 未找到技能下一跳记录。"]),
                "",
                "### 投影技能伤害画像",
                "",
                "- 这张表把 `LingjieGongfaStar.skill -> Skill.jian/mo/sha/xian_timelineId -> SkillExParams.channel -> timeline hit_frame` 合成到同一行，用来直接查某个星级技能在不同职业分支下的命中时点和伤害段。",
                "- `total_hurt_percent` 是静态 `hit_frame.Hurt_Precent` 的加总，只表示客户端时间线配置里的段数比例；最终实战伤害仍会叠加属性、目标、防御、服务端校验等运行态因素。",
                "",
                *(projected_skill_damage_profile_lines or ["- 未生成投影技能伤害画像。"]),
                "",
                *(projected_skill_damage_family_lines or ["- 未生成投影技能伤害模式 family。"]),
                "",
                "### Timeline 详情",
                "",
                "- 已从 `luaconfig_*.bytes` 导出的 timeline LuaConfig 中提取 `q_hurt_events / q_keyframe_events / q_track_time / q_timeline_attacktrack / q_timeline_suffertrack`。",
                "- attack/suffer track 内部是嵌套 JSON；当前索引已逐 clip 展开 `ClipType / Start_Frame / End_Frame / res_Name / action_Name / Sound_Id / Hit` 等字段，不直接解释 Unity playable 二进制。",
                "- 如果要继续还原 playable 资源内部曲线和完整编辑器结构，下一步可能需要专门的 Unity 资产解析/反序列化工具。",
                "",
                *(timeline_detail_lines or ["- 未找到 timeline 详情。"]),
                "",
                "#### Timeline clip 事件",
                "",
                *(timeline_clip_role_lines or ["- 未找到 timeline clip 事件。"]),
                "",
                *(timeline_clip_type_lines or ["- 未找到 timeline ClipType 摘要。"]),
                "",
                "#### Timeline 伤害时点",
                "",
                *(timeline_hit_frame_lines or ["- 未找到 timeline hit_frame。"]),
                "",
                *(timeline_channel_alignment_lines or ["- 未找到 SkillExParams.channel 对齐结果。"]),
                "",
                "### Timeline 特效资源",
                "",
                "- 已把 timeline 轨道中的 `res_Name` 映射到本地 `effect/**.bytes` 文件；这能确认可视特效资源的落盘位置，但还不是特效内部粒子/材质结构解析。",
                "- 对命中的特效 bundle 继续抽取 Unity 对象摘要，当前能稳定拿到对象类型分布、部分 GameObject/Material/Texture/MonoScript 名称和对象读取错误数量。",
                f"- 资源根目录：`{effect_asset_scan.get('resource_root', '')}`",
                "",
                *(effect_asset_lines or ["- 未找到 timeline 特效资源。"]),
                "",
                *(effect_bundle_object_lines or ["- 未找到特效 bundle 对象摘要。"]),
                "",
                *(effect_object_type_lines or ["- 未汇总出 Unity 对象类型。"]),
                "",
                "### Timeline Playable 资源",
                "",
                "- 已对 `playable/skill/timeline*.bytes` 做 Unity 对象摘要；当前主要能确认 `TimelineConfig` 脚本壳和 AssetBundle 资源名，MonoBehaviour 内部字段仍有读取边界。",
                "",
                *(playable_bundle_object_lines or ["- 未找到 playable bundle 对象摘要。"]),
                "",
                *(playable_object_type_lines or ["- 未汇总出 playable Unity 对象类型。"]),
                "",
                "### Timeline 音效资源",
                "",
                "- `SoundMgr.PlaySound` 会先用 `SoundConfig.GetSoundConfig(soundId)` 读取 `Sound.Sound` 配置，再把 `soundEventName` 交给底层 Wwise bridge 播放；timeline 里的 `Sound_Id` 不是直接播放的 Wwise event id。",
                "- 当前已把 timeline sound id 关联到 `Sound.Sound.soundEventName / soundEventId / soundBank`，并验证 `soundEventId` 是否出现在声明的 `.bnk` 文件中。",
                "- 对 Wwise HIRC 做了轻量解析：event -> action -> sound object -> WEM id 可形成链路，并已按 DIDX offset/size 抽出原始 `.wem` 文件；WEM 解码成可试听音频仍需要外部转换器。",
                "",
                *(timeline_sound_ref_lines or ["- 未找到 timeline 音效映射。"]),
                "",
                "### APK/IL2CPP 符号边界",
                "",
                "- 这一节只做字节级符号命中，用来判断 APK 内是否存在相关类名/方法名字符串；它不能直接还原 IL2CPP 方法体或 Lua 调用图。",
                "- 如果只命中 `global-metadata.dat` 而没有可读 Lua 实现，下一步需要 IL2CPP 专用工具把 metadata 与 `libil2cpp.so` 对齐，才能继续追方法定义和调用关系。",
                "",
                *(apk_symbol_lines or ["- 未找到 APK 符号命中。"]),
                "",
                "## 输出文件",
                "",
                f"- `{config_refs_path.name}`：逐行配置表引用和所在函数",
                f"- `{function_refs_path.name}`：按函数聚合的配置引用",
                f"- `{packets_path.name}`：`player.gongfahomemake` packet 字段签名",
                f"- `{vo_fields_path.name}`：核心 VO 字段语义和客户端是否写出",
                f"- `{vo_usage_path.name}`：packet/VO 对核心 VO 的嵌套引用",
                f"- `{net_functions_path.name}`：`GongfahomemakeNetLogic.lua` 的收发函数",
                f"- `{net_call_sites_path.name}`：UI/Model/Mgr 调用 NetLogic 的位置",
                f"- `{battle_refs_path.name}`：`GongFaHomeMakeVO / makeId / SkillProgramVO` 进入 `gongfanew` 上阵展示链路的引用",
                f"- `{equip_packets_path.name}`：自创功法进入通用装备协议时涉及的 packet/VO 字段签名",
                f"- `{equip_flow_path.name}`：装备链路在 UI、Mgr、NetLogic、VO 中的证据行",
                f"- `{state_updates_path.name}`：装备/保存/自创列表回包落到本地模型缓存的证据行",
                f"- `{skill_core_flow_path.name}`：`SkillNetLogic / SkillMgr / SkillData` 的请求、回包、缓存和刷新证据行",
                f"- `{battle_damage_flow_path.name}`：`SkillBase / SkillConfig` 中 timeline、channel、hurt_event 百分比分段和表现调度证据行",
                f"- `{fight_result_schema_path.name}`：`SM_FightResult / FightResultVO` 战斗回包字段和客户端消费语义",
                f"- `{fight_result_to_hurt_data_path.name}`：`SkillBase:SetSM_FightResult` 到 `HurtData:SetData` 的参数级映射",
                f"- `{fight_result_boundary_path.name}`：`FightResultVO` 从协议读取到 `SkillBase` 消费的回包边界证据",
                f"- `{hp_update_side_paths_path.name}`：`SM_UnitHpUpdate / SM_UnitMpUpdate / BuffResultVO` 到 `HurtData:SetData` 的旁路 HP/MP/Buff 更新映射",
                f"- `{fight_state_sync_paths_path.name}`：`SM_HpChange / SM_MpChange / SM_FixDamage / SM_Shadow*` 等状态同步与血条事件路径",
                f"- `{fight_request_intents_path.name}`：`CM_FightBy*` 客户端请求意图、`F_SendMsg` 发送点和 `SM_FightCast*` 服务端释放广播证据",
                f"- `{fight_cast_broadcast_flow_path.name}`：`SM_FightCast* -> FightMgr -> StateMachine/StateSkill -> SkillActor:Start` 的释放广播到本地技能实例链路",
                f"- `{skill_instance_lifecycle_path.name}`：`SkillActor -> SkillBase -> HurtEvent/HurtFrameVo/HurtData` 的技能实例、timeline 和伤害表现生命周期",
                f"- `{fight_authority_boundary_path.name}`：从 `CM_FightBy*` 到 `SM_FightResult/SM_HpChange` 的客户端意图、本地表现和服务端权威边界总览",
                f"- `{fight_side_channel_path.name}`：失败、打断、限制、timeline、强制位移、CD、选择状态和引导施法等非伤害 side-channel 汇总",
                f"- `{fight_status_codes_path.name}`：`RestrictStatus` bitmask 和 `PlayerType.UnitState` 消费点的状态码解释索引",
                f"- `{sync_unit_skill_cd_path.name}`：`SM_SyncUnit / SM_ReplaceSkill / SM_ChangeGroup` 的技能组、SkillInfoVO 和 CD 对齐落点",
                f"- `{sync_unit_state_path.name}`：`SM_SyncUnit` 状态分支、可见 HP/MP/影子 HP 写入以及 `RoleMgr:ReviveInfo` 源码缺口索引",
                f"- `{role_attribute_sync_path.name}`：`ChangedAttrsVo / SM_ChangedPlayerAttribute / SM_FightScore` 到实体属性和战力的写入落点",
                f"- `{attribute_definitions_path.name}`：`Attribute.Attribute` 属性 code、Lua symbol、中文名、显示分组和 showTips 规则",
                f"- `{gongfa_attr_change_path.name}`：`SM_GongFaLearn / SM_GongFaUpgrade / SM_GongFaUpgradeTimes` 到 `ChangedAttrsVo` 属性应用的链路",
                f"- `{gongfa_state_path.name}`：`Gongfa_Gongfa` 静态图鉴、`SM_GongFaView` 页状态和 `GongFaItemVO` 已学习状态覆盖链路",
                f"- `{gongfa_attr_display_path.name}`：`Attribute_Attribute`、功法当前/下一阶 attr map 和 UI 属性格式化链路",
                f"- `{gongfa_rich_text_path.name}`：功法详情、属性条目和仙术详情的语言表模板、颜色标签和文本组件渲染链路",
                f"- `{gongfa_localization_templates_path.name}`：功法富文本链路引用到的 `LuaLocalization` 模板原文、颜色和占位符统计",
                f"- `{gongfa_description_composition_path.name}`：`GetMainDes`、通玄描述和仙术详情列表的文案拼装规则",
                f"- `{fight_effect_flags_path.name}`：`SkillDefine.FightCastEffect` 位标志、HurtData 飘字分支和使用位置",
                f"- `{hurt_tips_types_path.name}`：`SkillDefine.HurtTipsType` 飘字类型枚举",
                f"- `{fight_config_values_path.name}`：按 `Fight` 命名空间解析出的 `ConfigValue` 全量键值",
                f"- `{hurt_tips_config_path.name}`：`HurtTipsMgr` 使用的飘字聚合配置和运行计时解释",
                f"- `{blood_type_ui_path.name}`：`BloodType` 到 `PanelBloodTips` prefab 子节点和 `BloodTipItem` 动画名的映射",
                f"- `{hurt_data_blood_sources_path.name}`：`HurtData / HurtTipsMgr` 中回包字段、聚合类型和 BloodType 飘字分支的来源索引",
                f"- `{skill_packets_path.name}`：`player.skill` 装备相关协议字段签名",
                f"- `{skill_mgr_refs_path.name}`：当前可读 Lua 中的 `SkillMgr / SkillData` 调用点",
                f"- `{projected_skills_path.name}`：`LingjieGongfaStar.skill` 到 `Skill.id` 的静态投影关联",
                f"- `{skill_next_hops_path.name}`：投影技能继续到 `SkillExParams.channel`、`LingjieGongfaJie.feature`、`MainFeaturePin.feature` 的下一跳关联",
                f"- `{projected_skill_damage_profiles_path.name}`：投影技能按职业分支聚合的 timeline、channel、hit_frame 和伤害段画像",
                f"- `{projected_skill_damage_families_path.name}`：按命中时序、伤害段、范围和目标参数聚合的投影技能伤害模式 family",
                f"- `{timeline_details_path.name}`：投影技能 timeline 的关键帧、伤害事件、轨道摘要和 playable 资源路径",
                f"- `{timeline_clips_path.name}`：timeline attack/suffer 轨道逐 clip 展开的事件、帧范围、特效、动作、音效和命中参数",
                f"- `{timeline_clip_types_path.name}`：timeline ClipType 角色、轨道名、args 键集合和样例值摘要",
                f"- `{timeline_hit_frames_path.name}`：timeline hit_frame 伤害帧、伤害比例、范围参数及其与 `q_hurt_events` 的对齐关系",
                f"- `{timeline_channel_alignment_path.name}`：`SkillExParams.channel` 周期参数与 timeline `q_hurt_events` 时间序列的对齐关系",
                f"- `{effect_assets_path.name}`：timeline 轨道特效 `res_Name` 到本地 `effect/**.bytes` 资源文件的映射",
                f"- `{effect_bundle_objects_path.name}`：timeline 特效 bundle 内部 Unity 对象类型和名称摘要",
                f"- `{playable_bundle_objects_path.name}`：timeline playable bundle 内部 Unity 对象类型和名称摘要",
                f"- `{timeline_sound_refs_path.name}`：timeline `Sound_Id` 到 `Sound.Sound` 配置和 Wwise bank 的映射",
                f"- `{apk_symbol_hits_path.name}`：APK 二进制侧 `SkillMgr / ReplaceSkill / GongFaHomeMakeVO` 等符号命中",
            ]
        ),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(
            {
                "source_dir": str(resolved_source_dir),
                "packet_index_dir": str(resolved_packet_index_dir),
                "stats": stats,
                "config_refs": config_ref_rows,
                "function_refs": function_summary_rows,
                "packets": runtime_packet_rows,
                "vo_fields": vo_field_rows,
                "vo_usages": vo_usage_rows,
                "net_functions": net_function_rows,
                "net_call_sites": net_call_rows,
                "battle_integration_refs": integration_ref_rows,
                "equip_packets": equip_packet_rows,
                "equip_flow_refs": equip_flow_rows,
                "state_update_refs": state_update_rows,
                "skill_core_flow_refs": skill_core_flow_rows,
                "battle_damage_flow_refs": battle_damage_flow_rows,
                "skill_packets": skill_packet_rows,
                "fight_result_schema": fight_result_schema_rows,
                "fight_result_to_hurt_data": fight_result_to_hurt_data_rows,
                "fight_result_boundary": fight_result_boundary_rows,
                "hp_update_side_paths": hp_update_side_path_rows,
                "fight_state_sync_paths": fight_state_sync_rows,
                "fight_request_intents": fight_request_intent_rows,
                "fight_cast_broadcast_flow": fight_cast_broadcast_flow_rows,
                "skill_instance_lifecycle": skill_instance_lifecycle_rows,
                "fight_authority_boundaries": fight_authority_boundary_rows,
                "fight_side_channels": fight_side_channel_rows,
                "fight_status_codes": fight_status_code_rows,
                "sync_unit_skill_cd": sync_unit_skill_cd_rows,
                "sync_unit_state": sync_unit_state_rows,
                "role_attribute_sync": role_attribute_sync_rows,
                "attribute_definitions": attribute_definition_rows,
                "gongfa_attr_change": gongfa_attr_change_rows,
                "gongfa_state": gongfa_state_rows,
                "gongfa_attr_display": gongfa_attr_display_rows,
                "gongfa_rich_text": gongfa_rich_text_rows,
                "gongfa_localization_templates": gongfa_localization_template_rows,
                "gongfa_description_composition": gongfa_description_composition_rows,
                "fight_effect_flags": fight_effect_flag_rows,
                "hurt_tips_types": hurt_tips_type_rows,
                "fight_config_values": fight_config_value_rows,
                "hurt_tips_config": hurt_tips_config_rows,
                "blood_type_ui": blood_type_ui_rows,
                "hurt_data_blood_sources": hurt_data_blood_source_rows,
                "skill_mgr_refs": skill_mgr_ref_rows,
                "projected_skills": projected_skill_rows,
                "skill_next_hops": skill_next_hop_rows,
                "projected_skill_damage_profiles": projected_skill_damage_profile_rows,
                "projected_skill_damage_families": projected_skill_damage_family_rows,
                "timeline_details": timeline_detail_rows,
                "timeline_clips": timeline_clip_rows,
                "timeline_clip_type_summary": timeline_clip_type_summary_rows,
                "timeline_hit_frames": timeline_hit_frame_rows,
                "timeline_channel_alignment": timeline_channel_alignment_rows,
                "timeline_effect_assets": effect_asset_rows,
                "timeline_effect_bundle_objects": effect_bundle_object_rows,
                "timeline_playable_bundle_objects": playable_bundle_object_rows,
                "timeline_sound_refs": timeline_sound_ref_rows,
                "apk_symbol_hits": apk_symbol_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "output_dir": str(out_dir),
        "source_dir": str(resolved_source_dir),
        "packet_index_dir": str(resolved_packet_index_dir),
        "stats": stats,
        "files": {
            "index_json": str(json_path),
            "config_refs_tsv": str(config_refs_path),
            "function_refs_tsv": str(function_refs_path),
            "packets_tsv": str(packets_path),
            "vo_fields_tsv": str(vo_fields_path),
            "vo_usage_tsv": str(vo_usage_path),
            "net_functions_tsv": str(net_functions_path),
            "net_call_sites_tsv": str(net_call_sites_path),
            "battle_refs_tsv": str(battle_refs_path),
            "equip_packets_tsv": str(equip_packets_path),
            "equip_flow_tsv": str(equip_flow_path),
            "state_updates_tsv": str(state_updates_path),
            "skill_core_flow_tsv": str(skill_core_flow_path),
            "battle_damage_flow_tsv": str(battle_damage_flow_path),
            "skill_packets_tsv": str(skill_packets_path),
            "fight_result_schema_tsv": str(fight_result_schema_path),
            "fight_result_to_hurt_data_tsv": str(fight_result_to_hurt_data_path),
            "fight_result_boundary_tsv": str(fight_result_boundary_path),
            "hp_update_side_paths_tsv": str(hp_update_side_paths_path),
            "fight_state_sync_paths_tsv": str(fight_state_sync_paths_path),
            "fight_request_intents_tsv": str(fight_request_intents_path),
            "fight_cast_broadcast_flow_tsv": str(fight_cast_broadcast_flow_path),
            "skill_instance_lifecycle_tsv": str(skill_instance_lifecycle_path),
            "fight_authority_boundaries_tsv": str(fight_authority_boundary_path),
            "fight_side_channels_tsv": str(fight_side_channel_path),
            "fight_status_codes_tsv": str(fight_status_codes_path),
            "sync_unit_skill_cd_tsv": str(sync_unit_skill_cd_path),
            "sync_unit_state_tsv": str(sync_unit_state_path),
            "role_attribute_sync_tsv": str(role_attribute_sync_path),
            "attribute_definitions_tsv": str(attribute_definitions_path),
            "gongfa_attr_change_tsv": str(gongfa_attr_change_path),
            "gongfa_state_tsv": str(gongfa_state_path),
            "gongfa_attr_display_tsv": str(gongfa_attr_display_path),
            "gongfa_rich_text_tsv": str(gongfa_rich_text_path),
            "gongfa_localization_templates_tsv": str(gongfa_localization_templates_path),
            "gongfa_description_composition_tsv": str(gongfa_description_composition_path),
            "fight_effect_flags_tsv": str(fight_effect_flags_path),
            "hurt_tips_types_tsv": str(hurt_tips_types_path),
            "fight_config_values_tsv": str(fight_config_values_path),
            "hurt_tips_config_tsv": str(hurt_tips_config_path),
            "blood_type_ui_tsv": str(blood_type_ui_path),
            "hurt_data_blood_sources_tsv": str(hurt_data_blood_sources_path),
            "skill_mgr_refs_tsv": str(skill_mgr_refs_path),
            "projected_skills_tsv": str(projected_skills_path),
            "skill_next_hops_tsv": str(skill_next_hops_path),
            "projected_skill_damage_profiles_tsv": str(projected_skill_damage_profiles_path),
            "projected_skill_damage_families_tsv": str(projected_skill_damage_families_path),
            "timeline_details_tsv": str(timeline_details_path),
            "timeline_clips_tsv": str(timeline_clips_path),
            "timeline_clip_types_tsv": str(timeline_clip_types_path),
            "timeline_hit_frames_tsv": str(timeline_hit_frames_path),
            "timeline_channel_alignment_tsv": str(timeline_channel_alignment_path),
            "effect_assets_tsv": str(effect_assets_path),
            "effect_bundle_objects_tsv": str(effect_bundle_objects_path),
            "playable_bundle_objects_tsv": str(playable_bundle_objects_path),
            "timeline_sound_refs_tsv": str(timeline_sound_refs_path),
            "apk_symbol_hits_tsv": str(apk_symbol_hits_path),
            "report": str(report_path),
        },
    }
