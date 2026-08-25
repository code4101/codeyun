from __future__ import annotations

"""Runtime-authoritative policy and bounded-batch planning for Xutian auto.

Production safety boundary
--------------------------
The game's native Xutian auto loop can hang the Android container and leave
ADB frames black.  A production batch must therefore monitor device health in
addition to Heaven Runtime progress.  Persistent black/ADB failure invalidates
the pre-restart GUI transaction and requires a full MuMu recovery.  The
``xutian_native_auto_started`` marker must survive that recovery: a retry may
only close the batch from exact Runtime completion plus a unique positive
wallet delta, and must never click Start again merely because the emulator was
restarted.  Device-health monitoring/recovery remains a required follow-up
before this job is considered production-complete.
"""

from dataclasses import asdict, dataclass
import re
import threading
import time
from typing import Any, Callable, Iterator, Mapping

from backend.core.fanxiu.data_annotation.tasks.yunmeng_native_auto import (
    YunmengNativeAutoAssets,
    YunmengNativeBatchPlan,
    _read_count,
    _set_count,
    plan_yunmeng_native_batch,
)


XUTIAN_NATIVE_AUTO_PROBE_CHALLENGES = 10
XUTIAN_MINIMUM_QUALITY_KEY = 6  # Quality 6 = 仙品
XUTIAN_PLAYER_QUALITY_KEY = 99
XUTIAN_QUALITY_8_SETTING_KEY = 15
XUTIAN_MAP_SCENE_ID = 614
XUTIAN_SETTINGS_SCENE_ID = 615
XUTIAN_ACTIVITY_SCENE_ID = 616
XUTIAN_ENTER_CONFIRM_SCENE_ID = 617
XUTIAN_TUTORIAL_SCENE_ID = 618
XUTIAN_NATIVE_AUTO_TASK_TYPE = "xutian_palace_native_auto"
XUTIAN_NATIVE_AUTO_TASK_ID = "xutian-palace-native-auto"
XUTIAN_NATIVE_AUTO_START_MARK = "xutian_native_auto_started"

_QUALITY_LABELS = {
    3: "上品怪物",
    4: "珍品怪物",
    5: "绝品怪物",
    6: "仙品怪物",
    7: "神品怪物",
    15: "圣品怪物",
}
_BOOST_FIELDS = {
    "use_item": "使用四倍符",
    "use_item_3": "使用封神令",
    "use_item_4": "使用斗战敕令",
}
_LOWER_SWITCH_LABELS = {
    "quick_auto": "开启快速自动挑战",
    "skip_animation": "跳过动画",
    "refill_challenge": "挑战体力不足自动使用虚天·万年灵液",
    "refill_explore": "探查体力不足自动使用虚天·探灵符",
}


@dataclass(frozen=True)
class XutianBatchObservation:
    requested_challenges: int
    completed_challenges: int
    currency_before: int
    currency_after: int
    currency_delta: int
    elapsed_seconds: float
    challenge_count_before: int
    challenge_count_after: int
    explore_count_before: int
    explore_count_after: int

    @property
    def currency_per_challenge(self) -> float:
        return self.currency_delta / self.completed_challenges

    @property
    def seconds_per_challenge(self) -> float:
        return self.elapsed_seconds / self.completed_challenges


def xutian_target_quality_keys(available_quality_keys: list[int]) -> set[int]:
    available = {int(value) for value in available_quality_keys}
    return {
        key for key in available
        if key != XUTIAN_PLAYER_QUALITY_KEY
        and (8 if key == XUTIAN_QUALITY_8_SETTING_KEY else key)
        >= XUTIAN_MINIMUM_QUALITY_KEY
    }


