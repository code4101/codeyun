"""资源每日处理的业务分组：顺序和准入在这里，动作实现留在各组件。

新增组件放入相应业务组，复用其 STAGE_ID/STAGE_VERSION 和完成判据。
模块按执行顺序延迟加载，不能因后续组件导入失败阻止前面组件落盘。
"""
from __future__ import annotations

from .resource_daily_contract import tuesday_purchase_cycle
from .resource_daily_execution import ResourceDailyExecution


def prepare_daily_resources(run: ResourceDailyExecution):
    """清理临时提示、找回资源，并完成已有装备的基础更新。"""
    from . import friend_notice
    yield from run.component(friend_notice, friend_notice.dismiss_friend_notice, "好友提示")
    from . import reward_recovery
    yield from run.component(reward_recovery, reward_recovery.recover_rewards, "找回")
    from . import divine_artifact_upgrade
    yield from run.component(divine_artifact_upgrade, divine_artifact_upgrade.upgrade_divine_artifacts, "神器升阶")
    from . import spirit_artifact_prompt_update
    yield from run.component(spirit_artifact_prompt_update, spirit_artifact_prompt_update.update_spirit_artifact_prompts, "洗灵更新")
    from . import sword_spirit_update
    yield from run.component(sword_spirit_update, sword_spirit_update.update_sword_spirit, "剑灵更新")


def exchange_daily_resources(run: ResourceDailyExecution):
    """按各商店的既有业务周期兑换；不改变购买策略和留存额度。"""
    from . import fengmosha_exchange
    cycle = fengmosha_exchange.fengmosha_exchange_cycle(run.moment)
    if cycle is not None:
        yield from run.component(fengmosha_exchange, fengmosha_exchange.exchange_fengmosha_resources,
                                 "秘境封魔杀兑换", cycle=cycle, with_moment=True)
    from . import lingzu_exchange
    cycle = lingzu_exchange.lingzu_exchange_cycle(run.moment)
    if cycle is not None:
        yield from run.component(lingzu_exchange, lingzu_exchange.exchange_lingzu_resources,
                                 "灵祖兑换", cycle=cycle, with_moment=True)
    cycle = tuesday_purchase_cycle(run.moment)
    if cycle is not None:
        from . import dongtian_exchange
        yield from run.component(dongtian_exchange, dongtian_exchange.purchase_dongtian_resources,
                                 "洞天兑换", cycle=cycle)
        from . import daofa_purchase
        yield from run.component(daofa_purchase, daofa_purchase.purchase_daofa_resources,
                                 "道法兑换", cycle=cycle)
        from . import xianyuan_purchase
        yield from run.component(xianyuan_purchase, xianyuan_purchase.purchase_xianyuan_resources,
                                 "仙缘兑换", cycle=cycle)
    from . import sect_exchange
    cycle = sect_exchange.sect_exchange_cycle(run.moment)
    if cycle is not None:
        yield from run.component(sect_exchange, sect_exchange.exchange_sect_resources,
                                 "宗门兑换", cycle=cycle)


def cultivate_daily_skills(run: ResourceDailyExecution):
    """用现有材料升级功法、炼神、天赋、神焰和三皇灵威。"""
    from . import gongfa_cultivation
    yield from run.component(gongfa_cultivation, gongfa_cultivation.upgrade_gongfa_book, "升级功法书")
    from . import lianshen_update
    yield from run.component(lianshen_update, lianshen_update.update_lianshen, "炼神更新")
    from . import role_talent_update
    yield from run.component(role_talent_update, role_talent_update.update_role_talents, "天赋技能树")
    from . import god_flame_update
    yield from run.component(god_flame_update, god_flame_update.update_god_flames, "神焰")
    from . import three_emperors_comprehend
    yield from run.component(three_emperors_comprehend, three_emperors_comprehend.comprehend_three_emperors, "三皇灵威领悟")
    from . import xianfu_science
    yield from run.component(xianfu_science, xianfu_science.develop_xianfu_science, "仙府玄机阁")


def use_daily_resources(run: ResourceDailyExecution):
    """复用已有资源使用流程及其阶段凭证，不重建子作业。"""
    from .resource_auto_use import execute_resource_auto_use_task
    yield from run.aggregate(execute_resource_auto_use_task)
