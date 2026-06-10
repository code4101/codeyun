import html
import hashlib
import json
import threading
import anyio
from typing import Any, Iterable, List, Optional, Tuple
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, delete, select, func, or_
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified
from backend.db import get_session
from backend.models import (
    AppSetting,
    CodexDiaryImportRun,
    NoteMetadataFeedbackOptimizationRun,
    NoteNode,
    NoteEdge,
    ResourceAccessGrant,
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
    NoteCalendarSummaryRequest,
    NoteCalendarSummaryResponse,
    NoteBatchUpdateRequest,
    NoteBatchUpdateResponse,
    AiNoteCategorizeRequest,
    AiNoteCategorizeResponse,
)
from backend.core.ai_chat import (
    OllamaClientError,
    chat_with_provider,
)
from backend.core.ai_app_config import (
    AI_APP_CODEX_DIARY,
    AI_APP_NOTE_TAXONOMY,
    AiAppConfigError,
    resolve_ai_app_runtime_config,
)
from backend.core.background_task_queue import background_task_queue
from backend.core.ai_chat_user_config import (
    AiChatUserConfigError,
    list_user_ai_chat_custom_provider_configs,
)
from backend.core.auth import get_current_active_user
from backend.core.codex_sessions import (
    resolve_codex_daily_summary_epoch_range,
)
from backend.core.codex_device_summary import collect_multi_codex_daily_summary_source
from backend.core.feature_access_guard import require_feature_access_dependency
from backend.core.guest_notes import get_current_active_or_guest_notes_user
from backend.core.note_access import note_list_mapping_to_response_dict, note_to_list_response_dict, note_to_response_dict
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
    is_note_auto_classification_blocked_category,
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
from backend.core.note_refs import (
    build_note_ref_map,
    load_notes_by_refs,
    note_edge_ref,
    note_public_api_id,
    note_public_id,
    note_ref_aliases,
)
from backend.core.note_walker import NoteGraphContext, NoteWalker, _resolve_time_point_expr
from backend.core.note_identity import allocate_new_note_identity
from backend.core.resource_identity import RESOURCE_TYPE_NOTE
from backend.api.websocket_manager import manager as ws_manager
import time
import uuid

router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("note-tools"))],
)

ALLOWED_ORDER_FIELDS = {"updated_at", "created_at", "start_at", "weight", "title", "private_level"}
NOTE_LIST_LOAD_COLUMNS = (
    NoteNode.id,
    NoteNode.numeric_id,
    NoteNode.legacy_id,
    NoteNode.user_id,
    NoteNode.title,
    NoteNode.weight,
    NoteNode.node_type,
    NoteNode.note_types,
    NoteNode.note_categories,
    NoteNode.primary_category,
    NoteNode.note_form,
    NoteNode.note_kind,
    NoteNode.note_scene,
    NoteNode.node_status,
    NoteNode.lifecycle_stage,
    NoteNode.color,
    NoteNode.weight_mode,
    NoteNode.private_level,
    NoteNode.custom_fields,
    NoteNode.created_at,
    NoteNode.updated_at,
    NoteNode.start_at,
    NoteNode.deleted_at,
    NoteNode.deleted_by_user_id,
)
NOTE_LIST_LOAD_FIELD_NAMES = {column.key for column in NOTE_LIST_LOAD_COLUMNS}
NOTE_CALENDAR_SCORE_COLUMNS = (
    NoteNode.id,
    NoteNode.numeric_id,
    NoteNode.user_id,
    NoteNode.weight,
    NoteNode.note_form,
    NoteNode.node_status,
    NoteNode.lifecycle_stage,
    NoteNode.custom_fields,
    NoteNode.start_at,
)
NOTE_AI_APP_ID = "note-taxonomy"
NOTE_AI_CATEGORY_DESCRIPTIONS = {
    "general": "默认综合分类",
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
CALENDAR_YEAR_MONTH_MEMOS_SETTING_VERSION = 1
CALENDAR_YEAR_MONTH_MEMO_KEY_RE = re.compile(r"^\d{4}-\d{2}$")
CALENDAR_YEAR_TITLE_KEY_RE = re.compile(r"^\d{4}$")
CODEX_DIARY_TIMEOUT_SECONDS = 300.0
CODEX_DIARY_STALE_HEARTBEAT_SECONDS = CODEX_DIARY_TIMEOUT_SECONDS * 2 + 60.0
CODEX_DIARY_PROMPT_VERSION = "2026-06-04.value-first-diary-v4"
CODEX_DIARY_TIMEZONE = "Asia/Shanghai"
CODEX_DIARY_AUTO_IMPORT_CRON = "0 1 * * *"
CODEX_DIARY_AUTO_IMPORT_TASK_NAME = "codex_diary_yesterday_import"
CODEX_DIARY_RUN_ID_FIELD = "__codex_diary_run_id"
CODEX_DIARY_DATE_FIELD = "__codex_diary_date"
CODEX_DIARY_SCOPE_FIELD = "__codex_diary_scope_key"
CODEX_DIARY_BLOCK_FIELD = "__codex_diary_block_key"
CODEX_DIARY_SOURCE_THREADS_FIELD = "__codex_source_thread_ids"
CODEX_DIARY_WORKLOG_FIELD = "__codex_diary_worklog"
CODEX_DIARY_TARGET_SECONDS = 10 * 60 * 60
CODEX_DIARY_PROGRESS_BASE_MINUTES = CODEX_DIARY_TARGET_SECONDS // 60
CODEX_DIARY_TINY_TAIL_SECONDS = 90 * 60
CODEX_DIARY_DRAFT_BATCH_SIZE = 12
CODEX_DIARY_AI_RECORD_LIMIT_PER_BLOCK = 24
CODEX_DIARY_AI_EDGE_RECORD_COUNT_PER_BLOCK = CODEX_DIARY_AI_RECORD_LIMIT_PER_BLOCK // 2
CODEX_DIARY_HEARTBEAT_INTERVAL_SECONDS = 2.0
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
    "你说得对",
    "你说的对",
    "可以",
    "好的",
    "对",
    "没错",
    "已改完",
    "已改",
    "已改好",
    "已修",
    "已经改完",
    "已经删了",
    "已删除",
    "修好了",
    "读到了",
    "查到了",
)
CODEX_DIARY_TITLE_BAD_VALUES = {
    "是的",
    "你说得对",
    "你说的对",
    "可以",
    "好的",
    "对",
    "没错",
    "已改完",
    "已改",
    "已改好",
    "已修",
    "已经改完",
    "已经删了",
    "已删除",
    "修好了",
    "读到了",
    "查到了",
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


class CalendarYearMonthMemosRead(BaseModel):
    memos: dict[str, str] = Field(default_factory=dict)
    year_titles: dict[str, str] = Field(default_factory=dict)
    updated_at: Optional[float] = None


class CalendarYearMonthMemosUpdate(BaseModel):
    memos: Optional[dict[str, str]] = None
    year_titles: Optional[dict[str, str]] = None


NOTE_DOC_RESOURCE_TYPE = RESOURCE_TYPE_NOTE
CODEX_DIARY_ITEM_NUMBER_PREFIX_RE = re.compile(
    r"^\s*(?:(?:第?\d+|[一二三四五六七八九十]+)[\.．、)]|[（(](?:\d+|[一二三四五六七八九十]+)[）)])\s*"
)
CODEX_DIARY_TITLE_PARALLEL_RE = re.compile(r"\s*(?:以及|并且|同时|和|与|及|、|/)\s*")
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
    "OCR",
    "token",
}
CODEX_DIARY_TOPIC_STOPWORDS = CODEX_DIARY_CATEGORY_HINT_STOPWORDS | {
    "codex",
    "codeyun",
    "星图",
    "星云",
    "笔记",
    "阅读",
    "用户",
    "状态",
    "层",
    "布局",
    "预览",
    "资源",
    "框架",
    "日志",
    "标题",
    "正文",
    "草案",
    "改成",
    "已改",
    "改为",
    "统一",
    "合并",
    "入口",
    "全量",
    "加载",
    "读取",
    "接入",
    "落库",
    "导入",
    "汇总",
    "今日",
    "昨日",
    "本地",
    "远端",
    "设备",
    "会话",
    "工作",
    "事项",
    "记录",
    "结果",
}
CODEX_DIARY_TOPIC_WEAK_SUBSTRINGS = {
    "改成",
    "已改",
    "改为",
    "统一",
    "合并",
    "入口",
    "全量",
    "加载",
    "读取",
    "接入",
    "落库",
    "修复",
    "新增",
    "调整",
    "完成",
    "实现",
}
CODEX_DIARY_CATEGORY_DOMAIN_ALIASES = (
    (
        ("codeyun笔记", "codeyunnote", "codeyunnote"),
        (
            "星图笔记",
            "语雀",
            "OneNote",
            "年历",
            "纪视图",
            "月备注",
            "年月总结",
            "章节节点",
            "文章大纲",
            "自然序文档",
            "/doc",
            "PDF",
            "PDF阅读器",
            "PDF 阅读器",
            "PDF用户状态层",
            "PDF 用户状态层",
            "页面笔记",
            "页码",
            "批注",
            "阅读状态",
            "阅读器",
            "缩放比例",
            "文档预览",
            "pdf_document",
            "pdf_documents",
        ),
    ),
    (
        ("codeyun集群", "集群", "cluster"),
        (
            "CodeYun 集群",
            "集群服务",
            "服务管理",
            "设备 token",
            "服务 token",
            "账号 token",
            "局域网",
            "OCR 集中",
            "OCR集中",
            "PaddleOCR",
            "CodeYun OCR",
        ),
    ),
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
            "洞天",
            "福地",
            "尊主",
            "侍从",
            "灵脉",
            "仙府",
            "首领",
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
    (
        ("AI", "人工智能"),
        (
            "人工智能",
            "小狼毫",
            "Rime",
            "Weasel",
            "librime",
            "输入法",
            "预编辑",
            "自定义短语",
            "输入历史",
            "预测模型",
            "上下文预测",
            "预测索引",
            "预测候选",
            "词库",
            "拼音标注",
            "仓颉",
        ),
    ),
)
CODEX_DIARY_INPUT_METHOD_FORCE_TERMS = (
    "小狼毫",
    "Rime",
    "Weasel",
    "librime",
    "输入法",
    "预编辑",
    "自定义短语",
    "输入历史",
    "预测模型",
    "上下文预测",
    "预测索引",
    "预测候选",
    "词库",
    "拼音标注",
    "仓颉",
)
CODEX_DIARY_CODEYUN_NOTE_FORCE_TERMS = (
    "星图笔记",
    "日历",
    "年视图",
    "月视图",
    "卷视图",
    "纪视图",
    "CalendarNotes",
    "语雀",
    "OneNote",
    "章节节点",
    "文章大纲",
    "自然序文档",
    "/doc",
)
CODEX_DIARY_CODEYUN_CLUSTER_FORCE_TERMS = (
    "CodeYun 集群",
    "集群服务",
    "服务管理",
    "设备 token",
    "服务 token",
    "账号 token",
    "局域网 CodeYun OCR",
    "CodeYun OCR",
    "OCR 集中",
    "OCR集中",
    "PaddleOCR",
)
CODEX_DIARY_ENGINEERING_VALUE_FORCE_TERMS = (
    "修复",
    "实现",
    "新增",
    "重构",
    "优化",
    "接入",
    "迁移",
    "补齐",
    "改造",
    "验证",
    "测试",
    "回归",
    "接口",
    "API",
    "页面",
    "组件",
    "前端",
    "后端",
    "脚本",
    "数据",
    "采集",
    "写表",
    "核对",
    "恢复",
    "数据库",
    "SQL",
    "缓存",
    "队列",
    "任务",
    "服务",
    "配置",
    "部署",
    "权限",
    "路由",
    "日志",
)
CODEX_DIARY_PROJECT_VALUE_FORCE_TERMS = (
    "方案",
    "设计",
    "规划",
    "项目",
    "交付",
    "流程",
    "规则",
    "策略",
    "边界",
    "排期",
    "复盘",
    "审计",
    "治理",
)
CODEX_DIARY_FANXIU_FORCE_TERMS = (
    "凡修",
    "prayer_cycle",
    "祈愿",
    "炼丹",
    "淬体",
    "灵兽",
    "妖王",
    "仙花",
    "洞天",
    "福地",
    "尊主",
    "侍从",
    "灵脉",
    "仙府",
    "首领",
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
)
CODEX_DIARY_ATTENDANCE_FORCE_TERMS = (
    "问卷",
    "问卷星",
    "wjx",
    "clockin",
    "kdocs",
)
CODEX_DIARY_ATTENDANCE_FORCE_CONTEXT_TERMS = (
    "652",
    "653",
)

codex_diary_import_scheduler = BackgroundScheduler()


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
    created_note_ids: List[int] = Field(default_factory=list)
    duplicate_note_ids: List[int] = Field(default_factory=list)
    error_message: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    created_notes: List[dict[str, Any]] = Field(default_factory=list)
    heartbeat_at: Optional[float] = None
    created_at: float
    finished_at: Optional[float] = None
    updated_at: float


class NoteMetadataFeedbackOptimizationRunRequest(BaseModel):
    trigger_reason: str = "manual"


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


def _collect_codex_diary_source(
    entry_specs: list[dict[str, Any]],
    root_identity: dict[str, str],
    target_date_text: str,
    *,
    user_id: int | None,
    session: Session,
) -> dict[str, Any]:
    return collect_multi_codex_daily_summary_source(
        entry_specs,
        root_identity,
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
        .where(_active_note_condition())
        .order_by(NoteNode.start_at, NoteNode.created_at)
    ).all()
    duplicate_ids: list[str] = []
    for note in rows:
        if _get_custom_field_value(note.custom_fields, CODEX_DIARY_DATE_FIELD) != diary_date:
            continue
        if _get_custom_field_value(note.custom_fields, CODEX_DIARY_SCOPE_FIELD) != scope_key:
            continue
        if note.id:
            duplicate_ids.append(_note_public_id(note))
    return duplicate_ids


def _codex_diary_public_note_ids(session: Session, user_id: int, note_ids: list[str]) -> list[str]:
    normalized_ids = [str(note_id).strip() for note_id in note_ids if str(note_id).strip()]
    if not normalized_ids:
        return []
    numeric_ids = [int(note_id) for note_id in normalized_ids if note_id.isdecimal()]
    legacy_ids = [note_id for note_id in normalized_ids if not note_id.isdecimal()]
    notes = session.exec(
        select(NoteNode).where(
            NoteNode.user_id == user_id,
            or_(
                or_(NoteNode.id.in_(legacy_ids), NoteNode.legacy_id.in_(legacy_ids)) if legacy_ids else False,
                NoteNode.numeric_id.in_(numeric_ids) if numeric_ids else False,
            ),
            _active_note_condition(),
        )
    ).all()
    note_by_ref: dict[str, NoteNode] = {}
    for note in notes:
        note_by_ref[str(note.id)] = note
        if getattr(note, "legacy_id", None):
            note_by_ref[str(note.legacy_id)] = note
        if note.numeric_id is not None:
            note_by_ref[str(int(note.numeric_id))] = note
    return [_note_public_id(note_by_ref[note_id]) for note_id in normalized_ids if note_id in note_by_ref]


def _codex_diary_public_note_numeric_ids(session: Session, user_id: int, note_ids: list[str]) -> list[int]:
    public_note_ids = _codex_diary_public_note_ids(session, user_id, note_ids)
    numeric_ids: list[int] = []
    for note_id in public_note_ids:
        normalized = str(note_id or "").strip()
        if not normalized.isdecimal():
            raise HTTPException(status_code=500, detail="Codex diary note is missing a numeric resource id")
        numeric_ids.append(int(normalized))
    return numeric_ids


def _serialize_codex_diary_import_run(
    run: CodexDiaryImportRun,
    *,
    current_user: User,
    session: Session,
) -> dict[str, Any]:
    created_notes: list[dict[str, Any]] = []
    for note_id in run.created_note_ids or []:
        resolved_note_id = _resolve_note_legacy_id(str(note_id), current_user, session)
        note = session.get(NoteNode, resolved_note_id) if resolved_note_id else None
        if note and note.user_id == current_user.id:
            created_notes.append(_serialize_note_read(note, current_user))
    created_note_ids = _codex_diary_public_note_numeric_ids(session, current_user.id, list(run.created_note_ids or []))
    duplicate_note_ids = _codex_diary_public_note_numeric_ids(session, current_user.id, list(run.duplicate_note_ids or []))
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
        "created_note_ids": created_note_ids,
        "duplicate_note_ids": duplicate_note_ids,
        "error_message": run.error_message,
        "result": run.result_json or None,
        "created_notes": created_notes,
        "heartbeat_at": run.heartbeat_at,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "updated_at": run.updated_at,
    }


