from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from backend.core.fanxiu.data_annotation.job_times import next_business_time


PRAYER_MAIN_SCENE_ID = 455
PRAYER_STORE_SCENE_ID = 456
PRAYER_MIN_COMPLETE_SAMPLES = 2
PRAYER_MIN_OBSERVATION_SECONDS = 30.0
PRAYER_MAX_TASK_CLAIM_BATCHES = 12


def _normalized_ocr_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("：", ":")


def prayer_entry_fragment(fragments: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the unique left-menu prayer entry without trusting its weekly prefix."""

    candidates = [
        item
        for item in fragments
        if isinstance(item, dict)
        and _normalized_ocr_text(item.get("text")).endswith("祈愿")
        and 2 <= len(_normalized_ocr_text(item.get("text"))) <= 6
        and float(item.get("x") or 0) < 250
        and float(item.get("y") or 0) < 1200
        and float(item.get("w") or 0) > 0
        and float(item.get("h") or 0) > 0
    ]
    return candidates[0] if len(candidates) == 1 else None


def fragment_center(fragment: dict[str, Any]) -> tuple[float, float]:
    """Return the OCR box center used for OCR-relative clicks."""

    return (
        float(fragment["x"]) + float(fragment["w"]) / 2,
        float(fragment["y"]) + float(fragment["h"]) / 2,
    )


def prayer_entry_action_point(fragment: dict[str, Any]) -> tuple[float, float]:
    """Return the visual icon center immediately above the prayer OCR label."""

    center_x, center_y = fragment_center(fragment)
    return center_x, center_y - 2.2 * float(fragment["h"])


def prayer_entry_action_points(fragment: dict[str, Any]) -> tuple[tuple[float, float], ...]:
    """Bound click candidates to the icon/claim badge/label of one OCR-proved item."""

    center_x, center_y = fragment_center(fragment)
    width = float(fragment["w"])
    height = float(fragment["h"])
    icon_y = center_y - 2.2 * height
    return (
        (center_x, icon_y),
        (center_x + 0.3 * width, icon_y),
        (center_x, center_y),
    )


def prayer_enter_fragment(
    fragments: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick the unique left-side ``进入`` action shown after selecting prayer."""

    candidates = [
        item
        for item in fragments
        if isinstance(item, dict)
        and _normalized_ocr_text(item.get("text")) == "进入"
        and float(item.get("x") or 0) < 300
        and 500 <= float(item.get("y") or 0) <= 900
        and float(item.get("w") or 0) > 0
        and float(item.get("h") or 0) > 0
    ]
    return candidates[0] if len(candidates) == 1 else None


def exact_ocr_fragment(
    fragments: Iterable[dict[str, Any]],
    target: str,
) -> dict[str, Any] | None:
    normalized_target = _normalized_ocr_text(target)
    candidates = [
        item
        for item in fragments
        if isinstance(item, dict)
        and _normalized_ocr_text(item.get("text")) == normalized_target
        and float(item.get("w") or 0) > 0
        and float(item.get("h") or 0) > 0
    ]
    return candidates[0] if len(candidates) == 1 else None


def prayer_store_tab_fragment(
    fragments: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick only the lower-right full ``祈愿商店`` tab on the prayer main page."""

    candidates = [
        item
        for item in fragments
        if isinstance(item, dict)
        and _normalized_ocr_text(item.get("text")) == "祈愿商店"
        and float(item.get("x") or 0) >= 600
        and float(item.get("y") or 0) >= 1000
        and float(item.get("w") or 0) > 0
        and float(item.get("h") or 0) > 0
    ]
    return candidates[0] if len(candidates) == 1 else None


def prayer_task_tab_fragment(
    fragments: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick only the lower task tab on the prayer page."""

    candidates = [
        item
        for item in fragments
        if isinstance(item, dict)
        and _normalized_ocr_text(item.get("text")) == "祈愿任务"
        and 450 <= float(item.get("x") or 0) <= 700
        and float(item.get("y") or 0) >= 1200
        and float(item.get("w") or 0) > 0
        and float(item.get("h") or 0) > 0
    ]
    return candidates[0] if len(candidates) == 1 else None


def prayer_task_one_key_fragment(
    fragments: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """Locate the task-page one-key button despite its animated first glyph."""

    candidates = [
        item
        for item in fragments
        if isinstance(item, dict)
        and _normalized_ocr_text(item.get("text")).endswith("键领取")
        and 250 <= float(item.get("x") or 0) <= 650
        and 1100 <= float(item.get("y") or 0) <= 1350
        and float(item.get("w") or 0) > 0
        and float(item.get("h") or 0) > 0
    ]
    return candidates[0] if len(candidates) == 1 else None


def prayer_new_round_confirm_visible(fragments: Iterable[dict[str, Any]]) -> bool:
    """Require both the rollover statement and its unique confirm action."""

    items = list(fragments)
    text = _normalized_ocr_text(_fragment_text(items))
    statement_matched = (
        "领取完本轮" in text and "开启新一轮" in text
    ) or (
        # Current task ROI consistently drops the leading glyphs and reads
        # 奖励/开启 as 关励/开后 on the real overlay.  The four independent
        # structural tokens remain unique to the rollover statement.
        "本轮次" in text and "新一轮" in text and "任务" in text
    )
    return (
        statement_matched
        and exact_ocr_fragment(items, "确认") is not None
    )


def prayer_task_state(
    fragments: Iterable[dict[str, Any]],
    *,
    task_context_confirmed: bool = False,
) -> str:
    """Classify task rewards without treating the persistent one-key button as claimable."""

    items = list(fragments)
    if (
        not task_context_confirmed and prayer_task_tab_fragment(items) is None
    ) or prayer_task_one_key_fragment(items) is None:
        return "loading"
    claimable_rows = [
        item
        for item in items
        if isinstance(item, dict)
        and _normalized_ocr_text(item.get("text")) == "领取"
        and float(item.get("x") or 0) >= 600
        and 700 <= float(item.get("y") or 0) <= 1250
    ]
    return "claimable" if claimable_rows else "settled"


def prayer_store_state(
    fragments: Iterable[dict[str, Any]],
    full_text: str,
    *,
    store_context_confirmed: bool = False,
) -> str:
    """Classify a fully rendered store without treating an animation as done."""

    items = list(fragments)
    if exact_ocr_fragment(items, "免费") is not None:
        return "claimable"
    text = _normalized_ocr_text(full_text)
    has_store_identity = bool(store_context_confirmed) or exact_ocr_fragment(items, "祈愿商店") is not None
    has_loaded_goods = "每日限购" in text or ("适度娱乐" in text and "理性消费" in text)
    if has_store_identity and has_loaded_goods:
        # After claiming, the game removes the whole free-gift card instead of
        # retaining a card marked 每日限购：0.
        return "claimed"
    return "loading"


def prayer_reward_overlay_dismissed(full_text: str, *, reward_seen: bool) -> bool:
    """Treat a post-action reward receipt as authoritative once its overlay closes.

    Closing the receipt can click through to another tab in the shared prayer
    shell, so the underlying frame is not required to remain on the store tab.
    """

    return reward_seen and "恭喜获得" not in _normalized_ocr_text(full_text)


def prayer_page_state(
    scene_id: int | None,
    fragments: Iterable[dict[str, Any]],
    full_text: str,
) -> str:
    """Identify only the three pages that this task can safely operate on."""

    items = list(fragments)
    if scene_id == 34:
        return "world"
    if exact_ocr_fragment(items, "祈愿任务") is not None:
        return "main"
    if exact_ocr_fragment(items, "祈愿商店") is not None:
        if scene_id == PRAYER_MAIN_SCENE_ID:
            return "main"
        if scene_id == PRAYER_STORE_SCENE_ID or prayer_store_state(items, full_text) != "loading":
            return "store"
    return "unknown"


def _click_fragment_center(runtime: Any, scene_id: int, fragment: dict[str, Any]) -> None:
    runtime.click_frame_point(scene_id, *fragment_center(fragment))


def _prayer_task_fragments(runtime: Any, frame: str) -> list[dict[str, Any]]:
    """Run fresh crop OCR only over #455's task business regions.

    The caller has already entered the task page, so the animated vertical tab
    title is not repeated as a state gate here.  It is both expensive and less
    stable than the actual reward rows and one-key action.
    """

    return runtime.ocr_fragments_in_shapes(
        PRAYER_MAIN_SCENE_ID,
        ("任务奖励", "一键领取"),
        padding=0,
        frame_data_url=frame,
        crop=True,
    )


def _prayer_store_fragments(runtime: Any, frame: str) -> list[dict[str, Any]]:
    """Run fresh crop OCR over #456's goods/actions, not the full-frame cache."""

    return runtime.ocr_fragments_in_shapes(
        PRAYER_STORE_SCENE_ID,
        ("免费祈愿礼包", "免费", "商品列表"),
        padding=0,
        frame_data_url=frame,
        crop=True,
    )


def _fragment_text(fragments: Iterable[dict[str, Any]]) -> str:
    return " ".join(str(item.get("text") or "") for item in fragments if isinstance(item, dict))


def _prayer_task_content_text(fragments: Iterable[dict[str, Any]]) -> str:
    """Build a progress fingerprint without the animated one-key button text."""

    items = list(fragments)
    one_key = prayer_task_one_key_fragment(items)
    return _normalized_ocr_text(
        _fragment_text(item for item in items if item is not one_key)
    )


@dataclass
class _PrayerStabilityWindow:
    """Bound a state wait without letting one slow OCR consume all evidence time.

    A synchronous OCR call cannot be interrupted safely.  Therefore the wall
    clock deadline is checked only after at least two *completed* observations.
    The normal failure budget is still bounded to at least 30 seconds (or twice
    the caller timeout), while a successful two-frame state returns immediately.
    """

    timeout_seconds: float
    accepted_states: frozenset[str] | None = None
    started_at: float = field(default_factory=time.monotonic)
    stable_state: str = ""
    stable_count: int = 0
    sample_count: int = 0
    deadline: float = field(init=False)

    def __post_init__(self) -> None:
        self.deadline = self.started_at + max(
            PRAYER_MIN_OBSERVATION_SECONDS,
            2.0 * max(1.0, float(self.timeout_seconds)),
        )

    def should_sample(self) -> bool:
        return self.sample_count < PRAYER_MIN_COMPLETE_SAMPLES or time.monotonic() < self.deadline

    def observe(self, state: str) -> int:
        self.sample_count += 1
        accepted = state != "loading" and (
            self.accepted_states is None or state in self.accepted_states
        )
        if accepted and state == self.stable_state:
            self.stable_count += 1
        elif accepted:
            self.stable_state = state
            self.stable_count = 1
        else:
            self.stable_state = state
            self.stable_count = 0
        return self.stable_count

    @property
    def stable(self) -> bool:
        return self.stable_count >= PRAYER_MIN_COMPLETE_SAMPLES


def _log_prayer_observation(
    owner: Any,
    *,
    phase: str,
    scene_id: int,
    state: str,
    text: str,
    tracker: _PrayerStabilityWindow,
    sample_started_at: float,
) -> None:
    now = time.monotonic()
    owner._log(
        "detail",
        (
            f"祈愿_每日资源：观测 phase={phase} scene=#{scene_id} state={state} "
            f"text={_normalized_ocr_text(text)[:120]!r} "
            f"timing=elapsed:{now - tracker.started_at:.2f}s/sample:{now - sample_started_at:.2f}s "
            f"stable_count={tracker.stable_count} sample={tracker.sample_count}"
        ),
    )


class PrayerDailyResourceTaskMixin:
    def _prayer_daily_result(
        self,
        payload: dict[str, Any],
        *,
        outcome: str,
        message: str,
    ) -> dict[str, Any]:
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "").strip()
        if scheduler_task_id:
            next_time = next_business_time(("00:00",))
            self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
            message = f"{message}，下次 {next_time}"
        return {
            "result": "success",
            "outcome": outcome,
            "message": message,
            "current_scene": 34,
        }

    def _wait_prayer_store_tab(
        self,
        runtime: Any,
        stop_event: threading.Event,
        *,
        timeout_seconds: float,
    ):
        deadline = time.monotonic() + max(0.5, float(timeout_seconds))
        last_text = ""
        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            frame = runtime.cur_frame(update=True)
            fragments = runtime.ocr_fragments(frame)
            match = prayer_store_tab_fragment(fragments)
            if match is not None:
                return frame, fragments, match
            last_text = runtime.ocr_text(frame)
            yield from runtime.wait_action_settle(0.15)
        raise RuntimeError(
            f"祈愿_每日资源：等待右下角完整“祈愿商店”页签超时，拒绝点击左上祈愿，末帧={last_text[:160]}"
        )

    def _enter_prayer_from_world(
        self,
        runtime: Any,
        stop_event: threading.Event,
        *,
        timeout_seconds: float,
    ):
        """Complete the selected-card -> enter-button -> prayer-page transition."""

        deadline = time.monotonic() + max(0.5, float(timeout_seconds))
        enter_clicked = False
        last_text = ""
        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            frame = runtime.cur_frame(update=True)
            fragments = runtime.ocr_fragments(frame)
            store_tab = prayer_store_tab_fragment(fragments)
            if store_tab is not None:
                return frame, fragments, store_tab
            if not enter_clicked:
                enter = prayer_enter_fragment(fragments)
                if enter is not None:
                    _click_fragment_center(runtime, 34, enter)
                    enter_clicked = True
                    yield from runtime.wait_action_settle(0.35)
                    continue
            last_text = runtime.ocr_text(frame)
            yield from runtime.wait_action_settle(0.15)
        step = "已点击‘进入’" if enter_clicked else "未唯一识别到‘进入’"
        raise RuntimeError(
            f"祈愿_每日资源：选中祈愿活动后{step}，仍未进入祈愿主页，末帧={last_text[:160]}"
        )

    def _claim_prayer_task_rewards(
        self,
        runtime: Any,
        stop_event: threading.Event,
        fragments: Iterable[dict[str, Any]],
        *,
        timeout_seconds: float,
    ):
        task_tab = prayer_task_tab_fragment(fragments)
        if task_tab is None:
            raise RuntimeError("祈愿_每日资源：祈愿主页未唯一识别到右下角“祈愿任务”页签")
        _click_fragment_center(runtime, PRAYER_MAIN_SCENE_ID, task_tab)

        tracker = _PrayerStabilityWindow(timeout_seconds)
        one_key: dict[str, Any] | None = None
        while tracker.should_sample():
            self._raise_if_stopped(stop_event)
            sample_started_at = time.monotonic()
            frame = runtime.cur_frame(update=True)
            items = _prayer_task_fragments(runtime, frame)
            state = prayer_task_state(items, task_context_confirmed=True)
            tracker.observe(state)
            _log_prayer_observation(
                self,
                phase="task_ready",
                scene_id=PRAYER_MAIN_SCENE_ID,
                state=state,
                text=_fragment_text(items),
                tracker=tracker,
                sample_started_at=sample_started_at,
            )
            if tracker.stable:
                one_key = prayer_task_one_key_fragment(items)
                break
            yield from runtime.wait_action_settle(0.35)
        if not tracker.stable:
            raise RuntimeError("祈愿_每日资源：祈愿任务页未形成连续两帧稳定业务状态")
        if tracker.stable_state == "settled":
            return "already_settled"
        if one_key is None:
            raise RuntimeError("祈愿_每日资源：存在可领取任务，但未唯一识别到“一键领取”")

        claim_batches = 0
        while one_key is not None and claim_batches < PRAYER_MAX_TASK_CLAIM_BATCHES:
            before_text = _prayer_task_content_text(items)
            claim_batches += 1
            _click_fragment_center(runtime, PRAYER_MAIN_SCENE_ID, one_key)
            tracker = _PrayerStabilityWindow(
                timeout_seconds,
                accepted_states=frozenset({"claimable", "settled"}),
            )
            next_one_key: dict[str, Any] | None = None
            new_round_confirmed = False
            while tracker.should_sample():
                self._raise_if_stopped(stop_event)
                sample_started_at = time.monotonic()
                frame = runtime.cur_frame(update=True)
                items = _prayer_task_fragments(runtime, frame)
                if prayer_new_round_confirm_visible(items):
                    if new_round_confirmed:
                        raise RuntimeError("祈愿_每日资源：确认开启新一轮后弹窗仍未关闭")
                    self._log("action", "祈愿_每日资源：本轮奖励已领完，确认开启新一轮奖励任务")
                    yield from runtime.wait_click(PRAYER_MAIN_SCENE_ID, "新一轮确认")
                    yield from runtime.wait_action_settle(0.8)
                    new_round_confirmed = True
                    continue
                raw_state = prayer_task_state(items, task_context_confirmed=True)
                current_text = _prayer_task_content_text(items)
                # 点击后的旧帧仍可能短暂显示“可领取”。只有业务文本已经变化，
                # 才把它认作下一领取批次，避免对未生效的同一按钮重复点击。
                state = (
                    "loading"
                    if raw_state == "claimable" and current_text == before_text
                    else raw_state
                )
                tracker.observe(state)
                _log_prayer_observation(
                    self,
                    phase=f"task_claim_verify batch={claim_batches} effective={state}",
                    scene_id=PRAYER_MAIN_SCENE_ID,
                    state=raw_state,
                    text=_fragment_text(items),
                    tracker=tracker,
                    sample_started_at=sample_started_at,
                )
                if tracker.stable:
                    if tracker.stable_state == "settled":
                        return "claimed"
                    next_one_key = prayer_task_one_key_fragment(items)
                    break
                yield from runtime.wait_action_settle(0.35)
            if not tracker.stable:
                raise RuntimeError(
                    "祈愿_每日资源：点击“一键领取”后未观察到结算或新的可领取批次"
                )
            if next_one_key is None:
                raise RuntimeError(
                    "祈愿_每日资源：出现新的可领取批次，但未唯一识别到“一键领取”"
                )
            one_key = next_one_key

        raise RuntimeError(
            f"祈愿_每日资源：连续领取达到安全上限 {PRAYER_MAX_TASK_CLAIM_BATCHES} 批后仍有可领取任务"
        )

    def _execute_prayer_daily_resource_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        payload = dict(payload or {})
        runtime = self._fanxiu_runtime(
            ctx,
            ctx["asset_tree_path"],
            stop_event=stop_event,
        )
        entry_timeout = float(payload.get("entry_timeout_seconds") or 60.0)
        page_timeout = float(payload.get("page_timeout_seconds") or 12.0)

        current_scene, _score, current_frame = runtime.current_scene(
            [34, 69, 194, 449, PRAYER_MAIN_SCENE_ID, PRAYER_STORE_SCENE_ID],
            update=True,
        )
        # A confirmed new-round overlay legitimately hides every #455 scene
        # identity anchor.  Recover this one proven overlay before applying the
        # normal safe-scene gate, then re-identify the underlying page.
        # Scene identification may return its last candidate frame when every
        # identity is occluded.  Take one explicit fresh frame for overlay
        # recovery so the prompt check is about the current screen.
        if current_scene is None:
            current_frame = runtime.cur_frame(update=True)
        current_fragments = runtime.ocr_fragments(current_frame)
        overlay_fragments = current_fragments
        if current_scene is None and not overlay_fragments:
            # Full-frame OCR is intentionally scene-cache based and can be
            # empty while an overlay hides every identity.  The #455 task ROIs
            # cover both this prompt sentence and its confirm action and can be
            # evaluated directly against the fresh frame without asserting the
            # obscured scene identity.
            overlay_fragments = _prayer_task_fragments(runtime, current_frame)
        if current_scene is None and prayer_new_round_confirm_visible(overlay_fragments):
            self._log("action", "祈愿_每日资源：任务入口检测到新一轮确认弹窗，先恢复 #455")
            yield from runtime.wait_click(PRAYER_MAIN_SCENE_ID, "新一轮确认")
            yield from runtime.wait_view(
                PRAYER_MAIN_SCENE_ID,
                timeout=page_timeout,
                label="祈愿_每日资源：确认新一轮后恢复祈愿主页 #455",
            )
            current_scene, _score, current_frame = runtime.current_scene(
                [PRAYER_MAIN_SCENE_ID],
                update=True,
            )
            current_fragments = runtime.ocr_fragments(current_frame)
        if current_scene in {69, 194}:
            # #194 can be left behind by an older, incorrect world-side
            # ``进入`` click.  Both scenes have proven graph routes back to the
            # stable world anchor, so recover before opening the daily entry.
            yield from runtime.goto_view(34)
            current_scene, _score, current_frame = runtime.current_scene(
                [34, 449, PRAYER_MAIN_SCENE_ID, PRAYER_STORE_SCENE_ID],
                update=True,
            )
        page_state = prayer_page_state(
            current_scene,
            current_fragments,
            runtime.ocr_text(current_frame),
        )
        if page_state == "unknown":
            raise RuntimeError(
                f"祈愿_每日资源：当前场景 #{current_scene} 不是可安全恢复的 #34/#455/#456，拒绝点击；"
                f"入口OCR={_normalized_ocr_text(_fragment_text(overlay_fragments))[:240]}"
            )

        task_result = "not_checked"
        if page_state == "world":
            entry_deadline = time.monotonic() + max(0.5, entry_timeout)
            entry: dict[str, Any] | None = None
            while time.monotonic() < entry_deadline:
                self._raise_if_stopped(stop_event)
                world_frame = runtime.cur_frame(update=True)
                entry = prayer_entry_fragment(runtime.ocr_fragments(world_frame))
                if entry is not None:
                    break
                yield from runtime.wait_action_settle(0.5)
            if entry is None:
                raise RuntimeError("祈愿_每日资源：可靠 #34 左侧菜单未唯一识别到祈愿活动")

            entry_error: Exception | None = None
            for point in prayer_entry_action_points(entry):
                runtime.click_frame_point(34, *point)
                try:
                    _frame, main_fragments, store_tab = yield from self._wait_prayer_store_tab(
                        runtime,
                        stop_event,
                        timeout_seconds=min(5.0, page_timeout),
                    )
                    break
                except RuntimeError as exc:
                    entry_error = exc
                    scene_id, _score, _frame = runtime.current_scene(
                        [34, 194, PRAYER_MAIN_SCENE_ID, PRAYER_STORE_SCENE_ID],
                        update=True,
                    )
                    if scene_id in {PRAYER_MAIN_SCENE_ID, PRAYER_STORE_SCENE_ID}:
                        _frame, main_fragments, store_tab = yield from self._wait_prayer_store_tab(
                            runtime,
                            stop_event,
                            timeout_seconds=page_timeout,
                        )
                        break
                    if scene_id != 34:
                        raise RuntimeError(
                            f"祈愿_每日资源：点击祈愿活动候选后误入 #{scene_id}，停止后续点击"
                        ) from exc
            else:
                raise RuntimeError(
                    "祈愿_每日资源：OCR 已定位祈愿活动，但限定的图标/角标/标签点击均未打开主页"
                ) from entry_error
            page_state = "main"
        elif page_state == "main":
            main_fragments = current_fragments
            store_tab = prayer_store_tab_fragment(current_fragments)
            if store_tab is None:
                _frame, main_fragments, store_tab = yield from self._wait_prayer_store_tab(
                    runtime,
                    stop_event,
                    timeout_seconds=page_timeout,
                )

        if page_state == "main":
            task_result = yield from self._claim_prayer_task_rewards(
                runtime,
                stop_event,
                main_fragments,
                timeout_seconds=page_timeout,
            )
            _frame, _fragments, store_tab = yield from self._wait_prayer_store_tab(
                runtime,
                stop_event,
                timeout_seconds=page_timeout,
            )
            _click_fragment_center(runtime, PRAYER_MAIN_SCENE_ID, store_tab)

        tracker = _PrayerStabilityWindow(page_timeout)
        free: dict[str, Any] | None = None
        while tracker.should_sample():
            self._raise_if_stopped(stop_event)
            sample_started_at = time.monotonic()
            store_frame = runtime.cur_frame(update=True)
            store_fragments = _prayer_store_fragments(runtime, store_frame)
            # 进入此循环前已经由 scene #456 或“祈愿商店”身份确认了事务
            # 上下文；竖排页签 OCR 不应成为商品区“每日限购/售罄”的重复门禁。
            state = prayer_store_state(
                store_fragments,
                _fragment_text(store_fragments),
                store_context_confirmed=True,
            )
            tracker.observe(state)
            _log_prayer_observation(
                self,
                phase="store_ready",
                scene_id=PRAYER_STORE_SCENE_ID,
                state=state,
                text=_fragment_text(store_fragments),
                tracker=tracker,
                sample_started_at=sample_started_at,
            )
            if tracker.stable:
                free = exact_ocr_fragment(store_fragments, "免费")
                break
            yield from runtime.wait_action_settle(0.35)
        if not tracker.stable:
            raise RuntimeError("祈愿_每日资源：祈愿商店未形成连续两帧稳定业务状态")

        if tracker.stable_state == "claimed":
            runtime.click_shape_center(PRAYER_STORE_SCENE_ID, "返回")
            yield from runtime.wait_view(34, timeout=page_timeout, label="祈愿_每日资源：返回世界 #34")
            return self._prayer_daily_result(
                payload,
                outcome=("task_claimed" if task_result == "claimed" else "already_claimed"),
                message=(
                    "祈愿_每日资源："
                    + ("祈愿任务奖励已一键领取；" if task_result == "claimed" else "祈愿任务当前无可领取奖励；")
                    + "今日免费礼包已领取"
                ),
            )
        if free is None:
            raise RuntimeError(
                "祈愿_每日资源：商店已确认，但既无唯一“免费”按钮，也未显示每日限购 0"
            )

        _click_fragment_center(runtime, PRAYER_STORE_SCENE_ID, free)

        reward_seen = False
        claimed = False
        tracker = _PrayerStabilityWindow(
            page_timeout,
            accepted_states=frozenset({"claimed"}),
        )
        while tracker.should_sample():
            self._raise_if_stopped(stop_event)
            sample_started_at = time.monotonic()
            frame = runtime.cur_frame(update=True)
            fragments = runtime.ocr_fragments(frame)
            text = _normalized_ocr_text(runtime.ocr_text(frame))
            if "恭喜获得" in text:
                reward_seen = True
                continue_button = exact_ocr_fragment(fragments, "点击屏幕继续")
                if continue_button is not None:
                    _click_fragment_center(runtime, PRAYER_STORE_SCENE_ID, continue_button)
                    yield from runtime.wait_action_settle(0.8)
                else:
                    yield from runtime.wait_action_settle(0.4)
                continue
            if prayer_reward_overlay_dismissed(text, reward_seen=reward_seen):
                # “恭喜获得”是本次点击后的直接收货凭证。奖励层关闭时，
                # 底部“点击屏幕继续”可能穿透到祈愿的其他页签；此时不再
                # 强求底层仍是商店商品区，否则会把炼体/天魂文案误判为加载中。
                claimed = True
                break
            store_fragments = _prayer_store_fragments(runtime, frame)
            state = prayer_store_state(
                store_fragments,
                _fragment_text(store_fragments),
                store_context_confirmed=True,
            )
            tracker.observe(state)
            _log_prayer_observation(
                self,
                phase="store_claim_verify",
                scene_id=PRAYER_STORE_SCENE_ID,
                state=state,
                text=_fragment_text(store_fragments),
                tracker=tracker,
                sample_started_at=sample_started_at,
            )
            if tracker.stable:
                claimed = True
                break
            yield from runtime.wait_action_settle(0.35)
        if not claimed:
            raise RuntimeError(
                "祈愿_每日资源：点击免费礼包后未连续确认商品区已加载且免费动作消失"
            )

        runtime.click_shape_center(PRAYER_STORE_SCENE_ID, "返回")
        yield from runtime.wait_view(34, timeout=page_timeout, label="祈愿_每日资源：领取后返回世界 #34")
        return self._prayer_daily_result(
            payload,
            outcome="claimed",
            message=(
                "祈愿_每日资源："
                + ("祈愿任务奖励已一键领取；" if task_result == "claimed" else "祈愿任务当前无可领取奖励；")
                + "免费礼包已领取并确认每日限购 0"
                + ("，奖励过场已确认" if reward_seen else "")
            ),
        )


__all__ = [
    "PRAYER_MAIN_SCENE_ID",
    "PRAYER_STORE_SCENE_ID",
    "PrayerDailyResourceTaskMixin",
    "exact_ocr_fragment",
    "fragment_center",
    "_prayer_task_fragments",
    "_prayer_store_fragments",
    "prayer_entry_action_point",
    "prayer_entry_action_points",
    "prayer_enter_fragment",
    "prayer_entry_fragment",
    "prayer_page_state",
    "prayer_task_one_key_fragment",
    "prayer_task_state",
    "prayer_task_tab_fragment",
    "prayer_store_tab_fragment",
    "prayer_store_state",
    "prayer_reward_overlay_dismissed",
]
