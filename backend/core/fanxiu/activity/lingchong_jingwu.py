from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field
from sqlmodel import Session, col, select

from backend.core.fanxiu.activity.exchange_event import (
    is_exchange_activity_active,
    list_exchange_activity_snapshot,
    replace_exchange_rankings,
    upsert_exchange_activity_snapshot,
)
from backend.core.fanxiu.activity.standard_observation import read_activity_rank_fact
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.models import (
    FanxiuExchangeActivity,
    FanxiuPacketBusinessRecord,
)


LINGCHONG_JINGWU_ACTIVITY_TYPE = "lingchong-jingwu"
LINGCHONG_JINGWU_OFFICIAL_NAME = "灵宠竞武"
LINGCHONG_JINGWU_USER_ALIAS = "灵武竞宠"
LINGCHONG_JINGWU_PARENT_ACTIVITY_ID = 8042901
LINGCHONG_JINGWU_PERSONAL_RANK_ID = 42905
LINGCHONG_JINGWU_PLANE_RANK_ID = 42906
TALENT_PILL_ITEM_ID = 9070095
# 默认完整阶梯为14档；单服旧12档只按已取证的整套任务ID接受。
# 缺档或混入历史梯度都必须失败，最高目标始终来自本期已加载任务。
PET_TALENT_LADDER_SIZE = 14
_PET_TALENT_CONDITION = re.compile(r"PetTalent\|(\d+)")


class LingchongJingwuReferences(BaseModel):
    activity_type: Literal["lingchong-jingwu"] = LINGCHONG_JINGWU_ACTIVITY_TYPE
    official_name: str = LINGCHONG_JINGWU_OFFICIAL_NAME
    user_alias: str = LINGCHONG_JINGWU_USER_ALIAS
    parent_activity_id: int
    cross_count: int
    personal_rank_activity_id: int
    plane_rank_activity_id: int


class LingchongJingwuTaskMilestone(BaseModel):
    task_id: int
    order: int
    name: str
    target: int
    progress: int
    status: int
    finished: bool
    talent_pill_count: int
    rewards: list[str] = Field(default_factory=list)


class LingchongJingwuResourceItem(BaseModel):
    item_id: int
    name: str
    quality: int
    count: int
    sort_order: int = 0
    # Historical storage/API key: keys are PetGift giftId, never pet types.
    aptitude_gain_by_pet_type: dict[int, int] = Field(default_factory=dict)
    minimum_aptitude_gain: int = Field(description="静态各资质分项效果的最小值，不是实际使用增量下界")
    maximum_aptitude_gain: int = Field(description="静态各资质分项效果的最大值，不是实际使用增量上界")

    @computed_field
    @property
    def aptitude_gain_by_gift_id(self) -> dict[int, int]:
        """Item.effectValue 分项效果；实际增量仍需校验目标灵兽资质容量。

        由旧字段派生，已有持久快照无需迁移，同时保留旧 API 字段。
        """
        return dict(self.aptitude_gain_by_pet_type)


class LingchongJingwuResourceSnapshot(BaseModel):
    activity_id: str
    captured_at: str
    source_kind: str = "readonly_backpack_runtime"
    complete: bool = True
    items: list[LingchongJingwuResourceItem]
    total_count: int
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


def _load_config_rows(root: Path, table: str) -> list[dict[str, Any]]:
    path = root / "parsed_configs" / table / "rows.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"无法读取凡修 {table} 配置") from exc
    rows: Any = payload if isinstance(payload, list) else payload.get("rows", payload)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise ValueError(f"凡修 {table} 配置格式无效")
    return [dict(row) for row in rows if isinstance(row, dict)]


