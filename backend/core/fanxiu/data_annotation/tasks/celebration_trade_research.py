"""商盟正式日程入口的只读取证，不交易、购买、攻击或赠礼。"""
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.schedule_navigation import select_schedule_activity
from backend.core.fanxiu.instrumentation.activity_runtime import read_activity_period_runtime_snapshot
from backend.core.fanxiu.instrumentation.celebration_trade import read_celebration_trade_main_panel


def inspect_celebration_trade_landing(context, *, activity_id: int, runtime_id: int):
    """Enter the exact live carousel occurrence and preserve its raw landing.

    The calendar selector binds Runtime ID, activity ID and cross count before
    its one 前往 click. An unannotated landing is observation evidence, not
    navigation success or permission to press the trade/map action button.
    """
    now = job_now()
    period = read_activity_period_runtime_snapshot(activity_id, runtime_id=runtime_id)
    yield from context.go_scene(66)
    target = yield from select_schedule_activity(context, '商盟争霸', enter=True,
        require_runtime_alignment=True, expected_activity_id=activity_id,
        expected_runtime_id=str(period['runtime_id']), expected_cross_count=8, now=now)
    yield from context.wait_action_settle(1.0)
    try:
        native = read_celebration_trade_main_panel(expected_activity_id=activity_id,
                    expected_runtime_id=runtime_id)
    except Exception as exc:
        native = dict(complete=False, reason=str(exc), consumption_authorized=False)
    # Native decoding can outlast the entry animation. Capture the resulting
    # page afterward rather than labelling the pre-load calendar as the landing.
    frame = context.cur_frame(update=True)
    return dict(activity_id=activity_id, period=period, selected_title=target.matched_text,
        landing_accepted=False, observation_only=True, native_snapshot=native,
        frame_data_url=frame, consumption_authorized=False)


def inspect_celebration_trade_main_panel(context, *, activity_id: int, runtime_id: int,
                                       return_to_world: bool = True):
    """Verify the real main page and exact native period; no map/trade click."""
    result = yield from inspect_celebration_trade_landing(context,
                activity_id=activity_id, runtime_id=runtime_id)
    yield from context.wait_scene_exact([959], timeout=30,
                label='商盟：等待已标注主页面加载')
    native = read_celebration_trade_main_panel(expected_activity_id=activity_id,
                expected_runtime_id=runtime_id)
    if native.get('complete') is not True:
        raise RuntimeError('商盟主页面原生周期未完整对齐')
    result.update(native_snapshot=native, landing_accepted=True,
                  frame_data_url=context.cur_frame(update=True), final_scene=959)
    if return_to_world:
        yield from context.go_scene(34)
        result['final_scene'] = 34
    return result
