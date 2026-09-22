from __future__ import annotations

"""缘定三生「正式运行」单元：任务奖励领取 + 自动联姻使用资源，最后回 #34。

活动期间每天 10/12/15/18/20 点各运行一次（资源榜 checkpoint 触发）。两个小模块连跑，
中间不返回世界，等两段都结束再回 #34；任务奖励是分档的，本期全部领完后本单元在当期
活动内幂等，后续触发会自动跳过领奖段。

设计约束（来自业务层《任务奖励领取》与 2026-09-22 真机梳理）：

- 领奖复用共用组件 ``claim_task_rows_by_ocr``（兽渊契约）：点整行安全区、用不受飘字
  遮挡的第三行任务标题观察推进，禁止拿「获得[…]」飘字当领取证据。
- 自动联姻面板的勾选/数量是推荐配置：来源勾「所有玩家」「在联姻大会中挑选弟子联姻」，
  评分方案用「默认」，勾「优先选择评分相近的弟子进行联姻」，不勾「不与低于己方评分」，
  数量拉到上限（等于己方剩余未联姻弟子数）。
- 「开启自动」是唯一不可逆动作，点击前必须先对齐配置；确认弹窗（#808）是可选节点，
  勾「本次登录不再提示」后不再出现，因此允许直接进入运行态。
"""

import base64
import io
import re
import time
from typing import Any, Iterator, Mapping

from backend.core.fanxiu.data_annotation.tasks.task_reward_rows import claim_task_rows_by_ocr
from backend.core.fanxiu.data_annotation.tasks.yuanding_sansheng import (
    YuandingSanshengTaskMixin,
    YUANDING_ACTIVITY_NAME,
    YUANDING_INTRO_SCENE_ID,
    YUANDING_MAIN_SCENE_ID,
    YUANDING_STORE_SCENE_ID,
    exact_fragment,
    fragment_center,
    fragment_text,
    yuanding_page_state,
)


YUANDING_RESOURCE_UNIT_TASK_TYPE = "yuanding_sansheng_resource_unit"
YUANDING_RESOURCE_UNIT_TASK_ID = "yuanding-sansheng-resource-unit"
YUANDING_RESOURCE_UNIT_LABEL = "缘定三生_正式运行"
# 活动期间每天的运行时点（业务层约定；资源榜 checkpoint 按这些时点排程）。
YUANDING_RESOURCE_UNIT_HOURS = (10, 12, 15, 18, 20)

YUANDING_DIZIJU_SCENE_ID = 804
YUANDING_CONFERENCE_SCENE_ID = 805
YUANDING_AUTO_MARRY_SCENE_ID = 806
YUANDING_MARRY_RESULT_SCENE_ID = 807
YUANDING_AUTO_CONFIRM_SCENE_ID = 808
YUANDING_TASK_PAGE_SCENE_ID = 809
YUANDING_XIANFU_SCENE_ID = 171
YUANDING_WORLD_SCENE_ID = 34
YUANDING_SCHEDULE_SCENE_ID = 66

# 自动联姻推荐配置：勾选框几何 + 目标勾选态（像素绿勾判定，与人工核对一致）。
AUTO_MARRY_CHECKBOX_TARGETS: tuple[tuple[str, tuple[int, int, int, int], bool], ...] = (
    ("仅接受社团", (128, 606, 46, 46), False),
    ("仅接受好友", (128, 664, 46, 46), False),
    ("接受所有玩家", (128, 726, 46, 46), True),
    ("联姻大会挑选", (128, 788, 46, 46), True),
    ("不与低于己方评分", (128, 939, 46, 46), False),
    ("优先评分相近", (128, 998, 46, 46), True),
)
AUTO_MARRY_PLUS_BUTTON = (700, 1300)
AUTO_MARRY_START_SHAPE = "开启自动"
AUTO_MARRY_BACKGROUND_TAP = (450, 130)
AUTO_MARRY_CONFIRM_CHECKBOX_TAP = (313, 947)
AUTO_MARRY_CONFIRM_OK_TAP = (613, 1060)
# 运行中点别的操作会被拦下并弹「自动联姻过程中不允许进行其他操作 / 是否停止自动联姻？」；
# 要离场就点「确定」（等价于先停止自动联姻）。
AUTO_MARRY_STOP_CONFIRM_OK_TAP = (597, 1062)


