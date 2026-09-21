from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from backend.core.fanxiu.activity.exchange_event import (
    exchange_activity_close_panel_at,
    exchange_activity_lifecycle_phase,
    list_exchange_activity_snapshot,
    replace_exchange_rankings,
    upsert_exchange_activity_snapshot,
)
from backend.core.fanxiu.activity.lingchong_jingwu import (
    project_lingchong_jingwu_rank_rows,
)
from backend.core.fanxiu.activity.standard_observation import read_activity_rank_fact
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.models import (
    FanxiuExchangeActivity,
    FanxiuPacketBusinessRecord,
)


LIANTI_FAXIANG_ACTIVITY_TYPE = "lianti-faxiang"
LIANTI_FAXIANG_OFFICIAL_NAME = "炼体法相"
LIANTI_FAXIANG_ACTIVITY_ID = 1043011
LIANTI_ESSENCE_ITEM_ID = 5030001
LIANTI_BREAKTHROUGH_ITEM_ID = 5030002
_SCORE_CONDITION = re.compile(r"PhysicalFightScore\|(\d+)")


class LiantiFaxiangResourceItem(BaseModel):
    item_id: int
    name: str
    quality: int
    count: int
    role: str
    score_per_item: int = 0


class LiantiFaxiangResourceSnapshot(BaseModel):
    activity_id: str
    captured_at: str
    source_kind: str = "readonly_backpack_runtime"
    complete: bool = True
    items: list[LiantiFaxiangResourceItem] = Field(default_factory=list)
    primary_resource_count: int = 0
    breakthrough_resource_count: int = 0
    maximum_score_gain: int = 0
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


def _event_date(worldline: dict[str, Any], key: str) -> str:
    text = str(worldline.get(f"{key}Text") or "").strip()
    if text:
        return text[:10]
    timestamp = int(worldline.get(key) or 0)
    if timestamp <= 0:
        raise ValueError("炼体法相世界线时间事实不完整")
    return datetime.fromtimestamp(timestamp / 1000).astimezone().date().isoformat()


def ensure_lianti_faxiang_activity(session: Session) -> str:
    """Project today's exact same-server preliminary into the shared store."""

    record = session.exec(
        select(FanxiuPacketBusinessRecord)
        .where(
            FanxiuPacketBusinessRecord.domain == "worldline_activity",
            FanxiuPacketBusinessRecord.entity_id == str(LIANTI_FAXIANG_ACTIVITY_ID),
        )
        .order_by(
            col(FanxiuPacketBusinessRecord.captured_at).desc(),
            col(FanxiuPacketBusinessRecord.updated_at).desc(),
        )
    ).first()
    if record is None:
        raise ValueError("尚未采集到炼体法相预赛活动实例")
    payload = dict(record.payload or {})
    worldline = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    if int(worldline.get("activityId") or 0) != LIANTI_FAXIANG_ACTIVITY_ID:
        raise ValueError("世界线事实不是当前炼体法相预赛")
    if str(worldline.get("name") or "") != LIANTI_FAXIANG_OFFICIAL_NAME:
        raise ValueError("炼体法相世界线名称与静态身份不一致")

    root = resolve_fanxiu_export_root()
    config = next(
        (
            row
            for row in _load_config_rows(root, "Activity")
            if int(row.get("id") or 0) == LIANTI_FAXIANG_ACTIVITY_ID
        ),
        None,
    )
    if config is None or str(config.get("littleName_plain") or "") != "(预赛)":
        raise ValueError("炼体法相 1043011 静态配置不是本服预赛")

    activity_payload = {
        "activity_type": LIANTI_FAXIANG_ACTIVITY_TYPE,
        "cross_count": 1,
        "start_date": _event_date(worldline, "startTime"),
        "end_date": _event_date(worldline, "endTime"),
        "game_activity_id": LIANTI_FAXIANG_ACTIVITY_ID,
        "game_rank_activity_id": LIANTI_FAXIANG_ACTIVITY_ID,
        "currency_name": "炼体积分",
        "captured_at": record.captured_at,
        "source_kind": "standard_runtime_facts",
        "resource_strategy": {
            "resource_metric": "淬体精魄库存",
            "score_metric": "每使用 1 个淬体精魄增加 100 炼体积分",
            "task_metric": "本期服务器下发的 PhysicalFightScore 任务进度",
            "primary_resource_item_id": LIANTI_ESSENCE_ITEM_ID,
            "breakthrough_resource_item_id": LIANTI_BREAKTHROUGH_ITEM_ID,
        },
        "evidence": {
            "official_name": LIANTI_FAXIANG_OFFICIAL_NAME,
            "phase": "预赛",
            "same_server": True,
            "rank_scope_identities": {"personal": {
                "runtime_rank_activity_id": LIANTI_FAXIANG_ACTIVITY_ID,
                "reward_activity_id": LIANTI_FAXIANG_ACTIVITY_ID,
            }},
            "worldline": dict(worldline),
            "worldline_fact": dict(record.evidence or {}),
        },
    }
    return upsert_exchange_activity_snapshot(session, activity_payload)


