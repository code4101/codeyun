from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation.pet_aptitude import (
    _decode_aptitudes,
    _decode_gift_limits,
    _decode_pet_rows,
    _decode_pet_talent_tasks,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)


class _FakeReader:
    def fields(self, value):
        return dict(value or {})

    def dictionary_fields(self, value):
        return dict(value or {})

    def list_items(self, value):
        return list(value["rows"]), value.get("count")

    def long(self, value):
        return int(value) if isinstance(value, (int, float)) else None


def test_decode_pet_rows_preserves_authoritative_five_aptitudes():
    reader = _FakeReader()
    rows, count = _decode_pet_rows(
        reader,
        {
            "petInfoVOList": {
                "count": 1,
                "rows": [
                    {
                        "petId": 7101,
                        "level": 94,
                        "pin": 0,
                        "giftMap": {
                            1: 157478,
                            2: 157597,
                            3: 82912,
                            4: 142895,
                            5: 27340,
                        },
                    }
                ],
            }
        },
    )

    assert count == 1
    assert rows == [
        {
            "pet_id": 7101,
            "level": 94,
            "pin": 0,
            "aptitudes": {
                1: 157478,
                2: 157597,
                3: 82912,
                4: 142895,
                5: 27340,
            },
            "aptitude_total": 568222,
        }
    ]


def test_decode_aptitudes_fails_closed_when_one_type_is_missing():
    with pytest.raises(FanxiuRuntimeMemoryError, match="五项资质不完整"):
        _decode_aptitudes(_FakeReader(), {1: 1, 2: 2, 3: 3, 4: 4})


def test_decode_pet_rows_rejects_duplicate_pet_identity():
    pet = {
        "petId": 7101,
        "level": 94,
        "pin": 0,
        "giftMap": {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
    }
    with pytest.raises(FanxiuRuntimeMemoryError, match="petId 重复"):
        _decode_pet_rows(
            _FakeReader(),
            {"petInfoVOList": {"count": 2, "rows": [pet, pet]}},
        )


def test_decode_gift_limits_preserves_five_type_caps():
    assert _decode_gift_limits(
        _FakeReader(),
        {
            "_PetGiftLimitMap": {
                7101: {1: 600000, 2: 600000, 3: 300000, 4: 400000, 5: 500000}
            }
        },
    ) == {
        7101: {1: 600000, 2: 600000, 3: 300000, 4: 400000, 5: 500000}
    }


def test_decode_pet_talent_tasks_requires_complete_current_ladder():
    rows = []
    for offset, task_id in enumerate(range(804290154, 804290168), start=1):
        rows.append(
            {
                "taskId": task_id,
                "status": 3,
                "turn": 0,
                "progressList": {
                    "count": 1,
                    "rows": [
                        {
                            "progress": 0,
                            "target": 6000 if task_id == 804290164 else offset * 100,
                            "finish": False,
                        }
                    ],
                },
            }
        )
    decoded = _decode_pet_talent_tasks(
        _FakeReader(),
        {
            "taskInfoMap": {
                3: {
                    "taskEntryVOs": {"count": 14, "rows": rows},
                    "finishTasks": {"count": 0, "rows": []},
                }
            }
        },
    )

    assert decoded["matched_task_count"] == 14
    assert decoded["tasks"][10] == {
        "task_id": 804290164,
        "status": 3,
        "turn": 0,
        "progress": 0,
        "target": 6000,
        "finished": False,
    }
