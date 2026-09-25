from __future__ import annotations

import difflib
import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, Callable
from pathlib import Path
from types import GeneratorType

from backend.core.fanxiu.mail.policy import (
    fanxiu_mail_reward_is_always_claim,
    fanxiu_mail_reward_name_known,
    fanxiu_mail_rewards_from_payload,
    fanxiu_mail_rewards_unresolved,
)
from backend.core.fanxiu.mail.runtime_store import (
    current_runtime_mail_sequence_snapshot,
)
from backend.core.fanxiu.runtime_gui.mail import (
    align_mail_window,
    best_mail_time_relation,
    build_mail_visual_observations,
    mail_runtime_time_key,
    mail_time_is_match,
    mail_window_geometry_from_asset,
)
from backend.core.fanxiu.runtime_gui import ocr_name_similarity
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.mail.runtime_store import mail_database_engine as _db_engine
from backend.core.fanxiu.data_annotation.unknown_recovery import build_unknown_evidence
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.tasks.world_menu_navigation import (
    open_world_menu_function,
)
from pyxllib.autogui import Shape, View
from pyxllib.prog import BehaviorTreeStatus


@dataclass
class _VisibleMailRow:
    raw: dict[str, Any]
    title_shape: Shape

    @property
    def title(self) -> str:
        return str(self.raw.get("title") or "")

    @property
    def time_text(self) -> str:
        return str(self.raw.get("time_text") or "")

    @property
    def status(self) -> str:
        return str(self.raw.get("status") or "无")



@dataclass(frozen=True)
class _RuntimeMailActionOutcome:
    policy: str
    wait_result: str
    visual_confirmed: bool


