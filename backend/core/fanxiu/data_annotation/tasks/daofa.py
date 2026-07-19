from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.catalog.server_relations import classify_fanxiu_target_relation
from backend.core.fanxiu.packet.decoded_store import list_fanxiu_packet_decoded_records
from backend.core.fanxiu.packet.insights import get_fanxiu_packet_runtime_insights
from backend.core.fanxiu.packet.service_runtime import (
    request_fanxiu_packet_service_catch_up,
    start_fanxiu_packet_service,
)
from backend.db import engine


DAOFA_PLAY_INFO_PROTOCOL_ID = 89202
DAOFA_CHALLENGE_PROTOCOL_ID = 89206
DAOFA_PLAY_INFO_PROTOCOL_NAME = "SM_LingArenaPlayInfo"
DAOFA_CHALLENGE_PROTOCOL_NAME = "SM_LingArenaChallenge"
_RANK_RE = re.compile(r"第\s*(\d+)\s*名")
_COUNT_RE = re.compile(r"(\d+)\s*[/／]\s*(\d+)")


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _container_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return [item for item in value["items"] if isinstance(item, dict)]
    return []


def normalize_daofa_packet_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize both initial-entry and post-challenge arena packets."""

    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    parsed = payload.get("parsed") if isinstance(payload.get("parsed"), dict) else {}
    name = str(record.get("name") or payload.get("name") or "")
    if name == DAOFA_PLAY_INFO_PROTOCOL_NAME:
        joiner = parsed.get("joinerVO") if isinstance(parsed.get("joinerVO"), dict) else {}
        rank = _int_or_none(joiner.get("rank"))
        remain_times = _int_or_none(joiner.get("remainTimes"))
        old_rank = None
    elif name == DAOFA_CHALLENGE_PROTOCOL_NAME:
        rank = _int_or_none(parsed.get("newRank"))
        remain_times = _int_or_none(parsed.get("remainTimes"))
        old_rank = _int_or_none(parsed.get("oldRank"))
    else:
        return {"ok": False, "available": False, "reason": "unsupported_protocol"}

    targets: list[dict[str, Any]] = []
    for item in _container_items(parsed.get("targets")):
        target_rank = _int_or_none(item.get("rank"))
        if target_rank is None:
            continue
        player = bool(item.get("player"))
        targets.append(
            {
                "id": _int_or_none(item.get("id")),
                "rank": target_rank,
                "name": str(item.get("name") or ""),
                "server_id": _int_or_none(item.get("server")),
                "power": _number_or_none(item.get("power")) or 0.0,
                "player": player,
                "is_npc": not player,
                "club": str(item.get("club") or ""),
            }
        )

    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    return {
        "ok": rank is not None and remain_times is not None and bool(targets or remain_times == 0),
        "available": True,
        "protocol": name,
        "pro_id": _int_or_none(record.get("pro_id") or payload.get("pro_id")),
        "rank": rank,
        "old_rank": old_rank,
        "remain_times": remain_times,
        "targets": sorted(targets, key=lambda item: int(item["rank"])),
        "captured_at": str(record.get("captured_at") or ""),
        "packet_id": str(record.get("packet_id") or evidence.get("packet_id") or ""),
        "pcap_name": str(record.get("pcap_name") or evidence.get("pcap_name") or ""),
    }


def latest_daofa_facts(*, protocol: str | None = None, since_seconds: int = 7200) -> dict[str, Any]:
    names = [protocol] if protocol else [DAOFA_PLAY_INFO_PROTOCOL_NAME, DAOFA_CHALLENGE_PROTOCOL_NAME]
    pro_ids = (
        [DAOFA_PLAY_INFO_PROTOCOL_ID]
        if protocol == DAOFA_PLAY_INFO_PROTOCOL_NAME
        else [DAOFA_CHALLENGE_PROTOCOL_ID]
        if protocol == DAOFA_CHALLENGE_PROTOCOL_NAME
        else [DAOFA_PLAY_INFO_PROTOCOL_ID, DAOFA_CHALLENGE_PROTOCOL_ID]
    )
    with Session(engine) as session:
        result = list_fanxiu_packet_decoded_records(
            session,
            names=names,
            pro_ids=pro_ids,
            since_seconds=max(60, int(since_seconds)),
            limit=100,
        )
    for record in result.get("records") or []:
        facts = normalize_daofa_packet_record(record)
        if facts.get("ok"):
            return facts
    return {"ok": True, "available": False, "reason": "no_fresh_daofa_packet"}


def current_player_battle_score() -> dict[str, Any]:
    """Read self power from the same player-panel snapshot used by the wiki."""

    result = get_fanxiu_packet_runtime_insights(sync=False)
    snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else {}
    account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
    login = account.get("latest_login") if isinstance(account.get("latest_login"), dict) else {}
    role_id = _int_or_none(login.get("role_id"))
    candidates: list[dict[str, Any]] = []
    profiles = snapshot.get("player_profiles") if isinstance(snapshot.get("player_profiles"), dict) else {}
    for collection in (profiles.get("daily_records"), profiles.get("records")):
        for row in collection if isinstance(collection, list) else []:
            if not isinstance(row, dict) or _int_or_none(row.get("role_id")) != role_id:
                continue
            if _number_or_none(row.get("battle_score")) is not None:
                candidates.append(row)
    for row in (account.get("latest_identity"), login):
        if isinstance(row, dict) and _number_or_none(row.get("battle_score")) is not None:
            candidates.append(row)
    if not candidates:
        return {"ok": False, "available": False, "reason": "self_battle_score_missing", "role_id": role_id}
    selected = max(candidates, key=lambda row: str(row.get("captured_at") or ""))
    return {
        "ok": True,
        "available": True,
        "role_id": role_id,
        "name": str(selected.get("name") or login.get("name") or ""),
        "server_id": _int_or_none(selected.get("server") or login.get("server")),
        "battle_score": float(selected["battle_score"]),
        "captured_at": str(selected.get("captured_at") or ""),
    }


def select_daofa_target(
    facts: dict[str, Any],
    *,
    battle_score: float,
    force_finish: bool = False,
    data_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    """Apply rank, power and dynamically loaded relation policy."""

    current_rank = _int_or_none(facts.get("rank"))
    if current_rank is None:
        return None
    targets: list[dict[str, Any]] = []
    for raw in facts.get("targets") or []:
        if not isinstance(raw, dict):
            continue
        target = dict(raw)
        rank = _int_or_none(target.get("rank"))
        power = _number_or_none(target.get("power"))
        if rank is None or power is None or power >= float(battle_score):
            continue
        target["relation"] = classify_fanxiu_target_relation(
            is_npc=bool(target.get("is_npc")),
            server_id=target.get("server_id"),
            data_dir=data_dir,
        )
        targets.append(target)

    ahead = [target for target in targets if int(target["rank"]) < current_rank]
    group_order = {"non_friendly": 0, "ally": 1, "alliance": 2, "same_server": 3}

    def attack_key(target: dict[str, Any]) -> tuple[int, int, int]:
        relation = target["relation"]
        relation_key = str(relation.get("relation") or "other_server")
        # Config order is protection-desc; larger indexes are less protected.
        server_priority = relation.get("server_priority")
        protection_key = -int(server_priority) if isinstance(server_priority, int) else 0
        return group_order.get(relation_key, 0), protection_key, int(target["rank"])

    if ahead:
        return min(ahead, key=attack_key)
    if force_finish:
        behind = [target for target in targets if int(target["rank"]) >= current_rank]
        return min(behind, key=lambda target: (float(target["power"]), int(target["rank"]))) if behind else None
    return None


def daofa_settlement_at(now: datetime) -> datetime:
    # Sunday settles at 22:00; Monday-Saturday end at 23:59.
    hour, minute = (22, 0) if now.weekday() == 6 else (23, 59)
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def should_force_finish_daofa(now: datetime, *, threshold_minutes: int = 30) -> bool:
    return daofa_settlement_at(now) - now <= timedelta(minutes=max(1, int(threshold_minutes)))


class DaofaTaskMixin:
    def _run_daofa_challenge_round(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        challenge_point: tuple[float, float] | None = None,
        prompt_timeout: float = 15.0,
        result_timeout: float = 600.0,
        return_timeout: float = 45.0,
    ):
        """Complete exactly one #376 -> (#377) -> #378 -> #376 round."""

        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )
        scene_id, _score, _frame = runtime.current_scene([376, 377, 378], update=True)
        prompt_seen = scene_id == 377
        if scene_id == 376:
            if challenge_point is None:
                raise RuntimeError("道法争锋：从 #376 开始挑战时必须提供目标挑战按钮落点")
            click_x, click_y = map(float, challenge_point)
            image376 = (ctx.get("images") or {}).get(376)
            if not isinstance(image376, dict):
                raise RuntimeError("道法争锋：缺少 #376 资产标注")
            width, height = self._frame_size(image376)
            if not (0.0 <= click_x <= width and 0.0 <= click_y <= height):
                raise ValueError(f"道法争锋：挑战落点越界 ({click_x:.1f}, {click_y:.1f})")
            runtime.click_frame_point(376, click_x, click_y)
            landed = yield from runtime.wait_scene(
                377,
                378,
                # #377 is optional.  When it is suppressed a real-player
                # fight may run for minutes before #378 appears, so this wait
                # must cover the battle rather than only the prompt grace.
                timeout=max(float(prompt_timeout), float(result_timeout)),
                label="道法争锋：等待挑战确认或挑战结果",
            )
            scene_id = int(getattr(landed, "id", landed))
            prompt_seen = scene_id == 377
        elif scene_id not in {377, 378}:
            raise RuntimeError("道法争锋：小闭环只能从 #376、#377 或 #378 开始")
        if scene_id == 377:
            runtime.click_shape_center(377, "确认")
            yield from runtime.wait_scene(
                378,
                timeout=float(result_timeout),
                label="道法争锋：确认挑战后等待战斗结果",
            )
        result_text = runtime.ocr_text(update=True)
        runtime.click_shape_center(378, "继续")
        yield from runtime.wait_scene(
            376,
            timeout=float(return_timeout),
            label="道法争锋：结果页继续并返回挑战页",
        )
        return {"status": "success", "prompt_seen": prompt_seen, "result_text": result_text, "final_scene": 376}

    def _daofa_remaining_from_ocr(self, runtime: Any) -> int | None:
        text = runtime.ocr_text_in_shapes(376, ("次数",), padding=12)
        match = _COUNT_RE.search(str(text or ""))
        if match is None:
            match = _COUNT_RE.search(runtime.ocr_text(update=True))
        return int(match.group(1)) if match else None

    def _daofa_challenge_template_delta(self, ctx: dict[str, Any]) -> tuple[float, float, dict[str, float]]:
        image = (ctx.get("images") or {}).get(376)
        if not isinstance(image, dict):
            raise RuntimeError("道法争锋：缺少 #376 资产")
        rank_shape = self._find_shape(image, "第x名")
        challenge_shape = self._find_shape(image, "挑战")
        window_shape = self._find_shape(image, "窗口")
        if rank_shape is None or challenge_shape is None or window_shape is None:
            raise RuntimeError("道法争锋：#376 必须标注「窗口/第x名」和「窗口/挑战」")
        width, height = self._frame_size(image)
        rank_cx = (float(rank_shape.get("x") or 0) + float(rank_shape.get("w") or 0) / 2) * width
        rank_cy = (float(rank_shape.get("y") or 0) + float(rank_shape.get("h") or 0) / 2) * height
        challenge_cx = (float(challenge_shape.get("x") or 0) + float(challenge_shape.get("w") or 0) / 2) * width
        challenge_cy = (float(challenge_shape.get("y") or 0) + float(challenge_shape.get("h") or 0) / 2) * height
        return challenge_cx - rank_cx, challenge_cy - rank_cy, self._box(window_shape, image)

    def _daofa_visible_ranks(self, runtime: Any, window_box: dict[str, float]) -> list[tuple[int, float, float]]:
        left = float(window_box.get("x") or 0)
        top = float(window_box.get("y") or 0)
        right = left + float(window_box.get("w") or 0)
        bottom = top + float(window_box.get("h") or 0)
        matches: list[tuple[int, float, float]] = []
        for line in runtime.ocr_fragments(update=True):
            text = str(line.get("text") or "")
            match = _RANK_RE.search(text)
            if not match:
                continue
            cx = float(line.get("x") or 0) + float(line.get("w") or 0) / 2
            cy = float(line.get("y") or 0) + float(line.get("h") or 0) / 2
            if left <= cx <= right and top <= cy <= bottom:
                matches.append((int(match.group(1)), cx, cy))
        return sorted(matches, key=lambda item: item[2])

    def _locate_daofa_challenge_point(
        self,
        runtime: Any,
        ctx: dict[str, Any],
        target_rank: int,
        *,
        max_scrolls: int = 16,
    ):
        delta_x, delta_y, window_box = self._daofa_challenge_template_delta(ctx)
        top = float(window_box.get("y") or 0) + 8.0
        bottom = float(window_box.get("y") or 0) + float(window_box.get("h") or 0) - 8.0
        direction = "down"
        for attempt in range(max(1, int(max_scrolls)) + 1):
            visible = self._daofa_visible_ranks(runtime, window_box)
            for rank, x, y in visible:
                if rank != int(target_rank):
                    continue
                point = (x + delta_x, y + delta_y)
                if top <= point[1] <= bottom:
                    return point
                direction = "down" if point[1] > bottom else "up"
                break
            else:
                ranks = [rank for rank, _x, _y in visible]
                if ranks:
                    if int(target_rank) < min(ranks):
                        direction = "up"
                    elif int(target_rank) > max(ranks):
                        direction = "down"
            if attempt >= int(max_scrolls):
                break
            changed = yield from runtime.scroll_shape_content(
                376,
                "窗口",
                direction=direction,
                ratio=0.38,
                unchanged_confirmations=2,
            )
            if not changed:
                direction = "up" if direction == "down" else "down"
        raise RuntimeError(f"道法争锋：在 #376 滚动窗口中未找到第 {target_rank} 名的可见挑战按钮")

    def _wait_daofa_packet(
        self,
        runtime: Any,
        *,
        protocol: str | tuple[str, ...],
        before_packet_id: str | dict[str, str],
        timeout: float = 45.0,
        reason: str,
    ):
        protocols = (protocol,) if isinstance(protocol, str) else protocol
        before_ids = (
            {protocols[0]: str(before_packet_id or "")}
            if isinstance(before_packet_id, str)
            else {name: str(before_packet_id.get(name) or "") for name in protocols}
        )
        try:
            start_fanxiu_packet_service(wait_seconds=1.0)
            request_fanxiu_packet_service_catch_up(
                reason=reason,
                wait_seconds=min(30.0, max(5.0, float(timeout) / 2)),
            )
        except Exception as exc:
            self._log("warning", f"道法争锋：主动追平抓包失败，继续轮询已解码事实：{exc}")
        started = time.monotonic()
        while time.monotonic() - started < float(timeout):
            for protocol_name in protocols:
                facts = latest_daofa_facts(protocol=protocol_name)
                if facts.get("available") and str(facts.get("packet_id") or "") != before_ids[protocol_name]:
                    return facts
            yield from runtime.wait_action_settle(1.0)
        return None

    def _leave_daofa_to_world(self, runtime: Any):
        # #376 is an activity overlay. Reuse the already verified activity-page
        # return closure until its lower-left return shape is promoted in assets.
        yield from self._日常报名返回世界(runtime)

    def _execute_daily_daofa_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None,
    ):
        payload = dict(payload or {})
        task_type = "daily_daofa"
        task_label = "道法争锋"
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )
        runtime.attrs["payload"] = payload
        scene_id, _score, frame = runtime.current_scene([34, 69, 376], update=True)
        started_in_daofa = scene_id == 376
        before_play_info = latest_daofa_facts(protocol=DAOFA_PLAY_INFO_PROTOCOL_NAME)
        text = runtime.ocr_text(frame)
        if scene_id != 376:
            if scene_id != 69:
                yield from self._enter_daily_from_world_like(
                    ctx, runtime, stop_event, frame, scene_id, text, label=task_label
                )
            status = yield from runtime.open_daily_entry(
                label=task_label,
                title_pattern=r"道\s*法",
                progress_can_mark_done=False,
                max_scrolls=int(payload.get("max_scrolls") or 30),
                reverse_scrolls=int(payload.get("reverse_scrolls") or 30),
                initial_checks=2,
            )
            if status != "open":
                retry_after = self._record_daily_entry_not_found_retry(
                    payload,
                    task_id="daily-daofa",
                    task_type=task_type,
                    label=task_label,
                    entry_label="道法",
                    seconds=int(payload.get("retry_seconds") or 1800),
                )
                yield from runtime.goto_view(34)
                return {"result": "skipped", "message": f"未找到道法入口，{retry_after} 重试"}
            yield from runtime.wait_scene(376, timeout=30.0, label="道法争锋：等待挑战页 #376")

        # Completion is authoritative on the visible game UI and does not need
        # target/ranking data.  This also avoids waiting for packet persistence
        # after all attempts have already been consumed.
        if self._daofa_remaining_from_ocr(runtime) == 0:
            yield from self._leave_daofa_to_world(runtime)
            next_time = self._record_daily_entry_done(
                payload,
                task_id="daily-daofa",
                task_type=task_type,
                label=task_label,
                message="OCR 已确认今日剩余挑战次数为 0",
            )
            return {"result": "success", "message": f"道法争锋已完成，下次 {next_time}", "current_scene": 34}

        if started_in_daofa:
            facts = latest_daofa_facts(protocol=DAOFA_PLAY_INFO_PROTOCOL_NAME)
            if not facts.get("available"):
                facts = latest_daofa_facts()
        else:
            facts = yield from self._wait_daofa_packet(
                runtime,
                protocol=DAOFA_PLAY_INFO_PROTOCOL_NAME,
                before_packet_id=str(before_play_info.get("packet_id") or ""),
                timeout=float(payload.get("packet_timeout") or 120.0),
                reason="daily-daofa-entry",
            )
            if facts is None:
                retry_after = (
                    datetime.now() + timedelta(seconds=max(60, int(payload.get("retry_seconds") or 1800)))
                ).strftime("%Y-%m-%d %H:%M:%S")
                self._record_scheduler_task_discovered_retry_after(
                    str(payload.get("__scheduler_task_id") or "daily-daofa"),
                    retry_after,
                    task_type=task_type,
                    label=task_label,
                    last_result="skipped",
                )
                yield from self._leave_daofa_to_world(runtime)
                return {
                    "result": "skipped",
                    "message": f"进入后最新排名回包未及时入库，{retry_after} 安全复查",
                    "current_scene": 34,
                }
        profile = current_player_battle_score()
        if not profile.get("available"):
            raise RuntimeError("道法争锋：玩家面板中缺少自己的战力，无法安全选人")
        battle_score = float(profile["battle_score"])
        completed_rounds = 0
        while True:
            remaining_ocr = self._daofa_remaining_from_ocr(runtime)
            remaining_packet = _int_or_none(facts.get("remain_times"))
            remaining = remaining_ocr if remaining_ocr is not None else remaining_packet
            if remaining is None:
                raise RuntimeError("道法争锋：OCR 和抓包都未取到剩余挑战次数")
            if remaining <= 0:
                yield from self._leave_daofa_to_world(runtime)
                next_time = self._record_daily_entry_done(
                    payload,
                    task_id="daily-daofa",
                    task_type=task_type,
                    label=task_label,
                    message=f"今日已完成 {completed_rounds} 次挑战",
                )
                return {"result": "success", "message": f"道法争锋已完成，下次 {next_time}", "current_scene": 34}

            now = datetime.now()
            force_finish = should_force_finish_daofa(
                now,
                threshold_minutes=int(payload.get("force_finish_minutes") or 30),
            )
            target = select_daofa_target(facts, battle_score=battle_score, force_finish=force_finish)
            if target is None:
                retry_after = (now + timedelta(seconds=max(60, int(payload.get("retry_seconds") or 1800)))).strftime("%Y-%m-%d %H:%M:%S")
                self._record_scheduler_task_discovered_retry_after(
                    str(payload.get("__scheduler_task_id") or "daily-daofa"),
                    retry_after,
                    task_type=task_type,
                    label=task_label,
                    last_result="skipped",
                )
                yield from self._leave_daofa_to_world(runtime)
                return {"result": "skipped", "message": f"排名前暂无可战胜目标，{retry_after} 复查", "current_scene": 34}

            relation = target.get("relation") or {}
            self._log(
                "action",
                f"道法争锋：剩余 {remaining} 次，选择第 {target['rank']} 名「{target['name']}」"
                f"（{relation.get('relation_label') or '未知关系'}，战力 {float(target['power']):.6g}）",
            )
            point = yield from self._locate_daofa_challenge_point(
                runtime,
                ctx,
                int(target["rank"]),
                max_scrolls=int(payload.get("rank_scrolls") or 16),
            )
            before = latest_daofa_facts(protocol=DAOFA_CHALLENGE_PROTOCOL_NAME)
            before_play_info_after_round = latest_daofa_facts(protocol=DAOFA_PLAY_INFO_PROTOCOL_NAME)
            yield from self._run_daofa_challenge_round(
                ctx,
                stop_event,
                challenge_point=point,
                prompt_timeout=float(payload.get("prompt_timeout") or 15.0),
                result_timeout=float(payload.get("battle_timeout") or 600.0),
                return_timeout=float(payload.get("return_timeout") or 45.0),
            )
            refreshed_facts = yield from self._wait_daofa_packet(
                runtime,
                protocol=(DAOFA_CHALLENGE_PROTOCOL_NAME, DAOFA_PLAY_INFO_PROTOCOL_NAME),
                before_packet_id={
                    DAOFA_CHALLENGE_PROTOCOL_NAME: str(before.get("packet_id") or ""),
                    DAOFA_PLAY_INFO_PROTOCOL_NAME: str(before_play_info_after_round.get("packet_id") or ""),
                },
                timeout=float(payload.get("packet_timeout") or 120.0),
                reason="daily-daofa-post-challenge",
            )
            completed_rounds += 1
            if refreshed_facts is None:
                remaining_after = self._daofa_remaining_from_ocr(runtime)
                if remaining_after == 0:
                    facts = {**facts, "rank": int(target["rank"]), "remain_times": 0, "targets": []}
                    self._log("warning", "道法争锋：新回包未及时入库，但 #376 OCR 已确认 0 次，按今日完成收尾")
                    continue
                retry_after = (
                    datetime.now() + timedelta(seconds=max(60, int(payload.get("retry_seconds") or 1800)))
                ).strftime("%Y-%m-%d %H:%M:%S")
                self._record_scheduler_task_discovered_retry_after(
                    str(payload.get("__scheduler_task_id") or "daily-daofa"),
                    retry_after,
                    task_type=task_type,
                    label=task_label,
                    last_result="skipped",
                )
                yield from self._leave_daofa_to_world(runtime)
                return {
                    "result": "skipped",
                    "message": f"挑战已完成但新目标回包未及时入库，{retry_after} 安全复查",
                    "current_scene": 34,
                }
            facts = refreshed_facts
            next_target = select_daofa_target(facts, battle_score=battle_score)
            if next_target is not None and int(facts.get("remain_times") or 0) > 0:
                self._log("detail", f"道法争锋：小循环完成，下一目标第 {next_target['rank']} 名「{next_target['name']}」")
