"""Shared daily conversation policy for every WeChat business group.

Business domains supply scope only; they never implement their own clock,
rollover or title rules. The service owns persisted threads and pending work.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
ROLLOVER_QUESTION_GAP_SECONDS = 2 * 60 * 60
GROUP_DOMAINS = {"attendance": "考勤群", "fanxiu": "凡修群", "general": "CodeYun 群聊"}


def should_roll_session(session_day: str | None, last_question_at: float | None, now: float) -> bool:
    """Reuse all day; after midnight defer rollover until a two-hour question gap.

This only determines eligibility. Group chatter does not create a new thread;
the service first confirms relevant input and carries pending work forward.
"""
    if not session_day:
        return True
    return (datetime.fromtimestamp(now, SHANGHAI).date().isoformat() != session_day
            and (last_question_at is None or now - last_question_at >= ROLLOVER_QUESTION_GAP_SECONDS))


def session_title(hook: dict, day: str) -> str:
    """The same title contract applies on initial creation and daily rollover."""
    label = ("CodeYun 私聊" if hook.get("kind") == "owner_private"
             else GROUP_DOMAINS[hook.get("domain", "attendance")])
    return f"{label} {hook.get('name') or hook['chat_id']} {day}"