def resolve_lingchong_jingwu_references(
    *,
    parent_activity_id: int = LINGCHONG_JINGWU_PARENT_ACTIVITY_ID,
    export_root: str | Path | None = None,
) -> LingchongJingwuReferences:
    """Resolve the 8-server event from the game's Activity graph.

    The public alias is intentionally not used as a config identity.  The
    authoritative current client and packet facts call the activity
    ``灵宠竞武``; ``灵武竞宠`` remains a user-facing search alias.
    """

    root = resolve_fanxiu_export_root(export_root)
    rows = _load_config_rows(root, "Activity")
    rows_by_id = {int(row.get("id") or 0): row for row in rows}
    parent = rows_by_id.get(int(parent_activity_id))
    if parent is None:
        raise ValueError(f"灵宠竞武活动配置不存在：{int(parent_activity_id)}")
    official_name = str(parent.get("name_plain") or parent.get("name") or "")
    if official_name != LINGCHONG_JINGWU_OFFICIAL_NAME:
        raise ValueError(
            f"活动 {int(parent_activity_id)} 身份不符：期望{LINGCHONG_JINGWU_OFFICIAL_NAME}，实际{official_name or '空'}"
        )
    cross_count = int(parent.get("crossGroup") or 0)
    if cross_count != 8:
        raise ValueError(f"灵宠竞武活动不是 8 跨：{cross_count}")
    follow = [int(value) for value in (parent.get("follow") or [])]
    if len(follow) != 2:
        raise ValueError("8跨灵宠竞武配置缺少个人榜/位面榜引用")

    personal_id: int | None = None
    plane_id: int | None = None
    for rank_id in follow:
        rank = rows_by_id.get(rank_id)
        if rank is None:
            raise ValueError(f"8跨灵宠竞武引用了不存在的榜单：{rank_id}")
        base_id = int(rank.get("baseId") or 0)
        if base_id == 42901:
            personal_id = rank_id
        elif base_id == 42902:
            plane_id = rank_id
    if personal_id is None or plane_id is None:
        raise ValueError("8跨灵宠竞武的个人榜/位面榜身份不完整")
    return LingchongJingwuReferences(
        parent_activity_id=int(parent_activity_id),
        cross_count=cross_count,
        personal_rank_activity_id=personal_id,
        plane_rank_activity_id=plane_id,
    )


def build_lingchong_jingwu_activity_payload(
    worldline: dict[str, Any],
    *,
    references: LingchongJingwuReferences,
    captured_at: str,
) -> dict[str, Any]:
    """Build the generic activity-store payload from one exact worldline fact."""

    if int(worldline.get("activityId") or 0) != references.parent_activity_id:
        raise ValueError("世界线事实不是当前 8跨灵宠竞武父活动")
    if str(worldline.get("name") or "") != references.official_name:
        raise ValueError("世界线活动名称与静态配置不一致")
    if int(worldline.get("serverCount") or 0) != references.cross_count:
        raise ValueError("世界线活动跨数与静态配置不一致")

    def event_date(key: str) -> str:
        timestamp = int(worldline.get(key) or 0)
        if timestamp <= 0:
            raise ValueError("8跨灵宠竞武世界线时间事实不完整")
        return datetime.fromtimestamp(timestamp / 1000).astimezone().date().isoformat()

    return {
        "activity_type": references.activity_type,
        "game_activity_id": references.parent_activity_id,
        "cross_count": references.cross_count,
        "start_date": event_date("startTime"),
        "end_date": event_date("endTime"),
        "game_rank_activity_id": references.personal_rank_activity_id,
        "currency_name": "灵兽资质积分",
        "captured_at": str(captured_at),
        "source_kind": "standard_runtime_facts",
        "resource_strategy": {
            "resource_metric": "饲灵丸库存与各资质分项效果",
            "task_metric": "本期已下发 PetTalent 任务进度",
        },
        "evidence": {
            "official_name": references.official_name,
            "user_alias": references.user_alias,
            "parent_activity_id": references.parent_activity_id,
            "rank_scope_identities": {
                "personal": {
                    "runtime_rank_activity_id": references.personal_rank_activity_id,
                    "reward_activity_id": references.personal_rank_activity_id,
                },
                "plane": {
                    "runtime_rank_activity_id": references.plane_rank_activity_id,
                    "reward_activity_id": references.plane_rank_activity_id,
                },
            },
            "worldline": dict(worldline),
        },
    }


def _reward_item_count(rewards: list[str], item_id: int) -> int:
    total = 0
    for reward in rewards:
        match = re.fullmatch(r"Item\|(\d+)_(-?\d+)(?:_.*)?", reward)
        if match is not None and int(match.group(1)) == int(item_id):
            total += int(match.group(2))
    return total


def _pet_talent_target(config: dict[str, Any]) -> int:
    for condition in config.get("finishCondition") or []:
        match = _PET_TALENT_CONDITION.fullmatch(str(condition or ""))
        if match is not None:
            return int(match.group(1))
    return 0