def _quest_items(parsed: dict[str, Any], field: str) -> list[dict[str, Any]]:
    raw = parsed.get(field)
    if isinstance(raw, dict):
        raw = raw.get("items")
    return [dict(item) for item in (raw or []) if isinstance(item, dict)]


def _task_milestones(
    observed_tasks: list[dict[str, Any]],
    *,
    export_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Join only server-declared QuestEntryVO IDs; never choose a static ladder."""

    if not observed_tasks:
        raise ValueError("炼体法相本期任务尚未加载，拒绝从多套静态梯度猜测")
    root = resolve_fanxiu_export_root(export_root)
    configs = {
        int(row.get("id") or 0): row
        for row in _load_config_rows(root, "ActiveTask")
        if int(row.get("activityId") or 0) == LIANTI_FAXIANG_ACTIVITY_ID
    }
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for observed in observed_tasks:
        task_id = int(observed.get("taskId") or observed.get("task_id") or 0)
        if task_id <= 0 or task_id in seen:
            raise ValueError("炼体法相本期任务 ID 缺失或重复")
        seen.add(task_id)
        config = configs.get(task_id)
        if config is None:
            raise ValueError(f"炼体法相本期任务缺少静态配置：{task_id}")
        target = 0
        for condition in config.get("finishCondition") or []:
            match = _SCORE_CONDITION.fullmatch(str(condition or ""))
            if match is not None:
                target = int(match.group(1))
                break
        progress_rows = observed.get("progressList") or observed.get("progress_list") or []
        if isinstance(progress_rows, dict):
            progress_rows = progress_rows.get("items") or []
        runtime_progress = next(
            (row for row in progress_rows if isinstance(row, dict)), {}
        )
        runtime_target = int(runtime_progress.get("target") or 0)
        if target <= 0 or runtime_target != target:
            raise ValueError(f"炼体法相任务 {task_id} 的 Runtime/配置目标不一致")
        progress = int(runtime_progress.get("progress") or 0)
        result.append(
            {
                "task_id": task_id,
                "order": int(config.get("sort") or 0),
                "name": str(config.get("name_plain") or config.get("name") or ""),
                "target": target,
                "progress": progress,
                "status": int(observed.get("status") or 0),
                "finished": bool(runtime_progress.get("finish")) or progress >= target,
                "must_get": str(config.get("corner_plain") or config.get("corner") or "") == "必拿",
                "rewards": [str(value) for value in (config.get("reward") or [])],
            }
        )
    result.sort(key=lambda row: (row["target"], row["order"], row["task_id"]))
    return result


def load_lianti_faxiang_observed_tasks(
    session: Session,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Report the missing Runtime task capability without reading raw history."""

    del session, export_root, start_date, end_date
    raise ValueError(
        "炼体法相本期任务 Runtime 读取尚未实现；已禁止使用抓包 raw JSON 兜底"
    )

def _resource_definitions(
    *, export_root: str | Path | None = None
) -> list[dict[str, Any]]:
    root = resolve_fanxiu_export_root(export_root)
    rows = {
        int(row.get("id") or 0): row for row in _load_config_rows(root, "Item")
    }
    definitions = []
    for item_id, role, score in (
        (LIANTI_ESSENCE_ITEM_ID, "score", 100),
        (LIANTI_BREAKTHROUGH_ITEM_ID, "breakthrough", 0),
    ):
        row = rows.get(item_id)
        if row is None:
            raise ValueError(f"炼体法相资源配置不存在：{item_id}")
        definitions.append(
            {
                "item_id": item_id,
                "name": str(row.get("name_plain") or row.get("name") or ""),
                "quality": int(row.get("quality") or 0),
                "role": role,
                "score_per_item": score,
            }
        )
    return definitions



def collect_lianti_faxiang_resource_snapshot(
    *,
    activity_id: str,
    export_root: str | Path | None = None,
) -> LiantiFaxiangResourceSnapshot:
    definitions = _resource_definitions(export_root=export_root)
    counts, runtime_evidence = read_backpack_item_counts(
        [row["item_id"] for row in definitions],
        manager_key="lianti-faxiang-resources",
    )
    items = [
        LiantiFaxiangResourceItem(
            **row,
            count=max(0, int(counts.get(int(row["item_id"]), 0))),
        )
        for row in definitions
    ]
    primary_count = next(
        (row.count for row in items if row.item_id == LIANTI_ESSENCE_ITEM_ID), 0
    )
    breakthrough_count = next(
        (row.count for row in items if row.item_id == LIANTI_BREAKTHROUGH_ITEM_ID), 0
    )
    return LiantiFaxiangResourceSnapshot(
        activity_id=activity_id,
        captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        items=items,
        primary_resource_count=primary_count,
        breakthrough_resource_count=breakthrough_count,
        maximum_score_gain=primary_count * 100,
        evidence={
            **runtime_evidence,
            "read_only": True,
            "score_per_primary_resource": 100,
            "breakthrough_resource_not_counted_as_score": True,
        },
    )



def store_lianti_faxiang_resource_snapshot(
    session: Session,
    snapshot: LiantiFaxiangResourceSnapshot,
) -> LiantiFaxiangResourceSnapshot:
    from backend.core.fanxiu.business_data import (
        upsert_fanxiu_business_records,
    )

    payload = snapshot.model_dump(mode="json")
    upsert_fanxiu_business_records(
        session,
        [
            {
                "domain": "resource_ranking_resource_snapshot",
                "record_key": f"{LIANTI_FAXIANG_ACTIVITY_TYPE}:{snapshot.activity_id}",
                "protocol": "runtime_memory_backpack",
                "source_kind": snapshot.source_kind,
                "entity_id": snapshot.activity_id,
                "entity_name": LIANTI_FAXIANG_OFFICIAL_NAME,
                "captured_at": snapshot.captured_at.replace("T", " ", 1),
                "payload": payload,
                "evidence": dict(snapshot.evidence),
            }
        ],
    )
    return snapshot




def load_lianti_faxiang_resource_snapshot(
    session: Session,
    *,
    activity_id: str,
) -> LiantiFaxiangResourceSnapshot:
    record = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == "resource_ranking_resource_snapshot",
            FanxiuPacketBusinessRecord.record_key
            == f"{LIANTI_FAXIANG_ACTIVITY_TYPE}:{activity_id}",
        )
    ).first()
    if record is not None:
        return LiantiFaxiangResourceSnapshot.model_validate(record.payload)
    definitions = _resource_definitions()
    return LiantiFaxiangResourceSnapshot(
        activity_id=activity_id,
        captured_at="",
        complete=False,
        items=[LiantiFaxiangResourceItem(**row, count=0) for row in definitions],
        reason="尚未从游戏采集炼体资源库存",
        evidence={"read_only": True, "score_per_primary_resource": 100},
    )


