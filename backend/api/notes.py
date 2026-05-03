import html
import hashlib
import json
import threading
from typing import Any, List, Optional, Tuple
import re
from datetime import datetime
from types import SimpleNamespace
import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select, func, or_
from sqlalchemy.orm.attributes import flag_modified
from backend.db import get_session
from backend.models import (
    AppSetting,
    CodexDiaryImportRun,
    CodexTextCacheTurn,
    NoteMetadataFeedbackOptimizationRun,
    NoteNode,
    NoteEdge,
    User,
    UserDevice,
)
from backend.schemas import (
    NoteCreate,
    NoteRead,
    NoteUpdate,
    NoteCategoryPaletteResponse,
    NoteCategoryMergeRequest,
    NoteCategoryPaletteUpdateRequest,
    EdgeCreate,
    EdgeRead,
    NoteListRead,
    GraphData,
    NoteFilterRule,
    NoteQueryRequest,
    NoteQueryResponse,
    NoteProgramRequest,
    NoteProgramResponse,
    NoteBatchUpdateRequest,
    NoteBatchUpdateResponse,
    AiNoteCategorizeRequest,
    AiNoteCategorizeResponse,
)
from backend.core.ai_chat import (
    CODEX_CLI_DEFAULT_COMMAND,
    CODEX_CLI_DEFAULT_MODEL,
    AiProviderConfig,
    OllamaClientError,
    chat_with_provider,
    get_default_ai_provider_id,
)
from backend.core.ai_chat_user_config import (
    AiChatUserConfigError,
    get_user_ai_chat_provider_runtime_config,
    list_user_ai_chat_custom_provider_configs,
)
from backend.core.ollama_access_keys import ensure_ollama_access_key_allowed
from backend.core.auth import get_current_active_user
from backend.core.codex_sessions import (
    annotate_codex_daily_summary_source,
    cache_remote_codex_thread_detail,
    cache_remote_codex_workload,
    collect_cached_codex_daily_summary_source,
    collect_codex_daily_summary_source,
    merge_codex_daily_summary_sources,
    resolve_codex_daily_summary_epoch_range,
)
from backend.core.device import get_device_id
from backend.core.feature_access_guard import require_feature_access_dependency
from backend.core.note_access import note_to_response_dict
from backend.core.note_semantics import (
    NOTE_CATEGORY_BUILTIN_KEYS,
    NOTE_CATEGORY_DEFAULT,
    NOTE_FORM_DEFAULT,
    NOTE_KIND_DEFAULT,
    NOTE_LIFECYCLE_STAGE_DEFAULT,
    NOTE_SCENE_DEFAULT,
    NOTE_TYPE_BUILTIN_PALETTE,
    NOTE_TYPE_DEFAULT,
    build_legacy_color_type_key,
    build_note_category_palette_setting_key,
    build_note_type_palette_setting_key,
    derive_legacy_semantics_from_taxonomy,
    derive_note_taxonomy_from_legacy,
    derive_primary_category,
    derive_primary_node_type,
    get_legacy_color_from_type_key,
    is_legacy_color_type_key,
    merge_note_types,
    normalize_lifecycle_stage,
    normalize_note_categories,
    normalize_note_color,
    normalize_note_form,
    normalize_note_scene,
    normalize_note_types,
)
from backend.core.note_progress import (
    get_completion_progress_expr,
    is_note_system_custom_field_key,
    normalize_completion_progress_expr,
    set_completion_progress_expr,
)
from backend.core.note_metadata_feedback import (
    create_note_metadata_feedback_optimization_run,
    get_note_metadata_feedback_status,
    record_note_metadata_feedback_for_created_note,
    record_note_metadata_feedback_for_update,
    serialize_note_metadata_feedback_optimization_run,
)
from backend.core.note_walker import NoteGraphContext, NoteWalker
import time
import uuid

router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("note-tools"))],
)

ALLOWED_ORDER_FIELDS = {"updated_at", "created_at", "start_at", "weight", "title", "private_level"}
NOTE_AI_APP_ID = "note-taxonomy"
NOTE_AI_CATEGORY_DESCRIPTIONS = {
    "general": "默认综合分类",
    "project": "长期性工作，非具体任务容器",
    "module": "项目的组成部分",
    "task": "具体的执行事项",
    "bug": "需要修复的问题",
}
NOTE_AI_FORM_OPTIONS = (
    {"key": "note", "label": "笔记", "description": "普通笔记形态"},
    {"key": "document", "label": "文档", "description": "偏正文排版的文档形态"},
    {"key": "memo", "label": "备忘", "description": "更短平快的便签形态"},
    {"key": "music", "label": "音乐", "description": "音乐作品、专辑或音频素材"},
    {"key": "video", "label": "影视", "description": "电影、剧集、视频或影像资料"},
    {"key": "game", "label": "游戏", "description": "游戏作品、攻略记录或游玩资料"},
    {"key": "book", "label": "书籍", "description": "书籍、电子书或长篇阅读材料"},
)
NOTE_AI_LIFECYCLE_OPTIONS = (
    {"key": "idea", "label": "笔记", "description": "普通记录"},
    {"key": "todo", "label": "想法", "description": "灵感草稿"},
    {"key": "doing", "label": "待办", "description": "准备执行"},
    {"key": "done", "label": "完成", "description": "已完成，可按进度展示"},
    {"key": "delete", "label": "废弃", "description": "已取消"},
)
NOTE_AI_REFERENCE_SAMPLE_LIMIT = 40
NOTE_AI_REFERENCE_PER_COMBO_LIMIT = 4
NOTE_AI_HTML_BREAK_RE = re.compile(r"</?(?:p|div|li|tr|h[1-6]|blockquote)\b[^>]*>|<br\s*/?>", re.IGNORECASE)
NOTE_AI_HTML_TAG_RE = re.compile(r"<[^>]+>")
NOTE_AI_TITLE_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)
NOTE_AI_WHITESPACE_RE = re.compile(r"\s+")
CODEX_DIARY_PROVIDER_ID = "codex-diary-import"
CODEX_DIARY_TIMEOUT_SECONDS = 900.0
CODEX_DIARY_PROMPT_VERSION = "2026-05-02.codex-cli-diary-draft-v1"
CODEX_DIARY_RUN_ID_FIELD = "__codex_diary_run_id"
CODEX_DIARY_DATE_FIELD = "__codex_diary_date"
CODEX_DIARY_SCOPE_FIELD = "__codex_diary_scope_key"
CODEX_DIARY_BLOCK_FIELD = "__codex_diary_block_key"
CODEX_DIARY_SOURCE_THREADS_FIELD = "__codex_source_thread_ids"
CODEX_DIARY_DIRECT_REMOTE_PROXIES = {"http": "", "https": "", "all": "", "no_proxy": "*"}
CODEX_DIARY_TARGET_SECONDS = 3600
CODEX_DIARY_TINY_TAIL_SECONDS = 15 * 60
CODEX_DIARY_RESULT_KEYWORDS = (
    "已",
    "完成",
    "实现",
    "修复",
    "新增",
    "优化",
    "调整",
    "确认",
    "定位",
    "改为",
    "补充",
    "接入",
    "支持",
    "清理",
    "验证",
    "生成",
    "保留",
    "避免",
    "解决",
)
CODEX_DIARY_DIALOGUE_PREFIXES = (
    "我先",
    "我会",
    "我已经",
    "已开始",
    "正在",
    "可以去",
    "你可以",
    "你确定",
    "是不是",
    "是的",
    "可以",
    "好的",
    "对",
    "没错",
    "已改完",
    "已经改完",
    "已经删了",
    "已删除",
)
CODEX_DIARY_TITLE_BAD_VALUES = {
    "是的",
    "可以",
    "好的",
    "对",
    "没错",
    "已改完",
    "已经改完",
    "已经删了",
    "已删除",
}
CODEX_DIARY_TITLE_KEYWORDS = (
    "接口",
    "流程",
    "菜单",
    "入口",
    "列表",
    "视图",
    "页面",
    "表格",
    "字段",
    "目录",
    "缓存",
    "链接",
    "分页",
    "权限",
    "配置",
    "数据",
    "节点",
    "功能",
    "清单",
    "明细",
    "时间线",
    "退款",
    "问卷",
    "课程",
    "设备",
    "Codex",
    "CodeYun",
    "星云",
)
CODEX_DIARY_ITEM_NUMBER_PREFIX_RE = re.compile(
    r"^\s*(?:(?:第?\d+|[一二三四五六七八九十]+)[\.．、)]|[（(](?:\d+|[一二三四五六七八九十]+)[）)])\s*"
)
CODEX_DIARY_HTML_LI_NUMBER_PREFIX_RE = re.compile(
    r"(<li\b[^>]*>\s*(?:<(?:span|p|strong|b|code)\b[^>]*>\s*)*)"
    r"(?:(?:(?:第?\d+|[一二三四五六七八九十]+)[\.．、)]|[（(](?:\d+|[一二三四五六七八九十]+)[）)])\s*)+",
    re.IGNORECASE,
)
CODEX_DIARY_CATEGORY_HINT_STOPWORDS = {
    "修复",
    "新增",
    "调整",
    "优化",
    "兼容",
    "流程",
    "接口",
    "页面",
    "数据",
    "字段",
    "导入",
    "导出",
    "查询",
    "状态",
    "规则",
    "任务",
    "项目",
    "节点",
    "脚本",
    "自动",
    "完成",
    "失败",
    "问题",
    "处理",
    "逻辑",
    "支持",
    "功能",
    "工具",
    "列表",
    "菜单",
    "缓存",
    "配置",
    "统一",
    "补充",
    "清理",
    "验证",
    "保存",
    "读取",
    "写入",
    "同步",
    "生成",
    "更新",
    "删除",
    "选择",
    "运行",
    "本地",
    "远端",
    "代码",
    "前端",
    "后端",
}
CODEX_DIARY_CATEGORY_DOMAIN_ALIASES = (
    (
        ("考勤", "attendance", "kq"),
        (
            "考勤",
            "课程",
            "问卷",
            "问卷星",
            "打卡",
            "念住",
            "禅寺",
            "学员",
            "讲师",
            "退款",
            "微信零钱",
            "在线考勤表",
            "考勤实际完成结点",
            "clockin_table",
            "clockin",
            "wjx",
            "kdocs",
        ),
    ),
    (
        ("凡修", "fanxiu"),
        (
            "凡修",
            "prayer_cycle",
            "祈愿",
            "炼丹",
            "淬体",
            "灵兽",
            "妖王",
            "仙花",
            "宗城",
            "法宝",
            "道具",
            "仙舟",
            "魔道",
            "昆仑",
            "寿元",
            "翠剑",
            "衣橱",
            "抽卡",
        ),
    ),
)


class CodexDiaryImportRunRequest(BaseModel):
    date: str
    entry_ids: List[str] = Field(default_factory=list)
    confirm_duplicate: bool = False


class CodexDiaryImportRunRead(BaseModel):
    id: str
    date: str
    timezone: str
    scope_key: str
    entry_ids: List[str] = Field(default_factory=list)
    entry_snapshot: List[dict[str, Any]] = Field(default_factory=list)
    confirm_duplicate: bool = False
    status: str
    stage: str
    stage_label: str
    source_thread_count: int = 0
    source_turn_count: int = 0
    source_user_message_count: int = 0
    source_assistant_message_count: int = 0
    created_note_count: int = 0
    created_note_ids: List[str] = Field(default_factory=list)
    duplicate_note_ids: List[str] = Field(default_factory=list)
    error_message: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    created_notes: List[dict[str, Any]] = Field(default_factory=list)
    heartbeat_at: Optional[float] = None
    created_at: float
    finished_at: Optional[float] = None
    updated_at: float


class NoteMetadataFeedbackOptimizationRunRequest(BaseModel):
    trigger_reason: str = "manual"


def _build_default_codex_diary_provider() -> AiProviderConfig:
    return AiProviderConfig(
        id=CODEX_DIARY_PROVIDER_ID,
        label="Codex CLI",
        kind="codex_cli",
        base_url=CODEX_CLI_DEFAULT_COMMAND,
        default_model=CODEX_CLI_DEFAULT_MODEL,
        timeout_seconds=CODEX_DIARY_TIMEOUT_SECONDS,
        api_key="",
        supports_stream=False,
        supports_vision=False,
        requires_api_key=False,
        configured=True,
        models=(CODEX_CLI_DEFAULT_MODEL,),
        is_custom=False,
    )


def _parse_codex_diary_date(value: str) -> tuple[str, float, float]:
    try:
        normalized_date, day_start_at, day_end_at = resolve_codex_daily_summary_epoch_range(value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from exc
    return normalized_date, float(day_start_at), float(day_end_at)


def _codex_diary_entry_label(entry: UserDevice | dict[str, Any]) -> str:
    if isinstance(entry, dict):
        return str(entry.get("name") or entry.get("device_id") or entry.get("entry_id") or "").strip()
    return str(entry.name or entry.device_id or entry.entry_id).strip()


def _get_codex_diary_entries(
    session: Session,
    current_user: User,
    entry_ids: List[str] | None,
) -> list[UserDevice]:
    normalized_ids = [
        str(entry_id or "").strip()
        for entry_id in (entry_ids or [])
        if str(entry_id or "").strip()
    ]
    if not normalized_ids:
        return session.exec(
            select(UserDevice)
            .where(
                UserDevice.user_id == current_user.id,
                UserDevice.is_active == True,  # noqa: E712
            )
            .order_by(UserDevice.order_index, UserDevice.created_at, UserDevice.entry_id)
        ).all()

    rows = session.exec(
        select(UserDevice).where(
            UserDevice.user_id == current_user.id,
            UserDevice.entry_id.in_(normalized_ids),
            UserDevice.is_active == True,  # noqa: E712
        )
    ).all()
    row_map = {row.entry_id: row for row in rows}
    missing_ids = [entry_id for entry_id in normalized_ids if entry_id not in row_map]
    if missing_ids:
        raise HTTPException(status_code=404, detail=f"设备不存在或不可用：{', '.join(missing_ids)}")
    return [row_map[entry_id] for entry_id in normalized_ids]


def _ensure_codex_diary_local_entry(entry: UserDevice) -> None:
    if entry.mode != "local":
        raise HTTPException(status_code=400, detail="This entry is not a local entry")
    if entry.device_id != get_device_id():
        raise HTTPException(status_code=409, detail="Local entry device_id does not match current node")


def _snapshot_codex_diary_entries(entries: list[UserDevice]) -> list[dict[str, Any]]:
    return [
        {
            "entry_id": entry.entry_id,
            "user_id": entry.user_id,
            "device_id": entry.device_id,
            "name": _codex_diary_entry_label(entry),
            "mode": entry.mode,
            "server_url": entry.server_url,
            "token": entry.token,
        }
        for entry in entries
    ]


def _build_codex_diary_scope_identity(entry_specs: list[dict[str, Any]]) -> tuple[str, dict[str, str]]:
    entry_ids = [str(entry["entry_id"]) for entry in entry_specs]
    digest = hashlib.sha1(",".join(sorted(entry_ids)).encode("utf-8")).hexdigest()[:12]
    return (
        f"entries:{digest}",
        {
            "root_key": f"codex-diary:{digest}:default-codex",
            "root_dir": f"{len(entry_ids)} 台设备各自默认 .codex",
        },
    )


def _remote_codex_base_url(entry: UserDevice | SimpleNamespace) -> str:
    if entry.mode != "remote":
        raise HTTPException(status_code=400, detail="This entry is not a remote entry")
    if not entry.server_url:
        raise HTTPException(status_code=400, detail="Remote entry has no server_url configured")
    return str(entry.server_url).rstrip("/")


def _remote_codex_headers(entry: UserDevice | SimpleNamespace) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {entry.token}",
        "X-Device-Token": str(entry.token),
    }


def _fetch_remote_codex_json(
    entry: UserDevice | SimpleNamespace,
    method: str,
    path: str,
    *,
    timeout: int = 20,
) -> dict[str, Any] | list[Any]:
    target_url = f"{_remote_codex_base_url(entry)}/api{path}"
    try:
        resp = requests.request(
            method=method,
            url=target_url,
            headers=_remote_codex_headers(entry),
            proxies=CODEX_DIARY_DIRECT_REMOTE_PROXIES.copy(),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"连接远端设备失败：{exc}") from exc

    if resp.status_code >= 400:
        detail = None
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            raw_detail = payload.get("detail") or payload.get("message") or payload.get("error")
            if isinstance(raw_detail, str) and raw_detail.strip():
                detail = raw_detail.strip()
        raise HTTPException(
            status_code=resp.status_code,
            detail=detail or resp.text.strip() or f"远端请求失败：HTTP {resp.status_code}",
        )

    content_type = resp.headers.get("content-type", "")
    if "application/json" not in content_type.lower():
        raise HTTPException(status_code=502, detail="远端设备返回的不是 JSON 数据")
    return resp.json()


