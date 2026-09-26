from __future__ import annotations

# Re-export the existing entry points for older callers and resident Kernel references.
from backend.core.fanxiu.data_annotation.tasks.xianqiao_trial_actions import normalize_xianqiao_trial_track
from backend.core.fanxiu.data_annotation.game_context import (
    BehaviorTreeContext,
    DEFAULT_SCROLL_DURATION_SECONDS,
    DEFAULT_SCROLL_RATIO,
    FloatingItemInstance,
    SceneMatch,
    SceneWaitTimeout,
    _FanxiuMatchedView,
    _FanxiuWaitCondition,
    _FanxiuWaitResult,
    _SceneGraphRecognition,
    _absolute_shape_box,
    repeated_template_item_box_from_anchor,
)

from backend.core.fanxiu.runtime_gui.scroll import (
    DEFAULT_SCROLL_SETTLE_SECONDS,
    DEFAULT_SCROLL_UNCHANGED_THRESHOLD,
)

import base64
import hashlib
import io
import json
import os
import random
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import GeneratorType
from typing import Any, Callable, Iterable, Iterator, Literal, Mapping, NoReturn, Sequence

from pyxllib.prog import BehaviorTreeStatus, scheduled_task_payload_with_meta

from backend.core.fanxiu.behavior_tree.kernel_scheduler import (
    data_annotation_asset_tree_path as _core_data_annotation_asset_tree_path,
    ensure_behavior_tree_jobs_registered,
    fanxiu_kernel_execution_state_path as _core_kernel_execution_state_path,
    fanxiu_kernel_scheduler_state_path as _core_kernel_scheduler_state_path,
    fanxiu_kernel_scheduler_settings_path as _core_kernel_scheduler_settings_path,
    fanxiu_data_annotation_world_facts_path as _core_data_annotation_world_facts_path,
)
from backend.core.fanxiu.data_annotation.jobs import (
    canonical_fanxiu_data_annotation_task_type,
    get_fanxiu_data_annotation_task_cell_definition as _data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_default_jobs
from backend.core.fanxiu.data_annotation.behavior_tree_container import BehaviorTreeContainer as _BehaviorTreeContainer
from backend.core.fanxiu.data_annotation.recognition_candidates import (
    default_recognition_candidate_ids,
    default_recognition_candidate_layers,
    layer3_recognition_candidate_ids,
    scene_asset_directory_path,
)
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.recognition_graph import (
    SceneGraphCandidate,
    choose_scene_from_graph,
)
from backend.core.fanxiu.data_annotation.recognition_ambiguity_incidents import (
    RECOGNIZER_VERSION,
    record_recognition_ambiguity,
)
from backend.core.fanxiu.data_annotation.scene_navigation import (
    NavigationCycleTracker,
    explicit_scene_jump_edges,
    posterior_landing_probabilities,
)
from backend.core.fanxiu.data_annotation.task_context import (
    mark_scheduler_next_time_written,
    execution_task_payload,
)
from backend.core.fanxiu.data_annotation.navigation_incidents import (
    NAVIGATION_MAX_REPLAN_STEPS,
    NAVIGATION_SEMANTIC_EDGE_RETRY_LIMIT,
    NAVIGATION_STABLE_FRAME_SIMILARITY,
    NAVIGATION_STALL_MAX_SECONDS,
    NAVIGATION_STATE_EDGE_RETRY_LIMIT,
    NavigationIncidentRecorder,
)
from backend.core.fanxiu.data_annotation.shape_inheritance import (
    ShapeInheritanceResolution,
    find_raw_shape_for_effective,
    resolve_shape_inheritance,
)
from backend.core.fanxiu.data_annotation.unknown_recovery import build_unknown_evidence, _image_similarity_percent
from backend.core.fanxiu.data_annotation.popup_guard import (
    FanxiuEmulatorRestartRequired,
    SceneInterruptionMixin,
)
from backend.core.fanxiu.data_annotation.ocr_spatial import (
    DEFAULT_TEXT_TOKEN_GAP_HEIGHT_RATIO,
    OcrTextMatch,
    find_fuzzy_text_matches,
    find_text_matches,
    group_ocr_tokens,
    locate_text_box,
    query_ocr_lines,
    query_spatial_ocr,
    select_text_match,
    select_fuzzy_text_match,
    union_fragment_box,
)
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values, retry_numeric_ocr
from backend.core.fanxiu.data_annotation.state import (
    append_kernel_scheduler_status_log,
    initial_kernel_scheduler_status,
    normalize_kernel_scheduler_settings,
    normalize_kernel_scheduler_guard_items,
    parse_data_annotation_task_time,
    persist_kernel_scheduler_status as _persist_kernel_scheduler_status_core,
    read_data_annotation_json as _read_data_annotation_json,
    read_kernel_scheduler_status as _read_kernel_scheduler_status_core,
    record_kernel_scheduler_task_fact,
    write_data_annotation_json as _write_data_annotation_json,
)
from backend.core.fanxiu.data_annotation.storage import (
    update_data_annotation_asset_tree,
)
from backend.core.fanxiu.client.mumu_control import (
    ensure_mumu_device_healthy,
    record_mumu_adb_failure,
    screencap_mumu_adb_png,
    _encode_png_frame,
)
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.info_window import publish_fanxiu_scene_recognition
from backend.core.temp_paths import codeyun_temp_root, prune_temp_files, trim_file_tail
from pyxllib.autogui import (
    ActionPlanner,
    SceneNavigator,
    SceneScorer,
    AutomationContext,
    Shape,
    ShapeMatchPlanner,
    View,
    flatten_shapes as _flatten_shapes,
    frame_size as _frame_size,
    image_number as _image_number,
    index_images as _index_images,
)

# Compatibility for existing Kernel references; new consumers import the
# observation contract directly instead of depending on this executor.
from backend.core.fanxiu.data_annotation.tasks.daily_observations import (
    parse_xianfu_visit_cd_seconds as _parse_xianfu_visit_cd_seconds,
    parse_xianfu_skill_cd_seconds as _parse_xianfu_skill_cd_seconds,
    parse_daily_boss_cd_seconds as _parse_daily_boss_cd_seconds,
    parse_daily_boss_cd_seconds_from_six_digits as _parse_daily_boss_cd_seconds_from_six_digits,
    parse_daily_boss_reward_remaining as _parse_daily_boss_reward_remaining,
    parse_daily_boss_hp_percent as _parse_daily_boss_hp_percent,
    parse_first_int as _parse_first_int,
)
DEFAULT_LAYER0_WAIT_SECONDS = 30.0
DEFAULT_GO_SCENE_CONTINUOUS_UNKNOWN_SECONDS = 60.0
DEFAULT_GO_SCENE_OBSERVATION_TIMEOUT_SECONDS = 60.0
DEFAULT_SCENE_RECOGNITION_POLL_SECONDS = 1.0
OFFLINE_CULTIVATION_SETTLE_WAIT_SECONDS = 120.0
UNKNOWN_FALLBACK_MAX_ATTEMPTS_PER_NAVIGATION = 4
# 凡修所有菜单都用“点背景”退出，全屏活动封面更是没有关闭按钮。 #424
# 只声明了左下退出角，没有一个用“图形是否可见”判定的箭头可用，因此允许
# 在同一次导航里做少量“背景退出”探针；次数保持最小，且每次点击后必须
# 由新画面证明状态改变。 声明坐标来自资产树，不在代码里再存一份。
UNKNOWN_BACKDROP_EXIT_MAX_ATTEMPTS_PER_NAVIGATION = 2
OCCLUSION_ASSET_GROUP_TITLE = "遮挡"
LEGACY_OCCLUSION_ASSET_GROUP_TITLES = {"遮挡标记"}


_ACTION_TRACE_DEFAULT_MAX_FILES = 10000
_ACTION_TRACE_DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024
_ACTION_TRACE_DEFAULT_MAX_AGE_SECONDS = 3 * 24 * 60 * 60
_ACTION_TRACE_DEFAULT_INDEX_MAX_BYTES = 16 * 1024 * 1024
_ACTION_TRACE_DEFAULT_PRUNE_INTERVAL = 100
_ACTION_TRACE_DEFAULT_PRUNE_RESERVE = 200


@dataclass(frozen=True)
class _UnknownFallbackDecision:
    status: Literal["clicked", "exhausted", "unavailable"]
    attempt: int = 0
    point: tuple[float, float] | None = None


def _now() -> datetime:
    return job_now()


def ensure_fanxiu_mail_table() -> None:
    from backend.core.fanxiu.mail.store import ensure_fanxiu_mail_table as _ensure_fanxiu_mail_table

    _ensure_fanxiu_mail_table()


def normalize_fanxiu_mail_title(value: Any) -> str:
    from backend.core.fanxiu.mail.store import normalize_fanxiu_mail_title as _normalize_fanxiu_mail_title

    return _normalize_fanxiu_mail_title(value)


def normalize_fanxiu_mail_time_text(value: Any) -> str:
    from backend.core.fanxiu.mail.store import normalize_fanxiu_mail_time_text as _normalize_fanxiu_mail_time_text

    return _normalize_fanxiu_mail_time_text(value)


def _recognize_data_annotation_ocr_frame(frame_data_url: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    from backend.core.fanxiu.game.macro_annotation import _recognize_data_annotation_ocr_frame as _recognize_frame

    return _recognize_frame(frame_data_url, options=options)


def _screencap_game_window2_service() -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import screencap_game_window2_service

    return screencap_game_window2_service()


def _remote_game_window2_screencap(entry: Any) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import remote_game_window2_screencap

    return remote_game_window2_screencap(entry)


def _match_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import match_game_window2_service

    return match_game_window2_service(payload)


def _match_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import match_remote_game_window2

    return match_remote_game_window2(entry, payload)


def _click_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import click_game_window2_service

    return click_game_window2_service(payload)


def _click_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import click_remote_game_window2

    return click_remote_game_window2(entry, payload)


def _drag_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import drag_game_window2_service

    return drag_game_window2_service(payload)


def _drag_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import drag_remote_game_window2

    return drag_remote_game_window2(entry, payload)


def _keyevent_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import keyevent_game_window2_service

    return keyevent_game_window2_service(payload)


def _keyevent_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import keyevent_remote_game_window2

    return keyevent_remote_game_window2(entry, payload)


def _text_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import text_game_window2_service

    return text_game_window2_service(payload)


def _text_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import text_remote_game_window2

    return text_remote_game_window2(entry, payload)


def _data_annotation_asset_tree_path(entry_id: str) -> Path:
    return _core_data_annotation_asset_tree_path(entry_id)


def _kernel_execution_state_path() -> Path:
    return _core_kernel_execution_state_path()


def _data_annotation_world_facts_path() -> Path:
    return _core_data_annotation_world_facts_path()


def _kernel_scheduler_state_path() -> Path:
    return _core_kernel_scheduler_state_path()


def _kernel_scheduler_settings_path() -> Path:
    return _core_kernel_scheduler_settings_path()


def _persist_kernel_scheduler_status(status: dict[str, Any]) -> None:
    _persist_kernel_scheduler_status_core(
        _kernel_execution_state_path(),
        _data_annotation_world_facts_path(),
        status,
    )


def _read_kernel_scheduler_status() -> dict[str, Any]:
    return _read_kernel_scheduler_status_core(_kernel_execution_state_path())


def _record_kernel_scheduler_task_fact(task: dict[str, Any], result: str) -> None:
    record_kernel_scheduler_task_fact(_data_annotation_world_facts_path(), task, result)


def _read_data_annotation_world_facts() -> dict[str, Any]:
    return _read_data_annotation_json(_data_annotation_world_facts_path(), {})


def _read_kernel_scheduler_tasks() -> list[dict[str, Any]]:
    # Scheduler persistence has one owner.  Keep this lazy import so the
    # The executor module can still be imported while kernel_scheduler_control is loading.
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
        read_scheduler_tasks,
    )

    return read_scheduler_tasks(
        scheduler_state_path=_kernel_scheduler_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
        now=_now(),
    )


def _write_kernel_scheduler_tasks(
    tasks: list[dict[str, Any]],
    *,
    execution_update_ids: set[str] | None = None,
) -> None:
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
        write_scheduler_tasks,
    )

    write_scheduler_tasks(
        tasks,
        scheduler_state_path=_kernel_scheduler_state_path(),
        execution_update_ids=execution_update_ids,
    )


def set_kernel_scheduler_task_trigger_time(
    task_name: str,
    trigger_time: datetime | str | None,
) -> str | None:
    """Set or clear the sole trigger timestamp of any Scheduler task."""

    # Scheduler persistence has one atomic field-level command.  A Job must
    # never write back a stale whole-table snapshot merely to update its own
    # ``next_time``.
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
        set_scheduler_task_next_time,
    )

    return set_scheduler_task_next_time(
        task_name,
        trigger_time,
        scheduler_state_path=_kernel_scheduler_state_path(),
        now=_now(),
    )


def _read_kernel_scheduler_settings() -> dict[str, Any]:
    return normalize_kernel_scheduler_settings(
        _read_data_annotation_json(_kernel_scheduler_settings_path(), None)
    )


def _data_annotation_task_supported(task: dict[str, Any]) -> bool:
    register_fanxiu_default_jobs()
    task_type = canonical_fanxiu_data_annotation_task_type(str(task.get("task_type") or ""))
    definition = _data_annotation_task_cell_definition(task_type)
    return bool(definition and definition.scheduler_supported)


def _data_annotation_task_payload_with_meta(task: dict[str, Any]) -> dict[str, Any]:
    return scheduled_task_payload_with_meta(task)


from backend.core.fanxiu.data_annotation.tasks.daily_activity_list_sync import DailyActivityListSyncTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_challenge import DailyChallengeTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_redpacket import DailyRedpacketTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_experience import DailyExperienceTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_signin import DailySigninTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_xuanhuang import DailyXuanhuangTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daofa import DaofaTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daozu_challenge import DaozuChallengeTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_task_rewards import DailyTaskRewardsTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_foundation import DailyFoundationTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_resources import DailyResourceTaskMixin
from backend.core.fanxiu.data_annotation.tasks.gift_code import GiftCodeTaskMixin
from backend.core.fanxiu.data_annotation.tasks.jianling import JianlingTaskMixin
from backend.core.fanxiu.data_annotation.tasks.xianyan import XianyanTaskMixin
from backend.core.fanxiu.data_annotation.tasks.login_game import LoginGameTaskMixin
from backend.core.fanxiu.data_annotation.tasks.maintenance import MaintenanceTaskMixin
from backend.core.fanxiu.data_annotation.tasks.mail import MailTaskMixin
from backend.core.fanxiu.data_annotation.tasks.mail_claim_law import MailClaimLawTaskMixin
from backend.core.fanxiu.data_annotation.tasks.misc_actions import MiscActionTaskMixin
from backend.core.fanxiu.data_annotation.tasks.mozu import MozuTaskMixin
from backend.core.fanxiu.data_annotation.tasks.moyu_signup import MoyuSignupTaskMixin
from backend.core.fanxiu.data_annotation.tasks.moyu_challenge import MoyuChallengeTaskMixin
from backend.core.fanxiu.data_annotation.tasks.signup_misc import SignupMiscTaskMixin
from backend.core.fanxiu.data_annotation.tasks.xianfu import XianfuTaskMixin
from backend.core.fanxiu.data_annotation.tasks.yihuo import 日常异火任务Mixin
from backend.core.fanxiu.data_annotation.tasks.zhenxie import ZhenxieTaskMixin
from backend.core.fanxiu.data_annotation.tasks.xianqiao_trial import XianqiaoTrialTaskMixin
from backend.core.fanxiu.data_annotation.tasks.weekly_hanli import WeeklyHanliTaskMixin
from backend.core.fanxiu.data_annotation.tasks.weekly_wanxian import WeeklyWanxianTaskMixin
from backend.core.fanxiu.data_annotation.tasks.bubble_claim_pills import BubbleClaimPillsTaskMixin
from backend.core.fanxiu.data_annotation.tasks.bubble_hide import BubbleHideTaskMixin
from backend.core.fanxiu.data_annotation.tasks.bubble_lifecycle import BubbleLifecycleTaskMixin
from backend.core.fanxiu.data_annotation.tasks.weekly_shengzu import WeeklyShengzuTaskMixin
from backend.core.fanxiu.data_annotation.tasks.lingquan import LingquanTaskMixin
from backend.core.fanxiu.data_annotation.tasks.lingta_challenge import LingtaChallengeTaskMixin
from backend.core.fanxiu.data_annotation.tasks.prayer_daily_resource import PrayerDailyResourceTaskMixin
from backend.core.fanxiu.data_annotation.tasks.resource_rank_daily_gift import ResourceRankDailyGiftTaskMixin
from backend.core.fanxiu.data_annotation.tasks.dandao_task_rewards import DandaoTaskRewardsTaskMixin
from backend.core.fanxiu.data_annotation.tasks.yuanding_sansheng_run import YuandingSanshengRunMixin
from backend.core.fanxiu.data_annotation.tasks.xianshi_exchange import XianshiExchangeTaskMixin


