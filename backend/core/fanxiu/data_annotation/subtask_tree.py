"""Read-only aggregate task projections from persisted facts and completion receipts.

Domain planners remain the authority for eligibility and timing. This tree is a
view, never a second Scheduler: every leaf belongs to one top-level Job. Opening
it cannot start a Kernel, read the game, repair data or change next_time.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlmodel import Session

from .subtask_execution import subtask_node_id

AGGREGATE_TASK_IDS = frozenset({"ranking-lifecycle", "resource-ranking", "theme-collection", "resource-auto-use"})
TERMINAL_STATES = {"completed", "retained", "unavailable"}


class SubtaskNode(BaseModel):
    id: str
    label: str
    kind: Literal["group", "instance", "subtask"]
    task_id: str
    status: str = "pending"
    message: str = ""
    instance_key: str = ""
    cycle_key: str = ""
    stage_id: str = ""
    due_at: str | None = None
    deadline_at: str | None = None
    retry_at: str | None = None
    completed_at: str | None = None
    supported: bool = True
    counts: dict[str, int] = Field(default_factory=dict)
    children: list[SubtaskNode] = Field(default_factory=list)


class SubtaskTreeResponse(BaseModel):
    ok: bool = True
    task_id: str
    business_date: str
    captured_at: str
    fact_captured_at: str = ""
    message: str = ""
    nodes: list[SubtaskNode] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    current_node_id: str | None = None


def publish_theme_subtask_plan(plan: dict[str, Any]) -> None:
    """Explicit fact publication by a business writer, separate from plan reads.