def _serialize_codex_diary_import_run_summary(run: CodexDiaryImportRun | None, session: Session | None = None) -> dict[str, Any] | None:
    if run is None:
        return None
    created_note_ids = list(run.created_note_ids or [])
    duplicate_note_ids = list(run.duplicate_note_ids or [])
    if session is not None:
        created_note_ids = _codex_diary_public_note_ids(session, int(run.user_id), created_note_ids)
        duplicate_note_ids = _codex_diary_public_note_ids(session, int(run.user_id), duplicate_note_ids)
    return {
        "id": run.id,
        "user_id": run.user_id,
        "status": run.status,
        "diary_date": run.diary_date,
        "timezone": run.timezone,
        "scope_key": run.scope_key,
        "entry_ids": list(run.entry_ids or []),
        "confirm_duplicate": bool(run.confirm_duplicate),
        "stage": run.stage,
        "stage_label": run.stage_label,
        "source_thread_count": int(run.source_thread_count or 0),
        "source_turn_count": int(run.source_turn_count or 0),
        "source_user_message_count": int(run.source_user_message_count or 0),
        "source_assistant_message_count": int(run.source_assistant_message_count or 0),
        "created_note_count": int(run.created_note_count or 0),
        "created_note_ids": created_note_ids,
        "duplicate_note_ids": duplicate_note_ids,
        "error_message": run.error_message,
        "result": run.result_json or {},
        "heartbeat_at": run.heartbeat_at,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "updated_at": run.updated_at,
    }


def get_codex_diary_auto_import_status(session: Session) -> dict[str, Any]:
    queue = background_task_queue.snapshot()
    mark_stale_codex_diary_import_runs(session, queue_snapshot=queue)
    latest_run = session.exec(
        select(CodexDiaryImportRun).order_by(CodexDiaryImportRun.created_at.desc())
    ).first()
    active_run = session.exec(
        select(CodexDiaryImportRun)
        .where(CodexDiaryImportRun.status.in_(["pending", "running"]))
        .order_by(CodexDiaryImportRun.created_at.desc())
    ).first()
    return {
        "cron": CODEX_DIARY_AUTO_IMPORT_CRON,
        "latest_run": _serialize_codex_diary_import_run_summary(latest_run, session=session),
        "active_run": _serialize_codex_diary_import_run_summary(active_run, session=session),
        "queue": queue,
    }


