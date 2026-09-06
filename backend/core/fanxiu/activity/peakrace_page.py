"""Explicit collection for the Peak Race total-board page; no scheduled actions."""

from datetime import datetime
import json

from sqlmodel import Session

from backend.core.fanxiu.activity.exchange_event import upsert_exchange_activity_snapshot
from backend.core.fanxiu.activity.rank_reward import load_activity_rank_reward_tiers
from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.activity_rank_runtime import read_activity_rank_runtime_snapshot
from backend.core.fanxiu.activity.ranking_key_points import project_ranking_key_points


def peakrace_ranking_key_points(total: dict) -> list[dict]:
    """Use the shared guard/self/last projection for the total board."""
    self_rank = total.get("self_ranking") or {}

    def row(raw: dict, *, has_player: bool = True) -> dict:
        return {
            **raw, "id": f"peakrace:{raw['rank']}", "ranking_scope": "personal",
            "name": raw.get("name", ""), "score": raw.get("score", 0),
            "server_name": raw.get("server_name", ""), "club_name": raw.get("club_name", ""),
            "has_player": has_player, "is_self": has_player and raw['rank'] == self_rank.get('rank'),
            "is_reward_guard": False, "is_last_player": False, "captured_at": "",
        }

    by_rank = {r['rank']: row(r) for r in total.get('rankings', [])}
    if self_rank:
        by_rank[self_rank['rank']] = row(self_rank)
    tiers = total.get('reward_tiers', [])
    projected = project_ranking_key_points(
        by_rank.values(), reward_tiers=tiers,
        reward_count=lambda tier: tier['amounts'].get('9070095', 0),
        placeholder_factory=lambda start, end, count: row({'rank': end}, has_player=False),
        rank_list_size=total.get('rank_list_size'),
    )
    for item in projected:
        tier = next((t for t in tiers if t['rank_start'] <= item['rank'] <= t['rank_end']), None)
        item['reward_counts'] = dict(tier['amounts']) if tier else {}
    return projected


def collect_peakrace_total_page(session: Session) -> str:
    """Persist the naturally loaded total board after the caller opens it in game.

    Reads only Runtime and static configuration. Does not navigate, claim rewards,
    spend resources or register a Job. Missing ranks remain missing, not zero.
    """
    schedule = read_fanxiu_activity_runtime_schedule()
    families = schedule.get("peakraceSchedules") or []
    candidates = [
        (family, occurrence)
        for family in families
        for occurrence in family["outer"]["runtimeOccurrences"]
        if occurrence.get("state") == 2
    ]
    if len(candidates) != 1:
        raise ValueError("没有唯一开放的巅峰赛")
    family, occurrence = candidates[0]
    rank_id = int(family["totalRank"]["activityId"])
    rank = read_activity_rank_runtime_snapshot(rank_id)
    if not rank.get("complete") or not rank.get("self_ranking"):
        raise ValueError("请先在游戏中打开巅峰榜总榜，再采集")
    def timestamp(value: int) -> str:
        return datetime.fromtimestamp(value / 1000).astimezone().isoformat(timespec="seconds")

    start = timestamp(occurrence["startTime"])
    end = timestamp(occurrence["endTime"])
    tiers = load_activity_rank_reward_tiers(
        reward_activity_id=rank_id, event_date=start[:10],
        world_level=occurrence.get("avgWorldLevel"),
    )
    path = resolve_fanxiu_export_root() / "parsed_configs" / "Item" / "rows.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else payload.get("rows", payload)
    rows = list(rows.values()) if isinstance(rows, dict) else rows
    names = {int(row["id"]): row.get("name_plain") or row.get("name") for row in rows}
    items = {}
    rewards = []
    for tier in tiers:
        amounts = {}
        for value in tier["rewards"]:
            kind, _, raw = value.partition("|")
            parts = raw.split("_")
            if kind != "Item" or len(parts) < 2:
                raise ValueError(f"尚未支持的巅峰赛奖励格式：{value}")
            item_id, count = int(parts[0]), int(parts[1])
            items[item_id] = names.get(item_id) or f"道具 {item_id}"
            amounts[str(item_id)] = amounts.get(str(item_id), 0) + count
        rewards.append({"rank_start": tier["rank_start"], "rank_end": tier["rank_end"], "amounts": amounts})
    return upsert_exchange_activity_snapshot(session, {
        "activity_type": "peakrace", "family": "resource_rank",
        "instance_key": f"peakrace:{family['outerActivityId']}:{start}:{end}",
        "runtime_id": str(occurrence["id"]), "game_activity_id": family["outerActivityId"],
        "game_rank_activity_id": rank_id, "cross_count": occurrence["runtimeCrossGroup"],
        "start_date": start[:10], "end_date": end[:10], "start_at": start, "end_at": end,
        "prepare_at": timestamp(occurrence["prepareEndTime"]),
        "close_at": timestamp(occurrence["closePanelTime"]),
        "captured_at": rank["captured_at"], "source_kind": "runtime_memory",
        "currency_name": "总榜积分", "current_currency": rank["self_ranking"]["score"],
        "instance_data": {"peakrace_total": {
            "self_ranking": rank["self_ranking"], "rankings": rank["rankings"],
            "rank_list_size": rank["rank_list_size"],
            "items": [{"id": key, "name": name} for key, name in items.items()],
            "reward_tiers": rewards,
        }},
    })