def _lianti_declared_personal_rank_id(activity: FanxiuExchangeActivity) -> int:
    """Resolve one selected instance's bound personal rank ID from the spec.

    Same-server preliminaries declare no ``follow`` and own the bound scope, so
    their activity id is the rank id.  Cross-server occurrences reference their
    child rank ids (personal first, for example ``43005``).  The historical
    same-server preliminary id is never used as a fallback.
    """

    from backend.core.fanxiu.activity.exchange_activity_registry import (
        resolve_registered_occurrence_rank_identities,
    )

    evidence = dict(activity.evidence or {})
    game_activity_id = int(
        activity.game_activity_id or evidence.get("game_activity_id") or 0
    )
    if game_activity_id <= 0:
        raise ValueError("炼体法相所选实例缺少 game_activity_id，无法校验个人榜绑定")
    follow = next(
        (
            tuple(int(value) for value in (row.get("follow") or ()))
            for row in _load_config_rows(resolve_fanxiu_export_root(), "Activity")
            if int(row.get("id") or 0) == game_activity_id
        ),
        None,
    )
    if follow is None:
        raise ValueError(f"炼体法相活动 {game_activity_id} 缺少静态 follow 声明")
    identities = resolve_registered_occurrence_rank_identities(
        activity_type=LIANTI_FAXIANG_ACTIVITY_TYPE,
        game_activity_id=game_activity_id,
        cross_count=int(activity.cross_count or 1),
        activity_follow=follow,
    )
    personal = identities.get("personal")
    if personal is None or int(personal.runtime_rank_activity_id) <= 0:
        raise ValueError("炼体法相缺少个人榜绑定身份")
    return int(personal.runtime_rank_activity_id)