def mark_stale_codex_diary_import_runs(
    session: Session,
    *,
    now_ts: float | None = None,
    queue_snapshot: dict[str, Any] | None = None,
) -> int:
    current_ts = float(now_ts if now_ts is not None else time.time())
    stale_before = current_ts - CODEX_DIARY_STALE_HEARTBEAT_SECONDS
    stale_minutes = int(CODEX_DIARY_STALE_HEARTBEAT_SECONDS // 60)
    if _codex_diary_queue_has_active_task(queue_snapshot=queue_snapshot):
        return 0
    runs = session.exec(
        select(CodexDiaryImportRun)
        .where(CodexDiaryImportRun.status.in_(["pending", "running"]))
        .order_by(CodexDiaryImportRun.created_at.asc())
    ).all()
    changed_count = 0
    for run in runs:
        heartbeat = run.heartbeat_at or run.updated_at or run.created_at
        if heartbeat is None or float(heartbeat) > stale_before:
            continue
        run.status = "failed"
        run.stage = "stale"
        run.stage_label = "任务心跳超时"
        run.error_message = (
            f"后台任务心跳超过 {stale_minutes} 分钟未更新，且当前执行队列中没有对应任务；"
            "通常是服务重启、进程中断或 AI 调用被外部终止。"
        )
        run.finished_at = current_ts
        run.heartbeat_at = current_ts
        run.updated_at = current_ts
        session.add(run)
        changed_count += 1

    if changed_count:
        session.commit()
    return changed_count


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


def _is_codex_diary_ai_palette_item(item: dict[str, Any] | None) -> bool:
    if not isinstance(item, dict):
        return False
    candidates = _collect_project_palette_candidates(
        item.get("key"),
        item.get("label"),
        str(item.get("key") or "").removeprefix("custom_"),
    )
    normalized_candidates = {_normalize_project_palette_token(candidate) for candidate in candidates}
    return bool(normalized_candidates & {"ai", "人工智能"})


def _find_codex_diary_category_key_by_domain_marker(
    palette_lookup: dict[str, dict[str, Any]],
    markers: tuple[str, ...],
) -> str | None:
    normalized_markers = [_normalize_project_palette_token(marker) for marker in markers]
    for item in _iter_unique_codex_diary_palette_items(palette_lookup):
        identity = _normalize_project_palette_token(
            " ".join(
                _collect_project_palette_candidates(
                    item.get("key"),
                    item.get("label"),
                    str(item.get("key") or "").removeprefix("custom_"),
                )
            )
        )
        if any(marker and marker in identity for marker in normalized_markers):
            key = str(item.get("key") or "").strip()
            if key:
                return key
    return None


def _add_codex_diary_category_score(scores: dict[str, int], category_key: Any, score: int) -> None:
    key = str(category_key or "").strip()
    if not key or score <= 0:
        return
    scores[key] = scores.get(key, 0) + int(score)


def _count_codex_diary_term_hits(text: str, terms: tuple[str, ...]) -> int:
    normalized_text = _normalize_project_palette_token(text)
    if not normalized_text:
        return 0
    hits = 0
    for term in terms:
        normalized_term = _normalize_project_palette_token(term)
        if normalized_term and normalized_term in normalized_text:
            hits += 1
    return hits


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
            if re.fullmatch(r"[a-z0-9]+", normalized_token or "") and len(normalized_token) < 3:
                continue
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


def _build_codex_diary_category_scores(
    turn: dict[str, Any],
    *,
    palette_lookup: dict[str, dict[str, Any]],
    title_hints: dict[str, str],
) -> dict[str, int]:
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
    combined_scores = dict(context_scores)
    for key, score in content_scores.items():
        combined_scores[key] = combined_scores.get(key, 0) + score
    attendance_key = _find_codex_diary_category_key_by_domain_marker(palette_lookup, ("考勤", "attendance", "kq"))
    content_text = _normalize_project_palette_token(
        " ".join(str(turn.get(key) or "") for key in ("user_request", "assistant_result", "thread_title"))
    )
    body_text = _normalize_project_palette_token(
        " ".join(str(turn.get(key) or "") for key in ("user_request", "assistant_result"))
    )
    if attendance_key:
        if any(_normalize_project_palette_token(term) in content_text for term in CODEX_DIARY_ATTENDANCE_FORCE_TERMS):
            _add_codex_diary_category_score(combined_scores, attendance_key, 360)
        elif (
            any(_normalize_project_palette_token(term) in content_text for term in CODEX_DIARY_ATTENDANCE_FORCE_CONTEXT_TERMS)
            and any(marker in content_text for marker in ("数据", "截图", "核对", "恢复", "记录"))
        ):
            _add_codex_diary_category_score(combined_scores, attendance_key, 260)

    fanxiu_key = _find_codex_diary_category_key_by_domain_marker(palette_lookup, ("凡修", "fanxiu"))
    fanxiu_hits = _count_codex_diary_term_hits(body_text, CODEX_DIARY_FANXIU_FORCE_TERMS)
    if fanxiu_key and fanxiu_hits:
        _add_codex_diary_category_score(combined_scores, fanxiu_key, 300 + fanxiu_hits * 36)

    input_method_hits = _count_codex_diary_term_hits(body_text, CODEX_DIARY_INPUT_METHOD_FORCE_TERMS)
    if input_method_hits:
        input_method_key = (
            _find_codex_diary_category_key_by_domain_marker(palette_lookup, ("AI", "人工智能"))
            or _find_codex_diary_category_key_by_domain_marker(palette_lookup, ("编程", "技术", "programming"))
        )
        if input_method_key:
            _add_codex_diary_category_score(combined_scores, input_method_key, 130 + input_method_hits * 24)

    codeyun_note_key = _find_codex_diary_category_key_by_domain_marker(palette_lookup, ("codeyun笔记", "codeyunnote"))
    codeyun_note_hits = _count_codex_diary_term_hits(body_text, CODEX_DIARY_CODEYUN_NOTE_FORCE_TERMS)
    if codeyun_note_key and codeyun_note_hits:
        _add_codex_diary_category_score(combined_scores, codeyun_note_key, 240 + codeyun_note_hits * 30)

    codeyun_cluster_key = _find_codex_diary_category_key_by_domain_marker(palette_lookup, ("codeyun集群", "集群", "cluster"))
    codeyun_cluster_hits = _count_codex_diary_term_hits(body_text, CODEX_DIARY_CODEYUN_CLUSTER_FORCE_TERMS)
    if codeyun_cluster_key and codeyun_cluster_hits:
        _add_codex_diary_category_score(combined_scores, codeyun_cluster_key, 300 + codeyun_cluster_hits * 36)

    engineering_hits = _count_codex_diary_term_hits(body_text, CODEX_DIARY_ENGINEERING_VALUE_FORCE_TERMS)
    has_explicit_domain_hit = any(
        bool(key) and int(combined_scores.get(key, 0)) >= 180
        for key in (attendance_key, fanxiu_key, input_method_key if input_method_hits else None, codeyun_note_key, codeyun_cluster_key)
    )
    if engineering_hits and not has_explicit_domain_hit:
        engineering_key = (
            _find_codex_diary_category_key_by_domain_marker(palette_lookup, ("编程", "技术", "programming", "develop", "代码"))
            or _find_codex_diary_category_key_by_domain_marker(palette_lookup, ("codeyun笔记", "codeyunnote"))
        )
        if engineering_key:
            _add_codex_diary_category_score(combined_scores, engineering_key, 130 + min(engineering_hits, 6) * 12)

    project_hits = _count_codex_diary_term_hits(body_text, CODEX_DIARY_PROJECT_VALUE_FORCE_TERMS)
    if project_hits:
        project_key = _find_codex_diary_category_key_by_domain_marker(palette_lookup, ("工作", "项目", "work", "project"))
        if project_key:
            _add_codex_diary_category_score(combined_scores, project_key, 320 + min(project_hits, 5) * 20)
    return combined_scores


def _normalize_codex_diary_category_weights(scores: list[tuple[str, int]]) -> list[dict[str, int | str]]:
    total = sum(max(0, int(score)) for _key, score in scores)
    if total <= 0:
        return [{"key": NOTE_CATEGORY_DEFAULT, "weight": 100}]

    weighted: list[dict[str, int | str]] = []
    remaining = 100
    for index, (key, score) in enumerate(scores):
        if index == len(scores) - 1:
            weight = remaining
        else:
            weight = max(1, round((int(score) / total) * 100))
            remaining -= weight
        weighted.append({"key": key, "weight": max(1, min(100, weight))})

    drift = 100 - sum(int(item["weight"]) for item in weighted)
    if drift and weighted:
        weighted[0]["weight"] = max(1, min(100, int(weighted[0]["weight"]) + drift))
    return weighted


def _codex_diary_category_item_by_key(
    key: str,
    *,
    palette_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    normalized_key = str(key or "").strip()
    if not normalized_key:
        return None
    for item in _iter_unique_codex_diary_palette_items(palette_lookup):
        if str(item.get("key") or "").strip() == normalized_key:
            return item
    return None


def _codex_diary_category_labels_for_weights(
    note_categories: list[dict[str, int | str]],
    *,
    palette_lookup: dict[str, dict[str, Any]],
) -> list[str]:
    labels: list[str] = []
    for item in note_categories:
        key = str(item.get("key") or "").strip()
        palette_item = _codex_diary_category_item_by_key(key, palette_lookup=palette_lookup)
        label = str(
            (palette_item or {}).get("label")
            or ("综合" if key == NOTE_CATEGORY_DEFAULT else key)
        )
        if label:
            labels.append(label)
    return labels


def _resolve_codex_diary_weighted_categories(
    scores: dict[str, int],
    *,
    palette_lookup: dict[str, dict[str, Any]],
    max_items: int = 3,
) -> list[dict[str, int | str]]:
    specific_candidates = [
        (key, int(score))
        for key, score in scores.items()
        if score > 0 and _is_specific_codex_diary_category_key(key)
    ]
    candidates = specific_candidates or [
        (key, int(score))
        for key, score in scores.items()
        if score > 0
    ]
    if not candidates:
        return [{"key": NOTE_CATEGORY_DEFAULT, "weight": 100}]

    candidates.sort(key=lambda item: (-item[1], item[0]))
    top_score = candidates[0][1]
    threshold = max(10, int(top_score * 0.2))
    selected = [
        (key, score)
        for key, score in candidates
        if score >= threshold
    ][:max_items]
    if not selected:
        selected = [candidates[0]]
    return _normalize_codex_diary_category_weights(selected)


def _enforce_codex_diary_primary_category(
    note_categories: list[dict[str, int | str]],
    primary_category_key: str,
    *,
    min_primary_weight: int = 60,
) -> list[dict[str, int | str]]:
    primary_key = str(primary_category_key or "").strip() or NOTE_CATEGORY_DEFAULT
    if not _is_specific_codex_diary_category_key(primary_key):
        return [{"key": NOTE_CATEGORY_DEFAULT, "weight": 100}]
    return [{"key": primary_key, "weight": 100}]


def _extract_codex_diary_topic_tokens(*values: Any) -> set[str]:
    stopwords = {_normalize_project_palette_token(item) for item in CODEX_DIARY_TOPIC_STOPWORDS}
    weak_substrings = {_normalize_project_palette_token(item) for item in CODEX_DIARY_TOPIC_WEAK_SUBSTRINGS}
    tokens: set[str] = set()

    def is_ignored(token: str) -> bool:
        if not token or token in stopwords:
            return True
        return any(fragment and fragment in token for fragment in weak_substrings)

    for value in values:
        normalized = _normalize_project_palette_token(value)
        if not normalized:
            continue

        for token in re.findall(r"[a-z][a-z0-9]{1,}", normalized):
            if not is_ignored(token):
                tokens.add(token)

        for cjk_text in re.findall(r"[\u4e00-\u9fff]+", normalized):
            if len(cjk_text) < 2:
                continue
            if len(cjk_text) <= 8 and not is_ignored(cjk_text):
                tokens.add(cjk_text)
            max_size = min(4, len(cjk_text))
            for size in range(2, max_size + 1):
                for start in range(0, len(cjk_text) - size + 1):
                    token = cjk_text[start : start + size]
                    if not is_ignored(token):
                        tokens.add(token)
    return tokens


def _is_high_signal_codex_diary_topic_token(token: str) -> bool:
    if re.fullmatch(r"[a-z0-9]{2,}", token):
        return token not in {"ui", "api"}
    return len(token) >= 2


def _build_codex_diary_topic_signature(record: dict[str, Any]) -> dict[str, Any]:
    content_tokens = _extract_codex_diary_topic_tokens(
        record.get("user_request"),
        record.get("assistant_result"),
    )
    context_tokens = _extract_codex_diary_topic_tokens(record.get("thread_title"))
    tokens = set(content_tokens)
    if len(tokens) < 3:
        tokens.update(context_tokens)
    anchors = {
        token
        for token in tokens
        if _is_high_signal_codex_diary_topic_token(token)
    }
    return {
        "tokens": tokens,
        "content_tokens": content_tokens,
        "context_tokens": context_tokens,
        "anchors": anchors,
    }


def _score_codex_diary_topic_group(record: dict[str, Any], group: dict[str, Any]) -> int:
    signature = record.get("codex_diary_topic_signature") or {}
    tokens = set(signature.get("tokens") or [])
    group_tokens = set(group.get("tokens") or [])
    if not tokens or not group_tokens:
        return 0

    shared = tokens & group_tokens
    if not shared:
        return 0

    content_shared = set(signature.get("content_tokens") or []) & set(group.get("content_tokens") or [])
    anchor_shared = set(signature.get("anchors") or []) & set(group.get("anchors") or [])
    same_thread = bool(record.get("thread_id")) and str(record.get("thread_id")) in set(group.get("thread_ids") or set())
    has_english_anchor = any(re.fullmatch(r"[a-z0-9]{2,}", token) for token in anchor_shared)

    if not content_shared and not has_english_anchor:
        if not (same_thread and len(shared) >= 2 and len(set(signature.get("content_tokens") or [])) < 3):
            return 0

    score = len(shared) + len(content_shared) * 2 + len(anchor_shared) * 2
    if has_english_anchor:
        score += 4
    if same_thread:
        score += 1
    return score


def _select_codex_diary_topic_group(record: dict[str, Any], groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    scored = [
        (_score_codex_diary_topic_group(record, group), group)
        for group in groups
    ]
    candidates = [(score, group) for score, group in scored if score >= 4]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            -item[0],
            float(item[1]["start_at"]),
            str(item[1]["group_key"]),
        ),
    )[0][1]


def _build_codex_diary_record_fallback_group_key(record: dict[str, Any], index: int) -> str:
    thread_id = str(record.get("thread_id") or "").strip()
    title_seed = _normalize_project_palette_token(record.get("thread_title") or record.get("user_request"))
    start_at = str(record.get("start_at") or "").strip()
    if thread_id and title_seed:
        return f"topic:{thread_id}:{title_seed[:40]}"
    if thread_id:
        return f"topic:{thread_id}"
    if title_seed:
        return f"topic:{title_seed[:40]}"
    return f"record:{start_at or index}"


def _build_codex_diary_topic_groups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        signature = _build_codex_diary_topic_signature(record)
        record["codex_diary_topic_signature"] = signature
        target = _select_codex_diary_topic_group(record, groups)
        if target is None:
            target = {
                "group_key": _build_codex_diary_record_fallback_group_key(record, index),
                "records": [],
                "tokens": set(),
                "content_tokens": set(),
                "anchors": set(),
                "thread_ids": set(),
                "start_at": float(record.get("start_at") or 0),
            }
            groups.append(target)

        target["records"].append(record)
        target["tokens"].update(signature["tokens"])
        target["content_tokens"].update(signature["content_tokens"])
        target["anchors"].update(signature["anchors"])
        if record.get("thread_id"):
            target["thread_ids"].add(str(record.get("thread_id")))
        target["start_at"] = min(float(target["start_at"] or 0), float(record.get("start_at") or 0))
    return groups


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
    palette_items = [
        item
        for item in _build_note_type_palette_response(user_id, session).get("items", [])
        if isinstance(item, dict) and not _is_imported_script_category_key(item.get("key"))
    ]
    if not any(str(item.get("key") or "").strip() == NOTE_CATEGORY_DEFAULT for item in palette_items):
        palette_items.insert(0, _fallback_note_ai_default_category_item())
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


def _build_codex_diary_note_title_hints(
    user_id: int,
    session: Session,
    *,
    allowed_category_keys: set[str],
    palette_lookup: dict[str, dict[str, Any]],
) -> dict[str, str]:
    rows = session.exec(
        select(NoteNode)
        .where(NoteNode.user_id == user_id)
        .where(_active_note_condition())
        .order_by(NoteNode.updated_at.desc())
        .limit(5000)
    ).all()
    hints: dict[str, str] = {}
    for note in rows:
        if _get_custom_field_value(note.custom_fields, CODEX_DIARY_DATE_FIELD):
            continue
        category_key = str(note.primary_category or "").strip()
        if not category_key:
            normalized_categories = normalize_note_categories(note.note_categories, fallback_category=NOTE_CATEGORY_DEFAULT)
            category_key = str(derive_primary_category(normalized_categories, NOTE_CATEGORY_DEFAULT) or "").strip()
        if not category_key:
            continue
        if category_key not in allowed_category_keys:
            continue
        if _is_codex_diary_ai_palette_item(_codex_diary_category_item_by_key(category_key, palette_lookup=palette_lookup)):
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
    scores = _build_codex_diary_category_scores(
        turn,
        palette_lookup=palette_lookup,
        title_hints=title_hints,
    )
    weighted_categories = _resolve_codex_diary_weighted_categories(scores, palette_lookup=palette_lookup)
    primary_category = derive_primary_category(weighted_categories, fallback_category=NOTE_CATEGORY_DEFAULT)
    return _codex_diary_category_result(primary_category, palette_lookup=palette_lookup)


def _resolve_codex_diary_group_categories(
    records: list[dict[str, Any]],
    *,
    palette_lookup: dict[str, dict[str, Any]],
    title_hints: dict[str, str],
    primary_category_key: str | None = None,
) -> list[dict[str, int | str]]:
    merged = {
        "user_request": " ".join(str(record.get("user_request") or "") for record in records),
        "assistant_result": " ".join(str(record.get("assistant_result") or "") for record in records),
        "project_label": " ".join(str(record.get("project_label") or "") for record in records),
        "thread_title": " ".join(str(record.get("thread_title") or "") for record in records),
        "source_root_dir": " ".join(str(record.get("source_root_dir") or "") for record in records),
    }
    scores = _build_codex_diary_category_scores(
        merged,
        palette_lookup=palette_lookup,
        title_hints=title_hints,
    )
    note_categories = _resolve_codex_diary_weighted_categories(scores, palette_lookup=palette_lookup)
    primary_key = primary_category_key or derive_primary_category(note_categories, fallback_category=NOTE_CATEGORY_DEFAULT)
    return _enforce_codex_diary_primary_category(note_categories, primary_key)


def _annotate_codex_diary_record_category(
    record: dict[str, Any],
    *,
    palette_lookup: dict[str, dict[str, Any]],
    title_hints: dict[str, str],
) -> None:
    scores = _build_codex_diary_category_scores(
        record,
        palette_lookup=palette_lookup,
        title_hints=title_hints,
    )
    note_categories = _resolve_codex_diary_weighted_categories(scores, palette_lookup=palette_lookup)
    primary_category = derive_primary_category(note_categories, fallback_category=NOTE_CATEGORY_DEFAULT)
    category = _codex_diary_category_result(primary_category, palette_lookup=palette_lookup)
    record["codex_diary_category_scores"] = scores
    record["codex_diary_category_key"] = primary_category
    record["codex_diary_category"] = category


def _codex_diary_record_primary_score(record: dict[str, Any]) -> int:
    category_key = str(record.get("codex_diary_category_key") or NOTE_CATEGORY_DEFAULT)
    scores = record.get("codex_diary_category_scores") or {}
    try:
        return int(scores.get(category_key) or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def _should_start_new_codex_diary_message_segment(
    record: dict[str, Any],
    current_segment: dict[str, Any],
) -> bool:
    record_category = str(record.get("codex_diary_category_key") or NOTE_CATEGORY_DEFAULT)
    segment_category = str(current_segment.get("category_key") or NOTE_CATEGORY_DEFAULT)
    if record_category == segment_category:
        return False

    record_is_specific = _is_specific_codex_diary_category_key(record_category)
    segment_is_specific = _is_specific_codex_diary_category_key(segment_category)
    record_score = _codex_diary_record_primary_score(record)

    if record_is_specific and segment_is_specific:
        return record_score >= 20
    if record_is_specific and not segment_is_specific:
        return record_score >= 24
    return False


def _new_codex_diary_message_segment(record: dict[str, Any], thread_key: str, segment_index: int) -> dict[str, Any]:
    category_key = str(record.get("codex_diary_category_key") or NOTE_CATEGORY_DEFAULT)
    return {
        "group_key": f"segment:{thread_key}:{segment_index}:{category_key}",
        "thread_key": thread_key,
        "records": [],
        "category_key": category_key,
        "start_at": float(record.get("start_at") or 0),
        "end_at": float(record.get("end_at") or record.get("start_at") or 0),
        "duration_seconds": 0.0,
    }


def _append_record_to_codex_diary_message_segment(segment: dict[str, Any], record: dict[str, Any]) -> None:
    segment["records"].append(record)
    segment["start_at"] = min(float(segment["start_at"]), float(record.get("start_at") or 0))
    segment["end_at"] = max(float(segment["end_at"]), float(record.get("end_at") or record.get("start_at") or 0))
    segment["duration_seconds"] = float(segment.get("duration_seconds") or 0) + float(record.get("duration_seconds") or 0)


def _build_codex_diary_segment_topic_signature(segment: dict[str, Any]) -> dict[str, set[str]]:
    tokens: set[str] = set()
    content_tokens: set[str] = set()
    anchors: set[str] = set()
    for record in segment.get("records") or []:
        signature = record.get("codex_diary_topic_signature")
        if not isinstance(signature, dict):
            signature = _build_codex_diary_topic_signature(record)
            record["codex_diary_topic_signature"] = signature
        tokens.update(signature.get("tokens") or set())
        content_tokens.update(signature.get("content_tokens") or set())
        anchors.update(signature.get("anchors") or set())
    return {
        "tokens": tokens,
        "content_tokens": content_tokens,
        "anchors": anchors,
    }


def _annotate_codex_diary_segment_topic(segment: dict[str, Any]) -> None:
    signature = _build_codex_diary_segment_topic_signature(segment)
    segment["tokens"] = signature["tokens"]
    segment["content_tokens"] = signature["content_tokens"]
    segment["anchors"] = signature["anchors"]
    segment["thread_ids"] = {
        str(record.get("thread_id"))
        for record in segment.get("records") or []
        if record.get("thread_id")
    }


def _can_merge_codex_diary_segment_into_current(
    segment: dict[str, Any],
    current_segments: list[dict[str, Any]],
) -> bool:
    if not current_segments:
        return True

    segment_tokens = set(segment.get("tokens") or set())
    current_tokens = {
        token
        for item in current_segments
        for token in set(item.get("tokens") or set())
    }
    if not segment_tokens or not current_tokens:
        return False

    shared = segment_tokens & current_tokens
    if not shared:
        return False

    segment_content_tokens = set(segment.get("content_tokens") or set())
    current_content_tokens = {
        token
        for item in current_segments
        for token in set(item.get("content_tokens") or set())
    }
    segment_anchors = set(segment.get("anchors") or set())
    current_anchors = {
        token
        for item in current_segments
        for token in set(item.get("anchors") or set())
    }
    segment_thread_ids = set(segment.get("thread_ids") or set())
    current_thread_ids = {
        thread_id
        for item in current_segments
        for thread_id in set(item.get("thread_ids") or set())
    }

    content_shared = segment_content_tokens & current_content_tokens
    anchor_shared = segment_anchors & current_anchors
    same_thread = bool(segment_thread_ids & current_thread_ids)
    has_english_anchor = any(re.fullmatch(r"[a-z0-9]{2,}", token) for token in anchor_shared)

    if not content_shared and not has_english_anchor:
        if not (same_thread and len(shared) >= 2):
            return False

    score = len(shared) + len(content_shared) * 2 + len(anchor_shared) * 2
    if has_english_anchor:
        score += 4
    if same_thread:
        score += 1
    return score >= 4


def _build_codex_diary_message_segments(
    records: list[dict[str, Any]],
    *,
    palette_lookup: dict[str, dict[str, Any]],
    title_hints: dict[str, str],
) -> list[dict[str, Any]]:
    thread_order: list[str] = []
    records_by_thread: dict[str, list[dict[str, Any]]] = {}
    for index, record in enumerate(records):
        _annotate_codex_diary_record_category(
            record,
            palette_lookup=palette_lookup,
            title_hints=title_hints,
        )
        thread_key = str(record.get("thread_id") or "").strip() or f"record:{index}"
        if thread_key not in records_by_thread:
            records_by_thread[thread_key] = []
            thread_order.append(thread_key)
        records_by_thread[thread_key].append(record)

    segments: list[dict[str, Any]] = []
    for thread_key in sorted(thread_order, key=lambda key: min(float(item.get("start_at") or 0) for item in records_by_thread[key])):
        current_segment: dict[str, Any] | None = None
        segment_index = 0
        for record in sorted(records_by_thread[thread_key], key=lambda item: (float(item.get("start_at") or 0), float(item.get("end_at") or 0))):
            if current_segment is None or _should_start_new_codex_diary_message_segment(record, current_segment):
                segment_index += 1
                current_segment = _new_codex_diary_message_segment(record, thread_key, segment_index)
                segments.append(current_segment)
            _append_record_to_codex_diary_message_segment(current_segment, record)

    for segment in segments:
        _annotate_codex_diary_segment_topic(segment)

    return sorted(segments, key=lambda item: (float(item["start_at"]), str(item["group_key"])))


def _codex_diary_turn_duration_seconds(turn: dict[str, Any]) -> float:
    try:
        start_at = float(turn.get("start_at") or 0)
        end_at = float(turn.get("end_at") or 0)
    except (TypeError, ValueError):
        return 60.0
    return max(60.0, end_at - start_at)


def _codex_diary_duration_minutes(duration_seconds: Any) -> int:
    try:
        return max(1, round(float(duration_seconds or 0) / 60))
    except (TypeError, ValueError):
        return 1


def _build_codex_diary_completion_progress_expr(block: dict[str, Any]) -> str:
    minutes = _codex_diary_duration_minutes(block.get("duration_seconds"))
    return f"{minutes}/{CODEX_DIARY_PROGRESS_BASE_MINUTES}"


def _build_codex_diary_worklog(
    run: CodexDiaryImportRun,
    block: dict[str, Any],
    *,
    source_thread_ids: list[str],
) -> dict[str, Any]:
    records = block.get("records") or []
    duration_seconds = max(0, int(round(float(block.get("duration_seconds") or 0))))
    start_at = float(block.get("start_at") or 0)
    end_at = float(block.get("end_at") or start_at)
    device_names = sorted({
        str(record.get("source_device_name") or "").strip()
        for record in records
        if str(record.get("source_device_name") or "").strip()
    })
    return {
        "version": 1,
        "date": run.diary_date,
        "timezone": run.timezone,
        "scope_key": run.scope_key,
        "block_key": str(block.get("block_key") or ""),
        "duration_seconds": duration_seconds,
        "duration_minutes": _codex_diary_duration_minutes(duration_seconds),
        "start_at": start_at,
        "end_at": end_at,
        "turn_count": len(records),
        "source_thread_ids": source_thread_ids,
        "source_devices": device_names,
    }


def _build_codex_diary_block_key(block: dict[str, Any]) -> str:
    records = block.get("records") or []
    return hashlib.sha1(
        json.dumps(
            {
                "group_key": block.get("group_key"),
                "note_categories": block.get("note_categories"),
                "category_key": block.get("category_key"),
                "start_at": block.get("start_at"),
                "end_at": block.get("end_at"),
                "threads": sorted({str(record.get("thread_id") or "") for record in records}),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]


def _merge_codex_diary_blocks_for_target_duration(
    blocks: list[dict[str, Any]],
    *,
    palette_lookup: dict[str, dict[str, Any]],
    title_hints: dict[str, str],
) -> list[dict[str, Any]]:
    if len(blocks) <= 1:
        return blocks

    merged_blocks: list[dict[str, Any]] = []
    current_by_category: dict[str, list[dict[str, Any]]] = {}
    duration_by_category: dict[str, float] = {}

    def close_current(category_key: str) -> None:
        current = current_by_category.get(category_key) or []
        current_duration = float(duration_by_category.get(category_key) or 0)
        if not current:
            return

        records = [
            record
            for block in current
            for record in (block.get("records") or [])
        ]
        if not records:
            current_by_category[category_key] = []
            duration_by_category[category_key] = 0.0
            return

        start_at = min(float(record.get("start_at") or 0) for record in records)
        end_at = max(float(record.get("end_at") or record.get("start_at") or 0) for record in records)
        note_categories = _enforce_codex_diary_primary_category(
            normalize_note_categories(current[0].get("note_categories"), fallback_category=category_key),
            category_key,
        )
        category = _codex_diary_category_result(category_key, palette_lookup=palette_lookup)
        category_labels = _codex_diary_category_labels_for_weights(note_categories, palette_lookup=palette_lookup)
        block = {
            "group_key": "timebucket:" + "|".join(str(item.get("block_key") or item.get("group_key") or "") for item in current),
            "note_categories": note_categories,
            "category_key": category_key,
            "category_label": " / ".join(category_labels) or str(category.get("label") or category_key),
            "records": records,
            "duration_seconds": current_duration,
            "start_at": start_at,
            "end_at": end_at,
        }
        block["title"] = _build_codex_diary_title(block)
        block["block_key"] = _build_codex_diary_block_key(block)
        merged_blocks.append(block)
        current_by_category[category_key] = []
        duration_by_category[category_key] = 0.0

    for block in sorted(blocks, key=lambda item: (float(item["start_at"]), str(item["block_key"]))):
        duration = float(block.get("duration_seconds") or 0)
        category_key = str(block.get("category_key") or NOTE_CATEGORY_DEFAULT)
        current = current_by_category.setdefault(category_key, [])
        current_duration = float(duration_by_category.get(category_key) or 0)
        if current and current_duration + duration > CODEX_DIARY_TARGET_SECONDS:
            close_current(category_key)
            current = current_by_category.setdefault(category_key, [])
            current_duration = 0.0
        current.append(block)
        duration_by_category[category_key] = current_duration + duration
    for category_key in list(current_by_category):
        close_current(category_key)
    return sorted(merged_blocks, key=lambda item: (float(item["start_at"]), str(item["category_label"]), str(item["block_key"])))


def _absorb_tiny_codex_diary_blocks(
    blocks: list[dict[str, Any]],
    *,
    palette_lookup: dict[str, dict[str, Any]],
    title_hints: dict[str, str],
) -> list[dict[str, Any]]:
    if len(blocks) <= 1:
        return blocks

    ordered_blocks = sorted(blocks, key=lambda item: (float(item["start_at"]), str(item["block_key"])))
    core_blocks = [
        block
        for block in ordered_blocks
        if float(block.get("duration_seconds") or 0) > CODEX_DIARY_TINY_TAIL_SECONDS
    ]
    if not core_blocks:
        total_duration = sum(float(block.get("duration_seconds") or 0) for block in ordered_blocks)
        if total_duration > CODEX_DIARY_TARGET_SECONDS:
            return ordered_blocks
        core_blocks = [
            sorted(
                ordered_blocks,
                key=lambda item: (
                    -float(item.get("duration_seconds") or 0),
                    float(item.get("start_at") or 0),
                    str(item.get("block_key") or ""),
                ),
            )[0]
        ]

    core_ids = {id(core) for core in core_blocks}
    tiny_blocks = [
        block
        for block in ordered_blocks
        if float(block.get("duration_seconds") or 0) <= CODEX_DIARY_TINY_TAIL_SECONDS
        and id(block) not in core_ids
    ]
    if not tiny_blocks:
        return ordered_blocks

    absorbed_ids: set[int] = set()

    def block_midpoint(block: dict[str, Any]) -> float:
        start_at = float(block.get("start_at") or 0)
        end_at = float(block.get("end_at") or start_at)
        return (start_at + end_at) / 2

    def refresh_target(target: dict[str, Any]) -> None:
        category_key = str(target.get("category_key") or NOTE_CATEGORY_DEFAULT)
        records = list(target.get("records") or [])
        target["records"] = records
        target["start_at"] = min(float(record.get("start_at") or 0) for record in records)
        target["end_at"] = max(float(record.get("end_at") or record.get("start_at") or 0) for record in records)
        target["note_categories"] = _resolve_codex_diary_group_categories(
            records,
            palette_lookup=palette_lookup,
            title_hints=title_hints,
            primary_category_key=category_key,
        )
        category_labels = _codex_diary_category_labels_for_weights(target["note_categories"], palette_lookup=palette_lookup)
        if category_labels:
            target["category_label"] = " / ".join(category_labels)
        target["title"] = _build_codex_diary_title(target)
        target["block_key"] = _build_codex_diary_block_key(target)

    for tiny in tiny_blocks:
        tiny_duration = float(tiny.get("duration_seconds") or 0)
        if tiny_duration <= 0:
            continue
        tiny_category = str(tiny.get("category_key") or NOTE_CATEGORY_DEFAULT)
        tiny_midpoint = block_midpoint(tiny)
        candidates = [
            target
            for target in core_blocks
            if float(target.get("duration_seconds") or 0) + tiny_duration <= CODEX_DIARY_TARGET_SECONDS
        ]
        if not candidates:
            continue
        target = sorted(
            candidates,
            key=lambda item: (
                0 if str(item.get("category_key") or NOTE_CATEGORY_DEFAULT) == tiny_category else 1,
                abs(block_midpoint(item) - tiny_midpoint),
                -float(item.get("duration_seconds") or 0),
            ),
        )[0]
        target["records"] = [*(target.get("records") or []), *(tiny.get("records") or [])]
        target["duration_seconds"] = float(target.get("duration_seconds") or 0) + tiny_duration
        refresh_target(target)
        absorbed_ids.add(id(tiny))

    result = [block for block in ordered_blocks if id(block) not in absorbed_ids]
    return sorted(result, key=lambda item: (float(item["start_at"]), str(item["category_label"]), str(item["block_key"])))


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
    candidate = _normalize_codex_diary_ai_title(phrase)
    if candidate and not _is_codex_diary_dialogue_sentence(candidate, [str(record.get("user_request") or "") for record in records]):
        return _truncate_codex_diary_text(candidate, 32, suffix="")
    for record in records:
        candidate = _normalize_codex_diary_ai_title(record.get("thread_title"))
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
    seen: set[str] = set()
    for entry in _build_codex_diary_summary_entries(records):
        candidate = _normalize_codex_diary_ai_title(entry.get("title"))
        if candidate and candidate not in seen and candidate != "Codex 事项":
            return _truncate_codex_diary_text(candidate, 32, suffix="") or "Codex 日记"
        seen.add(candidate)
    return "Codex 日记"


def _extract_codex_diary_ai_json(raw_content: Any) -> dict[str, Any]:
    content = str(raw_content or "").strip()
    if not content:
        raise ValueError("AI 没有返回可解析的日记草案")
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
        raise ValueError("AI 日记草案不是 JSON 对象")
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
            for separator in ("，", ",", "。", "；", ";", "：", ":", "、"):
                marker = f"{prefix}{separator}"
                if text.startswith(marker):
                    text = text[len(marker):].strip(" ：:，,；;。")
                    changed = True
                    break
            if changed:
                break
    text = re.sub(r"^(你这个反馈是对的|这个反馈是对的)[，,；;。]*", "", text).strip(" ：:，,；;。")
    text = re.sub(r"^(问题确实是|问题是|原因是|这次是|这次不是)[：:，,；;。]*", "", text).strip(" ：:，,；;。")
    text = re.sub(r"^(先按你说的|按你说的)[“\"'：:，,；;。]*", "", text).strip(" ：:，,；;。”\"'")
    text = re.sub(r"^(小模块先修处理了|已修这块|已经修好|已修好|修复了|处理了)[：:，,；;。]*", "", text).strip(" ：:，,；;。")
    return text


def _is_codex_diary_low_information_title(value: Any) -> bool:
    text = _strip_codex_diary_title_noise(value)
    if not text:
        return True
    if "?" in text or "？" in text:
        return True
    normalized = re.sub(r"[\s：:，,；;。.!！、]+", "", text)
    if not normalized:
        return True
    if normalized in CODEX_DIARY_TITLE_BAD_VALUES:
        return True
    return False


def _choose_primary_codex_diary_title_clause(value: str) -> str:
    text = str(value or "").strip(" ：:，,；;。")
    parts = [
        part.strip(" ：:，,；;。")
        for part in CODEX_DIARY_TITLE_PARALLEL_RE.split(text)
        if part.strip(" ：:，,；;。")
    ]
    if len(parts) <= 1:
        return text
    for part in parts:
        stripped = _strip_codex_diary_title_noise(part)
        if (
            stripped
            and stripped not in CODEX_DIARY_TITLE_BAD_VALUES
            and (len(stripped) > 2 or any(keyword.lower() in stripped.lower() for keyword in CODEX_DIARY_TITLE_KEYWORDS))
        ):
            return stripped
    return parts[0] if parts else text


def _normalize_codex_diary_ai_title(value: Any) -> str:
    text = _strip_codex_diary_title_noise(value)
    text = re.sub(r"^(综合|杂项|多项整合|多项处理|多项事项|general|CodeYun/笔记|codeyun|Codex)[：:\s]*", "", text, flags=re.IGNORECASE)
    text = _choose_primary_codex_diary_title_clause(text)
    text = text.rstrip(".…")
    if not text or _is_codex_diary_low_information_title(text):
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
            "你在为“星图笔记”生成 Codex 价值日记节点草案。",
            "目标不是复述流水账，也不是全面解释所有细节，而是捕捉今天真正最重要的价值：核心做成了什么、突破了什么、留下了什么可复用成果。",
            "输入已经先拆成候选事项，再按当天任务语义和累计不超过约 10 小时工作量聚合成 block；你必须逐块输出，不要新增、删除、合并或拆分 block。",
            "只根据每条记录的 user_request、assistant_result、thread_title 做语义归纳；assistant_result 优先。",
            "不要照搬聊天原文，不要输出工具日志、JSON、堆栈、操作记录、文件大段内容。",
            "先判断这个 block 的主价值是什么，再围绕主价值生成标题和正文；琐碎过程只在能支撑主价值时保留。",
            "标题必须优先体现这一块的核心工作突破、关键结果或主要事宜；不要为了全面覆盖所有琐碎细节而堆叠标题。",
            "一个标题只能表达一件事；不要用“与”“和”“及”“以及”“、”把两个事项并列写进标题。",
            "如果一个 block 里确实包含多件事，标题只选择最重要的主线事项，其余事项放到正文条目里。",
            "标题必须是信息密度高的短名词短语，保留最关键对象/动作/接口/页面/字段/路径名；不要带分类前缀。",
            "标题不要使用“综合”“杂项”“多项整合”这类笼统词；多个事项并列时只保留最能代表主线的一个或两个具体对象。",
            "标题禁止使用“是的”“可以”“好的”“已改完”“已经删了”这类低信息开头或低信息标题。",
            "正文条目写成总结性价值记录，每条只讲主成果、关键决策、实证结果、风险或后续点；把零散操作合并成少量主线条目。",
            "避免把节点写成“做了 A、看了 B、顺手改了 C”的流水账；低价值细节可以省略。",
            "summary_items 返回纯文本数组，每个数组元素不要自带 1.、2.、一、这类编号；编号由星图笔记编辑器自动生成。",
            "阶段通常为 done；不要输出进度，进度由后端按块累计时长自动计算。",
            "最终只输出 JSON 对象，不要 Markdown，不要解释。",
        ]
    )


def _build_codex_diary_ai_user_prompt(source: dict[str, Any], blocks: list[dict[str, Any]]) -> str:
    payload_blocks: list[dict[str, Any]] = []
    for block in blocks:
        records: list[dict[str, Any]] = []
        block_records = list(block.get("records") or [])
        prompt_records = block_records
        omitted_record_count = 0
        if len(block_records) > CODEX_DIARY_AI_RECORD_LIMIT_PER_BLOCK:
            edge_count = CODEX_DIARY_AI_EDGE_RECORD_COUNT_PER_BLOCK
            prompt_records = [*block_records[:edge_count], *block_records[-edge_count:]]
            omitted_record_count = len(block_records) - len(prompt_records)
        for record in prompt_records:
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
                "note_categories": block.get("note_categories"),
                "category_key": block.get("category_key"),
                "category_label": block.get("category_label"),
                "start_time": _format_codex_diary_time(float(block.get("start_at") or 0)),
                "end_time": _format_codex_diary_time(float(block.get("end_at") or 0)),
                "duration_minutes": _codex_diary_duration_minutes(block.get("duration_seconds")),
                "record_count": len(block_records),
                "omitted_middle_record_count": omitted_record_count,
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
                }
            ]
        },
    }
    return json.dumps(request_payload, ensure_ascii=False, indent=2)