An incomplete observation never erases the latest authoritative occurrences.
"""
    if not plan.get("plan_ready"):
        return
    from .kernel_scheduler_control import record_world_discovery
    record_world_discovery("theme_subtask_plan", {
        "captured_at": plan.get("captured_at", ""),
        "occurrences": plan.get("occurrences", []),
        "diagnostics": plan.get("diagnostics", []),
    })


def stage_label(kind: str) -> str:
    """Presentation names for domain identities; timing remains in domain rules."""
    labels = {
        "daily_reconcile": "日常对账", "exchange_tail_0030": "收尾兑换",
        "resource_free_gift_0510": "每日免费礼包", "lingzhuang_tier12_0515": "灵装强化",
        "lingzhuang_resource_use_once": "资源使用",
        "dandao_rewards_1810": "丹道任务奖励", "dandao_resource_use_0500": "丹道资源使用",
        "dandao_take_medicine_once": "丹道服药", "yuanding_gift_0500": "缘定三生礼包",
        "magic_initialization_0030": "魔道初始化", "magic_active_1900": "魔道资源与奖励",
        "magic_formal_1905": "魔道正式运行", "magic_mail_1200": "魔道邮件",
        "xutian_active_1000": "虚天殿挑战", "xutian_open_collection_1005": "虚天殿采集",
        "xianmeng_active_1000": "仙盟争霸挑战", "tiandi_yiju_active_1005": "天地弈局正式运行",
        "yunmeng_active_1005": "云梦采集", "yunmeng_challenge_1010": "云梦挑战",
        "yunmeng_challenge_2045": "云梦晚间挑战", "shengxian_peak_final_2330": "升仙会巅峰决赛",
        "zero_purchase_daily_0000": "零元购每日处理", "national_celebration_daily_0000": "庆典每日处理",
        "holy_wood_prayer_0000": "圣木祈愿", "holy_wood_prayer_2100": "圣木尾日祈愿",
        "garden_banquet_2100": "仙园游宴收尾", "visit_xianzun_2200": "仙缘送礼",
        "xianzang_config_0005": "仙藏配置", "xianzang_lottery_2110": "仙藏尾日抽奖",
        "kunlun_config_0005": "昆仑配置", "kunlun_lottery_2110": "昆仑尾日抽奖",
        "lingxiao_start_0005": "凌霄开场", "lingxiao_tail_2110": "凌霄收尾",
        "wanbao_start_0005": "万宝开场", "wanbao_tail_2110": "万宝收尾",
        "beast_abyss_registration_0500": "兽渊报名", "beast_abyss_initialization_1000": "兽渊初始化",
        "beast_abyss_formal_1005": "兽渊正式挑战", "beast_abyss_auto_clear_2045": "兽渊自动清理",
        "beast_abyss_manual_clear_2150": "兽渊手动清理",
    }
    if kind.startswith("yuanding_resource_"):
        return "缘定三生资源运行 " + kind[-4:-2] + ":" + kind[-2:]
    if kind.startswith("garden_banquet_"):
        return "仙园游宴 " + kind[-4:-2] + ":" + kind[-2:]
    return labels.get(kind, kind)


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai")) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def _planned_status(due_at: str, now: datetime) -> str:
    due = _date(due_at)
    return "scheduled" if due and due > now else "due"


def _summarize(node: SubtaskNode) -> Counter:
    if node.kind == "subtask":
        return Counter({node.status: 1})
    counts = sum((_summarize(child) for child in node.children), Counter())
    node.counts = dict(counts)
    fallback = (node.status if node.status != "pending" else "empty") if not counts else "completed" if set(counts) <= {"completed", "retained", "not_applicable"} else "settled"
    node.status = next((s for s in ("running", "error", "blocked", "retry_wait", "due", "pending", "scheduled", "pending_validation") if counts[s]), fallback)
    return counts


def _project_ranking_subtasks(task, now, result, schedule, ranking_rows):
    task_id = task["id"]
    day = now.date().isoformat()
    from backend.core.fanxiu.activity.ranking_lifecycle import discover_ranking_occurrences, checkpoints_for_occurrence, ranking_checkpoint_is_production, due_ranking_checkpoints
    from backend.core.fanxiu.activity.exchange_activity_registry import get_exchange_activity_spec
    family = "gameplay_rank" if task_id == "ranking-lifecycle" else "resource_rank"
    occurrences = discover_ranking_occurrences({"items": [r.get("raw", {}) for r in schedule.get("occurrences", [])]}, family=family)
    occurrences = tuple(o for o in occurrences if o.prepare_at.date() <= now.date() <= o.close_at.date())
    records = {(r["instance_key"], r["checkpoint_kind"], r["business_date"]): r for r in ranking_rows if r.get("family") == family}
    completed = {k for k, r in records.items() if r["status"] in TERMINAL_STATES}
    runnable_keys = {c.key for c in due_ranking_checkpoints(occurrences, now=now, completed_keys=completed, production_only=True)}
    for occurrence in occurrences:
        spec = get_exchange_activity_spec(occurrence.activity_type)
        parent = SubtaskNode(id=subtask_node_id(task_id, occurrence.instance_key), label=spec.label,
                             kind="instance", task_id=task_id, instance_key=occurrence.instance_key,
                                 message=f"{occurrence.cross_count}服 · {occurrence.start_at:%m-%d %H:%M} — {occurrence.close_at:%m-%d %H:%M}")
        candidates = {c.key: c.as_dict() for c in checkpoints_for_occurrence(occurrence, business_day=now.date())}
        # Show one-off closing obligations even when their final day is in
        # the future, without expanding every daily repetition in the period.
        for final_day in {occurrence.end_at.date(), occurrence.close_at.date()}:
            if final_day <= now.date():
                continue
            for c in checkpoints_for_occurrence(occurrence, business_day=final_day):
                if c.checkpoint_kind in {"exchange_tail_0030", "shengxian_peak_final_2330"}:
                    candidates.setdefault(c.key, c.as_dict())
        for key, row in records.items():
            if key[0] == occurrence.instance_key and key[-1] == day:
                candidates.setdefault(key, row)
        for key, c in sorted(candidates.items(), key=lambda item: (item[1]["due_at"], item[0])):
            from backend.core.fanxiu.activity.ranking_lifecycle import RankingCheckpoint
            checkpoint = RankingCheckpoint(instance_key=key[0], activity_type=occurrence.activity_type, family=family,
                runtime_id=occurrence.runtime_id, activity_id=occurrence.activity_id,
                checkpoint_kind=key[1], business_date=key[2], due_at=_date(c["due_at"]))
            supported = ranking_checkpoint_is_production(checkpoint)
            row = records.get(key, {})
            status = row.get("status") or _planned_status(c["due_at"], now)
            if not supported:
                status = "pending_validation"
            elif not row and status == "due" and key not in runnable_keys:
                status = "expired"
            elif status in {"pending", "blocked"} and (retry := _date(row.get("retry_at"))) and retry > now:
                status = "retry_wait" if status == "pending" else "blocked"
            parent.children.append(SubtaskNode(id=subtask_node_id(task_id, *key), label=stage_label(key[1]), kind="subtask",
                task_id=task_id, instance_key=key[0], stage_id=key[1], cycle_key=key[2], due_at=c["due_at"],
                status=status, supported=supported, retry_at=row.get("retry_at") or None,
                completed_at=row.get("completed_at") or None, message=row.get("message", "")))
        result.nodes.append(parent)
    result.fact_captured_at = schedule.get("captured_at", "")


def _project_theme_subtasks(task, now, result, theme_plan, theme_rows):
    task_id = task["id"]
    day = now.date().isoformat()
    from backend.core.fanxiu.activity.theme_collection import THEME_COLLECTION_MEMBERS, ThemeOccurrence, checkpoints_for_occurrence, active_theme_checkpoints, due_theme_checkpoints, PRODUCTION_THEME_STAGE_KINDS
    specs = {s.member_id: s for s in THEME_COLLECTION_MEMBERS}
    records = {(r["instance_key"], r["member_id"], r["stage_kind"], r["business_date"]): r for r in theme_rows}
    seen = set()
    for raw in theme_plan.get("occurrences", []):
        if raw["member_id"] not in specs:
            continue
        occurrence = ThemeOccurrence(member_id=raw["member_id"], activity_id=raw["activity_id"], name=raw["name"],
            instance_key=raw["instance_key"], start_at=_date(raw["start_at"]), end_at=_date(raw["end_at"]), close_at=_date(raw.get("close_at") or raw["end_at"]))
        if not (occurrence.start_at.date() <= now.date() <= occurrence.close_at.date()):
            continue
        spec = specs[occurrence.member_id]
        active_keys = {c.key for c in active_theme_checkpoints((occurrence,), now=now)}
        due_keys = {c.key for c in due_theme_checkpoints((occurrence,), now=now)}
        parent = SubtaskNode(id=subtask_node_id(task_id, occurrence.instance_key), label=occurrence.name,
            kind="instance", task_id=task_id, instance_key=occurrence.instance_key,
            message=f"{occurrence.start_at:%m-%d %H:%M} — {occurrence.end_at:%m-%d %H:%M}")
        if not spec.stage_rules:
            parent.status = "pending_validation"
            parent.supported = False
            parent.message += "；尚未声明可执行阶段"
        for c in checkpoints_for_occurrence(occurrence, spec, production_kinds={r.kind for r in spec.stage_rules}):
            # Today's rounds plus future one-off/tail stages; daily future repetitions are omitted.
            rule = next(r for r in spec.stage_rules if r.kind == c.kind)
            if c.business_date < day or (c.business_date > day and rule.day_scope == "daily"):
                continue
            seen.add(c.key)
            row = records.get(c.key, {})
            supported = c.kind in PRODUCTION_THEME_STAGE_KINDS
            status = row.get("status") or _planned_status(c.due_at.isoformat(), now)
            if not supported:
                status = "pending_validation"
            elif not row and status == "due" and c.key not in active_keys:
                status = "expired"
            elif not row and status == "due" and c.recurrence_group and c.key not in due_keys:
                status = "superseded"
            parent.children.append(SubtaskNode(id=subtask_node_id(task_id, *c.key), label=stage_label(c.kind), kind="subtask",
                task_id=task_id, instance_key=c.instance_key, cycle_key=c.business_date, stage_id=c.kind,
                due_at=c.due_at.isoformat(timespec="seconds"), deadline_at=c.deadline_at.isoformat() if c.deadline_at else None,
                status=status, supported=supported, completed_at=row.get("completed_at"), message=row.get("message", "")))
        result.nodes.append(parent)
    # Historical completion evidence remains visible before the first plan publication.
    for key, row in records.items():
        if key in seen or key[-1] != day:
            continue
        parent = next((n for n in result.nodes if n.instance_key == key[0]), None)
        if parent is None:
            spec = specs.get(key[1])
            parent = SubtaskNode(id=subtask_node_id(task_id, key[0]), label=sorted(spec.names)[0] if spec else key[1],
                kind="instance", task_id=task_id, instance_key=key[0], message="完成凭证；活动计划尚未同步")
            result.nodes.append(parent)
        parent.children.append(SubtaskNode(id=subtask_node_id(task_id, *key), label=stage_label(key[2]), kind="subtask",
            task_id=task_id, instance_key=key[0], cycle_key=key[3], stage_id=key[2], status=row["status"], completed_at=row["completed_at"]))
    result.fact_captured_at = theme_plan.get("captured_at", "")


def _project_resource_subtasks(task, now, result):
    task_id = task["id"]
    day = now.date().isoformat()
    from .tasks.resource_daily_plan import iter_resource_component_plan
    groups = {key: SubtaskNode(id=subtask_node_id(task_id, key), label=label, kind="group", task_id=task_id)
              for key, label in (("prepare", "准备资源"), ("claim", "领取资源"), ("exchange", "兑换资源"), ("use", "使用资源"), ("cultivate", "培养"))}
    progress = (task.get("payload") or {}).get("aggregate_progress") or {}
    observation = (task.get("payload") or {}).get("subtask_execution") or {}
    owned = task.get("attempt_id") and observation.get("attempt_id") == task["attempt_id"]
    moment = now
    if task.get("last_result") == "running":
        moment = (_date(observation.get("business_time")) if owned else None) or _date(task.get("started_at")) or now
    moment = moment or now
    result.business_date = moment.date().isoformat()
    for c in iter_resource_component_plan(moment):
        row = progress.get(c["cycle"], {}).get(c["stage_id"], {})
        status = "not_applicable" if not c["eligible"] else "pending"
        if c["eligible"] and row.get("stage_version") == c["version"]:
            status = {"complete": "completed", "failed": "error"}.get(row.get("status"), status)
        groups[c["group"]].children.append(SubtaskNode(id=subtask_node_id(task_id, c["cycle"], c["stage_id"]),
            label=c["label"], kind="subtask", task_id=task_id, stage_id=c["stage_id"], cycle_key=c["cycle"],
            due_at=datetime.combine(moment.date(), time.min, moment.tzinfo).isoformat(), status=status,
            completed_at=row.get("updated_at") if status == "completed" else None,
            message=str((row.get("result") or {}).get("error", "")) if isinstance(row.get("result"), dict) else ""))
    result.nodes = list(groups.values())


def project_subtask_tree(task: dict[str, Any], *, now: datetime,
                         schedule: dict[str, Any], theme_plan: dict[str, Any],
                         ranking_rows: list[dict[str, Any]], theme_rows: list[dict[str, Any]]) -> SubtaskTreeResponse:
    """Pure projection. Passed snapshots are never modified; identities isolate cycles."""
    task_id = task["id"]
    day = now.date().isoformat()
    result = SubtaskTreeResponse(task_id=task_id, business_date=day, captured_at=now.isoformat(timespec="seconds"))
    if task_id in {"ranking-lifecycle", "resource-ranking"}:
        _project_ranking_subtasks(task, now, result, schedule, ranking_rows)
    elif task_id == "theme-collection":
        _project_theme_subtasks(task, now, result, theme_plan, theme_rows)
    elif task_id == "resource-auto-use":
        _project_resource_subtasks(task, now, result)
    else:
        raise ValueError("该作业不是聚合作业")
    observation = (task.get("payload") or {}).get("subtask_execution") or {}
    live = bool(task.get("attempt_id") and task.get("last_result") == "running"
                and observation.get("attempt_id") == task["attempt_id"] and observation.get("status") == "running")
    def apply_current(nodes):
        for node in nodes:
            if live and node.id == observation.get("node_id"):
                node.status = "running"
                result.current_node_id = node.id
            apply_current(node.children)
    apply_current(result.nodes)
    result.counts = dict(sum((_summarize(node) for node in result.nodes), Counter()))
    if not result.nodes:
        result.message = "暂无已同步的活动实例；等待业务作业同步事实"
    return result


def read_subtask_tree(task_id: str, *, now: datetime | None = None) -> SubtaskTreeResponse:
    """Public read-only entry: persisted provider APIs only; no Runtime or maintenance."""
    from .kernel_scheduler_control import read_scheduler_tasks, read_world_facts
    from backend.core.fanxiu.activity.daily_activity_sync import load_worldline_activity_schedule_snapshot
    from backend.core.fanxiu.activity.ranking_lifecycle_store import list_ranking_checkpoint_rows
    from backend.core.fanxiu.activity.theme_collection_store import list_theme_stage_completions
    from backend.db import engine
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    task = next((t for t in read_scheduler_tasks() if t["id"] == task_id), None)
    if task is None:
        raise LookupError("作业不存在")
    with Session(engine) as session:
        ranking_rows = [r.model_dump() for r in list_ranking_checkpoint_rows(session)] if task_id in {"ranking-lifecycle", "resource-ranking"} else []
        theme_rows = list_theme_stage_completions(session, business_date=current.date().isoformat()) if task_id == "theme-collection" else []
    discoveries = read_world_facts().get("discoveries") or {}
    theme_plan = discoveries.get("theme_subtask_plan") or {}
    schedule = load_worldline_activity_schedule_snapshot()
    ranking_plan = discoveries.get(f"ranking_subtask_plan:{task_id}") or {}
    # 作业计划只有非空实例才提供替代日程的证据；空计划不能仅凭时间更新
    # 擦掉持久活动日程。失效实例仍由领域的 close_at 过滤。
    if ranking_plan.get("occurrences") and (_date(ranking_plan.get("captured_at")) or datetime.min.replace(tzinfo=current.tzinfo)) > (_date(schedule.get("captured_at")) or datetime.min.replace(tzinfo=current.tzinfo)):
        schedule = ranking_plan
    return project_subtask_tree(task, now=current, schedule=schedule,
                                theme_plan=theme_plan, ranking_rows=ranking_rows, theme_rows=theme_rows)