def _lianti_personal_rank_activity_id(activity: FanxiuExchangeActivity) -> int:
    """Return the selected instance's personal rank ID or fail explicitly."""

    bound = _lianti_declared_personal_rank_id(activity)
    selected = int(activity.game_rank_activity_id or 0)
    if selected <= 0:
        raise ValueError("炼体法相所选实例缺少个人榜身份")
    if selected != bound:
        raise ValueError(
            "炼体法相个人榜绑定身份与所选实例不一致："
            f"绑定={bound}，实例={selected}"
        )
    evidence = dict(activity.evidence or {})
    scope = dict(
        dict(evidence.get("rank_scope_identities") or {}).get("personal") or {}
    )
    scope_id = int(scope.get("runtime_rank_activity_id") or 0)
    if scope_id and scope_id != bound:
        raise ValueError(
            "炼体法相个人榜场景身份与绑定不一致："
            f"scope={scope_id}，绑定={bound}"
        )
    return bound


def _lianti_rank_snapshot_is_complete(snapshot: dict[str, Any]) -> bool:
    """Whether one Runtime read covers the full personal rank, not one page.

    A paged board can report ``complete`` for the current page while
    ``rank_list_size`` is the whole population: the first page carries 1..50
    (declared 50) and scrolling replaces the Runtime list with 51..60
    (declared 10).  Persisting either page would overwrite the saved full fact
    with a fragment, so require the total to be positive and every count
    (declared/loaded/row count) to equal it, with a duplicate-free contiguous
    1..total rank set.
    """

    total = int(snapshot.get("rank_list_size") or 0)
    if total <= 0:
        return False
    rows = [item for item in snapshot.get("rankings") or [] if isinstance(item, dict)]
    if (
        int(snapshot.get("declared_rank_count") or 0) != total
        or int(snapshot.get("loaded_rank_count") or 0) != total
        or len(rows) != total
    ):
        return False
    ranks = [int(item.get("rank") or 0) for item in rows]
    return sorted(ranks) == list(range(1, total + 1))


def _refresh_lianti_faxiang_rank_runtime_facts(
    session: Session,
    activity: FanxiuExchangeActivity,
    *,
    rank_activity_id: int,
) -> str:
    """Refresh the personal-rank fact from the client's loaded leaderboard.

    Returns ``updated`` when a fresh, complete Runtime snapshot replaced the
    persisted fact, and ``retained`` when the board is not loaded, cannot be
    read, or only a partial page is loaded, so the caller keeps the previous
    fact and its existing completeness checks.  A partial page never
    masquerades as ``updated``.
    """

    from backend.core.fanxiu.activity.standard_observation import (
        store_runtime_activity_rank_fact,
    )
    from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
        prepare_activity_rank_runtime,
        read_activity_rank_runtime_snapshot,
    )

    evidence = dict(activity.evidence or {})
    occurrence_runtime_id = str(
        evidence.get("runtime_id") or evidence.get("instance_key") or ""
    ).strip()
    if not occurrence_runtime_id or int(rank_activity_id) <= 0:
        return "retained"
    snapshot = read_activity_rank_runtime_snapshot(int(rank_activity_id))
    if (
        not snapshot.get("ok")
        and str(snapshot.get("error_code") or "")
        in {"process_cache_miss", "root_cache_miss"}
    ):
        recovery = prepare_activity_rank_runtime([int(rank_activity_id)])
        if bool(recovery.get("ok")):
            snapshot = read_activity_rank_runtime_snapshot(int(rank_activity_id))
    if not snapshot.get("ok") or not snapshot.get("complete"):
        return "retained"
    if not _lianti_rank_snapshot_is_complete(snapshot):
        return "retained"
    store_runtime_activity_rank_fact(
        session,
        snapshot,
        occurrence_runtime_id=occurrence_runtime_id,
    )
    return "updated"