def _collect_remote_codex_diary_source(
    entry_spec: dict[str, Any],
    target_date_text: str,
    *,
    user_id: int | None,
    session: Session,
) -> dict[str, Any]:
    remote_entry = SimpleNamespace(**entry_spec)
    workload_payload = _fetch_remote_codex_json(remote_entry, "GET", "/codex/workload", timeout=20)
    if not isinstance(workload_payload, dict):
        raise HTTPException(status_code=502, detail="远端 Codex workload 返回格式不正确")

    cache_info = cache_remote_codex_workload(entry_spec["entry_id"], workload_payload, session=session)
    root_key = str(cache_info["root_key"])
    _, day_start_at, day_end_at = resolve_codex_daily_summary_epoch_range(target_date_text)
    thread_ids = sorted(
        {
            str(thread_id)
            for thread_id in session.exec(
                select(CodexTextCacheTurn.thread_id)
                .where(
                    CodexTextCacheTurn.root_key == root_key,
                    CodexTextCacheTurn.end_at > day_start_at,
                    CodexTextCacheTurn.start_at < day_end_at,
                )
            ).all()
            if str(thread_id or "").strip()
        }
    )

    for thread_id in thread_ids:
        detail_payload = _fetch_remote_codex_json(
            remote_entry,
            "GET",
            f"/codex/threads/{thread_id}",
            timeout=20,
        )
        if not isinstance(detail_payload, dict):
            raise HTTPException(status_code=502, detail=f"远端 Codex 会话 {thread_id} 返回格式不正确")
        cache_remote_codex_thread_detail(entry_spec["entry_id"], detail_payload, session=session)

    return collect_cached_codex_daily_summary_source(
        root_key,
        target_date_text,
        user_id=user_id,
        session=session,
    )


def _collect_codex_diary_source(
    entry_specs: list[dict[str, Any]],
    root_identity: dict[str, str],
    target_date_text: str,
    *,
    user_id: int | None,
    session: Session,
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    for entry_spec in entry_specs:
        if entry_spec["mode"] == "local":
            local_entry = UserDevice(**entry_spec)
            _ensure_codex_diary_local_entry(local_entry)
            source = collect_codex_daily_summary_source(
                None,
                target_date_text,
                user_id=user_id,
                session=session,
            )
        else:
            source = _collect_remote_codex_diary_source(
                entry_spec,
                target_date_text,
                user_id=user_id,
                session=session,
            )

        sources.append(
            annotate_codex_daily_summary_source(
                source,
                source_entry_id=str(entry_spec["entry_id"]),
                source_device_name=_codex_diary_entry_label(entry_spec),
            )
        )

    return merge_codex_daily_summary_sources(
        sources,
        root_key=root_identity["root_key"],
        root_dir=root_identity["root_dir"],
        target_date_text=target_date_text,
        user_id=user_id,
        session=session,
    )


def _get_custom_field_value(custom_fields: Any, key: str) -> Any:
    if isinstance(custom_fields, dict):
        return custom_fields.get(key)
    if isinstance(custom_fields, list):
        for item in custom_fields:
            if isinstance(item, (list, tuple)) and len(item) >= 3 and item[0] == key:
                return item[2]
            if isinstance(item, dict) and item.get("key") == key:
                return item.get("value")
    return None


def _find_existing_codex_diary_notes(
    session: Session,
    *,
    user_id: int,
    diary_date: str,
    scope_key: str,
    day_start_at: float,
    day_end_at: float,
) -> list[str]:
    rows = session.exec(
        select(NoteNode)
        .where(
            NoteNode.user_id == user_id,
            NoteNode.start_at >= day_start_at,
            NoteNode.start_at < day_end_at,
        )
        .order_by(NoteNode.start_at, NoteNode.created_at)
    ).all()
    duplicate_ids: list[str] = []
    for note in rows:
        if _get_custom_field_value(note.custom_fields, CODEX_DIARY_DATE_FIELD) != diary_date:
            continue
        if _get_custom_field_value(note.custom_fields, CODEX_DIARY_SCOPE_FIELD) != scope_key:
            continue
        if note.id:
            duplicate_ids.append(str(note.id))
    return duplicate_ids


def _serialize_codex_diary_import_run(
    run: CodexDiaryImportRun,
    *,
    current_user: User,
    session: Session,
) -> dict[str, Any]:
    created_notes: list[dict[str, Any]] = []
    for note_id in run.created_note_ids or []:
        note = session.get(NoteNode, note_id)
        if note and note.user_id == current_user.id:
            created_notes.append(_serialize_note_read(note, current_user))
    return {
        "id": run.id,
        "date": run.diary_date,
        "timezone": run.timezone,
        "scope_key": run.scope_key,
        "entry_ids": list(run.entry_ids or []),
        "entry_snapshot": list(run.entry_snapshot or []),
        "confirm_duplicate": bool(run.confirm_duplicate),
        "status": run.status,
        "stage": run.stage,
        "stage_label": run.stage_label,
        "source_thread_count": int(run.source_thread_count or 0),
        "source_turn_count": int(run.source_turn_count or 0),
        "source_user_message_count": int(run.source_user_message_count or 0),
        "source_assistant_message_count": int(run.source_assistant_message_count or 0),
        "created_note_count": int(run.created_note_count or 0),
        "created_note_ids": list(run.created_note_ids or []),
        "duplicate_note_ids": list(run.duplicate_note_ids or []),
        "error_message": run.error_message,
        "result": run.result_json or None,
        "created_notes": created_notes,
        "heartbeat_at": run.heartbeat_at,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "updated_at": run.updated_at,
    }


def _touch_codex_diary_run(
    session: Session,
    run: CodexDiaryImportRun,
    *,
    status: str | None = None,
    stage: str | None = None,
    stage_label: str | None = None,
) -> None:
    now = time.time()
    if status is not None:
        run.status = status
    if stage is not None:
        run.stage = stage
    if stage_label is not None:
        run.stage_label = stage_label
    run.heartbeat_at = now
    run.updated_at = now
    session.add(run)
    session.commit()


def _normalize_project_palette_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)
    return text


def _is_specific_codex_diary_category_key(value: Any) -> bool:
    key = str(value or "").strip()
    return bool(key) and key != NOTE_CATEGORY_DEFAULT


def _iter_unique_codex_diary_palette_items(palette_lookup: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items_by_key: dict[str, dict[str, Any]] = {}
    for item in palette_lookup.values():
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key and key not in items_by_key:
            items_by_key[key] = item
    return list(items_by_key.values())


def _codex_diary_category_result(
    category_key: str,
    *,
    palette_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    key = str(category_key or "").strip() or NOTE_CATEGORY_DEFAULT
    for item in _iter_unique_codex_diary_palette_items(palette_lookup):
        if str(item.get("key") or "").strip() == key:
            return {
                "key": key,
                "label": str(item.get("label") or item.get("key") or ("综合" if key == NOTE_CATEGORY_DEFAULT else key)),
                "color": item.get("color"),
            }
    return {"key": key, "label": "综合" if key == NOTE_CATEGORY_DEFAULT else key, "color": None}


def _extract_codex_diary_category_hint_tokens(value: Any) -> list[str]:
    normalized = _normalize_project_palette_token(value)
    if not normalized:
        return []

    stopwords = {_normalize_project_palette_token(item) for item in CODEX_DIARY_CATEGORY_HINT_STOPWORDS}
    tokens: set[str] = set()
    for match in re.findall(r"[a-z][a-z0-9]{2,}", normalized):
        if match not in stopwords:
            tokens.add(match)

    for cjk_text in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(cjk_text) < 2:
            continue
        if len(cjk_text) <= 6 and cjk_text not in stopwords:
            tokens.add(cjk_text)
        max_size = min(4, len(cjk_text))
        for size in range(2, max_size + 1):
            for start in range(0, len(cjk_text) - size + 1):
                token = cjk_text[start : start + size]
                if token not in stopwords:
                    tokens.add(token)

    return sorted(tokens, key=lambda item: (-len(item), item))[:80]


def _codex_diary_domain_aliases_for_palette_item(item: dict[str, Any]) -> list[str]:
    identity = _normalize_project_palette_token(
        " ".join(
            _collect_project_palette_candidates(
                item.get("key"),
                item.get("label"),
                str(item.get("key") or "").removeprefix("custom_"),
            )
        )
    )
    if not identity:
        return []

    aliases: list[str] = []
    seen: set[str] = set()
    for markers, raw_aliases in CODEX_DIARY_CATEGORY_DOMAIN_ALIASES:
        normalized_markers = [_normalize_project_palette_token(marker) for marker in markers]
        if not any(marker and marker in identity for marker in normalized_markers):
            continue
        for alias in raw_aliases:
            normalized_alias = _normalize_project_palette_token(alias)
            if normalized_alias and normalized_alias not in seen:
                seen.add(normalized_alias)
                aliases.append(normalized_alias)
    return aliases


def _add_codex_diary_category_score(scores: dict[str, int], category_key: Any, score: int) -> None:
    key = str(category_key or "").strip()
    if not key or score <= 0:
        return
    scores[key] = scores.get(key, 0) + int(score)


def _score_codex_diary_category_texts(
    values: list[Any],
    *,
    palette_lookup: dict[str, dict[str, Any]],
    title_hints: dict[str, str],
    exact_weight: int,
    palette_token_weight: int,
    domain_alias_weight: int,
    title_hint_full_weight: int,
    title_hint_token_weight: int,
) -> dict[str, int]:
    combined_text = _normalize_project_palette_token(" ".join(str(value or "") for value in values))
    scores: dict[str, int] = {}
    if not combined_text:
        return scores

    for candidate in _collect_project_palette_candidates(*values):
        item = palette_lookup.get(_normalize_project_palette_token(candidate))
        if item:
            _add_codex_diary_category_score(scores, item.get("key"), exact_weight)

    for item in _iter_unique_codex_diary_palette_items(palette_lookup):
        category_key = item.get("key")
        for token in _collect_project_palette_candidates(
            item.get("key"),
            item.get("label"),
            str(item.get("key") or "").removeprefix("custom_"),
        ):
            normalized_token = _normalize_project_palette_token(token)
            if len(normalized_token) >= 2 and normalized_token in combined_text:
                _add_codex_diary_category_score(scores, category_key, palette_token_weight)
        for alias in _codex_diary_domain_aliases_for_palette_item(item):
            if alias in combined_text:
                _add_codex_diary_category_score(scores, category_key, domain_alias_weight)

    for hint_title, category_key in title_hints.items():
        if not _is_specific_codex_diary_category_key(category_key):
            continue
        normalized_hint = _normalize_project_palette_token(hint_title)
        if len(normalized_hint) >= 3 and normalized_hint in combined_text:
            _add_codex_diary_category_score(scores, category_key, title_hint_full_weight)
        for token in _extract_codex_diary_category_hint_tokens(normalized_hint):
            if token in combined_text:
                _add_codex_diary_category_score(scores, category_key, title_hint_token_weight)

    return scores


def _select_best_codex_diary_category_key(
    scores: dict[str, int],
    *,
    specific_only: bool = False,
) -> str | None:
    candidates = [
        (key, score)
        for key, score in scores.items()
        if score > 0 and (not specific_only or _is_specific_codex_diary_category_key(key))
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            -item[1],
            0 if _is_specific_codex_diary_category_key(item[0]) else 1,
            item[0],
        ),
    )[0][0]


def _codex_diary_group_key_for_record(record: dict[str, Any], category_key: str) -> str:
    key = str(category_key or NOTE_CATEGORY_DEFAULT).strip() or NOTE_CATEGORY_DEFAULT
    if key != NOTE_CATEGORY_DEFAULT:
        return key

    thread_id = str(record.get("thread_id") or "").strip()
    if thread_id:
        return f"{key}:thread:{thread_id}"

    title_seed = _normalize_project_palette_token(record.get("thread_title") or record.get("user_request"))
    if title_seed:
        return f"{key}:topic:{title_seed[:40]}"

    start_at = str(record.get("start_at") or "").strip()
    return f"{key}:record:{start_at}"


def _collect_project_palette_candidates(*values: Any) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        trimmed = str(value or "").strip()
        if not trimmed or trimmed in seen:
            return
        seen.add(trimmed)
        candidates.append(trimmed)

    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        add(text)
        for part in re.split(r"[|·/\\>＞:：／]+", text):
            add(part)
    return candidates


def _build_codex_diary_category_lookup(user_id: int, session: Session) -> dict[str, dict[str, Any]]:
    palette_items = _build_note_type_palette_response(user_id, session).get("items", [])
    lookup: dict[str, dict[str, Any]] = {}
    for item in palette_items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        label = str(item.get("label") or "").strip()
        if not key:
            continue
        for token in _collect_project_palette_candidates(key, label, key.removeprefix("custom_")):
            normalized = _normalize_project_palette_token(token)
            if normalized and normalized not in lookup:
                lookup[normalized] = item
    return lookup


def _build_codex_diary_note_title_hints(user_id: int, session: Session) -> dict[str, str]:
    rows = session.exec(
        select(NoteNode)
        .where(NoteNode.user_id == user_id)
        .order_by(NoteNode.updated_at.desc())
        .limit(5000)
    ).all()
    hints: dict[str, str] = {}
    for note in rows:
        category_key = str(note.primary_category or "").strip()
        if not category_key:
            normalized_categories = normalize_note_categories(note.note_categories, fallback_category=NOTE_CATEGORY_DEFAULT)
            category_key = str(derive_primary_category(normalized_categories, NOTE_CATEGORY_DEFAULT) or "").strip()
        if not category_key:
            continue
        for token in _collect_project_palette_candidates(note.title):
            normalized = _normalize_project_palette_token(token)
            if normalized and normalized not in hints:
                hints[normalized] = category_key
    return hints


def _resolve_codex_diary_category(
    turn: dict[str, Any],
    *,
    palette_lookup: dict[str, dict[str, Any]],
    title_hints: dict[str, str],
) -> dict[str, Any]:
    content_scores = _score_codex_diary_category_texts(
        [turn.get("user_request"), turn.get("assistant_result")],
        palette_lookup=palette_lookup,
        title_hints=title_hints,
        exact_weight=40,
        palette_token_weight=18,
        domain_alias_weight=16,
        title_hint_full_weight=16,
        title_hint_token_weight=5,
    )
    context_scores = _score_codex_diary_category_texts(
        [turn.get("project_label"), turn.get("thread_title"), turn.get("source_root_dir")],
        palette_lookup=palette_lookup,
        title_hints=title_hints,
        exact_weight=10,
        palette_token_weight=5,
        domain_alias_weight=3,
        title_hint_full_weight=5,
        title_hint_token_weight=1,
    )

    content_best_key = _select_best_codex_diary_category_key(content_scores, specific_only=True)
    if content_best_key and content_scores.get(content_best_key, 0) >= 12:
        return _codex_diary_category_result(content_best_key, palette_lookup=palette_lookup)

    combined_scores = dict(context_scores)
    for key, score in content_scores.items():
        combined_scores[key] = combined_scores.get(key, 0) + score

    best_key = _select_best_codex_diary_category_key(combined_scores, specific_only=True)
    if best_key:
        return _codex_diary_category_result(best_key, palette_lookup=palette_lookup)

    best_key = _select_best_codex_diary_category_key(combined_scores)
    if best_key:
        return _codex_diary_category_result(best_key, palette_lookup=palette_lookup)

    return _codex_diary_category_result(NOTE_CATEGORY_DEFAULT, palette_lookup=palette_lookup)


def _codex_diary_turn_duration_seconds(turn: dict[str, Any]) -> float:
    try:
        start_at = float(turn.get("start_at") or 0)
        end_at = float(turn.get("end_at") or 0)
    except (TypeError, ValueError):
        return 60.0
    return max(60.0, end_at - start_at)


def _format_codex_diary_time(timestamp: float) -> str:
    return datetime.fromtimestamp(float(timestamp)).strftime("%H:%M")


def _truncate_codex_diary_text(value: Any, limit: int, *, suffix: str = "…") -> str:
    text = NOTE_AI_WHITESPACE_RE.sub(" ", str(value or "").strip())
    if len(text) <= limit:
        return text
    if not suffix:
        return text[:limit].rstrip()
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def _format_codex_diary_inline_html(value: Any) -> str:
    text = _truncate_codex_diary_text(value, 220, suffix="")
    escaped = html.escape(text)
    escaped = re.sub(
        r"([A-Za-z]:\\[^，。；\s<]+|/[^\s，。；<]+)",
        r"<code>\1</code>",
        escaped,
    )
    for keyword in ("接口", "缓存", "风险", "错误", "失败", "阻塞", "完成"):
        escaped = escaped.replace(keyword, f"<strong>{keyword}</strong>")
    return escaped


def _clean_codex_diary_summary_text(value: Any) -> str:
    text = html.unescape(str(value or "")).replace("\xa0", " ")
    text = NOTE_AI_HTML_TAG_RE.sub(" ", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"^[#>*\-\s]+", "", text)
    text = NOTE_AI_WHITESPACE_RE.sub(" ", text).strip()
    return text


def _normalize_codex_diary_compare_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _is_codex_diary_dialogue_sentence(sentence: str, user_requests: list[str]) -> bool:
    text = sentence.strip()
    if not text:
        return True
    normalized = _normalize_codex_diary_compare_text(text)
    if any(normalized == _normalize_codex_diary_compare_text(request) for request in user_requests if request):
        return True
    if any(text.startswith(prefix) for prefix in CODEX_DIARY_DIALOGUE_PREFIXES) and not any(keyword in text for keyword in CODEX_DIARY_RESULT_KEYWORDS):
        return True
    if ("？" in text or "?" in text) and not any(keyword in text for keyword in CODEX_DIARY_RESULT_KEYWORDS):
        return True
    return False


def _score_codex_diary_summary_sentence(sentence: str) -> int:
    score = sum(1 for keyword in CODEX_DIARY_RESULT_KEYWORDS if keyword in sentence)
    if "。" in sentence or "；" in sentence:
        score += 1
    if len(sentence) > 140:
        score -= 1
    return score


def _join_codex_diary_summary_sentences(sentences: list[str]) -> str:
    parts = [sentence.strip().rstrip("。；;") for sentence in sentences if sentence.strip()]
    return "；".join(part for part in parts if part)


def _split_codex_diary_summary_sentences(value: Any, *, user_requests: list[str] | None = None) -> list[str]:
    text = _clean_codex_diary_summary_text(value)
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?；;])|[\r\n]+|(?:\s+-\s+)", text)
    sentences: list[str] = []
    seen: set[str] = set()
    requests = user_requests or []
    for part in parts:
        sentence = part.strip(" \t\r\n-•，,；;")
        if not sentence or sentence in seen:
            continue
        if _is_codex_diary_dialogue_sentence(sentence, requests):
            continue
        seen.add(sentence)
        sentences.append(sentence)
        if len(sentences) >= 6:
            break
    return sentences


