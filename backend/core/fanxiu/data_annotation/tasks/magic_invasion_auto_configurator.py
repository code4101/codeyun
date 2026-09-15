from __future__ import annotations

"""Runtime-aligned GUI reconciliation for 魔道入侵 native auto-exorcism."""

from collections.abc import Callable, Iterator, Mapping
from difflib import SequenceMatcher
from typing import Any

from pyxllib.autogui import frame_size

from backend.core.fanxiu.data_annotation.tasks.magic_invasion_auto_config import (
    AUTO_USE_EXORCISM_ORDER,
    ELDER_CHASE_CHAIN,
    ELDER_QUADRUPLE_MERIT,
    EXTRATERRESTRIAL_DEMON_CHASE_CHAIN,
    EXTRATERRESTRIAL_DEMON_QUADRUPLE_MERIT,
    FAST_EXORCISM,
    QUALITY_ELDER,
    QUALITY_EXTRATERRESTRIAL_DEMON,
    QUALITY_HALL_MASTER,
    QUALITY_SAINT_MASTER,
    QUALITY_SECT_MASTER,
    QUALITY_SUPREME_ELDER,
    SAINT_MASTER_CHASE_CHAIN,
    SAINT_MASTER_QUADRUPLE_MERIT,
    SECT_MASTER_CHASE_CHAIN,
    SECT_MASTER_QUADRUPLE_MERIT,
    SKIP_ANIMATION,
    SUPREME_ELDER_CHASE_CHAIN,
    SUPREME_ELDER_QUADRUPLE_MERIT,
    TENFOLD_EXORCISM,
    plan_magic_invasion_auto_from_runtime,
)


MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID = 698
MAGIC_INVASION_QUALITY_SCROLL_SHAPE = "品质配置滑窗"
MAGIC_INVASION_START_AUTO_SHAPE = "开启自动"

# These are formal asset titles, not OCR-derived coordinates.  Quality-row
# Shapes must carry a visual condition so the configurator can prove that a
# row is in the current scroll viewport before clicking its fixed asset center.
MAGIC_INVASION_AUTO_SHAPE_BY_KEY: dict[str, str] = {
    QUALITY_HALL_MASTER: "堂主",
    QUALITY_ELDER: "长老",
    ELDER_QUADRUPLE_MERIT: "长老·四倍功勋符",
    ELDER_CHASE_CHAIN: "长老·追命索",
    QUALITY_SECT_MASTER: "宗主",
    SECT_MASTER_QUADRUPLE_MERIT: "宗主·四倍功勋符",
    SECT_MASTER_CHASE_CHAIN: "宗主·追命索",
    QUALITY_SUPREME_ELDER: "太上长老",
    SUPREME_ELDER_QUADRUPLE_MERIT: "太上长老·四倍功勋符",
    SUPREME_ELDER_CHASE_CHAIN: "太上长老·追命索",
    QUALITY_SAINT_MASTER: "圣主",
    SAINT_MASTER_QUADRUPLE_MERIT: "圣主·四倍功勋符",
    SAINT_MASTER_CHASE_CHAIN: "圣主·追命索",
    QUALITY_EXTRATERRESTRIAL_DEMON: "天外魔神",
    EXTRATERRESTRIAL_DEMON_QUADRUPLE_MERIT: "天外魔神·四倍功勋符",
    EXTRATERRESTRIAL_DEMON_CHASE_CHAIN: "天外魔神·追命索",
    AUTO_USE_EXORCISM_ORDER: "自动使用除魔令",
    SKIP_ANIMATION: "跳过动画",
    FAST_EXORCISM: "快速除魔",
    TENFOLD_EXORCISM: "十连除魔",
}

