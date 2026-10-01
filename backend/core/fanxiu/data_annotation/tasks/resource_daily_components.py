"""资源每日处理的业务分组；声明由 resource_daily_plan 统一拥有。"""
from __future__ import annotations

from .resource_daily_plan import execute_resource_group


def prepare_daily_resources(run):
    yield from execute_resource_group(run, "prepare")


def exchange_daily_resources(run):
    yield from execute_resource_group(run, "exchange")


def cultivate_daily_skills(run):
    yield from execute_resource_group(run, "cultivate")


def use_daily_resources(run):
    from .resource_auto_use import execute_resource_auto_use_task
    yield from run.aggregate(execute_resource_auto_use_task)
    yield from execute_resource_group(run, "use")
