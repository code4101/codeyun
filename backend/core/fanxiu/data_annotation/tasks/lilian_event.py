from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from sqlmodel import Session

from backend.db import engine
from backend.models import FanxiuChoiceKnowledge
from backend.core.fanxiu.catalog.lilian_event import (
    load_lilian_event_catalog,
    match_lilian_catalog_event,
    select_lilian_catalog_choice,
)
from backend.core.fanxiu.choice_knowledge.store import (
    ChoiceSelection,
    DOMAIN_LILIAN_EVENT,
    INTERACTION_CHOICE_CLICK,
    match_choice_knowledge,
    normalize_choice_text,
    select_observed_option,
    update_exclusive_choice_outcome,
)
from backend.core.fanxiu.instrumentation.lilian_event import (
    lilian_reward_is_success,
    read_lilian_partner_snapshot,
    select_lilian_condition_partner,
)


class LilianEventFlowError(RuntimeError):
    """Raised when the current event leaves the implemented base flow."""


def record_lilian_choice_reward_outcome(
    session: Session,
    *,
    knowledge_id: str,
    observed_options: list[str],
    selected_text: str,
    selected_position: int,
    rewards: list[dict[str, Any]],
    capture_complete: bool,
    success: bool | None = None,
) -> FanxiuChoiceKnowledge:
    """Apply a complete reward response to the selected Lilian option."""

    if not capture_complete:
        raise ValueError("历练奖励读取不完整，拒绝更新题库")
    knowledge = session.get(FanxiuChoiceKnowledge, knowledge_id)
    if knowledge is None:
        raise ValueError(f"历练题库记录不存在：{knowledge_id}")
    return update_exclusive_choice_outcome(
        session,
        knowledge,
        observed_options=observed_options,
        selected_text=selected_text,
        selected_position=selected_position,
        success=(
            lilian_reward_is_success(rewards)
            if success is None
            else bool(success)
        ),
    )


def select_lilian_event_option(
    session: Session,
    *,
    observed_prompt: str,
    observed_options: list[str],
    match_threshold: float = 82.0,
) -> tuple[FanxiuChoiceKnowledge, ChoiceSelection]:
    """Select a known answer or keep the existing unknown-first behavior."""

    prompt = str(observed_prompt or "").strip()
    options = [str(option or "").strip() for option in observed_options]
    options = [option for option in options if option]
    if not prompt:
        raise ValueError("历练事件名称为空")
    if not options:
        raise ValueError("历练事件选项为空")

    knowledge, score = match_choice_knowledge(
        session,
        domain=DOMAIN_LILIAN_EVENT,
        observed_prompt=prompt,
    )
    if knowledge is None or score < float(match_threshold):
        knowledge = FanxiuChoiceKnowledge(
            domain=DOMAIN_LILIAN_EVENT,
            prompt=prompt,
            normalized_prompt=normalize_choice_text(prompt),
            interaction_mode=INTERACTION_CHOICE_CLICK,
            contexts=[{
                "key": DOMAIN_LILIAN_EVENT,
                "interaction_mode": INTERACTION_CHOICE_CLICK,
                "group_name": "",
                "options_order_fixed": True,
            }],
            options=[],
            options_complete=False,
            source="lilian_event_runtime",
        )
        session.add(knowledge)
        session.flush()

    catalog_match: dict[str, Any] | None = None
    try:
        catalog_match = select_lilian_catalog_choice(
            load_lilian_event_catalog(),
            prompt,
            options,
        )
    except Exception:  # noqa: BLE001 - runtime knowledge remains the safe fallback
        catalog_match = None
    if catalog_match is not None:
        selection = ChoiceSelection(
            text=str(catalog_match["observed_text"]),
            position=int(catalog_match["observed_position"]),
            status=1,
            score=float(catalog_match["option_score"]) * 100.0,
            reason="generated_config_dominant_reward",
        )
        knowledge.source = "lilian_event_generated_config"
    else:
        selection = select_observed_option(knowledge, options)
    session.add(knowledge)
    session.commit()
    session.refresh(knowledge)
    return knowledge, selection


def _view_id(value: Any) -> int | None:
    raw_id = getattr(value, "id", value)
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


def _read_lilian_options(runtime: Any, payload: dict[str, Any]) -> tuple[str, list[str]]:
    frame = runtime.cur_frame(update=True)
    fragments = runtime.ocr_fragments_in_shapes(
        436,
        ("选项",),
        padding=int(payload.get("lilian_option_padding") or 8),
        frame_data_url=frame,
    )
    options = [
        str(fragment.get("text") or "").strip()
        for fragment in fragments
        if str(fragment.get("text") or "").strip()
    ]
    return frame, options


