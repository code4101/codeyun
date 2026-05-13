import json
import re
import tempfile
import time
import uuid
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from passlib.context import CryptContext
from sqlmodel import Session, select

from backend.core.auth import get_current_active_user, get_optional_current_user_from_token
from backend.core.feature_access_guard import require_feature_access_dependency
from backend.db import get_session
from backend.models import NoteEdge, NoteNode, User
from backend.schemas import NoteRead, NoteUpdate
from backend.core.fanxiu_status import (
    derive_status_snapshot,
    load_status_payload,
    resolve_status_path_config,
    save_status_payload,
    save_status_config,
)
from backend.core.fanxiu_inventory import load_magic_treasure_hall, save_magic_treasure_hall
from backend.core.fanxiu_inventory import load_wardrobe_hall, save_wardrobe_hall
from backend.core.fanxiu_inventory import load_spirit_beast_hall, save_spirit_beast_hall
from backend.core.fanxiu_inventory import load_activity_list, save_activity_list
from backend.core.fanxiu_inventory import load_modao_invasion_exchange_list, save_modao_invasion_exchange_list
from backend.core.fanxiu_inventory import (
    load_shouyuan_exploration_exchange_list,
    save_shouyuan_exploration_exchange_list,
)
from backend.core.fanxiu_processes import match_fanxiu_command_line, list_fanxiu_processes, terminate_fanxiu_processes
from backend.core.fanxiu_region_data import (
    build_region_character_history_snapshot,
    build_region_character_snapshot,
    build_region_data_snapshot,
    create_region_character_record_if_stronger,
    disable_region_character_record,
    serialize_region_character_record,
    update_region_character_record,
)
from backend.core.local_script_processes import list_local_script_processes
from backend.core.note_access import note_to_response_dict
from backend.core.note_semantics import (
    NOTE_KIND_FANXIU_CHAR,
    NOTE_KIND_FANXIU_ACTIVITY_ITEM,
    NOTE_KIND_FANXIU_MAGIC_TREASURE_ITEM,
    NOTE_KIND_FANXIU_SPIRIT_BEAST_ITEM,
    NOTE_KIND_FANXIU_WARDROBE_ITEM,
    NOTE_KIND_DEFAULT,
    NOTE_WEIGHT_MODE_LINEAR,
    build_legacy_color_type_key,
    derive_note_taxonomy_from_legacy,
    derive_primary_node_type,
    normalize_note_color,
    normalize_note_types,
)
from backend.core.ocr_preview import OcrPreviewError, run_paddle_ocr_preview

router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("fanxiu"))],
)
status_router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("fanxiu"))],
)
chars_router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("fanxiu"))],
)
inventory_router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("fanxiu"))],
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

FANXIU_USERNAME = "凡修手游"
FANXIU_CHAR_TYPE = "memo"
FANXIU_CHAR_KIND = NOTE_KIND_FANXIU_CHAR
FANXIU_WARDROBE_TYPE = "doc"
FANXIU_WARDROBE_KIND = NOTE_KIND_FANXIU_WARDROBE_ITEM
FANXIU_SPIRIT_BEAST_TYPE = "doc"
FANXIU_SPIRIT_BEAST_KIND = NOTE_KIND_FANXIU_SPIRIT_BEAST_ITEM
FANXIU_MAGIC_TREASURE_TYPE = "doc"
FANXIU_MAGIC_TREASURE_KIND = NOTE_KIND_FANXIU_MAGIC_TREASURE_ITEM
FANXIU_ACTIVITY_TYPE = "doc"
FANXIU_ACTIVITY_KIND = NOTE_KIND_FANXIU_ACTIVITY_ITEM
CODE4101_USERNAME = "code4101"
DEFAULT_REGION_CHARACTER_GUILD = "凌霄阁"
FANXIU_CULTIVATION_REALMS = (
    "炼气",
    "筑基",
    "结丹",
    "元婴",
    "化神",
    "炼虚",
    "合体",
    "大乘",
    "真仙",
    "金仙",
)
FANXIU_CULTIVATION_STAGES = ("前期", "中期", "后期")
FANXIU_CULTIVATION_REALM_ALIASES = {
    "炼气": "炼气",
    "筑基": "筑基",
    "结丹": "结丹",
    "元婴": "元婴",
    "原因": "元婴",
    "化神": "化神",
    "炼虚": "炼虚",
    "合体": "合体",
    "大乘": "大乘",
    "真仙": "真仙",
    "金仙": "金仙",
}
FANXIU_CULTIVATION_LAYER_ALIASES = {
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "壹": 1,
    "贰": 2,
    "叁": 3,
    "肆": 4,
    "伍": 5,
    "陆": 6,
    "柒": 7,
    "捌": 8,
    "玖": 9,
    "拾": 10,
}
MAGIC_TREASURE_SECTION_KEYS = {"fabao", "xiantiangubao", "houtiangubao"}
XIANZHOU_RACE_CHAR_NAMES = (
    "凌玉灵",
    "大衍神君",
    "黑凤王",
    "黛儿",
    "南宫婉",
    "向之礼",
    "冰凤仙子",
    "银月",
    "甲天木",
    "元刹",
    "天元圣皇",
    "冰魄仙子",
)
MAGIC_TREASURE_TYPE_ABBR_MAP = {
    "攻": "攻击",
    "防": "防御",
    "灵": "灵力",
    "辅": "辅助",
}
QUALITY_LABELS = [
    "珍品",
    "绝品",
    "仙品一星",
    "仙品二星",
    "仙品三星",
    "仙品四星",
    "仙品五星",
    "仙品六星",
    "神品一星",
    "神品二星",
    "神品三星",
    "神品四星",
    "神品五星",
    "神品六星",
    "神品七星",
    "神品八星",
    "神品九星",
    "神品十星",
]


class FanxiuTaskStatusItem(BaseModel):
    name: str
    scheduled_at: str
    due: bool
    seconds_until_due: int
    is_next: bool = False


class FanxiuAccountStatusItem(BaseModel):
    name: str
    phone: Optional[str] = None
    is_current: bool = False
    has_due_task: bool = False
    due_count: int = 0
    task_count: int = 0
    next_task_name: Optional[str] = None
    next_task_at: Optional[str] = None
    tasks: List[FanxiuTaskStatusItem] = Field(default_factory=list)


class FanxiuRuntimeTimerItem(BaseModel):
    name: str
    scheduled_at: str
    due: bool
    seconds_until_due: int


class FanxiuStatusConfigRead(BaseModel):
    status_path: Optional[str] = None
    auto_detected_path: Optional[str] = None
    effective_path: Optional[str] = None
    mode: str
    file_exists: bool = False


class FanxiuStatusConfigUpdate(BaseModel):
    status_path: Optional[str] = None


class FanxiuStatusParseRequest(BaseModel):
    raw_status: dict[str, Any]


class FanxiuStatusUpdateRequest(BaseModel):
    raw_status: dict[str, Any]


class FanxiuProcessItem(BaseModel):
    pid: int
    parent_pid: Optional[int] = None
    name: str
    command_line: str
    created_at: Optional[str] = None
    matched_reason: str


class FanxiuProcessListResponse(BaseModel):
    items: List[FanxiuProcessItem] = Field(default_factory=list)


class LocalScriptProcessItem(BaseModel):
    pid: int
    parent_pid: Optional[int] = None
    name: str
    kind: str
    script: str
    script_path: Optional[str] = None
    command_line: str
    cwd: Optional[str] = None
    created_at: Optional[str] = None
    runtime_seconds: Optional[int] = None
    project_hint: str = ""
    is_fanxiu: bool = False


class LocalScriptProcessListResponse(BaseModel):
    items: List[LocalScriptProcessItem] = Field(default_factory=list)


class FanxiuProcessTerminateError(BaseModel):
    pid: int
    error: str


class FanxiuProcessTerminateResponse(BaseModel):
    matched: List[FanxiuProcessItem] = Field(default_factory=list)
    terminated: List[FanxiuProcessItem] = Field(default_factory=list)
    remaining: List[FanxiuProcessItem] = Field(default_factory=list)
    errors: List[FanxiuProcessTerminateError] = Field(default_factory=list)


class FanxiuStatusSnapshot(FanxiuStatusConfigRead):
    loaded_at: str
    error: Optional[str] = None
    current_account: Optional[str] = None
    recommended_account: Optional[str] = None
    next_task_path: Optional[str] = None
    next_task_name: Optional[str] = None
    next_task_at: Optional[str] = None
    next_task_seconds_until_due: Optional[int] = None
    program_initialized: bool = False
    all_tasks_completed: bool = False
    watchdog_hash: Optional[str] = None
    runtime_timers: List[FanxiuRuntimeTimerItem] = Field(default_factory=list)
    accounts: List[FanxiuAccountStatusItem] = Field(default_factory=list)
    raw_status: Optional[dict[str, Any]] = None


class FanxiuWardrobeItem(BaseModel):
    id: str
    name: str = ""
    rank: int = 0
    shenlian: int = 0
    type: str = ""
    quality: Optional[int] = None
    main_use: str = ""
    acquisition: str = ""
    date: date
    note_id: Optional[str] = None


class FanxiuWardrobeHallSnapshot(BaseModel):
    shizhuang: List[FanxiuWardrobeItem] = Field(default_factory=list)
    wuqi: List[FanxiuWardrobeItem] = Field(default_factory=list)
    huanshen: List[FanxiuWardrobeItem] = Field(default_factory=list)
    beishi: List[FanxiuWardrobeItem] = Field(default_factory=list)
    yuqi: List[FanxiuWardrobeItem] = Field(default_factory=list)


class FanxiuSpiritBeastHallSnapshot(BaseModel):
    lingshou: List[FanxiuWardrobeItem] = Field(default_factory=list)
    shengshou: List[FanxiuWardrobeItem] = Field(default_factory=list)


class FanxiuMagicTreasureHallSnapshot(BaseModel):
    fabao: List[FanxiuWardrobeItem] = Field(default_factory=list)
    xiantiangubao: List[FanxiuWardrobeItem] = Field(default_factory=list)
    houtiangubao: List[FanxiuWardrobeItem] = Field(default_factory=list)


class FanxiuActivityItem(BaseModel):
    id: str
    name: str = ""
    cross_count: int = 0
    start_date: date
    end_date: date
    note_id: Optional[str] = None


class FanxiuActivityListSnapshot(BaseModel):
    items: List[FanxiuActivityItem] = Field(default_factory=list)


class FanxiuRegionServerItem(BaseModel):
    id: str
    region_name: str = ""
    order: int = 0
    name: str = ""
    open_date: str = ""
    mark_type: str = ""
    mark_label: str = ""
    mark_title: str = ""


class FanxiuRegionAreaItem(BaseModel):
    id: str
    number: int = 0
    name: str = ""
    start_date: str = ""
    end_date: str = ""
    known_count: int = 0
    servers: List[FanxiuRegionServerItem] = Field(default_factory=list)


class FanxiuRegionDataSnapshot(BaseModel):
    regions: List[FanxiuRegionAreaItem] = Field(default_factory=list)


class FanxiuRegionCharacterItem(BaseModel):
    id: str
    region_name: str = ""
    server_name: str = ""
    guild_name: str = ""
    role_name: str = ""
    attack: str = ""
    cultivation_level: str = ""
    recorded_date: str = ""
    disabled: bool = False
    created_at: float = 0
    updated_at: float = 0
    disabled_at: Optional[float] = None


class FanxiuRegionCharacterSnapshot(BaseModel):
    characters: List[FanxiuRegionCharacterItem] = Field(default_factory=list)


class FanxiuRegionCharacterUpdate(BaseModel):
    guild_name: Optional[str] = None
    role_name: Optional[str] = None
    attack: Optional[str] = None
    cultivation_level: Optional[str] = None
    recorded_date: Optional[str] = None
    disabled: Optional[bool] = None


class FanxiuRegionCharacterHistorySnapshot(BaseModel):
    characters: List[FanxiuRegionCharacterItem] = Field(default_factory=list)


class FanxiuModaoInvasionExchangeItem(BaseModel):
    id: str
    name: str = ""
    magic_crystal_cost: int = 0
    purchase_limit: int = 0
    checked: bool = False


class FanxiuModaoInvasionPersonalRankingItem(BaseModel):
    id: str
    rank: int = 0
    name: str = ""
    plane: str = ""
    merit: int = 0


class FanxiuModaoInvasionRecord(BaseModel):
    id: str
    activity_id: str = ""
    label: str = ""
    personal_rankings: List[FanxiuModaoInvasionPersonalRankingItem] = Field(default_factory=list)
    items: List[FanxiuModaoInvasionExchangeItem] = Field(default_factory=list)


class FanxiuModaoInvasionSnapshot(BaseModel):
    records: List[FanxiuModaoInvasionRecord] = Field(default_factory=list)


class FanxiuShouyuanExplorationExchangeItem(BaseModel):
    id: str
    name: str = ""
    magic_crystal_cost: int = 0
    purchase_limit: int = 0
    checked: bool = False


class FanxiuShouyuanExplorationPersonalRankingItem(BaseModel):
    id: str
    rank: int = 0
    name: str = ""
    plane: str = ""
    merit: int = 0


class FanxiuShouyuanExplorationIncomeSpeedItem(BaseModel):
    id: str
    captured_date: str = ""
    search_count: int = 0
    beast_crystal: int = 0
    score: int = 0
    merit: int = 0
    remark: str = ""


class FanxiuShouyuanExplorationConsumptionEvaluationItem(BaseModel):
    id: str
    label: str = ""
    current: float = 0
    target: float = 0
    speed: float = 0


class FanxiuShouyuanExplorationRecord(BaseModel):
    id: str
    activity_id: str = ""
    label: str = ""
    personal_rankings: List[FanxiuShouyuanExplorationPersonalRankingItem] = Field(default_factory=list)
    income_speeds: List[FanxiuShouyuanExplorationIncomeSpeedItem] = Field(default_factory=list)
    consumption_evaluations: List[FanxiuShouyuanExplorationConsumptionEvaluationItem] = Field(default_factory=list)
    items: List[FanxiuShouyuanExplorationExchangeItem] = Field(default_factory=list)


class FanxiuShouyuanExplorationSnapshot(BaseModel):
    records: List[FanxiuShouyuanExplorationRecord] = Field(default_factory=list)


class FanxiuMagicTreasureOcrImportResponse(BaseModel):
    section_key: str
    lines: List[str] = Field(default_factory=list)
    item: FanxiuWardrobeItem


class FanxiuRegionCharacterOcrImportResponse(BaseModel):
    lines: List[str] = Field(default_factory=list)
    item: FanxiuRegionCharacterItem
    created: bool = True
    skipped_reason: str = ""


class FanxiuModaoInvasionOcrImportResponse(BaseModel):
    lines: List[str] = Field(default_factory=list)
    items: List[FanxiuModaoInvasionExchangeItem] = Field(default_factory=list)


class FanxiuModaoInvasionPersonalRankingOcrImportResponse(BaseModel):
    lines: List[str] = Field(default_factory=list)
    items: List[FanxiuModaoInvasionPersonalRankingItem] = Field(default_factory=list)


