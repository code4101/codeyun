from __future__ import annotations

import json
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from backend.core.fanxiu_sunlogin_rotate import get_fanxiu_mainwin_root


VISUAL_ACTION_MARKER_START = "<!-- codeyun-visual-action-v1"
VISUAL_ACTION_MARKER_END = "-->"
VISUAL_MACRO_DIRNAME = "视觉宏"
STRUCTURED_SCRIPT_FILENAME = "structured_runtime.py"
LAST_RESULT_FILENAME = "last_result.json"
_RUN_EVENTS_LOCK = threading.Lock()
_RUN_STOP_EVENTS: dict[str, threading.Event] = {}


class VisualMacroStopped(RuntimeError):
    pass


@dataclass
class VisualMacroRuntimeCallbacks:
    match: Callable[[dict[str, Any]], dict[str, Any]]
    click: Callable[[dict[str, Any]], dict[str, Any]]
    drag: Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class VisualMacroRunContext:
    cards: list[dict[str, Any]]
    selected_card_id: str
    base_payload: dict[str, Any]
    callbacks: VisualMacroRuntimeCallbacks
    timeout: float = 120.0
    tick_interval: float = 1.0
    stop_event: threading.Event | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)

    def log(self, message: str, **extra: Any) -> None:
        self.events.append({"type": "log", "message": message, "time": time.time(), **extra})

    def yield_tick(self, reason: str) -> None:
        self.raise_if_stopped()
        self.events.append({"type": "yield_tick", "reason": reason, "time": time.time()})
        time.sleep(max(0.0, self.tick_interval))
        self.raise_if_stopped()

    def raise_if_stopped(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise VisualMacroStopped("用户停止运行")


def begin_visual_macro_run(run_key: str) -> threading.Event:
    stop_event = threading.Event()
    with _RUN_EVENTS_LOCK:
        existing = _RUN_STOP_EVENTS.get(run_key)
        if existing is not None:
            existing.set()
        _RUN_STOP_EVENTS[run_key] = stop_event
    return stop_event


def end_visual_macro_run(run_key: str, stop_event: threading.Event) -> None:
    with _RUN_EVENTS_LOCK:
        if _RUN_STOP_EVENTS.get(run_key) is stop_event:
            _RUN_STOP_EVENTS.pop(run_key, None)


def stop_visual_macro_run(run_key: str) -> bool:
    with _RUN_EVENTS_LOCK:
        stop_event = _RUN_STOP_EVENTS.get(run_key)
    if stop_event is None:
        return False
    stop_event.set()
    return True


def _runtime_dir() -> Path:
    path = get_fanxiu_mainwin_root() / VISUAL_MACRO_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _structured_script_path() -> Path:
    return _runtime_dir() / STRUCTURED_SCRIPT_FILENAME


def _last_result_path() -> Path:
    return _runtime_dir() / LAST_RESULT_FILENAME


def _json_dumps(payload: Any, *, indent: int | None = None) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=indent)


def _parse_visual_program(body: str) -> dict[str, Any]:
    start = body.find(VISUAL_ACTION_MARKER_START)
    if start < 0:
        return {"version": 1, "operations": []}
    json_start = start + len(VISUAL_ACTION_MARKER_START)
    end = body.find(VISUAL_ACTION_MARKER_END, json_start)
    if end < 0:
        return {"version": 1, "operations": []}
    try:
        payload = json.loads(body[json_start:end].strip())
    except json.JSONDecodeError:
        return {"version": 1, "operations": []}
    if not isinstance(payload, dict):
        return {"version": 1, "operations": []}
    operations = payload.get("operations")
    if not isinstance(operations, list):
        operations = []
    return {"version": 1, "operations": [item for item in operations if isinstance(item, dict)]}


def _visual_program(card: dict[str, Any]) -> dict[str, Any]:
    return _parse_visual_program(str(card.get("body") or ""))


def _visual_operations(card: dict[str, Any]) -> list[dict[str, Any]]:
    return list(_visual_program(card).get("operations") or [])


def _operation_set_id(operation: dict[str, Any]) -> str:
    return str(operation.get("setId") or operation.get("id") or "")


