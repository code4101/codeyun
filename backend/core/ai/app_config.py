from __future__ import annotations

import time
from typing import Any

from sqlmodel import Session

from backend.core.ai.chat import AiProviderConfig, get_ai_provider, get_default_ai_provider_id
from backend.core.ai.chat_user_config import (
    AiChatUserConfigError,
    get_user_ai_chat_provider_runtime_config,
    list_user_ai_chat_custom_provider_configs,
)
from backend.core.ai.ollama_access_keys import ensure_ollama_access_key_allowed
from backend.core.system_ai_resources import (
    SystemAiResourceError,
    resolve_system_ai_resource,
)
from backend.models import AppSetting, User


AI_APP_NOTE_TAXONOMY = "note-taxonomy"
AI_APP_GIT_COMMIT = "ai-git-commit"
AI_APP_CODEX_DIARY = "codex-diary"
AI_APP_CODEX_DAILY_SUMMARY = "codex-daily-summary"
AI_APP_NOTE_SHEET_CLOCKIN_LINK_DETECTION = "note-sheet-clockin-link-detection"
AI_APP_NOTE_SHEET_EXCEL_IMPORT = "note-sheet-excel-import"
AI_APP_RIME_LINT = "rime-lint"
AI_APP_FANXIU_PSEUDOCODE = "fanxiu-pseudocode"
AI_APP_FANXIU_GAME_MACRO_ANNOTATION = "fanxiu-game-macro-annotation"
AI_APP_ATTENDANCE_PRECHECK = "attendance-precheck"
AI_APP_CODECLAW = "codeclaw"
AI_APP_WECHAT_DAILY_SUMMARY = "wechat-daily-summary"
AI_APP_WECHAT_CHAT_BOOK = "wechat-chat-book"
AI_APP_DEVICE_AGENT = "device-agent"
AI_APP_SKILL_BOOK_TRANSLATION = "skill-book-translation"
AI_APP_CODEX_CLI_PROVIDER = "codex-cli"
AI_APP_CODEX_SPARK_MODEL = "gpt-5.3-codex-spark"
AI_APP_OLLAMA_PROVIDER = "ollama"
AI_APP_OLLAMA_GIT_COMMIT_MODEL = "qwen3.5:4b-instruct"
AI_APP_DEEPSEEK_PROVIDER = "deepseek"
AI_APP_DEEPSEEK_PRO_MODEL = "deepseek-v4-pro"
AI_APP_GIT_COMMIT_DEFAULT_PROVIDER = AI_APP_OLLAMA_PROVIDER
AI_APP_GIT_COMMIT_DEFAULT_MODEL = AI_APP_OLLAMA_GIT_COMMIT_MODEL
AI_APP_CODEX_DIARY_DEFAULT_PROVIDER = AI_APP_CODEX_CLI_PROVIDER
AI_APP_CODEX_DIARY_DEFAULT_MODEL = AI_APP_CODEX_SPARK_MODEL
_CODEX_CLI_PROVIDER_ALIASES = {"codex", "codex-cli", "custom-codex-cli"}
_AI_APP_DEFAULTS: dict[str, tuple[str, str]] = {
    AI_APP_NOTE_TAXONOMY: (AI_APP_CODEX_CLI_PROVIDER, AI_APP_CODEX_SPARK_MODEL),
    AI_APP_GIT_COMMIT: (AI_APP_GIT_COMMIT_DEFAULT_PROVIDER, AI_APP_GIT_COMMIT_DEFAULT_MODEL),
    AI_APP_CODEX_DIARY: (AI_APP_CODEX_DIARY_DEFAULT_PROVIDER, AI_APP_CODEX_DIARY_DEFAULT_MODEL),
    AI_APP_CODEX_DAILY_SUMMARY: (AI_APP_DEEPSEEK_PROVIDER, AI_APP_DEEPSEEK_PRO_MODEL),
    AI_APP_NOTE_SHEET_CLOCKIN_LINK_DETECTION: (AI_APP_CODEX_CLI_PROVIDER, AI_APP_CODEX_SPARK_MODEL),
    AI_APP_NOTE_SHEET_EXCEL_IMPORT: (AI_APP_DEEPSEEK_PROVIDER, AI_APP_DEEPSEEK_PRO_MODEL),
    AI_APP_RIME_LINT: (AI_APP_CODEX_CLI_PROVIDER, AI_APP_CODEX_SPARK_MODEL),
    AI_APP_FANXIU_PSEUDOCODE: (AI_APP_DEEPSEEK_PROVIDER, AI_APP_DEEPSEEK_PRO_MODEL),
    AI_APP_FANXIU_GAME_MACRO_ANNOTATION: (AI_APP_CODEX_CLI_PROVIDER, "gpt-5.5"),
    AI_APP_ATTENDANCE_PRECHECK: (AI_APP_DEEPSEEK_PROVIDER, AI_APP_DEEPSEEK_PRO_MODEL),
    AI_APP_CODECLAW: (AI_APP_CODEX_CLI_PROVIDER, AI_APP_CODEX_SPARK_MODEL),
    AI_APP_WECHAT_DAILY_SUMMARY: (AI_APP_CODEX_CLI_PROVIDER, AI_APP_CODEX_SPARK_MODEL),
    AI_APP_WECHAT_CHAT_BOOK: (AI_APP_CODEX_CLI_PROVIDER, AI_APP_CODEX_SPARK_MODEL),
    AI_APP_DEVICE_AGENT: (AI_APP_CODEX_CLI_PROVIDER, ""),
    AI_APP_SKILL_BOOK_TRANSLATION: (AI_APP_DEEPSEEK_PROVIDER, AI_APP_DEEPSEEK_PRO_MODEL),
}
SYSTEM_AI_RESOURCE_FALLBACK_POLICIES: dict[str, frozenset[str]] = {
    AI_APP_NOTE_SHEET_EXCEL_IMPORT: frozenset({
        AI_APP_DEEPSEEK_PROVIDER,
    }),
    AI_APP_SKILL_BOOK_TRANSLATION: frozenset({
        AI_APP_DEEPSEEK_PROVIDER,
    }),
}
_AI_APP_CODEX_SPARK_DEFAULT_IDS = {
    app_id
    for app_id, (provider, model) in _AI_APP_DEFAULTS.items()
    if provider == AI_APP_CODEX_CLI_PROVIDER and model == AI_APP_CODEX_SPARK_MODEL
}

