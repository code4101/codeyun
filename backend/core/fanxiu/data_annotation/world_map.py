from __future__ import annotations

from typing import Any, Iterable

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text


WORLD_REALM_ORDER = ("人", "灵", "魔", "仙")
WORLD_REALM_NAMES = {
    "人": "人界",
    "灵": "灵界",
    "魔": "魔界",
    "仙": "仙界",
}
_WORLD_REALM_ALIASES = {
    **{key: key for key in WORLD_REALM_ORDER},
    **{value: key for key, value in WORLD_REALM_NAMES.items()},
}
_WORLD_REALM_OCR_OPTIONS = {
    "text_det_thresh": 0.2,
    "text_det_box_thresh": 0.35,
    "text_det_unclip_ratio": 1.1,
}
# The selector removes the current realm from the global order and fills the
# remaining three realms into these slots in order.
# Coordinates are ratios within #426「选项」 rather than absolute screen
# points, so the action still follows the asset's envelope.
_WORLD_REALM_OPTION_ANCHORS = (
    (0.20, 0.31),
    (0.35, 0.61),
    (0.71, 0.83),
)


def normalize_world_realm(value: Any) -> str:
    """Return the canonical one-character realm key."""

    text = _sanitize_ocr_text(str(value or ""))
    if text in _WORLD_REALM_ALIASES:
        return _WORLD_REALM_ALIASES[text]
    if text:
        first = text[0]
        if first in WORLD_REALM_ORDER:
            return first
    raise ValueError(
        f"未知地图界面 {value!r}；应为人界、灵界、魔界或仙界"
    )


def _raw(value: Any) -> dict[str, Any]:
    raw = getattr(value, "raw", value)
    return raw if isinstance(raw, dict) else {}


def _shape_geometry(
    runtime: Any,
    view_id: int,
    shape_title: str,
) -> tuple[Any, Any, float, float, float, float]:
    view = runtime.view(view_id)
    shape = runtime.shape(view, shape_title)
    view_raw = _raw(view)
    shape_raw = _raw(shape)
    width = float(view_raw.get("width") or 0)
    height = float(view_raw.get("height") or 0)
    if width <= 0 or height <= 0 or not shape_raw:
        raise RuntimeError(
            f"#{view_id}「{shape_title}」缺少有效尺寸，无法读取界面"
        )
    x = float(shape_raw.get("x") or 0) * width
    y = float(shape_raw.get("y") or 0) * height
    w = float(shape_raw.get("w") or 0) * width
    h = float(shape_raw.get("h") or 0) * height
    return view, shape, x, y, w, h


def _token_realm_candidates(
    tokens: Iterable[dict[str, Any]],
    *,
    center_x: float,
    center_y: float,
) -> list[tuple[float, str, dict[str, Any]]]:
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    for token in tokens:
        text = _sanitize_ocr_text(str(token.get("text") or ""))
        realm = next((char for char in text if char in WORLD_REALM_ORDER), "")
        if not realm:
            continue
        token_x = (
            float(token.get("x") or 0)
            + float(token.get("w") or 0) / 2
        )
        token_y = (
            float(token.get("y") or 0)
            + float(token.get("h") or 0) / 2
        )
        distance = (token_x - center_x) ** 2 + (token_y - center_y) ** 2
        candidates.append((distance, realm, dict(token)))
    return sorted(candidates, key=lambda item: item[0])


def read_world_realm(
    runtime: Any,
    *,
    frame_data_url: str | None = None,
) -> dict[str, Any]:
    """Read the current #425 realm from only its first character."""

    _view, _shape, x, y, w, h = _shape_geometry(
        runtime,
        425,
        "界面",
    )
    frame = (
        frame_data_url
        if isinstance(frame_data_url, str) and frame_data_url
        else runtime.cur_frame(update=True)
    )
    shared_tokens = runtime.ocr_tokens_in_shapes(
        425,
        ("界面",),
        padding=4,
        frame_data_url=frame,
    )
    cropped_tokens = runtime.ocr_tokens_in_shapes(
        425,
        ("界面",),
        padding=4,
        frame_data_url=frame,
        crop=True,
        options=_WORLD_REALM_OCR_OPTIONS,
    )
    # Stylized vertical glyphs can alternate between good full-frame OCR and
    # good cropped OCR. Prefer the shared full-frame pass, then let the crop
    # supply candidates that the first pass omitted.
    tokens = [*shared_tokens, *cropped_tokens]
    candidates = _token_realm_candidates(
        tokens,
        center_x=x + w / 2,
        center_y=y + h / 2,
    )
    if not candidates:
        return {
            "ok": False,
            "available": False,
            "realm": None,
            "realm_name": None,
            "source": "scene_425_first_character_ocr",
            "tokens": tokens,
            "shared_tokens": shared_tokens,
            "cropped_tokens": cropped_tokens,
            "reason": "current_realm_character_not_recognized",
        }
    _distance, realm, token = candidates[0]
    return {
        "ok": True,
        "available": True,
        "realm": realm,
        "realm_name": WORLD_REALM_NAMES[realm],
        "source": "scene_425_first_character_ocr",
        "token": token,
        "tokens": tokens,
        "shared_tokens": shared_tokens,
        "cropped_tokens": cropped_tokens,
    }


