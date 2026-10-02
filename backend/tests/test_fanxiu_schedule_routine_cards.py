from backend.core.fanxiu.instrumentation.schedule_cards import routine_schedule_card
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
import pytest


@pytest.mark.parametrize("activity_id,function_id", [(-1,12002),(-2,12007)])
def test_native_routine_card_keeps_carousel_slot_without_occurrence(activity_id,function_id):
    row=routine_schedule_card(
        {"activityId":activity_id,"activityType":activity_id,"state":2,"isShow":False,"countdown":1000},
        index=3, definitions=[{"functionId":function_id,"name_plain":"boss"}])
    assert row["index"]==3
    assert row["key"]==f"routine:{function_id}"
    assert row["runtime_id"] is None and row["start_time"] is None
    assert row["title"]=="boss"


def test_other_activity_id_is_not_a_routine_card():
    assert routine_schedule_card({"activityId":16090004},index=0,definitions=[]) is None
    assert routine_schedule_card({"activityId":-3},index=0,definitions=[]) is None


def test_routine_type_conflict_is_rejected():
    with pytest.raises(FanxiuRuntimeMemoryError):
        routine_schedule_card({"activityId":-1,"activityType":-2},index=0,definitions=[])
