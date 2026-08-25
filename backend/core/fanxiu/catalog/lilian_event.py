"""Read and join the generated configs behind Lilian choice events."""

from __future__ import annotations

import difflib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from backend.core.fanxiu.catalog.resources import (
    FanxiuResourceError,
    resolve_fanxiu_export_root,
)


_CONFIG_NAMES = (
    "PartnerTrainEvent",
    "PartnerTrainEventPlot",
    "PartnerTrainReward",
    "PartnerTrainCheck",
    "Item",
)
_COMMON_REWARD_ITEM_IDS = frozenset({17003, 19070174})
_NORMALIZE_RE = re.compile(r"[\s，。！？、；：,.!?;:'\"“”‘’（）()《》]+")


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _plain_text(row: Mapping[str, Any], field: str) -> str:
    return str(row.get(f"{field}_plain") or row.get(field) or "").strip()


def _reward_items(value: Any, item_names: Mapping[int, str]) -> list[dict[str, Any]]:
    specs = value if isinstance(value, list) else [value] if value else []
    result: list[dict[str, Any]] = []
    for spec in specs:
        parts = str(spec or "").split("|", 1)
        if len(parts) != 2 or parts[0] != "Item":
            continue
        item_parts = parts[1].rsplit("_", 1)
        item_id = _integer(item_parts[0])
        amount = _integer(item_parts[1]) if len(item_parts) == 2 else None
        if item_id is None or amount is None:
            continue
        result.append(
            {
                "item_id": item_id,
                "item_name": item_names.get(item_id, str(item_id)),
                "amount": amount,
                "spec": str(spec),
            }
        )
    return result