def _build_codex_diary_record_summary(records: list[dict[str, Any]]) -> str:
    user_requests = [
        str(record.get("user_request") or "").strip()
        for record in records
        if str(record.get("user_request") or "").strip()
    ]
    result_text = " ".join(
        str(record.get("assistant_result") or "").strip()
        for record in records
        if str(record.get("assistant_result") or "").strip()
    )
    sentences = _split_codex_diary_summary_sentences(result_text, user_requests=user_requests)
    if sentences:
        result_sentences = [sentence for sentence in sentences if _score_codex_diary_summary_sentence(sentence) > 0]
        selected = result_sentences[:2] or sentences[:2]
        return _truncate_codex_diary_text(_join_codex_diary_summary_sentences(selected), 220, suffix="")

    return "该事项已有 Codex 处理结果，细节可回到原会话查看。"


def _build_codex_diary_entry_title(records: list[dict[str, Any]], summary: str) -> str:
    phrase = re.split(r"[。！？!?；;，,]", _clean_codex_diary_summary_text(summary), maxsplit=1)[0]
    phrase = phrase.strip(" ：:，,；;。")
    if phrase and not _is_codex_diary_dialogue_sentence(phrase, [str(record.get("user_request") or "") for record in records]):
        return _truncate_codex_diary_text(phrase, 32, suffix="")
    for record in records:
        candidate = _clean_codex_diary_summary_text(record.get("thread_title"))
        if candidate and not _is_codex_diary_dialogue_sentence(candidate, [str(record.get("user_request") or "")]):
            return _truncate_codex_diary_text(candidate, 32, suffix="")
    return "Codex 事项"


def _build_codex_diary_summary_entries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    group_by_thread: dict[str, dict[str, Any]] = {}
    for record in records:
        thread_id = str(record.get("thread_id") or record.get("thread_title") or "").strip()
        if not thread_id:
            thread_id = f"record:{len(grouped)}"
        group = group_by_thread.get(thread_id)
        if group is None:
            group = {
                "thread_id": thread_id,
                "title": _truncate_codex_diary_text(record.get("thread_title") or "Codex 事项", 48, suffix=""),
                "records": [],
                "start_at": float(record.get("start_at") or 0),
                "end_at": float(record.get("end_at") or record.get("start_at") or 0),
                "devices": set(),
            }
            group_by_thread[thread_id] = group
            grouped.append(group)
        group["records"].append(record)
        group["start_at"] = min(float(group["start_at"] or 0), float(record.get("start_at") or 0))
        group["end_at"] = max(float(group["end_at"] or 0), float(record.get("end_at") or record.get("start_at") or 0))
        if record.get("source_device_name"):
            group["devices"].add(str(record.get("source_device_name")))

    entries: list[dict[str, Any]] = []
    for group in grouped:
        summary = _build_codex_diary_record_summary(group["records"])
        entries.append(
            {
                "title": _build_codex_diary_entry_title(group["records"], summary),
                "summary": summary,
                "devices": sorted(group["devices"]),
                "start_at": group["start_at"],
                "end_at": group["end_at"],
                "turn_count": len(group["records"]),
            }
        )
    return entries


def _build_codex_diary_title(block: dict[str, Any]) -> str:
    records = block["records"]
    title_parts: list[str] = []
    seen: set[str] = set()
    for entry in _build_codex_diary_summary_entries(records):
        candidate = _truncate_codex_diary_text(entry.get("title"), 16, suffix="")
        if candidate and candidate not in seen and candidate != "Codex 事项":
            seen.add(candidate)
            title_parts.append(candidate)
        if len(title_parts) >= 2:
            break
    title = "、".join(title_parts) or "Codex 日记"
    return _truncate_codex_diary_text(title, 32, suffix="") or "Codex 日记"


def _extract_codex_diary_ai_json(raw_content: Any) -> dict[str, Any]:
    content = str(raw_content or "").strip()
    if not content:
        raise ValueError("Codex CLI 没有返回可解析的日记草案")
    if content.startswith("```"):
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if fence_match:
            content = fence_match.group(1).strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\})", content, re.DOTALL)
        if not match:
            raise ValueError("Codex CLI 没有返回 JSON 对象")
        parsed = json.loads(match.group(1))
    if not isinstance(parsed, dict):
        raise ValueError("Codex CLI 日记草案不是 JSON 对象")
    return parsed


def _strip_codex_diary_title_noise(value: Any) -> str:
    text = _clean_codex_diary_summary_text(value)
    text = re.sub(r"\[([^\]]{1,80})\]\([^)]+\)", r"\1", text)
    text = text.replace("`", "")
    text = text.strip(" ：:，,；;。")
    changed = True
    while changed and text:
        changed = False
        for prefix in sorted(CODEX_DIARY_TITLE_BAD_VALUES, key=len, reverse=True):
            if text == prefix:
                return ""
            for separator in ("，", ",", "。", "；", ";", "：", ":"):
                marker = f"{prefix}{separator}"
                if text.startswith(marker):
                    text = text[len(marker):].strip(" ：:，,；;。")
                    changed = True
                    break
            if changed:
                break
    text = re.sub(r"^(你这个反馈是对的|这个反馈是对的)[，,；;。]*", "", text).strip(" ：:，,；;。")
    return text


def _normalize_codex_diary_ai_title(value: Any) -> str:
    text = _strip_codex_diary_title_noise(value)
    text = re.sub(r"^(综合|杂项|多项整合|多项处理|多项事项|general|CodeYun/笔记|codeyun|Codex)[：:\s]*", "", text, flags=re.IGNORECASE)
    text = text.rstrip(".…")
    if not text or text in CODEX_DIARY_TITLE_BAD_VALUES:
        return ""
    if len(text) <= 2 and not any(keyword.lower() in text.lower() for keyword in CODEX_DIARY_TITLE_KEYWORDS):
        return ""
    return _truncate_codex_diary_text(text, 32, suffix="")


def _strip_codex_diary_item_number_prefix(value: Any) -> str:
    text = _strip_codex_diary_title_noise(value)
    previous = None
    while text and text != previous:
        previous = text
        text = CODEX_DIARY_ITEM_NUMBER_PREFIX_RE.sub("", text).strip()
    return text


