from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.tasks.bubble_lifecycle import (
    bubble_claimed_item_ids,
    record_bubble_claim_item,
    record_bubble_claim_success,
)


class BubbleClaimPillsTaskMixin:
    """领取 37 手游气泡里的每周丹药礼包。"""

    @staticmethod
    def _bubble_overlay_scene(runtime: Any, scene_ids: tuple[int, ...], *, update: bool = True):
        scene_id, score, frame = runtime.current_scene(list(scene_ids), update=update)
        if scene_id in scene_ids:
            return scene_id, score, frame
        direct: list[tuple[float, int]] = []
        for candidate_id in scene_ids:
            matched, candidate_score, _frame = runtime.match_view(
                candidate_id,
                frame_data_url=frame,
            )
            if matched:
                direct.append((float(candidate_score or 0.0), candidate_id))
        if direct:
            candidate_score, candidate_id = max(direct)
            return candidate_id, candidate_score, frame
        return scene_id, score, frame

    def _record_bubble_claim_pills_done(
        self,
        payload: dict[str, Any],
        *,
        claim_count: int,
        now: datetime | None = None,
    ) -> None:
        completed_at = now or job_now()
        record_bubble_claim_success(
            self._bubble_lifecycle_world_facts_path(),
            now=completed_at,
            claim_count=claim_count,
        )

    def _ensure_bubble_gift_page(
        self,
        runtime: Any,
        *,
        transition_timeout: float,
    ):
        scene_id, _score, frame = self._bubble_overlay_scene(runtime, (592, 591, 590))
        if scene_id == 592:
            raise RuntimeError("气泡_领丹药：启动时停在未完成的角色选择事务 #592，拒绝猜测确认")
        if scene_id == 591:
            return 591
        # #591 的列表内容会随礼包与剩余次数动态变化，整页图模板不适合
        # 独立承担恢复身份。“窗口”内完整的精确“领取”条目是该 SDK 页的
        # 结构身份；用同一帧的条目模板 + OCR 识别，避免误去点被弹窗
        # 遮住的悬浮球。
        if self._bubble_visible_claim_items(runtime, frame=frame) or self._bubble_visible_claim_items(
            runtime,
            frame=frame,
            anchor_text="已领取",
        ):
            return 591
        if scene_id != 590:
            yield from runtime.open_sdk_bubble_menu(timeout=transition_timeout)
        yield from runtime.click_shape_center_then_view(
            590,
            "礼包",
            591,
            timeout=transition_timeout,
            label="气泡_领丹药：等待礼包列表 #591",
        )
        return 591

    def _bubble_visible_claim_items(
        self,
        runtime: Any,
        *,
        frame: str,
        anchor_text: str = "领取",
    ) -> list[Any]:
        """按条目模板定位当前窗口中的状态行，保持按钮 x 固定、只平移 y。"""

        items = runtime.find_floating_items_by_anchor_text(
            591,
            "礼包条目",
            "领取",
            anchor_text,
            container_shape="窗口",
            frame_data_url=frame,
            match_mode="exact",
        )
        visible = [
            item
            for item in items
            if runtime.floating_item_field_is_fully_inside(item, "领取", "窗口")
        ]
        tokens = runtime.ocr_tokens_in_shapes(
            591,
            ("窗口",),
            frame_data_url=frame,
        )
        for item in visible:
            box = item.item_box
            right = float(box.get("x") or 0) + float(box.get("w") or 0) * 0.67
            bottom = float(box.get("y") or 0) + float(box.get("h") or 0) * 0.58
            identity_tokens = []
            for token in tokens:
                center_x = float(token.get("x") or 0) + float(token.get("w") or 0) / 2
                center_y = float(token.get("y") or 0) + float(token.get("h") or 0) / 2
                if (
                    float(box.get("x") or 0) <= center_x <= right
                    and float(box.get("y") or 0) <= center_y <= bottom
                    and str(token.get("text") or "").strip()
                ):
                    identity_tokens.append(token)
            identity_tokens.sort(key=lambda token: (float(token.get("y") or 0), float(token.get("x") or 0)))
            item.bubble_identity = "".join(str(token.get("text") or "").strip() for token in identity_tokens)
        return sorted(visible, key=lambda item: float(item.item_box.get("y") or 0))

    def _try_open_bubble_claim_role(
        self,
        runtime: Any,
        item: Any,
        *,
        timeout: float,
        poll_seconds: float,
    ):
        """领取按钮每个位置只点一次；是否进入 #592 是有效性的唯一判据。"""

        runtime.click_floating_item_field(item, "领取")
        yield from runtime.wait_action_settle(poll_seconds)
        probe_samples = max(1, int(round(timeout / poll_seconds)))
        last_scene: int | None = 591
        for sample in range(probe_samples):
            scene_id, _score, frame = self._bubble_overlay_scene(runtime, (592, 591))
            last_scene = scene_id
            if scene_id == 592:
                return True
            if scene_id is None and (
                self._bubble_visible_claim_items(runtime, frame=frame)
                or self._bubble_visible_claim_items(runtime, frame=frame, anchor_text="已领取")
            ):
                # 整页 #591 图模板可能因剩余次数变动而丢失；若点击后
                # 同帧仍能看到完整领取条目，说明该按钮未生效，只跳过
                # 这一条，不把结构化 #591 误报为过渡超时。
                return False
            if scene_id not in {None, 591}:
                raise RuntimeError(
                    f"气泡_领丹药：点击领取后进入未知场景，scene={scene_id}"
                )
            if sample + 1 < probe_samples:
                yield from runtime.wait_action_settle(poll_seconds)
        if last_scene is None:
            raise TimeoutError("气泡_领丹药：点击领取后持续处于未识别过渡帧，禁止重复点击")
        return False

    def _wait_bubble_role_confirm_landing(
        self,
        runtime: Any,
        *,
        timeout: float,
        poll_seconds: float,
    ):
        """确认只点击一次；随后等待 #592 连续两帧消失。"""

        deadline = time.monotonic() + timeout
        absent_count = 0
        last_scene: int | None = 592
        while time.monotonic() < deadline:
            scene_id, _score, _frame = self._bubble_overlay_scene(runtime, (592, 591, 590))
            last_scene = scene_id
            if scene_id == 592:
                absent_count = 0
            else:
                absent_count += 1
                if scene_id in {591, 590} or absent_count >= 2:
                    return scene_id
            yield from runtime.wait_action_settle(poll_seconds)
        raise TimeoutError(
            f"气泡_领丹药：点击确认后 #592 未消失，禁止重复确认，scene={last_scene}"
        )

    def _close_bubble_claim_pages(
        self,
        runtime: Any,
        *,
        transition_timeout: float,
        poll_seconds: float,
        max_scrolls: int,
    ):
        scene_id, _score, frame = self._bubble_overlay_scene(runtime, (591, 590))
        list_visible = bool(
            self._bubble_visible_claim_items(runtime, frame=frame)
            or self._bubble_visible_claim_items(runtime, frame=frame, anchor_text="已领取")
        )
        if scene_id == 591 or list_visible:
            rewind_count = 0
            while rewind_count < max_scrolls:
                changed = yield from runtime.scroll_shape_content(
                    591,
                    "窗口",
                    direction="up",
                    ratio=0.55,
                    settle_seconds=poll_seconds,
                    unchanged_confirmations=2,
                )
                if not changed:
                    break
                rewind_count += 1
            else:
                raise RuntimeError(
                    f"气泡_领丹药：收尾回卷 {rewind_count} 次仍未到顶部，拒绝猜返回按钮"
                )
            scene_id, _score, frame = self._bubble_overlay_scene(runtime, (591, 590))
            list_visible = bool(
                self._bubble_visible_claim_items(runtime, frame=frame)
                or self._bubble_visible_claim_items(runtime, frame=frame, anchor_text="已领取")
            )
            if scene_id != 591 and not list_visible:
                raise RuntimeError("气泡_领丹药：列表回卷到顶后既无 #591 也无完整条目结构")
            yield from runtime.click_shape_center_then_view(
                591,
                "返回",
                590,
                timeout=transition_timeout,
                label="气泡_领丹药：返回气泡菜单 #590",
            )
            scene_id = 590
        if scene_id != 590:
            return

        # #590 没有独立关闭按钮；浮动气泡本身是同一个开关。
        yield from runtime.wait_click(421, "气泡", timeout=transition_timeout)
        deadline = time.monotonic() + transition_timeout
        while time.monotonic() < deadline:
            matched, _score, _frame = runtime.match_view(590, update=True)
            if not matched:
                return
            yield from runtime.wait_action_settle(poll_seconds)
        raise TimeoutError("气泡_领丹药：点击气泡后 #590 仍可见，未确认菜单关闭")

    def _execute_bubble_claim_pills_task(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少气泡_领丹药资产树路径，无法执行作业")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        transition_timeout = max(3.0, float(payload.get("transition_timeout_seconds") or 15.0))
        poll_seconds = max(0.2, float(payload.get("poll_seconds") or 0.75))
        max_claims = max(1, min(10, int(payload.get("max_claims") or 5)))
        max_scrolls = max(1, min(20, int(payload.get("max_scrolls") or 10)))
        minimum_completed_rewards = max(
            0,
            min(10, int(payload.get("minimum_completed_rewards") or 0)),
        )
        claim_probe_timeout = max(
            1.0,
            min(5.0, float(payload.get("claim_probe_timeout_seconds") or 2.5)),
        )
        target_name = str(payload.get("target_role_name") or "羊驼").strip()
        claimed = 0
        scroll_depth = 0
        scroll_count = 0
        facts_path = self._bubble_lifecycle_world_facts_path()
        attempted_item_ids = bubble_claimed_item_ids(facts_path, now=job_now())
        encountered_item_ids: list[str] = []
        observed_completed_item_ids: set[str] = set()

        yield from self._ensure_bubble_gift_page(
            runtime,
            transition_timeout=transition_timeout,
        )
        # 恢复执行时 SDK 会保留上次滚动位置。每次从窗口顶部
        # 开始才能保证“从上到下”和跨 Cell 幂等。
        rewind_count = 0
        while rewind_count < max_scrolls:
            changed = yield from runtime.scroll_shape_content(
                591,
                "窗口",
                direction="up",
                ratio=0.55,
                settle_seconds=poll_seconds,
                unchanged_confirmations=2,
            )
            if not changed:
                break
            rewind_count += 1
        else:
            raise RuntimeError(
                f"气泡_领丹药：向上回卷 {rewind_count} 次仍未确认窗口顶部"
            )

        while True:
            scene_id, _score, frame = self._bubble_overlay_scene(runtime, (591,))
            items = self._bubble_visible_claim_items(runtime, frame=frame)
            completed_items = self._bubble_visible_claim_items(
                runtime,
                frame=frame,
                anchor_text="已领取",
            )
            for completed_item in completed_items:
                completed_id = str(
                    getattr(completed_item, "bubble_identity", "") or ""
                ).strip()
                if completed_id:
                    observed_completed_item_ids.add(completed_id)
            if scene_id != 591 and not items and not completed_items:
                raise RuntimeError(f"气泡_领丹药：扫描礼包窗口时已离开 #591，scene={scene_id}")
            item = None
            item_id = ""
            for candidate in items:
                candidate_id = str(getattr(candidate, "bubble_identity", "") or "").strip()
                if not candidate_id:
                    raise RuntimeError("气泡_领丹药：无法从礼包标题/等级行构造稳定条目身份，拒绝点击")
                if candidate_id not in attempted_item_ids:
                    if candidate_id not in encountered_item_ids:
                        encountered_item_ids.append(candidate_id)
                    item = candidate
                    item_id = candidate_id
                    break

            if item is None:
                if scroll_count >= max_scrolls:
                    raise RuntimeError(
                        f"气泡_领丹药：滚动 {scroll_count} 次仍未到达窗口底部，停止避免无限滚动"
                    )
                changed = yield from runtime.scroll_shape_content(
                    591,
                    "窗口",
                    direction="down",
                    ratio=0.55,
                    settle_seconds=poll_seconds,
                    unchanged_confirmations=2,
                )
                scroll_count += 1
                if not changed:
                    break
                scroll_depth += 1
                continue

            attempted_item_ids.add(item_id)
            opened = yield from self._try_open_bubble_claim_role(
                runtime,
                item,
                timeout=claim_probe_timeout,
                poll_seconds=poll_seconds,
            )
            if not opened:
                # The empirically effective rewards are the first three rows.
                # For those only, tolerate one lost touch after a fresh frame
                # proves the same stable item is still enabled. Lower rows are
                # often intentionally inactive and remain single-attempt.
                item_ordinal = encountered_item_ids.index(item_id)
                if item_ordinal < 3:
                    _scene_id, _score, retry_frame = self._bubble_overlay_scene(runtime, (591,))
                    retry_item = next(
                        (
                            candidate
                            for candidate in self._bubble_visible_claim_items(
                                runtime,
                                frame=retry_frame,
                            )
                            if str(getattr(candidate, "bubble_identity", "") or "").strip()
                            == item_id
                        ),
                        None,
                    )
                    if retry_item is not None:
                        self._log(
                            "detail",
                            f"气泡_领丹药：前3项「{item_id[:36]}」首次触摸未生效，重新定位后仅重试一次",
                        )
                        opened = yield from self._try_open_bubble_claim_role(
                            runtime,
                            retry_item,
                            timeout=claim_probe_timeout,
                            poll_seconds=poll_seconds,
                        )
            if not opened:
                self._log(
                    "detail",
                    f"气泡_领丹药：窗口第 {scroll_depth} 屏的领取按钮无效，已完成有界处理",
                )
                continue
            if claimed >= max_claims:
                raise RuntimeError(
                    f"气泡_领丹药：已领取 {claimed} 个后仍出现有效领取，停止避免无限消费"
                )

            frame = runtime.cur_frame(update=True)
            role_text = runtime.ocr_text_in_shapes(
                592,
                ["羊驼角色"],
                padding=4,
                frame_data_url=frame,
            )
            if target_name not in role_text:
                raise RuntimeError(
                    f"气泡_领丹药：#592 未确认目标角色“{target_name}”，OCR={role_text[:80]}"
                )
            if runtime.shape_matches(592, "选择当前角色") is None:
                raise RuntimeError("气泡_领丹药：#592 未匹配未选中的角色单选框，拒绝猜测当前选择")

            yield from runtime.wait_click(592, "选择当前角色", timeout=transition_timeout)
            yield from runtime.wait_action_settle(poll_seconds)
            if runtime.shape_matches(592, "选择当前角色") is not None:
                raise RuntimeError("气泡_领丹药：点击角色后单选框仍为未选中态，拒绝点击确认")

            # 确认可能成功后不得重放；只做有界只读观察。
            yield from runtime.wait_click(592, "确认", timeout=transition_timeout)
            yield from runtime.wait_action_settle(poll_seconds)
            landing = yield from self._wait_bubble_role_confirm_landing(
                runtime,
                timeout=transition_timeout,
                poll_seconds=poll_seconds,
            )
            claimed += 1
            record_bubble_claim_item(
                facts_path,
                now=job_now(),
                item_id=item_id,
            )
            self._log(
                "success",
                f"气泡_领丹药：礼包「{item_id[:48]}」已确认发给“{target_name}”，"
                f"本 Cell 新增 {claimed} 个，落点={landing}",
            )
            yield from self._ensure_bubble_gift_page(
                runtime,
                transition_timeout=transition_timeout,
            )
            if landing not in {591, 590}:
                # 弹窗关闭到游戏底页后重新打开列表会回到顶部。
                scroll_depth = 0

        yield from self._close_bubble_claim_pages(
            runtime,
            transition_timeout=transition_timeout,
            poll_seconds=poll_seconds,
            max_scrolls=max_scrolls,
        )
        if len(observed_completed_item_ids) < minimum_completed_rewards:
            raise RuntimeError(
                "气泡_领丹药：虽已扫描到底并安全收尾，但只观察到 "
                f"{len(observed_completed_item_ids)}/{minimum_completed_rewards} 个“已领取”条目，"
                "拒绝写本周完成事实"
            )
        total_claimed = len(bubble_claimed_item_ids(facts_path, now=job_now()))
        self._record_bubble_claim_pills_done(payload, claim_count=total_claimed)
        message = (
            f"气泡_领丹药：已从上到下尝试礼包窗口并确认到底，"
            f"本次新增领取 {claimed} 个、本周累计 {total_claimed} 个；"
            "本周领取事实已确认，继续执行气泡隐藏阶段"
        )
        self._log("success", message)
        return {
            "result": "success",
            "message": message,
            "claim_count": claimed,
            "weekly_claim_count": total_claimed,
            "target_role_name": target_name,
        }