def build_lilian_event_catalog(
    event_rows: Iterable[Mapping[str, Any]],
    plot_rows: Iterable[Mapping[str, Any]],
    reward_rows: Iterable[Mapping[str, Any]],
    check_rows: Iterable[Mapping[str, Any]],
    item_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build the complete special-event choice/reward catalog from config rows."""

    events = [dict(row) for row in event_rows if _integer(row.get("eventType")) == 4]
    plots_source = [dict(row) for row in plot_rows]
    available_plot_groups = {
        group_id
        for row in plots_source
        if _integer(row.get("eventPlotType")) == 1
        and (group_id := _integer(row.get("eventGroupId"))) is not None
    }
    declared_group_counts = Counter(
        group_id
        for row in events
        if (group_id := _integer(row.get("eventGroupId"))) is not None
    )
    # One shipped row (英雄救美, id=140005) repeats the preceding eventGroupId
    # even though its plot rows correctly use 140005. Prefer the event id when
    # it owns a selection group; retain eventGroupId for the normal case.
    event_by_group: dict[int, dict[str, Any]] = {}
    for row in events:
        event_id = _integer(row.get("id"))
        declared_group = _integer(row.get("eventGroupId"))
        effective_group = (
            event_id
            if event_id in available_plot_groups
            and declared_group_counts.get(declared_group, 0) > 1
            else declared_group
        )
        if effective_group is not None:
            event_by_group[effective_group] = row
    plots_by_group: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for raw in plots_source:
        row = dict(raw)
        group_id = _integer(row.get("eventGroupId"))
        if group_id in event_by_group and _integer(row.get("eventPlotType")) == 1:
            plots_by_group[group_id].append(row)
    rewards = {
        _integer(row.get("rewardGroupId")): dict(row)
        for row in reward_rows
        if _integer(row.get("rewardGroupId")) is not None
    }
    checks = {
        _integer(row.get("checkGroupId")): dict(row)
        for row in check_rows
        if _integer(row.get("checkGroupId")) is not None
    }
    item_names = {
        item_id: _plain_text(row, "name")
        for row in item_rows
        if (item_id := _integer(row.get("id"))) is not None
    }

    catalog_events: list[dict[str, Any]] = []
    missing_rewards: set[int] = set()
    missing_checks: set[int] = set()
    for group_id, event in sorted(event_by_group.items()):
        choices: list[dict[str, Any]] = []
        for position, plot in enumerate(
            sorted(plots_by_group.get(group_id, []), key=lambda row: _integer(row.get("id")) or 0)
        ):
            win_reward_id = _integer(plot.get("winReward"))
            lose_reward_id = _integer(plot.get("loseReward"))
            check_group_id = _integer(plot.get("checkGroupId"))
            if win_reward_id is not None and win_reward_id not in rewards:
                missing_rewards.add(win_reward_id)
            if lose_reward_id is not None and lose_reward_id not in rewards:
                missing_rewards.add(lose_reward_id)
            if check_group_id is not None and check_group_id not in checks:
                missing_checks.add(check_group_id)
            win_items = _reward_items(
                (rewards.get(win_reward_id) or {}).get("reward"), item_names
            )
            lose_items = _reward_items(
                (rewards.get(lose_reward_id) or {}).get("reward"), item_names
            )
            premium_items = [
                item for item in win_items if item["item_id"] not in _COMMON_REWARD_ITEM_IDS
            ]
            choices.append(
                {
                    "id": _integer(plot.get("id")),
                    "position": position,
                    "text": _plain_text(plot, "eventDes"),
                    "check_group_id": check_group_id,
                    "check_condition": str(
                        (checks.get(check_group_id) or {}).get("checkCondition") or ""
                    ),
                    "win_text": _plain_text(plot, "winDes"),
                    "lose_text": _plain_text(plot, "loseDes"),
                    "win_reward_group_id": win_reward_id,
                    "lose_reward_group_id": lose_reward_id,
                    "win_rewards": win_items,
                    "lose_rewards": lose_items,
                    # Every known choice event has one reward branch containing
                    # a specialty item beyond the common consolation pair.
                    "preferred": bool(premium_items),
                    "preferred_reward_items": premium_items,
                }
            )
        preferred = [choice for choice in choices if choice["preferred"]]
        catalog_events.append(
            {
                "id": _integer(event.get("id")),
                "event_group_id": group_id,
                "name": _plain_text(event, "eventName"),
                "area_ids": list(event.get("areaIds") or []),
                "event_condition": str(event.get("condition") or ""),
                "special_condition": str(event.get("spEventCondition") or ""),
                "special_condition_description": _plain_text(event, "spEventConditionDes"),
                "special_reward_group_id": _integer(event.get("spReward")),
                "choices": choices,
                "preferred_choice_ids": [choice["id"] for choice in preferred],
                "answer_complete": len(preferred) == 1,
            }
        )

    selection_count = sum(len(event["choices"]) for event in catalog_events)
    answer_complete_count = sum(bool(event["answer_complete"]) for event in catalog_events)
    return {
        "ok": not missing_rewards and not missing_checks,
        "complete": (
            bool(catalog_events)
            and selection_count > 0
            and answer_complete_count == len(catalog_events)
            and not missing_rewards
            and not missing_checks
        ),
        "event_count": len(catalog_events),
        "choice_count": selection_count,
        "answer_complete_count": answer_complete_count,
        "conditional_choice_count": sum(
            bool(choice["check_group_id"])
            for event in catalog_events
            for choice in event["choices"]
        ),
        "missing_reward_group_ids": sorted(missing_rewards),
        "missing_check_group_ids": sorted(missing_checks),
        "events": catalog_events,
    }


def load_lilian_event_catalog(
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Load the generated parsed-config snapshot from the reverse-data root."""

    root = resolve_fanxiu_export_root(export_root)
    rows: dict[str, list[dict[str, Any]]] = {}
    paths: dict[str, str] = {}
    for name in _CONFIG_NAMES:
        path = root / "parsed_configs" / name / "rows.json"
        if not path.is_file():
            raise FanxiuResourceError(f"历练事件配置不存在：{path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise FanxiuResourceError(f"历练事件配置不是行列表：{path}")
        rows[name] = [dict(item) for item in raw if isinstance(item, dict)]
        paths[name] = str(path)
    result = build_lilian_event_catalog(
        rows["PartnerTrainEvent"],
        rows["PartnerTrainEventPlot"],
        rows["PartnerTrainReward"],
        rows["PartnerTrainCheck"],
        rows["Item"],
    )
    result.update({"source": "parsed_generated_config", "paths": paths})
    return result


def _normalized(value: str) -> str:
    return _NORMALIZE_RE.sub("", str(value or "")).lower()


def match_lilian_catalog_event(
    catalog: Mapping[str, Any],
    observed_prompt: str,
    *,
    threshold: float = 0.72,
) -> dict[str, Any] | None:
    """Match one visible event title to its generated-config event row."""

    prompt = _normalized(observed_prompt)
    if not prompt:
        return None
    best_event: Mapping[str, Any] | None = None
    best_score = 0.0
    for event in catalog.get("events") or []:
        event_name = _normalized(str(event.get("name") or ""))
        if not event_name:
            continue
        score = (
            1.0
            if event_name == prompt or event_name in prompt
            else difflib.SequenceMatcher(None, prompt, event_name).ratio()
        )
        if score > best_score:
            best_event = event
            best_score = score
    if best_event is None or best_score < float(threshold):
        return None
    result = dict(best_event)
    result["event_score"] = best_score
    return result


def select_lilian_catalog_choice(
    catalog: Mapping[str, Any],
    observed_prompt: str,
    observed_options: Iterable[str],
    *,
    threshold: float = 0.72,
) -> dict[str, Any] | None:
    """Match the current OCR presentation to the one dominant config answer."""

    prompt = _normalized(observed_prompt)
    options = [str(value or "").strip() for value in observed_options if str(value or "").strip()]
    if not prompt or not options:
        return None
    best_event: Mapping[str, Any] | None = None
    best_event_score = 0.0
    for event in catalog.get("events") or []:
        name = _normalized(str(event.get("name") or ""))
        score = 1.0 if name and name in prompt else difflib.SequenceMatcher(None, prompt, name).ratio()
        if score > best_event_score:
            best_event, best_event_score = event, score
    if best_event is None or best_event_score < threshold or not best_event.get("answer_complete"):
        return None
    preferred_id = (best_event.get("preferred_choice_ids") or [None])[0]
    preferred = next(
        (choice for choice in best_event.get("choices") or [] if choice.get("id") == preferred_id),
        None,
    )
    if not preferred:
        return None
    wanted = _normalized(str(preferred.get("text") or ""))
    ranked = [
        (difflib.SequenceMatcher(None, _normalized(option), wanted).ratio(), position, option)
        for position, option in enumerate(options)
    ]
    option_score, position, text = max(ranked, default=(0.0, -1, ""))
    if option_score < threshold:
        return None
    return {
        "event": dict(best_event),
        "choice": dict(preferred),
        "observed_text": text,
        "observed_position": position,
        "event_score": best_event_score,
        "option_score": option_score,
    }


__all__ = [
    "build_lilian_event_catalog",
    "load_lilian_event_catalog",
    "select_lilian_catalog_choice",
]