def _frame_image(frame_data_url: str) -> Any:
    from PIL import Image

    payload = str(frame_data_url or "").split(",", 1)[-1]
    return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")


def _checkbox_checked(frame_data_url: str, box: tuple[int, int, int, int]) -> bool | None:
    """像素级读取勾选框状态（绿勾占比）；读不到图时返回 None，由调用方重试。"""

    try:
        image = _frame_image(frame_data_url)
    except Exception:
        return None
    x, y, w, h = box
    crop = image.crop((x, y, x + w, y + h))
    pixels = list(crop.getdata())
    if not pixels:
        return None
    green = sum(1 for r, g, b in pixels if g > 110 and g > r + 25 and g > b + 15)
    return (green / len(pixels)) > 0.05


class YuandingSanshengRunMixin(YuandingSanshengTaskMixin):
    """资源使用 + 任务奖励的聚合运行单元（资源榜 checkpoint 调用）。"""

    @staticmethod
    def _require_scene(match: Any, expected: Any, *, label: str) -> int:
        """场景断言：不在预期场景就报错停手，绝不按坐标盲点。"""

        scene = int(getattr(match, "id", match) or 0)
        allowed = {int(expected)} if isinstance(expected, int) else {int(value) for value in expected}
        if scene not in allowed:
            raise RuntimeError(
                f"{label}：当前场景 #{scene} 不在预期 {sorted(allowed)}，拒绝继续点击"
            )
        return scene

    def _yuanding_open_activity_home(
        self, context: Any, stop_event: Any, *, page_timeout: float,
    ) -> Iterator[Any]:
        """把现场收敛到活动主页 #802：世界 → 日程卡片 → 说明层 → 查看详情。"""

        match = yield from context.wait_scene(
            [YUANDING_WORLD_SCENE_ID, YUANDING_SCHEDULE_SCENE_ID,
             YUANDING_INTRO_SCENE_ID, YUANDING_MAIN_SCENE_ID],
            wait=page_timeout,
            required=False,
        )
        scene = int(getattr(match, "id", match) or 0)
        known = {
            YUANDING_WORLD_SCENE_ID,
            YUANDING_SCHEDULE_SCENE_ID,
            YUANDING_INTRO_SCENE_ID,
            YUANDING_MAIN_SCENE_ID,
        }
        if scene not in known:
            # 上次中断可能留在弟子居/仙府等活动页，先回稳定入口再整单重入。
            yield from context.go_scene(YUANDING_WORLD_SCENE_ID)
            scene = YUANDING_WORLD_SCENE_ID
        if scene == YUANDING_WORLD_SCENE_ID:
            yield from context.go_scene(YUANDING_SCHEDULE_SCENE_ID)
            scene = YUANDING_SCHEDULE_SCENE_ID
        if scene == YUANDING_SCHEDULE_SCENE_ID:
            deadline = 0
            entry = None
            while deadline < int(page_timeout * 3):
                frame = context.cur_frame(update=True)
                fragments = context.ocr_fragments(frame)
                entry = exact_fragment(fragments, YUANDING_ACTIVITY_NAME)
                if entry is not None:
                    break
                deadline += 1
                yield from context.wait_action_settle(0.75)
            if entry is None:
                raise RuntimeError(
                    f"{YUANDING_RESOURCE_UNIT_LABEL}：日程未识别到「{YUANDING_ACTIVITY_NAME}」活动卡片"
                )
            # 公告/效果横幅覆盖卡片标题（遮挡区 y≈328–527），点击点下移到卡片下半部。
            cx, cy = fragment_center(entry)
            context.click_frame_point(YUANDING_SCHEDULE_SCENE_ID, cx, min(cy + 45.0, 560.0))
            context.clear_frame()
            match = yield from context.wait_scene(
                [YUANDING_INTRO_SCENE_ID, YUANDING_MAIN_SCENE_ID],
                wait=page_timeout * 2,
                label=f"{YUANDING_RESOURCE_UNIT_LABEL}：等待活动说明层",
            )
            scene = self._require_scene(
                match, {YUANDING_INTRO_SCENE_ID, YUANDING_MAIN_SCENE_ID},
                label=f"{YUANDING_RESOURCE_UNIT_LABEL}：活动入口落点",
            )
        if scene == YUANDING_INTRO_SCENE_ID:
            frame = context.cur_frame(update=True)
            details = exact_fragment(context.ocr_fragments(frame), "查看详情")
            if details is None:
                raise RuntimeError(f"{YUANDING_RESOURCE_UNIT_LABEL}：说明层未唯一识别到「查看详情」")
            temp = context.view(YUANDING_INTRO_SCENE_ID)
            cx, cy = fragment_center(details)
            context.click_frame_point(YUANDING_INTRO_SCENE_ID, cx, cy)
            context.clear_frame()
            del temp
        match = yield from context.wait_scene(
            [YUANDING_MAIN_SCENE_ID],
            wait=page_timeout * 2,
            label=f"{YUANDING_RESOURCE_UNIT_LABEL}：等待活动主页",
        )
        self._require_scene(match, YUANDING_MAIN_SCENE_ID, label=f"{YUANDING_RESOURCE_UNIT_LABEL}：活动主页")

    def _yuanding_task_reward_badge_visible(self, context: Any) -> bool:
        """任务页左上「任务」标题旁的「领」角标存在=还有可领档位。"""

        frame = context.cur_frame(update=True)
        text = context.ocr_text_in_shapes(
            YUANDING_TASK_PAGE_SCENE_ID,
            ("任务标题",),
            padding=0,
            frame_data_url=frame,
            crop=True,
        )
        return "领" in re.sub(r"\s+", "", str(text or ""))

    def _yuanding_claim_task_rewards(
        self, context: Any, stop_event: Any, *, page_timeout: float,
    ) -> Iterator[Any]:
        """进入任务页并领奖；本期已领完（无「领」角标）时直接跳过，保持幂等。"""

        self._raise_if_stopped(stop_event)
        context.click_shape_center(YUANDING_MAIN_SCENE_ID, "任务")
        context.clear_frame()
        match = yield from context.wait_scene(
            [YUANDING_TASK_PAGE_SCENE_ID],
            wait=page_timeout * 2,
            label=f"{YUANDING_RESOURCE_UNIT_LABEL}：等待任务页",
        )
        self._require_scene(match, YUANDING_TASK_PAGE_SCENE_ID, label=f"{YUANDING_RESOURCE_UNIT_LABEL}：任务页")
        if not self._yuanding_task_reward_badge_visible(context):
            skipped = True
        else:
            skipped = False
            yield from claim_task_rows_by_ocr(
                context,
                scene_id=YUANDING_TASK_PAGE_SCENE_ID,
                first_row_shape="首条任务领取区",
                observer_shape="第三行任务标题",
                label=f"{YUANDING_RESOURCE_UNIT_LABEL}_任务奖励",
                click_settle_seconds=2.0,
            )
        self._raise_if_stopped(stop_event)
        # 任务页左下「返回」会直接退出活动回到 #66 日程，因此统一用底部「榜单」页签回活动主页。
        context.click_shape_center(YUANDING_TASK_PAGE_SCENE_ID, "榜单")
        context.clear_frame()
        match = yield from context.wait_scene(
            [YUANDING_MAIN_SCENE_ID],
            wait=page_timeout * 2,
            label=f"{YUANDING_RESOURCE_UNIT_LABEL}：任务页回活动主页",
        )
        self._require_scene(match, YUANDING_MAIN_SCENE_ID, label=f"{YUANDING_RESOURCE_UNIT_LABEL}：任务页回主页落点")

    def _yuanding_auto_marry_running(self, context: Any) -> bool:
        frame = context.cur_frame(update=True)
        text = fragment_text(context.ocr_fragments(frame))
        return "自动联姻中" in text or "停止自动" in text

    def _yuanding_wait_auto_marry_consumption(
        self, context: Any, stop_event: Any, *, budget_seconds: float,
    ) -> Iterator[Any]:
        """让自动联姻先消耗候选池：池空（空空如也）或计数长时间不动或超预算即返回。

        运行中不允许离开页面，所以本单元的做法是「开自动 → 跑一会儿 → 停止自动 → 回 #34」。
        计数长时间不动的真实原因通常是候选池耗尽（面板显示「空空如也」），不是需要点继续。
        """

        deadline = time.monotonic() + max(0.0, float(budget_seconds))
        last_count = None
        stable = 0
        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            frame = context.cur_frame(update=True)
            text = fragment_text(context.ocr_fragments(frame))
            if "空空如也" in text:
                return {"reason": "candidate_pool_empty", "count": last_count}
            match = re.search(r"已完成联姻次数[：: ]*(\d+)", text)
            count = int(match.group(1)) if match else last_count
            if count is not None and count == last_count:
                stable += 1
            else:
                stable = 0
            last_count = count
            if stable >= 8:
                return {"reason": "count_stalled", "count": last_count}
            yield from context.wait_action_settle(7.0)
        return {"reason": "budget_elapsed", "count": last_count}

    def _yuanding_stop_auto_marry(
        self, context: Any, stop_event: Any, *, page_timeout: float,
    ) -> Iterator[Any]:
        """运行态收尾：先停止自动联姻，之后才能离场。

        游戏在自动联姻运行中不允许其它操作：点任何别的地方都会弹
        「自动联姻过程中不允许进行其他操作 / 是否停止自动联姻？」。因此离场的正确顺序是
        先点「停止自动」（或在该弹窗上点「确定」，效果等价），再关面板回弟子居。
        """

        for _attempt in range(4):
            self._raise_if_stopped(stop_event)
            frame = context.cur_frame(update=True)
            text = fragment_text(context.ocr_fragments(frame))
            if "是否停止自动联姻" in text:
                cx, cy = AUTO_MARRY_STOP_CONFIRM_OK_TAP
                context.click_frame_point(YUANDING_CONFERENCE_SCENE_ID, cx, cy)
                context.clear_frame()
                yield from context.wait_action_settle(2.0)
                continue
            if not self._yuanding_auto_marry_running(context):
                return False
            context.click_shape_center(YUANDING_CONFERENCE_SCENE_ID, "停止自动")
            context.clear_frame()
            yield from context.wait_action_settle(2.0)
        frame = context.cur_frame(update=True)
        text = fragment_text(context.ocr_fragments(frame))
        if "是否停止自动联姻" in text or "自动联姻中" in text:
            raise RuntimeError(f"{YUANDING_RESOURCE_UNIT_LABEL}：自动联姻未能停下，保留现场")
        return True

    def _yuanding_align_auto_marry_config(self, context: Any, stop_event: Any) -> None:
        """把自动联姻设置面板对齐到推荐配置，并把数量拉到上限。"""

        for name, box, target in AUTO_MARRY_CHECKBOX_TARGETS:
            state = None
            for _attempt in range(3):
                self._raise_if_stopped(stop_event)
                frame = context.cur_frame(update=True)
                state = _checkbox_checked(frame, box)
                if state is None:
                    yield from context.wait_action_settle(0.6)
                    continue
                if state == target:
                    state = target
                    break
                x, y, w, h = box
                context.click_frame_point(YUANDING_AUTO_MARRY_SCENE_ID, x + w / 2.0, y + h / 2.0)
                context.clear_frame()
                yield from context.wait_action_settle(1.0)
            if state is None:
                raise RuntimeError(f"{YUANDING_RESOURCE_UNIT_LABEL}：勾选框「{name}」状态读不到")

        # 数量拉满：连点加号直到数字不再增长（已是上限时点击无副作用）。
        previous = None
        for _attempt in range(12):
            self._raise_if_stopped(stop_event)
            frame = context.cur_frame(update=True)
            text = context.ocr_text_in_shapes(
                YUANDING_AUTO_MARRY_SCENE_ID,
                ("自动联姻弟子个数",),
                padding=0,
                frame_data_url=frame,
                crop=True,
            )
            numbers = re.findall(r"\d+", str(text or ""))
            current = int(numbers[-1]) if numbers else None
            if current is not None and previous is not None and current == previous:
                break
            previous = current
            x, y = AUTO_MARRY_PLUS_BUTTON
            context.click_frame_point(YUANDING_AUTO_MARRY_SCENE_ID, x, y)
            context.clear_frame()
            yield from context.wait_action_settle(0.8)

    def _yuanding_start_auto_marry(
        self, context: Any, stop_event: Any, *, page_timeout: float,
        auto_marry_seconds: float = 180.0,
    ) -> Iterator[Any]:
        """从活动主页进入弟子居/联姻大会，按推荐配置开启自动联姻（已开启则跳过）。"""

        context.click_shape_center(YUANDING_MAIN_SCENE_ID, "前往联姻")
        context.clear_frame()
        match = yield from context.wait_scene(
            [YUANDING_DIZIJU_SCENE_ID, YUANDING_CONFERENCE_SCENE_ID],
            wait=page_timeout * 2,
            label=f"{YUANDING_RESOURCE_UNIT_LABEL}：等待弟子居",
        )
        self._require_scene(
            match, {YUANDING_DIZIJU_SCENE_ID, YUANDING_CONFERENCE_SCENE_ID},
            label=f"{YUANDING_RESOURCE_UNIT_LABEL}：前往联姻落点",
        )
        match = yield from context.wait_scene(
            [YUANDING_DIZIJU_SCENE_ID, YUANDING_CONFERENCE_SCENE_ID],
            wait=page_timeout,
            required=False,
        )
        scene = int(getattr(match, "id", match) or 0)
        if scene == YUANDING_DIZIJU_SCENE_ID:
            context.click_shape_center(YUANDING_DIZIJU_SCENE_ID, "联姻大会入口")
            context.clear_frame()
            match = yield from context.wait_scene(
                [YUANDING_CONFERENCE_SCENE_ID],
                wait=page_timeout * 2,
                label=f"{YUANDING_RESOURCE_UNIT_LABEL}：等待联姻大会",
            )
            self._require_scene(
                match, YUANDING_CONFERENCE_SCENE_ID,
                label=f"{YUANDING_RESOURCE_UNIT_LABEL}：联姻大会入口落点",
            )
        if self._yuanding_auto_marry_running(context):
            started = False
        else:
            started = True
            # 自动联姻开关落在道具/公告横幅遮挡带内，横幅出现时点击会被吞 → 允许重试。
            for _attempt in range(3):
                self._raise_if_stopped(stop_event)
                context.click_shape_center(YUANDING_CONFERENCE_SCENE_ID, "自动联姻")
                context.clear_frame()
                match = yield from context.wait_scene(
                    [YUANDING_AUTO_MARRY_SCENE_ID, YUANDING_CONFERENCE_SCENE_ID],
                    wait=page_timeout,
                    required=False,
                )
                scene_now = self._require_scene(
                    match, {YUANDING_AUTO_MARRY_SCENE_ID, YUANDING_CONFERENCE_SCENE_ID},
                    label=f"{YUANDING_RESOURCE_UNIT_LABEL}：点自动联姻前场景",
                )
                if scene_now == YUANDING_AUTO_MARRY_SCENE_ID:
                    break
            else:
                raise RuntimeError(f"{YUANDING_RESOURCE_UNIT_LABEL}：未能打开自动联姻设置面板")
            yield from self._yuanding_align_auto_marry_config(context, stop_event)
            self._raise_if_stopped(stop_event)
            context.click_shape_center(YUANDING_AUTO_MARRY_SCENE_ID, AUTO_MARRY_START_SHAPE)
            context.clear_frame()
            match = yield from context.wait_scene(
                [YUANDING_AUTO_CONFIRM_SCENE_ID, YUANDING_CONFERENCE_SCENE_ID],
                wait=page_timeout,
                required=False,
            )
            scene_now = self._require_scene(
                match, {YUANDING_AUTO_CONFIRM_SCENE_ID, YUANDING_CONFERENCE_SCENE_ID},
                label=f"{YUANDING_RESOURCE_UNIT_LABEL}：开启自动后的落点",
            )
            if scene_now == YUANDING_AUTO_CONFIRM_SCENE_ID:
                # 可选节点：勾「本次登录不再提示」后确认，之后不再出现。
                cx, cy = AUTO_MARRY_CONFIRM_CHECKBOX_TAP
                context.click_frame_point(YUANDING_AUTO_CONFIRM_SCENE_ID, cx, cy)
                context.clear_frame()
                yield from context.wait_action_settle(1.0)
                cx, cy = AUTO_MARRY_CONFIRM_OK_TAP
                context.click_frame_point(YUANDING_AUTO_CONFIRM_SCENE_ID, cx, cy)
                context.clear_frame()
                match = yield from context.wait_scene(
                    [YUANDING_CONFERENCE_SCENE_ID],
                    wait=page_timeout,
                    label=f"{YUANDING_RESOURCE_UNIT_LABEL}：确认后回到联姻大会",
                )
                self._require_scene(
                    match, YUANDING_CONFERENCE_SCENE_ID,
                    label=f"{YUANDING_RESOURCE_UNIT_LABEL}：确认弹窗后落点",
                )
            if not self._yuanding_auto_marry_running(context):
                raise RuntimeError(f"{YUANDING_RESOURCE_UNIT_LABEL}：已点击开启自动但未进入自动联姻运行态")

        # 关闭联姻大会面板：只有面板外顶部背景有效（左右边缘无效）。
        consumption = yield from self._yuanding_wait_auto_marry_consumption(
            context, stop_event, budget_seconds=auto_marry_seconds,
        )
        yield from self._yuanding_stop_auto_marry(context, stop_event, page_timeout=page_timeout)
        closed = False
        for _attempt in range(3):
            self._raise_if_stopped(stop_event)
            cx, cy = AUTO_MARRY_BACKGROUND_TAP
            context.click_frame_point(YUANDING_CONFERENCE_SCENE_ID, cx, cy)
            context.clear_frame()
            match = yield from context.wait_scene(
                [YUANDING_DIZIJU_SCENE_ID, YUANDING_CONFERENCE_SCENE_ID],
                wait=page_timeout,
                required=False,
            )
            scene_now = self._require_scene(
                match, {YUANDING_DIZIJU_SCENE_ID, YUANDING_CONFERENCE_SCENE_ID},
                label=f"{YUANDING_RESOURCE_UNIT_LABEL}：关闭面板落点",
            )
            if scene_now == YUANDING_DIZIJU_SCENE_ID:
                closed = True
                break
        if not closed:
            # 兜底：面板外的弟子居左下返回箭头，直接退到仙府（等价于离开本页）。
            context.click_frame_point(YUANDING_CONFERENCE_SCENE_ID, 76, 1500)
            context.clear_frame()
            match = yield from context.wait_scene(
                [YUANDING_XIANFU_SCENE_ID, YUANDING_DIZIJU_SCENE_ID],
                wait=page_timeout * 2,
                required=False,
            )
            self._require_scene(
                match, {YUANDING_XIANFU_SCENE_ID, YUANDING_DIZIJU_SCENE_ID},
                label=f"{YUANDING_RESOURCE_UNIT_LABEL}：关面板兜底落点",
            )
        return {"auto_marry_started": started, "consumption": consumption}

    def _yuanding_leave_to_world(
        self, context: Any, stop_event: Any, *, page_timeout: float,
    ) -> Iterator[Any]:
        """收尾：弟子居 → 仙府 → 世界 #34（使用资源的显式终态）。"""

        self._raise_if_stopped(stop_event)
        match = yield from context.wait_scene(
            [YUANDING_DIZIJU_SCENE_ID, YUANDING_CONFERENCE_SCENE_ID, YUANDING_XIANFU_SCENE_ID],
            wait=page_timeout,
            required=False,
        )
        scene = int(getattr(match, "id", match) or 0)
        if scene == YUANDING_CONFERENCE_SCENE_ID:
            cx, cy = AUTO_MARRY_BACKGROUND_TAP
            context.click_frame_point(YUANDING_CONFERENCE_SCENE_ID, cx, cy)
            context.clear_frame()
            yield from context.wait_scene(
                [YUANDING_DIZIJU_SCENE_ID],
                wait=page_timeout,
                label=f"{YUANDING_RESOURCE_UNIT_LABEL}：关闭联姻大会面板",
            )
            scene = YUANDING_DIZIJU_SCENE_ID
        if scene == YUANDING_DIZIJU_SCENE_ID:
            context.click_shape_center(YUANDING_DIZIJU_SCENE_ID, "返回")
            context.clear_frame()
            match = yield from context.wait_scene(
                [YUANDING_XIANFU_SCENE_ID],
                wait=page_timeout * 2,
                label=f"{YUANDING_RESOURCE_UNIT_LABEL}：弟子居返回仙府",
            )
            scene = self._require_scene(
                match, YUANDING_XIANFU_SCENE_ID,
                label=f"{YUANDING_RESOURCE_UNIT_LABEL}：弟子居返回落点",
            )
        if scene == YUANDING_XIANFU_SCENE_ID:
            context.click_shape_center(YUANDING_XIANFU_SCENE_ID, "离开")
            context.clear_frame()
        match = yield from context.wait_scene(
            [YUANDING_WORLD_SCENE_ID],
            wait=page_timeout * 2,
            label=f"{YUANDING_RESOURCE_UNIT_LABEL}：返回世界",
        )
        self._require_scene(match, YUANDING_WORLD_SCENE_ID, label=f"{YUANDING_RESOURCE_UNIT_LABEL}：世界终态")

    def _execute_yuanding_sansheng_resource_unit(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None = None,
    ) -> Iterator[Any]:
        """缘定三生正式运行单元入口：任务奖励 + 使用资源，最后回 #34。"""

        payload = dict(payload or {})
        payload.pop("__scheduler_task_id", None)
        context = self._behavior_tree_context(ctx, ctx["asset_tree_path"], stop_event=stop_event)
        page_timeout = float(payload.get("page_timeout_seconds") or 20.0)
        skip_task_rewards = bool(payload.get("task_rewards_done"))

        yield from self._yuanding_open_activity_home(context, stop_event, page_timeout=page_timeout)
        if skip_task_rewards:
            task_reward_skipped = True
        else:
            task_reward_skipped = False
            yield from self._yuanding_claim_task_rewards(context, stop_event, page_timeout=page_timeout)
        marry_result = yield from self._yuanding_start_auto_marry(
            context,
            stop_event,
            page_timeout=page_timeout,
            auto_marry_seconds=float(payload.get("auto_marry_seconds") or 180.0),
        )
        yield from self._yuanding_leave_to_world(context, stop_event, page_timeout=page_timeout)
        return {
            "result": "success",
            "outcome": "resource_unit_done",
            "task_rewards_skipped": task_reward_skipped,
            "auto_marry_started": bool(marry_result.get("auto_marry_started")),
            "current_scene": YUANDING_WORLD_SCENE_ID,
            "message": (
                f"{YUANDING_RESOURCE_UNIT_LABEL}：任务奖励"
                + ("（本期已领完，跳过）" if task_reward_skipped else "已处理")
                + ("；已开启自动联姻" if marry_result.get("auto_marry_started") else "；自动联姻本来就在运行")
                + "；已回到 #34"
            ),
        }


__all__ = [
    "AUTO_MARRY_CHECKBOX_TARGETS",
    "YUANDING_AUTO_CONFIRM_SCENE_ID",
    "YUANDING_AUTO_MARRY_SCENE_ID",
    "YUANDING_CONFERENCE_SCENE_ID",
    "YUANDING_DIZIJU_SCENE_ID",
    "YUANDING_MARRY_RESULT_SCENE_ID",
    "YUANDING_RESOURCE_UNIT_HOURS",
    "YUANDING_RESOURCE_UNIT_LABEL",
    "YUANDING_RESOURCE_UNIT_TASK_ID",
    "YUANDING_RESOURCE_UNIT_TASK_TYPE",
    "YUANDING_TASK_PAGE_SCENE_ID",
    "YuandingSanshengRunMixin",
]