def _draft_codex_diary_block_without_ai(block: dict[str, Any]) -> dict[str, Any]:
    records = list(block.get("records") or [])
    summary_items = [
        str(entry.get("summary") or "").strip()
        for entry in _build_codex_diary_summary_entries(records)
        if str(entry.get("summary") or "").strip()
    ]
    if not summary_items:
        summary_items = ["该事项已有 Codex 处理结果，细节可回到原会话查看。"]
    block["title"] = _normalize_codex_diary_ai_title(block.get("title")) or _build_codex_diary_title(block)
    block["summary_items"] = summary_items[:6]
    block["lifecycle_stage"] = (
        "done" if any(str(record.get("assistant_result") or "").strip() for record in records) else "doing"
    )
    return block


def _draft_codex_diary_blocks_without_ai(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_draft_codex_diary_block_without_ai(block) for block in blocks]


def _draft_codex_diary_blocks_with_ai(
    source: dict[str, Any],
    blocks: list[dict[str, Any]],
    *,
    current_user: User,
    session: Session,
) -> list[dict[str, Any]]:
    if not blocks:
        return blocks
    runtime = resolve_ai_app_runtime_config(
        session=session,
        current_user=current_user,
        app_id=AI_APP_CODEX_DIARY,
    )
    response = chat_with_provider(
        provider_id=str(runtime["provider"]),
        base_url=runtime["base_url"],
        api_key=runtime["api_key"],
        model=runtime["model"],
        system_prompt=_build_codex_diary_ai_system_prompt(),
        messages=[{"role": "user", "content": _build_codex_diary_ai_user_prompt(source, blocks)}],
        response_format="json",
        timeout_seconds=CODEX_DIARY_TIMEOUT_SECONDS,
        extra_providers=runtime["extra_providers"],
    )
    payload = _extract_codex_diary_ai_json(response.get("content"))
    draft_blocks = payload.get("blocks")
    if not isinstance(draft_blocks, list):
        raise ValueError("AI 日记草案缺少 blocks")
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
            raise ValueError(f"AI 日记草案缺少 block：{block_key}")
        title = _normalize_codex_diary_ai_title(draft.get("title"))
        if not title:
            raise ValueError(f"AI 日记草案标题无效：{block_key}")
        summary_items = _normalize_codex_diary_ai_summary_items(draft.get("summary_items"))
        if not summary_items:
            raise ValueError(f"AI 日记草案正文为空：{block_key}")
        block["title"] = title
        block["summary_items"] = summary_items
        block["lifecycle_stage"] = str(draft.get("lifecycle_stage") or "done").strip() or "done"
    return blocks