def _normalize_codex_diary_ai_summary_items(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else [value]
    items: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        text = _strip_codex_diary_item_number_prefix(raw_item)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(_truncate_codex_diary_text(text, 260, suffix=""))
        if len(items) >= 6:
            break
    return items


def _repair_codex_diary_body_number_prefixes(content: Any) -> str:
    return CODEX_DIARY_HTML_LI_NUMBER_PREFIX_RE.sub(r"\1", str(content or ""))


def _build_codex_diary_ai_system_prompt() -> str:
    return "\n".join(
        [
            "你在为“星图笔记”生成 Codex 总结日记节点草案。",
            "输入已经按分类和累计约 1 小时工作量预分块；你必须逐块输出，不要新增、删除、合并或拆分 block。",
            "只根据每条记录的 user_request、assistant_result、thread_title 做语义归纳；assistant_result 优先。",
            "不要照搬聊天原文，不要输出工具日志、JSON、堆栈、操作记录、文件大段内容。",
            "标题必须是信息密度高的短名词短语，保留关键对象/动作/接口/页面/字段/路径名；不要带分类前缀。",
            "标题不要使用“综合”“杂项”“多项整合”这类笼统词；多个事项并列时保留前两个具体对象。",
            "标题禁止使用“是的”“可以”“好的”“已改完”“已经删了”这类低信息开头或低信息标题。",
            "正文条目写成总结性工作记录，每条只讲做了什么、达成什么结果、关键风险或后续点。",
            "summary_items 返回纯文本数组，每个数组元素不要自带 1.、2.、一、这类编号；编号由星图笔记编辑器自动生成。",
            "阶段通常为 done；completion_progress 使用 0 到 1 的字符串，已完成任务填 1。",
            "最终只输出 JSON 对象，不要 Markdown，不要解释。",
        ]
    )


def _build_codex_diary_ai_user_prompt(source: dict[str, Any], blocks: list[dict[str, Any]]) -> str:
    payload_blocks: list[dict[str, Any]] = []
    for block in blocks:
        records: list[dict[str, Any]] = []
        for record in block.get("records") or []:
            records.append(
                {
                    "time_range": record.get("time_range"),
                    "device": record.get("source_device_name"),
                    "project": record.get("project_label"),
                    "thread_title": _truncate_codex_diary_text(record.get("thread_title"), 120, suffix=""),
                    "user_request": _truncate_codex_diary_text(record.get("user_request"), 240, suffix=""),
                    "assistant_result": _truncate_codex_diary_text(record.get("assistant_result"), 520, suffix=""),
                }
            )
        payload_blocks.append(
            {
                "block_key": block.get("block_key"),
                "category_key": block.get("category_key"),
                "category_label": block.get("category_label"),
                "start_time": _format_codex_diary_time(float(block.get("start_at") or 0)),
                "end_time": _format_codex_diary_time(float(block.get("end_at") or 0)),
                "duration_minutes": max(1, round(float(block.get("duration_seconds") or 0) / 60)),
                "records": records,
            }
        )
    request_payload = {
        "date": source.get("date"),
        "timezone": getattr(source.get("timezone"), "key", str(source.get("timezone") or "")),
        "rules": {
            "block_count_must_equal": len(payload_blocks),
            "title_max_chars": 32,
            "summary_item_max_chars": 260,
        },
        "blocks": payload_blocks,
        "expected_response": {
            "blocks": [
                {
                    "block_key": "原样返回输入 block_key",
                    "title": "短标题",
                    "summary_items": ["总结性正文条目"],
                    "lifecycle_stage": "done",
                    "completion_progress": "1",
                }
            ]
        },
    }
    return json.dumps(request_payload, ensure_ascii=False, indent=2)


def _draft_codex_diary_blocks_with_ai(source: dict[str, Any], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not blocks:
        return blocks
    provider = _build_default_codex_diary_provider()
    response = chat_with_provider(
        provider_id=provider.id,
        model=provider.default_model,
        system_prompt=_build_codex_diary_ai_system_prompt(),
        messages=[{"role": "user", "content": _build_codex_diary_ai_user_prompt(source, blocks)}],
        response_format="json",
        timeout_seconds=CODEX_DIARY_TIMEOUT_SECONDS,
        extra_providers=(provider,),
    )
    payload = _extract_codex_diary_ai_json(response.get("content"))
    draft_blocks = payload.get("blocks")
    if not isinstance(draft_blocks, list):
        raise ValueError("Codex CLI 日记草案缺少 blocks")
    draft_by_key: dict[str, dict[str, Any]] = {}
    for item in draft_blocks:
        if not isinstance(item, dict):
            continue
        key = str(item.get("block_key") or "").strip()
        if key:
            draft_by_key[key] = item

    for block in blocks:
        block_key = str(block.get("block_key") or "").strip()
        draft = draft_by_key.get(block_key)
        if draft is None:
            raise ValueError(f"Codex CLI 日记草案缺少 block：{block_key}")
        title = _normalize_codex_diary_ai_title(draft.get("title"))
        if not title:
            raise ValueError(f"Codex CLI 日记草案标题无效：{block_key}")
        summary_items = _normalize_codex_diary_ai_summary_items(draft.get("summary_items"))
        if not summary_items:
            raise ValueError(f"Codex CLI 日记草案正文为空：{block_key}")
        block["title"] = title
        block["summary_items"] = summary_items
        block["lifecycle_stage"] = str(draft.get("lifecycle_stage") or "done").strip() or "done"
        block["completion_progress"] = str(draft.get("completion_progress") or "1").strip() or "1"
    return blocks


def _build_codex_diary_body_html(block: dict[str, Any]) -> str:
    records = block["records"]
    device_names = sorted({str(record.get("source_device_name") or "").strip() for record in records if record.get("source_device_name")})
    start_text = _format_codex_diary_time(block["start_at"])
    end_text = _format_codex_diary_time(block["end_at"])
    minutes = max(1, round(float(block["duration_seconds"]) / 60))
    lines = ["<ol>"]
    ai_summary_items = [
        _strip_codex_diary_item_number_prefix(item)
        for item in (block.get("summary_items") or [])
        if str(item or "").strip()
    ]
    if ai_summary_items:
        for item in ai_summary_items:
            lines.append(f"<li><span>{_format_codex_diary_inline_html(item)}</span></li>")
    else:
        for entry in _build_codex_diary_summary_entries(records):
            summary = _format_codex_diary_inline_html(entry["summary"])
            device_text = "、".join(entry["devices"])
            time_text = _format_codex_diary_time(float(entry.get("start_at") or block["start_at"]))
            suffix_parts = [part for part in (device_text, time_text) if part]
            suffix = f" <small>{html.escape(' '.join(suffix_parts))}</small>" if suffix_parts else ""
            lines.append(f"<li><span>{summary}</span>{suffix}</li>")
    lines.extend(
        [
            "</ol>",
            (
                "<p><strong>来源</strong>："
                f"{html.escape('、'.join(device_names) or 'Codex')}；"
                f"{len(records)} 轮；约 {minutes} 分钟；{html.escape(start_text)} - {html.escape(end_text)}</p>"
            ),
        ]
    )
    return _repair_codex_diary_body_number_prefixes("\n".join(lines))


def _build_codex_diary_blocks(source: dict[str, Any], *, user_id: int, session: Session) -> list[dict[str, Any]]:
    palette_lookup = _build_codex_diary_category_lookup(user_id, session)
    title_hints = _build_codex_diary_note_title_hints(user_id, session)
    grouped: dict[str, dict[str, Any]] = {}
    for raw_record in sorted(source.get("turn_records") or [], key=lambda item: (float(item.get("start_at") or 0), str(item.get("thread_id") or ""))):
        record = dict(raw_record)
        category = _resolve_codex_diary_category(record, palette_lookup=palette_lookup, title_hints=title_hints)
        category_key = str(category["key"] or NOTE_CATEGORY_DEFAULT)
        record["duration_seconds"] = _codex_diary_turn_duration_seconds(record)
        record["codex_diary_category"] = category
        group_key = _codex_diary_group_key_for_record(record, category_key)
        group = grouped.setdefault(
            group_key,
            {
                "group_key": group_key,
                "category_key": category_key,
                "category_label": str(category.get("label") or category_key),
                "records": [],
            },
        )
        group["records"].append(record)

    blocks: list[dict[str, Any]] = []
    for group in grouped.values():
        current_records: list[dict[str, Any]] = []
        current_duration = 0.0

        def close_current() -> None:
            nonlocal current_records, current_duration
            if not current_records:
                return
            start_at = min(float(record.get("start_at") or 0) for record in current_records)
            end_at = max(float(record.get("end_at") or record.get("start_at") or 0) for record in current_records)
            block = {
                "group_key": group["group_key"],
                "category_key": group["category_key"],
                "category_label": group["category_label"],
                "records": list(current_records),
                "duration_seconds": current_duration,
                "start_at": start_at,
                "end_at": end_at,
            }
            block["title"] = _build_codex_diary_title(block)
            block["block_key"] = hashlib.sha1(
                json.dumps(
                    {
                        "category_key": block["category_key"],
                        "start_at": block["start_at"],
                        "end_at": block["end_at"],
                        "threads": sorted({str(record.get("thread_id") or "") for record in current_records}),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:16]
            if (
                blocks
                and blocks[-1]["category_key"] == block["category_key"]
                and blocks[-1].get("group_key") == block.get("group_key")
                and block["duration_seconds"] < CODEX_DIARY_TINY_TAIL_SECONDS
            ):
                previous = blocks[-1]
                previous["records"].extend(block["records"])
                previous["duration_seconds"] += block["duration_seconds"]
                previous["end_at"] = max(previous["end_at"], block["end_at"])
                previous["title"] = _build_codex_diary_title(previous)
            else:
                blocks.append(block)
            current_records = []
            current_duration = 0.0

        for record in sorted(group["records"], key=lambda item: (float(item.get("start_at") or 0), str(item.get("thread_id") or ""))):
            current_records.append(record)
            current_duration += float(record.get("duration_seconds") or 0)
            if current_duration >= CODEX_DIARY_TARGET_SECONDS:
                close_current()
        close_current()

    return sorted(blocks, key=lambda item: (float(item["start_at"]), str(item["category_label"]), str(item["block_key"])))


def _create_codex_diary_note(
    session: Session,
    *,
    current_user: User,
    run: CodexDiaryImportRun,
    block: dict[str, Any],
) -> NoteNode:
    lifecycle_stage = str(block.get("lifecycle_stage") or "").strip() or (
        "done" if any(str(record.get("assistant_result") or "").strip() for record in block["records"]) else "doing"
    )
    completion_expr = str(block.get("completion_progress") or "").strip() or ("1" if lifecycle_stage == "done" else "0.6")
    category_key = str(block.get("category_key") or NOTE_CATEGORY_DEFAULT)
    note_categories = [{"key": category_key, "weight": 100}]
    taxonomy_fields = _build_legacy_fields_from_taxonomy(
        note_categories,
        category_key,
        "note",
        NOTE_SCENE_DEFAULT,
        lifecycle_stage,
    )
    source_thread_ids = sorted({str(record.get("thread_id") or "") for record in block["records"] if record.get("thread_id")})
    custom_fields = [
        [CODEX_DIARY_RUN_ID_FIELD, "string", run.id],
        [CODEX_DIARY_DATE_FIELD, "string", run.diary_date],
        [CODEX_DIARY_SCOPE_FIELD, "string", run.scope_key],
        [CODEX_DIARY_BLOCK_FIELD, "string", str(block.get("block_key") or "")],
        [CODEX_DIARY_SOURCE_THREADS_FIELD, "json", source_thread_ids],
    ]
    normalized_custom_fields = _apply_completion_progress_expr_to_note_data(
        {
            "custom_fields": custom_fields,
            "completion_progress_expr": completion_expr,
        },
        [],
    ).get("custom_fields", custom_fields)
    now = time.time()
    note = NoteNode(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        title=str(block["title"]),
        content=_build_codex_diary_body_html(block),
        weight=0,
        node_type=taxonomy_fields["node_type"],
        note_types=taxonomy_fields["note_types"],
        note_categories=taxonomy_fields["note_categories"],
        primary_category=taxonomy_fields["primary_category"],
        note_form=taxonomy_fields["note_form"],
        note_kind=taxonomy_fields["note_kind"],
        note_scene=taxonomy_fields["note_scene"],
        node_status=taxonomy_fields["node_status"],
        lifecycle_stage=taxonomy_fields["lifecycle_stage"],
        color=None,
        weight_mode=None,
        private_level=0,
        custom_fields=normalized_custom_fields,
        created_at=now,
        updated_at=now,
        start_at=float(block["start_at"]),
        history=[],
    )
    session.add(note)
    _record_created_note_metadata_feedback_safely(
        session,
        note=note,
        source_kind="codex_diary_import",
        source_ref_id=run.id,
    )
    return note


def _run_codex_diary_import_worker(
    db_bind: Any,
    *,
    run_id: str,
    user_id: int,
    entry_specs: list[dict[str, Any]],
    root_identity: dict[str, str],
) -> None:
    with Session(db_bind) as session:
        run = session.get(CodexDiaryImportRun, run_id)
        user = session.get(User, user_id)
        if run is None or user is None:
            return
        try:
            _touch_codex_diary_run(session, run, status="running", stage="collecting", stage_label="读取 Codex 来源")
            source = _collect_codex_diary_source(
                entry_specs,
                root_identity,
                run.diary_date,
                user_id=user_id,
                session=session,
            )
            run.source_thread_count = int(source.get("thread_count") or 0)
            run.source_turn_count = int(source.get("turn_count") or 0)
            run.source_user_message_count = int(source.get("user_message_count") or 0)
            run.source_assistant_message_count = int(source.get("assistant_message_count") or 0)
            session.add(run)
            session.commit()

            if not source.get("turn_records"):
                run.status = "completed"
                run.stage = "empty"
                run.stage_label = "当天没有可导入的 Codex 会话记录"
                run.result_json = {
                    "prompt_version": CODEX_DIARY_PROMPT_VERSION,
                    "source": {
                        "thread_count": run.source_thread_count,
                        "turn_count": run.source_turn_count,
                    },
                    "blocks": [],
                }
                run.finished_at = time.time()
                run.updated_at = run.finished_at
                run.heartbeat_at = run.finished_at
                session.add(run)
                session.commit()
                return

            _touch_codex_diary_run(session, run, status="running", stage="splitting", stage_label="按分类和时长拆分节点")
            blocks = _build_codex_diary_blocks(source, user_id=user_id, session=session)

            _touch_codex_diary_run(session, run, status="running", stage="drafting", stage_label="调用 Codex CLI 生成日记草案")
            blocks = _draft_codex_diary_blocks_with_ai(source, blocks)

            _touch_codex_diary_run(session, run, status="running", stage="writing", stage_label="写入星图笔记")
            created_notes: list[NoteNode] = []
            for block in blocks:
                created_notes.append(_create_codex_diary_note(session, current_user=user, run=run, block=block))
            session.commit()
            for note in created_notes:
                session.refresh(note)

            run.created_note_ids = [str(note.id) for note in created_notes if note.id]
            run.created_note_count = len(run.created_note_ids)
            run.status = "completed"
            run.stage = "completed"
            run.stage_label = f"已创建 {run.created_note_count} 个节点"
            run.result_json = {
                "prompt_version": CODEX_DIARY_PROMPT_VERSION,
                "draft_generator": "codex-cli-json-v1",
                "source": {
                    "thread_count": run.source_thread_count,
                    "turn_count": run.source_turn_count,
                    "user_message_count": run.source_user_message_count,
                    "assistant_message_count": run.source_assistant_message_count,
                },
                "blocks": [
                    {
                        "block_key": block.get("block_key"),
                        "title": block.get("title"),
                        "category_key": block.get("category_key"),
                        "category_label": block.get("category_label"),
                        "duration_seconds": block.get("duration_seconds"),
                        "start_at": block.get("start_at"),
                        "end_at": block.get("end_at"),
                        "source_thread_ids": sorted({str(record.get("thread_id") or "") for record in block.get("records") or []}),
                    }
                    for block in blocks
                ],
            }
            run.finished_at = time.time()
            run.updated_at = run.finished_at
            run.heartbeat_at = run.finished_at
            session.add(run)
            session.commit()
        except Exception as exc:
            run.status = "failed"
            run.stage = "failed"
            run.stage_label = "导入失败"
            run.error_message = str(getattr(exc, "detail", None) or exc)
            run.finished_at = time.time()
            run.updated_at = run.finished_at
            run.heartbeat_at = run.finished_at
            session.add(run)
            session.commit()


def _normalize_note_type_palette_item(value: Any, fallback_order: int = 0) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    raw_key = value.get("key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        return None
    key = raw_key.strip()
    if key == "note":
        key = NOTE_CATEGORY_DEFAULT
    elif key in {"doc", "memo"}:
        return None

    label = value.get("label")
    label = str(label).strip() if isinstance(label, str) else ""
    if not label:
        if is_legacy_color_type_key(key):
            legacy_color = get_legacy_color_from_type_key(key)
            label = f"旧色{legacy_color[1:]}" if legacy_color else key
        else:
            builtin_entry = next((item for item in NOTE_TYPE_BUILTIN_PALETTE if item["key"] == key), None)
            label = builtin_entry["label"] if builtin_entry else key
    elif key == NOTE_CATEGORY_DEFAULT and label == "笔记":
        # Auto-correct the historical builtin label carried over from the old type system.
        label = "综合"

    color = normalize_note_color(value.get("color"))
    if color is None and is_legacy_color_type_key(key):
        color = get_legacy_color_from_type_key(key)
    if color is None:
        builtin_entry = next((item for item in NOTE_TYPE_BUILTIN_PALETTE if item["key"] == key), None)
        if builtin_entry:
            color = builtin_entry["color"]
    if color is None:
        return None

    try:
        order = int(value.get("order", fallback_order))
    except (TypeError, ValueError):
        order = fallback_order

    source = value.get("source")
    builtin = bool(value.get("builtin")) or key in NOTE_CATEGORY_BUILTIN_KEYS
    if builtin:
        source = "builtin"
    elif is_legacy_color_type_key(key):
        source = "legacy"
    elif source not in {"builtin", "custom", "legacy"}:
        source = "custom"

    generated_from_color = normalize_note_color(value.get("generated_from_color"))
    if source == "legacy":
        generated_from_color = generated_from_color or get_legacy_color_from_type_key(key)

    return {
        "key": key,
        "label": label,
        "color": color,
        "order": order,
        "builtin": builtin,
        "source": source,
        "generated_from_color": generated_from_color,
    }


def _normalize_note_type_palette_items(items: Any) -> list[dict[str, Any]]:
    values = items if isinstance(items, list) else []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(values):
        normalized_item = _normalize_note_type_palette_item(item, fallback_order=index * 10)
        if not normalized_item:
            continue
        key = normalized_item["key"]
        if key in seen:
            continue
        seen.add(key)
        normalized.append(normalized_item)
    return normalized


def _load_note_type_palette_items(user_id: int, session: Session) -> list[dict[str, Any]]:
    for setting_key in (
        build_note_category_palette_setting_key(user_id),
        build_note_type_palette_setting_key(user_id),
    ):
        row = session.get(AppSetting, setting_key)
        if not row or not isinstance(row.value, dict):
            continue
        normalized = _normalize_note_type_palette_items(row.value.get("items"))
        if normalized:
            return normalized
    return []


def _default_note_type_palette_items() -> list[dict[str, Any]]:
    return _normalize_note_type_palette_items([
        {**item, "builtin": True, "source": "builtin"}
        for item in NOTE_TYPE_BUILTIN_PALETTE
    ])


def _resolve_effective_note_types(note: NoteNode) -> list[dict[str, Any]]:
    return _resolve_effective_note_types_payload(note.note_types, note.node_type, note.color)


def _resolve_effective_note_types_payload(
    note_types_value: Any,
    node_type_value: Any,
    color_value: Any,
) -> list[dict[str, Any]]:
    fallback_type = str(node_type_value or NOTE_TYPE_DEFAULT).strip() or NOTE_TYPE_DEFAULT
    note_types = normalize_note_types(note_types_value, fallback_type=fallback_type)
    normalized_note_color = normalize_note_color(color_value)
    if normalized_note_color and len(note_types) == 1:
        only_type = note_types[0]
        if only_type.get("key") == fallback_type and int(only_type.get("weight", 0)) == 100:
            legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
            if legacy_color_type_key:
                return [{"key": legacy_color_type_key, "weight": 100}]
    return note_types


def _resolve_effective_note_categories_payload(
    note_categories_value: Any,
    primary_category_value: Any,
    note_types_value: Any,
    node_type_value: Any,
    note_kind_value: Any,
    node_status_value: Any,
    color_value: Any,
) -> list[dict[str, Any]]:
    fallback_category = str(primary_category_value or NOTE_CATEGORY_DEFAULT).strip() or NOTE_CATEGORY_DEFAULT
    note_categories = normalize_note_categories(note_categories_value, fallback_category=fallback_category)
    if note_categories_value:
        return note_categories
    taxonomy = derive_note_taxonomy_from_legacy(
        _resolve_effective_note_types_payload(note_types_value, node_type_value, color_value),
        node_type=str(node_type_value or NOTE_TYPE_DEFAULT).strip() or NOTE_TYPE_DEFAULT,
        note_kind=str(note_kind_value or NOTE_KIND_DEFAULT).strip() or NOTE_KIND_DEFAULT,
        node_status=str(node_status_value or NOTE_LIFECYCLE_STAGE_DEFAULT).strip() or NOTE_LIFECYCLE_STAGE_DEFAULT,
    )
    return normalize_note_categories(taxonomy["note_categories"], fallback_category=fallback_category)


def _find_in_use_note_type_keys(user_id: int, target_keys: set[str], session: Session) -> set[str]:
    if not target_keys:
        return set()

    unresolved = {key for key in target_keys if key}
    if not unresolved:
        return set()

    matched: set[str] = set()
    rows = session.exec(
        select(
            NoteNode.note_categories,
            NoteNode.primary_category,
            NoteNode.note_types,
            NoteNode.node_type,
            NoteNode.note_kind,
            NoteNode.node_status,
            NoteNode.color,
        ).where(NoteNode.user_id == user_id)
    ).all()
    for note_categories, primary_category, note_types, node_type, note_kind, node_status, color in rows:
        for item in _resolve_effective_note_categories_payload(
            note_categories,
            primary_category,
            note_types,
            node_type,
            note_kind,
            node_status,
            color,
        ):
            key = str(item.get("key") or "").strip()
            if key in unresolved:
                matched.add(key)
                unresolved.discard(key)
                if not unresolved:
                    return matched
    return matched


def _is_note_type_in_use(user_id: int, type_key: str, session: Session) -> bool:
    normalized_key = str(type_key or "").strip()
    if not normalized_key:
        return False
    return normalized_key in _find_in_use_note_type_keys(user_id, {normalized_key}, session)


def _collect_note_type_usage(user_id: int, session: Session) -> dict[str, float]:
    usage_hundredths: dict[str, int] = {}
    seen_keys: set[str] = set()
    rows = session.exec(
        select(
            NoteNode.note_categories,
            NoteNode.primary_category,
            NoteNode.note_types,
            NoteNode.node_type,
            NoteNode.note_kind,
            NoteNode.node_status,
            NoteNode.color,
        ).where(NoteNode.user_id == user_id)
    ).all()
    for note_categories, primary_category, note_types, node_type, note_kind, node_status, color in rows:
        seen_in_note: set[str] = set()
        for item in _resolve_effective_note_categories_payload(
            note_categories,
            primary_category,
            note_types,
            node_type,
            note_kind,
            node_status,
            color,
        ):
            key = str(item.get("key") or "").strip()
            if not key or key in seen_in_note:
                continue
            seen_in_note.add(key)
            seen_keys.add(key)
            try:
                weight = int(item.get("weight", 0))
            except (TypeError, ValueError):
                weight = 0
            weight = max(0, min(100, weight))
            usage_hundredths[key] = usage_hundredths.get(key, 0) + weight
    return {
        key: usage_hundredths.get(key, 0) / 100
        for key in seen_keys
    }


def _discover_used_note_type_items(usage: dict[str, float]) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    for index, key in enumerate(sorted(usage.keys())):
        if is_legacy_color_type_key(key):
            legacy_color = get_legacy_color_from_type_key(key)
            discovered.append({
                "key": key,
                "label": f"旧色{legacy_color[1:]}" if legacy_color else key,
                "color": legacy_color or "#606266",
                "order": 2000 + index,
                "builtin": False,
                "source": "legacy",
                "generated_from_color": legacy_color,
            })
            continue
        builtin_entry = next((item for item in NOTE_TYPE_BUILTIN_PALETTE if item["key"] == key), None)
        discovered.append({
            "key": key,
            "label": builtin_entry["label"] if builtin_entry else key,
            "color": builtin_entry["color"] if builtin_entry else "#606266",
            "order": builtin_entry["order"] if builtin_entry else 1000 + index,
            "builtin": bool(builtin_entry),
            "source": "builtin" if builtin_entry else "custom",
            "generated_from_color": None,
        })
    return discovered


def _discover_legacy_color_palette_items(user_id: int, session: Session) -> list[dict[str, Any]]:
    raw_colors = session.exec(
        select(NoteNode.color).where(
            NoteNode.user_id == user_id,
            NoteNode.color.is_not(None),
        )
    ).all()
    discovered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_color in enumerate(raw_colors):
        normalized_color = normalize_note_color(raw_color)
        if not normalized_color:
            continue
        key = build_legacy_color_type_key(normalized_color)
        if not key or key in seen:
            continue
        seen.add(key)
        discovered.append({
            "key": key,
            "label": f"旧色{normalized_color[1:]}",
            "color": normalized_color,
            "order": 2000 + index,
            "builtin": False,
            "source": "legacy",
            "generated_from_color": normalized_color,
        })
    return discovered


def _build_note_type_palette_response(user_id: int, session: Session) -> dict[str, list[dict[str, Any]]]:
    stored_items = _load_note_type_palette_items(user_id, session)
    base_items = stored_items or _default_note_type_palette_items()
    usage = _collect_note_type_usage(user_id, session)
    merged = {item["key"]: item for item in base_items}
    for item in _discover_used_note_type_items(usage):
        merged.setdefault(item["key"], item)
    for item in _discover_legacy_color_palette_items(user_id, session):
        merged.setdefault(item["key"], item)
    items = sorted(merged.values(), key=lambda item: (int(item.get("order", 0)), str(item.get("label", item["key"])).lower()))
    for item in items:
        item["usage_count"] = round(float(usage.get(item["key"], 0)), 2)
    return {"items": items}


def _ensure_note_type_labels_unique(items: list[dict[str, Any]]) -> None:
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for item in items:
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        normalized = label.casefold()
        previous = seen.get(normalized)
        if previous is not None:
            duplicates.append(label)
            continue
        seen[normalized] = label
    if duplicates:
        duplicate_labels = ", ".join(sorted(set(duplicates)))
        raise HTTPException(status_code=400, detail=f"Category labels must be unique: {duplicate_labels}")


def _get_note_type_palette_keys(user_id: int, session: Session) -> set[str]:
    response = _build_note_type_palette_response(user_id, session)
    return {
        str(item.get("key") or "").strip()
        for item in response.get("items", [])
        if str(item.get("key") or "").strip()
    }


def _build_note_taxonomy_fields(
    note_types: Any,
    node_type: Any,
    note_kind: Any,
    node_status: Any,
) -> dict[str, Any]:
    taxonomy = derive_note_taxonomy_from_legacy(
        note_types,
        node_type=str(node_type or NOTE_TYPE_DEFAULT).strip() or NOTE_TYPE_DEFAULT,
        note_kind=str(note_kind or NOTE_KIND_DEFAULT).strip() or NOTE_KIND_DEFAULT,
        node_status=str(node_status or "").strip() or None,
    )
    return {
        "note_categories": taxonomy["note_categories"],
        "primary_category": taxonomy["primary_category"],
        "note_form": taxonomy["note_form"],
        "note_scene": taxonomy["note_scene"],
        "lifecycle_stage": taxonomy["lifecycle_stage"],
    }


def _build_legacy_fields_from_taxonomy(
    note_categories: Any,
    primary_category: Any,
    note_form: Any,
    note_scene: Any,
    lifecycle_stage: Any,
) -> dict[str, Any]:
    normalized_primary_category = str(primary_category or NOTE_CATEGORY_DEFAULT).strip() or NOTE_CATEGORY_DEFAULT
    normalized_note_form = normalize_note_form(note_form, default=NOTE_FORM_DEFAULT)
    normalized_note_scene = normalize_note_scene(note_scene, default=NOTE_SCENE_DEFAULT)
    normalized_lifecycle_stage = normalize_lifecycle_stage(lifecycle_stage, default=NOTE_LIFECYCLE_STAGE_DEFAULT)
    legacy = derive_legacy_semantics_from_taxonomy(
        note_categories,
        primary_category=normalized_primary_category,
        note_form=normalized_note_form,
        note_scene=normalized_note_scene,
        lifecycle_stage=normalized_lifecycle_stage,
    )
    return {
        "note_categories": legacy["note_categories"],
        "primary_category": legacy["primary_category"],
        "note_form": legacy["note_form"],
        "note_scene": legacy["note_scene"],
        "lifecycle_stage": legacy["lifecycle_stage"],
        "note_types": legacy["note_types"],
        "node_type": legacy["node_type"],
        "note_kind": legacy["note_kind"],
        "node_status": legacy["node_status"],
    }


def _merge_existing_progress_into_custom_fields(
    custom_fields: Any,
    existing_custom_fields: Any,
) -> Any:
    existing_expr = get_completion_progress_expr(existing_custom_fields)
    if existing_expr is None:
        return custom_fields
    if get_completion_progress_expr(custom_fields) is not None:
        return custom_fields
    return set_completion_progress_expr(custom_fields, existing_expr)


def _apply_completion_progress_expr_to_note_data(
    note_data: dict[str, Any],
    existing_custom_fields: Any,
) -> dict[str, Any]:
    next_data = dict(note_data)

    if "custom_fields" in next_data:
        next_data["custom_fields"] = _merge_existing_progress_into_custom_fields(
            next_data.get("custom_fields"),
            existing_custom_fields,
        )

    if "completion_progress_expr" in next_data:
        expr = normalize_completion_progress_expr(next_data.pop("completion_progress_expr"))
        target_custom_fields = next_data.get("custom_fields", existing_custom_fields)
        next_data["custom_fields"] = set_completion_progress_expr(target_custom_fields, expr)

    return next_data


def _serialize_note_list(note: NoteNode, current_user: User) -> dict[str, Any]:
    return note_to_response_dict(note, current_user)


def _serialize_note_read(
    note: NoteNode,
    current_user: User,
    **extra_fields: Any,
) -> dict[str, Any]:
    return note_to_response_dict(note, current_user, **extra_fields)


def _html_to_plain_text(value: Any) -> str:
    text = str(value or "")
    if not text.strip():
        return ""
    text = NOTE_AI_HTML_BREAK_RE.sub("\n", text)
    text = NOTE_AI_HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return NOTE_AI_WHITESPACE_RE.sub(" ", text).strip()


def _truncate_note_ai_text(value: str, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _extract_note_ai_json(raw_content: Any) -> dict[str, Any]:
    content = str(raw_content or "").strip()
    if not content:
        raise HTTPException(status_code=502, detail="AI 没有返回可解析的 JSON")

    if content.startswith("```"):
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if fence_match:
            content = fence_match.group(1).strip()

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\})", content, re.DOTALL)
        if not match:
            raise HTTPException(status_code=502, detail="AI 没有返回可解析的 JSON")
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="AI 返回的 JSON 格式无效") from exc

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="AI 返回的结果不是 JSON 对象")
    return parsed


def _resolve_note_ai_runtime_config(
    payload: AiNoteCategorizeRequest,
    *,
    current_user: User,
    session: Session,
) -> tuple[str, str | None, str | None]:
    resolved_provider = (payload.provider or get_default_ai_provider_id()).strip().lower() or get_default_ai_provider_id()
    saved_runtime = get_user_ai_chat_provider_runtime_config(session, current_user.id, resolved_provider)
    resolved_base_url = (payload.base_url or "").strip() or str(saved_runtime.get("base_url") or "").strip() or None
    resolved_api_key = (payload.api_key or "").strip() or str(saved_runtime.get("api_key") or "").strip() or None
    if resolved_provider == "ollama":
        ensure_ollama_access_key_allowed(session, resolved_api_key)
    return resolved_provider, resolved_base_url, resolved_api_key


def _normalize_note_ai_choice(
    value: Any,
    *,
    field_label: str,
    allowed_keys: set[str],
) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status_code=502, detail=f"AI 没有返回 {field_label}")
    if normalized not in allowed_keys:
        raise HTTPException(status_code=502, detail=f"AI 返回了未知{field_label}：{normalized}")
    return normalized


def _normalize_note_ai_title(value: Any, limit: int = 120) -> str:
    normalized = NOTE_AI_WHITESPACE_RE.sub(" ", str(value or "")).strip()
    if len(normalized) > limit:
        normalized = normalized[: max(1, limit - 1)].rstrip() + "…"
    return normalized


def _build_note_ai_title_tokens(title: str) -> set[str]:
    normalized = str(title or "").casefold()
    tokens: set[str] = set()
    for part in NOTE_AI_TITLE_TOKEN_RE.findall(normalized):
        if re.fullmatch(r"[a-z0-9]+", part):
            tokens.add(part)
            continue
        if len(part) <= 4:
            tokens.add(part)
        tokens.update(char for char in part if char.strip())
        tokens.update(part[index:index + 2] for index in range(len(part) - 1))
    return {token for token in tokens if token}


def _resolve_note_ai_taxonomy_snapshot(
    *,
    note_categories: Any,
    primary_category: Any,
    note_form: Any,
    note_scene: Any,
    lifecycle_stage: Any,
    note_types: Any,
    node_type: Any,
    note_kind: Any,
    node_status: Any,
    color: Any,
) -> dict[str, str]:
    has_explicit_taxonomy = bool(note_categories or primary_category or note_form or note_scene or lifecycle_stage)
    if has_explicit_taxonomy:
        fallback_category = str(primary_category or NOTE_CATEGORY_DEFAULT).strip() or NOTE_CATEGORY_DEFAULT
        normalized_categories = normalize_note_categories(note_categories, fallback_category=fallback_category)
        return {
            "primary_category": derive_primary_category(normalized_categories, fallback_category),
            "note_form": normalize_note_form(note_form, default=NOTE_FORM_DEFAULT),
            "note_scene": normalize_note_scene(note_scene or note_kind, default=NOTE_SCENE_DEFAULT),
            "lifecycle_stage": normalize_lifecycle_stage(
                lifecycle_stage or node_status,
                default=NOTE_LIFECYCLE_STAGE_DEFAULT,
            ),
        }

    legacy_taxonomy = derive_note_taxonomy_from_legacy(
        _resolve_effective_note_types_payload(note_types, node_type, color),
        node_type=str(node_type or NOTE_TYPE_DEFAULT).strip() or NOTE_TYPE_DEFAULT,
        note_kind=str(note_kind or NOTE_KIND_DEFAULT).strip() or NOTE_KIND_DEFAULT,
        node_status=str(node_status or NOTE_LIFECYCLE_STAGE_DEFAULT).strip() or NOTE_LIFECYCLE_STAGE_DEFAULT,
    )
    return {
        "primary_category": str(legacy_taxonomy["primary_category"]),
        "note_form": str(legacy_taxonomy["note_form"]),
        "note_scene": str(legacy_taxonomy["note_scene"]),
        "lifecycle_stage": str(legacy_taxonomy["lifecycle_stage"]),
    }


def _score_note_ai_reference_title(current_title: str, current_tokens: set[str], candidate_title: str) -> int:
    if not current_title or not candidate_title:
        return 0

    score = 0
    if candidate_title == current_title:
        score += 1000
    elif candidate_title in current_title or current_title in candidate_title:
        score += 200

    candidate_tokens = _build_note_ai_title_tokens(candidate_title)
    if current_tokens and candidate_tokens:
        shared = len(current_tokens & candidate_tokens)
        score += shared * 20
        if shared:
            score += int((shared / max(len(current_tokens), len(candidate_tokens))) * 100)

    return score


def _collect_note_ai_reference_lines(
    note: NoteNode,
    *,
    palette_items: list[dict[str, Any]],
    session: Session,
    limit: int = NOTE_AI_REFERENCE_SAMPLE_LIMIT,
) -> list[str]:
    form_labels = {item["key"]: item["label"] for item in NOTE_AI_FORM_OPTIONS}
    stage_labels = {item["key"]: item["label"] for item in NOTE_AI_LIFECYCLE_OPTIONS}
    category_labels = {
        str(item.get("key") or "").strip(): str(item.get("label") or "").strip() or str(item.get("key") or "").strip()
        for item in palette_items
        if str(item.get("key") or "").strip()
    }
    current_title = _normalize_note_ai_title(note.title, limit=200).casefold()
    current_tokens = _build_note_ai_title_tokens(current_title)
    rows = session.exec(
        select(
            NoteNode.id,
            NoteNode.title,
            NoteNode.note_categories,
            NoteNode.primary_category,
            NoteNode.note_form,
            NoteNode.note_scene,
            NoteNode.lifecycle_stage,
            NoteNode.note_types,
            NoteNode.node_type,
            NoteNode.note_kind,
            NoteNode.node_status,
            NoteNode.color,
            NoteNode.updated_at,
        ).where(
            NoteNode.user_id == note.user_id,
            NoteNode.id != note.id,
            NoteNode.title.is_not(None),
        )
    ).all()

    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    seen_lines: set[str] = set()
    for (
        _candidate_id,
        raw_title,
        note_categories,
        primary_category_value,
        note_form_value,
        note_scene_value,
        lifecycle_stage_value,
        note_types,
        node_type,
        note_kind,
        node_status,
        color,
        updated_at,
    ) in rows:
        title = _normalize_note_ai_title(raw_title)
        if not title:
            continue

        taxonomy = _resolve_note_ai_taxonomy_snapshot(
            note_categories=note_categories,
            primary_category=primary_category_value,
            note_form=note_form_value,
            note_scene=note_scene_value,
            lifecycle_stage=lifecycle_stage_value,
            note_types=note_types,
            node_type=node_type,
            note_kind=note_kind,
            node_status=node_status,
            color=color,
        )
        primary_category = taxonomy["primary_category"]
        note_form = taxonomy["note_form"]
        lifecycle_stage = taxonomy["lifecycle_stage"]
        combo_key = (primary_category, note_form, lifecycle_stage)
        line = (
            f"- {title} | {primary_category}({category_labels.get(primary_category, primary_category)})"
            f" | {note_form}({form_labels.get(note_form, note_form)})"
            f" | {lifecycle_stage}({stage_labels.get(lifecycle_stage, lifecycle_stage)})"
        )
        if line in seen_lines:
            continue
        seen_lines.add(line)
        buckets.setdefault(combo_key, []).append({
            "score": _score_note_ai_reference_title(current_title, current_tokens, title.casefold()),
            "updated_at": float(updated_at or 0),
            "title": title.casefold(),
            "line": line,
        })

    bucket_entries: list[dict[str, Any]] = []
    for combo_key, items in buckets.items():
        items.sort(key=lambda item: (-int(item["score"]), -float(item["updated_at"]), str(item["title"])))
        bucket_entries.append({
            "combo_key": combo_key,
            "items": items[:NOTE_AI_REFERENCE_PER_COMBO_LIMIT],
            "best_score": int(items[0]["score"]) if items else 0,
            "best_updated_at": float(items[0]["updated_at"]) if items else 0,
        })

    bucket_entries.sort(
        key=lambda bucket: (
            -int(bucket["best_score"]),
            str(bucket["combo_key"][0]),
            str(bucket["combo_key"][1]),
            str(bucket["combo_key"][2]),
            -float(bucket["best_updated_at"]),
        )
    )
    selected: list[str] = []
    for sample_index in range(NOTE_AI_REFERENCE_PER_COMBO_LIMIT):
        for bucket in bucket_entries:
            items = bucket["items"]
            if sample_index >= len(items):
                continue
            if len(selected) >= limit:
                return selected
            selected.append(str(items[sample_index]["line"]))
    return selected


def _build_note_ai_prompt(
    note: NoteNode,
    *,
    palette_items: list[dict[str, Any]],
    session: Session,
) -> tuple[str, str]:
    def _format_category_line(item: dict[str, Any]) -> str:
        key = str(item.get("key") or "").strip()
        label = str(item.get("label") or "").strip() or key
        description = NOTE_AI_CATEGORY_DESCRIPTIONS.get(key, "").strip()
        if description:
            return f"- {key} | {label} | {description}"
        return f"- {key} | {label}"

    categories_text = "\n".join(
        _format_category_line(item)
        for item in palette_items
        if str(item.get("key") or "").strip()
    )
    forms_text = "\n".join(
        f"- {item['key']} | {item['label']} | {item['description']}"
        for item in NOTE_AI_FORM_OPTIONS
    )
    stages_text = "\n".join(
        f"- {item['key']} | {item['label']} | {item['description']}"
        for item in NOTE_AI_LIFECYCLE_OPTIONS
    )
    plain_title = _normalize_note_ai_title(note.title, limit=200)
    if not plain_title:
        raise HTTPException(status_code=400, detail="当前节点缺少可供分析的标题")

    reference_text = "\n".join(_collect_note_ai_reference_lines(note, palette_items=palette_items, session=session))
    if not reference_text:
        reference_text = "(无可用参考样本)"

    system_prompt = (
        "你是 CodeYun 星图笔记里的“笔记分类”应用。"
        "你的任务是仅根据当前节点标题，并参考其他已有条目的标题、分类、形态、阶段元数据，"
        "为单条笔记选择最合适的分类、形态、阶段。"
        "正文不会提供，也不要尝试根据正文推断。"
        "必须严格从候选项中各选 1 个，不得自造值。"
        "优先根据标题语义和已有标注习惯判断分类，根据标题体现的内容载体判断形态，根据推进状态词判断阶段。"
        f"信息不足时，优先使用保守默认值：primary_category={NOTE_CATEGORY_DEFAULT}，note_form={NOTE_FORM_DEFAULT}，lifecycle_stage={NOTE_LIFECYCLE_STAGE_DEFAULT}。"
        "只返回 JSON 对象，不要 Markdown，不要额外解释。"
    )
    user_prompt = (
        "可用分类:\n"
        f"{categories_text}\n\n"
        "可用形态:\n"
        f"{forms_text}\n\n"
        "可用阶段:\n"
        f"{stages_text}\n\n"
        "节点标题:\n"
        f"{plain_title or '(空)'}\n\n"
        "已有条目标注样本（仅标题和元数据，格式：标题 | 分类 | 形态 | 阶段）:\n"
        f"{reference_text}\n\n"
        "请优先参考与当前标题更接近的样本，但不要机械照抄；如果信息不足，就回退到默认值。\n"
        "请输出严格 JSON，格式如下:\n"
        '{"primary_category":"分类key","note_form":"形态key","lifecycle_stage":"阶段key","reason":"一句话说明","confidence":0.0}'
    )
    return system_prompt, user_prompt


NOTE_HISTORY_FIELD_MAP = {
    "node_type": "n",
    "note_types": "nt",
    "note_kind": "nk",
    "node_status": "s",
    "title": "t",
    "weight": "w",
    "content": "c",
    "private_level": "p",
    "color": "cl",
    "weight_mode": "wm",
}
NOTE_HISTORY_NON_MERGEABLE_FIELDS = {"node_type", "note_types", "note_kind", "node_status", "weight_mode"}


def _prepare_note_update_data(db_note: NoteNode, raw_note_data: dict[str, Any]) -> dict[str, Any]:
    note_data = _apply_completion_progress_expr_to_note_data(raw_note_data, db_note.custom_fields)
    uses_new_taxonomy_input = bool({
        "note_categories",
        "primary_category",
        "note_form",
        "note_scene",
        "lifecycle_stage",
    } & set(note_data.keys()))

    if uses_new_taxonomy_input:
        effective_categories = note_data.get("note_categories", db_note.note_categories)
        effective_primary_category = note_data.get("primary_category", db_note.primary_category or NOTE_CATEGORY_DEFAULT)
        effective_note_form = note_data.get("note_form", db_note.note_form or NOTE_FORM_DEFAULT)
        effective_note_scene = note_data.get("note_scene", db_note.note_scene or db_note.note_kind or NOTE_SCENE_DEFAULT)
        effective_lifecycle_stage = note_data.get("lifecycle_stage", db_note.lifecycle_stage or db_note.node_status or NOTE_LIFECYCLE_STAGE_DEFAULT)
        note_data.update(_build_legacy_fields_from_taxonomy(
            effective_categories,
            effective_primary_category,
            effective_note_form,
            effective_note_scene,
            effective_lifecycle_stage,
        ))
    elif "note_types" in note_data:
        fallback_type = note_data.get("node_type") or db_note.node_type or NOTE_TYPE_DEFAULT
        normalized_note_types = normalize_note_types(note_data.get("note_types"), fallback_type=fallback_type)
        next_color = note_data.get("color", db_note.color)
        normalized_note_color = normalize_note_color(next_color)
        if normalized_note_color and (
            not note_data.get("note_types")
            or (
                len(normalized_note_types) == 1
                and normalized_note_types[0].get("key") == fallback_type
                and int(normalized_note_types[0].get("weight", 0)) == 100
            )
        ):
            legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
            if legacy_color_type_key:
                normalized_note_types = [{"key": legacy_color_type_key, "weight": 100}]
        note_data["note_types"] = normalized_note_types
        note_data["node_type"] = derive_primary_node_type(normalized_note_types, fallback_type=fallback_type)
    elif "node_type" in note_data:
        replacement_type = note_data.get("node_type") or NOTE_TYPE_DEFAULT
        normalized_note_types = normalize_note_types([], fallback_type=replacement_type)
        note_data["note_types"] = normalized_note_types
        note_data["node_type"] = derive_primary_node_type(normalized_note_types, fallback_type=replacement_type)

    if "color" in note_data:
        note_data["color"] = normalize_note_color(note_data.get("color"))
    elif db_note.color:
        normalized_note_color = normalize_note_color(db_note.color)
        fallback_type = db_note.node_type or NOTE_TYPE_DEFAULT
        existing_note_types = normalize_note_types(db_note.note_types, fallback_type=fallback_type)
        if normalized_note_color and len(existing_note_types) == 1:
            only_type = existing_note_types[0]
            if only_type.get("key") == fallback_type and int(only_type.get("weight", 0)) == 100:
                legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
                if legacy_color_type_key:
                    note_data["note_types"] = [{"key": legacy_color_type_key, "weight": 100}]
                    note_data["node_type"] = legacy_color_type_key

    if not uses_new_taxonomy_input:
        effective_note_types = note_data.get("note_types", db_note.note_types)
        effective_node_type = note_data.get("node_type", db_note.node_type)
        effective_note_kind = note_data.get("note_kind", db_note.note_kind)
        effective_node_status = note_data.get("node_status", db_note.node_status)
        note_data.update(_build_note_taxonomy_fields(
            effective_note_types,
            effective_node_type,
            effective_note_kind,
            effective_node_status,
        ))

    return note_data


def _append_note_history(db_note: NoteNode, note_data: dict[str, Any], now_ts: int) -> bool:
    if db_note.history is None:
        db_note.history = []

    touched_history = False
    one_hour = 3600

    for field, new_val in note_data.items():
        if field not in NOTE_HISTORY_FIELD_MAP:
            continue

        old_val = getattr(db_note, field)
        if old_val == new_val:
            continue

        f_code = NOTE_HISTORY_FIELD_MAP[field]
        last_entry = None
        for entry in reversed(db_note.history):
            if entry.get("f") == f_code:
                last_entry = entry
                break

        is_mergeable = False
        if last_entry and (now_ts - last_entry["ts"]) < one_hour and field not in NOTE_HISTORY_NON_MERGEABLE_FIELDS:
            is_mergeable = True

        if is_mergeable:
            if field == "content":
                old_len = len(old_val) if old_val else 0
                new_len = len(new_val) if new_val else 0
                diff = new_len - old_len
                try:
                    current_delta = int(last_entry["v"].replace("+", ""))
                except Exception:
                    current_delta = 0
                last_entry["v"] = f"{current_delta + diff:+d}"
            else:
                last_entry["v"] = new_val
            last_entry["ts"] = now_ts
        else:
            value_to_log = new_val
            if field == "content":
                old_len = len(old_val) if old_val else 0
                new_len = len(new_val) if new_val else 0
                value_to_log = f"{new_len - old_len:+d}"
            db_note.history.append({"ts": now_ts, "f": f_code, "v": value_to_log})

        touched_history = True

    if touched_history:
        flag_modified(db_note, "history")

    return touched_history


def _record_note_metadata_feedback_safely(
    session: Session,
    *,
    note: NoteNode,
    updates: dict[str, Any],
    source_kind: str,
    source_ref_id: str | None = None,
) -> None:
    try:
        record_note_metadata_feedback_for_update(
            session,
            note=note,
            updates=updates,
            source_kind=source_kind,
            source_ref_id=source_ref_id,
        )
    except Exception as exc:
        print(f"Failed to record note metadata feedback: {exc}")


def _record_created_note_metadata_feedback_safely(
    session: Session,
    *,
    note: NoteNode,
    source_kind: str,
    source_ref_id: str | None = None,
) -> None:
    try:
        record_note_metadata_feedback_for_created_note(
            session,
            note=note,
            source_kind=source_kind,
            source_ref_id=source_ref_id,
        )
    except Exception as exc:
        print(f"Failed to record created note metadata feedback: {exc}")


def _get_accessible_note(note_id: str, current_user: User, session: Session) -> NoteNode | None:
    query = select(NoteNode).where(NoteNode.id == note_id)
    if not current_user.is_superuser:
        query = query.where(NoteNode.user_id == current_user.id)
    return session.exec(query).first()


def _get_filtered_edge_pool(
    seed_note_id: str,
    mode: str,
    user_id: int,
    session: Session
) -> List[NoteEdge]:
    edges = session.exec(select(NoteEdge).where(NoteEdge.user_id == user_id)).all()
    if mode == "satellite":
        edges = [edge for edge in edges if edge.target_id != seed_note_id]
    return edges


def _get_component_note_ids(
    seed_note_id: str,
    mode: str,
    user_id: int,
    session: Session
) -> Tuple[set[str], List[NoteEdge]]:
    start_note = session.exec(
        select(NoteNode).where(NoteNode.id == seed_note_id, NoteNode.user_id == user_id)
    ).first()
    if not start_note:
        raise HTTPException(status_code=404, detail="Note not found")

    edges = _get_filtered_edge_pool(seed_note_id, mode, user_id, session)
    adj: dict[str, list[str]] = {}
    for edge in edges:
        u, v = edge.source_id, edge.target_id
        adj.setdefault(u, []).append(v)
        adj.setdefault(v, []).append(u)

    visited = {seed_note_id}
    queue = [seed_note_id]
    while queue:
        curr = queue.pop(0)
        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)

    return visited, edges