class BehaviorTreeExecutor(
    SceneInterruptionMixin,
    MaintenanceTaskMixin,
    DaofaTaskMixin,
    DaozuChallengeTaskMixin,
    DailyTaskRewardsTaskMixin,
    MozuTaskMixin,
    ZhenxieTaskMixin,
    日常异火任务Mixin,
    JianlingTaskMixin,
    XianyanTaskMixin,
    LoginGameTaskMixin,
    DailyFoundationTaskMixin,
    DailyResourceTaskMixin,
    DailyActivityListSyncTaskMixin,
    DailyChallengeTaskMixin,
    DailyRedpacketTaskMixin,
    DailyExperienceTaskMixin,
    DailySigninTaskMixin,
    DailyXuanhuangTaskMixin,
    BubbleLifecycleTaskMixin,
    BubbleClaimPillsTaskMixin,
    BubbleHideTaskMixin,
    WeeklyHanliTaskMixin,
    WeeklyShengzuTaskMixin,
    WeeklyWanxianTaskMixin,
    LingquanTaskMixin,
    LingtaChallengeTaskMixin,
    XianqiaoTrialTaskMixin,
    XianfuTaskMixin,
    MoyuSignupTaskMixin,
    MoyuChallengeTaskMixin,
    SignupMiscTaskMixin,
    GiftCodeTaskMixin,
    MiscActionTaskMixin,
    MailTaskMixin,
    MailClaimLawTaskMixin,
    PrayerDailyResourceTaskMixin,
    ResourceRankDailyGiftTaskMixin,
    DandaoTaskRewardsTaskMixin,
    YuandingSanshengRunMixin,
    XianshiExchangeTaskMixin,
):
    default_guard_enabled = False
    default_guard_interval_seconds = 2.0
    engineering_idle_guard_interval_seconds = 60.0
    device_health_guard_interval_seconds = 60.0
    idle_recovery_interval_seconds = 300.0
    default_guard_items = {
        "device_health": {"enabled": True, "entry_id": "", "updated_at": 0.0},
    }
    guard_definitions = {
        "device_health": {
            "id": "device_health",
            "label": "设备健康",
            "default_enabled": True,
            "message": "低频检查 MuMu/安卓容器，异常时恢复模拟器和游戏",
        },
    }
    scene_ids = {
        "world": 34,
        "world_menu": 35,
        "settings": 49,
        "hide_floating": 58,
        "daily": 69,
        "daily_boss_list": 178,
        "daily_boss_detail": 179,
        "daily_boss_fighting": 180,
        "daily_boss_done": 181,
        "daily_lingzu_activity": 183,
        "daily_lingzu_detail": 184,
        "daily_lingzu_cutscene": 185,
        "daily_lingzu_world_reward": 186,
        "daily_lingzu_elder": 187,
        "daily_lingzu_boss": 188,
        "daily_lingzu_result": 189,
        "daily_jianling_main": 190,
        "daily_jianling_confirm": 191,
        "daily_jianling_result": 192,
        "daily_lingta_entry": 193,
        "daily_lingta_main": 194,
        "daily_lingta_confirm": 195,
        "daily_lingta_result": 196,
        "daily_xianyuan_list": 197,
        "daily_xianyuan_detail": 198,
        "daily_xianyuan_dialogue": 199,
        "daily_xianyuan_challenge_dialogue": 200,
        "daily_xianyuan_challenge_confirm": 201,
        "daily_xianyuan_challenge_result": 202,
        "daily_xianyuan_leave_confirm": 203,
        "daily_assistant_overview": 204,
        "daily_assistant_tongyou_confirm": 210,
        "daily_assistant_one_key_result": 275,
        "daily_assistant_one_key_confirm": 276,
        "daily_assistant_one_key_progress": 277,
        "daily_shuangxiu_secret": 215,
        "daily_shuangxiu_detail": 216,
        "daily_shuangxiu_invite": 217,
        "daily_shuangxiu_xianyuan_invite": 218,
        "daily_shuangxiu_training_ready": 219,
        "daily_shuangxiu_complete": 221,
        "wanling_invite": 70,
        "youli": 71,
        "youli_home": 228,
        "youli_purchase": 229,
        "youli_purchase_empty": 233,
        "youli_region_detail": 236,
        "youli_quick_result": 237,
        "xianshi": 247,
        "xianshi_coin_tab": 248,
        "xianshi_coin_list": 249,
        "xianshi_coin_box_detail": 250,
        "youli_explore": 72,
        "youli_result": 73,
        "signup": 23,
        "signup_reward": 24,
        "gift": 78,
        "reward": 81,
        "duplicated": 82,
    }
    scene_threshold = 80
    layer3_similarity_threshold = 90.0
    scene_thresholds = {"gift": 60, "daily": 60, "hide_floating": 55}
    overlay_threshold = 55

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event: threading.Event | None = None
        self._guard_group_enabled = True
        self._guard_enabled = self.default_guard_enabled
        self._guard_entry_id = ""
        self._guard_interval_seconds = self.default_guard_interval_seconds
        self._guard_items: dict[str, dict[str, Any]] = json.loads(json.dumps(self.default_guard_items, ensure_ascii=False))
        self._last_device_health_guard_at = 0.0
        self._auto_close_candidates_cache: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}
        self._shape_inheritance_cache: tuple[
            list[dict[str, Any]],
            ShapeInheritanceResolution,
        ] | None = None
        self._missing_match_source_filenames: set[str] = set()
        self._log_scope = ""
        self._log_item_id = ""
        self._last_status_persist_at = 0.0
        self._cell_execution_lock = threading.RLock()
        self._shared_ocr_lock = threading.RLock()
        self._navigation_random = random.Random()
        self._status: dict[str, Any] = self._initial_status()

    def _wait_action_settle(self, ctx: dict[str, Any], stop_event: threading.Event, seconds: float = 2.0):
        self._clear_tick_frame(ctx)
        if stop_event.wait(max(0.0, float(seconds))):
            self._raise_if_stopped(stop_event)
        self._clear_tick_frame(ctx)
        yield BehaviorTreeStatus.RUNNING

    def analyze_external_frame(
        self, frame_data_url: str, *, frame_width: int, frame_height: int,
        target_scene_id: int | None = None,
        sampling_size: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        """Recognize an uploaded frame and propose one declared navigation action.

        This isolated observation has no device, Kernel, popup handler, asset
        mutation, frame persistence or authoritative local scene projection.
        The caller owns bounded polling, freshness and action acknowledgement.
        No learned transition counts are written from untrusted client receipts.
        The supplied frame uses the asset canvas. sampling_size records its
        original pixel dimensions before normalization, for image bandwidth
        matching only; OCR and proposed coordinates stay on the asset canvas.
        """
        from types import SimpleNamespace
        from backend.core.fanxiu.data_annotation.storage import (
            DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID,
            data_annotation_asset_tree_path,
            read_data_annotation_asset_tree_snapshot,
        )

        if not frame_data_url or frame_width <= 0 or frame_height <= 0:
            raise ValueError("必须提供已解码的外部截图和尺寸")
        path = data_annotation_asset_tree_path()
        snapshot = read_data_annotation_asset_tree_snapshot(path)
        if not snapshot.exists or not snapshot.tree:
            return {"status": "blocked", "reason": "assets_unavailable", "observation": {}}
        tree = self._resolved_asset_tree(snapshot.tree)
        images = self._index_images(tree)
        if target_scene_id is not None and target_scene_id not in images:
            return {"status": "blocked", "reason": "target_scene_missing", "observation": {}}
        ctx = {
            "entry": SimpleNamespace(mode="local", entry_id=DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID),
            "asset_tree": tree, "images": images, "asset_tree_path": path,
            "asset_tree_revision": snapshot.revision,
            "external_frame_only": True, "_disable_recognition_ambiguity_recording": True,
            "external_sampling_size": sampling_size,
        }
        with self._scene_observation_probe(ctx):
            recognition = self._identify_scene_number_by_graph(ctx, frame_data_url)
        scene_id = recognition.scene_id
        image = images.get(scene_id, {})
        observation = {
            "scene_id": scene_id, "scene_title": str(image.get("title") or ""),
            "score": recognition.score, "recognition_status": recognition.status,
            "matched_layer": recognition.matched_layer, "asset_revision": snapshot.revision,
            "frame_width": frame_width, "frame_height": frame_height,
        }
        result: dict[str, Any] = {"status": "completed", "observation": observation}
        if target_scene_id is None:
            return result
        if scene_id is None:
            return {**result, "status": "running", "reason": "scene_unknown",
                    "action": {"kind": "wait", "wait_ms": 1000}}
        if scene_id == 546:
            return {**result, "status": "blocked", "reason": "game_maintenance"}
        # Similarity is auxiliary evidence and must not authorize an action.
        if recognition.status == "similarity_tiebreak":
            return {**result, "status": "blocked", "reason": "scene_ambiguous"}
        if scene_id == target_scene_id:
            return result
        edges = explicit_scene_jump_edges(tree)
        queue: list[tuple[int, list[dict[str, Any]]]] = [(scene_id, [])]
        visited = {scene_id}
        route = None
        for source_id, prefix in queue:
            if source_id == target_scene_id:
                route = prefix
                break
            for edge in edges.get(source_id, []):
                if self._scene_navigation_shape_risk(edge["shape"]):
                    continue
                for landing_id in edge["target_ids"]:
                    if landing_id not in visited:
                        visited.add(landing_id)
                        queue.append((landing_id, [*prefix, edge]))
        if not route:
            return {**result, "status": "blocked", "reason": "navigation_path_missing"}
        edge = route[0]
        image, shape = edge["image"], edge["shape"]
        width, height = self._frame_size(image)
        if (frame_width, frame_height) != (width, height):
            observation["expected_frame_size"] = {"width": width, "height": height}
            return {**result, "status": "blocked", "reason": "frame_size_unsupported"}
        conditions = self._shape_match_conditions(shape)
        # Fixed navigation regions with matching explicitly disabled are a
        # supported asset contract (e.g. the world menu arrow). Their position
        # is trusted only after the containing scene and exact canvas match.
        match = {"matched": True} if not conditions and not shape.get("floating") else None
        for condition in conditions:
            match = self._match_shape(ctx, image, shape, frame_data_url, condition=condition)
            if match.get("matched"):
                break
        if not match or not match.get("matched"):
            return {**result, "status": "running", "reason": "navigation_shape_not_visible",
                    "action": {"kind": "wait", "wait_ms": 1000}}
        point = self._shape_match_resolved_click_point(image, shape, match)
        if point is None and not shape.get("floating"):
            point = ActionPlanner().shape_center(image, shape)
        if point is None or not (0 <= point[0] < frame_width and 0 <= point[1] < frame_height):
            return {**result, "status": "blocked", "reason": "navigation_shape_unresolved"}
        return {**result, "status": "running", "action": {
            "kind": "tap", "x": int(point[0]), "y": int(point[1]),
            "shape_id": str(shape.get("id") or ""), "shape_title": str(shape.get("title") or ""),
        }}

    def _view_for_image(self, image: dict[str, Any]) -> View:
        return View(image)

    def _shape_for_legacy_shape(self, image: dict[str, Any], shape: dict[str, Any]) -> Shape:
        return Shape(shape, parent_view=self._view_for_image(image))

    def _scroll_shape_content_changed(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        stop_event: threading.Event,
        *,
        reverse: bool = False,
        settle_seconds: float = DEFAULT_SCROLL_SETTLE_SECONDS,
        unchanged_threshold: float = DEFAULT_SCROLL_UNCHANGED_THRESHOLD,
    ):
        context = self._behavior_tree_context(ctx, ctx.get("asset_tree_path") if isinstance(ctx.get("asset_tree_path"), Path) else None, stop_event=stop_event)
        shape_model = self._shape_for_legacy_shape(image, shape)
        direction = shape_model.load_direction or "down"
        if reverse:
            direction = {
                "up": "down",
                "down": "up",
                "left": "right",
                "right": "left",
            }.get(str(direction).strip().lower(), "up")
        return (yield from context.scroll_shape_content(
            shape_model,
            direction=direction,
            settle_seconds=settle_seconds,
            unchanged_threshold=unchanged_threshold,
            stable_sample_count=1,
            unchanged_confirmations=1,
        ))

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
                is_occlusion_folder = (
                    node_type == "folder"
                    and (title == OCCLUSION_ASSET_GROUP_TITLE or title in LEGACY_OCCLUSION_ASSET_GROUP_TITLES)
                )
                current_in_occlusion = in_occlusion_folder or is_occlusion_folder
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

    def _initial_status(self) -> dict[str, Any]:
        return initial_kernel_scheduler_status()

    def _status_base_preserving_guard_locked(self) -> dict[str, Any]:
        base = self._initial_status()
        current_logs = [item for item in self._status.get("logs") or [] if isinstance(item, dict)]
        current_cell_logs = [item for item in self._status.get("cell_logs") or [] if isinstance(item, dict)]
        base.update({
            "guard_group_enabled": bool(self._guard_group_enabled),
            "guard_enabled": bool(self._guard_enabled),
            "guard_running": bool(self._status.get("guard_running")),
            "guard_entry_id": self._guard_entry_id,
            "guard_interval_seconds": self._guard_interval_seconds,
            "guard_items": json.loads(json.dumps(self._guard_items, ensure_ascii=False)),
            "last_guard_event": self._status.get("last_guard_event") if isinstance(self._status.get("last_guard_event"), dict) else {},
            "logs": current_logs[-500:],
            "cell_logs": current_cell_logs[:100],
        })
        return base

    def status(self, *, include_cell_logs: bool = True) -> dict[str, Any]:
        with self._lock:
            self._sync_guard_status_locked()
            payload = self._status if include_cell_logs else {**self._status, "cell_logs": []}
            return json.loads(json.dumps(payload, ensure_ascii=False))

    def replace_logs(self, logs: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            self._status["logs"] = list(logs)
            self._status["updated_at"] = time.time()
            self._sync_guard_status_locked()
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
        guard_group_running = False
        guard_running = False
        guard_items: dict[str, dict[str, Any]] = {}
        for guard_id, definition in self.guard_definitions.items():
            state = self._guard_items.get(guard_id)
            if not isinstance(state, dict):
                state = {}
            enabled = bool(state.get("enabled"))
            entry_id = str(state.get("entry_id") or "")
            running = False
            message = str(definition.get("message") or "")
            if guard_id == "device_health":
                running = False
                device_health = self._status.get("device_health")
                if isinstance(device_health, dict):
                    state_text = str(device_health.get("status") or "")
                    if state_text:
                        message = f"设备状态：{state_text}"
            guard_items[guard_id] = {
                **definition,
                "enabled": enabled,
                "running": running,
                "entry_id": entry_id,
                "updated_at": float(state.get("updated_at") or 0),
                "message": message,
            }
        self._status.update({
            "guard_group_enabled": bool(self._guard_group_enabled),
            "guard_enabled": bool(self._guard_enabled),
            "guard_running": bool(guard_running),
            "guard_group_running": bool(guard_group_running),
            "guard_entry_id": self._guard_entry_id,
            "guard_interval_seconds": self._guard_interval_seconds,
            "guard_items": guard_items,
        })

    def ensure_service(
        self,
        *,
        entry: Any,
        entry_id: str,
        asset_tree_path: Path,
        tick_seconds: float = 1.0,
    ) -> dict[str, Any]:
        del tick_seconds
        entry_id = str(getattr(entry, "entry_id", None) or entry_id)
        with self._lock:
            self._restore_persisted_config_locked()
            if self._guard_enabled:
                self._guard_entry_id = entry_id
            if not self._status.get("entry_id"):
                self._status["entry_id"] = entry_id
            self._set_status_locked("idle", "凡修框架已加载到 Jupyter Kernel", phase="idle")
        return self.status()

    def _restore_persisted_config_locked(self) -> None:
        if self._status.get("running"):
            return
        persisted = _read_kernel_scheduler_status()
        if not persisted:
            return
        normalize_kernel_scheduler_guard_items(persisted, self.guard_definitions)
        self._guard_group_enabled = bool(persisted.get("guard_group_enabled", True))
        self._guard_enabled = False
        self._guard_entry_id = str(persisted.get("guard_entry_id") or "")
        self._guard_interval_seconds = float(persisted.get("guard_interval_seconds") or self._guard_interval_seconds)
        raw_items = persisted.get("guard_items")
        if isinstance(raw_items, dict):
            self._guard_items = {
                str(key): dict(value)
                for key, value in raw_items.items()
                if isinstance(value, dict)
            }
        current_logs = [item for item in self._status.get("logs") or [] if isinstance(item, dict)]
        persisted_logs = [item for item in persisted.get("logs") or [] if isinstance(item, dict)]
        kept_logs = (current_logs or persisted_logs)[-500:]
        current_cell_logs = [item for item in self._status.get("cell_logs") or [] if isinstance(item, dict)]
        persisted_cell_logs = [item for item in persisted.get("cell_logs") or [] if isinstance(item, dict)]
        kept_cell_logs = (current_cell_logs or persisted_cell_logs)[:100]
        self._status.update({
            **self._status,
            "entry_id": persisted.get("entry_id") or persisted.get("guard_entry_id") or self._status.get("entry_id") or "",
            "current_scene": persisted.get("current_scene"),
            "message": "行为树常驻服务恢复配置",
            "logs": kept_logs,
            "cell_logs": kept_cell_logs,
            "updated_at": time.time(),
        })

    def _run_idle_guard_tick(
        self,
        entry: Any,
        entry_id: str,
        asset_tree_path: Path,
        *,
        stop_event: threading.Event | None = None,
    ) -> bool:
        return self._run_device_health_guard_tick(entry_id)

    def _run_idle_recovery(
        self,
        entry: Any,
        entry_id: str,
        asset_tree_path: Path,
        *,
        stop_event: threading.Event,
        max_popup_ticks: int | None = None,
        settle_seconds: float | None = None,
    ) -> None:
        """Run low-frequency device health maintenance while no job is active."""
        self._run_device_health_guard_tick(entry_id)

    def _run_device_health_guard_tick(self, entry_id: str, *, force: bool = False) -> bool:
        if not self._is_guard_enabled("device_health"):
            return False
        now = time.time()
        if (
            not force
            and self._last_device_health_guard_at > 0
            and now - self._last_device_health_guard_at < max(1.0, float(self.device_health_guard_interval_seconds))
        ):
            return False
        self._last_device_health_guard_at = now
        try:
            state = ensure_mumu_device_healthy(recover=True, reason="resident_heartbeat")
        except Exception as exc:
            state = {"status": "suspect", "last_error": str(exc)}
        status_text = str(state.get("status") or "unknown")
        recovered = bool(state.get("recovered"))
        with self._lock:
            previous = self._status.get("device_health")
            previous_status = str(previous.get("status") or "") if isinstance(previous, dict) else ""
            self._status["device_health"] = state
            if recovered:
                self._log_locked("warning", "设备健康守护已恢复 MuMu 安卓容器并拉起凡修游戏", scope="guard", item_id="device_health")
            elif status_text and status_text != previous_status and status_text != "healthy":
                self._log_locked("warning", f"设备健康异常：{status_text}", scope="guard", item_id="device_health")
            if entry_id and not self._status.get("entry_id"):
                self._status["entry_id"] = entry_id
            self._sync_guard_status_locked()
        if recovered:
            self._persist_status()
        return recovered

    def stop_current_task(self, entry_id: str) -> dict[str, Any]:
        with self._lock:
            if entry_id and self._status.get("entry_id") not in {"", entry_id}:
                return self.status()
            if not self._status.get("running"):
                self._sync_guard_status_locked()
                self._set_status_locked("idle", "当前没有正在运行的 Cell")
                return json.loads(json.dumps(self._status, ensure_ascii=False))
            if self._stop_event is not None:
                self._stop_event.set()
            self._set_status_locked("stopping", "当前任务停止请求已发送")
        return self.status()

    def set_guard(
        self,
        *,
        entry: Any,
        entry_id: str,
        enabled: bool,
        interval_seconds: float,
        guard_id: str = "device_health",
        asset_tree_path: Path,
    ) -> dict[str, Any]:
        guard_id = str(guard_id or "device_health").strip() or "device_health"
        if guard_id == "close_popups":
            raise ValueError("close_popups 独立弹窗守护已下线；弹窗由场景识别管线自动处理")
        if guard_id not in self.guard_definitions:
            raise ValueError(f"未知守护：{guard_id}")
        interval_seconds = max(0.5, min(30.0, float(interval_seconds or 2.0)))
        with self._lock:
            guard_item = self._guard_items.setdefault(guard_id, {})
            guard_item.update({
                "enabled": bool(enabled),
                "entry_id": entry_id if enabled else "",
                "updated_at": time.time(),
            })
            self._set_status_locked(str(self._status.get("status") or "idle"), f"守护{'已开启' if enabled else '已关闭'}：{guard_id}")
            self._sync_guard_status_locked()
            self._log_locked("info", self._status["message"], scope="guard", item_id=guard_id)
        self.ensure_service(entry=entry, entry_id=entry_id, asset_tree_path=asset_tree_path)
        return self.status()

    def set_guard_group_enabled(
        self,
        *,
        entry: Any,
        entry_id: str,
        enabled: bool,
        asset_tree_path: Path,
    ) -> dict[str, Any]:
        entry_id = str(getattr(entry, "entry_id", None) or entry_id)
        with self._lock:
            self._guard_group_enabled = bool(enabled)
            self._set_status_locked(
                "idle" if not self._status.get("running") else str(self._status.get("status") or "running"),
                "守护组已开启" if enabled else "守护组已关闭",
            )
            self._sync_guard_status_locked()
            self._log_locked("info", self._status["message"], scope="guard", item_id="guard_group")
        self.ensure_service(entry=entry, entry_id=entry_id, asset_tree_path=asset_tree_path)
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
            "current_cell_id": "",
            "interruptible": True,
        })

    def _canonical_task_type(self, task_type: str) -> str:
        return canonical_fanxiu_data_annotation_task_type(task_type)

    def _set_log_context(self, scope: str, item_id: str) -> tuple[str, str]:
        with self._lock:
            previous = (self._log_scope, self._log_item_id)
            self._log_scope = str(scope or "")
            self._log_item_id = str(item_id or "")
            return previous

    def _restore_log_context(self, previous: tuple[str, str]) -> None:
        with self._lock:
            self._log_scope, self._log_item_id = previous

    def _log_locked(
        self,
        kind: str,
        message: str,
        *,
        scope: str | None = None,
        item_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        log_scope = self._log_scope if scope is None else str(scope or "")
        log_item_id = self._log_item_id if item_id is None else str(item_id or "")
        append_kernel_scheduler_status_log(
            self._status,
            kind,
            message,
            scope=log_scope,
            item_id=log_item_id,
            time_text=_now().strftime("%H:%M:%S"),
            updated_at=time.time(),
            extra=extra,
        )

    def _log(self, kind: str, message: str) -> None:
        with self._lock:
            self._log_locked(kind, message)
            running = bool(self._status.get("running"))
        if running:
            self._persist_status(min_interval_seconds=2.0)

    def _cell_log_entry_base_id(self, item: dict[str, Any]) -> str:
        return hashlib.sha1(
            json.dumps(
                {
                    "time": item.get("time") or "",
                    "kind": item.get("kind") or "",
                    "scope": item.get("scope") or "",
                    "item_id": item.get("item_id") or "",
                    "message": item.get("message") or "",
                    "action": item.get("action") or "",
                    "source_file": item.get("source_file") or "",
                    "source_line": item.get("source_line") or "",
                    "source_expr": item.get("source_expr") or "",
                    "ts": item.get("ts") or "",
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:16]

    def _cell_log_entry(self, item: dict[str, Any], occurrence: int) -> dict[str, Any]:
        return {
            "id": f"context-{self._cell_log_entry_base_id(item)}-{occurrence}",
            "time": str(item.get("time") or ""),
            "kind": str(item.get("kind") or ""),
            "scope": str(item.get("scope") or ""),
            "item_id": str(item.get("item_id") or ""),
            "message": str(item.get("message") or ""),
            "action": str(item.get("action") or ""),
            "source_file": str(item.get("source_file") or ""),
            "source_path": str(item.get("source_path") or ""),
            "source_line": item.get("source_line") if isinstance(item.get("source_line"), int) else None,
            "source_expr": str(item.get("source_expr") or ""),
            "ts": str(item.get("ts") or ""),
        }

    def _task_cell_source(self, task_type: str, payload: dict[str, Any]) -> str:
        clean_payload = {
            str(key): value
            for key, value in dict(payload or {}).items()
            if not str(key).startswith("__")
        }
        return f"run_task_cell({task_type!r}, {clean_payload!r})"

    def _append_cell_log_locked(
        self,
        *,
        title: str,
        source: str,
        logs: list[dict[str, Any]] | None = None,
        cell_id: str | None = None,
    ) -> None:
        raw_entries = [item for item in (logs if logs is not None else self._status.get("logs") or []) if isinstance(item, dict)]
        raw_entries = raw_entries[-300:]
        seen_ids: dict[str, int] = {}
        entries: list[dict[str, Any]] = []
        for item in raw_entries:
            base_id = self._cell_log_entry_base_id(item)
            occurrence = seen_ids.get(base_id, 0)
            seen_ids[base_id] = occurrence + 1
            entries.append(self._cell_log_entry(item, occurrence))
        if not entries:
            now = _now().strftime("%H:%M:%S")
            entries = [{
                "id": f"context-cell-empty-{uuid.uuid4().hex[:8]}",
                "time": now,
                "kind": "info",
                "scope": "cell",
                "item_id": "framework",
                "message": f"提交 cell：{title}",
                "action": "",
                "source_file": "",
                "source_path": "",
                "source_line": None,
                "source_expr": "",
                "ts": str(time.time()),
            }]
        cell_id = str(cell_id or "").strip() or (
            f"cell-{hashlib.sha1((title + source + str(time.time())).encode('utf-8')).hexdigest()[:16]}"
        )
        cell = {
            "id": cell_id,
            "title": title,
            "source_kind": "command",
            "source": source,
            "started_at": str(entries[0].get("time") or ""),
            "ended_at": str(entries[-1].get("time") or ""),
            "entries": entries,
        }
        existing = self._status.get("cell_logs") if isinstance(self._status.get("cell_logs"), list) else []
        self._status["cell_logs"] = [cell, *[item for item in existing if isinstance(item, dict) and item.get("id") != cell_id]][:100]

    def _finish_daily_task(
        self,
        *,
        task_type: str,
        label: str,
        message: str,
        current_scene: int | None = 34,
    ) -> None:
        with self._lock:
            self._set_status_locked(
                "success",
                message,
                phase=f"{task_type}_done",
                current_scene=current_scene,
            )
            self._log_locked("success", self._status["message"])

    def _execute_daily_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None,
        *,
        task_type: str,
        label: str,
        flow: Callable[[BehaviorTreeContext], Any],
    ) -> Iterator[Any]:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError(f"缺少{label}资产树路径，无法执行作业")

        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        context_attrs = getattr(context, "attrs", None)
        if isinstance(context_attrs, dict):
            context_attrs["payload"] = payload
        result = flow(context)
        flow_result: Any = None
        if isinstance(result, GeneratorType):
            flow_result = yield from result
        else:
            flow_result = result
        yield from self._wait_action_settle(ctx, stop_event)
        if isinstance(flow_result, dict) and "next_time" in flow_result:
            raise RuntimeError(
                f"{label}违反调度契约：正式 flow 不得返回 next_time，"
                "必须在业务完成点调用统一原子入口持久化"
            )
        context_attrs = getattr(context, "attrs", None)
        completion_message = str(context_attrs.get("completion_message") or "").strip() if isinstance(context_attrs, dict) else ""
        if isinstance(flow_result, dict):
            completion_message = str(flow_result.get("message") or completion_message).strip()
        resolved_message = completion_message or f"{label}完成，已回到世界"
        self._finish_daily_task(
            task_type=task_type,
            label=label,
            message=resolved_message,
            current_scene=flow_result.get("current_scene", 34) if isinstance(flow_result, dict) else 34,
        )
        # Wrappers commonly perform a final go_scene after this helper returns.
        # That navigation updates the live progress message, so returning only
        # the framework string "success" lets Scheduler history persist the
        # later progress text instead of the verified business terminal.
        return {"result": "success", "message": resolved_message}

    def _task_cell_log_message(self, task_id: str, message: str) -> str:
        task_id = str(task_id or "").strip()
        return f"[{task_id}] {message}" if task_id else message

    def _normalize_task_result(self, value: Any) -> tuple[str, str]:
        """Normalize any ordinary business return to trigger success.

        Business success/failure is represented by the task's persisted
        ``next_time``.  Only a raised exception can make the Scheduler attempt
        fail and enter framework retry handling.
        """

        if isinstance(value, dict):
            message = str(value.get("message") or "").strip()
            return "success", message
        return "success", ""

    def _persist_status(self, *, min_interval_seconds: float = 0.0) -> None:
        now = time.monotonic()
        with self._lock:
            if min_interval_seconds > 0 and now - self._last_status_persist_at < min_interval_seconds:
                return
            self._last_status_persist_at = now
        try:
            _persist_kernel_scheduler_status(self.status())
        except Exception:
            pass

    def _task_label(self, task_type: str, payload: dict[str, Any] | None = None) -> str:
        task_type = self._canonical_task_type(task_type)
        definition = _data_annotation_task_cell_definition(task_type)
        label = definition.label if definition is not None else task_type
        if task_type == "go_scene":
            target = (payload or {}).get("target_scene_id") or (payload or {}).get("target")
            if target:
                label = f"到场景 #{target}"
        return label

    def _behavior_tree_context(
        self,
        ctx: dict[str, Any],
        asset_tree_path: Path | None = None,
        frame_data_url: str | None = None,
        stop_event: threading.Event | None = None,
    ) -> BehaviorTreeContext:
        if asset_tree_path is None:
            ctx_asset_tree_path = ctx.get("asset_tree_path")
            if isinstance(ctx_asset_tree_path, Path):
                asset_tree_path = ctx_asset_tree_path
        return BehaviorTreeContext(self, ctx, asset_tree_path=asset_tree_path, frame_data_url=frame_data_url, stop_event=stop_event)

    def _fanxiu_observer(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        frame_data_url: str | None = None,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        if isinstance(asset_tree_path, Path):
            context = self._behavior_tree_context(ctx, asset_tree_path, frame_data_url=frame_data_url, stop_event=stop_event)
            required_methods = (
                "cur_frame",
                "recognize_scene_in_frame",
                "ocr_fragments",
                "ocr_text",
                "clear_frame",
            )
            if all(hasattr(context, name) for name in required_methods):
                return context

        runner = self

        class _FallbackObserver:
            def __init__(self, initial_frame: str | None = None) -> None:
                self.frame_data_url = initial_frame

            def cur_frame(self, update: bool = False) -> str:
                if update:
                    self.clear_frame()
                if isinstance(self.frame_data_url, str) and self.frame_data_url:
                    return self.frame_data_url
                self.frame_data_url = runner._screencap(ctx)
                return self.frame_data_url

            def recognize_scene_in_frame(
                self,
                views: list[int] | None = None,
                *,
                frame_data_url: str,
            ) -> tuple[int | None, float, str]:
                if not isinstance(frame_data_url, str) or not frame_data_url:
                    raise ValueError("recognize_scene_in_frame 必须传入已有 frame_data_url")
                frame = frame_data_url
                scene_id, score = runner._identify_scene_number(ctx, frame, views)
                return scene_id, float(score or 0.0), frame

            def ocr_fragments(self, frame_data_url: str | None = None, *, update: bool = False) -> list[dict[str, Any]]:
                frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
                return runner._recognized_scene_ocr_fragments(ctx, frame)

            def ocr_text(self, frame_data_url: str | None = None, *, update: bool = False) -> str:
                frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
                return runner._ocr_text(self.ocr_fragments(frame))

            def click_frame_point(self, view: View | dict[str, Any], x: float, y: float) -> None:
                image = view.raw if isinstance(view, View) else view
                runner._click_frame_point(ctx, image, x, y)
                self.clear_frame()

            def click_shape_center(self, view: View | dict[str, Any], shape: Shape | str) -> None:
                image = view.raw if isinstance(view, View) else view
                if not isinstance(image, dict):
                    raise RuntimeError("缺少可点击 view")
                raw_shape = shape.raw if isinstance(shape, Shape) else runner._find_shape(image, str(shape))
                if not isinstance(raw_shape, dict):
                    raise RuntimeError(f"缺少 #{runner._image_number(image) or '?'}「{shape}」标注")
                x, y = ActionPlanner().shape_center(image, raw_shape)
                self.click_frame_point(image, x, y)

            def wait_click(self, view: View | int | str | dict[str, Any], shape: Shape | str, **_options: Any):
                if False:
                    yield BehaviorTreeStatus.RUNNING
                if isinstance(view, View):
                    image = view.raw
                elif isinstance(view, dict):
                    image = view
                else:
                    images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
                    view_id = int(str(view).lstrip("#"))
                    image = images.get(view_id)
                if not isinstance(image, dict):
                    raise RuntimeError(f"无法解析帧选择器：{view}")
                raw_shape = shape.raw if isinstance(shape, Shape) else runner._find_shape(image, str(shape))
                if not isinstance(raw_shape, dict):
                    raise RuntimeError(f"缺少 #{runner._image_number(image) or '?'}「{shape}」标注")
                x, y = ActionPlanner().shape_center(image, raw_shape)
                self.click_frame_point(image, x, y)

            def wait_action_settle(self, seconds: float = 1.0):
                yield from runner._wait_action_settle(ctx, stop_event, seconds=float(seconds or 0))

            def clear_frame(self) -> None:
                self.frame_data_url = None
                runner._clear_tick_frame(ctx)

        return _FallbackObserver(frame_data_url)

    def _behavior_tree_context_scene_text(
        self,
        ctx: dict[str, Any],
        context: Any,
        scene_ids: list[int] | None = None,
        *,
        frame: str | None = None,
        update: bool = False,
    ):
        if "entry" not in ctx and (not isinstance(frame, str) or not frame):
            if scene_ids is None:
                scene_id, score, frame = self._current_scene_number(ctx)
            else:
                frame = self._screencap(ctx)
                scene_id, score = self._identify_scene_number(ctx, frame, scene_ids)
        elif isinstance(frame, str) and frame and hasattr(context, "recognize_scene_in_frame"):
            scene_id, score, frame = context.recognize_scene_in_frame(
                scene_ids, frame_data_url=frame
            )
        elif hasattr(context, "current_scene"):
            # 只读文本采样：立即取得同一帧的场景与文字，不等待页面切换。
            scene_id, score, frame = yield from context.current_scene(
                scene_ids,
                update=update,
                label="读取场景及文本",
            )
        else:
            if not isinstance(frame, str) or not frame:
                frame = context.cur_frame(update=update) if hasattr(context, "cur_frame") else self._screencap(ctx)
            scene_id, score = self._identify_scene_number(ctx, frame, scene_ids)
        try:
            text = context.ocr_text(frame) if hasattr(context, "ocr_text") else self._recognized_scene_ocr_text(ctx, frame, scene_ids)
        except Exception:
            text = ""
        return scene_id, float(score or 0.0), frame, text

    def _behavior_tree_context_ocr_text_in_shapes(
        self,
        context: Any,
        view: View | dict[str, Any],
        shape_titles: Iterable[str],
        *,
        frame_data_url: str,
        padding: int = 16,
    ) -> str:
        if hasattr(context, "ocr_text_in_shapes"):
            return context.ocr_text_in_shapes(view, tuple(shape_titles), frame_data_url=frame_data_url, padding=padding)
        image = view.raw if isinstance(view, View) else view
        return self._ocr_text(self._ocr_fragments_in_shapes(frame_data_url, image, tuple(shape_titles), padding=padding))

    def _is_guard_enabled(self, guard_id: str) -> bool:
        guard_id = str(guard_id or "").strip()
        with self._lock:
            if not self._guard_group_enabled:
                return False
            return self._guard_item_enabled_locked(guard_id)

    def _guard_item_enabled(self, guard_id: str) -> bool:
        guard_id = str(guard_id or "").strip()
        with self._lock:
            return self._guard_item_enabled_locked(guard_id)

    def _guard_item_enabled_locked(self, guard_id: str) -> bool:
        if guard_id == "close_popups":
            return False
        state = self._guard_items.get(guard_id)
        return bool(state.get("enabled")) if isinstance(state, dict) else False

    def _guard_service_tick(
        self,
        guard_id: str,
        execution_ctx: dict[str, Any],
        asset_tree_path: Path,
        stop_event: threading.Event,
        *,
        allow_during_task: bool = False,
        guard_override: bool | None = None,
    ) -> BehaviorTreeStatus:
        self._raise_if_stopped(stop_event)
        guard_id = str(guard_id or "").strip()
        if guard_override is False:
            return BehaviorTreeStatus.SKIP
        if guard_override is True:
            enabled = self._guard_item_enabled(guard_id)
        else:
            enabled = self._is_guard_enabled(guard_id)
        if not enabled:
            return BehaviorTreeStatus.SKIP
        if guard_id == "device_health":
            entry_id = str(execution_ctx.get("entry_id") or self._status.get("entry_id") or "")
            return BehaviorTreeStatus.RUNNING if self._run_device_health_guard_tick(entry_id) else BehaviorTreeStatus.SKIP
        return BehaviorTreeStatus.SKIP

    def _run_behavior_tree(
        self,
        *,
        execution_ctx: dict[str, Any],
        asset_tree_path: Path,
        stop_event: threading.Event,
        action: Callable[[], Any],
        label: str,
        tick_seconds: float = 1.0,
        max_execution_seconds: float | None = None,
        guard_override: bool | None = None,
    ) -> Any:
        with self._cell_execution_lock:
            return _BehaviorTreeContainer(
                self,
                execution_ctx=execution_ctx,
                asset_tree_path=asset_tree_path,
                stop_event=stop_event,
                guard_override=guard_override,
            ).run_job_until_complete(
                action=action,
                label=label,
                tick_seconds=tick_seconds,
                max_execution_seconds=max_execution_seconds,
            )

    def _task_timeout_seconds(self, payload: dict[str, Any] | None = None) -> float | None:
        payload = payload if isinstance(payload, dict) else {}
        if bool(payload.get("unbounded_execution")):
            return None
        raw_value = payload.get("max_execution_seconds", payload.get("timeout_seconds", 7200))
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = 7200.0
        return max(30.0, min(21600.0, value))

    def _guard_override_from_payload(self, payload: dict[str, Any] | None) -> bool | None:
        if not isinstance(payload, dict) or "guard" not in payload:
            return None
        value = payload.get("guard")
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on", "enable", "enabled", "开启", "开", "启用"}:
            return True
        if text in {"0", "false", "no", "off", "disable", "disabled", "关闭", "关", "禁用"}:
            return False
        return None

    def _run_direct_action(
        self,
        action: Callable[[], Any],
        *,
        stop_event: threading.Event,
        tick_seconds: float = 1.0,
        max_execution_seconds: float | None = None,
    ) -> Any:
        result = action()
        if not isinstance(result, GeneratorType):
            return result
        started_at = time.monotonic()
        while True:
            self._raise_if_stopped(stop_event)
            if max_execution_seconds is not None and time.monotonic() - started_at > max_execution_seconds:
                stop_event.set()
                raise RuntimeError(f"行为树任务超时：超过 {max_execution_seconds:.0f} 秒")
            try:
                status = next(result)
            except StopIteration as stop:
                return stop.value
            if status == BehaviorTreeStatus.FAILURE:
                raise RuntimeError("行为树节点失败")
            stop_event.wait(max(0.1, float(tick_seconds or 1.0)))

    def _run_generic_task(
        self,
        *,
        entry: Any,
        entry_id: str,
        task_type: str,
        payload: dict[str, Any],
        asset_tree_path: Path,
        stop_event: threading.Event,
    ) -> None:
        task_id = str(payload.get("__scheduler_task_id") or "")
        current_cell_id = f"cell-{uuid.uuid4().hex[:16]}"
        with self._lock:
            self._status["current_cell_id"] = current_cell_id
        previous_log_context = self._set_log_context("job", task_id) if task_id else None
        try:
            tree = self._load_asset_tree(asset_tree_path)
            ctx = {
                "entry": entry,
                "entry_id": entry_id,
                "asset_tree": tree,
                "asset_tree_path": asset_tree_path,
                "images": self._index_images(tree),
            }
            self._require_assets(ctx)
            raw_task_result = self._run_behavior_tree(
                execution_ctx=ctx,
                asset_tree_path=asset_tree_path,
                stop_event=stop_event,
                action=lambda: self._execute_task(ctx, task_type, payload, stop_event),
                label=self._task_label(task_type, payload),
                tick_seconds=max(0.1, float(payload.get("__tick_seconds") or 1.0)),
                max_execution_seconds=self._task_timeout_seconds(payload),
                guard_override=self._guard_override_from_payload(payload),
            )
            task_result, task_message = self._normalize_task_result(raw_task_result)
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({
                    "status": "success" if task_result == "success" else str(task_result or "success"),
                    "phase": "done",
                    "message": task_message or (f"{self._task_label(task_type, payload)}完成" if task_result == "success" else f"{self._task_label(task_type, payload)}已跳过"),
                    "finished_at": time.time(),
                    "updated_at": time.time(),
                    "current_index": 1,
                    "current_code": "",
                })
                self._log_locked("success" if task_result == "success" else "skip", self._status["message"])
                self._append_cell_log_locked(
                    title=f"执行任务：{self._task_label(task_type, payload)}",
                    source=self._task_cell_source(task_type, payload),
                    cell_id=current_cell_id,
                )
        except InterruptedError:
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"status": "stopped", "phase": "stopped", "message": "已停止", "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("stop", "任务已停止")
                self._append_cell_log_locked(
                    title=f"执行任务：{self._task_label(task_type, payload)}",
                    source=self._task_cell_source(task_type, payload),
                    cell_id=current_cell_id,
                )
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"ok": False, "status": "error", "phase": "error", "message": str(detail), "error": str(detail), "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("error", str(detail))
                self._append_cell_log_locked(
                    title=f"执行任务：{self._task_label(task_type, payload)}",
                    source=self._task_cell_source(task_type, payload),
                    cell_id=current_cell_id,
                )
        finally:
            if previous_log_context is not None:
                self._restore_log_context(previous_log_context)
            self._persist_status()

    def _cleanup_failed_scheduler_task_to_scene(
        self,
        *,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        task_label: str,
        target_scene_id: int,
    ) -> bool:
        """Best-effort atomic cleanup after a Scheduler task fails.

        A fresh stop event is deliberate: a normal task exception should still
        return the game to the task's explicitly declared failure anchor.
        """
        target_scene_id = int(target_scene_id)
        cleanup_stop_event = threading.Event()
        with self._lock:
            self._set_status_locked(
                "running",
                f"{task_label}：任务失败，收尾返回锚点 #{target_scene_id}",
                phase="scheduler_failure_cleanup",
            )
            self._log_locked("action", f"{task_label}：任务失败，通用场景规划收尾到 #{target_scene_id}")
        self._persist_status()

        def target_confirmed() -> tuple[bool, float]:
            """Require the failure anchor on two independent fresh frames.

            Failure cleanup must not report success from one transient or stale
            scene match: that leaves the next Scheduler task inside the failed
            task's business page.  The focused Layer 0 probe is intentionally
            repeated after clearing the behavior-tree frame cache.
            """

            context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=cleanup_stop_event)
            last_score = 0.0
            for probe_index in range(2):
                scene_id, score, _frame = self._run_direct_action(
                    lambda: context.current_scene(
                        [target_scene_id],
                        update=True,
                        label=f"{task_label}：确认失败收尾锚点",
                    ),
                    stop_event=cleanup_stop_event,
                    max_execution_seconds=30.0,
                )
                last_score = float(score or 0.0)
                if scene_id != target_scene_id or not self._scene_matches_id(target_scene_id, last_score):
                    return False, last_score
                if probe_index == 0 and cleanup_stop_event.wait(0.35):
                    self._raise_if_stopped(cleanup_stop_event)
            return True, last_score

        def run_cleanup_route() -> None:
            self._run_direct_action(
                lambda: self._go_scene_task(
                    ctx,
                    asset_tree_path,
                    target_scene_id,
                    cleanup_stop_event,
                ),
                stop_event=cleanup_stop_event,
                max_execution_seconds=120.0,
            )

        try:
            run_cleanup_route()
            confirmed, score = target_confirmed()
            if not confirmed:
                with self._lock:
                    self._log_locked(
                        "warning",
                        f"{task_label}：首次失败收尾未连续确认 #{target_scene_id}，重新识别并规划",
                    )
                run_cleanup_route()
                confirmed, score = target_confirmed()
            if not confirmed:
                raise RuntimeError(f"失败收尾未连续确认 #{target_scene_id}")
        except Exception as cleanup_exc:
            try:
                confirmed, score = target_confirmed()
            except Exception:
                confirmed, score = False, 0.0
            if confirmed:
                with self._lock:
                    self._status.update({"current_scene": target_scene_id, "updated_at": time.time()})
                    self._log_locked(
                        "success",
                        f"{task_label}：失败收尾已到 #{target_scene_id} {score:.0f}%（来源 shape 落点未声明，但目标锚点已可靠确认）",
                    )
                return True
            with self._lock:
                self._log_locked("warning", f"{task_label}：失败收尾未到 #{target_scene_id}：{cleanup_exc}")
            return False
        with self._lock:
            self._log_locked("success", f"{task_label}：失败收尾已连续确认 #{target_scene_id} {score:.0f}%")
        return True

    def _find_asset_image_by_title(self, ctx: dict[str, Any], title: str) -> dict[str, Any] | None:
        def visit(nodes: Any) -> dict[str, Any] | None:
            if not isinstance(nodes, list):
                return None
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if node.get("type") == "image" and str(node.get("title") or "") == title:
                    return node
                found = visit(node.get("children"))
                if found is not None:
                    return found
            return None

        return visit(ctx.get("asset_tree"))

    def _known_blocking_overlay_info(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        def shape_titles(image: dict[str, Any] | None) -> list[str]:
            if not isinstance(image, dict):
                return []
            titles: list[str] = []
            for shape in image.get("shapes") or []:
                if isinstance(shape, dict):
                    title = str(shape.get("title") or "").strip()
                    if title:
                        titles.append(title)
            return titles

        frame = self._screencap(ctx)
        announcement_scene_id, _announcement_score = self._identify_scene_number(
            ctx,
            frame,
            [14],
        )
        announcement_is_formal_scene = bool(
            announcement_scene_id == 14
            and str(ctx.get("_last_scene_recognition_status") or "") != "startup_ocr"
        )
        if str(ctx.get("_last_scene_recognition_status") or "") == "startup_ocr":
            return None
        if announcement_is_formal_scene:
            image = self._find_asset_image_by_title(ctx, "游戏公告")
            close_shape = self._known_game_announcement_action_shape(image)
            if close_shape is None:
                return {
                    "scene_id": 14,
                    "title": "游戏公告",
                    "blocking": True,
                    "all_shapes": shape_titles(image),
                    "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少「关闭公告」动作标注，无法安全进入游戏",
                }
            return {
                "scene_id": 14,
                "title": "游戏公告",
                "blocking": False,
                "all_shapes": shape_titles(image),
                "action_shapes": [str(close_shape.get("title") or "")],
                "message": "检测到游戏公告遮挡，已有安全关闭动作标注",
            }
        purchase_scene_id, _purchase_score = self._identify_scene_number(ctx, frame, [224, 225])
        if purchase_scene_id in {224, 225}:
            images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
            candidate = images.get(224)
            image224 = candidate if isinstance(candidate, dict) else None
            candidate225 = images.get(225)
            image225 = candidate225 if isinstance(candidate225, dict) else None
            use_shape = self._find_shape(image224, "购买并使用")
            blank_shape = self._find_shape(image225, "空白")
            if use_shape is None or blank_shape is None:
                missing: list[str] = []
                if use_shape is None:
                    missing.append("#224「购买并使用」")
                if blank_shape is None:
                    missing.append("#225「空白」")
                return {
                    "scene_id": 224,
                    "title": "购买破界符",
                    "blocking": True,
                    "all_shapes": shape_titles(image224),
                    "message": f"检测到 #224「购买破界符」弹窗；资产树缺少 {'、'.join(missing)}，无法按 #224 连续购买到 #225 后回退",
                }
            return {
                "scene_id": 224,
                "title": "购买破界符",
                "blocking": False,
                "all_shapes": shape_titles(image224),
                "action_shapes": ["购买并使用", "#225 空白"],
                "message": "检测到 #224「购买破界符」弹窗，已有连续购买与 #225 回退标注",
            }
        return None

    def _known_blocking_overlay_message(self, ctx: dict[str, Any]) -> str | None:
        info = self._known_blocking_overlay_info(ctx)
        if not isinstance(info, dict) or not bool(info.get("blocking")):
            return None
        return str(info.get("message") or "")

    def _known_blocking_overlay_action(self, ctx: dict[str, Any], info: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
        title = str(info.get("title") or "")
        image: dict[str, Any] | None = None
        if title == "灵祖奖励浮层":
            images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
            candidate = images.get(186)
            image = candidate if isinstance(candidate, dict) else None
        elif title == "游戏公告":
            image = self._find_asset_image_by_title(ctx, "游戏公告")
        if not isinstance(image, dict):
            return None
        if title == "游戏公告":
            shape = self._known_game_announcement_action_shape(image)
        else:
            shape = self._find_shape(image, "关闭") or self._find_shape(image, "空白") or self._find_shape(image, "返回") or self._find_shape(image, "退出")
        if not isinstance(shape, dict):
            return None
        return image, shape

    def _known_game_announcement_action_shape(self, image: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(image, dict):
            return None
        shape = self._find_shape(image, "关闭公告")
        return shape if isinstance(shape, dict) else None

    def _clear_known_blocking_overlay_if_possible(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        label: str = "Scheduler",
        timeout: float = 12.0,
    ):
        info = self._known_blocking_overlay_info(ctx)
        if not isinstance(info, dict):
            return False
        message = str(info.get("message") or "检测到阻断浮层")
        if bool(info.get("blocking")):
            raise RuntimeError(message)
        action = self._known_blocking_overlay_action(ctx, info)
        if action is None:
            return False
        image, shape = action
        title = str(info.get("title") or image.get("title") or "阻断浮层")
        shape_title = str(shape.get("title") or "关闭")
        frame = self._screencap(ctx)
        with self._lock:
            self._set_status_locked("running", f"{label}：关闭{title}", phase="clear_blocking_overlay")
            self._log_locked("action", f"{label}：点击「{title}/{shape_title}」清理阻断浮层", scope="job", item_id="scheduler")
        x, y = ActionPlanner().shape_center(image, shape)
        click_ref = dict(image)
        click_ref["title"] = (title, shape_title)
        self._click_frame_point(ctx, click_ref, x, y)
        deadline = time.monotonic() + max(0.5, float(timeout or 12.0))
        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            yield from self._wait_action_settle(ctx, stop_event, seconds=1.0)
            self._clear_tick_frame(ctx)
            next_info = self._known_blocking_overlay_info(ctx)
            if not isinstance(next_info, dict) or str(next_info.get("title") or "") != title:
                with self._lock:
                    self._log_locked("success", f"{label}：{title}已清理", scope="job", item_id="scheduler")
                return True
            if bool(next_info.get("blocking")):
                raise RuntimeError(str(next_info.get("message") or message))
        raise RuntimeError(f"{label}：点击「{title}/{shape_title}」后阻断浮层仍未消失")

    def _set_scheduler_task_next_time(
        self,
        task_id: str,
        next_time_text: str | None,
    ) -> None:
        """Persist the absolute next run selected by the current job.

        Standard scheduled-job pattern:

        1. Inspect all business facts available to this run.
        2. Choose the next absolute time for every normal branch, including
           cooldown, already-complete and temporarily-unreachable branches.
        3. Call this method at the business decision point, then
           return ``success``.  A business action not being completed is not a
           scheduler failure when the job handled it and planned a revisit.
        4. Raise only when execution itself is broken or interrupted.  The
           external Scheduler then keeps the job retryable and applies its
           configured error delay; the next attempt never continues this run mid-flow.

        Passing ``None`` deliberately makes a job dormant until explicitly
        scheduled again.  Do not infer recurrence from ``trigger_description``.
        """
        task_id = str(task_id or "").strip()
        if not task_id:
            return
        try:
            set_kernel_scheduler_task_trigger_time(task_id, next_time_text)
        except LookupError:
            # A plain debug Cell may not correspond to a persisted task
            # instance. Debugging must not create hidden scheduling state.
            return

    def _persist_scheduler_task_next_time(
        self,
        task_id: str,
        next_time_text: str | None,
    ) -> None:
        """Strict Job completion-point write; failure prevents a success return."""

        task_id = str(task_id or "").strip()
        if not task_id:
            raise RuntimeError("业务写入 next_time 时缺少 Scheduler Job id")
        try:
            set_kernel_scheduler_task_trigger_time(task_id, next_time_text)
        except LookupError as exc:
            raise RuntimeError(f"业务写入 next_time 失败：Scheduler 中不存在 Job {task_id!r}") from exc
        mark_scheduler_next_time_written(task_id)

    def _persist_admission_decision(
        self,
        payload: dict[str, Any] | None,
        decision: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Consume a pure admission decision inside the Job business domain.

        The returned admission payload intentionally cannot transport
        ``next_time`` to the Cell or Scheduler.
        """

        if decision is None:
            return None
        normalized = dict(decision)
        if "next_time" not in normalized:
            raise RuntimeError("正常结束的作业准入决策缺少 next_time")
        next_time = normalized.pop("next_time")
        task_id = str((payload or {}).get("__scheduler_task_id") or "").strip()
        if not task_id:
            raise RuntimeError("作业准入写入 next_time 时缺少 __scheduler_task_id")
        self._persist_scheduler_task_next_time(
            task_id,
            str(next_time) if next_time is not None else None,
        )
        return normalized

    def _clear_scheduler_task_payload_flag(self, task_id: str, flag: str) -> None:
        task_id = str(task_id or "").strip()
        flag = str(flag or "").strip()
        if not task_id or not flag:
            return
        from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
            read_scheduler_tasks,
            update_scheduler_tasks,
        )

        tasks = read_scheduler_tasks(
            scheduler_state_path=_kernel_scheduler_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
            now=_now(),
        )
        task = next((item for item in tasks if str(item.get("id") or "") == task_id), None)
        if not isinstance(task, dict):
            return
        payload = dict(task.get("payload") or {})
        if flag not in payload:
            return
        payload.pop(flag, None)
        update_scheduler_tasks(
            [{"id": task_id, "payload": payload}],
            scheduler_state_path=_kernel_scheduler_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
            now=_now(),
        )

    def _get_scheduler_task_payload_flag(self, task_id: str, flag: str) -> Any:
        """Read one Job-owned idempotency fact from the authoritative task store."""

        task_id = str(task_id or "").strip()
        flag = str(flag or "").strip()
        if not task_id or not flag:
            return None
        from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
            read_scheduler_tasks,
        )

        tasks = read_scheduler_tasks(
            scheduler_state_path=_kernel_scheduler_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
            now=_now(),
        )
        task = next((item for item in tasks if str(item.get("id") or "") == task_id), None)
        if not isinstance(task, dict):
            return None
        return dict(task.get("payload") or {}).get(flag)

    def _set_scheduler_task_payload_flag(self, task_id: str, flag: str, value: Any) -> bool:
        """Persist a narrow idempotency flag via the atomic task store."""
        task_id = str(task_id or "").strip()
        flag = str(flag or "").strip()
        if not task_id or not flag:
            return False
        from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
            read_scheduler_tasks,
            update_scheduler_tasks,
        )

        try:
            scheduler_state_path = _kernel_scheduler_state_path()
            world_facts_path = _data_annotation_world_facts_path()
            now = _now()
            tasks = read_scheduler_tasks(
                scheduler_state_path=scheduler_state_path,
                world_facts_path=world_facts_path,
                now=now,
            )
            task = next((item for item in tasks if str(item.get("id") or "") == task_id), None)
            if not isinstance(task, dict):
                return False
            payload = dict(task.get("payload") or {})
            payload[flag] = value
            persisted = update_scheduler_tasks(
                [{"id": task_id, "payload": payload}],
                scheduler_state_path=scheduler_state_path,
                world_facts_path=world_facts_path,
                now=now,
            )
        except Exception:
            return False
        persisted_task = next(
            (item for item in persisted if str(item.get("id") or "") == task_id),
            None,
        )
        return bool(
            isinstance(persisted_task, dict)
            and dict(persisted_task.get("payload") or {}).get(flag) == value
        )

    def _schedule_login_job_first(self) -> str | None:
        """Atomically place login before every currently materialized Job time."""

        # Keep Scheduler persistence owned by kernel_scheduler_control and import lazily
        # so the long-lived behavior-tree executor can still be imported during bootstrap.
        from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
            schedule_login_job_first,
        )

        return schedule_login_job_first(
            scheduler_state_path=_kernel_scheduler_state_path(),
            now=_now(),
        )

    def _execute_task(self, ctx: dict[str, Any], task_type: str, payload: dict[str, Any], stop_event: threading.Event) -> Any:
        task_type = self._canonical_task_type(task_type)
        ensure_behavior_tree_jobs_registered()
        definition = _data_annotation_task_cell_definition(task_type)
        if definition is None:
            raise RuntimeError(f"暂不支持的任务类型：{task_type}")
        normalized_payload = dict(payload or {})
        if definition.normalize_payload is not None:
            normalized_payload = definition.normalize_payload(normalized_payload)

        def run_generator():
            with execution_task_payload(ctx, normalized_payload):
                scheduler_task_id = str(normalized_payload.get("__scheduler_task_id") or "")
                if (
                    scheduler_task_id
                    and ctx.get("entry") is not None
                    and task_type not in {
                        "login_game",
                        "maintenance_recovery",
                        "bubble_weekly_pills",
                    }
                ):
                    preflight = self._ensure_world_ready_via_login_game(
                        ctx,
                        stop_event,
                        normalized_payload,
                    )
                    preflight_result = (
                        (yield from preflight)
                        if isinstance(preflight, GeneratorType)
                        else preflight
                    )
                    if preflight_result == "scheduled":
                        return {
                            "result": "success",
                            "message": "检测到登录链，已让登录作业抢先；当前作业保持到期等待整单重跑",
                        }

                result = definition.handler(self, ctx, normalized_payload, stop_event)
                if isinstance(result, GeneratorType):
                    return (yield from result)
                return str(result or "success")

        return run_generator()


    def _ensure_world_main_for_right_menu(
        self,
        ctx: dict[str, Any],
        context: Any,
        stop_event: threading.Event,
        image34: dict[str, Any],
        *,
        task_label: str,
    ):
        frame = context.cur_frame(update=True)
        text = context.ocr_text(frame)
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        if "仙市" in compact and ("仙府" in compact or "储物袋" in compact):
            return
        width, height = self._frame_size(image34)
        candidates: list[tuple[float, float, str]] = []
        tokens = context.ocr_tokens(frame)
        for fragment in group_ocr_tokens(tokens):
            fragment_text = _sanitize_ocr_text(fragment.get("text"))
            if "世界" not in fragment_text:
                continue
            target_box = locate_text_box(query_spatial_ocr(tokens, fragment)["tokens"], "世界")
            if target_box is None:
                continue
            cx = float(target_box["x"]) + float(target_box["w"]) / 2
            cy = float(target_box["y"]) + float(target_box["h"]) / 2
            if cx <= width * 0.22 and cy >= height * 0.72:
                candidates.append((float(cx), float(cy), fragment_text))
        if not candidates:
            return
        x, y, source_text = sorted(candidates, key=lambda item: (item[1], item[0]))[-1]
        with self._lock:
            self._set_status_locked(
                "running",
                f"{task_label}：当前 #34 不是世界主态，点击底部「世界」恢复右侧菜单",
                phase="world_main_restore_for_right_menu",
                current_scene=34,
            )
            self._log_locked("action", f"{task_label}：OCR 命中底部「世界」({source_text})，点击恢复世界主态 ({x:.0f},{y:.0f})")
        context.click_frame_point(View(image34), x, y)
        yield from context.wait_action_settle(2.0)


    def _return_xianfu_learn_skill_to_world(self, context: BehaviorTreeContext):
        with self._lock:
            self._set_status_locked("running", "仙府_领悟绝技：返回世界 #34", phase="xianfu_skill_return_world")
            self._log_locked("action", "仙府_领悟绝技：按仙府收尾链路返回 #34")
        yield from self._return_xianfu_pages_to_world(
            context,
            task_label="仙府_领悟绝技",
            current_candidates=(177, 176, 172, 171, 34),
        )
        return "success"

    def _ensure_xianfu_learn_skill_xianpin_tab(self, context: BehaviorTreeContext, image176: dict[str, Any]):
        frame = context.cur_frame(update=True)
        status_text = self._ocr_text(self._ocr_fragments_in_shapes(frame, image176, ("状态", "价格"), padding=16))
        if _parse_xianfu_skill_cd_seconds(status_text) is not None:
            self._log("detail", f"仙府_领悟绝技：当前绝技页状态区已可读，跳过重复切换仙品绝技：{status_text}")
            return frame
        yield from self._switch_xianfu_learn_skill_xianpin_tab(context)
        return context.cur_frame(update=True)

    def _switch_xianfu_learn_skill_xianpin_tab(self, context: BehaviorTreeContext):
        view176 = context.get_view(176)
        tab_shape = view176.get_shape("仙品绝技") if isinstance(view176, View) else None
        if tab_shape is None:
            raise RuntimeError("缺少 #176「仙品绝技」标注，无法切换到仙品绝技读取 CD")
        with self._lock:
            self._set_status_locked("running", "仙府_领悟绝技：切换仙品绝技", phase="xianfu_skill_open_xianpin", current_scene=176)
            self._log_locked("action", "仙府_领悟绝技：点击 #176「仙品绝技」")
        tab_shape.click(context)
        yield from context.wait_scene([176], wait=5.0, label="仙府_领悟绝技：等待仙品绝技 #176")

    def _handle_xianfu_learn_skill_result_popup(
        self,
        context: BehaviorTreeContext,
        *,
        refresh_reference_frame_once: bool = False,
        scheduler_task_id: str = "",
    ):
        view177 = context.get_view(177)
        if not isinstance(view177, View):
            raise RuntimeError("缺少 #177「领悟绝技」结果弹窗标注，无法继续")
        yield from context.wait_scene([177], wait=18.0, label="仙府_领悟绝技：等待结果弹窗 #177")
        if refresh_reference_frame_once:
            frame_data_url = context.cur_frame()
            evidence_dir = self._refresh_scene_reference_frame(context, 177, frame_data_url)
            self._clear_scheduler_task_payload_flag(
                scheduler_task_id,
                "refresh_scene_177_reference_once",
            )
            self._log(
                "success",
                f"仙府_领悟绝技：已用本次真实 #177 画面重置参考帧；原图备份={evidence_dir}",
            )
        continue_shape = view177.get_shape("继续")
        if continue_shape is None:
            raise RuntimeError("缺少 #177「继续」标注，无法关闭领悟结果")
        with self._lock:
            self._set_status_locked("running", "仙府_领悟绝技：关闭结果弹窗", phase="xianfu_skill_continue", current_scene=177)
            self._log_locked("action", "仙府_领悟绝技：点击 #177「继续」")
        continue_shape.click(context)
        yield from context.wait_scene([176], wait=18.0, label="仙府_领悟绝技：返回绝技 #176")
        return "success"

    def _refresh_scene_reference_frame(
        self,
        context: BehaviorTreeContext,
        scene_id: int,
        frame_data_url: str,
    ) -> Path:
        view = context.get_view(scene_id)
        image = view.raw if isinstance(view, View) and isinstance(view.raw, dict) else None
        filename = str((image or {}).get("filename") or "").strip()
        asset_tree_path = context.asset_tree_path or context.ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path) or not filename:
            raise RuntimeError(f"缺少 #{scene_id} 参考帧路径，无法重置")
        image_path = asset_tree_path.parent / "images" / Path(filename).name
        if not image_path.is_file():
            raise RuntimeError(f"#{scene_id} 参考帧不存在：{image_path}")

        raw = self._decode_frame_data_url(frame_data_url)
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as source:
            converted = source.convert("RGB") if image_path.suffix.lower() in {".jpg", ".jpeg"} else source.convert("RGBA")
            width, height = converted.size
            expected_size = (int((image or {}).get("width") or 0), int((image or {}).get("height") or 0))
            if all(expected_size) and (width, height) != expected_size:
                raise RuntimeError(
                    f"#{scene_id} 直播帧尺寸 {width}x{height} 与标注尺寸 {expected_size[0]}x{expected_size[1]} 不一致，拒绝重置"
                )
            buffer = io.BytesIO()
            converted.save(buffer, format="JPEG" if image_path.suffix.lower() in {".jpg", ".jpeg"} else "PNG", quality=95)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        evidence_dir = asset_tree_path.parent / "recognition-ops" / "reference-refresh" / f"scene-{scene_id}-{stamp}"
        evidence_dir.mkdir(parents=True, exist_ok=False)
        backup_path = evidence_dir / f"before-{image_path.name}"
        shutil.copy2(image_path, backup_path)
        summary = {
            "scene_id": int(scene_id),
            "title": str((image or {}).get("title") or ""),
            "filename": image_path.name,
            "refreshed_at": datetime.now().isoformat(timespec="seconds"),
            "preserved_fields": ["id", "title", "filename", "shapes", "children"],
            "shape_count": len((image or {}).get("shapes") or []),
            "backup_path": os.fspath(backup_path),
            "captured_size": [int(width), int(height)],
        }
        _write_data_annotation_json(evidence_dir / "summary.json", summary)

        replacement = buffer.getvalue()
        temporary = image_path.with_name(f".{image_path.name}.{hashlib.sha256(replacement).hexdigest()[:12]}.tmp")
        temporary.write_bytes(replacement)
        temporary.replace(image_path)
        return evidence_dir


    def _raise_if_stopped(self, stop_event: threading.Event | None) -> None:
        repair_error = getattr(self, "_scene_repair_error", None)
        if repair_error is not None:
            raise repair_error
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError()

    def _load_asset_tree(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            raise RuntimeError("未找到帧树，请先保存帧树标注")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise RuntimeError("帧树格式错误")
        return [item for item in payload if isinstance(item, dict)]

    def _image_number(self, image: dict[str, Any]) -> int | None:
        return _image_number(image)

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
        return dict(self._shape_inheritance_resolution(nodes).images)

    def _shape_inheritance_resolution(
        self,
        nodes: list[dict[str, Any]],
    ) -> ShapeInheritanceResolution:
        cached = self._shape_inheritance_cache
        if cached is not None:
            raw_tree, resolution = cached
            if nodes is raw_tree or nodes is resolution.tree:
                return resolution
        resolution = resolve_shape_inheritance(nodes)
        self._shape_inheritance_cache = (nodes, resolution)
        return resolution

    def _resolved_asset_tree(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._shape_inheritance_resolution(nodes).tree

    def _invalidate_shape_inheritance_cache(self) -> None:
        self._shape_inheritance_cache = None

    def _invalidate_asset_derived_caches(self, asset_tree_path: Path) -> None:
        """Invalidate runner-global values derived from one asset snapshot."""

        self._invalidate_shape_inheritance_cache()
        self._auto_close_candidates_cache.pop(str(asset_tree_path), None)
        self._missing_match_source_filenames.clear()

    @staticmethod
    def _publish_asset_ctx_revision(ctx: dict[str, Any], revision: str) -> None:
        """Advance one ctx snapshot after an in-place persisted asset mutation."""

        ctx["asset_tree_revision"] = str(revision or "")
        ctx["asset_tree_generation"] = int(ctx.get("asset_tree_generation") or 0) + 1
        for key in (
            "_scene_graph_relation_cache",
            "_scene_discriminator_groups",
            "_scene_discriminator_score_cache",
        ):
            ctx.pop(key, None)

    def _jump_target_text(self, shape: dict[str, Any]) -> str:
        return SceneNavigator([]).jump_target_text(shape)

    def _parse_scene_jump_entries(self, value: Any) -> list[dict[str, Any]]:
        return SceneNavigator([]).parse_scene_jump_entries(value)

    def _serialize_scene_jump_entries(self, entries: list[dict[str, Any]]) -> str:
        return SceneNavigator([]).serialize_scene_jump_entries(entries)

    def _increment_scene_jump_target(self, shape: dict[str, Any], target_scene_id: int) -> bool:
        """Record one real landing in the shape's shared jump history.

        ``sceneJumpTarget`` is an observed-destination frequency table used as
        routing prior, *not* an allow-list.  Therefore a newly recognized,
        already-annotated scene must be appended here.  Do not simplify this
        back to ``SceneNavigator.increment_scene_jump_target``: that helper only
        increments labels already present and would silently discard new D
        landings.  Do not split new values into ``observedLanding`` either.
        """
        navigator = SceneNavigator([])
        current_text = navigator.jump_target_text(shape)
        if current_text in {"-1", "0"}:
            return False
        entries = navigator.parse_scene_jump_entries(current_text)
        target_label = str(int(target_scene_id))
        for entry in entries:
            if navigator.scene_jump_label_number(entry.get("label")) == int(target_scene_id):
                entry["count"] = int(entry.get("count") or 0) + 1
                break
        else:
            entries.append({"label": target_label, "count": 1})
        serialized = navigator.serialize_scene_jump_entries(entries)
        if serialized == current_text:
            return False
        shape["sceneJumpTarget"] = serialized
        return True

    def _record_scene_jump_landing(
        self,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        tree: list[dict[str, Any]],
        shape: dict[str, Any],
        target_scene_id: int,
        *,
        reason: str,
    ) -> None:
        resolution = self._shape_inheritance_resolution(tree)
        raw_shape = find_raw_shape_for_effective(resolution.raw_images, shape)
        target_shape = raw_shape if raw_shape is not None else shape
        shape_id = str(target_shape.get("id") or "").strip()
        if not shape_id:
            return
        updated = False

        def update_latest(items: list[dict[str, Any]]) -> bool:
            def visit(nodes: Any) -> bool:
                if not isinstance(nodes, list):
                    return False
                for node in nodes:
                    if not isinstance(node, dict):
                        continue
                    shapes = node.get("shapes")
                    if isinstance(shapes, list) and visit_shapes(shapes):
                        return True
                    if visit(node.get("children")):
                        return True
                return False

            def visit_shapes(shapes: list[Any]) -> bool:
                nonlocal updated
                for candidate in shapes:
                    if not isinstance(candidate, dict):
                        continue
                    if str(candidate.get("id") or "").strip() == shape_id:
                        # Mutate the lock-protected latest Shape, never replace
                        # it with the caller's stale frequency table. Other
                        # Cells may have revised targets or recorded landings.
                        updated = self._increment_scene_jump_target(candidate, target_scene_id)
                        return True
                    children = candidate.get("children")
                    if isinstance(children, list) and visit_shapes(children):
                        return True
                return False

            visit(items)
            return updated

        snapshot = update_data_annotation_asset_tree(asset_tree_path, update_latest)
        if not updated:
            return
        tree[:] = snapshot.tree
        ctx["asset_tree"] = tree
        self._invalidate_asset_derived_caches(asset_tree_path)
        ctx["images"] = self._index_images(tree)
        self._publish_asset_ctx_revision(ctx, snapshot.revision)
        self._log(
            "detail",
            f"场景跳转历史：记录「{target_shape.get('title') or '未命名'}」落点 #{target_scene_id}（{reason}）",
        )

    def _scene_jump_label_number(self, label: Any) -> int | None:
        return SceneNavigator([]).scene_jump_label_number(label)

    def _resolve_scene_jump_label(self, tree: list[dict[str, Any]], label: Any) -> list[int]:
        return SceneNavigator(self._resolved_asset_tree(tree)).resolve_scene_jump_label(label)

    def _scene_jump_target_ids(self, tree: list[dict[str, Any]], shape: dict[str, Any]) -> list[int]:
        return SceneNavigator(self._resolved_asset_tree(tree)).scene_jump_target_ids(shape)

    def _resolve_scene_image_title_ids(self, tree: list[dict[str, Any]], title: str) -> list[int]:
        tree = self._resolved_asset_tree(tree)
        result = [int(scene_id) for scene_id in SceneNavigator(tree).resolve_scene_image_title_ids(title)]
        seen = set(result)
        expected = str(title or "").strip()
        if not expected:
            return result

        def visit(items: list[dict[str, Any]]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "image") == "image" and str(item.get("title") or "").strip() == expected:
                    image_id = self._image_number(item)
                    if image_id is not None and int(image_id) not in seen:
                        result.append(int(image_id))
                        seen.add(int(image_id))
                children = item.get("children")
                if isinstance(children, list):
                    visit([child for child in children if isinstance(child, dict)])

        visit([item for item in tree if isinstance(item, dict)])
        return result

    def _scene_id_key(self, scene_id: int) -> str:
        for key, value in self.scene_ids.items():
            if int(value) == int(scene_id):
                return key
        return str(scene_id)

    def _scene_match_threshold(self, scene_id: int) -> float:
        key = self._scene_id_key(scene_id)
        return float(self.scene_thresholds.get(key, self.scene_threshold))

    def _scene_matches_id(self, scene_id: int, score: float) -> bool:
        return float(score) >= self._scene_match_threshold(scene_id)

    def _layer3_match_threshold(self, image: dict[str, Any]) -> float:
        configured = image.get("layer3SimilarityThreshold")
        if configured not in (None, ""):
            try:
                return max(0.0, min(100.0, float(configured)))
            except (TypeError, ValueError):
                pass
        return float(self.layer3_similarity_threshold)

    def _scene_match_cache_dir(self) -> Path:
        return codeyun_temp_root("fanxiu-scene-match")

    def _scene_match_cache_key(
        self,
        ctx: dict[str, Any],
        scene_ids: list[int],
        *,
        threshold: float | None,
    ) -> str:
        payload: dict[str, Any] = {
            "version": 2,
            "scene_ids": [int(scene_id) for scene_id in scene_ids],
            "threshold": round(float(threshold), 4) if threshold is not None else None,
            "asset_tree_revision": str(ctx.get("asset_tree_revision") or ""),
            "asset_tree_generation": int(ctx.get("asset_tree_generation") or 0),
        }
        if threshold is None:
            payload["scene_thresholds"] = {
                str(int(scene_id)): round(float(self._scene_match_threshold(int(scene_id))), 4)
                for scene_id in scene_ids
            }
        asset_tree_path = ctx.get("asset_tree_path")
        if isinstance(asset_tree_path, Path) and asset_tree_path.is_file():
            stat = asset_tree_path.stat()
            payload["asset_tree"] = {
                "path": str(asset_tree_path),
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
            }
            image_dir = asset_tree_path.parent / "images"
        else:
            image_dir = None
        image_signatures: list[dict[str, Any]] = []
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        for scene_id in scene_ids:
            image = images.get(int(scene_id))
            if not isinstance(image, dict):
                continue
            filename = str(image.get("filename") or "")
            record: dict[str, Any] = {"scene_id": int(scene_id), "filename": filename}
            if image_dir is not None and filename:
                path = image_dir / filename
                if path.is_file():
                    stat = path.stat()
                    record.update({"mtime_ns": stat.st_mtime_ns, "size": stat.st_size})
            image_signatures.append(record)
        payload["images"] = image_signatures
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _scene_frame_data_url_from_reference(self, ctx: dict[str, Any], image: dict[str, Any]) -> str:
        filename = str(image.get("filename") or "")
        if not filename:
            raise RuntimeError(f"帧「{image.get('title') or self._image_number(image) or '?'}」缺少图片文件")
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("ctx 缺少 asset_tree_path，无法把参考 scene 作为 match(s,x) 的 x")
        path = asset_tree_path.parent / "images" / filename
        if not path.is_file():
            raise RuntimeError(f"参考帧图片不存在：{path}")
        return self._data_url(path.read_bytes())

    def match_scene_frame(
        self,
        ctx: dict[str, Any],
        s: int | str,
        x: int | str,
        *,
        threshold: float | None = None,
        frame_data_url: str | None = None,
    ) -> dict[str, Any]:
        """Return the directed relation ``match(s, x)``.

        ``s`` is the reference scene whose identity rules are evaluated.
        ``x`` is either a live/reference frame data URL or a scene id whose
        reference image should be used as the fact frame.
        """

        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        try:
            reference_scene_id = int(str(s).lstrip("#"))
        except (TypeError, ValueError):
            raise RuntimeError(f"无法解析 match(s,x) 的 s：{s}") from None
        reference_image = images.get(reference_scene_id)
        if not isinstance(reference_image, dict):
            raise RuntimeError(f"找不到 match(s,x) 的 s 场景：#{reference_scene_id}")

        fact_scene_id: int | None = None
        if isinstance(frame_data_url, str) and frame_data_url:
            frame = frame_data_url
        elif isinstance(x, str) and x.startswith("data:image"):
            frame = x
        else:
            try:
                fact_scene_id = int(str(x).lstrip("#"))
            except (TypeError, ValueError):
                raise RuntimeError(f"无法解析 match(s,x) 的 x：{x}") from None
            fact_image = images.get(fact_scene_id)
            if not isinstance(fact_image, dict):
                raise RuntimeError(f"找不到 match(s,x) 的 x 场景：#{fact_scene_id}")
            frame = self._scene_frame_data_url_from_reference(ctx, fact_image)

        scene_threshold = float(threshold if threshold is not None else self._scene_match_threshold(reference_scene_id))
        score = float(self._scene_score(ctx, reference_image, frame) or 0.0)
        return {
            "s": reference_scene_id,
            "x": fact_scene_id if fact_scene_id is not None else "frame",
            "score": score,
            "threshold": scene_threshold,
            "matched": score >= scene_threshold,
        }

    def match_scene_matrix(
        self,
        ctx: dict[str, Any],
        scene_ids: list[int] | None = None,
        *,
        layer: int | None = 2,
        threshold: float | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Build a cached directed ``match(s, x)`` matrix for reference scenes."""

        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if scene_ids is None:
            scene_ids = [
                int(scene_id)
                for scene_id, image in images.items()
                if isinstance(image, dict) and (layer is None or int(View(image).layer) == int(layer))
            ]
        scene_ids = [int(scene_id) for scene_id in scene_ids if isinstance(images.get(int(scene_id)), dict)]
        scene_ids = list(dict.fromkeys(scene_ids))
        scene_threshold = float(threshold) if threshold is not None else None
        cache_key = self._scene_match_cache_key(ctx, scene_ids, threshold=scene_threshold)
        cache_path = self._scene_match_cache_dir() / f"{cache_key}.json"
        if use_cache and cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and cached.get("cache_key") == cache_key:
                    cached["cache_hit"] = True
                    return cached
            except Exception:
                pass

        matches: list[dict[str, Any]] = []
        # Keep one fact frame hot while evaluating every reference rule.  Scene
        # scoring shares OCR by current frame through ctx["_ocr_tokens_cache"];
        # iterating references first would cycle through every fact image and
        # evict/expire that OCR result before the next OCR rule can reuse it.
        # With facts outermost, a full matrix performs at most one OCR pass per
        # fact image instead of one OCR pass per OCR-reference/fact pair.
        for fact_id in scene_ids:
            for reference_id in scene_ids:
                if int(reference_id) == int(fact_id):
                    continue
                result = self.match_scene_frame(ctx, reference_id, fact_id, threshold=scene_threshold)
                if bool(result.get("matched")):
                    matches.append(result)

        payload = {
            "cache_key": cache_key,
            "cache_path": str(cache_path),
            "cache_hit": False,
            "score_mode": "strict_scene_identity",
            "layer": layer,
            "threshold": scene_threshold if scene_threshold is not None else "per_scene",
            "scene_ids": scene_ids,
            "match_count": len(matches),
            "matches": matches,
            "updated_at": time.time(),
        }
        if use_cache:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def _graph_scene_candidate_ids(self, ctx: dict[str, Any]) -> list[int]:
        images = ctx.get("images") or {}
        if not isinstance(images, dict):
            return []
        tree = ctx.get("asset_tree")
        if isinstance(tree, list):
            return default_recognition_candidate_ids(tree, images)
        candidate_ids = [
            int(scene_id)
            for scene_id in self._scene_candidate_ids(ctx)
            if isinstance(images.get(int(scene_id)), dict)
        ]
        if candidate_ids:
            return candidate_ids
        return [
            int(scene_id)
            for scene_id, image in images.items()
            if isinstance(image, dict)
            and int(View(image).layer) <= 2
        ]

    def _graph_scene_candidate_layers(self, ctx: dict[str, Any]) -> list[tuple[int, list[int]]]:
        images = ctx.get("images") or {}
        if not isinstance(images, dict):
            return []
        tree = ctx.get("asset_tree")
        if isinstance(tree, list):
            return [
                *default_recognition_candidate_layers(tree, images),
                (3, layer3_recognition_candidate_ids(tree, images)),
            ]
        candidate_ids = self._graph_scene_candidate_ids(ctx)
        return [
            (
                layer,
                [
                    int(scene_id)
                    for scene_id in candidate_ids
                    if isinstance(images.get(int(scene_id)), dict)
                    and int(View(images[int(scene_id)]).layer) == layer
                ],
            )
            for layer in (1, 2, 3)
        ]

    def _scene_match_edges_for_candidates(
        self,
        ctx: dict[str, Any],
        scene_ids: list[int],
        *,
        trace: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(int(scene_id) for scene_id in scene_ids))
        if len(ids) <= 1:
            return []
        relation_cache = ctx.setdefault("_scene_graph_relation_cache", {})
        if not isinstance(relation_cache, dict):
            relation_cache = {}
            ctx["_scene_graph_relation_cache"] = relation_cache
        edges: list[dict[str, Any]] = []
        for reference_id in ids:
            for fact_id in ids:
                if int(reference_id) == int(fact_id):
                    continue
                relation_key = self._scene_match_cache_key(
                    ctx,
                    [int(reference_id), int(fact_id)],
                    threshold=None,
                )
                cached_relation = relation_cache.get(relation_key)
                if isinstance(cached_relation, dict):
                    result = cached_relation
                    if bool(result.get("matched")):
                        edges.append(result)
                    continue
                try:
                    result = self.match_scene_frame(ctx, reference_id, fact_id)
                except Exception as exc:
                    if trace is not None:
                        trace.append({
                            "event": "graph_edge_error",
                            "s": int(reference_id),
                            "x": int(fact_id),
                            "error": str(exc)[:200],
                        })
                    continue
                relation_cache[relation_key] = result
                if bool(result.get("matched")):
                    edges.append(result)
        return edges

    def _scene_reference_similarity(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str,
    ) -> float | None:
        """Compare the stored scene frame with the current fact frame."""

        try:
            reference_frame = self._scene_frame_data_url_from_reference(ctx, image)
        except Exception:
            return None
        return _image_similarity_percent(self, reference_frame, frame_data_url)

    def _identify_scene_number_by_graph(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
        trace: list[dict[str, Any]] | None = None,
        *,
        include_default_popup_candidates: bool = True,
    ) -> _SceneGraphRecognition:
        ctx.pop("_last_layer3_auxiliary", None)

        def result(
            scene_id: int | None,
            score: float,
            status: str,
            matched_layer: int | None = None,
        ) -> _SceneGraphRecognition:
            recognition = _SceneGraphRecognition(
                scene_id,
                float(score or 0.0),
                str(status or "no_match"),
                matched_layer,
            )
            ctx["_last_scene_recognition_status"] = recognition.status
            return recognition

        frame_quality = self._scene_frame_quality(ctx, frame_data_url)
        if bool(frame_quality.get("unusable")):
            if trace is not None:
                trace.append({"event": "unusable_frame", **frame_quality})
            return result(None, 0.0, "unusable_frame")

        images = ctx.get("images") or {}
        if not isinstance(images, dict) or not images:
            return result(None, 0.0, "unavailable")
        recognition_layers: list[tuple[str, list[int]]] = []
        if preferred_scene_ids is not None:
            candidate_ids = [
                int(scene_id)
                for scene_id in preferred_scene_ids
                if int(scene_id) in images
                and isinstance(images.get(int(scene_id)), dict)
                and self._image_layer(images[int(scene_id)]) <= 2
                and bool(self._scene_identity_shapes(images[int(scene_id)]))
            ]
            recognition_layers.append(("layer0", candidate_ids))
        if preferred_scene_ids is None:
            popup_scene_ids = (
                self._popup_scene_candidate_ids(ctx)
                if include_default_popup_candidates
                else []
            )
            recognition_layers.extend(
                (
                    f"layer{layer}",
                    list(dict.fromkeys([
                        *candidate_ids,
                        *(popup_scene_ids if layer in {1, 2} else []),
                    ])),
                )
                for layer, candidate_ids in self._graph_scene_candidate_layers(ctx)
            )

        best_miss_score = 0.0
        evaluated_scene_ids: set[int] = set()
        for layer_label, candidate_ids in recognition_layers:
            layer_scene_ids = [
                int(scene_id)
                for scene_id in candidate_ids
                if int(scene_id) not in evaluated_scene_ids
            ]
            evaluated_scene_ids.update(layer_scene_ids)
            if layer_label == "layer3":
                scene_id, score, status = self._identify_scene_number_in_layer3_candidates(
                    ctx,
                    frame_data_url,
                    layer_scene_ids,
                    trace=trace,
                )
            else:
                scene_id, score, status = self._identify_scene_number_in_graph_candidates(
                    ctx,
                    frame_data_url,
                    layer_scene_ids,
                    layer_label=layer_label,
                    trace=trace,
                )
            best_miss_score = max(best_miss_score, float(score or 0.0))
            if status not in {"no_candidates", "no_match"}:
                return result(
                    scene_id,
                    score,
                    status,
                    int(layer_label.removeprefix("layer")),
                )
        return result(None, best_miss_score, "no_match")

    def _scene_frame_quality(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
    ) -> dict[str, Any]:
        """Reject a uniform white transition before scene assets can score it.

        The game can emit a pure-white frame between an animated landing and
        the next stable page.  Such a frame contains no identity evidence, but
        an image Shape whose reference crop is mostly blank may otherwise score
        above the normal scene threshold.  This guard therefore belongs before
        every recognition layer and returns ``unknown`` semantics; it is not a
        device-health failure and must not restart MuMu.
        """

        cached = ctx.get("_scene_frame_quality")
        if isinstance(cached, dict) and cached.get("frame_data_url") == frame_data_url:
            cached_summary = cached.get("summary")
            if isinstance(cached_summary, dict):
                return cached_summary
        summary: dict[str, Any] = {
            "unusable": False,
            "reason": "",
        }
        try:
            import numpy as np
            from PIL import Image

            with Image.open(io.BytesIO(self._decode_frame_data_url(frame_data_url))) as source:
                sample = source.convert("RGB").resize((32, 32))
            pixels = np.asarray(sample)
            mean = [float(value) for value in np.mean(pixels, axis=(0, 1))]
            near_white_ratio = float(np.mean(np.all(pixels >= 252, axis=2)))
            unique_colors = int(len(np.unique(pixels.reshape(-1, 3), axis=0)))
            unusable = bool(
                pixels.size
                and near_white_ratio >= 0.999
                and min(mean) >= 252.0
                and unique_colors <= 8
            )
            summary.update({
                "unusable": unusable,
                "reason": "uniform_white_transition" if unusable else "",
                "mean_rgb": [round(value, 3) for value in mean],
                "near_white_ratio": round(near_white_ratio, 6),
                "unique_sample_colors": unique_colors,
            })
        except Exception as exc:
            # Invalid/corrupt image handling remains owned by the normal frame
            # decoder.  Quality inspection must never turn it into a false miss.
            summary["inspection_error"] = str(exc)
        ctx["_scene_frame_quality"] = {
            "frame_data_url": frame_data_url,
            "summary": summary,
        }
        return summary

    def _identify_scene_number_in_layer3_candidates(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        candidate_scene_ids: list[int],
        *,
        trace: list[dict[str, Any]] | None = None,
    ) -> tuple[int | None, float, str]:
        """Rank identity-free Layer 3 references without producing a scene id.

        Layer 3 is diagnostic evidence for an unresolved frame.  Full-frame
        similarity can explain what an unknown frame resembles, but without a
        scene identity it cannot establish where navigation currently is.
        """

        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        scene_ids = [
            scene_id
            for scene_id in list(dict.fromkeys(int(item) for item in candidate_scene_ids))
            if isinstance(images.get(scene_id), dict)
            and not self._scene_identity_shapes(images[scene_id])
        ]
        if not scene_ids:
            return None, 0.0, "no_candidates"

        def similarity(scene_id: int) -> float:
            value = self._scene_reference_similarity(ctx, images[scene_id], frame_data_url)
            return float(value or 0.0)

        workers = min(len(scene_ids), 32)
        if len(scene_ids) == 1:
            scores = [similarity(scene_ids[0])]
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fanxiu-layer3-match") as executor:
                scores = list(executor.map(similarity, scene_ids))

        ranked = sorted(zip(scene_ids, scores), key=lambda item: item[1], reverse=True)
        best_reference_id, best_score = ranked[0]
        auxiliary = {
            "reference_id": int(best_reference_id),
            "score": round(float(best_score), 3),
            "threshold": round(float(self._layer3_match_threshold(images[best_reference_id])), 3),
            "above_threshold": bool(
                best_score >= self._layer3_match_threshold(images[best_reference_id])
            ),
        }
        ctx["_last_layer3_auxiliary"] = auxiliary
        if trace is not None:
            trace.append({
                "event": "layer3_auxiliary",
                **auxiliary,
            })
        return None, best_score, "no_match"

    def _scene_candidate_scores_parallel(
        self,
        ctx: dict[str, Any],
        images: dict[int, dict[str, Any]],
        scene_ids: list[int],
        frame_data_url: str,
    ) -> list[float]:
        def score(scene_id: int) -> float:
            image = images.get(int(scene_id))
            if not isinstance(image, dict):
                return 0.0
            return float(self._scene_score(ctx, image, frame_data_url) or 0.0)

        if len(scene_ids) <= 1:
            return [score(scene_id) for scene_id in scene_ids]
        workers = min(len(scene_ids), 32)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fanxiu-scene-match") as executor:
            return list(executor.map(score, scene_ids))

    def _identify_scene_number_in_graph_candidates(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        candidate_scene_ids: list[int],
        *,
        layer_label: str,
        trace: list[dict[str, Any]] | None = None,
    ) -> tuple[int | None, float, str]:
        if not candidate_scene_ids:
            return None, 0.0, "no_candidates"

        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        scene_ids = [
            scene_id
            for scene_id in list(dict.fromkeys(int(item) for item in candidate_scene_ids))
            if isinstance(images.get(scene_id), dict)
        ]
        scores = self._scene_candidate_scores_parallel(ctx, images, scene_ids, frame_data_url)
        candidates: list[SceneGraphCandidate] = []
        for scene_id, score in zip(scene_ids, scores):
            image = images.get(scene_id)
            if not isinstance(image, dict):
                continue
            matched = score >= self._scene_match_threshold(scene_id)
            candidates.append(SceneGraphCandidate(scene_id=scene_id, score=score, matched=matched))

        matched_ids = [item.scene_id for item in candidates if item.matched]
        edges: list[dict[str, Any]] = []
        if len(matched_ids) > 1:
            edges.extend(self._scene_match_edges_for_candidates(ctx, matched_ids, trace=trace))
            candidates = [
                SceneGraphCandidate(
                    scene_id=item.scene_id,
                    score=item.score,
                    matched=item.matched,
                    frame_similarity=(
                        self._scene_reference_similarity(ctx, images[item.scene_id], frame_data_url)
                        if item.matched and isinstance(images.get(item.scene_id), dict)
                        else None
                    ),
                )
                for item in candidates
            ]
        result = choose_scene_from_graph(candidates, edges, resolve_ties=layer_label == "layer2")
        ambiguity_entry_id = str(ctx.get("entry_id") or getattr(ctx.get("entry"), "entry_id", "") or "")
        if (
            ambiguity_entry_id
            and not ctx.get("_disable_recognition_ambiguity_recording")
            and result.status in {"similarity_tiebreak", "ambiguous"}
            and len(result.unresolved_candidates) >= 2
        ):
            try:
                captured_at = (
                    float(ctx.get("_tick_frame_captured_at") or 0.0)
                    if ctx.get("_tick_frame_data_url") == frame_data_url
                    else time.time()
                )
                record_recognition_ambiguity(
                    entry_id=ambiguity_entry_id,
                    frame_data_url=frame_data_url,
                    captured_at=captured_at,
                    layer=int(str(layer_label).removeprefix("layer") or 0),
                    tied_scene_ids=result.unresolved_candidates,
                    similarities={
                        item.scene_id: item.frame_similarity
                        for item in result.matched_candidates
                        if item.scene_id in result.unresolved_candidates
                    },
                    fallback_scene_id=result.scene_id if result.status == "similarity_tiebreak" else None,
                    asset_tree_sha256=str(ctx.get("asset_tree_revision") or ""),
                    recognizer_version=RECOGNIZER_VERSION,
                )
            except Exception as exc:
                self._log("error", f"识别并列运维事件写入失败：{exc}")
                if trace is not None:
                    trace.append({
                        "event": "graph_ambiguity_persist_failed",
                        "layer": layer_label,
                        "error": str(exc),
                    })
        if result.status == "unknown":
            if trace is not None:
                trace.append({
                    "event": "graph_layer_miss",
                    "layer": layer_label,
                    "candidate_count": len(candidates),
                    "best_scene_id": result.best_similarity_scene_id,
                    "best_score": round(float(result.best_similarity_score), 3),
                })
            return None, float(result.score), "no_match"
        if result.status == "ambiguous":
            if trace is not None:
                trace.append({
                    "event": "graph_ambiguous",
                    "layer": layer_label,
                    "candidates": list(result.unresolved_candidates),
                    "best_scene_id": result.best_similarity_scene_id,
                    "best_score": round(float(result.best_similarity_score), 3),
            })
            return None, float(result.score), "ambiguous"
        if trace is not None:
            trace.append({
                "event": "graph_result",
                "layer": layer_label,
                "status": result.status,
                "scene_id": result.scene_id,
                "score": round(float(result.score), 3),
                "matched_candidates": [
                    {"scene_id": item.scene_id, "score": round(float(item.score), 3)}
                    for item in result.matched_candidates[:12]
                ],
                "best_similarity_scene_id": result.best_similarity_scene_id,
                "best_similarity_score": round(float(result.best_similarity_score), 3),
            })
        return result.scene_id, float(result.score), result.status

    def _scene_key_order(self) -> list[str]:
        return [
            "duplicated",
            "reward",
            "wanling_invite",
            "gift",
            "youli_result",
            "youli_explore",
            "youli",
            "signup_reward",
            "signup",
            "daily_xianyuan_leave_confirm",
            "daily_xianyuan_challenge_result",
            "daily_xianyuan_challenge_confirm",
            "daily_xianyuan_challenge_dialogue",
            "daily_xianyuan_dialogue",
            "daily_xianyuan_detail",
            "daily_xianyuan_list",
            "youli_quick_result",
            "youli_region_detail",
            "youli_purchase_empty",
            "youli_purchase",
            "daily_assistant_one_key_progress",
            "daily_assistant_one_key_confirm",
            "daily_assistant_one_key_result",
            "daily_assistant_tongyou_confirm",
            "daily_assistant_overview",
            "daily_shuangxiu_secret",
            "daily_shuangxiu_detail",
            "daily_shuangxiu_invite",
            "daily_shuangxiu_xianyuan_invite",
            "daily_shuangxiu_training_ready",
            "daily_shuangxiu_complete",
            "daily",
            "settings",
            "world_menu",
            "hide_floating",
            "world",
        ]

    def _identify_scene_number(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
        trace: list[dict[str, Any]] | None = None,
        *,
        include_default_popup_candidates: bool = True,
    ) -> tuple[int | None, float]:
        recognition = self._identify_scene_number_by_graph(
            ctx,
            frame_data_url,
            preferred_scene_ids,
            trace=trace,
            include_default_popup_candidates=include_default_popup_candidates,
        )
        return recognition.scene_id, recognition.score

    def _commit_scene_observation(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        scene_id: int | None,
        score: float,
    ) -> dict[str, Any]:
        """Project one behavior-tree decision without re-running recognition."""

        identity_boxes: list[dict[str, Any]] = []
        all_shape_boxes: list[dict[str, Any]] = []
        frame_width = 0
        frame_height = 0
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        matched_image = images.get(int(scene_id)) if scene_id is not None else None
        asset_tree = ctx.get("asset_tree")
        asset_directory = scene_asset_directory_path(asset_tree, scene_id) if isinstance(asset_tree, list) else ""
        if isinstance(matched_image, dict):
            frame_width, frame_height = self._frame_size(matched_image)
            identity_boxes = [
                self._box(shape, matched_image)
                for shape in self._scene_identity_shapes(matched_image)
            ]
            all_shape_boxes = [
                self._box(shape, matched_image)
                for shape in self._all_scene_shapes(matched_image)
            ]
        captured_at = (
            float(ctx.get("_tick_frame_captured_at") or 0.0)
            if ctx.get("_tick_frame_data_url") == frame_data_url
            else 0.0
        )
        if captured_at <= 0.0:
            captured_at = time.time()
        committed_at = time.time()
        publish_fanxiu_scene_recognition(
            scene_id,
            score,
            scope="decision",
            source=str(ctx.get("_fanxiu_scene_observation_source") or "context"),
            entry_id=str(ctx.get("entry_id") or getattr(ctx.get("entry"), "entry_id", "") or ""),
            asset_generation=int(ctx.get("asset_tree_generation") or 0),
            frame_id=hashlib.sha256(str(frame_data_url).encode("utf-8")).hexdigest()[:16],
            asset_directory=asset_directory,
            boxes=identity_boxes,
            all_shape_boxes=all_shape_boxes,
            frame_width=frame_width,
            frame_height=frame_height,
            captured_at=captured_at,
            committed_at=committed_at,
        )
        return {"scene_id": scene_id, "score": float(score or 0.0), "frame": frame_data_url}

    @contextmanager
    def _scene_observation_probe(self, ctx: dict[str, Any]):
        """Keep nested candidate evaluation out of the authoritative projection."""

        marker = "_fanxiu_scene_observation_probe"
        previous = ctx.get(marker)
        ctx[marker] = True
        try:
            yield
        finally:
            if previous is None:
                ctx.pop(marker, None)
            else:
                ctx[marker] = previous

    def _scene_candidate_ids(self, ctx: dict[str, Any]) -> list[int]:
        return self._scene_candidate_ids_by_kind(ctx, include_popups=None)

    def _popup_scene_candidate_ids(self, ctx: dict[str, Any]) -> list[int]:
        tree = ctx.get("asset_tree")
        if not isinstance(tree, list):
            return []
        return [
            int(scene_id)
            for candidate in self._auto_close_guard_images(tree)
            if isinstance(candidate.get("image"), dict)
            and (scene_id := self._image_number(candidate["image"])) is not None
        ]

    def _scene_candidate_ids_by_kind(self, ctx: dict[str, Any], *, include_popups: bool | None) -> list[int]:
        images = ctx.get("images") or {}
        if not isinstance(images, dict):
            return []
        tree = ctx.get("asset_tree")
        if isinstance(tree, list):
            return default_recognition_candidate_ids(
                tree,
                images,
                include_popups=include_popups,
            )
        if include_popups is True:
            return []
        return [
            int(scene_id)
            for scene_id in self.scene_ids.values()
            if int(scene_id) in images
            and isinstance(images.get(int(scene_id)), dict)
            and int(View(images[int(scene_id)]).layer) <= 2
        ]

    def _scene_jump_edges(self, tree: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
        tree = self._resolved_asset_tree(tree)
        return explicit_scene_jump_edges(tree)

    def _find_scene_route(self, tree: list[dict[str, Any]], start_scene_id: int, target_scene_id: int) -> list[dict[str, Any]] | None:
        """Find a safe route using the same reliable landings as the planner.

        A rare historical destination is evidence of one landing, not a
        dependable edge.  Jump-result waiting uses this answer to decide
        whether to replan immediately or keep observing the current frame.
        """
        if start_scene_id == target_scene_id:
            return []
        edges = self._scene_jump_edges(tree)
        queue: list[tuple[int, list[dict[str, Any]]]] = [(start_scene_id, [])]
        visited = {start_scene_id}
        while queue:
            scene_id, route = queue.pop(0)
            for edge in edges.get(scene_id, []):
                if self._scene_navigation_edge_risk(edge, int(target_scene_id)) is None:
                    continue
                shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
                landings = self._scene_navigation_reliable_landings(
                    self._scene_navigation_landing_probabilities(
                        tree, shape, [int(value) for value in edge.get("target_ids") or []],
                    )
                )
                for next_scene_id in landings:
                    if next_scene_id in visited:
                        continue
                    next_route = [*route, edge]
                    if next_scene_id == target_scene_id:
                        return next_route
                    visited.add(next_scene_id)
                    queue.append((next_scene_id, next_route))
        return None

    def _scene_jump_edge_key(self, edge: dict[str, Any]) -> tuple[Any, ...]:
        shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
        return (
            int(edge.get("source_id") or 0),
            str(shape.get("id") or ""),
            str(shape.get("title") or ""),
            str(shape.get("sceneJumpTarget") or ""),
            tuple(int(scene_id) for scene_id in edge.get("target_ids") or []),
            bool(edge.get("_dynamic_confirm_edge")),
        )

    def _scene_jump_edge_semantic_key(self, edge: dict[str, Any]) -> tuple[Any, ...]:
        """Identify one action independently of mutable landing counters."""

        shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
        return (
            int(edge.get("source_id") or 0),
            str(shape.get("id") or ""),
            str(shape.get("title") or ""),
            bool(edge.get("_dynamic_confirm_edge")),
        )

    def _scene_jump_target_counts(self, tree: list[dict[str, Any]], shape: dict[str, Any]) -> dict[int, int]:
        navigator = SceneNavigator(self._resolved_asset_tree(tree))
        counts: dict[int, int] = {}
        for entry in navigator.parse_scene_jump_entries(shape.get("sceneJumpTarget")):
            count = int(entry.get("count") or 0)
            for scene_id in navigator.resolve_scene_jump_label(entry.get("label")):
                counts[int(scene_id)] = max(counts.get(int(scene_id), 0), count)
        return counts

    def _scene_navigation_shape_risk(self, shape: dict[str, Any]) -> int:
        # The same label can mean a harmless page tab or a destructive choice.
        # Keep the default conservative and let the annotated Shape state its
        # verified navigation role instead of encoding scene IDs here.
        if shape.get("navigationRole") == "non_navigation":
            return 100
        if shape.get("navigationRole") in {"safe_tab", "safe_exit"}:
            return 0
        title = _sanitize_ocr_text(shape.get("title"))
        if not title:
            return 0
        high_risk_keywords = (
            "确认",
            "确定",
            "一键领取",
            "领取",
            "购买",
            "缔结",
            "结契",
            "挑战",
            "拜谒",
            "兑换",
            "升级",
            "升阶",
            "执行",
            "删除",
            "使用",
            "保留",
            "重铸",
            "祭炼",
            "装配",
            "替换",
            "神铸",
            "强化",
            "洗炼",
            "合成",
        )
        return 100 if any(keyword in title for keyword in high_risk_keywords) else 0

    def _scene_navigation_shape_exit_score(self, shape: dict[str, Any]) -> int:
        title = _sanitize_ocr_text(shape.get("title"))
        if title in {"离开", "返回", "关闭", "退出", "关闭下方菜单", "回到世界"}:
            return 1
        return 0

    def _scene_route_navigation_risk(self, route: list[dict[str, Any]]) -> int:
        risk = 0
        for edge in route:
            shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
            risk += self._scene_navigation_shape_risk(shape)
        return risk

    @staticmethod
    def _scene_navigation_target_denied(shape: dict[str, Any], target_scene_id: int) -> bool:
        """Honor a Shape's target scope without encoding route IDs in code.

        A forward control may have historical landings on the world page while
        still being the correct entrance to its own area.  An explicit allow
        list keeps that control available for the area and excludes unrelated
        destinations without requiring a deny entry for every future scene.
        """
        target = str(int(target_scene_id))
        allowed = shape.get("navigationAllowTargets")
        if isinstance(allowed, (list, tuple, set)) and target not in {
            str(value).strip() for value in allowed
        }:
            return True
        targets = shape.get("navigationDenyTargets")
        return isinstance(targets, (list, tuple, set)) and target in {
            str(value).strip() for value in targets
        }

    def _scene_navigation_edge_risk(
        self,
        edge: dict[str, Any],
        target_scene_id: int,
    ) -> int | None:
        shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
        if self._scene_navigation_target_denied(shape, target_scene_id):
            return None
        risk = self._scene_navigation_shape_risk(shape)
        return None if risk >= 100 else risk

    def _scene_navigation_distances_to_target(
        self,
        tree: list[dict[str, Any]],
        navigation_edges: dict[int, list[dict[str, Any]]],
        target_scene_id: int,
    ) -> dict[int, int]:
        reverse_edges: dict[int, set[int]] = {}
        for source_id, edges in navigation_edges.items():
            for edge in edges:
                if self._scene_navigation_edge_risk(edge, int(target_scene_id)) is None:
                    continue
                shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
                reliable = self._scene_navigation_reliable_landings(
                    self._scene_navigation_landing_probabilities(
                        tree, shape, [int(value) for value in edge.get("target_ids") or []],
                    )
                )
                for landing_id in reliable:
                    reverse_edges.setdefault(int(landing_id), set()).add(int(source_id))
        distances = {int(target_scene_id): 0}
        queue = [int(target_scene_id)]
        while queue:
            landing_id = queue.pop(0)
            next_distance = distances[landing_id] + 1
            for source_id in reverse_edges.get(landing_id, set()):
                if source_id in distances:
                    continue
                distances[source_id] = next_distance
                queue.append(source_id)
        return distances

    def _scene_navigation_reachability_values(
        self,
        tree: list[dict[str, Any]],
        navigation_edges: dict[int, list[dict[str, Any]]],
        target_scene_id: int,
    ) -> dict[int, float]:
        """Value-iterate the probability of still reaching the target.

        Every annotated control is a stochastic transition: its landing
        distribution is the posterior of the ``sceneJumpTarget`` frequency
        table.  Solving ``V(s) = γ·max_a Σ_l P(l|a)·V(l)`` with ``V(target)=1``
        answers "how likely is this scene to still arrive *cheaply*", weighing
        the whole landing distribution instead of a single declared hop.

        ``γ`` is the geometric price of one navigation step.  It has to bite:
        at γ≈1 a detour that walks into a page and straight back out inherits
        almost the full value of the world it returns to, so useless round
        trips (storage bag, daily page, …) look as good as the real route.  At
        γ=0.8 every extra step costs 20% of the remaining value.

        Transitions use the confidence lower bound of each landing rather than
        its raw posterior.  A control whose landings are genuinely diffuse —
        a return button that goes wherever the caller came from — simply has
        little evidence for any single destination, so its mass collapses on
        its own.  No control is classified or special-cased by name.
        """

        discount = 0.8
        actions: dict[int, list[dict[int, float]]] = {}
        for source_id, edges in navigation_edges.items():
            source_actions: list[dict[int, float]] = []
            for edge in edges:
                if self._scene_navigation_edge_risk(edge, int(target_scene_id)) is None:
                    continue
                target_ids = [int(value) for value in edge.get("target_ids") or []]
                if not target_ids:
                    continue
                shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
                probabilities = self._scene_navigation_landing_probabilities(
                    tree, shape, target_ids,
                )
                probabilities = self._scene_navigation_reliable_landings(probabilities)
                if probabilities:
                    source_actions.append(probabilities)
            if source_actions:
                actions[int(source_id)] = source_actions
        target = int(target_scene_id)
        values: dict[int, float] = {target: 1.0}
        for _iteration in range(200):
            updated: dict[int, float] = {}
            delta = 0.0
            for source_id, source_actions in actions.items():
                # The goal is absorbing: V(target) = 1 by definition, never the
                # value of its own outgoing controls.
                if source_id == target:
                    continue
                best = 0.0
                for probabilities in source_actions:
                    expected = 0.0
                    for landing_id, probability in probabilities.items():
                        expected += probability * values.get(int(landing_id), 0.0)
                    if expected > best:
                        best = expected
                value = discount * best
                updated[source_id] = value
                delta = max(delta, abs(value - values.get(source_id, 0.0)))
            updated[target] = 1.0
            values = updated
            if delta < 1e-9:
                break
        return values

    _SCENE_NAVIGATION_CONFIDENCE_Z = 1.0
    _SCENE_NAVIGATION_MIN_LANDING_PROBABILITY = 0.05

    def _scene_navigation_reliable_landings(
        self,
        probabilities: Mapping[int, float],
    ) -> dict[int, float]:
        """Ignore incidental destinations when planning a reliable route.

        A historical return control can list many caller-dependent landings.
        Its one-off destination is evidence of a past landing, not a usable
        route.  Keep its probability in diagnostics, but do not propagate a
        target through that edge in value iteration or next-action ranking.
        """
        return {
            int(scene_id): float(probability)
            for scene_id, probability in probabilities.items()
            if float(probability) >= self._SCENE_NAVIGATION_MIN_LANDING_PROBABILITY
        }

    def _scene_navigation_landing_probabilities(
        self,
        tree: list[dict[str, Any]],
        shape: dict[str, Any],
        target_ids: list[int],
        *,
        confidence_z: float | None = None,
    ) -> dict[int, float]:
        """Diluted landings floored at a confidence lower bound.

        The frequency table is evidence, not truth.  Two dilutions stack:

        1. One pseudo-observation goes to an unspecified landing, so a single
           hit is at most 50% likely instead of a certainty.  Rich evidence
           barely moves (315/560 → 56.1%).
        2. The remaining observation error is subtracted as a confidence lower
           bound (normal approximation, z≈1), which is what makes a diffuse
           control collapse on its own: a button that "returns wherever it came
           from" has little evidence for any single destination.

        Nothing here looks at control names or semantics.
        """

        z = (
            float(confidence_z)
            if confidence_z is not None
            else float(self._SCENE_NAVIGATION_CONFIDENCE_Z)
        )
        return posterior_landing_probabilities(
            self._scene_jump_target_counts(tree, shape),
            target_ids,
            confidence_z=z,
        )

    def _scene_navigation_edge_progress_probability(
        self,
        tree: list[dict[str, Any]],
        edge: dict[str, Any],
        target_scene_id: int,
        *,
        distances_to_target: Mapping[int, int],
        landing_probability_cache: dict[tuple[Any, ...], dict[int, float]],
    ) -> tuple[float, list[int]]:
        source_id = int(edge.get("source_id") or 0)
        source_distance = distances_to_target.get(source_id)
        shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
        edge_key = self._scene_jump_edge_key(edge)
        # Populate the shared cache before the reachability guard.  The caller
        # reads ``landing_probability_cache[edge_key]`` unconditionally, so an
        # unreachable source must still leave a posterior here instead of
        # raising KeyError.  The returned progress weight is unchanged.
        landing_probabilities = landing_probability_cache.get(edge_key)
        if landing_probabilities is None:
            landing_probabilities = posterior_landing_probabilities(
                self._scene_jump_target_counts(tree, shape),
                [int(scene_id) for scene_id in edge.get("target_ids") or []],
            )
            landing_probability_cache[edge_key] = landing_probabilities
        if source_distance is None or self._scene_navigation_edge_risk(edge, target_scene_id) is None:
            return 0.0, []
        reliable_landing_ids = self._scene_navigation_reliable_landings(
            self._scene_navigation_landing_probabilities(
                tree, shape, [int(scene_id) for scene_id in edge.get("target_ids") or []],
            )
        )
        progress_landing_ids = [
            int(landing_id)
            for landing_id in reliable_landing_ids
            if int(landing_id) == int(target_scene_id)
            or distances_to_target.get(int(landing_id), source_distance) < source_distance
        ]
        probability = sum(landing_probabilities[landing_id] for landing_id in progress_landing_ids)
        return min(1.0, max(0.0, probability)), progress_landing_ids

    def _rank_scene_next_edge(
        self,
        tree: list[dict[str, Any]],
        edge: dict[str, Any],
        target_scene_id: int,
        *,
        order: int,
        navigation_edges: dict[int, list[dict[str, Any]]] | None = None,
        distances_to_target: Mapping[int, int] | None = None,
        landing_probability_cache: dict[tuple[Any, ...], dict[int, float]] | None = None,
        reachability_values: Mapping[int, float] | None = None,
        log_rejections: bool = True,
    ) -> dict[str, Any] | None:
        source_id = int(edge.get("source_id") or 0)
        target_ids = []
        for scene_id in edge.get("target_ids") or []:
            scene_id = int(scene_id)
            if scene_id not in target_ids:
                target_ids.append(scene_id)
        if not target_ids:
            return None

        shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
        shape_title = str(shape.get("title") or "")
        current_edge_risk = self._scene_navigation_edge_risk(edge, int(target_scene_id))
        if current_edge_risk is None and self._scene_navigation_target_denied(shape, target_scene_id):
            if log_rejections:
                self._log(
                    "detail",
                    f"场景移动：#{source_id}「{shape_title}」标注禁止用于前往 #{target_scene_id}",
                )
            return None
        if current_edge_risk == 0 and shape.get("navigationRole") == "safe_exit":
            if log_rejections:
                self._log(
                    "detail",
                    f"场景移动：#{source_id}「{shape_title}」标注为可导航收尾动作",
                )
        if current_edge_risk is None:
            return None
        target_counts = self._scene_jump_target_counts(tree, shape)
        navigation_edges = navigation_edges if navigation_edges is not None else self._scene_jump_edges(tree)
        distances_to_target = (
            distances_to_target
            if distances_to_target is not None
            else self._scene_navigation_distances_to_target(tree, navigation_edges, int(target_scene_id))
        )
        landing_probability_cache = landing_probability_cache if landing_probability_cache is not None else {}
        progress_probability, progress_landing_ids = self._scene_navigation_edge_progress_probability(
            tree,
            edge,
            int(target_scene_id),
            distances_to_target=distances_to_target,
            landing_probability_cache=landing_probability_cache,
        )
        landing_probabilities = landing_probability_cache[self._scene_jump_edge_key(edge)]
        # A route must reduce the remaining hop count under the currently
        # available graph.  Longer alternatives become progress only after a
        # failed edge is removed and distances are recomputed below.
        if progress_probability <= 0:
            return None
        expected_reachability = 0.0
        if reachability_values is not None:
            # Incremental (advantage) score: only landing mass that leaves this
            # scene better off counts.  Walking into a page and straight back
            # out, or a self-loop, contributes exactly zero, so the ranking
            # separates real progress instead of sharing one baseline.
            current_value = float(reachability_values.get(int(source_id), 0.0))
            action_probabilities = self._scene_navigation_landing_probabilities(
                tree, shape, target_ids,
            )
            action_probabilities = self._scene_navigation_reliable_landings(action_probabilities)
            for landing_id, probability in action_probabilities.items():
                gain = float(reachability_values.get(int(landing_id), 0.0)) - current_value
                if gain > 0:
                    expected_reachability += float(probability) * gain
            if expected_reachability <= 0:
                return None
        elif progress_probability <= 0:
            return None
        # The probability model ranks whole landing distributions, so the
        # dominant landing need not be one the hop-distance model calls
        # progress; fall back to the distribution's mode in that case.
        if progress_landing_ids:
            best_landing_id = max(
                progress_landing_ids,
                key=lambda landing_id: landing_probabilities.get(int(landing_id), 0.0),
            )
        else:
            best_landing_id = max(
                landing_probabilities,
                key=lambda landing_id: landing_probabilities[int(landing_id)],
            )
        source_distance = int(distances_to_target.get(source_id, 1))
        downstream_len = int(distances_to_target.get(best_landing_id, max(0, source_distance - 1)))
        direct = best_landing_id == int(target_scene_id)
        best_count = target_counts.get(best_landing_id, 0)
        posterior_score = int(
            round(
                (expected_reachability if reachability_values is not None else progress_probability)
                * 1_000_000
            )
        )
        self_count = target_counts.get(source_id, 0)
        wrong_target_count = max(
            (
                count
                for landing_id, count in target_counts.items()
                if landing_id != int(target_scene_id)
            ),
            default=0,
        )
        ambiguity = len(target_ids)
        dynamic_penalty = 1 if edge.get("_dynamic_confirm_edge") else 0
        total_path_len = 1 + int(downstream_len)
        direct_target_count = target_counts.get(int(target_scene_id), 0)
        navigation_risk = current_edge_risk
        exit_score = self._scene_navigation_shape_exit_score(shape) if not direct else 0
        score = (
            int(posterior_score),
            int(direct_target_count),
            -int(total_path_len),
            int(exit_score),
            int(best_count),
            -int(self_count),
            -int(wrong_target_count),
            -int(total_path_len),
            -int(navigation_risk),
            -int(ambiguity),
            -int(dynamic_penalty),
            -int(order),
        )
        reason = "下一步主要直达目标" if direct else f"下一步主要落到 #{best_landing_id}"
        if best_count:
            reason += f"，历史命中 {best_count} 次"
        reason += f"，单步进展权重 {progress_probability:.3%}"
        if reachability_values is not None:
            reason += f"，期望增益 {expected_reachability:.3%}"
        if self_count:
            reason += f"，自身落点 {self_count} 次"
        if exit_score:
            reason += "，低风险退出"
        if ambiguity > 1:
            reason += f"，声明落点 {ambiguity} 个"
        if dynamic_penalty:
            reason += "，动态确认边"
        return {
            "edge": edge,
            "score": score,
            "reason": reason,
            "landing_id": best_landing_id,
            "downstream_len": downstream_len,
            "weight": progress_probability,
            # Evidence kept explicit so the read-only planner can report the
            # single-step posterior, the sampling weight and the value-iteration
            # advantage separately.  The live selector only reads ``weight``.
            "progress_probability": progress_probability,
            "expected_reachability": (
                expected_reachability if reachability_values is not None else None
            ),
            "landing_probabilities": dict(landing_probabilities),
            "target_counts": dict(target_counts),
            "declared_target_ids": list(target_ids),
        }

    def _scene_next_edge_candidates(
        self,
        tree: list[dict[str, Any]],
        current_scene_id: int,
        target_scene_id: int,
        *,
        failed_edge_keys: set[tuple[Any, ...]] | None = None,
        read_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Shared provider: rank every safe next edge out of one scene.

        This is the single ranking implementation.  The live ``go_scene``
        selector samples from the returned list; the read-only planner reports
        it.  ``read_only`` only suppresses diagnostic logging so inspection
        never mutates runner status; candidate scores are unaffected.
        """
        failed_edge_keys = failed_edge_keys or set()
        candidates: list[dict[str, Any]] = []
        navigation_edges = self._scene_jump_edges(tree)
        if failed_edge_keys:
            # An attempt-local failed action can sit downstream of the current
            # scene. Remove it throughout this temporary graph before computing
            # distances; otherwise its fictional shortcut can suppress a valid
            # longer route from an upstream scene after a cycle/backtrack.
            navigation_edges = {
                source_id: [
                    edge for edge in edges
                    if self._scene_jump_edge_key(edge) not in failed_edge_keys
                    and self._scene_jump_edge_semantic_key(edge) not in failed_edge_keys
                ]
                for source_id, edges in navigation_edges.items()
            }
        distances_to_target = self._scene_navigation_distances_to_target(
            tree,
            navigation_edges,
            int(target_scene_id),
        )
        reachability_values = self._scene_navigation_reachability_values(
            tree,
            navigation_edges,
            int(target_scene_id),
        )
        landing_probability_cache: dict[tuple[Any, ...], dict[int, float]] = {}
        for order, edge in enumerate(navigation_edges.get(int(current_scene_id), [])):
            # ``sceneJumpTarget`` is a live landing-frequency table.  Every
            # self-loop increments it before the next replan, so the full edge
            # key changes even though this is still the exact same action.
            # Accept both key forms for compatibility, but make semantic keys
            # the stable failure identity used by goto.
            if (
                self._scene_jump_edge_key(edge) in failed_edge_keys
                or self._scene_jump_edge_semantic_key(edge) in failed_edge_keys
            ):
                continue
            ranked = self._rank_scene_next_edge(
                tree,
                edge,
                int(target_scene_id),
                order=order,
                navigation_edges=navigation_edges,
                distances_to_target=distances_to_target,
                landing_probability_cache=landing_probability_cache,
                reachability_values=reachability_values,
                log_rejections=not read_only,
            )
            if ranked is not None:
                candidates.append(ranked)
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates

    def _select_scene_next_edge(
        self,
        tree: list[dict[str, Any]],
        current_scene_id: int,
        target_scene_id: int,
        *,
        failed_edge_keys: set[tuple[Any, ...]] | None = None,
    ) -> dict[str, Any] | None:
        candidates = self._scene_next_edge_candidates(
            tree,
            current_scene_id,
            target_scene_id,
            failed_edge_keys=failed_edge_keys,
        )
        if not candidates:
            return None
        # Unchanged live behaviour: every ranked candidate is sampled by its own
        # probability weight, not a first-best shortcut.  Keep it that way so a
        # dominant-but-uncertain action can still be explored.
        return self._navigation_random.choices(
            candidates,
            weights=[max(1e-12, float(item.get("weight") or 0.0)) for item in candidates],
            k=1,
        )[0]

    def plan_scene_navigation(
        self,
        tree: list[dict[str, Any]],
        source_scene_id: int,
        target_scene_id: int,
        *,
        limit: int = 3,
        max_downstream_steps: int = 16,
    ) -> dict[str, Any]:
        """Read-only dynamic next-step candidate planning for one scene pair.

        Contract: report the same ranked candidates the live selector samples
        from, enriched with single-step landing posteriors and an illustrative
        downstream route.  The numbers are deliberately separate and none is a
        calibrated full-path success rate:

        * ``landing_probabilities`` — single-step posterior of the annotated
          ``sceneJumpTarget`` frequency table (diluted; declared-only landings
          stay at zero).
        * ``single_step_progress_probability`` — summed posterior of landings
          this planner counts as progress; this is the live sampling ``weight``.
        * ``discounted_reachability_gain`` — value-iteration advantage, a
          planning score rather than a probability.
        * ``selection_probability`` — the candidate's share of the sampling
          weights, i.e. exactly what the live ``random.choices`` uses across
          candidates.

        It never samples, never writes runner/scheduler/asset state and never
        touches a device or Kernel.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if (
            isinstance(max_downstream_steps, bool)
            or not isinstance(max_downstream_steps, int)
            or max_downstream_steps < 0
        ):
            raise ValueError("max_downstream_steps must be a nonnegative integer")
        tree = self._resolved_asset_tree(tree)
        images = self._index_images(tree)
        source = int(source_scene_id)
        target = int(target_scene_id)
        result: dict[str, Any] = {
            "status": "ok",
            "planning": "dynamic_next_step_candidates",
            "read_only": True,
            "source_scene_id": source,
            "target_scene_id": target,
            "limit": int(limit),
            "candidate_count": 0,
            "returned_count": 0,
            "candidates": [],
            "contract": (
                "下一步候选排序；landing_probabilities 为单步落点后验，"
                "single_step_progress_probability 为单步进展权重，"
                "discounted_reachability_gain 为规划增益，"
                "selection_probability 为执行采样权重占比；"
                "均非标定全路径成功率"
            ),
        }
        if source not in images:
            return {**result, "status": "unknown_source_scene"}
        if target not in images:
            return {**result, "status": "unknown_target_scene"}
        if source == target:
            return {**result, "status": "already_at_target"}
        source_image = images[source]
        if str(source_image.get("navigationAliasOf") or "").strip() == str(target):
            # A navigation alias is a visual overlay, not an action edge.
            # Live go_scene accepts it only after fresh-frame target identity
            # confirmation; a read-only plan cannot make that observation.
            return {
                **result,
                "status": "alias_confirmation_required",
                "alias_target_scene_id": target,
            }
        navigation_edges = self._scene_jump_edges(tree)
        distances_to_target = self._scene_navigation_distances_to_target(
            tree,
            navigation_edges,
            target,
        )
        if source not in distances_to_target:
            return {**result, "status": "no_path"}
        candidates = self._scene_next_edge_candidates(tree, source, target, read_only=True)
        if not candidates:
            return {**result, "status": "no_path"}
        weights = [max(1e-12, float(item.get("weight") or 0.0)) for item in candidates]
        total_weight = float(sum(weights))
        payloads = [
            self._navigation_candidate_payload(
                item,
                target_scene_id=target,
                selection_probability=(weight / total_weight if total_weight > 0 else 0.0),
                tree=tree,
                navigation_edges=navigation_edges,
                distances_to_target=distances_to_target,
                max_downstream_steps=max_downstream_steps,
            )
            for item, weight in zip(candidates[: int(limit)], weights[: int(limit)])
        ]
        return {
            **result,
            "candidate_count": len(candidates),
            "returned_count": len(payloads),
            "candidates": payloads,
        }

    def _navigation_candidate_payload(
        self,
        ranked: dict[str, Any],
        *,
        target_scene_id: int,
        selection_probability: float,
        tree: list[dict[str, Any]],
        navigation_edges: dict[int, list[dict[str, Any]]],
        distances_to_target: Mapping[int, int],
        max_downstream_steps: int,
    ) -> dict[str, Any]:
        edge = ranked.get("edge") if isinstance(ranked.get("edge"), dict) else {}
        shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
        landing_probabilities = {
            int(scene_id): float(probability)
            for scene_id, probability in (ranked.get("landing_probabilities") or {}).items()
        }
        landing_counts = {
            int(scene_id): int(count)
            for scene_id, count in (ranked.get("target_counts") or {}).items()
        }
        expected_landing = ranked.get("landing_id")
        expected_landing_id = int(expected_landing) if expected_landing is not None else None
        return {
            "source_scene_id": int(edge.get("source_id") or 0),
            "action_shape_id": str(shape.get("id") or ""),
            "action_title": str(shape.get("title") or ""),
            "dynamic_confirm_edge": bool(edge.get("_dynamic_confirm_edge")),
            "declared_target_ids": [
                int(value) for value in ranked.get("declared_target_ids") or []
            ],
            "expected_landing_id": expected_landing_id,
            "expected_landing_probability": (
                landing_probabilities.get(expected_landing_id)
                if expected_landing_id is not None
                else None
            ),
            "landing_probabilities": landing_probabilities,
            "landing_observed_counts": landing_counts,
            "single_step_progress_probability": float(
                ranked.get("progress_probability") or 0.0
            ),
            "discounted_reachability_gain": (
                None
                if ranked.get("expected_reachability") is None
                else float(ranked["expected_reachability"])
            ),
            "selection_probability": float(selection_probability),
            "score": [int(value) for value in ranked.get("score") or ()],
            "reason": str(ranked.get("reason") or ""),
            "downstream": self._navigation_downstream_path(
                tree,
                start_scene_id=expected_landing_id,
                target_scene_id=int(target_scene_id),
                distances_to_target=distances_to_target,
                max_steps=max_downstream_steps,
            ),
        }

    def _navigation_downstream_path(
        self,
        tree: list[dict[str, Any]],
        *,
        start_scene_id: int | None,
        target_scene_id: int,
        distances_to_target: Mapping[int, int],
        max_steps: int,
    ) -> dict[str, Any]:
        """Illustrative continuation that follows this planner's best candidate.

        Every step takes the top-ranked candidate from the shared provider.  A
        missing route, a revisited scene or the step cap truncates the sketch
        explicitly.  This is a schematic, never a new topology search or a
        probability product.
        """
        target = int(target_scene_id)
        sketch: dict[str, Any] = {
            "status": "start",
            "start_scene_id": None if start_scene_id is None else int(start_scene_id),
            "target_scene_id": target,
            "steps": [],
        }
        if start_scene_id is None:
            sketch["status"] = "no_expected_landing"
            return sketch
        current = int(start_scene_id)
        if current == target:
            sketch["status"] = "reached"
            return sketch
        visited = {current}
        steps = 0
        while steps < int(max_steps):
            if current not in distances_to_target:
                sketch["status"] = "no_path"
                return sketch
            candidates = self._scene_next_edge_candidates(
                tree, current, target, read_only=True,
            )
            if not candidates:
                sketch["status"] = "no_path"
                return sketch
            best = candidates[0]
            landing = best.get("landing_id")
            best_edge = best.get("edge") if isinstance(best.get("edge"), dict) else {}
            best_shape = best_edge.get("shape") if isinstance(best_edge.get("shape"), dict) else {}
            sketch["steps"].append({
                "from_scene_id": current,
                "action_title": str(best_shape.get("title") or ""),
                "to_scene_id": None if landing is None else int(landing),
                "single_step_progress_probability": float(
                    best.get("progress_probability") or 0.0
                ),
                "discounted_reachability_gain": (
                    None
                    if best.get("expected_reachability") is None
                    else float(best["expected_reachability"])
                ),
            })
            steps += 1
            if landing is None:
                sketch["status"] = "no_expected_landing"
                return sketch
            landing = int(landing)
            if landing == target:
                sketch["status"] = "reached"
                return sketch
            if landing in visited:
                sketch["status"] = "cycle"
                return sketch
            visited.add(landing)
            current = landing
        sketch["status"] = "depth_limit"
        return sketch


    def _scene_navigation_exploration_priority(self, shape: dict[str, Any]) -> int:
        """Rank bounded navigation actions when the static graph has no route.

        This is deliberately narrower than "click any shape".  go_scene may
        explore only annotated controls whose UI semantics normally move or
        dismiss the current view.  The real landing is then recognized,
        recorded in sceneJumpTarget and fed back into the next planning step.
        """
        if self._scene_navigation_shape_risk(shape) >= 100:
            return 0
        title = _sanitize_ocr_text(shape.get("title"))
        priorities = {
            "回到世界": 500,
            "返回": 480,
            "离开": 460,
            "退出": 440,
            "关闭下方菜单": 430,
            "关闭": 420,
            "空白": 380,
            "取消": 340,
            # Confirmation is intentionally last: it is useful for known
            # prompt/result scenes such as #330, but less universally safe
            # than an explicit return/close control.
            "确定": 200,
            "确认": 200,
            # Some result pages use the same annotated control both as their
            # scene identity and as the only safe dismissal action.
            "继续": 180,
            "点击屏幕继续": 180,
        }
        return priorities.get(title, 0)

    def _select_scene_exploration_edge(
        self,
        tree: list[dict[str, Any]],
        image: dict[str, Any],
        current_scene_id: int,
        target_scene_id: int,
        *,
        failed_edge_keys: set[tuple[Any, ...]] | None = None,
        explored_shape_keys: set[tuple[str, str, str]] | None = None,
        navigation_state_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Choose one safe, evidence-ranked action without requiring a route."""
        failed_edge_keys = failed_edge_keys or set()
        explored_shape_keys = explored_shape_keys or set()
        candidates: list[dict[str, Any]] = []
        for order, shape in enumerate(self._flatten_shapes(image.get("shapes"))):
            priority = self._scene_navigation_exploration_priority(shape)
            if priority <= 0:
                continue
            exploration_key = (
                str(navigation_state_key or f"scene:{int(current_scene_id)}"),
                str(shape.get("id") or ""),
                str(shape.get("title") or ""),
            )
            if exploration_key in explored_shape_keys:
                continue
            target_ids = self._scene_jump_target_ids(tree, shape)
            edge = {
                "source_id": int(current_scene_id),
                "image": image,
                "shape": shape,
                "target_ids": target_ids,
                "_dynamic_exploration": True,
            }
            if (
                self._scene_jump_edge_key(edge) in failed_edge_keys
                or self._scene_jump_edge_semantic_key(edge) in failed_edge_keys
            ):
                continue
            counts = self._scene_jump_target_counts(tree, shape)
            total_count = sum(counts.values())
            self_count = counts.get(int(current_scene_id), 0)
            progressing_count = total_count - self_count
            progress_rate = int(1000 * progressing_count / total_count) if total_count else 500
            candidates.append({
                "edge": edge,
                "score": (
                    int(priority),
                    int(progress_rate),
                    int(progressing_count),
                    -int(self_count),
                    int(total_count),
                    -len(target_ids),
                    -int(order),
                ),
                "reason": (
                    f"静态无路，动态尝试已标注导航动作"
                    + (
                        f"，历史前进 {progressing_count}/{total_count} 次、自身落点 {self_count} 次"
                        if total_count
                        else "，暂无落点历史"
                    )
                ),
                "landing_id": None,
                "downstream_len": None,
            })
        if not candidates:
            return None
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[0]

    def _navigation_frame_signature(self, frame_data_url: str) -> bytes:
        """Return a small visual signature used to distinguish planner states.

        Scene recognition and planner state are intentionally different: two
        visually different screens may both be unknown, or may share one scene
        id.  The signature lets goto continue unknown -> unknown exploration
        without repeatedly clicking the same fallback on an unchanged screen.
        """
        try:
            from PIL import Image

            png_data = self._decode_frame_data_url(frame_data_url)
            with Image.open(io.BytesIO(png_data)) as source:
                resampling = getattr(Image, "Resampling", Image).LANCZOS
                return source.convert("L").resize((16, 16), resampling).tobytes()
        except Exception:
            return str(frame_data_url or "").encode("utf-8", errors="replace")

    def _navigation_state_key(
        self,
        frame_data_url: str,
        current_scene_id: int | None,
        states: list[tuple[int | None, bytes, str]],
    ) -> str:
        """Resolve the current observed screen to a stable state within one goto."""
        signature = self._navigation_frame_signature(frame_data_url)
        for known_scene_id, known_signature, state_key in states:
            if known_scene_id != current_scene_id:
                continue
            if signature == known_signature:
                return state_key
            if len(signature) == 256 and len(known_signature) == 256:
                total_delta = sum(abs(a - b) for a, b in zip(signature, known_signature))
                similarity = 100.0 * (1.0 - total_delta / (255.0 * len(signature)))
                if similarity >= 95.0:
                    return state_key
        prefix = f"scene:{current_scene_id}" if current_scene_id is not None else "unknown"
        state_key = f"{prefix}:state:{len(states) + 1}"
        states.append((current_scene_id, signature, state_key))
        return state_key

    def _try_navigation_fallback_return(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        *,
        navigation_state_key: str,
        target_scene_id: int,
        attempted_actions: dict[tuple[str, str], dict[str, float | int]],
        current_scene_id: int | None = None,
        current_score: float = 0.0,
        incident_recorder: NavigationIncidentRecorder | None = None,
    ) -> _UnknownFallbackDecision:
        """Consume one recovery attempt after continuous unknown qualification.

        #424 has no scene identity and therefore never becomes a Layer 1/2
        recognition or graph node.  Unknown frames may use it while returning
        to the stable world.  #611 is a verified full-screen promotion overlay:
        its own only labelled action is the business-changing ``前往``, while
        the formal lower-left #424 return closed the overlay to #34 in real
        behavior-tree context. Other recognized scenes must still use their own graph edge.
        """
        recognized_overlay_return = (
            int(current_scene_id or 0) == 611 and int(target_scene_id) == 34
        )
        if current_scene_id is not None and not recognized_overlay_return:
            return _UnknownFallbackDecision("unavailable")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        fallback_image = images.get(424) if isinstance(images, dict) else None
        if not isinstance(fallback_image, dict):
            return _UnknownFallbackDecision("unavailable")
        attempt_key = ("__continuous_unknown__", "fallback_return")
        shape = self._find_shape(fallback_image, "返回")
        if not isinstance(shape, dict):
            return _UnknownFallbackDecision("unavailable")
        state = attempted_actions.setdefault(attempt_key, {"count": 0})
        attempt_count = int(state.get("count") or 0)
        if attempt_count >= UNKNOWN_FALLBACK_MAX_ATTEMPTS_PER_NAVIGATION:
            return _UnknownFallbackDecision("exhausted", attempt=attempt_count)

        # #424 is an action template, not proof that every unknown page has a
        # return button.  Locate the masked arrow in the current frame first;
        # zero or multiple candidates must fail closed instead of projecting
        # the reference coordinate onto an unrelated page.
        match_shape = {
            **shape,
            "floating": True,
            "imageMatchRole": "required",
            "ocrMatchRole": "off",
            "_match_scan_box": {
                "x": 0.0,
                "y": 0.75,
                "w": 0.30,
                "h": 0.25,
            },
        }
        try:
            match_result = self._match_shape(
                ctx,
                fallback_image,
                match_shape,
                frame_data_url,
                condition="image",
            )
        except Exception as exc:
            self._log("warning", f"场景移动：#424「返回」图形匹配失败，保留现场：{exc}")
            return _UnknownFallbackDecision("unavailable", attempt=attempt_count)
        click_point = self._shape_match_resolved_click_point(
            fallback_image,
            match_shape,
            match_result,
        )
        if not bool(match_result.get("matched")) or click_point is None:
            if not bool(state.get("not_visible_logged")):
                reason = str(match_result.get("reason") or "not_matched")
                candidate_count = len(match_result.get("matches") or [])
                self._log(
                    "warning",
                    "场景移动：当前帧未唯一确认左下返回按钮，禁止使用 #424 固定坐标"
                    f"（reason={reason}, candidates={candidate_count}）",
                )
                state["not_visible_logged"] = 1
            return _UnknownFallbackDecision("unavailable", attempt=attempt_count)

        attempt_count += 1
        state["count"] = attempt_count
        state.pop("not_visible_logged", None)
        fallback_reason = (
            "#611 推广弹窗使用正式左下返回"
            if recognized_overlay_return
            else "unknown 回世界使用正式左下返回"
        )
        if incident_recorder is not None:
            incident_recorder.trigger(
                trigger_type="normal_actions_exhausted",
                trigger_label=f"{fallback_reason}，开始使用 #424[返回]",
                threshold={
                    "fallback_scene_id": 424,
                    "fallback_attempt": attempt_count,
                    "continuous_unknown_seconds": DEFAULT_GO_SCENE_CONTINUOUS_UNKNOWN_SECONDS,
                    "max_attempts_per_navigation": UNKNOWN_FALLBACK_MAX_ATTEMPTS_PER_NAVIGATION,
                },
                frame_data_url=frame_data_url,
                current_scene_id=current_scene_id,
                current_score=current_score,
                candidate_scene_ids=[
                    scene_id
                    for scene_id in (current_scene_id, target_scene_id, 424)
                    if scene_id is not None
                ],
            )
            incident_recorder.mark_fallback_used()
        x, y = click_point
        with self._lock:
            self._status.update({
                "phase": "go_scene_navigation_fallback",
                "current_scene": None,
                "message": (
                    f"场景移动：{fallback_reason}（第 {attempt_count} 次），"
                    f"随后重新识别并规划到 #{target_scene_id}"
                ),
                "updated_at": time.time(),
            })
        self._log(
            "action",
            f"场景移动：{fallback_reason}，点击一次 #424「返回」（第 {attempt_count}/"
            f"{UNKNOWN_FALLBACK_MAX_ATTEMPTS_PER_NAVIGATION} 次），随后重新计时并规划到 #{target_scene_id}",
        )
        self._save_action_trace(
            ctx,
            fallback_image,
            {
                "kind": "click",
                "point": [float(x), float(y)],
                "label": "click #424 返回 navigation_fallback",
                "shape_title": shape.get("title"),
                "shape_id": shape.get("id"),
                "source_scene_id": current_scene_id,
                "target_scene_id": int(target_scene_id),
                "navigation_fallback_scene_id": 424,
                "navigation_state_key": navigation_state_key,
                "navigation_fallback_attempt": attempt_count,
                "navigation_fallback_match": {
                    "similarity": match_result.get("similarity"),
                    "box": match_result.get("box"),
                    "resolved_box": match_result.get("resolved_box"),
                    "reason": match_result.get("reason"),
                },
            },
            frame_data_url=frame_data_url,
        )
        self._click_frame_point(ctx, fallback_image, x, y, save_action_trace=False)
        self._clear_tick_frame(ctx)
        return _UnknownFallbackDecision("clicked", attempt=attempt_count, point=(float(x), float(y)))

    def _try_navigation_backdrop_exit(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        *,
        navigation_state_key: str,
        target_scene_id: int,
        attempted_actions: dict[tuple[str, str], dict[str, float | int]],
        current_scene_id: int | None = None,
        current_score: float = 0.0,
        incident_recorder: NavigationIncidentRecorder | None = None,
    ) -> _UnknownFallbackDecision:
        """Consume one declared backdrop-exit probe for an unknown overlay.

        凡修没有为菜单准备关闭按钮，退出方式是点背景；全屏活动宣传封面
        （云梦试剑、万域问鼎这类）连左下返回箭头都不画，所以上面基于
        “箭头图形是否可见”的 #424 分支永远不可用，导航阶梯只能停在修复
        边界。 这里复用 #424 在资产树里声明的左下退出角坐标，把它当作
        “背景退出”探针，而不是在代码里另存一套坐标。

        有界且失败即停：

        * 只在连续 unknown 的恢复阶梯里工作，已识别场景永不触发；
        * 每次导航最多 ``UNKNOWN_BACKDROP_EXIT_MAX_ATTEMPTS_PER_NAVIGATION`` 次；
        * 每次点击后画面必须变化（比对本次与上次探针帧），否则立即停手；
        * 声明坐标必须落在左下角，越界就拒绝点击；
        * ``#424`` 可显式声明 ``backdropExitProbe=false`` 关闭该探针。
        """

        if current_scene_id is not None:
            return _UnknownFallbackDecision("unavailable")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        fallback_image = images.get(424) if isinstance(images, dict) else None
        if not isinstance(fallback_image, dict):
            return _UnknownFallbackDecision("unavailable")
        if fallback_image.get("backdropExitProbe") is False:
            return _UnknownFallbackDecision("unavailable")
        shape = self._find_shape(fallback_image, "返回")
        if not isinstance(shape, dict):
            return _UnknownFallbackDecision("unavailable")
        width, height = self._frame_size(fallback_image)
        x = (float(shape.get("x") or 0.0) + float(shape.get("w") or 0.0) / 2) * width
        y = (float(shape.get("y") or 0.0) + float(shape.get("h") or 0.0) / 2) * height
        if width <= 0 or height <= 0 or x > width * 0.35 or y < height * 0.7:
            self._log(
                "warning",
                f"场景移动：背景退出声明点 ({x:.0f},{y:.0f}) 不在左下安全区，拒绝点击",
            )
            return _UnknownFallbackDecision("unavailable")
        attempt_key = ("__continuous_unknown__", "backdrop_exit")
        state = attempted_actions.setdefault(attempt_key, {"count": 0})
        attempt_count = int(state.get("count") or 0)
        if attempt_count >= UNKNOWN_BACKDROP_EXIT_MAX_ATTEMPTS_PER_NAVIGATION:
            return _UnknownFallbackDecision("exhausted", attempt=attempt_count)
        previous_frame = state.get("last_frame")
        if isinstance(previous_frame, str) and previous_frame:
            # 上一次背景点击没有改变画面，说明这里不是“点背景就退出”的浮层，
            # 再点一次只会重复同一个无效动作，保留现场交给修复边界。
            similarity = _image_similarity_percent(self, previous_frame, frame_data_url)
            if similarity >= 99.0:
                return _UnknownFallbackDecision("exhausted", attempt=attempt_count)
        attempt_count += 1
        state["count"] = attempt_count
        state["last_frame"] = frame_data_url
        reason = "unknown 全屏浮层使用声明的左下背景退出"
        if incident_recorder is not None:
            incident_recorder.trigger(
                trigger_type="normal_actions_exhausted",
                trigger_label=f"{reason}，开始背景退出探针",
                threshold={
                    "backdrop_exit_attempt": attempt_count,
                    "max_attempts_per_navigation": UNKNOWN_BACKDROP_EXIT_MAX_ATTEMPTS_PER_NAVIGATION,
                    "declared_point": [round(float(x), 1), round(float(y), 1)],
                    "continuous_unknown_seconds": DEFAULT_GO_SCENE_CONTINUOUS_UNKNOWN_SECONDS,
                },
                frame_data_url=frame_data_url,
                current_scene_id=current_scene_id,
                current_score=current_score,
                candidate_scene_ids=[target_scene_id, 424],
            )
            incident_recorder.mark_fallback_used()
        with self._lock:
            self._status.update({
                "phase": "go_scene_navigation_fallback",
                "current_scene": None,
                "message": (
                    f"场景移动：{reason}（第 {attempt_count} 次），"
                    f"随后重新识别并规划到 #{target_scene_id}"
                ),
                "updated_at": time.time(),
            })
        self._log(
            "action",
            f"场景移动：{reason}，点击一次左下背景 ({x:.0f},{y:.0f})（第 {attempt_count}/"
            f"{UNKNOWN_BACKDROP_EXIT_MAX_ATTEMPTS_PER_NAVIGATION} 次），随后重新计时并规划到 #{target_scene_id}",
        )
        self._save_action_trace(
            ctx,
            fallback_image,
            {
                "kind": "click",
                "point": [float(x), float(y)],
                "label": "click backdrop exit navigation_fallback",
                "shape_title": shape.get("title"),
                "shape_id": shape.get("id"),
                "source_scene_id": current_scene_id,
                "target_scene_id": int(target_scene_id),
                "navigation_backdrop_exit_attempt": attempt_count,
                "navigation_state_key": navigation_state_key,
            },
            frame_data_url=frame_data_url,
        )
        self._click_frame_point(ctx, fallback_image, x, y, save_action_trace=False)
        self._clear_tick_frame(ctx)
        return _UnknownFallbackDecision("clicked", attempt=attempt_count, point=(float(x), float(y)))

    def _wait_or_click_navigation_fallback_return(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        stop_event: threading.Event,
        *,
        navigation_state_key: str,
        target_scene_id: int,
        attempted_actions: dict[tuple[str, str], dict[str, float | int]],
        current_scene_id: int | None = None,
        current_score: float = 0.0,
        incident_recorder: NavigationIncidentRecorder | None = None,
    ):
        decision = self._try_navigation_fallback_return(
            ctx,
            frame_data_url,
            navigation_state_key=navigation_state_key,
            target_scene_id=target_scene_id,
            attempted_actions=attempted_actions,
            current_scene_id=current_scene_id,
            current_score=current_score,
            incident_recorder=incident_recorder,
        )
        used_backdrop_exit = False
        if decision.status == "unavailable":
            # 画面上没有画出左下返回箭头（典型是全屏活动封面），退回复工具
            # 仍然存在：本游戏所有菜单都靠点背景退出。
            decision = self._try_navigation_backdrop_exit(
                ctx,
                frame_data_url,
                navigation_state_key=navigation_state_key,
                target_scene_id=target_scene_id,
                attempted_actions=attempted_actions,
                current_scene_id=current_scene_id,
                current_score=current_score,
                incident_recorder=incident_recorder,
            )
            used_backdrop_exit = decision.status in {"clicked", "exhausted"}
        if decision.status == "clicked":
            yield from self._wait_action_settle(ctx, stop_event, seconds=1.5)
            if incident_recorder is not None and incident_recorder.active:
                after_frame = self._screencap(ctx)
                after_scene_id, after_score = self._identify_scene_number(ctx, after_frame)
                fallback_image = (ctx.get("images") or {}).get(424)
                fallback_shape = self._find_shape(fallback_image, "返回")
                incident_recorder.record_action(
                    kind="fallback",
                    source_scene_id=current_scene_id,
                    source_score=current_score,
                    shape=fallback_shape,
                    reason=(
                        "连续一分钟 unknown 后使用声明的左下背景退出"
                        if used_backdrop_exit
                        else "连续一分钟 unknown 后的通用返回投影"
                    ),
                    before_frame=frame_data_url,
                    landing_scene_id=after_scene_id,
                    landing_score=after_score,
                    after_frame=after_frame,
                    frame_similarity=_image_similarity_percent(self, frame_data_url, after_frame),
                    navigation_state_key=navigation_state_key,
                    attempt=decision.attempt,
                    point=decision.point,
                )
            return True
        if decision.status == "exhausted":
            label = "左下背景退出" if used_backdrop_exit else "#424「返回」"
            self._log(
                "warning",
                f"场景移动：本次导航已尝试 {label}{decision.attempt} 次，停止重复点击并保留现场",
            )
        return False

    def _scene_route_candidate_ids(self, tree: list[dict[str, Any]], target_scene_id: int) -> list[int]:
        tree = self._resolved_asset_tree(tree)
        images = self._index_images(tree)
        distances_to_target = self._scene_navigation_distances_to_target(
            tree, self._scene_jump_edges(tree), int(target_scene_id),
        )

        def is_route_candidate(scene_id: int) -> bool:
            image = images.get(int(scene_id))
            if not isinstance(image, dict):
                return False
            try:
                declared_layer = int(image.get("layer") or self._image_layer(image))
            except (TypeError, ValueError):
                declared_layer = self._image_layer(image)
            return not (declared_layer >= 3 and not self._scene_identity_shapes(image))

        candidates = SceneNavigator(tree).route_candidate_ids(
            target_scene_id,
            confirmation_scene_ids=(),
        )
        candidates = [
            int(scene_id)
            for scene_id in candidates
            if is_route_candidate(int(scene_id))
            and int(scene_id) in distances_to_target
        ]
        candidate_set = {int(scene_id) for scene_id in candidates}
        image_ids: list[int] = []

        def collect_image_ids(items: list[dict[str, Any]]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                image_id = self._image_number(item)
                if (
                    image_id is not None
                    and is_route_candidate(int(image_id))
                    and int(image_id) not in image_ids
                ):
                    image_ids.append(int(image_id))
                children = item.get("children")
                if isinstance(children, list):
                    collect_image_ids([child for child in children if isinstance(child, dict)])

        collect_image_ids([item for item in tree if isinstance(item, dict)])

        reachable_sources: list[tuple[int, int]] = []
        for image_id in image_ids:
            if int(image_id) == int(target_scene_id) or int(image_id) in candidate_set:
                continue
            distance = distances_to_target.get(int(image_id))
            if distance is None:
                continue
            reachable_sources.append((distance, int(image_id)))
        for _route_len, image_id in sorted(reachable_sources, key=lambda item: (item[0], image_ids.index(item[1]))):
            if image_id not in candidate_set:
                candidates.append(image_id)
                candidate_set.add(image_id)

        return candidates

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
        diagnostic = ""
        try:
            evidence = build_unknown_evidence(
                self,
                ctx,
                frame_data_url,
                label=f"go_scene_{target_scene_id}",
                expected_scene_ids=[target_scene_id],
                last_scene_id=current_scene_id,
                last_score=0.0,
            )
            report_suffix = f"，证据={evidence.report_path}" if evidence.report_path else ""
            frame_suffix = f"，截图={evidence.frame_path}" if evidence.frame_path else ""
            diagnostic = f"；unknown诊断={evidence.classification}：{evidence.suggestion}{frame_suffix}{report_suffix}"
        except Exception as exc:
            diagnostic = f"；unknown诊断生成失败：{exc}"
        detail = "；".join([
            f"目标场景=#{target_scene_id}",
            f"当前/点击前场景={current_text}",
            f"动作 shape={action_title}",
            f"累计等待={elapsed_seconds:.1f}s",
            f"最近识别={history[-1] if history else '无'}",
        ])
        incident_recorder = ctx.get("_navigation_incident_recorder")
        if isinstance(incident_recorder, NavigationIncidentRecorder):
            incident_recorder.trigger(
                trigger_type="recovery_exhausted",
                trigger_label="统一识别或有界恢复耗尽，仍缺少可靠导航落点",
                threshold={
                    "fallback_max_attempts_per_navigation": UNKNOWN_FALLBACK_MAX_ATTEMPTS_PER_NAVIGATION,
                    "elapsed_seconds": round(float(elapsed_seconds or 0.0), 1),
                },
                frame_data_url=frame_data_url,
                current_scene_id=current_scene_id,
                current_score=0.0,
                candidate_scene_ids=[
                    scene_id
                    for scene_id in (current_scene_id, target_scene_id)
                    if scene_id is not None
                ],
            )
            incident_recorder.finalize(
                status="unrecovered",
                final_scene_id=current_scene_id,
                final_frame=frame_data_url,
                message=detail,
            )
            ctx.pop("_navigation_incident_recorder", None)
        self._log("error", f"场景跳转缺少可靠标注，已中断：{detail}{diagnostic}")
        context = self._behavior_tree_context(ctx, asset_tree_path)
        context.require_scene_repair(
            current_scene_id, frame_data_url, expected_scene_ids=[target_scene_id],
            reason=f"场景导航的有界恢复已耗尽：{detail}{diagnostic}",
        )

    def _require_assets(self, ctx: dict[str, Any]) -> None:
        images: dict[int, dict[str, Any]] = ctx["images"]
        if not images:
            raise RuntimeError("缺少帧标注，请先保存帧树")

    def _image(self, ctx: dict[str, Any], key: str) -> dict[str, Any] | None:
        return ctx["images"].get(self.scene_ids[key])

    def _flatten_shapes(self, shapes: Any) -> list[dict[str, Any]]:
        return _flatten_shapes(shapes)

    def _find_shape(self, image: dict[str, Any] | None, *titles: str, contains: bool = False) -> dict[str, Any] | None:
        if not image:
            return None
        view = View(image)
        for shape in view.get_shapes():
            if not shape.title:
                continue
            if any((contains and title in shape.title) or (not contains and shape.title == title) for title in titles):
                return shape.raw
        return None

    def _effective_shape_source_image(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
    ) -> dict[str, Any]:
        # Inherited Shape configuration always runs in the child/host scene's
        # image context.  Its source scene is only the annotation owner.
        return image

    def _image_layer(self, image: dict[str, Any]) -> int:
        return int(View(image).layer)

    def _navigation_scene_id(
        self,
        ctx: dict[str, Any],
        scene_id: int | None,
        frame_data_url: str | None = None,
    ) -> int | None:
        if scene_id is None:
            return None
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image = images.get(int(scene_id))
        if isinstance(image, dict):
            try:
                declared_layer = int(image.get("layer") or self._image_layer(image))
            except (TypeError, ValueError):
                declared_layer = self._image_layer(image)
            if declared_layer >= 3 and not self._scene_identity_shapes(image):
                return None
        return int(scene_id)

    def _scene_identity_shapes(self, image: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            shape.raw
            for shape in View(image).get_shapes(include_groups=False)
            if shape.is_scene_identity
        ]

    def _all_scene_shapes(self, image: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            shape.raw
            for shape in View(image).get_shapes(include_groups=False)
        ]

    def _popup_match_shapes(self, image: dict[str, Any]) -> list[dict[str, Any]]:
        shapes = [shape for shape in self._flatten_shapes(image.get("shapes")) if shape.get("kind") != "group"]
        identity = [shape for shape in shapes if bool(shape.get("isSceneIdentity"))]
        return identity or shapes[:4]

    def _frame_size(self, image: dict[str, Any]) -> tuple[int, int]:
        return _frame_size(image)

    def _box(self, shape: dict[str, Any], image: dict[str, Any]) -> dict[str, Any]:
        return ActionPlanner().shape_box(image, shape)

    def _data_url(self, data: bytes) -> str:
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")

    def _action_trace_dir(self) -> Path:
        return codeyun_temp_root("fanxiu_action_trace")

    def _action_trace_max_files(self) -> int:
        raw = os.environ.get("CODEYUN_FANXIU_ACTION_TRACE_MAX_FILES")
        try:
            value = int(raw) if raw is not None else _ACTION_TRACE_DEFAULT_MAX_FILES
        except ValueError:
            value = _ACTION_TRACE_DEFAULT_MAX_FILES
        return max(0, value)

    def _action_trace_retention_int(self, name: str, default: int) -> int:
        raw = os.environ.get(name)
        try:
            value = int(raw) if raw is not None else default
        except ValueError:
            value = default
        return max(0, value)

    def _action_trace_max_bytes(self) -> int:
        return self._action_trace_retention_int(
            "CODEYUN_FANXIU_ACTION_TRACE_MAX_BYTES",
            _ACTION_TRACE_DEFAULT_MAX_BYTES,
        )

    def _action_trace_max_age_seconds(self) -> int:
        return self._action_trace_retention_int(
            "CODEYUN_FANXIU_ACTION_TRACE_MAX_AGE_SECONDS",
            _ACTION_TRACE_DEFAULT_MAX_AGE_SECONDS,
        )

    def _action_trace_index_max_bytes(self) -> int:
        return self._action_trace_retention_int(
            "CODEYUN_FANXIU_ACTION_TRACE_INDEX_MAX_BYTES",
            _ACTION_TRACE_DEFAULT_INDEX_MAX_BYTES,
        )

    def _action_trace_enabled(self) -> bool:
        value = str(os.environ.get("CODEYUN_FANXIU_ACTION_TRACE", "1")).strip().lower()
        return value not in {"0", "false", "no", "off"}

    def _action_trace_prune_interval(self) -> int:
        raw = os.environ.get("CODEYUN_FANXIU_ACTION_TRACE_PRUNE_INTERVAL")
        try:
            value = int(raw) if raw is not None else _ACTION_TRACE_DEFAULT_PRUNE_INTERVAL
        except ValueError:
            value = _ACTION_TRACE_DEFAULT_PRUNE_INTERVAL
        return max(1, value)

    def _action_trace_prune_due(self) -> bool:
        interval = self._action_trace_prune_interval()
        count = int(getattr(self, "_action_trace_writes_since_prune", -1)) + 1
        if count <= 0 or count >= interval:
            self._action_trace_writes_since_prune = 0
            return True
        self._action_trace_writes_since_prune = count
        return False

    def _action_trace_prune_target(self, max_files: int) -> int:
        raw = os.environ.get("CODEYUN_FANXIU_ACTION_TRACE_PRUNE_RESERVE")
        try:
            reserve = int(raw) if raw is not None else _ACTION_TRACE_DEFAULT_PRUNE_RESERVE
        except ValueError:
            reserve = _ACTION_TRACE_DEFAULT_PRUNE_RESERVE
        reserve = max(0, min(int(max_files), reserve))
        return max(0, int(max_files) - reserve)

    def _decode_frame_data_url(self, frame_data_url: str) -> bytes:
        if not frame_data_url.startswith("data:image"):
            raise RuntimeError("frame_data_url 不是图片 data URL")
        return base64.b64decode(frame_data_url.split(",", 1)[1])

    def _annotate_action_trace_png(self, png_data: bytes, action: dict[str, Any]) -> bytes:
        from PIL import Image, ImageDraw, ImageFont

        with Image.open(io.BytesIO(png_data)) as source:
            image = source.convert("RGBA")
        draw = ImageDraw.Draw(image)
        kind = str(action.get("kind") or "")
        color = (255, 48, 48, 255)
        if kind == "drag":
            start = action.get("start") if isinstance(action.get("start"), (list, tuple)) else (0, 0)
            end = action.get("end") if isinstance(action.get("end"), (list, tuple)) else (0, 0)
            sx, sy = float(start[0]), float(start[1])
            ex, ey = float(end[0]), float(end[1])
            draw.line((sx, sy, ex, ey), fill=color, width=8)
            radius = 18
            draw.ellipse((sx - radius, sy - radius, sx + radius, sy + radius), outline=(255, 214, 0, 255), width=6)
            draw.ellipse((ex - radius, ey - radius, ex + radius, ey + radius), outline=color, width=6)
        else:
            point = action.get("point") if isinstance(action.get("point"), (list, tuple)) else (0, 0)
            x, y = float(point[0]), float(point[1])
            radius = 22
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color, width=7)
            draw.line((x - radius * 1.5, y, x + radius * 1.5, y), fill=color, width=5)
            draw.line((x, y - radius * 1.5, x, y + radius * 1.5), fill=color, width=5)
        label = str(action.get("label") or kind or "action")
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        draw.rectangle((8, 8, min(image.width - 8, 8 + max(260, len(label) * 8)), 42), fill=(0, 0, 0, 160))
        draw.text((14, 16), label[:120], fill=(255, 255, 255, 255), font=font)
        out = io.BytesIO()
        image.convert("RGB").save(out, format="PNG")
        return out.getvalue()

    def _prune_action_trace_files(
        self,
        trace_dir: Path,
        *,
        max_files: int,
        target_files: int | None = None,
        max_bytes: int | None = None,
        max_age_seconds: int | None = None,
    ) -> None:
        if max_files <= 0:
            return
        byte_limit = self._action_trace_max_bytes() if max_bytes is None else max(0, int(max_bytes))
        age_limit = self._action_trace_max_age_seconds() if max_age_seconds is None else max(0, int(max_age_seconds))
        prune_temp_files(
            trace_dir,
            patterns=("*.png",),
            max_files=max_files,
            target_files=target_files,
            max_bytes=byte_limit,
            target_bytes=max(0, int(byte_limit * 0.9)),
            max_age_seconds=age_limit,
        )
        trim_file_tail(
            trace_dir / "index.jsonl",
            max_bytes=self._action_trace_index_max_bytes(),
        )

    def _save_action_trace(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        action: dict[str, Any],
        *,
        frame_data_url: str | None = None,
    ) -> None:
        if not self._action_trace_enabled():
            return
        max_files = self._action_trace_max_files()
        if max_files <= 0:
            return
        try:
            frame = frame_data_url
            if not frame:
                try:
                    frame = self._capture_frame(ctx)
                except Exception:
                    frame = self._screencap(ctx)
            png_data = self._decode_frame_data_url(frame)
            trace_dir = self._action_trace_dir()
            trace_dir.mkdir(parents=True, exist_ok=True)
            stamp = _now().strftime("%Y%m%d_%H%M%S_%f")
            image_number = self._image_number(image) or "unknown"
            kind = str(action.get("kind") or "action")
            stem = f"{stamp}_{kind}_scene{image_number}"
            raw_path = trace_dir / f"{stem}_before.png"
            marked_path = trace_dir / f"{stem}_marked.png"
            raw_path.write_bytes(png_data)
            marked_path.write_bytes(self._annotate_action_trace_png(png_data, action))
            record = {
                "time": _now().isoformat(timespec="seconds"),
                "kind": kind,
                "image_number": image_number,
                "image_title": image.get("title"),
                "action": action,
                "before": str(raw_path),
                "marked": str(marked_path),
                "execution_task": self._status.get("current_task"),
                "phase": self._status.get("phase"),
            }
            with (trace_dir / "index.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            if self._action_trace_prune_due():
                self._prune_action_trace_files(
                    trace_dir,
                    max_files=max_files,
                    target_files=self._action_trace_prune_target(max_files),
                )
        except Exception as exc:
            self._log("detail", f"动作回溯截图保存失败：{exc}")

    def _set_tick_frame(self, ctx: dict[str, Any], frame_data_url: str | None) -> None:
        if frame_data_url:
            if ctx.get("_tick_frame_data_url") != frame_data_url:
                ctx["_tick_frame_captured_at"] = time.time()
            ctx["_tick_frame_data_url"] = frame_data_url

    def _clear_tick_frame(self, ctx: dict[str, Any]) -> None:
        ctx.pop("_tick_frame_data_url", None)
        ctx.pop("_tick_frame_captured_at", None)

    def _capture_frame(self, ctx: dict[str, Any]) -> str:
        entry: Any = ctx["entry"]
        while True:
            try:
                response = _screencap_game_window2_service() if entry.mode == "local" else _remote_game_window2_screencap(entry)
                break
            except Exception as exc:
                if entry.mode != "local":
                    raise
                state = record_mumu_adb_failure(exc, recover=True)
                recovered = bool(state.get("recovered"))
                with self._lock:
                    self._status["device_health"] = state
                    if recovered:
                        self._log_locked(
                            "warning",
                            "ADB 持续取帧失败后已恢复 MuMu 安卓容器；当前 GUI 事务作废",
                            scope="guard",
                            item_id="device_health",
                        )
                    self._sync_guard_status_locked()
                if recovered:
                    raise FanxiuEmulatorRestartRequired(
                        "ADB 持续取帧失败；已完整重启 MuMu，当前业务尝试作废",
                        evidence={
                            "reason": "adb_frame_recovery",
                            "device_health": state,
                            "error": str(exc),
                        },
                        recovery_succeeded=True,
                    ) from exc
                if state.get("recovery_deferred") != "frame_unusable_observation_window":
                    raise
                elapsed = max(0.0, float(state.get("frame_unusable_elapsed_seconds") or 0.0))
                threshold = max(elapsed, float(state.get("frame_unusable_recovery_seconds") or 30.0))
                wait_seconds = max(0.1, min(2.0, threshold - elapsed))
                self._log(
                    "detail",
                    f"ADB 黑帧仍在有界观察窗口 {elapsed:.1f}/{threshold:.1f}s，{wait_seconds:.1f}s 后重试同一截图事务",
                )
                time.sleep(wait_seconds)
        # Local Tasks and uploaded observations share one annotation canvas.
        # Native dimensions remain in context for image bandwidth matching;
        # the ADB input provider maps the declared canvas to current wm size.
        from io import BytesIO
        from PIL import Image
        from backend.core.fanxiu.remote.frame_geometry import FrameGeometry
        from backend.core.fanxiu.client.mumu_control import DEFAULT_FIXED_WIDTH, DEFAULT_FIXED_HEIGHT
        raw = bytes(response.body or b"")
        with Image.open(BytesIO(raw)) as image:
            geometry = FrameGeometry(*image.size, int(DEFAULT_FIXED_WIDTH), int(DEFAULT_FIXED_HEIGHT))
            normalized = geometry.normalize_image(image)
            ctx["frame_sampling_size"] = image.size
            if image.size == normalized.size:
                return self._data_url(raw)
            output = BytesIO()
            normalized.save(output, format="PNG")
            return self._data_url(output.getvalue())

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
            entry_id=str(getattr(ctx.get("entry"), "entry_id", "") or getattr(ctx.get("entry"), "id", "") or ""),
            scan=scan,
            match_strategy=match_strategy,
            ocr_enabled=ocr_enabled,
        )
        if ctx.get("external_frame_only"):
            payload.update(save_match_frame=False, require_supplied_frame=True)
            payload["sampling_size"] = ctx.get("external_sampling_size")
            payload["asset_revision"] = str(ctx.get("asset_tree_revision") or "")
        elif ctx.get("frame_sampling_size"):
            payload["sampling_size"] = ctx["frame_sampling_size"]
        entry: Any = ctx["entry"]
        return _match_game_window2_service(payload) if entry.mode == "local" else _match_remote_game_window2(entry, payload)

    def _build_shape_match_payload(
        self,
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
        *,
        entry_id: str = "",
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
        scan_box = shape.get("_match_scan_box")
        if not isinstance(scan_box, dict):
            scan_box = None
        scan_box_payload = self._box(scan_box, image) if scan_box is not None else None
        payload = {
            "entry_id": str(entry_id or ""),
            "filename": filename,
            "box": self._box(shape, image),
            "scan_box": scan_box_payload if scan else None,
            "scan": scan,
            "scan_scales": shape.get("scanScales") if scan else None,
            "pixel_tolerance": int(shape.get("pixelTolerance") if shape.get("pixelTolerance") is not None else 20),
            "alpha_mask_data_url": ((shape.get("alphaMask") or {}).get("dataUrl") if isinstance(shape.get("alphaMask"), dict) else None),
            "ocr_mask_mode": shape.get("ocrMaskMode") or "inherit-envelope",
            "ocr_mask_data_url": ((shape.get("ocrMask") or {}).get("dataUrl") if isinstance(shape.get("ocrMask"), dict) else None),
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
        return ShapeMatchPlanner().ocr_fallback_enabled(shape)

    def _shape_click_needs_frame(self, shape: dict[str, Any]) -> bool:
        flags = self._shape_match_payload_flags(shape)
        return str(flags.get("image_role") or "") != "off" or bool(flags.get("ocr_enabled"))

    def _match_source_filename(self, image: dict[str, Any]) -> str:
        return str(image.get("filename") or "").strip()

    def _match_source_missing_cached(self, image: dict[str, Any]) -> bool:
        filename = self._match_source_filename(image)
        return bool(filename and filename in self._missing_match_source_filenames)

    def _record_missing_match_source(self, image: dict[str, Any], exc: Exception) -> bool:
        message = str(exc)
        if "截图不存在" not in message:
            return False
        filename = self._match_source_filename(image)
        if not filename:
            return False
        self._missing_match_source_filenames.add(filename)
        return True

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
        if self._match_source_missing_cached(image):
            return 0
        try:
            condition = "image" if match_strategy == "anchor_pixel" else "auto"
            score = float(self._match_shape(ctx, image, shape, frame_data_url, condition=condition).get("similarity") or 0)
            if ocr_fallback and score < self.scene_threshold and self._shape_ocr_fallback_enabled(shape):
                ocr_score = float(self._match_shape(ctx, image, shape, frame_data_url, condition="ocr").get("similarity") or 0)
                score = max(score, ocr_score)
            return score
        except Exception as exc:
            if not self._record_missing_match_source(image, exc):
                self._log("detail", f"匹配失败：{image.get('title')} / {shape.get('title')}：{exc}")
            return 0

    def _shape_match_role(self, shape: dict[str, Any], key: str, default: str = "required") -> str:
        return ShapeMatchPlanner().match_role(shape, key, default)

    def _shape_ocr_role(self, shape: dict[str, Any]) -> str:
        return ShapeMatchPlanner().ocr_role(shape)

    def _shape_image_role(self, shape: dict[str, Any]) -> str:
        return ShapeMatchPlanner().match_role(shape, "imageMatchRole", "off")

    def _shape_match_payload_flags(self, shape: dict[str, Any], *, condition: str = "auto") -> dict[str, Any]:
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

    def _shape_match_conditions(self, shape: dict[str, Any]) -> list[str]:
        first = "ocr" if self._shape_prefers_ocr_first(shape) else "image"
        conditions: list[str] = []
        if self._shape_image_role(shape) != "off":
            conditions.append("image")
        if self._shape_ocr_role(shape) != "off" and str(shape.get("ocrText") or "").strip():
            conditions.append("ocr")
        if first == "ocr":
            conditions.sort(key=lambda item: 0 if item == "ocr" else 1)
        return conditions

    def _shape_prefers_ocr_first(self, shape: dict[str, Any]) -> bool:
        title = str(shape.get("title") or "")
        jump_target = str(shape.get("sceneJumpTarget") or "")
        return bool(title == "邮件" and jump_target.startswith("121") and str(shape.get("ocrText") or "").strip())

    def _match_shape(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
        *,
        condition: str = "auto",
        require_unique: bool = True,
    ) -> dict[str, Any]:
        flags = self._shape_match_payload_flags(shape, condition=condition)
        image_role = str(flags["image_role"])
        ocr_role = str(flags["ocr_role"])
        ocr_enabled = bool(flags["ocr_enabled"])
        if condition == "auto" and image_role != "off" and ocr_role != "required":
            flags = self._shape_match_payload_flags(shape, condition="image")
            ocr_enabled = False
        if image_role == "off" and not ocr_enabled:
            return {"ok": False, "matched": False, "similarity": 0, "matches": [], "box": self._box(shape, image), "reason": "match_disabled", "flags": flags}
        if ocr_enabled:
            self._shared_spatial_ocr_result(ctx, frame_data_url)
            result = self._shape_cached_frame_ocr_match(ctx, image, shape, frame_data_url)
            result["flags"] = flags
            if condition == "ocr" or result.get("matched") or ocr_role == "required" or image_role == "off":
                return result
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
            if bool(flags["scan"]) and image_role != "off" and require_unique:
                result = self._resolve_unique_floating_image_match(
                    ctx,
                    image,
                    shape,
                    frame_data_url,
                    result,
                    match_strategy=str(flags["match_strategy"]),
                )
        except Exception as exc:
            raise RuntimeError(f"浮动标注「{shape.get('title') or shape.get('id')}」匹配失败：{exc}") from exc
        similarity = float(result.get("similarity") or 0)
        if bool(flags["scan"]) and image_role != "off" and not require_unique:
            # Enumeration read: the caller asked for every matching object, so
            # the unique-selection pass above was skipped.  The raw full-frame
            # similarity can be 0 even when valid candidates exist, so judge
            # validity from each candidate's own crop_similarity.
            raw_matches = result.get("matches") if isinstance(result.get("matches"), list) else []
            candidate_similarity = max(
                (
                    float(item.get("crop_similarity") or 0)
                    for item in raw_matches
                    if isinstance(item, dict)
                ),
                default=0.0,
            )
            if candidate_similarity > similarity:
                similarity = candidate_similarity
        result_ocr_matched = bool(ocr_enabled and self._shape_match_result_ocr_matches(shape, result))
        if ocr_enabled and not result_ocr_matched and self._has_cached_ocr_tokens(ctx, frame_data_url):
            existing_fixed_box = result.get("fixed_box") if isinstance(result.get("fixed_box"), dict) else None
            existing_resolved_box = result.get("resolved_box") if isinstance(result.get("resolved_box"), dict) else None
            frame_ocr_result = self._shape_cached_frame_ocr_match(ctx, image, shape, frame_data_url)
            if bool(frame_ocr_result.get("matched")):
                if existing_fixed_box is not None:
                    if isinstance(frame_ocr_result.get("fixed_box"), dict):
                        frame_ocr_result["ocr_box"] = frame_ocr_result.get("fixed_box")
                    frame_ocr_result["fixed_box"] = existing_fixed_box
                    frame_ocr_result["resolved_box"] = existing_resolved_box or existing_fixed_box
                result = {**result, **frame_ocr_result}
                result_ocr_matched = True
                similarity = max(similarity, float(result.get("similarity") or 0))
        matched = result_ocr_matched or similarity >= float(self.scene_threshold)
        if ocr_enabled and not result_ocr_matched:
            matched = matched and bool(result.get("matches"))
        if matched:
            fixed_box = result.get("fixed_box")
            if isinstance(result.get("resolved_box"), dict):
                pass
            elif isinstance(fixed_box, dict):
                result["resolved_box"] = fixed_box
            else:
                result["resolved_box"] = result.get("box") if isinstance(result.get("box"), dict) else self._box(shape, image)
        result["matched"] = matched
        result["flags"] = flags
        return result

    def _resolve_unique_floating_image_match(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
        initial_result: dict[str, Any],
        *,
        match_strategy: str,
    ) -> dict[str, Any]:
        """Adapt pixel tolerance until a full-frame image hit is unique."""

        configured = max(0, min(255, int(shape.get("pixelTolerance") if shape.get("pixelTolerance") is not None else 20)))
        threshold = float(self.scene_threshold)
        reference_box = initial_result.get("box") if isinstance(initial_result.get("box"), dict) else None
        initial_matches = initial_result.get("matches") if isinstance(initial_result.get("matches"), list) else []
        initial_count = sum(
            1 for item in initial_matches
            if isinstance(item, dict) and float(item.get("crop_similarity") or 0) >= threshold
        )
        # A larger pixel tolerance is less strict; a smaller one is stricter.
        if initial_count == 0:
            tolerances = [configured, *(value for value in (configured + 5, configured + 10, configured + 20, configured + 35) if value <= 255)]
        elif initial_count > 1:
            tolerances = [configured, *(value for value in (configured - 5, configured - 10, configured - 15, 0) if 0 <= value < configured)]
        else:
            tolerances = [configured]
        tolerances = list(dict.fromkeys(tolerances))
        attempts: list[dict[str, Any]] = []
        result = initial_result

        for index, tolerance in enumerate(tolerances):
            if index:
                probe_shape = {**shape, "pixelTolerance": tolerance}
                result = self._run_match(
                    ctx,
                    image,
                    probe_shape,
                    frame_data_url,
                    scan=True,
                    match_strategy=match_strategy,
                    ocr_enabled=False,
                )
            raw_matches = result.get("matches") if isinstance(result.get("matches"), list) else []
            candidates = [item for item in raw_matches if isinstance(item, dict) and float(item.get("crop_similarity") or 0) >= threshold]
            selection_threshold = threshold
            if len(candidates) > 1:
                for stricter_threshold in range(int(threshold) + 1, 101):
                    stricter = [
                        item for item in raw_matches
                        if isinstance(item, dict) and float(item.get("crop_similarity") or 0) >= stricter_threshold
                    ]
                    if len(stricter) == 1:
                        candidates = stricter
                        selection_threshold = float(stricter_threshold)
                        break
            attempts.append({
                "pixel_tolerance": tolerance,
                "selection_threshold": selection_threshold,
                "candidate_count": len(candidates),
            })
            if len(candidates) != 1:
                continue
            selected = candidates[0]
            resolved = dict(result)
            if reference_box is not None:
                resolved["box"] = reference_box
            resolved.update({
                "fixed_box": selected.get("box") or result.get("fixed_box"),
                "resolved_box": selected.get("box") or result.get("resolved_box"),
                "similarity": selected.get("crop_similarity") or selected.get("similarity") or 0,
                "score": selected.get("crop_score") or selected.get("score") or 0,
                "pixel_tolerance": tolerance,
                "selection_threshold": selection_threshold,
                "candidate_count": 1,
                "adaptive_match_attempts": attempts,
                "unique_match": True,
            })
            return resolved

        unresolved = dict(result)
        unresolved.update({
            "similarity": 0,
            "score": 0.0,
            "candidate_count": attempts[-1]["candidate_count"] if attempts else 0,
            "adaptive_match_attempts": attempts,
            "unique_match": False,
            "reason": "floating_image_not_unique",
        })
        return unresolved

    def _shape_match_result_ocr_matches(self, shape: dict[str, Any], result: dict[str, Any]) -> bool:
        target = _sanitize_ocr_text(shape.get("ocrText"))
        if not target:
            return False
        mode = str(shape.get("ocrMatchMode") or "contains")
        raw_matches = result.get("matches")
        if not isinstance(raw_matches, list):
            return False
        existing_fixed_box = result.get("fixed_box") if isinstance(result.get("fixed_box"), dict) else None
        for item in raw_matches:
            if not isinstance(item, dict):
                continue
            text = _sanitize_ocr_text(item.get("text") or item.get("ocr_text"))
            if text and self._ocr_text_matches(text, target, mode):
                result["ocr_text"] = text
                fixed_box = self._ocr_fragment_box(item)
                if fixed_box is not None:
                    if existing_fixed_box is not None:
                        result["ocr_box"] = fixed_box
                        result.setdefault("resolved_box", existing_fixed_box)
                    else:
                        result["fixed_box"] = fixed_box
                        result["resolved_box"] = fixed_box
                return True
        return False

    def _scene_identity_ocr_line(
        self,
        lines: list[dict[str, Any]],
        search_box: dict[str, Any],
        target: str,
        mode: str,
    ) -> dict[str, Any] | None:
        """Pick the authoritative OCR line for one scene-identity shape.

        Exact box overlaps win; when annotation drift leaves the ROI just off
        the rendered line (a real #567 frame had “效果说明” overlap at 0.22,
        below the 0.30 token threshold), fall back to the nearest line whose
        text matches within one line-height / box-width of the box centre.
        """

        exact = [
            line
            for line in query_ocr_lines(lines, search_box)
            if self._ocr_text_matches(_sanitize_ocr_text(line.get("text")), target, mode)
        ]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            return None

        left = float(search_box.get("x") or 0.0)
        top = float(search_box.get("y") or 0.0)
        width = float(search_box.get("w") or 0.0)
        height = float(search_box.get("h") or 0.0)
        center_x = left + width / 2.0
        center_y = top + height / 2.0
        best: tuple[float, dict[str, Any]] | None = None
        for line in lines:
            if not isinstance(line, dict):
                continue
            line_text = _sanitize_ocr_text(line.get("text"))
            if not line_text or not self._ocr_text_matches(line_text, target, mode):
                continue
            line_x = float(line.get("x") or 0.0)
            line_y = float(line.get("y") or 0.0)
            line_width = float(line.get("w") or 0.0)
            line_height = float(line.get("h") or 0.0)
            dx = abs((line_x + line_width / 2.0) - center_x)
            dy = abs((line_y + line_height / 2.0) - center_y)
            if dx > width or dy > max(height, line_height):
                continue
            distance = dx + dy
            if best is None or distance < best[0]:
                best = (distance, line)
        return best[1] if best is not None else None

    def _shape_cached_frame_ocr_match(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
    ) -> dict[str, Any]:
        result = {
            "ok": True,
            "matched": False,
            "similarity": 0,
            "matches": [],
            "box": self._box(shape, image),
            "reason": "cached_frame_ocr",
        }
        target = _sanitize_ocr_text(shape.get("ocrText"))
        if not target:
            return result
        mode = str(shape.get("ocrMatchMode") or "contains")
        cache = ctx.get("_ocr_tokens_cache")
        if not isinstance(cache, dict) or cache.get("frame") != frame_data_url:
            return result
        tokens = cache.get("tokens") if isinstance(cache.get("tokens"), list) else []
        box = self._box(shape, image)
        floating_ocr = bool(shape.get("floating"))
        search_box = box
        if floating_ocr:
            explicit_scan_box = shape.get("_match_scan_box")
            if isinstance(explicit_scan_box, dict):
                search_box = self._box(explicit_scan_box, image)
            else:
                width, height = self._frame_size(image)
                search_box = {"x": 0.0, "y": 0.0, "w": float(width), "h": float(height)}
        spatial = query_spatial_ocr(tokens, search_box)
        text = _sanitize_ocr_text(spatial.get("text"))
        if (
            not self._ocr_text_matches(text, target, mode)
            and shape.get("_wait_click_action_title")
            and isinstance(shape.get("_match_scan_box"), dict)
        ):
            # Full-frame detection can omit a clearly visible small menu word.
            # Recheck only the declared action envelope, using the same OCR
            # model and frame. Scene identity scans do not incur this fallback.
            crop = self._crop_frame_data_url_for_shapes(
                frame_data_url,
                {**image, "shapes": [{**shape, **shape["_match_scan_box"], "title": "action-envelope"}], "children": []},
                ["action-envelope"], padding=0,
            )
            if crop is not None:
                crop_url, offset_x, offset_y = crop
                local = self._ocr_frame(crop_url, options={"return_word_box": True})
                local_tokens = [
                    {**item, "x": float(item.get("x") or 0) + offset_x,
                     "y": float(item.get("y") or 0) + offset_y}
                    for item in local.get("tokens") or []
                ]
                spatial = query_spatial_ocr(local_tokens, search_box)
                text = _sanitize_ocr_text(spatial.get("text"))
                result["reason"] = "action_envelope_ocr"
                self._log("detail", f"局部 OCR 复核 {shape.get('_wait_click_action_title')}：{text[:120]}")
        if (
            not floating_ocr
            and bool(shape.get("isSceneIdentity"))
            and not (text and self._ocr_text_matches(text, target, mode))
        ):
            # A tight identity ROI can clip its leading/trailing character, or
            # sit a few pixels off the rendered line, so the token-ROI text may
            # be a fragment such as “前拥有论剑玉：” or empty.  Paddle's own
            # line grouping already owns the complete text; accept the
            # authoritative line instead of trusting the clipped ROI boundary,
            # preferring an exact overlap and otherwise the nearest matching
            # line within one line-height of the box.  Only scene identity
            # incurs this fallback.
            cached_lines = cache.get("lines") if isinstance(cache.get("lines"), list) else []
            matched_line = self._scene_identity_ocr_line(
                cached_lines, search_box, target, mode
            )
            if matched_line is not None:
                line_text = _sanitize_ocr_text(matched_line.get("text"))
                text = line_text
                spatial = {
                    "text": line_text,
                    "fragments": [matched_line],
                    "tokens": spatial.get("tokens") or [],
                }
                result["reason"] = "cached_frame_ocr_line"
        fragments = spatial.get("fragments") if isinstance(spatial.get("fragments"), list) else []
        result["ocr_text"] = text
        result["matches"] = fragments
        token_box = None
        literal_target = target
        if floating_ocr and mode == "regex":
            try:
                regex_match = re.search(target, text)
            except re.error:
                regex_match = None
            literal_target = regex_match.group(0) if regex_match else ""
        if floating_ocr and mode in {"contains", "exact", "regex"}:
            exact_matches = find_text_matches(spatial.get("tokens") or [], literal_target) if literal_target else []
            if len(exact_matches) > 1:
                result["reason"] = "floating_ocr_ambiguous"
                result["candidate_boxes"] = [match.box for match in exact_matches]
                return result
            if exact_matches:
                token_box = exact_matches[0].box
                result["ocr_text"] = exact_matches[0].text
        text_matched = (
            bool(token_box)
            if floating_ocr and mode == "exact"
            else bool(text and self._ocr_text_matches(text, target, mode))
        )
        if text_matched:
            if floating_ocr and mode in {"contains", "exact", "regex"} and token_box is None:
                return result
            result["matched"] = True
            result["similarity"] = 100
            token_box = token_box or locate_text_box(spatial.get("tokens") or [], target)
            ocr_box = token_box or union_fragment_box(fragments)
            result["fixed_box"] = box
            result["resolved_box"] = ocr_box if floating_ocr and ocr_box is not None else box
            result["floating_ocr"] = floating_ocr
            if ocr_box is not None:
                # ``token_box`` already is the exact union of the matched
                # character boxes.  Re-slicing it by the full aggregated text
                # would incorrectly apply the old uniform-line estimate twice.
                result["ocr_box"] = token_box or ocr_box
        return result

    def _ocr_options_cache_key(self, options: dict[str, Any] | None = None) -> str:
        if not options:
            return "{}"
        try:
            return json.dumps(options, ensure_ascii=False, sort_keys=True, default=str)
        except TypeError:
            return str(sorted((str(key), str(value)) for key, value in options.items()))

    def _cached_ocr_result(self, ctx: dict[str, Any], frame_data_url: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        canonical_options = {**dict(options or {}), "return_word_box": True}
        cache = ctx.setdefault("_ocr_tokens_cache", {})
        options_key = self._ocr_options_cache_key(canonical_options)
        if (
            isinstance(cache, dict)
            and cache.get("version") == 4
            and cache.get("frame") == frame_data_url
            and cache.get("options_key") == options_key
            and isinstance(cache.get("tokens"), list)
            and isinstance(cache.get("lines"), list)
        ):
            return cache
        response = self._ocr_frame(frame_data_url, options=canonical_options)
        lines = response.get("lines") if isinstance(response.get("lines"), list) else []
        tokens = response.get("tokens") if isinstance(response.get("tokens"), list) else []
        cache = {
            "version": 4,
            "frame": frame_data_url,
            "options_key": options_key,
            "lines": lines,
            "tokens": tokens,
        }
        ctx["_ocr_tokens_cache"] = cache
        return cache

    def _shared_spatial_ocr_result(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        canonical_options = {**dict(options or {}), "return_word_box": True}
        options_key = self._ocr_options_cache_key(canonical_options)
        with self._shared_ocr_lock:
            cache = ctx.get("_ocr_tokens_cache")
            if (
                isinstance(cache, dict)
                and cache.get("version") == 4
                and cache.get("frame") == frame_data_url
                and cache.get("options_key") == options_key
                and isinstance(cache.get("tokens"), list)
                and isinstance(cache.get("lines"), list)
            ):
                return cache
            return self._cached_ocr_result(ctx, frame_data_url, options=canonical_options)

    def _cached_ocr_tokens(self, ctx: dict[str, Any], frame_data_url: str) -> list[dict[str, Any]]:
        result = self._cached_ocr_result(ctx, frame_data_url)
        tokens = result.get("tokens")
        return tokens if isinstance(tokens, list) else []

    def _cached_ocr_fragments(self, ctx: dict[str, Any], frame_data_url: str) -> list[dict[str, Any]]:
        result = self._cached_ocr_result(ctx, frame_data_url)
        lines = result.get("lines")
        return lines if isinstance(lines, list) else []

    def _has_cached_ocr_tokens(self, ctx: dict[str, Any], frame_data_url: str) -> bool:
        cache = ctx.get("_ocr_tokens_cache")
        tokens = cache.get("tokens") if isinstance(cache, dict) and cache.get("frame") == frame_data_url else None
        return isinstance(tokens, list) and any(isinstance(token, dict) for token in tokens)

    def _ocr_text_matches(self, text: str, target: str, mode: str) -> bool:
        mode = str(mode or "contains").strip().lower()
        if mode == "exact":
            return text == target
        if mode == "regex":
            try:
                return re.search(target, text) is not None
            except re.error:
                return target in text
        if mode == "wildcard":
            pattern = "^" + re.escape(target).replace("\\*", ".*").replace("\\?", ".") + "$"
            return re.search(pattern, text) is not None
        return target in text

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
        return SceneScorer(
            shape_score=lambda score_ctx, score_image, score_shape, score_frame: self._scene_identity_image_shape_score(
                score_ctx,
                score_image,
                score_shape,
                score_frame,
            ),
            shape_ocr_score=lambda score_ctx, score_image, score_shape, score_frame: float(
                self._match_shape(
                    score_ctx,
                    score_image,
                    score_shape,
                    score_frame,
                    condition="ocr",
                ).get("similarity") or 0
            ),
            threshold=float(self.scene_threshold),
            log_detail=lambda message: self._log("detail", message),
        ).scene_identity_shape_score(ctx, image, shape, frame_data_url)

    def _scene_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str,
    ) -> float:
        scene_identity_shapes = self._scene_identity_shapes(image)
        if not scene_identity_shapes and self._image_layer(image) == 3:
            return 0.0
        scorer = SceneScorer(
            shape_score=lambda score_ctx, score_image, score_shape, score_frame: self._scene_identity_image_shape_score(
                score_ctx,
                score_image,
                score_shape,
                score_frame,
            ),
            shape_ocr_score=lambda score_ctx, score_image, score_shape, score_frame: float(
                self._match_shape(
                    score_ctx,
                    score_image,
                    score_shape,
                    score_frame,
                    condition="ocr",
                ).get("similarity") or 0
            ),
            threshold=float(self.scene_threshold),
            log_detail=lambda message: self._log("detail", message),
        )
        scores = [
            scorer.scene_identity_shape_score(ctx, image, shape, frame_data_url)
            for shape in scene_identity_shapes
        ]
        score = min(scores) if scores else 0.0
        if score >= float(self.scene_threshold) and bool(image.get("floatingAlignmentRequired")):
            score = min(score, self._floating_scene_alignment_score(ctx, image, frame_data_url))
        return self._scene_discriminator_adjusted_score(ctx, image, frame_data_url, score)

    def _floating_scene_alignment_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str,
    ) -> float:
        """Require floating OCR identities to describe one translated panel.

        Independent full-frame OCR hits prove that labels exist; they do not
        prove that the labels belong to the same popup.  This alignment gate
        compares every live OCR box with its annotated box and accepts only a
        common translation, mirroring shared GUI-alignment semantics.
        """

        anchors = [
            shape
            for shape in self._scene_identity_shapes(image)
            if bool(shape.get("floating"))
            and self._shape_ocr_role(shape) == "required"
            and str(shape.get("ocrText") or "").strip()
        ]
        if len(anchors) < 2:
            self._log("detail", f"#{self._image_number(image) or '?'} 浮动弹窗缺少至少两个 OCR 身份锚点")
            return 0.0
        offsets: list[tuple[float, float, str]] = []
        for shape in anchors:
            result = self._match_shape(ctx, image, shape, frame_data_url, condition="ocr")
            resolved = result.get("resolved_box") if isinstance(result.get("resolved_box"), dict) else None
            if not bool(result.get("matched")) or resolved is None:
                return 0.0
            expected = self._box(shape, image)
            offsets.append((
                float(resolved.get("x") or 0) + float(resolved.get("w") or 0) / 2
                - (float(expected.get("x") or 0) + float(expected.get("w") or 0) / 2),
                float(resolved.get("y") or 0) + float(resolved.get("h") or 0) / 2
                - (float(expected.get("y") or 0) + float(expected.get("h") or 0) / 2),
                str(shape.get("title") or shape.get("id") or "anchor"),
            ))
        dx_values = sorted(item[0] for item in offsets)
        dy_values = sorted(item[1] for item in offsets)
        middle = len(offsets) // 2
        dx = dx_values[middle] if len(offsets) % 2 else (dx_values[middle - 1] + dx_values[middle]) / 2
        dy = dy_values[middle] if len(offsets) % 2 else (dy_values[middle - 1] + dy_values[middle]) / 2
        tolerance = max(1.0, float(image.get("floatingAlignmentTolerance") or 36.0))
        maximum_error = max(max(abs(item_dx - dx), abs(item_dy - dy)) for item_dx, item_dy, _title in offsets)
        if maximum_error > tolerance:
            self._log(
                "detail",
                f"#{self._image_number(image) or '?'} 浮动弹窗 OCR 锚点不共线：最大偏差 {maximum_error:.1f}px > {tolerance:.1f}px",
            )
            return 0.0
        ctx["_floating_scene_alignment"] = {
            "frame": frame_data_url,
            "image_id": self._image_number(image),
            "dx": dx,
            "dy": dy,
            "maximum_error": maximum_error,
            "anchors": [title for _item_dx, _item_dy, title in offsets],
        }
        return 100.0

    def _scene_identity_image_shape_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
    ) -> float:
        if self._shape_ocr_role(shape) == "required" and str(shape.get("ocrText") or "").strip():
            return 0.0
        score = self._shape_score(ctx, image, shape, frame_data_url, ocr_fallback=False)
        return score

    def _scene_discriminator_groups(self, ctx: dict[str, Any]) -> list[list[dict[str, Any]]]:
        images = ctx.get("images") or {}
        if not isinstance(images, dict):
            return []
        cache = ctx.get("_scene_discriminator_groups")
        if isinstance(cache, list):
            return cache
        records: list[dict[str, Any]] = []
        for raw_image_id, item in images.items():
            if not isinstance(item, dict):
                continue
            image_id = self._image_number(item)
            if image_id is None:
                try:
                    image_id = int(raw_image_id)
                except Exception:
                    continue
            for shape in View(item).get_shapes(include_groups=False):
                if not bool(shape.raw.get("discriminatorEnabled")):
                    continue
                records.append({"image_id": int(image_id), "image": item, "shape": shape.raw})

        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            shape = record["shape"]
            group_id = str(shape.get("discriminatorGroupId") or "").strip()
            if group_id:
                grouped.setdefault(f"group:{group_id}", []).append(record)
            grouped.setdefault(f"box:{self._shape_box_signature(shape)}", []).append(record)

        groups: list[list[dict[str, Any]]] = []
        seen: set[tuple[tuple[int, str], ...]] = set()
        for members in grouped.values():
            if len({int(member["image_id"]) for member in members}) < 2:
                continue
            signature = tuple(sorted((int(member["image_id"]), str(member["shape"].get("id") or "")) for member in members))
            if signature in seen:
                continue
            seen.add(signature)
            groups.append(members)
        ctx["_scene_discriminator_groups"] = groups
        return groups

    def _shape_box_signature(self, shape: dict[str, Any]) -> tuple[float, float, float, float]:
        return (
            round(float(shape.get("x") or 0), 4),
            round(float(shape.get("y") or 0), 4),
            round(float(shape.get("w") or 0), 4),
            round(float(shape.get("h") or 0), 4),
        )

    def _scene_discriminator_adjusted_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str,
        base_score: float,
    ) -> float:
        image_id = self._image_number(image)
        if image_id is None:
            return base_score
        score = float(base_score or 0)
        for group in self._scene_discriminator_groups(ctx):
            if int(image_id) not in {int(member["image_id"]) for member in group}:
                continue
            scores = [
                (
                    self._scene_discriminator_member_score(ctx, member, frame_data_url),
                    int(member["image_id"]),
                    member["shape"],
                )
                for member in group
            ]
            scores = [item for item in scores if item[0] > 0]
            if not scores:
                continue
            scores.sort(key=lambda item: item[0], reverse=True)
            best_score, best_image_id, best_shape = scores[0]
            current_score = max((item[0] for item in scores if item[1] == int(image_id)), default=0.0)
            gap = best_score - current_score
            second_score = scores[1][0] if len(scores) > 1 else 0.0
            if best_image_id != int(image_id) and best_score >= 50 and gap >= 4:
                self._log(
                    "detail",
                    (
                        f"场景区分：#{image_id} 被 #{best_image_id}「{best_shape.get('title') or best_shape.get('id')}」"
                        f"压制，{current_score:.0f}% < {best_score:.0f}%"
                    ),
                )
                return 0.0
            if best_image_id == int(image_id) and best_score - second_score >= 4:
                score = max(score, best_score)
        return score

    def _scene_discriminator_member_score(self, ctx: dict[str, Any], member: dict[str, Any], frame_data_url: str) -> float:
        cache = ctx.setdefault("_scene_discriminator_score_cache", {})
        if not isinstance(cache, dict) or cache.get("frame") != frame_data_url:
            cache = {"frame": frame_data_url, "scores": {}}
            ctx["_scene_discriminator_score_cache"] = cache
        scores = cache.setdefault("scores", {})
        shape = member["shape"]
        cache_key = f"{member['image_id']}:{shape.get('id') or shape.get('title') or self._shape_box_signature(shape)}"
        if cache_key not in scores:
            scores[cache_key] = float(self._shape_score(ctx, member["image"], shape, frame_data_url, ocr_fallback=False) or 0)
        return float(scores.get(cache_key) or 0)

    def _popup_score(self, ctx: dict[str, Any], image: dict[str, Any], frame_data_url: str) -> float:
        return self._match_image_score(
            ctx,
            image,
            frame_data_url,
            self._popup_match_shapes(image),
            log_label="弹窗标识",
            scan_fallback=False,
            ocr_fallback=True,
        )

    def _match_image_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str,
        shapes: list[dict[str, Any]],
        *,
        log_label: str,
        scan_fallback: bool = True,
        ocr_fallback: bool = True,
    ) -> float:
        scores: list[float] = []
        for shape in shapes:
            score = self._shape_score(ctx, image, shape, frame_data_url, ocr_fallback=ocr_fallback)
            if scan_fallback and score < 50 and self._shape_image_role(shape) != "off" and not self._match_source_missing_cached(image):
                try:
                    scan_score = float(self._run_match(ctx, image, shape, frame_data_url, scan=True, match_strategy="auto").get("similarity") or 0)
                    score = max(score, scan_score)
                except Exception as exc:
                    if not self._record_missing_match_source(image, exc):
                        self._log("detail", f"{log_label}扫描失败：{image.get('title')} / {shape.get('title')}：{exc}")
            scores.append(score)
        scores = [score for score in scores if score > 0]
        if not scores:
            return 0
        scores.sort(reverse=True)
        return sum(scores[: min(3, len(scores))]) / min(3, len(scores))

    def _identify_scene(self, ctx: dict[str, Any], frame_data_url: str, keys: list[str] | None = None) -> tuple[str, float]:
        candidate_ids = [
            int(self.scene_ids[key])
            for key in (keys or self._scene_key_order())
            if key in self.scene_ids and self._image(ctx, key) is not None
        ]
        scene_id, score = self._identify_scene_number(
            ctx,
            frame_data_url,
            preferred_scene_ids=candidate_ids,
        )
        return (self._scene_id_key(scene_id), score) if scene_id is not None else ("", score)

    def _scene_matches(self, key: str, score: float) -> bool:
        scene_id = self.scene_ids.get(key)
        return scene_id is not None and self._scene_matches_id(int(scene_id), score)

    def _click_shape(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str | None = None,
        *,
        match_result: dict[str, Any] | None = None,
        jitter_radius: int = 0,
        x_ratio: float = 0.5,
        y_ratio: float = 0.5,
    ) -> None:
        self._raise_if_stopped(getattr(self, "_stop_event", None))
        xuanhuang_forward = (
            self._image_number(image) == 418
            and str(shape.get("title") or "").strip() == "前往"
        )
        frame_data_url = self._guard_xuanhuang_forward_click(ctx, image, shape, frame_data_url)
        if xuanhuang_forward:
            match_result = None
        action_match_result: dict[str, Any] | None = None
        if frame_data_url:
            action_match_result = match_result if isinstance(match_result, dict) else self._match_shape(ctx, image, shape, frame_data_url)
            if not bool(action_match_result.get("matched")) and self._shape_ocr_fallback_enabled(shape):
                ocr_match_result = self._match_shape(ctx, image, shape, frame_data_url, condition="ocr")
                if bool(ocr_match_result.get("matched")):
                    action_match_result = ocr_match_result
            self._require_shape_match(action_match_result, shape)
        if x_ratio == 0.5 and y_ratio == 0.5:
            raw_click_x, raw_click_y = ActionPlanner().shape_center(image, shape)
        else:
            width, height = self._frame_size(image)
            raw_click_x = (float(shape.get("x") or 0) + float(shape.get("w") or 0) * x_ratio) * width
            raw_click_y = (float(shape.get("y") or 0) + float(shape.get("h") or 0) * y_ratio) * height
        click_x, click_y = raw_click_x, raw_click_y
        resolved_click = None
        click_bounds = self._box(shape, image)
        if shape.get("clickResolvedBox") is not False:
            if x_ratio == 0.5 and y_ratio == 0.5:
                resolved_click = self._shape_match_resolved_click_point(image, shape, action_match_result)
            else:
                resolved_click = self._shape_match_resolved_click_point(
                    image,
                    shape,
                    action_match_result,
                    x_ratio=x_ratio,
                    y_ratio=y_ratio,
                )
        if resolved_click is not None and not self._shape_should_keep_raw_click_for_ocr_navigation(shape, action_match_result):
            click_x, click_y = resolved_click
            if isinstance(action_match_result, dict):
                resolved_box = action_match_result.get("resolved_box") or action_match_result.get("fixed_box")
                if isinstance(resolved_box, dict):
                    click_bounds = resolved_box
        if action_match_result is not None:
            self._log(
                "detail",
                (
                    f"点击标注「{shape.get('title') or shape.get('id')}」："
                    f"similarity={float(action_match_result.get('similarity') or 0):.0f}，"
                    f"ocr={str(action_match_result.get('ocr_text') or '')[:40]}，"
                    f"fixed_box={action_match_result.get('fixed_box')}，"
                    f"click=({click_x:.1f},{click_y:.1f})，"
                    f"raw=({raw_click_x:.1f},{raw_click_y:.1f})"
                ),
            )
        payload = ActionPlanner().click_shape_payload(image, shape)
        entry: Any = ctx["entry"]
        payload["x"] = float(click_x)
        payload["y"] = float(click_y)
        click_x = float(payload.get("x") or 0)
        click_y = float(payload.get("y") or 0)
        if jitter_radius > 0:
            click_x, click_y = self._randomly_perturb_click_point(
                image,
                click_x,
                click_y,
                radius=jitter_radius,
                bounds=click_bounds,
            )
            payload["x"] = click_x
            payload["y"] = click_y
            self._log(
                "detail",
                f"点击无响应重试：随机扰动半径 r={jitter_radius}px，实际落点=({click_x:.1f},{click_y:.1f})",
            )
        self._save_action_trace(
            ctx,
            image,
            {
                "kind": "click",
                "point": [click_x, click_y],
                "label": f"click #{self._image_number(image) or '?'} {shape.get('title') or shape.get('id') or ''}".strip(),
                "shape_title": shape.get("title"),
                "shape_id": shape.get("id"),
            },
            frame_data_url=frame_data_url,
        )
        if entry.mode == "local":
            payload["input_backend"] = "adb"
            _click_game_window2_service(payload)
        else:
            _click_remote_game_window2(entry, payload)
        self._clear_tick_frame(ctx)

    @staticmethod
    def _navigation_retry_jitter_radius(no_response_count: int) -> int:
        """Grow retry jitter exponentially, with at most 50px growth per retry."""

        count = max(0, int(no_response_count or 0))
        if count <= 0:
            return 0
        radius = 1
        for _ in range(1, count):
            radius = min(radius * 2, radius + 50)
        return radius

    def _randomly_perturb_click_point(
        self,
        image: dict[str, Any],
        x: float,
        y: float,
        *,
        radius: int,
        bounds: Mapping[str, Any] | None = None,
    ) -> tuple[float, float]:
        """Apply random jitter inside both the game frame and target bounds."""

        width, height = self._frame_size(image)
        radius = max(0, int(radius or 0))
        if radius <= 0:
            return float(x), float(y)
        jittered_x = float(x) + random.randint(-radius, radius)
        jittered_y = float(y) + random.randint(-radius, radius)
        min_x, min_y = 0.0, 0.0
        max_x, max_y = max(0.0, float(width) - 1.0), max(0.0, float(height) - 1.0)
        if isinstance(bounds, Mapping):
            bound_x = float(bounds.get("x") or 0)
            bound_y = float(bounds.get("y") or 0)
            bound_w = float(bounds.get("w") or 0)
            bound_h = float(bounds.get("h") or 0)
            if bound_w > 0 and bound_h > 0:
                min_x = max(min_x, bound_x)
                min_y = max(min_y, bound_y)
                # Match the frame's ``width - 1`` pixel convention so a
                # rounded ADB coordinate cannot land just beyond the Shape's
                # right or bottom edge.
                max_x = min(max_x, bound_x + max(0.0, bound_w - 1.0))
                max_y = min(max_y, bound_y + max(0.0, bound_h - 1.0))
        return (
            min(max(jittered_x, min_x), max_x),
            min(max(jittered_y, min_y), max_y),
        )

    def _guard_xuanhuang_forward_click(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str | None,
    ) -> str | None:
        """Require a fresh, positive #418 attempt fraction before clicking 前往."""

        if self._image_number(image) != 418 or str(shape.get("title") or "").strip() != "前往":
            return frame_data_url

        # The frame must come from ``wait_click`` after its mandatory Layer 0
        # arbitration. Capturing here would create an unguarded observation gap
        # in which a popup could replace #418 immediately before the click.
        fresh_frame = str(frame_data_url or "")
        if not fresh_frame:
            raise RuntimeError(
                "安全拦截：#418[前往] 必须由 wait_click 提供 Layer 0 守护帧，禁止点击"
            )
        scene_id, scene_score = self._identify_scene_number(ctx, fresh_frame, preferred_scene_ids=[418])
        if scene_id != 418 or not self._scene_matches_id(418, float(scene_score or 0.0)):
            raise RuntimeError(
                "安全拦截：点击 #418[前往] 前无法在新帧确认 #418，禁止点击"
            )

        lines = self._ocr_fragments_in_shapes(
            fresh_frame,
            image,
            ("次数",),
            padding=12,
            ctx=ctx,
        )
        counter_text = self._ocr_text(lines)
        fraction = parse_ocr_values(counter_text, expected_count=2)
        if fraction is None:
            raise RuntimeError(
                f"安全拦截：无法从 #418[次数] 识别完整分子/分母，禁止点击[前往]，OCR={counter_text!r}"
            )
        numerator, denominator = fraction
        if not 0 <= numerator <= denominator:
            raise RuntimeError(
                f"安全拦截：#418[次数] 数值异常 {numerator}/{denominator}，禁止点击[前往]"
            )
        if numerator == 0:
            raise RuntimeError("安全拦截：#418[次数] 分子为0，禁止点击[前往]")
        self._log("detail", f"安全确认：#418[次数]={numerator}/{denominator}，允许点击[前往]")
        return fresh_frame

    def _shape_match_resolved_click_point(
        self,
        image: dict[str, Any],
        shape: dict[str, Any],
        match_result: dict[str, Any] | None,
        *,
        x_ratio: float = 0.5,
        y_ratio: float = 0.5,
    ) -> tuple[float, float] | None:
        if not isinstance(match_result, dict):
            return None
        reference_box = match_result.get("box")
        resolved_box = match_result.get("resolved_box") or match_result.get("fixed_box")
        if not isinstance(reference_box, dict) or not isinstance(resolved_box, dict):
            return None
        ref_w = float(reference_box.get("w") or 0)
        ref_h = float(reference_box.get("h") or 0)
        dst_w = float(resolved_box.get("w") or 0)
        dst_h = float(resolved_box.get("h") or 0)
        if ref_w <= 0 or ref_h <= 0 or dst_w <= 0 or dst_h <= 0:
            return None
        if bool(match_result.get("floating_ocr")):
            return (
                float(resolved_box.get("x") or 0) + dst_w * float(x_ratio),
                float(resolved_box.get("y") or 0) + dst_h * float(y_ratio),
            )
        if x_ratio == 0.5 and y_ratio == 0.5:
            raw_x, raw_y = ActionPlanner().shape_center(image, shape)
        else:
            width, height = self._frame_size(image)
            raw_x = (float(shape.get("x") or 0) + float(shape.get("w") or 0) * x_ratio) * width
            raw_y = (float(shape.get("y") or 0) + float(shape.get("h") or 0) * y_ratio) * height
        ref_x = float(reference_box.get("x") or 0)
        ref_y = float(reference_box.get("y") or 0)
        dst_x = float(resolved_box.get("x") or 0)
        dst_y = float(resolved_box.get("y") or 0)
        return (
            dst_x + (raw_x - ref_x) * dst_w / ref_w,
            dst_y + (raw_y - ref_y) * dst_h / ref_h,
        )

    def _shape_should_keep_raw_click_for_ocr_navigation(
        self,
        shape: dict[str, Any],
        match_result: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(match_result, dict):
            return False
        if not str(shape.get("sceneJumpTarget") or "").strip():
            return False
        title = str(shape.get("title") or "").strip()
        if title not in {"返回", "关闭", "离开", "退出", "回到世界"}:
            return False
        if self._shape_image_role(shape) != "off" or self._shape_ocr_role(shape) != "required":
            return False
        return isinstance(match_result.get("fixed_box"), dict) or isinstance(match_result.get("resolved_box"), dict)

    def _click_scene_route_shape(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str | None = None,
        *,
        jitter_radius: int = 0,
    ) -> None:
        # A route edge describes a possible destination, not evidence that its
        # menu entry is still at the stored coordinates. Required OCR/image
        # localization failures must propagate before input; dynamic menus can
        # replace an entry with another activity at exactly the same position.
        self._click_shape(
            ctx, image, shape, frame_data_url, jitter_radius=jitter_radius,
        )

    def _wait_shape_match(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image: dict[str, Any],
        shape: dict[str, Any],
        *,
        timeout: float,
        label: str,
        min_similarity: float | None = None,
        require_resolved_box: bool = False,
    ):
        deadline = time.monotonic() + max(0.1, float(timeout or 0.1))
        last_similarity = 0.0
        last_ocr_text = ""
        source_scene_id = self._image_number(image)
        context = self._behavior_tree_context(ctx, stop_event=stop_event)

        def accept_result(result: dict[str, Any]) -> bool:
            similarity = float(result.get("similarity") or 0)
            if (
                not bool(result.get("matched"))
                and min_similarity is not None
                and similarity >= float(min_similarity)
                and (not require_resolved_box or isinstance(result.get("resolved_box") or result.get("fixed_box"), dict))
            ):
                result["matched"] = True
                if not isinstance(result.get("resolved_box"), dict) and isinstance(result.get("fixed_box"), dict):
                    result["resolved_box"] = result.get("fixed_box")
            return bool(result.get("matched"))

        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            _wait_scene_match = yield from context.wait_scene([source_scene_id] if source_scene_id is not None else None, label=f'{label}：Shape 等待前守护', wait=5.0, required=False)
            (scene_id, _scene_score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if source_scene_id is not None and scene_id != source_scene_id:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{label}：当前 #{scene_id or 'unknown'}，等待 #{source_scene_id}",
                        phase="wait_shape_scene",
                    )
                yield BehaviorTreeStatus.RUNNING
                continue
            for condition in self._shape_match_conditions(shape):
                result = self._match_shape(ctx, image, shape, frame, condition=condition)
                last_similarity = max(last_similarity, float(result.get("similarity") or 0))
                last_ocr_text = str(result.get("ocr_text") or last_ocr_text)[:40]
                if accept_result(result):
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

    def _shape_has_click_condition(self, shape: dict[str, Any]) -> bool:
        return bool(self._shape_match_conditions(shape))

    def _click_shape_respecting_conditions(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image: dict[str, Any],
        shape: dict[str, Any],
        payload: dict[str, Any],
        *,
        label: str,
        x_ratio: float = 0.5,
        y_ratio: float = 0.5,
        timeout_key: str = "shape_click_timeout",
    ):
        context = self._behavior_tree_context(ctx, stop_event=stop_event)
        view = View(image)
        target = Shape(shape, parent_view=view)
        yield from context.wait_click(
            view,
            target,
            timeout=float(
                payload.get(timeout_key)
                or payload.get("shape_click_timeout")
                or 8.0
            ),
            x_ratio=float(x_ratio),
            y_ratio=float(y_ratio),
            _source_info={"label": label},
        )

    def _click_frame_point(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        x: float,
        y: float,
        *,
        save_action_trace: bool = True,
    ) -> None:
        self._raise_if_stopped(getattr(self, "_stop_event", None))
        payload = ActionPlanner().click_point_payload(image, x, y)
        entry: Any = ctx["entry"]
        if save_action_trace:
            self._save_action_trace(
                ctx,
                image,
                {
                    "kind": "click",
                    "point": [float(x), float(y)],
                    "label": f"click #{self._image_number(image) or '?'} ({float(x):.0f},{float(y):.0f})",
                },
            )
        if entry.mode == "local":
            payload["input_backend"] = "adb"
            _click_game_window2_service(payload)
        else:
            _click_remote_game_window2(entry, payload)
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
        self._raise_if_stopped(getattr(self, "_stop_event", None))
        payload = ActionPlanner().drag_point_payload(
            image,
            start_x,
            start_y,
            end_x,
            end_y,
            duration_ms=duration_ms,
        )
        entry: Any = ctx["entry"]
        self._save_action_trace(
            ctx,
            image,
            {
                "kind": "drag",
                "start": [float(start_x), float(start_y)],
                "end": [float(end_x), float(end_y)],
                "duration_ms": int(duration_ms),
                "label": (
                    f"drag #{self._image_number(image) or '?'} "
                    f"({float(start_x):.0f},{float(start_y):.0f})->({float(end_x):.0f},{float(end_y):.0f})"
                ),
            },
        )
        if entry.mode == "local":
            payload["input_backend"] = "adb"
            _drag_game_window2_service(payload)
        else:
            _drag_remote_game_window2(entry, payload)
        self._clear_tick_frame(ctx)

    def _shape_center(
        self,
        shape: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str | None = None,
        ctx: dict[str, Any] | None = None,
        *,
        strict_live: bool = False,
    ) -> tuple[float, float]:
        if strict_live and not bool(shape.get("floating")):
            raise RuntimeError(
                f"shape 不是 floating，不能严格定位实时中心：{shape.get('title') or shape.get('id')}"
            )
        if strict_live and (not frame_data_url or not isinstance(ctx, dict)):
            raise RuntimeError(
                f"严格定位实时中心缺少直播帧或运行上下文：{shape.get('title') or shape.get('id')}"
            )
        if bool(shape.get("floating")) and frame_data_url and isinstance(ctx, dict):
            try:
                match_result = self._match_shape(ctx, image, shape, frame_data_url)
                resolved = self._shape_match_resolved_click_point(image, shape, match_result)
                if resolved is not None and bool(match_result.get("matched")):
                    return resolved
            except Exception as exc:
                if strict_live:
                    raise RuntimeError(
                        f"浮动 shape 无法严格定位实时中心：{shape.get('title') or shape.get('id')}"
                    ) from exc
                self._log(
                    "detail",
                    f"浮动 shape 中心定位失败，退回参考坐标：{shape.get('title') or shape.get('id')}：{exc}",
                )
            if strict_live:
                raise RuntimeError(
                    f"浮动 shape 未唯一匹配实时中心：{shape.get('title') or shape.get('id')}"
                )
        return ActionPlanner().shape_center(image, shape)

    def _click_generic_back(self, ctx: dict[str, Any]) -> None:
        image = self._image(ctx, "settings") or self._image(ctx, "world")
        if not image:
            return
        width, height = self._frame_size(image)
        self._click_frame_point(ctx, image, width * 0.085, height * 0.947)

    def _keyevents(self, ctx: dict[str, Any], keys: list[str]) -> None:
        payload = {"keys": keys}
        entry: Any = ctx["entry"]
        (_keyevent_game_window2_service(payload) if entry.mode == "local" else _keyevent_remote_game_window2(entry, payload))
        self._clear_tick_frame(ctx)

    def _text(self, ctx: dict[str, Any], text: str) -> None:
        payload = {"text": text}
        entry: Any = ctx["entry"]
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

    def _wait_expected_scene(
        self, ctx: dict[str, Any], stop_event: threading.Event, scene_ids: list[int],
        *, timeout: float, label: str,
    ) -> tuple[int, float]:
        """兼容旧日常调用；严格落点等待由交互上下文统一实现。"""
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(
            ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event,
        )
        match = yield from context.wait_scene_exact(scene_ids, timeout=timeout, label=label)
        return int(match.scene_id), float(match.score)

    def _wait_scene_id(
        self, ctx: dict[str, Any], stop_event: threading.Event, target_scene_id: int,
        *, timeout: float, label: str = "等待场景",
    ):
        """保留旧单场景入口的二元结果和 RuntimeError 超时契约。"""
        try:
            return (yield from self._wait_expected_scene(
                ctx, stop_event, [target_scene_id], timeout=timeout, label=label,
            ))
        except TimeoutError as exc:
            raise RuntimeError(str(exc)) from exc


    def _ocr_frame(self, frame_data_url: str, *, options: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = _recognize_data_annotation_ocr_frame(frame_data_url, options=options)
        except Exception as exc:
            self._log("detail", f"OCR 失败：{exc}")
            return {"lines": [], "tokens": []}
        return {
            "lines": [line.model_dump() for line in response.lines],
            "tokens": [token.model_dump() for token in response.tokens],
        }

    def _ocr_tokens(self, frame_data_url: str) -> list[dict[str, Any]]:
        response = self._ocr_frame(frame_data_url)
        tokens = response.get("tokens")
        return tokens if isinstance(tokens, list) else []

    def _ocr_fragments(self, frame_data_url: str) -> list[dict[str, Any]]:
        response = self._ocr_frame(frame_data_url)
        lines = response.get("lines")
        return lines if isinstance(lines, list) else []

    def _ocr_fragments_in_shapes(
        self,
        frame_data_url: str,
        image: dict[str, Any],
        shape_titles: tuple[str, ...] | list[str],
        *,
        padding: int = 16,
        options: dict[str, Any] | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if isinstance(ctx, dict):
            query_box = self._query_box_for_shapes(image, shape_titles, padding=padding, ctx=ctx)
            if query_box is None:
                return []
            cached = self._shared_spatial_ocr_result(ctx, frame_data_url, options=options)
            return query_ocr_lines(cached.get("lines") or [], query_box)
        crop = self._crop_frame_data_url_for_shapes(frame_data_url, image, shape_titles, padding=padding, ctx=ctx)
        if crop is None:
            return []
        crop_data_url, offset_x, offset_y = crop
        response = self._ocr_frame(crop_data_url, options=options)
        lines = response.get("lines") if isinstance(response.get("lines"), list) else []
        for line in lines:
            line["x"] = float(line.get("x") or 0) + offset_x
            line["y"] = float(line.get("y") or 0) + offset_y
        return lines

    def _ocr_tokens_in_shapes(
        self,
        frame_data_url: str,
        image: dict[str, Any],
        shape_titles: tuple[str, ...] | list[str],
        *,
        padding: int = 16,
        options: dict[str, Any] | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if isinstance(ctx, dict):
            query_box = self._query_box_for_shapes(image, shape_titles, padding=padding, ctx=ctx)
            if query_box is None:
                return []
            cached = self._shared_spatial_ocr_result(ctx, frame_data_url, options=options)
            spatial = query_spatial_ocr(cached.get("tokens") or [], query_box)
            return spatial.get("tokens") if isinstance(spatial.get("tokens"), list) else []
        crop = self._crop_frame_data_url_for_shapes(frame_data_url, image, shape_titles, padding=padding, ctx=ctx)
        if crop is None:
            return []
        crop_data_url, offset_x, offset_y = crop
        response = self._ocr_frame(crop_data_url, options=options)
        tokens = response.get("tokens") if isinstance(response.get("tokens"), list) else []
        for token in tokens:
            token["x"] = float(token.get("x") or 0) + offset_x
            token["y"] = float(token.get("y") or 0) + offset_y
        return tokens

    def _query_box_for_shapes(
        self,
        image: dict[str, Any],
        shape_titles: tuple[str, ...] | list[str],
        *,
        padding: int = 16,
        ctx: dict[str, Any] | None = None,
    ) -> dict[str, float] | None:
        boxes: list[dict[str, Any]] = []
        for title in shape_titles:
            shape = self._find_shape(image, str(title))
            if shape:
                source_image = self._effective_shape_source_image(ctx, image, shape) if isinstance(ctx, dict) else image
                boxes.append(self._box(shape, source_image))
                continue
            # Shape inheritance is resolved explicitly through
            # ``parentSceneIds`` before this helper is called.  Physical
            # asset-tree ancestors are editorial structure only.
        if not boxes:
            return None
        width = max(1.0, float(image.get("width") or 900))
        height = max(1.0, float(image.get("height") or 1600))
        left = max(0.0, min(float(box.get("x") or 0) for box in boxes) - padding)
        top = max(0.0, min(float(box.get("y") or 0) for box in boxes) - padding)
        right = min(width, max(float(box.get("x") or 0) + float(box.get("w") or 0) for box in boxes) + padding)
        bottom = min(height, max(float(box.get("y") or 0) + float(box.get("h") or 0) for box in boxes) + padding)
        if right <= left or bottom <= top:
            return None
        return {"x": left, "y": top, "w": right - left, "h": bottom - top}

    def _crop_frame_data_url_for_shapes(
        self,
        frame_data_url: str,
        image: dict[str, Any],
        shape_titles: tuple[str, ...] | list[str],
        *,
        padding: int = 16,
        ctx: dict[str, Any] | None = None,
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
                        source_image = self._effective_shape_source_image(ctx, image, shape) if isinstance(ctx, dict) else image
                        boxes.append(self._box(shape, source_image))
                        continue
                    # See ``_query_box_for_shapes``: no implicit physical
                    # parent inheritance is allowed here.
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
            self._log("detail", f"裁剪 OCR 区域失败，已按 shape 边界安全失败：{exc}")
            return None

    def _scene_shape_boxes(self, ctx: dict[str, Any], image: dict[str, Any]) -> list[dict[str, float]]:
        boxes: list[dict[str, float]] = []
        for shape in View(image).get_shapes(include_groups=False):
            source_image = self._effective_shape_source_image(ctx, image, shape.raw)
            box = self._box(shape.raw, source_image)
            if float(box.get("w") or 0) > 0 and float(box.get("h") or 0) > 0:
                boxes.append(box)
        return boxes

    @staticmethod
    def _deduplicate_spatial_ocr(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: dict[tuple[str, int, int, int, int], dict[str, Any]] = {}
        for item in items:
            key = (
                _sanitize_ocr_text(item.get("text")),
                round(float(item.get("x") or 0)),
                round(float(item.get("y") or 0)),
                round(float(item.get("w") or 0)),
                round(float(item.get("h") or 0)),
            )
            unique.setdefault(key, item)
        return sorted(unique.values(), key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0)))

    def _ocr_fragments_in_scene_shapes(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        image: dict[str, Any],
    ) -> list[dict[str, Any]]:
        boxes = self._scene_shape_boxes(ctx, image)
        if not boxes:
            return []
        cached = self._shared_spatial_ocr_result(ctx, frame_data_url)
        lines: list[dict[str, Any]] = []
        for box in boxes:
            lines.extend(query_ocr_lines(cached.get("lines") or [], box))
        return self._deduplicate_spatial_ocr(lines)

    def _ocr_tokens_in_scene_shapes(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        image: dict[str, Any],
    ) -> list[dict[str, Any]]:
        boxes = self._scene_shape_boxes(ctx, image)
        if not boxes:
            return []
        cached = self._shared_spatial_ocr_result(ctx, frame_data_url, options={"return_word_box": True})
        tokens: list[dict[str, Any]] = []
        for box in boxes:
            spatial = query_spatial_ocr(cached.get("tokens") or [], box)
            tokens.extend(spatial.get("tokens") or [])
        return self._deduplicate_spatial_ocr(tokens)

    def _recognized_scene_image(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
    ) -> dict[str, Any] | None:
        scene_id, _score = self._identify_scene_number(ctx, frame_data_url, preferred_scene_ids)
        if scene_id is None:
            return None
        image = (ctx.get("images") or {}).get(int(scene_id))
        return image if isinstance(image, dict) else None

    def _recognized_scene_ocr_fragments(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        image = self._recognized_scene_image(ctx, frame_data_url, preferred_scene_ids)
        if image is None:
            return []
        return self._ocr_fragments_in_scene_shapes(ctx, frame_data_url, image)

    def _recognized_scene_ocr_tokens(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        image = self._recognized_scene_image(ctx, frame_data_url, preferred_scene_ids)
        if image is None:
            return []
        return self._ocr_tokens_in_scene_shapes(ctx, frame_data_url, image)

    def _recognized_scene_ocr_text(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
    ) -> str:
        return self._ocr_text(
            self._recognized_scene_ocr_fragments(ctx, frame_data_url, preferred_scene_ids)
        )

    def _ocr_text(self, fragments: list[dict[str, Any]]) -> str:
        return "".join(_sanitize_ocr_text(fragment.get("text")) for fragment in fragments)

    def _ocr_fragment_box(self, fragment: dict[str, Any]) -> dict[str, float] | None:
        x = float(fragment.get("x") or 0)
        y = float(fragment.get("y") or 0)
        w = float(fragment.get("w") or 0)
        h = float(fragment.get("h") or 0)
        if w <= 0 or h <= 0:
            return None
        return {"x": x, "y": y, "w": w, "h": h}

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
        fragments: list[dict[str, Any]],
        image: dict[str, Any] | None,
        shape_title: str,
        *,
        include: tuple[str, ...],
        exclude: tuple[str, ...] = (),
        tokens: list[dict[str, Any]] | None = None,
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
        for fragment in fragments:
            text = _sanitize_ocr_text(fragment.get("text"))
            if not text:
                continue
            if include and not all(fragment in text for fragment in include):
                continue
            if exclude and any(fragment in text for fragment in exclude):
                continue
            fragment_x = float(fragment.get("x") or 0)
            fragment_w = float(fragment.get("w") or 0)
            cx = fragment_x + fragment_w / 2
            if include and fragment_w > 0 and text:
                target_fragment = next((fragment for fragment in include if fragment in text), "")
                if target_fragment:
                    token_box = locate_text_box(tokens or [], target_fragment)
                    if token_box is not None:
                        cx = float(token_box["x"]) + float(token_box["w"]) / 2
                        cy = float(token_box["y"]) + float(token_box["h"]) / 2
                    else:
                        cy = float(fragment.get("y") or 0) + float(fragment.get("h") or 0) / 2
                else:
                    cy = float(fragment.get("y") or 0) + float(fragment.get("h") or 0) / 2
            else:
                cy = float(fragment.get("y") or 0) + float(fragment.get("h") or 0) / 2
            if left <= cx <= right and top <= cy <= bottom:
                matches.append((cx, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _ocr_row_clicks_in_shape(
        self,
        lines: list[dict[str, Any]],
        image: dict[str, Any] | None,
        shape_title: str,
        *,
        include: tuple[str, ...],
        exclude: tuple[str, ...] = (),
        click_target: str = "shape_center",
        occlusion_boxes: Iterable[Mapping[str, Any]] = (),
    ) -> list[tuple[float, float, str]]:
        shape = self._find_shape(image, shape_title) if image else None
        if not shape or not image:
            return []
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        width = float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        default_click_x = left + width / 2
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
                click_x = default_click_x
                if click_target in {"text_center", "unoccluded_text"}:
                    text_x = float(line.get("x") or 0)
                    text_w = float(line.get("w") or 0)
                    click_x = max(left, min(left + width, text_x + text_w / 2))
                    if click_target == "unoccluded_text":
                        text_left = max(left, text_x)
                        text_right = min(left + width, text_x + text_w)
                        inset = min(8.0, max(2.0, text_w * 0.1))
                        candidates = (click_x, text_right - inset, text_left + inset)

                        def blocked(candidate_x: float) -> bool:
                            return any(
                                float(box.get("x") or 0) <= candidate_x
                                <= float(box.get("x") or 0) + float(box.get("w") or 0)
                                and float(box.get("y") or 0) <= cy
                                <= float(box.get("y") or 0) + float(box.get("h") or 0)
                                for box in occlusion_boxes
                            )

                        safe_x = next(
                            (candidate_x for candidate_x in candidates if text_left <= candidate_x <= text_right and not blocked(candidate_x)),
                            None,
                        )
                        if safe_x is None:
                            continue
                        click_x = safe_x
                matches.append((click_x, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _parse_fraction(self, text: str) -> tuple[int, int] | None:
        values = parse_ocr_values(_sanitize_ocr_text(text), expected_count=2)
        return (values[0], values[1]) if values is not None else None

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
        self._commit_scene_observation(ctx, frame_data_url, scene_id, score)
        if scene_id is not None:
            with self._lock:
                self._status.update({"current_scene": scene_id, "updated_at": time.time()})
        return scene_id, score, frame_data_url

    def _xianfu_home_text_is_scene(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        world_markers = (
            "日程",
            "角色",
            "装备",
            "星海",
            "功法书",
            "储物袋",
        )
        if any(marker in normalized for marker in world_markers):
            return False
        markers = (
            "玄机阁",
            "仙侣居",
            "仙侶居",
            "本命金身",
            "拜仙台",
            "寻仙台",
            "仙府管家",
        )
        hits = sum(1 for marker in markers if marker in normalized)
        if hits >= 2:
            return True
        return "仙府管家" in normalized and ("寻仙台" in normalized or "拜仙台" in normalized)

    def _is_xianfu_entry_cutscene(self, ctx: dict[str, Any], scene_id: int | None) -> bool:
        if scene_id != 185:
            return False
        image = (ctx.get("images") or {}).get(185) if isinstance(ctx.get("images"), dict) else None
        if not isinstance(image, dict):
            return False
        title = str(image.get("title") or "")
        shapes = image.get("shapes") if isinstance(image.get("shapes"), list) else []
        has_skip = any(str(shape.get("title") or "") == "跳过" for shape in shapes if isinstance(shape, dict))
        return has_skip and "过场" in title

    def _scene_jump_source_stall_timeout(
        self,
        *,
        source_scene_id: int,
        target_scene_id: int,
        expected_ids: list[int],
        shape: dict[str, Any],
    ) -> float:
        # Business-specific transition duration belongs in
        # ``layer0_wait_seconds``.  The navigation core keeps only a generic
        # minimum and extends it with that caller-supplied wait window.
        return 8.0

    def _scene_jump_preferred_wait_seconds(
        self,
        *,
        source_scene_id: int,
        target_scene_id: int,
        expected_ids: list[int],
        shape: dict[str, Any],
        requested_wait_seconds: float | None,
    ) -> float:
        preferred = max(
            0.0,
            float(
                requested_wait_seconds
                if requested_wait_seconds is not None
                else (DEFAULT_LAYER0_WAIT_SECONDS if expected_ids else 0.0)
            ),
        )
        # #609 is the offline-cultivation acknowledgement shown after login.
        # Its confirmed landing can be either #34 or #20.  A stream of reward
        # banners may temporarily cover #20's required identity even though no
        # further input is needed.  Keep the exact declared Layer-0 candidates
        # alive until the banners settle; a reliable #20/#34 match still
        # returns immediately, so the longer ceiling adds no normal-path cost.
        if (
            int(source_scene_id) == 609
            and int(target_scene_id) == 34
            and str(shape.get("title") or "").strip() == "确定"
            and {20, 34}.issubset({int(item) for item in expected_ids})
        ):
            preferred = max(preferred, OFFLINE_CULTIVATION_SETTLE_WAIT_SECONDS)
        return preferred

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
        return_source_on_stall: bool = False,
        layer0_wait_seconds: float | None = None,
    ):
        ctx.pop("_last_scene_jump_evidence", None)
        shape = edge["shape"]
        expected_ids = list(edge.get("target_ids") or [])
        allows_self = source_scene_id in expected_ids
        preferred_wait_seconds = self._scene_jump_preferred_wait_seconds(
            source_scene_id=source_scene_id,
            target_scene_id=target_scene_id,
            expected_ids=expected_ids,
            shape=shape,
            requested_wait_seconds=layer0_wait_seconds,
        )
        # A declared historical self-loop may be accepted after 30 seconds of
        # stable source-scene evidence, but it must not shorten the independent
        # continuous-unknown budget.  Slow world transitions can temporarily
        # produce frames without any scene identity; escalating those at the
        # self-loop deadline violates go_scene's 60-second unknown contract.
        self_loop_timeout_seconds = max(30.0, preferred_wait_seconds)
        timeout_seconds = max(
            DEFAULT_GO_SCENE_CONTINUOUS_UNKNOWN_SECONDS,
            preferred_wait_seconds,
        )
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_frame = ""
        history: list[str] = []
        left_source = False
        shape_jump_target = str(shape.get("sceneJumpTarget") or "").strip()
        landing_counts = self._scene_jump_target_counts(tree, shape)
        dynamic_landing = bool(edge.get("_dynamic_confirm_edge")) or shape_jump_target == "-1" or shape_jump_target.startswith("-1(")
        source_stall_timeout = self._scene_jump_source_stall_timeout(
            source_scene_id=source_scene_id,
            target_scene_id=target_scene_id,
            expected_ids=expected_ids,
            shape=shape,
        )
        source_stall_timeout = max(source_stall_timeout, preferred_wait_seconds)
        context = self._behavior_tree_context(
            ctx,
            asset_tree_path,
            stop_event=stop_event,
        )
        first_poll = True
        unconfirmed_unexpected_landing: int | None = None

        def remember_landing(
            scene_id: int | None,
            score: float,
            frame_data_url: str,
            elapsed_seconds: float,
            *,
            outcome: str,
        ) -> None:
            self._commit_scene_observation(ctx, frame_data_url, scene_id, score)
            ctx["_last_scene_jump_evidence"] = {
                "scene_id": scene_id,
                "score": float(score or 0.0),
                "frame_data_url": frame_data_url,
                "elapsed_seconds": float(elapsed_seconds),
                "history": list(history[-40:]),
                "outcome": outcome,
            }

        while True:
            self._raise_if_stopped(stop_event)
            if not first_poll:
                # Layer-0 waiting is a real fresh-frame polling window, not a
                # tight loop whose pace accidentally depends on the outer
                # behavior-tree driver.  The explicit interruptible wait also
                # gives long UI transitions time to publish their stable HUD.
                yield from self._wait_action_settle(
                    ctx,
                    stop_event,
                    seconds=DEFAULT_SCENE_RECOGNITION_POLL_SECONDS,
                )
            first_poll = False
            elapsed = time.monotonic() - start

            expected_id_set = {int(item) for item in expected_ids}
            fallback_scene_id: int | None = None
            fallback_score = 0.0
            with self._scene_observation_probe(ctx):
                _wait_scene_match = yield from context.wait_scene(expected_ids or None, label=f'场景跳转：等待 #{source_scene_id} 动作落点', wait=5.0, required=False)
                (matched_expected, expected_score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
            if matched_expected == source_scene_id and source_scene_id not in expected_ids:
                history.append(f"{elapsed:.1f}s #{matched_expected} {expected_score:.0f}% preferred-source ignored left={left_source}")
                matched_expected = None
            if matched_expected is not None and int(matched_expected) not in expected_id_set:
                fallback_scene_id, fallback_score = int(matched_expected), float(expected_score or 0.0)
                history.append(
                    f"{elapsed:.1f}s #{fallback_scene_id} {fallback_score:.0f}% "
                    f"preferred-fallback expected={expected_ids} left={left_source}"
                )
                matched_expected = None
            # ``sceneJumpTarget`` is learned from historical landings.  The
            # source scene may therefore be a declared candidate even while a
            # delayed transition or confirmation popup is still forming.  Do
            # not terminally accept that first source frame here: keep the
            # action's expected-scene scope alive and let the bounded
            # ``declared_self_loop`` branch below confirm a real self-loop.
            if matched_expected is not None and matched_expected != source_scene_id:
                if (
                    landing_counts.get(int(matched_expected), 0) <= 1
                    and unconfirmed_unexpected_landing != int(matched_expected)
                ):
                    # A single historical hit (or a declared-only target) can
                    # be a transient misattribution. Confirm it on a second
                    # fresh frame before treating it as a graph landing.
                    unconfirmed_unexpected_landing = int(matched_expected)
                    history.append(f"{elapsed:.1f}s #{matched_expected} rare-declared-await-fresh-frame")
                    continue
                last_scene_id, last_score, last_frame = matched_expected, expected_score, frame
                if matched_expected != source_scene_id:
                    left_source = True
                history.append(f"{elapsed:.1f}s #{matched_expected} {expected_score:.0f}% expected={expected_score:.0f}% left={left_source}")
                if not edge.get("_dynamic_confirm_edge"):
                    self._record_scene_jump_landing(
                        ctx,
                        asset_tree_path,
                        tree,
                        shape,
                        int(matched_expected),
                        reason="命中声明落点",
                    )
                self._log("info", f"场景跳转：#{source_scene_id} -> #{matched_expected}，{elapsed:.1f}s")
                remember_landing(int(matched_expected), float(expected_score or 0.0), frame, elapsed, outcome="declared_landing")
                return matched_expected

            # Reuse the single layered result; navigation cannot re-identify
            # the same frame by path convenience or discard its specific ID.
            scene_id, score = (
                (fallback_scene_id, fallback_score)
                if fallback_scene_id is not None
                else (matched_expected, expected_score)
            )
            if scene_id != unconfirmed_unexpected_landing:
                unconfirmed_unexpected_landing = None
            last_scene_id, last_score, last_frame = scene_id, score, frame
            if scene_id is not None and scene_id != source_scene_id and int(scene_id) in expected_ids:
                if (
                    landing_counts.get(int(scene_id), 0) <= 1
                    and unconfirmed_unexpected_landing != int(scene_id)
                ):
                    unconfirmed_unexpected_landing = int(scene_id)
                    history.append(f"{elapsed:.1f}s #{scene_id} rare-declared-await-fresh-frame")
                    continue
                left_source = True
                history.append(f"{elapsed:.1f}s #{scene_id} {score:.0f}% declared-landing left={left_source}")
                if not edge.get("_dynamic_confirm_edge"):
                    self._record_scene_jump_landing(
                        ctx,
                        asset_tree_path,
                        tree,
                        shape,
                        int(scene_id),
                        reason="命中声明落点",
                    )
                self._log("info", f"场景跳转：#{source_scene_id} -> #{scene_id}，{elapsed:.1f}s，命中声明落点")
                remember_landing(int(scene_id), float(score or 0.0), frame, elapsed, outcome="declared_landing")
                return int(scene_id)
            if (
                scene_id is not None
                and scene_id != source_scene_id
                and int(scene_id) not in expected_id_set
                and scene_id != target_scene_id
                and expected_ids
                and preferred_wait_seconds > 0
                and elapsed < preferred_wait_seconds
                and self._find_scene_route(tree, int(scene_id), target_scene_id) is None
            ):
                left_source = True
                scene_text = f"#{scene_id}"
                history.append(f"{elapsed:.1f}s {scene_text} layer0-wait-before-replan target={expected_ids}")
                with self._lock:
                    self._status.update({
                        "phase": "go_scene_wait_layer0",
                        "current_scene": scene_id,
                        "message": (
                            f"跳转等待：#{source_scene_id} -> #{target_scene_id}，"
                            f"继续等待预期落点 {expected_ids}，当前 {scene_text} {score:.0f}%"
                        ),
                        "updated_at": time.time(),
                    })
                continue
            # A/B/C in sceneJumpTarget are historical observations, not a
            # whitelist.  A reliably recognized new D is equally valid: record
            # it in the same frequency table and let the outer goto planner
            # continue from D.  Only true unknown/recovery exhaustion is fatal.
            if scene_id is not None and scene_id != source_scene_id:
                if unconfirmed_unexpected_landing != int(scene_id):
                    # One asynchronous or transitional frame is not evidence
                    # that this click caused a new graph edge. Reobserve the
                    # same scene after the normal fresh-frame polling interval
                    # before persisting a previously undeclared landing.
                    unconfirmed_unexpected_landing = int(scene_id)
                    history.append(f"{elapsed:.1f}s #{scene_id} unexpected-await-fresh-frame")
                    continue
                left_source = True
                history.append(f"{elapsed:.1f}s #{scene_id} {score:.0f}% observed-landing left={left_source}")
                if not dynamic_landing:
                    self._record_scene_jump_landing(
                        ctx,
                        asset_tree_path,
                        tree,
                        shape,
                        int(scene_id),
                        reason="实际识别落点",
                    )
                route_exists = self._find_scene_route(tree, int(scene_id), target_scene_id) is not None
                if scene_id == target_scene_id:
                    self._log("info", f"场景跳转：#{source_scene_id} -> #{scene_id}，{elapsed:.1f}s，到达目标")
                elif route_exists:
                    self._log(
                        "info",
                        f"场景跳转：#{source_scene_id} -> #{scene_id}，{elapsed:.1f}s，记录落点并重新规划到 #{target_scene_id}",
                    )
                else:
                    self._log(
                        "warning",
                        f"场景跳转：#{source_scene_id} -> #{scene_id}，{elapsed:.1f}s，已记录新落点；上层将尝试通用恢复",
                    )
                remember_landing(int(scene_id), float(score or 0.0), frame, elapsed, outcome="observed_landing")
                return int(scene_id)
            if scene_id is not None and scene_id != source_scene_id:
                left_source = True
            scene_text = f"#{scene_id}" if scene_id is not None else "unknown"
            history.append(f"{elapsed:.1f}s {scene_text} {score:.0f}% expected={expected_score:.0f}% left={left_source}")
            if expected_ids and preferred_wait_seconds > 0 and elapsed < preferred_wait_seconds:
                history.append(f"{elapsed:.1f}s {scene_text} layer0-wait target={expected_ids}")
                with self._lock:
                    self._status.update({
                        "phase": "go_scene_wait_layer0",
                        "current_scene": scene_id,
                        "message": (
                            f"跳转等待：#{source_scene_id} -> #{target_scene_id}，"
                            f"继续等待预期落点 {expected_ids}，当前 {scene_text} {score:.0f}%"
                        ),
                        "updated_at": time.time(),
                    })
                continue
            with self._lock:
                self._status.update({
                    "phase": "go_scene_wait",
                    "current_scene": scene_id,
                    "message": f"跳转等待：#{source_scene_id} -> #{target_scene_id}，当前 {scene_text} {score:.0f}%",
                    "updated_at": time.time(),
                })

            if not left_source and last_scene_id == source_scene_id and not allows_self and elapsed >= source_stall_timeout:
                self._record_scene_jump_landing(
                    ctx,
                    asset_tree_path,
                    tree,
                    shape,
                    source_scene_id,
                    reason="点击后稳定识别仍在源场景",
                )
                if return_source_on_stall:
                    self._log(
                        "warning",
                        f"场景跳转：#{source_scene_id} 点击「{shape.get('title') or '未命名'}」"
                        f"{elapsed:.1f}s 后仍停在源场景，回到动态选择尝试其它候选",
                    )
                    remember_landing(source_scene_id, float(last_score or score or 0.0), last_frame or frame, elapsed, outcome="source_stall")
                    return source_scene_id
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

            if (
                allows_self
                and last_scene_id == source_scene_id
                and elapsed >= self_loop_timeout_seconds
            ):
                self._record_scene_jump_landing(
                    ctx,
                    asset_tree_path,
                    tree,
                    shape,
                    source_scene_id,
                    reason="声明自环并稳定识别源场景",
                )
                self._log("info", f"场景跳转：#{source_scene_id} -> #{source_scene_id}，30s 保底确认自身")
                remember_landing(source_scene_id, float(last_score or 0.0), last_frame or frame, elapsed, outcome="declared_self_loop")
                return source_scene_id

            if elapsed < timeout_seconds:
                continue

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
                self._record_scene_jump_landing(
                    ctx,
                    asset_tree_path,
                    tree,
                    shape,
                    source_scene_id,
                    reason="点击后超时仍稳定识别源场景",
                )
                if return_source_on_stall:
                    self._log(
                        "warning",
                        f"场景跳转：#{source_scene_id} 点击「{shape.get('title') or '未命名'}」"
                        f"{elapsed:.1f}s 后仍停在源场景，回到动态选择尝试其它候选",
                    )
                    remember_landing(source_scene_id, float(last_score or 0.0), last_frame or frame, elapsed, outcome="source_stall")
                    return source_scene_id
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

            if not dynamic_landing:
                self._record_scene_jump_landing(
                    ctx,
                    asset_tree_path,
                    tree,
                    shape,
                    int(last_scene_id),
                    reason="超时前稳定识别落点",
                )
            self._log(
                "warning",
                f"场景跳转：#{source_scene_id} -> #{last_scene_id}，超时前稳定识别；记录落点并交由上层重新规划",
            )
            remember_landing(int(last_scene_id), float(last_score or 0.0), last_frame or frame, elapsed, outcome="timeout_landing")
            return int(last_scene_id)

    def _wait_for_go_scene_recognition(
        self,
        ctx: dict[str, Any],
        context: BehaviorTreeContext,
        tree: list[dict[str, Any]],
        target_scene_id: int,
        stop_event: threading.Event,
        initial_frame: str,
        *,
        candidate_scene_ids: list[int] | None = None,
        wait_seconds: float = DEFAULT_GO_SCENE_CONTINUOUS_UNKNOWN_SECONDS,
        max_wait_seconds: float = DEFAULT_GO_SCENE_OBSERVATION_TIMEOUT_SECONDS,
    ):
        """Keep observing until a scene is known or unknown is continuous.

        Candidates are only fresh-frame Layer-0 hints; misses retain the full
        layered fallback. No candidates means a normal global observation.
        Unknown is not a navigation node.  Every pass consumes a fresh full
        recognition result and yields without action until ``wait_seconds``
        has elapsed without a reliable Layer 0/1/2 scene id.  Layer 3 scores
        and unresolved ambiguity remain auxiliary evidence for the unknown.
        """

        started_at = time.monotonic()
        wait_seconds = max(0.0, float(wait_seconds))
        max_wait_seconds = max(wait_seconds, float(max_wait_seconds))
        frame = initial_frame
        best_score = 0.0
        best_layer3_auxiliary: dict[str, Any] | None = None
        attempts = 0
        continuous_unknown_started_at: float | None = None
        ctx.pop("_last_go_scene_recognition_wait_elapsed", None)
        ctx.pop("_last_go_scene_recognition_evidence", None)

        while True:
            self._raise_if_stopped(stop_event)
            attempts += 1
            ctx.pop("_last_scene_recognition_status", None)
            with self._scene_observation_probe(ctx):
                _wait_scene_match = yield from context.wait_scene(candidate_scene_ids or None, label=f'场景移动：识别前往 #{target_scene_id} 的当前位置', wait=5.0, required=False)
                (current_scene_id, score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
            recognition_status = str(
                ctx.pop(
                    "_last_scene_recognition_status",
                    "matched" if current_scene_id is not None else "no_match",
                )
                or "no_match"
            )
            best_score = max(best_score, float(score or 0.0))
            layer3_auxiliary = ctx.get("_last_layer3_auxiliary")
            if isinstance(layer3_auxiliary, dict) and (
                best_layer3_auxiliary is None
                or float(layer3_auxiliary.get("score") or 0.0)
                > float(best_layer3_auxiliary.get("score") or 0.0)
            ):
                best_layer3_auxiliary = dict(layer3_auxiliary)

            if current_scene_id is not None and self._scene_matches_id(
                int(current_scene_id),
                float(score or 0.0),
            ):
                # A first-pass match did not wait.  Avoid another clock read
                # here: callers only need the actual duration for an eventual
                # unknown result, and navigation tests deliberately advance a
                # fake clock when recording historical landings.
                ctx["_last_go_scene_recognition_wait_elapsed"] = 0.0
                if attempts > 1:
                    self._log(
                        "info",
                        f"场景移动：整体识别等待后命中 #{current_scene_id} {float(score or 0.0):.0f}%",
                    )
                self._commit_scene_observation(ctx, frame, current_scene_id, score)
                return current_scene_id, float(score or 0.0), frame, recognition_status

            now = time.monotonic()
            elapsed = now - started_at
            # Any result without a reliable scene id is still unknown for
            # navigation.  The initial frame already exists when this wait
            # starts, so its first unresolved result is timed from started_at;
            # starting after recognition finishes makes equal 60s limits
            # mathematically impossible to satisfy in real context.
            if continuous_unknown_started_at is None:
                continuous_unknown_started_at = started_at if attempts == 1 else now
            continuous_unknown_seconds = (
                max(0.0, now - continuous_unknown_started_at)
                if continuous_unknown_started_at is not None
                else 0.0
            )
            if continuous_unknown_seconds >= wait_seconds:
                ctx["_last_go_scene_recognition_wait_elapsed"] = elapsed
                ctx["_last_go_scene_recognition_evidence"] = {
                    "attempts": attempts,
                    "continuous_unknown_seconds": continuous_unknown_seconds,
                    "best_score": best_score,
                    "best_layer3_auxiliary": best_layer3_auxiliary,
                    "last_unresolved_status": recognition_status,
                }
                layer3_text = ""
                if best_layer3_auxiliary is not None:
                    layer3_text = (
                        f"，Layer 3 辅助参考 #{best_layer3_auxiliary.get('reference_id')} "
                        f"{float(best_layer3_auxiliary.get('score') or 0.0):.0f}%（非场景 ID）"
                    )
                self._log(
                    "warning",
                    f"场景移动：完整识别为 unknown（连续 {continuous_unknown_seconds:.1f}s），"
                    f"获得一次 #424 恢复资格，最佳分数 {best_score:.0f}%{layer3_text}",
                )
                self._commit_scene_observation(ctx, frame, None, best_score)
                return None, best_score, frame, "continuous_unknown"
            if elapsed >= max_wait_seconds:
                ctx["_last_go_scene_recognition_wait_elapsed"] = elapsed
                final_status = "ambiguous" if recognition_status == "ambiguous" else "observation_timeout"
                self._log(
                    "warning",
                    f"场景移动：观察达到 {max_wait_seconds:.1f}s，但未形成连续 "
                    f"{wait_seconds:.1f}s unknown；结果={final_status}，禁止执行 #424",
                )
                self._commit_scene_observation(ctx, frame, None, best_score)
                return None, best_score, frame, final_status

            unknown_remaining = wait_seconds - continuous_unknown_seconds
            remaining = min(unknown_remaining, max_wait_seconds - elapsed)
            with self._lock:
                self._status.update({
                    "phase": "go_scene_wait_recognition",
                    "current_scene": None,
                    "message": (
                        f"场景移动：未识别到可靠场景，保持当前业务等待；"
                        f"连续 unknown {continuous_unknown_seconds:.1f}/{wait_seconds:.1f}s，"
                        f"总等待 {elapsed:.1f}/{max_wait_seconds:.1f}s"
                    ),
                    "updated_at": time.time(),
                })
            yield from self._wait_action_settle(
                ctx,
                stop_event,
                seconds=min(DEFAULT_SCENE_RECOGNITION_POLL_SECONDS, remaining),
            )

    def _go_scene_task(
        self,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        target_scene_id: int,
        stop_event: threading.Event,
        *,
        layer0_wait_seconds: float | None = None,
        known_paths_only: bool = False,
    ):
        tree = ctx.get("asset_tree")
        if not isinstance(tree, list):
            tree = self._load_asset_tree(asset_tree_path)
            ctx["asset_tree"] = tree
            ctx["images"] = self._index_images(tree)

        failed_edge_keys_by_state: dict[str, set[tuple[Any, ...]]] = {}
        last_failed_edges_by_state: dict[str, dict[str, Any]] = {}
        explored_shape_keys: set[tuple[str, str, str]] = set()
        navigation_fallback_attempts: dict[tuple[str, str], dict[str, float | int]] = {}
        navigation_states: list[tuple[int | None, bytes, str]] = []
        stalled_edge_attempts: dict[tuple[Any, ...], int] = {}
        semantic_stalled_edge_attempts: dict[tuple[Any, ...], int] = {}
        repeated_landings: dict[tuple[Any, ...], int] = {}
        cycle_tracker = NavigationCycleTracker()
        globally_failed_edge_keys: set[tuple[Any, ...]] = set()
        alias_observation_attempted: set[int] = set()
        navigation_started_at = time.monotonic()
        incident_recorder = NavigationIncidentRecorder(
            self,
            ctx,
            asset_tree_path,
            target_scene_id=target_scene_id,
            started_monotonic=navigation_started_at,
        )
        ctx["_navigation_incident_recorder"] = incident_recorder
        # Attempt-local hints: never carry a stale landing into a new Cell.
        recognition_scene_ids: list[int] = []
        last_navigation_frame = ""
        last_navigation_scene_id: int | None = None
        last_navigation_score = 0.0
        context = self._behavior_tree_context(
            ctx,
            asset_tree_path,
            stop_event=stop_event,
        )

        def confirm_target_on_fresh_frame():
            """Reject a cached/transient target hit before reporting success."""
            yield from self._wait_action_settle(
                ctx,
                stop_event,
                seconds=DEFAULT_SCENE_RECOGNITION_POLL_SECONDS,
            )
            _wait_scene_match = yield from context.wait_scene([target_scene_id], label=f'场景移动：复核目标 #{target_scene_id}', wait=5.0, required=False)
            (full_scene_id, full_score, fresh_frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            confirmed = (
                full_scene_id == int(target_scene_id)
                and self._scene_matches_id(int(target_scene_id), float(full_score or 0.0))
            )
            observed_scene_id = (
                int(full_scene_id)
                if full_scene_id is not None
                and self._scene_matches_id(int(full_scene_id), float(full_score or 0.0))
                else None
            )
            return confirmed, observed_scene_id, float(full_score or 0.0), fresh_frame

        for _step_index in range(NAVIGATION_MAX_REPLAN_STEPS):
            self._raise_if_stopped(stop_event)
            if time.monotonic() - navigation_started_at >= NAVIGATION_STALL_MAX_SECONDS:
                incident_recorder.trigger(
                    trigger_type="duration_limit",
                    trigger_label="活跃导航累计 10 分钟仍未到达目标",
                    threshold={"max_duration_seconds": NAVIGATION_STALL_MAX_SECONDS},
                    frame_data_url=last_navigation_frame,
                    current_scene_id=last_navigation_scene_id,
                    current_score=last_navigation_score,
                    candidate_scene_ids=[
                        scene_id
                        for scene_id in (last_navigation_scene_id, target_scene_id)
                        if scene_id is not None
                    ],
                )
                incident_recorder.finalize(
                    status="unrecovered",
                    final_scene_id=last_navigation_scene_id,
                    final_score=last_navigation_score,
                    final_frame=last_navigation_frame,
                    message=f"导航达到 {NAVIGATION_STALL_MAX_SECONDS:.0f} 秒硬上限",
                )
                raise RuntimeError(f"场景移动停滞超过 {NAVIGATION_STALL_MAX_SECONDS:.0f} 秒，未到达 #{target_scene_id}")
            frame = self._screencap(ctx)
            last_navigation_frame = frame
            known_scene_id = ctx.pop("_go_scene_known_scene_id", None)
            if known_scene_id is not None:
                recognition_scene_ids = list(dict.fromkeys([int(known_scene_id), *recognition_scene_ids]))
            transition_guard = ctx.get("_go_scene_unknown_transition_guard")
            guarded_transition = False
            guarded_wait_seconds = DEFAULT_GO_SCENE_CONTINUOUS_UNKNOWN_SECONDS
            if int(target_scene_id) == 34 and isinstance(transition_guard, dict):
                try:
                    reference_scene_id = int(transition_guard.get("reference_scene_id") or 0)
                    threshold = float(transition_guard.get("similarity_threshold") or 94.0)
                    reference_image = (ctx.get("images") or {}).get(reference_scene_id)
                    similarity = (
                        self._scene_reference_similarity(ctx, reference_image, frame)
                        if isinstance(reference_image, dict) and reference_scene_id > 0
                        else None
                    )
                except (TypeError, ValueError):
                    similarity = None
                if similarity is not None and similarity >= threshold:
                    guarded_transition = True
                    guarded_wait_seconds = max(
                        DEFAULT_GO_SCENE_CONTINUOUS_UNKNOWN_SECONDS,
                        float(transition_guard.get("wait_seconds") or 120.0),
                    )
                    phase = str(transition_guard.get("phase") or "go_scene_wait_guarded_transition")
                    label = str(transition_guard.get("label") or "动态回城过场")
                    if not transition_guard.get("announced"):
                        transition_guard["announced"] = True
                        self._log(
                            "wait",
                            f"场景移动：检测到{label}（与 #{reference_scene_id} 全帧相似 "
                            f"{similarity:.0f}%），只等待可靠场景自然落地",
                        )
                    with self._lock:
                        self._status.update({
                            "phase": phase,
                            "current_scene": None,
                            "message": f"场景移动：{label}中，等待自然落到 #34",
                            "updated_at": time.time(),
                        })
            current_scene_id, score, frame, recognition_status = yield from self._wait_for_go_scene_recognition(
                ctx,
                context,
                tree,
                target_scene_id,
                stop_event,
                frame,
                candidate_scene_ids=recognition_scene_ids or None,
                wait_seconds=guarded_wait_seconds,
                max_wait_seconds=guarded_wait_seconds,
            )
            recognition_wait_elapsed = float(
                ctx.pop("_last_go_scene_recognition_wait_elapsed", 0.0) or 0.0
            )
            last_navigation_frame = frame
            last_navigation_scene_id = current_scene_id
            last_navigation_score = float(score or 0.0)
            navigation_state_key = self._navigation_state_key(frame, current_scene_id, navigation_states)
            failed_edge_keys = failed_edge_keys_by_state.setdefault(navigation_state_key, set())
            failed_edge_keys.update(globally_failed_edge_keys)
            last_failed_edge = last_failed_edges_by_state.get(navigation_state_key)
            if current_scene_id is None:
                if recognition_status != "continuous_unknown":
                    return self._save_unknown_scene_frame(
                        ctx,
                        asset_tree_path,
                        tree,
                        frame,
                        target_scene_id=target_scene_id,
                        current_scene_id=None,
                        action_shape=None,
                        elapsed_seconds=recognition_wait_elapsed,
                        history=[
                            f"整体识别等待 {recognition_wait_elapsed:.1f}s 后为 {recognition_status} "
                            f"{score:.0f}%；未形成连续一分钟 unknown，禁止执行 #424"
                        ],
                    )
                if not known_paths_only and (yield from self._wait_or_click_navigation_fallback_return(
                    ctx,
                    frame,
                    stop_event,
                    navigation_state_key=navigation_state_key,
                    target_scene_id=target_scene_id,
                    attempted_actions=navigation_fallback_attempts,
                    current_score=score,
                    incident_recorder=incident_recorder,
                )):
                    continue
                return self._save_unknown_scene_frame(
                    ctx,
                    asset_tree_path,
                    tree,
                    frame,
                    target_scene_id=target_scene_id,
                    current_scene_id=None,
                    action_shape=None,
                    elapsed_seconds=recognition_wait_elapsed,
                    history=[
                        f"完整识别管线连续 {recognition_wait_elapsed:.1f}s "
                        f"unknown，最佳分数 {score:.0f}%"
                    ],
                )
            if current_scene_id == target_scene_id and self._scene_matches_id(int(target_scene_id), float(score or 0.0)):
                confirmed, observed_scene_id, observed_score, fresh_frame = yield from confirm_target_on_fresh_frame()
                if not confirmed:
                    observed_text = f"#{observed_scene_id}" if observed_scene_id is not None else "unknown"
                    self._log(
                        "warning",
                        f"场景移动：#{target_scene_id} 首次命中未通过新帧确认，当前 {observed_text} "
                        f"{observed_score:.0f}%，继续重新规划",
                    )
                    if observed_scene_id is not None:
                        ctx["_go_scene_known_scene_id"] = observed_scene_id
                    last_navigation_frame = fresh_frame
                    continue
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"已在目标场景 #{target_scene_id}")
                incident_recorder.finalize(
                    status="recovered_with_fallback" if incident_recorder.fallback_used else "recovered_after_stall",
                    final_scene_id=target_scene_id,
                    final_score=score,
                    final_frame=frame,
                    message="重新规划后到达目标场景",
                )
                ctx.pop("_navigation_incident_recorder", None)
                return "success"
            has_navigation_edge = current_scene_id is not None and self._select_scene_next_edge(
                tree,
                int(current_scene_id),
                target_scene_id,
                failed_edge_keys=failed_edge_keys,
            ) is not None
            last_failed_source_matches = False
            if last_failed_edge is not None and current_scene_id is not None:
                try:
                    last_failed_source_matches = int(last_failed_edge.get("source_id")) == int(current_scene_id)
                except (TypeError, ValueError):
                    last_failed_source_matches = False
            if not has_navigation_edge and not last_failed_source_matches:
                current_scene_id = self._navigation_scene_id(ctx, current_scene_id, frame)
                navigation_state_key = self._navigation_state_key(frame, current_scene_id, navigation_states)
                failed_edge_keys = failed_edge_keys_by_state.setdefault(navigation_state_key, set())
                failed_edge_keys.update(globally_failed_edge_keys)
                last_failed_edge = last_failed_edges_by_state.get(navigation_state_key)
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
                    history=[f"弱兜底匹配不可作为导航起点 {score:.0f}%；禁止直接执行 #424"],
                )
            if current_scene_id == target_scene_id and self._scene_matches_id(int(target_scene_id), float(score or 0.0)):
                confirmed, observed_scene_id, observed_score, fresh_frame = yield from confirm_target_on_fresh_frame()
                if not confirmed:
                    observed_text = f"#{observed_scene_id}" if observed_scene_id is not None else "unknown"
                    self._log(
                        "warning",
                        f"场景移动：#{target_scene_id} 首次命中未通过新帧确认，当前 {observed_text} "
                        f"{observed_score:.0f}%，继续重新规划",
                    )
                    if observed_scene_id is not None:
                        ctx["_go_scene_known_scene_id"] = observed_scene_id
                    last_navigation_frame = fresh_frame
                    continue
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"已在目标场景 #{target_scene_id}")
                incident_recorder.finalize(
                    status="recovered_with_fallback" if incident_recorder.fallback_used else "recovered_after_stall",
                    final_scene_id=target_scene_id,
                    final_score=score,
                    final_frame=frame,
                    message="重新规划后到达目标场景",
                )
                ctx.pop("_navigation_incident_recorder", None)
                return "success"

            current_image = (ctx.get("images") or {}).get(int(current_scene_id))
            alias_target = (
                str(current_image.get("navigationAliasOf") or "").strip()
                if isinstance(current_image, dict) else ""
            )
            alias_id = int(alias_target) if alias_target.isdecimal() else None
            if (
                alias_id is not None
                and alias_id != int(target_scene_id)
                and int(current_scene_id) not in alias_observation_attempted
            ):
                # An overlaid scene may settle into its annotated base scene
                # without any input.  Give that transition one short chance
                # before choosing a longer clickable route from the overlay.
                alias_observation_attempted.add(int(current_scene_id))
                with self._scene_observation_probe(ctx):
                    alias_match = yield from context.wait_scene(
                        [alias_id],
                        label=f"场景移动：等待 #{current_scene_id} 别名自然落到 #{alias_id}",
                        wait=3.0,
                        required=False,
                    )
                if (
                    alias_match is not None
                    and alias_match.scene_id == alias_id
                    and self._scene_matches_id(alias_id, float(alias_match.score or 0.0))
                ):
                    overlay_scene_id = int(current_scene_id)
                    current_scene_id = alias_id
                    score = float(alias_match.score or 0.0)
                    frame = alias_match.frame_data_url or frame
                    self._commit_scene_observation(ctx, frame, current_scene_id, score)
                    last_navigation_frame = frame
                    last_navigation_scene_id = current_scene_id
                    last_navigation_score = score
                    navigation_state_key = self._navigation_state_key(frame, current_scene_id, navigation_states)
                    failed_edge_keys = failed_edge_keys_by_state.setdefault(navigation_state_key, set())
                    failed_edge_keys.update(globally_failed_edge_keys)
                    last_failed_edge = last_failed_edges_by_state.get(navigation_state_key)
                    self._log("info", f"场景移动：#{overlay_scene_id} 别名新帧确认 #{alias_id}，直接规划 #{target_scene_id}")
            if alias_target == str(int(target_scene_id)):
                # A scene annotation may declare that it overlays another
                # scene's identity.  This is not a clickable graph edge:
                # accept the target only after a fresh frame independently
                # recognizes that target underneath the overlay.  Otherwise
                # preserve the live frame instead of inventing a route.
                confirmed = False
                observed_scene_id: int | None = None
                observed_score = 0.0
                fresh_frame = frame
                for _world_variant_confirm_attempt in range(3):
                    confirmed, observed_scene_id, observed_score, fresh_frame = yield from confirm_target_on_fresh_frame()
                    if confirmed:
                        break
                if not confirmed:
                    observed_text = f"#{observed_scene_id}" if observed_scene_id is not None else "unknown"
                    self._log(
                        "warning",
                        f"场景移动：当前 #{current_scene_id} 新帧未复核出 #{target_scene_id}"
                        f"（{observed_text} {observed_score:.0f}%），场景别名未获证实，保留现场",
                    )
                    incident_recorder.trigger(
                        trigger_type="scene_alias_unconfirmed",
                        trigger_label=f"#{current_scene_id} 场景别名未复核出 #{target_scene_id}",
                        threshold={
                            "observed_scene_id": int(observed_scene_id or 0),
                            "observed_score": round(float(observed_score or 0.0), 1),
                        },
                        frame_data_url=fresh_frame or frame,
                        current_scene_id=current_scene_id,
                        current_score=score,
                        candidate_scene_ids=[current_scene_id, target_scene_id],
                    )
                    incident_recorder.finalize(
                        status="unrecovered",
                        final_scene_id=current_scene_id,
                        final_score=score,
                        final_frame=fresh_frame or frame,
                        message=f"#{current_scene_id} 场景别名未复核出 #{target_scene_id}，已保留现场",
                    )
                    ctx.pop("_navigation_incident_recorder", None)
                    context.require_scene_repair(
                        int(current_scene_id),
                        fresh_frame or frame,
                        expected_scene_ids=[target_scene_id],
                        reason=f"go_scene({target_scene_id}) 失败：当前 #{current_scene_id} 未在新帧复核出 "
                               f"#{target_scene_id}（{observed_text} {observed_score:.0f}%）；"
                               "场景别名未获证实，已保留现场。",
                    )
                frame = fresh_frame
                score = observed_score
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"已在目标场景 #{target_scene_id}（#{current_scene_id} 场景别名新帧复核确认）")
                incident_recorder.finalize(
                    status="recovered_with_fallback" if incident_recorder.fallback_used else "recovered_after_stall",
                    final_scene_id=target_scene_id,
                    final_score=observed_score,
                    final_frame=fresh_frame,
                    message=f"#{current_scene_id} 场景别名经新帧确认已在 #{target_scene_id}",
                )
                ctx.pop("_navigation_incident_recorder", None)
                return "success"

            decision = self._select_scene_next_edge(
                tree,
                current_scene_id,
                target_scene_id,
                failed_edge_keys=failed_edge_keys,
            )
            current_image = (
                (ctx.get("images") or {}).get(int(current_scene_id))
                if isinstance(ctx.get("images"), dict)
                else None
            )
            current_is_layer1_hub = (
                isinstance(current_image, dict)
                and int(View(current_image).layer) == 1
            )
            if decision is None:
                if (
                    not known_paths_only
                    and int(current_scene_id or 0) == 611
                    and int(target_scene_id) == 34
                    and (yield from self._wait_or_click_navigation_fallback_return(
                        ctx,
                        frame,
                        stop_event,
                        navigation_state_key=navigation_state_key,
                        target_scene_id=target_scene_id,
                        attempted_actions=navigation_fallback_attempts,
                        current_scene_id=current_scene_id,
                        current_score=score,
                        incident_recorder=incident_recorder,
                    ))
                ):
                    continue
                # Layer-1 scenes are stable navigation hubs.  Missing a route
                # from a hub is an asset/business-path defect, not permission
                # to click generic blank/return actions and manufacture an
                # unknown transition.
                if not known_paths_only and isinstance(current_image, dict) and not current_is_layer1_hub:
                    decision = self._select_scene_exploration_edge(
                        tree,
                        current_image,
                        current_scene_id,
                        target_scene_id,
                        failed_edge_keys=failed_edge_keys,
                        explored_shape_keys=explored_shape_keys,
                        navigation_state_key=navigation_state_key,
                    )
            if decision is None:
                if current_is_layer1_hub:
                    incident_recorder.trigger(
                        trigger_type="layer1_route_missing",
                        trigger_label="Layer 1 枢纽缺少到目标的可靠路径，拒绝猜测点击",
                        threshold={"failed_edge_count": len(failed_edge_keys)},
                        frame_data_url=frame,
                        current_scene_id=current_scene_id,
                        current_score=score,
                        candidate_scene_ids=[current_scene_id, target_scene_id],
                    )
                    incident_recorder.finalize(
                        status="unrecovered",
                        final_scene_id=current_scene_id,
                        final_score=score,
                        final_frame=frame,
                        message="Layer 1 枢纽缺少可靠路径，已保留现场",
                    )
                    ctx.pop("_navigation_incident_recorder", None)
                    context.require_scene_repair(
                        int(current_scene_id), frame,
                        expected_scene_ids=[target_scene_id],
                        reason=f"go_scene({target_scene_id}) 失败：当前 #{current_scene_id} 是 Layer 1 枢纽，"
                               "但场景图没有可靠路径；已拒绝空白/#424 猜测点击。",
                    )
                incident_recorder.trigger(
                    trigger_type="normal_actions_exhausted",
                    trigger_label="当前已知场景没有可到达目标的低风险动作，拒绝冒充 unknown 使用 #424",
                    threshold={"failed_edge_count": len(failed_edge_keys)},
                    frame_data_url=frame,
                    current_scene_id=current_scene_id,
                    current_score=score,
                    candidate_scene_ids=[current_scene_id, target_scene_id],
                )
                incident_recorder.finalize(
                    status="unrecovered",
                    final_scene_id=current_scene_id,
                    final_score=score,
                    final_frame=frame,
                    message="无可用低风险导航动作",
                )
                ctx.pop("_navigation_incident_recorder", None)
                context.require_scene_repair(
                    int(current_scene_id), frame,
                    expected_scene_ids=[target_scene_id],
                    reason=f"go_scene({target_scene_id}) 失败：无法从当前#{current_scene_id}找到可达#{target_scene_id}的安全路径，"
                           f"已失败动作 {len(failed_edge_keys)} 个；需要核对身份及出口标注。",
                )
            edge = decision["edge"]
            image = edge["image"]
            shape = edge["shape"]
            shape_title = str(shape.get("title") or "未命名")
            if int(target_scene_id) == 34:
                # Returning to the stable world anchor is common and a false
                # positive here is unusually destructive: one stale/animated
                # frame once identified the real world as #69 and immediately
                # clicked #69「退出」at the lower-left world entry.  Require a
                # fresh-frame confirmation before every return-to-world click.
                _wait_scene_match = yield from context.wait_scene([current_scene_id], label='场景移动：回世界前复核当前场景', wait=5.0, required=False)
                (confirm_scene_id, confirm_score, confirm_frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                if (
                    confirm_scene_id != current_scene_id
                    or not self._scene_matches_id(int(confirm_scene_id), float(confirm_score or 0.0))
                ):
                    confirm_text = f"#{confirm_scene_id}" if confirm_scene_id is not None else "unknown"
                    self._log(
                        "warning",
                        (
                            f"场景移动：回 #34 前新帧由 #{current_scene_id} 变为 {confirm_text} "
                            f"{float(confirm_score or 0.0):.0f}%，取消本次「{shape_title}」点击并重新规划"
                        ),
                    )
                    if confirm_scene_id is not None and self._scene_matches_id(int(confirm_scene_id), float(confirm_score or 0.0)):
                        ctx["_go_scene_known_scene_id"] = int(confirm_scene_id)
                    continue
                frame = confirm_frame
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"场景移动：#{current_scene_id} -> #{target_scene_id}，点击 {shape_title}",
                    phase="go_scene",
                    current_scene=current_scene_id,
                )
            self._log(
                "action",
                f"场景移动：#{current_scene_id} -> #{target_scene_id}，点击 {shape_title}"
                f"（{decision['reason']}）",
            )
            if edge.get("_dynamic_exploration"):
                explored_shape_keys.add((
                    navigation_state_key,
                    str(shape.get("id") or ""),
                    str(shape.get("title") or ""),
                ))
            semantic_retry_key = (
                int(target_scene_id),
                *self._scene_jump_edge_semantic_key(edge),
            )
            no_response_count = semantic_stalled_edge_attempts.get(semantic_retry_key, 0)
            jitter_radius = self._navigation_retry_jitter_radius(no_response_count)
            navigation_action = str(shape.get("navigationAction") or "").strip()
            if navigation_action == "scroll_list_entry":
                outcome = yield from context.open_navigation_list_entry(
                    context.view(int(current_scene_id)),
                    Shape(shape, parent_view=context.view(int(current_scene_id))),
                )
                if outcome != "open":
                    raise RuntimeError(
                        f"场景移动：#{current_scene_id}「{shape_title}」滚动列表中未找到入口"
                    )
            elif navigation_action == "schedule_card_forward":
                from backend.core.fanxiu.data_annotation.schedule_cards import (
                    prepare_schedule_card_for_activity_ids,
                )

                forward_shape = str(shape.get("navigationForwardShape") or "").strip()
                if not forward_shape:
                    raise ValueError("场景跳转 Shape 缺少 navigationForwardShape")
                selected = yield from prepare_schedule_card_for_activity_ids(
                    context, shape.get("navigationRuntimeActivityIds") or (),
                )
                self._log("detail", f"场景移动：活动卡片身份已核验：{selected['title']}")
                context.click_shape(
                    int(current_scene_id),
                    forward_shape,
                    frame_data_url=context.cur_frame(update=True),
                )
            elif navigation_action == "world_menu_function":
                outcome = yield from context.open_navigation_world_menu_entry(
                    context.view(int(current_scene_id)),
                    Shape(shape, parent_view=context.view(int(current_scene_id))),
                )
                if outcome != "open":
                    raise RuntimeError(f"场景移动：#{current_scene_id}「{shape_title}」菜单入口未打开")
            elif navigation_action == "role_menu_feature":
                outcome = yield from context.open_navigation_role_menu_entry(
                    context.view(int(current_scene_id)),
                    Shape(shape, parent_view=context.view(int(current_scene_id))),
                )
                if outcome != "open":
                    raise RuntimeError(f"场景移动：#{current_scene_id}「{shape_title}」角色功能未打开")
            elif navigation_action == "floating_task_field":
                outcome = yield from context.open_navigation_floating_task_field(
                    context.view(int(current_scene_id)),
                    Shape(shape, parent_view=context.view(int(current_scene_id))),
                )
                if outcome != "open":
                    raise RuntimeError(f"场景移动：#{current_scene_id}「{shape_title}」浮动任务入口未找到")
            elif navigation_action == "activity_menu_item":
                outcome = yield from context.open_navigation_activity_menu_item(
                    context.view(int(current_scene_id)),
                    Shape(shape, parent_view=context.view(int(current_scene_id))),
                )
                if outcome != "open":
                    raise RuntimeError(f"场景移动：#{current_scene_id}「{shape_title}」活动菜单入口未打开")
            elif navigation_action:
                raise RuntimeError(f"场景移动：未知 navigationAction={navigation_action}")
            else:
                self._click_scene_route_shape(
                    ctx,
                    image,
                    shape,
                    frame,
                    jitter_radius=jitter_radius,
                )
            actual_scene_id = yield from self._wait_scene_jump_result(
                ctx,
                asset_tree_path,
                tree,
                source_scene_id=current_scene_id,
                target_scene_id=target_scene_id,
                edge=edge,
                stop_event=stop_event,
                return_source_on_stall=True,
                layer0_wait_seconds=layer0_wait_seconds,
            )
            # Replan with this action's context, including transition landings.
            # This is a candidate set, not permission to trust an old scene ID.
            recognition_scene_ids = list(dict.fromkeys(
                ([int(actual_scene_id)] if actual_scene_id is not None else [])
                + [int(value) for value in (edge.get("target_ids") or [])]
            ))
            landing_evidence = ctx.pop("_last_scene_jump_evidence", None)
            after_frame = (
                str(landing_evidence.get("frame_data_url") or "")
                if isinstance(landing_evidence, dict)
                else ""
            )
            landing_score = (
                float(landing_evidence.get("score") or 0.0)
                if isinstance(landing_evidence, dict)
                else 0.0
            )
            frame_similarity = _image_similarity_percent(self, frame, after_frame)
            incident_recorder.record_action(
                kind="navigation",
                source_scene_id=current_scene_id,
                source_score=score,
                shape=shape,
                reason=str(decision.get("reason") or ""),
                before_frame=frame,
                landing_scene_id=actual_scene_id,
                landing_score=landing_score,
                after_frame=after_frame,
                frame_similarity=frame_similarity,
                navigation_state_key=navigation_state_key,
                point=ActionPlanner().shape_center(image, shape),
            )
            last_navigation_frame = after_frame or frame
            last_navigation_scene_id = actual_scene_id
            last_navigation_score = landing_score
            if actual_scene_id == target_scene_id:
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"到达目标场景 #{target_scene_id}")
                incident_recorder.finalize(
                    status="recovered_with_fallback" if incident_recorder.fallback_used else "recovered_after_stall",
                    final_scene_id=target_scene_id,
                    final_score=landing_score,
                    final_frame=after_frame,
                    message="导航停滞后重新规划成功",
                )
                ctx.pop("_navigation_incident_recorder", None)
                return "success"
            if actual_scene_id == current_scene_id:
                scene_edge_key = self._scene_jump_edge_key(edge)
                semantic_scene_edge_key = self._scene_jump_edge_semantic_key(edge)
                edge_key = (navigation_state_key, *scene_edge_key)
                attempts = stalled_edge_attempts.get(edge_key, 0) + 1
                stalled_edge_attempts[edge_key] = attempts
                semantic_edge_key = (
                    int(target_scene_id),
                    *self._scene_jump_edge_semantic_key(edge),
                )
                semantic_attempts = semantic_stalled_edge_attempts.get(semantic_edge_key, 0) + 1
                semantic_stalled_edge_attempts[semantic_edge_key] = semantic_attempts
                last_failed_edge = edge
                last_failed_edges_by_state[navigation_state_key] = edge
                if (
                    attempts >= NAVIGATION_STATE_EDGE_RETRY_LIMIT
                    and frame_similarity is not None
                    and frame_similarity >= NAVIGATION_STABLE_FRAME_SIMILARITY
                ):
                    incident_recorder.trigger(
                        trigger_type="stable_self_loop",
                        trigger_label="同一场景、同一动作重复执行且真实画面稳定，未向目标推进",
                        threshold={
                            "state_attempts": attempts,
                            "semantic_attempts": semantic_attempts,
                            "state_retry_limit": NAVIGATION_STATE_EDGE_RETRY_LIMIT,
                            "semantic_retry_limit": NAVIGATION_SEMANTIC_EDGE_RETRY_LIMIT,
                            "frame_similarity": frame_similarity,
                            "stable_frame_similarity": NAVIGATION_STABLE_FRAME_SIMILARITY,
                        },
                        frame_data_url=after_frame or frame,
                        current_scene_id=current_scene_id,
                        current_score=landing_score or score,
                        candidate_scene_ids=[current_scene_id, target_scene_id],
                    )
                if semantic_attempts < NAVIGATION_SEMANTIC_EDGE_RETRY_LIMIT:
                    self._log(
                        "warning",
                        f"场景移动：点击 {shape_title} 后仍在 #{current_scene_id}，"
                        "扩大随机扰动半径后再尝试一次",
                    )
                    yield from self._wait_action_settle(ctx, stop_event, seconds=1.0)
                    continue
                # Failure exclusion must survive the landing counter mutation
                # performed by ``_record_scene_jump_landing``.  Otherwise an
                # action that stays on the same scene is immediately treated
                # as a new edge and can be clicked until the 24-step ceiling.
                failed_edge_keys.add(semantic_scene_edge_key)
                if semantic_attempts >= NAVIGATION_SEMANTIC_EDGE_RETRY_LIMIT:
                    globally_failed_edge_keys.add(semantic_scene_edge_key)
                self._log(
                    "warning",
                    f"场景移动：点击 {shape_title} 后仍在 #{current_scene_id}，"
                    + (
                        f"跨动态画面累计无效 {semantic_attempts} 次，全局排除该候选并重新规划"
                        if semantic_attempts >= NAVIGATION_SEMANTIC_EDGE_RETRY_LIMIT
                        else f"本轮排除该候选并重新选择通往 #{target_scene_id} 的下一步"
                    ),
                )
                yield BehaviorTreeStatus.RUNNING
                continue
            if after_frame:
                landing_state_key = self._navigation_state_key(
                    after_frame, actual_scene_id, navigation_states,
                )
                cycle = cycle_tracker.observe(
                    navigation_state_key,
                    self._scene_jump_edge_semantic_key(edge),
                    landing_state_key,
                )
                if cycle is not None:
                    cycle_origin_action, cycle_count = cycle
                    if cycle_count >= 2:
                        globally_failed_edge_keys.add(cycle_origin_action)
                        self._log(
                            "warning",
                            f"场景移动：画面状态回到此前节点 {cycle_count} 次，"
                            "排除闭环起点动作并重新规划",
                        )
            # A successful click can still be useless for this destination:
            # e.g. #34「日常」-> #69 followed by #69「退出」-> #34.
            # Exclude a semantic action after three identical landings in one
            # navigation attempt.  Its history counters change on every click,
            # so the full edge key cannot identify this loop reliably.
            transition_key = (
                int(current_scene_id),
                *self._scene_jump_edge_semantic_key(edge),
                int(actual_scene_id),
            )
            repeated_landings[transition_key] = repeated_landings.get(transition_key, 0) + 1
            if repeated_landings[transition_key] >= 3:
                globally_failed_edge_keys.add(self._scene_jump_edge_semantic_key(edge))
                self._log(
                    "warning",
                    f"场景移动：#{current_scene_id} 点击「{shape_title}」连续"
                    f" {repeated_landings[transition_key]} 次落到 #{actual_scene_id}，"
                    f"仍未到达 #{target_scene_id}；本次导航排除该动作",
                )
            self._log("detail", f"场景移动：实际到达 #{actual_scene_id}，重新规划到 #{target_scene_id}")
            yield from self._wait_action_settle(ctx, stop_event, seconds=1.5)

        incident_recorder.trigger(
            trigger_type="replan_step_limit",
            trigger_label="达到最大重规划步数仍未到达目标",
            threshold={"max_replan_steps": NAVIGATION_MAX_REPLAN_STEPS},
            frame_data_url=last_navigation_frame,
            current_scene_id=last_navigation_scene_id,
            current_score=last_navigation_score,
            candidate_scene_ids=[
                scene_id
                for scene_id in (last_navigation_scene_id, target_scene_id)
                if scene_id is not None
            ],
        )
        incident_recorder.finalize(
            status="unrecovered",
            final_scene_id=last_navigation_scene_id,
            final_score=last_navigation_score,
            final_frame=last_navigation_frame,
            message="达到最大重规划步数",
        )
        ctx.pop("_navigation_incident_recorder", None)
        raise RuntimeError(f"场景移动超过最大重规划步数，未到达 #{target_scene_id}")
