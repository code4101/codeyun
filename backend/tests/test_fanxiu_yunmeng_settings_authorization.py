"""Persisted native settings cannot grant authorization to consume refill items."""
from dataclasses import replace

import pytest

from backend.core.fanxiu.data_annotation.tasks.yunmeng_native_auto import (
    YunmengNativeAutoRequest,
    YunmengNativeAutoSettings,
    validate_yunmeng_reused_settings,
)


SAFE_SETTINGS = YunmengNativeAutoSettings(
    requested_challenges=10, use_high_power_boost=False, use_score_boost=False,
    use_chase_sword=False, skip_battle=True, auto_refill_stamina=False,
    fast_auto=True, skip_animation=True,
)


def test_previous_refill_setting_requires_current_request_authorization():
    previous = replace(SAFE_SETTINGS, auto_refill_stamina=True)
    request = YunmengNativeAutoRequest(requested_challenges=10)
    with pytest.raises(RuntimeError, match="锁定设置"):
        validate_yunmeng_reused_settings(request, previous)
    validate_yunmeng_reused_settings(replace(request, auto_refill_stamina=True), previous)
    validate_yunmeng_reused_settings(request, SAFE_SETTINGS)


@pytest.mark.parametrize("field", ["skip_battle", "fast_auto", "skip_animation"])
def test_consumable_authorization_does_not_bypass_required_modes(field):
    request = YunmengNativeAutoRequest(requested_challenges=10, auto_refill_stamina=True)
    with pytest.raises(RuntimeError, match="锁定设置"):
        validate_yunmeng_reused_settings(request, replace(SAFE_SETTINGS, **{field: False}))