class _MailPolicyClassificationError(RuntimeError):
    def __init__(self, message: str, *, unknown_items: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.unknown_items = list(unknown_items or [])


class _MailWindowAmbiguous(RuntimeError):
    """Identical title/time rows cannot establish a unique mail identity."""


class MailTaskMixin:
    # 邮件详情偶尔会在服务器结算或连续翻页后延迟二十余秒才稳定为
    # #122/#123。12 秒会把仍在加载的真实详情误判成 unknown。
    _MAIL_DETAIL_READY_TIMEOUT_SECONDS = 30.0
    _MAIL_RUNTIME_READ_ATTEMPTS = 3

    def _execute_mail_selective_claim_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
        *,
        protected_claim_authorizer: Callable[[dict[str, Any]], bool] | None = None,
        cleanup_after_claim: bool = True,
    ) -> str:
        from backend.core.fanxiu.mail.store import ensure_fanxiu_mail_table

        ensure_fanxiu_mail_table()
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少邮件_选择性领取资产树路径，无法执行邮件作业")
        if not self._refresh_runtime_mail_snapshot("任务开始", force_refresh=True):
            raise RuntimeError("邮件_选择性领取：动态邮件模型不可用，拒绝基于过期记录处理")
        initial_snapshot = current_runtime_mail_sequence_snapshot(_db_engine)
        if not initial_snapshot.get("complete"):
            raise RuntimeError("邮件_选择性领取：任务开始未得到完整 Runtime 邮件序列")
        self._validate_mail_policy_with_unknown_assistance(initial_snapshot, reason="任务开始")
        # 上限只负责防失控，不能承担“到底”判断。200 封邮件叠加半页滚动时仍可能
        # 超过 80 次，因此保留更宽的工程保险；正常流程应由重复邮件行主动收尾。
        max_scrolls = max(1, int(payload.get("max_scrolls") or 150))
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)

        with self._lock:
            self._set_status_locked("running", "邮件_选择性领取：进入邮件 #121", phase="mail_selective_claim_go_mail")
        mail_scene = yield from self._open_mail_selective_claim_entry(context)
        scene_id = getattr(mail_scene, "scene_id", getattr(mail_scene, "id", None))
        if scene_id != 121:
            raise RuntimeError(f"邮件_选择性领取：公共菜单导航未落到邮件 #121，当前 #{scene_id or 'unknown'}")
        with self._lock:
            self._status.update({"current_scene": 121, "updated_at": time.time()})
            self._log_locked("info", "邮件_选择性领取：已由 Runtime-GUI 菜单导航进入邮件 #121")
        image121 = ctx.get("images", {}).get(121)
        if not isinstance(image121, dict):
            raise RuntimeError("缺少 #121 邮件帧标注，无法清理邮件")
        view121 = View(image121)
        list_shape = view121.get_shape("邮件清单2")
        if list_shape is None:
            raise RuntimeError("缺少 #121「邮件清单2」标注，无法遍历邮件清单")

        claimed_visible_count = sum(
            1
            for item in initial_snapshot.get("items") or []
            if bool(item.get("present_in_runtime"))
            and not bool(item.get("locked"))
            and str(item.get("execution_status") or "") == "claimed"
        )
        if cleanup_after_claim and claimed_visible_count >= 20:
            self._log(
                "info",
                "邮件_选择性领取：跨 Cell 新批次检测到 "
                f"{claimed_visible_count} 封已领取邮件；先一键删除缩短列表，再重读 Runtime",
            )
            checkpoint_cleanup = yield from self._delete_read_mail_until_clean(
                context,
                view121,
                stop_event,
                reason=f"新批次已有 {claimed_visible_count} 封已领取邮件",
                initial_snapshot=initial_snapshot,
            )
            initial_snapshot = checkpoint_cleanup["snapshot"]

        geometry = mail_window_geometry_from_asset(image121)
        ordered_result = yield from self._execute_ordered_runtime_claim_batch(
            ctx,
            stop_event,
            context=context,
            image121=image121,
            view121=view121,
            list_shape=list_shape,
            geometry=geometry,
            snapshot=initial_snapshot,
            target_mail_ids={
                str(value)
                for value in payload.get("target_mail_ids") or []
                if str(value)
            },
            protected_claim_authorizer=protected_claim_authorizer,
            cleanup_after_claim=cleanup_after_claim,
            max_scrolls=max_scrolls,
        )
        target_count = len(
            self._select_precise_mail_claim_targets(
                initial_snapshot,
                {
                    str(value)
                    for value in payload.get("target_mail_ids") or []
                    if str(value)
                },
                protected_claim_authorizer=protected_claim_authorizer,
            )
        )
        self._validate_precise_mail_terminal_result(
            ordered_result,
            target_count=target_count,
            require_garbage_cleanup=cleanup_after_claim,
        )
        message = (
            "邮件_选择性领取：完整闭环，"
            f"领取 {int(ordered_result.get('claimed_count') or 0)} 封，"
            f"删除前 {int(ordered_result.get('garbage_before') or 0)} 封，"
            f"删除 {int(ordered_result.get('deleted_count') or 0)} 封，"
            f"剩余垃圾 {int(ordered_result.get('garbage_after') or 0)} 封，"
            f"保留 {int(ordered_result.get('protected_count') or 0)} 封，"
            f"批次目标 {target_count} 封"
        )
        self._mail_selective_claim_terminal_message = message
        with self._lock:
            self._set_status_locked(
                "running",
                message,
                phase="mail_selective_claim_done",
                current_scene=34,
            )
            self._log_locked("success", message)
        return "success"

    @staticmethod
    def _precise_mail_claim_targets(
        snapshot: dict[str, Any],
        *,
        protected_claim_authorizer: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        items = snapshot.get("items")
        if not isinstance(items, list):
            return []
        return [
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("execution_status") or "") == "unclaimed"
            and bool(item.get("present_in_runtime"))
            and not bool(item.get("locked"))
            and (
                str(item.get("action_policy") or "") == "claim"
                or (
                    protected_claim_authorizer is not None
                    and bool(protected_claim_authorizer(item))
                )
            )
        ]

    @staticmethod
    def _runtime_mail_identity(item: dict[str, Any]) -> str:
        return str(item.get("id") or item.get("mail_id") or "").strip()

    @classmethod
    def _deletable_runtime_mail_garbage(
        cls,
        snapshot: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Return the exact unlocked Runtime mails covered by one-key delete."""

        items = snapshot.get("items")
        if not isinstance(items, list):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            mail_id = cls._runtime_mail_identity(item)
            if (
                mail_id
                and bool(item.get("present_in_runtime"))
                and not bool(item.get("locked"))
                and (
                    str(item.get("execution_status") or "") == "claimed"
                    or (
                        str(item.get("execution_status") or "") == "no_attachment"
                        and cls._runtime_mail_read_state(item) is True
                    )
                )
            ):
                result[mail_id] = item
        return result

    @staticmethod
    def _runtime_mail_read_state(item: dict[str, Any]) -> bool | None:
        """Return Runtime's authoritative read flag when projection preserved it."""

        direct = item.get("read")
        if isinstance(direct, bool):
            return direct
        payload = item.get("payload")
        runtime_payload = payload.get("runtime") if isinstance(payload, dict) else None
        nested = runtime_payload.get("read") if isinstance(runtime_payload, dict) else None
        return nested if isinstance(nested, bool) else None

    @classmethod
    def _protected_runtime_mail_ids(cls, snapshot: dict[str, Any]) -> set[str]:
        """Return locked or policy-retained mails that deletion must preserve."""

        return {
            cls._runtime_mail_identity(item)
            for item in snapshot.get("items") or []
            if isinstance(item, dict)
            and bool(item.get("present_in_runtime"))
            and cls._runtime_mail_identity(item)
            and (
                bool(item.get("locked"))
                or (
                    bool(item.get("has_attachment"))
                    and str(item.get("execution_status") or "") == "unclaimed"
                )
            )
        }

    @staticmethod
    def _validate_precise_mail_policy_snapshot(
        snapshot: dict[str, Any],
        *,
        reason: str,
        require_all_classified: bool = False,
    ) -> None:
        """Fail closed unless every live attachment has an explicit safe policy."""

        failures: list[str] = []
        unknown_items: list[dict[str, Any]] = []
        for item in snapshot.get("items") or []:
            if not isinstance(item, dict) or not bool(item.get("present_in_runtime")):
                continue
            if str(item.get("execution_status") or "") != "unclaimed" or not bool(
                item.get("has_attachment")
            ):
                continue
            mail_id = str(item.get("id") or item.get("mail_id") or "?")
            desired = str(item.get("desired_status") or "").strip()
            policy = str(item.get("action_policy") or "").strip()
            locked = bool(item.get("locked"))
            # Reward-name completeness is an authority requirement for a
            # positive claim action, not for an explicit no-op.  A retained
            # or locked mail is already fail-closed: this task will not click
            # it, so a newly introduced item id must not block unrelated,
            # fully classified claim targets forever.
            if locked:
                if desired != "锁定" or policy:
                    failures.append(f"{mail_id}:锁定邮件策略不一致")
                continue
            payload = item.get("payload")
            rewards = fanxiu_mail_rewards_from_payload(payload)
            if fanxiu_mail_rewards_unresolved(payload) or not rewards:
                failures.append(f"{mail_id}:奖励未解析")
                continue
            has_always_claim_reward = any(
                fanxiu_mail_reward_is_always_claim(reward)
                for reward in rewards
            )
            unresolved = [
                reward
                for reward in rewards
                if not fanxiu_mail_reward_name_known(reward)
            ]
            if unresolved and not has_always_claim_reward:
                item_ids = [str(reward.get("item_id") or "?") for reward in unresolved]
                unknown_items.extend(
                    {
                        "mail_id": mail_id,
                        "item_id": str(reward.get("item_id") or ""),
                        "reward_type": reward.get("type"),
                        "item_type": str(reward.get("item_type") or ""),
                        "item_type_id": reward.get("item_type_id"),
                        "item_sub_type_id": reward.get("item_sub_type_id"),
                        "runtime_name_id": reward.get("runtime_name_id"),
                        "icon": str(reward.get("icon") or ""),
                        "use_condition": str(reward.get("use_condition") or ""),
                        "name_source": str(reward.get("name_source") or ""),
                        "policy_resolution": str(reward.get("policy_resolution") or ""),
                    }
                    for reward in unresolved
                )
                # At task start an unknown retained mail must not prevent
                # unrelated, fully classified claim targets from running.  At
                # terminal verification it is not a completed business state:
                # keep the job due and escalate the exact evidence.
                if require_all_classified or desired not in {"锁定", "留存"} or policy:
                    failures.append(f"{mail_id}:存在未知道具 {item_ids}")
                    continue
            if desired in {"锁定", "留存"} and not policy:
                continue
            if desired == "可领" and policy == "claim":
                continue
            failures.append(f"{mail_id}:desired={desired or '-'} policy={policy or '-'}")
        if failures:
            raise _MailPolicyClassificationError(
                f"邮件_选择性领取：{reason}存在 {len(failures)} 封未完成安全分类的附件邮件，"
                f"拒绝领取并拒绝顺延到次日；details={failures[:8]}",
                unknown_items=unknown_items,
            )

    def _validate_mail_policy_with_unknown_assistance(
        self,
        snapshot: dict[str, Any],
        *,
        reason: str,
        require_all_classified: bool = False,
    ) -> None:
        try:
            self._validate_precise_mail_policy_snapshot(
                snapshot,
                reason=reason,
                require_all_classified=require_all_classified,
            )
        except _MailPolicyClassificationError as exc:
            if exc.unknown_items:
                from backend.core.fanxiu.client.unknown_item_assistance import (
                    enqueue_fanxiu_unknown_item_assistance,
                )

                assistance = enqueue_fanxiu_unknown_item_assistance(
                    exc.unknown_items,
                    db_bind=_db_engine(),
                )
                self._log(
                    "error",
                    "邮件_选择性领取：真正未知道具已提交 Codex CLI 工程协助，"
                    f"signature={assistance.get('signature')} task={assistance.get('task_id')} "
                    f"queued={assistance.get('queued')}",
                )
            raise

    @staticmethod
    def _validate_precise_mail_terminal_result(
        result: dict[str, Any],
        *,
        target_count: int,
        require_garbage_cleanup: bool = True,
    ) -> None:
        """Validate the count contract before exposing a successful summary."""

        if str(result.get("result") or "") != "success":
            raise RuntimeError("邮件_选择性领取：批次没有形成 success 业务终态")
        claimed_count = int(result.get("claimed_count") or 0)
        garbage_before = int(result.get("garbage_before") or 0)
        garbage_after = int(result.get("garbage_after") or 0)
        deleted_count = int(result.get("deleted_count") or 0)
        protected_count = int(result.get("protected_count") or 0)
        if claimed_count != int(target_count):
            raise RuntimeError(
                "邮件_选择性领取：批次仍有待领取目标或领取计数不一致，"
                f"target={target_count} claimed={claimed_count}"
            )
        if require_garbage_cleanup and (
            garbage_after != 0 or deleted_count != garbage_before
        ):
            raise RuntimeError(
                "邮件_选择性领取：可删除垃圾未形成归零闭环，"
                f"before={garbage_before} deleted={deleted_count} after={garbage_after}"
            )
        if min(claimed_count, garbage_before, garbage_after, deleted_count, protected_count) < 0:
            raise RuntimeError("邮件_选择性领取：业务终态计数非法，拒绝报告成功")

    @classmethod
    def _select_precise_mail_claim_targets(
        cls,
        snapshot: dict[str, Any],
        target_mail_ids: set[str] | None,
        *,
        protected_claim_authorizer: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        targets = cls._precise_mail_claim_targets(
            snapshot,
            protected_claim_authorizer=protected_claim_authorizer,
        )
        wanted = {str(value) for value in target_mail_ids or set() if str(value)}
        if not wanted:
            return targets
        return [
            item
            for item in targets
            if str(item.get("id") or item.get("mail_id") or "") in wanted
        ]

    @classmethod
    def _runtime_mail_target_still_requires_claim(
        cls,
        snapshot: dict[str, Any],
        mail_id: str,
        *,
        protected_claim_authorizer: Callable[[dict[str, Any]], bool] | None = None,
    ) -> bool:
        """Return whether one exact Runtime identity still needs a claim.

        A claim request is irreversible and the detail sheet may already have
        switched from #122 (claim) to #123 (delete) before the batch's stable
        snapshot is refreshed.  Only a new complete MailMgr read may classify
        that case as already completed; title similarity is insufficient when
        several adjacent mails look identical.
        """

        target_id = str(mail_id or "")
        return any(
            str(item.get("id") or item.get("mail_id") or "") == target_id
            for item in cls._precise_mail_claim_targets(
                snapshot,
                protected_claim_authorizer=protected_claim_authorizer,
            )
        )

    @staticmethod
    def _first_screen_runtime_mapping(
        snapshot: dict[str, Any],
        image121: dict[str, Any],
        fragments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Infer Runtime #0 exclusively from the ordered OCR rows 2/3/4."""

        items = snapshot.get("items")
        if (
            not snapshot.get("complete")
            or not isinstance(items, list)
            or int(snapshot.get("decoded_count") or -1) != len(items)
            or len(items) < 4
        ):
            raise RuntimeError("邮件_选择性领取：首屏领取缺少完整 Runtime #0..#3 序列")
        geometry = mail_window_geometry_from_asset(image121)
        observations = build_mail_visual_observations(
            fragments,
            geometry,
            visible_slots=(1, 2, 3),
        )
        by_slot = {item.slot_index: item for item in observations}
        evidence: list[dict[str, Any]] = []
        known_title_anchor_count = 0
        for slot in (1, 2, 3):
            observation = by_slot.get(slot)
            runtime_item = items[slot]
            if observation is None:
                raise RuntimeError(
                    f"邮件_选择性领取：首屏第 {slot + 1} 行 OCR 缺失，不能反推第1行"
                )
            expected_title = str(runtime_item.get("title") or "")
            title_score = max(
                (
                    ocr_name_similarity(expected_title, candidate)
                    for candidate in observation.title_candidates
                ),
                default=0.0,
            )
            expected_time = mail_runtime_time_key(runtime_item)
            time_matched = mail_time_is_match(
                best_mail_time_relation(observation.time_candidates, expected_time)
            )
            runtime_title_unknown = expected_title.startswith("未知邮件类型")
            if not runtime_title_unknown and title_score >= 0.68:
                known_title_anchor_count += 1
            if (title_score < 0.68 and not runtime_title_unknown) or not time_matched:
                raise RuntimeError(
                    f"邮件_选择性领取：首屏第 {slot + 1} 行未按序匹配 Runtime #{slot}；"
                    f"title_score={title_score:.2f} time_matched={time_matched} "
                    f"ocr_titles={list(observation.title_candidates)} "
                    f"ocr_times={list(observation.time_candidates)} "
                    f"runtime_title={expected_title} runtime_time={expected_time}"
                )
            evidence.append(
                {
                    "slot_index": slot,
                    "runtime_index": slot,
                    "title_score": round(title_score, 4),
                    "time_matched": True,
                    "runtime_title_unknown": runtime_title_unknown,
                }
            )
        if known_title_anchor_count < 1:
            raise RuntimeError(
                "邮件_选择性领取：首屏第2/3/4行仅有未知 Runtime 标题与时间证据，"
                "缺少至少一条真实标题锚点，不能反推第1行"
            )
        first = items[0]
        return {
            "slot_index": 0,
            "runtime_index": int(first.get("runtime_index") or 0),
            "mail_id": str(first.get("id") or first.get("mail_id") or ""),
            "title": str(first.get("title") or ""),
            "create_time_text": str(first.get("create_time_text") or ""),
            "evidence": evidence,
        }

    @staticmethod
    def _ordered_runtime_window_mapping(
        snapshot: dict[str, Any],
        image121: dict[str, Any],
        fragments: list[dict[str, Any]],
        *,
        previous_offset: int,
        known_top: bool,
    ) -> dict[str, Any]:
        """Map one freshly OCRed GUI window to the ordered Runtime sequence."""

        items = list(snapshot.get("items") or [])
        geometry = mail_window_geometry_from_asset(image121)
        # The footer overlays the fifth lattice row on the real #121 screen.
        # Only rows 1..4 (slots 0..3) are fully actionable; a partially visible
        # fifth title must trigger a downward scroll, never a click.
        visible_slots = [slot for slot in geometry.visible_slot_indices() if int(slot) <= 3]
        observations = []
        if known_top:
            try:
                MailTaskMixin._first_screen_runtime_mapping(snapshot, image121, fragments)
                anchor_count = 3
            except RuntimeError:
                # Some client mail types render completely blank rows. Require
                # two independent visible anchors instead of inventing their
                # missing titles/times. Row 0 is usable only when its exact,
                # globally unique title and time both match current MailMgr.
                if not snapshot.get("complete") or snapshot.get("decoded_count") != len(items):
                    raise
                observations = build_mail_visual_observations(
                    fragments, geometry, visible_slots=visible_slots,
                )
                if items:
                    title = str(items[0].get("title") or "")
                    stamp = mail_runtime_time_key(items[0])
                    unique_title = bool(title) and sum(str(x.get("title") or "") == title for x in items) == 1
                    observations = [
                        replace(o, trusted=True, reliability=1.0)
                        if o.slot_index == 0 and unique_title
                        and any(ocr_name_similarity(title, candidate) >= 0.99 for candidate in o.title_candidates)
                        and mail_time_is_match(
                            best_mail_time_relation(o.time_candidates, stamp)
                        )
                        else o for o in observations
                    ]
                alignment = align_mail_window(
                    items, observations, visible_slots=visible_slots,
                    min_anchor_count=2, expected_runtime_offset=0,
                )
                if not alignment.aligned or alignment.runtime_offset != 0:
                    raise
                anchor_count = alignment.anchor_count
            offset = 0
        else:
            observations = build_mail_visual_observations(
                fragments,
                geometry,
                visible_slots=visible_slots,
            )
            candidates: list[tuple[int, int, list[dict[str, Any]]]] = []
            # Returning from a claimed detail can restore the list at the
            # previous pre-scroll offset, or one row above it after the list
            # settles.  A strictly increasing lower bound then rejects a fully
            # aligned window and aborts the batch.  Accept that one-row rebound;
            # the caller only scrolls again when every remaining target is
            # beyond this verified visible window.
            for offset_candidate in range(max(0, int(previous_offset) - 1), len(items)):
                evidence: list[dict[str, Any]] = []
                for observation in observations:
                    runtime_index = offset_candidate + int(observation.slot_index)
                    if not 0 <= runtime_index < len(items):
                        continue
                    runtime_item = items[runtime_index]
                    expected_title = str(runtime_item.get("title") or "")
                    title_score = max(
                        (
                            ocr_name_similarity(expected_title, candidate)
                            for candidate in observation.title_candidates
                        ),
                        default=0.0,
                    )
                    expected_time = mail_runtime_time_key(runtime_item)
                    time_matched = mail_time_is_match(
                        best_mail_time_relation(
                            observation.time_candidates, expected_time
                        )
                    )
                    runtime_title_unknown = expected_title.startswith("未知邮件类型")
                    if time_matched and (title_score >= 0.68 or runtime_title_unknown):
                        evidence.append(
                            {
                                "slot_index": int(observation.slot_index),
                                "runtime_index": runtime_index,
                                "title_score": round(title_score, 4),
                                "runtime_title_unknown": runtime_title_unknown,
                            }
                        )
                if (
                    len(evidence) >= 2
                    and any(float(item["title_score"]) >= 0.68 for item in evidence)
                ):
                    candidates.append((len(evidence), offset_candidate, evidence))
            if candidates:
                strongest = max(item[0] for item in candidates)
                strongest_candidates = [item for item in candidates if item[0] == strongest]
                if len(strongest_candidates) != 1:
                    raise _MailWindowAmbiguous(
                        "邮件_选择性领取：同名同时间窗口存在多个等强序列位置，禁止猜测最小偏移；"
                        f"offsets={[item[1] for item in strongest_candidates]}"
                    )
                _count, offset, _evidence = strongest_candidates[0]
                anchor_count = strongest
            else:
                # A controlled scroll gives us a continuity boundary.  OCR can
                # occasionally return only one complete row after the inertial
                # list settles; requiring two rows then rejects an otherwise
                # exact, unique title/time anchor.  Reuse the shared alignment
                # law, which accepts one anchor only when it is exact and the
                # competing offsets are unambiguous.  Repeated identical rows
                # therefore remain fail-closed.
                exact_alignment = align_mail_window(
                    items,
                    observations,
                    visible_slots=visible_slots,
                    min_anchor_count=1,
                    # One exact unique title separates the best offset by
                    # 0.68 when every neighbouring midnight mail shares the
                    # same minute.  Keep the margin below that exact-title
                    # contribution; duplicated title/time rows still tie at
                    # zero and remain ambiguous.
                    min_score_margin=0.5,
                    expected_runtime_offset=max(0, int(previous_offset)),
                )
                if not exact_alignment.aligned:
                    observed_summary = [
                        {
                            "slot": int(observation.slot_index),
                            "titles": list(observation.title_candidates),
                            "times": list(observation.time_candidates),
                        }
                        for observation in observations
                    ]
                    raise RuntimeError(
                        "邮件_选择性领取：滚动后当前窗口既没有至少两行按序匹配 Runtime，"
                        "也没有唯一精确单行锚点；"
                        f"alignment={exact_alignment.status}:{exact_alignment.reason} "
                        f"observed={observed_summary}"
                    )
                offset = int(exact_alignment.runtime_offset or 0)
                anchor_count = int(exact_alignment.anchor_count)
        mappings = []
        for slot in visible_slots:
            runtime_index = offset + int(slot)
            if not 0 <= runtime_index < len(items):
                continue
            item = items[runtime_index]
            observation = next(
                (
                    candidate
                    for candidate in observations
                    if int(candidate.slot_index) == int(slot)
                ),
                None,
            )
            observed_title = ""
            if observation is not None and observation.title_candidates:
                runtime_title = str(item.get("title") or "")
                if runtime_title.startswith("未知邮件类型"):
                    observed_title = max(
                        observation.title_candidates,
                        key=lambda candidate: len(re.sub(r"\s+", "", candidate)),
                    )
                else:
                    observed_title = max(
                        observation.title_candidates,
                        key=lambda candidate: ocr_name_similarity(runtime_title, candidate),
                    )
            mappings.append(
                {
                    "slot_index": int(slot),
                    "runtime_index": int(item.get("runtime_index") or runtime_index),
                    "mail_id": str(item.get("id") or item.get("mail_id") or ""),
                    "title": str(item.get("title") or ""),
                    "observed_title": observed_title,
                    "create_time_text": str(item.get("create_time_text") or ""),
                }
            )
        return {
            "runtime_offset": offset,
            "anchor_count": anchor_count,
            "mappings": mappings,
        }

    def _execute_ordered_runtime_claim_batch(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        context: BehaviorTreeContext,
        image121: dict[str, Any],
        view121: View,
        list_shape: Shape,
        geometry: Any,
        snapshot: dict[str, Any],
        target_mail_ids: set[str] | None = None,
        protected_claim_authorizer: Callable[[dict[str, Any]], bool] | None = None,
        cleanup_after_claim: bool = True,
        max_scrolls: int = 150,
    ):
        """Claim the one Runtime target batch, then delete once and verify once."""

        targets = self._select_precise_mail_claim_targets(
            snapshot,
            target_mail_ids,
            protected_claim_authorizer=protected_claim_authorizer,
        )
        target_by_id = {
            str(item.get("id") or item.get("mail_id") or ""): item
            for item in targets
        }
        claimed_ids: set[str] = set()
        open_attempts: dict[str, int] = {}
        previous_offset = -1
        known_top = True
        scroll_calls = 0
        wrong_detail_recoveries: dict[str, int] = {}
        ambiguity_deleted = 0
        ambiguity_delete_batches = 0
        while len(claimed_ids) < len(target_by_id):
            self._raise_if_stopped(stop_event)
            window: dict[str, Any] | None = None
            last_mapping_error: RuntimeError | None = None
            for ocr_attempt in range(4):
                frame = context.cur_frame(update=True)
                fragments = context.ocr_fragments_in_shapes(
                    image121,
                    ("第1封", "邮件清单2"),
                    padding=0,
                    frame_data_url=frame,
                )
                try:
                    window = self._ordered_runtime_window_mapping(
                        snapshot,
                        image121,
                        fragments,
                        previous_offset=previous_offset,
                        known_top=known_top,
                    )
                    break
                except _MailWindowAmbiguous as exc:
                    last_mapping_error = exc
                    break
                except RuntimeError as exc:
                    last_mapping_error = exc
                    if ocr_attempt >= 3:
                        raise
                    self._log(
                        "info",
                        f"邮件_选择性领取：{'首屏第2/3/4行' if known_top else '滚动稳定窗口'}"
                        f"第 {ocr_attempt + 1}/4 帧暂未齐全；原地等待重取，禁止 scroll/drag",
                    )
                    yield from context.wait_action_settle(0.6 if known_top else 1.0)
                    context.clear_frame()
            if window is None:
                if (
                    isinstance(last_mapping_error, _MailWindowAmbiguous)
                    and cleanup_after_claim
                    and self._deletable_runtime_mail_garbage(snapshot)
                ):
                    # Remove only already-proven garbage through the normal
                    # protected-mail deletion contract, then establish a new
                    # top-of-list anchor. Never resolve a tie by clicking.
                    self._log("info", f"{last_mapping_error}；清理已领邮件后重新从顶部定位")
                    cleanup = yield from self._delete_read_mail_until_clean(
                        context, view121, stop_event,
                        reason="同名窗口歧义清理已领邮件",
                    )
                    ambiguity_deleted += int(cleanup["deleted_count"])
                    ambiguity_delete_batches += int(cleanup["batch_count"])
                    snapshot = cleanup["snapshot"]
                    yield from self._leave_mail_scene_to_world(
                        ctx, stop_event, context, 121, label="邮件_选择性领取",
                    )
                    yield from self._open_mail_selective_claim_entry(context)
                    previous_offset = -1
                    known_top = True
                    context.clear_frame()
                    continue
                raise last_mapping_error or RuntimeError("邮件_选择性领取：首屏映射失败")
            mappings = list(window["mappings"])
            visible_targets = [
                mapping
                for mapping in mappings
                if str(mapping.get("mail_id") or "") in target_by_id
                and str(mapping.get("mail_id") or "") not in claimed_ids
            ]
            if visible_targets:
                mapping = min(visible_targets, key=lambda item: int(item.get("slot_index") or 0))
                slot_index = int(mapping.get("slot_index") or 0)
                click_x, click_y = self._precise_mail_click_point(
                    image121,
                    list_shape,
                    geometry,
                    slot_index=slot_index,
                )
                title = str(mapping.get("title") or "")
                observed_title = str(mapping.get("observed_title") or "")
                repeated_visible_signature = sum(
                    1
                    for visible_mapping in mappings
                    if str(visible_mapping.get("title") or "") == title
                    and str(visible_mapping.get("create_time_text") or "")
                    == str(mapping.get("create_time_text") or "")
                ) > 1
                if not known_top:
                    geometry_title = (
                        observed_title
                        if title.startswith("未知邮件类型") and observed_title
                        else title
                    )
                    observed_point = self._precise_mail_observed_title_point(
                        fragments,
                        title=geometry_title,
                        fallback_y=click_y,
                        geometry=geometry,
                        # A drag may stop well between two asset lattice rows.
                        # Runtime already selected the row; OCR only refines
                        # its position on this stable new frame.
                        # A wide correction is useful for a uniquely named row
                        # after an inertial drag.  It is unsafe for adjacent
                        # identical mails: the neighbouring title can be closer
                        # than the intended row and opens an already-claimed
                        # #123 detail.  Keep repeated groups inside half a row.
                        max_row_distance_ratio=(
                            0.48 if repeated_visible_signature else 0.8
                        ),
                    )
                    if observed_point is not None:
                        click_x, click_y = observed_point
                        self._log(
                            "detail",
                            f"邮件_选择性领取：滚动稳定帧重新定位 Runtime "
                            f"#{mapping.get('runtime_index')}「{title}」"
                            f"（画面标题「{geometry_title}」）到 "
                            f"({click_x:.0f},{click_y:.0f})",
                        )
                row_shape = self._mail_row_title_shape(
                    view121,
                    {
                        "title": title,
                        "time_text": str(mapping.get("create_time_text") or ""),
                        "x": click_x,
                        "y": click_y,
                    },
                )
                if row_shape is None:
                    raise RuntimeError(f"邮件_选择性领取：无法构造 Runtime #{mapping.get('runtime_index')} 点击区域")
                self._log(
                    "action",
                    f"邮件_选择性领取：当前窗口 Runtime #{mapping.get('runtime_index')}「{title}」可见；"
                    "立即打开领取，禁止先滚动",
                )
                outcome = yield from self._claim_runtime_mail_row(
                    context,
                    _VisibleMailRow(
                        {
                            "title": title,
                            "time_text": str(mapping.get("create_time_text") or ""),
                            "mail_key": str(mapping.get("mail_id") or ""),
                        },
                        row_shape,
                    ),
                    delete_after_reward=False,
                    require_claim=True,
                )
                if outcome.wait_result == "claim_action_absent":
                    mail_id = str(mapping.get("mail_id") or "")
                    refreshed = self._read_complete_precise_mail_snapshot(
                        stop_event,
                        reason=f"#{mapping.get('runtime_index')} 详情无领取动作后只读复验",
                    )
                    if not self._runtime_mail_target_still_requires_claim(
                        refreshed,
                        mail_id,
                        protected_claim_authorizer=protected_claim_authorizer,
                    ):
                        snapshot = refreshed
                        claimed_ids.add(mail_id)
                        self._log(
                            "success",
                            f"邮件_选择性领取：Runtime #{mapping.get('runtime_index')}「{title}」"
                            "详情已无领取动作，刷新 MailMgr 确认该精确邮件已结算；"
                            "按幂等完成继续，不重复点击",
                        )
                        continue
                    wrong_detail_recoveries[mail_id] = (
                        wrong_detail_recoveries.get(mail_id, 0) + 1
                    )
                    if wrong_detail_recoveries[mail_id] >= 2:
                        raise RuntimeError(
                            f"邮件_选择性领取：Runtime #{mapping.get('runtime_index')}「{title}」"
                            "连续打开无领取动作的相邻详情，但只读 MailMgr 仍判该精确邮件可领"
                        )
                    self._log(
                        "warning",
                        f"邮件_选择性领取：Runtime #{mapping.get('runtime_index')}「{title}」"
                        "详情无领取动作，且只读 MailMgr 仍判目标可领；判定本次落到相邻已领行，"
                        "返回世界并从邮件顶部重建一次窗口，不执行删除或领取",
                    )
                    yield from self._leave_mail_scene_to_world(
                        ctx,
                        stop_event,
                        context,
                        121,
                        label="邮件_选择性领取",
                    )
                    yield from self._open_mail_selective_claim_entry(context)
                    snapshot = refreshed
                    previous_offset = -1
                    known_top = True
                    context.clear_frame()
                    continue
                if not outcome.visual_confirmed:
                    mail_id = str(mapping.get("mail_id") or "")
                    open_attempts[mail_id] = open_attempts.get(mail_id, 0) + 1
                    if (
                        outcome.wait_result == "detail_not_found"
                        and open_attempts[mail_id] < 2
                    ):
                        self._log(
                            "warning",
                            f"邮件_选择性领取：Runtime #{mapping.get('runtime_index')}「{title}」"
                            "首次点击未打开详情；丢弃旧坐标，原地重新 OCR/定位后重试一次",
                        )
                        yield from context.wait_action_settle(1.5)
                        context.clear_frame()
                        continue
                    raise RuntimeError(
                        f"邮件_选择性领取：Runtime #{mapping.get('runtime_index')}「{title}」"
                        f"未完成领取返回闭环；wait_result={outcome.wait_result}"
                    )
                mail_id = str(mapping.get("mail_id") or "")
                refreshed = self._read_complete_precise_mail_snapshot(
                    stop_event,
                    reason=f"#{mapping.get('runtime_index')} 领取后只读复验",
                )
                if self._runtime_mail_target_still_requires_claim(
                    refreshed,
                    mail_id,
                    protected_claim_authorizer=protected_claim_authorizer,
                ):
                    raise RuntimeError(
                        f"邮件_选择性领取：Runtime #{mapping.get('runtime_index')}「{title}」"
                        "画面已返回邮件列表，但 MailMgr 仍判该精确邮件可领"
                    )
                snapshot = refreshed
                claimed_ids.add(mail_id)
                self._log(
                    "success",
                    f"邮件_选择性领取：Runtime #{mapping.get('runtime_index')}「{title}」"
                    "已由画面返回与 MailMgr 状态共同确认领取完成；"
                    f"批次进度 {len(claimed_ids)}/{len(target_by_id)}",
                )
                continue

            # Batch membership is stable by mail ID, but Runtime positions
            # can change after each refreshed snapshot (for example new mail).
            # Compare the visible window against that same snapshot, never
            # against indices captured when the batch was first selected.
            current_indices = {
                str(item.get("id") or item.get("mail_id") or ""):
                    int(item.get("runtime_index") or index)
                for index, item in enumerate(snapshot.get("items") or [])
            }
            remaining_ids = set(target_by_id) - claimed_ids
            missing_ids = remaining_ids - current_indices.keys()
            if missing_ids:
                raise RuntimeError(
                    f"邮件_选择性领取：待领邮件已不在当前 Runtime 快照，需重新建批次：{sorted(missing_ids)}"
                )
            remaining_indices = [current_indices[mail_id] for mail_id in remaining_ids]
            last_visible_index = max(int(item.get("runtime_index") or 0) for item in mappings)
            if min(remaining_indices) <= last_visible_index:
                raise RuntimeError(
                    "邮件_选择性领取：当前窗口存在应领目标却没有生成点击映射，禁止滚动；"
                    f"remaining={remaining_indices} window={[(m.get('slot_index'), m.get('runtime_index')) for m in mappings]}"
                )
            if scroll_calls >= max(1, int(max_scrolls)):
                raise RuntimeError(
                    f"邮件_选择性领取：达到 max_scrolls={max_scrolls}，仍有待领 Runtime 邮件"
                )
            loaded = yield from context.scroll_shape_content(list_shape, settle_seconds=2.0)
            scroll_calls += 1
            if not loaded:
                raise RuntimeError(
                    f"邮件_选择性领取：单向到底仍有 Runtime 目标 {remaining_indices}，拒绝回顶"
                )
            # Content-change detection can fire while the inertial drag is
            # still settling.  Never OCR/click that transition frame.
            yield from context.wait_action_settle(3.0)
            context.clear_frame()
            previous_offset = int(window["runtime_offset"])
            known_top = False

        if cleanup_after_claim:
            cleanup = yield from self._delete_read_mail_until_clean(
                context,
                view121,
                stop_event,
                reason="批量领取完成后统一删除",
            )
            final_snapshot = cleanup["snapshot"]
            cleanup["before_count"] += ambiguity_deleted
            cleanup["deleted_count"] += ambiguity_deleted
            cleanup["batch_count"] += ambiguity_delete_batches
        else:
            final_snapshot = self._read_complete_precise_mail_snapshot(
                stop_event,
                reason="精确领取完成后只读复查",
            )
            garbage_count = len(self._deletable_runtime_mail_garbage(final_snapshot))
            cleanup = {
                "snapshot": final_snapshot,
                "before_count": garbage_count,
                "after_count": garbage_count,
                "deleted_count": 0,
                "batch_count": 0,
                "protected_count": sum(
                    1
                    for item in final_snapshot.get("items") or []
                    if isinstance(item, dict)
                    and bool(item.get("present_in_runtime"))
                    and (
                        bool(item.get("locked"))
                        or str(item.get("action_policy") or "") != "claim"
                    )
                ),
            }
            self._log(
                "success",
                "邮件_选择性领取：专用精确领取已完成；保留邮件列表，不执行通用垃圾清理",
            )
        yield from self._leave_mail_scene_to_world(
            ctx,
            stop_event,
            context,
            121,
            label="邮件_选择性领取",
        )
        self._validate_mail_policy_with_unknown_assistance(
            final_snapshot,
            reason="任务完成复查",
            require_all_classified=True,
        )
        remaining = self._select_precise_mail_claim_targets(
            final_snapshot,
            target_mail_ids,
            protected_claim_authorizer=protected_claim_authorizer,
        )
        if remaining:
            raise RuntimeError(
                "邮件_选择性领取：Runtime 终检仍有本批次待领目标："
                f"{[int(item.get('runtime_index') or 0) for item in remaining]}"
            )
        self._log(
            "success",
            f"邮件_选择性领取完整闭环：领取 {len(claimed_ids)} 封，"
            f"删除 {cleanup['deleted_count']}/{cleanup['before_count']} 封，"
            f"Runtime 终检无本批次待领目标；cleanup_after_claim={cleanup_after_claim}",
        )
        return {
            "result": "success",
            "claimed_count": len(claimed_ids),
            "garbage_before": int(cleanup["before_count"]),
            "garbage_after": int(cleanup["after_count"]),
            "deleted_count": int(cleanup["deleted_count"]),
            "delete_batches": int(cleanup["batch_count"]),
            "protected_count": int(cleanup["protected_count"]),
        }

    def _read_complete_precise_mail_snapshot(
        self,
        stop_event: threading.Event,
        *,
        reason: str,
    ) -> dict[str, Any]:
        last_error = f"邮件_选择性领取：{reason} Runtime 读取失败"
        attempts = max(1, int(self._MAIL_RUNTIME_READ_ATTEMPTS))
        for attempt in range(attempts):
            self._raise_if_stopped(stop_event)
            if self._refresh_runtime_mail_snapshot(reason, force_refresh=True):
                current = current_runtime_mail_sequence_snapshot(_db_engine)
                items = current.get("items")
                if (
                    current.get("complete")
                    and isinstance(items, list)
                    and int(current.get("decoded_count") or -1) == len(items)
                ):
                    return current
                last_error = (
                    f"邮件_选择性领取：{reason}未得到完整 Runtime 邮件序列"
                )
            if attempt + 1 < attempts:
                self._log(
                    "wait",
                    f"邮件_选择性领取：{reason}第 {attempt + 1}/{attempts} 次 Runtime "
                    "快照未物化，保留当前邮件页后重读",
                )
                time.sleep(0.6)
        raise RuntimeError(last_error)

    @staticmethod
    def _precise_mail_click_point(
        image121: dict[str, Any],
        list_shape: Shape,
        geometry: Any,
        *,
        slot_index: int,
    ) -> tuple[float, float]:
        width = float(image121.get("width") or geometry.frame_width)
        raw = list_shape.raw
        click_x = (float(raw.get("x") or 0) + float(raw.get("w") or 0) * 0.43) * width
        click_y = float(geometry.row_center_y(slot_index))
        if int(slot_index) > 0 and geometry.list_bottom > geometry.list_top:
            row_top = click_y - float(geometry.row_half_height)
            row_bottom = click_y + float(geometry.row_half_height)
            edge_tolerance = min(12.0, float(geometry.row_half_height) * 0.2)
            if (
                row_top < geometry.list_top - edge_tolerance
                or row_bottom > geometry.list_bottom + edge_tolerance
            ):
                raise RuntimeError(
                    "邮件_选择性领取：目标行只有中心落入清单，但整行未进入可点击窗口，必须继续滚动"
                )
        return click_x, click_y

    @staticmethod
    def _precise_mail_observed_title_point(
        fragments: list[dict[str, Any]],
        *,
        title: str,
        fallback_y: float,
        geometry: Any,
        max_row_distance_ratio: float = 0.48,
    ) -> tuple[float, float] | None:
        """Use the stable frame's title box when OCR observed the target row.

        MailMgr decides *which* mail is actionable and the global alignment
        decides *which visible row* represents it.  A list may nevertheless
        stop between the asset's integer row lattice after a nudge.  In that
        case the observed OCR title is a more accurate click coordinate than
        the nominal slot centre.  OCR never chooses the business target here;
        it only refines the pixel position of an already aligned MailMgr item.
        """

        # OCR often drops decorative brackets around a mail title.  Compare
        # semantic characters so a scrolled row can still refine its click
        # position instead of falling back to the stale asset row lattice.
        expected = "".join(ch for ch in _sanitize_ocr_text(title) if ch.isalnum())
        if not expected:
            return None
        expected_title_y = float(fallback_y) + float(geometry.title_center_offset)
        # OCR is only allowed to refine the pixel position of the row already
        # selected by the global sequence alignment.  Adjacent mails can have
        # exactly the same title (and even the same minute); when OCR misses the
        # intended row, accepting a same-title fragment from a neighbouring row
        # would silently turn slot N into slot N-1/N+1.
        row_pitch = float(getattr(geometry, "row_pitch", 0.0) or 0.0)
        max_row_distance = (
            row_pitch * max(0.0, float(max_row_distance_ratio))
            if row_pitch > 0
            else None
        )
        candidates: list[tuple[float, float, float]] = []
        for fragment in fragments:
            text = "".join(
                ch for ch in _sanitize_ocr_text(fragment.get("text")) if ch.isalnum()
            )
            if not text:
                continue
            similarity = difflib.SequenceMatcher(None, text, expected).ratio()
            if text != expected and similarity < 0.92:
                continue
            width = float(fragment.get("w") or 0)
            height = float(fragment.get("h") or 0)
            if width <= 0 or height <= 0:
                continue
            cx = float(fragment.get("x") or 0) + width / 2
            cy = float(fragment.get("y") or 0) + height / 2
            if (
                max_row_distance is not None
                and abs(cy - expected_title_y) > max_row_distance
            ):
                continue
            candidates.append((similarity, cx, cy))
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (item[0], -abs(item[2] - expected_title_y)),
            reverse=True,
        )
        _similarity, click_x, click_y = candidates[0]
        return click_x, click_y

    def _finish_mail_selective_claim_schedule(
        self,
        payload: dict[str, Any],
        message: str,
    ) -> str:
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "").strip()
        if not scheduler_task_id:
            return message
        # Respect Scheduler's planned business clock during an early run.
        # datetime.now() would advance a midnight job back to the very same
        # upcoming midnight, leaving a successfully completed job due again.
        now = job_now()
        next_time = (now + timedelta(days=1)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
        return f"{message}，下次 {next_time}"

    def _click_confirmed_mail_delete_prompt(
        self,
        context,
        scene_id: int,
        *,
        frame_data_url: str | None = None,
    ) -> None:
        """点击已经由当前帧或 wait_scene 确认过的删除确认弹窗。"""

        context.click_shape(int(scene_id), "确认", frame_data_url=frame_data_url)

    def _delete_read_mail_until_clean(
        self,
        context: BehaviorTreeContext,
        mail_view: View,
        stop_event: threading.Event,
        *,
        reason: str,
        initial_snapshot: dict[str, Any] | None = None,
        max_batches: int = 8,
    ):
        """Delete eligible mail in bounded batches with fresh Runtime proof."""

        snapshot = initial_snapshot or self._read_complete_precise_mail_snapshot(
            stop_event,
            reason=f"{reason}删除前读取",
        )
        eligible = self._deletable_runtime_mail_garbage(snapshot)
        protected_ids = self._protected_runtime_mail_ids(snapshot)
        before_count = len(eligible)
        deleted_count = 0
        batches = 0
        while eligible:
            self._raise_if_stopped(stop_event)
            if batches >= max(1, int(max_batches)):
                raise RuntimeError(
                    f"邮件_选择性领取：{reason}达到 {max_batches} 批后仍有 "
                    f"{len(eligible)} 封可删除垃圾"
                )
            previous_ids = set(eligible)
            result_scene = yield from self._delete_read_mail_once(
                context,
                mail_view,
                reason=f"{reason}（第 {batches + 1} 批，删除前 {len(previous_ids)} 封）",
            )
            if result_scene == 34:
                yield from self._open_mail_selective_claim_entry(context)
                yield from context.wait_scene(
                    [121],
                    wait=12.0,
                    label="邮件_选择性领取：批量删除后重新进入邮件 #121",
                )
            elif result_scene != 121:
                raise RuntimeError(
                    f"邮件_选择性领取：确认删除后落点异常 #{result_scene or 'unknown'}"
                )
            snapshot = self._read_complete_precise_mail_snapshot(
                stop_event,
                reason=f"{reason}第 {batches + 1} 批删除后强制刷新",
            )
            current_ids = {
                self._runtime_mail_identity(item)
                for item in snapshot.get("items") or []
                if isinstance(item, dict)
                and bool(item.get("present_in_runtime"))
                and self._runtime_mail_identity(item)
            }
            missing_protected = protected_ids - current_ids
            if missing_protected:
                raise RuntimeError(
                    "邮件_选择性领取：一键删除后锁定或策略保留邮件消失，拒绝继续；"
                    f"missing={sorted(missing_protected)[:8]}"
                )
            eligible = self._deletable_runtime_mail_garbage(snapshot)
            current_eligible_ids = set(eligible)
            if not current_eligible_ids < previous_ids:
                raise RuntimeError(
                    "邮件_选择性领取：一键删除后可删除垃圾集合没有严格减少，拒绝误报成功；"
                    f"before={len(previous_ids)} after={len(current_eligible_ids)}"
                )
            removed = len(previous_ids - current_eligible_ids)
            deleted_count += removed
            batches += 1
            self._log(
                "success",
                f"邮件_选择性领取：{reason}第 {batches} 批严格减少 {removed} 封，"
                f"剩余 {len(current_eligible_ids)} 封",
            )
        return {
            "before_count": before_count,
            "after_count": 0,
            "deleted_count": deleted_count,
            "batch_count": batches,
            "protected_count": len(protected_ids),
            "snapshot": snapshot,
        }

    def _delete_read_mail_once(self, context: BehaviorTreeContext, mail_view: View, *, reason: str):
        """在已确认位于邮件列表时执行一次安全的一键删除闭环。"""

        delete_read_shape = mail_view.get_shape("一键删除")
        if delete_read_shape is None:
            raise RuntimeError("缺少 #121「一键删除」标注，无法完成邮件删除闭环")
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_选择性领取：{reason}，一键删除已阅",
                phase="mail_selective_claim_delete_read",
                current_scene=121,
            )
            self._log_locked("action", f"邮件_选择性领取：{reason}，点击 #121「一键删除」")
        yield from context.wait_click(mail_view, delete_read_shape)
        # #210/#278 are Layer 0-owned and use the preceding「一键删除」intent
        # plus their asset description to authorize confirmation. #348 is the
        # only remaining business modal; if absent, re-check the mail page.
        try:
            result_view = yield from context.wait_scene(
                [348],
                wait=6.0,
                label="邮件_选择性领取：一键删除后优先等待确认弹窗",
            )
        except TimeoutError:
            result_view = yield from context.wait_scene(
                [121],
                wait=6.0,
                label="邮件_选择性领取：未见确认弹窗后复核邮件页",
            )
        result_scene = getattr(result_view, "scene_id", getattr(result_view, "id", None))
        if result_scene == 348:
            with self._lock:
                self._set_status_locked(
                    "running",
                    "邮件_选择性领取：确认一键删除",
                    phase="mail_selective_claim_confirm_delete_read",
                    current_scene=result_scene,
                )
                self._log_locked("action", f"邮件_选择性领取：#{result_scene} 点击「确认」")
            self._click_confirmed_mail_delete_prompt(context, result_scene)
            result_view = yield from context.wait_scene(
                (121,),
                wait=12.0,
                label="邮件_选择性领取：确认一键删除后等待邮件页",
            )
            result_scene = getattr(result_view, "scene_id", getattr(result_view, "id", None))
        elif result_scene == 121:
            self._log("info", "邮件_选择性领取：没有可删除邮件，继续当前流程")
        else:
            raise RuntimeError("邮件_选择性领取：一键删除后未进入确认弹窗，也未回到邮件页")
        return result_scene


















































































    def _open_mail_selective_claim_entry(
        self,
        context: BehaviorTreeContext,
    ):
        """Open mail through the shared Runtime-GUI world-menu contract."""

        return (yield from open_world_menu_function(
            context,
            "邮件",
            expected_scene_ids=(121,),
        ))

    def _mail_row_title_shape(self, view: View, row: dict[str, Any]) -> Shape | None:
        if not isinstance(view.raw, dict):
            return None
        width, height = self._frame_size(view.raw)
        try:
            x = float(row.get("x") or 0)
            y = float(row.get("y") or 0)
        except (TypeError, ValueError):
            return None
        raw = {
            "id": f"mail-row-title:{row.get('time_text') or ''}:{row.get('title') or ''}",
            "kind": "rect",
            "title": str(row.get("title") or "邮件标题"),
            "x": max(0.0, min(0.999, (x - 16.0) / max(1, width))),
            "y": max(0.0, min(0.999, (y - 12.0) / max(1, height))),
            "w": max(1.0 / max(1, width), 32.0 / max(1, width)),
            "h": max(1.0 / max(1, height), 24.0 / max(1, height)),
            "imageMatchRole": "off",
            "ocrMatchRole": "off",
        }
        return Shape(raw, parent_view=view)

    def _claim_runtime_mail_row(
        self,
        context: BehaviorTreeContext,
        mail: _VisibleMailRow,
        *,
        delete_after_reward: bool = True,
        require_claim: bool = False,
    ):
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_选择性领取：打开「{mail.title}」",
                phase="mail_selective_claim_open_row",
                current_scene=121,
            )
            self._log_locked("action", f"邮件_选择性领取：点击标题「{mail.title}」")
        mail.title_shape.click(context)
        action_point: tuple[float, float] | None = None
        if require_claim:
            detail_view, action_point = yield from self._wait_precise_mail_detail(
                context,
                mail.title,
                timeout=self._MAIL_DETAIL_READY_TIMEOUT_SECONDS,
            )
        else:
            detail_view = yield from context.wait_scene([122, 123], wait=12.0, label=f"邮件_选择性领取：等待「{mail.title}」详情")
        detail_scene_id = getattr(detail_view, "scene_id", getattr(detail_view, "id", None))
        if detail_scene_id not in {122, 123}:
            return _RuntimeMailActionOutcome("claim", "detail_not_found", False)
        if require_claim and detail_scene_id != 122:
            detail_scene = context.get_view(int(detail_scene_id))
            back_shape = detail_scene.get_shape("空白-返回") if isinstance(detail_scene, View) else None
            if back_shape is not None:
                back_shape.click(context)
            else:
                context.click_frame_point(int(detail_scene_id), 1, 1)
            yield from context.wait_scene(
                [121],
                wait=12.0,
                label="邮件_选择性领取：模型与详情不一致，安全返回邮件 #121",
            )
            self._log(
                "warning",
                f"邮件_选择性领取：模型判定「{mail.title}」可领，但详情页 "
                f"#{detail_view.id} 已无领取动作；先返回列表，交由新鲜 MailMgr 精确身份复验",
            )
            return _RuntimeMailActionOutcome("claim", "claim_action_absent", False)
        actual_policy = "claim" if detail_view.id == 122 else "delete"
        action_title = "领取" if detail_view.id == 122 else "删除"
        action_shape = detail_view.get_shape(action_title)
        if action_shape is None and detail_view.id == 123:
            action_shape = detail_view.get_shape("领取")
        if action_shape is None:
            raise RuntimeError(f"缺少 #{detail_view.id}「{action_title}」标注，无法处理邮件")
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_选择性领取：{action_title}「{mail.title}」",
                phase="mail_selective_claim_claim",
                current_scene=detail_view.id,
            )
            self._log_locked("action", f"邮件_选择性领取：点击 #{detail_view.id}「{action_shape.title}」")
        if action_point is not None:
            self._log(
                "detail",
                f"邮件_选择性领取：标题与动作词联合确认后，点击详情页「{action_title}」动作区域中心 {action_point}",
            )
            context.click_frame_point(detail_view.id, *action_point)
        else:
            action_shape.click(context)
        wait_result = yield from self._wait_mail_list_or_reopen_from_world_after_action(
            context,
            detail_view,
            timeout=18.0,
            label="邮件_选择性领取：返回邮件 #121",
        )
        confirmed_list_results = {"list", "reopened", "list_after_reward", "reopened_after_reward"}
        if delete_after_reward and wait_result in confirmed_list_results:
            image121 = (context.ctx.get("images") or {}).get(121)
            if not isinstance(image121, dict):
                raise RuntimeError("缺少 #121 邮件帧标注，无法在领取返回后执行一键删除")
            yield from self._delete_read_mail_once(context, View(image121), reason="领取返回 #121 后")
        if wait_result in {"timeout", "detail_still_open"}:
            back_shape = detail_view.get_shape("空白-返回")
            if back_shape is None:
                raise RuntimeError("邮件_选择性领取：领取后未回邮件列表，且缺少详情页「空白-返回」标注")
            self._log("info", f"邮件_选择性领取：{action_title}后未自动回列表，点击详情页返回")
            back_shape.click(context)
            yield from context.wait_scene([121], wait=12.0, label="邮件_选择性领取：详情页返回邮件 #121")
        return _RuntimeMailActionOutcome(
            actual_policy,
            wait_result,
            actual_policy == "claim"
            and wait_result in confirmed_list_results,
        )

    def _wait_precise_mail_detail(
        self,
        context: BehaviorTreeContext,
        expected_title: str,
        *,
        timeout: float,
    ):
        """Confirm a mail detail by its title and action button, not a rigid body template."""

        expected = re.sub(r"\s+", "", _sanitize_ocr_text(expected_title))
        started_at = time.monotonic()
        last_frame = ""
        last_texts: list[str] = []
        last_scene_hint: int | None = None
        stable_scene_reads = 0
        while time.monotonic() - started_at < max(1.0, float(timeout)):
            _wait_scene_match = yield from context.wait_scene([122, 123], label='邮件：确认详情浮层', wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            last_frame = frame
            if scene_id not in {122, 123}:
                # ``current_scene`` also evaluates active business/popup nodes.
                # A full-screen mail sheet can therefore lose that combined
                # graph even though the formal #122/#123 subgraph identifies it
                # unambiguously (the SDK floating ball is a common overlay).
                # Reuse the strict detail-only graph as an observation fallback;
                # the existing two-consecutive-frame gate below still prevents
                # one noisy template match from authorizing a claim click.
                scene_id = self._mail_detail_overlay_scene(context, frame)
            action_scene = self._mail_detail_action_shape_scene(context, frame)
            if action_scene in {122, 123}:
                scene_id = action_scene
            if scene_id in {122, 123} and scene_id == last_scene_hint:
                stable_scene_reads += 1
            elif scene_id in {122, 123}:
                last_scene_hint = scene_id
                stable_scene_reads = 1
            else:
                last_scene_hint = None
                stable_scene_reads = 0
            fragments = context.ocr_fragments(frame)
            texts = [
                re.sub(r"\s+", "", _sanitize_ocr_text(item.get("text")))
                for item in fragments
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ]
            last_texts = texts
            claim_fragments = [
                item
                for item in fragments
                if re.sub(r"\s+", "", _sanitize_ocr_text(item.get("text"))) == "领取"
                and float(item.get("w") or 0) > 0
                and float(item.get("h") or 0) > 0
            ]
            delete_fragments = [
                item
                for item in fragments
                if re.sub(r"\s+", "", _sanitize_ocr_text(item.get("text"))) == "删除"
                and float(item.get("w") or 0) > 0
                and float(item.get("h") or 0) > 0
            ]
            # The list window has already been aligned against the ordered MailMgr
            # sequence.  A row identity may therefore be inferred from adjacent
            # OCR anchors (for example, seeing c/d also fixes the preceding b);
            # do not require the detail title to OCR perfectly a second time.
            if len(claim_fragments) == 1 and not delete_fragments:
                detail_view = context.view(122)
                action_shape = detail_view.get_shape("领取")
                if action_shape is None:
                    return None, None
                raw_action = action_shape.raw
                frame_width = float(detail_view.raw.get("width") or 900)
                frame_height = float(detail_view.raw.get("height") or 1600)
                action_point = (
                    (float(raw_action.get("x") or 0) + float(raw_action.get("w") or 0) / 2) * frame_width,
                    (float(raw_action.get("y") or 0) + float(raw_action.get("h") or 0) / 2) * frame_height,
                )
                self._log(
                    "info",
                    f"邮件_选择性领取：清单序列已定位「{expected_title}」，详情唯一动作「领取」确认 #122",
                )
                return detail_view, action_point
            if len(delete_fragments) == 1 and not claim_fragments:
                detail_view = context.view(123)
                action_shape = detail_view.get_shape("删除") or detail_view.get_shape("领取")
                if action_shape is None:
                    return None, None
                raw_action = action_shape.raw
                frame_width = float(detail_view.raw.get("width") or 900)
                frame_height = float(detail_view.raw.get("height") or 1600)
                return detail_view, (
                    (float(raw_action.get("x") or 0) + float(raw_action.get("w") or 0) / 2) * frame_width,
                    (float(raw_action.get("y") or 0) + float(raw_action.get("h") or 0) / 2) * frame_height,
                )
            if stable_scene_reads >= 2 and scene_id in {122, 123}:
                # OCR can miss a stylised button.  Two consecutive scene reads are
                # an independent fallback; a single image match is not enough to
                # override the MailMgr/list-window plan.
                return context.view(scene_id), None
            yield from context.wait_action_settle(0.6)
        if last_frame:
            try:
                evidence = build_unknown_evidence(
                    self,
                    context.ctx,
                    last_frame,
                    label=f"mail_detail_{expected_title}",
                    expected_scene_ids=[122, 123],
                    last_scene_id=None,
                    last_score=0.0,
                )
                self._log(
                    "warning",
                    f"邮件_选择性领取：详情联合确认超时，目标「{expected_title}」，"
                    f"OCR={last_texts}，截图={evidence.frame_path}，证据={evidence.report_path}",
                )
            except Exception as exc:
                self._log(
                    "warning",
                    f"邮件_选择性领取：详情联合确认超时，目标「{expected_title}」，OCR={last_texts}；"
                    f"保存证据失败：{exc}",
                )
        return None, None

    @staticmethod
    def _mail_detail_action_shape_scene(
        context: BehaviorTreeContext,
        frame_data_url: str,
    ) -> int | None:
        """Resolve a detail overlay from its formal action Shapes."""

        try:
            claim_score = float(
                context.shape_score(122, "领取", frame_data_url=frame_data_url)
            )
        except Exception:
            claim_score = 0.0
        try:
            delete_score = float(
                context.shape_score(123, "删除", frame_data_url=frame_data_url)
            )
        except Exception:
            delete_score = 0.0
        if claim_score >= 90.0 and claim_score > delete_score + 10.0:
            return 122
        if delete_score >= 90.0 and delete_score > claim_score + 10.0:
            return 123
        return None

    def _mail_detail_overlay_scene(
        self,
        context: BehaviorTreeContext,
        frame_data_url: str,
    ) -> int | None:
        """Resolve the overlay through the formal #122/#123 graph nodes."""

        scene_id, _score, _frame = context.recognize_scene_in_frame(
            [122, 123], frame_data_url=frame_data_url
        )
        return scene_id if scene_id in {122, 123} else None

    def _wait_mail_list_after_detail_action(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        context: BehaviorTreeContext,
        scene_id: int,
        *,
        timeout: float,
        label: str,
    ):
        detail_image = (ctx.get("images") or {}).get(scene_id)
        if isinstance(detail_image, dict):
            detail_view = View(detail_image)
            wait_result = yield from self._wait_mail_list_or_reopen_from_world_after_action(
                context,
                detail_view,
                timeout=timeout,
                label=label,
            )
            if wait_result in {"list", "reopened", "list_after_reward", "reopened_after_reward"}:
                return wait_result
            if wait_result in {"timeout", "detail_still_open"}:
                back_shape = detail_view.get_shape("空白-返回")
                if back_shape is not None:
                    self._log("info", f"{label}：详情页未自动回列表，点击详情页返回")
                    back_shape.click(context)
                    yield from self._wait_mail_list_ready_or_restore_world(
                        ctx,
                        stop_event,
                        timeout=12.0,
                        label=label,
                    )
                    return "list"
        yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=timeout, label=label)
        return "list"

    def _wait_mail_list_or_reopen_from_world_after_action(
        self,
        context: BehaviorTreeContext,
        detail_view: View,
        *,
        timeout: float,
        label: str,
    ):
        ctx = context.ctx
        stop_event = context.stop_event or threading.Event()
        image121 = (ctx.get("images") or {}).get(121)
        marker_shape = self._find_shape(image121, "邮件标识") if isinstance(image121, dict) else None
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_marker_score = 0.0
        last_ocr_at = 0.0
        last_text = ""
        saw_reward_transition = False
        reward_continue_click_count = 0
        last_reward_continue_signature = ""
        reward_item_detail_close_count = 0
        fresh_mail_list_streak = 0
        while True:
            self._raise_if_stopped(stop_event)
            context.clear_frame() if hasattr(context, "clear_frame") else self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            elapsed = time.monotonic() - start
            detail_scene_id = detail_view.id if isinstance(detail_view.id, int) else None
            candidates = [scene for scene in [121, 347, 250, 34, detail_scene_id] if isinstance(scene, int)]
            scene_id, score, frame, text = yield from self._behavior_tree_context_scene_text(ctx, context, candidates, update=True)
            last_scene_id, last_score = scene_id, score
            if scene_id != 121:
                fresh_mail_list_streak = 0
            marker_score = 0.0
            marker_matched = False
            if scene_id == 250:
                if reward_item_detail_close_count >= 2:
                    raise RuntimeError(f"{label}：奖励后连续打开 #250 道具详情，已停止避免循环")
                self._log("info", f"{label}：奖励点击后打开 #250 道具详情，使用正式「返回」标注关闭")
                yield from context.wait_click(250, "返回", timeout=8.0, label=f"{label}：关闭奖励道具详情")
                reward_item_detail_close_count += 1
                yield from context.wait_action_settle(0.8)
                continue
            if (
                scene_id != 347
                and self._mail_continue_hint_text_matches(text)
            ):
                if not saw_reward_transition:
                    compact_transition_text = _sanitize_ocr_text(text).replace("\n", " | ")
                    self._log(
                        "info",
                        f"{label}：奖励继续帧 OCR={compact_transition_text[:600] or '<empty>'}",
                    )
                saw_reward_transition = True
                with self._lock:
                    self._status.update(
                        {
                            "phase": "mail_selective_claim_wait_reward_transition",
                            "current_scene": scene_id,
                            "message": f"{label}：检测到领取奖励继续提示，等待回到邮件 #121",
                            "updated_at": time.time(),
                        }
                    )
                continue_signature = _sanitize_ocr_text(text).replace(" ", "")
                if continue_signature != last_reward_continue_signature:
                    if reward_continue_click_count >= 6:
                        raise RuntimeError(f"{label}：连续奖励继续页超过 6 层，已停止避免无界点击")
                    continue_point = self._mail_continue_hint_click_point(context, frame)
                    if continue_point is not None:
                        image347 = (ctx.get("images") or {}).get(347)
                        click_view = image347 if isinstance(image347, dict) else detail_view
                        self._log(
                            "info",
                            f"{label}：领取结果明确提示「点击屏幕继续」，点击提示文字关闭过场",
                        )
                        context.click_frame_point(click_view, *continue_point)
                        reward_continue_click_count += 1
                        last_reward_continue_signature = continue_signature
                        yield from context.wait_action_settle(0.8)
                continue
            if scene_id == 347 or self._mail_reward_transition_text_matches(text):
                if not saw_reward_transition:
                    compact_transition_text = _sanitize_ocr_text(text).replace("\n", " | ")
                    self._log(
                        "info",
                        f"{label}：奖励过场 OCR={compact_transition_text[:600] or '<empty>'}",
                    )
                saw_reward_transition = True
                with self._lock:
                    self._status.update(
                        {
                            "phase": "mail_selective_claim_wait_reward_transition",
                            "current_scene": 347 if scene_id == 347 else scene_id,
                            "message": f"{label}：检测到 #347 领取奖励过场，等待自动回到邮件 #121",
                            "updated_at": time.time(),
                        }
                    )
                continue
            if scene_id == 121:
                if isinstance(image121, dict) and marker_shape:
                    try:
                        marker_result = self._match_shape(ctx, image121, marker_shape, frame)
                        marker_score = float(marker_result.get("similarity") or 0)
                        marker_matched = bool(marker_result.get("matched"))
                    except Exception as exc:
                        self._log("detail", f"{label}：邮件标识匹配失败：{exc}")
                else:
                    marker_matched = True
                last_marker_score = marker_score
                if marker_matched:
                    # A reward animation can briefly expose the underlying
                    # #121 marker between consecutive reward pages.  Returning
                    # on that single frame lets the batch OCR the next reward
                    # page as if it were the mail list.  Once any reward layer
                    # has been observed, require two fresh consecutive #121
                    # frames; a reappearing overlay resets the streak above.
                    if saw_reward_transition:
                        fresh_mail_list_streak += 1
                        last_reward_continue_signature = ""
                        if fresh_mail_list_streak < 2:
                            self._log(
                                "detail",
                                f"{label}：奖励后首次识别到 #121 与邮件标识，"
                                "继续获取新鲜帧确认奖励过场已完整结束",
                            )
                            yield from context.wait_action_settle(0.5)
                            continue
                    with self._lock:
                        self._status.update({"current_scene": 121, "updated_at": time.time()})
                    self._log("success", f"{label}：已到达 #121 {score:.0f}%，邮件标识 {marker_score:.0f}%")
                    return "list_after_reward" if saw_reward_transition else "list"
            if detail_scene_id is not None and scene_id == detail_scene_id and elapsed >= 5.0:
                self._log("info", f"{label}：领取后仍停留 #{detail_scene_id} {score:.0f}%，提前走详情页返回")
                return "detail_still_open"
            now = time.monotonic()
            if scene_id == 34 or now - last_ocr_at >= 1.2:
                last_ocr_at = now
                last_text = text or last_text
            if scene_id == 34:
                self._log("info", f"{label}：领取后落到世界页，重新打开邮件列表")
                yield from context.wait_action_settle(0.8)
                reopened = self._reopen_mail_from_current_world_like(context)
                result = (yield from reopened) if isinstance(reopened, GeneratorType) else reopened
                if result == "success":
                    return "reopened_after_reward" if saw_reward_transition else "reopened"
                self._log("info", f"{label}：从世界页重新打开邮件失败 result={result}，继续等待")
            with self._lock:
                self._status.update(
                    {
                        "phase": "mail_selective_claim_wait_list_or_world",
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
                self._log("info", f"{label}：等待列表或世界页超时，最后 {scene_text} {last_score:.0f}%，邮件标识 {last_marker_score:.0f}% OCR={last_text}")
                return "timeout"

    def _reopen_mail_from_current_world_like(self, context: BehaviorTreeContext):
        return (yield from self._open_mail_selective_claim_entry(context))

    def _mail_reward_transition_text_matches(self, text: str) -> bool:
        compact = _sanitize_ocr_text(text).replace(" ", "")
        if not compact:
            return False
        has_reward_title = "恭喜获得" in compact or bool(re.search(r"[恭共]喜.{0,3}[获莎]?得", compact))
        has_continue_hint = "点击屏幕继续" in compact or "点击继续" in compact
        has_auto_close = "自动关闭" in compact or bool(re.search(r"\d+秒后.{0,4}关闭", compact))
        return bool(has_reward_title and (has_continue_hint or has_auto_close or "获得" in compact))

    def _mail_continue_hint_text_matches(self, text: str) -> bool:
        compact = _sanitize_ocr_text(text).replace(" ", "")
        return "点击屏幕继续" in compact or "点击继续" in compact

    def _mail_continue_hint_click_point(
        self,
        context: BehaviorTreeContext,
        frame: str,
    ) -> tuple[float, float] | None:
        for fragment in context.ocr_fragments(frame):
            text = _sanitize_ocr_text(fragment.get("text")).replace(" ", "")
            if "点击屏幕继续" not in text and "点击继续" not in text:
                continue
            x = float(fragment.get("x") or 0)
            y = float(fragment.get("y") or 0)
            width = float(fragment.get("w") or 0)
            height = float(fragment.get("h") or 0)
            if width > 0 and height > 0:
                return x + width / 2, y + height / 2
        return None

    def _refresh_runtime_mail_snapshot(self, label: str, *, force_refresh: bool) -> bool:
        del force_refresh
        try:
            from sqlmodel import Session

            from backend.core.fanxiu.mail.runtime_sync import sync_fanxiu_mail_from_runtime

            with Session(_db_engine()) as session:
                result = sync_fanxiu_mail_from_runtime(session)
            runtime_timings = result.get("runtime_timings") or {}
            stage_text = "/".join(
                f"{key}={float(runtime_timings.get(key) or 0.0):.2f}s"
                for key in (
                    "process_discovery",
                    "lua_state",
                    "manager_resolve",
                    "snapshot_decode",
                )
            )
            with self._lock:
                self._log_locked(
                    "info",
                    "邮件_动态模型："
                    f"{label} current={result.get('record_count', 0)} "
                    f"updated={result.get('updated', 0)} inserted={result.get('inserted', 0)} "
                    f"absent={result.get('absent', 0)} "
                    f"context={float(result.get('runtime_elapsed_seconds') or 0.0):.2f}s "
                    f"projection={float(result.get('projection_elapsed_seconds') or 0.0):.2f}s "
                    f"root_cache_hit={result.get('root_cache_hit')} stages={stage_text}",
                )
            return bool(result.get("ok"))
        except Exception as exc:
            with self._lock:
                self._log_locked("error", f"邮件_动态模型：{label}失败：{exc}")
            return False

