from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from typing import Any

from sqlmodel import Session, select

from backend.core.ai.app_config import AI_APP_DEVICE_AGENT, resolve_ai_app_runtime_config
from backend.core.ai.chat import OllamaClientError, chat_with_provider, get_ai_provider_status
from backend.core.devices.device import get_device_id
from backend.db import engine
from backend.models import AppSetting, DeviceAgentSession, DeviceAgentTurn, User


DEVICE_AGENT_CONFIG_KEY = "system:device_agent:config"
DEVICE_AGENT_DEFAULT_PROVIDER = "codex-cli"
DEVICE_AGENT_DEFAULT_MODEL = ""
DEVICE_AGENT_FALLBACK_MODEL = "gpt-5.5"
DEVICE_AGENT_RUNTIME_CONTEXT_KEY = "_device_agent_runtime"
DEVICE_AGENT_DEFAULT_AI_TIMEOUT_SECONDS = 300


class DeviceAgentError(RuntimeError):
    pass


def _now() -> float:
    return time.time()


def _hostname() -> str:
    return socket.gethostname()


def _device_agent_ai_timeout_seconds() -> float:
    raw = os.getenv("CODEYUN_DEVICE_AGENT_AI_TIMEOUT_SECONDS")
    try:
        value = float(raw) if raw is not None else DEVICE_AGENT_DEFAULT_AI_TIMEOUT_SECONDS
    except ValueError:
        value = DEVICE_AGENT_DEFAULT_AI_TIMEOUT_SECONDS
    return max(5.0, min(value, 1800.0))


def _default_config() -> dict[str, Any]:
    hostname = _hostname()
    return {
        "enabled": True,
        "display_name": hostname,
        "device_role": "CodeYun 设备节点",
        "local_context": "",
        "responsibilities": "",
        "default_provider": DEVICE_AGENT_DEFAULT_PROVIDER,
        "default_model": DEVICE_AGENT_DEFAULT_MODEL,
        "updated_at": None,
    }