def _observed_pet_talent_entries(
    task_entries: list[dict[str, Any]],
    *,
    config_by_id: dict[int, dict[str, Any]],
    finished_task_ids: list[int],
) -> list[dict[str, Any]]:
    """Normalize the Runtime-observed ladder for this exact instance.

    Only tasks that exist in this activity's static PetTalent config are
    accepted. A claimed task may be represented only in ``finishTasks``; its
    progress is reconstructed from the static target instead of being assumed
    to be 0. Unobserved static IDs are never treated as present or zero.
    """
    observed: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in task_entries:
        task_id = int(raw.get("taskId") or raw.get("task_id") or 0)
        if task_id <= 0 or task_id in seen:
            raise ValueError(f"本期资质任务 ID 缺失或重复：{task_id}")
        if task_id not in config_by_id:
            raise ValueError(f"本期资质任务缺少静态配置：{task_id}")
        seen.add(task_id)
        observed.append(dict(raw))
    for finished_id in finished_task_ids:
        task_id = int(finished_id)
        if task_id in seen or task_id not in config_by_id:
            continue
        target = _pet_talent_target(config_by_id[task_id])
        if target <= 0:
            raise ValueError(f"本期资质任务缺少目标条件：{task_id}")
        observed.append({
            "taskId": task_id,
            "status": 5,
            "turn": 0,
            "rewardTime": 0,
            "progressList": [{"progress": target, "target": target, "finish": True}],
        })
        seen.add(task_id)
    return observed


def _validate_pet_talent_ladder(milestones: list[LingchongJingwuTaskMilestone], *, expected_size: int = PET_TALENT_LADDER_SIZE) -> str | None:
    """Require the complete ladder size already bound to its loaded config IDs.

    Observing one tier is never enough: the sort values must be the unique
    1..expected_size sequence and targets must be valid and strictly increasing. A
    missing tier or a mix of two retained ladders fails closed.
    """
    if len(milestones) != expected_size:
        return f"本期资质任务档数不是 {expected_size}：{len(milestones)}"
    sorts = sorted(row.order for row in milestones)
    if sorts != list(range(1, expected_size + 1)):
        return f"本期资质任务 sort 不是 1..{expected_size} 唯一序列：{sorts}"
    targets = [row.target for row in milestones]
    if (any(target <= 0 for target in targets)
            or len(set(targets)) != len(targets)
            or targets != sorted(targets)):
        return f"本期资质任务目标档无效或重复：{targets}"
    return None


def resolve_loaded_pet_talent_ladder_size(configs, observed_task_ids):
    """Accept one complete exported ladder whose IDs are actually loaded.

    The observed single-server config retains both 12- and 14-tier ladders.
    ID-sort binds each contiguous ladder; the verified 16-server replacement
    has an explicit complete ID/order/target signature below. The legacy 12-tier exception is
    bound to its actual exported 104291101..104291112 IDs; another truncated
    export must not redefine a 14-tier event as a complete 12-tier ladder.
    """
    groups = {}
    for row in configs:
        ident, order = int(row['id']), int(row.get('sort') or 0)
        groups.setdefault(ident-order, []).append(row)
    matches = []
    # The 16-server client replaces tiers 1..3 with 1604290165..167,
    # retaining tiers 4..14. QuestMgr on 2026-10-08 loaded this exact set;
    # ID-sort alone cannot represent the replacement. Bind the complete
    # exported ID/order/target signature, never a generic mixed ladder.
    cross_replacement = {
        1604290165: (1, 50), 1604290166: (2, 100),
        1604290167: (3, 200),
        **{1604290150 + order: (order, target) for order, target in
           enumerate((300, 400, 500, 700, 1000, 1500, 2000,
                      3000, 4000, 5000, 6000), start=4)},
    }
    if set(observed_task_ids) == set(cross_replacement):
        selected = {int(row['id']): row for row in configs
                    if int(row['id']) in cross_replacement}
        if (len(selected) == 14 and all(
                (int(selected[ident].get('sort') or 0),
                 _pet_talent_target(selected[ident])) == signature
                for ident, signature in cross_replacement.items())):
            matches.append(14)
    for rows in groups.values():
        if len(rows) not in (12, 14):
            continue
        if len(rows) == 12 and {int(r['id']) for r in rows} != set(range(104291101, 104291113)):
            continue
        if sorted(int(r.get('sort') or 0) for r in rows) != list(range(1, len(rows)+1)):
            continue
        if {int(r['id']) for r in rows} == set(observed_task_ids):
            matches.append(len(rows))
    if len(matches) != 1:
        raise ValueError('已加载资质任务不对应唯一完整的已知12/14档，禁止使用资源')
    return matches[0]