def _instruction_sets(card: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for operation in _visual_operations(card):
        set_id = _operation_set_id(operation)
        if not set_id:
            continue
        if set_id not in groups:
            groups[set_id] = []
            order.append(set_id)
        groups[set_id].append(operation)
    return [
        {
            "id": set_id,
            "label": next((str(item.get("setLabel") or "").strip() for item in groups[set_id] if str(item.get("setLabel") or "").strip()), ""),
            "instructions": groups[set_id],
        }
        for set_id in order
    ]


def _action_label(operation: dict[str, Any]) -> str:
    action = str(operation.get("action") or "")
    target = str(operation.get("target") or "")
    action_label = {
        "waitClick": "等待点击",
        "guardClick": "守护点击",
        "click": "点击",
        "drag": "拖拽",
        "wait": "等待",
        "find": "查找",
        "findAll": "批量查找",
    }.get(action, action or "指令")
    target_label = {"image": "图片", "text": "文本", "coordinate": "坐标"}.get(target, target or "对象")
    return f"{action_label} {target_label}".strip()


def _operation_title(operation: dict[str, Any]) -> str:
    return str(operation.get("label") or "").strip() or _action_label(operation)


def _set_title(instruction_set: dict[str, Any]) -> str:
    label = str(instruction_set.get("label") or "").strip()
    if label:
        return label
    instructions = instruction_set.get("instructions") if isinstance(instruction_set.get("instructions"), list) else []
    return _operation_title(instructions[0]) if instructions else "空指令集"


def _build_reference_index(cards: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    instruction_index: dict[str, dict[str, Any]] = {}
    set_index: dict[str, dict[str, Any]] = {}
    instruction_name_counts: dict[str, int] = {}
    set_name_counts: dict[str, int] = {}
    for card in cards:
        for instruction_set in _instruction_sets(card):
            set_label = str(instruction_set.get("label") or "").strip()
            if set_label:
                set_name_counts[set_label] = set_name_counts.get(set_label, 0) + 1
                set_index[set_label] = {"card": card, "set": instruction_set}
            for operation in instruction_set.get("instructions") or []:
                label = str(operation.get("label") or "").strip()
                if not label:
                    continue
                instruction_name_counts[label] = instruction_name_counts.get(label, 0) + 1
                instruction_index[label] = {"card": card, "set": instruction_set, "operation": operation}
    return {
        "instruction": {name: value for name, value in instruction_index.items() if instruction_name_counts.get(name) == 1},
        "instructionSet": {name: value for name, value in set_index.items() if set_name_counts.get(name) == 1},
    }


def _point_with_jitter(point: dict[str, Any] | None) -> tuple[int, int]:
    if not isinstance(point, dict):
        return 0, 0
    x = float(point.get("x") or 0)
    y = float(point.get("y") or 0)
    r = max(0.0, float(point.get("r") or 0))
    if r > 0:
        angle = random.random() * math.tau
        radius = math.sqrt(random.random()) * r
        x += math.cos(angle) * radius
        y += math.sin(angle) * radius
    return round(x), round(y)


def _pointer_start(operation: dict[str, Any]) -> dict[str, Any] | None:
    pointer = operation.get("pointer") if isinstance(operation.get("pointer"), dict) else {}
    start = pointer.get("start") if isinstance(pointer.get("start"), dict) else None
    return start


def _pointer_end(operation: dict[str, Any]) -> dict[str, Any] | None:
    pointer = operation.get("pointer") if isinstance(operation.get("pointer"), dict) else {}
    end = pointer.get("end") if isinstance(pointer.get("end"), dict) else None
    return end


def _image_box(operation: dict[str, Any]) -> dict[str, Any]:
    box = operation.get("box") if isinstance(operation.get("box"), dict) else None
    mode = str(operation.get("imageBoxMode") or "anchor")
    if mode == "manual" and box:
        return {
            "name": _operation_title(operation),
            "x": round(float(box.get("x") or 0)),
            "y": round(float(box.get("y") or 0)),
            "w": max(1, round(float(box.get("w") or 1))),
            "h": max(1, round(float(box.get("h") or 1))),
        }
    start = _pointer_start(operation) or {}
    width = max(1, round(float((box or {}).get("w") or 50)))
    height = max(1, round(float((box or {}).get("h") or 50)))
    center_x = round(float(start.get("x") or 0))
    center_y = round(float(start.get("y") or 0))
    return {
        "name": _operation_title(operation),
        "x": max(0, center_x - width // 2),
        "y": max(0, center_y - height // 2),
        "w": width,
        "h": height,
    }


def _match_score(operation: dict[str, Any], match_result: dict[str, Any]) -> int:
    scan = str(operation.get("scan") or "fixed")
    if scan == "fixed":
        value = match_result.get("fixed_similarity", match_result.get("similarity", 0))
    else:
        value = match_result.get("template_similarity", match_result.get("similarity", 0))
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _image_target_matches(ctx: VisualMacroRunContext, operation: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    filename = str(operation.get("frame") or "").strip()
    if not filename:
        ctx.log(f"{_operation_title(operation)}：未绑定截图")
        return False, None
    payload = {
        **ctx.base_payload,
        "filename": filename,
        "box": _image_box(operation),
        "pixel_tolerance": max(0, min(255, int(operation.get("pixelTolerance") or 0))),
    }
    result = ctx.callbacks.match(payload)
    score = _match_score(operation, result)
    threshold = int(round(float(operation.get("threshold") or 0.8) * 100))
    matched = score >= threshold
    ctx.log(f"{_operation_title(operation)}：图像相似度 {score}% / 阈值 {threshold}%", matched=matched, match=result)
    return matched, result


def _target_matches(ctx: VisualMacroRunContext, operation: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    target = str(operation.get("target") or "coordinate")
    if target == "coordinate":
        return True, None
    if target == "image":
        return _image_target_matches(ctx, operation)
    ctx.log(f"{_operation_title(operation)}：文本目标暂未接入 OCR 执行器")
    return False, None


def _click_operation(ctx: VisualMacroRunContext, operation: dict[str, Any]) -> dict[str, Any]:
    x, y = _point_with_jitter(_pointer_start(operation))
    payload = {**ctx.base_payload, "x": x, "y": y}
    result = ctx.callbacks.click(payload)
    ctx.log(f"{_operation_title(operation)}：点击 ({x}, {y})", click=result)
    ctx.yield_tick("click")
    return result


def _drag_operation(ctx: VisualMacroRunContext, operation: dict[str, Any]) -> dict[str, Any]:
    start_x, start_y = _point_with_jitter(_pointer_start(operation))
    end_x, end_y = _point_with_jitter(_pointer_end(operation))
    pointer = operation.get("pointer") if isinstance(operation.get("pointer"), dict) else {}
    duration_ms = max(50, min(3000, int(pointer.get("durationMs") or 300)))
    payload = {
        **ctx.base_payload,
        "start_x": start_x,
        "start_y": start_y,
        "end_x": end_x,
        "end_y": end_y,
        "duration_ms": duration_ms,
    }
    result = ctx.callbacks.drag(payload)
    ctx.log(f"{_operation_title(operation)}：拖拽 ({start_x}, {start_y}) -> ({end_x}, {end_y})", drag=result)
    ctx.yield_tick("drag")
    return result


def _run_normal_instruction(ctx: VisualMacroRunContext, operation: dict[str, Any], deadline: float) -> None:
    ctx.raise_if_stopped()
    action = str(operation.get("action") or "")
    if action == "click":
        ctx.results[operation.get("id") or _operation_title(operation)] = _click_operation(ctx, operation)
        return
    if action == "drag":
        ctx.results[operation.get("id") or _operation_title(operation)] = _drag_operation(ctx, operation)
        return
    if action == "guardClick":
        matched, _result = _target_matches(ctx, operation)
        if matched:
            ctx.results[operation.get("id") or _operation_title(operation)] = _click_operation(ctx, operation)
        else:
            ctx.log(f"{_operation_title(operation)}：对象不存在，跳过")
        return
    if action == "find" or action == "findAll":
        matched, result = _target_matches(ctx, operation)
        ctx.results[operation.get("id") or _operation_title(operation)] = {"matched": matched, "match": result}
        ctx.yield_tick(action)
        return
    if action == "wait":
        condition = str(operation.get("condition") or "appear")
        timeout = max(0.0, float(operation.get("timeout") or 0))
        if str(operation.get("target") or "coordinate") == "coordinate":
            sleep_seconds = timeout if timeout > 0 else ctx.tick_interval
            ctx.log(f"{_operation_title(operation)}：等待 {sleep_seconds:g} 秒")
            time.sleep(min(max(0.0, sleep_seconds), max(0.0, deadline - time.time())))
            ctx.yield_tick("wait")
            return
        wait_deadline = min(deadline, time.time() + timeout) if timeout > 0 else deadline
        while time.time() <= wait_deadline:
            ctx.raise_if_stopped()
            matched, result = _target_matches(ctx, operation)
            ok = (condition == "disappear" and not matched) or (condition != "disappear" and matched)
            if ok:
                ctx.results[operation.get("id") or _operation_title(operation)] = {"matched": matched, "match": result}
                return
            ctx.yield_tick("wait")
        raise RuntimeError(f"{_operation_title(operation)} 等待超时")
    if action == "waitClick":
        while time.time() <= deadline:
            ctx.raise_if_stopped()
            matched, _result = _target_matches(ctx, operation)
            if matched:
                ctx.results[operation.get("id") or _operation_title(operation)] = _click_operation(ctx, operation)
                return
            ctx.yield_tick("waitClick")
        raise RuntimeError(f"{_operation_title(operation)} 等待超时")
    ctx.log(f"{_operation_title(operation)}：暂不支持的动作 {action}")


def _run_instruction(
    ctx: VisualMacroRunContext,
    operation: dict[str, Any],
    reference_index: dict[str, dict[str, dict[str, Any]]],
    stack: list[str],
    deadline: float,
) -> None:
    ctx.raise_if_stopped()
    if str(operation.get("kind") or "normal") != "ref":
        _run_normal_instruction(ctx, operation, deadline)
        return

    ref_name = str(operation.get("refName") or "").strip()
    target_kind = str(operation.get("refTargetKind") or "instruction")
    if not ref_name:
        ctx.log("调用：未选择目标，跳过")
        return
    stack_key = f"{target_kind}:{ref_name}"
    if stack_key in stack:
        raise RuntimeError(f"检测到递归调用：{' -> '.join([*stack, stack_key])}")
    target = reference_index.get(target_kind, {}).get(ref_name)
    if not target:
        ctx.log(f"调用：找不到唯一目标 {ref_name}")
        return
    ctx.log(f"调用：{ref_name}")
    if target_kind == "instructionSet":
        _run_instruction_set(ctx, target["set"], reference_index, [*stack, stack_key], deadline)
    else:
        _run_instruction(ctx, target["operation"], reference_index, [*stack, stack_key], deadline)


def _run_instruction_set(
    ctx: VisualMacroRunContext,
    instruction_set: dict[str, Any],
    reference_index: dict[str, dict[str, dict[str, Any]]],
    stack: list[str],
    deadline: float,
) -> None:
    ctx.raise_if_stopped()
    ctx.log(f"指令集开始：{_set_title(instruction_set)}")
    for operation in instruction_set.get("instructions") or []:
        ctx.raise_if_stopped()
        if time.time() > deadline:
            raise RuntimeError("视觉脚本运行超时")
        _run_instruction(ctx, operation, reference_index, stack, deadline)
    ctx.log(f"指令集结束：{_set_title(instruction_set)}")


def _build_structured_python(cards: list[dict[str, Any]], selected_card_id: str) -> str:
    selected = next((card for card in cards if str(card.get("id")) == selected_card_id), None)
    selected_title = str((selected or {}).get("title") or "未命名脚本")
    payload = {
        "selected_card_id": selected_card_id,
        "selected_title": selected_title,
        "cards": [
            {
                "id": card.get("id"),
                "title": card.get("title") or "",
                "operations": _visual_operations(card),
            }
            for card in cards
        ],
    }
    return (
        "# Auto-generated by CodeYun Fanxiu visual macro runner.\n"
        "# This file is a readable execution plan; the live runner injects window callbacks.\n\n"
        f"VISUAL_SCRIPT = {_json_dumps(payload, indent=2)}\n\n"
        "class BehaviorTree:\n"
        "    def tick(self, ctx):\n"
        "        for instruction_set in ctx.selected_script_sets():\n"
        "            yield from ctx.run_instruction_set(instruction_set)\n"
    )


def run_fanxiu_visual_script(
    cards: list[dict[str, Any]],
    *,
    selected_card_id: str,
    base_payload: dict[str, Any],
    callbacks: VisualMacroRuntimeCallbacks,
    timeout: float = 120.0,
    tick_interval: float = 1.0,
    stop_event: threading.Event | None = None,
) -> dict[str, Any]:
    selected = next((card for card in cards if str(card.get("id")) == selected_card_id), None)
    if selected is None:
        raise RuntimeError("脚本不存在")

    script_path = _structured_script_path()
    script_path.write_text(_build_structured_python(cards, selected_card_id), encoding="utf-8")

    ctx = VisualMacroRunContext(
        cards=cards,
        selected_card_id=selected_card_id,
        base_payload=base_payload,
        callbacks=callbacks,
        timeout=timeout,
        tick_interval=tick_interval,
        stop_event=stop_event,
    )
    deadline = float("inf") if timeout <= 0 else time.time() + max(1.0, timeout)
    reference_index = _build_reference_index(cards)
    status = "completed"
    try:
        ctx.log(f"脚本开始：{selected.get('title') or '未命名脚本'}")
        for instruction_set in _instruction_sets(selected):
            ctx.raise_if_stopped()
            if time.time() > deadline:
                raise RuntimeError("视觉脚本运行超时")
            _run_instruction_set(ctx, instruction_set, reference_index, [], deadline)
        ctx.log(f"脚本结束：{selected.get('title') or '未命名脚本'}")
    except VisualMacroStopped as exc:
        status = "stopped"
        ctx.log(str(exc))

    payload = {
        "ok": True,
        "status": status,
        "script_path": os.fspath(script_path),
        "selected_card_id": selected_card_id,
        "selected_title": selected.get("title") or "",
        "events": ctx.events,
        "results": ctx.results,
        "updated_at": time.time(),
    }
    _last_result_path().write_text(_json_dumps(payload, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "status": status,
        "script_path": os.fspath(script_path),
        "cache_hits": 0,
        "cache_misses": 0,
        "compiled_cards": 1,
        "log": "\n".join(str(event.get("message") or event.get("reason") or event.get("type")) for event in ctx.events),
        "result": _json_dumps(payload, indent=2),
        "updated_at": payload["updated_at"],
    }