def _choose_lilian_option(
    runtime: Any,
    *,
    selection: ChoiceSelection,
    payload: dict[str, Any],
):
    """Click one proven #436 option with bounded same-transaction retries."""

    max_attempts = max(1, int(payload.get("lilian_option_click_attempts") or 3))
    timeout = float(payload.get("lilian_result_timeout") or 30.0)
    for attempt in range(max_attempts):
        scene_id, _score, _frame = runtime.current_scene(
            [436, 437, 438],
            update=True,
        )
        if scene_id in (437, 438):
            return scene_id
        if scene_id != 436:
            raise LilianEventFlowError(
                f"历练_事件：选择前不再是正式 #436，而是 #{scene_id or 'unknown'}"
            )
        option_frame, visible_options = _read_lilian_options(runtime, payload)
        normalized_target = normalize_choice_text(selection.text)
        if sum(
            normalize_choice_text(option) == normalized_target
            for option in visible_options
        ) != 1:
            raise LilianEventFlowError(
                f"历练_事件：#436 当前选项不再唯一包含「{selection.text}」"
            )
        runtime.click_ocr_text(
            436,
            selection.text,
            in_shapes=("选项",),
            frame_data_url=option_frame,
        )
        try:
            landed = yield from runtime.wait_view(
                437,
                438,
                timeout=timeout,
                label=f"历练_事件：等待事件结果（{attempt + 1}/{max_attempts}）",
            )
        except TimeoutError:
            scene_id, _score, _frame = runtime.current_scene(
                [436, 437, 438],
                update=True,
            )
            if scene_id in (437, 438):
                return scene_id
            if scene_id == 436 and attempt + 1 < max_attempts:
                continue
            raise
        landed_id = _view_id(landed)
        if landed_id in (437, 438):
            return landed_id
    raise LilianEventFlowError(
        f"历练_事件：连续 {max_attempts} 次点击同一已证明选项后仍停留 #436"
    )


def _click_lilian_partner_action(runtime: Any, target_name: str, payload: dict[str, Any]):
    """Find an arbitrary-length partner name and click its same-row action."""

    match = yield from runtime.wait_ocr_text(
        435,
        target_name,
        in_shapes=("窗口",),
        timeout_seconds=float(payload.get("lilian_partner_timeout") or 45.0),
        max_scrolls_per_direction=int(
            payload.get("lilian_partner_max_scrolls") or 30
        ),
    )
    if match is None:
        raise LilianEventFlowError(
            f"历练_事件：#435[窗口] 未找到仙侣「{target_name}」"
        )
    target_view = runtime.view(435)
    status_shape = runtime.shape(435, "状态")
    frame_width, _frame_height = runtime.runner._frame_size(target_view.raw)
    status_x = (
        float(status_shape.raw.get("x") or 0)
        + float(status_shape.raw.get("w") or 0) / 2
    ) * frame_width
    _name_x, name_y = match.point()
    runtime.click_frame_point(435, status_x, name_y)
    yield from runtime.wait_action_settle(
        float(payload.get("lilian_partner_settle_seconds") or 1.0)
    )


def _fill_lilian_team_after_first_member(runtime: Any, payload: dict[str, Any]):
    """Fill the remaining four slots without replacing the first member."""

    for _index in range(4):
        match = yield from runtime.wait_ocr_text(
            435,
            "上阵",
            in_shapes=("窗口",),
            occurrence=0,
            timeout_seconds=float(payload.get("lilian_partner_timeout") or 45.0),
            max_scrolls_per_direction=int(
                payload.get("lilian_partner_max_scrolls") or 30
            ),
        )
        if match is None:
            raise LilianEventFlowError(
                "历练_事件：#435[窗口] 无法找到足够的可上阵仙侣"
            )
        runtime.click_frame_point(435, *match.point())
        yield from runtime.wait_action_settle(
            float(payload.get("lilian_partner_settle_seconds") or 1.0)
        )


