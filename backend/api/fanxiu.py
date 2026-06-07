import base64
import contextlib
import difflib
import hashlib
import asyncio
import io
import json
import mimetypes
import os
import re
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time as dt_time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from types import GeneratorType
from typing import Any, Callable, List, Optional

import requests
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlmodel import Session, or_, select
from starlette.background import BackgroundTask
from pyxllib.prog.behavior_tree import Status as BehaviorTreeStatus

from backend.api.device import REMOTE_DEVICE_DIRECT_PROXIES
from backend.core.auth import (
    ALGORITHM,
    SECRET_KEY,
    create_access_token,
    get_current_active_user,
    get_optional_current_user_from_token,
    verify_api_token,
)
from backend.core.feature_access_guard import ensure_feature_access, require_feature_access_dependency
from backend.core.game_window_service_runtime import (
    GameWindowServiceError,
    get_game_window_service_status,
    open_game_window_service_stream,
    start_game_window_service,
)
from backend.core.service_tokens import SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL, require_service_scope
from backend.core.settings import get_settings
from backend.core.note_identity import allocate_new_note_identity
from backend.core.note_refs import note_edge_ref, note_public_id, note_ref_aliases
from backend.db import engine, get_session
from backend.models import FanxiuMailRecord, FanxiuPseudoCodeCard, NoteEdge, NoteNode, User, UserDevice
from backend.schemas import NoteRead, NoteUpdate
from backend.core.fanxiu_mumu_control import (
    activate_mumu_window,
    capture_mumu_window_frame,
    click_mumu_window_processed_point,
    clear_fanxiu_burst_frames,
    delete_fanxiu_screenshot,
    drag_mumu_window_processed_points,
    get_fanxiu_burst_frame_path,
    get_fanxiu_match_frame_path,
    get_fanxiu_screenshot_path,
    import_fanxiu_burst_frames,
    keyevent_mumu_adb,
    keyevents_mumu_adb,
    list_fanxiu_burst_frames,
    list_fanxiu_screenshots,
    match_fanxiu_screenshot_box_frame,
    read_fanxiu_screenshot_pre_label,
    save_fanxiu_burst_frame,
    save_fanxiu_screenshot_frame,
    screencap_mumu_adb_cached_png,
    screencap_mumu_adb_png,
    stream_mumu_adb_screencap_mjpeg,
    stream_mumu_window_mjpeg,
    text_mumu_adb,
    write_fanxiu_screenshot_pre_label,
)
from backend.core.fanxiu_pseudocode_runtime import compile_fanxiu_pseudocode, start_fanxiu_pseudocode_script
from backend.core.fanxiu_visual_macro_runtime import (
    VisualMacroRuntimeCallbacks,
    begin_visual_macro_run,
    end_visual_macro_run,
    run_fanxiu_visual_script,
    stop_visual_macro_run,
)
from backend.core.ai_app_config import (
    AiAppConfigError,
)
from backend.core.ai_chat import OllamaClientError
from backend.core.fanxiu_inventory import load_magic_treasure_hall, save_magic_treasure_hall
from backend.core.fanxiu_inventory import load_spirit_artifact_hall, save_spirit_artifact_hall
from backend.core.fanxiu_inventory import load_wardrobe_hall, save_wardrobe_hall
from backend.core.fanxiu_inventory import load_spirit_beast_hall, save_spirit_beast_hall
from backend.core.fanxiu_inventory import load_activity_list, save_activity_list
from backend.core.fanxiu_inventory import load_modao_invasion_exchange_list, save_modao_invasion_exchange_list
from backend.core.fanxiu_inventory import (
    load_shouyuan_exploration_exchange_list,
    save_shouyuan_exploration_exchange_list,
)
from backend.core.fanxiu_processes import match_fanxiu_process_fields, list_fanxiu_processes, terminate_fanxiu_processes
from backend.core.fanxiu_packet_capture import build_fanxiu_packet_capture_snapshot
from backend.core.fanxiu_android_proxy import fanxiu_android_proxy_service
from backend.core.fanxiu_packet_activity import fanxiu_packet_activity_service
from backend.core.fanxiu_packet_proxy import fanxiu_packet_proxy_service
from backend.core.fanxiu_capture_runtime import fanxiu_capture_runtime_service
from backend.core.fanxiu_activity_packet_sync import (
    get_fanxiu_activity_packet_schedule,
    sync_fanxiu_activity_packets,
)
from backend.core.fanxiu_packet_insights import (
    get_fanxiu_packet_runtime_insights,
    get_fanxiu_packet_storage_bag_snapshot,
    sync_fanxiu_packet_runtime_insights,
)
from backend.core.fanxiu_status_models import (
    FanxiuActivityPacketSyncRequest,
    FanxiuActivityPacketSyncResponse,
    FanxiuAndroidProxyStatus,
    FanxiuBehaviorTreeServiceResponse,
    FanxiuBehaviorTreeServiceStatus,
    FanxiuCaptureRuntimeRequest,
    FanxiuCaptureRuntimeStatus,
    FanxiuMailPacketSyncRequest,
    FanxiuMailPacketSyncResponse,
    FanxiuMailRecordListResponse,
    FanxiuPacketActivityFlow,
    FanxiuPacketActivityHistoryResponse,
    FanxiuPacketActivityPayloadEvent,
    FanxiuPacketActivityStartRequest,
    FanxiuPacketActivityStatus,
    FanxiuPacketActivityStreamDirection,
    FanxiuPacketActivityStreamResponse,
    FanxiuPacketCaptureAddress,
    FanxiuPacketCaptureConnection,
    FanxiuPacketCaptureDnsMapping,
    FanxiuPacketCaptureProcess,
    FanxiuPacketCaptureSessionStatus,
    FanxiuPacketCaptureSnapshot,
    FanxiuPacketCaptureSnapshotRequest,
    FanxiuPacketInsightResponse,
    FanxiuPacketInsightSyncRequest,
    FanxiuPacketPayloadDirection,
    FanxiuPacketPayloadPreview,
    FanxiuPacketProxyEvent,
    FanxiuPacketProxyEventListResponse,
    FanxiuPacketProxyLogFile,
    FanxiuPacketProxyLogListResponse,
    FanxiuPacketProxyLogLoadResponse,
    FanxiuPacketProxySaveRequest,
    FanxiuPacketProxySaveResponse,
    FanxiuPacketProxyStartRequest,
    FanxiuPacketProxyStatus,
    FanxiuPacketProxyTimelineResponse,
    FanxiuPacketStorageBagResponse,
    FanxiuPlayerProfileRecordListResponse,
    FanxiuProcessItem,
    FanxiuProcessListResponse,
    FanxiuProcessTerminateError,
    FanxiuProcessTerminateResponse,
    FanxiuTcpBusinessCategorySummary,
    FanxiuTcpBusinessEntry,
    FanxiuTcpBusinessEntryListResponse,
    FanxiuTcpBusinessProtocolSample,
    FanxiuTcpBusinessProtocolSummary,
    FanxiuTcpCaptureFile,
    FanxiuTcpCaptureListResponse,
    FanxiuTcpDecodeRequest,
    FanxiuTcpDecodeResponse,
    FanxiuTcpRecordItem,
    FanxiuTcpRecordListResponse,
    LocalScriptProcessItem,
    LocalScriptProcessListResponse,
)
from backend.core.fanxiu_game_window_models import (
    FanxiuGameWindow2ActivateRequest,
    FanxiuGameWindow2BurstClearRequest,
    FanxiuGameWindow2BurstFrameRequest,
    FanxiuGameWindow2BurstImportRequest,
    FanxiuGameWindow2BurstListRequest,
    FanxiuGameWindow2ClickRequest,
    FanxiuGameWindow2DragRequest,
    FanxiuGameWindow2KeyeventRequest,
    FanxiuGameWindow2MatchBox,
    FanxiuGameWindow2MatchRequest,
    FanxiuGameWindow2SaveFrameRequest,
    FanxiuGameWindow2ScreencapRequest,
    FanxiuGameWindow2ScreenshotDeleteRequest,
    FanxiuGameWindow2ScreenshotListRequest,
    FanxiuGameWindow2ScreenshotPreLabelRequest,
    FanxiuGameWindow2ScreenshotPreLabelSaveRequest,
    FanxiuGameWindow2ServiceActivateRequest,
    FanxiuGameWindow2ServiceBurstClearRequest,
    FanxiuGameWindow2ServiceBurstFrameRequest,
    FanxiuGameWindow2ServiceBurstImportRequest,
    FanxiuGameWindow2ServiceBurstListRequest,
    FanxiuGameWindow2ServiceClickRequest,
    FanxiuGameWindow2ServiceDragRequest,
    FanxiuGameWindow2ServiceKeyeventRequest,
    FanxiuGameWindow2ServiceMatchRequest,
    FanxiuGameWindow2ServiceSaveFrameRequest,
    FanxiuGameWindow2ServiceScreenshotDeleteRequest,
    FanxiuGameWindow2ServiceScreenshotPreLabelRequest,
    FanxiuGameWindow2ServiceScreenshotPreLabelSaveRequest,
    FanxiuGameWindow2ServiceTextRequest,
    FanxiuGameWindow2StreamTokenRequest,
    FanxiuGameWindow2StreamTokenResponse,
    FanxiuGameWindow2TextRequest,
    FanxiuDataAnnotationAssetTreeRequest,
    FanxiuDataAnnotationMacroAnnotateRequest,
    FanxiuDataAnnotationMacroAnnotateResponse,
    FanxiuDataAnnotationMacroPoint,
    FanxiuDataAnnotationOcrFrameLine,
    FanxiuDataAnnotationOcrFrameRequest,
    FanxiuDataAnnotationOcrFrameResponse,
    FanxiuPseudoCodeCardCreateRequest,
    FanxiuPseudoCodeCardListResponse,
    FanxiuPseudoCodeCardRead,
    FanxiuPseudoCodeCardUpdateRequest,
    FanxiuPseudoCodeCompileRequest,
    FanxiuPseudoCodeRunResponse,
    FanxiuPseudoCodeStartRequest,
    FanxiuVisualScriptRunRequest,
    FanxiuVisualScriptStopRequest,
)
from backend.core.fanxiu_inventory_models import (
    FanxiuActivityItem,
    FanxiuActivityListSnapshot,
    FanxiuFormationEffectDetailImportItem,
    FanxiuFormationRequirementImportItem,
    FanxiuFormationRequirementOcrImportResponse,
    FanxiuMagicTreasureHallSnapshot,
    FanxiuMagicTreasureOcrImportResponse,
    FanxiuModaoInvasionExchangeItem,
    FanxiuModaoInvasionOcrImportResponse,
    FanxiuModaoInvasionPersonalRankingItem,
    FanxiuModaoInvasionPersonalRankingOcrImportResponse,
    FanxiuModaoInvasionRecord,
    FanxiuModaoInvasionSnapshot,
    FanxiuShouyuanExplorationConsumptionEvaluationItem,
    FanxiuShouyuanExplorationExchangeItem,
    FanxiuShouyuanExplorationIncomeSpeedItem,
    FanxiuShouyuanExplorationIncomeSpeedOcrImportResponse,
    FanxiuShouyuanExplorationOcrImportResponse,
    FanxiuShouyuanExplorationPersonalRankingItem,
    FanxiuShouyuanExplorationPersonalRankingOcrImportResponse,
    FanxiuShouyuanExplorationRecord,
    FanxiuShouyuanExplorationSnapshot,
    FanxiuSpiritArtifactAttributeRecognitionResponse,
    FanxiuSpiritArtifactAttributeValue,
    FanxiuSpiritArtifactHallSnapshot,
    FanxiuSpiritArtifactItem,
    FanxiuSpiritArtifactMarketItem,
    FanxiuSpiritArtifactMarketRecognitionResponse,
    FanxiuSpiritArtifactPartRow,
    FanxiuSpiritArtifactRankPart,
    FanxiuSpiritArtifactRankRecognitionResponse,
    FanxiuSpiritArtifactStorageBagChoice,
    FanxiuSpiritArtifactStorageBagItem,
    FanxiuSpiritArtifactStorageBagRecognitionResponse,
    FanxiuSpiritBeastHallSnapshot,
    FanxiuWardrobeHallSnapshot,
    FanxiuWardrobeItem,
)
from backend.core.fanxiu_ocr_utils import (
    _extract_magic_treasure_ocr_line_entries,
    _extract_magic_treasure_ocr_lines,
    _extract_ocr_line_entries,
    _extract_shape_rectangle,
    _extract_shape_text,
    _sanitize_ocr_text,
)
from backend.core.fanxiu_formation_ocr import (
    _build_formation_effect_details_from_ocr_document,
    _build_formation_requirements_from_ocr_document,
    _is_formation_requirement_condition,
    _looks_like_formation_effect_line,
    _match_formation_effect_detail_heading,
    _merge_formation_effect_text,
    _normalize_formation_effect_detail,
    _normalize_formation_effect_name,
    _normalize_formation_effect_text,
    _normalize_formation_requirement_text,
)
from backend.core.fanxiu_modao_shouyuan_ocr import (
    _build_modao_invasion_exchange_items_from_ocr_document,
    _build_modao_invasion_personal_rankings_from_ocr_document,
    _build_shouyuan_exploration_income_speed_from_ocr_document,
    _extract_first_int_from_text,
    _extract_last_int_from_text,
    _extract_modao_invasion_effective_cost,
    _extract_shouyuan_exploration_beast_crystal,
    _extract_shouyuan_exploration_labeled_total,
    _extract_shouyuan_exploration_search_count,
    _is_modao_invasion_non_item_line,
    _join_ocr_line_entries,
    _looks_like_modao_invasion_personal_ranking_line,
    _normalize_modao_invasion_item_name,
    _normalize_modao_invasion_personal_ranking_name,
    _normalize_modao_invasion_personal_ranking_plane,
    _parse_modao_invasion_cost_line,
    _parse_modao_invasion_header_line,
    _parse_modao_invasion_personal_ranking_header_line,
    _parse_modao_invasion_personal_ranking_plane_line,
)
from backend.core.fanxiu_player_profile_store import (
    list_fanxiu_player_profile_records,
    list_latest_fanxiu_player_profile_records,
)
from backend.core.fanxiu_tcp_flow import (
    decode_fanxiu_tcp_pcap,
    list_fanxiu_tcp_business_entries,
    list_fanxiu_tcp_captures,
    list_fanxiu_tcp_records,
)
from backend.core.fanxiu_mail_store import (
    ensure_fanxiu_mail_table,
    mark_fanxiu_mail_action,
    normalize_fanxiu_mail_time_text,
    normalize_fanxiu_mail_title,
)
from backend.core.fanxiu_mail_policy import (
    fanxiu_mail_action_policy_for_record,
    fanxiu_mail_action_policy_for_rewards,
    fanxiu_mail_rewards_from_payload,
)
from backend.core.fanxiu_mail_packet_sync import sync_fanxiu_mail_packets
from backend.core.fanxiu_packet_insight_worker import (
    fanxiu_packet_insight_worker,
    sync_fanxiu_capture_paths,
)
from backend.core.fanxiu_data_annotation_jobs import (
    DataAnnotationManualJobDefinition as _DataAnnotationManualJobDefinition,
    _DATA_ANNOTATION_MANUAL_JOB_REGISTRY,
    create_data_annotation_manual_job,
    data_annotation_manual_jobs_state,
    get_fanxiu_data_annotation_manual_job_definition as _data_annotation_manual_job_definition,
    pop_next_data_annotation_manual_job,
    read_data_annotation_manual_jobs,
    requeue_running_data_annotation_manual_jobs,
    register_fanxiu_data_annotation_manual_job,
)
from backend.core.fanxiu_data_annotation_models import (
    FanxiuDataAnnotationRuntimeLogEntry,
    FanxiuDataAnnotationRuntimeLogResponse,
    FanxiuDataAnnotationRuntimeStatus,
    FanxiuDataAnnotationRuntimeTaskRequest,
    FanxiuDataAnnotationRuntimeStopRequest,
    FanxiuDataAnnotationRuntimeGuardRequest,
    FanxiuDataAnnotationSchedulerTaskItem,
    FanxiuDataAnnotationSchedulerTasksResponse,
    FanxiuDataAnnotationSchedulerPlanItem,
    FanxiuDataAnnotationSchedulerPlanResponse,
    FanxiuDataAnnotationSchedulerRunDueRequest,
    FanxiuDataAnnotationSchedulerRunNowRequest,
    FanxiuDataAnnotationWorldFactsResponse,
)
from backend.core.fanxiu_data_annotation_state import (
    append_data_annotation_runtime_log_once,
    append_data_annotation_runtime_status_log,
    data_annotation_scheduler_task_state as _data_annotation_scheduler_task_state,
    data_annotation_task_due as _data_annotation_task_due,
    initial_data_annotation_runtime_status,
    initial_data_annotation_world_facts as _initial_data_annotation_world_facts,
    is_data_annotation_runtime_live_empty,
    next_data_annotation_scheduler_time as _core_next_data_annotation_scheduler_time,
    normalize_data_annotation_runtime_guard_items,
    parse_data_annotation_task_time,
    persist_data_annotation_runtime_status,
    read_data_annotation_runtime_status,
    read_data_annotation_json as _read_data_annotation_json,
    read_data_annotation_world_facts,
    record_data_annotation_scheduler_task_fact,
    write_data_annotation_json as _write_data_annotation_json,
    write_data_annotation_world_facts,
)
from backend.core.fanxiu_data_annotation_scheduler import (
    build_data_annotation_scheduler_plan,
    data_annotation_fact_time_text as _data_annotation_fact_time_text,
    data_annotation_scheduler_run_now_task as _core_data_annotation_scheduler_run_now_task,
    data_annotation_scheduler_order_key,
    data_annotation_scheduler_task_plan_reason,
    data_annotation_world_facts_summary,
    merge_data_annotation_scheduler_task_updates,
    repair_data_annotation_scheduler_tasks,
    sync_data_annotation_scheduler_tasks_from_world_facts,
)
from backend.core.fanxiu_data_annotation_scheduler_defaults import (
    default_data_annotation_scheduler_tasks as _default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu_data_annotation_runtime import (
    DataAnnotationRuntimeContainer as _DataAnnotationRuntimeContainer,
    DataAnnotationRuntimeGroupSpec as _DataAnnotationRuntimeGroupSpec,
    DataAnnotationRuntimeNodeSpec as _DataAnnotationRuntimeNodeSpec,
)
from backend.core.fanxiu_game_macro_annotation import (
    _annotate_game_macro_shape_with_ai,
    _build_game_macro_annotation_prompt,
    _build_game_macro_ocr_context,
    _clamp_game_macro_box,
    _coerce_float,
    _decode_game_macro_data_url_to_bytes,
    _extract_game_macro_annotation_json,
    _recognize_data_annotation_ocr_frame,
    _summarize_game_macro_ocr_document,
)
from backend.core.fanxiu_behavior_tree_service import (
    get_behavior_tree_status,
    start_behavior_tree_service,
    stop_behavior_tree_service,
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
SPIRIT_ARTIFACT_PARTS = {
    "血晶摩诃剑": ("柄", "刃", "穗", "鞘", "珠", "纹"),
    "天月落星幡": ("镜", "幅", "带", "杆", "印", "纹"),
    "弥罗宝光幢": ("焰", "柱", "环", "座", "珠", "纹"),
    "鸿古干天戈": ("锋", "芒", "珠", "坠", "柄", "气"),
    "青暝岁月灯": ("盏", "芯", "穗", "杆", "纹", "荧"),
    "苍烟神火炉": ("饰", "盖", "身", "柄", "光", "座"),
    "御海镇神图": ("卷", "瑚", "海", "轴", "灵", "山"),
}
SPIRIT_ARTIFACT_NAME_ALIASES = {
    "青冥岁月灯": "青暝岁月灯",
}
SPIRIT_ARTIFACT_CARD_REGIONS = (
    (0.16, 0.215, 0.38, 0.335),
    (0.16, 0.335, 0.38, 0.455),
    (0.16, 0.455, 0.38, 0.575),
    (0.62, 0.215, 0.84, 0.335),
    (0.62, 0.335, 0.84, 0.455),
    (0.62, 0.455, 0.84, 0.575),
)
SPIRIT_ARTIFACT_RANKED_QUALITIES = {"red", "blue_purple"}
SPIRIT_ARTIFACT_COMMON_ATTRIBUTE_BASES = {
    "混沌道威": ("chaos_power", Decimal("5000")),
    "攻击": ("attack", Decimal("10000")),
    "灵力": ("spirit_power", Decimal("1200000")),
    "气血": ("health", Decimal("1200000")),
    "守御": ("defense", Decimal("10000")),
}
SPIRIT_ARTIFACT_ATTRIBUTE_ALIASES = {
    "混沌灵威": "混沌道威",
    "防御": "守御",
}
SPIRIT_ARTIFACT_EXCLUSIVE_ATTRIBUTE_BASES = {
    "血晶摩诃剑": {
        "暴击附伤": Decimal("10000"),
        "暴击": Decimal("30000"),
    },
    "天月落星幡": {
        "功法附伤": Decimal("60000"),
        "招架": Decimal("30000"),
        "神通吸血": Decimal("10000"),
    },
    "弥罗宝光幢": {
        "法宝附伤": Decimal("60000"),
        "炼体附伤": Decimal("60000"),
        "闪避": Decimal("30000"),
    },
    "鸿古干天戈": {
        "灵兽附伤": Decimal("60000"),
        "仙语附伤": Decimal("60000"),
        "全技能减伤": Decimal("10000"),
    },
    "青暝岁月灯": {
        "灵宝抵御": Decimal("24000"),
        "功法抵御": Decimal("24000"),
        "全技能减伤": Decimal("8000"),
    },
    "苍烟神火炉": {
        "招架": Decimal("24000"),
        "灵兽附伤": Decimal("48000"),
        "法宝附伤": Decimal("48000"),
    },
    "御海镇神图": {
        "仙语附伤": Decimal("48000"),
        "灵暴附伤": Decimal("8000"),
        "灵暴": Decimal("24000"),
    },
}
FULLWIDTH_DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")


FANXIU_GAME_WINDOW2_STREAM_TOKEN_SCOPE = "fanxiu.game-window2:stream"
FANXIU_GAME_WINDOW2_STREAM_TOKEN_EXPIRE_HOURS = 2


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


def _normalize_spirit_artifact_rank_text(text: Any) -> str:
    return _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)


def _flatten_ocr_entries(line_entries: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [entry for group in line_entries for entry in group]


def _relative_region_to_pixels(frame: Any, region: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = region
    return (
        max(0, min(width, int(width * x1))),
        max(0, min(height, int(height * y1))),
        max(0, min(width, int(width * x2))),
        max(0, min(height, int(height * y2))),
    )


def _entries_in_relative_region(
    entries: list[dict[str, Any]],
    frame: Any,
    region: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    x1, y1, x2, y2 = _relative_region_to_pixels(frame, region)
    region_entries: list[dict[str, Any]] = []
    for entry in entries:
        entry_center_x = (float(entry.get("x", 0)) + float(entry.get("x2", entry.get("x", 0)))) / 2
        entry_center_y = float(entry.get("y", 0))
        if x1 <= entry_center_x <= x2 and y1 <= entry_center_y <= y2:
            region_entries.append(entry)
    return region_entries


def _extract_spirit_artifact_card_rank(entries: list[dict[str, Any]]) -> int:
    fragments = [
        _normalize_spirit_artifact_rank_text(entry.get("text"))
        for entry in sorted(entries, key=lambda item: (float(item.get("y", 0)), float(item.get("x", 0))))
    ]
    joined = "".join(fragment for fragment in fragments if fragment)
    for matched in re.findall(r"(\d{1,3})阶", joined):
        value = int(matched)
        if value >= 0:
            return value
    return 0


def _extract_spirit_artifact_card_rank_from_crop(frame: Any, region: tuple[float, float, float, float]) -> int:
    import cv2

    x1, y1, x2, y2 = _relative_region_to_pixels(frame, region)
    width = x2 - x1
    height = y2 - y1
    if width <= 0 or height <= 0:
        return 0

    crop_x1 = x1 + int(width * 0.18)
    crop_x2 = x1 + int(width * 0.55)
    crop_y1 = y1 + int(height * 0.06)
    crop_y2 = y1 + int(height * 0.65)
    crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
    if crop.size == 0:
        return 0
    if crop.ndim == 3 and crop.shape[2] == 4:
        crop = cv2.cvtColor(crop, cv2.COLOR_BGRA2BGR)
    crop = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
            temp_path = Path(temp_file.name)
        if not cv2.imwrite(str(temp_path), crop):
            return 0
        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        line_entries = _extract_ocr_line_entries(preview.get("document") or {})
    except OcrPreviewError:
        return 0
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    joined = "".join(_join_ocr_line_entries(group) for group in line_entries)
    for matched in re.findall(r"\d{1,3}", _normalize_spirit_artifact_rank_text(joined)):
        value = int(matched)
        if value > 0:
            return value
    return 0


def _fill_missing_spirit_artifact_ranks_from_card_crops(payload: dict[str, Any], frame: Any) -> dict[str, Any]:
    if not payload.get("matched"):
        return payload
    parts = payload.get("parts")
    if not isinstance(parts, list):
        return payload

    def normalized_rank(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    for part, region in zip(parts, SPIRIT_ARTIFACT_CARD_REGIONS):
        if not isinstance(part, dict):
            continue
        if str(part.get("quality") or "") not in SPIRIT_ARTIFACT_RANKED_QUALITIES:
            continue
        if normalized_rank(part.get("rank")) > 0:
            continue
        fallback_rank = _extract_spirit_artifact_card_rank_from_crop(frame, region)
        if fallback_rank > 0:
            part["rank"] = fallback_rank
    return payload


def _classify_spirit_artifact_card_color(frame: Any, region: tuple[float, float, float, float]) -> tuple[str, str]:
    import cv2
    import numpy as np

    x1, y1, x2, y2 = _relative_region_to_pixels(frame, region)
    if x2 <= x1 or y2 <= y1:
        return "unknown", ""

    width = x2 - x1
    height = y2 - y1
    sample_x1 = x1 + int(width * 0.30)
    sample_x2 = x1 + int(width * 0.92)
    sample_y1 = y1 + int(height * 0.10)
    sample_y2 = y1 + int(height * 0.24)
    patch = frame[sample_y1:sample_y2, sample_x1:sample_x2]
    if patch.size == 0:
        return "unknown", ""
    if patch.ndim == 3 and patch.shape[2] == 4:
        patch = cv2.cvtColor(patch, cv2.COLOR_BGRA2BGR)
    if patch.ndim != 3 or patch.shape[2] < 3:
        return "unknown", ""

    hsv = cv2.cvtColor(patch[:, :, :3], cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    red_mask = (((hue <= 12) | (hue >= 170)) & (saturation >= 45) & (value >= 70))
    yellow_mask = ((hue >= 15) & (hue <= 45) & (saturation >= 35) & (value >= 80))
    blue_purple_mask = ((hue >= 95) & (hue <= 165) & (saturation >= 30) & (value >= 60))
    red_ratio = float(np.count_nonzero(red_mask)) / float(red_mask.size)
    yellow_ratio = float(np.count_nonzero(yellow_mask)) / float(yellow_mask.size)
    blue_purple_ratio = float(np.count_nonzero(blue_purple_mask)) / float(blue_purple_mask.size)

    mean_bgr = patch[:, :, :3].reshape(-1, 3).mean(axis=0)
    background_color = f"#{int(mean_bgr[2]):02x}{int(mean_bgr[1]):02x}{int(mean_bgr[0]):02x}"
    if red_ratio >= 0.08:
        return "red", background_color
    if yellow_ratio >= 0.55 and blue_purple_ratio < 0.25:
        return "yellow", background_color
    if blue_purple_ratio >= 0.18:
        return "blue_purple", background_color
    if yellow_ratio >= 0.08:
        return "yellow", background_color
    return "unknown", background_color


def _spirit_artifact_lines_from_document(preview_document: dict[str, Any]) -> tuple[list[list[dict[str, Any]]], list[str]]:
    line_entries = _extract_ocr_line_entries(preview_document)
    lines = [_join_ocr_line_entries(group) for group in line_entries]
    return line_entries, [line for line in lines if line]


def _has_spirit_artifact_effect_line(line_entries: list[list[dict[str, Any]]], lines: list[str], frame: Any) -> bool:
    height = frame.shape[0]
    for group in line_entries:
        joined = _join_ocr_line_entries(group)
        if "灵器效果" not in joined:
            continue
        group_y = sum(float(entry.get("y", 0)) for entry in group) / max(len(group), 1)
        if group_y >= height * 0.45:
            return True
    return any("灵器效果" in line for line in lines)


def _spirit_artifact_name_edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    previous[right_index - 1] + (0 if left_char == right_char else 1),
                )
            )
        previous = current
    return previous[-1]


def _normalize_spirit_artifact_name_text(text: Any) -> str:
    return re.sub(r"[^\u4e00-\u9fff]", "", _sanitize_ocr_text(text)).replace("部件", "")


def _match_spirit_artifact_name_from_text(text: Any, *, allow_fuzzy: bool = False) -> str:
    line = _sanitize_ocr_text(text)
    if not line:
        return ""

    for artifact_name in SPIRIT_ARTIFACT_PARTS:
        if artifact_name in line:
            return artifact_name
    for alias, artifact_name in SPIRIT_ARTIFACT_NAME_ALIASES.items():
        if alias in line:
            return artifact_name
    if not allow_fuzzy:
        return ""

    normalized = _normalize_spirit_artifact_name_text(line)
    if len(normalized) < 2:
        return ""

    substring_matches = [
        artifact_name
        for artifact_name in SPIRIT_ARTIFACT_PARTS
        if normalized in artifact_name
    ]
    if len(substring_matches) == 1:
        return substring_matches[0]

    best_name = ""
    best_distance = 999
    best_score = -1.0
    for artifact_name in SPIRIT_ARTIFACT_PARTS:
        candidates = [normalized]
        if len(normalized) > len(artifact_name):
            candidates = [
                normalized[index:index + len(artifact_name)]
                for index in range(0, len(normalized) - len(artifact_name) + 1)
            ]
        for candidate in candidates:
            distance = _spirit_artifact_name_edit_distance(candidate, artifact_name)
            score = 1 - distance / max(len(candidate), len(artifact_name), 1)
            if distance < best_distance or (distance == best_distance and score > best_score):
                best_name = artifact_name
                best_distance = distance
                best_score = score

    if best_name and best_distance <= 2 and best_score >= 0.6:
        return best_name
    return ""


def _match_spirit_artifact_name(lines: list[str]) -> tuple[str, str]:
    for line in lines:
        if "部件" not in line:
            continue
        matched_name = _match_spirit_artifact_name_from_text(line, allow_fuzzy=True)
        if matched_name:
            return matched_name, line

    for line in lines:
        matched_name = _match_spirit_artifact_name_from_text(line)
        if matched_name:
            return matched_name, line
    return "", ""


def _match_spirit_artifact_part_title(lines: list[str]) -> tuple[str, str, str]:
    for line in lines:
        matched_name = _match_spirit_artifact_name_from_text(line, allow_fuzzy=True)
        if not matched_name:
            continue

        line_text = _sanitize_ocr_text(line)
        tails: list[str] = []
        if matched_name in line_text:
            tails.append(line_text.split(matched_name, 1)[1])
        for alias, artifact_name in SPIRIT_ARTIFACT_NAME_ALIASES.items():
            if artifact_name == matched_name and alias in line_text:
                tails.append(line_text.split(alias, 1)[1])
        if not tails:
            tails.append(line_text)

        for tail in tails:
            for part_name in SPIRIT_ARTIFACT_PARTS[matched_name]:
                if part_name in tail:
                    return matched_name, part_name, line
    return "", "", ""


def _parse_spirit_artifact_attribute_text(text: Any) -> tuple[str, str, Decimal] | None:
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    matched = re.match(r"^([\u4e00-\u9fff]+)[：:]?(\d+(?:\.\d+)?)(万|%)?", normalized)
    if not matched:
        return None

    raw_label = re.sub(r"^[满巅]+", "", matched.group(1))
    label = SPIRIT_ARTIFACT_ATTRIBUTE_ALIASES.get(raw_label, raw_label)
    value_text = f"{matched.group(2)}{matched.group(3) or ''}"
    try:
        value = Decimal(matched.group(2))
    except InvalidOperation:
        return None
    if matched.group(3) == "万":
        value *= Decimal("10000")
    return label, value_text, value


def _format_spirit_artifact_attribute_percent(value: Decimal, base_value: Decimal) -> str:
    if base_value <= 0:
        return "0%"
    percent = (value * Decimal("100") / base_value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{percent}%"


def _extract_spirit_artifact_attribute_lines(
    line_entries: list[list[dict[str, Any]]],
    frame: Any,
) -> list[str]:
    height, width = frame.shape[:2]
    marker_y: float | None = None
    for group in line_entries:
        joined = _join_ocr_line_entries(group)
        if "当前附加属性" not in joined:
            continue
        group_y = sum(float(entry.get("y", 0)) for entry in group) / max(len(group), 1)
        marker_y = group_y if marker_y is None else min(marker_y, group_y)
    if marker_y is None:
        return []

    lines: list[str] = []
    for group in line_entries:
        group_y = sum(float(entry.get("y", 0)) for entry in group) / max(len(group), 1)
        if group_y <= marker_y + 10 or group_y >= height * 0.76:
            continue

        left_entries = [
            entry
            for entry in group
            if (float(entry.get("x", 0)) + float(entry.get("x2", entry.get("x", 0)))) / 2 <= width * 0.52
        ]
        if not left_entries:
            continue
        joined = _join_ocr_line_entries(left_entries)
        if joined:
            lines.append(joined)
    return lines


def _build_spirit_artifact_attribute_recognition(
    preview_document: dict[str, Any],
    frame: Any,
) -> dict[str, Any]:
    line_entries, lines = _spirit_artifact_lines_from_document(preview_document)
    attribute_lines = _extract_spirit_artifact_attribute_lines(line_entries, frame)
    if not attribute_lines:
        return {
            "matched": False,
            "reason": "未识别到当前附加属性，已跳过",
            "lines": lines,
            "common_stats": {},
            "exclusive_stats": {},
            "attributes": [],
        }

    artifact_name, part_name, title_text = _match_spirit_artifact_part_title(lines)
    if not artifact_name or not part_name:
        return {
            "matched": False,
            "reason": "未识别到灵器部件标题",
            "lines": lines,
            "common_stats": {},
            "exclusive_stats": {},
            "attributes": [],
        }

    common_stats: dict[str, str] = {}
    exclusive_stats: dict[str, str] = {}
    attributes: list[dict[str, str]] = []
    peerless_values: list[int] = []
    exclusive_bases = SPIRIT_ARTIFACT_EXCLUSIVE_ATTRIBUTE_BASES.get(artifact_name, {})

    for source_text in attribute_lines:
        parsed = _parse_spirit_artifact_attribute_text(source_text)
        if parsed is None:
            continue
        label, raw_value, value = parsed
        if label == "灵器无双":
            peerless_value = int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            if peerless_value >= 0:
                peerless_values.append(peerless_value)
                attributes.append(
                    {
                        "label": label,
                        "percent": f"{peerless_value}%",
                        "raw_value": raw_value,
                        "source_text": source_text,
                    }
                )
            continue
        if label in SPIRIT_ARTIFACT_COMMON_ATTRIBUTE_BASES:
            field_key, base_value = SPIRIT_ARTIFACT_COMMON_ATTRIBUTE_BASES[label]
            percent = _format_spirit_artifact_attribute_percent(value, base_value)
            common_stats[field_key] = percent
        elif label in exclusive_bases:
            percent = _format_spirit_artifact_attribute_percent(value, exclusive_bases[label])
            exclusive_stats[label] = percent
        else:
            continue
        attributes.append(
            {
                "label": label,
                "percent": percent,
                "raw_value": raw_value,
                "source_text": source_text,
            }
        )

    return {
        "matched": True,
        "artifact_name": artifact_name,
        "part_name": part_name,
        "title_text": title_text,
        "lines": lines,
        "artifact_peerless_1": peerless_values[0] if len(peerless_values) >= 1 else 0,
        "artifact_peerless_2": peerless_values[1] if len(peerless_values) >= 2 else 0,
        "common_stats": common_stats,
        "exclusive_stats": exclusive_stats,
        "attributes": attributes,
    }


def _extract_spirit_artifact_market_cost(lines: list[str], item_line_index: int) -> int:
    candidate_lines = [lines[item_line_index]]
    if item_line_index + 1 < len(lines):
        candidate_lines.append(lines[item_line_index + 1])

    for line in candidate_lines:
        normalized = _normalize_spirit_artifact_rank_text(line)
        if "兑换所需" not in normalized:
            continue
        matched = re.search(r"兑换所需[：:]?.*?(\d+)", normalized)
        if matched:
            return max(0, int(matched.group(1))) or 80
    return 80


def _extract_spirit_artifact_market_currency(line_entries: list[list[dict[str, Any]]], frame: Any) -> int:
    height, width = frame.shape[:2]
    candidates: list[tuple[float, int]] = []
    for entry in _flatten_ocr_entries(line_entries):
        center_x = (float(entry.get("x", 0)) + float(entry.get("x2", entry.get("x", 0)))) / 2
        center_y = float(entry.get("y", 0))
        if center_x < width * 0.55 or center_y > height * 0.18:
            continue
        normalized = _normalize_spirit_artifact_rank_text(entry.get("text"))
        for matched in re.findall(r"\d{1,9}", normalized):
            value = int(matched)
            if value >= 0:
                candidates.append((center_x, value))
    if not candidates:
        return 0
    return max(candidates, key=lambda item: item[0])[1]


def _build_spirit_artifact_market_recognition(
    preview_document: dict[str, Any],
    frame: Any,
) -> dict[str, Any]:
    line_entries, lines = _spirit_artifact_lines_from_document(preview_document)
    market_currency_count = _extract_spirit_artifact_market_currency(line_entries, frame)
    if not any("珍宝阁" in line for line in lines):
        return {
            "matched": False,
            "reason": "未识别到珍宝阁，已跳过",
            "market_currency_count": 0,
            "lines": lines,
            "items": [],
        }
    if not any("兑换所需" in line for line in lines):
        return {
            "matched": False,
            "reason": "未识别到兑换所需，已跳过",
            "market_currency_count": market_currency_count,
            "lines": lines,
            "items": [],
        }

    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for line_index, line in enumerate(lines):
        artifact_name, part_name, _title_text = _match_spirit_artifact_part_title([line])
        if not artifact_name or not part_name:
            continue
        item_key = (artifact_name, part_name)
        if item_key in seen:
            continue
        seen.add(item_key)
        items.append(
            {
                "order": len(items) + 1,
                "artifact_name": artifact_name,
                "part_name": part_name,
                "cost": _extract_spirit_artifact_market_cost(lines, line_index),
            }
        )

    if not items:
        return {
            "matched": False,
            "reason": "未识别到可兑换灵器部件",
            "market_currency_count": market_currency_count,
            "lines": lines,
            "items": [],
        }

    return {
        "matched": True,
        "market_currency_count": market_currency_count,
        "lines": lines,
        "items": items,
    }


def _spirit_artifact_lcs_length(left: str, right: str) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    for left_char in left:
        current = [0]
        for right_index, right_char in enumerate(right, start=1):
            if left_char == right_char:
                current.append(previous[right_index - 1] + 1)
            else:
                current.append(max(previous[right_index], current[right_index - 1]))
        previous = current
    return previous[-1]


def _score_spirit_artifact_name_fragment(text: Any, artifact_name: str) -> float:
    normalized = _normalize_spirit_artifact_name_text(text)
    if not normalized:
        return 0.0
    if artifact_name in normalized:
        return 1.0
    for alias, canonical_name in SPIRIT_ARTIFACT_NAME_ALIASES.items():
        if canonical_name == artifact_name and alias in normalized:
            return 1.0

    cleaned = re.sub(r"(灵器|部件|自选|任选|选择|可选|支持|礼包|宝箱|箱)", "", normalized)
    if not cleaned:
        return 0.0
    if len(cleaned) >= 2 and cleaned in artifact_name:
        return min(0.95, 0.5 + 0.1 * len(cleaned))
    return _spirit_artifact_lcs_length(cleaned, artifact_name) / max(len(artifact_name), 1)


def _match_spirit_artifact_part_choice_text(text: Any) -> tuple[str, str, str]:
    source_text = _sanitize_ocr_text(text)
    artifact_name, part_name, title_text = _match_spirit_artifact_part_title([source_text])
    if artifact_name and part_name:
        return artifact_name, part_name, title_text

    normalized = _normalize_spirit_artifact_name_text(source_text)
    if len(normalized) < 2:
        return "", "", source_text

    candidates: list[tuple[float, str, str]] = []
    for candidate_artifact_name, part_names in SPIRIT_ARTIFACT_PARTS.items():
        artifact_score = _score_spirit_artifact_name_fragment(normalized, candidate_artifact_name)
        if artifact_score < 0.35:
            continue
        for candidate_part_name in part_names:
            if candidate_part_name not in normalized:
                continue
            score = artifact_score + 0.15
            if candidate_artifact_name in normalized:
                tail = normalized.split(candidate_artifact_name, 1)[1]
                if candidate_part_name in tail:
                    score += 0.15
                elif candidate_part_name in candidate_artifact_name:
                    score -= 0.1
            candidates.append((score, candidate_artifact_name, candidate_part_name))

    if not candidates:
        return "", "", source_text
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best_artifact_name, best_part_name = candidates[0]
    if best_score < 0.55:
        return "", "", source_text
    if len(candidates) >= 2:
        second_score, second_artifact_name, second_part_name = candidates[1]
        if (
            best_score - second_score < 0.08
            and (best_artifact_name, best_part_name) != (second_artifact_name, second_part_name)
        ):
            return "", "", source_text
    return best_artifact_name, best_part_name, source_text


def _spirit_artifact_name_fragments(artifact_name: str) -> list[str]:
    fragments = [artifact_name]
    fragments.extend(alias for alias, canonical_name in SPIRIT_ARTIFACT_NAME_ALIASES.items() if canonical_name == artifact_name)
    if len(artifact_name) >= 4:
        fragments.append(artifact_name[-3:])
        fragments.append(artifact_name[:2])
    return sorted({fragment for fragment in fragments if len(fragment) >= 2}, key=len, reverse=True)


def _match_spirit_artifact_name_from_bag_title(text: Any) -> str:
    normalized = _normalize_spirit_artifact_name_text(text)
    normalized = re.sub(r"(升品|自选|宝匣|宝箱|礼包|灵器|部件|壹|贰|叁|肆|伍|陆|柒|捌|玖|拾)", "", normalized)
    if len(normalized) < 2:
        return ""

    scored = [
        (_score_spirit_artifact_name_fragment(normalized, artifact_name), artifact_name)
        for artifact_name in SPIRIT_ARTIFACT_PARTS
    ]
    scored.sort(reverse=True)
    best_score, best_name = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0
    if best_score >= 0.6 and best_score - second_score >= 0.12:
        return best_name
    return ""


def _match_spirit_artifact_part_choices_text(text: Any, *, artifact_hint: str = "") -> list[tuple[str, str, str]]:
    source_text = _sanitize_ocr_text(text)
    if not source_text:
        return []

    artifact_mentions: list[tuple[int, int, str, str]] = []
    for artifact_name in SPIRIT_ARTIFACT_PARTS:
        for matched in re.finditer(re.escape(artifact_name), source_text):
            artifact_mentions.append((matched.start(), matched.end(), artifact_name, artifact_name))
    for alias, artifact_name in SPIRIT_ARTIFACT_NAME_ALIASES.items():
        for matched in re.finditer(re.escape(alias), source_text):
            artifact_mentions.append((matched.start(), matched.end(), alias, artifact_name))

    artifact_mentions.sort(key=lambda item: item[0])
    choices: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, (start, end, matched_name, artifact_name) in enumerate(artifact_mentions):
        next_start = artifact_mentions[index + 1][0] if index + 1 < len(artifact_mentions) else len(source_text)
        tail = source_text[end:next_start]
        for part_name in SPIRIT_ARTIFACT_PARTS[artifact_name]:
            if part_name not in tail:
                continue
            choice_key = (artifact_name, part_name)
            if choice_key in seen:
                continue
            seen.add(choice_key)
            choices.append((artifact_name, part_name, f"{matched_name}·{part_name}"))
            break

    if choices:
        return choices

    if artifact_hint in SPIRIT_ARTIFACT_PARTS:
        hint_choices: list[tuple[str, str, str]] = []
        seen_parts: set[str] = set()
        for fragment in _spirit_artifact_name_fragments(artifact_hint):
            for matched in re.finditer(re.escape(fragment), source_text):
                tail = source_text[matched.end():matched.end() + 8]
                for part_name in SPIRIT_ARTIFACT_PARTS[artifact_hint]:
                    part_index = tail.find(part_name)
                    if part_index < 0 or part_index > 2 or part_name in seen_parts:
                        continue
                    seen_parts.add(part_name)
                    part_tail = tail[part_index:]
                    suffix = ""
                    suffix_match = re.match(rf"{re.escape(part_name)}(曜仙[镜境])", part_tail)
                    if suffix_match:
                        suffix = suffix_match.group(1)
                    hint_choices.append((artifact_hint, part_name, f"{fragment}·{part_name}{suffix}"))
                    break
        if hint_choices:
            return hint_choices

    artifact_name, part_name, source_text = _match_spirit_artifact_part_choice_text(source_text)
    if not artifact_name or not part_name:
        return []
    return [(artifact_name, part_name, source_text)]


def _normalize_spirit_artifact_storage_bag_title(text: Any) -> str:
    normalized = _normalize_spirit_artifact_rank_text(text)
    normalized = re.sub(r"^(?:[xX×*])?\d{1,6}(?!选)", "", normalized)
    normalized = re.sub(r"^(?:数量|拥有|持有)[：:]?(?:[xX×*])?\d{1,6}", "", normalized)
    return normalized


def _looks_like_spirit_artifact_storage_bag_title(text: Any) -> bool:
    normalized = _normalize_spirit_artifact_rank_text(text)
    if not normalized:
        return False
    if any(keyword in normalized for keyword in ("境界要求", "选择奖励", "列表", "确定", "装有")):
        return False
    return "自选" in normalized or "宝匣" in normalized


def _parse_spirit_artifact_storage_bag_quantity_text(text: Any) -> int | None:
    normalized = _normalize_spirit_artifact_rank_text(text)
    if re.fullmatch(r"(?:[xX×*])?\d{1,6}", normalized):
        return int(re.search(r"\d{1,6}", normalized).group(0))
    matched = re.match(r"^(?:[xX×*])?(\d{1,6})(?!选)", normalized)
    if matched:
        return int(matched.group(1))
    return None


def _extract_spirit_artifact_storage_bag_title(
    group: list[dict[str, Any]],
) -> tuple[str, int, float, str] | None:
    joined = _join_ocr_line_entries(group)
    if not _looks_like_spirit_artifact_storage_bag_title(joined):
        return None

    title_entries = [
        entry
        for entry in group
        if _looks_like_spirit_artifact_storage_bag_title(entry.get("text"))
    ]
    title_entry = max(title_entries, key=lambda entry: len(_sanitize_ocr_text(entry.get("text"))), default=None)
    title_text = _normalize_spirit_artifact_storage_bag_title(title_entry.get("text") if title_entry else joined)
    if not _looks_like_spirit_artifact_storage_bag_title(title_text):
        title_text = _normalize_spirit_artifact_storage_bag_title(joined)
    if not _looks_like_spirit_artifact_storage_bag_title(title_text):
        return None

    quantity = _parse_spirit_artifact_storage_bag_quantity_text(joined) or 0
    title_x = float(title_entry.get("x", 0)) if title_entry else min(float(entry.get("x", 0)) for entry in group)
    left_quantity_candidates: list[tuple[float, int]] = []
    for entry in group:
        entry_x2 = float(entry.get("x2", entry.get("x", 0)))
        if entry_x2 > title_x + 8:
            continue
        parsed_quantity = _parse_spirit_artifact_storage_bag_quantity_text(entry.get("text"))
        if parsed_quantity is not None:
            left_quantity_candidates.append((entry_x2, parsed_quantity))
    if left_quantity_candidates:
        quantity = max(left_quantity_candidates, key=lambda item: item[0])[1]

    title_y = sum(float(entry.get("y", 0)) for entry in group) / max(len(group), 1)
    return title_text, quantity, title_y, _match_spirit_artifact_name_from_bag_title(title_text)


def _extract_spirit_artifact_storage_bag_choices(
    line_entries: list[list[dict[str, Any]]],
    *,
    start_index: int,
    stop_y: float | None,
    artifact_hint: str = "",
) -> list[dict[str, Any]]:
    choices: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for group in line_entries[start_index + 1:]:
        group_y = sum(float(entry.get("y", 0)) for entry in group) / max(len(group), 1)
        if stop_y is not None and group_y >= stop_y:
            break
        joined = _join_ocr_line_entries(group)
        if not joined or "自选" in joined:
            continue

        matched_in_entries = False
        for entry in group:
            raw_text = _sanitize_ocr_text(entry.get("text"))
            for artifact_name, part_name, source_text in _match_spirit_artifact_part_choices_text(
                raw_text,
                artifact_hint=artifact_hint,
            ):
                choice_key = (source_text, artifact_name, part_name)
                if choice_key in seen:
                    continue
                seen.add(choice_key)
                choices.append(
                    {
                        "order": len(choices) + 1,
                        "raw_name": source_text,
                        "artifact_name": artifact_name,
                        "part_name": part_name,
                    }
                )
                matched_in_entries = True

        if matched_in_entries:
            continue
        for artifact_name, part_name, source_text in _match_spirit_artifact_part_choices_text(
            joined,
            artifact_hint=artifact_hint,
        ):
            choice_key = (source_text, artifact_name, part_name)
            if choice_key in seen:
                continue
            seen.add(choice_key)
            choices.append(
                {
                    "order": len(choices) + 1,
                    "raw_name": source_text,
                    "artifact_name": artifact_name,
                    "part_name": part_name,
                }
            )
    return choices


def _build_spirit_artifact_storage_bag_recognition(
    preview_document: dict[str, Any],
    frame: Any,
) -> dict[str, Any]:
    line_entries, lines = _spirit_artifact_lines_from_document(preview_document)
    title_rows: list[tuple[int, str, int, float, str]] = []
    for line_index, group in enumerate(line_entries):
        parsed_title = _extract_spirit_artifact_storage_bag_title(group)
        if parsed_title is None:
            continue
        title, quantity, title_y, artifact_hint = parsed_title
        title_rows.append((line_index, title, quantity, title_y, artifact_hint))

    if not title_rows:
        return {
            "matched": False,
            "reason": "未识别到自选箱标题",
            "lines": lines,
            "items": [],
        }

    items: list[dict[str, Any]] = []
    for title_index, (line_index, title, quantity, _title_y, artifact_hint) in enumerate(title_rows):
        stop_y = title_rows[title_index + 1][3] if title_index + 1 < len(title_rows) else None
        choices = _extract_spirit_artifact_storage_bag_choices(
            line_entries,
            start_index=line_index,
            stop_y=stop_y,
            artifact_hint=artifact_hint,
        )
        if not choices:
            continue
        items.append(
            {
                "order": len(items) + 1,
                "title": title,
                "quantity": quantity,
                "choices": choices,
            }
        )

    if not items:
        return {
            "matched": False,
            "reason": "未识别到自选支持类型",
            "lines": lines,
            "items": [],
        }

    return {
        "matched": True,
        "lines": lines,
        "items": items,
    }


def _build_spirit_artifact_rank_recognition(
    preview_document: dict[str, Any],
    frame: Any,
) -> dict[str, Any]:
    line_entries, lines = _spirit_artifact_lines_from_document(preview_document)
    if not _has_spirit_artifact_effect_line(line_entries, lines, frame):
        return {
            "matched": False,
            "reason": "未识别到灵器效果，已跳过",
            "lines": lines,
            "parts": [],
        }

    artifact_name, title_text = _match_spirit_artifact_name(lines)
    if not artifact_name:
        return {
            "matched": False,
            "reason": "未识别到灵器名称",
            "title_text": title_text,
            "lines": lines,
            "parts": [],
        }

    entries = _flatten_ocr_entries(line_entries)
    parts: list[dict[str, Any]] = []
    for part_name, region in zip(SPIRIT_ARTIFACT_PARTS[artifact_name], SPIRIT_ARTIFACT_CARD_REGIONS):
        card_entries = _entries_in_relative_region(entries, frame, region)
        quality, background_color = _classify_spirit_artifact_card_color(frame, region)
        rank = (
            _extract_spirit_artifact_card_rank(card_entries)
            if quality in SPIRIT_ARTIFACT_RANKED_QUALITIES
            else 0
        )
        parts.append(
            {
                "part_name": part_name,
                "rank": rank,
                "realm": 0,
                "quality": quality,
                "background_color": background_color,
            }
        )

    return {
        "matched": True,
        "artifact_name": artifact_name,
        "title_text": title_text,
        "lines": lines,
        "parts": parts,
    }


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

    conditions = [NoteNode.id == normalized_note_id, NoteNode.legacy_id == normalized_note_id]
    if normalized_note_id.isdecimal():
        conditions.append(NoteNode.numeric_id == int(normalized_note_id))
    statement = select(NoteNode).where(
        or_(*conditions),
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

    source_refs = note_ref_aliases(source_note)
    target_ref = note_edge_ref(target_note)
    edges = session.exec(
        select(NoteEdge).where(
            (NoteEdge.source_id.in_(source_refs)) | (NoteEdge.target_id.in_(source_refs))
        )
    ).all()

    for edge in edges:
        next_source_id = target_ref if str(edge.source_id) in source_refs else edge.source_id
        next_target_id = target_ref if str(edge.target_id) in source_refs else edge.target_id
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
                "is_fanxiu": bool(
                    match_fanxiu_process_fields(
                        name=str(item.get("name") or ""),
                        command_line=str(item.get("command_line") or ""),
                        cwd=item.get("cwd"),
                    )
                ),
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


@status_router.post("/packet-capture/snapshot", response_model=FanxiuPacketCaptureSnapshot)
def get_fanxiu_packet_capture_snapshot(
    payload: FanxiuPacketCaptureSnapshotRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuPacketCaptureSnapshot.model_validate(
        build_fanxiu_packet_capture_snapshot(payload.dns_hosts, resolve_dns=payload.resolve_dns)
    )


@status_router.get("/packet-capture/tcp/captures", response_model=FanxiuTcpCaptureListResponse)
def list_fanxiu_packet_tcp_captures(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuTcpCaptureListResponse.model_validate(
        list_fanxiu_tcp_captures(limit=limit)
    )


@status_router.get("/packet-capture/tcp/records", response_model=FanxiuTcpRecordListResponse)
def list_fanxiu_packet_tcp_records(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuTcpRecordListResponse.model_validate(
        list_fanxiu_tcp_records(limit=limit)
    )


@status_router.get("/packet-capture/tcp/business-entries", response_model=FanxiuTcpBusinessEntryListResponse)
def list_fanxiu_packet_tcp_business_entries(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    category: str = Query("", max_length=80),
    protocol: str = Query("", max_length=120),
    hidden_protocols: str = Query("", max_length=8000),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    hidden_protocol_list = [
        item.strip()
        for item in hidden_protocols.split(",")
        if item.strip()
    ]
    return FanxiuTcpBusinessEntryListResponse.model_validate(
        list_fanxiu_tcp_business_entries(
            category=category,
            protocol=protocol,
            hidden_protocols=hidden_protocol_list,
            page=page,
            page_size=page_size,
        )
    )


@status_router.post("/packet-capture/tcp/decode", response_model=FanxiuTcpDecodeResponse)
def decode_fanxiu_packet_tcp_capture(
    payload: FanxiuTcpDecodeRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        result = decode_fanxiu_tcp_pcap(
            payload.pcap,
            stream=payload.stream,
            server_host=payload.server_host,
            persist=payload.persist,
        )
        if payload.persist:
            sync_fanxiu_activity_packets(force=False)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FanxiuTcpDecodeResponse.model_validate(result)


@status_router.get("/packet-capture/tcp/worldline-activity/latest")
def get_fanxiu_latest_worldline_activity_schedule(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_fanxiu_write_permission(current_user, session)
    return get_fanxiu_activity_packet_schedule()


@status_router.post("/activity-packet-sync", response_model=FanxiuActivityPacketSyncResponse)
def sync_fanxiu_activity_packet_history(
    payload: FanxiuActivityPacketSyncRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuActivityPacketSyncResponse.model_validate(
        sync_fanxiu_activity_packets(force=payload.force)
    )


@status_router.get("/packet-capture/tcp/insights", response_model=FanxiuPacketInsightResponse)
def get_fanxiu_packet_insights(
    auto_sync: bool = Query(False),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuPacketInsightResponse.model_validate(
        get_fanxiu_packet_runtime_insights(sync=auto_sync)
    )


@status_router.get("/packet-capture/tcp/player-profiles", response_model=FanxiuPlayerProfileRecordListResponse)
def list_fanxiu_packet_player_profiles(
    limit: int = Query(1000, ge=1, le=5000),
    history: bool = Query(False),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    if history:
        records = list_fanxiu_player_profile_records(session, limit=limit)
    else:
        records = list_latest_fanxiu_player_profile_records(session, limit=limit)
    return FanxiuPlayerProfileRecordListResponse(ok=True, count=len(records), records=records)


def _fanxiu_mail_create_time_sort_value(row: FanxiuMailRecord) -> float:
    if row.create_time_ms is not None:
        try:
            return float(row.create_time_ms)
        except (TypeError, ValueError):
            pass
    normalized = normalize_fanxiu_mail_time_text(row.create_time_text)
    if not normalized:
        return 0.0
    try:
        return datetime.strptime(normalized, "%Y年%m月%d日%H:%M").timestamp() * 1000
    except ValueError:
        return 0.0


def _fanxiu_mail_record_sort_key(row: FanxiuMailRecord) -> tuple[float, float, float]:
    return (
        _fanxiu_mail_create_time_sort_value(row),
        float(row.last_seen_at or 0),
        float(row.updated_at or 0),
    )


@status_router.get("/mail-records", response_model=FanxiuMailRecordListResponse)
def list_fanxiu_mail_records(
    limit: int = Query(2000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    status: str = Query(""),
    action_policy: str = Query(""),
    source: str = Query("packet"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    ensure_fanxiu_mail_table()
    stmt = select(FanxiuMailRecord)
    status_text = status.strip() if isinstance(status, str) else ""
    action_policy_text = action_policy.strip() if isinstance(action_policy, str) else ""
    source_text = source.strip().lower() if isinstance(source, str) else "packet"
    if status_text:
        stmt = stmt.where(FanxiuMailRecord.status == status_text)
    if action_policy_text:
        stmt = stmt.where(FanxiuMailRecord.action_policy == action_policy_text)
    if source_text and source_text != "all":
        stmt = stmt.where(FanxiuMailRecord.source == source_text)
    stmt = stmt.order_by(FanxiuMailRecord.last_seen_at.desc(), FanxiuMailRecord.updated_at.desc())
    rows = session.exec(stmt).all()
    rows = sorted(rows, key=_fanxiu_mail_record_sort_key, reverse=True)
    total_count = len(rows)
    rows = rows[offset:offset + limit]
    records = [row.model_dump() for row in rows]
    return FanxiuMailRecordListResponse(
        ok=True,
        count=len(records),
        total=total_count,
        offset=offset,
        limit=limit,
        records=records,
    )


@status_router.post("/mail-records/sync-packets", response_model=FanxiuMailPacketSyncResponse)
def sync_fanxiu_mail_records_from_packets(
    payload: FanxiuMailPacketSyncRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuMailPacketSyncResponse.model_validate(
        sync_fanxiu_mail_packets(session, clear_existing=payload.clear_existing)
    )


@status_router.get("/packet-capture/tcp/storage-bag")
def get_fanxiu_packet_storage_bag(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return get_fanxiu_packet_storage_bag_snapshot(sync=False)


@status_router.post("/packet-capture/tcp/insights/sync", response_model=FanxiuPacketInsightResponse)
def sync_fanxiu_packet_insights(
    payload: FanxiuPacketInsightSyncRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuPacketInsightResponse.model_validate(
        sync_fanxiu_packet_runtime_insights(force=payload.force)
    )


@status_router.get("/packet-capture/tcp/worker/status")
def get_fanxiu_packet_worker_status(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_fanxiu_write_permission(current_user, session)
    return fanxiu_packet_insight_worker.status()


@status_router.post("/packet-capture/tcp/worker/realtime-scan")
def run_fanxiu_packet_worker_realtime_scan(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_fanxiu_write_permission(current_user, session)
    return fanxiu_packet_insight_worker.scan_once()


@status_router.post("/packet-capture/tcp/worker/maintenance")
def run_fanxiu_packet_worker_maintenance(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_fanxiu_write_permission(current_user, session)
    return fanxiu_packet_insight_worker.maintenance_once()


@status_router.get("/capture-runtime/status", response_model=FanxiuCaptureRuntimeStatus)
def get_fanxiu_capture_runtime_status(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuCaptureRuntimeStatus.model_validate(fanxiu_capture_runtime_service.status())


@status_router.post("/capture-runtime/ensure", response_model=FanxiuCaptureRuntimeStatus)
def ensure_fanxiu_capture_runtime(
    payload: FanxiuCaptureRuntimeRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuCaptureRuntimeStatus.model_validate(
        fanxiu_capture_runtime_service.ensure_running(payload.reason)
    )


@status_router.post("/capture-runtime/release", response_model=FanxiuCaptureRuntimeStatus)
def release_fanxiu_capture_runtime(
    payload: FanxiuCaptureRuntimeRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuCaptureRuntimeStatus.model_validate(fanxiu_capture_runtime_service.release(payload.reason))


@status_router.post("/capture-runtime/stop", response_model=FanxiuCaptureRuntimeStatus)
def stop_fanxiu_capture_runtime(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuCaptureRuntimeStatus.model_validate(fanxiu_capture_runtime_service.force_stop("api-stop"))


@status_router.get("/packet-capture/activity/status", response_model=FanxiuPacketActivityStatus)
def get_fanxiu_packet_activity_status(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuPacketActivityStatus.model_validate(fanxiu_packet_activity_service.status())


@status_router.get("/packet-capture/activity/history", response_model=FanxiuPacketActivityHistoryResponse)
def get_fanxiu_packet_activity_history(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    key: str = Query(""),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuPacketActivityHistoryResponse.model_validate(
        fanxiu_packet_activity_service.history(offset=offset, limit=limit, key=key)
    )


@status_router.get("/packet-capture/activity/stream", response_model=FanxiuPacketActivityStreamResponse)
def get_fanxiu_packet_activity_stream(
    key: str = Query(""),
    max_bytes: int = Query(32768, ge=1024, le=65536),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuPacketActivityStreamResponse.model_validate(
        fanxiu_packet_activity_service.stream(key=key, max_bytes=max_bytes)
    )


@status_router.post("/packet-capture/activity/start", response_model=FanxiuPacketActivityStatus)
def start_fanxiu_packet_activity(
    payload: FanxiuPacketActivityStartRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuPacketActivityStatus.model_validate(fanxiu_packet_activity_service.start(payload.bind_ip))


@status_router.post("/packet-capture/activity/stop", response_model=FanxiuPacketActivityStatus)
def stop_fanxiu_packet_activity(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuPacketActivityStatus.model_validate(fanxiu_packet_activity_service.stop())


@status_router.delete("/packet-capture/activity", response_model=FanxiuPacketActivityStatus)
def clear_fanxiu_packet_activity(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuPacketActivityStatus.model_validate(fanxiu_packet_activity_service.clear())


def _recommended_fanxiu_proxy_address(status: dict[str, Any]) -> str:
    addresses = [str(item) for item in status.get("addresses") or []]
    for address in addresses:
        if not address.startswith("127.") and not address.startswith("198.18."):
            return address
    return addresses[0] if addresses else ""


def _saved_fanxiu_capture_host_port() -> tuple[str, int]:
    state = fanxiu_packet_proxy_service.session_state()
    host = str(state.get("host") or "0.0.0.0").strip()
    try:
        port = int(state.get("port") or 8899)
    except (TypeError, ValueError):
        port = 8899
    return host, port


def _ensure_fanxiu_capture_session(device_id: str = "") -> Optional[dict[str, Any]]:
    state = fanxiu_packet_proxy_service.session_state()
    if not state.get("active"):
        return None

    host, port = _saved_fanxiu_capture_host_port()
    selected_device = str(device_id or state.get("device_id") or "").strip()
    proxy_status = fanxiu_packet_proxy_service.status()
    needs_start = (
        not proxy_status.get("running")
        or str(proxy_status.get("host") or "") != host
        or int(proxy_status.get("port") or 0) != port
    )
    if needs_start:
        try:
            proxy_status = fanxiu_packet_proxy_service.start(host, port)
        except RuntimeError as exc:
            fanxiu_packet_proxy_service.save_session_state(
                active=True,
                host=host,
                port=port,
                device_id=selected_device,
                target_proxy=str(state.get("target_proxy") or ""),
                last_error=f"恢复 Python 抓包代理失败：{exc}",
            )
            return None

    target_proxy = _recommended_fanxiu_proxy_address(proxy_status) or str(state.get("target_proxy") or "")
    android_status = fanxiu_android_proxy_service.status(
        device_id=selected_device,
        target_proxy=target_proxy,
    )
    if target_proxy and android_status.get("available") and not android_status.get("matches_target"):
        try:
            android_status = fanxiu_android_proxy_service.set_http_proxy(
                target_proxy,
                device_id=str(android_status.get("device_id") or selected_device),
            )
        except Exception as exc:
            android_status["last_error"] = f"恢复安卓代理失败：{exc}"

    fanxiu_packet_proxy_service.save_session_state(
        active=True,
        host=host,
        port=port,
        device_id=str(android_status.get("device_id") or selected_device),
        target_proxy=target_proxy,
        last_error=str(android_status.get("last_error") or ""),
    )
    return android_status


def _fanxiu_packet_capture_session_status(
    *,
    android_status: Optional[dict[str, Any]] = None,
    device_id: str = "",
) -> dict[str, Any]:
    if android_status is None:
        android_status = _ensure_fanxiu_capture_session(device_id=device_id)
    proxy_status = fanxiu_packet_proxy_service.status()
    state = fanxiu_packet_proxy_service.session_state()
    target_proxy = _recommended_fanxiu_proxy_address(proxy_status) or str(state.get("target_proxy") or "")
    android = android_status or fanxiu_android_proxy_service.status(
        device_id=device_id,
        target_proxy=target_proxy,
    )
    if target_proxy and not android.get("target_proxy"):
        android["target_proxy"] = target_proxy
        android["matches_target"] = android.get("http_proxy") == target_proxy
    last_error = str(android.get("last_error") or proxy_status.get("last_error") or state.get("last_error") or "")
    return {
        "active": bool(proxy_status.get("running") and android.get("matches_target")),
        "target_proxy": target_proxy,
        "proxy": proxy_status,
        "android": android,
        "last_error": last_error,
    }


@status_router.get("/packet-capture/session/status", response_model=FanxiuPacketCaptureSessionStatus)
def get_fanxiu_packet_capture_session_status(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuPacketCaptureSessionStatus.model_validate(
        _fanxiu_packet_capture_session_status()
    )


@status_router.post("/packet-capture/session/start", response_model=FanxiuPacketCaptureSessionStatus)
def start_fanxiu_packet_capture_session(
    payload: FanxiuPacketProxyStartRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        proxy_status = fanxiu_packet_proxy_service.start(payload.host, payload.port)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    target_proxy = _recommended_fanxiu_proxy_address(proxy_status)
    android_status: dict[str, Any]
    try:
        android_status = fanxiu_android_proxy_service.set_http_proxy(
            target_proxy,
            device_id=payload.device_id,
        )
    except Exception as exc:
        android_status = fanxiu_android_proxy_service.status(
            device_id=payload.device_id,
            target_proxy=target_proxy,
        )
        android_status["last_error"] = str(exc)

    fanxiu_packet_proxy_service.save_session_state(
        active=True,
        host=payload.host,
        port=payload.port,
        device_id=str(android_status.get("device_id") or payload.device_id),
        target_proxy=target_proxy,
        last_error=str(android_status.get("last_error") or ""),
    )
    return FanxiuPacketCaptureSessionStatus.model_validate(
        _fanxiu_packet_capture_session_status(android_status=android_status, device_id=payload.device_id)
    )


@status_router.post("/packet-capture/session/stop", response_model=FanxiuPacketCaptureSessionStatus)
def stop_fanxiu_packet_capture_session(
    payload: FanxiuPacketProxyStartRequest = FanxiuPacketProxyStartRequest(),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    android_status: dict[str, Any]
    try:
        android_status = fanxiu_android_proxy_service.clear_http_proxy(device_id=payload.device_id)
    except Exception as exc:
        proxy_status = fanxiu_packet_proxy_service.status()
        target_proxy = _recommended_fanxiu_proxy_address(proxy_status)
        android_status = fanxiu_android_proxy_service.status(
            device_id=payload.device_id,
            target_proxy=target_proxy,
        )
        android_status["last_error"] = f"清理安卓代理失败，已保留 Python 代理运行：{exc}"
        host, port = _saved_fanxiu_capture_host_port()
        fanxiu_packet_proxy_service.save_session_state(
            active=True,
            host=host,
            port=port,
            device_id=str(android_status.get("device_id") or payload.device_id),
            target_proxy=target_proxy,
            last_error=str(android_status.get("last_error") or ""),
        )
        return FanxiuPacketCaptureSessionStatus.model_validate(
            _fanxiu_packet_capture_session_status(android_status=android_status, device_id=payload.device_id)
        )

    fanxiu_packet_proxy_service.stop()
    host, port = _saved_fanxiu_capture_host_port()
    fanxiu_packet_proxy_service.save_session_state(
        active=False,
        host=host,
        port=port,
        device_id=str(android_status.get("device_id") or payload.device_id),
    )
    return FanxiuPacketCaptureSessionStatus.model_validate(
        _fanxiu_packet_capture_session_status(android_status=android_status, device_id=payload.device_id)
    )


@status_router.get("/packet-capture/proxy/status", response_model=FanxiuPacketProxyStatus)
def get_fanxiu_packet_proxy_status(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    _ensure_fanxiu_capture_session()
    return FanxiuPacketProxyStatus.model_validate(fanxiu_packet_proxy_service.status())


@status_router.post("/packet-capture/proxy/start", response_model=FanxiuPacketProxyStatus)
def start_fanxiu_packet_proxy(
    payload: FanxiuPacketProxyStartRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return FanxiuPacketProxyStatus.model_validate(
            fanxiu_packet_proxy_service.start(payload.host, payload.port)
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@status_router.post("/packet-capture/proxy/stop", response_model=FanxiuPacketProxyStatus)
def stop_fanxiu_packet_proxy(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuPacketProxyStatus.model_validate(fanxiu_packet_proxy_service.stop())


@status_router.get("/packet-capture/proxy/events", response_model=FanxiuPacketProxyEventListResponse)
def get_fanxiu_packet_proxy_events(
    limit: int = Query(200, ge=1, le=500),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    _ensure_fanxiu_capture_session()
    return FanxiuPacketProxyEventListResponse.model_validate(
        fanxiu_packet_proxy_service.list_events(limit)
    )


@status_router.get("/packet-capture/proxy/timeline", response_model=FanxiuPacketProxyTimelineResponse)
def get_fanxiu_packet_proxy_timeline(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    event_filter: str = Query("all", pattern="^(candidate|readable|encrypted_or_resource|all)$"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    _ensure_fanxiu_capture_session()
    return FanxiuPacketProxyTimelineResponse.model_validate(
        fanxiu_packet_proxy_service.list_timeline(offset=offset, limit=limit, event_filter=event_filter)
    )


@status_router.delete("/packet-capture/proxy/events", response_model=FanxiuPacketProxyEventListResponse)
def clear_fanxiu_packet_proxy_events(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuPacketProxyEventListResponse.model_validate(
        fanxiu_packet_proxy_service.clear_events()
    )


@status_router.post("/packet-capture/proxy/events/save", response_model=FanxiuPacketProxySaveResponse)
def save_fanxiu_packet_proxy_events(
    payload: FanxiuPacketProxySaveRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuPacketProxySaveResponse.model_validate(
        fanxiu_packet_proxy_service.save_events(payload.label)
    )


@status_router.get("/packet-capture/proxy/logs", response_model=FanxiuPacketProxyLogListResponse)
def list_fanxiu_packet_proxy_logs(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuPacketProxyLogListResponse.model_validate(
        fanxiu_packet_proxy_service.list_logs(limit)
    )


@status_router.get("/packet-capture/proxy/logs/load", response_model=FanxiuPacketProxyLogLoadResponse)
def load_fanxiu_packet_proxy_log(
    name: str = Query(..., min_length=1),
    limit: int = Query(500, ge=1, le=2000),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return FanxiuPacketProxyLogLoadResponse.model_validate(
            fanxiu_packet_proxy_service.load_log(name, limit)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@status_router.post("/processes/terminate", response_model=FanxiuProcessTerminateResponse)
def terminate_fanxiu_scripts(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    result = stop_behavior_tree_service()
    service = result.get("service") or {}
    stop_result = result.get("stop_result") or {}
    if service.get("process_count"):
        return FanxiuProcessTerminateResponse.model_validate(
            {
                **stop_result,
                "remaining": service.get("processes") or stop_result.get("remaining") or [],
            }
        )
    return FanxiuProcessTerminateResponse.model_validate(stop_result)


@status_router.get("/behavior-tree-service", response_model=FanxiuBehaviorTreeServiceStatus)
def get_fanxiu_behavior_tree_service(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuBehaviorTreeServiceStatus.model_validate(get_behavior_tree_status())


@status_router.post("/behavior-tree-service/start", response_model=FanxiuBehaviorTreeServiceResponse)
def start_fanxiu_behavior_tree(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return FanxiuBehaviorTreeServiceResponse.model_validate(start_behavior_tree_service(replace_existing=True))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@status_router.post("/behavior-tree-service/stop", response_model=FanxiuBehaviorTreeServiceResponse)
def stop_fanxiu_behavior_tree(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuBehaviorTreeServiceResponse.model_validate(stop_behavior_tree_service())


def _stream_fanxiu_game_window(
    title: Optional[str] = None,
    fps: float = 10.0,
    quality: int = 80,
    mode: str = "screen",
    area: str = "outer",
    crop: Optional[str] = None,
    trim_border: Optional[str] = None,
    rotate: str = "90",
    fixed_width: int = 0,
    fixed_height: int = 0,
    auto_dismiss_popup: bool = False,
    popup_check_interval: float = 3.0,
):
    try:
        frames = stream_mumu_window_mjpeg(
            title=title,
            fps=fps,
            quality=quality,
            mode=mode,
            area=area,
            crop=crop,
            trim_border=trim_border,
            rotate=rotate,
            fixed_width=fixed_width,
            fixed_height=fixed_height,
            auto_dismiss_popup=auto_dismiss_popup,
            popup_check_interval=popup_check_interval,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return StreamingResponse(
        frames,
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@status_router.get("/live-annotation/stream")
def stream_fanxiu_live_annotation(
    title: Optional[str] = Query(None),
    fps: float = Query(10.0, ge=1.0, le=30.0),
    quality: int = Query(80, ge=1, le=100),
    mode: str = Query("screen", pattern="^(auto|printwindow|screen)$"),
    area: str = Query("outer", pattern="^(outer|client)$"),
    crop: Optional[str] = Query(None),
    trim_border: Optional[str] = Query(None),
    rotate: str = Query("90", pattern="^(0|90|180|270|ccw|cw|none)$"),
    fixed_width: int = Query(0, ge=0, le=4096),
    fixed_height: int = Query(0, ge=0, le=4096),
):
    return _stream_fanxiu_game_window(
        title=title,
        fps=fps,
        quality=quality,
        mode=mode,
        area=area,
        crop=crop,
        trim_border=trim_border,
        rotate=rotate,
        fixed_width=fixed_width,
        fixed_height=fixed_height,
    )


def _get_user_device_or_404(session: Session, current_user: User, entry_id: str) -> UserDevice:
    entry = session.get(UserDevice, entry_id)
    if not entry or entry.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Device entry not found")
    if not entry.is_active:
        raise HTTPException(status_code=400, detail="Device entry is inactive")
    return entry


def _get_service_user_device_or_404(session: Session, entry_id: str) -> UserDevice:
    entry = session.get(UserDevice, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Device entry not found")
    if not entry.is_active:
        raise HTTPException(status_code=400, detail="Device entry is inactive")
    return entry


def _decode_game_window2_stream_token(session: Session, token: str) -> tuple[UserDevice, User]:
    credentials_exception = HTTPException(status_code=401, detail="Invalid game window stream token")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise credentials_exception from exc

    if payload.get("scope") != FANXIU_GAME_WINDOW2_STREAM_TOKEN_SCOPE:
        raise credentials_exception
    username = payload.get("username")
    entry_id = payload.get("entry_id")
    if not username or not entry_id:
        raise credentials_exception

    current_user = session.exec(select(User).where(User.username == username)).first()
    if current_user is None:
        raise credentials_exception
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    return _get_user_device_or_404(session, current_user, str(entry_id)), current_user


def _remote_entry_base_url(entry: UserDevice) -> str:
    if entry.mode != "remote" or not entry.server_url:
        raise HTTPException(status_code=400, detail="远程设备入口未配置后端地址")
    return entry.server_url.rstrip("/")


def _remote_entry_headers(entry: UserDevice) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {entry.token}",
        "X-Device-Token": entry.token,
    }


def _normalize_game_window2_title(title: Optional[str]) -> Optional[str]:
    value = (title or "").strip()
    if not value:
        return title
    if "mumu" in value.lower():
        return "MuMu"
    return title


def _game_window2_desktop_title(title: Optional[str]) -> str:
    return _normalize_game_window2_title(title) or "MuMu"


def _game_window2_stream_params(
    *,
    title: Optional[str],
    title_match: str,
    fps: float,
    quality: int,
    mode: str,
    area: str,
    crop: Optional[str],
    trim_border: Optional[str],
    rotate: str,
    fixed_width: int,
    fixed_height: int,
    auto_dismiss_popup: bool,
    popup_check_interval: float,
) -> dict[str, Any]:
    normalized_title = _normalize_game_window2_title(title)
    return {
        "title": normalized_title or "",
        "title_match": title_match,
        "fps": fps,
        "quality": quality,
        "mode": mode,
        "area": area,
        "crop": crop or "",
        "trim_border": trim_border or "",
        "rotate": rotate,
        "fixed_width": fixed_width,
        "fixed_height": fixed_height,
        "auto_dismiss_popup": "true" if auto_dismiss_popup else "false",
        "popup_check_interval": popup_check_interval,
    }


def _extract_stream_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("message") or payload.get("error")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return response.text.strip() or f"画面流服务返回 HTTP {response.status_code}"


def _stream_response_from_requests(response: requests.Response, *, cleanup: Callable[[], None] | None = None) -> StreamingResponse:
    if response.status_code >= 400:
        detail = _extract_stream_error(response)
        response.close()
        raise HTTPException(status_code=response.status_code, detail=detail)

    def close_stream() -> None:
        try:
            response.close()
        finally:
            if cleanup is not None:
                cleanup()

    return StreamingResponse(
        response.iter_content(chunk_size=64 * 1024),
        media_type=response.headers.get("content-type") or "multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
        background=BackgroundTask(close_stream),
    )


def _open_remote_game_window2_stream(entry: UserDevice, params: dict[str, Any]) -> requests.Response:
    target_url = f"{_remote_entry_base_url(entry)}/api/fanxiu/game-window2/service-stream"
    try:
        return requests.get(
            target_url,
            headers=_remote_entry_headers(entry),
            params=params,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 60.0),
            stream=True,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏画面流不可达：{exc}") from exc


def _stream_game_window2_service(params: dict[str, Any]) -> StreamingResponse:
    try:
        response = open_game_window_service_stream(params)
    except GameWindowServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    fanxiu_capture_runtime_service.ensure_running("game-window2-stream")
    return _stream_response_from_requests(
        response,
        cleanup=lambda: fanxiu_capture_runtime_service.release("game-window2-stream"),
    )


def _game_window2_click_payload(req: FanxiuGameWindow2ClickRequest | FanxiuGameWindow2ServiceClickRequest) -> dict[str, Any]:
    return req.model_dump(exclude_none=True, exclude={"entry_id"})


def _game_window2_activate_payload(
    req: FanxiuGameWindow2ActivateRequest | FanxiuGameWindow2ServiceActivateRequest,
) -> dict[str, Any]:
    return req.model_dump(exclude_none=True, exclude={"entry_id"})


def _game_window2_drag_payload(req: FanxiuGameWindow2DragRequest | FanxiuGameWindow2ServiceDragRequest) -> dict[str, Any]:
    return req.model_dump(exclude_none=True, exclude={"entry_id"})


def _game_window2_keyevent_payload(
    req: FanxiuGameWindow2KeyeventRequest | FanxiuGameWindow2ServiceKeyeventRequest,
) -> dict[str, Any]:
    return req.model_dump(exclude_none=True, exclude={"entry_id"})


def _game_window2_text_payload(req: FanxiuGameWindow2TextRequest | FanxiuGameWindow2ServiceTextRequest) -> dict[str, Any]:
    return req.model_dump(exclude_none=True, exclude={"entry_id"})


def _game_window2_save_frame_payload(
    req: FanxiuGameWindow2SaveFrameRequest | FanxiuGameWindow2ServiceSaveFrameRequest,
) -> dict[str, Any]:
    return req.model_dump(exclude_none=True, exclude={"entry_id"})


def _game_window2_match_payload(
    req: FanxiuGameWindow2MatchRequest | FanxiuGameWindow2ServiceMatchRequest,
) -> dict[str, Any]:
    return req.model_dump(exclude_none=True, exclude={"entry_id"})


_GAME_WINDOW2_MATCH_CACHE_TTL = 8.0
_GAME_WINDOW2_MATCH_CACHE_MAX_SIZE = 64
_game_window2_match_cache_lock = threading.Lock()
_game_window2_match_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_game_window2_match_inflight: dict[str, threading.Event] = {}


def _clone_json_dict(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data, ensure_ascii=False, default=str))


def _game_window2_match_cache_key(payload: dict[str, Any]) -> str:
    frame_data_url = str(payload.get("current_frame_data_url") or "")
    cache_payload = dict(payload)
    if frame_data_url:
        cache_payload["current_frame_data_url"] = hashlib.sha256(frame_data_url.encode("utf-8")).hexdigest()
    else:
        return ""
    raw = json.dumps(cache_payload, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_game_window2_match_cache(cache_key: str) -> dict[str, Any] | None:
    if not cache_key:
        return None
    now = time.monotonic()
    with _game_window2_match_cache_lock:
        cached = _game_window2_match_cache.get(cache_key)
        if cached is None:
            return None
        cached_at, result = cached
        if now - cached_at > _GAME_WINDOW2_MATCH_CACHE_TTL:
            _game_window2_match_cache.pop(cache_key, None)
            return None
        return _clone_json_dict(result)


def _set_game_window2_match_cache(cache_key: str, result: dict[str, Any]) -> None:
    if not cache_key:
        return
    now = time.monotonic()
    cloned = _clone_json_dict(result)
    with _game_window2_match_cache_lock:
        expired_keys = [
            key for key, (cached_at, _) in _game_window2_match_cache.items()
            if now - cached_at > _GAME_WINDOW2_MATCH_CACHE_TTL
        ]
        for key in expired_keys:
            _game_window2_match_cache.pop(key, None)
        while len(_game_window2_match_cache) >= _GAME_WINDOW2_MATCH_CACHE_MAX_SIZE:
            oldest_key = min(_game_window2_match_cache, key=lambda key: _game_window2_match_cache[key][0])
            _game_window2_match_cache.pop(oldest_key, None)
        _game_window2_match_cache[cache_key] = (now, cloned)


def _run_game_window2_match_with_cache(payload: dict[str, Any], producer: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    cache_key = _game_window2_match_cache_key(payload)
    cached = _get_game_window2_match_cache(cache_key)
    if cached is not None:
        return cached

    owner = False
    inflight: threading.Event | None = None
    if cache_key:
        with _game_window2_match_cache_lock:
            inflight = _game_window2_match_inflight.get(cache_key)
            if inflight is None:
                inflight = threading.Event()
                _game_window2_match_inflight[cache_key] = inflight
                owner = True

    if cache_key and not owner and inflight is not None:
        wait_timeout = 2.0 if payload.get("read_only_cache") else 8.0
        if inflight.wait(timeout=wait_timeout):
            cached = _get_game_window2_match_cache(cache_key)
            if cached is not None:
                return cached

    try:
        result = producer()
        _set_game_window2_match_cache(cache_key, result)
        return result
    finally:
        if cache_key and owner and inflight is not None:
            with _game_window2_match_cache_lock:
                _game_window2_match_inflight.pop(cache_key, None)
            inflight.set()


def _click_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return click_mumu_window_processed_point(
            x=float(payload.get("x") or 0),
            y=float(payload.get("y") or 0),
            title=_game_window2_desktop_title(payload.get("title")),
            title_match=payload.get("title_match") or "contains",
            mode=payload.get("mode"),
            area=payload.get("area"),
            crop=payload.get("crop"),
            trim_border=payload.get("trim_border"),
            rotate=payload.get("rotate"),
            fixed_width=int(payload.get("fixed_width") or 0),
            fixed_height=int(payload.get("fixed_height") or 0),
            frame_width=int(payload.get("frame_width") or 0) or None,
            frame_height=int(payload.get("frame_height") or 0) or None,
            input_backend=payload.get("input_backend") or "desktop",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _activate_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return activate_mumu_window(
            title=_game_window2_desktop_title(payload.get("title")),
            title_match=payload.get("title_match") or "contains",
            click_title=bool(payload.get("click_title", True)),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _drag_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return drag_mumu_window_processed_points(
            start_x=float(payload.get("start_x") or 0),
            start_y=float(payload.get("start_y") or 0),
            end_x=float(payload.get("end_x") or 0),
            end_y=float(payload.get("end_y") or 0),
            duration_ms=int(payload.get("duration_ms") or 300),
            title=_game_window2_desktop_title(payload.get("title")),
            title_match=payload.get("title_match") or "contains",
            mode=payload.get("mode"),
            area=payload.get("area"),
            crop=payload.get("crop"),
            trim_border=payload.get("trim_border"),
            rotate=payload.get("rotate"),
            fixed_width=int(payload.get("fixed_width") or 0),
            fixed_height=int(payload.get("fixed_height") or 0),
            frame_width=int(payload.get("frame_width") or 0) or None,
            frame_height=int(payload.get("frame_height") or 0) or None,
            input_backend=payload.get("input_backend") or "desktop",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _keyevent_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        keys = payload.get("keys")
        if isinstance(keys, list) and keys:
            return keyevents_mumu_adb(keys)
        return keyevent_mumu_adb(str(payload.get("key") or ""))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _text_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return text_mumu_adb(str(payload.get("text") or ""))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _encode_bgr_png_response(frame: Any, headers: dict[str, str]) -> Response:
    import cv2

    ok, data = cv2.imencode(".png", frame)
    if not ok:
        raise HTTPException(status_code=500, detail="编码游戏窗口截图失败")
    safe_headers = {key: str(value).encode("ascii", "ignore").decode("ascii") for key, value in headers.items()}
    return Response(content=data.tobytes(), media_type="image/png", headers=safe_headers)


def _screencap_game_window2_service(
    *,
    prefer_cached: bool = False,
    cached_only: bool = False,
    allow_window_fallback: bool = False,
    title: str | None = None,
    title_match: str = "contains",
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
) -> Response:
    title = _game_window2_desktop_title(title)
    adb_error = ""
    if prefer_cached or cached_only:
        try:
            data, meta = screencap_mumu_adb_cached_png(cached_only=True)
        except Exception as exc:
            adb_error = str(exc)
            if cached_only:
                raise HTTPException(status_code=400, detail=adb_error) from exc
        else:
            return Response(
                content=data,
                media_type="image/png",
                headers={
                    "Cache-Control": "no-store",
                    "X-CodeYun-Input": str(meta.get("input") or ""),
                    "X-CodeYun-Adb-Serial": str(meta.get("adb_serial") or ""),
                    "X-CodeYun-Adb-Host": str(meta.get("adb_host") or ""),
                    "X-CodeYun-Adb-Port": str(meta.get("adb_port") or ""),
                    "X-CodeYun-Adb-Size": str(meta.get("adb_size") or ""),
                },
            )
    elif not cached_only:
        try:
            data, meta = screencap_mumu_adb_png()
        except Exception as exc:
            adb_error = str(exc)
        else:
            return Response(
                content=data,
                media_type="image/png",
                headers={
                    "Cache-Control": "no-store",
                    "X-CodeYun-Input": str(meta.get("input") or ""),
                    "X-CodeYun-Adb-Serial": str(meta.get("adb_serial") or ""),
                    "X-CodeYun-Adb-Host": str(meta.get("adb_host") or ""),
                    "X-CodeYun-Adb-Port": str(meta.get("adb_port") or ""),
                    "X-CodeYun-Adb-Size": str(meta.get("adb_size") or ""),
                },
            )

    if not allow_window_fallback:
        raise HTTPException(status_code=400, detail=f"ADB截图失败：{adb_error or '未取得 MuMu ADB 截图'}")

    try:
        frame = capture_mumu_window_frame(
            title=title,
            title_match=title_match,
            mode=mode,
            area=area,
            crop=crop,
            trim_border=trim_border,
            rotate=rotate,
            fixed_width=fixed_width,
            fixed_height=fixed_height,
            prefer_cached=True,
        )
    except Exception as exc:
        detail = str(exc)
        if adb_error:
            detail = f"{detail}；ADB截图失败：{adb_error}"
        raise HTTPException(status_code=400, detail=detail) from exc
    return _encode_bgr_png_response(
        frame,
        {
            "Cache-Control": "no-store",
            "X-CodeYun-Input": "window_capture",
            "X-CodeYun-Adb-Port": "",
            "X-CodeYun-Adb-Size": "",
            "X-CodeYun-Adb-Error": adb_error[:200],
        },
    )


def _save_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    title = _normalize_game_window2_title(payload.get("title"))
    try:
        return save_fanxiu_screenshot_frame(
            title=title,
            title_match=payload.get("title_match") or "contains",
            mode=payload.get("mode"),
            area=payload.get("area"),
            crop=payload.get("crop"),
            trim_border=payload.get("trim_border"),
            rotate=payload.get("rotate"),
            fixed_width=int(payload.get("fixed_width") or 0),
            fixed_height=int(payload.get("fixed_height") or 0),
            quality=int(payload.get("quality") or 82),
            current_frame_data_url=payload.get("current_frame_data_url"),
            overwrite_filename=payload.get("overwrite_filename"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _match_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    title = _normalize_game_window2_title(payload.get("title"))
    try:
        return _run_game_window2_match_with_cache(
            payload,
            lambda: match_fanxiu_screenshot_box_frame(
                filename=payload["filename"],
                box=payload["box"],
                scan=bool(payload.get("scan")),
                scan_box=payload.get("scan_box"),
                pixel_tolerance=int(payload.get("pixel_tolerance") if payload.get("pixel_tolerance") is not None else 5),
                alpha_mask_data_url=payload.get("alpha_mask_data_url"),
                tolerance_min_data_url=payload.get("tolerance_min_data_url"),
                tolerance_max_data_url=payload.get("tolerance_max_data_url"),
                title=title,
                title_match=payload.get("title_match") or "contains",
                mode=payload.get("mode"),
                area=payload.get("area"),
                crop=payload.get("crop"),
                trim_border=payload.get("trim_border"),
                rotate=payload.get("rotate"),
                fixed_width=int(payload.get("fixed_width") or 0),
                fixed_height=int(payload.get("fixed_height") or 0),
                quality=int(payload.get("quality") or 82),
                current_frame_data_url=payload.get("current_frame_data_url"),
                prefer_cached=bool(payload.get("prefer_cached", True)),
                match_strategy=payload.get("match_strategy") or "auto",
                match_search_radius=payload.get("match_search_radius"),
                ocr_enabled=bool(payload.get("ocr_enabled")),
                ocr_text=payload.get("ocr_text"),
                ocr_match_mode=payload.get("ocr_match_mode") or "contains",
                ocr_min_confidence=float(payload.get("ocr_min_confidence") or 0.0),
                debug_match=bool(payload.get("debug_match")),
                save_match_frame=bool(payload.get("save_match_frame", True)),
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _save_burst_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return save_fanxiu_burst_frame(
            title=payload.get("title"),
            title_match=payload.get("title_match") or "contains",
            mode=payload.get("mode"),
            area=payload.get("area"),
            crop=payload.get("crop"),
            trim_border=payload.get("trim_border"),
            rotate=payload.get("rotate"),
            fixed_width=int(payload.get("fixed_width") or 0),
            fixed_height=int(payload.get("fixed_height") or 0),
            current_frame_data_url=payload.get("current_frame_data_url"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _list_burst_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return list_fanxiu_burst_frames(
            page=int(payload.get("page") or 1),
            page_size=int(payload.get("page_size") or 24),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _clear_burst_game_window2_service() -> dict[str, Any]:
    try:
        return clear_fanxiu_burst_frames()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _import_burst_game_window2_service(filenames: list[str]) -> dict[str, Any]:
    try:
        return import_fanxiu_burst_frames(filenames)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _burst_game_window2_service_image(filename: str) -> FileResponse:
    try:
        path = get_fanxiu_burst_frame_path(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png")


def _click_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    target_url = f"{_remote_entry_base_url(entry)}/api/fanxiu/game-window2/service-input/click"
    try:
        response = requests.post(
            target_url,
            headers=_remote_entry_headers(entry),
            json=payload,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 12.0),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏操作服务不可达：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_extract_stream_error(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="远程游戏操作服务响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="远程游戏操作服务响应格式不支持")
    return data


def _activate_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    target_url = f"{_remote_entry_base_url(entry)}/api/fanxiu/game-window2/service-input/activate"
    try:
        response = requests.post(
            target_url,
            headers=_remote_entry_headers(entry),
            json=payload,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 12.0),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏窗口激活服务不可达：{exc}") from exc
    if response.status_code >= 400:
        if response.status_code == 404:
            raise HTTPException(
                status_code=502,
                detail="远程 codeyun 缺少激活窗口接口，请更新并重启远程 codeyun；如果已更新，请停止并重启“凡修游戏画面流”服务。",
            )
        raise HTTPException(status_code=response.status_code, detail=_extract_stream_error(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="远程游戏窗口激活服务响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="远程游戏窗口激活服务响应格式不支持")
    return data


def _drag_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    target_url = f"{_remote_entry_base_url(entry)}/api/fanxiu/game-window2/service-input/drag"
    try:
        response = requests.post(
            target_url,
            headers=_remote_entry_headers(entry),
            json=payload,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 12.0),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏拖拽服务不可达：{exc}") from exc
    if response.status_code >= 400:
        if response.status_code == 404:
            raise HTTPException(
                status_code=502,
                detail="目标 codeyun 缺少拖拽接口，请更新并重启 codepc_mf 的 codeyun；如果已更新，请停止并重启“凡修游戏画面流”服务。",
            )
        raise HTTPException(status_code=response.status_code, detail=_extract_stream_error(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="远程游戏拖拽服务响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="远程游戏拖拽服务响应格式不支持")
    return data


def _post_remote_game_window2_json(entry: UserDevice, service_path: str, payload: dict[str, Any], action: str) -> dict[str, Any]:
    target_url = f"{_remote_entry_base_url(entry)}/api/fanxiu/game-window2/{service_path}"
    try:
        response = requests.post(
            target_url,
            headers=_remote_entry_headers(entry),
            json=payload,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 12.0),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏{action}服务不可达：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_extract_stream_error(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏{action}服务响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail=f"远程游戏{action}服务响应格式不支持")
    return data


def _keyevent_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return _post_remote_game_window2_json(entry, "service-input/keyevent", payload, "按键")


def _text_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return _post_remote_game_window2_json(entry, "service-input/text", payload, "文本输入")


def _save_remote_game_window2_frame(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    target_url = f"{_remote_entry_base_url(entry)}/api/fanxiu/game-window2/service-save-frame"
    try:
        response = requests.post(
            target_url,
            headers=_remote_entry_headers(entry),
            json=payload,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 20.0),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏保存帧服务不可达：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_extract_stream_error(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="远程游戏保存帧服务响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="远程游戏保存帧服务响应格式不支持")
    return data


def _match_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    target_url = f"{_remote_entry_base_url(entry)}/api/fanxiu/game-window2/service-match"
    try:
        response = requests.post(
            target_url,
            headers=_remote_entry_headers(entry),
            json=payload,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 30.0),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏匹配服务不可达：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_extract_stream_error(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="远程游戏匹配服务响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="远程游戏匹配服务响应格式不支持")
    return data


def _screenshot_game_window2_service_list() -> dict[str, Any]:
    try:
        return list_fanxiu_screenshots()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _screenshot_game_window2_service_pre_label(filename: str) -> dict[str, Any]:
    try:
        return read_fanxiu_screenshot_pre_label(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _save_screenshot_game_window2_service_pre_label(filename: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return write_fanxiu_screenshot_pre_label(filename, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _delete_screenshot_game_window2_service_image(filename: str) -> dict[str, Any]:
    try:
        return delete_fanxiu_screenshot(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _screenshot_game_window2_service_image(filename: str) -> FileResponse:
    try:
        path = get_fanxiu_screenshot_path(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        headers={"Cache-Control": "private, no-cache"},
    )


def _match_game_window2_service_image(filename: str) -> FileResponse:
    try:
        path = get_fanxiu_match_frame_path(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/jpeg",
        filename=path.name,
        headers={"Cache-Control": "no-store"},
    )


def _remote_game_window2_screenshot_json(
    entry: UserDevice,
    path: str,
    *,
    method: str = "post",
    payload: dict[str, Any] | None = None,
    action: str,
) -> dict[str, Any]:
    target_url = f"{_remote_entry_base_url(entry)}/api/fanxiu/game-window2/{path.lstrip('/')}"
    try:
        response = requests.request(
            method,
            target_url,
            headers=_remote_entry_headers(entry),
            json=payload,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 20.0),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏{action}服务不可达：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_extract_stream_error(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏{action}服务响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail=f"远程游戏{action}服务响应格式不支持")
    return data


def _remote_game_window2_screenshot_image(entry: UserDevice, filename: str) -> Response:
    target_url = f"{_remote_entry_base_url(entry)}/api/fanxiu/game-window2/service-screenshot/image"
    try:
        response = requests.get(
            target_url,
            headers=_remote_entry_headers(entry),
            params={"filename": filename},
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 30.0),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏截图服务不可达：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_extract_stream_error(response))
    return Response(
        content=response.content,
        media_type=response.headers.get("content-type") or "image/jpeg",
        headers={"Cache-Control": "private, no-cache"},
    )


def _remote_game_window2_screencap(entry: UserDevice) -> Response:
    target_url = f"{_remote_entry_base_url(entry)}/api/fanxiu/game-window2/service-screencap"
    try:
        response = requests.get(
            target_url,
            headers=_remote_entry_headers(entry),
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 20.0),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏 ADB 截图服务不可达：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_extract_stream_error(response))
    return Response(
        content=response.content,
        media_type=response.headers.get("content-type") or "image/png",
        headers={"Cache-Control": "no-store"},
    )


def _remote_game_window2_match_image(entry: UserDevice, filename: str) -> Response:
    target_url = f"{_remote_entry_base_url(entry)}/api/fanxiu/game-window2/service-match/image"
    try:
        response = requests.get(
            target_url,
            headers=_remote_entry_headers(entry),
            params={"filename": filename},
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 30.0),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏匹配帧服务不可达：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_extract_stream_error(response))
    return Response(
        content=response.content,
        media_type=response.headers.get("content-type") or "image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


class _DataAnnotationRuntimeRunner:
    default_guard_enabled = True
    default_guard_interval_seconds = 2.0
    default_guard_items = {
        "wanling_invite": {"enabled": False, "entry_id": "", "updated_at": 0.0},
    }
    guard_definitions = {
        "close_popups": {
            "id": "close_popups",
            "label": "关闭弹窗",
            "message": "常驻处理已标注弹窗和遮挡",
        },
        "wanling_invite": {
            "id": "wanling_invite",
            "label": "万灵切磋邀请",
            "message": "占位触发守护，当前仅保留开关",
        },
    }
    scene_ids = {
        "world": 34,
        "world_menu": 35,
        "settings": 49,
        "hide_floating": 58,
        "daily": 69,
        "wanling_invite": 70,
        "youli": 71,
        "youli_explore": 72,
        "youli_result": 73,
        "daily_activity": 75,
        "signup": 23,
        "signup_reward": 24,
        "gift": 78,
        "reward": 81,
        "duplicated": 82,
    }
    scene_threshold = 80
    scene_thresholds = {"gift": 60, "daily": 60, "hide_floating": 55}
    overlay_threshold = 55

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._service_thread: threading.Thread | None = None
        self._service_stop_event: threading.Event | None = None
        self._service_wake_event = threading.Event()
        self._service_entry: UserDevice | None = None
        self._service_entry_id = ""
        self._service_asset_tree_path: Path | None = None
        self._service_runtime_state_path: Path | None = None
        self._service_manual_job_state_path: Path | None = None
        self._service_scheduler_state_path: Path | None = None
        self._service_world_facts_path: Path | None = None
        self._service_generation = 0
        self._service_heartbeat_at = 0.0
        self._service_step = ""
        self._service_owner_token = ""
        self._service_owner_path: Path | None = None
        self._stop_event: threading.Event | None = None
        self._guard_enabled = self.default_guard_enabled
        self._guard_entry_id = ""
        self._guard_interval_seconds = self.default_guard_interval_seconds
        self._guard_items: dict[str, dict[str, Any]] = json.loads(json.dumps(self.default_guard_items, ensure_ascii=False))
        self._auto_close_candidates_cache: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}
        self._log_scope = ""
        self._log_item_id = ""
        self._status: dict[str, Any] = self._initial_status()

    def _initial_status(self) -> dict[str, Any]:
        return initial_data_annotation_runtime_status()

    def _status_base_preserving_guard_locked(self) -> dict[str, Any]:
        base = self._initial_status()
        base.update({
            "guard_enabled": bool(self._guard_enabled),
            "guard_running": bool(self._status.get("guard_running")),
            "guard_entry_id": self._guard_entry_id,
            "guard_interval_seconds": self._guard_interval_seconds,
            "guard_items": json.loads(json.dumps(self._guard_items, ensure_ascii=False)),
            "last_guard_event": self._status.get("last_guard_event") if isinstance(self._status.get("last_guard_event"), dict) else {},
            "service_running": bool(self._service_thread is not None and self._service_thread.is_alive()),
        })
        return base

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._sync_guard_status_locked()
            self._sync_service_status_locked()
            return json.loads(json.dumps(self._status, ensure_ascii=False))

    def replace_logs(self, logs: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            self._status["logs"] = list(logs)
            self._status["updated_at"] = time.time()
            self._sync_guard_status_locked()
            self._sync_service_status_locked()
            return json.loads(json.dumps(self._status, ensure_ascii=False))

    def wait_until_idle(self, timeout_seconds: float = 5.0) -> bool:
        deadline = time.time() + max(0.0, timeout_seconds)
        while time.time() < deadline:
            with self._lock:
                running = bool(self._status.get("running"))
                stopping = str(self._status.get("status") or "") == "stopping"
            if not running and not stopping:
                return True
            time.sleep(0.1)
        with self._lock:
            return not bool(self._status.get("running"))

    def _sync_guard_status_locked(self) -> None:
        service_running = self._service_thread is not None and self._service_thread.is_alive()
        guard_running = bool(self._guard_enabled and service_running)
        guard_items: dict[str, dict[str, Any]] = {}
        for guard_id, definition in self.guard_definitions.items():
            state = self._guard_items.get(guard_id)
            if not isinstance(state, dict):
                state = {}
            enabled = bool(state.get("enabled"))
            entry_id = str(state.get("entry_id") or "")
            running = False
            message = str(definition.get("message") or "")
            if guard_id == "close_popups":
                enabled = bool(self._guard_enabled)
                entry_id = self._guard_entry_id
                running = bool(guard_running)
                last_guard_event = self._status.get("last_guard_event")
                if isinstance(last_guard_event, dict) and last_guard_event.get("title"):
                    message = str(last_guard_event.get("title") or "")
            guard_items[guard_id] = {
                **definition,
                "enabled": enabled,
                "running": running,
                "entry_id": entry_id,
                "updated_at": float(state.get("updated_at") or 0),
                "message": message,
            }
        self._status.update({
            "guard_enabled": bool(self._guard_enabled),
            "guard_running": bool(guard_running),
            "guard_entry_id": self._guard_entry_id,
            "guard_interval_seconds": self._guard_interval_seconds,
            "guard_items": guard_items,
        })

    def _sync_service_status_locked(self) -> None:
        self._status["service_running"] = bool(self._service_thread is not None and self._service_thread.is_alive())

    def _pending_manual_job_count(self) -> int:
        return sum(
            1
            for job in _read_data_annotation_manual_jobs()
            if str(job.get("status") or "") in {"pending", "queued", "running"}
        )

    def _service_should_restart_for_pending_jobs_locked(self) -> bool:
        if self._service_thread is None or not self._service_thread.is_alive():
            return False
        if self._status.get("running"):
            return False
        try:
            pending_count = self._pending_manual_job_count()
        except Exception:
            return False
        if pending_count <= 0:
            return False
        heartbeat_at = float(self._service_heartbeat_at or 0.0)
        if heartbeat_at <= 0:
            return True
        return time.time() - heartbeat_at > max(10.0, self._guard_interval_seconds * 3)

    def _mark_service_heartbeat(self, step: str) -> None:
        with self._lock:
            self._service_heartbeat_at = time.time()
            self._service_step = str(step or "")
            token = self._service_owner_token
            owner_path = self._service_owner_path
            entry_id = self._service_entry_id
            generation = self._service_generation
        if token and owner_path is not None:
            self._write_service_owner(owner_path, token, entry_id, generation, self._service_step)

    def _service_owner_stale(self, owner: dict[str, Any]) -> bool:
        updated_at = float(owner.get("updated_at") or 0.0)
        if updated_at <= 0:
            return True
        return time.time() - updated_at > max(30.0, self._guard_interval_seconds * 10)

    def _write_service_owner(self, owner_path: Path, token: str, entry_id: str, generation: int, step: str) -> None:
        try:
            owner_path.parent.mkdir(parents=True, exist_ok=True)
            owner_path.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "token": token,
                        "entry_id": entry_id,
                        "generation": generation,
                        "step": step,
                        "updated_at": time.time(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _acquire_service_owner(self, entry_id: str, generation: int) -> tuple[bool, str]:
        owner_path = _data_annotation_runtime_state_path().parent / "behavior_tree_service_owner.json"
        token = uuid.uuid4().hex
        try:
            owner_path.parent.mkdir(parents=True, exist_ok=True)
            if owner_path.exists():
                try:
                    owner = json.loads(owner_path.read_text(encoding="utf-8"))
                except Exception:
                    owner = {}
                if isinstance(owner, dict) and not self._service_owner_stale(owner):
                    pid = owner.get("pid")
                    step = owner.get("step") or "unknown"
                    return False, f"行为树执行器已由后端进程 {pid} 持有：{step}"
                try:
                    owner_path.unlink()
                except FileNotFoundError:
                    pass
            fd = os.open(str(owner_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(
                    {
                        "pid": os.getpid(),
                        "token": token,
                        "entry_id": entry_id,
                        "generation": generation,
                        "step": "starting",
                        "updated_at": time.time(),
                    },
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
            self._service_owner_token = token
            self._service_owner_path = owner_path
            return True, ""
        except FileExistsError:
            return False, "行为树执行器已由另一个后端实例抢先启动"
        except Exception as exc:
            return False, f"行为树执行器单例锁获取失败：{exc}"

    def _release_service_owner(self) -> None:
        with self._lock:
            owner_path = self._service_owner_path
            token = self._service_owner_token
            self._service_owner_path = None
            self._service_owner_token = ""
        if owner_path is None or not token:
            return
        try:
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
        except Exception:
            owner = {}
        if isinstance(owner, dict) and str(owner.get("token") or "") == token:
            try:
                owner_path.unlink()
            except FileNotFoundError:
                pass

    def ensure_service(
        self,
        *,
        entry: UserDevice,
        entry_id: str,
        asset_tree_path: Path,
        tick_seconds: float = 1.0,
    ) -> dict[str, Any]:
        entry_id = str(getattr(entry, "entry_id", None) or entry_id)
        with self._lock:
            self._restore_persisted_config_locked()
            self._service_entry = entry
            self._service_entry_id = entry_id
            self._service_asset_tree_path = asset_tree_path
            self._service_runtime_state_path = _data_annotation_runtime_state_path()
            self._service_manual_job_state_path = _data_annotation_manual_job_state_path()
            self._service_scheduler_state_path = _data_annotation_scheduler_state_path()
            self._service_world_facts_path = _data_annotation_world_facts_path()
            if self._guard_enabled:
                self._guard_entry_id = entry_id
            if not self._status.get("entry_id"):
                self._status["entry_id"] = entry_id
            if self._service_thread is not None and self._service_thread.is_alive():
                if not self._service_should_restart_for_pending_jobs_locked():
                    self._service_wake_event.set()
                    self._sync_service_status_locked()
                    return json.loads(json.dumps(self._status, ensure_ascii=False))
                if self._service_stop_event is not None:
                    self._service_stop_event.set()
                self._service_generation += 1
                self._log_locked("stop", f"行为树常驻服务心跳停滞，准备重启：{self._service_step or 'unknown'}")
            else:
                self._service_generation += 1
            stop_event = threading.Event()
            self._service_stop_event = stop_event
            generation = self._service_generation
            acquired, owner_message = self._acquire_service_owner(entry_id, generation)
            if not acquired:
                self._sync_service_status_locked()
                self._set_status_locked("idle", owner_message, phase="service_owned_by_other")
                self._log_locked("info", owner_message)
                return json.loads(json.dumps(self._status, ensure_ascii=False))
            requeued_count = _requeue_running_data_annotation_manual_jobs()
            self._service_heartbeat_at = time.time()
            self._service_step = "starting"
            thread = threading.Thread(
                target=self._run_service_loop,
                kwargs={"stop_event": stop_event, "tick_seconds": tick_seconds, "generation": generation},
                name="fanxiu-data-annotation-runtime-service",
                daemon=True,
            )
            self._service_thread = thread
            self._sync_service_status_locked()
            self._log_locked("info", "行为树常驻服务已启动")
            if requeued_count:
                self._log_locked("stop", f"已重置 {requeued_count} 个残留 running 作业为 queued")
            thread.start()
        self._service_wake_event.set()
        return self.status()

    def _restore_persisted_config_locked(self) -> None:
        if self._status.get("service_running") or self._status.get("running") or self._status.get("logs"):
            return
        persisted = _read_data_annotation_runtime_status()
        if not persisted:
            return
        self._guard_enabled = bool(persisted.get("guard_enabled"))
        self._guard_entry_id = str(persisted.get("guard_entry_id") or "")
        self._guard_interval_seconds = float(persisted.get("guard_interval_seconds") or self._guard_interval_seconds)
        raw_items = persisted.get("guard_items")
        if isinstance(raw_items, dict):
            self._guard_items = {
                str(key): dict(value)
                for key, value in raw_items.items()
                if isinstance(value, dict)
            }
        kept_logs = [item for item in persisted.get("logs") or [] if isinstance(item, dict)][-500:]
        self._status.update({
            **self._status,
            "entry_id": persisted.get("entry_id") or persisted.get("guard_entry_id") or self._status.get("entry_id") or "",
            "current_scene": persisted.get("current_scene"),
            "message": "行为树常驻服务恢复配置",
            "logs": kept_logs,
            "updated_at": time.time(),
        })

    def _service_context(self) -> tuple[UserDevice, str, Path] | None:
        with self._lock:
            entry = self._service_entry
            entry_id = self._service_entry_id
            asset_tree_path = self._service_asset_tree_path
        if entry is None or not entry_id or asset_tree_path is None:
            return None
        return entry, entry_id, asset_tree_path

    def _service_paths_still_current(self) -> bool:
        with self._lock:
            runtime_state_path = self._service_runtime_state_path
            manual_job_state_path = self._service_manual_job_state_path
            scheduler_state_path = self._service_scheduler_state_path
            world_facts_path = self._service_world_facts_path
        return (
            runtime_state_path == _data_annotation_runtime_state_path()
            and manual_job_state_path == _data_annotation_manual_job_state_path()
            and scheduler_state_path == _data_annotation_scheduler_state_path()
            and world_facts_path == _data_annotation_world_facts_path()
        )

    def _run_service_loop(self, *, stop_event: threading.Event, tick_seconds: float, generation: int) -> None:
        last_idle_guard_at = 0.0
        while not stop_event.is_set():
            with self._lock:
                if generation != self._service_generation:
                    break
            self._mark_service_heartbeat("loop")
            context = self._service_context()
            if context is None or not self._service_paths_still_current():
                self._mark_service_heartbeat("waiting_context")
                self._service_wake_event.wait(max(0.2, float(tick_seconds or 1.0)))
                self._service_wake_event.clear()
                continue
            entry, entry_id, asset_tree_path = context
            try:
                if not self.status().get("running"):
                    self._mark_service_heartbeat("manual_job_poll")
                    if _start_next_data_annotation_manual_job_if_idle(entry, entry_id) is not None:
                        self._mark_service_heartbeat("manual_job_started")
                        self._service_wake_event.wait(0.1)
                        self._service_wake_event.clear()
                        continue
                    self._mark_service_heartbeat("scheduler_poll")
                    started_due = self._start_due_scheduler_tasks_if_idle(entry, entry_id, asset_tree_path)
                    if started_due:
                        self._mark_service_heartbeat("scheduler_started")
                        self._service_wake_event.wait(0.1)
                        self._service_wake_event.clear()
                        continue
                    interval = self._guard_interval_seconds
                    now = time.time()
                    if self._guard_enabled and now - last_idle_guard_at >= max(0.5, interval):
                        last_idle_guard_at = now
                        self._mark_service_heartbeat("idle_guard")
                        self._run_idle_guard_tick(entry, entry_id, asset_tree_path)
                        self._mark_service_heartbeat("idle_guard_done")
            except Exception as exc:
                with self._lock:
                    self._log_locked("error", f"行为树 tick 失败：{exc}")
                    self._status.update({"ok": False, "status": "error", "message": str(exc), "error": str(exc), "updated_at": time.time()})
                self._persist_status()
            self._service_wake_event.wait(max(0.2, float(tick_seconds or 1.0)))
            self._service_wake_event.clear()
        with self._lock:
            self._sync_service_status_locked()
            self._log_locked("stop", "行为树常驻服务已停止")
        self._release_service_owner()
        self._persist_status()

    def _start_due_scheduler_tasks_if_idle(self, entry: UserDevice, entry_id: str, asset_tree_path: Path) -> bool:
        if self.status().get("running"):
            return False
        tasks = _read_data_annotation_scheduler_tasks()
        due_tasks = sorted(
            [
                item
                for item in tasks
                if str(item.get("schedule_kind") or "") != "manual"
                and _data_annotation_task_due(item)
                and _data_annotation_task_supported(item)
            ],
            key=data_annotation_scheduler_order_key,
        )
        if not due_tasks:
            return False
        self.start_scheduler_tasks(
            entry=entry,
            entry_id=entry_id,
            tasks=due_tasks,
            all_tasks=tasks,
            asset_tree_path=asset_tree_path,
            run_label="执行全部到期任务",
        )
        return True

    def _run_idle_guard_tick(self, entry: UserDevice, entry_id: str, asset_tree_path: Path) -> None:
        try:
            tree = self._load_asset_tree(asset_tree_path)
            ctx = {
                "entry": entry,
                "asset_tree": tree,
                "asset_tree_path": asset_tree_path,
                "images": self._index_images(tree),
            }
            self._require_assets(ctx)
            self._runtime_guard_service_tick("close_popups", ctx, asset_tree_path, threading.Event())
            frame = self._screencap(ctx)
            key, score = self._identify_scene(ctx, frame)
            scene_id = self.scene_ids.get(key)
            if scene_id is not None and self._scene_matches(key, score):
                with self._lock:
                    self._status.update({
                        "entry_id": entry_id,
                        "current_scene": scene_id,
                        "status": "idle",
                        "phase": "idle_tick",
                        "message": f"空转识别：#{scene_id} {key} {score:.0f}%",
                        "updated_at": time.time(),
                    })
            self._clear_tick_frame(ctx)
            self._persist_status()
        except Exception as exc:
            with self._lock:
                self._log_locked("error", f"守护空转失败：{exc}", scope="guard", item_id="close_popups")

    def stop_current_task(self, entry_id: str) -> dict[str, Any]:
        with self._lock:
            if entry_id and self._status.get("entry_id") not in {"", entry_id}:
                return self.status()
            if not self._status.get("running"):
                self._sync_guard_status_locked()
                self._sync_service_status_locked()
                service_running = bool(self._status.get("service_running"))
                self._set_status_locked("idle", "当前没有正在运行的任务" if service_running else "行为树常驻服务未初始化")
                return json.loads(json.dumps(self._status, ensure_ascii=False))
            if self._stop_event is not None:
                self._stop_event.set()
            self._set_status_locked("stopping", "当前任务停止请求已发送")
        return self.status()

    def set_guard(
        self,
        *,
        entry: UserDevice,
        entry_id: str,
        enabled: bool,
        interval_seconds: float,
        guard_id: str = "close_popups",
        asset_tree_path: Path,
    ) -> dict[str, Any]:
        guard_id = str(guard_id or "close_popups").strip() or "close_popups"
        interval_seconds = max(0.5, min(30.0, float(interval_seconds or 2.0)))
        with self._lock:
            guard_item = self._guard_items.setdefault(guard_id, {})
            guard_item.update({
                "enabled": bool(enabled),
                "entry_id": entry_id if enabled else "",
                "updated_at": time.time(),
            })
            if guard_id != "close_popups":
                self._set_status_locked(str(self._status.get("status") or "idle"), f"守护{'已开启' if enabled else '已关闭'}：{guard_id}")
                self._sync_guard_status_locked()
                self._sync_service_status_locked()
                self._log_locked("info", self._status["message"], scope="guard", item_id=guard_id)
            else:
                self._guard_enabled = bool(enabled)
                self._guard_entry_id = entry_id if enabled else ""
                self._guard_interval_seconds = interval_seconds
                if not enabled:
                    self._set_status_locked("idle" if not self._status.get("running") else str(self._status.get("status") or "running"), "守护已关闭")
                    self._sync_guard_status_locked()
                    self._sync_service_status_locked()
                else:
                    self._set_status_locked(str(self._status.get("status") or "idle"), "守护已开启")
                    self._sync_guard_status_locked()
                    self._sync_service_status_locked()
        self.ensure_service(entry=entry, entry_id=entry_id, asset_tree_path=asset_tree_path)
        self._service_wake_event.set()
        return self.status()

    def start_runtime_task(
        self,
        *,
        entry: UserDevice,
        entry_id: str,
        task_type: str,
        payload: dict[str, Any],
        asset_tree_path: Path,
    ) -> dict[str, Any]:
        task_type = str(task_type or "").strip()
        payload = dict(payload or {})
        definition = _data_annotation_manual_job_definition(task_type)
        if definition is None:
            raise HTTPException(status_code=400, detail=f"暂不支持的任务类型：{task_type}")
        if definition.normalize_payload is not None:
            payload = definition.normalize_payload(payload)
        return self._run_inline_runtime_task(
            entry=entry,
            entry_id=entry_id,
            task_type=task_type,
            payload=payload,
            asset_tree_path=asset_tree_path,
        )

    def _run_inline_runtime_task(
        self,
        *,
        entry: UserDevice,
        entry_id: str,
        task_type: str,
        payload: dict[str, Any],
        asset_tree_path: Path,
    ) -> dict[str, Any]:
        payload = dict(payload or {})
        with self._lock:
            if self._status.get("running"):
                raise HTTPException(status_code=409, detail="数据标注 Runtime 正在运行任务")
            stop_event = threading.Event()
            self._stop_event = stop_event
            now = time.time()
            label = self._runtime_task_label(task_type, payload)
            self._status = {
                **self._status_base_preserving_guard_locked(),
                "running": True,
                "status": "running",
                "entry_id": entry_id,
                "task_type": task_type,
                "current_task": label,
                "phase": "start",
                "message": "任务已启动",
                "total": 1,
                "current_task_id": str(payload.get("__scheduler_task_id") or ""),
                "interruptible": bool(payload.get("__scheduler_interruptible", True)),
                "started_at": now,
                "updated_at": now,
            }
            self._log_locked("info", f"启动 Runtime 任务：{label}")
        self._run_generic_runtime_task(
            entry=entry,
            entry_id=entry_id,
            task_type=task_type,
            payload=dict(payload),
            asset_tree_path=asset_tree_path,
            stop_event=stop_event,
        )
        return self.status()

    def start_manual_runtime_task(
        self,
        *,
        entry: UserDevice,
        entry_id: str,
        task: dict[str, Any],
        asset_tree_path: Path,
    ) -> dict[str, Any]:
        task_id = str(task.get("id") or uuid.uuid4().hex)
        task_type = str(task.get("task_type") or "detect_scene")
        payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        label = str(task.get("label") or self._runtime_task_label(task_type, payload) or task_type)
        with self._lock:
            if self._status.get("running"):
                raise HTTPException(status_code=409, detail="数据标注 Runtime 正在运行任务")
            stop_event = threading.Event()
            self._stop_event = stop_event
            now = time.time()
            self._status = {
                **self._status_base_preserving_guard_locked(),
                "running": True,
                "status": "running",
                "entry_id": entry_id,
                "task_type": task_type,
                "current_task": label,
                "phase": "manual_job",
                "message": f"手动作业已启动：{label}",
                "total": 1,
                "current_task_id": task_id,
                "interruptible": bool(task.get("interruptible", True)),
                "started_at": now,
                "updated_at": now,
            }
            self._log_locked(
                "info",
                self._manual_job_log_message(task_id, self._status["message"]),
                scope="manual_job",
                item_id="manual_job",
            )
        self._run_manual_runtime_task(
            entry=entry,
            entry_id=entry_id,
            task=dict(task),
            asset_tree_path=asset_tree_path,
            stop_event=stop_event,
        )
        return self.status()

    def start_scheduler_tasks(
        self,
        *,
        entry: UserDevice,
        entry_id: str,
        tasks: list[dict[str, Any]],
        all_tasks: list[dict[str, Any]],
        asset_tree_path: Path,
        run_label: str = "执行全部到期任务",
    ) -> dict[str, Any]:
        if not tasks:
            raise HTTPException(status_code=400, detail="没有可执行的到期任务")
        is_run_due = run_label == "执行全部到期任务"
        with self._lock:
            if self._status.get("running"):
                raise HTTPException(status_code=409, detail="数据标注 Runtime 正在运行任务")
            stop_event = threading.Event()
            self._stop_event = stop_event
            now = time.time()
            self._status = {
                **self._status_base_preserving_guard_locked(),
                "running": True,
                "status": "running",
                "entry_id": entry_id,
                "task_type": "scheduler_run_due" if is_run_due else "scheduler_run_now",
                "current_task": run_label,
                "phase": "start",
                "message": f"Scheduler 已启动：{run_label}，共 {len(tasks)} 个任务",
                "total": len(tasks),
                "current_task_id": "scheduler_run_due" if is_run_due else str(tasks[0].get("id") or "scheduler_run_now"),
                "interruptible": all(bool(item.get("interruptible", True)) for item in tasks),
                "started_at": now,
                "updated_at": now,
            }
            self._log_locked("info", f"启动 Scheduler：{run_label}，共 {len(tasks)} 个")
        self._run_scheduler_tasks(
            entry=entry,
            entry_id=entry_id,
            tasks=[dict(item) for item in tasks],
            all_tasks=[dict(item) for item in all_tasks],
            asset_tree_path=asset_tree_path,
            stop_event=stop_event,
            run_label=run_label,
        )
        return self.status()

    def _set_status_locked(self, status: str, message: str = "", **extra: Any) -> None:
        self._status.update({"status": status, "updated_at": time.time(), **extra})
        if message:
            self._status["message"] = message

    def _clear_current_task_locked(self) -> None:
        self._status.update({
            "running": False,
            "task_type": "",
            "current_task": "",
            "current_task_id": "",
            "interruptible": True,
        })

    def _set_log_context(self, scope: str, item_id: str) -> tuple[str, str]:
        with self._lock:
            previous = (self._log_scope, self._log_item_id)
            self._log_scope = str(scope or "")
            self._log_item_id = str(item_id or "")
            return previous

    def _restore_log_context(self, previous: tuple[str, str]) -> None:
        with self._lock:
            self._log_scope, self._log_item_id = previous

    def _log_locked(self, kind: str, message: str, *, scope: str | None = None, item_id: str | None = None) -> None:
        log_scope = self._log_scope if scope is None else str(scope or "")
        log_item_id = self._log_item_id if item_id is None else str(item_id or "")
        append_data_annotation_runtime_status_log(
            self._status,
            kind,
            message,
            scope=log_scope,
            item_id=log_item_id,
            time_text=datetime.now().strftime("%H:%M:%S"),
            updated_at=time.time(),
        )

    def _log(self, kind: str, message: str) -> None:
        with self._lock:
            self._log_locked(kind, message)

    def _manual_job_log_message(self, task_id: str, message: str) -> str:
        task_id = str(task_id or "").strip()
        return f"[{task_id}] {message}" if task_id else message

    def _persist_status(self) -> None:
        try:
            _persist_data_annotation_runtime_status(self.status())
        except Exception:
            pass

    def _runtime_task_label(self, task_type: str, payload: dict[str, Any] | None = None) -> str:
        definition = _data_annotation_manual_job_definition(task_type)
        label = definition.label if definition is not None else task_type
        if task_type == "go_scene":
            target = (payload or {}).get("target_scene_id") or (payload or {}).get("target")
            if target:
                label = f"到场景 #{target}"
        if task_type == "mail_claim_check" and (payload or {}).get("observe_only"):
            label = "邮件_全量遍历"
        return label

    def _runtime_guard_enabled(self, guard_id: str) -> bool:
        guard_id = str(guard_id or "").strip()
        with self._lock:
            if guard_id == "close_popups":
                return bool(self._guard_enabled)
            state = self._guard_items.get(guard_id)
            return bool(state.get("enabled")) if isinstance(state, dict) else False

    def _runtime_guard_service_tick(
        self,
        guard_id: str,
        runtime_ctx: dict[str, Any],
        asset_tree_path: Path,
        stop_event: threading.Event,
    ) -> BehaviorTreeStatus:
        self._raise_if_stopped(stop_event)
        guard_id = str(guard_id or "").strip()
        if not self._runtime_guard_enabled(guard_id):
            return BehaviorTreeStatus.SKIP
        if guard_id != "close_popups":
            return BehaviorTreeStatus.SKIP
        with self._lock:
            if str(self._status.get("phase") or "") == "manual_job":
                return BehaviorTreeStatus.SKIP
        previous_log_context = self._set_log_context("guard", "close_popups")
        try:
            frame = self._screencap(runtime_ctx)
            if not self._auto_close_popup_guard_step(runtime_ctx, asset_tree_path, frame):
                return BehaviorTreeStatus.SKIP
            self._persist_status()
            self._clear_tick_frame(runtime_ctx)
            return BehaviorTreeStatus.RUNNING
        finally:
            self._restore_log_context(previous_log_context)

    def _run_runtime_behavior_tree(
        self,
        *,
        runtime_ctx: dict[str, Any],
        asset_tree_path: Path,
        stop_event: threading.Event,
        action: Callable[[], Any],
        label: str,
        tick_seconds: float = 1.0,
        max_runtime_seconds: float | None = None,
    ) -> Any:
        return _DataAnnotationRuntimeContainer(
            self,
            runtime_ctx=runtime_ctx,
            asset_tree_path=asset_tree_path,
            stop_event=stop_event,
        ).run_job_until_complete(
            action=action,
            label=label,
            tick_seconds=tick_seconds,
            max_runtime_seconds=max_runtime_seconds,
        )

    def _task_timeout_seconds(self, payload: dict[str, Any] | None = None) -> float:
        payload = payload if isinstance(payload, dict) else {}
        raw_value = payload.get("max_runtime_seconds", payload.get("timeout_seconds", 600))
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = 600.0
        return max(30.0, min(3600.0, value))

    def _run_direct_runtime_action(
        self,
        action: Callable[[], Any],
        *,
        stop_event: threading.Event,
        tick_seconds: float = 1.0,
        max_runtime_seconds: float | None = None,
    ) -> Any:
        result = action()
        if not isinstance(result, GeneratorType):
            return result
        started_at = time.monotonic()
        while True:
            self._raise_if_stopped(stop_event)
            if max_runtime_seconds is not None and time.monotonic() - started_at > max_runtime_seconds:
                stop_event.set()
                raise RuntimeError(f"行为树任务超时：超过 {max_runtime_seconds:.0f} 秒")
            try:
                status = next(result)
            except StopIteration as stop:
                return stop.value
            if status == BehaviorTreeStatus.FAILURE:
                raise RuntimeError("行为树节点失败")
            stop_event.wait(max(0.1, float(tick_seconds or 1.0)))

    def _run_generic_runtime_task(
        self,
        *,
        entry: UserDevice,
        entry_id: str,
        task_type: str,
        payload: dict[str, Any],
        asset_tree_path: Path,
        stop_event: threading.Event,
    ) -> None:
        task_id = str(payload.get("__scheduler_task_id") or "")
        previous_log_context = self._set_log_context("job", task_id) if task_id else None
        try:
            tree = self._load_asset_tree(asset_tree_path)
            ctx = {
                "entry": entry,
                "asset_tree": tree,
                "asset_tree_path": asset_tree_path,
                "images": self._index_images(tree),
            }
            self._require_assets(ctx)
            task_result = self._run_runtime_behavior_tree(
                runtime_ctx=ctx,
                asset_tree_path=asset_tree_path,
                stop_event=stop_event,
                action=lambda: self._execute_runtime_task(ctx, task_type, payload, stop_event),
                label=self._runtime_task_label(task_type, payload),
                max_runtime_seconds=self._task_timeout_seconds(payload),
            )
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({
                    "status": "success" if task_result == "success" else str(task_result or "success"),
                    "phase": "done",
                    "message": f"{self._runtime_task_label(task_type, payload)}完成" if task_result == "success" else f"{self._runtime_task_label(task_type, payload)}已跳过",
                    "finished_at": time.time(),
                    "updated_at": time.time(),
                    "current_index": 1,
                    "current_code": "",
                })
                self._log_locked("success" if task_result == "success" else "skip", self._status["message"])
        except InterruptedError:
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"status": "stopped", "phase": "stopped", "message": "已停止", "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("stop", "任务已停止")
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"ok": False, "status": "error", "phase": "error", "message": str(detail), "error": str(detail), "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("error", str(detail))
        finally:
            if previous_log_context is not None:
                self._restore_log_context(previous_log_context)
            self._persist_status()

    def _run_manual_runtime_task(
        self,
        *,
        entry: UserDevice,
        entry_id: str,
        task: dict[str, Any],
        asset_tree_path: Path,
        stop_event: threading.Event,
    ) -> None:
        task_id = str(task.get("id") or "")
        previous_log_context = self._set_log_context("manual_job", "manual_job")
        try:
            tree = self._load_asset_tree(asset_tree_path)
            ctx = {
                "entry": entry,
                "asset_tree": tree,
                "asset_tree_path": asset_tree_path,
                "images": self._index_images(tree),
            }
            self._require_assets(ctx)
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            result = self._run_direct_runtime_action(
                lambda: self._execute_runtime_task(ctx, str(task.get("task_type") or ""), payload, stop_event),
                stop_event=stop_event,
                max_runtime_seconds=self._task_timeout_seconds(payload),
            )
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "")
            if scheduler_task_id:
                tasks = _read_data_annotation_scheduler_tasks()
                self._mark_scheduler_task(tasks, scheduler_task_id, str(result or "success"))
            elif (result or "success") == "success":
                self._mark_matching_scheduler_tasks_for_manual_success(str(task.get("task_type") or ""), payload)
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({
                    "status": "success" if (result or "success") == "success" else str(result or "success"),
                    "phase": "done",
                    "message": f"手动作业完成：{task.get('label') or task.get('task_type') or task_id}",
                    "finished_at": time.time(),
                    "updated_at": time.time(),
                    "current_index": 1,
                })
                self._log_locked("success", self._manual_job_log_message(task_id, self._status["message"]), scope="manual_job", item_id="manual_job")
        except InterruptedError:
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"status": "stopped", "phase": "stopped", "message": "手动作业已停止", "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("stop", self._manual_job_log_message(task_id, "手动作业已停止"), scope="manual_job", item_id="manual_job")
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "")
            if scheduler_task_id:
                tasks = _read_data_annotation_scheduler_tasks()
                self._mark_scheduler_task(tasks, scheduler_task_id, "error")
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"ok": False, "status": "error", "phase": "error", "message": str(detail), "error": str(detail), "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("error", self._manual_job_log_message(task_id, str(detail)), scope="manual_job", item_id="manual_job")
        finally:
            _remove_data_annotation_manual_job(task_id)
            if previous_log_context is not None:
                self._restore_log_context(previous_log_context)
            self._persist_status()

    def _run_scheduler_tasks(
        self,
        *,
        entry: UserDevice,
        entry_id: str,
        tasks: list[dict[str, Any]],
        all_tasks: list[dict[str, Any]],
        asset_tree_path: Path,
        stop_event: threading.Event,
        run_label: str = "执行全部到期任务",
    ) -> None:
        try:
            tree = self._load_asset_tree(asset_tree_path)
            ctx = {
                "entry": entry,
                "asset_tree": tree,
                "asset_tree_path": asset_tree_path,
                "images": self._index_images(tree),
            }
            self._require_assets(ctx)
            for index, task in enumerate(tasks):
                self._raise_if_stopped(stop_event)
                task_id = str(task.get("id") or "")
                label = str(task.get("label") or task_id or task.get("task_type") or "未命名任务")
                previous_log_context = self._set_log_context("job", task_id) if task_id else None
                try:
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"Scheduler 执行 {index + 1}/{len(tasks)}：{label}",
                            current_index=index,
                            current_task=label,
                            task_type=str(task.get("task_type") or ""),
                            phase="scheduler_task",
                            current_task_id=task_id,
                            interruptible=bool(task.get("interruptible", True)),
                        )
                        self._log_locked("action", f"开始到期任务：{label}")
                    self._mark_scheduler_task(all_tasks, task_id, "running")
                    result = self._run_runtime_behavior_tree(
                        runtime_ctx=ctx,
                        asset_tree_path=asset_tree_path,
                        stop_event=stop_event,
                        action=lambda task=task: self._execute_runtime_task(
                            ctx,
                            str(task.get("task_type") or ""),
                            _data_annotation_task_payload_with_meta(task),
                            stop_event,
                        ),
                        label=label,
                        max_runtime_seconds=self._task_timeout_seconds(_data_annotation_task_payload_with_meta(task)),
                    )
                    self._mark_scheduler_task(all_tasks, task_id, result or "success")
                    with self._lock:
                        self._log_locked("success" if (result or "success") == "success" else "skip", f"到期任务{('完成' if (result or 'success') == 'success' else '跳过')}：{label}")
                finally:
                    if previous_log_context is not None:
                        self._restore_log_context(previous_log_context)
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({
                    "status": "success",
                    "phase": "done",
                    "message": f"{run_label}完成",
                    "finished_at": time.time(),
                    "updated_at": time.time(),
                    "current_index": len(tasks),
                    "current_code": "",
                })
                self._log_locked("success", f"Scheduler {run_label}完成")
        except InterruptedError:
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"status": "stopped", "phase": "stopped", "message": "已停止", "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("stop", "Scheduler 任务已停止")
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            current_task_id = ""
            with self._lock:
                current_task_id = str(self._status.get("current_task_id") or "")
                self._clear_current_task_locked()
                self._status.update({"ok": False, "status": "error", "phase": "error", "message": str(detail), "error": str(detail), "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("error", str(detail), scope="job" if current_task_id else None, item_id=current_task_id or None)
            if current_task_id:
                self._mark_scheduler_task(all_tasks, current_task_id, "error")
        finally:
            self._persist_status()

    def _mark_scheduler_task(self, tasks: list[dict[str, Any]], task_id: str, result: str) -> None:
        if not task_id:
            return
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        changed = False
        for item in tasks:
            if str(item.get("id") or "") != task_id:
                continue
            if result == "running":
                item["last_run_at"] = now_text
                item["retry_after"] = None
            elif result in {"success", "skipped", "unsupported"}:
                item["retry_after"] = None
                item["next_time"] = _next_data_annotation_scheduler_time(item)
            elif result == "error":
                cooldown_seconds = int(item.get("cooldown_seconds") or 600)
                item["retry_after"] = (datetime.now() + timedelta(seconds=cooldown_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            item["last_result"] = result
            changed = True
            break
        if changed:
            _write_data_annotation_scheduler_tasks(tasks)
            _record_data_annotation_scheduler_task_fact(item, result)

    def _mark_matching_scheduler_tasks_for_manual_success(self, task_type: str, payload: dict[str, Any]) -> None:
        if str(payload.get("__scheduler_task_id") or ""):
            return
        normalized_task_type = str(task_type or "").strip()
        if not normalized_task_type:
            return
        tasks = _read_data_annotation_scheduler_tasks()
        matched = False
        for item in tasks:
            if str(item.get("task_type") or "") != normalized_task_type:
                continue
            if not _data_annotation_task_supported(item):
                continue
            last_result = str(item.get("last_result") or "")
            if not item.get("retry_after") and last_result not in {"queued", "running", "error", "stopped"}:
                continue
            self._mark_scheduler_task(tasks, str(item.get("id") or ""), "success")
            matched = True
        if matched:
            self._log("detail", f"手动作业成功后已同步清理同类 Scheduler 重试：{normalized_task_type}")

    def _execute_runtime_task(self, ctx: dict[str, Any], task_type: str, payload: dict[str, Any], stop_event: threading.Event) -> str:
        if task_type in {"legacy_daily_task", "legacy_dynamic_task"}:
            legacy_name = str(payload.get("legacy_name") or task_type)
            self._log("skip", f"旧版任务「{legacy_name}」尚未迁移，已跳过")
            return "unsupported"
        definition = _data_annotation_manual_job_definition(task_type)
        if definition is None:
            raise RuntimeError(f"暂不支持的任务类型：{task_type}")
        normalized_payload = dict(payload or {})
        if definition.normalize_payload is not None:
            normalized_payload = definition.normalize_payload(normalized_payload)
        result = definition.handler(self, ctx, normalized_payload, stop_event)
        if isinstance(result, GeneratorType):
            return result
        return str(result or "success")

    def _execute_mail_claim_check_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        ensure_fanxiu_mail_table()
        payload = dict(payload or {})
        entry_mode = str(payload.get("entry_mode") or payload.get("mail_entry_mode") or "dynamic").strip().lower()
        observe_only = bool(payload.get("observe_only") or payload.get("scan_only"))
        scan_mode = str(payload.get("scan_mode") or ("full" if payload.get("full_scan") else "incremental")).strip().lower()
        use_current_page = bool(payload.get("use_current_page"))
        target_title = str(payload.get("target_title") or payload.get("mail_title") or "").strip()
        target_time_text = self._normalize_mail_time_text(str(payload.get("target_time_text") or payload.get("mail_time_text") or "").strip())
        capture_enabled = not bool(payload.get("skip_capture") or payload.get("no_capture"))
        try:
            max_actions = int(payload.get("max_actions") or 0)
        except (TypeError, ValueError):
            max_actions = 0
        raw_action_policies = payload.get("action_policies")
        if isinstance(raw_action_policies, list):
            action_policies = {str(item or "").strip().lower() for item in raw_action_policies}
            action_policies &= {"claim", "delete"}
        else:
            action_policies = {"claim", "delete"}
        if not action_policies:
            action_policies = {"claim", "delete"}
        if observe_only and not payload.get("scan_mode") and not payload.get("full_scan"):
            scan_mode = "full"
        capture_reason = f"mail-full-scan:{'observe' if observe_only else 'action'}"
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少邮件_领取检查资产树路径，无法执行邮件作业")
        try:
            if capture_enabled:
                fanxiu_capture_runtime_service.ensure_running(capture_reason)
                with self._lock:
                    self._log_locked("info", f"邮件_抓包：已请求抓包服务 {capture_reason}")
                self._refresh_recent_mail_packets_for_runtime_log("启动抓包后", flush_capture=False)
            else:
                with self._lock:
                    self._log_locked("info", "邮件_领取检查：本轮跳过抓包协作，仅使用当前页与既有邮件事实")
        except Exception as exc:
            with self._lock:
                self._log_locked("error", f"邮件_抓包：启动抓包服务失败：{exc}")
            raise
        try:
            if observe_only:
                with self._lock:
                    self._log_locked("info", "邮件_全量遍历：只观察并滚动加载邮件，不领取、不删除")
            scene_id, score, frame = self._current_scene_number(ctx)
            force_reopen_mail = observe_only or scan_mode in {"full", "full_scan", "observe", "observe_only", "refresh", "sync"}
            if scene_id == 121 and not use_current_page and (force_reopen_mail or (not observe_only and self._pending_packet_mail_action_count() > 0)):
                image121 = ctx.get("images", {}).get(121)
                back_shape = self._find_shape(image121, "空白-返回") if isinstance(image121, dict) else None
                if isinstance(image121, dict) and back_shape:
                    reason = "刷新邮件 packet 列表" if force_reopen_mail else "重置列表顶部"
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"邮件_领取检查：退出邮件页以{reason}",
                            phase="mail_claim_reset_mail_list",
                            current_scene=121,
                        )
                        self._log_locked("action", f"邮件_领取检查：点击 #121「空白-返回」，重新从顶部进入邮件，{reason}")
                    self._click_shape(ctx, image121, back_shape, frame)
                    yield from self._wait_scene_id(ctx, stop_event, 34, timeout=12.0, label="邮件_领取检查：返回世界 #34")
                    scene_id = 34
                else:
                    with self._lock:
                        self._log_locked("error", "邮件_领取检查：缺少 #121「空白-返回」标注，保留当前位置扫描")
            if scene_id != 121:
                open_result = self._open_mail_scene(ctx, stop_event, asset_tree_path, entry_mode=entry_mode)
                result = (yield from open_result) if isinstance(open_result, GeneratorType) else open_result
                if result == "no_mail":
                    return "success"
                if result != "success":
                    return result
            else:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"邮件_领取检查：当前已在邮件 #121 {score:.0f}%",
                        phase="mail_claim_resume_mail_scene",
                        current_scene=121,
                    )
                    self._log_locked("info", f"邮件_领取检查：当前已在邮件 #121 {score:.0f}%，直接扫描")
            scan_result = self._scan_mail_scene(
                ctx,
                stop_event,
                action_enabled=not observe_only,
                scan_mode=scan_mode,
                action_policies=action_policies,
                max_actions=max_actions if max_actions > 0 else None,
                target_title=target_title,
                target_time_text=target_time_text,
            )
            return (yield from scan_result) if isinstance(scan_result, GeneratorType) else scan_result
        finally:
            if capture_enabled:
                self._refresh_recent_mail_packets_for_runtime_log("释放抓包前", flush_capture=True)
                try:
                    fanxiu_capture_runtime_service.release(capture_reason)
                    with self._lock:
                        self._log_locked("info", f"邮件_抓包：已释放抓包服务 {capture_reason}")
                except Exception as exc:
                    with self._lock:
                        self._log_locked("error", f"邮件_抓包：释放抓包服务失败：{exc}")

    def _refresh_recent_mail_packets_for_runtime_log(self, label: str, *, flush_capture: bool) -> None:
        try:
            pcap_paths: list[str] = []
            if flush_capture:
                flush = fanxiu_capture_runtime_service.flush_recent_capture(f"mail-claim-check:{label}", restart=False)
                pcap_path = str(flush.get("pcap_path") or "").strip() if isinstance(flush, dict) else ""
                if pcap_path and bool(flush.get("flushed")):
                    pcap_paths.append(pcap_path)
                with self._lock:
                    self._log_locked(
                        "info",
                        "邮件_抓包协作："
                        f"{label} flush={bool(flush.get('flushed')) if isinstance(flush, dict) else False} "
                        f"pcap_size={flush.get('pcap_size', 0) if isinstance(flush, dict) else 0}",
                    )
            if pcap_paths:
                result = sync_fanxiu_capture_paths(pcap_paths, max_streams=4)
            else:
                result = {"decoded_count": 0, "mail_packet_sync": {}}
            mail_sync = result.get("mail_packet_sync") or {}
            if not mail_sync:
                for decoded_item in result.get("decoded") or []:
                    if isinstance(decoded_item, dict) and isinstance(decoded_item.get("batch_mail_packet_sync"), dict):
                        mail_sync = decoded_item["batch_mail_packet_sync"]
                        break
            with self._lock:
                self._log_locked(
                    "info",
                    "邮件_抓包协作："
                    f"{label} decoded={result.get('decoded_count', 0)} "
                    f"updated={mail_sync.get('updated', 0)} inserted={mail_sync.get('inserted', 0)} "
                    f"source_packets={mail_sync.get('source_packets', 0)} action_packets={mail_sync.get('action_packets', 0)}",
                )
        except Exception as exc:
            with self._lock:
                self._log_locked("error", f"邮件_抓包协作：{label}失败：{exc}")

    def _open_mail_scene(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        asset_tree_path: Path,
        *,
        entry_mode: str = "dynamic",
    ) -> str:
        if entry_mode in {"stable", "menu", "full", "full_scan", "debug"}:
            visible_menu_result = self._try_open_mail_from_visible_world_menu(ctx, stop_event, timeout=1.0)
            opened_from_menu = (yield from visible_menu_result) if isinstance(visible_menu_result, GeneratorType) else visible_menu_result
            if opened_from_menu == "success":
                return "success"
        with self._lock:
            self._set_status_locked("running", "邮件_领取检查：确认世界 #34", phase="mail_claim_go_world")
            self._log_locked("action", "邮件_领取检查：先确认进入世界 #34")
        go_scene_result = self._go_scene_task(ctx, asset_tree_path, 34, stop_event)
        result = (yield from go_scene_result) if isinstance(go_scene_result, GeneratorType) else go_scene_result
        if result != "success":
            return result
        if entry_mode in {"stable", "menu", "full", "full_scan", "debug"}:
            stable_result = self._open_mail_stable_entry(ctx, stop_event, asset_tree_path)
            return (yield from stable_result) if isinstance(stable_result, GeneratorType) else stable_result
        dynamic_result = self._try_open_mail_dynamic_entry(ctx, stop_event)
        opened = (yield from dynamic_result) if isinstance(dynamic_result, GeneratorType) else dynamic_result
        if opened == "no_mail":
            return "no_mail"
        if opened == "success":
            return "success"
        stable_result = self._open_mail_stable_entry(ctx, stop_event, asset_tree_path)
        return (yield from stable_result) if isinstance(stable_result, GeneratorType) else stable_result

    def _try_open_mail_dynamic_entry(self, ctx: dict[str, Any], stop_event: threading.Event) -> str:
        image34 = ctx.get("images", {}).get(34)
        image68 = ctx.get("images", {}).get(68)
        if not isinstance(image68, dict) and isinstance(image34, dict):
            image68 = self._find_child_image_by_number(image34, 68)
        mail_shape = self._find_shape(image68, "邮件") if isinstance(image68, dict) else None
        if not isinstance(image68, dict) or not mail_shape:
            self._log("detail", "邮件_领取检查：#68 动态邮件入口标注缺失，尝试稳定入口")
            return "missing"
        with self._lock:
            self._set_status_locked("running", "邮件_领取检查：检测 #68 邮件入口", phase="mail_claim_check_mail", current_scene=34)
            self._log_locked("action", "邮件_领取检查：检测 #68「邮件」")
        frame = self._screencap(ctx)
        result = self._match_shape(ctx, image68, mail_shape, frame)
        similarity = float(result.get("similarity") or 0)
        matched = bool(result.get("matched"))
        if not matched:
            with self._lock:
                self._set_status_locked("running", "邮件_领取检查：#68 未命中，改走 #35 稳定入口", phase="mail_claim_dynamic_missing", current_scene=34)
                self._log_locked("info", f"邮件_领取检查：未发现 #68「邮件」{similarity:.0f}%，改走稳定入口")
            return "missing"
        with self._lock:
            self._set_status_locked("running", "邮件_领取检查：打开 #68 邮件入口", phase="mail_claim_open_mail", current_scene=34)
            self._log_locked("action", f"邮件_领取检查：识别到 #68「邮件」{similarity:.0f}%，点击打开")
        self._click_shape(ctx, image68, mail_shape, frame)
        yield from self._wait_mail_list_ready(ctx, stop_event, timeout=12.0, label="邮件_领取检查：等待邮件 #121")
        return "success"

    def _open_mail_stable_entry(self, ctx: dict[str, Any], stop_event: threading.Event, asset_tree_path: Path) -> str:
        image34 = ctx.get("images", {}).get(34)
        open_shape = self._find_shape(image34, "打开下方菜单") if isinstance(image34, dict) else None
        if not isinstance(image34, dict) or not open_shape:
            raise RuntimeError("缺少 #34「打开下方菜单」标注，无法走稳定邮件入口")
        with self._lock:
            self._set_status_locked("running", "邮件_领取检查：打开下方菜单 #35", phase="mail_claim_open_world_menu", current_scene=34)
            self._log_locked("action", "邮件_领取检查：#68 不可用，尝试 #34 -> #35 稳定入口")
        frame = self._screencap(ctx)
        try:
            self._click_shape(ctx, image34, open_shape, frame)
        except RuntimeError as exc:
            message = str(exc)
            if "打开下方菜单" not in message or "定位" not in message:
                raise
            box = self._box(open_shape, image34)
            x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
            y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
            with self._lock:
                self._log_locked("info", f"邮件_领取检查：#34「打开下方菜单」图像定位失败，改按固定标注点击 ({x:.0f},{y:.0f})")
            self._click_frame_point(ctx, image34, x, y)
        with self._lock:
            self._set_status_locked("running", "邮件_领取检查：等待下方菜单展开", phase="mail_claim_wait_world_menu", current_scene=34)
        deadline = time.time() + 1.0
        while time.time() < deadline:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
        menu_result = self._open_mail_from_world_menu_shape(ctx, stop_event)
        return (yield from menu_result) if isinstance(menu_result, GeneratorType) else menu_result

    def _open_mail_from_world_menu_shape(self, ctx: dict[str, Any], stop_event: threading.Event) -> str:
        # Runtime actions must be driven by the asset-tree annotations. Do not
        # infer alternate menu coordinates from screenshots here; if the current
        # UI changed, update the #34/#35/#121 shapes or sceneJumpTarget data.
        image35 = ctx.get("images", {}).get(35)
        mail_shape = self._find_shape(image35, "邮件") if isinstance(image35, dict) else None
        menu_shape = self._find_shape(image35, "菜单") if isinstance(image35, dict) else None
        if not isinstance(image35, dict) or (not mail_shape and not menu_shape):
            raise RuntimeError("缺少 #35「邮件」或「菜单」标注，无法走稳定邮件入口")
        self._raise_if_stopped(stop_event)
        with self._lock:
            self._set_status_locked("running", "邮件_领取检查：等待 #35 邮件命中", phase="mail_claim_wait_world_menu_mail", current_scene=35)
        if mail_shape and not menu_shape:
            wait_result = self._wait_shape_match(
                ctx,
                stop_event,
                image35,
                mail_shape,
                timeout=8.0,
                label="邮件_领取检查：等待 #35「邮件」",
            )
            frame, match_result = (yield from wait_result) if isinstance(wait_result, GeneratorType) else wait_result
            with self._lock:
                self._set_status_locked("running", "邮件_领取检查：点击 #35 邮件", phase="mail_claim_click_world_menu_mail", current_scene=35)
                self._log_locked("action", "邮件_领取检查：按 #35「邮件」标注点击")
            self._click_shape(ctx, image35, mail_shape, frame, match_result=match_result)
            yield from self._wait_mail_list_ready(ctx, stop_event, timeout=12.0, label="邮件_领取检查：等待邮件 #121")
            return "success"
        deadline = time.time() + 8.0
        last_score = 0.0
        last_ocr = ""
        while time.time() < deadline:
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            if mail_shape:
                match_score = self._shape_score(ctx, image35, mail_shape, frame, match_strategy="auto")
                last_score = max(last_score, match_score)
                if match_score >= float(self.scene_threshold):
                    mail_box = self._box(mail_shape, image35)
                    x = float(mail_box.get("x") or 0) + float(mail_box.get("w") or 0) / 2
                    y = float(mail_box.get("y") or 0) + float(mail_box.get("h") or 0) / 2
                    with self._lock:
                        self._set_status_locked("running", "邮件_领取检查：点击 #35 邮件", phase="mail_claim_click_world_menu_mail", current_scene=35)
                        self._log_locked("action", f"邮件_领取检查：#35「邮件」标注命中 {match_score:.0f}%，点击标注中心 ({x:.0f},{y:.0f})")
                    self._click_frame_point(ctx, image35, x, y)
                    yield from self._wait_mail_list_ready(ctx, stop_event, timeout=12.0, label="邮件_领取检查：等待邮件 #121")
                    return "success"

            ocr_lines = self._ocr_lines(frame)
            last_ocr = " / ".join(str(item.get("text") or "") for item in ocr_lines[-3:]) or last_ocr
            menu_matches = self._ocr_centers_in_shape(ocr_lines, image35, "菜单", include=("邮件",))
            if menu_matches:
                x, y, text = menu_matches[0]
                x, y = self._mail_world_menu_icon_click_point(image35, x, y)
                with self._lock:
                    self._set_status_locked("running", "邮件_领取检查：点击 #35 邮件 OCR", phase="mail_claim_click_world_menu_mail", current_scene=35)
                    self._log_locked("action", f"邮件_领取检查：#35 菜单 OCR 命中「{text}」，点击邮件入口 ({x:.0f},{y:.0f})")
                self._click_frame_point(ctx, image35, x, y)
                yield from self._wait_mail_list_ready(ctx, stop_event, timeout=12.0, label="邮件_领取检查：等待邮件 #121")
                return "success"

            with self._lock:
                self._set_status_locked(
                    "running",
                    f"邮件_领取检查：等待 #35 邮件入口 {last_score:.0f}%",
                    phase="mail_claim_wait_world_menu_mail",
                    current_scene=35,
                )
            yield BehaviorTreeStatus.RUNNING
        raise RuntimeError(f"邮件_领取检查：等待 #35 邮件入口超时，最后 {last_score:.0f}% OCR={last_ocr}")

    def _mail_world_menu_icon_click_point(self, image35: dict[str, Any], x: float, y: float) -> tuple[float, float]:
        mail_shape = self._find_shape(image35, "邮件")
        if mail_shape:
            box = self._box(mail_shape, image35)
            return float(box.get("x") or 0) + float(box.get("w") or 0) / 2, float(box.get("y") or 0) + float(box.get("h") or 0) / 2
        return x, y

    def _try_open_mail_from_visible_world_menu(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float,
    ) -> str:
        image35 = ctx.get("images", {}).get(35)
        if not isinstance(image35, dict):
            return "missing"
        mail_shape = self._find_shape(image35, "邮件")
        deadline = time.time() + max(0.1, float(timeout or 0.1))
        while time.time() < deadline:
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            if mail_shape:
                scene_id, score, _frame = self._current_scene_number(ctx)
                if scene_id == 35 and score >= float(self.scene_threshold):
                    return (yield from self._open_mail_from_world_menu_shape(ctx, stop_event))
                match_score = self._shape_score(ctx, image35, mail_shape, frame, match_strategy="auto")
                if match_score >= float(self.scene_threshold):
                    return (yield from self._open_mail_from_world_menu_shape(ctx, stop_event))
            menu_matches = self._ocr_centers_in_shape(self._ocr_lines(frame), image35, "菜单", include=("邮件",))
            if menu_matches:
                x, y, text = menu_matches[0]
                x, y = self._mail_world_menu_icon_click_point(image35, x, y)
                with self._lock:
                    self._set_status_locked("running", "邮件_领取检查：点击 #35 邮件 OCR", phase="mail_claim_click_world_menu_mail", current_scene=35)
                    self._log_locked("action", f"邮件_领取检查：#35 无「邮件」shape，点击菜单 OCR「{text}」({x:.0f},{y:.0f})")
                self._click_frame_point(ctx, image35, x, y)
                yield from self._wait_mail_list_ready(ctx, stop_event, timeout=12.0, label="邮件_领取检查：等待邮件 #121")
                return "success"
            with self._lock:
                self._set_status_locked("running", "邮件_领取检查：探测可见下方菜单邮件入口", phase="mail_claim_probe_world_menu_mail")
            yield BehaviorTreeStatus.RUNNING
        return "missing"

    def _scan_mail_scene(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        action_enabled: bool = True,
        scan_mode: str = "incremental",
        action_policies: set[str] | None = None,
        max_actions: int | None = None,
        target_title: str = "",
        target_time_text: str = "",
    ) -> str:
        image121 = ctx.get("images", {}).get(121)
        if not isinstance(image121, dict):
            raise RuntimeError("缺少 #121 邮件帧标注，无法扫描邮件")
        first_shape = self._find_shape(image121, "第1封")
        list_shape = self._find_shape(image121, "邮件清单2") or self._find_shape(image121, "邮件清单")
        if not first_shape:
            raise RuntimeError("缺少 #121「第1封」标注，无法处理首封邮件")
        if not list_shape:
            raise RuntimeError("缺少 #121「邮件清单2」标注，无法遍历邮件清单")

        processed_count = 0
        seen_count = 0
        scroll_count = 0
        scan_started_at = time.monotonic()
        last_signature = ""
        stable_same_pages = 0
        empty_pages = 0
        max_scrolls = 80
        configured_max_actions = max_actions is not None
        max_actions = max(1, min(int(max_actions or 200), 200))
        max_stable_same_pages = 5
        max_empty_pages = 5
        mode = str(scan_mode or "incremental").strip().lower()
        full_scan = mode in {"full", "full_scan", "observe", "observe_only"}
        target_title = str(target_title or "").strip()
        target_time_text = self._normalize_mail_time_text(str(target_time_text or "").strip())
        target_requested = bool(target_title or target_time_text)
        allowed_policies = (set(action_policies or {"claim", "delete"}) & {"claim", "delete"}) or {"claim", "delete"}
        pending_actions = self._pending_packet_mail_action_count(allowed_policies=allowed_policies) if action_enabled else 0
        if action_enabled and (pending_actions > 0 or full_scan):
            max_scrolls = min(max_scrolls, 24)
            max_scan_seconds = 420.0 if full_scan else 300.0
        elif not action_enabled and full_scan:
            max_scrolls = min(max_scrolls, 16)
            max_scan_seconds = 360.0
        else:
            max_scan_seconds = 0.0
        scan_state = self._read_mail_scan_state()
        watermark_time = "" if full_scan else str(scan_state.get("confirmed_time_bucket") or "")
        top_time = ""
        crossed_watermark = not bool(watermark_time)
        scan_truncated = False
        if action_enabled:
            with self._lock:
                self._log_locked("info", f"邮件_领取检查：packet 待处理邮件 {pending_actions} 封")
                if target_requested:
                    target_parts = []
                    if target_title:
                        target_parts.append(f"标题={target_title}")
                    if target_time_text:
                        target_parts.append(f"时间={target_time_text}")
                    self._log_locked("info", f"邮件_领取检查：本轮只处理目标邮件：{'，'.join(target_parts)}")
            if pending_actions <= 0 and not full_scan and not target_requested:
                with self._lock:
                    self._log_locked("success", "邮件_领取检查：packet 无待处理邮件，跳过动作扫描")
                return "success"
        if watermark_time:
            with self._lock:
                self._log_locked("info", f"邮件_领取检查：增量扫描水位 {watermark_time}，需扫到更早时间才可停止")
        else:
            with self._lock:
                self._log_locked("info", "邮件_领取检查：未建立增量水位，本轮按深扫建立水位")
        while scroll_count <= max_scrolls and processed_count < max_actions:
            self._raise_if_stopped(stop_event)
            if max_scan_seconds > 0 and time.monotonic() - scan_started_at >= max_scan_seconds:
                scan_truncated = True
                with self._lock:
                    self._log_locked(
                        "info",
                        f"邮件_领取检查：动作扫描达到内部时间预算 {max_scan_seconds:.0f}s，提前收尾",
                    )
                break
            frame = self._screencap(ctx)
            rows = self._recognize_visible_mail_rows(ctx, image121, frame)
            action_candidate: dict[str, Any] | None = None
            for row in rows:
                self._prepare_mail_row_policy(row, action_enabled=action_enabled, action_policies=allowed_policies)
                if target_requested and not self._mail_row_matches_target(row, target_title=target_title, target_time_text=target_time_text):
                    row["policy"] = ""
                seen_count += 1
                top_time = top_time or str(row.get("time_text") or "")
                if watermark_time and self._mail_time_is_older_than(str(row.get("time_text") or ""), watermark_time):
                    crossed_watermark = True
                if action_candidate is None and row.get("policy"):
                    action_candidate = row
            if rows:
                row_summary = "；".join(
                    f"{str(row.get('title') or '')[:18]}|{row.get('time_text') or '-'}|{row.get('policy') or '-'}|lock={row.get('list_lock_score', '-')}"
                    for row in rows[:6]
                )
                with self._lock:
                    self._log_locked("detail", f"邮件_领取检查：当前页重新 OCR 识别 {len(rows)} 行：{row_summary}")
            if action_candidate is not None:
                action_status = self._process_mail_row(ctx, stop_event, image121, action_candidate)
                status = (yield from action_status) if isinstance(action_status, GeneratorType) else action_status
                if status == "processed":
                    processed_count += 1
                    if target_requested:
                        break
                    if not full_scan and self._pending_packet_mail_action_count(allowed_policies=allowed_policies) <= 0:
                        with self._lock:
                            self._log_locked("success", "邮件_领取检查：packet 待处理邮件已清零，停止扫描")
                        break
                    continue
            if action_enabled and not full_scan and not target_requested and self._pending_packet_mail_action_count(allowed_policies=allowed_policies) <= 0:
                break

            if watermark_time and crossed_watermark:
                with self._lock:
                    self._log_locked("success", f"邮件_领取检查：已扫到早于水位 {watermark_time} 的邮件，增量段完整接回")
                break

            signature = self._mail_rows_signature(rows)
            if rows:
                empty_pages = 0
                if signature and signature == last_signature:
                    stable_same_pages += 1
                    if stable_same_pages >= max_stable_same_pages:
                        break
                else:
                    stable_same_pages = 0
                    last_signature = signature
            else:
                empty_pages += 1
                if empty_pages >= max_empty_pages:
                    break
            scroll_count += 1
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"邮件_领取检查：邮件清单向下滚动 {scroll_count}",
                    phase="mail_claim_scroll_list",
                    current_scene=121,
                )
                self._log_locked("action", f"邮件_领取检查：当前页无可处理邮件，滚动邮件清单2 {scroll_count}")
            self._scroll_shape_content(ctx, image121, list_shape)
            yield from self._wait_scroll_settle(ctx, stop_event)

        if processed_count >= max_actions and not configured_max_actions:
            raise RuntimeError(f"邮件_领取检查：达到单轮处理上限 {max_actions}，为避免异常循环已停止")
        if configured_max_actions and processed_count >= max_actions:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"邮件_领取检查：达到本轮指定处理数 {max_actions}，见到 {seen_count} 封，处理 {processed_count} 封",
                    phase="mail_claim_done",
                    current_scene=121,
                )
                self._log_locked("success", f"邮件_领取检查：达到本轮指定处理数 {max_actions}，见到 {seen_count} 封，处理 {processed_count} 封")
            return "success"
        if target_requested and processed_count > 0:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"邮件_领取检查：目标邮件处理完成，见到 {seen_count} 封，处理 {processed_count} 封",
                    phase="mail_claim_done",
                    current_scene=121,
                )
                self._log_locked("success", f"邮件_领取检查：目标邮件处理完成，见到 {seen_count} 封，处理 {processed_count} 封")
            return "success"
        pending_after_scan = self._pending_packet_mail_action_count(allowed_policies=allowed_policies) if action_enabled else 0
        if action_enabled and full_scan and pending_after_scan > 0 and not scan_truncated:
            marked_count = self._mark_pending_packet_mail_actions_not_visible(
                reason=f"full_scan_seen={seen_count}; processed={processed_count}; scrolls={scroll_count}",
                allowed_policies=allowed_policies,
            )
            with self._lock:
                self._log_locked(
                    "info",
                    f"邮件_领取检查：完整扫描未见 {marked_count} 封待处理邮件，标记为 missing_from_list",
                )
        elif action_enabled and full_scan and pending_after_scan > 0 and scan_truncated:
            with self._lock:
                self._log_locked(
                    "info",
                    f"邮件_领取检查：本轮扫描被时间预算截断，仍有 {pending_after_scan} 封待处理邮件，不标记 missing_from_list",
                )
        if watermark_time and not crossed_watermark:
            self._write_mail_scan_state(
                {
                    **scan_state,
                    "status": "gap_risk",
                    "last_scan_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_seen_top_time": top_time,
                    "last_seen_count": seen_count,
                    "last_processed_count": processed_count,
                    "message": f"未接回增量水位 {watermark_time}",
                }
            )
            raise RuntimeError(f"邮件_领取检查：未接回增量水位 {watermark_time}，可能存在中间遗漏，请继续遍历或用 full_scan/observe_only 补洞")
        if top_time:
            self._write_mail_scan_state(
                {
                    "status": "confirmed",
                    "confirmed_time_bucket": top_time,
                    "confirmed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_scan_mode": "full" if full_scan else "incremental",
                    "last_seen_count": seen_count,
                    "last_processed_count": processed_count,
                    "previous_confirmed_time_bucket": scan_state.get("confirmed_time_bucket") or "",
                }
            )

        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_领取检查：完成，见到 {seen_count} 封，处理 {processed_count} 封",
                phase="mail_claim_done",
                current_scene=121,
            )
            self._log_locked("success", f"邮件_领取检查：完成，见到 {seen_count} 封，处理 {processed_count} 封")
        return "success"

    def _recognize_visible_mail_rows(
        self,
        ctx: dict[str, Any],
        image121: dict[str, Any],
        frame: str,
    ) -> list[dict[str, Any]]:
        started_at = time.monotonic()
        lines = self._ocr_lines_in_shapes(frame, image121, ("第1封", "邮件清单2"))
        first_rows = self._mail_rows_in_shape(lines, image121, "第1封")
        list_rows = self._mail_rows_in_shape(lines, image121, "邮件清单2")
        rows = self._merge_visible_mail_rows_by_position(first_rows, list_rows)
        self._annotate_mail_rows_list_state(ctx, image121, frame, rows)
        elapsed = time.monotonic() - started_at
        self._log("detail", f"邮件_领取检查：当前页 OCR+行解析耗时 {elapsed:.1f}s，识别 {len(rows)} 行")
        return rows

    def _read_mail_scan_state(self) -> dict[str, Any]:
        payload = _read_data_annotation_json(_data_annotation_mail_scan_state_path(), {})
        return payload if isinstance(payload, dict) else {}

    def _write_mail_scan_state(self, payload: dict[str, Any]) -> None:
        _write_data_annotation_json(_data_annotation_mail_scan_state_path(), payload)

    def _pending_packet_mail_action_count(self, *, allowed_policies: set[str] | None = None) -> int:
        policies = (set(allowed_policies or {"claim", "delete"}) & {"claim", "delete"}) or {"claim", "delete"}
        with Session(engine) as session:
            records = session.exec(
                select(FanxiuMailRecord).where(
                    FanxiuMailRecord.source == "packet",
                    FanxiuMailRecord.locked == False,  # noqa: E712
                )
            ).all()
        return sum(1 for record in records if fanxiu_mail_action_policy_for_rewards(fanxiu_mail_rewards_from_payload(record.payload)) in policies)

    def _mark_pending_packet_mail_actions_not_visible(self, *, reason: str, allowed_policies: set[str] | None = None) -> int:
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        marked = 0
        policies = (set(allowed_policies or {"claim", "delete"}) & {"claim", "delete"}) or {"claim", "delete"}
        with Session(engine) as session:
            records = session.exec(
                select(FanxiuMailRecord).where(
                    FanxiuMailRecord.source == "packet",
                    FanxiuMailRecord.action_policy.in_(tuple(sorted(policies))),
                    FanxiuMailRecord.locked == False,  # noqa: E712
                    FanxiuMailRecord.status.not_in(("claimed", "deleted", "missing_from_list")),
                )
            ).all()
            for record in records:
                if fanxiu_mail_action_policy_for_record(record) not in policies:
                    continue
                evidence = dict(record.evidence or {})
                evidence.update(
                    {
                        "runtime_action": "missing_from_list",
                        "missing_from_list_at": now_text,
                        "missing_from_list_reason": reason,
                    }
                )
                record.status = "missing_from_list"
                record.action_policy = ""
                record.evidence = evidence
                record.updated_at = time.time()
                session.add(record)
                marked += 1
            if marked:
                session.commit()
        return marked

    def _mail_time_is_older_than(self, current_time_text: str, watermark_time_text: str) -> bool:
        current = parse_data_annotation_task_time(normalize_fanxiu_mail_time_text(current_time_text))
        watermark = parse_data_annotation_task_time(normalize_fanxiu_mail_time_text(watermark_time_text))
        return current is not None and watermark is not None and current < watermark

    def _prepare_and_maybe_process_mail_row(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image121: dict[str, Any],
        row: dict[str, Any],
        *,
        action_enabled: bool = True,
        action_policies: set[str] | None = None,
        target_title: str = "",
        target_time_text: str = "",
    ) -> str:
        self._prepare_mail_row_policy(row, action_enabled=action_enabled, action_policies=action_policies)
        if (target_title or target_time_text) and not self._mail_row_matches_target(row, target_title=target_title, target_time_text=target_time_text):
            row["policy"] = ""
        if not row.get("policy"):
            return "seen"
        return (yield from self._process_mail_row(ctx, stop_event, image121, row))

    def _mail_row_matches_target(self, row: dict[str, Any], *, target_title: str, target_time_text: str) -> bool:
        if target_title:
            observed_title = str(row.get("title") or "").strip()
            if self._mail_title_similarity(observed_title, target_title) < 0.86:
                return False
        if target_time_text:
            observed_time = self._normalize_mail_time_text(str(row.get("time_text") or ""))
            if observed_time != self._normalize_mail_time_text(target_time_text):
                return False
        return True

    def _prepare_mail_row_policy(
        self,
        row: dict[str, Any],
        *,
        action_enabled: bool = True,
        action_policies: set[str] | None = None,
    ) -> None:
        title = str(row.get("title") or "").strip()
        time_text = self._normalize_mail_time_text(str(row.get("time_text") or ""))
        row["policy"] = ""
        if not self._is_valid_mail_time_text(time_text):
            row["time_text"] = ""
            row["mail_key"] = ""
            return
        row["time_text"] = time_text
        record = self._find_packet_mail_record(title, time_text, action_policies=action_policies)
        row["mail_key"] = str(record.mail_key or "") if record else ""
        if action_enabled:
            policy = self._mail_row_packet_action_policy(title, time_text)
            allowed_policies = (set(action_policies or {"claim", "delete"}) & {"claim", "delete"}) or {"claim", "delete"}
            row["policy"] = policy if policy in allowed_policies else ""

    def _mail_row_packet_action_policy(self, title: str, time_text: str) -> str:
        records = self._find_packet_mail_records_for_visible_row(title, time_text)
        if not records:
            return ""
        policies: set[str] = set()
        for record in records:
            if bool(record.locked):
                return ""
            policy = fanxiu_mail_action_policy_for_rewards(fanxiu_mail_rewards_from_payload(record.payload))
            if policy not in {"claim", "delete"}:
                return ""
            policies.add(policy)
        if "claim" in policies:
            return "claim"
        if policies == {"delete"}:
            return "delete"
        return ""

    def _find_packet_mail_records_for_visible_row(self, title: str, time_text: str) -> list[FanxiuMailRecord]:
        normalized_title = normalize_fanxiu_mail_title(title)
        normalized_time = normalize_fanxiu_mail_time_text(time_text)
        if not normalized_title or not normalized_time:
            return []
        with Session(engine) as session:
            exact = session.exec(
                select(FanxiuMailRecord)
                .where(
                    FanxiuMailRecord.source == "packet",
                    FanxiuMailRecord.normalized_title == normalized_title,
                    FanxiuMailRecord.create_time_text == normalized_time,
                )
                .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
            ).all()
            if exact:
                return list(exact)
            same_time = session.exec(
                select(FanxiuMailRecord)
                .where(
                    FanxiuMailRecord.source == "packet",
                    FanxiuMailRecord.create_time_text == normalized_time,
                )
                .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
            ).all()
        observed_key = self._mail_title_similarity_key(title)
        if len(observed_key) < 3:
            return []
        scored: list[tuple[float, FanxiuMailRecord]] = []
        for record in same_time:
            score = self._mail_title_similarity(title, str(record.title or record.normalized_title or ""))
            if score > 0:
                scored.append((score, record))
        if not scored:
            return []
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score = scored[0][0]
        threshold = 0.58 if len(observed_key) >= 5 else 0.72
        if best_score < threshold:
            return []
        return [record for score, record in scored if score >= best_score - 0.06]

    def _find_packet_mail_record(
        self,
        title: str,
        time_text: str,
        *,
        action_policies: set[str] | None = None,
    ) -> FanxiuMailRecord | None:
        normalized_title = normalize_fanxiu_mail_title(title)
        normalized_time = normalize_fanxiu_mail_time_text(time_text)
        if not normalized_title or not normalized_time:
            return None
        with Session(engine) as session:
            record = session.exec(
                select(FanxiuMailRecord).where(
                    FanxiuMailRecord.source == "packet",
                    FanxiuMailRecord.normalized_title == normalized_title,
                    FanxiuMailRecord.create_time_text == normalized_time,
                )
            ).first()
            if record:
                return record
            record = session.exec(
                select(FanxiuMailRecord).where(
                    FanxiuMailRecord.source == "packet",
                    FanxiuMailRecord.title == title,
                    FanxiuMailRecord.create_time_text == normalized_time,
                )
            ).first()
            if record:
                return record
            time_candidates = session.exec(
                select(FanxiuMailRecord)
                .where(
                    FanxiuMailRecord.source == "packet",
                    FanxiuMailRecord.create_time_text == normalized_time,
                )
                .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
            ).all()
            fuzzy = self._select_packet_mail_record_by_fuzzy_title(
                title,
                time_candidates,
                action_policies=action_policies,
            )
            if fuzzy:
                return fuzzy
            return None

    def _visible_packet_mail_action_policy(self, record: FanxiuMailRecord | None) -> str:
        if record is None or bool(record.locked):
            return ""
        status = str(record.status or "").strip().lower()
        if status in {"claimed", "deleted"}:
            return ""
        if status in {"claim_requested", "delete_requested"}:
            retry_policy = status.removesuffix("_requested")
            if not self._mail_requested_action_retryable(record, retry_policy):
                return ""
            return retry_policy
        return fanxiu_mail_action_policy_for_rewards(fanxiu_mail_rewards_from_payload(record.payload))

    def _mail_requested_action_retryable(self, record: FanxiuMailRecord, policy: str) -> bool:
        if policy not in {"claim", "delete"}:
            return False
        evidence = record.evidence if isinstance(record.evidence, dict) else {}
        requested_action = str(evidence.get("runtime_requested_action") or "").strip().lower()
        if requested_action and requested_action != policy:
            return False
        requested_at = str(evidence.get("runtime_action_requested_at") or "").strip()
        try:
            requested_ts = datetime.strptime(requested_at, "%Y-%m-%d %H:%M:%S").timestamp() if requested_at else 0.0
        except ValueError:
            requested_ts = 0.0
        if requested_ts and time.time() - requested_ts < 60.0:
            return False
        server_protocol = "SM_GetMailReward" if policy == "claim" else "SM_DeleteMail"
        for event in evidence.get("mail_actions") or []:
            if isinstance(event, dict) and str(event.get("protocol") or "") == server_protocol:
                return False
        return True

    def _mail_row_has_attachment_hint(self, row: dict[str, Any]) -> bool:
        return bool(row.get("list_has_lock") or row.get("has_attachment_hint"))

    def _select_packet_mail_record_by_fuzzy_title(
        self,
        observed_title: str,
        candidates: list[FanxiuMailRecord],
        *,
        action_policies: set[str] | None = None,
    ) -> FanxiuMailRecord | None:
        observed_key = self._mail_title_similarity_key(observed_title)
        if len(observed_key) < 3:
            return None
        allowed_policies = (set(action_policies or {"claim", "delete"}) & {"claim", "delete"}) or {"claim", "delete"}
        scored: list[tuple[float, FanxiuMailRecord, str]] = []
        for candidate in candidates:
            candidate_title = str(candidate.title or candidate.normalized_title or "")
            score = self._mail_title_similarity(observed_title, candidate_title)
            if score <= 0:
                continue
            scored.append((score, candidate, self._visible_packet_mail_action_policy(candidate)))
        if not scored:
            return None
        scored.sort(key=lambda item: (item[0], float(item[1].last_seen_at or item[1].updated_at or 0)), reverse=True)
        best_score, best_record, best_policy = scored[0]
        threshold = 0.58 if len(observed_key) >= 5 else 0.72
        if best_score < threshold or best_policy not in allowed_policies:
            return None
        close_candidates = [item for item in scored if item[0] >= best_score - 0.06]
        close_policies = {policy for _, _, policy in close_candidates}
        if close_policies != {best_policy}:
            return None
        return best_record

    def _mail_title_similarity_key(self, value: str) -> str:
        text = normalize_fanxiu_mail_title(value)
        return re.sub(r"[^\u4e00-\u9fff0-9A-Za-z]", "", text)

    def _mail_title_similarity(self, left: str, right: str) -> float:
        left_key = self._mail_title_similarity_key(left)
        right_key = self._mail_title_similarity_key(right)
        if not left_key or not right_key:
            return 0.0
        if left_key == right_key:
            return 1.0
        base = difflib.SequenceMatcher(None, left_key, right_key).ratio()
        shorter, longer = sorted((left_key, right_key), key=len)
        if len(shorter) >= 3 and shorter in longer:
            base = max(base, len(shorter) / max(len(longer), 1))
        return float(base)

    def _find_packet_mail_key(self, title: str, time_text: str) -> str:
        record = self._find_packet_mail_record(title, time_text)
        return str(record.mail_key or "") if record else ""

    def _update_packet_mail_action_for_row(self, row: dict[str, Any], *, status: str, evidence: dict[str, Any]) -> None:
        mail_key = str(row.get("mail_key") or "").strip()
        if not mail_key:
            mail_key = self._find_packet_mail_key(
                str(row.get("title") or ""),
                str(row.get("time_text") or ""),
            )
        if not mail_key:
            return
        with Session(engine) as session:
            mark_fanxiu_mail_action(session, mail_key, status=status, evidence=evidence)
            session.commit()

    def _process_mail_row(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image121: dict[str, Any],
        row: dict[str, Any],
    ) -> str:
        title = str(row.get("title") or "")
        policy = str(row.get("policy") or "")
        if policy not in {"claim", "delete"}:
            return "seen"
        with self._lock:
            action_label = "处理"
            self._set_status_locked(
                "running",
                f"邮件_领取检查：打开「{title}」准备{action_label}",
                phase=f"mail_claim_open_{policy}",
                current_scene=121,
            )
            self._log_locked("action", f"邮件_领取检查：打开「{title}」准备{action_label}")
        self._click_frame_point(ctx, image121, float(row.get("x") or 0), float(row.get("y") or 0))
        scene_result = self._wait_mail_detail_or_list_scene(ctx, stop_event, timeout=12.0, label=f"邮件_领取检查：等待「{title}」详情")
        scene_id, _score = yield from scene_result
        if scene_id == 121:
            return "seen"
        if scene_id not in {122, 123}:
            raise RuntimeError(f"邮件_领取检查：打开「{title}」进入未知详情 #{scene_id}，为避免误操作已停止")
        target_scene_id = scene_id
        actual_policy = "claim" if target_scene_id == 122 else "delete"
        if actual_policy != policy:
            with self._lock:
                self._log_locked(
                    "detail",
                    f"邮件_领取检查：「{title}」列表策略={policy}，详情实际为 #{target_scene_id} {actual_policy}，按详情按钮处理",
                )
        detail_image = ctx.get("images", {}).get(target_scene_id)
        action_shape = self._find_shape(detail_image, "领取" if actual_policy == "claim" else "删除") if isinstance(detail_image, dict) else None
        if not isinstance(detail_image, dict) or not action_shape:
            raise RuntimeError(f"缺少 #{target_scene_id}「{'领取' if actual_policy == 'claim' else '删除'}」标注，无法处理邮件")
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_领取检查：{('领取' if actual_policy == 'claim' else '删除')}「{title}」",
                phase=f"mail_claim_do_{actual_policy}",
                current_scene=target_scene_id,
            )
            self._log_locked("action", f"邮件_领取检查：等待并点击 #{target_scene_id}「{'领取' if actual_policy == 'claim' else '删除'}」")
        match_result = yield from self._wait_shape_match(
            ctx,
            stop_event,
            detail_image,
            action_shape,
            timeout=8.0,
            label=f"邮件_领取检查：等待「{title}」{'领取' if actual_policy == 'claim' else '删除'}按钮",
        )
        frame, action_match = match_result
        self._click_shape(ctx, detail_image, action_shape, frame, match_result=action_match)
        yield from self._wait_mail_list_ready(ctx, stop_event, timeout=18.0, label="邮件_领取检查：返回邮件 #121")
        self._update_packet_mail_action_for_row(
            row,
            status=f"{actual_policy}_requested",
            evidence={"runtime_requested_action": actual_policy, "runtime_action_requested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        )
        return "processed"

    def _probe_and_maybe_delete_mail_row(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image121: dict[str, Any],
        row: dict[str, Any],
    ) -> str:
        if self._mail_row_has_attachment_hint(row):
            return "seen"
        title = str(row.get("title") or "")
        if not title:
            return "seen"
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_领取检查：探测「{title}」是否可删除",
                phase="mail_claim_probe_delete",
                current_scene=121,
            )
            self._log_locked("action", f"邮件_领取检查：打开「{title}」探测无附件删除")
        self._click_frame_point(ctx, image121, float(row.get("x") or 0), float(row.get("y") or 0))
        scene_id, score = yield from self._wait_mail_detail_or_list_scene(ctx, stop_event, timeout=12.0, label=f"邮件_领取检查：探测「{title}」详情")
        if scene_id == 121:
            return "seen"
        if scene_id == 122:
            with self._lock:
                self._log_locked("detail", f"邮件_领取检查：探测「{title}」进入 #122，视为有附件/可领取，返回不处理")
            yield from self._return_mail_detail_to_list(ctx, stop_event, 122)
            return "seen"
        if scene_id != 123:
            raise RuntimeError(f"邮件_领取检查：探测「{title}」进入未知详情 #{scene_id}，为避免误操作已停止")
        detail_image = ctx.get("images", {}).get(123)
        action_shape = self._find_shape(detail_image, "删除") if isinstance(detail_image, dict) else None
        if not isinstance(detail_image, dict) or not action_shape:
            raise RuntimeError("缺少 #123「删除」标注，无法执行无附件邮件删除")
        frame = self._screencap(ctx)
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_领取检查：UI确认无附件，删除「{title}」",
                phase="mail_claim_do_delete",
                current_scene=123,
            )
            self._log_locked("action", f"邮件_领取检查：UI确认 #123，点击「删除」：{title}")
        self._click_shape(ctx, detail_image, action_shape, frame)
        yield from self._wait_mail_list_ready(ctx, stop_event, timeout=18.0, label="邮件_领取检查：返回邮件 #121")
        self._update_packet_mail_action_for_row(
            row,
            status="delete_requested",
            evidence={
                "runtime_requested_action": "delete",
                "runtime_action_requested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "runtime_action_source": "ui_delete_probe",
            },
        )
        return "processed"

    def _return_mail_detail_to_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        scene_id: int,
    ):
        detail_image = ctx.get("images", {}).get(scene_id)
        back_shape = self._find_shape(detail_image, "空白-返回") if isinstance(detail_image, dict) else None
        if not isinstance(detail_image, dict) or not back_shape:
            raise RuntimeError(f"缺少 #{scene_id}「空白-返回」标注，无法从邮件详情返回")
        frame = self._screencap(ctx)
        self._click_shape(ctx, detail_image, back_shape, frame)
        yield from self._wait_mail_list_ready(ctx, stop_event, timeout=18.0, label="邮件_领取检查：返回邮件 #121")

    def _wait_mail_detail_or_list_scene(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float,
        label: str,
    ):
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        candidates = [121, 122, 123]
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, score = self._identify_scene_number(ctx, frame, candidates)
            last_scene_id, last_score = scene_id, score
            if scene_id in candidates:
                with self._lock:
                    self._status.update({"current_scene": scene_id, "updated_at": time.time()})
                self._log("success", f"{label}：识别到 #{scene_id} {score:.0f}%")
                return scene_id, score
            with self._lock:
                self._status.update({
                    "phase": "wait_mail_detail",
                    "current_scene": scene_id,
                    "message": f"{label}：当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    "updated_at": time.time(),
                })
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"{label} 超时，未检测到邮件详情，最后 {scene_text} {last_score:.0f}%")

    def _mail_rows_in_shape(self, lines: list[dict[str, Any]], image: dict[str, Any], shape_title: str) -> list[dict[str, Any]]:
        shape = self._find_shape(image, shape_title)
        if not shape:
            return []
        template_shape = self._find_shape(image, "邮件模板")
        template_children = self._flatten_shapes(template_shape.get("children")) if isinstance(template_shape, dict) else []
        title_shape = next((item for item in template_children if str(item.get("title") or "").strip() == "标题"), None)
        time_shape = next((item for item in template_children if str(item.get("title") or "").strip() == "时间"), None)
        if template_shape and title_shape and time_shape:
            rows = self._mail_rows_in_shape_by_template(lines, image, shape, title_shape, time_shape)
            if rows:
                return rows
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        candidates: list[dict[str, Any]] = []
        date_lines: list[dict[str, Any]] = []
        for line in sorted(lines, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0))):
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if not (left <= cx <= right and top <= cy <= bottom):
                continue
            if self._looks_like_mail_time(text):
                date_lines.append({"text": text, "x": cx, "y": cy, "is_read": "已阅" in text or "已读" in text})
                continue
            if not self._looks_like_mail_title(text):
                continue
            title = re.sub(r"[0-9A-Za-z]+$", "", normalize_fanxiu_mail_title(text)).strip()
            if not title or not self._looks_like_mail_title(title):
                continue
            candidates.append({"title": title, "x": cx, "y": cy, "raw_text": text})
        rows: list[dict[str, Any]] = []
        for item in candidates:
            title = str(item.get("title") or "")
            if not title:
                continue
            below_dates = [line for line in date_lines if float(line["y"]) >= float(item["y"]) and float(line["y"]) - float(item["y"]) < 80]
            if below_dates:
                raw_time_text = str(below_dates[0].get("text") or "")
                item["time_text"] = self._normalize_mail_time_text(raw_time_text)
                item["raw_time_text"] = raw_time_text
                item["is_read"] = bool(below_dates[0].get("is_read"))
            rows.append(item)
        return rows

    def _mail_template_child_shape(self, image: dict[str, Any], title: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        template_shape = self._find_shape(image, "邮件模板")
        template_children = self._flatten_shapes(template_shape.get("children")) if isinstance(template_shape, dict) else []
        title_shape = next((item for item in template_children if str(item.get("title") or "").strip() == "标题"), None)
        child_shape = next((item for item in template_children if str(item.get("title") or "").strip() == title), None)
        if not (isinstance(template_shape, dict) and isinstance(title_shape, dict) and isinstance(child_shape, dict)):
            return None
        return template_shape, title_shape, child_shape

    def _mail_row_template_child_shape(self, image: dict[str, Any], row: dict[str, Any], title: str) -> dict[str, Any] | None:
        found = self._mail_template_child_shape(image, title)
        if found is None:
            return None
        _template_shape, title_shape, child_shape = found
        _width, height = self._frame_size(image)
        try:
            row_title_center_y = float(row.get("y") or 0) / max(1, height)
        except (TypeError, ValueError):
            return None
        title_center_y = float(title_shape.get("y") or 0) + float(title_shape.get("h") or 0) / 2
        child_center_y = float(child_shape.get("y") or 0) + float(child_shape.get("h") or 0) / 2
        adjusted = dict(child_shape)
        adjusted["y"] = max(0.0, min(1.0, row_title_center_y + (child_center_y - title_center_y) - float(child_shape.get("h") or 0) / 2))
        adjusted["title"] = str(child_shape.get("title") or title)
        adjusted["imageMatchRole"] = "required"
        adjusted["ocrMatchRole"] = "off"
        return adjusted

    def _annotate_mail_rows_list_state(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str,
        rows: list[dict[str, Any]],
    ) -> None:
        if not rows or "entry" not in ctx:
            return
        for row in rows:
            row.setdefault("list_has_lock", False)
            row.setdefault("has_attachment_hint", False)
            lock_shape = self._mail_row_template_child_shape(image, row, "锁定")
            if lock_shape is None:
                continue
            score = self._shape_score(ctx, image, lock_shape, frame_data_url, match_strategy="anchor_pixel", ocr_fallback=False)
            row["list_lock_score"] = score
            if score >= 75:
                row["list_has_lock"] = True
                row["has_attachment_hint"] = True

    def _mail_rows_in_shape_by_template(
        self,
        lines: list[dict[str, Any]],
        image: dict[str, Any],
        list_shape: dict[str, Any],
        title_shape: dict[str, Any],
        time_shape: dict[str, Any],
    ) -> list[dict[str, Any]]:
        list_box = self._box(list_shape, image)
        title_box = self._box(title_shape, image)
        time_box = self._box(time_shape, image)
        list_left = float(list_box.get("x") or 0)
        list_top = float(list_box.get("y") or 0)
        list_right = list_left + float(list_box.get("w") or 0)
        list_bottom = list_top + float(list_box.get("h") or 0)
        title_left = float(title_box.get("x") or 0)
        title_right = title_left + float(title_box.get("w") or 0)
        title_height = float(title_box.get("h") or 0)
        title_center_y = float(title_box.get("y") or 0) + title_height / 2
        time_center_y = float(time_box.get("y") or 0) + float(time_box.get("h") or 0) / 2
        title_offset_y = title_center_y - time_center_y
        title_y_tolerance = max(18.0, title_height * 1.25)
        normalized_lines: list[dict[str, Any]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if not (list_left <= cx <= list_right and list_top <= cy <= list_bottom):
                continue
            normalized_lines.append({"text": text, "x": cx, "y": cy, "w": w, "h": h})
        time_lines = [
            line for line in normalized_lines
            if self._looks_like_mail_time(str(line.get("text") or ""))
        ]
        rows: list[dict[str, Any]] = []
        for time_line in sorted(time_lines, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0))):
            raw_time_text = str(time_line.get("text") or "")
            time_text = self._normalize_mail_time_text(raw_time_text)
            if not self._is_valid_mail_time_text(time_text):
                continue
            expected_title_y = float(time_line.get("y") or 0) + title_offset_y
            candidates: list[dict[str, Any]] = []
            for line in normalized_lines:
                text = str(line.get("text") or "")
                if text == raw_time_text:
                    continue
                if self._looks_like_mail_time(text):
                    continue
                cx = float(line.get("x") or 0)
                cy = float(line.get("y") or 0)
                if not (title_left <= cx <= title_right):
                    continue
                if abs(cy - expected_title_y) > title_y_tolerance:
                    continue
                if not self._looks_like_mail_title(text):
                    continue
                title = normalize_fanxiu_mail_title(text).strip()
                if not title or not self._looks_like_mail_title(title):
                    continue
                candidates.append({"title": title, "x": cx, "y": cy, "raw_text": text})
            if not candidates:
                continue
            best = max(candidates, key=lambda item: len(str(item.get("title") or "")))
            rows.append({
                **best,
                "time_text": time_text,
                "raw_time_text": raw_time_text,
                "is_read": "已阅" in raw_time_text or "已读" in raw_time_text,
            })
        return rows

    def _normalize_mail_time_text(self, text: str) -> str:
        return normalize_fanxiu_mail_time_text(_sanitize_ocr_text(text))

    def _is_valid_mail_time_text(self, text: str) -> bool:
        return bool(normalize_fanxiu_mail_time_text(text))

    def _looks_like_mail_time(self, text: str) -> bool:
        return bool(re.search(r"\d{4}年|\d{1,2}月\d{1,2}(?:日)?|\d{1,2}:\d{2}", text))

    def _looks_like_mail_title(self, text: str) -> bool:
        normalized = normalize_fanxiu_mail_title(text)
        if len(normalized) < 2:
            return False
        if any(token in normalized for token in ("邮件", "已锁定", "一键删除", "一键领取", "年月日", "已阅", "未阅", "已读")):
            return False
        if self._looks_like_mail_time(normalized):
            return False
        return bool(re.search(r"[\u4e00-\u9fff]", normalized))

    def _mail_rows_signature(self, rows: list[dict[str, Any]]) -> str:
        return "|".join(f"{row.get('title') or ''}@{row.get('time_text') or ''}" for row in rows[:4])

    def _merge_visible_mail_rows_by_position(
        self,
        first_rows: list[dict[str, Any]],
        list_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int]] = set()
        for row in sorted([*first_rows, *list_rows], key=lambda item: float(item.get("y") or 0)):
            title = normalize_fanxiu_mail_title(str(row.get("title") or ""))
            time_text = self._normalize_mail_time_text(str(row.get("time_text") or ""))
            y_bucket = int(round(float(row.get("y") or 0) / 16.0))
            key = (title, time_text, y_bucket)
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
        return merged

    def _execute_daily_signup_task(self, ctx: dict[str, Any], stop_event: threading.Event) -> str:
        frame = self._screencap(ctx)
        scene_id, score = self._identify_scene_number(ctx, frame, [23, 24, 69])
        if scene_id is not None:
            with self._lock:
                self._status.update({"current_scene": scene_id, "updated_at": time.time()})
        if scene_id == 23:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_报名：当前已在报名 #23 {score:.0f}%",
                    phase="daily_signup_resume_signup_scene",
                    current_scene=23,
                )
                self._log_locked("info", f"日常_报名：当前已在报名 #23 {score:.0f}%，直接处理报名列")
            list_result = self._execute_daily_signup_signup_list(ctx, stop_event)
            return (yield from list_result) if isinstance(list_result, GeneratorType) else list_result
        if scene_id == 24:
            image24 = ctx.get("images", {}).get(24)
            claim_shape = self._find_shape(image24, "领取") if isinstance(image24, dict) else None
            if not isinstance(image24, dict) or not claim_shape:
                raise RuntimeError("当前在 #24，但缺少 #24「领取」标注，无法续跑日常报名")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_报名：当前已在领取 #24 {score:.0f}%",
                    phase="daily_signup_resume_claim_scene",
                    current_scene=24,
                )
                self._log_locked("action", "日常_报名：当前已在 #24，点击「领取」后返回报名列")
            self._click_shape(ctx, image24, claim_shape, frame)
            yield from self._wait_scene_id(ctx, stop_event, 23, timeout=12.0, label="日常_报名：返回报名 #23")
            list_result = self._execute_daily_signup_signup_list(ctx, stop_event)
            return (yield from list_result) if isinstance(list_result, GeneratorType) else list_result
        if scene_id == 69:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_报名：当前已在日常 #69 {score:.0f}%",
                    phase="daily_signup_check_signup",
                    current_scene=69,
                )
                self._log_locked("info", f"日常_报名：当前已在日常 #69 {score:.0f}%，直接检查活动报名")
            entry_result = self._execute_daily_signup_activity_entry(ctx, stop_event)
            entry_status = (yield from entry_result) if isinstance(entry_result, GeneratorType) else entry_result
            return entry_status or "success"
        with self._lock:
            self._set_status_locked("running", "日常_报名：确认日常 #69", phase="daily_signup_go_scene")
            self._log_locked("action", "日常_报名：先确认进入日常 #69")
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_报名资产树路径，无法执行场景确认")
        go_scene_result = self._go_scene_task(ctx, asset_tree_path, 69, stop_event)
        result = (yield from go_scene_result) if isinstance(go_scene_result, GeneratorType) else go_scene_result
        if result == "success":
            with self._lock:
                self._set_status_locked("running", "日常_报名：检查活动报名", phase="daily_signup_check_signup")
                self._log_locked("success", "日常_报名：已到达日常 #69")
            entry_result = self._execute_daily_signup_activity_entry(ctx, stop_event)
            entry_status = (yield from entry_result) if isinstance(entry_result, GeneratorType) else entry_result
            if entry_status != "success":
                return entry_status or "success"
        return result

    def _execute_daily_signup_activity_entry(self, ctx: dict[str, Any], stop_event: threading.Event) -> str:
        self._raise_if_stopped(stop_event)
        image = ctx.get("images", {}).get(75)
        if not isinstance(image, dict):
            raise RuntimeError("缺少 #75 活动报名帧标注，无法检查日常报名入口")
        claim_shape = self._find_shape(image, "活动报名-领取")
        click_shape = self._find_shape(image, "活动报名")
        if not claim_shape:
            raise RuntimeError("缺少 #75「活动报名-领取」标注，无法识别领取状态")
        if not click_shape:
            raise RuntimeError("缺少 #75「活动报名」标注，无法点击活动报名入口")
        frame = self._screencap(ctx)
        result = self._run_match(ctx, image, claim_shape, frame, match_strategy="auto", ocr_enabled=True)
        similarity = float(result.get("similarity") or 0)
        matched = bool(result.get("matches")) or similarity >= float(self.scene_threshold)
        if not matched:
            with self._lock:
                self._set_status_locked("running", "日常_报名：今日无需报名", phase="daily_signup_done")
                self._log_locked("success", "日常_报名：未识别到「领」，今日报名已完成")
            return "success"
        with self._lock:
            self._set_status_locked("running", "日常_报名：点击活动报名", phase="daily_signup_click_signup")
            self._log_locked("action", "日常_报名：识别到「领」，点击活动报名")
        self._click_shape(ctx, image, click_shape, frame)
        scene_id, score = yield from self._wait_scene_id(ctx, stop_event, 23, timeout=12.0, label="日常_报名：等待报名 #23")
        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_报名：已进入报名 #23 {score:.0f}%",
                phase="daily_signup_signup_scene_ready",
                current_scene=scene_id,
            )
            self._log_locked("success", f"日常_报名：已确认报名 #23 {score:.0f}%")
        list_result = self._execute_daily_signup_signup_list(ctx, stop_event)
        list_status = (yield from list_result) if isinstance(list_result, GeneratorType) else list_result
        return list_status or "success"

    def _execute_daily_signup_signup_list(self, ctx: dict[str, Any], stop_event: threading.Event) -> str:
        image23 = ctx.get("images", {}).get(23)
        image24 = ctx.get("images", {}).get(24)
        if not isinstance(image23, dict):
            raise RuntimeError("缺少 #23 报名帧标注，无法处理报名列")
        if not isinstance(image24, dict):
            raise RuntimeError("缺少 #24 报名领取帧标注，无法领取报名奖励")
        column_shape = self._find_shape(image23, "报名列")
        claim_shape = self._find_shape(image24, "领取")
        if not column_shape:
            raise RuntimeError("缺少 #23「报名列」标注，无法识别可报名项目")
        if not claim_shape:
            raise RuntimeError("缺少 #24「领取」标注，无法领取报名奖励")

        clicked_count = 0
        empty_scroll_count = 0
        last_empty_scroll_signature = ""
        max_clicks = 30
        max_empty_scrolls = 8
        while clicked_count < max_clicks:
            self._raise_if_stopped(stop_event)
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_报名：扫描报名列，已领取 {clicked_count}",
                    phase="daily_signup_scan_signup_list",
                    current_scene=23,
                )
            frame = self._screencap(ctx)
            lines = self._ocr_lines(frame)
            matches = self._ocr_row_clicks_in_shape(lines, image23, "报名列", include=("报名",), exclude=("已报名",))
            if matches:
                x, y, text = matches[0]
                empty_scroll_count = 0
                last_empty_scroll_signature = ""
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_报名：点击报名 {text}",
                        phase="daily_signup_click_signup_item",
                        current_scene=23,
                    )
                    self._log_locked("action", f"日常_报名：点击报名列「{text}」")
                self._click_frame_point(ctx, image23, x, y)
                yield from self._wait_scene_id(ctx, stop_event, 24, timeout=12.0, label="日常_报名：等待领取 #24")
                claim_frame = self._screencap(ctx)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_报名：点击领取",
                        phase="daily_signup_claim_reward",
                        current_scene=24,
                    )
                    self._log_locked("action", "日常_报名：点击 #24「领取」")
                self._click_shape(ctx, image24, claim_shape, claim_frame)
                yield from self._wait_scene_id(ctx, stop_event, 23, timeout=12.0, label="日常_报名：返回报名 #23")
                clicked_count += 1
                continue

            signature = self._daily_signup_first_row_signature(lines, image23, ctx=ctx)
            if signature and signature == last_empty_scroll_signature:
                with self._lock:
                    self._log_locked("info", "日常_报名：报名列滚动后签名未变化，判定已滚到底")
                break
            if empty_scroll_count >= max_empty_scrolls:
                with self._lock:
                    self._log_locked("info", f"日常_报名：报名列空滚动达到兜底上限 {max_empty_scrolls}，停止扫描")
                break
            last_empty_scroll_signature = signature
            empty_scroll_count += 1
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_报名：报名列向下滚动 {empty_scroll_count}",
                    phase="daily_signup_scroll_signup_list",
                    current_scene=23,
                )
                self._log_locked("action", f"日常_报名：报名列未发现「报名」，向下滚动 {empty_scroll_count}")
            self._scroll_shape_content(ctx, image23, column_shape)
            yield from self._wait_scroll_settle(ctx, stop_event)

        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_报名：报名处理完成，领取 {clicked_count} 个",
                phase="daily_signup_done",
                current_scene=23,
            )
            self._log_locked("success", f"日常_报名：报名处理完成，领取 {clicked_count} 个")
        return "success"

    def _scroll_shape_content(self, ctx: dict[str, Any], image: dict[str, Any], shape: dict[str, Any]) -> None:
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        width = float(box.get("w") or 0)
        height = float(box.get("h") or 0)
        x = left + width / 2
        direction = str(shape.get("contentDirection") or "down").strip().lower()
        if direction == "up":
            start_y = top + height * 0.25
            end_y = top + height * 0.75
        else:
            start_y = top + height * 0.75
            end_y = top + height * 0.25
        self._drag_frame_point(ctx, image, x, start_y, x, end_y, duration_ms=1000)

    def _wait_scroll_settle(self, ctx: dict[str, Any], stop_event: threading.Event, seconds: float = 1.0):
        self._clear_tick_frame(ctx)
        if stop_event.wait(max(0.0, float(seconds))):
            self._raise_if_stopped(stop_event)
        self._clear_tick_frame(ctx)
        yield BehaviorTreeStatus.RUNNING

    def _ocr_row_clicks_in_shape(
        self,
        lines: list[dict[str, Any]],
        image: dict[str, Any] | None,
        shape_title: str,
        *,
        include: tuple[str, ...],
        exclude: tuple[str, ...] = (),
    ) -> list[tuple[float, float, str]]:
        shape = self._find_shape(image, shape_title) if image else None
        if not shape or not image:
            return []
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        width = float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        click_x = left + width / 2
        matches: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            if include and not all(fragment in text for fragment in include):
                continue
            if exclude and any(fragment in text for fragment in exclude):
                continue
            y = float(line.get("y") or 0)
            h = float(line.get("h") or 0)
            cy = y + h / 2
            if top <= cy <= bottom:
                matches.append((click_x, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _daily_signup_first_row_signature(self, lines: list[dict[str, Any]], image23: dict[str, Any], *, ctx: dict[str, Any] | None = None) -> str:
        exclude_boxes = self._occlusion_marker_boxes(ctx, image23) if ctx else []
        marker_image = self._find_child_image_by_number(image23, 25) or image23
        signature = self._text_signature_in_shape(lines, marker_image, "第1行", "shape 1", exclude_boxes=exclude_boxes)
        if signature:
            return signature
        return self._vertical_text_signature_in_shape(lines, image23, "报名列", exclude_boxes=exclude_boxes)

    def _text_signature_in_shape(
        self,
        lines: list[dict[str, Any]],
        image: dict[str, Any] | None,
        *shape_titles: str,
        exclude_boxes: list[dict[str, float]] | None = None,
    ) -> str:
        shape = self._find_shape(image, *shape_titles) if image else None
        if not shape or not image:
            return ""
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        fragments: list[str] = []
        for line in sorted(lines, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0))):
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if self._point_in_boxes(cx, cy, exclude_boxes):
                continue
            if left <= cx <= right and top <= cy <= bottom:
                fragments.append(text)
        return "|".join(fragments)

    def _vertical_text_signature_in_shape(
        self,
        lines: list[dict[str, Any]],
        image: dict[str, Any] | None,
        shape_title: str,
        *,
        exclude_boxes: list[dict[str, float]] | None = None,
    ) -> str:
        shape = self._find_shape(image, shape_title) if image else None
        if not shape or not image:
            return ""
        box = self._box(shape, image)
        top = float(box.get("y") or 0)
        bottom = top + float(box.get("h") or 0)
        fragments: list[str] = []
        for line in sorted(lines, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0))):
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            y = float(line.get("y") or 0)
            h = float(line.get("h") or 0)
            cy = y + h / 2
            x = float(line.get("x") or 0)
            w = float(line.get("w") or 0)
            if self._point_in_boxes(x + w / 2, cy, exclude_boxes):
                continue
            if top <= cy <= bottom:
                fragments.append(text)
        return "|".join(fragments)

    def _occlusion_marker_boxes(self, ctx: dict[str, Any] | None, image: dict[str, Any]) -> list[dict[str, float]]:
        if not ctx:
            return []
        tree = ctx.get("asset_tree")
        if not isinstance(tree, list):
            return []
        boxes: list[dict[str, float]] = []

        def visit(nodes: list[dict[str, Any]], in_occlusion_folder: bool = False) -> None:
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_type = str(node.get("type") or "").strip()
                title = str(node.get("title") or "").strip()
                current_in_occlusion = in_occlusion_folder or (node_type == "folder" and title == "遮挡标记")
                if current_in_occlusion and node_type == "image":
                    for shape in self._flatten_shapes(node.get("shapes")):
                        if shape.get("kind") == "group":
                            continue
                        box = self._box(shape, image)
                        boxes.append({
                            "x": float(box.get("x") or 0),
                            "y": float(box.get("y") or 0),
                            "w": float(box.get("w") or 0),
                            "h": float(box.get("h") or 0),
                        })
                children = node.get("children")
                if isinstance(children, list):
                    visit([child for child in children if isinstance(child, dict)], current_in_occlusion)

        visit(tree)
        return boxes

    def _point_in_boxes(self, x: float, y: float, boxes: list[dict[str, float]] | None) -> bool:
        if not boxes:
            return False
        for box in boxes:
            left = float(box.get("x") or 0)
            top = float(box.get("y") or 0)
            right = left + float(box.get("w") or 0)
            bottom = top + float(box.get("h") or 0)
            if left <= x <= right and top <= y <= bottom:
                return True
        return False

    def _execute_gift_code_task(self, ctx: dict[str, Any], codes: list[str], stop_event: threading.Event) -> None:
        with self._lock:
            self._set_status_locked("running", "对齐 #49 设置页", phase="align_settings")
        self._align_settings(ctx, stop_event)
        for index, code in enumerate(codes):
            self._raise_if_stopped(stop_event)
            with self._lock:
                self._set_status_locked("running", f"处理第 {index + 1}/{len(codes)} 个：{code}", current_index=index, current_code=code, phase="process_code")
                self._log_locked("action", f"开始兑换：{code}")
            self._process_code(ctx, code, index == len(codes) - 1, stop_event)
        with self._lock:
            self._set_status_locked("running", "从 #49 回退", phase="finish_back")
        self._finish_from_settings(ctx, stop_event)

    def _raise_if_stopped(self, stop_event: threading.Event) -> None:
        if stop_event.is_set():
            raise InterruptedError()

    def _load_asset_tree(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            raise RuntimeError("未找到帧树，请先保存帧树标注")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise RuntimeError("帧树格式错误")
        return [item for item in payload if isinstance(item, dict)]

    def _image_number(self, image: dict[str, Any]) -> int | None:
        title = str(image.get("title") or "")
        filename = str(image.get("filename") or "")
        id_text = str(image.get("id") or "")
        for text in (filename, title, id_text):
            match = re.search(r"(\d+)", text)
            if match:
                return int(match.group(1))
        return None

    def _find_child_image_by_number(self, image: dict[str, Any], number: int) -> dict[str, Any] | None:
        def visit(items: list[dict[str, Any]]) -> dict[str, Any] | None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "image" and self._image_number(item) == number:
                    return item
                children = item.get("children")
                if isinstance(children, list):
                    found = visit([child for child in children if isinstance(child, dict)])
                    if found is not None:
                        return found
            return None

        children = image.get("children")
        if not isinstance(children, list):
            return None
        return visit([child for child in children if isinstance(child, dict)])

    def _index_images(self, nodes: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        def visit(items: list[dict[str, Any]]) -> None:
            for item in items:
                if item.get("type") == "image":
                    number = self._image_number(item)
                    if number is not None:
                        result[number] = item
                children = item.get("children")
                if isinstance(children, list):
                    visit([child for child in children if isinstance(child, dict)])
        visit(nodes)
        return result

    def _jump_target_text(self, shape: dict[str, Any]) -> str:
        return str(shape.get("sceneJumpTarget") or "").strip()

    def _parse_scene_jump_entries(self, value: Any) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for raw_token in str(value or "").split(","):
            token = raw_token.strip()
            if not token:
                continue
            count = 0
            match = re.match(r"^(.*?)\((\d+)\)$", token)
            if match:
                token = match.group(1).strip()
                count = int(match.group(2))
            if token:
                entries.append({"label": token, "count": count})
        return entries

    def _serialize_scene_jump_entries(self, entries: list[dict[str, Any]]) -> str:
        normalized: list[dict[str, Any]] = []
        for entry in entries:
            label = str(entry.get("label") or "").strip()
            if not label:
                continue
            count = max(0, int(entry.get("count") or 0))
            normalized.append({"label": label, "count": count})
        normalized.sort(key=lambda item: int(item.get("count") or 0), reverse=True)
        return ",".join(
            f"{item['label']}({item['count']})" if int(item.get("count") or 0) > 0 else item["label"]
            for item in normalized
        )

    def _increment_scene_jump_target(self, shape: dict[str, Any], target_scene_id: int) -> bool:
        current_text = self._jump_target_text(shape)
        if current_text in {"-1", "0"}:
            return False
        entries = self._parse_scene_jump_entries(current_text)
        for entry in entries:
            if self._scene_jump_label_number(entry.get("label")) == target_scene_id:
                entry["count"] = int(entry.get("count") or 0) + 1
                shape["sceneJumpTarget"] = self._serialize_scene_jump_entries(entries)
                return True
        return False

    def _scene_jump_label_number(self, label: Any) -> int | None:
        text = str(label or "").strip()
        if text.startswith("#"):
            text = text[1:].strip()
        return int(text) if text.isdecimal() else None

    def _collect_folder_image_numbers(self, node: dict[str, Any]) -> list[int]:
        result: list[int] = []
        children = node.get("children")
        if not isinstance(children, list):
            return result
        for child in children:
            if not isinstance(child, dict):
                continue
            if child.get("type") == "image":
                number = self._image_number(child)
                if number is not None:
                    result.append(number)
            result.extend(self._collect_folder_image_numbers(child))
        return result

    def _resolve_scene_jump_label(self, tree: list[dict[str, Any]], label: Any) -> list[int]:
        number = self._scene_jump_label_number(label)
        if number is not None:
            return [number]
        target = str(label or "").strip()
        if not target:
            return []
        found: list[int] = []

        def visit(items: list[dict[str, Any]]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("title") or "").strip() == target:
                    if item.get("type") == "image":
                        number = self._image_number(item)
                        if number is not None:
                            found.append(number)
                    elif item.get("type") == "folder":
                        found.extend(self._collect_folder_image_numbers(item))
                children = item.get("children")
                if isinstance(children, list):
                    visit([child for child in children if isinstance(child, dict)])

        visit(tree)
        return found

    def _scene_jump_target_ids(self, tree: list[dict[str, Any]], shape: dict[str, Any]) -> list[int]:
        result: list[int] = []
        for entry in self._parse_scene_jump_entries(self._jump_target_text(shape)):
            for scene_id in self._resolve_scene_jump_label(tree, entry.get("label")):
                if scene_id not in result:
                    result.append(scene_id)
        return result

    def _resolve_scene_image_title_ids(self, tree: list[dict[str, Any]], title: str) -> list[int]:
        target = title.strip()
        if not target:
            return []
        found: list[int] = []

        def visit(items: list[dict[str, Any]]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "image" and str(item.get("title") or "").strip() == target:
                    number = self._image_number(item)
                    if number is not None and number not in found:
                        found.append(number)
                children = item.get("children")
                if isinstance(children, list):
                    visit([child for child in children if isinstance(child, dict)])

        visit(tree)
        return found

    def _implicit_parent_return_target_ids(
        self,
        tree: list[dict[str, Any]],
        shape: dict[str, Any],
        parent_image: dict[str, Any] | None,
        parent_folder_title: str = "",
    ) -> list[int]:
        if self._jump_target_text(shape):
            return []
        title = str(shape.get("title") or "").strip()
        if title not in {"离开", "返回", "关闭", "关闭下方菜单"}:
            return []
        if parent_image:
            parent_id = self._image_number(parent_image)
            if parent_id is not None:
                return [parent_id]
        return self._resolve_scene_image_title_ids(tree, parent_folder_title)

    def _scene_id_key(self, scene_id: int) -> str:
        for key, value in self.scene_ids.items():
            if int(value) == int(scene_id):
                return key
        return str(scene_id)

    def _scene_match_threshold(self, scene_id: int) -> float:
        key = self._scene_id_key(scene_id)
        return float(self.scene_thresholds.get(key, self.scene_threshold))

    def _scene_matches_id(self, scene_id: int, score: float) -> bool:
        return score >= self._scene_match_threshold(scene_id)

    def _identify_scene_number(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
    ) -> tuple[int | None, float]:
        images: dict[int, dict[str, Any]] = ctx.get("images") or {}
        candidates: list[tuple[int, float]] = []
        if preferred_scene_ids:
            for scene_id in preferred_scene_ids:
                image = images.get(scene_id)
                if not image:
                    continue
                score = self._scene_score(ctx, image, frame_data_url)
                candidates.append((scene_id, score))
            candidates.sort(key=lambda item: (item[1], -item[0]), reverse=True)
            if not candidates:
                return None, 0.0
            scene_id, score = candidates[0]
            return (scene_id, score) if self._scene_matches_id(scene_id, score) else (None, score)

        for scene_id, image in images.items():
            score = self._scene_score(ctx, image, frame_data_url)
            candidates.append((scene_id, score))
        candidates.sort(key=lambda item: (item[1], -item[0]), reverse=True)
        if not candidates:
            return None, 0.0
        scene_id, score = candidates[0]
        return (scene_id, score) if self._scene_matches_id(scene_id, score) else (None, score)

    def _scene_jump_edges(self, tree: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
        edges: dict[int, list[dict[str, Any]]] = {}

        def visit(
            items: list[dict[str, Any]],
            parent_image: dict[str, Any] | None = None,
            parent_folder_title: str = "",
        ) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                current_parent_image = parent_image
                current_parent_folder_title = parent_folder_title
                if item.get("type") == "folder":
                    current_parent_folder_title = str(item.get("title") or "").strip() or parent_folder_title
                if item.get("type") == "image":
                    source_id = self._image_number(item)
                    if source_id is not None:
                        for shape in self._flatten_shapes(item.get("shapes")):
                            if shape.get("kind") == "group":
                                continue
                            target_text = self._jump_target_text(shape)
                            if target_text in {"-1", "0"}:
                                continue
                            target_ids = (
                                self._scene_jump_target_ids(tree, shape)
                                if target_text
                                else self._implicit_parent_return_target_ids(
                                    tree,
                                    shape,
                                    parent_image,
                                    parent_folder_title,
                                )
                            )
                            if target_ids:
                                edges.setdefault(source_id, []).append({
                                    "source_id": source_id,
                                    "image": item,
                                    "shape": shape,
                                    "target_ids": target_ids,
                                })
                    current_parent_image = item
                children = item.get("children")
                if isinstance(children, list):
                    visit(
                        [child for child in children if isinstance(child, dict)],
                        current_parent_image,
                        current_parent_folder_title,
                    )

        visit(tree)
        return edges

    def _find_scene_route(self, tree: list[dict[str, Any]], start_scene_id: int, target_scene_id: int) -> list[dict[str, Any]] | None:
        if start_scene_id == target_scene_id:
            return []
        edges = self._scene_jump_edges(tree)
        queue: list[tuple[int, list[dict[str, Any]]]] = [(start_scene_id, [])]
        visited = {start_scene_id}
        while queue:
            scene_id, route = queue.pop(0)
            for edge in edges.get(scene_id, []):
                for next_scene_id in edge["target_ids"]:
                    if next_scene_id in visited:
                        continue
                    next_route = [*route, edge]
                    if next_scene_id == target_scene_id:
                        return next_route
                    visited.add(next_scene_id)
                    queue.append((next_scene_id, next_route))
        return None

    def _scene_jump_confirmation_scene_ids(self, tree: list[dict[str, Any]]) -> list[int]:
        result: list[int] = []
        source_shape = {"title": "离开"}

        def visit(items: list[dict[str, Any]]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "image" and self._scene_jump_intermediate_confirm_shape(item, source_shape):
                    scene_id = self._image_number(item)
                    if scene_id is not None and scene_id not in result:
                        result.append(scene_id)
                children = item.get("children")
                if isinstance(children, list):
                    visit([child for child in children if isinstance(child, dict)])

        visit(tree)
        return result

    def _scene_route_candidate_ids(self, tree: list[dict[str, Any]], target_scene_id: int) -> list[int]:
        edges = self._scene_jump_edges(tree)
        result: list[int] = [target_scene_id]
        for scene_id in self._scene_jump_confirmation_scene_ids(tree):
            if scene_id not in result:
                result.append(scene_id)
        for source_scene_id in sorted(edges):
            if source_scene_id == target_scene_id:
                continue
            if self._find_scene_route(tree, source_scene_id, target_scene_id) is not None:
                result.append(source_scene_id)
        return result

    def _write_asset_tree(self, asset_tree_path: Path, tree: list[dict[str, Any]]) -> None:
        _write_data_annotation_json(asset_tree_path, tree)
        self._auto_close_candidates_cache.pop(str(asset_tree_path), None)

    def _save_unknown_scene_frame(
        self,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        tree: list[dict[str, Any]],
        frame_data_url: str,
        *,
        target_scene_id: int,
        current_scene_id: int | None,
        action_shape: dict[str, Any] | None,
        elapsed_seconds: float,
        history: list[str],
    ) -> dict[str, Any]:
        action_title = action_shape.get("title") if isinstance(action_shape, dict) else "unknown"
        current_text = f"#{current_scene_id}" if current_scene_id is not None else "unknown"
        detail = "；".join([
            f"目标场景=#{target_scene_id}",
            f"当前/点击前场景={current_text}",
            f"动作 shape={action_title}",
            f"累计等待={elapsed_seconds:.1f}s",
            f"最近识别={history[-1] if history else '无'}",
        ])
        self._log("error", f"场景跳转缺少可靠标注，已中断：{detail}")
        raise RuntimeError(f"场景跳转缺少可靠标注，已中断，请人工补标/修标后重试：{detail}")

    def _is_independent_exit_shape(self, shape: dict[str, Any]) -> bool:
        return self._jump_target_text(shape) == "-1"

    def _auto_close_guard_action_shape(self, image: dict[str, Any]) -> dict[str, Any] | None:
        shapes = [shape for shape in self._flatten_shapes(image.get("shapes")) if shape.get("kind") != "group"]
        # Fanxiu annotation convention: in the top-level popup group, "空白" means
        # the background/overlay area that closes the popup when tapped. Prefer it
        # over tiny close buttons and "确定", which may trigger extra scene changes.
        for shape in shapes:
            if str(shape.get("title") or "").strip() == "空白":
                return shape
        for title in ("关闭",):
            for shape in shapes:
                if str(shape.get("title") or "").strip() == title:
                    return shape
        for shape in shapes:
            if self._is_independent_exit_shape(shape):
                return shape
        for shape in shapes:
            if str(shape.get("title") or "").strip() == "确定":
                return shape
        return None

    def _index_guard_candidates(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen_image_ids: set[int] = set()

        def add_candidate(image: dict[str, Any], folder_path: str, action_shape: dict[str, Any] | None) -> None:
            identity = id(image)
            if identity in seen_image_ids:
                return
            seen_image_ids.add(identity)
            candidates.append({
                "image": image,
                "folder_path": folder_path,
                "action_shape": action_shape,
            })

        def add_first_level_popup_images(folder: dict[str, Any]) -> None:
            children = folder.get("children")
            if not isinstance(children, list):
                return
            folder_title = str(folder.get("title") or "").strip()
            for child in children:
                if not isinstance(child, dict) or child.get("type") != "image":
                    continue
                add_candidate(child, folder_title, self._auto_close_guard_action_shape(child))

        def visit(items: list[dict[str, Any]], path: list[str]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                node_type = str(item.get("type") or "")
                title = str(item.get("title") or "").strip()
                current_path = [*path, title] if title else path
                if node_type == "folder" and title == "弹窗":
                    add_first_level_popup_images(item)
                    continue
                if node_type == "image":
                    action_shape = self._auto_close_guard_action_shape(item)
                    if action_shape is not None and self._is_independent_exit_shape(action_shape):
                        add_candidate(item, "/".join(path), action_shape)
                children = item.get("children")
                if isinstance(children, list):
                    visit([child for child in children if isinstance(child, dict)], current_path)

        visit(nodes, [])
        return candidates

    def _handle_auto_close_popup_47_child_84(
        self,
        ctx: dict[str, Any],
        popup_47: dict[str, Any],
        frame_data_url: str,
        popup_score: float,
        event: dict[str, Any],
    ) -> bool:
        child_84 = self._find_child_image_by_number(popup_47, 84)
        if not child_84:
            return False
        child_score = self._popup_score(ctx, child_84, frame_data_url)
        if child_score < self.overlay_threshold:
            return False
        no_more_prompt_shape = self._find_shape(child_84, "不再提示")
        confirm_shape = self._find_shape(child_84, "确认")
        if not confirm_shape:
            with self._lock:
                self._status.update({
                    "current_scene": 84,
                    "message": f"守护命中：#84 {child_score:.0f}%，缺少「确认」标注",
                    "last_guard_event": {**event, "image": "#84", "title": str(child_84.get("title") or ""), "score": round(child_score, 1), "action": "missing_confirm"},
                    "updated_at": time.time(),
                })
                self._log_locked("error", self._status["message"])
            return True
        if no_more_prompt_shape:
            no_more_prompt_score = self._shape_score(ctx, child_84, no_more_prompt_shape, frame_data_url)
            if no_more_prompt_score < self.overlay_threshold:
                self._click_shape(ctx, child_84, no_more_prompt_shape, frame_data_url)
                with self._lock:
                    self._status.update({
                        "current_scene": 84,
                        "message": f"守护处理：#47/#84 点击「不再提示」 {popup_score:.0f}%/{child_score:.0f}%",
                        "last_guard_event": {
                            **event,
                            "image": "#84",
                            "title": str(child_84.get("title") or ""),
                            "score": round(child_score, 1),
                            "parent_score": round(popup_score, 1),
                            "action": "click:不再提示",
                        },
                        "updated_at": time.time(),
                    })
                    self._log_locked("guardClick", self._status["message"])
                return True
        self._click_shape(ctx, child_84, confirm_shape)
        with self._lock:
            self._status.update({
                "current_scene": 84,
                "message": f"守护处理：#47/#84 点击「确认」 {popup_score:.0f}%/{child_score:.0f}%",
                "last_guard_event": {
                    **event,
                    "image": "#84",
                    "title": str(child_84.get("title") or ""),
                    "score": round(child_score, 1),
                    "parent_score": round(popup_score, 1),
                    "action": "click:确认",
                },
                "updated_at": time.time(),
            })
            self._log_locked("guardClick", self._status["message"])
        return True

    def _handle_auto_close_popup_47_child_86(
        self,
        ctx: dict[str, Any],
        popup_47: dict[str, Any],
        frame_data_url: str,
        popup_score: float,
        event: dict[str, Any],
    ) -> bool:
        child_86 = self._find_child_image_by_number(popup_47, 86)
        if not child_86:
            return False
        child_score = self._popup_score(ctx, child_86, frame_data_url)
        if child_score < self.overlay_threshold:
            return False
        confirm_shape = self._find_shape(child_86, "确认")
        if not confirm_shape:
            with self._lock:
                self._status.update({
                    "current_scene": 86,
                    "message": f"守护命中：#86 {child_score:.0f}%，缺少「确认」标注",
                    "last_guard_event": {**event, "image": "#86", "title": str(child_86.get("title") or ""), "score": round(child_score, 1), "action": "missing_confirm"},
                    "updated_at": time.time(),
                })
                self._log_locked("error", self._status["message"])
            return True
        self._click_shape(ctx, child_86, confirm_shape, frame_data_url)
        with self._lock:
            self._status.update({
                "current_scene": 86,
                "message": f"守护处理：#47/#86 点击「确认」 {popup_score:.0f}%/{child_score:.0f}%",
                "last_guard_event": {
                    **event,
                    "image": "#86",
                    "title": str(child_86.get("title") or ""),
                    "score": round(child_score, 1),
                    "parent_score": round(popup_score, 1),
                    "action": "click:确认",
                },
                "updated_at": time.time(),
            })
            self._log_locked("guardClick", self._status["message"])
        return True

    def _auto_close_guard_images(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._index_guard_candidates(nodes)

    def _auto_close_guard_candidates_for_path(self, asset_tree_path: Path) -> list[dict[str, Any]]:
        try:
            stat = asset_tree_path.stat()
            signature = (int(stat.st_mtime_ns), int(stat.st_size))
        except OSError:
            signature = (0, 0)
        cache_key = str(asset_tree_path)
        cached = self._auto_close_candidates_cache.get(cache_key)
        if cached and cached[0] == signature[0] and cached[1] == signature[1]:
            return cached[2]
        tree = self._load_asset_tree(asset_tree_path)
        candidates = self._auto_close_guard_images(tree)
        self._auto_close_candidates_cache[cache_key] = (signature[0], signature[1], candidates)
        return candidates

    def _auto_close_popup_candidate_score(self, ctx: dict[str, Any], candidate: dict[str, Any], frame_data_url: str) -> float:
        image = candidate.get("image")
        if not isinstance(image, dict):
            return 0.0
        return self._popup_score(ctx, image, frame_data_url)

    def _auto_close_popup_candidate_scores_serial(
        self,
        ctx: dict[str, Any],
        candidates: list[dict[str, Any]],
        frame_data_url: str,
    ) -> list[float]:
        return [self._auto_close_popup_candidate_score(ctx, candidate, frame_data_url) for candidate in candidates]

    def _auto_close_popup_candidate_scores_parallel(
        self,
        ctx: dict[str, Any],
        candidates: list[dict[str, Any]],
        frame_data_url: str,
    ) -> list[float]:
        if len(candidates) <= 1:
            return self._auto_close_popup_candidate_scores_serial(ctx, candidates, frame_data_url)
        workers = len(candidates)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fanxiu-popup-match") as executor:
            return list(executor.map(lambda candidate: self._auto_close_popup_candidate_score(ctx, candidate, frame_data_url), candidates))

    def _auto_close_popup_candidate_scores(
        self,
        ctx: dict[str, Any],
        candidates: list[dict[str, Any]],
        frame_data_url: str,
    ) -> list[float]:
        return self._auto_close_popup_candidate_scores_parallel(ctx, candidates, frame_data_url)

    def _auto_close_popup_first_match(
        self,
        ctx: dict[str, Any],
        candidates: list[dict[str, Any]],
        frame_data_url: str,
    ) -> tuple[dict[str, Any] | None, float]:
        if not candidates:
            return None, 0.0
        scores = self._auto_close_popup_candidate_scores_parallel(ctx, candidates, frame_data_url)
        for candidate, score in zip(candidates, scores):
            if score >= self.overlay_threshold:
                return candidate, score
        return None, 0.0

    def _auto_close_popup_guard_step(self, ctx: dict[str, Any], asset_tree_path: Path, frame_data_url: str) -> bool:
        candidates = self._auto_close_guard_candidates_for_path(asset_tree_path)
        candidate, score = self._auto_close_popup_first_match(ctx, candidates, frame_data_url)
        if candidate is not None:
            image = candidate.get("image")
            if not isinstance(image, dict):
                return False
            image_number = self._image_number(image)
            image_label = f"#{image_number}" if image_number is not None else str(image.get("title") or image.get("filename") or "unknown")
            folder_path = str(candidate.get("folder_path") or "")
            action_shape = candidate.get("action_shape")
            event = {
                "time": time.time(),
                "kind": "popup",
                "image": image_label,
                "title": str(image.get("title") or ""),
                "folder_path": folder_path,
                "score": round(score, 1),
                "action": "",
            }
            if image_number == 47 and self._handle_auto_close_popup_47_child_84(ctx, image, frame_data_url, score, event):
                return True
            if image_number == 47 and self._handle_auto_close_popup_47_child_86(ctx, image, frame_data_url, score, event):
                return True
            if not isinstance(action_shape, dict):
                with self._lock:
                    self._status.update({
                        "current_scene": image_number,
                        "message": f"守护命中：{image_label} {score:.0f}%，缺少关闭标注",
                        "last_guard_event": {**event, "action": "missing_action"},
                        "updated_at": time.time(),
                    })
                    self._log_locked("error", self._status["message"])
                return False
            action_title = str(action_shape.get("title") or "shape")
            self._click_shape(ctx, image, action_shape, frame_data_url)
            with self._lock:
                self._status.update({
                    "current_scene": image_number,
                    "message": f"守护处理：{image_label} 点击「{action_title}」 {score:.0f}%",
                    "last_guard_event": {**event, "action": f"click:{action_title}"},
                    "updated_at": time.time(),
                })
                self._log_locked("guardClick", self._status["message"])
            return True
        return False

    def _require_assets(self, ctx: dict[str, Any]) -> None:
        images: dict[int, dict[str, Any]] = ctx["images"]
        if not images:
            raise RuntimeError("缺少帧标注，请先保存帧树")

    def _image(self, ctx: dict[str, Any], key: str) -> dict[str, Any] | None:
        return ctx["images"].get(self.scene_ids[key])

    def _flatten_shapes(self, shapes: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if not isinstance(shapes, list):
            return result
        for item in shapes:
            if not isinstance(item, dict):
                continue
            result.append(item)
            result.extend(self._flatten_shapes(item.get("children")))
        return result

    def _find_shape(self, image: dict[str, Any] | None, *titles: str, contains: bool = False) -> dict[str, Any] | None:
        if not image:
            return None
        for shape in self._flatten_shapes(image.get("shapes")):
            title = str(shape.get("title") or "").strip()
            if not title:
                continue
            for target in titles:
                if (contains and target in title) or (not contains and title == target):
                    return shape
        return None

    def _scene_identity_shapes(self, image: dict[str, Any]) -> list[dict[str, Any]]:
        shapes = [shape for shape in self._flatten_shapes(image.get("shapes")) if shape.get("kind") != "group"]
        return [shape for shape in shapes if bool(shape.get("isSceneIdentity"))]

    def _popup_match_shapes(self, image: dict[str, Any]) -> list[dict[str, Any]]:
        shapes = [shape for shape in self._flatten_shapes(image.get("shapes")) if shape.get("kind") != "group"]
        identity = [shape for shape in shapes if bool(shape.get("isSceneIdentity"))]
        return identity or shapes[:4]

    def _frame_size(self, image: dict[str, Any]) -> tuple[int, int]:
        return max(1, int(image.get("width") or 900)), max(1, int(image.get("height") or 1600))

    def _box(self, shape: dict[str, Any], image: dict[str, Any]) -> dict[str, Any]:
        width, height = self._frame_size(image)
        return {
            "name": str(shape.get("title") or ""),
            "x": float(shape.get("x") or 0) * width,
            "y": float(shape.get("y") or 0) * height,
            "w": max(1, float(shape.get("w") or 0) * width),
            "h": max(1, float(shape.get("h") or 0) * height),
        }

    def _data_url(self, data: bytes) -> str:
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")

    def _set_tick_frame(self, ctx: dict[str, Any], frame_data_url: str | None) -> None:
        if frame_data_url:
            ctx["_tick_frame_data_url"] = frame_data_url

    def _clear_tick_frame(self, ctx: dict[str, Any]) -> None:
        ctx.pop("_tick_frame_data_url", None)

    def _capture_frame(self, ctx: dict[str, Any]) -> str:
        entry: UserDevice = ctx["entry"]
        response = _screencap_game_window2_service() if entry.mode == "local" else _remote_game_window2_screencap(entry)
        return self._data_url(bytes(response.body or b""))

    def _screencap(self, ctx: dict[str, Any]) -> str:
        frame_data_url = ctx.get("_tick_frame_data_url")
        if isinstance(frame_data_url, str) and frame_data_url:
            return frame_data_url
        frame_data_url = self._capture_frame(ctx)
        self._set_tick_frame(ctx, frame_data_url)
        return frame_data_url

    def _run_match(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
        *,
        scan: bool = False,
        match_strategy: str = "auto",
        ocr_enabled: bool = False,
    ) -> dict[str, Any]:
        payload = self._build_shape_match_payload(
            image,
            shape,
            frame_data_url,
            scan=scan,
            match_strategy=match_strategy,
            ocr_enabled=ocr_enabled,
        )
        entry: UserDevice = ctx["entry"]
        return _match_game_window2_service(payload) if entry.mode == "local" else _match_remote_game_window2(entry, payload)

    def _build_shape_match_payload(
        self,
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
        *,
        scan: bool,
        match_strategy: str,
        ocr_enabled: bool,
        save_match_frame: bool | None = None,
    ) -> dict[str, Any]:
        filename = str(image.get("filename") or "")
        if not filename:
            raise RuntimeError(f"帧「{image.get('title') or image.get('id')}」缺少图片文件")
        ocr_text = str(shape.get("ocrText") or "").strip()
        use_ocr = bool(ocr_enabled and ocr_text)
        if save_match_frame is None:
            save_match_frame = not use_ocr
        payload = {
            "filename": filename,
            "box": self._box(shape, image),
            "scan": scan,
            "pixel_tolerance": int(shape.get("pixelTolerance") if shape.get("pixelTolerance") is not None else 5),
            "alpha_mask_data_url": ((shape.get("alphaMask") or {}).get("dataUrl") if isinstance(shape.get("alphaMask"), dict) else None),
            "tolerance_min_data_url": ((shape.get("toleranceRange") or {}).get("minDataUrl") if isinstance(shape.get("toleranceRange"), dict) else None),
            "tolerance_max_data_url": ((shape.get("toleranceRange") or {}).get("maxDataUrl") if isinstance(shape.get("toleranceRange"), dict) else None),
            "current_frame_data_url": frame_data_url,
            "prefer_cached": False,
            "match_strategy": match_strategy,
            "match_search_radius": int(shape.get("jitterRadius") or 0) if bool(shape.get("jitterEnabled")) and match_strategy == "auto" and not scan else None,
            "ocr_enabled": use_ocr,
            "ocr_text": ocr_text if use_ocr else "",
            "ocr_match_mode": shape.get("ocrMatchMode") or "contains",
            "read_only_cache": use_ocr,
            "save_match_frame": bool(save_match_frame),
        }
        return payload

    def _shape_ocr_fallback_enabled(self, shape: dict[str, Any]) -> bool:
        if not str(shape.get("ocrText") or "").strip():
            return False
        return str(shape.get("ocrMatchRole") or "off").strip().lower() != "off"

    def _shape_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
        *,
        match_strategy: str = "anchor_pixel",
        ocr_fallback: bool = True,
    ) -> float:
        try:
            condition = "image" if match_strategy == "anchor_pixel" else "auto"
            score = float(self._match_shape(ctx, image, shape, frame_data_url, condition=condition).get("similarity") or 0)
            if ocr_fallback and score < self.scene_threshold and self._shape_ocr_fallback_enabled(shape):
                ocr_score = float(self._match_shape(ctx, image, shape, frame_data_url, condition="ocr").get("similarity") or 0)
                score = max(score, ocr_score)
            return score
        except Exception as exc:
            self._log("detail", f"匹配失败：{image.get('title')} / {shape.get('title')}：{exc}")
            return 0

    def _shape_match_role(self, shape: dict[str, Any], key: str, default: str = "required") -> str:
        role = str(shape.get(key) or default).strip().lower()
        return role if role in {"required", "optional", "off"} else default

    def _shape_ocr_role(self, shape: dict[str, Any]) -> str:
        default = "required" if bool(shape.get("ocrEnabled")) and str(shape.get("ocrText") or "").strip() else "off"
        return self._shape_match_role(shape, "ocrMatchRole", default)

    def _shape_image_role(self, shape: dict[str, Any]) -> str:
        return self._shape_match_role(shape, "imageMatchRole", "required")

    def _shape_runtime_match_payload_flags(self, shape: dict[str, Any], *, condition: str = "auto") -> dict[str, Any]:
        ocr_text = str(shape.get("ocrText") or "").strip()
        image_role = self._shape_image_role(shape)
        ocr_role = self._shape_ocr_role(shape)
        force_image = condition == "image"
        force_ocr = condition == "ocr"
        ocr_enabled = bool(not force_image and ocr_role != "off" and ocr_text)
        scan_enabled = bool(shape.get("floating") and not ocr_enabled)
        jitter_enabled = bool(shape.get("jitterEnabled") and not scan_enabled and not ocr_enabled)
        return {
            "image_role": image_role,
            "ocr_role": ocr_role,
            "ocr_enabled": ocr_enabled,
            "scan": scan_enabled,
            "match_strategy": "auto" if (force_ocr or scan_enabled or jitter_enabled) else "anchor_pixel",
        }

    def _match_shape(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
        *,
        condition: str = "auto",
    ) -> dict[str, Any]:
        flags = self._shape_runtime_match_payload_flags(shape, condition=condition)
        image_role = str(flags["image_role"])
        ocr_role = str(flags["ocr_role"])
        ocr_enabled = bool(flags["ocr_enabled"])
        if image_role == "off" and not ocr_enabled:
            return {"ok": False, "matched": False, "similarity": 0, "matches": [], "box": self._box(shape, image), "reason": "match_disabled", "flags": flags}
        try:
            result = self._run_match(
                ctx,
                image,
                shape,
                frame_data_url,
                scan=bool(flags["scan"]),
                match_strategy=str(flags["match_strategy"]),
                ocr_enabled=ocr_enabled,
            )
        except Exception as exc:
            raise RuntimeError(f"浮动标注「{shape.get('title') or shape.get('id')}」匹配失败：{exc}") from exc
        similarity = float(result.get("similarity") or 0)
        matched = similarity >= float(self.scene_threshold)
        if ocr_enabled:
            matched = matched and bool(result.get("matches"))
        if matched:
            fixed_box = result.get("fixed_box")
            if isinstance(fixed_box, dict):
                result["resolved_box"] = fixed_box
            else:
                result["resolved_box"] = result.get("box") if isinstance(result.get("box"), dict) else self._box(shape, image)
        result["matched"] = matched
        result["flags"] = flags
        return result

    def _require_shape_match(
        self,
        result: dict[str, Any],
        shape: dict[str, Any],
    ) -> None:
        if bool(result.get("matched")):
            return
        flags = result.get("flags") if isinstance(result.get("flags"), dict) else {}
        ocr_text = str(shape.get("ocrText") or "").strip()
        if str(flags.get("ocr_role") or "") == "required" and bool(flags.get("ocr_enabled")):
            raise RuntimeError(f"未能按 OCR 定位浮动按钮「{shape.get('title') or shape.get('id')}」：目标 {ocr_text}")
        if str(flags.get("image_role") or "") == "required":
            raise RuntimeError(f"未能按图像定位浮动按钮「{shape.get('title') or shape.get('id')}」")
        if str(flags.get("image_role") or "") != "off" or bool(flags.get("ocr_enabled")):
            kind = "浮动按钮" if bool(shape.get("floating")) else "按钮"
            raise RuntimeError(f"未能定位{kind}「{shape.get('title') or shape.get('id')}」")

    def _scene_identity_shape_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
    ) -> float:
        image_role = self._shape_image_role(shape)
        ocr_role = self._shape_ocr_role(shape)
        scores: list[tuple[str, float]] = []
        if image_role != "off":
            scores.append((image_role, self._shape_score(ctx, image, shape, frame_data_url, ocr_fallback=False)))
        if ocr_role != "off" and str(shape.get("ocrText") or "").strip():
            try:
                scores.append(
                    (
                        ocr_role,
                        float(
                            self._match_shape(
                                ctx,
                                image,
                                shape,
                                frame_data_url,
                                condition="ocr",
                            ).get("similarity") or 0
                        ),
                    )
                )
            except Exception as exc:
                self._log("detail", f"OCR匹配失败：{image.get('title')} / {shape.get('title')}：{exc}")
                scores.append((ocr_role, 0))
        if not scores:
            return 0
        threshold = float(self.scene_threshold)
        required_scores = [score for role, score in scores if role == "required"]
        if required_scores and any(score < threshold for score in required_scores):
            return 0
        return max(score for _role, score in scores)

    def _scene_score(self, ctx: dict[str, Any], image: dict[str, Any], frame_data_url: str) -> float:
        scores = [
            self._scene_identity_shape_score(ctx, image, shape, frame_data_url)
            for shape in self._scene_identity_shapes(image)
        ]
        return max(scores) if scores else 0

    def _popup_score(self, ctx: dict[str, Any], image: dict[str, Any], frame_data_url: str) -> float:
        return self._match_image_score(ctx, image, frame_data_url, self._popup_match_shapes(image), log_label="弹窗标识")

    def _match_image_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str,
        shapes: list[dict[str, Any]],
        *,
        log_label: str,
        scan_fallback: bool = True,
    ) -> float:
        scores: list[float] = []
        for shape in shapes:
            score = self._shape_score(ctx, image, shape, frame_data_url)
            if scan_fallback and score < 50:
                try:
                    scan_score = float(self._run_match(ctx, image, shape, frame_data_url, scan=True, match_strategy="auto").get("similarity") or 0)
                    score = max(score, scan_score)
                except Exception as exc:
                    self._log("detail", f"{log_label}扫描失败：{image.get('title')} / {shape.get('title')}：{exc}")
            scores.append(score)
        scores = [score for score in scores if score > 0]
        if not scores:
            return 0
        scores.sort(reverse=True)
        return sum(scores[: min(3, len(scores))]) / min(3, len(scores))

    def _identify_scene(self, ctx: dict[str, Any], frame_data_url: str, keys: list[str] | None = None) -> tuple[str, float]:
        priorities = {
            "duplicated": 12,
            "reward": 11,
            "wanling_invite": 10,
            "gift": 9,
            "youli_result": 8,
            "youli_explore": 7,
            "youli": 6,
            "daily_activity": 5,
            "signup_reward": 5,
            "signup": 5,
            "daily": 4,
            "settings": 3,
            "world_menu": 2,
            "hide_floating": 1,
            "world": 0,
        }
        candidates: list[tuple[str, float]] = []
        for key in keys or list(priorities):
            image = self._image(ctx, key)
            if image is None:
                continue
            score = self._scene_score(ctx, image, frame_data_url)
            candidates.append((key, score))
        candidates.sort(key=lambda item: (item[1], priorities.get(item[0], 0)), reverse=True)
        return candidates[0] if candidates else ("", 0)

    def _scene_matches(self, key: str, score: float) -> bool:
        return bool(key) and score >= float(self.scene_thresholds.get(key, self.scene_threshold))

    def _click_shape(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str | None = None,
        *,
        match_result: dict[str, Any] | None = None,
    ) -> None:
        width, height = self._frame_size(image)
        box = self._box(shape, image)
        action_match_result: dict[str, Any] | None = None
        if frame_data_url:
            action_match_result = match_result if isinstance(match_result, dict) else self._match_shape(ctx, image, shape, frame_data_url)
            self._require_shape_match(action_match_result, shape)
        click_x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
        click_y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
        if action_match_result is not None:
            self._log(
                "detail",
                (
                    f"点击标注「{shape.get('title') or shape.get('id')}」："
                    f"similarity={float(action_match_result.get('similarity') or 0):.0f}，"
                    f"ocr={str(action_match_result.get('ocr_text') or '')[:40]}，"
                    f"fixed_box={action_match_result.get('fixed_box')}，"
                    f"click=({click_x:.1f},{click_y:.1f})"
                ),
            )
        payload = {
            "x": click_x,
            "y": click_y,
            "mode": "screen",
            "area": "client",
            "rotate": "0",
            "fixed_width": width,
            "fixed_height": height,
            "frame_width": width,
            "frame_height": height,
            "input_backend": "adb",
        }
        entry: UserDevice = ctx["entry"]
        (_click_game_window2_service(payload) if entry.mode == "local" else _click_remote_game_window2(entry, payload))
        self._clear_tick_frame(ctx)

    def _wait_shape_match(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image: dict[str, Any],
        shape: dict[str, Any],
        *,
        timeout: float,
        label: str,
    ):
        deadline = time.monotonic() + max(0.1, float(timeout or 0.1))
        last_similarity = 0.0
        last_ocr_text = ""
        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            result = self._match_shape(ctx, image, shape, frame)
            last_similarity = float(result.get("similarity") or 0)
            last_ocr_text = str(result.get("ocr_text") or "")[:40]
            if bool(result.get("matched")):
                return frame, result
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：等待命中 {last_similarity:.0f}%",
                    phase="wait_shape_match",
                )
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
        raise RuntimeError(f"{label} 超时，最后 {last_similarity:.0f}% OCR={last_ocr_text}")

    def _click_frame_point(self, ctx: dict[str, Any], image: dict[str, Any], x: float, y: float) -> None:
        width, height = self._frame_size(image)
        payload = {
            "x": max(0.0, min(float(width - 1), float(x))),
            "y": max(0.0, min(float(height - 1), float(y))),
            "mode": "screen",
            "area": "client",
            "rotate": "0",
            "fixed_width": width,
            "fixed_height": height,
            "frame_width": width,
            "frame_height": height,
            "input_backend": "adb",
        }
        entry: UserDevice = ctx["entry"]
        (_click_game_window2_service(payload) if entry.mode == "local" else _click_remote_game_window2(entry, payload))
        self._clear_tick_frame(ctx)

    def _drag_frame_point(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        duration_ms: int = 300,
    ) -> None:
        width, height = self._frame_size(image)
        payload = {
            "start_x": max(0.0, min(float(width - 1), float(start_x))),
            "start_y": max(0.0, min(float(height - 1), float(start_y))),
            "end_x": max(0.0, min(float(width - 1), float(end_x))),
            "end_y": max(0.0, min(float(height - 1), float(end_y))),
            "duration_ms": duration_ms,
            "mode": "screen",
            "area": "client",
            "rotate": "0",
            "fixed_width": width,
            "fixed_height": height,
            "frame_width": width,
            "frame_height": height,
            "input_backend": "adb",
        }
        entry: UserDevice = ctx["entry"]
        (_drag_game_window2_service(payload) if entry.mode == "local" else _drag_remote_game_window2(entry, payload))
        self._clear_tick_frame(ctx)

    def _shape_center(self, shape: dict[str, Any], image: dict[str, Any], frame_data_url: str | None = None, ctx: dict[str, Any] | None = None) -> tuple[float, float]:
        box = self._box(shape, image)
        return (
            float(box.get("x") or 0) + float(box.get("w") or 0) / 2,
            float(box.get("y") or 0) + float(box.get("h") or 0) / 2,
        )

    def _click_generic_back(self, ctx: dict[str, Any]) -> None:
        image = self._image(ctx, "settings") or self._image(ctx, "world")
        if not image:
            return
        width, height = self._frame_size(image)
        self._click_frame_point(ctx, image, width * 0.085, height * 0.947)

    def _keyevents(self, ctx: dict[str, Any], keys: list[str]) -> None:
        payload = {"keys": keys}
        entry: UserDevice = ctx["entry"]
        (_keyevent_game_window2_service(payload) if entry.mode == "local" else _keyevent_remote_game_window2(entry, payload))
        self._clear_tick_frame(ctx)

    def _text(self, ctx: dict[str, Any], text: str) -> None:
        payload = {"text": text}
        entry: UserDevice = ctx["entry"]
        (_text_game_window2_service(payload) if entry.mode == "local" else _text_remote_game_window2(entry, payload))
        self._clear_tick_frame(ctx)

    def _wait_for_scene(self, ctx: dict[str, Any], stop_event: threading.Event, keys: list[str], timeout: float, interval: float = 0.8) -> tuple[str, float, str]:
        deadline = time.time() + timeout
        last_key, last_score = "", 0.0
        last_frame = ""
        while time.time() < deadline:
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            key, score = self._identify_scene(ctx, frame, keys)
            last_key, last_score, last_frame = key, score, frame
            if key in keys and self._scene_matches(key, score):
                return key, score, frame
            self._clear_tick_frame(ctx)
            time.sleep(interval)
        return last_key, last_score, last_frame

    def _wait_scene_id(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        target_scene_id: int,
        *,
        timeout: float,
        label: str = "等待场景",
    ):
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            elapsed = time.monotonic() - start
            scene_id, score = self._identify_scene_number(ctx, frame, [target_scene_id])
            last_scene_id, last_score = scene_id, score
            if scene_id == target_scene_id:
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"{label}：已到达 #{target_scene_id} {score:.0f}%")
                return target_scene_id, score
            with self._lock:
                self._status.update({
                    "phase": "wait_scene",
                    "current_scene": scene_id,
                    "message": f"{label}：当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    "updated_at": time.time(),
                })
            if elapsed >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"{label} 超时，未检测到 #{target_scene_id}，最后 {scene_text} {last_score:.0f}%")

    def _wait_mail_list_ready(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float,
        label: str = "等待邮件列表",
    ):
        image121 = (ctx.get("images") or {}).get(121)
        marker_shape = self._find_shape(image121, "邮件标识") if isinstance(image121, dict) else None
        if not isinstance(image121, dict) or not marker_shape:
            return (yield from self._wait_scene_id(ctx, stop_event, 121, timeout=timeout, label=label))
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_marker_score = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            elapsed = time.monotonic() - start
            scene_id, score = self._identify_scene_number(ctx, frame, [121])
            last_scene_id, last_score = scene_id, score
            marker_score = 0.0
            marker_matched = False
            if scene_id == 121:
                try:
                    marker_result = self._match_shape(ctx, image121, marker_shape, frame)
                    marker_score = float(marker_result.get("similarity") or 0)
                    marker_matched = bool(marker_result.get("matched"))
                except Exception as exc:
                    self._log("detail", f"{label}：邮件标识匹配失败：{exc}")
            last_marker_score = marker_score
            if scene_id == 121 and marker_matched:
                with self._lock:
                    self._status.update({"current_scene": 121, "updated_at": time.time()})
                self._log("success", f"{label}：已到达 #121 {score:.0f}%，邮件标识 {marker_score:.0f}%")
                return 121, score
            with self._lock:
                self._status.update(
                    {
                        "phase": "wait_mail_list_ready",
                        "current_scene": scene_id,
                        "message": (
                            f"{label}：当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} "
                            f"{score:.0f}%，邮件标识 {marker_score:.0f}%"
                        ),
                        "updated_at": time.time(),
                    }
                )
            if elapsed >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"{label} 超时，最后 {scene_text} {last_score:.0f}%，邮件标识 {last_marker_score:.0f}%")

    def _ocr_lines(self, frame_data_url: str) -> list[dict[str, Any]]:
        try:
            response = _recognize_data_annotation_ocr_frame(frame_data_url)
        except Exception as exc:
            self._log("detail", f"OCR 失败：{exc}")
            return []
        return [line.model_dump() for line in response.lines]

    def _ocr_lines_in_shapes(
        self,
        frame_data_url: str,
        image: dict[str, Any],
        shape_titles: tuple[str, ...] | list[str],
        *,
        padding: int = 16,
    ) -> list[dict[str, Any]]:
        crop = self._crop_frame_data_url_for_shapes(frame_data_url, image, shape_titles, padding=padding)
        if crop is None:
            return self._ocr_lines(frame_data_url)
        crop_data_url, offset_x, offset_y = crop
        lines = self._ocr_lines(crop_data_url)
        for line in lines:
            line["x"] = float(line.get("x") or 0) + offset_x
            line["y"] = float(line.get("y") or 0) + offset_y
        return lines

    def _crop_frame_data_url_for_shapes(
        self,
        frame_data_url: str,
        image: dict[str, Any],
        shape_titles: tuple[str, ...] | list[str],
        *,
        padding: int = 16,
    ) -> tuple[str, float, float] | None:
        try:
            header, encoded = frame_data_url.split(",", 1) if "," in frame_data_url else ("", frame_data_url)
            raw = base64.b64decode(encoded)
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as pil_image:
                width, height = pil_image.size
                boxes: list[dict[str, Any]] = []
                for title in shape_titles:
                    shape = self._find_shape(image, str(title))
                    if shape:
                        boxes.append(self._box(shape, image))
                if not boxes:
                    return None
                left = max(0, int(min(float(box.get("x") or 0) for box in boxes) - padding))
                top = max(0, int(min(float(box.get("y") or 0) for box in boxes) - padding))
                right = min(width, int(max(float(box.get("x") or 0) + float(box.get("w") or 0) for box in boxes) + padding))
                bottom = min(height, int(max(float(box.get("y") or 0) + float(box.get("h") or 0) for box in boxes) + padding))
                if right <= left or bottom <= top:
                    return None
                cropped = pil_image.crop((left, top, right, bottom))
                buffer = io.BytesIO()
                cropped.save(buffer, format="PNG")
                data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
                return data_url, float(left), float(top)
        except Exception as exc:
            self._log("detail", f"裁剪 OCR 区域失败，回退全图 OCR：{exc}")
            return None

    def _ocr_text(self, lines: list[dict[str, Any]]) -> str:
        return "".join(_sanitize_ocr_text(line.get("text")) for line in lines)

    def _text_in_shape(self, lines: list[dict[str, Any]], image: dict[str, Any] | None, shape_title: str) -> str:
        shape = self._find_shape(image, shape_title) if image else None
        if not shape or not image:
            return ""
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        fragments: list[str] = []
        for line in lines:
            cx = float(line.get("x") or 0) + float(line.get("w") or 0) / 2
            cy = float(line.get("y") or 0) + float(line.get("h") or 0) / 2
            if left <= cx <= right and top <= cy <= bottom:
                fragments.append(_sanitize_ocr_text(line.get("text")))
        return "".join(fragment for fragment in fragments if fragment)

    def _ocr_centers_in_shape(
        self,
        lines: list[dict[str, Any]],
        image: dict[str, Any] | None,
        shape_title: str,
        *,
        include: tuple[str, ...],
        exclude: tuple[str, ...] = (),
    ) -> list[tuple[float, float, str]]:
        shape = self._find_shape(image, shape_title) if image else None
        if not shape or not image:
            return []
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        matches: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            if include and not all(fragment in text for fragment in include):
                continue
            if exclude and any(fragment in text for fragment in exclude):
                continue
            line_x = float(line.get("x") or 0)
            line_w = float(line.get("w") or 0)
            cx = line_x + line_w / 2
            if include and line_w > 0 and text:
                target_fragment = next((fragment for fragment in include if fragment in text), "")
                if target_fragment:
                    index = text.find(target_fragment)
                    if index >= 0:
                        cx = line_x + line_w * ((index + len(target_fragment) / 2) / max(1, len(text)))
            cy = float(line.get("y") or 0) + float(line.get("h") or 0) / 2
            if left <= cx <= right and top <= cy <= bottom:
                matches.append((cx, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _parse_fraction(self, text: str) -> tuple[int, int] | None:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        match = re.search(r"(\d{1,5})/(\d{1,5})", normalized)
        if not match:
            return None
        current = int(match.group(1))
        total = int(match.group(2))
        return (current, total) if total > 0 else None

    def _current_scene(self, ctx: dict[str, Any], keys: list[str] | None = None) -> tuple[str, float, str]:
        frame = self._screencap(ctx)
        key, score = self._identify_scene(ctx, frame, keys)
        if key and self._scene_matches(key, score):
            with self._lock:
                self._status.update({"current_scene": self.scene_ids.get(key), "updated_at": time.time()})
        return key, score, frame

    def _current_scene_number(self, ctx: dict[str, Any], frame: str | None = None) -> tuple[int | None, float, str]:
        frame_data_url = frame or self._screencap(ctx)
        scene_id, score = self._identify_scene_number(ctx, frame_data_url)
        if scene_id is not None:
            with self._lock:
                self._status.update({"current_scene": scene_id, "updated_at": time.time()})
        return scene_id, score, frame_data_url

    def _scene_jump_intermediate_confirm_shape(
        self,
        current_image: dict[str, Any] | None,
        source_shape: dict[str, Any],
    ) -> dict[str, Any] | None:
        if current_image is None:
            return None
        source_title = str(source_shape.get("title") or "").strip()
        if source_title not in {"离开", "返回", "关闭"}:
            return None
        scene_title = str(current_image.get("title") or "").strip()
        if "离开" not in scene_title and "退出" not in scene_title:
            return None
        for shape in self._flatten_shapes(current_image.get("shapes")):
            if str(shape.get("title") or "").strip() in {"确认", "确定"}:
                return shape
        return None

    def _wait_scene_jump_result(
        self,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        tree: list[dict[str, Any]],
        *,
        source_scene_id: int,
        target_scene_id: int,
        edge: dict[str, Any],
        stop_event: threading.Event,
    ):
        shape = edge["shape"]
        expected_ids = list(edge.get("target_ids") or [])
        allows_self = source_scene_id in expected_ids
        timeout_seconds = 30.0 if allows_self else 60.0
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_frame = ""
        history: list[str] = []
        left_source = False
        handled_intermediate_scene_ids: set[int] = set()

        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            elapsed = time.monotonic() - start

            scene_id, score = self._identify_scene_number(ctx, frame)
            last_scene_id, last_score, last_frame = scene_id, score, frame
            if scene_id is not None and scene_id != source_scene_id:
                left_source = True
            matched_expected, expected_score = self._identify_scene_number(ctx, frame, expected_ids)
            scene_text = f"#{scene_id}" if scene_id is not None else "unknown"
            history.append(f"{elapsed:.1f}s {scene_text} {score:.0f}% expected={expected_score:.0f}% left={left_source}")
            if scene_id is not None and scene_id in expected_ids:
                if self._increment_scene_jump_target(shape, scene_id):
                    self._write_asset_tree(asset_tree_path, tree)
                    ctx["images"] = self._index_images(tree)
                self._log("info", f"场景跳转：#{source_scene_id} -> #{scene_id}，{elapsed:.1f}s")
                return scene_id
            if scene_id is not None and scene_id not in handled_intermediate_scene_ids:
                current_image = (ctx.get("images") or {}).get(scene_id)
                confirm_shape = self._scene_jump_intermediate_confirm_shape(current_image, shape)
                if confirm_shape is not None:
                    confirm_title = str(confirm_shape.get("title") or "确认")
                    handled_intermediate_scene_ids.add(scene_id)
                    with self._lock:
                        self._status.update({
                            "phase": "go_scene_confirm",
                            "current_scene": scene_id,
                            "message": f"跳转确认：#{source_scene_id} -> #{target_scene_id}，点击 {confirm_title}",
                            "updated_at": time.time(),
                        })
                    self._log("action", f"场景跳转确认：#{scene_id}，点击 {confirm_title}")
                    self._click_shape(ctx, current_image, confirm_shape, frame)
                    continue
            with self._lock:
                self._status.update({
                    "phase": "go_scene_wait",
                    "current_scene": scene_id,
                    "message": f"跳转等待：#{source_scene_id} -> #{target_scene_id}，当前 {scene_text} {score:.0f}%",
                    "updated_at": time.time(),
                })

            if elapsed < timeout_seconds:
                continue

            if allows_self and last_scene_id == source_scene_id:
                if self._increment_scene_jump_target(shape, source_scene_id):
                    self._write_asset_tree(asset_tree_path, tree)
                    ctx["images"] = self._index_images(tree)
                self._log("info", f"场景跳转：#{source_scene_id} -> #{source_scene_id}，30s 保底确认自身")
                return source_scene_id

            if last_scene_id is None:
                return self._save_unknown_scene_frame(
                    ctx,
                    asset_tree_path,
                    tree,
                    last_frame or frame,
                    target_scene_id=target_scene_id,
                    current_scene_id=source_scene_id,
                    action_shape=shape,
                    elapsed_seconds=elapsed,
                    history=history,
                )

            if not left_source and last_scene_id == source_scene_id and not allows_self:
                return self._save_unknown_scene_frame(
                    ctx,
                    asset_tree_path,
                    tree,
                    last_frame or frame,
                    target_scene_id=target_scene_id,
                    current_scene_id=source_scene_id,
                    action_shape=shape,
                    elapsed_seconds=elapsed,
                    history=history,
                )

            raise RuntimeError(
                f"场景跳转实际到达 #{last_scene_id}，但该落点不在「{shape.get('title') or '未命名'}」的 sceneJumpTarget 中；"
                "Runtime 已中断，请人工确认并修正标注后重试"
            )

    def _go_scene_task(
        self,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        target_scene_id: int,
        stop_event: threading.Event,
    ):
        tree = ctx.get("asset_tree")
        if not isinstance(tree, list):
            tree = self._load_asset_tree(asset_tree_path)
            ctx["asset_tree"] = tree
            ctx["images"] = self._index_images(tree)

        for _step_index in range(24):
            self._raise_if_stopped(stop_event)
            route_candidate_ids = self._scene_route_candidate_ids(tree, target_scene_id)
            frame = self._screencap(ctx)
            current_scene_id, score = self._identify_scene_number(ctx, frame, route_candidate_ids)
            if current_scene_id is None:
                current_scene_id, score = self._identify_scene_number(ctx, frame)
            if current_scene_id is None:
                return self._save_unknown_scene_frame(
                    ctx,
                    asset_tree_path,
                    tree,
                    frame,
                    target_scene_id=target_scene_id,
                    current_scene_id=None,
                    action_shape=None,
                    elapsed_seconds=0.0,
                    history=[f"起点识别 unknown {score:.0f}%"],
                )
            if current_scene_id == target_scene_id:
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"已在目标场景 #{target_scene_id}")
                return "success"

            route = self._find_scene_route(tree, current_scene_id, target_scene_id)
            if route is None:
                current_image = (ctx.get("images") or {}).get(current_scene_id)
                confirm_shape = self._scene_jump_intermediate_confirm_shape(current_image, {"title": "离开"})
                if confirm_shape is not None:
                    confirm_title = str(confirm_shape.get("title") or "确认")
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"场景移动确认：#{current_scene_id} -> #{target_scene_id}，点击 {confirm_title}",
                            phase="go_scene_confirm",
                            current_scene=current_scene_id,
                        )
                    self._log("action", f"场景移动确认：#{current_scene_id} -> #{target_scene_id}，点击 {confirm_title}")
                    self._click_shape(ctx, current_image, confirm_shape, frame)
                    actual_scene_id = yield from self._wait_scene_jump_result(
                        ctx,
                        asset_tree_path,
                        tree,
                        source_scene_id=current_scene_id,
                        target_scene_id=target_scene_id,
                        edge={
                            "source_id": current_scene_id,
                            "image": current_image,
                            "shape": confirm_shape,
                            "target_ids": [target_scene_id],
                        },
                        stop_event=stop_event,
                    )
                    if actual_scene_id == target_scene_id:
                        with self._lock:
                            self._status.update({
                                "current_scene": target_scene_id,
                                "updated_at": time.time(),
                            })
                        self._log("success", f"到达目标场景 #{target_scene_id}")
                        return "success"
                    self._log("detail", f"场景移动：确认后实际到达 #{actual_scene_id}，重新规划到 #{target_scene_id}")
                    continue
                raise RuntimeError(f"没有从 #{current_scene_id} 到 #{target_scene_id} 的可规划场景跳转路径")
            edge = route[0]
            image = edge["image"]
            shape = edge["shape"]
            shape_title = str(shape.get("title") or "未命名")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"场景移动：#{current_scene_id} -> #{target_scene_id}，点击 {shape_title}",
                    phase="go_scene",
                    current_scene=current_scene_id,
                )
            self._log("action", f"场景移动：#{current_scene_id} -> #{target_scene_id}，点击 {shape_title}")
            self._click_shape(ctx, image, shape, frame)
            actual_scene_id = yield from self._wait_scene_jump_result(
                ctx,
                asset_tree_path,
                tree,
                source_scene_id=current_scene_id,
                target_scene_id=target_scene_id,
                edge=edge,
                stop_event=stop_event,
            )
            if actual_scene_id == target_scene_id:
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"到达目标场景 #{target_scene_id}")
                return "success"
            self._log("detail", f"场景移动：实际到达 #{actual_scene_id}，重新规划到 #{target_scene_id}")

        raise RuntimeError(f"场景移动超过最大重规划步数，未到达 #{target_scene_id}")

    def _execute_hide_floating_window(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        image = self._image(ctx, "hide_floating")
        icon = self._find_shape(image, "图标")
        target = self._find_shape(image, "隐藏区")
        if not image or not icon or not target:
            raise RuntimeError("#58 缺少「图标」或「隐藏区」标注")
        frame = self._screencap(ctx)
        score = self._shape_score(ctx, image, icon, frame)
        if score < self.scene_thresholds.get("hide_floating", 55):
            self._log("info", f"浮动窗未明显出现，图标匹配 {score:.0f}%")
            return
        start_x, start_y = self._shape_center(icon, image, frame, ctx)
        end_x, end_y = self._shape_center(target, image)
        with self._lock:
            self._set_status_locked("running", f"隐藏浮动窗：图标匹配 {score:.0f}%", phase="hide_floating", current_scene=58)
        self._drag_frame_point(ctx, image, start_x, start_y, end_x, end_y, duration_ms=350)
        time.sleep(0.8)

    def _align_settings(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        for attempt in range(12):
            frame = self._screencap(ctx)
            key, score = self._identify_scene(ctx, frame, ["settings", "gift", "duplicated", "reward", "world_menu", "world"])
            matched = key if self._scene_matches(key, score) else ""
            self._log("detail", f"对齐 #49：当前 {matched or 'unknown'} {score:.0f}%")
            if matched:
                with self._lock:
                    scene_id = self.scene_ids.get(matched)
                    self._status.update({"current_scene": scene_id, "updated_at": time.time()})
            if matched == "settings":
                return
            if matched == "reward":
                self._log("detail", "对齐 #49：检测到 #81 过渡奖励，等待回到设置页")
                self._clear_tick_frame(ctx)
                time.sleep(1.0)
                continue
            if matched in {"gift", "duplicated"}:
                close_shape = self._find_shape(self._image(ctx, "gift"), "关闭窗口")
                if close_shape is None:
                    close_shape = self._find_shape(self._image(ctx, "gift"), "关闭", contains=True)
                if close_shape:
                    self._log("detail", f"对齐 #49：检测到 #{self.scene_ids.get(matched)}，点击关闭窗口")
                    self._click_shape(ctx, self._image(ctx, "gift"), close_shape, frame)
                    time.sleep(0.9)
                    continue
            if matched == "world_menu":
                settings_shape = self._find_shape(self._image(ctx, "world_menu"), "设置")
                if not settings_shape:
                    raise RuntimeError("#35 缺少「设置」标注")
                self._log("detail", "对齐 #49：确认 #35 后匹配点击浮动「设置」")
                self._click_shape(ctx, self._image(ctx, "world_menu"), settings_shape, frame)
                time.sleep(1.0)
                continue
            if matched == "world":
                open_shape = self._find_shape(self._image(ctx, "world"), "打开下方菜单")
                if not open_shape:
                    raise RuntimeError("#34 缺少「打开下方菜单」标注")
                self._log("detail", "对齐 #49：确认 #34 后点击打开下方菜单")
                self._click_shape(ctx, self._image(ctx, "world"), open_shape, frame)
                time.sleep(0.8)
                continue
            if attempt >= 3:
                self._log("detail", "对齐 #49：未知场景，点击画面返回按钮兜底")
                self._click_generic_back(ctx)
                time.sleep(0.9)
                continue
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            time.sleep(0.8)
        raise RuntimeError("无法对齐到 #49 设置页")

    def _open_gift(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        frame = self._screencap(ctx)
        image = self._image(ctx, "settings")
        shape = self._find_shape(image, "兑换礼包")
        if not image or not shape:
            raise RuntimeError("#49 缺少「兑换礼包」标注")
        box = self._box(shape, image)
        _width, height = self._frame_size(image)
        self._click_frame_point(
            ctx,
            image,
            float(box.get("x") or 0) + float(box.get("w") or 0) / 2,
            float(box.get("y") or 0) - height * 0.02,
        )
        key, score, _frame = self._wait_for_scene(ctx, stop_event, ["gift"], 6)
        if key != "gift" or not self._scene_matches(key, score):
            raise RuntimeError("点击兑换礼包后未进入 #78")

    def _clear_and_type(self, ctx: dict[str, Any], code: str, stop_event: threading.Event) -> None:
        image = self._image(ctx, "gift")
        shape = self._find_shape(image, "输入兑换码")
        if not image or not shape:
            raise RuntimeError("#78 缺少「输入兑换码」标注")
        self._click_shape(ctx, image, shape)
        time.sleep(0.25)
        self._keyevents(ctx, ["KEYCODE_MOVE_END", *["KEYCODE_DEL" for _ in range(40)]])
        time.sleep(0.25)
        self._raise_if_stopped(stop_event)
        self._text(ctx, code)
        time.sleep(0.35)

    def _submit_code(self, ctx: dict[str, Any], code: str) -> None:
        image = self._image(ctx, "gift")
        shape = self._find_shape(image, "兑换")
        if not image or not shape:
            raise RuntimeError("#78 缺少「兑换」按钮标注")
        self._click_shape(ctx, image, shape)
        self._log("action", f"已提交：{code}")

    def _settle_after_submit(self, ctx: dict[str, Any], code: str, is_last: bool, stop_event: threading.Event) -> None:
        deadline = time.time() + 16.0
        plain_gift_since = 0.0
        last_seen = ""
        while time.time() < deadline:
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            overlay = self._detect_overlay(ctx, frame)
            if overlay == "duplicated":
                if is_last:
                    self._log("info", f"{code}：检测到 #82 已领取，关闭窗口")
                    self._close_gift_to_settings(ctx, stop_event)
                else:
                    self._log("info", f"{code}：检测到 #82 已领取，继续下一个")
                return
            if overlay == "reward":
                last_seen = "reward"
                self._clear_tick_frame(ctx)
                time.sleep(0.8)
                continue

            key, score = self._identify_scene(ctx, frame, ["settings", "gift"])
            if key == "settings" and self._scene_matches(key, score):
                self._log("info", f"{code}：已回到 #49")
                return
            if key == "gift" and self._scene_matches(key, score):
                last_seen = "gift"
                if plain_gift_since <= 0:
                    plain_gift_since = time.time()
                if time.time() - plain_gift_since >= 4.0:
                    if is_last:
                        self._log("info", f"{code}：提交后停留 #78，关闭窗口")
                        self._close_gift_to_settings(ctx, stop_event)
                    else:
                        self._log("info", f"{code}：提交后停留 #78，继续下一个")
                    return
            else:
                plain_gift_since = 0.0
                last_seen = key or last_seen
            self._clear_tick_frame(ctx)
            time.sleep(0.8)

        if is_last:
            self._log("info", f"{code}：等待结果超时，尝试对齐 #49")
            self._align_settings(ctx, stop_event)
        else:
            self._log("info", f"{code}：等待结果超时，继续下一个（最后看到 {last_seen or 'unknown'}）")

    def _detect_overlay(self, ctx: dict[str, Any], frame: str) -> str:
        duplicated = self._image(ctx, "duplicated")
        if duplicated:
            for title in ("礼包已被领取", "已被领取"):
                shape = self._find_shape(duplicated, title, contains=True)
                if shape and self._shape_score(ctx, duplicated, shape, frame) >= self.overlay_threshold:
                    return "duplicated"
        reward = self._image(ctx, "reward")
        if reward:
            for title in ("恭喜获得", "点击继续", "奖品"):
                shape = self._find_shape(reward, title, contains=True)
                if shape and self._shape_score(ctx, reward, shape, frame) >= 65:
                    return "reward"
        return ""

    def _process_code(self, ctx: dict[str, Any], code: str, is_last: bool, stop_event: threading.Event) -> None:
        frame = self._screencap(ctx)
        key, score = self._identify_scene(ctx, frame, ["settings", "gift"])
        if key == "settings" and self._scene_matches(key, score):
            with self._lock:
                self._set_status_locked("running", f"进入 #78 填写：{code}", phase="open_gift", current_scene=49)
            self._open_gift(ctx, stop_event)
        elif key != "gift" or not self._scene_matches(key, score):
            with self._lock:
                self._set_status_locked("running", f"重新对齐后填写：{code}", phase="align_settings")
            self._align_settings(ctx, stop_event)
            self._open_gift(ctx, stop_event)
        with self._lock:
            self._set_status_locked("running", f"输入礼包码：{code}", phase="type_code", current_scene=78)
        self._clear_and_type(ctx, code, stop_event)
        with self._lock:
            self._set_status_locked("running", f"提交礼包码：{code}", phase="submit_code")
        self._submit_code(ctx, code)
        with self._lock:
            self._set_status_locked("running", f"等待兑换结果：{code}", phase="wait_result")
        self._settle_after_submit(ctx, code, is_last, stop_event)

    def _close_gift_to_settings(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        image = self._image(ctx, "gift")
        shape = self._find_shape(image, "关闭窗口")
        if not image or not shape:
            raise RuntimeError("#78 缺少「关闭窗口」标注")
        self._click_shape(ctx, image, shape)
        key, score, _frame = self._wait_for_scene(ctx, stop_event, ["settings"], 2.5, interval=0.25)
        if key == "settings" and self._scene_matches(key, score):
            with self._lock:
                self._status.update({"current_scene": 49, "updated_at": time.time()})

    def _finish_from_settings(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        image = self._image(ctx, "settings")
        shape = self._find_shape(image, "回退")
        if not image or not shape:
            raise RuntimeError("#49 缺少「回退」标注")
        with self._lock:
            self._status.update({"current_scene": 49, "updated_at": time.time()})
        self._click_shape(ctx, image, shape)
        key, score, _frame = self._wait_for_scene(ctx, stop_event, ["world", "world_menu", "settings"], 2.5, interval=0.25)
        if key and self._scene_matches(key, score):
            with self._lock:
                self._status.update({"current_scene": self.scene_ids.get(key), "updated_at": time.time()})


def _normalize_data_annotation_go_scene_payload(payload: dict[str, Any]) -> dict[str, Any]:
    target_scene_id = int(payload.get("target_scene_id") or payload.get("target") or 49)
    return {**payload, "target_scene_id": target_scene_id}


def _normalize_data_annotation_debug_eval_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # Agent-facing Runtime scratchpad. Keep this as a registered manual job so
    # probes use the same resident loop, queue, timeout, stop flag, and logs as
    # real behavior-tree work. Do not bypass this by calling runner internals
    # from one-off scripts unless the user explicitly asks for a private probe.
    payload = dict(payload or {})
    code = str(payload.get("code") or payload.get("source") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="debug_eval 需要 payload.code")
    mode = str(payload.get("mode") or "readonly").strip().lower()
    if mode not in {"readonly", "act"}:
        raise HTTPException(status_code=400, detail="debug_eval mode 只支持 readonly/act")
    return {
        **payload,
        "code": code,
        "mode": mode,
        "call_task": bool(payload.get("call_task", True)),
        "max_output_chars": max(200, min(20000, int(payload.get("max_output_chars") or 4000))),
        "timeout_seconds": max(30, min(3600, int(payload.get("timeout_seconds") or payload.get("max_runtime_seconds") or 120))),
    }


class _DataAnnotationRuntimeDebugContext:
    """Stable facade injected as ``ctx`` for ``debug_eval`` manual jobs.

    Typical payload:
        {"task_type": "debug_eval", "payload": {
            "mode": "readonly",
            "code": "info = ctx.scene(); ctx.log(info); result = info",
        }}

    ``readonly`` is the default and blocks tap/drag. Use ``mode='act'`` only
    when the temporary function is intentionally allowed to change game state.
    """

    def __init__(
        self,
        runner: _DataAnnotationRuntimeRunner,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        readonly: bool = True,
    ) -> None:
        self._runner = runner
        self._ctx = ctx
        self._stop_event = stop_event
        self.readonly = bool(readonly)
        self.output: list[Any] = []

    @property
    def raw(self) -> dict[str, Any]:
        return self._ctx

    def check_stop(self) -> None:
        self._runner._raise_if_stopped(self._stop_event)

    def log(self, value: Any, *, kind: str = "detail") -> Any:
        message = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        self.output.append(value)
        self._runner._log(kind, str(message)[:1000])
        return value

    def table(self, rows: Any) -> Any:
        return self.log(rows)

    def status(self) -> dict[str, Any]:
        return self._runner.status()

    def frame(self) -> str:
        self.check_stop()
        return self._runner._screencap(self._ctx)

    def scene(self, frame: str | None = None, preferred_scene_ids: list[int] | None = None) -> dict[str, Any]:
        self.check_stop()
        frame_data_url = frame or self.frame()
        scene_id, score = self._runner._identify_scene_number(self._ctx, frame_data_url, preferred_scene_ids)
        with self._runner._lock:
            self._runner._status.update({
                "phase": "debug_eval",
                "current_scene": scene_id,
                "message": f"debug_eval 场景识别：{('#' + str(scene_id)) if scene_id is not None else 'unknown'} {score:.0f}%",
                "updated_at": time.time(),
            })
        return {"scene_id": scene_id, "score": score}

    def ocr(self, frame: str | None = None) -> list[dict[str, Any]]:
        self.check_stop()
        return self._runner._ocr_lines(frame or self.frame())

    def image(self, scene: int | str) -> dict[str, Any] | None:
        images: dict[int, dict[str, Any]] = self._ctx.get("images") or {}
        if isinstance(scene, int):
            return images.get(scene)
        text = str(scene or "").strip()
        if text.isdigit():
            return images.get(int(text))
        if text in self._runner.scene_ids:
            return images.get(self._runner.scene_ids[text])
        for image in images.values():
            if str(image.get("title") or "").strip() == text:
                return image
        return None

    def shape(self, scene: int | str, title: str, *, contains: bool = False) -> dict[str, Any] | None:
        return self._runner._find_shape(self.image(scene), title, contains=contains)

    def _require_act(self) -> None:
        if self.readonly:
            raise RuntimeError("debug_eval 当前为 readonly，动作调用需要 payload.mode='act'")

    def tap_shape(self, scene: int | str, title: str, *, contains: bool = False, frame: str | None = None) -> None:
        self._require_act()
        image = self.image(scene)
        shape = self._runner._find_shape(image, title, contains=contains)
        if not image or not shape:
            raise RuntimeError(f"找不到标注：scene={scene} shape={title}")
        self._runner._click_shape(self._ctx, image, shape, frame_data_url=frame)

    def tap(self, scene: int | str, x: float, y: float) -> None:
        self._require_act()
        image = self.image(scene)
        if not image:
            raise RuntimeError(f"找不到场景标注：{scene}")
        self._runner._click_frame_point(self._ctx, image, x, y)

    def drag(self, scene: int | str, start_x: float, start_y: float, end_x: float, end_y: float, duration_ms: int = 1000) -> None:
        self._require_act()
        image = self.image(scene)
        if not image:
            raise RuntimeError(f"找不到场景标注：{scene}")
        self._runner._drag_frame_point(self._ctx, image, start_x, start_y, end_x, end_y, duration_ms=duration_ms)

    def yield_tick(self):
        self.check_stop()
        yield BehaviorTreeStatus.RUNNING


def _run_data_annotation_debug_eval(
    runner: _DataAnnotationRuntimeRunner,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> Any:
    payload = _normalize_data_annotation_debug_eval_payload(payload)
    debug_ctx = _DataAnnotationRuntimeDebugContext(runner, ctx, stop_event, readonly=payload["mode"] != "act")
    namespace: dict[str, Any] = {
        "ctx": debug_ctx,
        "payload": payload,
        "BehaviorTreeStatus": BehaviorTreeStatus,
        "json": json,
        "time": time,
    }
    stdout = io.StringIO()
    with runner._lock:
        runner._set_status_locked("running", f"debug_eval 执行中：{payload['mode']}", phase="debug_eval")
    with contextlib.redirect_stdout(stdout):
        exec(payload["code"], namespace, namespace)
        result = None
        task = namespace.get("task")
        if payload.get("call_task") and callable(task):
            result = task(debug_ctx)
        elif "result" in namespace:
            result = namespace["result"]
    output = stdout.getvalue().strip()
    max_chars = int(payload.get("max_output_chars") or 4000)
    if output:
        runner._log("detail", f"debug_eval stdout: {output[:max_chars]}")
    if debug_ctx.output:
        runner._log("detail", f"debug_eval output: {json.dumps(debug_ctx.output, ensure_ascii=False, default=str)[:max_chars]}")
    if isinstance(result, GeneratorType):
        return result
    if result is not None:
        runner._log("detail", f"debug_eval result: {json.dumps(result, ensure_ascii=False, default=str)[:max_chars]}")
    return "success"


@register_fanxiu_data_annotation_manual_job(
    "debug_eval",
    "调试代码",
    scheduler_supported=False,
    normalize_payload=_normalize_data_annotation_debug_eval_payload,
)
def _run_data_annotation_debug_eval_manual_job(
    runner: _DataAnnotationRuntimeRunner,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> Any:
    return _run_data_annotation_debug_eval(runner, ctx, payload, stop_event)


@register_fanxiu_data_annotation_manual_job("detect_scene", "单步识别", scheduler_supported=False)
def _run_data_annotation_detect_scene_manual_job(
    runner: _DataAnnotationRuntimeRunner,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> str:
    frame = runner._screencap(ctx)
    key, score = runner._identify_scene(ctx, frame)
    scene_id = runner.scene_ids.get(key) if runner._scene_matches(key, score) else None
    with runner._lock:
        runner._status.update({
            "phase": "manual_tick",
            "current_scene": scene_id,
            "message": f"单步识别：{key if scene_id is not None else 'unknown'} {score:.0f}%",
            "updated_at": time.time(),
        })
        runner._log_locked("detail", runner._status["message"])
    return "success"


@register_fanxiu_data_annotation_manual_job("manual_tick", "单步识别", scheduler_supported=False)
def _run_data_annotation_manual_tick_job(
    runner: _DataAnnotationRuntimeRunner,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> str:
    return _run_data_annotation_detect_scene_manual_job(runner, ctx, payload, stop_event)


@register_fanxiu_data_annotation_manual_job("gift_code_redeem", "兑换礼包码", scheduler_supported=True)
def _run_data_annotation_gift_code_manual_job(
    runner: _DataAnnotationRuntimeRunner,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> str:
    raw_codes = payload.get("codes")
    codes = [str(item).strip() for item in raw_codes] if isinstance(raw_codes, list) else []
    codes = [code for code in codes if code]
    if not codes:
        runner._log("skip", "礼包码为空，跳过")
        return "skipped"
    runner._execute_gift_code_task(ctx, codes, stop_event)
    return "success"


@register_fanxiu_data_annotation_manual_job(
    "go_scene",
    "到场景",
    scheduler_supported=True,
    normalize_payload=_normalize_data_annotation_go_scene_payload,
)
def _run_data_annotation_go_scene_manual_job(
    runner: _DataAnnotationRuntimeRunner,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> Any:
    target_scene_id = int(payload.get("target_scene_id") or 49)
    with runner._lock:
        runner._set_status_locked("running", f"场景移动到 #{target_scene_id}", phase="go_scene")
    if target_scene_id == 49:
        runner._align_settings(ctx, stop_event)
        return "success"
    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("缺少场景移动资产树路径，当前只支持直接对齐 #49")
    return runner._go_scene_task(ctx, asset_tree_path, target_scene_id, stop_event)


@register_fanxiu_data_annotation_manual_job("hide_floating_window", "隐藏浮动窗", scheduler_supported=True)
def _run_data_annotation_hide_floating_window_manual_job(
    runner: _DataAnnotationRuntimeRunner,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> str:
    runner._execute_hide_floating_window(ctx, stop_event)
    return "success"


@register_fanxiu_data_annotation_manual_job("daily_signup", "日常_报名", scheduler_supported=True)
def _run_data_annotation_daily_signup_manual_job(
    runner: _DataAnnotationRuntimeRunner,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> Any:
    return runner._execute_daily_signup_task(ctx, stop_event)


@register_fanxiu_data_annotation_manual_job("mail_claim_check", "邮件_领取检查", scheduler_supported=True)
def _run_data_annotation_mail_claim_check_manual_job(
    runner: _DataAnnotationRuntimeRunner,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> Any:
    return runner._execute_mail_claim_check_task(ctx, stop_event, payload)


_DATA_ANNOTATION_RUNTIME_RUNNER = _DataAnnotationRuntimeRunner()


def _data_annotation_dir() -> Path:
    return get_settings().data_dir / "fanxiu" / "data-annotation"


def _data_annotation_runtime_dir() -> Path:
    path = _data_annotation_dir() / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _data_annotation_runtime_state_path() -> Path:
    return _data_annotation_runtime_dir() / "runtime_state.json"


def _data_annotation_world_facts_path() -> Path:
    return _data_annotation_runtime_dir() / "world_facts.json"


def _data_annotation_scheduler_state_path() -> Path:
    return _data_annotation_runtime_dir() / "scheduler_tasks.json"


def _data_annotation_manual_job_state_path() -> Path:
    return _data_annotation_runtime_dir() / "manual_jobs.json"


def _data_annotation_mail_scan_state_path() -> Path:
    return _data_annotation_runtime_dir() / "mail_scan_state.json"


def _read_data_annotation_world_facts() -> dict[str, Any]:
    return read_data_annotation_world_facts(_data_annotation_world_facts_path())


def _write_data_annotation_world_facts(facts: dict[str, Any]) -> None:
    write_data_annotation_world_facts(_data_annotation_world_facts_path(), facts)


def _record_data_annotation_scheduler_task_fact(task: dict[str, Any], result: str) -> None:
    record_data_annotation_scheduler_task_fact(_data_annotation_world_facts_path(), task, result)


def _persist_data_annotation_runtime_status(status: dict[str, Any]) -> None:
    persist_data_annotation_runtime_status(
        _data_annotation_runtime_state_path(),
        _data_annotation_world_facts_path(),
        status,
    )


def _read_data_annotation_runtime_status() -> dict[str, Any]:
    return read_data_annotation_runtime_status(_data_annotation_runtime_state_path())


def _is_data_annotation_runtime_live_empty(status: dict[str, Any]) -> bool:
    return is_data_annotation_runtime_live_empty(status)


def _append_data_annotation_runtime_log_once(status: dict[str, Any], kind: str, message: str) -> None:
    append_data_annotation_runtime_log_once(status, kind, message, time_text=datetime.now().strftime("%H:%M:%S"))


def _normalize_data_annotation_runtime_guard_items(status: dict[str, Any]) -> None:
    normalize_data_annotation_runtime_guard_items(status, _DATA_ANNOTATION_RUNTIME_RUNNER.guard_definitions)


def _repair_orphaned_data_annotation_scheduler_runs(tasks: list[dict[str, Any]]) -> bool:
    pending_scheduler_ids: set[str] = set()
    for job in _read_data_annotation_manual_jobs():
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "")
        if scheduler_task_id:
            pending_scheduler_ids.add(scheduler_task_id)
    if _DATA_ANNOTATION_RUNTIME_RUNNER.status().get("running"):
        return False
    changed = False
    current_ts = time.time()
    for task in tasks:
        task_id = str(task.get("id") or "")
        if not task_id or task_id in pending_scheduler_ids:
            continue
        if str(task.get("last_result") or "") not in {"queued", "running"}:
            continue
        last_run_ts = parse_data_annotation_task_time(task.get("last_run_at"))
        if last_run_ts is not None and current_ts - last_run_ts < 60:
            continue
        task["last_result"] = "stopped"
        task["retry_after"] = None
        checkpoint = task.get("checkpoint") if isinstance(task.get("checkpoint"), dict) else {}
        checkpoint["recovered_from_orphaned_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        task["checkpoint"] = checkpoint
        changed = True
    return changed


def _data_annotation_runtime_status() -> dict[str, Any]:
    status = _DATA_ANNOTATION_RUNTIME_RUNNER.status()
    persisted = _read_data_annotation_runtime_status()
    if persisted and _is_data_annotation_runtime_live_empty(status):
        status.update(persisted)
        status["running"] = False
        status["guard_running"] = False
        status["service_running"] = False
        status["updated_at"] = time.time()
        if persisted.get("running"):
            status["status"] = "stopped"
            status["phase"] = "stopped"
            status["message"] = "后端已重载，运行状态已结束"
            status["finished_at"] = status.get("finished_at") or time.time()
            _append_data_annotation_runtime_log_once(status, "stop", "后端已重载，运行状态已结束")
        elif persisted.get("guard_enabled") or persisted.get("guard_running"):
            status["status"] = "idle"
            status["message"] = "后端已重载，行为树服务待恢复"
            _append_data_annotation_runtime_log_once(status, "stop", "后端已重载，行为树服务待恢复")
    _normalize_data_annotation_runtime_guard_items(status)
    status.pop("priority", None)
    _persist_data_annotation_runtime_status(status)
    return status


def _read_data_annotation_scheduler_tasks() -> list[dict[str, Any]]:
    raw = _read_data_annotation_json(_data_annotation_scheduler_state_path(), None)
    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        _default_data_annotation_scheduler_tasks(),
        _read_data_annotation_world_facts(),
        task_supported=_data_annotation_task_supported,
        now=datetime.now(),
    )
    if _repair_orphaned_data_annotation_scheduler_runs(tasks):
        changed = True
    if changed:
        _write_data_annotation_scheduler_tasks(tasks)
    return tasks


def _write_data_annotation_scheduler_tasks(tasks: list[dict[str, Any]]) -> None:
    _write_data_annotation_json(
        _data_annotation_scheduler_state_path(),
        [_data_annotation_scheduler_task_state(task) for task in tasks],
    )


def _read_data_annotation_manual_jobs() -> list[dict[str, Any]]:
    raw = _read_data_annotation_json(_data_annotation_manual_job_state_path(), [])
    return read_data_annotation_manual_jobs(raw)


def _write_data_annotation_manual_jobs(jobs: list[dict[str, Any]]) -> None:
    _write_data_annotation_json(_data_annotation_manual_job_state_path(), data_annotation_manual_jobs_state(jobs))


def _requeue_running_data_annotation_manual_jobs() -> int:
    jobs = _read_data_annotation_manual_jobs()
    updated, changed_count = requeue_running_data_annotation_manual_jobs(jobs)
    if changed_count:
        _write_data_annotation_manual_jobs(updated)
    return changed_count


def _remove_data_annotation_manual_job(job_id: str) -> None:
    job_id = str(job_id or "")
    if not job_id:
        return
    jobs = [job for job in _read_data_annotation_manual_jobs() if str(job.get("id") or "") != job_id]
    _write_data_annotation_manual_jobs(jobs)


def _enqueue_data_annotation_manual_job(
    task_type: str,
    payload: dict[str, Any] | None = None,
    *,
    label: str = "",
    interruptible: bool | None = None,
) -> dict[str, Any]:
    task_type = str(task_type or "detect_scene").strip() or "detect_scene"
    definition = _data_annotation_manual_job_definition(task_type)
    job = create_data_annotation_manual_job(
        task_type,
        payload,
        label=label,
        interruptible=interruptible,
        definition=definition,
        task_label=_DATA_ANNOTATION_RUNTIME_RUNNER._runtime_task_label,
        now=time.time(),
    )
    jobs = _read_data_annotation_manual_jobs()
    jobs.append(job)
    _write_data_annotation_manual_jobs(jobs)
    return job


def _queue_data_annotation_manual_job_status(
    *,
    entry: UserDevice,
    entry_id: str,
    task_type: str,
    payload: dict[str, Any] | None = None,
    label: str = "",
    interruptible: bool | None = None,
) -> dict[str, Any]:
    asset_tree_path = _data_annotation_asset_tree_path(entry_id)
    _DATA_ANNOTATION_RUNTIME_RUNNER.ensure_service(entry=entry, entry_id=entry_id, asset_tree_path=asset_tree_path)
    job = _enqueue_data_annotation_manual_job(
        task_type,
        payload,
        label=label,
        interruptible=interruptible,
    )
    _DATA_ANNOTATION_RUNTIME_RUNNER._service_wake_event.set()
    status = _DATA_ANNOTATION_RUNTIME_RUNNER.status()
    status.update({
        "entry_id": entry_id,
        "phase": "manual_job_queued",
        "message": f"手动作业已排队：{job.get('label') or job.get('task_type')}",
        "updated_at": time.time(),
    })
    logs = list(status.get("logs") or [])
    logs.append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "kind": "info",
        "scope": "manual_job",
        "item_id": "manual_job",
        "message": f"[{job.get('id')}] {status['message']}",
    })
    status["logs"] = logs[-500:]
    _persist_data_annotation_runtime_status(status)
    return status


def _submit_data_annotation_manual_job(
    *,
    entry: UserDevice,
    entry_id: str,
    task_type: str,
    payload: dict[str, Any] | None = None,
    label: str = "",
    interruptible: bool | None = None,
) -> dict[str, Any]:
    task_type = str(task_type or "detect_scene").strip() or "detect_scene"
    definition = _data_annotation_manual_job_definition(task_type)
    if definition is None:
        raise HTTPException(status_code=400, detail=f"暂不支持的任务类型：{task_type}")
    return _queue_data_annotation_manual_job_status(
        entry=entry,
        entry_id=entry_id,
        task_type=task_type,
        payload=payload,
        label=label,
        interruptible=interruptible if interruptible is not None else definition.interruptible,
    )


def _pop_next_data_annotation_manual_job() -> dict[str, Any] | None:
    jobs = _read_data_annotation_manual_jobs()
    selected, claimed_jobs = pop_next_data_annotation_manual_job(jobs)
    if selected is None:
        return None
    _write_data_annotation_manual_jobs(claimed_jobs)
    return selected


def _start_next_data_annotation_manual_job_if_idle(entry: UserDevice, entry_id: str) -> dict[str, Any] | None:
    if _DATA_ANNOTATION_RUNTIME_RUNNER.status().get("running"):
        return None
    task = _pop_next_data_annotation_manual_job()
    if task is None:
        return None
    return _DATA_ANNOTATION_RUNTIME_RUNNER.start_manual_runtime_task(
        entry=entry,
        entry_id=entry_id,
        task=task,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
    )


def _next_data_annotation_scheduler_time(task: dict[str, Any], now: datetime | None = None) -> str | None:
    return _core_next_data_annotation_scheduler_time(task, now if now is not None else datetime.now())


def _sync_data_annotation_scheduler_tasks_from_world_facts(tasks: list[dict[str, Any]]) -> bool:
    return sync_data_annotation_scheduler_tasks_from_world_facts(
        tasks,
        _read_data_annotation_world_facts(),
        now=datetime.now(),
    )


def _data_annotation_task_supported(task: dict[str, Any]) -> bool:
    definition = _data_annotation_manual_job_definition(str(task.get("task_type") or ""))
    return bool(definition and definition.scheduler_supported)


def _data_annotation_scheduler_task_view(task: dict[str, Any]) -> dict[str, Any]:
    return {**task, "supported": _data_annotation_task_supported(task)}


def _data_annotation_scheduler_task_plan_reason(task: dict[str, Any], due: bool) -> str:
    return data_annotation_scheduler_task_plan_reason(
        task,
        due,
        task_supported=_data_annotation_task_supported,
        now_ts=time.time(),
    )


def _data_annotation_world_facts_summary(facts: dict[str, Any]) -> dict[str, Any]:
    return data_annotation_world_facts_summary(facts)


def _build_data_annotation_scheduler_plan() -> dict[str, Any]:
    return build_data_annotation_scheduler_plan(
        _read_data_annotation_scheduler_tasks(),
        _DATA_ANNOTATION_RUNTIME_RUNNER.status(),
        _read_data_annotation_world_facts(),
        _data_annotation_scheduler_state_path(),
        task_supported=_data_annotation_task_supported,
        task_due=_data_annotation_task_due,
        now_ts=time.time(),
    )


def _data_annotation_task_payload_with_meta(task: dict[str, Any]) -> dict[str, Any]:
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    return {
        **payload,
        "__scheduler_task_id": str(task.get("id") or ""),
        "__scheduler_interruptible": bool(task.get("interruptible", True)),
    }


def _data_annotation_scheduler_run_now_task(
    tasks: list[dict[str, Any]],
    task_id: str,
    payload_override: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return _core_data_annotation_scheduler_run_now_task(tasks, task_id, payload_override)


def _prepare_data_annotation_runtime_for_scheduler_task(task: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    status = _DATA_ANNOTATION_RUNTIME_RUNNER.status()
    if not status.get("running"):
        return None
    task_id = str(task.get("id") or "")
    task["last_result"] = "queued"
    _record_data_annotation_scheduler_task_fact(task, "queued")
    _write_data_annotation_scheduler_tasks(tasks)
    message = f"当前有任务运行，{task_id or task.get('label') or task.get('task_type')} 已排队"
    status.update({"message": message, "updated_at": time.time()})
    _persist_data_annotation_runtime_status(status)
    return status


def _start_data_annotation_runtime_task(entry: UserDevice, req: FanxiuDataAnnotationRuntimeTaskRequest) -> dict[str, Any]:
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _submit_data_annotation_manual_job(
        entry=entry,
        entry_id=entry_id,
        task_type=req.task_type,
        payload=req.payload,
    )


def _serialize_fanxiu_pseudocode_card(card: FanxiuPseudoCodeCard) -> dict[str, Any]:
    return {
        "id": card.id,
        "scope": card.scope,
        "title": card.title or "",
        "body": card.body or "",
        "enabled": bool(card.enabled),
        "order_index": int(card.order_index or 0),
        "created_at": float(card.created_at or 0),
        "updated_at": float(card.updated_at or 0),
    }


def _list_fanxiu_pseudocode_card_rows(session: Session, user_id: int) -> list[FanxiuPseudoCodeCard]:
    return session.exec(
        select(FanxiuPseudoCodeCard)
        .where(FanxiuPseudoCodeCard.user_id == user_id)
        .order_by(FanxiuPseudoCodeCard.order_index.asc(), FanxiuPseudoCodeCard.created_at.asc())
    ).all()


def _next_fanxiu_pseudocode_card_order(session: Session, user_id: int, scope: str) -> int:
    rows = session.exec(
        select(FanxiuPseudoCodeCard.order_index)
        .where(FanxiuPseudoCodeCard.user_id == user_id)
        .where(FanxiuPseudoCodeCard.scope == scope)
    ).all()
    return max((int(value or 0) for value in rows), default=-1) + 1


FANXIU_PSEUDOCODE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})#([A-Za-z0-9_\-\u4e00-\u9fff（）()《》【】「」]+)?")


def _extract_fanxiu_pseudocode_refs(*segments: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for segment in segments:
        for match in FANXIU_PSEUDOCODE_REF_RE.finditer(segment or ""):
            image_no = int(match.group(1))
            label = (match.group(2) or "").strip()
            key = (image_no, label)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "ref": f"{image_no}#{label}" if label else f"{image_no}#",
                    "image_no": image_no,
                    "filename": f"{image_no:04d}.jpg",
                    "label": label,
                }
            )
    return refs


def _read_game_window2_pre_label_for_entry(entry: UserDevice, filename: str) -> dict[str, Any]:
    if entry.mode == "local":
        return _screenshot_game_window2_service_pre_label(filename)
    return _remote_game_window2_screenshot_json(
        entry,
        "service-screenshot/pre-label",
        payload={"filename": filename},
        action="截图预标注",
    )


def _normalize_fanxiu_pseudocode_image_context(data: dict[str, Any], filename: str, image_no: int) -> dict[str, Any]:
    payload = data.get("payload") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        payload = {}
    size = payload.get("size") if isinstance(payload.get("size"), dict) else {}
    boxes: list[dict[str, Any]] = []
    raw_boxes = payload.get("boxes")
    if isinstance(raw_boxes, list):
        for index, raw_box in enumerate(raw_boxes, start=1):
            if not isinstance(raw_box, dict):
                continue
            try:
                x = int(raw_box.get("x", 0))
                y = int(raw_box.get("y", 0))
                w = int(raw_box.get("w", 0))
                h = int(raw_box.get("h", 0))
            except (TypeError, ValueError):
                continue
            boxes.append(
                {
                    "index": index,
                    "name": str(raw_box.get("name") or "").strip(),
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                    "xywh": [x, y, w, h],
                }
            )
    return {
        "image_no": image_no,
        "filename": filename,
        "pre_label_filename": str(data.get("filename") or f"{Path(filename).stem}_pre.json") if isinstance(data, dict) else f"{Path(filename).stem}_pre.json",
        "exists": bool(data.get("exists")) if isinstance(data, dict) else False,
        "size": {
            "width": int(size.get("width") or 0),
            "height": int(size.get("height") or 0),
        },
        "boxes": boxes,
    }


def _build_fanxiu_pseudocode_annotation_context(entry: UserDevice | None, card: FanxiuPseudoCodeCard) -> dict[str, Any]:
    refs = _extract_fanxiu_pseudocode_refs(card.title or "", card.body or "")
    image_map: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for ref in refs:
        filename = str(ref["filename"])
        image_no = int(ref["image_no"])
        if filename not in image_map:
            if entry is None:
                image_map[filename] = {
                    "image_no": image_no,
                    "filename": filename,
                    "exists": False,
                    "size": {"width": 0, "height": 0},
                    "boxes": [],
                    "error": "未选择设备，无法读取截图标注",
                }
                errors.append(f"{filename}: 未选择设备")
            else:
                try:
                    data = _read_game_window2_pre_label_for_entry(entry, filename)
                    image_map[filename] = _normalize_fanxiu_pseudocode_image_context(data, filename, image_no)
                except Exception as exc:
                    image_map[filename] = {
                        "image_no": image_no,
                        "filename": filename,
                        "exists": False,
                        "size": {"width": 0, "height": 0},
                        "boxes": [],
                        "error": str(exc),
                    }
                    errors.append(f"{filename}: {exc}")
        label = str(ref.get("label") or "").strip()
        if label:
            image_context = image_map[filename]
            matched_box = next((box for box in image_context.get("boxes", []) if box.get("name") == label), None)
            ref["matched_box"] = matched_box
            if matched_box is None:
                ref["error"] = f"{filename} 中没有标注框：{label}"

    return {
        "refs": refs,
        "images": list(image_map.values()),
        "errors": errors,
    }


def _serialize_fanxiu_pseudocode_card_for_compile(card: FanxiuPseudoCodeCard, entry: UserDevice | None) -> dict[str, Any]:
    payload = _serialize_fanxiu_pseudocode_card(card)
    payload["annotation_context"] = _build_fanxiu_pseudocode_annotation_context(entry, card)
    return payload


def _run_fanxiu_pseudocode_operation(action: str, operation) -> dict[str, Any]:
    try:
        return operation()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _visual_macro_run_key(user_id: int, entry_id: str, card_id: str) -> str:
    return f"{user_id}:{entry_id}:{card_id}"


@status_router.get("/game-window2/pseudocode-cards", response_model=FanxiuPseudoCodeCardListResponse)
def list_fanxiu_game_window2_pseudocode_cards(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    rows = _list_fanxiu_pseudocode_card_rows(session, current_user.id)
    return {"items": [_serialize_fanxiu_pseudocode_card(row) for row in rows]}


@status_router.post("/game-window2/pseudocode-cards", response_model=FanxiuPseudoCodeCardRead)
def create_fanxiu_game_window2_pseudocode_card(
    req: FanxiuPseudoCodeCardCreateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    now = time.time()
    card = FanxiuPseudoCodeCard(
        user_id=current_user.id,
        scope=req.scope,
        title=req.title,
        body=req.body,
        enabled=req.enabled,
        order_index=req.order_index if req.order_index is not None else _next_fanxiu_pseudocode_card_order(session, current_user.id, req.scope),
        created_at=now,
        updated_at=now,
    )
    session.add(card)
    session.commit()
    session.refresh(card)
    return _serialize_fanxiu_pseudocode_card(card)


@status_router.patch("/game-window2/pseudocode-cards/{card_id}", response_model=FanxiuPseudoCodeCardRead)
def update_fanxiu_game_window2_pseudocode_card(
    card_id: str,
    req: FanxiuPseudoCodeCardUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    card = session.get(FanxiuPseudoCodeCard, card_id)
    if not card or card.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="伪代码卡片不存在")
    updates = req.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(card, key, value)
    card.updated_at = time.time()
    session.add(card)
    session.commit()
    session.refresh(card)
    return _serialize_fanxiu_pseudocode_card(card)


@status_router.delete("/game-window2/pseudocode-cards/{card_id}")
def delete_fanxiu_game_window2_pseudocode_card(
    card_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    card = session.get(FanxiuPseudoCodeCard, card_id)
    if not card or card.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="伪代码卡片不存在")
    session.delete(card)
    session.commit()
    return {"ok": True, "id": card_id}


@status_router.post("/game-window2/pseudocode/compile", response_model=FanxiuPseudoCodeRunResponse)
def compile_fanxiu_game_window2_pseudocode(
    req: FanxiuPseudoCodeCompileRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    entry = _get_user_device_or_404(session, current_user, req.entry_id) if req.entry_id.strip() else None
    rows = _list_fanxiu_pseudocode_card_rows(session, current_user.id)
    cards = [_serialize_fanxiu_pseudocode_card_for_compile(row, entry) for row in rows]
    return _run_fanxiu_pseudocode_operation(
        "编译",
        lambda: compile_fanxiu_pseudocode(cards, model=req.model, timeout=req.timeout),
    )


@status_router.post("/game-window2/pseudocode/start", response_model=FanxiuPseudoCodeRunResponse)
def start_fanxiu_game_window2_pseudocode(
    req: FanxiuPseudoCodeStartRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    return _run_fanxiu_pseudocode_operation(
        "启动",
        lambda: start_fanxiu_pseudocode_script(timeout=req.timeout),
    )


@status_router.post("/game-window2/visual-script/run", response_model=FanxiuPseudoCodeRunResponse)
def run_fanxiu_game_window2_visual_script(
    req: FanxiuVisualScriptRunRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    rows = _list_fanxiu_pseudocode_card_rows(session, current_user.id)
    cards = [_serialize_fanxiu_pseudocode_card(row) for row in rows]
    base_payload = req.model_dump(
        exclude_none=True,
        exclude={"entry_id", "card_id", "timeout", "tick_interval"},
    )

    def run_match(payload: dict[str, Any]) -> dict[str, Any]:
        return _match_game_window2_service(payload) if entry.mode == "local" else _match_remote_game_window2(entry, payload)

    def run_click(payload: dict[str, Any]) -> dict[str, Any]:
        return _click_game_window2_service(payload) if entry.mode == "local" else _click_remote_game_window2(entry, payload)

    def run_drag(payload: dict[str, Any]) -> dict[str, Any]:
        return _drag_game_window2_service(payload) if entry.mode == "local" else _drag_remote_game_window2(entry, payload)

    def run_activate(payload: dict[str, Any]) -> dict[str, Any]:
        return (
            _activate_game_window2_service(payload)
            if entry.mode == "local"
            else _activate_remote_game_window2(entry, payload)
        )

    run_key = _visual_macro_run_key(current_user.id, req.entry_id, req.card_id)
    stop_event = begin_visual_macro_run(run_key)

    def run_operation() -> dict[str, Any]:
        try:
            activate_result = run_activate(
                {
                    "title": base_payload.get("title"),
                    "title_match": base_payload.get("title_match") or "contains",
                    "click_title": True,
                }
            )
            result = run_fanxiu_visual_script(
                cards,
                selected_card_id=req.card_id,
                base_payload=base_payload,
                callbacks=VisualMacroRuntimeCallbacks(match=run_match, click=run_click, drag=run_drag),
                timeout=req.timeout,
                tick_interval=req.tick_interval,
                stop_event=stop_event,
            )
            title = activate_result.get("window_title") or activate_result.get("title") or "目标窗口"
            result["log"] = f"{time.strftime('%H:%M:%S')} 激活窗口：{title}\n{result.get('log') or ''}".rstrip()
            return result
        finally:
            end_visual_macro_run(run_key, stop_event)

    return _run_fanxiu_pseudocode_operation(
        "执行",
        run_operation,
    )


@status_router.post("/game-window2/visual-script/stop")
def stop_fanxiu_game_window2_visual_script(
    req: FanxiuVisualScriptStopRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    _get_user_device_or_404(session, current_user, req.entry_id)
    stopped = stop_visual_macro_run(_visual_macro_run_key(current_user.id, req.entry_id, req.card_id))
    return {"ok": True, "stopped": stopped}


@status_router.post("/game-window2/stream-token", response_model=FanxiuGameWindow2StreamTokenResponse)
def create_fanxiu_game_window2_stream_token(
    req: FanxiuGameWindow2StreamTokenRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _get_user_device_or_404(session, current_user, req.entry_id)
    expires = timedelta(hours=FANXIU_GAME_WINDOW2_STREAM_TOKEN_EXPIRE_HOURS)
    token = create_access_token(
        {
            "sub": FANXIU_GAME_WINDOW2_STREAM_TOKEN_SCOPE,
            "scope": FANXIU_GAME_WINDOW2_STREAM_TOKEN_SCOPE,
            "username": current_user.username,
            "entry_id": req.entry_id,
        },
        expires_delta=expires,
    )
    return {
        "token": token,
        "expires_in_seconds": int(expires.total_seconds()),
    }


_GAME_WINDOW2_SERVICE_STATUS_CACHE_TTL = 10.0
_game_window2_service_status_cache_lock = threading.Lock()
_game_window2_service_status_cache: tuple[float, dict[str, Any]] | None = None


def _clone_game_window2_service_status(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data, ensure_ascii=False, default=str))


def _get_game_window2_service_status_cache() -> dict[str, Any] | None:
    now = time.monotonic()
    with _game_window2_service_status_cache_lock:
        if _game_window2_service_status_cache is None:
            return None
        cached_at, status = _game_window2_service_status_cache
        if now - cached_at > _GAME_WINDOW2_SERVICE_STATUS_CACHE_TTL:
            return None
        return _clone_game_window2_service_status(status)


def _set_game_window2_service_status_cache(status: dict[str, Any]) -> None:
    global _game_window2_service_status_cache
    with _game_window2_service_status_cache_lock:
        _game_window2_service_status_cache = (time.monotonic(), _clone_game_window2_service_status(status))


def _clear_game_window2_service_status_cache() -> None:
    global _game_window2_service_status_cache
    with _game_window2_service_status_cache_lock:
        _game_window2_service_status_cache = None


@status_router.get("/game-window2/service-status")
async def get_fanxiu_game_window2_service_status(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    cached = _get_game_window2_service_status_cache()
    if cached is not None:
        return cached
    status = await asyncio.to_thread(get_game_window_service_status)
    _set_game_window2_service_status_cache(status)
    return status


@status_router.post("/game-window2/service-start")
def start_fanxiu_game_window2_service(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _clear_game_window2_service_status_cache()
    try:
        result = start_game_window_service()
        service = result.get("service") if isinstance(result, dict) else None
        if isinstance(service, dict):
            _set_game_window2_service_status_cache(service)
        return result
    except GameWindowServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@status_router.get("/game-window2/stream")
def stream_fanxiu_game_window2(
    token: str = Query(...),
    title: Optional[str] = Query(None),
    title_match: str = Query("contains", pattern="^(contains|exact)$"),
    fps: float = Query(12.0, ge=1.0, le=30.0),
    quality: int = Query(82, ge=1, le=100),
    mode: str = Query("screen", pattern="^(auto|printwindow|screen)$"),
    area: str = Query("client", pattern="^(outer|client)$"),
    crop: Optional[str] = Query(None),
    trim_border: Optional[str] = Query(None),
    rotate: str = Query("0", pattern="^(0|90|180|270|ccw|cw|none)$"),
    fixed_width: int = Query(0, ge=0, le=4096),
    fixed_height: int = Query(0, ge=0, le=4096),
    adb_screencap: bool = Query(False),
    auto_dismiss_popup: bool = Query(False),
    popup_check_interval: float = Query(3.0, ge=1.0, le=30.0),
    session: Session = Depends(get_session),
):
    entry, _current_user = _decode_game_window2_stream_token(session, token)
    params = _game_window2_stream_params(
        title=title,
        title_match=title_match,
        fps=fps,
        quality=quality,
        mode=mode,
        area=area,
        crop=crop,
        trim_border=trim_border,
        rotate=rotate,
        fixed_width=fixed_width,
        fixed_height=fixed_height,
        auto_dismiss_popup=auto_dismiss_popup,
        popup_check_interval=popup_check_interval,
    )
    if adb_screencap:
        return StreamingResponse(
            stream_mumu_adb_screencap_mjpeg(fps=fps),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )
    if entry.mode == "local":
        return _stream_game_window2_service(params)
    return _stream_response_from_requests(_open_remote_game_window2_stream(entry, params))


@status_router.get("/game-window2/service-stream")
def stream_fanxiu_game_window2_service(
    title: Optional[str] = Query(None),
    title_match: str = Query("contains", pattern="^(contains|exact)$"),
    fps: float = Query(12.0, ge=1.0, le=30.0),
    quality: int = Query(82, ge=1, le=100),
    mode: str = Query("screen", pattern="^(auto|printwindow|screen)$"),
    area: str = Query("client", pattern="^(outer|client)$"),
    crop: Optional[str] = Query(None),
    trim_border: Optional[str] = Query(None),
    rotate: str = Query("0", pattern="^(0|90|180|270|ccw|cw|none)$"),
    fixed_width: int = Query(0, ge=0, le=4096),
    fixed_height: int = Query(0, ge=0, le=4096),
    auto_dismiss_popup: bool = Query(False),
    popup_check_interval: float = Query(3.0, ge=1.0, le=30.0),
    _token_device: Any = Depends(verify_api_token),
):
    params = _game_window2_stream_params(
        title=title,
        title_match=title_match,
        fps=fps,
        quality=quality,
        mode=mode,
        area=area,
        crop=crop,
        trim_border=trim_border,
        rotate=rotate,
        fixed_width=fixed_width,
        fixed_height=fixed_height,
        auto_dismiss_popup=auto_dismiss_popup,
        popup_check_interval=popup_check_interval,
    )
    return _stream_game_window2_service(params)


@status_router.post("/game-window2/input/click")
def click_fanxiu_game_window2(
    req: FanxiuGameWindow2ClickRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    payload = _game_window2_click_payload(req)
    if entry.mode == "local":
        return _click_game_window2_service(payload)
    return _click_remote_game_window2(entry, payload)


@status_router.post("/game-window2/input/activate")
def activate_fanxiu_game_window2(
    req: FanxiuGameWindow2ActivateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    payload = _game_window2_activate_payload(req)
    if entry.mode == "local":
        return _activate_game_window2_service(payload)
    return _activate_remote_game_window2(entry, payload)


@status_router.post("/game-window2/service-input/activate")
def activate_fanxiu_game_window2_service(
    req: FanxiuGameWindow2ServiceActivateRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _activate_game_window2_service(_game_window2_activate_payload(req))


@status_router.post("/game-window2/service-input/click")
def click_fanxiu_game_window2_service(
    req: FanxiuGameWindow2ServiceClickRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _click_game_window2_service(_game_window2_click_payload(req))


@status_router.post("/game-window2/input/drag")
def drag_fanxiu_game_window2(
    req: FanxiuGameWindow2DragRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    payload = _game_window2_drag_payload(req)
    if entry.mode == "local":
        return _drag_game_window2_service(payload)
    return _drag_remote_game_window2(entry, payload)


@status_router.post("/game-window2/service-input/drag")
def drag_fanxiu_game_window2_service(
    req: FanxiuGameWindow2ServiceDragRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _drag_game_window2_service(_game_window2_drag_payload(req))


@status_router.post("/game-window2/input/keyevent")
def keyevent_fanxiu_game_window2(
    req: FanxiuGameWindow2KeyeventRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    payload = _game_window2_keyevent_payload(req)
    if entry.mode == "local":
        return _keyevent_game_window2_service(payload)
    return _keyevent_remote_game_window2(entry, payload)


@status_router.post("/game-window2/service-input/keyevent")
def keyevent_fanxiu_game_window2_service(
    req: FanxiuGameWindow2ServiceKeyeventRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _keyevent_game_window2_service(_game_window2_keyevent_payload(req))


@status_router.post("/game-window2/input/text")
def text_fanxiu_game_window2(
    req: FanxiuGameWindow2TextRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    payload = _game_window2_text_payload(req)
    if entry.mode == "local":
        return _text_game_window2_service(payload)
    return _text_remote_game_window2(entry, payload)


@status_router.post("/game-window2/service-input/text")
def text_fanxiu_game_window2_service(
    req: FanxiuGameWindow2ServiceTextRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _text_game_window2_service(_game_window2_text_payload(req))


@status_router.post("/game-window2/screencap")
def screencap_fanxiu_game_window2(
    req: FanxiuGameWindow2ScreencapRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode == "local":
        return _screencap_game_window2_service(
            prefer_cached=req.prefer_cached,
            cached_only=req.cached_only,
            title=req.title,
            title_match=req.title_match,
            mode=req.mode,
            area=req.area,
            crop=req.crop,
            trim_border=req.trim_border,
            rotate=req.rotate,
            fixed_width=req.fixed_width,
            fixed_height=req.fixed_height,
        )
    return _remote_game_window2_screencap(entry)


@status_router.get("/game-window2/service-screencap")
def screencap_fanxiu_game_window2_service(
    _token_device: Any = Depends(verify_api_token),
):
    return _screencap_game_window2_service()


def _data_annotation_asset_tree_path(entry_id: str) -> Path:
    safe_entry_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", entry_id).strip("._") or "default"
    return _data_annotation_dir() / "asset-trees" / f"{safe_entry_id}.json"


@status_router.get("/data-annotation/asset-tree")
def get_fanxiu_data_annotation_asset_tree(
    entry_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _get_user_device_or_404(session, current_user, entry_id)
    path = _data_annotation_asset_tree_path(entry_id)
    if not path.is_file():
        return {"ok": True, "entry_id": entry_id, "exists": False, "tree": [], "updated_at": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = []
    tree = payload if isinstance(payload, list) else []
    return {
        "ok": True,
        "entry_id": entry_id,
        "exists": True,
        "tree": tree,
        "updated_at": path.stat().st_mtime,
    }


@status_router.put("/data-annotation/asset-tree")
def save_fanxiu_data_annotation_asset_tree(
    req: FanxiuDataAnnotationAssetTreeRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _get_user_device_or_404(session, current_user, req.entry_id)
    path = _data_annotation_asset_tree_path(req.entry_id)
    _write_data_annotation_json(path, req.tree)
    return {
        "ok": True,
        "entry_id": req.entry_id,
        "exists": True,
        "tree": req.tree,
        "updated_at": path.stat().st_mtime,
    }


@status_router.post("/game-window2/save-frame")
def save_fanxiu_game_window2_frame(
    req: FanxiuGameWindow2SaveFrameRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    payload = _game_window2_save_frame_payload(req)
    if entry.mode == "local":
        return _save_game_window2_service(payload)
    return _save_remote_game_window2_frame(entry, payload)


@status_router.post("/game-window2/service-save-frame")
def save_fanxiu_game_window2_frame_service(
    req: FanxiuGameWindow2ServiceSaveFrameRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _save_game_window2_service(_game_window2_save_frame_payload(req))


@status_router.post("/game-window2/burst/save")
def save_fanxiu_game_window2_burst_frame(
    req: FanxiuGameWindow2BurstFrameRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode != "local":
        raise HTTPException(status_code=400, detail="连拍缓存暂仅支持本机设备")
    return _save_burst_game_window2_service(_game_window2_save_frame_payload(req))


@status_router.post("/game-window2/service-burst/save")
def save_fanxiu_game_window2_burst_frame_service(
    req: FanxiuGameWindow2ServiceBurstFrameRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _save_burst_game_window2_service(_game_window2_save_frame_payload(req))


@status_router.post("/game-window2/burst/list")
def list_fanxiu_game_window2_burst_frames(
    req: FanxiuGameWindow2BurstListRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode != "local":
        raise HTTPException(status_code=400, detail="连拍缓存暂仅支持本机设备")
    return _list_burst_game_window2_service(req.model_dump(exclude_none=True, exclude={"entry_id"}))


@status_router.post("/game-window2/service-burst/list")
def list_fanxiu_game_window2_burst_frames_service(
    req: FanxiuGameWindow2ServiceBurstListRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _list_burst_game_window2_service(req.model_dump(exclude_none=True))


@status_router.get("/game-window2/burst/image")
def get_fanxiu_game_window2_burst_frame_image(
    entry_id: str = Query(...),
    filename: str = Query(...),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, entry_id)
    if entry.mode != "local":
        raise HTTPException(status_code=400, detail="连拍缓存暂仅支持本机设备")
    return _burst_game_window2_service_image(filename)


@status_router.get("/game-window2/service-burst/image")
def get_fanxiu_game_window2_burst_frame_image_service(
    filename: str = Query(...),
    _token_device: Any = Depends(verify_api_token),
):
    return _burst_game_window2_service_image(filename)


@status_router.post("/game-window2/burst/clear")
def clear_fanxiu_game_window2_burst_frames(
    req: FanxiuGameWindow2BurstClearRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode != "local":
        raise HTTPException(status_code=400, detail="连拍缓存暂仅支持本机设备")
    return _clear_burst_game_window2_service()


@status_router.post("/game-window2/service-burst/clear")
def clear_fanxiu_game_window2_burst_frames_service(
    _req: FanxiuGameWindow2ServiceBurstClearRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _clear_burst_game_window2_service()


@status_router.post("/game-window2/burst/import")
def import_fanxiu_game_window2_burst_frames(
    req: FanxiuGameWindow2BurstImportRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode != "local":
        raise HTTPException(status_code=400, detail="连拍缓存暂仅支持本机设备")
    return _import_burst_game_window2_service(req.filenames)


@status_router.post("/game-window2/service-burst/import")
def import_fanxiu_game_window2_burst_frames_service(
    req: FanxiuGameWindow2ServiceBurstImportRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _import_burst_game_window2_service(req.filenames)


@status_router.post("/game-window2/match")
def match_fanxiu_game_window2_screenshot_box(
    req: FanxiuGameWindow2MatchRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    payload = _game_window2_match_payload(req)
    if entry.mode == "local":
        return _match_game_window2_service(payload)
    return _match_remote_game_window2(entry, payload)


@status_router.post("/game-window2/service-match")
def match_fanxiu_game_window2_screenshot_box_service(
    req: FanxiuGameWindow2ServiceMatchRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _match_game_window2_service(_game_window2_match_payload(req))


@status_router.get("/game-window2/match/image")
def get_fanxiu_game_window2_match_image(
    entry_id: str = Query(...),
    filename: str = Query(...),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _match_game_window2_service_image(filename)
    return _remote_game_window2_match_image(entry, filename)


@status_router.get("/game-window2/service-match/image")
def get_fanxiu_game_window2_match_image_service(
    filename: str = Query(...),
    _token_device: Any = Depends(verify_api_token),
):
    return _match_game_window2_service_image(filename)


@status_router.get("/data-annotation/runtime/status", response_model=FanxiuDataAnnotationRuntimeStatus)
def get_fanxiu_data_annotation_runtime_status(
    entry_id: str = Query("", max_length=128),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if entry_id:
        entry = _get_user_device_or_404(session, current_user, entry_id)
        resolved_entry_id = str(getattr(entry, "entry_id", None) or entry_id)
        _DATA_ANNOTATION_RUNTIME_RUNNER.ensure_service(
            entry=entry,
            entry_id=resolved_entry_id,
            asset_tree_path=_data_annotation_asset_tree_path(resolved_entry_id),
        )
    return FanxiuDataAnnotationRuntimeStatus.model_validate(_data_annotation_runtime_status())


@status_router.get(
    "/data-annotation/runtime/service/status",
    response_model=FanxiuDataAnnotationRuntimeStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL))],
)
def get_fanxiu_data_annotation_runtime_service_status(
    entry_id: str = Query("", max_length=128),
    session: Session = Depends(get_session),
):
    if entry_id:
        entry = _get_service_user_device_or_404(session, entry_id)
        resolved_entry_id = str(getattr(entry, "entry_id", None) or entry_id)
        _DATA_ANNOTATION_RUNTIME_RUNNER.ensure_service(
            entry=entry,
            entry_id=resolved_entry_id,
            asset_tree_path=_data_annotation_asset_tree_path(resolved_entry_id),
        )
    return FanxiuDataAnnotationRuntimeStatus.model_validate(_data_annotation_runtime_status())


@status_router.post("/data-annotation/runtime/task/start", response_model=FanxiuDataAnnotationRuntimeStatus)
def start_fanxiu_data_annotation_runtime_task(
    req: FanxiuDataAnnotationRuntimeTaskRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    return FanxiuDataAnnotationRuntimeStatus.model_validate(_start_data_annotation_runtime_task(entry, req))


@status_router.post(
    "/data-annotation/runtime/service/task/start",
    response_model=FanxiuDataAnnotationRuntimeStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL))],
)
def start_fanxiu_data_annotation_runtime_service_task(
    req: FanxiuDataAnnotationRuntimeTaskRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    return FanxiuDataAnnotationRuntimeStatus.model_validate(_start_data_annotation_runtime_task(entry, req))


@status_router.post("/data-annotation/runtime/task/stop", response_model=FanxiuDataAnnotationRuntimeStatus)
def stop_fanxiu_data_annotation_runtime_task(
    req: FanxiuDataAnnotationRuntimeStopRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    status = _DATA_ANNOTATION_RUNTIME_RUNNER.stop_current_task(req.entry_id or "")
    _persist_data_annotation_runtime_status(status)
    return FanxiuDataAnnotationRuntimeStatus.model_validate(status)


@status_router.post(
    "/data-annotation/runtime/service/task/stop",
    response_model=FanxiuDataAnnotationRuntimeStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL))],
)
def stop_fanxiu_data_annotation_runtime_service_task(
    req: FanxiuDataAnnotationRuntimeStopRequest,
):
    status = _DATA_ANNOTATION_RUNTIME_RUNNER.stop_current_task(req.entry_id or "")
    _persist_data_annotation_runtime_status(status)
    return FanxiuDataAnnotationRuntimeStatus.model_validate(status)


@status_router.post("/data-annotation/runtime/guard/set", response_model=FanxiuDataAnnotationRuntimeStatus)
def set_fanxiu_data_annotation_runtime_guard(
    req: FanxiuDataAnnotationRuntimeGuardRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    status = _DATA_ANNOTATION_RUNTIME_RUNNER.set_guard(
        entry=entry,
        entry_id=entry_id,
        guard_id=req.guard_id,
        enabled=req.enabled,
        interval_seconds=req.interval_seconds,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
    )
    _persist_data_annotation_runtime_status(status)
    return FanxiuDataAnnotationRuntimeStatus.model_validate(status)


@status_router.post(
    "/data-annotation/runtime/service/guard/set",
    response_model=FanxiuDataAnnotationRuntimeStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL))],
)
def set_fanxiu_data_annotation_runtime_service_guard(
    req: FanxiuDataAnnotationRuntimeGuardRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    status = _DATA_ANNOTATION_RUNTIME_RUNNER.set_guard(
        entry=entry,
        entry_id=entry_id,
        guard_id=req.guard_id,
        enabled=req.enabled,
        interval_seconds=req.interval_seconds,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
    )
    _persist_data_annotation_runtime_status(status)
    return FanxiuDataAnnotationRuntimeStatus.model_validate(status)


@status_router.post("/data-annotation/runtime/task/tick", response_model=FanxiuDataAnnotationRuntimeStatus)
def tick_fanxiu_data_annotation_runtime_task(
    req: FanxiuDataAnnotationRuntimeTaskRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    task_type = req.task_type or "detect_scene"
    if task_type == "manual_tick":
        task_type = "detect_scene"
    status = _submit_data_annotation_manual_job(
        entry=entry,
        entry_id=entry_id,
        task_type=task_type,
        payload=req.payload,
        label="单步识别" if task_type == "detect_scene" else "",
    )
    return FanxiuDataAnnotationRuntimeStatus.model_validate(status)


@status_router.post(
    "/data-annotation/runtime/service/task/tick",
    response_model=FanxiuDataAnnotationRuntimeStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL))],
)
def tick_fanxiu_data_annotation_runtime_service_task(
    req: FanxiuDataAnnotationRuntimeTaskRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    task_type = req.task_type or "detect_scene"
    if task_type == "manual_tick":
        task_type = "detect_scene"
    status = _submit_data_annotation_manual_job(
        entry=entry,
        entry_id=entry_id,
        task_type=task_type,
        payload=req.payload,
        label="单步识别" if task_type == "detect_scene" else "",
    )
    return FanxiuDataAnnotationRuntimeStatus.model_validate(status)


@status_router.get("/data-annotation/runtime/logs", response_model=FanxiuDataAnnotationRuntimeLogResponse)
def get_fanxiu_data_annotation_runtime_logs(
    limit: int = Query(500, ge=1, le=2000),
    scope: str = Query("", max_length=64),
    item_id: str = Query("", max_length=128),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    status = _data_annotation_runtime_status()
    log_items = [item for item in (status.get("logs") or []) if isinstance(item, dict)]
    scope = str(scope or "").strip()
    item_id = str(item_id or "").strip()
    if scope:
        log_items = [item for item in log_items if str(item.get("scope") or "") == scope]
    if item_id:
        log_items = [item for item in log_items if str(item.get("item_id") or "") == item_id]
    entries = [
        FanxiuDataAnnotationRuntimeLogEntry(
            id=f"runtime-{index}",
            time=str(item.get("time") or ""),
            kind=str(item.get("kind") or ""),
            scope=str(item.get("scope") or ""),
            item_id=str(item.get("item_id") or ""),
            message=str(item.get("message") or ""),
            ts="",
        )
        for index, item in enumerate(log_items[-limit:])
    ]
    return FanxiuDataAnnotationRuntimeLogResponse(entries=entries, path=str(_data_annotation_runtime_state_path()))


@status_router.get("/data-annotation/runtime/world-facts", response_model=FanxiuDataAnnotationWorldFactsResponse)
def get_fanxiu_data_annotation_world_facts(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    return FanxiuDataAnnotationWorldFactsResponse(
        facts=_read_data_annotation_world_facts(),
        path=str(_data_annotation_world_facts_path()),
    )


@status_router.delete("/data-annotation/runtime/logs", response_model=FanxiuDataAnnotationRuntimeLogResponse)
def clear_fanxiu_data_annotation_runtime_logs(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    status = _DATA_ANNOTATION_RUNTIME_RUNNER.status()
    status["logs"] = []
    _DATA_ANNOTATION_RUNTIME_RUNNER.replace_logs([])
    _persist_data_annotation_runtime_status(status)
    return FanxiuDataAnnotationRuntimeLogResponse(entries=[], path=str(_data_annotation_runtime_state_path()))


@status_router.get("/data-annotation/scheduler/tasks", response_model=FanxiuDataAnnotationSchedulerTasksResponse)
def get_fanxiu_data_annotation_scheduler_tasks(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    return FanxiuDataAnnotationSchedulerTasksResponse(
        tasks=[
            FanxiuDataAnnotationSchedulerTaskItem.model_validate(_data_annotation_scheduler_task_view(item))
            for item in _read_data_annotation_scheduler_tasks()
        ],
        path=str(_data_annotation_scheduler_state_path()),
    )


@status_router.get("/data-annotation/scheduler/plan", response_model=FanxiuDataAnnotationSchedulerPlanResponse)
def get_fanxiu_data_annotation_scheduler_plan(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    return FanxiuDataAnnotationSchedulerPlanResponse.model_validate(_build_data_annotation_scheduler_plan())


@status_router.put("/data-annotation/scheduler/tasks", response_model=FanxiuDataAnnotationSchedulerTasksResponse)
def put_fanxiu_data_annotation_scheduler_tasks(
    tasks: list[FanxiuDataAnnotationSchedulerTaskItem],
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    payload = merge_data_annotation_scheduler_task_updates(
        _read_data_annotation_scheduler_tasks(),
        [item.model_dump() for item in tasks],
        now=datetime.now(),
    )
    _write_data_annotation_scheduler_tasks(payload)
    return FanxiuDataAnnotationSchedulerTasksResponse(
        tasks=[
            FanxiuDataAnnotationSchedulerTaskItem.model_validate(_data_annotation_scheduler_task_view(item))
            for item in _read_data_annotation_scheduler_tasks()
        ],
        path=str(_data_annotation_scheduler_state_path()),
    )


@status_router.post("/data-annotation/scheduler/task/run-now", response_model=FanxiuDataAnnotationRuntimeStatus)
def run_now_fanxiu_data_annotation_scheduler_task(
    req: FanxiuDataAnnotationSchedulerRunNowRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    tasks = _read_data_annotation_scheduler_tasks()
    state_task = next((item for item in tasks if item.get("id") == req.task_id), None)
    run_task = _data_annotation_scheduler_run_now_task(tasks, req.task_id, req.payload)
    if state_task is None or run_task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not _data_annotation_task_supported(run_task):
        raise HTTPException(status_code=400, detail="任务尚未纳入当前框架验收")
    blocked_status = _prepare_data_annotation_runtime_for_scheduler_task(state_task, tasks)
    if blocked_status is not None:
        return FanxiuDataAnnotationRuntimeStatus.model_validate(blocked_status)
    state_task["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state_task["last_result"] = "queued"
    _write_data_annotation_scheduler_tasks(tasks)
    status = _submit_data_annotation_manual_job(
        entry=entry,
        entry_id=entry_id,
        task_type=str(run_task.get("task_type") or ""),
        payload=_data_annotation_task_payload_with_meta(run_task),
        label=f"手动任务：{run_task.get('label') or run_task.get('id') or run_task.get('task_type')}",
        interruptible=bool(run_task.get("interruptible", True)),
    )
    return FanxiuDataAnnotationRuntimeStatus.model_validate(status)


@status_router.post(
    "/data-annotation/scheduler/service/task/run-now",
    response_model=FanxiuDataAnnotationRuntimeStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL))],
)
def run_now_fanxiu_data_annotation_scheduler_service_task(
    req: FanxiuDataAnnotationSchedulerRunNowRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    tasks = _read_data_annotation_scheduler_tasks()
    state_task = next((item for item in tasks if item.get("id") == req.task_id), None)
    run_task = _data_annotation_scheduler_run_now_task(tasks, req.task_id, req.payload)
    if state_task is None or run_task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not _data_annotation_task_supported(run_task):
        raise HTTPException(status_code=400, detail="任务尚未纳入当前框架验收")
    blocked_status = _prepare_data_annotation_runtime_for_scheduler_task(state_task, tasks)
    if blocked_status is not None:
        return FanxiuDataAnnotationRuntimeStatus.model_validate(blocked_status)
    state_task["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state_task["last_result"] = "queued"
    _write_data_annotation_scheduler_tasks(tasks)
    status = _submit_data_annotation_manual_job(
        entry=entry,
        entry_id=entry_id,
        task_type=str(run_task.get("task_type") or ""),
        payload=_data_annotation_task_payload_with_meta(run_task),
        label=f"手动任务：{run_task.get('label') or run_task.get('id') or run_task.get('task_type')}",
        interruptible=bool(run_task.get("interruptible", True)),
    )
    return FanxiuDataAnnotationRuntimeStatus.model_validate(status)


@status_router.post("/data-annotation/scheduler/run-due", response_model=FanxiuDataAnnotationRuntimeStatus)
def run_due_fanxiu_data_annotation_scheduler_tasks(
    req: FanxiuDataAnnotationSchedulerRunDueRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    asset_tree_path = _data_annotation_asset_tree_path(entry_id)
    _DATA_ANNOTATION_RUNTIME_RUNNER.ensure_service(entry=entry, entry_id=entry_id, asset_tree_path=asset_tree_path)
    tasks = _read_data_annotation_scheduler_tasks()
    due_tasks = sorted(
        [
            item
            for item in tasks
            if str(item.get("schedule_kind") or "") != "manual"
            and _data_annotation_task_due(item)
            and _data_annotation_task_supported(item)
        ],
        key=data_annotation_scheduler_order_key,
    )
    if not due_tasks:
        status = _data_annotation_runtime_status()
        status.update({"message": "没有可执行的到期任务", "updated_at": time.time()})
        _persist_data_annotation_runtime_status(status)
        return FanxiuDataAnnotationRuntimeStatus.model_validate(status)
    blocked_status = _prepare_data_annotation_runtime_for_scheduler_task(due_tasks[0], tasks)
    if blocked_status is not None:
        return FanxiuDataAnnotationRuntimeStatus.model_validate(blocked_status)
    _DATA_ANNOTATION_RUNTIME_RUNNER._service_wake_event.set()
    status = _DATA_ANNOTATION_RUNTIME_RUNNER.status()
    status.update({
        "entry_id": entry_id,
        "phase": "scheduler_due_queued",
        "message": f"已唤醒常驻行为树执行到期任务：{due_tasks[0].get('label') or due_tasks[0].get('id')}",
        "updated_at": time.time(),
    })
    _persist_data_annotation_runtime_status(status)
    return FanxiuDataAnnotationRuntimeStatus.model_validate(status)


@status_router.post(
    "/data-annotation/scheduler/service/run-due",
    response_model=FanxiuDataAnnotationRuntimeStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL))],
)
def run_due_fanxiu_data_annotation_scheduler_service_tasks(
    req: FanxiuDataAnnotationSchedulerRunDueRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    asset_tree_path = _data_annotation_asset_tree_path(entry_id)
    _DATA_ANNOTATION_RUNTIME_RUNNER.ensure_service(entry=entry, entry_id=entry_id, asset_tree_path=asset_tree_path)
    tasks = _read_data_annotation_scheduler_tasks()
    due_tasks = sorted(
        [
            item
            for item in tasks
            if str(item.get("schedule_kind") or "") != "manual"
            and _data_annotation_task_due(item)
            and _data_annotation_task_supported(item)
        ],
        key=data_annotation_scheduler_order_key,
    )
    if not due_tasks:
        status = _data_annotation_runtime_status()
        status.update({"message": "没有可执行的到期任务", "updated_at": time.time()})
        _persist_data_annotation_runtime_status(status)
        return FanxiuDataAnnotationRuntimeStatus.model_validate(status)
    blocked_status = _prepare_data_annotation_runtime_for_scheduler_task(due_tasks[0], tasks)
    if blocked_status is not None:
        return FanxiuDataAnnotationRuntimeStatus.model_validate(blocked_status)
    _DATA_ANNOTATION_RUNTIME_RUNNER._service_wake_event.set()
    status = _DATA_ANNOTATION_RUNTIME_RUNNER.status()
    status.update({
        "entry_id": entry_id,
        "phase": "scheduler_due_queued",
        "message": f"已唤醒常驻行为树执行到期任务：{due_tasks[0].get('label') or due_tasks[0].get('id')}",
        "updated_at": time.time(),
    })
    _persist_data_annotation_runtime_status(status)
    return FanxiuDataAnnotationRuntimeStatus.model_validate(status)


@status_router.post("/data-annotation/ocr-frame", response_model=FanxiuDataAnnotationOcrFrameResponse)
def recognize_fanxiu_data_annotation_ocr_frame(
    req: FanxiuDataAnnotationOcrFrameRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    try:
        return _recognize_data_annotation_ocr_frame(req.image_data_url)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@status_router.post("/data-annotation/macro/annotate", response_model=FanxiuDataAnnotationMacroAnnotateResponse)
def annotate_fanxiu_data_annotation_macro_shape(
    req: FanxiuDataAnnotationMacroAnnotateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    try:
        return _annotate_game_macro_shape_with_ai(req, current_user=current_user, session=session)
    except (AiAppConfigError, OllamaClientError, ValueError, RuntimeError) as exc:
        return FanxiuDataAnnotationMacroAnnotateResponse(
            ok=False,
            used_ai=False,
            box=_clamp_game_macro_box(req.fallback_box.model_dump(), req.fallback_box, req.frame_width, req.frame_height),
            confidence=0,
            label="",
            reason=str(exc),
            raw="",
        )


@status_router.post("/game-window2/screenshot/list")
def list_fanxiu_game_window2_screenshot(
    req: FanxiuGameWindow2ScreenshotListRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode == "local":
        return _screenshot_game_window2_service_list()
    return _remote_game_window2_screenshot_json(entry, "service-screenshot/list", action="截图列表")


@status_router.post("/game-window2/service-screenshot/list")
def list_fanxiu_game_window2_screenshot_service(
    _token_device: Any = Depends(verify_api_token),
):
    return _screenshot_game_window2_service_list()


@status_router.post("/game-window2/screenshot/delete")
def delete_fanxiu_game_window2_screenshot(
    req: FanxiuGameWindow2ScreenshotDeleteRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode == "local":
        return _delete_screenshot_game_window2_service_image(req.filename)
    return _remote_game_window2_screenshot_json(
        entry,
        "service-screenshot/delete",
        payload={"filename": req.filename},
        action="截图删除",
    )


@status_router.post("/game-window2/service-screenshot/delete")
def delete_fanxiu_game_window2_screenshot_service(
    req: FanxiuGameWindow2ServiceScreenshotDeleteRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _delete_screenshot_game_window2_service_image(req.filename)


@status_router.get("/game-window2/screenshot/image")
def get_fanxiu_game_window2_screenshot_image(
    entry_id: str = Query(...),
    filename: str = Query(...),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _screenshot_game_window2_service_image(filename)
    return _remote_game_window2_screenshot_image(entry, filename)


@status_router.get("/game-window2/service-screenshot/image")
def get_fanxiu_game_window2_screenshot_image_service(
    filename: str = Query(...),
    _token_device: Any = Depends(verify_api_token),
):
    return _screenshot_game_window2_service_image(filename)


@status_router.post("/game-window2/screenshot/pre-label")
def get_fanxiu_game_window2_screenshot_pre_label(
    req: FanxiuGameWindow2ScreenshotPreLabelRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode == "local":
        return _screenshot_game_window2_service_pre_label(req.filename)
    return _remote_game_window2_screenshot_json(
        entry,
        "service-screenshot/pre-label",
        payload={"filename": req.filename},
        action="截图预标注",
    )


@status_router.post("/game-window2/service-screenshot/pre-label")
def get_fanxiu_game_window2_screenshot_pre_label_service(
    req: FanxiuGameWindow2ServiceScreenshotPreLabelRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _screenshot_game_window2_service_pre_label(req.filename)


@status_router.put("/game-window2/screenshot/pre-label")
def save_fanxiu_game_window2_screenshot_pre_label(
    req: FanxiuGameWindow2ScreenshotPreLabelSaveRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode == "local":
        return _save_screenshot_game_window2_service_pre_label(req.filename, req.payload)
    return _remote_game_window2_screenshot_json(
        entry,
        "service-screenshot/pre-label",
        method="put",
        payload={"filename": req.filename, "payload": req.payload},
        action="截图预标注保存",
    )


@status_router.put("/game-window2/service-screenshot/pre-label")
def save_fanxiu_game_window2_screenshot_pre_label_service(
    req: FanxiuGameWindow2ServiceScreenshotPreLabelSaveRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _save_screenshot_game_window2_service_pre_label(req.filename, req.payload)


@inventory_router.post("/inventory/spirit-artifact-ranks/recognize", response_model=FanxiuSpiritArtifactRankRecognitionResponse)
def recognize_fanxiu_spirit_artifact_ranks(
    title: Optional[str] = Query(None),
    mode: str = Query("screen", pattern="^(auto|printwindow|screen)$"),
    area: str = Query("outer", pattern="^(outer|client)$"),
    crop: Optional[str] = Query(None),
    trim_border: Optional[str] = Query(None),
    rotate: str = Query("90", pattern="^(0|90|180|270|ccw|cw|none)$"),
    fixed_width: int = Query(0, ge=0, le=4096),
    fixed_height: int = Query(0, ge=0, le=4096),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)

    try:
        frame = capture_mumu_window_frame(
            title=title,
            mode=mode,
            area=area,
            crop=crop,
            trim_border=trim_border,
            rotate=rotate,
            fixed_width=fixed_width,
            fixed_height=fixed_height,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    temp_path: Path | None = None
    try:
        import cv2

        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
            temp_path = Path(temp_file.name)
        if not cv2.imwrite(str(temp_path), frame):
            raise HTTPException(status_code=500, detail="保存游戏窗口截图失败")

        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        payload = _build_spirit_artifact_rank_recognition(preview.get("document") or {}, frame)
        payload = _fill_missing_spirit_artifact_ranks_from_card_crops(payload, frame)
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return FanxiuSpiritArtifactRankRecognitionResponse.model_validate(payload)


@inventory_router.post("/inventory/spirit-artifact-attributes/recognize", response_model=FanxiuSpiritArtifactAttributeRecognitionResponse)
def recognize_fanxiu_spirit_artifact_attributes(
    title: Optional[str] = Query(None),
    mode: str = Query("screen", pattern="^(auto|printwindow|screen)$"),
    area: str = Query("outer", pattern="^(outer|client)$"),
    crop: Optional[str] = Query(None),
    trim_border: Optional[str] = Query(None),
    rotate: str = Query("90", pattern="^(0|90|180|270|ccw|cw|none)$"),
    fixed_width: int = Query(0, ge=0, le=4096),
    fixed_height: int = Query(0, ge=0, le=4096),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)

    try:
        frame = capture_mumu_window_frame(
            title=title,
            mode=mode,
            area=area,
            crop=crop,
            trim_border=trim_border,
            rotate=rotate,
            fixed_width=fixed_width,
            fixed_height=fixed_height,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    temp_path: Path | None = None
    try:
        import cv2

        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
            temp_path = Path(temp_file.name)
        if not cv2.imwrite(str(temp_path), frame):
            raise HTTPException(status_code=500, detail="保存游戏窗口截图失败")

        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        payload = _build_spirit_artifact_attribute_recognition(preview.get("document") or {}, frame)
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return FanxiuSpiritArtifactAttributeRecognitionResponse.model_validate(payload)


@inventory_router.post("/inventory/spirit-artifact-market/recognize", response_model=FanxiuSpiritArtifactMarketRecognitionResponse)
def recognize_fanxiu_spirit_artifact_market(
    title: Optional[str] = Query(None),
    mode: str = Query("screen", pattern="^(auto|printwindow|screen)$"),
    area: str = Query("outer", pattern="^(outer|client)$"),
    crop: Optional[str] = Query(None),
    trim_border: Optional[str] = Query(None),
    rotate: str = Query("90", pattern="^(0|90|180|270|ccw|cw|none)$"),
    fixed_width: int = Query(0, ge=0, le=4096),
    fixed_height: int = Query(0, ge=0, le=4096),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)

    try:
        frame = capture_mumu_window_frame(
            title=title,
            mode=mode,
            area=area,
            crop=crop,
            trim_border=trim_border,
            rotate=rotate,
            fixed_width=fixed_width,
            fixed_height=fixed_height,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    temp_path: Path | None = None
    try:
        import cv2

        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
            temp_path = Path(temp_file.name)
        if not cv2.imwrite(str(temp_path), frame):
            raise HTTPException(status_code=500, detail="保存游戏窗口截图失败")

        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        payload = _build_spirit_artifact_market_recognition(preview.get("document") or {}, frame)
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return FanxiuSpiritArtifactMarketRecognitionResponse.model_validate(payload)


@inventory_router.post(
    "/inventory/spirit-artifact-storage-bag/recognize",
    response_model=FanxiuSpiritArtifactStorageBagRecognitionResponse,
)
def recognize_fanxiu_spirit_artifact_storage_bag(
    title: Optional[str] = Query(None),
    mode: str = Query("screen", pattern="^(auto|printwindow|screen)$"),
    area: str = Query("outer", pattern="^(outer|client)$"),
    crop: Optional[str] = Query(None),
    trim_border: Optional[str] = Query(None),
    rotate: str = Query("90", pattern="^(0|90|180|270|ccw|cw|none)$"),
    fixed_width: int = Query(0, ge=0, le=4096),
    fixed_height: int = Query(0, ge=0, le=4096),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)

    try:
        frame = capture_mumu_window_frame(
            title=title,
            mode=mode,
            area=area,
            crop=crop,
            trim_border=trim_border,
            rotate=rotate,
            fixed_width=fixed_width,
            fixed_height=fixed_height,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    temp_path: Path | None = None
    try:
        import cv2

        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
            temp_path = Path(temp_file.name)
        if not cv2.imwrite(str(temp_path), frame):
            raise HTTPException(status_code=500, detail="保存游戏窗口截图失败")

        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        payload = _build_spirit_artifact_storage_bag_recognition(preview.get("document") or {}, frame)
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return FanxiuSpiritArtifactStorageBagRecognitionResponse.model_validate(payload)


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
                item["note_id"] = note_public_id(db_note)
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
                item["note_id"] = note_public_id(db_note)
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
                item["note_id"] = note_public_id(db_note)
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


@inventory_router.get("/inventory/spirit-artifact-hall", response_model=FanxiuSpiritArtifactHallSnapshot)
def get_fanxiu_spirit_artifact_hall():
    try:
        payload = load_spirit_artifact_hall()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FanxiuSpiritArtifactHallSnapshot.model_validate(payload)


@inventory_router.put("/inventory/spirit-artifact-hall", response_model=FanxiuSpiritArtifactHallSnapshot)
def update_fanxiu_spirit_artifact_hall(
    payload: FanxiuSpiritArtifactHallSnapshot,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        saved_payload = save_spirit_artifact_hall(payload.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修灵器仓库失败：{exc}") from exc
    return FanxiuSpiritArtifactHallSnapshot.model_validate(saved_payload)


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
            item["note_id"] = note_public_id(db_note)
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
        note_identity = allocate_new_note_identity(session)
        db_note = NoteNode(
            id=note_identity.primary_id,
            numeric_id=note_identity.numeric_id,
            legacy_id=note_identity.legacy_id,
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

    item["note_id"] = note_public_id(db_note)
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
        note_identity = allocate_new_note_identity(session)
        db_note = NoteNode(
            id=note_identity.primary_id,
            numeric_id=note_identity.numeric_id,
            legacy_id=note_identity.legacy_id,
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

    item["note_id"] = note_public_id(db_note)
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
        note_identity = allocate_new_note_identity(session)
        db_note = NoteNode(
            id=note_identity.primary_id,
            numeric_id=note_identity.numeric_id,
            legacy_id=note_identity.legacy_id,
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

    item["note_id"] = note_public_id(db_note)
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
        note_identity = allocate_new_note_identity(session)
        db_note = NoteNode(
            id=note_identity.primary_id,
            numeric_id=note_identity.numeric_id,
            legacy_id=note_identity.legacy_id,
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

    item["note_id"] = note_public_id(db_note)
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
        note_identity = allocate_new_note_identity(session)
        db_note = NoteNode(
            id=note_identity.primary_id,
            numeric_id=note_identity.numeric_id,
            legacy_id=note_identity.legacy_id,
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

