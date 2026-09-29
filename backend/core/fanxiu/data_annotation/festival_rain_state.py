"""红包雨巡检：活动窗口 + 服务端未参与事实，只唤醒已有红包 Job。"""
from datetime import datetime

from backend.core.fanxiu.instrumentation.festival_speech import read_festival_rain_state


def classify_festival_rain_state(snapshot: dict, *, now_ms: int) -> dict:
    """Pure trigger policy; closed/unknown/completed windows never dispatch.

    ActivityMgr supplies the daily window (including seasonal end dates).
    Calendar guesses such as 'every day at 19:30 forever' are unnecessary.
    Scheduler retains ownership of running attempts and failure backoff.
    """
    activity = snapshot.get('activity') or {}
    start, end = activity.get('startTime'), activity.get('endTime')
    ready = bool(
        snapshot.get('ok') and snapshot.get('complete')
        and activity.get('baseId') == 190000 and activity.get('state') == 2
        and isinstance(start, int) and isinstance(end, int)
        and start <= now_ms < end
        and type(snapshot.get('answer')) is int and snapshot['answer'] == 0
    )
    return {
        'ok': bool(snapshot.get('ok')),
        'facts': {'festival_rain': {**snapshot, 'trigger_ready': ready}},
        'due_task_ids': ['daily-redpacket'] if ready else [],
    }


def inspect_festival_rain_game_state() -> dict:
    snapshot = read_festival_rain_state()
    return classify_festival_rain_state(snapshot, now_ms=int(datetime.now().timestamp() * 1000))