def _get_rule_value(note: NoteNode, field: str):
    if field.startswith("custom_fields."):
        key = field.split(".", 1)[1]
        custom_fields = note.custom_fields or []
        if isinstance(custom_fields, list):
            for item in custom_fields:
                if isinstance(item, list) and len(item) >= 3 and item[0] == key:
                    return item[2]
        elif isinstance(custom_fields, dict):
            return custom_fields.get(key)
        return None

    value = getattr(note, field, None)
    if field in {"lifecycle_stage", "node_status"}:
        return normalize_lifecycle_stage(value, default=NOTE_LIFECYCLE_STAGE_DEFAULT)
    return value


def _matches_rule(note: NoteNode, rule: NoteFilterRule) -> bool:
    field_value = _get_rule_value(note, rule.field)
    op = rule.op
    rule_value = rule.value
    rule_values = list(rule.values or [])

    if rule.field in {"lifecycle_stage", "node_status"}:
        rule_value = normalize_lifecycle_stage(rule.value, default=NOTE_LIFECYCLE_STAGE_DEFAULT) if rule.value is not None else None
        rule_values = [
            normalize_lifecycle_stage(value, default=NOTE_LIFECYCLE_STAGE_DEFAULT)
            for value in rule_values
        ]

    if op == "eq":
        return field_value == rule_value
    if op == "neq":
        return field_value != rule_value
    if op == "in":
        return field_value in rule_values
    if op == "not_in":
        return field_value not in rule_values
    if op == "contains":
        if field_value is None:
            return False
        return str(rule_value or "").lower() in str(field_value).lower()
    if op == "not_contains":
        if field_value is None:
            return True
        return str(rule_value or "").lower() not in str(field_value).lower()
    if op == "regex_search":
        if field_value is None:
            return False
        try:
            return re.search(str(rule_value or ""), str(field_value)) is not None
        except re.error:
            return False
    if op == "gte":
        return field_value is not None and field_value >= rule_value
    if op == "lte":
        return field_value is not None and field_value <= rule_value
    if op == "between":
        if len(rule_values) < 2 or field_value is None:
            return False
        start, end = rule_values[0], rule_values[1]
        return start <= field_value <= end

    return True