_QUALITY_KEYS = frozenset(
    {
        QUALITY_HALL_MASTER,
        QUALITY_ELDER,
        ELDER_QUADRUPLE_MERIT,
        ELDER_CHASE_CHAIN,
        QUALITY_SECT_MASTER,
        SECT_MASTER_QUADRUPLE_MERIT,
        SECT_MASTER_CHASE_CHAIN,
        QUALITY_SUPREME_ELDER,
        SUPREME_ELDER_QUADRUPLE_MERIT,
        SUPREME_ELDER_CHASE_CHAIN,
        QUALITY_SAINT_MASTER,
        SAINT_MASTER_QUADRUPLE_MERIT,
        SAINT_MASTER_CHASE_CHAIN,
        QUALITY_EXTRATERRESTRIAL_DEMON,
        EXTRATERRESTRIAL_DEMON_QUADRUPLE_MERIT,
        EXTRATERRESTRIAL_DEMON_CHASE_CHAIN,
    }
)

# Quality rows live inside the scrollable pane, so their vertical position is
# only valid for the current scroll state and cannot be stored as one asset
# per state.  The row is therefore located at runtime by OCR on its label, and
# only the pane's own column geometry stays fixed here (900x1600 frame).
_QUALITY_PANE_SHAPE = MAGIC_INVASION_QUALITY_SCROLL_SHAPE
_QUALITY_ROW_CHECKBOX_X = 0.150
_QUALITY_BOOST_ON_X = 0.761
_QUALITY_BOOST_OFF_X = 0.839
# Second/third line of one quality row: 四倍功勋符 then 追命索.
_QUALITY_BOOST_ROW_OFFSETS = (0.028, 0.061)
_QUALITY_ROW_OCR_MIN_SIMILARITY = 65.0
_BOOST_ROW_INDEX = {
    "quadruple_merit": 0,
    "chase_chain": 1,
}


def expected_magic_invasion_auto_shape_titles() -> tuple[str, ...]:
    """Return every Shape title required to reconcile configuration.

    ``开启自动`` is deliberately excluded: configuration alignment and
    starting the native loop are separate irreversible steps.
    """

    return (
        MAGIC_INVASION_QUALITY_SCROLL_SHAPE,
        *MAGIC_INVASION_AUTO_SHAPE_BY_KEY.values(),
    )


def _runtime_panel_identity(snapshot: Mapping[str, Any]) -> tuple[int, int, int]:
    if snapshot.get("source") != "active_map_act_auto_tips_view":
        raise RuntimeError("魔道自动除魔 Runtime 快照不是活动设置面板来源")
    evidence = dict(snapshot.get("evidence") or {})
    identity = (
        int(evidence.get("pid") or 0),
        int(evidence.get("process_start_ticks") or 0),
        int(evidence.get("panel_address") or 0),
    )
    if min(identity) <= 0:
        raise RuntimeError(f"魔道自动除魔 Runtime 面板身份不完整：{identity!r}")
    return identity


def _preflight_pending_shapes(
    context: Any,
    *,
    scene_id: int,
    action_keys: tuple[str, ...],
) -> None:
    view = context.view(scene_id)
    get_shape = getattr(view, "get_shape", None)
    if not callable(get_shape):
        raise RuntimeError("魔道自动除魔无法读取正式场景 Shape")

    # Only stable controls are assets: the scroll pane ROI and the global
    # toggles that live outside it.  Quality rows are located at runtime by
    # OCR, because their position depends on the current scroll state.
    required = [
        MAGIC_INVASION_AUTO_SHAPE_BY_KEY[key]
        for key in action_keys
        if key not in _QUALITY_KEYS
    ]
    if any(key in _QUALITY_KEYS for key in action_keys):
        required.insert(0, MAGIC_INVASION_QUALITY_SCROLL_SHAPE)
    missing = [title for title in required if get_shape(title) is None]
    if missing:
        raise RuntimeError(
            "魔道自动除魔缺少待点正式 Shape：" + "、".join(missing)
        )