def _lianti_personal_rank_fact_for_projection(fact: dict[str, Any]) -> dict[str, Any]:
    """Adapt one personal-rank fact to 灵宠竞武's shared row projection.

    炼体法相没有独立投影，直接复用灵宠竞武的个人榜几何校验（申报数量、
    行数、名次连续）。但那套投影还把 ``rank_vo_type`` 当作“只接受封包采集”
    的来源门禁；炼体法相今天的榜事实可能只存在于客户端已加载的 Lua 榜对象
    里，没有任何封包覆盖新周期。这里的榜 ID 本身已经固定了榜作用域，因此
    在来源明确时把 VO 标记归一化，其余完整性与连续性校验保持原样。
    """

    vo_type = str(fact.get("rank_vo_type") or "")
    if vo_type in {"", "ActivityRankPersonalVO"}:
        return fact
    if vo_type != "runtime_memory_activity_rank":
        raise ValueError(f"炼体法相个人榜 VO 类型不匹配：{vo_type}")
    normalized = dict(fact)
    normalized["rank_vo_type"] = "ActivityRankPersonalVO"
    return normalized


def _parse_fact_captured_at(value: str) -> datetime | None:
    """Parse a fact timestamp into an aware datetime, else ``None``.

    ``None`` means "no exact timestamp"; callers decide whether that is a
    legacy fallback (non-runtime packet facts) or a hard failure (Runtime
    facts must be exact).
    """

    try:
        parsed = datetime.fromisoformat(value.replace(" ", "T", 1))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _validate_lianti_fact_binding(
    fact: dict[str, Any],
    *,
    activity: FanxiuExchangeActivity,
    phase: str,
) -> None:
    """Enforce exact-occurrence Runtime binding and the real capture window.

    A Runtime fact must carry the same ``occurrence_runtime_id`` as this
    occurrence.  A settlement read must itself be a Runtime fact, so a stale
    non-runtime packet fact can never authorize a post-end collection.  The
    capture timestamp is checked against the real window
    ``start_at..close_panel_at`` (not merely the calendar end date).  Only a
    legacy fact without a parseable timestamp falls back to the date range.
    """

    captured_date = str(fact.get("captured_at") or "")[:10]
    if not captured_date:
        raise ValueError("炼体法相个人榜事实不属于所选活动周期")
    captured_at = _parse_fact_captured_at(str(fact.get("captured_at") or ""))
    fact_runtime_id = str(
        (fact.get("evidence") or {}).get("occurrence_runtime_id") or ""
    ).strip()
    occurrence_runtime_id = str(
        (activity.evidence or {}).get("runtime_id") or ""
    ).strip()
    is_runtime_fact = (
        str(fact.get("rank_vo_type") or "") == "runtime_memory_activity_rank"
    )
    if is_runtime_fact and (
        not occurrence_runtime_id or fact_runtime_id != occurrence_runtime_id
    ):
        raise ValueError("炼体法相个人榜 Runtime 事实未绑定所选活动实例")
    if phase == "settlement" and not is_runtime_fact:
        raise ValueError("炼体法相结算期新采集必须来自绑定 Runtime 事实")
    if is_runtime_fact and captured_at is None:
        # A Runtime fact must carry a parseable exact timestamp; falling back to
        # a whole-day comparison could accept a fact captured outside the real
        # panel window.  Legacy non-runtime packet facts keep the date fallback.
        raise ValueError("炼体法相个人榜 Runtime 事实缺少可解析的采集时刻")
    if captured_at is not None:
        start_boundary = datetime.fromisoformat(activity.start_at)
        if start_boundary.tzinfo is None:
            start_boundary = start_boundary.astimezone()
        close_boundary = exchange_activity_close_panel_at(activity)
        fact_moment = captured_at.astimezone(start_boundary.tzinfo)
        if not start_boundary <= fact_moment <= close_boundary:
            raise ValueError("炼体法相个人榜事实不属于所选活动周期")
    elif not activity.start_date <= captured_date <= activity.end_date:
        raise ValueError("炼体法相个人榜事实不属于所选活动周期")


