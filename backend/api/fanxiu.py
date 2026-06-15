import base64
import difflib
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
from backend.core.access.auth import (
    ALGORITHM,
    SECRET_KEY,
    create_access_token,
    get_current_active_user,
    get_optional_current_user_from_token,
    verify_api_token,
)
from backend.core.access.feature_access_guard import ensure_feature_access, require_feature_access_dependency
from backend.core.runtime.game_window_service import (
    GameWindowServiceError,
    get_game_window_service_status,
    open_game_window_service_stream,
    start_game_window_service,
)
from backend.core.access.service_tokens import SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL, require_service_scope
from backend.core.settings import get_settings
from backend.core.fanxiu.runtime.errors import FanxiuRuntimeError
from backend.core.notes.identity import allocate_new_note_identity
from backend.core.notes.refs import note_edge_ref, note_public_id, note_ref_aliases
from backend.db import engine, get_session
from backend.models import FanxiuMailRecord, FanxiuPseudoCodeCard, NoteEdge, NoteNode, User, UserDevice
from backend.schemas import NoteRead, NoteUpdate
from backend.core.fanxiu.runtime.mumu_control import (
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
from backend.core.fanxiu.game.window_actions import (
    click_game_window2_service as _core_click_game_window2_service,
    click_remote_game_window2 as _core_click_remote_game_window2,
    drag_game_window2_service as _core_drag_game_window2_service,
    drag_remote_game_window2 as _core_drag_remote_game_window2,
    extract_stream_error as _core_extract_stream_error,
    game_window2_desktop_title as _core_game_window2_desktop_title,
    keyevent_game_window2_service as _core_keyevent_game_window2_service,
    keyevent_remote_game_window2 as _core_keyevent_remote_game_window2,
    match_game_window2_service as _core_match_game_window2_service,
    match_remote_game_window2 as _core_match_remote_game_window2,
    normalize_game_window2_title as _core_normalize_game_window2_title,
    post_remote_game_window2_json as _core_post_remote_game_window2_json,
    remote_entry_base_url as _core_remote_entry_base_url,
    remote_entry_headers as _core_remote_entry_headers,
    remote_game_window2_screencap as _core_remote_game_window2_screencap,
    screencap_game_window2_service as _core_screencap_game_window2_service,
    text_game_window2_service as _core_text_game_window2_service,
    text_remote_game_window2 as _core_text_remote_game_window2,
)
from backend.core.fanxiu.game.pseudocode_runtime import compile_fanxiu_pseudocode, start_fanxiu_pseudocode_script
from backend.core.fanxiu.game.visual_macro_runtime import (
    VisualMacroRuntimeCallbacks,
    begin_visual_macro_run,
    end_visual_macro_run,
    run_fanxiu_visual_script,
    stop_visual_macro_run,
)
from backend.core.ai.app_config import (
    AiAppConfigError,
)
from backend.core.ai.chat import OllamaClientError
from backend.core.fanxiu.catalog.inventory import load_magic_treasure_hall, save_magic_treasure_hall
from backend.core.fanxiu.catalog.inventory import load_spirit_artifact_hall, save_spirit_artifact_hall
from backend.core.fanxiu.catalog.inventory import load_wardrobe_hall, save_wardrobe_hall
from backend.core.fanxiu.catalog.inventory import load_spirit_beast_hall, save_spirit_beast_hall
from backend.core.fanxiu.catalog.inventory import load_activity_list, save_activity_list
from backend.core.fanxiu.catalog.inventory import load_modao_invasion_exchange_list, save_modao_invasion_exchange_list
from backend.core.fanxiu.catalog.inventory import (
    load_shouyuan_exploration_exchange_list,
    save_shouyuan_exploration_exchange_list,
)
from backend.core.fanxiu.runtime.processes import match_fanxiu_process_fields, list_fanxiu_processes, terminate_fanxiu_processes
from backend.core.fanxiu.packet.capture import build_fanxiu_packet_capture_snapshot
from backend.core.fanxiu.runtime.android_proxy import fanxiu_android_proxy_service
from backend.core.fanxiu.packet.activity import fanxiu_packet_activity_service
from backend.core.fanxiu.packet.proxy import fanxiu_packet_proxy_service
from backend.core.fanxiu.runtime.capture_runtime import fanxiu_capture_runtime_service
from backend.core.fanxiu.packet.activity_sync import (
    get_fanxiu_activity_packet_schedule,
    sync_fanxiu_activity_packets,
)
from backend.core.fanxiu.packet.insights import (
    get_fanxiu_packet_runtime_insights,
    get_fanxiu_packet_storage_bag_snapshot,
    sync_fanxiu_packet_runtime_insights,
)
from backend.core.fanxiu.catalog.status_models import (
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
    FanxiuMailRecordUpdateRequest,
    FanxiuMailRecordUpdateResponse,
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
from backend.core.fanxiu.game.window_models import (
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
    FanxiuDataAnnotationRemoveBackgroundRequest,
    FanxiuDataAnnotationRemoveBackgroundResponse,
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
from backend.core.fanxiu.catalog.inventory_models import (
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
from backend.core.fanxiu.game.ocr_utils import (
    _extract_magic_treasure_ocr_line_entries,
    _extract_magic_treasure_ocr_lines,
    _extract_ocr_line_entries,
    _extract_shape_rectangle,
    _extract_shape_text,
    _sanitize_ocr_text,
)
from backend.core.fanxiu.catalog.formation_ocr import (
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
from backend.core.fanxiu.catalog.modao_shouyuan_ocr import (
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
from backend.core.fanxiu.packet.player_profile_store import (
    list_fanxiu_player_profile_records,
    list_latest_fanxiu_player_profile_records,
)
from backend.core.fanxiu.packet.tcp_flow import (
    decode_fanxiu_tcp_pcap,
    list_fanxiu_tcp_business_entries,
    list_fanxiu_tcp_captures,
    list_fanxiu_tcp_records,
)
from backend.core.fanxiu.mail.store import (
    ensure_fanxiu_mail_table,
    mark_fanxiu_mail_action,
    normalize_fanxiu_mail_time_text,
    normalize_fanxiu_mail_title,
    update_fanxiu_mail_desired_status,
)
from backend.core.fanxiu.mail.policy import (
    fanxiu_mail_action_policy_for_record,
    fanxiu_mail_action_policy_for_rewards,
    fanxiu_mail_visible_group_action_policy,
    fanxiu_mail_rewards_from_payload,
    fanxiu_mail_rewards_unresolved,
)
from backend.core.fanxiu.mail.packet_sync import (
    _mail_rewards_summary,
    _normalize_mail_rewards,
    sync_fanxiu_mail_packets,
    trace_fanxiu_mail_packet_gap,
)
from backend.core.fanxiu.packet.insight_worker import (
    fanxiu_packet_insight_worker,
    sync_fanxiu_capture_paths,
)
from backend.core.fanxiu.data_annotation.jobs import (
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
from backend.core.fanxiu.data_annotation import runtime_control as _runtime_control
from backend.core.fanxiu.data_annotation.models import (
    FanxiuDataAnnotationDoctorWatchEnsureResponse,
    FanxiuDataAnnotationDoctorWatchLatestResponse,
    FanxiuDataAnnotationRuntimeLogEntry,
    FanxiuDataAnnotationRuntimeLogResponse,
    FanxiuDataAnnotationRuntimeStatus,
    FanxiuDataAnnotationRuntimeTaskRequest,
    FanxiuDataAnnotationRuntimeStopRequest,
    FanxiuDataAnnotationRuntimeGuardGroupRequest,
    FanxiuDataAnnotationRuntimeGuardRequest,
    FanxiuDataAnnotationSchedulerTaskItem,
    FanxiuDataAnnotationSchedulerTasksResponse,
    FanxiuDataAnnotationSchedulerPlanItem,
    FanxiuDataAnnotationSchedulerPlanResponse,
    FanxiuDataAnnotationSchedulerRunDueRequest,
    FanxiuDataAnnotationSchedulerRunNowRequest,
    FanxiuDataAnnotationSchedulerSettingsRequest,
    FanxiuDataAnnotationWorldFactsResponse,
)
from backend.core.fanxiu.data_annotation.state import (
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
from backend.core.fanxiu.data_annotation.scheduler import (
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
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks as _default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.runtime import (
    DataAnnotationRuntimeContainer as _DataAnnotationRuntimeContainer,
    DataAnnotationRuntimeGroupSpec as _DataAnnotationRuntimeGroupSpec,
    DataAnnotationRuntimeNodeSpec as _DataAnnotationRuntimeNodeSpec,
)
from backend.core.fanxiu.runtime.behavior_tree import (
    DEFAULT_FANXIU_ENTRY_ID,
    acquire_fanxiu_job_group_isolation,
    create_fanxiu_runtime_runner,
    data_annotation_asset_tree_path as _core_data_annotation_asset_tree_path,
    fanxiu_data_annotation_dir as _core_data_annotation_dir,
    fanxiu_data_annotation_mail_scan_state_path as _core_mail_scan_state_path,
    fanxiu_data_annotation_manual_job_state_path as _core_manual_job_state_path,
    fanxiu_data_annotation_runtime_dir as _core_data_annotation_runtime_dir,
    fanxiu_data_annotation_runtime_logs as _core_data_annotation_runtime_logs,
    fanxiu_data_annotation_runtime_status as _core_data_annotation_runtime_status,
    fanxiu_data_annotation_runtime_state_path as _core_runtime_state_path,
    fanxiu_data_annotation_scheduler_settings_path as _core_scheduler_settings_path,
    fanxiu_data_annotation_scheduler_state_path as _core_scheduler_state_path,
    fanxiu_data_annotation_world_facts_path as _core_world_facts_path,
    fanxiu_job_group_isolated,
    fanxiu_job_group_isolation_path as _core_job_group_isolation_path,
    fanxiu_runtime_runner_wake,
    clear_fanxiu_data_annotation_runtime_logs as _core_clear_data_annotation_runtime_logs,
    release_fanxiu_job_group_isolation,
    register_fanxiu_runtime_runner,
    resolve_fanxiu_entry,
)
from backend.core.fanxiu.game.macro_annotation import (
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
from backend.core.fanxiu.data_annotation.rembg import remove_fanxiu_data_annotation_background
from backend.core.fanxiu.runtime.behavior_tree_service import (
    get_behavior_tree_status,
    start_behavior_tree_service,
    stop_behavior_tree_service,
)
from backend.core.runtime.local_script_processes import list_local_script_processes
from backend.core.notes.access import note_to_response_dict
from backend.core.notes.semantics import (
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
from backend.core.ocr.preview import OcrPreviewError, run_paddle_ocr_preview

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


def _fanxiu_mail_record_has_display_payload(row: FanxiuMailRecord) -> bool:
    payload = row.payload or {}
    content = payload.get("mail_content_text")
    if isinstance(content, str) and content.strip():
        return True
    rewards = payload.get("mail_rewards")
    if isinstance(rewards, list) and rewards:
        return True
    packet = payload.get("packet")
    if isinstance(packet, dict):
        packet_content = packet.get("mail_content_text")
        if isinstance(packet_content, str) and packet_content.strip():
            return True
        packet_rewards = packet.get("mail_rewards")
        if isinstance(packet_rewards, list) and packet_rewards:
            return True
    return False


def _fanxiu_mail_reward_existing_index(rewards: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(rewards, list):
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for reward in rewards:
        if not isinstance(reward, dict):
            continue
        item_id = str(reward.get("item_id") or reward.get("id") or "").strip()
        if item_id and item_id not in indexed:
            indexed[item_id] = reward
    return indexed


def _fanxiu_mail_enrich_recomputed_rewards(
    recomputed: list[dict[str, Any]],
    existing_rewards: Any,
) -> list[dict[str, Any]]:
    existing_by_id = _fanxiu_mail_reward_existing_index(existing_rewards)
    if not existing_by_id:
        return recomputed
    enriched: list[dict[str, Any]] = []
    for reward in recomputed:
        item_id = str(reward.get("item_id") or "").strip()
        existing = existing_by_id.get(item_id) or {}
        merged = dict(reward)
        for key in ("item_name", "item_type", "quality", "icon", "small_icon", "description", "name_source"):
            if not merged.get(key) and existing.get(key):
                merged[key] = existing[key]
        enriched.append(merged)
    return enriched


def _fanxiu_mail_record_dump_for_response(row: FanxiuMailRecord) -> dict[str, Any]:
    payload = row.payload or {}
    if not isinstance(payload, dict):
        payload = {}
    packet = payload.get("packet") if isinstance(payload.get("packet"), dict) else {}
    mail_vo = payload.get("mailVo") if isinstance(payload.get("mailVo"), dict) else packet.get("mailVo")
    existing_rewards = payload.get("mail_rewards")
    if not isinstance(existing_rewards, list):
        existing_rewards = packet.get("mail_rewards")
    rewards = existing_rewards if isinstance(existing_rewards, list) else []
    if not rewards and isinstance(mail_vo, dict):
        recomputed_rewards = _normalize_mail_rewards(mail_vo)
        if recomputed_rewards:
            rewards = _fanxiu_mail_enrich_recomputed_rewards(recomputed_rewards, rewards)
    direct_content = payload.get("mail_content_text")
    packet_content = packet.get("mail_content_text") if isinstance(packet, dict) else ""
    content_text = direct_content if isinstance(direct_content, str) else packet_content
    response_payload: dict[str, Any] = {}
    if isinstance(content_text, str) and content_text.strip():
        response_payload["mail_content_text"] = content_text
    if rewards:
        response_payload["mail_rewards"] = rewards
        response_payload["mail_rewards_summary"] = _mail_rewards_summary(rewards)
    for key in (
        "mail_rewards_unresolved",
        "mail_rewards_unresolved_reason",
        "has_attachment_hint",
        "orphan_action_status",
    ):
        if key in payload:
            response_payload[key] = payload.get(key)
    evidence = row.evidence or {}
    if not isinstance(evidence, dict):
        evidence = {}
    response_evidence = {
        key: evidence.get(key)
        for key in (
            "orphan_action",
            "visible_orphan_backfill",
            "has_attachment_hint",
            "pcap_name",
            "orphan_action_reason",
        )
        if key in evidence
    }
    return {
        "id": row.id,
        "mail_key": row.mail_key,
        "mail_id": row.mail_id,
        "title": row.title,
        "normalized_title": row.normalized_title,
        "mail_type": row.mail_type,
        "create_time_text": row.create_time_text,
        "create_time_ms": row.create_time_ms,
        "source": row.source,
        "status": row.status,
        "locked": row.locked,
        "action_policy": row.action_policy,
        "last_action_error": row.last_action_error,
        "seen_count": row.seen_count,
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
        "last_seen_capture_at": row.last_seen_capture_at,
        "payload": response_payload,
        "evidence": response_evidence,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }

@status_router.get("/mail-records", response_model=FanxiuMailRecordListResponse)
def list_fanxiu_mail_records(
    limit: int = Query(2000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    status: str = Query(""),
    action_policy: str = Query(""),
    source: str = Query("packet_evidence"),
    include_empty_actions: bool = Query(False),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    ensure_fanxiu_mail_table()
    stmt = select(FanxiuMailRecord)
    status_text = status.strip() if isinstance(status, str) else ""
    action_policy_text = action_policy.strip() if isinstance(action_policy, str) else ""
    source_text = source.strip().lower() if isinstance(source, str) else "packet_evidence"
    if status_text:
        stmt = stmt.where(FanxiuMailRecord.status == status_text)
    if action_policy_text:
        stmt = stmt.where(FanxiuMailRecord.action_policy == action_policy_text)
    if source_text in {"packet_evidence", "packet+orphan", "packet_orphan"}:
        stmt = stmt.where(FanxiuMailRecord.source.in_(("packet", "packet_orphan_action")))
    elif source_text and source_text != "all":
        stmt = stmt.where(FanxiuMailRecord.source == source_text)
    stmt = stmt.order_by(FanxiuMailRecord.last_seen_at.desc(), FanxiuMailRecord.updated_at.desc())
    rows = session.exec(stmt).all()
    if include_empty_actions is not True:
        rows = [row for row in rows if _fanxiu_mail_record_has_display_payload(row)]
    rows = sorted(rows, key=_fanxiu_mail_record_sort_key, reverse=True)
    total_count = len(rows)
    rows = rows[offset:offset + limit]
    records = [_fanxiu_mail_record_dump_for_response(row) for row in rows]
    payload = {
        "ok": True,
        "count": len(records),
        "total": total_count,
        "offset": offset,
        "limit": limit,
        "records": records,
    }
    response = Response(
        content=json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        media_type="application/json",
    )
    for key, value in payload.items():
        setattr(response, key, value)
    return response


@status_router.patch("/mail-records/{mail_key}", response_model=FanxiuMailRecordUpdateResponse)
def update_fanxiu_mail_record_status(
    mail_key: str,
    payload: FanxiuMailRecordUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        record = update_fanxiu_mail_desired_status(session, mail_key, desired_status=payload.status)
    except ValueError:
        raise HTTPException(status_code=400, detail="邮件状态只能是：锁定、留存、可领")
    if record is None:
        raise HTTPException(status_code=404, detail="邮件记录不存在")
    session.commit()
    session.refresh(record)
    return FanxiuMailRecordUpdateResponse(ok=True, record=_fanxiu_mail_record_dump_for_response(record))


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
    return _core_remote_entry_base_url(entry)


def _remote_entry_headers(entry: UserDevice) -> dict[str, str]:
    return _core_remote_entry_headers(entry)


def _normalize_game_window2_title(title: Optional[str]) -> Optional[str]:
    return _core_normalize_game_window2_title(title)


def _game_window2_desktop_title(title: Optional[str]) -> str:
    return _core_game_window2_desktop_title(title)


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
    return _core_extract_stream_error(response)


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




def _click_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    return _core_click_game_window2_service(payload)


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
    return _core_drag_game_window2_service(payload)


def _keyevent_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    return _core_keyevent_game_window2_service(payload)


def _text_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    return _core_text_game_window2_service(payload)


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
    return _core_screencap_game_window2_service(
        prefer_cached=prefer_cached,
        cached_only=cached_only,
        allow_window_fallback=allow_window_fallback,
        title=title,
        title_match=title_match,
        mode=mode,
        area=area,
        crop=crop,
        trim_border=trim_border,
        rotate=rotate,
        fixed_width=fixed_width,
        fixed_height=fixed_height,
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
    return _core_match_game_window2_service(payload)


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
    return _core_click_remote_game_window2(entry, payload)


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
    return _core_drag_remote_game_window2(entry, payload)


def _post_remote_game_window2_json(entry: UserDevice, service_path: str, payload: dict[str, Any], action: str) -> dict[str, Any]:
    return _core_post_remote_game_window2_json(entry, service_path, payload, action)


def _keyevent_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return _core_keyevent_remote_game_window2(entry, payload)


def _text_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return _core_text_remote_game_window2(entry, payload)


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
    return _core_match_remote_game_window2(entry, payload)


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
    return _core_remote_game_window2_screencap(entry)


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


class _FanxiuRuntimeRunnerProxy:
    def __init__(self) -> None:
        self._runner: Any | None = None

    def resolve(self) -> Any:
        if self._runner is None:
            self._runner = create_fanxiu_runtime_runner()
        return self._runner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.resolve(), name)


_DATA_ANNOTATION_RUNTIME_RUNNER: Any = _FanxiuRuntimeRunnerProxy()


def __getattr__(name: str) -> Any:
    if name == "_DataAnnotationRuntimeRunner":
        from backend.core.fanxiu.data_annotation.runtime_runner import DataAnnotationRuntimeRunner

        return DataAnnotationRuntimeRunner
    raise AttributeError(name)


def _sync_data_annotation_runtime_runner_to_core() -> None:
    register_fanxiu_runtime_runner(_DATA_ANNOTATION_RUNTIME_RUNNER)


def _raise_fanxiu_runtime_http_error(exc: FanxiuRuntimeError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _data_annotation_dir() -> Path:
    return _core_data_annotation_dir()


def _data_annotation_runtime_dir() -> Path:
    return _core_data_annotation_runtime_dir()


def _data_annotation_runtime_state_path() -> Path:
    return _core_runtime_state_path()


def _data_annotation_world_facts_path() -> Path:
    return _core_world_facts_path()


def _data_annotation_scheduler_state_path() -> Path:
    return _core_scheduler_state_path()


def _data_annotation_scheduler_settings_path() -> Path:
    return _core_scheduler_settings_path()


def _data_annotation_manual_job_state_path() -> Path:
    return _core_manual_job_state_path()


def _data_annotation_job_group_isolation_path() -> Path:
    return _core_job_group_isolation_path()


def _data_annotation_mail_scan_state_path() -> Path:
    return _core_mail_scan_state_path()


def _read_data_annotation_world_facts() -> dict[str, Any]:
    return _runtime_control.read_world_facts(_data_annotation_world_facts_path())


def _write_data_annotation_world_facts(facts: dict[str, Any]) -> None:
    _runtime_control.write_world_facts(facts, _data_annotation_world_facts_path())


def _record_data_annotation_scheduler_task_fact(task: dict[str, Any], result: str) -> None:
    _runtime_control.record_scheduler_task_fact(task, result, world_facts_path=_data_annotation_world_facts_path())


def _persist_data_annotation_runtime_status(status: dict[str, Any]) -> None:
    _runtime_control.persist_runtime_status(
        status,
        runtime_state_path=_data_annotation_runtime_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )


def _read_data_annotation_runtime_status() -> dict[str, Any]:
    return _runtime_control.read_runtime_status(_data_annotation_runtime_state_path())


def _is_data_annotation_runtime_live_empty(status: dict[str, Any]) -> bool:
    return is_data_annotation_runtime_live_empty(status)


def _append_data_annotation_runtime_log_once(status: dict[str, Any], kind: str, message: str) -> None:
    _runtime_control.append_runtime_log_once(status, kind, message)


def _normalize_data_annotation_runtime_guard_items(status: dict[str, Any]) -> None:
    _sync_data_annotation_runtime_runner_to_core()
    _runtime_control.normalize_runtime_guard_items(status)


def _repair_orphaned_data_annotation_scheduler_runs(tasks: list[dict[str, Any]]) -> bool:
    _sync_data_annotation_runtime_runner_to_core()
    return _runtime_control.repair_orphaned_scheduler_runs(
        tasks,
        manual_job_path=_data_annotation_manual_job_state_path(),
    )


def _data_annotation_runtime_status() -> dict[str, Any]:
    _sync_data_annotation_runtime_runner_to_core()
    return _core_data_annotation_runtime_status(
        runtime_state_path=_data_annotation_runtime_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )


def _read_data_annotation_scheduler_tasks() -> list[dict[str, Any]]:
    return _runtime_control.read_scheduler_tasks(
        scheduler_state_path=_data_annotation_scheduler_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
        manual_job_path=_data_annotation_manual_job_state_path(),
        now=datetime.now(),
    )


def _write_data_annotation_scheduler_tasks(tasks: list[dict[str, Any]]) -> None:
    _runtime_control.write_scheduler_tasks(tasks, scheduler_state_path=_data_annotation_scheduler_state_path())


def _read_data_annotation_manual_jobs() -> list[dict[str, Any]]:
    return _runtime_control.read_manual_jobs(_data_annotation_manual_job_state_path())


def _write_data_annotation_manual_jobs(jobs: list[dict[str, Any]]) -> None:
    _runtime_control.write_manual_jobs(jobs, _data_annotation_manual_job_state_path())


def _requeue_running_data_annotation_manual_jobs() -> int:
    return _runtime_control.requeue_running_manual_jobs(_data_annotation_manual_job_state_path())


def _remove_data_annotation_manual_job(job_id: str) -> None:
    _runtime_control.remove_manual_job(job_id, _data_annotation_manual_job_state_path())


def _enqueue_data_annotation_manual_job(
    task_type: str,
    payload: dict[str, Any] | None = None,
    *,
    label: str = "",
    interruptible: bool | None = None,
) -> dict[str, Any]:
    return _runtime_control.enqueue_manual_job(
        task_type,
        payload,
        label=label,
        interruptible=interruptible,
        manual_job_path=_data_annotation_manual_job_state_path(),
    )


def _queue_data_annotation_manual_job_status(
    *,
    entry: UserDevice,
    entry_id: str,
    task_type: str,
    payload: dict[str, Any] | None = None,
    label: str = "",
    interruptible: bool | None = None,
) -> dict[str, Any]:
    _sync_data_annotation_runtime_runner_to_core()
    return _runtime_control.queue_manual_job_status(
        entry=entry,
        entry_id=entry_id,
        task_type=task_type,
        payload=payload,
        label=label,
        interruptible=interruptible,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
        manual_job_path=_data_annotation_manual_job_state_path(),
        runtime_state_path=_data_annotation_runtime_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )


def _submit_data_annotation_manual_job(
    *,
    entry: UserDevice,
    entry_id: str,
    task_type: str,
    payload: dict[str, Any] | None = None,
    label: str = "",
    interruptible: bool | None = None,
) -> dict[str, Any]:
    _sync_data_annotation_runtime_runner_to_core()
    try:
        return _runtime_control.submit_manual_job(
            entry=entry,
            entry_id=entry_id,
            task_type=task_type,
            payload=payload,
            label=label,
            interruptible=interruptible,
            asset_tree_path=_data_annotation_asset_tree_path(entry_id),
            manual_job_path=_data_annotation_manual_job_state_path(),
            runtime_state_path=_data_annotation_runtime_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _pop_next_data_annotation_manual_job() -> dict[str, Any] | None:
    return _runtime_control.pop_next_manual_job(_data_annotation_manual_job_state_path())


def _start_next_data_annotation_manual_job_if_idle(entry: UserDevice, entry_id: str) -> dict[str, Any] | None:
    _sync_data_annotation_runtime_runner_to_core()
    return _runtime_control.start_next_manual_job_if_idle(
        entry=entry,
        entry_id=entry_id,
        manual_job_path=_data_annotation_manual_job_state_path(),
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
    )


def _next_data_annotation_scheduler_time(task: dict[str, Any], now: datetime | None = None) -> str | None:
    return _runtime_control.next_scheduler_time(task, now)


def _sync_data_annotation_scheduler_tasks_from_world_facts(tasks: list[dict[str, Any]]) -> bool:
    return _runtime_control.sync_scheduler_tasks_from_world_facts(
        tasks,
        world_facts_path=_data_annotation_world_facts_path(),
        now=datetime.now(),
    )


def _data_annotation_task_supported(task: dict[str, Any]) -> bool:
    return _runtime_control.task_supported(task)


def _data_annotation_scheduler_task_view(task: dict[str, Any]) -> dict[str, Any]:
    return _runtime_control.scheduler_task_view(task)


def _data_annotation_scheduler_task_plan_reason(task: dict[str, Any], due: bool) -> str:
    return _runtime_control.scheduler_task_plan_reason(task, due)


def _data_annotation_world_facts_summary(facts: dict[str, Any]) -> dict[str, Any]:
    return _runtime_control.world_facts_summary(facts)


def _build_data_annotation_scheduler_plan() -> dict[str, Any]:
    _sync_data_annotation_runtime_runner_to_core()
    entry_id = DEFAULT_FANXIU_ENTRY_ID
    try:
        entry = resolve_fanxiu_entry(entry_id)
    except Exception:
        entry = None
    return _runtime_control.build_scheduler_plan(
        entry=entry,
        entry_id=entry_id,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
        scheduler_state_path=_data_annotation_scheduler_state_path(),
        scheduler_settings_path=_data_annotation_scheduler_settings_path(),
        world_facts_path=_data_annotation_world_facts_path(),
        manual_job_path=_data_annotation_manual_job_state_path(),
    )


def _data_annotation_task_payload_with_meta(task: dict[str, Any]) -> dict[str, Any]:
    return _runtime_control.task_payload_with_meta(task)


def _data_annotation_scheduler_run_now_task(
    tasks: list[dict[str, Any]],
    task_id: str,
    payload_override: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return _core_data_annotation_scheduler_run_now_task(tasks, task_id, payload_override)


def _prepare_data_annotation_runtime_for_scheduler_task(task: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    _sync_data_annotation_runtime_runner_to_core()
    return _runtime_control.prepare_runtime_for_scheduler_task(
        task,
        tasks,
        scheduler_state_path=_data_annotation_scheduler_state_path(),
        runtime_state_path=_data_annotation_runtime_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )


def _start_data_annotation_runtime_task(entry: UserDevice, req: FanxiuDataAnnotationRuntimeTaskRequest) -> dict[str, Any]:
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    _sync_data_annotation_runtime_runner_to_core()
    try:
        return _runtime_control.start_runtime_task(
            entry=entry,
            entry_id=entry_id,
            task_type=req.task_type,
            payload=req.payload,
            asset_tree_path=_data_annotation_asset_tree_path(entry_id),
            manual_job_path=_data_annotation_manual_job_state_path(),
            runtime_state_path=_data_annotation_runtime_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
        )
    except FanxiuRuntimeError as exc:
        _raise_fanxiu_runtime_http_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    return _core_data_annotation_asset_tree_path(entry_id)


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
        _sync_data_annotation_runtime_runner_to_core()
        _runtime_control.ensure_runtime_service(
            entry=entry,
            entry_id=resolved_entry_id,
            asset_tree_path=_data_annotation_asset_tree_path(resolved_entry_id),
            runtime_state_path=_data_annotation_runtime_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
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
        _sync_data_annotation_runtime_runner_to_core()
        _runtime_control.ensure_runtime_service(
            entry=entry,
            entry_id=resolved_entry_id,
            asset_tree_path=_data_annotation_asset_tree_path(resolved_entry_id),
            runtime_state_path=_data_annotation_runtime_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
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
    _sync_data_annotation_runtime_runner_to_core()
    status = _runtime_control.stop_current_task(
        req.entry_id or "",
        runtime_state_path=_data_annotation_runtime_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    return FanxiuDataAnnotationRuntimeStatus.model_validate(status)


@status_router.post(
    "/data-annotation/runtime/service/task/stop",
    response_model=FanxiuDataAnnotationRuntimeStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL))],
)
def stop_fanxiu_data_annotation_runtime_service_task(
    req: FanxiuDataAnnotationRuntimeStopRequest,
):
    _sync_data_annotation_runtime_runner_to_core()
    status = _runtime_control.stop_current_task(
        req.entry_id or "",
        runtime_state_path=_data_annotation_runtime_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
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
    _sync_data_annotation_runtime_runner_to_core()
    status = _runtime_control.set_runtime_guard(
        entry=entry,
        entry_id=entry_id,
        guard_id=req.guard_id,
        enabled=req.enabled,
        interval_seconds=req.interval_seconds,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
        runtime_state_path=_data_annotation_runtime_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    return FanxiuDataAnnotationRuntimeStatus.model_validate(status)


@status_router.post("/data-annotation/runtime/guard/group/set", response_model=FanxiuDataAnnotationRuntimeStatus)
def set_fanxiu_data_annotation_runtime_guard_group(
    req: FanxiuDataAnnotationRuntimeGuardGroupRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    _sync_data_annotation_runtime_runner_to_core()
    status = _runtime_control.set_runtime_guard_group_enabled(
        entry=entry,
        entry_id=entry_id,
        enabled=req.enabled,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
        runtime_state_path=_data_annotation_runtime_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
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
    _sync_data_annotation_runtime_runner_to_core()
    status = _runtime_control.set_runtime_guard(
        entry=entry,
        entry_id=entry_id,
        guard_id=req.guard_id,
        enabled=req.enabled,
        interval_seconds=req.interval_seconds,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
        runtime_state_path=_data_annotation_runtime_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    return FanxiuDataAnnotationRuntimeStatus.model_validate(status)


@status_router.post(
    "/data-annotation/runtime/service/guard/group/set",
    response_model=FanxiuDataAnnotationRuntimeStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL))],
)
def set_fanxiu_data_annotation_runtime_service_guard_group(
    req: FanxiuDataAnnotationRuntimeGuardGroupRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    _sync_data_annotation_runtime_runner_to_core()
    status = _runtime_control.set_runtime_guard_group_enabled(
        entry=entry,
        entry_id=entry_id,
        enabled=req.enabled,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
        runtime_state_path=_data_annotation_runtime_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
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
    _sync_data_annotation_runtime_runner_to_core()
    try:
        status = _runtime_control.submit_tick_task(
            entry=entry,
            entry_id=entry_id,
            task_type=req.task_type,
            payload=req.payload,
            asset_tree_path=_data_annotation_asset_tree_path(entry_id),
            manual_job_path=_data_annotation_manual_job_state_path(),
            runtime_state_path=_data_annotation_runtime_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    _sync_data_annotation_runtime_runner_to_core()
    try:
        status = _runtime_control.submit_tick_task(
            entry=entry,
            entry_id=entry_id,
            task_type=req.task_type,
            payload=req.payload,
            asset_tree_path=_data_annotation_asset_tree_path(entry_id),
            manual_job_path=_data_annotation_manual_job_state_path(),
            runtime_state_path=_data_annotation_runtime_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    _sync_data_annotation_runtime_runner_to_core()
    log_items = _core_data_annotation_runtime_logs(
        limit=limit,
        scope=scope,
        item_id=item_id,
        runtime_state_path=_data_annotation_runtime_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    entries = [
        FanxiuDataAnnotationRuntimeLogEntry(
            id=f"runtime-{index}",
            time=str(item.get("time") or ""),
            kind=str(item.get("kind") or ""),
            scope=str(item.get("scope") or ""),
            item_id=str(item.get("item_id") or ""),
            message=str(item.get("message") or ""),
            action=str(item.get("action") or ""),
            source_file=str(item.get("source_file") or ""),
            source_path=str(item.get("source_path") or ""),
            source_line=item.get("source_line") if isinstance(item.get("source_line"), int) else None,
            source_expr=str(item.get("source_expr") or ""),
            ts="",
        )
        for index, item in enumerate(log_items)
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


@status_router.get("/data-annotation/runtime/doctor-watch/latest", response_model=FanxiuDataAnnotationDoctorWatchLatestResponse)
def get_fanxiu_data_annotation_doctor_watch_latest(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    return FanxiuDataAnnotationDoctorWatchLatestResponse.model_validate(_runtime_control.read_doctor_watch_latest())


@status_router.post("/data-annotation/runtime/doctor-watch/ensure", response_model=FanxiuDataAnnotationDoctorWatchEnsureResponse)
def ensure_fanxiu_data_annotation_doctor_watch(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    return FanxiuDataAnnotationDoctorWatchEnsureResponse.model_validate(_runtime_control.ensure_doctor_watch_background())


@status_router.delete("/data-annotation/runtime/logs", response_model=FanxiuDataAnnotationRuntimeLogResponse)
def clear_fanxiu_data_annotation_runtime_logs(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _sync_data_annotation_runtime_runner_to_core()
    _core_clear_data_annotation_runtime_logs(
        runtime_state_path=_data_annotation_runtime_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    return FanxiuDataAnnotationRuntimeLogResponse(entries=[], path=str(_data_annotation_runtime_state_path()))


@status_router.get("/data-annotation/scheduler/tasks", response_model=FanxiuDataAnnotationSchedulerTasksResponse)
def get_fanxiu_data_annotation_scheduler_tasks(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    settings = _runtime_control.read_scheduler_settings(
        scheduler_settings_path=_data_annotation_scheduler_settings_path()
    )
    return FanxiuDataAnnotationSchedulerTasksResponse(
        tasks=[
            FanxiuDataAnnotationSchedulerTaskItem.model_validate(_data_annotation_scheduler_task_view(item))
            for item in _read_data_annotation_scheduler_tasks()
        ],
        job_group_enabled=bool(settings.get("job_group_enabled", True)),
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
    payload = _runtime_control.update_scheduler_tasks(
        [item.model_dump() for item in tasks],
        scheduler_state_path=_data_annotation_scheduler_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
        manual_job_path=_data_annotation_manual_job_state_path(),
        now=datetime.now(),
    )
    _sync_data_annotation_runtime_runner_to_core()
    fanxiu_runtime_runner_wake()
    return FanxiuDataAnnotationSchedulerTasksResponse(
        tasks=[
            FanxiuDataAnnotationSchedulerTaskItem.model_validate(_data_annotation_scheduler_task_view(item))
            for item in payload
        ],
        job_group_enabled=bool(_runtime_control.read_scheduler_settings(
            scheduler_settings_path=_data_annotation_scheduler_settings_path()
        ).get("job_group_enabled", True)),
        path=str(_data_annotation_scheduler_state_path()),
    )


@status_router.get("/data-annotation/scheduler/settings", response_model=FanxiuDataAnnotationSchedulerTasksResponse)
def get_fanxiu_data_annotation_scheduler_settings(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    settings = _runtime_control.read_scheduler_settings(
        scheduler_settings_path=_data_annotation_scheduler_settings_path()
    )
    return FanxiuDataAnnotationSchedulerTasksResponse(
        tasks=[
            FanxiuDataAnnotationSchedulerTaskItem.model_validate(_data_annotation_scheduler_task_view(item))
            for item in _read_data_annotation_scheduler_tasks()
        ],
        job_group_enabled=bool(settings.get("job_group_enabled", True)),
        path=str(_data_annotation_scheduler_state_path()),
    )


@status_router.put("/data-annotation/scheduler/settings", response_model=FanxiuDataAnnotationSchedulerTasksResponse)
def put_fanxiu_data_annotation_scheduler_settings(
    req: FanxiuDataAnnotationSchedulerSettingsRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    settings = _runtime_control.set_scheduler_job_group_enabled(
        req.job_group_enabled,
        scheduler_settings_path=_data_annotation_scheduler_settings_path(),
    )
    _sync_data_annotation_runtime_runner_to_core()
    fanxiu_runtime_runner_wake()
    return FanxiuDataAnnotationSchedulerTasksResponse(
        tasks=[
            FanxiuDataAnnotationSchedulerTaskItem.model_validate(_data_annotation_scheduler_task_view(item))
            for item in _read_data_annotation_scheduler_tasks()
        ],
        job_group_enabled=bool(settings.get("job_group_enabled", True)),
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
    _sync_data_annotation_runtime_runner_to_core()
    try:
        status = _runtime_control.run_now_scheduler_task(
            entry=entry,
            entry_id=entry_id,
            task_id=req.task_id,
            payload_override=req.payload,
            interrupt_same_group=req.interrupt_same_group,
            scheduler_state_path=_data_annotation_scheduler_state_path(),
            runtime_state_path=_data_annotation_runtime_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
            manual_job_path=_data_annotation_manual_job_state_path(),
            asset_tree_path=_data_annotation_asset_tree_path(entry_id),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="任务不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    _sync_data_annotation_runtime_runner_to_core()
    try:
        status = _runtime_control.run_now_scheduler_task(
            entry=entry,
            entry_id=entry_id,
            task_id=req.task_id,
            payload_override=req.payload,
            interrupt_same_group=req.interrupt_same_group,
            scheduler_state_path=_data_annotation_scheduler_state_path(),
            runtime_state_path=_data_annotation_runtime_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
            manual_job_path=_data_annotation_manual_job_state_path(),
            asset_tree_path=_data_annotation_asset_tree_path(entry_id),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="任务不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    _sync_data_annotation_runtime_runner_to_core()
    status = _runtime_control.run_due_scheduler_tasks(
        entry=entry,
        entry_id=entry_id,
        scheduler_state_path=_data_annotation_scheduler_state_path(),
        scheduler_settings_path=_data_annotation_scheduler_settings_path(),
        runtime_state_path=_data_annotation_runtime_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
        manual_job_path=_data_annotation_manual_job_state_path(),
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
    )
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
    _sync_data_annotation_runtime_runner_to_core()
    status = _runtime_control.run_due_scheduler_tasks(
        entry=entry,
        entry_id=entry_id,
        scheduler_state_path=_data_annotation_scheduler_state_path(),
        scheduler_settings_path=_data_annotation_scheduler_settings_path(),
        runtime_state_path=_data_annotation_runtime_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
        manual_job_path=_data_annotation_manual_job_state_path(),
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
    )
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


@status_router.post("/data-annotation/remove-background", response_model=FanxiuDataAnnotationRemoveBackgroundResponse)
def remove_fanxiu_data_annotation_background_api(
    req: FanxiuDataAnnotationRemoveBackgroundRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    try:
        return remove_fanxiu_data_annotation_background(
            req.image_data_url,
            model=req.model,
            alpha_matting=req.alpha_matting,
            post_process_mask=req.post_process_mask,
        )
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