class FanxiuShouyuanExplorationOcrImportResponse(BaseModel):
    lines: List[str] = Field(default_factory=list)
    items: List[FanxiuShouyuanExplorationExchangeItem] = Field(default_factory=list)


class FanxiuShouyuanExplorationPersonalRankingOcrImportResponse(BaseModel):
    lines: List[str] = Field(default_factory=list)
    items: List[FanxiuShouyuanExplorationPersonalRankingItem] = Field(default_factory=list)


class FanxiuShouyuanExplorationIncomeSpeedOcrImportResponse(BaseModel):
    lines: List[str] = Field(default_factory=list)
    item: FanxiuShouyuanExplorationIncomeSpeedItem


class FanxiuFormationRequirementImportItem(BaseModel):
    text: str
    effect_text: str = ""


class FanxiuFormationEffectDetailImportItem(BaseModel):
    effect_name: str
    effect_detail: str = ""


class FanxiuFormationRequirementOcrImportResponse(BaseModel):
    lines: List[str] = Field(default_factory=list)
    requirements: List[FanxiuFormationRequirementImportItem] = Field(default_factory=list)
    effect_details: List[FanxiuFormationEffectDetailImportItem] = Field(default_factory=list)


def _sanitize_ocr_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _extract_shape_text(shape: dict[str, Any]) -> str:
    raw_label = shape.get("label")
    if isinstance(raw_label, str):
        try:
            payload = json.loads(raw_label)
        except json.JSONDecodeError:
            return _sanitize_ocr_text(raw_label)
        if isinstance(payload, dict):
            return _sanitize_ocr_text(payload.get("text"))
    return ""