def read_lingchong_task_milestones(
    activity_id: int, *, export_root: str | Path | None = None,
) -> dict[str, Any]:
    """读取本期已加载的资质任务档次，不触发领取。

    The Runtime-loaded task IDs are this instance's ladder. Static ActiveTask
    config retains several historical ladders for the same parent activity, so
    requiring every static ID is a false negative; an unseen ID is never
    assumed to be 0. Claimed tiers may exist only in ``finishTasks`` and are
    reconstructed from their static target. Loaded IDs must match one complete
    exported 12/14-tier ladder before any target is returned.
    """
    from backend.core.fanxiu.instrumentation.daily_task_rewards import (
        TaskRewardDomainSpec, build_activity_task_reward_snapshot,
        read_activity_task_reward_snapshots,
    )

    configs = [r for r in _load_config_rows(resolve_fanxiu_export_root(export_root), "ActiveTask")
               if int(r.get("activityId") or 0) == int(activity_id)
               and any(_PET_TALENT_CONDITION.fullmatch(str(c)) for c in r.get("finishCondition", []))]
    config_by_id = {int(r["id"]): r for r in configs}
    if not config_by_id:
        return {"ok": False, "complete": False, "milestones": [],
                "reason": f"活动 {int(activity_id)} 没有本期资质任务静态配置"}
    shared = read_activity_task_reward_snapshots(domains=(), include_activity_tasks=True)
    if not shared.get("ok"):
        return {"ok": False, "complete": False, "milestones": [],
                "reason": shared.get("reason") or "QuestMgr 活动任务不可读"}
    entries = [dict(r) for r in shared.get("task_entries") or []
               if int(r.get("taskId") or r.get("task_id") or 0) in config_by_id]
    finished = [int(t) for t in shared.get("finished_task_ids") or [] if int(t) in config_by_id]
    try:
        observed = _observed_pet_talent_entries(
            entries, config_by_id=config_by_id, finished_task_ids=finished,
        )
        milestones = load_lingchong_jingwu_task_milestones(
            observed, parent_activity_id=int(activity_id), export_root=export_root,
        )
    except ValueError as exc:
        return {"ok": False, "complete": False, "milestones": [], "reason": str(exc)}
    if not milestones:
        return {"ok": False, "complete": False, "milestones": [],
                "reason": "QuestMgr 尚未加载本期资质任务"}
    try:
        ladder_size = resolve_loaded_pet_talent_ladder_size(configs, [row.task_id for row in milestones])
    except ValueError as exc:
        return {"ok": False, "complete": False, "milestones": [], "reason": str(exc)}
    ladder_error = _validate_pet_talent_ladder(milestones, expected_size=ladder_size)
    if ladder_error is not None:
        return {"ok": False, "complete": False, "milestones": [], "reason": ladder_error}
    ordered_ids = tuple(m.task_id for m in milestones)
    spec = TaskRewardDomainSpec(
        key=f"lingchong_{int(activity_id)}", label="灵兽资质任务",
        activity_id=int(activity_id), task_ids=ordered_ids,
        condition_key="PetTalent", thresholds=tuple(range(1, len(ordered_ids) + 1)),
    )
    rewards = build_activity_task_reward_snapshot(
        spec=spec, task_entries=observed, finished_task_ids=sorted(set(finished)),
    )
    highest = max(milestones, key=lambda row: (row.target, row.order, row.task_id))
    return {"ok": True, "complete": bool(rewards["complete"]),
            "activity_id": int(activity_id), "milestones": [r.model_dump() for r in milestones],
            "finished_task_ids": sorted(set(finished)), "missing_task_ids": [],
            "target": highest.target, "progress": highest.progress,
            "goal_finished": bool(highest.finished), "task_ids": list(ordered_ids),
            "reward_state": rewards, "captured_at": shared.get("captured_at")}