def validate_xutian_auto_settings(
    snapshot: Mapping[str, Any],
    *,
    requested_challenges: int,
) -> list[str]:
    """Return exact Runtime mismatches for the user's persistent policy."""

    requested = int(requested_challenges)
    if requested <= 0:
        raise ValueError("虚天自动挑战次数必须大于 0")
    settings = dict(snapshot.get("auto_settings") or {})
    raw = dict((snapshot.get("evidence") or {}).get("auto_settings_raw") or {})
    available = [int(value) for value in snapshot.get("available_quality_keys") or ()]
    if not settings or not raw or not available:
        return ["Runtime 自动配置快照不完整"]
    target_qualities = xutian_target_quality_keys(available)
    mismatches: list[str] = []
    for key in available:
        if key == XUTIAN_PLAYER_QUALITY_KEY:
            desired = False
            name = "quality_player"
        else:
            desired = key in target_qualities
            name = "quality_8" if key == XUTIAN_QUALITY_8_SETTING_KEY else f"quality_{key}"
        if settings.get(name) is not desired:
            mismatches.append(f"{name} 应为 {desired}")
        if desired:
            fields = dict(raw.get(str(key)) or {})
            for field in ("use_item", "use_item_3", "use_item_4"):
                if fields.get(field) is not True:
                    mismatches.append(f"{name}.{field} 应开启")
    special = dict(snapshot.get("special_options") or {})
    if special.get("find_demon_selected") is not False:
        mismatches.append("寻妖符应关闭")
    if special.get("native_soul_lock_selected") is not False:
        mismatches.append("本命魂锁应关闭")
    required_switches = {
        "refill_challenge": True,
        "refill_explore": True,
        "quick_auto": True,
        "skip_animation": True,
    }
    for name, desired in required_switches.items():
        if settings.get(name) is not desired:
            mismatches.append(f"{name} 应为 {desired}")
    if int(settings.get("challenge_count") or 0) != requested:
        mismatches.append(
            f"challenge_count 应为 {requested}，实际 {settings.get('challenge_count')!r}"
        )
    return mismatches


def plan_xutian_native_batch(
    *,
    required_new_currency: int,
    measured_currency_delta: int | None = None,
    measured_challenges: int | None = None,
    previous_currency_delta: int | None = None,
    previous_challenges: int | None = None,
) -> YunmengNativeBatchPlan:
    """Reuse the proven probe/geometric/stable-final planner from Yunmeng."""

    return plan_yunmeng_native_batch(
        required_new_currency=required_new_currency,
        measured_currency_delta=measured_currency_delta,
        measured_challenges=measured_challenges,
        previous_currency_delta=previous_currency_delta,
        previous_challenges=previous_challenges,
    )


def build_xutian_batch_observation(
    *,
    requested_challenges: int,
    before_resource: Mapping[str, Any],
    after_resource: Mapping[str, Any],
    currency_before: int,
    currency_after: int,
    elapsed_seconds: float,
) -> dict[str, Any]:
    before_progress = dict(before_resource.get("auto_progress") or {})
    after_progress = dict(after_resource.get("auto_progress") or {})
    completed = int(after_progress.get("completed_challenges") or 0)
    requested = int(requested_challenges)
    delta = int(currency_after) - int(currency_before)
    if requested <= 0 or completed != requested:
        raise ValueError(
            f"虚天批次完成次数不一致：requested={requested}, completed={completed}"
        )
    if bool(after_progress.get("running")):
        raise ValueError("虚天批次仍在运行，不能形成散点")
    if delta <= 0:
        raise ValueError("虚天批次纳元晶没有正向增长")
    if float(elapsed_seconds) <= 0:
        raise ValueError("虚天批次耗时无效")
    before_challenge = int((before_resource.get("challenge") or {}).get("count") or 0)
    after_challenge = int((after_resource.get("challenge") or {}).get("count") or 0)
    before_explore = int((before_resource.get("explore") or {}).get("count") or 0)
    after_explore = int((after_resource.get("explore") or {}).get("count") or 0)
    row = XutianBatchObservation(
        requested_challenges=requested,
        completed_challenges=completed,
        currency_before=int(currency_before),
        currency_after=int(currency_after),
        currency_delta=delta,
        elapsed_seconds=float(elapsed_seconds),
        challenge_count_before=before_challenge,
        challenge_count_after=after_challenge,
        explore_count_before=before_explore,
        explore_count_after=after_explore,
    )
    return {
        **asdict(row),
        "currency_per_challenge": row.currency_per_challenge,
        "seconds_per_challenge": row.seconds_per_challenge,
    }


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _read_auto_snapshot() -> dict[str, Any]:
    from backend.core.fanxiu.instrumentation.xutian_runtime import (
        read_xutian_auto_settings_snapshot,
    )

    return read_xutian_auto_settings_snapshot()