def _draft_codex_diary_blocks_in_batches(
    source: dict[str, Any],
    blocks: list[dict[str, Any]],
    *,
    current_user: User,
    session: Session,
    run: CodexDiaryImportRun | None = None,
) -> list[dict[str, Any]]:
    if not blocks:
        return blocks

    def draft_batch_with_split_retry(
        batch: list[dict[str, Any]],
        *,
        batch_index: int,
        total_batches: int,
        depth: int = 0,
    ) -> list[dict[str, Any]]:
        try:
            return _draft_codex_diary_blocks_with_ai(
                source,
                batch,
                current_user=current_user,
                session=session,
            )
        except Exception as exc:
            if len(batch) <= 1:
                raise
            mid = max(1, len(batch) // 2)
            if run is not None:
                retry_events = list((run.result_json or {}).get("draft_retry_events") or [])
                retry_events.append(
                    {
                        "batch_index": batch_index,
                        "total_batches": total_batches,
                        "depth": depth,
                        "block_count": len(batch),
                        "split_sizes": [mid, len(batch) - mid],
                        "error": str(getattr(exc, "detail", None) or exc),
                    }
                )
                run.result_json = {**(run.result_json or {}), "draft_retry_events": retry_events}
                _touch_codex_diary_run(
                    session,
                    run,
                    status="running",
                    stage="drafting_retry",
                    stage_label=f"AI 草案缺块，拆分重试 {batch_index}/{total_batches}",
                )
            return [
                *draft_batch_with_split_retry(
                    batch[:mid],
                    batch_index=batch_index,
                    total_batches=total_batches,
                    depth=depth + 1,
                ),
                *draft_batch_with_split_retry(
                    batch[mid:],
                    batch_index=batch_index,
                    total_batches=total_batches,
                    depth=depth + 1,
                ),
            ]

    drafted_blocks: list[dict[str, Any]] = []
    total_batches = (len(blocks) + CODEX_DIARY_DRAFT_BATCH_SIZE - 1) // CODEX_DIARY_DRAFT_BATCH_SIZE
    for batch_index, offset in enumerate(range(0, len(blocks), CODEX_DIARY_DRAFT_BATCH_SIZE), start=1):
        if run is not None:
            _touch_codex_diary_run(
                session,
                run,
                status="running",
                stage="drafting",
                stage_label=f"调用 AI 生成日记草案 {batch_index}/{total_batches}",
            )
        batch = blocks[offset : offset + CODEX_DIARY_DRAFT_BATCH_SIZE]
        try:
            drafted_blocks.extend(draft_batch_with_split_retry(batch, batch_index=batch_index, total_batches=total_batches))
        except Exception as exc:
            error_message = str(getattr(exc, "detail", None) or exc)
            if run is not None:
                _touch_codex_diary_run(
                    session,
                    run,
                    status="running",
                    stage="drafting_fallback",
                    stage_label=f"AI 草案失败，使用规则摘要 {batch_index}/{total_batches}",
                )
                fallback_events = list((run.result_json or {}).get("draft_fallback_events") or [])
                fallback_events.append(
                    {
                        "batch_index": batch_index,
                        "total_batches": total_batches,
                        "error": error_message,
                    }
                )
                run.result_json = {**(run.result_json or {}), "draft_fallback_events": fallback_events}
                session.add(run)
                session.commit()
            drafted_blocks.extend(_draft_codex_diary_blocks_without_ai(batch))
    return drafted_blocks


def _build_codex_diary_body_html(block: dict[str, Any]) -> str:
    records = block["records"]
    device_names = sorted({str(record.get("source_device_name") or "").strip() for record in records if record.get("source_device_name")})
    start_text = _format_codex_diary_time(block["start_at"])
    end_text = _format_codex_diary_time(block["end_at"])
    minutes = _codex_diary_duration_minutes(block.get("duration_seconds"))
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
    allowed_category_keys = {
        str(item.get("key") or "").strip()
        for item in _iter_unique_codex_diary_palette_items(palette_lookup)
        if str(item.get("key") or "").strip()
    }
    records: list[dict[str, Any]] = []
    for raw_record in sorted(source.get("turn_records") or [], key=lambda item: (float(item.get("start_at") or 0), str(item.get("thread_id") or ""))):
        record = dict(raw_record)
        record["duration_seconds"] = _codex_diary_turn_duration_seconds(record)
        records.append(record)
    title_hints = _build_codex_diary_note_title_hints(
        user_id,
        session,
        allowed_category_keys=allowed_category_keys,
        palette_lookup=palette_lookup,
    )

    message_segments = _build_codex_diary_message_segments(
        records,
        palette_lookup=palette_lookup,
        title_hints=title_hints,
    )
    for segment in message_segments:
        note_categories = _resolve_codex_diary_group_categories(
            segment["records"],
            palette_lookup=palette_lookup,
            title_hints=title_hints,
        )
        category_key = derive_primary_category(note_categories, fallback_category=NOTE_CATEGORY_DEFAULT)
        category = _codex_diary_category_result(category_key, palette_lookup=palette_lookup)
        segment["note_categories"] = note_categories
        segment["category_key"] = category_key
        segment["category_label"] = str(category.get("label") or category_key)
        for record in segment["records"]:
            record["codex_diary_category"] = category

    blocks: list[dict[str, Any]] = []
    current_segments: list[dict[str, Any]] = []
    current_merge_key = ""
    current_category_key = ""

    def close_current() -> None:
        nonlocal current_segments, current_duration, current_merge_key, current_category_key
        if not current_segments:
            return
        current_records = [
            record
            for segment in current_segments
            for record in (segment.get("records") or [])
        ]
        start_at = min(float(record.get("start_at") or 0) for record in current_records)
        end_at = max(float(record.get("end_at") or record.get("start_at") or 0) for record in current_records)
        category_key = current_category_key or NOTE_CATEGORY_DEFAULT
        note_categories = _resolve_codex_diary_group_categories(
            current_records,
            palette_lookup=palette_lookup,
            title_hints=title_hints,
            primary_category_key=category_key,
        )
        category = _codex_diary_category_result(category_key, palette_lookup=palette_lookup)
        category_labels = _codex_diary_category_labels_for_weights(note_categories, palette_lookup=palette_lookup)
        block = {
            "group_key": current_merge_key,
            "note_categories": note_categories,
            "category_key": category_key,
            "category_label": " / ".join(category_labels) or str(category.get("label") or category_key),
            "records": current_records,
            "duration_seconds": current_duration,
            "start_at": start_at,
            "end_at": end_at,
        }
        block["title"] = _build_codex_diary_title(block)
        block["block_key"] = _build_codex_diary_block_key(block)
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
            previous["note_categories"] = _resolve_codex_diary_group_categories(
                previous["records"],
                palette_lookup=palette_lookup,
                title_hints=title_hints,
                primary_category_key=previous["category_key"],
            )
            previous["category_label"] = " / ".join(
                _codex_diary_category_labels_for_weights(previous["note_categories"], palette_lookup=palette_lookup)
            ) or previous["category_label"]
            previous["title"] = _build_codex_diary_title(previous)
        else:
            blocks.append(block)
        current_segments = []
        current_duration = 0.0
        current_merge_key = ""
        current_category_key = ""

    current_duration = 0.0
    for segment in message_segments:
        category_key = str(segment.get("category_key") or NOTE_CATEGORY_DEFAULT)
        segment_duration = float(segment.get("duration_seconds") or 0)
        can_merge = (
            current_segments
            and category_key == current_category_key
            and _is_specific_codex_diary_category_key(category_key)
            and _can_merge_codex_diary_segment_into_current(segment, current_segments)
        )
        if current_segments and not can_merge:
            close_current()
        elif current_segments and current_duration + segment_duration > CODEX_DIARY_TARGET_SECONDS:
            close_current()
        if not current_segments:
            current_merge_key = f"{category_key}:{segment.get('group_key') or ''}"
            current_category_key = category_key
        current_segments.append(segment)
        current_duration += segment_duration
        if current_duration >= CODEX_DIARY_TARGET_SECONDS:
            close_current()
    close_current()

    blocks = sorted(blocks, key=lambda item: (float(item["start_at"]), str(item["category_label"]), str(item["block_key"])))
    merged_blocks = _merge_codex_diary_blocks_for_target_duration(
        blocks,
        palette_lookup=palette_lookup,
        title_hints=title_hints,
    )
    return _absorb_tiny_codex_diary_blocks(
        merged_blocks,
        palette_lookup=palette_lookup,
        title_hints=title_hints,
    )


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
    completion_expr = _build_codex_diary_completion_progress_expr(block)
    category_key = str(block.get("category_key") or NOTE_CATEGORY_DEFAULT)
    note_categories = normalize_note_categories(block.get("note_categories"), fallback_category=category_key)
    primary_category = derive_primary_category(note_categories, fallback_category=category_key)
    taxonomy_fields = _build_legacy_fields_from_taxonomy(
        note_categories,
        primary_category,
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
        [
            CODEX_DIARY_WORKLOG_FIELD,
            "json",
            _build_codex_diary_worklog(run, block, source_thread_ids=source_thread_ids),
        ],
    ]
    normalized_custom_fields = _apply_completion_progress_expr_to_note_data(
        {
            "custom_fields": custom_fields,
            "completion_progress_expr": completion_expr,
        },
        [],
    ).get("custom_fields", custom_fields)
    now = time.time()
    note_identity = allocate_new_note_identity(session)
    note = NoteNode(
        id=note_identity.primary_id,
        numeric_id=note_identity.numeric_id,
        legacy_id=note_identity.legacy_id,
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
        start_at=_codex_diary_note_start_at(run, block),
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


def _codex_diary_note_start_at(run: CodexDiaryImportRun, block: dict[str, Any]) -> float:
    start_at = float(block["start_at"])
    end_at = float(block.get("end_at") or start_at)
    try:
        _, day_start_at, _ = _parse_codex_diary_date(run.diary_date)
    except HTTPException:
        return start_at
    if start_at < day_start_at and end_at > day_start_at:
        return day_start_at
    return start_at


def _build_codex_diary_source_result(run: CodexDiaryImportRun, source: dict[str, Any]) -> dict[str, Any]:
    result = {
        "thread_count": run.source_thread_count,
        "turn_count": run.source_turn_count,
        "user_message_count": run.source_user_message_count,
        "assistant_message_count": run.source_assistant_message_count,
    }
    source_failures = source.get("source_failures")
    if isinstance(source_failures, list) and source_failures:
        result["source_failures"] = source_failures
    return result


def _build_codex_diary_blocks_result(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "block_key": block.get("block_key"),
            "title": block.get("title"),
            "note_categories": block.get("note_categories"),
            "category_key": block.get("category_key"),
            "category_label": block.get("category_label"),
            "duration_seconds": block.get("duration_seconds"),
            "completion_progress_expr": _build_codex_diary_completion_progress_expr(block),
            "start_at": block.get("start_at"),
            "end_at": block.get("end_at"),
            "source_thread_ids": sorted({str(record.get("thread_id") or "") for record in block.get("records") or []}),
        }
        for block in blocks
    ]


def _mark_codex_diary_import_run_failed(
    db_bind: Any,
    *,
    run_id: str,
    error_message: str,
    session: Session | None = None,
) -> None:
    now = time.time()

    def apply_failure(active_session: Session) -> bool:
        failed_run = active_session.get(CodexDiaryImportRun, run_id)
        if failed_run is None:
            return False
        failed_run.status = "failed"
        failed_run.stage = "failed"
        failed_run.stage_label = "导入失败"
        failed_run.error_message = error_message
        failed_run.finished_at = now
        failed_run.updated_at = now
        failed_run.heartbeat_at = now
        active_session.add(failed_run)
        active_session.commit()
        return True

    if session is not None:
        try:
            session.rollback()
            if apply_failure(session):
                return
        except Exception:
            try:
                session.rollback()
            except Exception:
                pass

    try:
        with Session(db_bind) as fallback_session:
            apply_failure(fallback_session)
    except Exception:
        pass


def _run_codex_diary_import_worker(
    db_bind: Any,
    *,
    run_id: str,
    user_id: int,
    entry_specs: list[dict[str, Any]],
    root_identity: dict[str, str],
) -> None:
    heartbeat_stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None

    def start_heartbeat() -> None:
        nonlocal heartbeat_thread
        if heartbeat_thread is not None and heartbeat_thread.is_alive():
            return

        def _heartbeat_loop() -> None:
            while not heartbeat_stop.wait(CODEX_DIARY_HEARTBEAT_INTERVAL_SECONDS):
                now = time.time()
                try:
                    with Session(db_bind) as heartbeat_session:
                        heartbeat_run = heartbeat_session.get(CodexDiaryImportRun, run_id)
                        if heartbeat_run is None or heartbeat_run.status != "running":
                            return
                        heartbeat_run.heartbeat_at = now
                        heartbeat_run.updated_at = now
                        heartbeat_session.add(heartbeat_run)
                        heartbeat_session.commit()
                except Exception:
                    continue

        heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        heartbeat_thread.start()

    def stop_heartbeat() -> None:
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1.0)

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
                run.result_json = {
                    "prompt_version": CODEX_DIARY_PROMPT_VERSION,
                    "source": _build_codex_diary_source_result(run, source),
                    "blocks": [],
                }
                run.status = "completed"
                run.stage = "empty"
                run.stage_label = "当天没有可导入的 Codex 会话记录"
                run.finished_at = time.time()
                run.updated_at = run.finished_at
                run.heartbeat_at = run.finished_at
                session.add(run)
                session.commit()
                return

            start_heartbeat()
            _touch_codex_diary_run(session, run, status="running", stage="splitting", stage_label="按主题和时长拆分节点")
            blocks = _build_codex_diary_blocks(source, user_id=user_id, session=session)

            draft_runtime = resolve_ai_app_runtime_config(
                session=session,
                current_user=user,
                app_id=AI_APP_CODEX_DIARY,
            )
            _touch_codex_diary_run(session, run, status="running", stage="drafting", stage_label="调用 AI 生成日记草案")
            blocks = _draft_codex_diary_blocks_in_batches(source, blocks, current_user=user, session=session, run=run)

            _touch_codex_diary_run(session, run, status="running", stage="writing", stage_label="写入星图笔记")
            created_note_ids: list[str] = list(run.created_note_ids or [])
            draft_fallback_events = list((run.result_json or {}).get("draft_fallback_events") or [])
            draft_generator = (
                "deepseek-json-v1+deterministic-fallback-v1"
                if draft_fallback_events
                else "deepseek-json-v1"
            )
            run.result_json = {
                "prompt_version": CODEX_DIARY_PROMPT_VERSION,
                "draft_generator": draft_generator,
                "draft_provider": str(draft_runtime.get("provider") or ""),
                "draft_model": str(draft_runtime.get("model") or ""),
                "draft_fallback_events": draft_fallback_events,
                "source": _build_codex_diary_source_result(run, source),
                "blocks": _build_codex_diary_blocks_result(blocks),
            }
            session.add(run)
            session.commit()

            total_blocks = len(blocks)
            for index, block in enumerate(blocks, start=1):
                note = _create_codex_diary_note(session, current_user=user, run=run, block=block)
                note_id = _note_public_id(note)
                created_note_ids = [*created_note_ids, note_id]
                now = time.time()
                run.created_note_ids = created_note_ids
                run.created_note_count = len(created_note_ids)
                run.stage_label = f"写入星图笔记 {index}/{total_blocks}"
                run.updated_at = now
                run.heartbeat_at = now
                session.add(run)
                session.commit()
                session.refresh(note)

            run.status = "completed"
            run.stage = "completed"
            run.stage_label = f"已创建 {run.created_note_count} 个节点"
            run.finished_at = time.time()
            run.updated_at = run.finished_at
            run.heartbeat_at = run.finished_at
            session.add(run)
            session.commit()
        except Exception as exc:
            _mark_codex_diary_import_run_failed(
                db_bind,
                run_id=run_id,
                error_message=str(getattr(exc, "detail", None) or exc),
                session=session,
            )
        finally:
            stop_heartbeat()


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
    elif source not in {"builtin", "custom", "legacy", "import"}:
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


def _fallback_note_ai_default_category_item() -> dict[str, Any]:
    for item in _default_note_type_palette_items():
        if item["key"] == NOTE_CATEGORY_DEFAULT:
            return item
    return {
        "key": NOTE_CATEGORY_DEFAULT,
        "label": "综合",
        "color": "#606266",
        "order": 0,
        "builtin": True,
        "source": "builtin",
    }


def _filter_note_auto_classification_category_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("key") or "").strip()
        label = str(item.get("label") or "").strip()
        if not key or key in seen or is_note_auto_classification_blocked_category(key, label):
            continue
        seen.add(key)
        filtered.append(item)

    if NOTE_CATEGORY_DEFAULT not in seen:
        default_item = _fallback_note_ai_default_category_item()
        filtered.insert(0, default_item)
    return filtered


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
        if _is_imported_script_category_key(key):
            continue
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


def _is_imported_script_category_key(value: Any) -> bool:
    return str(value or "").strip().startswith("import_")


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
    base_items = [
        item
        for item in (stored_items or _default_note_type_palette_items())
        if not _is_imported_script_category_key(item.get("key"))
    ]
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
    return note_to_list_response_dict(note, current_user)


def _serialize_note_read(
    note: NoteNode,
    current_user: User,
    **extra_fields: Any,
) -> dict[str, Any]:
    return note_to_response_dict(note, current_user, **extra_fields)