AI_APP_CONFIG_SETTING_KEY_PREFIX = "ai_app.config.user"
LEGACY_AI_GIT_COMMIT_CONFIG_SETTING_KEY_PREFIX = "ai_git_commit.config.user"

AI_APP_DEFINITIONS: tuple[dict[str, str], ...] = (
    {
        "id": AI_APP_NOTE_TAXONOMY,
        "group": "星图笔记",
        "label": "笔记分类",
        "description": "仅分析当前标题，并参考已有条目的标题、分类、形态、阶段后回写结果。",
    },
    {
        "id": AI_APP_GIT_COMMIT,
        "group": "开发工具",
        "label": "AI提交",
        "description": "生成 Git 提交信息，GitHub 项目自动提交和分层归纳提交共用这一组模型配置。",
    },
    {
        "id": AI_APP_CODEX_DIARY,
        "group": "星图笔记",
        "label": "Codex 星图日记",
        "description": "按日期读取 Codex 会话，按主题拆分后写入星图笔记节点。",
    },
    {
        "id": AI_APP_CODEX_DAILY_SUMMARY,
        "group": "开发工具",
        "label": "Codex 日志总结",
        "description": "读取 Codex 会话日志，生成日报或多设备汇总结果，不直接创建星图笔记节点。",
    },
    {
        "id": AI_APP_NOTE_SHEET_CLOCKIN_LINK_DETECTION,
        "group": "考勤",
        "label": "表格打卡链接检测",
        "description": "在问卷星/打卡页面中辅助判断目标打卡链接。",
    },
    {
        "id": AI_APP_NOTE_SHEET_EXCEL_IMPORT,
        "group": "考勤",
        "label": "考勤表 Excel 导入",
        "description": "把考勤 Excel 内容映射为星图表格数据行。",
    },
    {
        "id": AI_APP_RIME_LINT,
        "group": "文本工具",
        "label": "Rime 文本校对",
        "description": "对 Rime 词库或候选文本做轻量校对。",
    },
    {
        "id": AI_APP_FANXIU_PSEUDOCODE,
        "group": "凡修",
        "label": "凡修伪代码编译",
        "description": "把凡修卡片伪代码编译为可执行脚本。",
    },
    {
        "id": AI_APP_FANXIU_GAME_MACRO_ANNOTATION,
        "group": "凡修",
        "label": "游戏窗口录制标注",
        "description": "录制宏时根据截图和操作点辅助判断按钮、图标或拖拽控件的 shape 区域。",
    },
    {
        "id": AI_APP_ATTENDANCE_PRECHECK,
        "group": "考勤",
        "label": "问卷预检报告",
        "description": "为考勤问卷反馈行生成并填写 AI 初判文本。",
    },
    {
        "id": AI_APP_CODECLAW,
        "group": "开发工具",
        "label": "CodeClaw 微信接入",
        "description": "微信消息入口使用的 AI 模型，默认交给本机 Codex CLI 处理并回复微信。",
    },
    {
        "id": AI_APP_WECHAT_DAILY_SUMMARY,
        "group": "星图笔记",
        "label": "微信聊天日总结",
        "description": "读取本机微信聊天数据，按联系人或群生成日总结并写入星图笔记。",
    },
    {
        "id": AI_APP_WECHAT_CHAT_BOOK,
        "group": "星图笔记",
        "label": "微信群聊事件成书",
        "description": "从指定微信群聊按语义线程归组重点事件，忠实编辑并按月份、日期归档到图书馆。",
    },
)