def collect_and_store_lianti_faxiang_activity(
    session: Session,
    *,
    activity_id: str | None = None,
    today: date | None = None,
    now: datetime | None = None,
) -> Any:
    if activity_id:
        # A caller that already resolved one exact occurrence owns its identity.
        # Do not materialize the legacy same-server preliminary over it.
        selected_id = activity_id
    else:
        selected_id = ensure_lianti_faxiang_activity(session)
    activity = session.get(FanxiuExchangeActivity, selected_id)
    if activity is None or activity.activity_type != LIANTI_FAXIANG_ACTIVITY_TYPE:
        raise ValueError("炼体法相活动不存在")
    # A settled-but-still-open panel (settlement) must remain collectable so the
    # final personal rank can be read before the panel closes.  ``today`` keeps
    # the legacy whole-day compatibility for callers without an exact clock;
    # ``now`` enforces the exact-moment upper bound.
    if now is not None:
        phase = exchange_activity_lifecycle_phase(activity, at=now)
    else:
        phase = exchange_activity_lifecycle_phase(activity, today=today)
    if phase not in {"active", "settlement"}:
        raise ValueError("炼体法相活动不在有效日期内")
    # The selected occurrence's bound personal scope must be proven before any
    # Runtime read.  A missing or mismatched identity is an explicit failure,
    # never a silent fallback to the same-server preliminary id.
    rank_activity_id = _lianti_personal_rank_activity_id(activity)
    # 炼体法相没有自己的封包采集阶段：只有游戏内榜单页被打开过，客户端才会
    # 把榜对象加载进运行态。缺了这一步，对账永远读到跨周期的旧事实（例如
    # 2026-08-14 的快照），日期校验必然失败并每十分钟重试一次。这里与瑶池
    # 花会保持同一种“机会式刷新”：榜没加载就保留旧事实，由完整性检查如实
    # 报告，而不是让适配器抛出不存在的错误。
    rank_runtime_refresh = _refresh_lianti_faxiang_rank_runtime_facts(
        session, activity, rank_activity_id=rank_activity_id,
    )
    fact = read_activity_rank_fact(session, rank_activity_id)
    _validate_lianti_fact_binding(fact, activity=activity, phase=phase)
    try:
        rows = project_lingchong_jingwu_rank_rows(
            _lianti_personal_rank_fact_for_projection(fact),
            scope="personal",
        )
    except ValueError as exc:
        # The shared geometry projection names its original 灵宠竞武 owner.
        # Surface it as the current 炼体法相 bound rank so diagnostics identify
        # the real instance instead of a reused function's label.
        raise ValueError(
            f"炼体法相个人榜（rank {rank_activity_id}）校验失败：{exc}"
        ) from exc
    activity.captured_at = str(fact["captured_at"])
    activity.source_kind = "standard_runtime_facts"
    evidence = dict(activity.evidence or {})
    evidence["rank_runtime_refresh"] = rank_runtime_refresh
    evidence["rank_fact_provenance"] = {
        "rank_vo_type": str(fact.get("rank_vo_type") or ""),
        "protocol": str(fact.get("protocol") or ""),
    }
    evidence["rank_scope_completeness"] = {
        "personal": {
            "declared": int(fact.get("rank_list_size") or 0),
            "loaded": len(fact.get("items") or []),
        }
    }
    activity.evidence = evidence
    session.add(activity)
    replace_exchange_rankings(
        session,
        activity_type=LIANTI_FAXIANG_ACTIVITY_TYPE,
        activity_id=activity.id,
        rows=rows,
        captured_at=activity.captured_at,
    )
    return list_exchange_activity_snapshot(
        session,
        activity_type=LIANTI_FAXIANG_ACTIVITY_TYPE,
        activity_id=activity.id,
    ).selected_activity


__all__ = [
    "LIANTI_FAXIANG_ACTIVITY_TYPE",
    "LIANTI_FAXIANG_OFFICIAL_NAME",
    "LIANTI_FAXIANG_ACTIVITY_ID",
    "ensure_lianti_faxiang_activity",
    "load_lianti_faxiang_observed_tasks",
    "LiantiFaxiangResourceSnapshot",
    "collect_lianti_faxiang_resource_snapshot",
    "store_lianti_faxiang_resource_snapshot",
    "load_lianti_faxiang_resource_snapshot",
    "collect_and_store_lianti_faxiang_activity",
]