def _runtime_identity(snapshot: Mapping[str, Any]) -> tuple[int, int, int]:
    evidence = dict(snapshot.get("evidence") or {})
    identity = (
        int(evidence.get("pid") or 0),
        int(evidence.get("process_start_ticks") or 0),
        int(snapshot.get("current_heaven") or 0),
    )
    if min(identity) <= 0 or snapshot.get("source") != "runtime_memory":
        raise RuntimeError(f"虚天自动配置 Runtime 身份不完整：{identity!r}")
    return identity


def _wait_scene(
    runtime: Any,
    targets: tuple[int, ...],
    *,
    timeout_seconds: float,
) -> tuple[int, float, str]:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    last_scene: int | None = None
    last_score = 0.0
    last_frame = ""
    while time.monotonic() < deadline:
        last_scene, last_score, last_frame = runtime.current_scene(
            list(targets), update=True
        )
        if int(last_scene or 0) in targets and float(last_score) >= 80.0:
            return int(last_scene), float(last_score), last_frame
        time.sleep(0.25)
    text = runtime.ocr_text(last_frame) if last_frame else ""
    raise RuntimeError(
        f"等待虚天场景超时：targets={targets}, scene={last_scene}, "
        f"score={float(last_score):.1f}, ocr={text[:120]}"
    )


def _enter_xutian_map(runtime: Any) -> Iterator[Any]:
    from datetime import datetime

    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        select_schedule_activity,
    )

    yield from runtime.goto_view(66)
    yield from select_schedule_activity(
        runtime,
        r"虚天(殿)?",
        enter=True,
        require_runtime_alignment=True,
        now=datetime.now().astimezone(),
    )
    scene, _score, frame = _wait_scene(
        runtime,
        (XUTIAN_ACTIVITY_SCENE_ID, XUTIAN_MAP_SCENE_ID),
        timeout_seconds=30.0,
    )
    if scene == XUTIAN_ACTIVITY_SCENE_ID:
        runtime.click_shape_center(XUTIAN_ACTIVITY_SCENE_ID, "前往")
        _wait_scene(runtime, (XUTIAN_ENTER_CONFIRM_SCENE_ID,), timeout_seconds=15.0)
        # This confirmation may enter the map even if the following transition
        # animation is visually unknown.  It is authorized once and never
        # repeated from an unknown frame.
        runtime.click_shape_center(XUTIAN_ENTER_CONFIRM_SCENE_ID, "确认")
        scene, _score, frame = _wait_scene(
            runtime,
            (XUTIAN_MAP_SCENE_ID, XUTIAN_TUTORIAL_SCENE_ID),
            timeout_seconds=45.0,
        )
    if scene == XUTIAN_TUTORIAL_SCENE_ID:
        runtime.click_shape_center(XUTIAN_TUTORIAL_SCENE_ID, "点击空白关闭")
        _wait_scene(runtime, (XUTIAN_MAP_SCENE_ID,), timeout_seconds=15.0)


def _find_setting_label(
    runtime: Any,
    label: str,
    *,
    region: str,
    max_scrolls: int = 10,
) -> Iterator[Any]:
    match = yield from runtime.wait_ocr_text(
        XUTIAN_SETTINGS_SCENE_ID,
        label,
        in_shapes=(region,),
        timeout_seconds=20.0,
        poll_seconds=0.5,
        max_scrolls_per_direction=max_scrolls,
        search_direction="down",
        match_mode="fuzzy",
        min_similarity=82.0,
        ambiguity_margin=4.0,
        crop_fallback=True,
    )
    if match is None:
        raise RuntimeError(f"虚天自动设置未找到配置行：{label}")
    return match


def _click_checkbox_for_label(runtime: Any, match: Any) -> None:
    # The checked square is one text-height immediately left of every label in
    # the retained #615 real frame.  The OCR box authorizes the row; Runtime,
    # never the checkmark pixels, authorizes and verifies the state change.
    x, y = match.point(
        anchor="top_left",
        offset=(-1.0, 0.5),
        offset_unit="height",
    )
    runtime.click_frame_point(XUTIAN_SETTINGS_SCENE_ID, x, y)