def _prepare_lilian_special_team(
    runtime: Any,
    *,
    prompt: str,
    payload: dict[str, Any],
    partner_snapshot: dict[str, Any] | None = None,
):
    catalog_event = match_lilian_catalog_event(
        load_lilian_event_catalog(),
        prompt,
        threshold=float(payload.get("lilian_event_match_threshold") or 0.72),
    )
    if catalog_event is None:
        catalog_event = {
            "id": None,
            "name": prompt,
            "special_condition": "",
        }
    special_condition = str(catalog_event.get("special_condition") or "")
    selected_partner: dict[str, Any] | None = None
    resolved_partner_snapshot = partner_snapshot
    if special_condition.startswith(("IncludeXianLv|", "CaptainSex|", "CaptainCareer|")):
        snapshot = partner_snapshot or read_lilian_partner_snapshot()
        condition_before = runtime.ocr_text_in_shapes(
            435,
            ("特殊条件",),
            padding=int(payload.get("lilian_condition_padding") or 8),
        )
        visible_text = runtime.ocr_text_in_shapes(
            435,
            ("窗口",),
            padding=int(payload.get("lilian_partner_visible_padding") or 4),
        )
        visible_key = normalize_choice_text(str(visible_text or ""))
        partners = [
            dict(item)
            for item in snapshot.get("partners") or []
            if isinstance(item, dict)
        ]
        partners.sort(
            key=lambda item: normalize_choice_text(str(item.get("name") or ""))
            not in visible_key
        )
        snapshot = {**snapshot, "partners": partners}
        resolved_partner_snapshot = snapshot
        if "已满足" not in str(condition_before or ""):
            selected_partner = select_lilian_condition_partner(
                snapshot,
                special_condition,
            )
            if selected_partner is None:
                raise LilianEventFlowError(
                    f"历练_事件：条件 {special_condition} 未能确定上阵仙侣"
                )
            yield from _click_lilian_partner_action(
                runtime,
                str(selected_partner["name"]),
                payload,
            )
        yield from _fill_lilian_team_after_first_member(runtime, payload)
    else:
        yield from runtime.wait_click(
            435,
            "一键上阵",
            timeout=float(payload.get("lilian_team_timeout") or 20.0),
        )
        yield from runtime.wait_action_settle(
            float(payload.get("lilian_partner_settle_seconds") or 1.0)
        )

    condition_text = runtime.ocr_text_in_shapes(
        435,
        ("特殊条件",),
        padding=int(payload.get("lilian_condition_padding") or 8),
    )
    if "已满足" not in str(condition_text or ""):
        raise LilianEventFlowError(
            "历练_事件：组队后特殊条件仍未确认满足，拒绝派遣；"
            f"event={catalog_event.get('name')!r}, condition={special_condition!r}, "
            f"visible={condition_text!r}"
        )
    return catalog_event, selected_partner, resolved_partner_snapshot


def _finish_lilian_event_reward(
    runtime: Any,
    *,
    scene_id: int,
    payload: dict[str, Any],
):
    """Finish the bounded #437/#438 reward transaction and return #425."""

    max_steps = max(2, int(payload.get("lilian_reward_finish_max_steps") or 5))
    for _step in range(max_steps):
        if scene_id == 425:
            return scene_id
        if scene_id == 437:
            landed = yield from runtime.wait_click_then_view(
                437,
                "领取",
                438,
                timeout=float(payload.get("lilian_claim_timeout") or 20.0),
            )
            scene_id = _view_id(landed)
            if scene_id != 438:
                raise LilianEventFlowError(
                    f"历练_事件：领取后未进入 #438，而是 #{scene_id or 'unknown'}"
                )
            continue
        if scene_id == 438:
            landed = yield from runtime.wait_click_then_view(
                438,
                "关闭",
                425,
                437,
                timeout=float(payload.get("lilian_reward_close_timeout") or 20.0),
            )
            scene_id = _view_id(landed)
            if scene_id not in (425, 437):
                raise LilianEventFlowError(
                    "历练_事件：关闭奖励后未进入 #425/#437，而是 "
                    f"#{scene_id or 'unknown'}"
                )
            continue
        raise LilianEventFlowError(
            f"历练_事件：奖励收尾进入未接入场景 #{scene_id or 'unknown'}"
        )
    raise LilianEventFlowError(
        f"历练_事件：奖励收尾超过 {max_steps} 步，仍停留 #{scene_id or 'unknown'}"
    )