def load_lingchong_jingwu_task_milestones(
    observed_tasks: list[dict[str, Any]],
    *,
    parent_activity_id: int = LINGCHONG_JINGWU_PARENT_ACTIVITY_ID,
    export_root: str | Path | None = None,
) -> list[LingchongJingwuTaskMilestone]:
    """Join this instance's exact QuestEntryVO rows to ActiveTask config.

    ActiveTask retains multiple ladders for the same parent activity.  Picking
    the longest or newest ladder can silently display tasks from another
    server tier.  Therefore the current packet/Runtime task IDs are mandatory.
    """

    if not observed_tasks:
        raise ValueError("灵宠竞武本期任务尚未加载，拒绝从多套静态梯度猜测")
    root = resolve_fanxiu_export_root(export_root)
    rows_by_id = {
        int(row.get("id") or 0): row
        for row in _load_config_rows(root, "ActiveTask")
        if int(row.get("activityId") or 0) == int(parent_activity_id)
    }
    milestones: list[LingchongJingwuTaskMilestone] = []
    seen: set[int] = set()
    for observed in observed_tasks:
        task_id = int(observed.get("taskId") or observed.get("task_id") or 0)
        if task_id <= 0 or task_id in seen:
            raise ValueError("灵宠竞武本期任务 ID 缺失或重复")
        seen.add(task_id)
        config = rows_by_id.get(task_id)
        if config is None:
            raise ValueError(f"灵宠竞武本期任务缺少静态配置：{task_id}")
        target = _pet_talent_target(config)
        progress_rows = observed.get("progressList") or observed.get("progress_list") or []
        if isinstance(progress_rows, dict):
            progress_rows = progress_rows.get("items") or []
        runtime_progress = next(
            (row for row in progress_rows if isinstance(row, dict)),
            {},
        )
        runtime_target = int(runtime_progress.get("target") or 0)
        if target <= 0 or runtime_target != target:
            raise ValueError(f"灵宠竞武任务 {task_id} 的 Runtime/配置目标不一致")
        rewards = [str(value) for value in (config.get("reward") or [])]
        status = int(observed.get("status") or 0)
        progress = int(runtime_progress.get("progress") or 0)
        milestones.append(
            LingchongJingwuTaskMilestone(
                task_id=task_id,
                order=int(config.get("sort") or 0),
                name=str(config.get("name_plain") or config.get("name") or ""),
                target=target,
                progress=progress,
                status=status,
                finished=bool(runtime_progress.get("finish")) or progress >= target,
                talent_pill_count=_reward_item_count(rewards, TALENT_PILL_ITEM_ID),
                rewards=rewards,
            )
        )
    milestones.sort(key=lambda row: (row.target, row.order, row.task_id))
    return milestones