def _reconcile_quality_toggle(
    runtime: Any,
    *,
    key: int,
    desired: bool,
    identity: tuple[int, int, int],
) -> Iterator[Any]:
    name = "quality_8" if key == 15 else f"quality_{key}"
    before = _read_auto_snapshot()
    if _runtime_identity(before) != identity:
        raise RuntimeError("虚天自动设置期间 Runtime 身份发生变化")
    if before["auto_settings"].get(name) is desired:
        return
    label = _QUALITY_LABELS[key]
    match = yield from _find_setting_label(
        runtime, label, region="上组配置滚动区"
    )
    _click_checkbox_for_label(runtime, match)
    yield from runtime.wait_action_settle(0.7)
    after = _read_auto_snapshot()
    if _runtime_identity(after) != identity:
        raise RuntimeError("虚天品质设置后 Runtime 身份发生变化")
    if after["auto_settings"].get(name) is not desired:
        raise RuntimeError(f"虚天品质「{label}」点击后 Runtime 未变为 {desired}")


def _find_boost_row_after_quality(runtime: Any, quality_label: str, boost_label: str) -> Any:
    from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens

    frame = runtime.cur_frame(update=True)
    quality = runtime.find_ocr_text(
        XUTIAN_SETTINGS_SCENE_ID,
        quality_label,
        in_shapes=("上组配置滚动区",),
        frame_data_url=frame,
        match_mode="fuzzy",
        min_similarity=82.0,
    )
    if quality is None:
        return None
    quality_y = quality.y + quality.h / 2
    candidates = []
    for fragment in group_ocr_tokens(runtime.full_frame_ocr_tokens(frame)):
        text = _compact(fragment.get("text") or "")
        center_y = float(fragment.get("y") or 0) + float(fragment.get("h") or 0) / 2
        if _compact(boost_label) == text and quality_y < center_y < quality_y + 220:
            candidates.append((center_y, fragment))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _reconcile_quality_boosts(
    runtime: Any,
    *,
    key: int,
    identity: tuple[int, int, int],
) -> Iterator[Any]:
    name = "quality_8" if key == 15 else f"quality_{key}"
    quality_label = _QUALITY_LABELS[key]
    for field, boost_label in _BOOST_FIELDS.items():
        before = _read_auto_snapshot()
        if _runtime_identity(before) != identity:
            raise RuntimeError("虚天增益设置期间 Runtime 身份发生变化")
        fields = dict((before.get("evidence") or {}).get("auto_settings_raw", {}).get(str(key)) or {})
        if before["auto_settings"].get(name) is not True:
            raise RuntimeError(f"虚天增益设置前品质「{quality_label}」并未开启")
        if fields.get(field) is True:
            continue

        # Reposition the quality row near the top of its own scroll pane so its
        # three child boost rows are visible together.  A fresh OCR row is
        # required after every scroll and every click.
        for _attempt in range(8):
            match = yield from _find_setting_label(
                runtime,
                quality_label,
                region="上组配置滚动区",
                max_scrolls=6,
            )
            row = _find_boost_row_after_quality(runtime, quality_label, boost_label)
            if row is not None:
                break
            changed = yield from runtime.scroll_shape_content(
                runtime.resolve_shape_selector(
                    runtime.view(XUTIAN_SETTINGS_SCENE_ID), "上组配置滚动区"
                ),
                direction="down",
            )
            if not changed:
                break
        else:
            row = None
        if row is None:
            raise RuntimeError(
                f"虚天品质「{quality_label}」未能可靠显示增益行「{boost_label}」"
            )
        center_y = float(row.get("y") or 0) + float(row.get("h") or 0) / 2
        # The #615 retained frame proves the right-hand “开” column center at
        # x≈700 for all three boost rows.  Runtime is re-read immediately after
        # the action; a mis-hit therefore fails closed instead of being trusted.
        runtime.click_frame_point(XUTIAN_SETTINGS_SCENE_ID, 700.0, center_y)
        yield from runtime.wait_action_settle(0.7)
        after = _read_auto_snapshot()
        after_fields = dict((after.get("evidence") or {}).get("auto_settings_raw", {}).get(str(key)) or {})
        if _runtime_identity(after) != identity or after_fields.get(field) is not True:
            raise RuntimeError(
                f"虚天品质「{quality_label}」增益「{boost_label}」Runtime 后验失败"
            )