def execute_lilian_event_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
):
    """Run the base Lilian event flow and return to the world scene."""

    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("历练_事件：缺少资产树路径，无法执行")
    runtime = runner._fanxiu_runtime(
        ctx,
        asset_tree_path,
        stop_event=stop_event,
    )
    scene_id, _score, _frame = runtime.current_scene(
        [34],
        update=True,
    )
    if scene_id != 34:
        raise LilianEventFlowError(
            f"历练_事件：作业入口必须是 #34，而是 #{scene_id or 'unknown'}"
        )

    landed = yield from runtime.wait_click_then_view(
        34,
        "大地图",
        425,
        timeout=float(payload.get("lilian_map_timeout") or 20.0),
    )
    scene_id = _view_id(landed)
    if scene_id == 425:
        landed = yield from runtime.wait_click_then_view(
            425,
            "历练按钮",
            427,
            timeout=float(payload.get("lilian_panel_timeout") or 20.0),
        )
        scene_id = _view_id(landed)
    if scene_id == 427:
        landed = yield from runtime.wait_click_then_view(
            427,
            "事件",
            428,
            429,
            timeout=float(payload.get("lilian_event_timeout") or 20.0),
        )
        scene_id = _view_id(landed)

    if scene_id not in (428, 429):
        raise RuntimeError(
            f"历练_事件：进入事件页后落点异常 #{scene_id or 'unknown'}"
        )

    processed_events: list[dict[str, Any]] = []
    partner_snapshot: dict[str, Any] | None = None
    max_events = max(1, int(payload.get("lilian_max_events_per_run") or 20))

    while scene_id == 428:
        if len(processed_events) >= max_events:
            raise LilianEventFlowError(
                f"历练_事件：连续处理 {len(processed_events)} 个事件后仍显示 #428，"
                "拒绝误报完成"
            )
        landed = yield from runtime.wait_click_then_view(
            428,
            "前往",
            434,
            timeout=float(payload.get("lilian_go_event_timeout") or 20.0),
        )
        if _view_id(landed) != 434:
            raise LilianEventFlowError("历练_事件：#428 前往后未进入 #434")

        prompt = runtime.ocr_text_in_shapes(
            434,
            ("事件",),
            padding=int(payload.get("lilian_prompt_padding") or 8),
        )
        landed = yield from runtime.wait_click_then_view(
            434,
            "历练",
            435,
            timeout=float(payload.get("lilian_prepare_timeout") or 20.0),
        )
        if _view_id(landed) != 435:
            raise LilianEventFlowError("历练_事件：#434 历练后未进入 #435")

        catalog_event, selected_partner, partner_snapshot = yield from _prepare_lilian_special_team(
            runtime,
            prompt=prompt,
            payload=payload,
            partner_snapshot=partner_snapshot,
        )
        landed = yield from runtime.wait_click_then_view(
            435,
            "派遣",
            436,
            timeout=float(payload.get("lilian_dispatch_timeout") or 20.0),
        )
        if _view_id(landed) != 436:
            raise LilianEventFlowError("历练_事件：派遣后未进入 #436")

        _option_frame, observed_options = _read_lilian_options(runtime, payload)
        with Session(engine) as session:
            knowledge, selection = select_lilian_event_option(
                session,
                observed_prompt=prompt,
                observed_options=observed_options,
                match_threshold=float(
                    payload.get("lilian_prompt_match_threshold") or 82.0
                ),
            )
            knowledge_id = knowledge.id

        scene_id = yield from _choose_lilian_option(
            runtime,
            selection=selection,
            payload=payload,
        )
        if scene_id not in (437, 438):
            raise LilianEventFlowError(
                f"历练_事件：选择后进入未接入场景 #{scene_id or 'unknown'}"
            )

        yield from _finish_lilian_event_reward(
            runtime,
            scene_id=scene_id,
            payload=payload,
        )

        processed_events.append(
            {
                "event": prompt,
                "event_id": catalog_event.get("id"),
                "special_condition": catalog_event.get("special_condition"),
                "selected_partner": selected_partner,
                "selected_option": selection.text,
            }
        )

        landed = yield from runtime.wait_click_then_view(
            425,
            "历练按钮",
            427,
            timeout=float(payload.get("lilian_panel_timeout") or 20.0),
        )
        if _view_id(landed) != 427:
            raise LilianEventFlowError("历练_事件：处理事件后重新进入历练面板失败")
        landed = yield from runtime.wait_click_then_view(
            427,
            "事件",
            428,
            429,
            timeout=float(payload.get("lilian_event_timeout") or 20.0),
        )
        scene_id = _view_id(landed)

    if scene_id != 429:
        raise LilianEventFlowError(
            f"历练_事件：处理事件后未进入 #428/#429，而是 #{scene_id or 'unknown'}"
        )

    landed = yield from runtime.wait_click_then_view(
        429,
        "关闭事件页",
        425,
        timeout=float(payload.get("lilian_close_event_timeout") or 20.0),
    )
    if _view_id(landed) != 425:
        raise RuntimeError("历练_事件：关闭 #429 后未进入 #425")

    runtime.click_frame_point(425, 80, 1480)
    landed = yield from runtime.wait_view(
        34,
        timeout=float(payload.get("lilian_return_world_timeout") or 20.0),
        label="历练_事件：返回世界",
    )
    if _view_id(landed) != 34:
        raise RuntimeError("历练_事件：关闭历练地图后未返回 #34")

    result = {
        "result": "success",
        "message": (
            f"历练_事件：连续处理 {len(processed_events)} 个事件，"
            "#429 已确认没有可处理事件，返回 #34 后完成"
        ),
        "current_scene": 34,
        "processed_event_count": len(processed_events),
        "processed_events": processed_events,
    }
    if processed_events:
        result.update(processed_events[-1])
    return result