def _serialize_note_edge(
    edge: NoteEdge,
    *,
    note_public_ids: dict[str, int | str] | None = None,
    session: Session | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    public_ids = note_public_ids or {}

    def resolve(note_id: str) -> int | str:
        normalized = str(note_id or "")
        if normalized in public_ids:
            return public_ids[normalized]
        if session is not None:
            ref_conditions = [
                NoteNode.id == normalized,
                NoteNode.legacy_id == normalized,
            ]
            if normalized.isdecimal():
                ref_conditions.append(NoteNode.numeric_id == int(normalized))
            query = select(NoteNode).where(or_(*ref_conditions))
            if user_id is not None:
                query = query.where(NoteNode.user_id == user_id)
            note = session.exec(query).first()
            if note is not None:
                return note_public_api_id(note)
        return normalized

    return {
        "id": str(edge.id),
        "user_id": int(edge.user_id),
        "source_id": resolve(edge.source_id),
        "target_id": resolve(edge.target_id),
        "label": edge.label,
        "created_at": float(edge.created_at or 0),
    }


def _serialize_note_edges_for_nodes(edges: list[NoteEdge], notes: list[NoteNode]) -> list[dict[str, Any]]:
    note_public_ids: dict[str, int | str] = {}
    for note in notes:
        public_id = note_public_api_id(note)
        for ref in note_ref_aliases(note):
            note_public_ids[ref] = public_id
    return [_serialize_note_edge(edge, note_public_ids=note_public_ids) for edge in edges]


def _note_mapping_public_api_id(note: Any) -> int | str:
    numeric_id = int(note.get("numeric_id") or 0)
    if numeric_id > 0:
        return numeric_id
    return str(note.get("id") or "")


def _note_mapping_ref_aliases(note: Any) -> set[str]:
    refs = {
        str(note.get("id") or "").strip(),
        str(note.get("legacy_id") or "").strip(),
        str(_note_mapping_public_api_id(note)).strip(),
    }
    return {ref for ref in refs if ref}


def _note_mapping_edge_ref_set(notes: Iterable[Any]) -> set[str]:
    refs: set[str] = set()
    for note in notes:
        refs.update(_note_mapping_ref_aliases(note))
    return refs


def _serialize_note_edges_for_note_mappings(edges: list[NoteEdge], notes: Iterable[Any]) -> list[dict[str, Any]]:
    note_public_ids: dict[str, int | str] = {}
    for note in notes:
        public_id = _note_mapping_public_api_id(note)
        for ref in _note_mapping_ref_aliases(note):
            note_public_ids[ref] = public_id
    return [_serialize_note_edge(edge, note_public_ids=note_public_ids) for edge in edges]


def _load_edges_between_refs(session: Session, user_id: int, refs: set[str]) -> list[NoteEdge]:
    if not refs:
        return []
    candidate_edges = session.exec(
        select(NoteEdge).where(
            NoteEdge.user_id == user_id,
            NoteEdge.source_id.in_(refs),
        )
    ).all()
    return [edge for edge in candidate_edges if edge.target_id in refs]


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
) -> tuple[str, str | None, str | None, str | None]:
    try:
        runtime = resolve_ai_app_runtime_config(
            session=session,
            current_user=current_user,
            app_id=AI_APP_NOTE_TAXONOMY,
            provider=payload.provider,
            base_url=payload.base_url,
            api_key=payload.api_key,
            model=payload.model,
        )
    except AiAppConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return str(runtime["provider"]), runtime["base_url"], runtime["api_key"], runtime["model"]


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
        if primary_category not in category_labels:
            continue
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


def _is_numeric_note_ref(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and text.isdecimal()


def _note_public_id(note: NoteNode) -> str:
    return note_public_id(note)


def _note_resource_update_room(note_ref: str) -> str:
    return f"resource:note:{note_ref}"


def _broadcast_note_resource_update(note: NoteNode) -> None:
    public_ref = _note_public_id(note)
    message = {
        "type": "resource-updated",
        "resource_type": "note",
        "resource_id": public_ref,
        "version": int(note.version or 1),
        "updated_at": float(note.updated_at or time.time()),
        "updated_by_user_id": note.user_id,
    }
    try:
        anyio.from_thread.run(ws_manager.broadcast, _note_resource_update_room(public_ref), message)
    except RuntimeError:
        pass


def _note_edge_ref_set(notes: Iterable[NoteNode]) -> set[str]:
    refs: set[str] = set()
    for note in notes:
        refs.update(note_ref_aliases(note))
    return refs


def _active_note_condition():
    return or_(NoteNode.deleted_at.is_(None), NoteNode.deleted_at <= 0)


def _deleted_note_condition():
    return NoteNode.deleted_at > 0


def _resolve_note_legacy_id(note_ref: str, current_user: User, session: Session) -> str | None:
    note = _get_accessible_note_by_any_ref(note_ref, current_user, session)
    if note is None:
        return None
    return str(note.id)


def _resolve_note_public_ref(note_ref: str, current_user: User, session: Session) -> str | None:
    note = _get_accessible_note(note_ref, current_user, session)
    if note is None:
        return None
    return _note_public_id(note)


def _get_accessible_note_by_any_ref(
    note_id: str,
    current_user: User,
    session: Session,
    *,
    include_deleted: bool = False,
) -> NoteNode | None:
    normalized_id = str(note_id or "").strip()
    if _is_numeric_note_ref(normalized_id):
        query = select(NoteNode).where(NoteNode.numeric_id == int(normalized_id))
    else:
        query = select(NoteNode).where(or_(NoteNode.id == normalized_id, NoteNode.legacy_id == normalized_id))
    if not current_user.is_superuser:
        query = query.where(NoteNode.user_id == current_user.id)
    if not include_deleted:
        query = query.where(_active_note_condition())
    return session.exec(query).first()


def _get_accessible_note(
    note_id: str,
    current_user: User,
    session: Session,
    *,
    include_deleted: bool = False,
) -> NoteNode | None:
    normalized_id = str(note_id or "").strip()
    if not _is_numeric_note_ref(normalized_id):
        return None
    query = select(NoteNode).where(NoteNode.numeric_id == int(normalized_id))
    if not current_user.is_superuser:
        query = query.where(NoteNode.user_id == current_user.id)
    if not include_deleted:
        query = query.where(_active_note_condition())
    return session.exec(query).first()


def _get_filtered_edge_pool(
    seed_note_id: str,
    mode: str,
    user_id: int,
    session: Session
) -> List[NoteEdge]:
    edges = session.exec(select(NoteEdge).where(NoteEdge.user_id == user_id)).all()
    if mode == "satellite":
        seed_refs = {str(seed_note_id)}
        edges = [edge for edge in edges if str(edge.target_id) not in seed_refs]
    return edges


def _get_component_note_ids(
    seed_note_id: str,
    mode: str,
    user_id: int,
    session: Session
) -> Tuple[set[str], List[NoteEdge]]:
    normalized_seed_id = str(seed_note_id or "").strip()
    if not _is_numeric_note_ref(normalized_seed_id):
        raise HTTPException(status_code=404, detail="Note not found")
    start_note = session.exec(
        select(NoteNode)
        .where(NoteNode.numeric_id == int(normalized_seed_id), NoteNode.user_id == user_id)
        .where(_active_note_condition())
    ).first()
    if not start_note:
        raise HTTPException(status_code=404, detail="Note not found")

    seed_node_id = _note_public_id(start_note)
    all_notes = session.exec(
        select(NoteNode).where(NoteNode.user_id == user_id).where(_active_note_condition())
    ).all()
    ref_to_note = build_note_ref_map(all_notes)
    edges = session.exec(select(NoteEdge).where(NoteEdge.user_id == user_id)).all()
    adj: dict[str, list[str]] = {}
    component_edges: list[NoteEdge] = []
    for edge in edges:
        source_note = ref_to_note.get(str(edge.source_id))
        target_note = ref_to_note.get(str(edge.target_id))
        if source_note is None or target_note is None:
            continue
        u, v = _note_public_id(source_note), _note_public_id(target_note)
        if mode == "satellite" and v == seed_node_id:
            continue
        component_edges.append(edge)
        adj.setdefault(u, []).append(v)
        adj.setdefault(v, []).append(u)

    visited = {seed_node_id}
    queue = [seed_node_id]
    while queue:
        curr = queue.pop(0)
        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)

    return visited, component_edges


def _get_rule_value(note: NoteNode, field: str):
    if field == "id":
        return _note_public_id(note)
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
    if rule.field == "id":
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
    notes = session.exec(select(NoteNode).where(NoteNode.user_id == user_id).where(_active_note_condition())).all()
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