def _quality_row_label(action: Mapping[str, Any]) -> str:
    """Return the quality row label a pending action belongs to."""

    parent = str(action.get("quality") or "")
    if parent:
        return MAGIC_INVASION_AUTO_SHAPE_BY_KEY[parent]
    key = str(action.get("key") or "")
    if key in _QUALITY_KEYS:
        return MAGIC_INVASION_AUTO_SHAPE_BY_KEY[key]
    raise KeyError(key)


def _quality_row_anchor(
    context: Any,
    *,
    scene_id: int,
    label: str,
) -> dict[str, float] | None:
    """Locate one quality row by OCR on its label inside the scroll pane."""

    tokens = context.ocr_tokens_in_shapes(
        scene_id,
        [_QUALITY_PANE_SHAPE],
        padding=2,
    )
    best: tuple[tuple[float, float], Mapping[str, Any]] | None = None
    for token in tokens or []:
        if not isinstance(token, Mapping):
            continue
        text = str(token.get("text") or "").strip()
        if not text:
            continue
        if text == label:
            similarity = 1.0
        elif label in text or text in label:
            # 「长老」 is a substring of 「太上长老」's row title, so require the
            # longer text to be the one actually rendered on this row.
            similarity = 0.9 if len(text) <= len(label) else 0.5
        else:
            similarity = SequenceMatcher(None, text, label).ratio()
        if similarity < 0.85:
            continue
        rank = (similarity, float(token.get("score") or 0.0))
        if best is None or rank > best[0]:
            best = (rank, token)
    if best is None:
        return None
    token = best[1]
    return {
        "x": float(token.get("x") or 0.0) + float(token.get("w") or 0.0) / 2.0,
        "y": float(token.get("y") or 0.0) + float(token.get("h") or 0.0) / 2.0,
    }


def _quality_action_point(
    *,
    frame_width: float,
    frame_height: float,
    anchor: Mapping[str, float],
    action: Mapping[str, Any],
) -> tuple[float, float]:
    """Compute the click point for one row action from the row's OCR anchor."""

    key = str(action.get("key") or "")
    boost_row = next(
        (
            index
            for suffix, index in _BOOST_ROW_INDEX.items()
            if key.endswith(suffix)
        ),
        None,
    )
    if boost_row is not None:
        desired = bool(action.get("desired"))
        x = (_QUALITY_BOOST_ON_X if desired else _QUALITY_BOOST_OFF_X) * frame_width
        y = float(anchor["y"]) + _QUALITY_BOOST_ROW_OFFSETS[boost_row] * frame_height
        return x, y
    return _QUALITY_ROW_CHECKBOX_X * frame_width, float(anchor["y"])


def _scroll_quality_pane_to_top(
    context: Any,
    *,
    scene_id: int,
    max_scrolls: int,
) -> Iterator[Any]:
    for _attempt in range(max_scrolls):
        changed = yield from context.scroll_shape_content(
            scene_id,
            MAGIC_INVASION_QUALITY_SCROLL_SHAPE,
            direction="up",
            unchanged_confirmations=2,
        )
        if not changed:
            return
    raise RuntimeError("魔道自动除魔品质滑窗在有界次数内未能归顶")


def _apply_quality_actions(
    context: Any,
    *,
    scene_id: int,
    actions: tuple[Mapping[str, Any], ...],
    max_scrolls: int,
) -> Iterator[Any]:
    if not actions:
        return []

    yield from _scroll_quality_pane_to_top(
        context,
        scene_id=scene_id,
        max_scrolls=max_scrolls,
    )
    frame_width, frame_height = frame_size(context.view(scene_id).raw)
    pending = list(actions)
    applied: list[str] = []
    for scroll_index in range(max_scrolls + 1):
        anchors: dict[str, dict[str, float]] = {}
        visible: list[Mapping[str, Any]] = []
        for action in pending:
            anchor = _quality_row_anchor(
                context,
                scene_id,
                label=_quality_row_label(action),
            )
            if anchor is not None:
                visible.append(action)
                anchors[str(action["key"])] = anchor
        for action in visible:
            x, y = _quality_action_point(
                frame_width=frame_width,
                frame_height=frame_height,
                anchor=anchors[str(action["key"])],
                action=action,
            )
            context.click_frame_point(scene_id, x, y)
            yield from context.wait_action_settle(0.45)
            pending.remove(action)
            applied.append(str(action["key"]))
        if not pending:
            return applied
        if scroll_index >= max_scrolls:
            break
        changed = yield from context.scroll_shape_content(
            scene_id,
            MAGIC_INVASION_QUALITY_SCROLL_SHAPE,
            direction="down",
            unchanged_confirmations=2,
        )
        if not changed:
            break
    missing = [_quality_row_label(action) for action in pending]
    raise RuntimeError(
        "魔道自动除魔品质滑窗未显示待点行：" + "、".join(missing)
    )