class AiAppConfigError(RuntimeError):
    """Raised when persisted AI app configuration cannot be used."""


def build_ai_app_config_setting_key(user_id: int) -> str:
    return f"{AI_APP_CONFIG_SETTING_KEY_PREFIX}.{int(user_id)}"


def build_legacy_ai_git_commit_config_setting_key(user_id: int) -> str:
    return f"{LEGACY_AI_GIT_COMMIT_CONFIG_SETTING_KEY_PREFIX}.{int(user_id)}"


def _known_app_ids() -> set[str]:
    return {item["id"] for item in AI_APP_DEFINITIONS} | set(_AI_APP_DEFAULTS)


def _normalize_app_id(value: str | None) -> str:
    normalized = (value or "").strip()
    if normalized not in _known_app_ids():
        raise AiAppConfigError("未知 AI 功能配置")
    return normalized


def _normalize_app_config_item(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    updated_at = item.get("updated_at")
    return {
        "enabled": item.get("enabled") is not False,
        "provider": str(item.get("provider") or item.get("provider_id") or "").strip().lower(),
        "model": str(item.get("model") or "").strip(),
        "updated_at": float(updated_at) if isinstance(updated_at, (int, float)) else None,
    }


def _is_codex_cli_provider(value: Any) -> bool:
    normalized = str(value or "").strip().lower().replace("_", "-")
    return normalized in _CODEX_CLI_PROVIDER_ALIASES or normalized.endswith("-codex-cli")


def _coerce_app_config_for_app(app_id: str, item: dict[str, Any]) -> dict[str, Any]:
    if app_id == AI_APP_GIT_COMMIT:
        provider = str(item.get("provider") or "").strip().lower()
        model = str(item.get("model") or "").strip()
        if (
            not provider
            and not model
            or provider == AI_APP_OLLAMA_PROVIDER and model in {"", "qwen3-vl:4b"}
            or _is_codex_cli_provider(provider) and model in {"", AI_APP_CODEX_SPARK_MODEL, "gpt-5.4", "deepseek-v4-flash"}
            or provider == AI_APP_DEEPSEEK_PROVIDER and model in {"", "deepseek-v4-flash"}
        ):
            return {
                **item,
                "provider": AI_APP_OLLAMA_PROVIDER,
                "model": AI_APP_OLLAMA_GIT_COMMIT_MODEL,
            }
    if app_id in _AI_APP_CODEX_SPARK_DEFAULT_IDS:
        provider = str(item.get("provider") or "").strip().lower()
        model = str(item.get("model") or "").strip()
        if (provider == AI_APP_DEEPSEEK_PROVIDER and model in {"", "deepseek-v4-flash"}) or (
            _is_codex_cli_provider(provider) and model in {"", "gpt-5.4", "deepseek-v4-flash"}
        ):
            return {
                **item,
                "provider": AI_APP_CODEX_CLI_PROVIDER,
                "model": AI_APP_CODEX_SPARK_MODEL,
            }
    if app_id == AI_APP_CODEX_DIARY:
        provider = str(item.get("provider") or "").strip().lower()
        model = str(item.get("model") or "").strip()
        if (provider == AI_APP_DEEPSEEK_PROVIDER and model in {"", "deepseek-v4-flash"}) or (
            _is_codex_cli_provider(provider) and model in {"", "gpt-5.4", "deepseek-v4-flash"}
        ):
            return {
                **item,
                "provider": AI_APP_CODEX_DIARY_DEFAULT_PROVIDER,
                "model": AI_APP_CODEX_DIARY_DEFAULT_MODEL,
            }
    return item


def _default_app_config(app_id: str) -> dict[str, Any]:
    provider, model = _AI_APP_DEFAULTS.get(app_id, ("", ""))
    return {
        "enabled": True,
        "provider": provider,
        "model": model,
        "updated_at": None,
    }


def _load_app_config_map(session: Session, user_id: int) -> dict[str, dict[str, Any]]:
    row = session.get(AppSetting, build_ai_app_config_setting_key(user_id))
    if row is None or not isinstance(row.value, dict):
        return {}

    raw_apps = row.value.get("apps")
    if not isinstance(raw_apps, dict):
        return {}

    apps: dict[str, dict[str, Any]] = {}
    for app_id, raw_item in raw_apps.items():
        try:
            normalized_app_id = _normalize_app_id(str(app_id))
        except AiAppConfigError:
            continue
        apps[normalized_app_id] = _normalize_app_config_item(raw_item)
    return apps


def _save_app_config_map(session: Session, user_id: int, apps: dict[str, dict[str, Any]]) -> None:
    setting_key = build_ai_app_config_setting_key(user_id)
    row = session.get(AppSetting, setting_key)
    payload = {"apps": apps}
    now = time.time()
    if row is None:
        row = AppSetting(key=setting_key, value=payload, updated_at=now)
    else:
        row.value = payload
        row.updated_at = now
    session.add(row)
    session.commit()


def _load_legacy_ai_git_commit_config(session: Session, user_id: int) -> dict[str, Any] | None:
    row = session.get(AppSetting, build_legacy_ai_git_commit_config_setting_key(user_id))
    if row is None or not isinstance(row.value, dict):
        return None
    provider = str(row.value.get("provider_id") or row.value.get("provider") or "").strip().lower()
    model = str(row.value.get("model") or "").strip()
    if not provider and not model:
        return None
    return {
        "enabled": True,
        "provider": provider,
        "model": model,
        "updated_at": float(row.updated_at) if isinstance(row.updated_at, (int, float)) else None,
    }


def get_user_ai_app_config(session: Session, user_id: int, app_id: str) -> dict[str, Any]:
    normalized_app_id = _normalize_app_id(app_id)
    apps = _load_app_config_map(session, user_id)
    item = apps.get(normalized_app_id)
    if item is None and normalized_app_id == AI_APP_GIT_COMMIT:
        item = _load_legacy_ai_git_commit_config(session, user_id)
    if item is None:
        item = _default_app_config(normalized_app_id)
    item = _coerce_app_config_for_app(normalized_app_id, item)
    return {
        "id": normalized_app_id,
        **item,
    }


def list_user_ai_app_configs(session: Session, user_id: int) -> list[dict[str, Any]]:
    return [
        {
            **definition,
            **get_user_ai_app_config(session, user_id, definition["id"]),
        }
        for definition in AI_APP_DEFINITIONS
    ]


def save_user_ai_app_config(
    session: Session,
    user_id: int,
    app_id: str,
    *,
    enabled: bool = True,
    provider: str = "",
    model: str = "",
) -> dict[str, Any]:
    normalized_app_id = _normalize_app_id(app_id)
    apps = _load_app_config_map(session, user_id)
    now = time.time()
    apps[normalized_app_id] = {
        "enabled": bool(enabled),
        "provider": provider.strip().lower(),
        "model": model.strip(),
        "updated_at": now,
    }
    apps[normalized_app_id] = _coerce_app_config_for_app(normalized_app_id, apps[normalized_app_id])
    _save_app_config_map(session, user_id, apps)
    return {
        "id": normalized_app_id,
        **apps[normalized_app_id],
    }


def resolve_ai_app_runtime_config(
    *,
    session: Session,
    current_user: User | None,
    app_id: str,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    try:
        normalized_app_id = _normalize_app_id(app_id)
        app_config = (
            get_user_ai_app_config(session, current_user.id, normalized_app_id)
            if current_user is not None
            else {"provider": "", "model": "", "enabled": True}
        )
        configured_provider = str(app_config.get("provider") or "").strip().lower()
        resolved_provider = (provider or configured_provider or get_default_ai_provider_id()).strip().lower()
        if not resolved_provider:
            resolved_provider = get_default_ai_provider_id()

        resolved_base_url = (base_url or "").strip()
        resolved_api_key = (api_key or "").strip()
        resolved_model = (model or "").strip() or str(app_config.get("model") or "").strip()
        extra_providers: tuple[AiProviderConfig, ...] = ()

        if current_user is not None:
            extra_providers = tuple(list_user_ai_chat_custom_provider_configs(session, current_user.id))
            saved_config = get_user_ai_chat_provider_runtime_config(session, current_user.id, resolved_provider)
            if not resolved_base_url:
                resolved_base_url = str(saved_config.get("base_url") or "").strip()
            if not resolved_api_key:
                resolved_api_key = str(saved_config.get("api_key") or "").strip()
            if not resolved_model:
                preferred_models = saved_config.get("preferred_models")
                if isinstance(preferred_models, list):
                    resolved_model = next(
                        (str(item).strip() for item in preferred_models if str(item).strip()),
                        "",
                    )
                if not resolved_model:
                    resolved_model = str(saved_config.get("preferred_model") or "").strip()

        resource_scope = "user"
        system_resource_id = None
        allowed_system_providers = SYSTEM_AI_RESOURCE_FALLBACK_POLICIES.get(
            normalized_app_id,
            frozenset(),
        )
        if current_user is not None and resolved_provider in allowed_system_providers:
            current_provider = get_ai_provider(
                resolved_provider,
                base_url=resolved_base_url or None,
                api_key=resolved_api_key or None,
                extra_providers=extra_providers,
            )
            if not current_provider.configured:
                system_runtime = resolve_system_ai_resource(
                    session=session,
                    provider_id=resolved_provider,
                )
                resolved_base_url = str(system_runtime.get("base_url") or "").strip()
                resolved_api_key = str(system_runtime.get("api_key") or "").strip()
                extra_providers = tuple(system_runtime.get("extra_providers") or ())
                resource_scope = "system"
                system_resource_id = system_runtime["system_resource_id"]

        if resolved_provider == "ollama":
            ensure_ollama_access_key_allowed(session, resolved_api_key)

        return {
            "app": app_id,
            "provider": resolved_provider,
            "base_url": resolved_base_url or None,
            "api_key": resolved_api_key or None,
            "model": resolved_model or None,
            "extra_providers": extra_providers,
            "resource_scope": resource_scope,
            "system_resource_id": system_resource_id,
        }
    except (AiChatUserConfigError, SystemAiResourceError) as exc:
        raise AiAppConfigError(str(exc)) from exc