def _apply_sql_rule(query, rule: NoteFilterRule):
    if rule.field.startswith("custom_fields."):
        return query, False

    column = getattr(NoteNode, rule.field, None)
    if column is None:
        return query, False

    normalized_value = rule.value
    normalized_values = list(rule.values or [])
    if rule.field in {"lifecycle_stage", "node_status"}:
        normalized_value = normalize_lifecycle_stage(rule.value, default=NOTE_LIFECYCLE_STAGE_DEFAULT) if rule.value is not None else None
        normalized_values = [
            normalize_lifecycle_stage(value, default=NOTE_LIFECYCLE_STAGE_DEFAULT)
            for value in normalized_values
        ]
        if normalized_value == "done":
            normalized_values = list(dict.fromkeys([*normalized_values, "done", "predone"]))
        elif normalized_values:
            normalized_values = list(dict.fromkeys([
                *normalized_values,
                *(["predone"] if "done" in normalized_values else []),
            ]))

    if rule.op == "eq":
        if rule.field in {"lifecycle_stage", "node_status"} and normalized_value == "done":
            return query.where(column.in_(["done", "predone"])), True
        return query.where(column == normalized_value), True
    if rule.op == "neq":
        if rule.field in {"lifecycle_stage", "node_status"} and normalized_value == "done":
            return query.where(~column.in_(["done", "predone"])), True
        return query.where(column != normalized_value), True
    if rule.op == "in" and normalized_values:
        return query.where(column.in_(normalized_values)), True
    if rule.op == "not_in" and normalized_values:
        return query.where(~column.in_(normalized_values)), True
    if rule.op == "gte":
        return query.where(column >= normalized_value), True
    if rule.op == "lte":
        return query.where(column <= normalized_value), True
    if rule.op == "between" and len(normalized_values) >= 2:
        return query.where(column >= normalized_values[0]).where(column <= normalized_values[1]), True
    if rule.op == "contains" and rule.field == "title" and normalized_value is not None:
        return query.where(NoteNode.title.contains(str(normalized_value))), True
    if rule.op == "not_contains" and rule.field == "title" and normalized_value is not None:
        return query.where(~NoteNode.title.contains(str(normalized_value))), True

    return query, False


def _sort_notes(notes: List[NoteNode], order_by: str, order_desc: bool) -> List[NoteNode]:
    sort_field = order_by if order_by in ALLOWED_ORDER_FIELDS else "updated_at"
    return sorted(
        notes,
        key=lambda note: (_get_rule_value(note, sort_field) is None, _get_rule_value(note, sort_field)),
        reverse=order_desc
    )


def _load_note_context(user_id: int, session: Session, *, include_edges: bool) -> NoteGraphContext:
    notes = session.exec(select(NoteNode).where(NoteNode.user_id == user_id)).all()
    edges = []
    if include_edges:
        edges = session.exec(select(NoteEdge).where(NoteEdge.user_id == user_id)).all()
    return NoteGraphContext.from_items(notes, edges)


def _apply_program_matcher(builder, matcher) -> None:
    if matcher.kind == "all":
        builder.all()
        return
    if matcher.kind == "none":
        builder.none()
        return
    if matcher.kind == "id":
        builder.match_id(matcher.ids)
        return
    if matcher.kind == "field":
        if not matcher.field:
            raise HTTPException(status_code=400, detail="field matcher requires a field name")
        is_time_field = matcher.field in {"start_at", "updated_at"}
        if is_time_field and (matcher.time_value is not None or matcher.time_values):
            builder.match_time_field(
                matcher.field,
                matcher.op or "eq",
                time_value=matcher.time_value,
                time_values=matcher.time_values,
            )
            return
        builder.match_field(
            matcher.field,
            matcher.op or "eq",
            value=matcher.value,
            values=matcher.values,
        )
        return
    if matcher.kind == "title_contains":
        builder.match_title(str(matcher.value or ""), ignore_case=matcher.ignore_case)
        return
    if matcher.kind == "full_text_contains":
        builder.match_full_text(str(matcher.value or ""), ignore_case=matcher.ignore_case)
        return
    if matcher.kind == "seed":
        builder.is_seed()
        return
    if matcher.kind == "depth":
        builder.match_depth(min_depth=matcher.min_depth, max_depth=matcher.max_depth)
        return
    if matcher.kind == "relative_month_window":
        builder.relative_month_window(
            field=matcher.field or "start_at",
            start_month_offset=matcher.start_month_offset,
            end_month_offset=matcher.end_month_offset,
        )
        return

    raise HTTPException(status_code=400, detail=f"Unsupported matcher kind: {matcher.kind}")