def configure_magic_invasion_auto_options(
    context: Any,
    *,
    scene_id: int = MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID,
    runtime_reader: Callable[[], Mapping[str, Any]] | None = None,
    max_quality_scrolls: int = 12,
) -> Iterator[Any]:
    """Reconcile #698 options and prove the same panel has zero delta.

    This function never clicks ``开启自动``.  Its only successful terminal is
    a complete after-snapshot from the identical Runtime panel identity whose
    planner reports no remaining action.
    """

    if int(max_quality_scrolls) <= 0:
        raise ValueError("魔道自动除魔品质滑窗次数上限必须大于 0")
    if runtime_reader is None:
        from backend.core.fanxiu.instrumentation.magic_invasion_auto_settings import (
            read_magic_invasion_auto_settings_snapshot,
        )

        runtime_reader = read_magic_invasion_auto_settings_snapshot

    before = runtime_reader()
    before_plan = plan_magic_invasion_auto_from_runtime(before)
    panel_identity = _runtime_panel_identity(before)
    action_keys = tuple(str(action["key"]) for action in before_plan["actions"])
    _preflight_pending_shapes(
        context,
        scene_id=scene_id,
        action_keys=action_keys,
    )
    if not action_keys:
        return {
            "already_configured": True,
            "panel_identity": panel_identity,
            "applied_keys": [],
            "before": before_plan["current"],
            "after": before_plan["current"],
        }

    global_keys = tuple(key for key in action_keys if key not in _QUALITY_KEYS)
    quality_actions = tuple(
        action
        for action in before_plan["actions"]
        if str(action.get("key") or "") in _QUALITY_KEYS
    )
    applied = yield from _apply_quality_actions(
        context,
        scene_id=scene_id,
        actions=quality_actions,
        max_scrolls=int(max_quality_scrolls),
    )
    for key in global_keys:
        context.click_shape_center(
            scene_id,
            MAGIC_INVASION_AUTO_SHAPE_BY_KEY[key],
        )
        yield from context.wait_action_settle(0.45)
        applied.append(key)

    after = runtime_reader()
    after_plan = plan_magic_invasion_auto_from_runtime(after)
    after_identity = _runtime_panel_identity(after)
    if after_identity != panel_identity:
        raise RuntimeError(
            "魔道自动除魔配置前后不是同一 Runtime 面板："
            f"before={panel_identity!r}, after={after_identity!r}"
        )
    if not after_plan["already_configured"]:
        remaining = [str(action["key"]) for action in after_plan["actions"]]
        raise RuntimeError(
            "魔道自动除魔配置后仍有差异：" + ", ".join(remaining)
        )
    return {
        "already_configured": False,
        "panel_identity": panel_identity,
        "applied_keys": applied,
        "before": before_plan["current"],
        "after": after_plan["current"],
    }


__all__ = [
    "MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID",
    "MAGIC_INVASION_AUTO_SHAPE_BY_KEY",
    "MAGIC_INVASION_QUALITY_SCROLL_SHAPE",
    "MAGIC_INVASION_START_AUTO_SHAPE",
    "configure_magic_invasion_auto_options",
    "expected_magic_invasion_auto_shape_titles",
]
