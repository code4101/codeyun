"""Pure occurrence/time-format contract; no game simulation."""
from datetime import datetime
import pytest
from backend.core.fanxiu.data_annotation.tasks.celebration_trade_auto import next_celebration_trade_checkpoint


@pytest.mark.parametrize('hour,minute,expected', [
    (12, 0, '2026-10-13 20:45:00'),
    (20, 45, '2026-10-13 21:45:00'),
    (21, 44, '2026-10-13 21:45:00'),
    (21, 45, None),
    (23, 59, None),
])
def test_checkpoint_stays_in_occurrence_and_uses_scheduler_format(hour, minute, expected):
    assert next_celebration_trade_checkpoint(datetime(2026, 10, 13, hour, minute)) == expected