def _extract_shape_rectangle(points: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(points, list) or len(points) < 2:
        return None

    flattened: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        try:
            flattened.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            return None

    xs = [item[0] for item in flattened]
    ys = [item[1] for item in flattened]
    return min(xs), min(ys), max(xs), max(ys)


def _extract_ocr_line_entries(preview_document: dict[str, Any]) -> list[list[dict[str, Any]]]:
    raw_shapes = preview_document.get("shapes") or []
    if not isinstance(raw_shapes, list):
        return []

    entries: list[dict[str, Any]] = []
    for shape in raw_shapes:
        if not isinstance(shape, dict):
            continue
        text = _extract_shape_text(shape)
        if not text:
            continue
        rectangle = _extract_shape_rectangle(shape.get("points"))
        if rectangle is None:
            continue
        x1, y1, x2, y2 = rectangle
        entries.append(
            {
                "text": text,
                "x": x1,
                "x2": x2,
                "width": max(x2 - x1, 1),
                "y": (y1 + y2) / 2,
                "height": max(y2 - y1, 1),
            }
        )

    if not entries:
        return []

    entries.sort(key=lambda item: (item["y"], item["x"]))
    heights = sorted(entry["height"] for entry in entries)
    median_height = heights[len(heights) // 2]
    tolerance = max(12.0, median_height * 0.75)

    grouped: list[list[dict[str, Any]]] = []
    current_group: list[dict[str, Any]] = []
    current_y = 0.0
    for entry in entries:
        if not current_group:
            current_group = [entry]
            current_y = entry["y"]
            continue

        if abs(entry["y"] - current_y) <= tolerance:
            current_group.append(entry)
            current_y = sum(item["y"] for item in current_group) / len(current_group)
            continue

        grouped.append(sorted(current_group, key=lambda item: item["x"]))
        current_group = [entry]
        current_y = entry["y"]

    if current_group:
        grouped.append(sorted(current_group, key=lambda item: item["x"]))

    return [[item for item in group if str(item["text"]).strip()] for group in grouped]


def _extract_magic_treasure_ocr_line_entries(preview_document: dict[str, Any]) -> list[list[dict[str, Any]]]:
    return _extract_ocr_line_entries(preview_document)


def _extract_magic_treasure_ocr_lines(preview_document: dict[str, Any]) -> list[list[str]]:
    grouped_entries = _extract_magic_treasure_ocr_line_entries(preview_document)
    return [[str(item["text"]) for item in group if str(item["text"]).strip()] for group in grouped_entries]


def _normalize_formation_requirement_text(text: str) -> str:
    normalized = _sanitize_ocr_text(text)
    return re.sub(r"\s*[（(]?\s*\d+\s*/\s*\d+\s*[)）]?\s*$", "", normalized).strip()


def _normalize_formation_effect_text(text: str) -> str:
    normalized = _sanitize_ocr_text(text)
    if not normalized:
        return ""
    normalized = re.sub(r"[［\[]", "【", normalized)
    normalized = re.sub(r"[］\]]", "】", normalized)
    normalized = re.sub(r"\s*[:：]\s*", "", normalized)
    normalized = re.sub(r"【\s*", "【", normalized)
    normalized = re.sub(r"\s*】", "】", normalized)
    return normalized.strip()


def _normalize_formation_effect_name(text: str) -> str:
    normalized = _sanitize_ocr_text(text)
    normalized = re.sub(r"^[^\u4e00-\u9fffA-Za-z0-9【】\[\]（）()]+", "", normalized)
    normalized = re.sub(r"\s*[:：]\s*", "", normalized)
    return normalized.strip()


def _normalize_formation_effect_detail(text: str) -> str:
    normalized = _sanitize_ocr_text(text)
    normalized = re.sub(r"\s*[（(]?\s*\d+\s*/\s*\d+\s*[)）]?\s*$", "", normalized).strip()
    return normalized


def _is_formation_requirement_condition(text: str) -> bool:
    return bool(re.match(r"^(入阵|上阵|点亮|阵法神通达到)", _normalize_formation_requirement_text(text)))


def _looks_like_formation_effect_line(text: str) -> bool:
    normalized = _normalize_formation_effect_text(text)
    return normalized.startswith("【")


def _merge_formation_effect_text(left: str, right: str) -> str:
    parts: list[str] = []
    for chunk in [left, right]:
        for item in re.split(r"[；;]+", _sanitize_ocr_text(chunk)):
            normalized = _normalize_formation_effect_text(item)
            if normalized and normalized not in parts:
                parts.append(normalized)
    return "；".join(parts)


def _match_formation_effect_detail_heading(text: str, heading: str) -> tuple[bool, str]:
    normalized = _normalize_formation_effect_name(text)
    match = re.match(rf"^{heading}[：:]?(.*)$", normalized)
    if not match:
        return False, ""
    return True, _normalize_formation_effect_name(match.group(1))


def _build_formation_requirements_from_ocr_document(
    preview_document: dict[str, Any],
) -> tuple[list[dict[str, str]], list[str]]:
    line_entries = _extract_ocr_line_entries(preview_document)
    lines = [
        "".join(_sanitize_ocr_text(item.get("text")) for item in group if _sanitize_ocr_text(item.get("text")))
        for group in line_entries
    ]
    normalized_lines = [line for line in lines if line]
    if not normalized_lines:
        raise ValueError("未能从截图中识别触发条件")

    raw_items: list[dict[str, str]] = []
    pending_effect_lines: list[str] = []
    current_condition_lines: list[str] = []

    def flush_condition_lines() -> None:
        nonlocal current_condition_lines, pending_effect_lines
        if not current_condition_lines:
            return
        normalized_condition = _normalize_formation_requirement_text("".join(current_condition_lines))
        if normalized_condition:
            raw_items.append(
                {
                    "text": normalized_condition,
                    "effect_text": "；".join(pending_effect_lines),
                }
            )
        current_condition_lines = []
        pending_effect_lines = []

    for line in normalized_lines:
        if _looks_like_formation_effect_line(line):
            flush_condition_lines()
            normalized_effect = _normalize_formation_effect_text(line)
            if normalized_effect and (not pending_effect_lines or pending_effect_lines[-1] != normalized_effect):
                pending_effect_lines.append(normalized_effect)
            continue

        if _is_formation_requirement_condition(line):
            flush_condition_lines()
            current_condition_lines = [line]
            continue

        if current_condition_lines:
            current_condition_lines.append(line)
            continue

        normalized_effect = _normalize_formation_effect_text(line)
        if normalized_effect and (not pending_effect_lines or pending_effect_lines[-1] != normalized_effect):
            pending_effect_lines.append(normalized_effect)

    flush_condition_lines()

    if not raw_items:
        raise ValueError("未能从截图中识别触发条件")

    merged: list[dict[str, str]] = []
    merged_by_text: dict[str, dict[str, str]] = {}
    for item in raw_items:
        key = _sanitize_ocr_text(item.get("text"))
        if not key:
            continue
        existing = merged_by_text.get(key)
        if existing is None:
            payload = {
                "text": key,
                "effect_text": _normalize_formation_effect_text(item.get("effect_text", "")),
            }
            merged_by_text[key] = payload
            merged.append(payload)
            continue
        existing["effect_text"] = _merge_formation_effect_text(existing.get("effect_text", ""), item.get("effect_text", ""))

    if not merged:
        raise ValueError("未能从截图中识别触发条件")
    return merged, normalized_lines


def _build_formation_effect_details_from_ocr_document(
    preview_document: dict[str, Any],
) -> tuple[list[dict[str, str]], list[str]]:
    line_entries = _extract_ocr_line_entries(preview_document)
    lines = [
        "".join(_sanitize_ocr_text(item.get("text")) for item in group if _sanitize_ocr_text(item.get("text")))
        for group in line_entries
    ]
    normalized_lines = [line for line in lines if line]
    if not normalized_lines:
        raise ValueError("未能从截图中识别词缀效果")

    raw_items: list[dict[str, str]] = []
    current_name = ""
    current_detail_lines: list[str] = []
    waiting_name = False
    collecting_detail = False

    def flush_current() -> None:
        nonlocal current_name, current_detail_lines, waiting_name, collecting_detail
        effect_name = _normalize_formation_effect_name(current_name)
        effect_detail = _normalize_formation_effect_detail("".join(current_detail_lines))
        if effect_name and effect_detail:
            raw_items.append(
                {
                    "effect_name": effect_name,
                    "effect_detail": effect_detail,
                }
            )
        current_name = ""
        current_detail_lines = []
        waiting_name = False
        collecting_detail = False

    for line in normalized_lines:
        is_name_heading, name_remainder = _match_formation_effect_detail_heading(line, "名字")
        if is_name_heading:
            flush_current()
            current_name = name_remainder
            waiting_name = not bool(name_remainder)
            collecting_detail = False
            continue

        is_effect_heading, effect_remainder = _match_formation_effect_detail_heading(line, "效果")
        if is_effect_heading:
            collecting_detail = True
            if effect_remainder:
                current_detail_lines.append(effect_remainder)
            continue

        if waiting_name and not current_name:
            current_name = _normalize_formation_effect_name(line)
            waiting_name = False
            continue

        if collecting_detail:
            normalized_detail_line = _normalize_formation_effect_detail(line)
            if normalized_detail_line:
                current_detail_lines.append(normalized_detail_line)

    flush_current()

    merged: list[dict[str, str]] = []
    merged_by_name: dict[str, dict[str, str]] = {}
    for item in raw_items:
        effect_name = _normalize_formation_effect_name(item.get("effect_name", ""))
        effect_detail = _normalize_formation_effect_detail(item.get("effect_detail", ""))
        if not effect_name or not effect_detail:
            continue
        existing = merged_by_name.get(effect_name)
        if existing is None:
            payload = {
                "effect_name": effect_name,
                "effect_detail": effect_detail,
            }
            merged_by_name[effect_name] = payload
            merged.append(payload)
            continue
        if effect_detail != existing["effect_detail"]:
            existing["effect_detail"] = "\n".join(
                dict.fromkeys(
                    line
                    for line in [*existing["effect_detail"].splitlines(), *effect_detail.splitlines()]
                    if line
                )
            )

    if not merged:
        raise ValueError("未能从截图中识别词缀效果")
    return merged, normalized_lines


def _extract_first_int_from_text(value: str) -> int | None:
    match = re.search(r"\d+", _sanitize_ocr_text(value))
    if not match:
        return None
    return int(match.group(0))


def _normalize_modao_invasion_item_name(value: str) -> str:
    normalized = _sanitize_ocr_text(value)
    normalized = re.sub(r"(?:活动(?:内)?限购|限购).*$", "", normalized)
    normalized = re.sub(r"^(?:\d+折)?(?:\d+)?", "", normalized)
    normalized = re.sub(r"^[^\u4e00-\u9fffA-Za-z]+", "", normalized)
    normalized = re.sub(r"[：:]+$", "", normalized)
    normalized = re.sub(r"^[·•]+|[·•]+$", "", normalized)
    return normalized.strip()


def _is_modao_invasion_non_item_line(value: str) -> bool:
    normalized = _sanitize_ocr_text(value)
    return any(
        token in normalized
        for token in (
            "兑换宝阁",
            "当前拥有位面魔晶",
            "活动期间累计位面魔晶",
            "规则",
        )
    )


def _parse_modao_invasion_header_line(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    fragments = [_sanitize_ocr_text(entry.get("text")) for entry in entries if _sanitize_ocr_text(entry.get("text"))]
    joined = "".join(fragments)
    if not joined or "限购" not in joined or _is_modao_invasion_non_item_line(joined):
        return None

    prefix = ""
    purchase_limit = None
    discount_rate = None

    discount_match = re.search(r"(\d+)折", joined)
    if discount_match:
        discount_rate = int(discount_match.group(1))

    matched = re.search(r"^(.*?)(?:活动(?:内)?限购|限购)[:：]?\D*(\d+)", joined)
    if matched:
        prefix = matched.group(1)
        purchase_limit = int(matched.group(2))
    else:
        for index, fragment in enumerate(fragments):
            if "限购" not in fragment:
                continue
            prefix = "".join(fragments[:index]) or re.sub(r"(?:活动(?:内)?限购|限购).*$", "", joined)
            purchase_limit = _extract_first_int_from_text("".join(fragments[index:]))
            break

    name = _normalize_modao_invasion_item_name(prefix)
    if not name or purchase_limit is None:
        return None

    return {
        "name": name,
        "purchase_limit": purchase_limit,
        "discount_rate": discount_rate,
    }


def _extract_modao_invasion_effective_cost(value: str, *, discount_rate: int | None = None) -> int | None:
    normalized = _sanitize_ocr_text(value)
    if not normalized:
        return None

    numeric_groups = re.findall(r"\d+", normalized)
    if len(numeric_groups) >= 2:
        return int(numeric_groups[0])

    if not normalized.isdigit():
        return int(numeric_groups[0]) if numeric_groups else None

    if discount_rate is not None and 1 <= discount_rate <= 9:
        for split_index in range(1, len(normalized)):
            left_text = normalized[:split_index]
            right_text = normalized[split_index:]
            if not left_text or not right_text:
                continue
            left_value = int(left_text)
            right_value = int(right_text)
            if left_value <= 0 or right_value <= 0 or right_value < left_value:
                continue
            if left_value * 10 == right_value * discount_rate:
                return left_value

    return int(normalized)


def _parse_modao_invasion_cost_line(entries: list[dict[str, Any]], *, discount_rate: int | None = None) -> int | None:
    fragments = [_sanitize_ocr_text(entry.get("text")) for entry in entries if _sanitize_ocr_text(entry.get("text"))]
    joined = "".join(fragments)
    if not joined or "限购" in joined or _is_modao_invasion_non_item_line(joined):
        return None

    seen_cost_prefix = False
    for fragment in fragments:
        if any(token in fragment for token in ("所需", "所", "需")):
            seen_cost_prefix = True
            remainder = re.sub(r"^.*?(?:所需|所|需)[:：]?", "", fragment)
            value = _extract_modao_invasion_effective_cost(remainder, discount_rate=discount_rate)
            if value is not None:
                return value
            continue

        value = _extract_modao_invasion_effective_cost(fragment, discount_rate=discount_rate)
        if seen_cost_prefix and value is not None:
            return value

    return _extract_modao_invasion_effective_cost(joined, discount_rate=discount_rate)


def _build_modao_invasion_exchange_items_from_ocr_document(
    preview_document: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    line_entries = _extract_ocr_line_entries(preview_document)
    lines = [
        "".join(_sanitize_ocr_text(item.get("text")) for item in group if _sanitize_ocr_text(item.get("text")))
        for group in line_entries
    ]

    header_rows: list[tuple[int, dict[str, Any]]] = []
    for index, group in enumerate(line_entries):
        parsed = _parse_modao_invasion_header_line(group)
        if parsed is not None:
            header_rows.append((index, parsed))

    imported_items: list[dict[str, Any]] = []
    for header_index, (line_index, header) in enumerate(header_rows):
        next_line_index = header_rows[header_index + 1][0] if header_index + 1 < len(header_rows) else len(line_entries)
        magic_crystal_cost = None
        for cost_index in range(line_index + 1, next_line_index):
            magic_crystal_cost = _parse_modao_invasion_cost_line(
                line_entries[cost_index],
                discount_rate=header.get("discount_rate"),
            )
            if magic_crystal_cost is not None:
                break

        if magic_crystal_cost is None:
            continue

        imported_items.append(
            {
                "id": str(uuid.uuid4()),
                "name": header["name"],
                "magic_crystal_cost": magic_crystal_cost,
                "purchase_limit": header["purchase_limit"],
            }
        )

    if not imported_items:
        raise ValueError("未能从截图中识别可导入的兑换条目")

    return imported_items, [line for line in lines if line]


def _looks_like_modao_invasion_personal_ranking_line(value: str) -> bool:
    normalized = _sanitize_ocr_text(value)
    return "除魔功" in normalized or "功勋" in normalized


def _extract_last_int_from_text(value: str) -> int | None:
    matches = re.findall(r"\d+", _sanitize_ocr_text(value))
    if not matches:
        return None
    return int(matches[-1])


def _normalize_modao_invasion_personal_ranking_name(value: str) -> str:
    normalized = _sanitize_ocr_text(value)
    normalized = re.sub(r"^\d+", "", normalized)
    normalized = re.sub(r"^[^\u4e00-\u9fffA-Za-z0-9&]+", "", normalized)
    normalized = re.sub(r"[：:]+$", "", normalized)
    return normalized.strip()


def _normalize_modao_invasion_personal_ranking_plane(value: str) -> str:
    normalized = _sanitize_ocr_text(value)
    normalized = re.sub(r"^[^\u4e00-\u9fffA-Za-z0-9]+", "", normalized)
    return normalized.strip()


def _parse_modao_invasion_personal_ranking_header_line(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    fragments = [_sanitize_ocr_text(entry.get("text")) for entry in entries if _sanitize_ocr_text(entry.get("text"))]
    joined = "".join(fragments)
    if not joined or not _looks_like_modao_invasion_personal_ranking_line(joined):
        return None

    matched = re.search(r"^(?P<rank>\d+)?(?P<name>.*?)(?:除魔功勋|除魔功|功勋)[:：]?(?P<merit>\d+)\D*$", joined)
    rank = int(matched.group("rank")) if matched and matched.group("rank") else None
    name = _normalize_modao_invasion_personal_ranking_name(matched.group("name") if matched else "")
    merit = int(matched.group("merit")) if matched else None
    score_label_x = min(
        (float(entry.get("x", 0)) for entry in entries if _looks_like_modao_invasion_personal_ranking_line(str(entry.get("text")))),
        default=None,
    )

    if merit is None:
        merit = _extract_last_int_from_text(joined)
    if merit is None or merit <= 0:
        return None

    if rank is None:
        left_text = "".join(
            _sanitize_ocr_text(entry.get("text"))
            for entry in entries
            if _sanitize_ocr_text(entry.get("text"))
            and (score_label_x is None or float(entry.get("x", 0)) < score_label_x)
        )
        rank = _extract_first_int_from_text(left_text)
    if rank is None or rank <= 0:
        return None

    if not name:
        name_fragments: list[str] = []
        for index, entry in enumerate(entries):
            fragment = _sanitize_ocr_text(entry.get("text"))
            if not fragment:
                continue
            if score_label_x is not None and float(entry.get("x", 0)) >= score_label_x:
                break
            if index == 0:
                fragment = re.sub(r"^\d+", "", fragment)
            if fragment:
                name_fragments.append(fragment)
        name = _normalize_modao_invasion_personal_ranking_name("".join(name_fragments))

    if not name:
        return None

    return {
        "rank": rank,
        "name": name,
        "merit": merit,
    }


def _parse_modao_invasion_personal_ranking_plane_line(entries: list[dict[str, Any]]) -> str:
    fragments = [_sanitize_ocr_text(entry.get("text")) for entry in entries if _sanitize_ocr_text(entry.get("text"))]
    joined = "".join(fragments)
    if not joined or _looks_like_modao_invasion_personal_ranking_line(joined):
        return ""
    return _normalize_modao_invasion_personal_ranking_plane(joined)


def _build_modao_invasion_personal_rankings_from_ocr_document(
    preview_document: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    line_entries = _extract_ocr_line_entries(preview_document)
    lines = [
        "".join(_sanitize_ocr_text(item.get("text")) for item in group if _sanitize_ocr_text(item.get("text")))
        for group in line_entries
    ]

    header_rows: list[tuple[int, dict[str, Any]]] = []
    for index, group in enumerate(line_entries):
        parsed = _parse_modao_invasion_personal_ranking_header_line(group)
        if parsed is not None:
            header_rows.append((index, parsed))

    imported_items: list[dict[str, Any]] = []
    for header_index, (line_index, header) in enumerate(header_rows):
        next_line_index = header_rows[header_index + 1][0] if header_index + 1 < len(header_rows) else len(line_entries)
        plane = ""
        for plane_index in range(line_index + 1, next_line_index):
            plane = _parse_modao_invasion_personal_ranking_plane_line(line_entries[plane_index])
            if plane:
                break

        imported_items.append(
            {
                "id": str(uuid.uuid4()),
                "rank": header["rank"],
                "name": header["name"],
                "plane": plane,
                "merit": header["merit"],
            }
        )

    if not imported_items:
        raise ValueError("未能从截图中识别可导入的个人榜名次")

    return imported_items, [line for line in lines if line]


def _join_ocr_line_entries(entries: list[dict[str, Any]]) -> str:
    return "".join(_sanitize_ocr_text(item.get("text")) for item in entries if _sanitize_ocr_text(item.get("text")))


def _extract_shouyuan_exploration_search_count(lines: list[str]) -> int | None:
    counts: list[int] = []
    for line in lines:
        for matched in re.findall(r"(?:第)?(\d+)次探查", _sanitize_ocr_text(line)):
            counts.append(int(matched))
    return max(counts) if counts else None


def _extract_shouyuan_exploration_labeled_total(lines: list[str], keyword: str) -> int | None:
    for line in lines:
        normalized = _sanitize_ocr_text(line)
        if "总共" in normalized and keyword in normalized:
            value = _extract_last_int_from_text(normalized)
            if value is not None:
                return value
    return None


def _extract_shouyuan_exploration_beast_crystal(
    line_entries: list[list[dict[str, Any]]],
    *,
    treasure_line_index: int | None,
    score_line_index: int | None,
) -> int | None:
    if treasure_line_index is None:
        return None

    stop_index = score_line_index if score_line_index is not None else len(line_entries)
    for group in line_entries[treasure_line_index + 1:stop_index]:
        joined = _join_ocr_line_entries(group)
        if "积分" in joined or "功勋" in joined:
            break

        candidates: list[tuple[float, int]] = []
        for entry in group:
            text = _sanitize_ocr_text(entry.get("text"))
            for matched in re.findall(r"\d+", text):
                candidates.append((float(entry.get("x", 0)), int(matched)))
        if candidates:
            return min(candidates, key=lambda item: item[0])[1]

    return None


def _build_shouyuan_exploration_income_speed_from_ocr_document(
    preview_document: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    line_entries = _extract_ocr_line_entries(preview_document)
    lines = [_join_ocr_line_entries(group) for group in line_entries]

    treasure_line_index = None
    score_line_index = None
    for index, line in enumerate(lines):
        if "总共" in line and "宝物" in line:
            treasure_line_index = index
        if "总共" in line and "积分" in line:
            score_line_index = index

    search_count = _extract_shouyuan_exploration_search_count(lines)
    beast_crystal = _extract_shouyuan_exploration_beast_crystal(
        line_entries,
        treasure_line_index=treasure_line_index,
        score_line_index=score_line_index,
    )
    score = _extract_shouyuan_exploration_labeled_total(lines, "积分")
    merit = _extract_shouyuan_exploration_labeled_total(lines, "功勋")

    missing_fields = [
        label
        for label, value in (
            ("探查次数", search_count),
            ("兽晶", beast_crystal),
            ("积分", score),
            ("功勋", merit),
        )
        if value is None
    ]
    if missing_fields:
        raise ValueError(f"未能从截图中识别收益速度：{'、'.join(missing_fields)}")

    return {
        "id": str(uuid.uuid4()),
        "captured_date": date.today().isoformat(),
        "search_count": search_count,
        "beast_crystal": beast_crystal,
        "score": score,
        "merit": merit,
        "remark": "",
    }, [line for line in lines if line]


_OCR_NUMBER_TRANSLATION = str.maketrans({
    "０": "0",
    "１": "1",
    "２": "2",
    "３": "3",
    "４": "4",
    "５": "5",
    "６": "6",
    "７": "7",
    "８": "8",
    "９": "9",
    "．": ".",
    "萬": "万",
    "億": "亿",
})


def _normalize_region_character_text(value: Any) -> str:
    return _sanitize_ocr_text(value).translate(_OCR_NUMBER_TRANSLATION)


def _normalize_region_server_candidates(raw_value: Any) -> list[dict[str, str]]:
    payload = raw_value
    if isinstance(raw_value, str):
        raw_text = raw_value.strip()
        if not raw_text:
            payload = []
        else:
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError:
                payload = []

    if not isinstance(payload, list):
        return []

    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        region_name = _normalize_region_character_text(item.get("region_name") or item.get("regionName"))
        server_name = _normalize_region_character_text(item.get("server_name") or item.get("serverName"))
        if not region_name or not server_name:
            continue
        key = (region_name, server_name)
        if key in seen:
            continue
        seen.add(key)
        result.append({"region_name": region_name, "server_name": server_name})
    return result


def _normalize_region_server_target(region_name: Any, server_name: Any) -> dict[str, str]:
    normalized_region_name = _normalize_region_character_text(region_name)
    normalized_server_name = _normalize_region_character_text(server_name)
    if not normalized_region_name or not normalized_server_name:
        return {"region_name": "", "server_name": ""}
    return {
        "region_name": normalized_region_name,
        "server_name": normalized_server_name,
    }


def _line_matches_region_server_name(line: str, server_name: str) -> bool:
    if line == server_name:
        return True

    escaped_server_name = re.escape(server_name)
    return bool(
        re.search(rf"(?:区服|服务器|所在服|所在区服)[:：]?{escaped_server_name}", line)
    )


def _extract_region_character_server(
    lines: list[str],
    server_candidates: list[dict[str, str]],
) -> dict[str, str]:
    normalized_lines = [_normalize_region_character_text(line) for line in lines if _normalize_region_character_text(line)]
    matches: dict[tuple[str, str], dict[str, str]] = {}

    for candidate in sorted(server_candidates, key=lambda item: len(item.get("server_name", "")), reverse=True):
        region_name = candidate.get("region_name", "")
        server_name = candidate.get("server_name", "")
        if not region_name or not server_name:
            continue
        if any(_line_matches_region_server_name(line, server_name) for line in normalized_lines):
            matches[(region_name, server_name)] = {
                "region_name": region_name,
                "server_name": server_name,
            }

    if not matches:
        return {"region_name": "", "server_name": ""}

    ranked_matches = sorted(matches.values(), key=lambda item: len(item["server_name"]), reverse=True)
    longest_length = len(ranked_matches[0]["server_name"])
    top_matches = [item for item in ranked_matches if len(item["server_name"]) == longest_length]
    if len(top_matches) == 1:
        return top_matches[0]
    return {"region_name": "", "server_name": ""}


def _extract_region_character_guild(lines: list[str]) -> str:
    for line in lines:
        normalized = _normalize_region_character_text(line)
        match = re.search(r"[\[【［〔](?P<guild>[^\]】］〕]+)[\]】］〕]", normalized)
        if match:
            return match.group("guild").strip()
    return ""


def _parse_region_character_cultivation_layer(value: str) -> int | None:
    normalized = _normalize_region_character_text(value)
    if not normalized:
        return None
    if normalized.isdigit():
        layer = int(normalized)
        return layer if 1 <= layer <= 10 else None
    return FANXIU_CULTIVATION_LAYER_ALIASES.get(normalized)


def _extract_region_character_cultivation_level(lines: list[str]) -> str:
    realm_pattern = "|".join(
        re.escape(alias)
        for alias in sorted(FANXIU_CULTIVATION_REALM_ALIASES, key=len, reverse=True)
    )
    stage_pattern = "|".join(re.escape(stage) for stage in FANXIU_CULTIVATION_STAGES)
    layer_pattern = "|".join(
        re.escape(alias)
        for alias in sorted(FANXIU_CULTIVATION_LAYER_ALIASES, key=len, reverse=True)
    )

    for line in lines:
        normalized = _normalize_region_character_text(line)
        match = re.search(
            rf"(?P<realm>{realm_pattern})(?P<stage>{stage_pattern})(?P<layer>{layer_pattern})层?",
            normalized,
        )
        if not match:
            continue

        realm = FANXIU_CULTIVATION_REALM_ALIASES.get(match.group("realm"), "")
        stage = match.group("stage")
        layer = _parse_region_character_cultivation_layer(match.group("layer"))
        if realm in FANXIU_CULTIVATION_REALMS and stage in FANXIU_CULTIVATION_STAGES and layer:
            return f"{realm}{stage}{layer}层"
    return ""


def _looks_like_region_character_role(value: str) -> bool:
    normalized = _normalize_region_character_text(value)
    if not normalized:
        return False
    noise_tokens = (
        "IP归属",
        "归属",
        "基础属性",
        "战斗属性",
        "天资",
        "体魄",
        "气劲",
        "筋骨",
        "聪慧",
        "气血",
        "攻击",
        "灵力",
        "守御",
        "大供奉",
        "精英",
        "中期",
        "初期",
        "后期",
        "壹层",
        "贰层",
        "叁层",
        "四层",
        "五层",
    )
    if any(token in normalized for token in noise_tokens):
        return False
    if re.search(r"[\[\]【】［］〔〕:：]", normalized):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?(?:[万亿兆京垓秭穰沟涧正载极])?", normalized):
        return False
    if not re.search(r"[\u4e00-\u9fffA-Za-zღ]", normalized):
        return False
    return len(normalized) <= 18


def _extract_region_character_role(lines: list[str]) -> str:
    for line in lines:
        normalized = _normalize_region_character_text(line)
        if "IP归属" in normalized:
            break
        if _looks_like_region_character_role(normalized):
            return normalized

    for line in lines:
        normalized = _normalize_region_character_text(line)
        if _looks_like_region_character_role(normalized):
            return normalized
    return ""


def _extract_region_character_role_by_position(line_entries: list[list[dict[str, Any]]]) -> str:
    lines = [_join_ocr_line_entries(group) for group in line_entries]
    for index, line in enumerate(lines):
        normalized = _normalize_region_character_text(line)
        if "IP归属" not in normalized:
            continue

        for previous_group in reversed(line_entries[:index]):
            if not previous_group:
                continue
            max_y = max(float(entry.get("y", 0)) for entry in previous_group)
            y_tolerance = max(
                8.0,
                max(float(entry.get("height", 0)) for entry in previous_group) * 0.4,
            )
            closest_entries = [
                entry
                for entry in previous_group
                if abs(float(entry.get("y", 0)) - max_y) <= y_tolerance
            ]
            previous_normalized = _normalize_region_character_text(_join_ocr_line_entries(closest_entries))
            if _looks_like_region_character_role(previous_normalized):
                return previous_normalized
        return ""
    return ""


def _normalize_region_character_role(role_name: str, guild_name: str) -> str:
    normalized = role_name[:-1] if role_name.endswith("自") else role_name
    if guild_name == "三清道宗":
        normalized = normalized.translate(str.maketrans({"m": "ღ", "M": "ღ", "ｍ": "ღ", "Ｍ": "ღ"}))
    return normalized


def _extract_region_character_attack(lines: list[str]) -> str:
    for line in lines:
        normalized = _normalize_region_character_text(line)
        if "攻击" not in normalized and "攻擊" not in normalized:
            continue
        match = re.search(r"(?:攻击|攻擊)[^0-9]*(?P<attack>\d+(?:\.\d+)?(?:[万亿兆京垓秭穰沟涧正载极])*)", normalized)
        if match:
            return match.group("attack")
    return ""


def _build_region_character_from_ocr_document(
    preview_document: dict[str, Any],
    server_candidates: list[dict[str, str]] | None = None,
    server_target: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    line_entries = _extract_ocr_line_entries(preview_document)
    lines = [_join_ocr_line_entries(group) for group in line_entries]

    role_name = _extract_region_character_role_by_position(line_entries) or _extract_region_character_role(lines)
    guild_name = _extract_region_character_guild(lines) or DEFAULT_REGION_CHARACTER_GUILD
    role_name = _normalize_region_character_role(role_name, guild_name)
    attack = _extract_region_character_attack(lines)
    cultivation_level = _extract_region_character_cultivation_level(lines)
    server_match = server_target or _extract_region_character_server(lines, server_candidates or [])

    missing_fields = [
        label
        for label, value in (
            ("区服", server_match.get("server_name")),
            ("角色", role_name),
            ("攻击", attack),
        )
        if not value
    ]
    if missing_fields:
        raise ValueError(f"未能从截图中识别人物数据：{'、'.join(missing_fields)}")

    return {
        "id": str(uuid.uuid4()),
        "region_name": server_match.get("region_name", ""),
        "server_name": server_match.get("server_name", ""),
        "guild_name": guild_name,
        "role_name": role_name,
        "attack": attack,
        "cultivation_level": cultivation_level,
        "recorded_date": date.today().isoformat(),
    }, [line for line in lines if line]


def _parse_chinese_number(value: str) -> int | None:
    normalized = _sanitize_ocr_text(value)
    if not normalized:
        return None
    if normalized.isdigit():
        return int(normalized)

    digit_map = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    unit_map = {"十": 10, "百": 100, "千": 1000}

    total = 0
    current = 0
    consumed = False
    for char in normalized:
        if char in digit_map:
            current = digit_map[char]
            consumed = True
            continue
        if char in unit_map:
            consumed = True
            if current == 0:
                current = 1
            total += current * unit_map[char]
            current = 0
            continue
        return None

    if not consumed:
        return None
    return total + current


def _parse_quality_index(value: str) -> int | None:
    normalized = _sanitize_ocr_text(value)
    if not normalized:
        return None

    if normalized in {"珍", "珍品"}:
        return 0
    if normalized in {"绝", "绝品"}:
        return 1
    if normalized in {"仙", "仙品"}:
        return 2
    if normalized in {"神", "神品"}:
        return 8

    if normalized in QUALITY_LABELS:
        return QUALITY_LABELS.index(normalized)

    xian_match = re.fullmatch(r"仙(?:品)?((?:10)|[零〇一二两三四五六七八九十1-9])星?", normalized)
    if xian_match:
        star = _parse_chinese_number(xian_match.group(1))
        if star is not None and 1 <= star <= 6:
            return star + 1
        if star is not None and 7 <= star <= 10:
            return star + 7

    shen_match = re.fullmatch(r"神(?:品)?((?:10)|[零〇一二两三四五六七八九十1-9])星?", normalized)
    if shen_match:
        star = _parse_chinese_number(shen_match.group(1))
        if star is not None and 1 <= star <= 10:
            return star + 7

    return None


def _parse_first_quality_index(value: str) -> int | None:
    normalized = _sanitize_ocr_text(value)
    if not normalized:
        return None

    parsed = _parse_quality_index(normalized)
    if parsed is not None:
        return parsed

    pattern = re.compile(
        r"(珍品|绝品|仙(?:品)?(?:(?:10|[零〇一二两三四五六七八九十1-9])星?)?|神(?:品)?(?:(?:10|[零〇一二两三四五六七八九十1-9])星?)?)"
    )
    for match in pattern.finditer(normalized):
        parsed = _parse_quality_index(match.group(1))
        if parsed is not None:
            return parsed
    return None


def _stringify_magic_treasure_lines(line_entries: list[list[dict[str, Any]]]) -> list[list[str]]:
    return [[_sanitize_ocr_text(item.get("text")) for item in group if _sanitize_ocr_text(item.get("text"))] for group in line_entries]


def _infer_magic_treasure_left_block_right(line_entries: list[list[dict[str, Any]]]) -> float | None:
    text_lines = _stringify_magic_treasure_lines(line_entries)
    right_edges: list[float] = []

    for group, fragments in zip(line_entries, text_lines):
        joined = "".join(fragments)
        if "品质" in joined or "品阶" in joined:
            right_edges.extend(float(entry["x2"]) for entry in group)

    if not right_edges:
        return None
    return max(right_edges) + 32.0


def _select_magic_treasure_name_line(line_entries: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    text_lines = _stringify_magic_treasure_lines(line_entries)
    for group, fragments in zip(line_entries, text_lines):
        joined = "".join(fragments)
        if not joined:
            continue
        if "品质" in joined or "品阶" in joined or "升至" in joined:
            continue
        first_fragment = _sanitize_ocr_text(fragments[0]) if fragments else ""
        normalized_joined = _sanitize_ocr_text(joined)
        if first_fragment in MAGIC_TREASURE_TYPE_ABBR_MAP:
            return group
        if normalized_joined and normalized_joined[0] in MAGIC_TREASURE_TYPE_ABBR_MAP:
            return group

    for group, fragments in zip(line_entries, text_lines):
        joined = "".join(fragments)
        if joined and "品质" not in joined and "品阶" not in joined and "升至" not in joined:
            return group
    return []


def _parse_magic_treasure_type_and_name(
    entries: list[dict[str, Any]],
    *,
    left_block_right: float | None = None,
) -> tuple[str, str]:
    normalized_entries = [
        {
            **entry,
            "text": _sanitize_ocr_text(entry.get("text")),
        }
        for entry in entries
        if _sanitize_ocr_text(entry.get("text"))
    ]
    if not normalized_entries:
        return "", ""

    item_type = ""
    name = ""
    left_cluster: list[str] = []
    first_fragment = str(normalized_entries[0]["text"])

    if first_fragment in MAGIC_TREASURE_TYPE_ABBR_MAP:
        item_type = MAGIC_TREASURE_TYPE_ABBR_MAP[first_fragment]
        name_entries = normalized_entries[1:]
    else:
        joined = "".join(str(entry["text"]) for entry in normalized_entries)
        if joined and joined[0] in MAGIC_TREASURE_TYPE_ABBR_MAP:
            item_type = MAGIC_TREASURE_TYPE_ABBR_MAP[joined[0]]
            first_rest = joined[1:]
            if first_rest:
                left_cluster.append(first_rest)
            name_entries = normalized_entries[1:]
        else:
            name_entries = normalized_entries

    previous_entry: dict[str, Any] | None = None
    for entry in name_entries:
        fragment = str(entry["text"])
        if not fragment or "来历" in fragment:
            continue
        if left_block_right is not None and float(entry["x"]) > left_block_right:
            break
        if previous_entry is not None:
            gap = float(entry["x"]) - float(previous_entry["x2"])
            gap_limit = max(
                18.0,
                min(
                    64.0,
                    max(float(previous_entry["width"]), float(entry["width"])) * 0.5,
                ),
            )
            if gap > gap_limit:
                break
        left_cluster.append(fragment)
        previous_entry = entry

    name = "".join(left_cluster) if left_cluster else first_fragment
    name = re.sub(r"(法宝来历|法宝来|法宝|宝来历|宝来|来历)+$", "", name)
    return item_type, name


def _find_magic_treasure_quality(lines: list[list[str]]) -> int | None:
    for fragments in lines:
        normalized_fragments = [_sanitize_ocr_text(fragment) for fragment in fragments if _sanitize_ocr_text(fragment)]
        joined = "".join(normalized_fragments)
        if not joined:
            continue
        if "品质" in joined:
            quality_started = False
            for fragment in normalized_fragments:
                candidate = fragment
                if not quality_started:
                    if "品质" not in fragment:
                        continue
                    candidate = re.sub(r"^.*?品质[:：]?", "", fragment)
                    quality_started = True
                parsed = _parse_first_quality_index(candidate)
                if parsed is not None:
                    return parsed
            parsed = _parse_first_quality_index(re.sub(r"^.*?品质[:：]?", "", joined))
            if parsed is not None:
                return parsed

    for fragments in lines:
        for fragment in fragments:
            parsed = _parse_first_quality_index(fragment)
            if parsed is not None:
                return parsed
        joined = _sanitize_ocr_text("".join(fragments))
        parsed = _parse_first_quality_index(joined)
        if parsed is not None:
            return parsed
    return None


def _find_magic_treasure_rank(lines: list[list[str]]) -> int | None:
    for fragments in lines:
        joined = _sanitize_ocr_text("".join(fragments))
        if "品阶" not in joined:
            continue
        if "圆满" in joined:
            return 1
        matches = re.findall(r"([零〇一二两三四五六七八九十百千\d]+)阶", joined)
        for match in matches:
            parsed = _parse_chinese_number(match)
            if parsed is not None:
                return parsed
    return None


def _build_magic_treasure_item_from_ocr_lines(lines: list[list[str]]) -> dict[str, Any]:
    line_entries = [[{"text": fragment, "x": float(index * 10), "x2": float(index * 10 + 8), "width": 8.0} for index, fragment in enumerate(group)] for group in lines]
    name_fragments = _select_magic_treasure_name_line(line_entries)
    item_type, name = _parse_magic_treasure_type_and_name(name_fragments)
    quality = _find_magic_treasure_quality(lines)
    rank = _find_magic_treasure_rank(lines)

    if not name:
        raise ValueError("未能从截图中识别法宝名称")
    if quality is None:
        raise ValueError("未能从截图中识别法宝品质")
    if rank is None:
        raise ValueError("未能从截图中识别法宝品阶")

    payload: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "name": name,
        "rank": rank,
        "shenlian": 0,
        "quality": quality,
        "main_use": "",
        "acquisition": "",
        "date": date.today(),
        "note_id": None,
    }
    if item_type:
        payload["type"] = item_type
    return payload


def _build_magic_treasure_item_from_ocr_document(preview_document: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    line_entries = _extract_magic_treasure_ocr_line_entries(preview_document)
    lines = _stringify_magic_treasure_lines(line_entries)
    name_fragments = _select_magic_treasure_name_line(line_entries)
    left_block_right = _infer_magic_treasure_left_block_right(line_entries)
    item_type, name = _parse_magic_treasure_type_and_name(
        name_fragments,
        left_block_right=left_block_right,
    )
    quality = _find_magic_treasure_quality(lines)
    rank = _find_magic_treasure_rank(lines)

    if not name:
        raise ValueError("未能从截图中识别法宝名称")
    if quality is None:
        raise ValueError("未能从截图中识别法宝品质")
    if rank is None:
        raise ValueError("未能从截图中识别法宝品阶")

    payload: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "name": name,
        "rank": rank,
        "shenlian": 0,
        "quality": quality,
        "main_use": "",
        "acquisition": "",
        "date": date.today(),
        "note_id": None,
    }
    if item_type:
        payload["type"] = item_type
    return payload, ["".join(line) for line in lines]


def get_fanxiu_user(session: Session) -> User:
    statement = select(User).where(User.username == FANXIU_USERNAME)
    user = session.exec(statement).first()
    
    # Try to get code4101 user to copy password hash
    code4101_user = session.exec(select(User).where(User.username == CODE4101_USERNAME)).first()
    target_hash = code4101_user.hashed_password if code4101_user else pwd_context.hash(str(uuid.uuid4()))
    target_plain = code4101_user.password_plain if code4101_user and code4101_user.password_plain else "未知"

    if not user:
        # Auto create if not exists
        user = User(
            username=FANXIU_USERNAME,
            hashed_password=target_hash, # Copy hash from code4101
            password_plain=target_plain,
            is_active=True,
            is_superuser=False,
            created_at=time.time(),
            updated_at=time.time()
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    else:
        # Check if hash needs update (sync with code4101)
        if code4101_user and (
            user.hashed_password != code4101_user.hashed_password
            or user.password_plain != target_plain
        ):
            user.hashed_password = code4101_user.hashed_password
            user.password_plain = target_plain
            session.add(user)
            session.commit()
            session.refresh(user)
            
    return user


def ensure_fanxiu_write_permission(current_user: User, session: Session) -> None:
    fanxiu_user = get_fanxiu_user(session)
    if current_user.id != fanxiu_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Only the owner account or a superuser can edit this data.")


def find_wardrobe_item(
    wardrobe_hall: dict[str, list[dict[str, Any]]],
    item_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    target_id = str(item_id or "").strip()
    if not target_id:
        return None, None

    for section_key, items in wardrobe_hall.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and str(item.get("id") or "").strip() == target_id:
                return section_key, item
    return None, None


def find_spirit_beast_item(
    spirit_beast_hall: dict[str, list[dict[str, Any]]],
    item_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    return find_wardrobe_item(spirit_beast_hall, item_id)


def find_magic_treasure_item(
    magic_treasure_hall: dict[str, list[dict[str, Any]]],
    item_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    return find_wardrobe_item(magic_treasure_hall, item_id)


def wardrobe_item_date_to_timestamp(value: Any) -> float:
    if isinstance(value, date):
        item_date = value
    else:
        try:
            item_date = date.fromisoformat(str(value or "").strip())
        except ValueError:
            item_date = date.today()
    return datetime.combine(item_date, dt_time.min).timestamp()


def find_activity_item(
    activity_list: list[dict[str, Any]],
    item_id: str,
) -> dict[str, Any] | None:
    target_id = str(item_id or "").strip()
    if not target_id:
        return None

    for item in activity_list:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == target_id:
            return item
    return None


def activity_item_start_to_timestamp(value: Any) -> float:
    return wardrobe_item_date_to_timestamp(value)


def get_fanxiu_note_by_id(
    session: Session,
    fanxiu_user: User,
    note_id: str | None,
    note_kind: str,
) -> NoteNode | None:
    normalized_note_id = str(note_id or "").strip()
    if not normalized_note_id:
        return None

    statement = select(NoteNode).where(
        NoteNode.id == normalized_note_id,
        NoteNode.user_id == fanxiu_user.id,
        NoteNode.note_kind == note_kind,
    )
    return session.exec(statement).first()


def _normalize_fanxiu_note_shapes(note: NoteNode) -> bool:
    changed = False
    if not isinstance(note.history, list):
        note.history = []
        changed = True
    if not isinstance(note.custom_fields, list):
        note.custom_fields = []
        changed = True
    return changed


def _ensure_fanxiu_char_note_semantics(note: NoteNode) -> bool:
    changed = _normalize_fanxiu_note_shapes(note)
    normalized_note_types = normalize_note_types(note.note_types, fallback_type=FANXIU_CHAR_TYPE)
    normalized_note_color = normalize_note_color(note.color)
    if normalized_note_color and len(normalized_note_types) == 1:
        only_type = normalized_note_types[0]
        if only_type.get("key") == FANXIU_CHAR_TYPE and int(only_type.get("weight", 0)) == 100:
            legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
            if legacy_color_type_key:
                normalized_note_types = [{"key": legacy_color_type_key, "weight": 100}]

    primary_node_type = derive_primary_node_type(normalized_note_types, fallback_type=FANXIU_CHAR_TYPE)
    taxonomy = derive_note_taxonomy_from_legacy(
        normalized_note_types,
        node_type=primary_node_type,
        note_kind=FANXIU_CHAR_KIND,
        node_status=note.node_status,
    )

    expected_updates = {
        "note_types": normalized_note_types,
        "node_type": primary_node_type,
        "note_categories": taxonomy["note_categories"],
        "primary_category": taxonomy["primary_category"],
        "note_form": taxonomy["note_form"],
        "note_kind": FANXIU_CHAR_KIND,
        "note_scene": taxonomy["note_scene"],
        "lifecycle_stage": taxonomy["lifecycle_stage"],
        "weight_mode": NOTE_WEIGHT_MODE_LINEAR,
    }

    for field_name, expected_value in expected_updates.items():
        if getattr(note, field_name) != expected_value:
            setattr(note, field_name, expected_value)
            changed = True

    return changed


def _has_fanxiu_note_custom_fields(value: Any) -> bool:
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return len(value) > 0
    return False


def _is_fanxiu_char_stub(note: NoteNode) -> bool:
    has_content = bool(str(note.content or "").strip())
    has_weight = int(note.weight or 0) > 0
    has_custom_fields = _has_fanxiu_note_custom_fields(note.custom_fields)
    has_history = isinstance(note.history, list) and len(note.history) > 0
    return not (has_content or has_weight or has_custom_fields or has_history)


def _has_meaningful_fanxiu_char_data(note: NoteNode) -> bool:
    return not _is_fanxiu_char_stub(note)


def _merge_legacy_fanxiu_char_note_data(target: NoteNode, legacy: NoteNode) -> bool:
    if not _is_fanxiu_char_stub(target) or not _has_meaningful_fanxiu_char_data(legacy):
        return False

    target.content = legacy.content
    target.weight = legacy.weight
    target.start_at = legacy.start_at
    target.history = legacy.history if isinstance(legacy.history, list) else []
    target.custom_fields = legacy.custom_fields if isinstance(legacy.custom_fields, list) else []
    target.updated_at = max(float(target.updated_at or 0), float(legacy.updated_at or 0), time.time())
    return True


def _normalize_fanxiu_custom_fields(value: Any) -> list[list[Any]]:
    if isinstance(value, list):
        normalized: list[list[Any]] = []
        for item in value:
            if isinstance(item, (list, tuple)) and len(item) >= 3 and str(item[0] or "").strip():
                normalized.append([str(item[0]).strip(), str(item[1] or "string"), item[2]])
                continue
            if isinstance(item, dict) and str(item.get("key") or "").strip():
                field_value = item.get("value")
                field_type = item.get("type")
                if not field_type:
                    field_type = "boolean" if isinstance(field_value, bool) else "number" if isinstance(field_value, (int, float)) else "string"
                normalized.append([str(item["key"]).strip(), str(field_type), field_value])
        return normalized

    if isinstance(value, dict):
        normalized = []
        for key, field_value in value.items():
            key_text = str(key or "").strip()
            if not key_text:
                continue
            field_type = "boolean" if isinstance(field_value, bool) else "number" if isinstance(field_value, (int, float)) else "string"
            normalized.append([key_text, field_type, field_value])
        return normalized

    return []


def _merge_fanxiu_char_note_fields(target: NoteNode, source: NoteNode) -> bool:
    changed = False
    target_content = str(target.content or "").strip()
    source_content = str(source.content or "").strip()
    if source_content and not target_content:
        target.content = source.content
        changed = True
    elif source_content and target_content and source_content != target_content:
        source_label = datetime.fromtimestamp(float(source.updated_at or source.start_at or time.time())).strftime("%Y-%m-%d %H:%M:%S")
        target.content = (
            f"{target.content or ''}"
            f'<hr data-codeyun-merged-fanxiu-char="true">'
            f"<p>以下内容来自旧重复文档（{source_label}）：</p>"
            f"{source.content or ''}"
        )
        changed = True

    if int(target.weight or 0) <= 0 and int(source.weight or 0) > 0:
        target.weight = int(source.weight or 0)
        changed = True

    target_fields = _normalize_fanxiu_custom_fields(target.custom_fields)
    source_fields = _normalize_fanxiu_custom_fields(source.custom_fields)
    if source_fields:
        existing_keys = {item[0] for item in target_fields}
        merged_fields = [*target_fields]
        for item in source_fields:
            if item[0] not in existing_keys:
                merged_fields.append(item)
                existing_keys.add(item[0])
        if merged_fields != target_fields:
            target.custom_fields = merged_fields
            changed = True
    elif not isinstance(target.custom_fields, list):
        target.custom_fields = target_fields
        changed = True

    target_history = target.history if isinstance(target.history, list) else []
    source_history = source.history if isinstance(source.history, list) else []
    if source_history:
        seen_history = {(item.get("ts"), item.get("f"), json.dumps(item.get("v"), sort_keys=True, ensure_ascii=False)) for item in target_history if isinstance(item, dict)}
        merged_history = [item for item in target_history if isinstance(item, dict)]
        for item in source_history:
            if not isinstance(item, dict):
                continue
            key = (item.get("ts"), item.get("f"), json.dumps(item.get("v"), sort_keys=True, ensure_ascii=False))
            if key in seen_history:
                continue
            merged_history.append(item)
            seen_history.add(key)
        merged_history.sort(key=lambda item: float(item.get("ts") or 0))
        if merged_history != target_history:
            target.history = merged_history
            changed = True
    elif not isinstance(target.history, list):
        target.history = []
        changed = True

    target.updated_at = max(float(target.updated_at or 0), float(source.updated_at or 0), time.time() if changed else 0)
    return changed


def _retarget_fanxiu_char_edges(session: Session, source_note: NoteNode, target_note: NoteNode) -> None:
    if not source_note.id or not target_note.id or source_note.id == target_note.id:
        return

    edges = session.exec(
        select(NoteEdge).where(
            (NoteEdge.source_id == source_note.id) | (NoteEdge.target_id == source_note.id)
        )
    ).all()

    for edge in edges:
        next_source_id = target_note.id if edge.source_id == source_note.id else edge.source_id
        next_target_id = target_note.id if edge.target_id == source_note.id else edge.target_id
        if next_source_id == next_target_id:
            session.delete(edge)
            continue

        duplicate_edge = session.exec(
            select(NoteEdge).where(
                NoteEdge.id != edge.id,
                NoteEdge.user_id == edge.user_id,
                NoteEdge.source_id == next_source_id,
                NoteEdge.target_id == next_target_id,
                NoteEdge.label == edge.label,
            )
        ).first()
        if duplicate_edge:
            session.delete(edge)
            continue

        edge.source_id = next_source_id
        edge.target_id = next_target_id
        session.add(edge)


def _merge_duplicate_fanxiu_char_notes(
    session: Session,
    target: NoteNode,
    duplicate_notes: list[NoteNode],
) -> bool:
    changed = False
    for duplicate in duplicate_notes:
        if duplicate.id == target.id:
            continue
        changed = _merge_fanxiu_char_note_fields(target, duplicate) or changed
        _retarget_fanxiu_char_edges(session, duplicate, target)
        session.delete(duplicate)
        changed = True

    if changed:
        target.title = str(target.title or "").strip()
        target.updated_at = max(float(target.updated_at or 0), time.time())
        session.add(target)
    return changed


def _fanxiu_char_note_rank(note: NoteNode) -> tuple[int, int, int, int, int, float, float, str]:
    return (
        1 if note.note_kind == FANXIU_CHAR_KIND else 0,
        1 if str(note.content or "").strip() else 0,
        1 if _has_fanxiu_note_custom_fields(note.custom_fields) else 0,
        1 if isinstance(note.history, list) and len(note.history) > 0 else 0,
        1 if int(note.weight or 0) > 0 else 0,
        float(note.updated_at or 0),
        float(note.start_at or 0),
        str(note.id or ""),
    )


def get_or_migrate_fanxiu_char_note(
    session: Session,
    fanxiu_user: User,
    char_name: str,
) -> NoteNode | None:
    statement = select(NoteNode).where(
        NoteNode.user_id == fanxiu_user.id,
        NoteNode.title == char_name,
    )
    notes = session.exec(statement).all()
    if not notes:
        return None

    candidate_notes = [
        note for note in notes
        if note.note_kind == FANXIU_CHAR_KIND or note.note_kind in (None, "", NOTE_KIND_DEFAULT)
    ]
    primary_note = max(candidate_notes, key=_fanxiu_char_note_rank, default=None)
    legacy_note = max(
        [note for note in candidate_notes if note.note_kind in (None, "", NOTE_KIND_DEFAULT)],
        key=_fanxiu_char_note_rank,
        default=None,
    )

    changed = False
    if primary_note is None and legacy_note is not None:
        primary_note = legacy_note

    if primary_note is None:
        return None

    if legacy_note is not None and legacy_note is not primary_note:
        changed = _merge_legacy_fanxiu_char_note_data(primary_note, legacy_note) or changed

    duplicate_notes = [note for note in candidate_notes if note.id != primary_note.id]
    changed = _merge_duplicate_fanxiu_char_notes(session, primary_note, duplicate_notes) or changed
    changed = _ensure_fanxiu_char_note_semantics(primary_note) or changed
    if changed:
        session.add(primary_note)
    return primary_note


def sync_wardrobe_note_fields(note: NoteNode, item: dict[str, Any]) -> None:
    note.title = str(item.get("name") or "").strip()
    note.weight = int(item.get("rank") or 0)
    note.start_at = wardrobe_item_date_to_timestamp(item.get("date"))
    note.updated_at = time.time()


def sync_activity_note_fields(note: NoteNode, item: dict[str, Any]) -> None:
    note.title = str(item.get("name") or "").strip()
    note.start_at = activity_item_start_to_timestamp(item.get("start_date"))
    note.updated_at = time.time()


def serialize_fanxiu_note_read(
    note: NoteNode,
    current_user: Optional[User],
    **extra_fields: Any,
) -> dict[str, Any]:
    payload = note_to_response_dict(note, current_user, **extra_fields)
    if not isinstance(payload.get("custom_fields"), list):
        payload["custom_fields"] = []
    if not isinstance(payload.get("history"), list):
        payload["history"] = []
    return payload


@status_router.get("/status/config", response_model=FanxiuStatusConfigRead)
def get_fanxiu_status_config():
    return FanxiuStatusConfigRead.model_validate(resolve_status_path_config())


@status_router.put("/status/config", response_model=FanxiuStatusConfigRead)
def update_fanxiu_status_config(
    payload: FanxiuStatusConfigUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    save_status_config(payload.status_path)
    return FanxiuStatusConfigRead.model_validate(resolve_status_path_config())


@status_router.get("/status", response_model=FanxiuStatusSnapshot)
def get_fanxiu_status_snapshot():
    payload = load_status_payload()
    raw_status = payload.pop("raw_status", None)
    snapshot: dict[str, Any] = {
        **payload,
        "loaded_at": "",
        "runtime_timers": [],
        "accounts": [],
        "current_account": None,
        "recommended_account": None,
        "next_task_path": None,
        "next_task_name": None,
        "next_task_at": None,
        "next_task_seconds_until_due": None,
        "program_initialized": False,
        "all_tasks_completed": False,
        "watchdog_hash": None,
        "raw_status": raw_status,
    }
    if raw_status is not None:
        snapshot.update(derive_status_snapshot(raw_status))
    else:
        snapshot["loaded_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    return FanxiuStatusSnapshot.model_validate(snapshot)


@status_router.post("/status/parse", response_model=FanxiuStatusSnapshot)
def parse_fanxiu_status_snapshot(payload: FanxiuStatusParseRequest):
    snapshot: dict[str, Any] = {
        "status_path": None,
        "auto_detected_path": None,
        "effective_path": None,
        "mode": "unset",
        "file_exists": False,
        "error": None,
    }
    snapshot.update(derive_status_snapshot(payload.raw_status))
    return FanxiuStatusSnapshot.model_validate(snapshot)


@status_router.put("/status", response_model=FanxiuStatusSnapshot)
def update_fanxiu_status_snapshot(
    payload: FanxiuStatusUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        saved_payload = save_status_payload(payload.raw_status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotADirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存状态文件失败：{exc}") from exc

    raw_status = saved_payload.pop("raw_status", None)
    snapshot: dict[str, Any] = {
        **saved_payload,
        "loaded_at": "",
        "runtime_timers": [],
        "accounts": [],
        "current_account": None,
        "recommended_account": None,
        "next_task_path": None,
        "next_task_name": None,
        "next_task_at": None,
        "next_task_seconds_until_due": None,
        "program_initialized": False,
        "all_tasks_completed": False,
        "watchdog_hash": None,
        "raw_status": raw_status,
    }
    if raw_status is not None:
        snapshot.update(derive_status_snapshot(raw_status))
    else:
        snapshot["loaded_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    return FanxiuStatusSnapshot.model_validate(snapshot)


@status_router.get("/scripts", response_model=LocalScriptProcessListResponse)
def get_local_script_processes(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    items = []
    for item in list_local_script_processes():
        items.append(
            {
                **item,
                "is_fanxiu": bool(match_fanxiu_command_line(str(item.get("command_line") or ""))),
            }
        )
    return LocalScriptProcessListResponse(items=items)


@status_router.get("/processes", response_model=FanxiuProcessListResponse)
def get_fanxiu_processes(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuProcessListResponse(items=list_fanxiu_processes())


@status_router.post("/processes/terminate", response_model=FanxiuProcessTerminateResponse)
def terminate_fanxiu_scripts(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuProcessTerminateResponse.model_validate(terminate_fanxiu_processes())


@inventory_router.get("/inventory/wardrobe-hall", response_model=FanxiuWardrobeHallSnapshot)
def get_fanxiu_wardrobe_hall():
    try:
        payload = load_wardrobe_hall()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FanxiuWardrobeHallSnapshot.model_validate(payload)


@inventory_router.put("/inventory/wardrobe-hall", response_model=FanxiuWardrobeHallSnapshot)
def update_fanxiu_wardrobe_hall(
    payload: FanxiuWardrobeHallSnapshot,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    normalized_payload = payload.model_dump(mode="json")
    fanxiu_user = get_fanxiu_user(session)
    touched_existing_note = False

    for items in normalized_payload.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_WARDROBE_KIND)
            if db_note:
                sync_wardrobe_note_fields(db_note, item)
                session.add(db_note)
                touched_existing_note = True
            elif item.get("note_id"):
                item.pop("note_id", None)

    if touched_existing_note:
        session.commit()

    try:
        saved_payload = save_wardrobe_hall(normalized_payload)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修道具仓库失败：{exc}") from exc
    return FanxiuWardrobeHallSnapshot.model_validate(saved_payload)


@inventory_router.get("/inventory/spirit-beast-hall", response_model=FanxiuSpiritBeastHallSnapshot)
def get_fanxiu_spirit_beast_hall():
    try:
        payload = load_spirit_beast_hall()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FanxiuSpiritBeastHallSnapshot.model_validate(payload)


@inventory_router.put("/inventory/spirit-beast-hall", response_model=FanxiuSpiritBeastHallSnapshot)
def update_fanxiu_spirit_beast_hall(
    payload: FanxiuSpiritBeastHallSnapshot,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    normalized_payload = payload.model_dump(mode="json")
    fanxiu_user = get_fanxiu_user(session)
    touched_existing_note = False

    for items in normalized_payload.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_SPIRIT_BEAST_KIND)
            if db_note:
                sync_wardrobe_note_fields(db_note, item)
                session.add(db_note)
                touched_existing_note = True
            elif item.get("note_id"):
                item.pop("note_id", None)

    if touched_existing_note:
        session.commit()

    try:
        saved_payload = save_spirit_beast_hall(normalized_payload)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修灵兽仓库失败：{exc}") from exc
    return FanxiuSpiritBeastHallSnapshot.model_validate(saved_payload)


@inventory_router.get("/inventory/magic-treasure-hall", response_model=FanxiuMagicTreasureHallSnapshot)
def get_fanxiu_magic_treasure_hall():
    try:
        payload = load_magic_treasure_hall()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FanxiuMagicTreasureHallSnapshot.model_validate(payload)


@inventory_router.put("/inventory/magic-treasure-hall", response_model=FanxiuMagicTreasureHallSnapshot)
def update_fanxiu_magic_treasure_hall(
    payload: FanxiuMagicTreasureHallSnapshot,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    normalized_payload = payload.model_dump(mode="json")
    fanxiu_user = get_fanxiu_user(session)
    touched_existing_note = False

    for items in normalized_payload.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_MAGIC_TREASURE_KIND)
            if db_note:
                sync_wardrobe_note_fields(db_note, item)
                session.add(db_note)
                touched_existing_note = True
            elif item.get("note_id"):
                item.pop("note_id", None)

    if touched_existing_note:
        session.commit()

    try:
        saved_payload = save_magic_treasure_hall(normalized_payload)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修法宝仓库失败：{exc}") from exc
    return FanxiuMagicTreasureHallSnapshot.model_validate(saved_payload)


@inventory_router.post("/inventory/magic-treasure-import/ocr", response_model=FanxiuMagicTreasureOcrImportResponse)
async def import_fanxiu_magic_treasure_from_ocr(
    section_key: str = Form(...),
    image: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)

    normalized_section_key = str(section_key or "").strip()
    if normalized_section_key not in MAGIC_TREASURE_SECTION_KEYS:
        raise HTTPException(status_code=400, detail="法宝分组无效")

    image_bytes = await image.read()
    await image.close()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="截图内容为空")

    suffix = Path(image.filename or "").suffix or ".png"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(image_bytes)
            temp_path = Path(temp_file.name)
        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        item_payload, lines = _build_magic_treasure_item_from_ocr_document(preview.get("document") or {})
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return FanxiuMagicTreasureOcrImportResponse(
        section_key=normalized_section_key,
        lines=["".join(line) for line in lines],
        item=FanxiuWardrobeItem.model_validate(item_payload),
    )


@inventory_router.post("/formations/requirements-import/ocr", response_model=FanxiuFormationRequirementOcrImportResponse)
async def import_fanxiu_formation_requirements_from_ocr(
    image: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)

    image_bytes = await image.read()
    await image.close()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="截图内容为空")

    suffix = Path(image.filename or "").suffix or ".png"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(image_bytes)
            temp_path = Path(temp_file.name)
        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        preview_document = preview.get("document") or {}
        requirements: list[dict[str, str]] = []
        effect_details: list[dict[str, str]] = []
        requirement_lines: list[str] = []
        effect_detail_lines: list[str] = []
        requirement_error: ValueError | None = None
        effect_detail_error: ValueError | None = None

        try:
            requirements, requirement_lines = _build_formation_requirements_from_ocr_document(preview_document)
        except ValueError as exc:
            requirement_error = exc

        try:
            effect_details, effect_detail_lines = _build_formation_effect_details_from_ocr_document(preview_document)
        except ValueError as exc:
            effect_detail_error = exc

        if not requirements and not effect_details:
            detail = str(requirement_error or effect_detail_error or "未能从截图中识别触发条件或词缀效果")
            raise ValueError(detail)

        lines = list(dict.fromkeys([*requirement_lines, *effect_detail_lines]))
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return FanxiuFormationRequirementOcrImportResponse(
        lines=lines,
        requirements=[FanxiuFormationRequirementImportItem.model_validate(item) for item in requirements],
        effect_details=[FanxiuFormationEffectDetailImportItem.model_validate(item) for item in effect_details],
    )


@inventory_router.get("/activity-list", response_model=FanxiuActivityListSnapshot)
def get_fanxiu_activity_list():
    try:
        payload = load_activity_list()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FanxiuActivityListSnapshot(items=payload)


@inventory_router.put("/activity-list", response_model=FanxiuActivityListSnapshot)
def update_fanxiu_activity_list(
    payload: FanxiuActivityListSnapshot,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    normalized_items = payload.model_dump(mode="json")["items"]
    fanxiu_user = get_fanxiu_user(session)
    touched_existing_note = False

    for item in normalized_items:
        if not isinstance(item, dict):
            continue
        db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_ACTIVITY_KIND)
        if db_note:
            sync_activity_note_fields(db_note, item)
            session.add(db_note)
            touched_existing_note = True
        elif item.get("note_id"):
            item.pop("note_id", None)

    if touched_existing_note:
        session.commit()

    try:
        saved_payload = save_activity_list(normalized_items)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修活动列表失败：{exc}") from exc
    return FanxiuActivityListSnapshot(items=saved_payload)


@inventory_router.get("/region-data", response_model=FanxiuRegionDataSnapshot)
def get_fanxiu_region_data(session: Session = Depends(get_session)):
    return FanxiuRegionDataSnapshot.model_validate(build_region_data_snapshot(session))


@inventory_router.get("/region-data/characters", response_model=FanxiuRegionCharacterSnapshot)
def get_fanxiu_region_characters(session: Session = Depends(get_session)):
    return FanxiuRegionCharacterSnapshot.model_validate(build_region_character_snapshot(session))


@inventory_router.get("/region-data/characters/history", response_model=FanxiuRegionCharacterHistorySnapshot)
def get_fanxiu_region_character_history(
    region_name: str = Query(""),
    server_name: str = Query(""),
    guild_name: str = Query(""),
    role_name: str = Query(""),
    include_disabled: bool = Query(True),
    session: Session = Depends(get_session),
):
    return FanxiuRegionCharacterHistorySnapshot.model_validate(
        build_region_character_history_snapshot(
            session,
            region_name=region_name.strip(),
            server_name=server_name.strip(),
            guild_name=guild_name.strip(),
            role_name=role_name.strip(),
            include_disabled=include_disabled,
        )
    )


@inventory_router.patch("/region-data/characters/{character_id}", response_model=FanxiuRegionCharacterItem)
def patch_fanxiu_region_character(
    character_id: str,
    payload: FanxiuRegionCharacterUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    update_payload = payload.model_dump(exclude_unset=True)
    record = update_region_character_record(session, character_id, update_payload)
    return FanxiuRegionCharacterItem.model_validate(serialize_region_character_record(record))


@inventory_router.delete("/region-data/characters/{character_id}", response_model=FanxiuRegionCharacterItem)
def delete_fanxiu_region_character(
    character_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    record = disable_region_character_record(session, character_id)
    return FanxiuRegionCharacterItem.model_validate(serialize_region_character_record(record))


@inventory_router.post(
    "/region-data/characters/import/ocr",
    response_model=FanxiuRegionCharacterOcrImportResponse,
)
async def import_fanxiu_region_character_from_ocr(
    image: UploadFile = File(...),
    server_candidates: str = Form("[]"),
    target_region_name: str = Form(""),
    target_server_name: str = Form(""),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)

    image_bytes = await image.read()
    await image.close()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="截图内容为空")

    suffix = Path(image.filename or "").suffix or ".png"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(image_bytes)
            temp_path = Path(temp_file.name)
        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        normalized_server_candidates = _normalize_region_server_candidates(server_candidates)
        normalized_server_target = _normalize_region_server_target(target_region_name, target_server_name)
        item, lines = _build_region_character_from_ocr_document(
            preview.get("document") or {},
            normalized_server_candidates,
            normalized_server_target if normalized_server_target.get("server_name") else None,
        )
        record, created = create_region_character_record_if_stronger(session, item)
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return FanxiuRegionCharacterOcrImportResponse(
        lines=lines,
        item=FanxiuRegionCharacterItem.model_validate(serialize_region_character_record(record)),
        created=created,
        skipped_reason="" if created else "攻击未高于旧记录，保留旧数据",
    )


@inventory_router.get("/activity-list/modao-invasion", response_model=FanxiuModaoInvasionSnapshot)
def get_fanxiu_modao_invasion_exchange_list():
    try:
        payload = load_modao_invasion_exchange_list()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FanxiuModaoInvasionSnapshot.model_validate(payload)


@inventory_router.put("/activity-list/modao-invasion", response_model=FanxiuModaoInvasionSnapshot)
def update_fanxiu_modao_invasion_exchange_list(
    payload: FanxiuModaoInvasionSnapshot,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    normalized_snapshot = payload.model_dump(mode="json")

    try:
        saved_payload = save_modao_invasion_exchange_list(normalized_snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存魔道入侵兑换表失败：{exc}") from exc
    return FanxiuModaoInvasionSnapshot.model_validate(saved_payload)


@inventory_router.post(
    "/activity-list/modao-invasion/import/ocr",
    response_model=FanxiuModaoInvasionOcrImportResponse,
)
async def import_fanxiu_modao_invasion_exchange_list_from_ocr(
    image: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)

    image_bytes = await image.read()
    await image.close()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="截图内容为空")

    suffix = Path(image.filename or "").suffix or ".png"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(image_bytes)
            temp_path = Path(temp_file.name)
        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        items, lines = _build_modao_invasion_exchange_items_from_ocr_document(preview.get("document") or {})
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return FanxiuModaoInvasionOcrImportResponse(
        lines=lines,
        items=[FanxiuModaoInvasionExchangeItem.model_validate(item) for item in items],
    )


@inventory_router.post(
    "/activity-list/modao-invasion/personal-rankings/import/ocr",
    response_model=FanxiuModaoInvasionPersonalRankingOcrImportResponse,
)
async def import_fanxiu_modao_invasion_personal_rankings_from_ocr(
    image: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)

    image_bytes = await image.read()
    await image.close()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="截图内容为空")

    suffix = Path(image.filename or "").suffix or ".png"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(image_bytes)
            temp_path = Path(temp_file.name)
        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        items, lines = _build_modao_invasion_personal_rankings_from_ocr_document(preview.get("document") or {})
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return FanxiuModaoInvasionPersonalRankingOcrImportResponse(
        lines=lines,
        items=[FanxiuModaoInvasionPersonalRankingItem.model_validate(item) for item in items],
    )


@inventory_router.get("/activity-list/shouyuan-exploration", response_model=FanxiuShouyuanExplorationSnapshot)
def get_fanxiu_shouyuan_exploration_exchange_list():
    try:
        payload = load_shouyuan_exploration_exchange_list()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FanxiuShouyuanExplorationSnapshot.model_validate(payload)


@inventory_router.put("/activity-list/shouyuan-exploration", response_model=FanxiuShouyuanExplorationSnapshot)
def update_fanxiu_shouyuan_exploration_exchange_list(
    payload: FanxiuShouyuanExplorationSnapshot,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    normalized_snapshot = payload.model_dump(mode="json")

    try:
        saved_payload = save_shouyuan_exploration_exchange_list(normalized_snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存兽渊探秘兑换表失败：{exc}") from exc
    return FanxiuShouyuanExplorationSnapshot.model_validate(saved_payload)


@inventory_router.post(
    "/activity-list/shouyuan-exploration/import/ocr",
    response_model=FanxiuShouyuanExplorationOcrImportResponse,
)
async def import_fanxiu_shouyuan_exploration_exchange_list_from_ocr(
    image: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)

    image_bytes = await image.read()
    await image.close()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="截图内容为空")

    suffix = Path(image.filename or "").suffix or ".png"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(image_bytes)
            temp_path = Path(temp_file.name)
        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        items, lines = _build_modao_invasion_exchange_items_from_ocr_document(preview.get("document") or {})
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return FanxiuShouyuanExplorationOcrImportResponse(
        lines=lines,
        items=[FanxiuShouyuanExplorationExchangeItem.model_validate(item) for item in items],
    )


@inventory_router.post(
    "/activity-list/shouyuan-exploration/personal-rankings/import/ocr",
    response_model=FanxiuShouyuanExplorationPersonalRankingOcrImportResponse,
)
async def import_fanxiu_shouyuan_exploration_personal_rankings_from_ocr(
    image: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)

    image_bytes = await image.read()
    await image.close()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="截图内容为空")

    suffix = Path(image.filename or "").suffix or ".png"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(image_bytes)
            temp_path = Path(temp_file.name)
        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        items, lines = _build_modao_invasion_personal_rankings_from_ocr_document(preview.get("document") or {})
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return FanxiuShouyuanExplorationPersonalRankingOcrImportResponse(
        lines=lines,
        items=[FanxiuShouyuanExplorationPersonalRankingItem.model_validate(item) for item in items],
    )


@inventory_router.post(
    "/activity-list/shouyuan-exploration/income-speeds/import/ocr",
    response_model=FanxiuShouyuanExplorationIncomeSpeedOcrImportResponse,
)
async def import_fanxiu_shouyuan_exploration_income_speed_from_ocr(
    image: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)

    image_bytes = await image.read()
    await image.close()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="截图内容为空")

    suffix = Path(image.filename or "").suffix or ".png"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(image_bytes)
            temp_path = Path(temp_file.name)
        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        item, lines = _build_shouyuan_exploration_income_speed_from_ocr_document(preview.get("document") or {})
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return FanxiuShouyuanExplorationIncomeSpeedOcrImportResponse(
        lines=lines,
        item=FanxiuShouyuanExplorationIncomeSpeedItem.model_validate(item),
    )


@inventory_router.get("/inventory/wardrobe-notes/{item_id}", response_model=Optional[NoteRead])
def read_fanxiu_wardrobe_note(
    item_id: str,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    wardrobe_hall = load_wardrobe_hall()
    _, item = find_wardrobe_item(wardrobe_hall, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Wardrobe item not found")

    fanxiu_user = get_fanxiu_user(session)
    db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_WARDROBE_KIND)
    if not db_note:
        return None
    return serialize_fanxiu_note_read(db_note, current_user)


@inventory_router.put("/inventory/wardrobe-notes/{item_id}", response_model=NoteRead)
def update_fanxiu_wardrobe_note(
    item_id: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    wardrobe_hall = load_wardrobe_hall()
    _, item = find_wardrobe_item(wardrobe_hall, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Wardrobe item not found")

    fanxiu_user = get_fanxiu_user(session)
    db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_WARDROBE_KIND)

    current_time = time.time()
    normalized_note_types = normalize_note_types(note_in.note_types, fallback_type=FANXIU_WARDROBE_TYPE)
    normalized_note_color = normalize_note_color(note_in.color)
    if normalized_note_color and (
        not note_in.note_types
        or (
            len(normalized_note_types) == 1
            and normalized_note_types[0].get("key") == FANXIU_WARDROBE_TYPE
            and int(normalized_note_types[0].get("weight", 0)) == 100
        )
    ):
        legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
        if legacy_color_type_key:
            normalized_note_types = [{"key": legacy_color_type_key, "weight": 100}]
    primary_node_type = derive_primary_node_type(normalized_note_types, fallback_type=FANXIU_WARDROBE_TYPE)
    taxonomy = derive_note_taxonomy_from_legacy(
        normalized_note_types,
        node_type=primary_node_type,
        note_kind=FANXIU_WARDROBE_KIND,
        node_status=note_in.node_status,
    )

    item_title = str(item.get("name") or "").strip()
    item_weight = int(item.get("rank") or 0)
    item_start_at = wardrobe_item_date_to_timestamp(item.get("date"))
    if not item_title:
        raise HTTPException(status_code=400, detail="请先填写条目名称，再编辑文档。")

    if not db_note:
        db_note = NoteNode(
            id=str(uuid.uuid4()),
            user_id=fanxiu_user.id,
            title=item_title,
            content=note_in.content or "",
            weight=item_weight,
            node_type=primary_node_type,
            note_types=normalized_note_types,
            note_categories=taxonomy["note_categories"],
            primary_category=taxonomy["primary_category"],
            note_form=taxonomy["note_form"],
            note_kind=FANXIU_WARDROBE_KIND,
            note_scene=taxonomy["note_scene"],
            node_status=note_in.node_status,
            lifecycle_stage=taxonomy["lifecycle_stage"],
            color=normalized_note_color,
            weight_mode=NOTE_WEIGHT_MODE_LINEAR,
            created_at=current_time,
            updated_at=current_time,
            start_at=item_start_at,
            history=[],
            custom_fields=[],
        )
        session.add(db_note)
    else:
        if note_in.content is not None:
            db_note.content = note_in.content
        if note_in.note_types is not None:
            db_note.note_types = normalized_note_types
            db_note.node_type = primary_node_type
        elif not db_note.note_types:
            db_note.note_types = normalized_note_types
            db_note.node_type = primary_node_type
        if "color" in note_in.model_fields_set:
            db_note.color = normalized_note_color
        elif db_note.color:
            existing_note_types = normalize_note_types(db_note.note_types, fallback_type=db_note.node_type or FANXIU_WARDROBE_TYPE)
            normalized_existing_color = normalize_note_color(db_note.color)
            if normalized_existing_color and len(existing_note_types) == 1:
                only_type = existing_note_types[0]
                fallback_type = db_note.node_type or FANXIU_WARDROBE_TYPE
                if only_type.get("key") == fallback_type and int(only_type.get("weight", 0)) == 100:
                    legacy_color_type_key = build_legacy_color_type_key(normalized_existing_color)
                    if legacy_color_type_key:
                        db_note.note_types = [{"key": legacy_color_type_key, "weight": 100}]
                        db_note.node_type = legacy_color_type_key
        if db_note.note_kind != FANXIU_WARDROBE_KIND:
            db_note.note_kind = FANXIU_WARDROBE_KIND
        if db_note.weight_mode != NOTE_WEIGHT_MODE_LINEAR:
            db_note.weight_mode = NOTE_WEIGHT_MODE_LINEAR
        if note_in.node_status is not None:
            db_note.node_status = note_in.node_status
        if note_in.custom_fields is not None:
            db_note.custom_fields = note_in.custom_fields
        elif not isinstance(db_note.custom_fields, list):
            db_note.custom_fields = []

        refreshed_taxonomy = derive_note_taxonomy_from_legacy(
            db_note.note_types,
            node_type=db_note.node_type or FANXIU_WARDROBE_TYPE,
            note_kind=FANXIU_WARDROBE_KIND,
            node_status=db_note.node_status,
        )
        db_note.note_categories = refreshed_taxonomy["note_categories"]
        db_note.primary_category = refreshed_taxonomy["primary_category"]
        db_note.note_form = refreshed_taxonomy["note_form"]
        db_note.note_scene = refreshed_taxonomy["note_scene"]
        db_note.lifecycle_stage = refreshed_taxonomy["lifecycle_stage"]
        db_note.updated_at = current_time
        session.add(db_note)

    db_note.title = item_title
    db_note.weight = item_weight
    db_note.start_at = item_start_at

    session.commit()
    session.refresh(db_note)

    item["note_id"] = db_note.id
    try:
        save_wardrobe_hall(wardrobe_hall)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修道具仓库失败：{exc}") from exc
    return serialize_fanxiu_note_read(db_note, current_user)


@inventory_router.get("/inventory/spirit-beast-notes/{item_id}", response_model=Optional[NoteRead])
def read_fanxiu_spirit_beast_note(
    item_id: str,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    spirit_beast_hall = load_spirit_beast_hall()
    _, item = find_spirit_beast_item(spirit_beast_hall, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Spirit beast item not found")

    fanxiu_user = get_fanxiu_user(session)
    db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_SPIRIT_BEAST_KIND)
    if not db_note:
        return None
    return serialize_fanxiu_note_read(db_note, current_user)


@inventory_router.put("/inventory/spirit-beast-notes/{item_id}", response_model=NoteRead)
def update_fanxiu_spirit_beast_note(
    item_id: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    spirit_beast_hall = load_spirit_beast_hall()
    _, item = find_spirit_beast_item(spirit_beast_hall, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Spirit beast item not found")

    fanxiu_user = get_fanxiu_user(session)
    db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_SPIRIT_BEAST_KIND)

    current_time = time.time()
    normalized_note_types = normalize_note_types(note_in.note_types, fallback_type=FANXIU_SPIRIT_BEAST_TYPE)
    normalized_note_color = normalize_note_color(note_in.color)
    if normalized_note_color and (
        not note_in.note_types
        or (
            len(normalized_note_types) == 1
            and normalized_note_types[0].get("key") == FANXIU_SPIRIT_BEAST_TYPE
            and int(normalized_note_types[0].get("weight", 0)) == 100
        )
    ):
        legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
        if legacy_color_type_key:
            normalized_note_types = [{"key": legacy_color_type_key, "weight": 100}]
    primary_node_type = derive_primary_node_type(normalized_note_types, fallback_type=FANXIU_SPIRIT_BEAST_TYPE)
    taxonomy = derive_note_taxonomy_from_legacy(
        normalized_note_types,
        node_type=primary_node_type,
        note_kind=FANXIU_SPIRIT_BEAST_KIND,
        node_status=note_in.node_status,
    )

    item_title = str(item.get("name") or "").strip()
    item_weight = int(item.get("rank") or 0)
    item_start_at = wardrobe_item_date_to_timestamp(item.get("date"))
    if not item_title:
        raise HTTPException(status_code=400, detail="请先填写条目名称，再编辑文档。")

    if not db_note:
        db_note = NoteNode(
            id=str(uuid.uuid4()),
            user_id=fanxiu_user.id,
            title=item_title,
            content=note_in.content or "",
            weight=item_weight,
            node_type=primary_node_type,
            note_types=normalized_note_types,
            note_categories=taxonomy["note_categories"],
            primary_category=taxonomy["primary_category"],
            note_form=taxonomy["note_form"],
            note_kind=FANXIU_SPIRIT_BEAST_KIND,
            note_scene=taxonomy["note_scene"],
            node_status=note_in.node_status,
            lifecycle_stage=taxonomy["lifecycle_stage"],
            color=normalized_note_color,
            weight_mode=NOTE_WEIGHT_MODE_LINEAR,
            created_at=current_time,
            updated_at=current_time,
            start_at=item_start_at,
            history=[],
            custom_fields=[],
        )
        session.add(db_note)
    else:
        if note_in.content is not None:
            db_note.content = note_in.content
        if note_in.note_types is not None:
            db_note.note_types = normalized_note_types
            db_note.node_type = primary_node_type
        elif not db_note.note_types:
            db_note.note_types = normalized_note_types
            db_note.node_type = primary_node_type
        if "color" in note_in.model_fields_set:
            db_note.color = normalized_note_color
        elif db_note.color:
            existing_note_types = normalize_note_types(db_note.note_types, fallback_type=db_note.node_type or FANXIU_SPIRIT_BEAST_TYPE)
            normalized_existing_color = normalize_note_color(db_note.color)
            if normalized_existing_color and len(existing_note_types) == 1:
                only_type = existing_note_types[0]
                fallback_type = db_note.node_type or FANXIU_SPIRIT_BEAST_TYPE
                if only_type.get("key") == fallback_type and int(only_type.get("weight", 0)) == 100:
                    legacy_color_type_key = build_legacy_color_type_key(normalized_existing_color)
                    if legacy_color_type_key:
                        db_note.note_types = [{"key": legacy_color_type_key, "weight": 100}]
                        db_note.node_type = legacy_color_type_key
        if db_note.note_kind != FANXIU_SPIRIT_BEAST_KIND:
            db_note.note_kind = FANXIU_SPIRIT_BEAST_KIND
        if db_note.weight_mode != NOTE_WEIGHT_MODE_LINEAR:
            db_note.weight_mode = NOTE_WEIGHT_MODE_LINEAR
        if note_in.node_status is not None:
            db_note.node_status = note_in.node_status
        if note_in.custom_fields is not None:
            db_note.custom_fields = note_in.custom_fields
        elif not isinstance(db_note.custom_fields, list):
            db_note.custom_fields = []

        refreshed_taxonomy = derive_note_taxonomy_from_legacy(
            db_note.note_types,
            node_type=db_note.node_type or FANXIU_SPIRIT_BEAST_TYPE,
            note_kind=FANXIU_SPIRIT_BEAST_KIND,
            node_status=db_note.node_status,
        )
        db_note.note_categories = refreshed_taxonomy["note_categories"]
        db_note.primary_category = refreshed_taxonomy["primary_category"]
        db_note.note_form = refreshed_taxonomy["note_form"]
        db_note.note_scene = refreshed_taxonomy["note_scene"]
        db_note.lifecycle_stage = refreshed_taxonomy["lifecycle_stage"]
        db_note.updated_at = current_time
        session.add(db_note)

    db_note.title = item_title
    db_note.weight = item_weight
    db_note.start_at = item_start_at

    session.commit()
    session.refresh(db_note)

    item["note_id"] = db_note.id
    try:
        save_spirit_beast_hall(spirit_beast_hall)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修灵兽仓库失败：{exc}") from exc
    return serialize_fanxiu_note_read(db_note, current_user)


@inventory_router.get("/inventory/magic-treasure-notes/{item_id}", response_model=Optional[NoteRead])
def read_fanxiu_magic_treasure_note(
    item_id: str,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    magic_treasure_hall = load_magic_treasure_hall()
    _, item = find_magic_treasure_item(magic_treasure_hall, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Magic treasure item not found")

    fanxiu_user = get_fanxiu_user(session)
    db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_MAGIC_TREASURE_KIND)
    if not db_note:
        return None
    return serialize_fanxiu_note_read(db_note, current_user)


@inventory_router.put("/inventory/magic-treasure-notes/{item_id}", response_model=NoteRead)
def update_fanxiu_magic_treasure_note(
    item_id: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    magic_treasure_hall = load_magic_treasure_hall()
    _, item = find_magic_treasure_item(magic_treasure_hall, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Magic treasure item not found")

    fanxiu_user = get_fanxiu_user(session)
    db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_MAGIC_TREASURE_KIND)

    current_time = time.time()
    normalized_note_types = normalize_note_types(note_in.note_types, fallback_type=FANXIU_MAGIC_TREASURE_TYPE)
    normalized_note_color = normalize_note_color(note_in.color)
    if normalized_note_color and (
        not note_in.note_types
        or (
            len(normalized_note_types) == 1
            and normalized_note_types[0].get("key") == FANXIU_MAGIC_TREASURE_TYPE
            and int(normalized_note_types[0].get("weight", 0)) == 100
        )
    ):
        legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
        if legacy_color_type_key:
            normalized_note_types = [{"key": legacy_color_type_key, "weight": 100}]
    primary_node_type = derive_primary_node_type(normalized_note_types, fallback_type=FANXIU_MAGIC_TREASURE_TYPE)
    taxonomy = derive_note_taxonomy_from_legacy(
        normalized_note_types,
        node_type=primary_node_type,
        note_kind=FANXIU_MAGIC_TREASURE_KIND,
        node_status=note_in.node_status,
    )

    item_title = str(item.get("name") or "").strip()
    item_weight = int(item.get("rank") or 0)
    item_start_at = wardrobe_item_date_to_timestamp(item.get("date"))
    if not item_title:
        raise HTTPException(status_code=400, detail="请先填写条目名称，再编辑文档。")

    if not db_note:
        db_note = NoteNode(
            id=str(uuid.uuid4()),
            user_id=fanxiu_user.id,
            title=item_title,
            content=note_in.content or "",
            weight=item_weight,
            node_type=primary_node_type,
            note_types=normalized_note_types,
            note_categories=taxonomy["note_categories"],
            primary_category=taxonomy["primary_category"],
            note_form=taxonomy["note_form"],
            note_kind=FANXIU_MAGIC_TREASURE_KIND,
            note_scene=taxonomy["note_scene"],
            node_status=note_in.node_status,
            lifecycle_stage=taxonomy["lifecycle_stage"],
            color=normalized_note_color,
            weight_mode=NOTE_WEIGHT_MODE_LINEAR,
            created_at=current_time,
            updated_at=current_time,
            start_at=item_start_at,
            history=[],
            custom_fields=[],
        )
        session.add(db_note)
    else:
        if note_in.content is not None:
            db_note.content = note_in.content
        if note_in.note_types is not None:
            db_note.note_types = normalized_note_types
            db_note.node_type = primary_node_type
        elif not db_note.note_types:
            db_note.note_types = normalized_note_types
            db_note.node_type = primary_node_type
        if "color" in note_in.model_fields_set:
            db_note.color = normalized_note_color
        elif db_note.color:
            existing_note_types = normalize_note_types(db_note.note_types, fallback_type=db_note.node_type or FANXIU_MAGIC_TREASURE_TYPE)
            normalized_existing_color = normalize_note_color(db_note.color)
            if normalized_existing_color and len(existing_note_types) == 1:
                only_type = existing_note_types[0]
                fallback_type = db_note.node_type or FANXIU_MAGIC_TREASURE_TYPE
                if only_type.get("key") == fallback_type and int(only_type.get("weight", 0)) == 100:
                    legacy_color_type_key = build_legacy_color_type_key(normalized_existing_color)
                    if legacy_color_type_key:
                        db_note.note_types = [{"key": legacy_color_type_key, "weight": 100}]
                        db_note.node_type = legacy_color_type_key
        if db_note.note_kind != FANXIU_MAGIC_TREASURE_KIND:
            db_note.note_kind = FANXIU_MAGIC_TREASURE_KIND
        if db_note.weight_mode != NOTE_WEIGHT_MODE_LINEAR:
            db_note.weight_mode = NOTE_WEIGHT_MODE_LINEAR
        if note_in.node_status is not None:
            db_note.node_status = note_in.node_status
        if note_in.custom_fields is not None:
            db_note.custom_fields = note_in.custom_fields
        elif not isinstance(db_note.custom_fields, list):
            db_note.custom_fields = []

        refreshed_taxonomy = derive_note_taxonomy_from_legacy(
            db_note.note_types,
            node_type=db_note.node_type or FANXIU_MAGIC_TREASURE_TYPE,
            note_kind=FANXIU_MAGIC_TREASURE_KIND,
            node_status=db_note.node_status,
        )
        db_note.note_categories = refreshed_taxonomy["note_categories"]
        db_note.primary_category = refreshed_taxonomy["primary_category"]
        db_note.note_form = refreshed_taxonomy["note_form"]
        db_note.note_scene = refreshed_taxonomy["note_scene"]
        db_note.lifecycle_stage = refreshed_taxonomy["lifecycle_stage"]
        db_note.updated_at = current_time
        session.add(db_note)

    db_note.title = item_title
    db_note.weight = item_weight
    db_note.start_at = item_start_at

    session.commit()
    session.refresh(db_note)

    item["note_id"] = db_note.id
    try:
        save_magic_treasure_hall(magic_treasure_hall)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修法宝仓库失败：{exc}") from exc
    return serialize_fanxiu_note_read(db_note, current_user)


@inventory_router.get("/activity-notes/{item_id}", response_model=Optional[NoteRead])
def read_fanxiu_activity_note(
    item_id: str,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    activity_list = load_activity_list()
    item = find_activity_item(activity_list, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="活动条目不存在")

    fanxiu_user = get_fanxiu_user(session)
    db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_ACTIVITY_KIND)
    if not db_note:
        return None
    return serialize_fanxiu_note_read(db_note, current_user)


@inventory_router.put("/activity-notes/{item_id}", response_model=NoteRead)
def update_fanxiu_activity_note(
    item_id: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    activity_list = load_activity_list()
    item = find_activity_item(activity_list, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="活动条目不存在")

    fanxiu_user = get_fanxiu_user(session)
    db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_ACTIVITY_KIND)

    current_time = time.time()
    normalized_note_types = normalize_note_types(note_in.note_types, fallback_type=FANXIU_ACTIVITY_TYPE)
    normalized_note_color = normalize_note_color(note_in.color)
    if normalized_note_color and (
        not note_in.note_types
        or (
            len(normalized_note_types) == 1
            and normalized_note_types[0].get("key") == FANXIU_ACTIVITY_TYPE
            and int(normalized_note_types[0].get("weight", 0)) == 100
        )
    ):
        legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
        if legacy_color_type_key:
            normalized_note_types = [{"key": legacy_color_type_key, "weight": 100}]
    primary_node_type = derive_primary_node_type(normalized_note_types, fallback_type=FANXIU_ACTIVITY_TYPE)
    taxonomy = derive_note_taxonomy_from_legacy(
        normalized_note_types,
        node_type=primary_node_type,
        note_kind=FANXIU_ACTIVITY_KIND,
        node_status=note_in.node_status,
    )

    item_title = str(item.get("name") or "").strip()
    item_start_at = activity_item_start_to_timestamp(item.get("start_date"))
    if not item_title:
        raise HTTPException(status_code=400, detail="请先填写活动名称，再编辑文档。")

    if not db_note:
        db_note = NoteNode(
            id=str(uuid.uuid4()),
            user_id=fanxiu_user.id,
            title=item_title,
            content=note_in.content or "",
            weight=0,
            node_type=primary_node_type,
            note_types=normalized_note_types,
            note_categories=taxonomy["note_categories"],
            primary_category=taxonomy["primary_category"],
            note_form=taxonomy["note_form"],
            note_kind=FANXIU_ACTIVITY_KIND,
            note_scene=taxonomy["note_scene"],
            node_status=note_in.node_status,
            lifecycle_stage=taxonomy["lifecycle_stage"],
            color=normalized_note_color,
            weight_mode=NOTE_WEIGHT_MODE_LINEAR,
            created_at=current_time,
            updated_at=current_time,
            start_at=item_start_at,
            history=[],
            custom_fields=[],
        )
        session.add(db_note)
    else:
        if note_in.content is not None:
            db_note.content = note_in.content
        if note_in.note_types is not None:
            db_note.note_types = normalized_note_types
            db_note.node_type = primary_node_type
        elif not db_note.note_types:
            db_note.note_types = normalized_note_types
            db_note.node_type = primary_node_type
        if "color" in note_in.model_fields_set:
            db_note.color = normalized_note_color
        elif db_note.color:
            existing_note_types = normalize_note_types(db_note.note_types, fallback_type=db_note.node_type or FANXIU_ACTIVITY_TYPE)
            normalized_existing_color = normalize_note_color(db_note.color)
            if normalized_existing_color and len(existing_note_types) == 1:
                only_type = existing_note_types[0]
                fallback_type = db_note.node_type or FANXIU_ACTIVITY_TYPE
                if only_type.get("key") == fallback_type and int(only_type.get("weight", 0)) == 100:
                    legacy_color_type_key = build_legacy_color_type_key(normalized_existing_color)
                    if legacy_color_type_key:
                        db_note.note_types = [{"key": legacy_color_type_key, "weight": 100}]
                        db_note.node_type = legacy_color_type_key
        if db_note.note_kind != FANXIU_ACTIVITY_KIND:
            db_note.note_kind = FANXIU_ACTIVITY_KIND
        if db_note.weight_mode != NOTE_WEIGHT_MODE_LINEAR:
            db_note.weight_mode = NOTE_WEIGHT_MODE_LINEAR
        if note_in.node_status is not None:
            db_note.node_status = note_in.node_status
        if note_in.custom_fields is not None:
            db_note.custom_fields = note_in.custom_fields
        elif not isinstance(db_note.custom_fields, list):
            db_note.custom_fields = []

        refreshed_taxonomy = derive_note_taxonomy_from_legacy(
            db_note.note_types,
            node_type=db_note.node_type or FANXIU_ACTIVITY_TYPE,
            note_kind=FANXIU_ACTIVITY_KIND,
            node_status=db_note.node_status,
        )
        db_note.note_categories = refreshed_taxonomy["note_categories"]
        db_note.primary_category = refreshed_taxonomy["primary_category"]
        db_note.note_form = refreshed_taxonomy["note_form"]
        db_note.note_scene = refreshed_taxonomy["note_scene"]
        db_note.lifecycle_stage = refreshed_taxonomy["lifecycle_stage"]
        db_note.updated_at = current_time
        session.add(db_note)

    db_note.title = item_title
    db_note.start_at = item_start_at

    session.commit()
    session.refresh(db_note)

    item["note_id"] = db_note.id
    try:
        save_activity_list(activity_list)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修活动列表失败：{exc}") from exc
    return serialize_fanxiu_note_read(db_note, current_user)

@chars_router.get("/chars", response_model=List[NoteRead])
def read_chars(
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session)
):
    """
    Get all Xianzhou Race characters data.
    Publicly accessible.
    """
    fanxiu_user = get_fanxiu_user(session)
    notes: list[NoteNode] = []
    changed = False
    for char_name in XIANZHOU_RACE_CHAR_NAMES:
        note = get_or_migrate_fanxiu_char_note(session, fanxiu_user, char_name)
        if note is None:
            continue
        changed = True if note in session.new or note in session.dirty else changed
        notes.append(note)
    if changed:
        session.commit()
        for note in notes:
            session.refresh(note)
    return [serialize_fanxiu_note_read(note, current_user) for note in notes]

@chars_router.get("/chars/{char_name}", response_model=NoteRead)
def read_char(
    char_name: str,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session)
):
    """
    Get specific character data.
    Publicly accessible.
    """
    fanxiu_user = get_fanxiu_user(session)
    note = get_or_migrate_fanxiu_char_note(session, fanxiu_user, char_name)
    
    if not note:
        raise HTTPException(status_code=404, detail="Character not found")
    if note in session.new or note in session.dirty:
        session.commit()
        session.refresh(note)
        
    return serialize_fanxiu_note_read(note, current_user)

@chars_router.put("/chars/{char_name}", response_model=NoteRead)
def update_char(
    char_name: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Update or create character data.
    Restricted to specific users.
    """
    # STRICT PERMISSION: Only 'fanxiu_official' itself can edit.
    # Even 'code4101' cannot edit directly via this API unless logged in as 'fanxiu_official'.
    # This enforces data ownership isolation.
    
    ensure_fanxiu_write_permission(current_user, session)
    fanxiu_user = get_fanxiu_user(session)
    db_note = get_or_migrate_fanxiu_char_note(session, fanxiu_user, char_name)
    
    current_time = time.time()
    normalized_note_types = normalize_note_types(note_in.note_types, fallback_type=FANXIU_CHAR_TYPE)
    normalized_note_color = normalize_note_color(note_in.color)
    if normalized_note_color and (
        not note_in.note_types
        or (
            len(normalized_note_types) == 1
            and normalized_note_types[0].get("key") == FANXIU_CHAR_TYPE
            and int(normalized_note_types[0].get("weight", 0)) == 100
        )
    ):
        legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
        if legacy_color_type_key:
            normalized_note_types = [{"key": legacy_color_type_key, "weight": 100}]
    primary_node_type = derive_primary_node_type(normalized_note_types, fallback_type=FANXIU_CHAR_TYPE)
    taxonomy = derive_note_taxonomy_from_legacy(
        normalized_note_types,
        node_type=primary_node_type,
        note_kind=FANXIU_CHAR_KIND,
        node_status=note_in.node_status,
    )
    
    if not db_note:
        # Create new
        db_note = NoteNode(
            id=str(uuid.uuid4()),
            user_id=fanxiu_user.id,
            title=char_name, 
            content=note_in.content or "",
            weight=note_in.weight if note_in.weight is not None else 0,
            node_type=primary_node_type,
            note_types=normalized_note_types,
            note_categories=taxonomy["note_categories"],
            primary_category=taxonomy["primary_category"],
            note_form=taxonomy["note_form"],
            note_kind=FANXIU_CHAR_KIND,
            note_scene=taxonomy["note_scene"],
            node_status=note_in.node_status,
            lifecycle_stage=taxonomy["lifecycle_stage"],
            color=normalized_note_color,
            weight_mode=NOTE_WEIGHT_MODE_LINEAR,
            created_at=current_time,
            updated_at=current_time,
            start_at=note_in.start_at if note_in.start_at is not None else current_time,
            history=[],
            custom_fields=[],
        )
        session.add(db_note)
    else:
        # Update existing
        if note_in.content is not None:
            db_note.content = note_in.content
        if note_in.weight is not None:
            db_note.weight = note_in.weight
        if note_in.start_at is not None:
            db_note.start_at = note_in.start_at
        if note_in.note_types is not None:
            db_note.note_types = normalized_note_types
            db_note.node_type = primary_node_type
        elif not db_note.note_types:
            db_note.note_types = normalized_note_types
            db_note.node_type = primary_node_type
        if "color" in note_in.model_fields_set:
            db_note.color = normalized_note_color
        elif db_note.color:
            existing_note_types = normalize_note_types(db_note.note_types, fallback_type=db_note.node_type or FANXIU_CHAR_TYPE)
            normalized_existing_color = normalize_note_color(db_note.color)
            if normalized_existing_color and len(existing_note_types) == 1:
                only_type = existing_note_types[0]
                fallback_type = db_note.node_type or FANXIU_CHAR_TYPE
                if only_type.get("key") == fallback_type and int(only_type.get("weight", 0)) == 100:
                    legacy_color_type_key = build_legacy_color_type_key(normalized_existing_color)
                    if legacy_color_type_key:
                        db_note.note_types = [{"key": legacy_color_type_key, "weight": 100}]
                        db_note.node_type = legacy_color_type_key
        if db_note.note_kind != FANXIU_CHAR_KIND:
            db_note.note_kind = FANXIU_CHAR_KIND
        if db_note.weight_mode != NOTE_WEIGHT_MODE_LINEAR:
            db_note.weight_mode = NOTE_WEIGHT_MODE_LINEAR
        if note_in.node_status is not None:
            db_note.node_status = note_in.node_status

        refreshed_taxonomy = derive_note_taxonomy_from_legacy(
            db_note.note_types,
            node_type=db_note.node_type or FANXIU_CHAR_TYPE,
            note_kind=FANXIU_CHAR_KIND,
            node_status=db_note.node_status,
        )
        db_note.note_categories = refreshed_taxonomy["note_categories"]
        db_note.primary_category = refreshed_taxonomy["primary_category"]
        db_note.note_form = refreshed_taxonomy["note_form"]
        db_note.note_scene = refreshed_taxonomy["note_scene"]
        db_note.lifecycle_stage = refreshed_taxonomy["lifecycle_stage"]

        db_note.updated_at = current_time
        session.add(db_note)
        
    session.commit()
    session.refresh(db_note)
    return serialize_fanxiu_note_read(db_note, current_user)


router.include_router(status_router)
router.include_router(inventory_router)
router.include_router(chars_router)