def _reconcile_lower_switch(
    runtime: Any,
    *,
    name: str,
    desired: bool,
    identity: tuple[int, int, int],
) -> Iterator[Any]:
    before = _read_auto_snapshot()
    if _runtime_identity(before) != identity:
        raise RuntimeError("虚天下组设置期间 Runtime 身份发生变化")
    if before["auto_settings"].get(name) is desired:
        return
    match = yield from _find_setting_label(
        runtime,
        _LOWER_SWITCH_LABELS[name],
        region="下组配置滚动区",
        max_scrolls=6,
    )
    _click_checkbox_for_label(runtime, match)
    yield from runtime.wait_action_settle(0.7)
    after = _read_auto_snapshot()
    if (
        _runtime_identity(after) != identity
        or after["auto_settings"].get(name) is not desired
    ):
        raise RuntimeError(f"虚天下组开关「{_LOWER_SWITCH_LABELS[name]}」Runtime 后验失败")


def _configure_and_run_batch(
    runtime: Any,
    *,
    requested_challenges: int,
    stop_event: threading.Event,
    before_start: Callable[[Mapping[str, Any]], None] | None = None,
) -> Iterator[Any]:
    runtime.click_shape_center(XUTIAN_MAP_SCENE_ID, "自动挑战")
    _wait_scene(runtime, (XUTIAN_SETTINGS_SCENE_ID,), timeout_seconds=15.0)
    initial = _read_auto_snapshot()
    identity = _runtime_identity(initial)
    special = dict(initial.get("special_options") or {})
    if special.get("find_demon_selected") or special.get("native_soul_lock_selected"):
        raise RuntimeError("虚天特殊探查道具当前已选中，尚无安全关闭动作，拒绝启动")
    targets = xutian_target_quality_keys(initial["available_quality_keys"])
    for key in initial["available_quality_keys"]:
        if key == XUTIAN_PLAYER_QUALITY_KEY:
            if initial["auto_settings"].get("quality_player") is not False:
                raise RuntimeError("虚天玩家目标开关已开启但当前没有安全定位资产")
            continue
        yield from _reconcile_quality_toggle(
            runtime,
            key=key,
            desired=key in targets,
            identity=identity,
        )
    for key in sorted(targets):
        yield from _reconcile_quality_boosts(runtime, key=key, identity=identity)
    for name in _LOWER_SWITCH_LABELS:
        yield from _reconcile_lower_switch(
            runtime, name=name, desired=True, identity=identity
        )

    count_assets = YunmengNativeAutoAssets(
        home_scene_id=XUTIAN_MAP_SCENE_ID,
        settings_scene_id=XUTIAN_SETTINGS_SCENE_ID,
        terminal_scene_ids=(XUTIAN_MAP_SCENE_ID,),
    )
    yield from _set_count(
        runtime,
        count_assets,
        int(requested_challenges),
        max_adjustments=min(100, max(20, int(requested_challenges) // 5)),
    )
    final_settings = _read_auto_snapshot()
    mismatches = validate_xutian_auto_settings(
        final_settings,
        requested_challenges=int(requested_challenges),
    )
    if _runtime_identity(final_settings) != identity or mismatches:
        raise RuntimeError(f"虚天自动设置 Runtime 复验失败：{mismatches}")
    if _read_count(runtime, count_assets) != int(requested_challenges):
        raise RuntimeError("虚天挑战次数 GUI 与 Runtime 未对齐")
    if before_start is not None:
        before_start(final_settings)
    auto_started_at = time.monotonic()
    runtime.click_shape_center(XUTIAN_SETTINGS_SCENE_ID, "开启自动")

    deadline = time.monotonic() + max(60.0, int(requested_challenges) * 2.0)
    observed_running = False
    while time.monotonic() < deadline:
        if stop_event.is_set():
            raise InterruptedError()
        yield from runtime.wait_action_settle(0.5)
        progress = _read_auto_snapshot()
        if _runtime_identity(progress) != identity:
            raise RuntimeError("虚天自动挑战期间 Runtime 身份发生变化")
        state = dict(progress.get("auto_progress") or {})
        observed_running = observed_running or bool(state.get("running"))
        completed = int(state.get("completed_challenges") or 0)
        if not bool(state.get("running")) and completed == int(requested_challenges):
            _wait_scene(runtime, (XUTIAN_MAP_SCENE_ID,), timeout_seconds=15.0)
            terminal = dict(progress)
            terminal["batch_elapsed_seconds"] = time.monotonic() - auto_started_at
            return terminal
        if not bool(state.get("running")) and completed > int(requested_challenges):
            raise RuntimeError(
                f"虚天自动挑战完成次数越界：{completed}>{requested_challenges}"
            )
    raise RuntimeError(
        "虚天自动挑战 Runtime 终态超时："
        f"requested={requested_challenges}, observed_running={observed_running}"
    )


def _store_batch_observation(
    *,
    wallet_after: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> str:
    from datetime import date

    from sqlmodel import Session, select

    from backend.core.fanxiu.activity.standard_observation import (
        store_runtime_currency_fact,
    )
    from backend.db import engine
    from backend.models import FanxiuExchangeActivity

    with Session(engine) as session:
        store_runtime_currency_fact(session, dict(wallet_after))
        activity = session.exec(
            select(FanxiuExchangeActivity).where(
                FanxiuExchangeActivity.activity_type == "xutian-palace",
                FanxiuExchangeActivity.start_date <= date.today().isoformat(),
                FanxiuExchangeActivity.end_date >= date.today().isoformat(),
            )
        ).first()
        if activity is None:
            raise RuntimeError("虚天殿当前活动实例不存在，无法保存批次散点")
        evidence = dict(activity.evidence or {})
        batches = list(evidence.get("xutian_native_auto_batches") or [])
        batches.append(dict(observation))
        evidence["xutian_native_auto_batches"] = batches[-100:]
        activity.evidence = evidence
        session.add(activity)
        session.commit()
        return str(activity.id)


def execute_xutian_native_auto_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> Iterator[Any]:
    """Run one formally-owned native batch and persist its yield/time point."""

    from backend.core.fanxiu.instrumentation.wallet import (
        read_wallet_currency_snapshot,
    )
    from backend.core.fanxiu.instrumentation.xutian_runtime import (
        read_xutian_resource_snapshot,
    )

    requested = int(payload.get("requested_challenges") or XUTIAN_NATIVE_AUTO_PROBE_CHALLENGES)
    existing_mark = payload.get(XUTIAN_NATIVE_AUTO_START_MARK)
    if isinstance(existing_mark, dict):
        # Recovery always owns the exact already-authorized batch.  A later
        # run-now payload must not replace it with a fresh irreversible batch.
        requested = int(existing_mark.get("requested_challenges") or 0)
    if not 1 <= requested <= 500:
        raise ValueError("虚天原生自动挑战单批必须在 1..500 次内")
    wallet_before = read_wallet_currency_snapshot(12, allow_discovery=True)
    resource_before = read_xutian_resource_snapshot()
    runtime = runner._fanxiu_runtime(ctx)
    task_id = str(ctx.get("scheduler_task_id") or XUTIAN_NATIVE_AUTO_TASK_ID)
    if isinstance(existing_mark, dict):
        marked_requested = int(existing_mark.get("requested_challenges") or 0)
        progress = dict(resource_before.get("auto_progress") or {})
        if bool(progress.get("running")):
            deadline = time.monotonic() + max(60.0, requested * 2.0)
            while time.monotonic() < deadline:
                if stop_event.is_set():
                    raise InterruptedError()
                yield from runtime.wait_action_settle(0.5)
                current = _read_auto_snapshot()
                current_progress = dict(current.get("auto_progress") or {})
                if not bool(current_progress.get("running")):
                    resource_before = read_xutian_resource_snapshot()
                    progress = dict(resource_before.get("auto_progress") or {})
                    break
        if int(progress.get("completed_challenges") or 0) != requested:
            raise RuntimeError(
                "虚天自动挑战防重复标记存在，但 Runtime 未证明精确批次完成；"
                "保留标记和现场，禁止再次点击开启自动"
            )
        marked_wallet = dict(existing_mark.get("wallet_before") or {})
        current_delta = int(wallet_before["exchange_currency"]) - int(
            marked_wallet.get("exchange_currency") or 0
        )
        cumulative_delta = int(wallet_before["cumulative_currency"]) - int(
            marked_wallet.get("cumulative_currency") or 0
        )
        if current_delta <= 0 or current_delta != cumulative_delta:
            raise RuntimeError(
                "虚天自动挑战 Runtime 已完成，但绝对钱包未形成唯一正向增量；"
                "保留防重复标记，禁止重跑"
            )
        before_resource = dict(existing_mark.get("resource_before") or {})
        recovered_observation = build_xutian_batch_observation(
            requested_challenges=requested,
            before_resource=before_resource,
            after_resource=resource_before,
            currency_before=int(marked_wallet["exchange_currency"]),
            currency_after=int(wallet_before["exchange_currency"]),
            elapsed_seconds=max(
                0.001,
                time.time() - float(existing_mark.get("started_epoch") or time.time()),
            ),
        )
        activity_id = _store_batch_observation(
            wallet_after=wallet_before,
            observation=recovered_observation,
        )
        yield from runtime.goto_view(34)
        runner._clear_scheduler_task_payload_flag(
            task_id, XUTIAN_NATIVE_AUTO_START_MARK
        )
        runner._persist_scheduler_task_next_time(task_id, None)
        message = (
            f"虚天殿_自动挑战：从防重复标记恢复 {requested} 次批次，"
            f"纳元晶 +{recovered_observation['currency_delta']}，已回到世界"
        )
        return {
            "result": "success",
            "message": message,
            "activity_id": activity_id,
            "observation": recovered_observation,
            "recovered": True,
            "final_scene": 34,
        }
    yield from _enter_xutian_map(runtime)

    def persist_start_mark(final_settings: Mapping[str, Any]) -> None:
        marker = {
            "started_at": str(final_settings.get("captured_at") or ""),
            "started_epoch": time.time(),
            "requested_challenges": requested,
            "current_heaven": int(final_settings.get("current_heaven") or 0),
            "wallet_before": {
                "exchange_currency": int(wallet_before["exchange_currency"]),
                "cumulative_currency": int(wallet_before["cumulative_currency"]),
            },
            "resource_before": {
                "challenge": dict(resource_before.get("challenge") or {}),
                "explore": dict(resource_before.get("explore") or {}),
                "auto_progress": dict(resource_before.get("auto_progress") or {}),
            },
        }
        if not runner._set_scheduler_task_payload_flag(
            task_id,
            XUTIAN_NATIVE_AUTO_START_MARK,
            marker,
        ):
            raise RuntimeError("虚天自动挑战防重复标记未持久化，拒绝点击开启自动")
        payload[XUTIAN_NATIVE_AUTO_START_MARK] = marker

    terminal = yield from _configure_and_run_batch(
        runtime,
        requested_challenges=requested,
        stop_event=stop_event,
        before_start=persist_start_mark,
    )
    elapsed = float(terminal.get("batch_elapsed_seconds") or 0.0)
    wallet_after = read_wallet_currency_snapshot(12, allow_discovery=False)
    resource_after = read_xutian_resource_snapshot()
    observation = build_xutian_batch_observation(
        requested_challenges=requested,
        before_resource=resource_before,
        after_resource=resource_after,
        currency_before=int(wallet_before["exchange_currency"]),
        currency_after=int(wallet_after["exchange_currency"]),
        elapsed_seconds=elapsed,
    )
    activity_id = _store_batch_observation(
        wallet_after=wallet_after,
        observation=observation,
    )
    runner._clear_scheduler_task_payload_flag(task_id, XUTIAN_NATIVE_AUTO_START_MARK)
    payload.pop(XUTIAN_NATIVE_AUTO_START_MARK, None)
    yield from runtime.goto_view(34)
    scene, score, _frame = runtime.current_scene([34], update=True)
    if int(scene or 0) != 34 or float(score) < 90.0:
        raise RuntimeError(
            f"虚天自动挑战收尾未可靠回到 #34：scene={scene}, score={score}"
        )
    runner._persist_scheduler_task_next_time(task_id, None)
    message = (
        f"虚天殿_自动挑战：完成 {requested} 次，纳元晶 +{observation['currency_delta']}，"
        f"{observation['seconds_per_challenge']:.3f}秒/次，已回到世界"
    )
    runner._log("success", message)
    return {
        "result": "success",
        "message": message,
        "activity_id": activity_id,
        "observation": observation,
        "final_scene": 34,
    }


__all__ = [
    "XutianBatchObservation",
    "XUTIAN_NATIVE_AUTO_TASK_ID",
    "XUTIAN_NATIVE_AUTO_TASK_TYPE",
    "build_xutian_batch_observation",
    "execute_xutian_native_auto_job",
    "plan_xutian_native_batch",
    "validate_xutian_auto_settings",
    "xutian_target_quality_keys",
]
