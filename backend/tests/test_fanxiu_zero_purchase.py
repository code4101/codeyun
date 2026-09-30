from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from backend.core.fanxiu.data_annotation.tasks.zero_purchase import return_state
from backend.core.fanxiu.data_annotation.tasks.theme_collection import read_theme_collection_plan


def test_return_state_requires_direct_evidence():
    assert return_state("领取", "消耗返还进度：1/5") == ("claimable", 1, 5)
    assert return_state("10时26分可领", "消耗返还进度：2/5") == ("waiting", 2, 5)
    assert return_state("已领完", "消耗返还进度：5/5") == ("complete", 5, 5)
    for button, progress in [("", "2/5"), ("购买", "消耗返还进度：1/5"), ("", "消耗返还进度：2/5")]:
        with pytest.raises(ValueError):
            return_state(button, progress)


@pytest.mark.parametrize('base_id,member', [(701101, 'zero-purchase'), (11640000, 'national-celebration')])
def test_menu_period_drives_new_instance_and_daily_completion(base_id, member):
    tz = ZoneInfo("Asia/Shanghai")
    start = datetime(2026, 9, 28, 0, 0, 10, tzinfo=tz)
    end = datetime(2026, 10, 5, 23, 59, 50, tzinfo=tz)
    def plan(activity_id, now, completed=()):
        return read_theme_collection_plan(
            plan={"status": "ready", "occurrences": [], "activity_observations": []},
            now=now,
            menu_reader=lambda: SimpleNamespace(complete=True, items=[SimpleNamespace(
                activity_id=activity_id, base_id=base_id,
            )]),
            xianyuan_reader=lambda **_: {"available": True, "occurrences": []},
            period_reader=lambda _: {"complete": True, "start_time_ms": int(start.timestamp()*1000),
                                     "end_time_ms": int(end.timestamp()*1000)},
            completion_reader=lambda: completed,
        )
    now = datetime(2026, 9, 30, 13, tzinfo=tz)
    first = plan(11710001, now)
    stage, = first["due_stages"]
    assert stage["member_id"] == member
    key = tuple(stage[k] for k in ("instance_key", "member_id", "kind", "business_date"))
    done = plan(11710001, now, [key])
    assert not done["due_stages"]
    assert done["next_time"] == "2026-10-01 00:00:00"
    assert plan(11710002, now, [key])["due_stages"]
    assert plan(11710001, datetime(2026, 10, 1, 1, tzinfo=tz), [key])["due_stages"]
    assert not plan(11710001, datetime(2026, 10, 6, tzinfo=tz), [key])["due_stages"]


def test_partner_upgrade_parse_does_not_turn_unknown_into_zero():
    from backend.core.fanxiu.data_annotation.tasks.national_celebration import parse_upgrade_state
    assert parse_upgrade_state('0/20', '30') == (0, 20, 30)
    for material, level in [('', '30'), ('20/0', '30'), ('20/20', '')]:
        with pytest.raises(ValueError):
            parse_upgrade_state(material, level)
