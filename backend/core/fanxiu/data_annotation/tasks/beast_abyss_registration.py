"""兽渊开启前一天报名；游戏封面的组队事实承担幂等，不依赖上次执行步骤。"""
from datetime import timedelta

from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
from backend.core.fanxiu.data_annotation.schedule_navigation import select_schedule_activity


def register_beast_abyss(context, *, occurrence, now):
    """进入次日实例，免费创建默认团队或确认已组队，最终回世界。

    #860 报名封面、#861 团队列表、#862 创建表单均来自真实操作。
    提交后只等待组队证据，不在结果不明时再次点击创建。
    """
    local = now.astimezone(occurrence.start_at.tzinfo)
    if (occurrence.activity_type != "beast-abyss"
            or local.date() + timedelta(days=1) != occurrence.start_at.date()
            or local.hour < 5 or local < occurrence.prepare_at):
        raise ValueError("兽渊报名仅在开启前一天05:00后执行")
    match = yield from context.wait_scene([34, 66, 860, 861, 862], wait=8)
    scene = int(getattr(match, "id", match))
    if scene not in {860, 861, 862}:
        yield from context.go_scene(66)
        schedule = read_fanxiu_activity_runtime_schedule(allow_discovery=False, force_refresh=True)
        # 使用当前日程身份与日历格对齐；预告期到报名期的 Runtime row id 会变化。
        yield from select_schedule_activity(
            context, "兽渊探秘", day_offset=1, enter=True,
            runtime_schedule=schedule, require_runtime_alignment=True,
            expected_activity_id=occurrence.activity_id,
            expected_cross_count=occurrence.cross_count, now=local,
        )
        yield from context.wait_shape(860, "开启倒计时", timeout=20)
        scene = 860
    created = False
    if scene == 860:
        # 不把 OCR 阴性当作未组队：必须在同一稳定区域读到明确业务状态。
        state = ""
        for _ in range(8):
            frame = context.cur_frame(update=True)
            state = context.ocr_text_in_shapes(860, ["组队状态"], frame_data_url=frame)
            if "无法退出或解散团队" in state or "请道友组团参与" in state:
                break
            yield from context.wait_action_settle(0.5)
        else:
            raise RuntimeError("兽渊报名：组队状态未确认，保留现场")
        if "无法退出或解散团队" not in state:
            yield from context.wait_click_then_scene(860, "前往组团", 861, timeout=20)
            scene = 861
    if scene == 861:
        yield from context.wait_click_then_scene(861, "创建团队", 862, timeout=20)
        scene = 862
    if scene == 862:
        yield from context.wait_shape(862, "首次创建免费", timeout=8)
        yield from context.wait_click(862, "创建", timeout=8)
        yield from context.wait_shape(860, "已组队提示", timeout=20)
        created = True
    yield from context.wait_click_then_scene(860, "返回", 34, timeout=20)
    return {"status": "completed", "message": "兽渊报名完成，已返回世界",
            "created": created, "already_registered": not created}