def _normalize_config(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    defaults = _default_config()
    return {
        "enabled": raw.get("enabled") is not False,
        "display_name": str(raw.get("display_name") or defaults["display_name"]).strip() or defaults["display_name"],
        "device_role": str(raw.get("device_role") or defaults["device_role"]).strip() or defaults["device_role"],
        "local_context": str(raw.get("local_context") or "").strip(),
        "responsibilities": str(raw.get("responsibilities") or "").strip(),
        "default_provider": str(raw.get("default_provider") or defaults["default_provider"]).strip() or defaults["default_provider"],
        "default_model": str(raw.get("default_model") or "").strip(),
        "updated_at": raw.get("updated_at") if isinstance(raw.get("updated_at"), (int, float)) else None,
    }


def get_device_agent_config(session: Session) -> dict[str, Any]:
    row = session.get(AppSetting, DEVICE_AGENT_CONFIG_KEY)
    return _normalize_config(row.value if row is not None else None)


def save_device_agent_config(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    current = get_device_agent_config(session)
    next_config = _normalize_config({**current, **dict(payload or {})})
    next_config["updated_at"] = _now()
    row = session.get(AppSetting, DEVICE_AGENT_CONFIG_KEY)
    if row is None:
        row = AppSetting(key=DEVICE_AGENT_CONFIG_KEY, value=next_config)
    else:
        row.value = next_config
        row.updated_at = _now()
    session.add(row)
    session.commit()
    return next_config


def get_device_agent_manifest(session: Session, *, current_user: User | None = None) -> dict[str, Any]:
    config = get_device_agent_config(session)
    runtime = resolve_device_agent_runtime_config(session, current_user=current_user, config=config)
    ai_provider = _get_device_agent_ai_provider_status(runtime)
    if not config["enabled"]:
        status = "disabled"
    elif ai_provider["available"]:
        status = "available"
    else:
        status = "degraded"
    return {
        "device_id": get_device_id(),
        "hostname": _hostname(),
        "enabled": bool(config["enabled"]),
        "display_name": config["display_name"],
        "device_role": config["device_role"],
        "local_context": config["local_context"],
        "responsibilities": config["responsibilities"],
        "default_provider": runtime["provider"],
        "default_model": runtime["model"],
        "configured_provider": config["default_provider"],
        "configured_model": config["default_model"],
        "status": status,
        "ai_provider": ai_provider,
        "agent": {
            "name": "Device Agent",
            "module": "device_agent",
            "version": "v1",
        },
    }


def resolve_device_agent_runtime_config(
    session: Session,
    *,
    current_user: User | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_config = config or get_device_agent_config(session)
    provider_override = str(normalized_config.get("default_provider") or "").strip() or None
    model_override = str(normalized_config.get("default_model") or "").strip() or None
    runtime = resolve_ai_app_runtime_config(
        session=session,
        current_user=current_user,
        app_id=AI_APP_DEVICE_AGENT,
        provider=provider_override,
        model=model_override,
    )
    provider_id = str(runtime.get("provider") or DEVICE_AGENT_DEFAULT_PROVIDER).strip() or DEVICE_AGENT_DEFAULT_PROVIDER
    model = str(runtime.get("model") or DEVICE_AGENT_FALLBACK_MODEL).strip() or DEVICE_AGENT_FALLBACK_MODEL
    return {
        "provider": provider_id,
        "model": model,
        "base_url": runtime.get("base_url"),
        "api_key": runtime.get("api_key"),
        "extra_providers": runtime.get("extra_providers") or (),
    }


def _get_device_agent_ai_provider_status(runtime: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(runtime.get("provider") or DEVICE_AGENT_DEFAULT_PROVIDER).strip() or DEVICE_AGENT_DEFAULT_PROVIDER
    model = str(runtime.get("model") or DEVICE_AGENT_FALLBACK_MODEL).strip() or DEVICE_AGENT_FALLBACK_MODEL
    try:
        status = get_ai_provider_status(provider_id=provider_id)
        return {
            "provider": provider_id,
            "model": model,
            "available": bool(status.get("available")),
            "configured": bool(status.get("configured")),
            "kind": status.get("kind"),
            "label": status.get("label"),
            "error": status.get("error"),
        }
    except Exception as exc:
        return {
            "provider": provider_id,
            "model": model,
            "available": False,
            "configured": False,
            "kind": "",
            "label": provider_id,
            "error": str(exc),
        }


def _normalize_requester(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    kind = str(raw.get("kind") or "device").strip().lower()
    if kind not in {"device", "user", "system"}:
        kind = "device"
    return {
        "kind": kind,
        "id": str(raw.get("id") or "").strip(),
        "display_name": str(raw.get("display_name") or "").strip(),
    }


def _normalize_request_type(value: Any) -> str:
    normalized = str(value or "ask").strip().lower()
    return normalized if normalized in {"ask", "diagnose", "delegate", "repair"} else "ask"


def _session_title(instruction: str) -> str:
    text = " ".join((instruction or "").strip().split())
    return text[:80] or "设备代理会话"


def create_device_agent_session(
    session: Session,
    *,
    requester: dict[str, Any] | None,
    request_type: str,
    instruction: str,
    context: dict[str, Any] | None = None,
    title: str | None = None,
    current_user: User | None = None,
) -> dict[str, Any]:
    if not instruction.strip():
        raise DeviceAgentError("请求内容不能为空")

    normalized_requester = _normalize_requester(requester)
    now = _now()
    item = DeviceAgentSession(
        local_device_id=get_device_id(),
        peer_device_id=normalized_requester["id"],
        peer_name=normalized_requester["display_name"],
        requester_kind=normalized_requester["kind"],
        title=(title or "").strip() or _session_title(instruction),
        status="pending",
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    turn = _create_turn(
        session,
        item,
        requester=normalized_requester,
        request_type=request_type,
        instruction=instruction,
        context=context,
        runtime_config=resolve_device_agent_runtime_config(session, current_user=current_user),
    )
    return serialize_device_agent_session(item, turns=[turn])


def append_device_agent_turn(
    session: Session,
    session_id: str,
    *,
    requester: dict[str, Any] | None,
    request_type: str,
    instruction: str,
    context: dict[str, Any] | None = None,
    current_user: User | None = None,
) -> dict[str, Any]:
    item = session.get(DeviceAgentSession, session_id)
    if item is None:
        raise DeviceAgentError("设备代理会话不存在")
    if not instruction.strip():
        raise DeviceAgentError("请求内容不能为空")
    normalized_requester = _normalize_requester(requester)
    turn = _create_turn(
        session,
        item,
        requester=normalized_requester,
        request_type=request_type,
        instruction=instruction,
        context=context,
        runtime_config=resolve_device_agent_runtime_config(session, current_user=current_user),
    )
    return serialize_device_agent_turn(turn)


def _create_turn(
    session: Session,
    item: DeviceAgentSession,
    *,
    requester: dict[str, Any],
    request_type: str,
    instruction: str,
    context: dict[str, Any] | None,
    runtime_config: dict[str, Any],
) -> DeviceAgentTurn:
    now = _now()
    turn_context = dict(context or {})
    turn_context[DEVICE_AGENT_RUNTIME_CONTEXT_KEY] = {
        "provider": str(runtime_config.get("provider") or DEVICE_AGENT_DEFAULT_PROVIDER),
        "model": str(runtime_config.get("model") or DEVICE_AGENT_FALLBACK_MODEL),
    }
    turn = DeviceAgentTurn(
        session_id=item.id,
        role="requester",
        requester=requester,
        request_type=_normalize_request_type(request_type),
        instruction=instruction.strip(),
        context=turn_context,
        status="pending",
        stage="starting",
        stage_label="正在启动设备代理",
        heartbeat_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(turn)
    session.commit()
    session.refresh(turn)

    item.status = "pending"
    item.last_turn_id = turn.id
    item.updated_at = now
    session.add(item)
    session.commit()

    execution_id = start_device_agent_turn(turn.id)
    turn.queue_task_id = execution_id
    turn.updated_at = _now()
    session.add(turn)
    session.commit()
    session.refresh(turn)
    return turn


def start_device_agent_turn(turn_id: str) -> str:
    """Start an agent turn immediately, independently of the shared job queue."""
    execution_id = uuid.uuid4().hex
    worker = threading.Thread(
        target=run_device_agent_turn_worker,
        args=(turn_id,),
        name=f"codeyun-device-agent-{execution_id[:8]}",
        daemon=True,
    )
    worker.start()
    return execution_id


def list_device_agent_sessions(session: Session, *, limit: int = 30) -> list[dict[str, Any]]:
    statement = (
        select(DeviceAgentSession)
        .order_by(DeviceAgentSession.updated_at.desc())
        .limit(max(1, min(int(limit or 30), 100)))
    )
    return [serialize_device_agent_session(item, include_turns=False) for item in session.exec(statement).all()]


def get_device_agent_session(session: Session, session_id: str) -> dict[str, Any]:
    item = session.get(DeviceAgentSession, session_id)
    if item is None:
        raise DeviceAgentError("设备代理会话不存在")
    turns = session.exec(
        select(DeviceAgentTurn)
        .where(DeviceAgentTurn.session_id == item.id)
        .order_by(DeviceAgentTurn.created_at)
    ).all()
    return serialize_device_agent_session(item, turns=list(turns))


def get_device_agent_turn(session: Session, turn_id: str) -> dict[str, Any]:
    turn = session.get(DeviceAgentTurn, turn_id)
    if turn is None:
        raise DeviceAgentError("设备代理请求不存在")
    return serialize_device_agent_turn(turn)


def serialize_device_agent_session(
    item: DeviceAgentSession,
    *,
    turns: list[DeviceAgentTurn] | None = None,
    include_turns: bool = True,
) -> dict[str, Any]:
    payload = {
        "id": item.id,
        "local_device_id": item.local_device_id,
        "peer_device_id": item.peer_device_id,
        "peer_name": item.peer_name,
        "requester_kind": item.requester_kind,
        "title": item.title,
        "status": item.status,
        "last_turn_id": item.last_turn_id,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_turns:
        payload["turns"] = [serialize_device_agent_turn(turn) for turn in turns or []]
    return payload


def serialize_device_agent_turn(turn: DeviceAgentTurn) -> dict[str, Any]:
    context = dict(turn.context or {})
    runtime_context = context.pop(DEVICE_AGENT_RUNTIME_CONTEXT_KEY, None)
    return {
        "id": turn.id,
        "session_id": turn.session_id,
        "role": turn.role,
        "requester": turn.requester or {},
        "request_type": turn.request_type,
        "instruction": turn.instruction,
        "context": context,
        "runtime": runtime_context if isinstance(runtime_context, dict) else {},
        "status": turn.status,
        "stage": turn.stage,
        "stage_label": turn.stage_label,
        "queue_task_id": turn.queue_task_id,
        "heartbeat_at": turn.heartbeat_at,
        "result_report": turn.result_report or {},
        "error_message": turn.error_message,
        "created_at": turn.created_at,
        "started_at": turn.started_at,
        "finished_at": turn.finished_at,
        "updated_at": turn.updated_at,
    }


def _update_turn(session: Session, turn: DeviceAgentTurn, *, status: str | None = None, stage: str, stage_label: str) -> None:
    now = _now()
    if status is not None:
        turn.status = status
    turn.stage = stage
    turn.stage_label = stage_label
    turn.heartbeat_at = now
    turn.updated_at = now
    if status == "running" and turn.started_at is None:
        turn.started_at = now
    session.add(turn)
    parent = session.get(DeviceAgentSession, turn.session_id)
    if parent is not None:
        parent.status = turn.status
        parent.updated_at = now
        parent.last_turn_id = turn.id
        session.add(parent)
    session.commit()


def run_device_agent_turn_worker(turn_id: str) -> dict[str, Any]:
    with Session(engine) as session:
        turn = session.get(DeviceAgentTurn, turn_id)
        if turn is None:
            raise DeviceAgentError("设备代理请求不存在")
        parent = session.get(DeviceAgentSession, turn.session_id)
        if parent is None:
            raise DeviceAgentError("设备代理会话不存在")

        try:
            _update_turn(session, turn, status="running", stage="loading_context", stage_label="读取设备代理身份")
            config = get_device_agent_config(session)
            if not config["enabled"]:
                raise DeviceAgentError("设备代理未启用")
            runtime = _resolve_turn_runtime(session, turn, config)

            _update_turn(session, turn, status="running", stage="building_prompt", stage_label="整理会话上下文")
            prompt = build_device_agent_prompt(session, config, parent, turn)

            _update_turn(session, turn, status="running", stage="calling_ai", stage_label="调用设备代理模型")
            response = chat_with_provider(
                provider_id=runtime["provider"],
                model=runtime["model"],
                messages=[{"role": "user", "content": prompt}],
                system_prompt=build_device_agent_system_prompt(config),
                response_format="json",
                timeout_seconds=_device_agent_ai_timeout_seconds(),
            )
            content = str(response.get("content") or "").strip()
            report = _parse_report(content)

            completed_at = _now()
            turn.status = "completed"
            turn.stage = "completed"
            turn.stage_label = "已完成"
            turn.result_report = report
            turn.error_message = None
            turn.heartbeat_at = completed_at
            turn.finished_at = completed_at
            turn.updated_at = completed_at
            session.add(turn)
            parent.status = "completed"
            parent.updated_at = completed_at
            parent.last_turn_id = turn.id
            session.add(parent)
            session.commit()
            return serialize_device_agent_turn(turn)
        except (DeviceAgentError, OllamaClientError, Exception) as exc:
            failed_at = _now()
            turn.status = "failed"
            turn.stage = "failed"
            turn.stage_label = "设备代理执行失败"
            turn.error_message = str(exc)
            turn.heartbeat_at = failed_at
            turn.finished_at = failed_at
            turn.updated_at = failed_at
            turn.result_report = {
                "status": "failed",
                "summary": str(exc),
                "findings": [],
                "actions_taken": [],
                "not_verified": [],
                "suggested_next_steps": [],
                "final_message": f"设备代理执行失败：{exc}",
            }
            session.add(turn)
            parent.status = "failed"
            parent.updated_at = failed_at
            parent.last_turn_id = turn.id
            session.add(parent)
            session.commit()
            return serialize_device_agent_turn(turn)


def build_device_agent_system_prompt(config: dict[str, Any]) -> str:
    return "\n".join(
        [
            "你是当前设备的 CodeYun 设备代理。",
            "",
            "你的身份：",
            "- 你代表当前这台已部署 CodeYun 的设备。",
            f"- 当前设备标识：{get_device_id()}",
            f"- 当前设备名称：{config.get('display_name') or _hostname()}",
            f"- 当前主机名：{_hostname()}",
            f"- 当前设备职责：{config.get('device_role') or 'CodeYun 设备节点'}",
            f"- 本机上下文：{config.get('local_context') or '未配置'}",
            f"- 负责事项：{config.get('responsibilities') or '未配置'}",
            "",
            "协作对象：",
            "- 你主要会收到其他可信 CodeYun 设备发来的询问、诊断、任务委托或修复请求。",
            "- 也可能收到来自 CodeYun UI 或调试工具的同类请求。",
            "- 请求能到达这里，表示对方已通过 CodeYun 设备 token 或用户权限访问控制。",
            "- 你应把请求理解为围绕当前设备环境的协作请求，而不是普通闲聊。",
            "",
            "职责要求：",
            "1. 站在当前设备管理员的角度处理请求。",
            "2. 明确区分当前设备环境和请求方环境。",
            "3. 优先基于当前设备的真实状态、文件、服务、日志、登录态、浏览器状态、业务接口和本地工具判断。",
            "4. 对排查类请求，应主动执行必要检查，并汇报检查进展。",
            "5. 对修复类请求，可以在当前设备上执行合理修复动作。",
            "6. 不要假装执行过未执行的检查；不确定就明确说明。",
            "7. 不要原文返回密钥、Token、Cookie、密码等敏感内容，必要时脱敏。",
            "8. 最终返回执行报告：结论、证据、已执行动作、未确认事项、建议下一步。",
            "",
            "最终只输出一个 JSON 对象，不要使用 Markdown 代码块。字段包括：status、summary、findings、actions_taken、not_verified、suggested_next_steps、final_message。",
        ]
    )


def build_device_agent_prompt(
    session: Session,
    config: dict[str, Any],
    parent: DeviceAgentSession,
    turn: DeviceAgentTurn,
) -> str:
    previous_turns = session.exec(
        select(DeviceAgentTurn)
        .where(DeviceAgentTurn.session_id == parent.id)
        .where(DeviceAgentTurn.id != turn.id)
        .order_by(DeviceAgentTurn.created_at.desc())
        .limit(8)
    ).all()
    history = [
        {
            "request_type": item.request_type,
            "instruction": item.instruction,
            "status": item.status,
            "summary": (item.result_report or {}).get("summary") if isinstance(item.result_report, dict) else "",
        }
        for item in reversed(previous_turns)
    ]
    payload = {
        "requester": turn.requester or {},
        "request_type": turn.request_type,
            "instruction": turn.instruction,
            "context": _public_turn_context(turn.context),
            "session": {
            "id": parent.id,
            "title": parent.title,
            "peer_device_id": parent.peer_device_id,
            "peer_name": parent.peer_name,
            "history": history,
        },
        "current_device": {
            "device_id": get_device_id(),
            "display_name": config.get("display_name"),
            "hostname": _hostname(),
            "device_role": config.get("device_role"),
            "local_context": config.get("local_context"),
            "responsibilities": config.get("responsibilities"),
        },
    }
    return "\n".join(
        [
            "请处理以下设备代理请求，并返回结构化执行报告。",
            "",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
    )


def _public_turn_context(value: Any) -> dict[str, Any]:
    context = dict(value) if isinstance(value, dict) else {}
    context.pop(DEVICE_AGENT_RUNTIME_CONTEXT_KEY, None)
    return context


def _resolve_turn_runtime(session: Session, turn: DeviceAgentTurn, config: dict[str, Any]) -> dict[str, str]:
    raw_context = turn.context if isinstance(turn.context, dict) else {}
    raw_runtime = raw_context.get(DEVICE_AGENT_RUNTIME_CONTEXT_KEY)
    runtime_context = raw_runtime if isinstance(raw_runtime, dict) else {}
    provider = str(runtime_context.get("provider") or "").strip()
    model = str(runtime_context.get("model") or "").strip()
    if provider and model:
        return {"provider": provider, "model": model}
    runtime = resolve_device_agent_runtime_config(session, config=config)
    return {"provider": runtime["provider"], "model": runtime["model"]}


def _parse_report(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        payload = {
            "status": "partial",
            "summary": content[:200] or "设备代理已返回非 JSON 文本",
            "findings": [],
            "actions_taken": [],
            "not_verified": ["模型返回内容不是 JSON 对象"],
            "suggested_next_steps": [],
            "final_message": content,
        }
    if not isinstance(payload, dict):
        payload = {"status": "partial", "summary": str(payload), "final_message": str(payload)}
    return {
        "status": str(payload.get("status") or "completed"),
        "summary": str(payload.get("summary") or payload.get("final_message") or "").strip(),
        "findings": payload.get("findings") if isinstance(payload.get("findings"), list) else [],
        "actions_taken": payload.get("actions_taken") if isinstance(payload.get("actions_taken"), list) else [],
        "not_verified": payload.get("not_verified") if isinstance(payload.get("not_verified"), list) else [],
        "suggested_next_steps": payload.get("suggested_next_steps") if isinstance(payload.get("suggested_next_steps"), list) else [],
        "final_message": str(payload.get("final_message") or payload.get("summary") or "").strip(),
    }