def _build_program_walker(context: NoteGraphContext, request: NoteProgramRequest) -> NoteWalker:
    walker = NoteWalker(
        context,
        expand=request.program.expand.default,
        select=request.program.select.default,
    )

    for rule in request.program.expand.rules:
        if rule.action == "include":
            builder = walker.expand
        elif rule.action == "filter":
            builder = walker.filter_expand
        else:
            builder = walker.skip_expand
        _apply_program_matcher(builder, rule.matcher)

    for rule in request.program.select.rules:
        if rule.action == "include":
            builder = walker.include
        elif rule.action == "filter":
            builder = walker.filter
        else:
            builder = walker.exclude
        _apply_program_matcher(builder, rule.matcher)

    return walker


def _ensure_seed_ids_exist(context: NoteGraphContext, seed_ids: List[str]) -> None:
    missing = [seed_id for seed_id in seed_ids if context.get_note(seed_id) is None]
    if missing:
        raise HTTPException(status_code=404, detail=f"Seed notes not found: {', '.join(missing)}")


def _execute_note_program(
    request: NoteProgramRequest,
    *,
    current_user: User,
    user_id: int,
    session: Session,
):
    need_edges = request.result.include_edges or request.executor.kind == "component"
    context = _load_note_context(user_id, session, include_edges=need_edges)
    walker = _build_program_walker(context, request)

    if request.executor.kind == "scan":
        walk_result = walker.collect_all(include_edges=request.result.include_edges)
    elif request.executor.kind == "component":
        if not request.executor.seed_ids:
            raise HTTPException(status_code=400, detail="component executor requires seed_ids")
        _ensure_seed_ids_exist(context, request.executor.seed_ids)
        walk_result = walker.collect_component(
            request.executor.seed_ids,
            mode=request.executor.mode,
            max_depth=request.executor.max_depth,
            include_edges=request.result.include_edges,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported executor kind: {request.executor.kind}")

    sorted_nodes = _sort_notes(walk_result.nodes, request.result.order_by, request.result.order_desc)
    total_nodes = len(sorted_nodes)
    visible_nodes = sorted_nodes[request.result.skip: request.result.skip + request.result.limit]
    visible_ids = {node.id for node in visible_nodes}

    if request.result.include_edges:
        visible_edges = [
            edge for edge in walk_result.edges
            if edge.source_id in visible_ids and edge.target_id in visible_ids
        ]
    else:
        visible_edges = []

    return {
        "nodes": [_serialize_note_list(note, current_user) for note in visible_nodes],
        "edges": visible_edges,
        "total_nodes": total_nodes,
        "total_edges": len(visible_edges),
    }

# --- Notes ---

@router.get("/", response_model=List[NoteListRead])
def read_notes(
    skip: int = 0,
    limit: int = 128, # Default to 128 as requested
    created_start: Optional[float] = Query(None, description="Filter by start_at >= start"),
    created_end: Optional[float] = Query(None, description="Filter by start_at <= end"),
    updated_start: Optional[float] = Query(None, description="Filter by updated_at >= start"),
    updated_end: Optional[float] = Query(None, description="Filter by updated_at <= end"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Retrieve notes for the current user.
    Supports filtering by start_at (mapped from created_*) and update time range.
    Default limit is 128.
    """
    query = select(NoteNode).where(NoteNode.user_id == current_user.id)
    
    # Apply Time Filters
    # Note: 'created_start/end' params now filter by 'start_at' field
    if created_start is not None:
        query = query.where(NoteNode.start_at >= created_start)
    if created_end is not None:
        query = query.where(NoteNode.start_at <= created_end)
        
    if updated_start is not None:
        query = query.where(NoteNode.updated_at >= updated_start)
    if updated_end is not None:
        query = query.where(NoteNode.updated_at <= updated_end)
        
    # Order by updated_at desc to get the "latest" ones first
    query = query.order_by(NoteNode.updated_at.desc())
    
    statement = query.offset(skip).limit(limit)
    notes = session.exec(statement).all()
    return [_serialize_note_list(note, current_user) for note in notes]


@router.post("/query", response_model=NoteQueryResponse)
def query_notes(
    request: NoteQueryRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Query a reusable note set using a generic scope + rules model.
    """
    scope = request.scope
    query = select(NoteNode).where(NoteNode.user_id == current_user.id)

    edge_pool: List[NoteEdge] = []
    if scope.mode in {"planetary", "satellite"}:
        if not scope.seed_note_id:
            raise HTTPException(status_code=400, detail="seed_note_id is required for graph scopes")
        note_ids, edge_pool = _get_component_note_ids(
            scope.seed_note_id,
            scope.mode,
            current_user.id,
            session
        )
        if not note_ids:
            return {"nodes": [], "edges": [], "total_nodes": 0, "total_edges": 0}
        query = query.where(NoteNode.id.in_(note_ids))

    python_rules: List[NoteFilterRule] = []
    for rule in request.rules:
        query, handled = _apply_sql_rule(query, rule)
        if not handled:
            python_rules.append(rule)

    candidate_notes = session.exec(query).all()
    filtered_notes = [note for note in candidate_notes if all(_matches_rule(note, rule) for rule in python_rules)]
    sorted_notes = _sort_notes(filtered_notes, request.order_by, request.order_desc)

    total_nodes = len(sorted_notes)
    visible_notes = sorted_notes[request.skip: request.skip + request.limit]
    visible_ids = {note.id for note in visible_notes}

    if request.include_edges:
        if not edge_pool:
            edge_pool = session.exec(select(NoteEdge).where(NoteEdge.user_id == current_user.id)).all()
        visible_edges = [
            edge for edge in edge_pool
            if edge.source_id in visible_ids and edge.target_id in visible_ids
        ]
    else:
        visible_edges = []

    return {
        "nodes": [_serialize_note_list(note, current_user) for note in visible_notes],
        "edges": visible_edges,
        "total_nodes": total_nodes,
        "total_edges": len(visible_edges)
    }


@router.post("/query-program", response_model=NoteProgramResponse)
def query_note_program(
    request: NoteProgramRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Execute a walker-style filtering program over the current user's note graph.
    """
    return _execute_note_program(request, current_user=current_user, user_id=current_user.id, session=session)


@router.post("/batch-update", response_model=NoteBatchUpdateResponse)
def batch_update_notes(
    request: NoteBatchUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Batch update notes for the current user.
    """
    note_ids = [str(note_id).strip() for note_id in request.ids if str(note_id).strip()]
    if not note_ids:
        raise HTTPException(status_code=400, detail="ids is required")

    patch = request.patch.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="patch is required")
    if patch.get("weight") is not None and patch.get("weight_delta") is not None:
        raise HTTPException(status_code=400, detail="weight and weight_delta cannot be used together")

    notes = session.exec(
        select(NoteNode).where(
            NoteNode.user_id == current_user.id,
            NoteNode.id.in_(note_ids)
        )
    ).all()

    note_by_id = {str(note.id): note for note in notes}
    ordered_notes = [note_by_id[note_id] for note_id in note_ids if note_id in note_by_id]

    now_ts = int(time.time())
    updated_notes: List[NoteNode] = []
    touched_history = False

    for note in ordered_notes:
        next_patch = dict(patch)
        if "private_level" in next_patch and next_patch["private_level"] is not None:
            next_patch["private_level"] = int(next_patch["private_level"])
        if "weight_delta" in next_patch and next_patch["weight_delta"] is not None:
            delta = int(next_patch.pop("weight_delta"))
            next_patch["weight"] = int(note.weight or 0) + delta
        elif "weight" in next_patch and next_patch["weight"] is not None:
            next_patch["weight"] = int(next_patch["weight"])

        prepared_patch = _prepare_note_update_data(note, next_patch)
        changed_fields = {
            key: value
            for key, value in prepared_patch.items()
            if getattr(note, key) != value
        }
        if not changed_fields:
            continue

        if _append_note_history(note, changed_fields, now_ts):
            touched_history = True
        _record_note_metadata_feedback_safely(
            session,
            note=note,
            updates=changed_fields,
            source_kind="batch_update",
        )

        for key, value in changed_fields.items():
            setattr(note, key, value)

        note.updated_at = time.time()
        session.add(note)
        updated_notes.append(note)

    if updated_notes or touched_history:
        session.commit()
        for note in updated_notes:
            session.refresh(note)

    return {
        "updated_count": len(updated_notes),
        "notes": [_serialize_note_list(note, current_user) for note in updated_notes],
    }


@router.post("/{note_id}/ai-categorize", response_model=AiNoteCategorizeResponse)
def ai_categorize_note(
    note_id: str,
    payload: AiNoteCategorizeRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    note = _get_accessible_note(note_id, current_user, session)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    palette_items = _build_note_type_palette_response(note.user_id, session).get("items", [])
    if not palette_items:
        palette_items = _default_note_type_palette_items()

    resolved_provider, resolved_base_url, resolved_api_key = _resolve_note_ai_runtime_config(
        payload,
        current_user=current_user,
        session=session,
    )
    resolved_model = (payload.model or "").strip()
    extra_providers = list_user_ai_chat_custom_provider_configs(session, current_user.id)
    system_prompt, user_prompt = _build_note_ai_prompt(note, palette_items=palette_items, session=session)

    try:
        ai_response = chat_with_provider(
            provider_id=resolved_provider,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            messages=[{"role": "user", "content": user_prompt}],
            model=resolved_model or None,
            system_prompt=system_prompt,
            temperature=0,
            extra_providers=extra_providers,
        )
    except (OllamaClientError, AiChatUserConfigError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    parsed = _extract_note_ai_json(ai_response.get("content"))
    allowed_category_keys = {
        str(item.get("key") or "").strip()
        for item in palette_items
        if str(item.get("key") or "").strip()
    }
    allowed_form_keys = {item["key"] for item in NOTE_AI_FORM_OPTIONS}
    allowed_stage_keys = {item["key"] for item in NOTE_AI_LIFECYCLE_OPTIONS}

    primary_category = _normalize_note_ai_choice(
        parsed.get("primary_category"),
        field_label="分类",
        allowed_keys=allowed_category_keys,
    )
    note_form = _normalize_note_ai_choice(
        parsed.get("note_form"),
        field_label="形态",
        allowed_keys=allowed_form_keys,
    )
    lifecycle_stage = _normalize_note_ai_choice(
        parsed.get("lifecycle_stage"),
        field_label="阶段",
        allowed_keys=allowed_stage_keys,
    )

    taxonomy_payload = _build_legacy_fields_from_taxonomy(
        [{"key": primary_category, "weight": 100}],
        primary_category,
        note_form,
        note.note_scene or note.note_kind or NOTE_SCENE_DEFAULT,
        lifecycle_stage,
    )
    changed_fields = {
        field: value
        for field, value in taxonomy_payload.items()
        if getattr(note, field) != value
    }
    if changed_fields:
        _append_note_history(note, changed_fields, int(time.time()))
        _record_note_metadata_feedback_safely(
            session,
            note=note,
            updates=changed_fields,
            source_kind="ai_categorize",
            source_ref_id=NOTE_AI_APP_ID,
        )
        for field, value in changed_fields.items():
            setattr(note, field, value)
        note.updated_at = time.time()
        session.add(note)
        session.commit()
        session.refresh(note)

    category_label_map = {
        str(item.get("key") or "").strip(): str(item.get("label") or "").strip() or str(item.get("key") or "").strip()
        for item in palette_items
    }
    form_label_map = {item["key"]: item["label"] for item in NOTE_AI_FORM_OPTIONS}
    stage_label_map = {item["key"]: item["label"] for item in NOTE_AI_LIFECYCLE_OPTIONS}
    summary = " / ".join([
        category_label_map.get(primary_category, primary_category),
        form_label_map.get(note_form, note_form),
        stage_label_map.get(lifecycle_stage, lifecycle_stage),
    ])
    note_payload = _serialize_note_read(note, current_user)
    if not isinstance(note_payload.get("custom_fields"), list):
        note_payload["custom_fields"] = []

    return {
        "app": NOTE_AI_APP_ID,
        "provider": resolved_provider,
        "model": str(ai_response.get("model") or resolved_model or ""),
        "summary": f"已标记为 {summary}",
        "note": note_payload,
    }


@router.get("/category-palette", response_model=NoteCategoryPaletteResponse)
@router.get("/type-palette", response_model=NoteCategoryPaletteResponse)
def get_note_category_palette(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    return _build_note_type_palette_response(current_user.id, session)


@router.get("/category-palette/{category_key}/can-delete")
@router.get("/type-palette/{category_key}/can-delete")
def can_delete_note_category_palette_item(
    category_key: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    return {"can_delete": not _is_note_type_in_use(current_user.id, category_key, session)}


@router.put("/category-palette", response_model=NoteCategoryPaletteResponse)
@router.put("/type-palette", response_model=NoteCategoryPaletteResponse)
def update_note_category_palette(
    request: NoteCategoryPaletteUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    items = _normalize_note_type_palette_items([item.model_dump() for item in request.items])
    _ensure_note_type_labels_unique(items)
    next_keys = {item["key"] for item in items}
    existing_keys = {item["key"] for item in (_load_note_type_palette_items(current_user.id, session) or _default_note_type_palette_items())}
    removed_keys = {key for key in existing_keys if key not in next_keys}
    blocked_keys = sorted(_find_in_use_note_type_keys(current_user.id, removed_keys, session))
    if blocked_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete categories that are still in use: {', '.join(blocked_keys)}",
        )
    setting_key = build_note_category_palette_setting_key(current_user.id)
    row = session.get(AppSetting, setting_key)
    if row is None:
        row = AppSetting(key=setting_key)
    row.value = {"items": items}
    row.updated_at = time.time()
    session.add(row)
    session.commit()
    return _build_note_type_palette_response(current_user.id, session)


@router.post("/category-palette/merge", response_model=NoteCategoryPaletteResponse)
@router.post("/type-palette/merge", response_model=NoteCategoryPaletteResponse)
def merge_note_category_palette_item(
    request: NoteCategoryMergeRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    source_key = str(request.source_key or "").strip()
    target_key = str(request.target_key or "").strip()
    if not source_key or not target_key:
        raise HTTPException(status_code=400, detail="source_key and target_key are required")
    if source_key == target_key:
        raise HTTPException(status_code=400, detail="source_key and target_key must be different")

    palette_keys = _get_note_type_palette_keys(current_user.id, session)
    if source_key not in palette_keys:
        raise HTTPException(status_code=404, detail=f"Category not found: {source_key}")
    if target_key not in palette_keys:
        raise HTTPException(status_code=404, detail=f"Category not found: {target_key}")

    changed = False
    now = time.time()
    notes = session.exec(select(NoteNode).where(NoteNode.user_id == current_user.id)).all()
    for note in notes:
        effective_note_categories = _resolve_effective_note_categories_payload(
            note.note_categories,
            note.primary_category,
            note.note_types,
            note.node_type,
            note.note_kind,
            note.node_status,
            note.color,
        )
        merged_note_categories, did_change = merge_note_types(
            effective_note_categories,
            source_key=source_key,
            target_key=target_key,
            fallback_type=note.primary_category or NOTE_CATEGORY_DEFAULT,
        )
        if not did_change:
            continue
        taxonomy_payload = _build_legacy_fields_from_taxonomy(
            merged_note_categories,
            derive_primary_category(merged_note_categories, fallback_category=note.primary_category or NOTE_CATEGORY_DEFAULT),
            note.note_form or NOTE_FORM_DEFAULT,
            note.note_scene or note.note_kind or NOTE_SCENE_DEFAULT,
            note.lifecycle_stage or note.node_status or NOTE_LIFECYCLE_STAGE_DEFAULT,
        )
        for field, value in taxonomy_payload.items():
            setattr(note, field, value)
        note.updated_at = now
        session.add(note)
        changed = True

    if changed:
        session.commit()

    return _build_note_type_palette_response(current_user.id, session)


@router.post("/codex-diary/import-runs", response_model=CodexDiaryImportRunRead)
def create_codex_diary_import_run(
    req: CodexDiaryImportRunRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    diary_date, day_start_at, day_end_at = _parse_codex_diary_date(req.date)
    entries = _get_codex_diary_entries(session, current_user, req.entry_ids)
    if not entries:
        raise HTTPException(status_code=400, detail="没有可用于导入的设备")

    entry_specs = _snapshot_codex_diary_entries(entries)
    scope_key, root_identity = _build_codex_diary_scope_identity(entry_specs)
    duplicate_note_ids = _find_existing_codex_diary_notes(
        session,
        user_id=current_user.id,
        diary_date=diary_date,
        scope_key=scope_key,
        day_start_at=day_start_at,
        day_end_at=day_end_at,
    )
    if duplicate_note_ids and not req.confirm_duplicate:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "该日期已导入过 Codex 总结日记，继续会重复生成一批新节点。",
                "duplicate_note_ids": duplicate_note_ids,
                "duplicate_count": len(duplicate_note_ids),
            },
        )

    now = time.time()
    run = CodexDiaryImportRun(
        user_id=current_user.id,
        diary_date=diary_date,
        scope_key=scope_key,
        entry_ids=[str(entry["entry_id"]) for entry in entry_specs],
        entry_snapshot=entry_specs,
        confirm_duplicate=bool(req.confirm_duplicate),
        duplicate_note_ids=duplicate_note_ids,
        status="running",
        stage="queued",
        stage_label="已进入队列",
        heartbeat_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    threading.Thread(
        target=_run_codex_diary_import_worker,
        kwargs={
            "db_bind": session.get_bind(),
            "run_id": run.id,
            "user_id": current_user.id,
            "entry_specs": entry_specs,
            "root_identity": root_identity,
        },
        daemon=True,
    ).start()
    return _serialize_codex_diary_import_run(run, current_user=current_user, session=session)


@router.get("/codex-diary/import-runs/{run_id}", response_model=CodexDiaryImportRunRead)
def get_codex_diary_import_run(
    run_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    run = session.get(CodexDiaryImportRun, run_id)
    if run is None or run.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Codex diary import run not found")
    return _serialize_codex_diary_import_run(run, current_user=current_user, session=session)


@router.get("/metadata-feedback/status")
def get_metadata_feedback_status(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    return get_note_metadata_feedback_status(session)


@router.post("/metadata-feedback/optimization-runs")
def create_metadata_feedback_optimization_run(
    req: NoteMetadataFeedbackOptimizationRunRequest | None = None,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    trigger_reason = (req.trigger_reason if req else "manual").strip() or "manual"
    run = create_note_metadata_feedback_optimization_run(
        session,
        trigger_reason=trigger_reason,
        enqueue=True,
        require_auto_conditions=False,
    )
    if run is None:
        raise HTTPException(status_code=409, detail="暂时不满足优化任务触发条件")
    return serialize_note_metadata_feedback_optimization_run(run)


@router.get("/metadata-feedback/optimization-runs/{run_id}")
def get_metadata_feedback_optimization_run(
    run_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    run = session.get(NoteMetadataFeedbackOptimizationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Metadata feedback optimization run not found")
    return serialize_note_metadata_feedback_optimization_run(run)


@router.post("/", response_model=NoteRead)
def create_note(
    note: NoteCreate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Create a new note.
    """
    normalized_note_color = normalize_note_color(note.color)
    normalized_custom_fields = _apply_completion_progress_expr_to_note_data(
        {
            "custom_fields": note.custom_fields,
            "completion_progress_expr": note.completion_progress_expr,
        },
        [],
    ).get("custom_fields", note.custom_fields)
    uses_new_taxonomy_input = bool({
        "note_categories",
        "primary_category",
        "note_form",
        "note_scene",
        "lifecycle_stage",
    } & set(note.model_fields_set))

    if uses_new_taxonomy_input:
        taxonomy_fields = _build_legacy_fields_from_taxonomy(
            note.note_categories,
            note.primary_category or NOTE_CATEGORY_DEFAULT,
            note.note_form or NOTE_FORM_DEFAULT,
            note.note_scene or note.note_kind or NOTE_SCENE_DEFAULT,
            note.lifecycle_stage or note.node_status or NOTE_LIFECYCLE_STAGE_DEFAULT,
        )
        normalized_note_types = normalize_note_types(taxonomy_fields["note_types"], fallback_type=taxonomy_fields["node_type"])
        primary_node_type = str(taxonomy_fields["node_type"] or NOTE_TYPE_DEFAULT).strip() or NOTE_TYPE_DEFAULT
        effective_note_kind = str(taxonomy_fields["note_kind"] or NOTE_KIND_DEFAULT).strip() or NOTE_KIND_DEFAULT
        effective_node_status = str(taxonomy_fields["node_status"] or NOTE_LIFECYCLE_STAGE_DEFAULT).strip() or NOTE_LIFECYCLE_STAGE_DEFAULT
    else:
        normalized_note_types = normalize_note_types(note.note_types, fallback_type=note.node_type or NOTE_TYPE_DEFAULT)
        fallback_type = note.node_type or NOTE_TYPE_DEFAULT
        if normalized_note_color and (
            not note.note_types
            or (
                len(normalized_note_types) == 1
                and normalized_note_types[0].get("key") == fallback_type
                and int(normalized_note_types[0].get("weight", 0)) == 100
            )
        ):
            legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
            if legacy_color_type_key:
                normalized_note_types = [{"key": legacy_color_type_key, "weight": 100}]
        primary_node_type = derive_primary_node_type(normalized_note_types, fallback_type=note.node_type or NOTE_TYPE_DEFAULT)
        effective_note_kind = note.note_kind or NOTE_KIND_DEFAULT
        effective_node_status = note.node_status or NOTE_LIFECYCLE_STAGE_DEFAULT
        taxonomy_fields = _build_note_taxonomy_fields(
            normalized_note_types,
            primary_node_type,
            effective_note_kind,
            effective_node_status,
        )
    current_time = time.time()
    db_note = NoteNode(
        id=str(uuid.uuid4()), # Generate UUID
        user_id=current_user.id,
        title=note.title,
        content=note.content,
        weight=note.weight,
        node_type=primary_node_type,
        note_types=normalized_note_types,
        note_categories=taxonomy_fields["note_categories"],
        primary_category=taxonomy_fields["primary_category"],
        note_form=taxonomy_fields["note_form"],
        note_kind=effective_note_kind,
        note_scene=taxonomy_fields["note_scene"],
        node_status=effective_node_status,
        lifecycle_stage=taxonomy_fields["lifecycle_stage"],
        color=normalized_note_color,
        weight_mode=note.weight_mode,
        private_level=note.private_level,
        custom_fields=normalized_custom_fields,
        # parent_id=note.parent_id, # Deprecated
        created_at=current_time,
        updated_at=current_time,
        start_at=note.start_at if note.start_at is not None else current_time,
        history=[]
    )
    
    session.add(db_note)
    session.commit()
    session.refresh(db_note)
    return _serialize_note_read(db_note, current_user)

@router.get("/{note_id}", response_model=NoteRead)
def read_note(
    note_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Get a specific note.
    """
    note = _get_accessible_note(note_id, current_user, session)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    
    # Calculate edge count
    edge_count = session.exec(
        select(func.count()).select_from(NoteEdge).where(
            NoteEdge.user_id == note.user_id,
            or_(NoteEdge.source_id == note_id, NoteEdge.target_id == note_id)
        )
    ).one()
    
    # Calculate out_degree (for Satellite mode)
    out_degree = session.exec(
        select(func.count()).select_from(NoteEdge).where(
            NoteEdge.user_id == note.user_id,
            NoteEdge.source_id == note_id
        )
    ).one()

    # --- Inherited Custom Fields Logic ---
    # 1. Fetch direct parents (incoming edges)
    direct_parent_edges = session.exec(
        select(NoteEdge).where(
            NoteEdge.user_id == note.user_id,
            NoteEdge.target_id == note_id
        )
    ).all()
    
    parent_ids = [e.source_id for e in direct_parent_edges]
    
    # 2. Fetch parent nodes
    parent_nodes = []
    if parent_ids:
        parent_nodes = session.exec(
            select(NoteNode).where(
                NoteNode.id.in_(parent_ids),
                NoteNode.user_id == note.user_id
            )
        ).all()

    # 3. Ancestors (Simplified: just one level up for now or BFS for full ancestors?)
    # User asked for "Direct Parent" and "Other Indirect Ancestors".
    # For performance, let's go up 2-3 levels or fetch all reachable ancestors?
    # Fetching all ancestors in a graph can be heavy.
    # Let's implement a limited BFS upstream (e.g., max depth 3) to find ancestors.
    
    ancestor_fields = {} # Key -> [Key, Type, Value]
    direct_parent_fields = {} # Key -> [Key, Type, Value]
    
    # Process Direct Parents first
    for p_node in parent_nodes:
        if p_node.custom_fields:
            if isinstance(p_node.custom_fields, list):
                for field_item in p_node.custom_fields:
                    # field_item is [key, type, value] or {"key":...} (if old format lingers?)
                    # We migrated, so assume list [k, t, v]
                    if isinstance(field_item, list) and len(field_item) >= 3:
                        k, t, v = field_item[0], field_item[1], field_item[2]
                        if is_note_system_custom_field_key(k):
                            continue
                        direct_parent_fields[k] = [k, t, v]
            elif isinstance(p_node.custom_fields, dict):
                # Fallback for unmigrated (shouldn't happen if migration ran)
                for k, v in p_node.custom_fields.items():
                    if is_note_system_custom_field_key(k):
                        continue
                    direct_parent_fields[k] = [k, "string", v]
    
    # Process Ancestors (BFS Upstream)
    # We already have parents. Let's find their parents.
    visited_ancestors = set(parent_ids)
    queue = list(parent_ids)
    max_depth = 3 # Limit depth to prevent performance issues
    current_depth = 0
    
    while queue and current_depth < max_depth:
        # Get next level parents
        next_level_ids = []
        if queue:
            # Find edges where target is in queue (incoming to current level)
            upstream_edges = session.exec(
                select(NoteEdge).where(
                    NoteEdge.user_id == note.user_id,
                    NoteEdge.target_id.in_(queue)
                )
            ).all()
            
            new_ancestor_ids = []
            for edge in upstream_edges:
                if edge.source_id not in visited_ancestors and edge.source_id != note_id:
                    visited_ancestors.add(edge.source_id)
                    new_ancestor_ids.append(edge.source_id)
            
            if new_ancestor_ids:
                # Fetch these ancestor nodes
                ancestor_nodes = session.exec(
                    select(NoteNode).where(
                        NoteNode.id.in_(new_ancestor_ids),
                        NoteNode.user_id == note.user_id
                    )
                ).all()
                
                for anc_node in ancestor_nodes:
                    if anc_node.custom_fields:
                        if isinstance(anc_node.custom_fields, list):
                            for field_item in anc_node.custom_fields:
                                if isinstance(field_item, list) and len(field_item) >= 3:
                                    k, t, v = field_item[0], field_item[1], field_item[2]
                                    if is_note_system_custom_field_key(k):
                                        continue
                                    ancestor_fields[k] = [k, t, v]
                        elif isinstance(anc_node.custom_fields, dict):
                            for k, v in anc_node.custom_fields.items():
                                if is_note_system_custom_field_key(k):
                                    continue
                                ancestor_fields[k] = [k, "string", v]
                
                queue = new_ancestor_ids
            else:
                queue = []
        current_depth += 1

    # Remove keys from ancestor_fields that are already in direct_parent_fields
    # User wants: 1. Own, 2. Direct Parent (but not own), 3. Indirect (but not own or direct)
    
    # We will return these as separate Lists (of Lists) in the response.
    # We need to filter out duplicates.
    
    # Direct fields are prioritized over Ancestor fields
    final_direct_fields = []
    final_ancestor_fields = []
    
    # But wait, frontend also checks against "Own" fields.
    # The API just returns parents/ancestors. Frontend does the "Own" check?
    # No, API should probably filter? Or return raw context?
    # Previous implementation returned raw context for parents/ancestors, frontend filtered against own.
    # Let's keep that.
    
    # Convert dicts back to lists
    final_direct_fields = list(direct_parent_fields.values())
    
    # Filter ancestors: if in direct, remove from ancestor
    for k in list(ancestor_fields.keys()):
        if k in direct_parent_fields:
            del ancestor_fields[k]
            
    final_ancestor_fields = list(ancestor_fields.values())
    
    return _serialize_note_read(
        note,
        current_user,
        edge_count=edge_count,
        out_degree=out_degree,
        inherited_fields={
            "direct": final_direct_fields,
            "ancestors": final_ancestor_fields,
        },
    )

@router.get("/{note_id}/connected-component", response_model=GraphData)
def get_connected_component(
    note_id: str,
    mode: str = Query("planetary", description="Mode: 'planetary' (default) or 'satellite'"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Get the weakly connected component containing the given note.
    mode='satellite': Ignore incoming edges to the center note (only outgoing).
    """
    note_ids, edges = _get_component_note_ids(note_id, mode, current_user.id, session)
    nodes = session.exec(
        select(NoteNode).where(
            NoteNode.user_id == current_user.id,
            NoteNode.id.in_(note_ids)
        )
    ).all()
    component_edges = [edge for edge in edges if edge.source_id in note_ids and edge.target_id in note_ids]
    return {"nodes": [_serialize_note_list(note, current_user) for note in nodes], "edges": component_edges}

@router.put("/{note_id}", response_model=NoteRead)
def update_note(
    note_id: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Update a note.
    """
    db_note = _get_accessible_note(note_id, current_user, session)
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    
    note_data = _prepare_note_update_data(db_note, note_in.model_dump(exclude_unset=True))
    _append_note_history(db_note, note_data, int(time.time()))
    _record_note_metadata_feedback_safely(
        session,
        note=db_note,
        updates=note_data,
        source_kind="manual_update",
    )

    for key, value in note_data.items():
        setattr(db_note, key, value)
    
    db_note.updated_at = time.time()
    session.add(db_note)
    session.commit()
    session.refresh(db_note)
    return _serialize_note_read(db_note, current_user)

@router.delete("/{note_id}")
def delete_note(
    note_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Delete a note.
    """
    db_note = _get_accessible_note(note_id, current_user, session)
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")

    edges = session.exec(
        select(NoteEdge).where(
            or_(
                NoteEdge.source_id == note_id,
                NoteEdge.target_id == note_id,
            )
        )
    ).all()
    for edge in edges:
        session.delete(edge)
    session.delete(db_note)
    session.commit()
    return {"ok": True}

# --- Edges ---

@router.get("/edges/", response_model=List[EdgeRead])
def read_edges(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Retrieve all edges for the current user.
    """
    statement = select(NoteEdge).where(NoteEdge.user_id == current_user.id)
    edges = session.exec(statement).all()
    return edges

@router.post("/edges/", response_model=EdgeRead)
def create_edge(
    edge: EdgeCreate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Create a directed edge between two notes.
    Idempotent: If edge exists, return existing one (or update timestamp).
    """
    # Verify nodes exist and belong to user
    source = session.exec(select(NoteNode).where(NoteNode.id == edge.source_id, NoteNode.user_id == current_user.id)).first()
    target = session.exec(select(NoteNode).where(NoteNode.id == edge.target_id, NoteNode.user_id == current_user.id)).first()
    
    if not source or not target:
        raise HTTPException(status_code=404, detail="Source or Target node not found")

    # Prevent self-loop if desired (optional)
    if edge.source_id == edge.target_id:
        raise HTTPException(status_code=400, detail="Self-loops are not allowed")

    # Check if edge already exists
    statement = select(NoteEdge).where(
        NoteEdge.user_id == current_user.id,
        NoteEdge.source_id == edge.source_id,
        NoteEdge.target_id == edge.target_id
    )
    existing_edge = session.exec(statement).first()
    
    if existing_edge:
        # Idempotent: Return existing edge
        # Optionally update label if provided
        if edge.label is not None and edge.label != existing_edge.label:
            existing_edge.label = edge.label
            session.add(existing_edge)
            session.commit()
            session.refresh(existing_edge)
        return existing_edge

    db_edge = NoteEdge(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        source_id=edge.source_id,
        target_id=edge.target_id,
        label=edge.label,
        created_at=time.time()
    )
    session.add(db_edge)
    session.commit()
    session.refresh(db_edge)
    return db_edge

@router.delete("/edges/")
def delete_edge_by_nodes(
    source: str,
    target: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Delete an edge by source and target IDs.
    Robust: If edge doesn't exist, return success anyway (idempotent delete).
    """
    statement = select(NoteEdge).where(
        NoteEdge.user_id == current_user.id,
        NoteEdge.source_id == source,
        NoteEdge.target_id == target
    )
    db_edges = session.exec(statement).all()
    
    if not db_edges:
        # Already deleted or never existed, return success
        return {"ok": True, "message": "Edge not found, treated as deleted"}
    
    for edge in db_edges:
        session.delete(edge)
    
    session.commit()
    return {"ok": True}

@router.delete("/edges/{edge_id}")
def delete_edge(
    edge_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Delete an edge by ID.
    Robust: If edge doesn't exist, return success.
    """
    statement = select(NoteEdge).where(NoteEdge.id == edge_id, NoteEdge.user_id == current_user.id)
    db_edge = session.exec(statement).first()
    if not db_edge:
        # Robustness: Don't error on missing delete target
        return {"ok": True, "message": "Edge not found, treated as deleted"}
    
    session.delete(db_edge)
    session.commit()
    return {"ok": True}