def _find_target_token(
    tokens: Iterable[dict[str, Any]],
    target: str,
) -> dict[str, Any] | None:
    for token in tokens:
        text = _sanitize_ocr_text(str(token.get("text") or ""))
        if target in text:
            return dict(token)
    return None


def _token_center(token: dict[str, Any]) -> tuple[float, float]:
    return (
        float(token.get("x") or 0)
        + float(token.get("w") or 0) / 2,
        float(token.get("y") or 0)
        + float(token.get("h") or 0) / 2,
    )


def _ordered_target_point(
    runtime: Any,
    *,
    current: str,
    target: str,
) -> tuple[float, float]:
    _view, _shape, x, y, w, h = _shape_geometry(
        runtime,
        426,
        "选项",
    )
    if current == target:
        raise RuntimeError("当前界面与目标界面相同，无需选择")
    visible_realms = [
        realm for realm in WORLD_REALM_ORDER if realm != current
    ]
    option_index = visible_realms.index(target)
    x_ratio, y_ratio = _WORLD_REALM_OPTION_ANCHORS[option_index]
    return x + w * x_ratio, y + h * y_ratio


def ensure_world_realm(
    runtime: Any,
    target: Any,
    *,
    max_attempts: int = 3,
):
    """Idempotently ensure #425 is showing the requested realm."""

    target_realm = normalize_world_realm(target)
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        current = read_world_realm(runtime)
        if not current.get("ok"):
            yield from runtime.wait_action_settle(0.5)
            current = read_world_realm(runtime)
        if not current.get("ok"):
            raise RuntimeError(
                "无法从 #425「界面」首字确认当前界面，"
                f"OCR={current.get('tokens')}"
            )
        current_realm = str(current["realm"])
        if current_realm == target_realm:
            return {
                "ok": True,
                "changed": bool(attempts),
                "realm": target_realm,
                "realm_name": WORLD_REALM_NAMES[target_realm],
                "attempts": attempts,
            }

        runtime.click_shape_center(425, "界面")
        yield from runtime.wait_action_settle(1.0)
        option_tokens = runtime.ocr_tokens_in_shapes(
            426,
            ("选项",),
            padding=4,
            crop=True,
            options=_WORLD_REALM_OCR_OPTIONS,
        )
        target_token = _find_target_token(option_tokens, target_realm)
        if target_token is not None:
            click_x, click_y = _token_center(target_token)
            action_source = "target_first_character_ocr"
        else:
            click_x, click_y = _ordered_target_point(
                runtime,
                current=current_realm,
                target=target_realm,
            )
            action_source = "ordered_anchor_fallback"
        runtime.click_frame_point(426, click_x, click_y)
        yield from runtime.wait_action_settle(2.0)
        runtime.clear_frame()
        observed = read_world_realm(runtime)
        attempts.append(
            {
                "attempt": attempt,
                "from": current_realm,
                "target": target_realm,
                "action_source": action_source,
                "click": [click_x, click_y],
                "observed": observed.get("realm"),
                "option_tokens": option_tokens,
            }
        )
        if observed.get("realm") == target_realm:
            return {
                "ok": True,
                "changed": True,
                "realm": target_realm,
                "realm_name": WORLD_REALM_NAMES[target_realm],
                "attempts": attempts,
            }
        yield from runtime.wait_action_settle(0.5)

    raise RuntimeError(
        f"切换到{WORLD_REALM_NAMES[target_realm]}失败，"
        f"已尝试 {len(attempts)} 次；证据={attempts}"
    )