def load_lingchong_jingwu_resource_definitions(
    *,
    export_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """读取饲灵丸的静态资质分项效果，不推算实际使用增量。

    Item.effectValue 的键是资质 giftId（攻击、气血、灵力、御兽、魔兽）。
    GetPetUpItemList 通过 GetGiftLimitByPetIdAndGiftId 过滤适用道具；
    使用前仍需确认目标灵兽对应资质的剩余容量。
    """

    root = resolve_fanxiu_export_root(export_root)
    definitions: list[dict[str, Any]] = []
    config_rows = _load_config_rows(root, "Item")
    pet_item_type = 28  # ItemType.PetUpItem, used by PetModel.GetPetUpItemList.
    for row in config_rows:
        name = str(row.get("name_plain") or row.get("name") or "")
        if int(row.get("type") or 0) != pet_item_type:
            continue
        gains: dict[int, int] = {}
        raw_effect = str(row.get("effectValue") or "")
        for token in raw_effect.split(","):
            match = re.fullmatch(r"(\d+):(\d+)", token.strip())
            if match is not None:
                gains[int(match.group(1))] = int(match.group(2))
        if not gains:
            continue
        definitions.append(
            {
                "item_id": int(row.get("id") or 0),
                "name": name,
                "quality": int(row.get("quality") or 0),
                "sort_order": int(row.get("num") or 0),
                "aptitude_gain_by_pet_type": gains,
            }
        )
    # Quality ascending (low to high, verified against current Item rows),
    # then num descending. Callers that need an explicit selection order must
    # still sort via pet_resource_planning.order_pet_resources_low_to_high.
    definitions.sort(key=lambda row: (row["quality"], -row["sort_order"]))
    if not definitions:
        raise ValueError("没有找到可计算资质增量的饲灵丸配置")
    return definitions


def collect_lingchong_jingwu_resource_snapshot(
    *,
    activity_id: str,
    export_root: str | Path | None = None,
) -> LingchongJingwuResourceSnapshot:
    """Read current feeding-pill counts from the already-loaded backpack."""

    definitions = load_lingchong_jingwu_resource_definitions(export_root=export_root)
    counts, runtime_evidence = read_backpack_item_counts(
        [row["item_id"] for row in definitions],
        manager_key="lingchong-jingwu-resources",
    )
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    items = []
    for row in definitions:
        gains = dict(row["aptitude_gain_by_pet_type"])
        items.append(
            LingchongJingwuResourceItem(
                **row,
                count=max(0, int(counts.get(int(row["item_id"]), 0))),
                minimum_aptitude_gain=min(gains.values()),
                maximum_aptitude_gain=max(gains.values()),
            )
        )
    return LingchongJingwuResourceSnapshot(
        activity_id=str(activity_id),
        captured_at=captured_at,
        items=items,
        total_count=sum(row.count for row in items),
        evidence={
            **runtime_evidence,
            "official_name": LINGCHONG_JINGWU_OFFICIAL_NAME,
            "user_alias": LINGCHONG_JINGWU_USER_ALIAS,
            "item_count": len(items),
            "effect_key_kind": "pet_gift_id",
            "score_requires_pet_gift_capacity": True,
        },
    )


def ensure_lingchong_jingwu_activity(session: Session) -> str:
    """Project the exact 8-server worldline instance into the shared store."""

    record = session.exec(
        select(FanxiuPacketBusinessRecord)
        .where(
            FanxiuPacketBusinessRecord.domain == "worldline_activity",
            FanxiuPacketBusinessRecord.entity_id
            == str(LINGCHONG_JINGWU_PARENT_ACTIVITY_ID),
        )
        .order_by(
            col(FanxiuPacketBusinessRecord.captured_at).desc(),
            col(FanxiuPacketBusinessRecord.updated_at).desc(),
        )
    ).first()
    if record is None:
        raise ValueError("尚未采集到8跨灵宠竞武活动实例")
    payload = dict(record.payload or {})
    worldline = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    references = resolve_lingchong_jingwu_references()
    activity_payload = build_lingchong_jingwu_activity_payload(
        dict(worldline),
        references=references,
        captured_at=record.captured_at,
    )
    existing = session.exec(
        select(FanxiuExchangeActivity).where(
            FanxiuExchangeActivity.activity_type == LINGCHONG_JINGWU_ACTIVITY_TYPE,
            FanxiuExchangeActivity.cross_count == references.cross_count,
            FanxiuExchangeActivity.start_date == activity_payload["start_date"],
            FanxiuExchangeActivity.end_date == activity_payload["end_date"],
        )
    ).first()
    existing_evidence = dict(existing.evidence or {}) if existing is not None else {}
    activity_payload["evidence"] = {
        **existing_evidence,
        **dict(activity_payload.get("evidence") or {}),
        "worldline_fact": dict(record.evidence or {}),
    }
    if existing is not None and existing.captured_at:
        def captured_time(value: str) -> datetime:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

        try:
            if captured_time(existing.captured_at) > captured_time(record.captured_at):
                activity_payload["captured_at"] = existing.captured_at
                activity_payload["source_kind"] = existing.source_kind
        except ValueError:
            # Keep a non-empty materialized timestamp rather than replacing it
            # with an older worldline projection whose format cannot be ordered.
            activity_payload["captured_at"] = existing.captured_at
            activity_payload["source_kind"] = existing.source_kind
    return upsert_exchange_activity_snapshot(session, activity_payload)


def _quest_items(parsed: dict[str, Any], field: str) -> list[dict[str, Any]]:
    raw = parsed.get(field)
    if isinstance(raw, dict):
        raw = raw.get("items")
    return [dict(item) for item in (raw or []) if isinstance(item, dict)]


def load_lingchong_jingwu_observed_tasks(
    session: Session,
    *,
    export_root: str | Path | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Report the missing Runtime task capability without reading raw history."""

    del session, export_root, start_date, end_date
    raise ValueError(
        "灵宠竞武本期任务 Runtime 读取尚未实现；已禁止使用抓包 raw JSON 兜底"
    )

def store_lingchong_jingwu_resource_snapshot(
    session: Session,
    snapshot: LingchongJingwuResourceSnapshot,
) -> LingchongJingwuResourceSnapshot:
    from backend.core.fanxiu.business_data import (
        upsert_fanxiu_business_records,
    )

    payload = snapshot.model_dump(mode="json")
    upsert_fanxiu_business_records(
        session,
        [
            {
                "domain": "lingchong_jingwu_resource_snapshot",
                "record_key": f"lingchong-jingwu-resources:{snapshot.activity_id}",
                "protocol": "runtime_memory_backpack",
                "source_kind": snapshot.source_kind,
                "entity_id": snapshot.activity_id,
                "entity_name": LINGCHONG_JINGWU_OFFICIAL_NAME,
                "captured_at": snapshot.captured_at.replace("T", " ", 1),
                "payload": payload,
                "evidence": dict(snapshot.evidence),
            }
        ],
    )
    return snapshot




def load_lingchong_jingwu_resource_snapshot(
    session: Session,
    *,
    activity_id: str,
) -> LingchongJingwuResourceSnapshot:
    record = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain
            == "lingchong_jingwu_resource_snapshot",
            FanxiuPacketBusinessRecord.record_key
            == f"lingchong-jingwu-resources:{activity_id}",
        )
    ).first()
    if record is not None:
        return LingchongJingwuResourceSnapshot.model_validate(record.payload)
    definitions = load_lingchong_jingwu_resource_definitions()
    items = [
        LingchongJingwuResourceItem(
            **row,
            count=0,
            minimum_aptitude_gain=min(row["aptitude_gain_by_pet_type"].values()),
            maximum_aptitude_gain=max(row["aptitude_gain_by_pet_type"].values()),
        )
        for row in definitions
    ]
    return LingchongJingwuResourceSnapshot(
        activity_id=activity_id,
        captured_at="",
        source_kind="readonly_backpack_runtime",
        complete=False,
        items=items,
        total_count=0,
        reason="尚未从游戏采集饲灵丸库存",
        evidence={"item_count": len(items), "score_requires_pet_type": True},
    )


def collect_and_store_lingchong_jingwu_activity(
    session: Session,
    *,
    activity_id: str | None = None,
    today: date | None = None,
) -> Any:
    """Refresh the selected occurrence's complete standard ranking facts.

    Same-server ActivityRankMainView owns its personal rank directly and has
    no plane rank. Cross-server occurrences declare their scopes in follow;
    selecting a single-server instance never materializes a different 8-cross
    event as a prerequisite.
    """

    selected_id = activity_id or ensure_lingchong_jingwu_activity(session)
    activity = session.get(FanxiuExchangeActivity, selected_id)
    if activity is None or activity.activity_type != LINGCHONG_JINGWU_ACTIVITY_TYPE:
        raise ValueError("灵宠竞武活动不存在")
    if not is_exchange_activity_active(activity, today=today):
        raise ValueError("灵宠竞武活动不在有效日期内")
    from .exchange_activity_registry import resolve_registered_occurrence_rank_identities
    parent_id = int(activity.game_activity_id or (activity.evidence or {}).get('parent_activity_id') or 0)
    if not activity.game_activity_id:
        # Older 8-cross materializations stored the proven parent only in
        # evidence. Recover that exact identity, never a default cross event.
        worldline = (activity.evidence or {}).get('worldline') or {}
        if (int(worldline.get('activityId') or 0) != parent_id
                or int(worldline.get('serverCount') or 0) != activity.cross_count):
            raise ValueError('所选灵宠旧实例缺少可恢复的世界线身份')
    definitions = _load_config_rows(resolve_fanxiu_export_root(), 'Activity')
    definition = next((row for row in definitions if int(row.get('id') or 0) == parent_id), None)
    if definition is None or str(definition.get('name_plain') or definition.get('name')) != LINGCHONG_JINGWU_OFFICIAL_NAME:
        raise ValueError('所选灵宠竞武实例缺少正式Activity身份')
    if int(definition.get('crossGroup') or 1) != activity.cross_count:
        raise ValueError('所选灵宠竞武实例跨数与正式Activity身份不符')
    identities = resolve_registered_occurrence_rank_identities(
        activity_type=LINGCHONG_JINGWU_ACTIVITY_TYPE,
        game_activity_id=parent_id, cross_count=activity.cross_count,
        activity_follow=tuple(int(value) for value in definition.get('follow') or ()),
    )
    facts = {scope: read_activity_rank_fact(session, identity.runtime_rank_activity_id)
             for scope, identity in identities.items()}
    rows = []
    for scope, fact in facts.items():
        _require_fact_in_activity_period(activity, fact, label=scope)
        if fact.get('rank_vo_type') == 'runtime_memory_activity_rank':
            # GUI collection proves the exact occurrence before persisting.
            # The memory transport uses one VO tag for every scope; the static
            # activity binding above, rather than that tag, owns scope identity.
            occurrence_id = str((activity.evidence or {}).get('runtime_id') or
                                (activity.evidence or {}).get('instance_key') or '')
            if not occurrence_id or str((fact.get('evidence') or {}).get('occurrence_runtime_id') or '') != occurrence_id:
                raise ValueError(f'灵宠竞武{scope} Runtime 事实未绑定所选活动实例')
            fact = {**fact, 'rank_vo_type': ('ActivityRankPersonalVO' if scope == 'personal'
                                          else 'ActivityRankCrossServerVO')}
        rows.extend(project_lingchong_jingwu_rank_rows(fact, scope=scope))
    captured_at = max(str(fact['captured_at']) for fact in facts.values())
    evidence = dict(activity.evidence or {})
    evidence.update(
        {
            "rank_scope_identities": {scope: {
                'runtime_rank_activity_id': identity.runtime_rank_activity_id,
                'reward_activity_id': identity.reward_activity_id,
            } for scope, identity in identities.items()},
            "rank_scope_completeness": {scope: {
                'declared': int(fact.get('rank_list_size') or 0),
                'loaded': len(fact.get('items') or []),
            } for scope, fact in facts.items()},
        }
    )
    activity.evidence = evidence
    activity.game_activity_id = parent_id
    activity.captured_at = captured_at
    activity.source_kind = "standard_runtime_facts"
    session.add(activity)
    replace_exchange_rankings(
        session,
        activity_type=LINGCHONG_JINGWU_ACTIVITY_TYPE,
        activity_id=activity.id,
        rows=rows,
        captured_at=captured_at,
    )
    return list_exchange_activity_snapshot(
        session,
        activity_type=LINGCHONG_JINGWU_ACTIVITY_TYPE,
        activity_id=activity.id,
    ).selected_activity


def _require_fact_in_activity_period(
    activity: FanxiuExchangeActivity,
    fact: dict[str, Any],
    *,
    label: str,
) -> None:
    captured_date = str(fact.get("captured_at") or "")[:10]
    if not captured_date or not (activity.start_date <= captured_date <= activity.end_date):
        raise ValueError(
            f"灵宠竞武{label}事实不属于所选活动周期：{captured_date or '缺少时间'}"
        )


def project_lingchong_jingwu_rank_rows(
    rank_fact: dict[str, Any],
    *,
    scope: Literal["personal", "plane"],
) -> list[dict[str, Any]]:
    """Validate and project a complete standard rank fact."""

    expected_vo = (
        "ActivityRankPersonalVO" if scope == "personal" else "ActivityRankCrossServerVO"
    )
    if str(rank_fact.get("rank_vo_type") or "") != expected_vo:
        raise ValueError(f"灵宠竞武{scope}榜 VO 类型不匹配")
    declared = int(rank_fact.get("rank_list_size") or 0)
    items = [dict(item) for item in (rank_fact.get("items") or []) if isinstance(item, dict)]
    if declared <= 0 or len(items) != declared:
        raise ValueError(f"灵宠竞武{scope}榜不完整：{len(items)}/{declared}")
    ranks = [int(item.get("rank") or 0) for item in items]
    if ranks != list(range(1, declared + 1)):
        raise ValueError(f"灵宠竞武{scope}榜排名不连续")
    self_rank = int(rank_fact.get("rank") or 0)
    rows: list[dict[str, Any]] = []
    for item in items:
        rank = int(item["rank"])
        role_key = str(item.get("key") or item.get("id") or f"{scope}:{rank}")
        rows.append(
            {
                "ranking_scope": scope,
                "rank": rank,
                "score": int(item.get("score") or 0),
                "role_key": role_key,
                "name": str(item.get("name") or ""),
                "server_id": item.get("server_id") or (item.get("id") if scope == "plane" else None),
                "server_name": str(item.get("server_name") or ""),
                "club_name": str(item.get("club_name") or ""),
                "is_self": rank == self_rank,
                "is_reward_guard": False,
                "is_last_player": rank == declared,
                "has_player": True,
                "raw_data": {
                    "reported_rank_list_size": declared,
                    "loaded_player_count": len(items),
                    "scope_complete": True,
                    "rank_vo_type": expected_vo,
                },
            }
        )
    return rows


__all__ = [
    "LINGCHONG_JINGWU_ACTIVITY_TYPE",
    "LINGCHONG_JINGWU_OFFICIAL_NAME",
    "LINGCHONG_JINGWU_USER_ALIAS",
    "LINGCHONG_JINGWU_PARENT_ACTIVITY_ID",
    "LINGCHONG_JINGWU_PERSONAL_RANK_ID",
    "LINGCHONG_JINGWU_PLANE_RANK_ID",
    "LingchongJingwuReferences",
    "LingchongJingwuTaskMilestone",
    "LingchongJingwuResourceItem",
    "LingchongJingwuResourceSnapshot",
    "resolve_lingchong_jingwu_references",
    "build_lingchong_jingwu_activity_payload",
    "load_lingchong_jingwu_task_milestones",
    "load_lingchong_jingwu_resource_definitions",
    "collect_lingchong_jingwu_resource_snapshot",
    "ensure_lingchong_jingwu_activity",
    "load_lingchong_jingwu_observed_tasks",
    "store_lingchong_jingwu_resource_snapshot",
    "load_lingchong_jingwu_resource_snapshot",
    "collect_and_store_lingchong_jingwu_activity",
    "project_lingchong_jingwu_rank_rows",
]
