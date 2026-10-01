"""Declarative composition consumed by both resource execution and its tree.

Eligibility and business cycles are evaluated for the parent's frozen moment.
Modules stay lazy so a later import failure cannot prevent earlier receipts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from importlib import import_module

from .resource_daily_contract import RESOURCE_DAILY_STAGES, resource_daily_cycle_key, tuesday_purchase_cycle


@dataclass(frozen=True)
class ResourceComponent:
    group: str
    module: str
    operation: str
    label: str
    cadence: str = "daily"
    with_moment: bool = False

    def cycle(self, moment: datetime) -> str | None:
        if self.cadence == "tuesday":
            return tuesday_purchase_cycle(moment)
        if self.cadence == "monday":
            return f"week:{moment.date().isoformat()}" if moment.weekday() == 0 else None
        if self.cadence == "domain":
            module = import_module(f"{__package__}.{self.module}")
            return getattr(module, f"{self.module}_cycle")(moment)
        return moment.date().isoformat()

    def identity(self) -> tuple[str, str]:
        module = import_module(f"{__package__}.{self.module}")
        return module.STAGE_ID, getattr(module, "STAGE_VERSION", "1")


RESOURCE_COMPONENTS = (
    ResourceComponent("prepare", "friend_notice", "dismiss_friend_notice", "好友提示"),
    ResourceComponent("prepare", "reward_recovery", "recover_rewards", "找回"),
    ResourceComponent("prepare", "divine_artifact_upgrade", "upgrade_divine_artifacts", "神器升阶"),
    # Full spirit-artifact cultivation requires the whole-hall initial analysis
    # and staggered upgrades. Only the accepted prompt update belongs here;
    # never route a single-component cultivation API around those prerequisites.
    ResourceComponent("prepare", "spirit_artifact_prompt_update", "update_spirit_artifact_prompts", "洗灵更新"),
    ResourceComponent("prepare", "sword_spirit_update", "update_sword_spirit", "剑灵更新"),
    ResourceComponent("claim", "trial_manual", "claim_trial_manual", "领取试炼手册"),
    ResourceComponent("claim", "growth_fund", "claim_growth_fund", "成长基金领取", "monday", True),
    ResourceComponent("exchange", "fengmosha_exchange", "exchange_fengmosha_resources", "秘境封魔杀兑换", "domain", True),
    ResourceComponent("exchange", "lingzu_exchange", "exchange_lingzu_resources", "灵祖兑换", "domain", True),
    ResourceComponent("exchange", "dongtian_exchange", "purchase_dongtian_resources", "洞天兑换", "tuesday"),
    ResourceComponent("exchange", "daofa_purchase", "purchase_daofa_resources", "道法兑换", "tuesday"),
    ResourceComponent("exchange", "xianyuan_purchase", "purchase_xianyuan_resources", "仙缘兑换", "tuesday"),
    ResourceComponent("exchange", "sect_exchange", "exchange_sect_resources", "宗门兑换", "domain"),
    ResourceComponent("use", "talisman_cultivation", "complete_talisman_cultivation", "法宝共鸣与神炼"),
    ResourceComponent("use", "wardrobe_cultivation", "complete_wardrobe", "衣装阁"),
    ResourceComponent("cultivate", "danling_upgrade", "upgrade_danling", "丹灵升级"),
    ResourceComponent("cultivate", "gongfa_cultivation", "upgrade_gongfa_book", "升级功法书"),
    ResourceComponent("cultivate", "lianshen_update", "update_lianshen", "炼神更新"),
    ResourceComponent("cultivate", "role_talent_update", "update_role_talents", "天赋技能树"),
    ResourceComponent("cultivate", "god_flame_update", "update_god_flames", "神焰"),
    ResourceComponent("cultivate", "three_emperors_comprehend", "comprehend_three_emperors", "三皇灵威领悟"),
    ResourceComponent("cultivate", "spirit_root_update", "update_spirit_root", "灵根领悟"),
    ResourceComponent("cultivate", "physical_comprehension", "comprehend_first_physical_book", "炼体感悟"),
    ResourceComponent("cultivate", "god_seal_update", "update_god_seals", "神印升级"),
    ResourceComponent("cultivate", "xianfu_science", "develop_xianfu_science", "仙府玄机阁"),
)


def iter_resource_component_plan(moment: datetime):
    """Yield qualified leaf descriptors in execution order, including ineligible ones."""
    for group in ("prepare", "claim", "exchange", "use", "cultivate"):
        if group == "claim":
            for stage in RESOURCE_DAILY_STAGES:
                cycle = resource_daily_cycle_key(stage, moment)
                eligible = not stage.monday_only or moment.weekday() == 0
                yield {"group": group, "stage_id": stage.task_id, "version": "1", "label": stage.label,
                       "cycle": cycle, "eligible": eligible}
        if group == "use":
            from .resource_auto_use import RESOURCE_AUTO_USE_STAGES
            for stage_id, label in RESOURCE_AUTO_USE_STAGES:
                yield {"group": group, "stage_id": stage_id, "version": "1", "label": label,
                       "cycle": moment.date().isoformat(), "eligible": True}
        for spec in RESOURCE_COMPONENTS:
            if spec.group != group:
                continue
            stage_id, version = spec.identity()
            cycle = spec.cycle(moment)
            yield {"group": group, "stage_id": stage_id, "version": version, "label": spec.label,
                   "cycle": cycle or moment.date().isoformat(), "eligible": cycle is not None}


def execute_resource_group(run, group: str):
    """Use the same declarations as the tree; import each component only when reached."""
    for spec in RESOURCE_COMPONENTS:
        if spec.group != group:
            continue
        cycle = spec.cycle(run.moment)
        if cycle is None:
            continue
        module = import_module(f"{__package__}.{spec.module}")
        yield from run.component(module, getattr(module, spec.operation), spec.label,
                                 cycle=cycle, with_moment=spec.with_moment)
