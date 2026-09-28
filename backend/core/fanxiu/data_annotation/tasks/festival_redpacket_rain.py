"""鸿运红包雨：独立于 Redbag UID 的发言领奖事务。"""
import time
from ...instrumentation.festival_speech import read_festival_speech_snapshot
from ...instrumentation.chat import read_repeated_chat_phrase
from ...client.mumu_control import keyevents_mumu_adb, text_mumu_adb


def claim_visible_festival_redpacket_rain(context):
    """处理已进入的红包雨聊天或世界页可见入口；每期 answer>0 幂等跳过。

    仅发送当前44频道 Runtime 共识话术一次。奖励由原生动画自动结算，
    不点击普通红包的“开”，不把普通红包空队列当作本活动完成。
    """
    current = yield from context.wait_scene([887, 34], wait=5, required=False)
    scene = current.scene_id if current else None
    if scene != 887:
        if scene != 34:
            return None
        text = context.ocr_text_in_shapes(34, ('红包雨状态',), crop=True, padding=0)
        if '红包雨' not in text:
            return None
    before = read_festival_speech_snapshot()
    if before['answer'] is None:
        raise RuntimeError('红包雨服务端参与状态未同步，不发送')
    if before['answer'] > 0:
        if scene == 887:
            context.click_shape_center(887, '返回')
            yield from context.go_scene(34)
        return {'status': 'complete', 'sent': False, 'answer': before['answer'], 'reason': 'already_claimed'}
    if scene != 887:
        context.click_shape_center(34, '红包雨入口')
        current = yield from context.wait_scene([887], wait=15)
        if current.scene_id != 887:
            raise RuntimeError('红包雨入口未进入专用聊天')
    ready = read_festival_speech_snapshot()
    channels = [row for row in ready['channels'] if str(row['channel']).startswith('44_')]
    if len(channels) != 1 or ready['answer'] != 0:
        raise RuntimeError('红包雨发送前频道/参与状态改变')
    channel = channels[0]
    phrase = read_repeated_chat_phrase(44, int(channel['sub_channel_id']))
    if not phrase['ready']:
        raise RuntimeError('红包雨未形成唯一 Runtime 话术')
    # Send once only after the current exact activity channel has been proved.
    context.click_shape_center(887, '输入框')
    yield from context.wait_action_settle(.5)
    keyevents_mumu_adb(['KEYCODE_MOVE_END', *['KEYCODE_DEL' for _ in range(64)]])
    text_mumu_adb(phrase['phrase'])
    yield from context.wait_action_settle(.5)
    context.click_shape_center_fast(887, '发送')  # 收起输入层
    yield from context.wait_action_settle(.8)
    context.click_shape_center_fast(887, '发送')
    deadline = time.monotonic() + 90
    while True:
        yield from context.wait_action_settle(3)
        after = read_festival_speech_snapshot()
        if (after['pid'], after['process_start_ticks']) != (ready['pid'], ready['process_start_ticks']):
            raise RuntimeError('红包雨发送后进程改变')
        if after['answer'] > 0:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('红包雨发送后未形成服务端参与终态，不重发')
    # Falling bags finish themselves; allow the short native reward sequence.
    yield from context.wait_action_settle(20)
    landed = yield from context.wait_scene([887], wait=20)
    if landed.scene_id != 887:
        raise RuntimeError('红包雨奖励动画尚未返回聊天，保留现场')
    context.click_shape_center(887, '返回')
    yield from context.go_scene(34)
    return {'status': 'complete', 'sent': True, 'answer': after['answer'],
            'channel': channel, 'phrase': phrase['phrase']}