def _normalize_note_program_public_ids(request: NoteProgramRequest, current_user: User, session: Session) -> None:
    def normalize_ids(values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values or []:
            if not _is_numeric_note_ref(str(value)):
                raise HTTPException(status_code=404, detail="Note not found")
            public_ref = _resolve_note_public_ref(str(value), current_user, session)
            if public_ref:
                normalized.append(public_ref)
        return normalized

    request.executor.seed_ids = normalize_ids(request.executor.seed_ids)
    for channel in (request.program.select, request.program.expand):
        for rule in channel.rules:
            if rule.matcher.kind == "id":
                rule.matcher.ids = normalize_ids(rule.matcher.ids)


def _program_matcher_to_filter_rule(matcher) -> Optional[NoteFilterRule]:
    if matcher.kind != "field" or not matcher.field:
        return None

    op = matcher.op or "eq"
    if matcher.field in {"start_at", "updated_at"} and (matcher.time_value is not None or matcher.time_values):
        if op == "between":
            values = [
                resolved
                for resolved in (_resolve_time_point_expr(expr) for expr in (matcher.time_values or []))
                if resolved is not None
            ]
            if len(values) < 2:
                return None
            return NoteFilterRule(field=matcher.field, op=op, values=values[:2])

        value = _resolve_time_point_expr(matcher.time_value)
        if value is None:
            return None
        return NoteFilterRule(field=matcher.field, op=op, value=value)

    return NoteFilterRule(
        field=matcher.field,
        op=op,
        value=matcher.value,
        values=list(matcher.values or []),
    )


def _try_execute_note_program_sql_scan(
    request: NoteProgramRequest,
    *,
    current_user: User,
    user_id: int,
    session: Session,
) -> Optional[dict[str, Any]]:
    if request.executor.kind != "scan":
        return None
    if request.program.expand.default or request.program.expand.rules:
        return None
    if request.program.select.default:
        return None

    select_rules = request.program.select.rules
    if not select_rules:
        return None

    first_rule = select_rules[0]
    first_filter = _program_matcher_to_filter_rule(first_rule.matcher)
    first_is_include_all = first_rule.action == "include" and first_rule.matcher.kind == "all"
    first_is_supported_range = (
        first_rule.action == "include"
        and first_filter is not None
        and first_filter.field in {"start_at", "updated_at"}
        and first_filter.op == "between"
    )
    if not first_is_include_all and not first_is_supported_range:
        return None

    tail_rules = select_rules[1:]
    if any(rule.action != "exclude" for rule in tail_rules):
        return None

    query = select(NoteNode).where(NoteNode.user_id == user_id).where(_active_note_condition())
    if first_filter is not None:
        query, handled = _apply_sql_rule(query, first_filter)
        if not handled:
            return None

    exclude_rules: list[NoteFilterRule] = []
    for rule in tail_rules:
        filter_rule = _program_matcher_to_filter_rule(rule.matcher)
        if filter_rule is None:
            return None
        exclude_rules.append(filter_rule)

    if not exclude_rules:
        total_nodes = session.exec(select(func.count()).select_from(query.order_by(None).subquery())).one()
        sort_field = request.result.order_by if request.result.order_by in ALLOWED_ORDER_FIELDS else "updated_at"
        sort_column = getattr(NoteNode, sort_field)
        order_expr = sort_column.desc() if request.result.order_desc else sort_column.asc()
        visible_rows = session.execute(
            query
            .with_only_columns(*NOTE_LIST_LOAD_COLUMNS)
            .order_by(order_expr)
            .offset(request.result.skip)
            .limit(request.result.limit)
        ).all()
        visible_mappings = [row._mapping for row in visible_rows]
        if request.result.include_edges:
            visible_refs = _note_mapping_edge_ref_set(visible_mappings)
            visible_edges = _load_edges_between_refs(session, user_id, visible_refs)
        else:
            visible_edges = []
        return {
            "nodes": [note_list_mapping_to_response_dict(note, current_user) for note in visible_mappings],
            "edges": _serialize_note_edges_for_note_mappings(visible_edges, visible_mappings),
            "total_nodes": int(total_nodes or 0),
            "total_edges": len(visible_edges),
        }

    if all(rule.field in NOTE_LIST_LOAD_FIELD_NAMES for rule in exclude_rules):
        query = query.options(load_only(*NOTE_LIST_LOAD_COLUMNS))

    candidate_notes = session.exec(query).all()
    filtered_notes = [
        note
        for note in candidate_notes
        if not any(_matches_rule(note, rule) for rule in exclude_rules)
    ]
    sorted_notes = _sort_notes(filtered_notes, request.result.order_by, request.result.order_desc)
    total_nodes = len(sorted_notes)
    visible_nodes = sorted_notes[request.result.skip: request.result.skip + request.result.limit]
    if request.result.include_edges:
        visible_refs = _note_edge_ref_set(visible_nodes)
        visible_edges = _load_edges_between_refs(session, user_id, visible_refs)
    else:
        visible_edges = []

    return {
        "nodes": [_serialize_note_list(note, current_user) for note in visible_nodes],
        "edges": _serialize_note_edges_for_nodes(visible_edges, visible_nodes),
        "total_nodes": total_nodes,
        "total_edges": len(visible_edges),
    }


def _get_note_mapping_custom_field(note: Any, key: str) -> Any:
    fields = note.get("custom_fields") or []
    if not isinstance(fields, list):
        return None
    for item in fields:
        if isinstance(item, list) and len(item) >= 3 and item[0] == key:
            return item[2]
        if isinstance(item, dict) and item.get("key") == key:
            return item.get("value")
    return None


def _get_note_mapping_rule_value(note: Any, field: str) -> Any:
    if field.startswith("custom_fields."):
        return _get_note_mapping_custom_field(note, field.split(".", 1)[1])
    value = note.get(field)
    if field in {"lifecycle_stage", "node_status"}:
        return normalize_lifecycle_stage(value, default=NOTE_LIFECYCLE_STAGE_DEFAULT)
    return value


def _matches_mapping_rule(note: Any, rule: NoteFilterRule) -> bool:
    field_value = _get_note_mapping_rule_value(note, rule.field)
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
        return field_value is not None and str(rule_value or "").lower() in str(field_value).lower()
    if op == "not_contains":
        return field_value is None or str(rule_value or "").lower() not in str(field_value).lower()
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
        return rule_values[0] <= field_value <= rule_values[1]

    return True


def _score_note_calendar_mapping(note: Any, *, volume: bool) -> float:
    weight = float(note.get("weight") or 0)
    stage = str(note.get("lifecycle_stage") or note.get("node_status") or "").lower()
    stage_score = 1.0 if stage in {"done", "predone"} else 0.8 if stage == "doing" else 0.4 if stage == "todo" else 0.0
    year_score = weight * 10 + stage_score
    if not volume:
        return year_score

    source_kind = str(_get_note_mapping_custom_field(note, "source_kind") or "")
    source_boost = 0
    if "chapter" in source_kind or "section" in source_kind:
        source_boost = 80
    elif "week" in source_kind:
        source_boost = 50
    elif "child" in source_kind:
        source_boost = 35
    elif "day_group" in source_kind:
        source_boost = 16
    form_boost = 8 if note.get("note_form") == "document" else 0
    return year_score + weight * 12 + source_boost + form_boost


def _is_preferred_calendar_mapping(note: Any) -> bool:
    return float(note.get("weight") or 0) > 0 or bool(_get_note_mapping_custom_field(note, "source_kind"))


def _insert_calendar_summary_candidate(
    candidates: list[tuple[float, float, Any]],
    item: tuple[float, float, Any],
    *,
    limit: int,
) -> None:
    score, start_at, _note = item
    insert_at = next(
        (
            index for index, (existing_score, existing_start_at, _existing_note) in enumerate(candidates)
            if score > existing_score or (score == existing_score and start_at < existing_start_at)
        ),
        -1,
    )
    if insert_at >= 0:
        candidates.insert(insert_at, item)
        if len(candidates) > limit:
            candidates.pop()
    elif len(candidates) < limit:
        candidates.append(item)


def _try_execute_note_calendar_summary_sql_scan(
    request: NoteCalendarSummaryRequest,
    *,
    current_user: User,
    user_id: int,
    session: Session,
) -> Optional[dict[str, Any]]:
    query_request = request.query
    if query_request.executor.kind != "scan" or query_request.result.include_edges:
        return None
    if query_request.program.expand.default or query_request.program.expand.rules:
        return None
    if query_request.program.select.default:
        return None

    select_rules = query_request.program.select.rules
    if not select_rules:
        return None
    first_rule = select_rules[0]
    first_filter = _program_matcher_to_filter_rule(first_rule.matcher)
    if first_rule.action != "include" or first_filter is None:
        return None
    if first_filter.field != "start_at" or first_filter.op != "between":
        return None
    tail_rules = select_rules[1:]
    tail_filters: list[NoteFilterRule] = []
    python_exclude_filters: list[NoteFilterRule] = []
    for rule in tail_rules:
        filter_rule = _program_matcher_to_filter_rule(rule.matcher)
        if filter_rule is None:
            return None
        if rule.action == "filter":
            tail_filters.append(filter_rule)
        elif (
            rule.action == "exclude"
            and filter_rule.field.startswith("custom_fields.")
            and filter_rule.op in {"eq", "neq", "in", "not_in", "contains", "not_contains", "regex_search"}
        ):
            python_exclude_filters.append(filter_rule)
        else:
            return None

    buckets = [
        bucket for bucket in request.buckets
        if bucket.key and bucket.end_at > bucket.start_at
    ]
    if not buckets:
        return {"buckets": [], "nodes": [], "total_nodes": 0}
    sorted_buckets = sorted(buckets, key=lambda bucket: (bucket.start_at, bucket.end_at))

    query = select(NoteNode).where(NoteNode.user_id == user_id).where(_active_note_condition())
    query, handled = _apply_sql_rule(query, first_filter)
    if not handled:
        return None
    for filter_rule in tail_filters:
        query, handled = _apply_sql_rule(query, filter_rule)
        if not handled:
            return None

    rows = session.execute(
        query
        .with_only_columns(*NOTE_CALENDAR_SCORE_COLUMNS)
        .order_by(NoteNode.start_at.asc())
    ).all()

    bucket_states: dict[str, dict[str, Any]] = {
        bucket.key: {
            "request": bucket,
            "total_nodes": 0,
            "ranked": [],
            "preferred": [],
            "documents": [],
        }
        for bucket in buckets
    }
    total_nodes = 0
    bucket_index = 0

    for row in rows:
        note = row._mapping
        if any(_matches_mapping_rule(note, rule) for rule in python_exclude_filters):
            continue
        ts = float(note.get("start_at") or 0)
        while bucket_index < len(sorted_buckets) and sorted_buckets[bucket_index].end_at < ts:
            bucket_index += 1
        if bucket_index >= len(sorted_buckets):
            break
        current_bucket = sorted_buckets[bucket_index]
        matched_bucket = current_bucket if current_bucket.start_at <= ts <= current_bucket.end_at else None
        if matched_bucket is None:
            continue
        state = bucket_states[matched_bucket.key]
        state["total_nodes"] += 1
        total_nodes += 1
        limit = max(1, min(200, int(matched_bucket.limit or 100)))
        score = _score_note_calendar_mapping(note, volume=matched_bucket.mode in {"volume", "era"})
        item = (score, ts, note)
        _insert_calendar_summary_candidate(state["ranked"], item, limit=limit)
        if _is_preferred_calendar_mapping(note):
            _insert_calendar_summary_candidate(state["preferred"], item, limit=limit)
        if note.get("note_form") == "document":
            _insert_calendar_summary_candidate(state["documents"], item, limit=limit)

    candidate_numeric_ids: list[int] = []
    candidate_legacy_ids: list[str] = []
    candidate_seen: set[tuple[str, Any]] = set()
    for state in bucket_states.values():
        for source in (state["ranked"], state["preferred"], state["documents"]):
            for _score, _ts, note in source:
                numeric_id = int(note.get("numeric_id") or 0)
                candidate_key: tuple[str, Any]
                if numeric_id > 0:
                    candidate_key = ("numeric", numeric_id)
                    if candidate_key not in candidate_seen:
                        candidate_numeric_ids.append(numeric_id)
                else:
                    legacy_id = str(note.get("id") or "")
                    candidate_key = ("id", legacy_id)
                    if legacy_id and candidate_key not in candidate_seen:
                        candidate_legacy_ids.append(legacy_id)
                candidate_seen.add(candidate_key)

    full_note_by_key: dict[tuple[str, Any], Any] = {}
    if candidate_numeric_ids or candidate_legacy_ids:
        candidate_query = select(NoteNode).where(NoteNode.user_id == user_id).where(_active_note_condition())
        lookup_conditions = []
        if candidate_numeric_ids:
            lookup_conditions.append(NoteNode.numeric_id.in_(candidate_numeric_ids))
        if candidate_legacy_ids:
            lookup_conditions.append(NoteNode.id.in_(candidate_legacy_ids))
        candidate_query = candidate_query.where(or_(*lookup_conditions))
        full_rows = session.execute(candidate_query.with_only_columns(*NOTE_LIST_LOAD_COLUMNS)).all()
        for row in full_rows:
            note = row._mapping
            numeric_id = int(note.get("numeric_id") or 0)
            if numeric_id > 0:
                full_note_by_key[("numeric", numeric_id)] = note
            full_note_by_key[("id", str(note.get("id") or ""))] = note

    response_buckets: list[dict[str, Any]] = []
    nodes_by_id: dict[Any, dict[str, Any]] = {}
    for bucket in buckets:
        state = bucket_states[bucket.key]
        source = state["ranked"]
        if bucket.mode == "volume" and state["preferred"]:
            source = state["preferred"]
        elif bucket.mode == "era" and state["documents"]:
            source = state["documents"]
        sorted_items = sorted(source, key=lambda item: item[1])
        full_notes = []
        for _score, _ts, note in sorted_items:
            numeric_id = int(note.get("numeric_id") or 0)
            full_note = full_note_by_key.get(("numeric", numeric_id)) if numeric_id > 0 else None
            if full_note is None:
                full_note = full_note_by_key.get(("id", str(note.get("id") or "")))
            if full_note is not None:
                full_notes.append(full_note)
        nodes = [note_list_mapping_to_response_dict(note, current_user) for note in full_notes]
        for node in nodes:
            nodes_by_id[node["id"]] = node
        response_buckets.append({
            "key": bucket.key,
            "total_nodes": int(state["total_nodes"]),
            "nodes": nodes,
        })

    return {
        "buckets": response_buckets,
        "nodes": list(nodes_by_id.values()),
        "total_nodes": int(total_nodes),
    }


def _execute_note_program(
    request: NoteProgramRequest,
    *,
    current_user: User,
    user_id: int,
    session: Session,
):
    need_edges = request.result.include_edges or request.executor.kind == "component"
    _normalize_note_program_public_ids(request, current_user, session)
    sql_scan_result = _try_execute_note_program_sql_scan(
        request,
        current_user=current_user,
        user_id=user_id,
        session=session,
    )
    if sql_scan_result is not None:
        return sql_scan_result

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
    visible_ids = _note_edge_ref_set(visible_nodes)

    if request.result.include_edges:
        visible_edges = [
            edge for edge in walk_result.edges
            if edge.source_id in visible_ids and edge.target_id in visible_ids
        ]
    else:
        visible_edges = []

    return {
        "nodes": [_serialize_note_list(note, current_user) for note in visible_nodes],
        "edges": _serialize_note_edges_for_nodes(visible_edges, visible_nodes),
        "total_nodes": total_nodes,
        "total_edges": len(visible_edges),
    }


def _calendar_year_month_memos_setting_key(user_id: int) -> str:
    return f"note.calendar.year_month_memos.user.{int(user_id)}"


def _normalize_calendar_year_month_memos(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    raw = value.get("memos") if isinstance(value, dict) and isinstance(value.get("memos"), dict) else value
    if not isinstance(raw, dict):
        return {}

    memos: dict[str, str] = {}
    for key, text in raw.items():
        memo_key = str(key or "").strip()
        if not CALENDAR_YEAR_MONTH_MEMO_KEY_RE.fullmatch(memo_key):
            continue
        normalized_text = str(text or "").strip()
        if not normalized_text:
            continue
        memos[memo_key] = normalized_text[:200]
    return dict(sorted(memos.items()))


def _normalize_calendar_year_titles(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    raw = value.get("year_titles") if isinstance(value, dict) and isinstance(value.get("year_titles"), dict) else value
    if not isinstance(raw, dict):
        return {}

    titles: dict[str, str] = {}
    for key, text in raw.items():
        year_key = str(key or "").strip()
        if not CALENDAR_YEAR_TITLE_KEY_RE.fullmatch(year_key):
            continue
        normalized_text = str(text or "").strip()
        if not normalized_text:
            continue
        titles[year_key] = normalized_text[:80]
    return dict(sorted(titles.items()))


# --- Notes ---

@router.get("/", response_model=List[NoteListRead])
def read_notes(
    skip: int = 0,
    limit: int = 128, # Default to 128 as requested
    created_start: Optional[float] = Query(None, description="Filter by start_at >= start"),
    created_end: Optional[float] = Query(None, description="Filter by start_at <= end"),
    updated_start: Optional[float] = Query(None, description="Filter by updated_at >= start"),
    updated_end: Optional[float] = Query(None, description="Filter by updated_at <= end"),
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    """
    Retrieve notes for the current user.
    Supports filtering by start_at (mapped from created_*) and update time range.
    Default limit is 128.
    """
    query = select(NoteNode).where(NoteNode.user_id == current_user.id).where(_active_note_condition())
    
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


@router.get("/trash", response_model=List[NoteListRead])
def read_deleted_notes(
    skip: int = 0,
    limit: int = 200,
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session),
):
    query = (
        select(NoteNode)
        .where(NoteNode.user_id == current_user.id)
        .where(_deleted_note_condition())
        .order_by(NoteNode.deleted_at.desc(), NoteNode.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    notes = session.exec(query).all()
    return [_serialize_note_list(note, current_user) for note in notes]


@router.post("/query", response_model=NoteQueryResponse)
def query_notes(
    request: NoteQueryRequest,
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    """
    Query a reusable note set using a generic scope + rules model.
    """
    scope = request.scope
    query = select(NoteNode).where(NoteNode.user_id == current_user.id).where(_active_note_condition())

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
        numeric_note_ids = [int(note_id) for note_id in note_ids if str(note_id).isdecimal()]
        query = query.where(NoteNode.numeric_id.in_(numeric_note_ids))

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
    visible_ids = _note_edge_ref_set(visible_notes)

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
        "edges": _serialize_note_edges_for_nodes(visible_edges, visible_notes),
        "total_nodes": total_nodes,
        "total_edges": len(visible_edges)
    }


@router.post("/query-program", response_model=NoteProgramResponse)
def query_note_program(
    request: NoteProgramRequest,
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    """
    Execute a walker-style filtering program over the current user's note graph.
    """
    return _execute_note_program(request, current_user=current_user, user_id=current_user.id, session=session)


@router.post("/query-program/calendar-summary", response_model=NoteCalendarSummaryResponse)
def query_note_calendar_summary(
    request: NoteCalendarSummaryRequest,
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    """
    Execute the calendar scan as bucket summaries so large year/volume/era views
    do not need every matching node serialized and sent to the browser.
    """
    _normalize_note_program_public_ids(request.query, current_user, session)
    result = _try_execute_note_calendar_summary_sql_scan(
        request,
        current_user=current_user,
        user_id=current_user.id,
        session=session,
    )
    if result is None:
        raise HTTPException(status_code=400, detail="Unsupported calendar summary query")
    return result


@router.post("/batch-update", response_model=NoteBatchUpdateResponse)
def batch_update_notes(
    request: NoteBatchUpdateRequest,
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    """
    Batch update notes for the current user.
    """
    requested_note_ids = [str(note_id).strip() for note_id in request.ids if str(note_id).strip()]
    if not requested_note_ids or any(not _is_numeric_note_ref(note_id) for note_id in requested_note_ids):
        raise HTTPException(status_code=400, detail="ids is required")
    numeric_ids = [int(note_id) for note_id in requested_note_ids]

    patch = request.patch.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="patch is required")
    if patch.get("weight") is not None and patch.get("weight_delta") is not None:
        raise HTTPException(status_code=400, detail="weight and weight_delta cannot be used together")

    notes = session.exec(
        select(NoteNode).where(
            NoteNode.user_id == current_user.id,
            NoteNode.numeric_id.in_(numeric_ids),
            _active_note_condition(),
        )
    ).all()

    note_by_id = {int(note.numeric_id): note for note in notes if note.numeric_id is not None}
    ordered_notes = [note_by_id[note_id] for note_id in numeric_ids if note_id in note_by_id]

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


@router.get("/calendar/year-month-memos", response_model=CalendarYearMonthMemosRead)
def get_calendar_year_month_memos(
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    setting_key = _calendar_year_month_memos_setting_key(current_user.id)
    row = session.get(AppSetting, setting_key)
    return {
        "memos": _normalize_calendar_year_month_memos(row.value if row else None),
        "year_titles": _normalize_calendar_year_titles(row.value if row else None),
        "updated_at": row.updated_at if row else None,
    }


@router.put("/calendar/year-month-memos", response_model=CalendarYearMonthMemosRead)
def update_calendar_year_month_memos(
    request: CalendarYearMonthMemosUpdate,
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    setting_key = _calendar_year_month_memos_setting_key(current_user.id)
    now = time.time()
    row = session.get(AppSetting, setting_key)
    if row is None:
        row = AppSetting(key=setting_key)
    existing_value = row.value if row else None
    memos = (
        _normalize_calendar_year_month_memos(request.memos)
        if request.memos is not None
        else _normalize_calendar_year_month_memos(existing_value)
    )
    year_titles = (
        _normalize_calendar_year_titles(request.year_titles)
        if request.year_titles is not None
        else _normalize_calendar_year_titles(existing_value)
    )
    row.value = {
        "version": CALENDAR_YEAR_MONTH_MEMOS_SETTING_VERSION,
        "memos": memos,
        "year_titles": year_titles,
    }
    row.updated_at = now
    session.add(row)
    session.commit()
    return {"memos": memos, "year_titles": year_titles, "updated_at": now}


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
    category_items = _filter_note_auto_classification_category_items(palette_items)

    resolved_provider, resolved_base_url, resolved_api_key, resolved_model = _resolve_note_ai_runtime_config(
        payload,
        current_user=current_user,
        session=session,
    )
    extra_providers = list_user_ai_chat_custom_provider_configs(session, current_user.id)
    system_prompt, user_prompt = _build_note_ai_prompt(note, palette_items=category_items, session=session)

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
        for item in category_items
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
        for item in category_items
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
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    return _build_note_type_palette_response(current_user.id, session)


@router.get("/category-palette/{category_key}/can-delete")
@router.get("/type-palette/{category_key}/can-delete")
def can_delete_note_category_palette_item(
    category_key: str,
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    return {"can_delete": not _is_note_type_in_use(current_user.id, category_key, session)}


@router.put("/category-palette", response_model=NoteCategoryPaletteResponse)
@router.put("/type-palette", response_model=NoteCategoryPaletteResponse)
def update_note_category_palette(
    request: NoteCategoryPaletteUpdateRequest,
    current_user: User = Depends(get_current_active_or_guest_notes_user),
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
    current_user: User = Depends(get_current_active_or_guest_notes_user),
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
    notes = session.exec(
        select(NoteNode).where(NoteNode.user_id == current_user.id).where(_active_note_condition())
    ).all()
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


def _build_codex_diary_duplicate_http_error(
    duplicate_note_ids: list[str],
    *,
    user_id: int,
    session: Session,
) -> HTTPException:
    public_duplicate_note_ids = _codex_diary_public_note_numeric_ids(session, user_id, duplicate_note_ids)
    return HTTPException(
        status_code=409,
        detail={
            "message": "该日期已导入过 Codex 总结日记，继续会重复生成一批新节点。",
            "duplicate_note_ids": public_duplicate_note_ids,
            "duplicate_count": len(public_duplicate_note_ids),
        },
    )


def _create_codex_diary_import_run_record(
    session: Session,
    *,
    current_user: User,
    diary_date_text: str,
    entry_ids: List[str] | None = None,
    confirm_duplicate: bool = False,
    skip_duplicate: bool = False,
    skip_active: bool = False,
) -> tuple[CodexDiaryImportRun, list[dict[str, Any]], dict[str, str], bool]:
    diary_date, day_start_at, day_end_at = _parse_codex_diary_date(diary_date_text)
    entries = _get_codex_diary_entries(session, current_user, entry_ids)
    if not entries:
        raise HTTPException(status_code=400, detail="没有可用于导入的设备")

    entry_specs = _snapshot_codex_diary_entries(entries)
    scope_key, root_identity = _build_codex_diary_scope_identity(entry_specs)
    active_run = session.exec(
        select(CodexDiaryImportRun)
        .where(CodexDiaryImportRun.user_id == current_user.id)
        .where(CodexDiaryImportRun.diary_date == diary_date)
        .where(CodexDiaryImportRun.scope_key == scope_key)
        .where(CodexDiaryImportRun.status.in_(["pending", "running"]))
        .order_by(CodexDiaryImportRun.created_at.desc())
    ).first()
    if active_run is not None:
        if skip_active:
            return active_run, entry_specs, root_identity, False
        raise HTTPException(
            status_code=409,
            detail={
                "code": "active_import",
                "message": "该日期的 Codex 总结日记仍在导入中，请等待当前任务完成后再试。",
                "run_id": active_run.id,
                "stage": active_run.stage,
                "stage_label": active_run.stage_label,
            },
        )
    duplicate_note_ids = _find_existing_codex_diary_notes(
        session,
        user_id=current_user.id,
        diary_date=diary_date,
        scope_key=scope_key,
        day_start_at=day_start_at,
        day_end_at=day_end_at,
    )

    now = time.time()
    if duplicate_note_ids and not confirm_duplicate:
        if not skip_duplicate:
            raise _build_codex_diary_duplicate_http_error(
                duplicate_note_ids,
                user_id=current_user.id,
                session=session,
            )
        public_duplicate_note_ids = _codex_diary_public_note_ids(session, current_user.id, duplicate_note_ids)
        run = CodexDiaryImportRun(
            user_id=current_user.id,
            diary_date=diary_date,
            scope_key=scope_key,
            entry_ids=[str(entry["entry_id"]) for entry in entry_specs],
            entry_snapshot=entry_specs,
            confirm_duplicate=False,
            duplicate_note_ids=public_duplicate_note_ids,
            status="skipped",
            stage="duplicate",
            stage_label="该日期已导入过，自动任务已跳过",
            heartbeat_at=now,
            created_at=now,
            finished_at=now,
            updated_at=now,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run, entry_specs, root_identity, False

    public_duplicate_note_ids = _codex_diary_public_note_ids(session, current_user.id, duplicate_note_ids)
    run = CodexDiaryImportRun(
        user_id=current_user.id,
        diary_date=diary_date,
        scope_key=scope_key,
        entry_ids=[str(entry["entry_id"]) for entry in entry_specs],
        entry_snapshot=entry_specs,
        confirm_duplicate=bool(confirm_duplicate),
        duplicate_note_ids=public_duplicate_note_ids,
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
    return run, entry_specs, root_identity, True


@router.post("/codex-diary/import-runs", response_model=CodexDiaryImportRunRead)
def create_codex_diary_import_run(
    req: CodexDiaryImportRunRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    run, entry_specs, root_identity, should_run = _create_codex_diary_import_run_record(
        session,
        current_user=current_user,
        diary_date_text=req.date,
        entry_ids=req.entry_ids,
        confirm_duplicate=bool(req.confirm_duplicate),
    )

    if should_run:
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


def _codex_diary_yesterday_text(now: datetime | None = None) -> str:
    timezone = ZoneInfo(CODEX_DIARY_TIMEZONE)
    reference = now.astimezone(timezone) if now is not None else datetime.now(timezone)
    return (reference.date() - timedelta(days=1)).isoformat()


def _codex_diary_queue_has_active_task(
    task_name: str = CODEX_DIARY_AUTO_IMPORT_TASK_NAME,
    *,
    queue_snapshot: dict[str, Any] | None = None,
) -> bool:
    queue = queue_snapshot if isinstance(queue_snapshot, dict) else background_task_queue.snapshot()
    running = queue.get("running")
    if isinstance(running, dict) and running.get("name") == task_name:
        return True
    return any(
        isinstance(item, dict) and item.get("name") == task_name
        for item in queue.get("pending") or []
    )


def run_codex_diary_auto_import_job(
    db_bind: Any,
    target_date_text: str | None = None,
    *,
    trigger_reason: str = "scheduled",
) -> dict[str, Any]:
    target_date = target_date_text or _codex_diary_yesterday_text()
    runnable_runs: list[tuple[str, int, list[dict[str, Any]], dict[str, str]]] = []
    results: list[dict[str, Any]] = []

    with Session(db_bind) as session:
        users = session.exec(
            select(User)
            .where(User.is_active == True)  # noqa: E712
            .order_by(User.id)
        ).all()
        for user in users:
            if user.id is None:
                continue
            user_result: dict[str, Any] = {
                "user_id": int(user.id),
                "username": user.username,
                "date": target_date,
                "status": "pending",
            }
            try:
                run, entry_specs, root_identity, should_run = _create_codex_diary_import_run_record(
                    session,
                    current_user=user,
                    diary_date_text=target_date,
                    entry_ids=[],
                    confirm_duplicate=False,
                    skip_duplicate=True,
                    skip_active=True,
                )
            except HTTPException as exc:
                detail = getattr(exc, "detail", None)
                user_result.update(
                    {
                        "status": "skipped" if exc.status_code == 400 else "failed",
                        "error_message": detail if isinstance(detail, str) else str(detail or exc),
                    }
                )
                results.append(user_result)
                continue

            user_result.update(
                {
                    "run_id": run.id,
                    "status": "queued" if should_run else "skipped",
                    "stage_label": run.stage_label,
                    "entry_count": len(entry_specs),
                    "duplicate_note_count": len(run.duplicate_note_ids or []),
                }
            )
            results.append(user_result)
            if should_run:
                runnable_runs.append((run.id, int(user.id), entry_specs, root_identity))

    for run_id, user_id, entry_specs, root_identity in runnable_runs:
        _run_codex_diary_import_worker(
            db_bind,
            run_id=run_id,
            user_id=user_id,
            entry_specs=entry_specs,
            root_identity=root_identity,
        )

    with Session(db_bind) as session:
        run_map = {
            str(run_id): session.get(CodexDiaryImportRun, run_id)
            for run_id, _, _, _ in runnable_runs
        }
        for item in results:
            run_id = str(item.get("run_id") or "")
            run = run_map.get(run_id)
            if run is None:
                continue
            item.update(
                {
                    "status": run.status,
                    "stage_label": run.stage_label,
                    "created_note_count": int(run.created_note_count or 0),
                    "error_message": run.error_message,
                }
            )

    return {
        "date": target_date,
        "trigger_reason": trigger_reason,
        "user_count": len(results),
        "queued_run_count": len(runnable_runs),
        "results": results,
    }


def maybe_enqueue_codex_diary_yesterday_import(*, trigger_reason: str = "scheduled") -> str | None:
    from backend.db import engine

    if _codex_diary_queue_has_active_task():
        return None
    target_date = _codex_diary_yesterday_text()
    return background_task_queue.enqueue(
        CODEX_DIARY_AUTO_IMPORT_TASK_NAME,
        run_codex_diary_auto_import_job,
        engine,
        target_date,
        trigger_reason=trigger_reason,
        metadata={"date": target_date, "trigger_reason": trigger_reason},
    )


def init_codex_diary_import_scheduler() -> None:
    from backend.core.settings import get_settings

    if get_settings().is_test:
        return
        
    from backend.db import engine
    from backend.models import AppSetting
    from sqlmodel import Session
    with Session(engine) as session:
        row = session.get(AppSetting, f"background_task.{CODEX_DIARY_AUTO_IMPORT_TASK_NAME}.enabled")
        enabled = bool(row.value.get("enabled", False)) if row and isinstance(row.value, dict) else False
        
    if not enabled:
        return
        
    if not codex_diary_import_scheduler.running:
        codex_diary_import_scheduler.start()
    codex_diary_import_scheduler.add_job(
        maybe_enqueue_codex_diary_yesterday_import,
        CronTrigger.from_crontab(CODEX_DIARY_AUTO_IMPORT_CRON, timezone=ZoneInfo(CODEX_DIARY_TIMEZONE)),
        id=CODEX_DIARY_AUTO_IMPORT_TASK_NAME,
        replace_existing=True,
        max_instances=1,
    )
    print(f"Codex diary auto import scheduled: {CODEX_DIARY_AUTO_IMPORT_CRON}")


def shutdown_codex_diary_import_scheduler() -> None:
    if codex_diary_import_scheduler.running:
        codex_diary_import_scheduler.shutdown(wait=False)


@router.get("/codex-diary/import-runs/{run_id}", response_model=CodexDiaryImportRunRead)
def get_codex_diary_import_run(
    run_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    run = session.get(CodexDiaryImportRun, run_id)
    if run is None or run.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Codex diary import run not found")
    session.refresh(run)
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
    current_user: User = Depends(get_current_active_or_guest_notes_user),
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
    note_identity = allocate_new_note_identity(session)
    db_note = NoteNode(
        id=note_identity.primary_id,
        numeric_id=note_identity.numeric_id,
        legacy_id=note_identity.legacy_id,
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
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    """
    Get a specific note.
    """
    note = _get_accessible_note(note_id, current_user, session)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    note_refs = note_ref_aliases(note)
    
    # Calculate edge count
    edge_count = session.exec(
        select(func.count()).select_from(NoteEdge).where(
            NoteEdge.user_id == note.user_id,
            or_(NoteEdge.source_id.in_(note_refs), NoteEdge.target_id.in_(note_refs))
        )
    ).one()
    
    # Calculate out_degree (for Satellite mode)
    out_degree = session.exec(
        select(func.count()).select_from(NoteEdge).where(
            NoteEdge.user_id == note.user_id,
            NoteEdge.source_id.in_(note_refs)
        )
    ).one()

    # --- Inherited Custom Fields Logic ---
    # 1. Fetch direct parents (incoming edges)
    direct_parent_edges = session.exec(
        select(NoteEdge).where(
            NoteEdge.user_id == note.user_id,
            NoteEdge.target_id.in_(note_refs)
        )
    ).all()
    
    parent_ids = [e.source_id for e in direct_parent_edges]
    
    # 2. Fetch parent nodes
    parent_nodes = list({note_public_id(parent): parent for parent in load_notes_by_refs(session, note.user_id, parent_ids).values()}.values()) if parent_ids else []

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
    visited_ancestors = {note_public_id(parent) for parent in parent_nodes}
    queue = sorted(_note_edge_ref_set(parent_nodes))
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
            
            source_ref_map = load_notes_by_refs(session, note.user_id, [edge.source_id for edge in upstream_edges])
            new_ancestor_nodes: list[NoteNode] = []
            for edge in upstream_edges:
                source_note = source_ref_map.get(str(edge.source_id))
                if source_note is None:
                    continue
                source_id = note_public_id(source_note)
                if source_id not in visited_ancestors and source_id != note_public_id(note):
                    visited_ancestors.add(source_id)
                    new_ancestor_nodes.append(source_note)
            
            if new_ancestor_nodes:
                for anc_node in new_ancestor_nodes:
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
                
                queue = sorted(_note_edge_ref_set(new_ancestor_nodes))
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
    current_user: User = Depends(get_current_active_or_guest_notes_user),
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
            NoteNode.numeric_id.in_([int(note_id) for note_id in note_ids if str(note_id).isdecimal()]),
            _active_note_condition(),
        )
    ).all()
    component_refs = _note_edge_ref_set(nodes)
    component_edges = [edge for edge in edges if str(edge.source_id) in component_refs and str(edge.target_id) in component_refs]
    return {
        "nodes": [_serialize_note_list(note, current_user) for note in nodes],
        "edges": _serialize_note_edges_for_nodes(component_edges, nodes),
    }

@router.put("/{note_id}", response_model=NoteRead)
def update_note(
    note_id: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    """
    Update a note.
    """
    db_note = _get_accessible_note(note_id, current_user, session)
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")

    if note_in.base_version is not None and int(db_note.version or 1) != int(note_in.base_version):
        raise HTTPException(status_code=409, detail="文档版本已变化，请重新读取后再写入")

    note_data = _prepare_note_update_data(db_note, note_in.model_dump(exclude_unset=True, exclude={"base_version"}))
    _append_note_history(db_note, note_data, int(time.time()))
    _record_note_metadata_feedback_safely(
        session,
        note=db_note,
        updates=note_data,
        source_kind="manual_update",
    )

    for key, value in note_data.items():
        setattr(db_note, key, value)

    if note_data:
        db_note.version = max(int(db_note.version or 1), 1) + 1
    db_note.updated_at = time.time()
    session.add(db_note)
    session.commit()
    session.refresh(db_note)
    if note_data:
        _broadcast_note_resource_update(db_note)
    return _serialize_note_read(db_note, current_user)

@router.delete("/{note_id}")
def delete_note(
    note_id: str,
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    """
    Delete a note.
    """
    db_note = _get_accessible_note(note_id, current_user, session)
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    now = time.time()
    db_note.deleted_at = now
    db_note.deleted_by_user_id = current_user.id
    db_note.updated_at = now
    db_note.version = max(int(db_note.version or 1), 1) + 1
    note_refs = note_ref_aliases(db_note)
    session.exec(
        delete(NoteEdge).where(
            NoteEdge.user_id == current_user.id,
            or_(NoteEdge.source_id.in_(note_refs), NoteEdge.target_id.in_(note_refs)),
        )
    )
    session.add(db_note)
    session.commit()
    session.refresh(db_note)
    _broadcast_note_resource_update(db_note)
    return {"ok": True}


@router.post("/{note_id}/restore", response_model=NoteRead)
def restore_note(
    note_id: str,
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session),
):
    db_note = _get_accessible_note_by_any_ref(note_id, current_user, session, include_deleted=True)
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    if not db_note.deleted_at:
        return _serialize_note_read(db_note, current_user)

    now = time.time()
    db_note.deleted_at = None
    db_note.deleted_by_user_id = None
    db_note.updated_at = now
    db_note.version = max(int(db_note.version or 1), 1) + 1
    session.add(db_note)
    session.commit()
    session.refresh(db_note)
    _broadcast_note_resource_update(db_note)
    return _serialize_note_read(db_note, current_user)

# --- Edges ---

@router.get("/edges/", response_model=List[EdgeRead])
def read_edges(
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    """
    Retrieve all edges for the current user.
    """
    statement = select(NoteEdge).where(NoteEdge.user_id == current_user.id)
    edges = session.exec(statement).all()
    return [_serialize_note_edge(edge, session=session, user_id=current_user.id) for edge in edges]

@router.post("/edges/", response_model=EdgeRead)
def create_edge(
    edge: EdgeCreate,
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    """
    Create a directed edge between two notes.
    Idempotent: If edge exists, return existing one (or update timestamp).
    """
    source = _get_accessible_note(edge.source_id, current_user, session)
    target = _get_accessible_note(edge.target_id, current_user, session)
    
    if not source or not target:
        raise HTTPException(status_code=404, detail="Source or Target node not found")

    # Prevent self-loop if desired (optional)
    if str(source.id) == str(target.id):
        raise HTTPException(status_code=400, detail="Self-loops are not allowed")

    source_ref = note_edge_ref(source)
    target_ref = note_edge_ref(target)
    source_refs = note_ref_aliases(source)
    target_refs = note_ref_aliases(target)

    # Check if edge already exists
    statement = select(NoteEdge).where(
        NoteEdge.user_id == current_user.id,
        NoteEdge.source_id.in_(source_refs),
        NoteEdge.target_id.in_(target_refs),
    )
    existing_edge = session.exec(statement).first()
    
    if existing_edge:
        changed = False
        if existing_edge.source_id != source_ref or existing_edge.target_id != target_ref:
            existing_edge.source_id = source_ref
            existing_edge.target_id = target_ref
            changed = True
        # Idempotent: Return existing edge
        # Optionally update label if provided
        if edge.label is not None and edge.label != existing_edge.label:
            existing_edge.label = edge.label
            changed = True
        if changed:
            session.add(existing_edge)
            session.commit()
            session.refresh(existing_edge)
        return _serialize_note_edge(existing_edge, session=session, user_id=current_user.id)

    db_edge = NoteEdge(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        source_id=source_ref,
        target_id=target_ref,
        label=edge.label,
        created_at=time.time()
    )
    session.add(db_edge)
    session.commit()
    session.refresh(db_edge)
    return _serialize_note_edge(db_edge, session=session, user_id=current_user.id)

@router.delete("/edges/")
def delete_edge_by_nodes(
    source: str,
    target: str,
    current_user: User = Depends(get_current_active_or_guest_notes_user),
    session: Session = Depends(get_session)
):
    """
    Delete an edge by source and target IDs.
    Robust: If edge doesn't exist, return success anyway (idempotent delete).
    """
    source_note = _get_accessible_note(source, current_user, session)
    target_note = _get_accessible_note(target, current_user, session)
    source_refs = note_ref_aliases(source_note) if source_note is not None else {str(source)}
    target_refs = note_ref_aliases(target_note) if target_note is not None else {str(target)}
    statement = select(NoteEdge).where(
        NoteEdge.user_id == current_user.id,
        NoteEdge.source_id.in_(source_refs),
        NoteEdge.target_id.in_(target_refs),
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
    current_user: User = Depends(get_current_active_or_guest_notes_user),
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
