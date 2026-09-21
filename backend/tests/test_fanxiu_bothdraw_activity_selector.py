from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation.bothdraw import select_bothdraw_activity
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)


def test_singleton_contract_is_preserved_without_expected_id() -> None:
    info = {"activity_id": 30402}
    activity_id, selected = select_bothdraw_activity({30402: info})

    assert activity_id == 30402
    assert selected is info


def test_singleton_contract_still_rejects_multiple_instances() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="当前活动实例不唯一"):
        select_bothdraw_activity({30402: {}, 102: {}})


def test_expected_id_selects_exact_instance_from_many() -> None:
    holy = {"activity_id": 30402}
    other = {"activity_id": 102}
    activity_id, selected = select_bothdraw_activity(
        {102: other, 30402: holy}, 30402
    )

    assert activity_id == 30402
    assert selected is holy


def test_expected_id_missing_fails_without_falling_back_to_first() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="未加载活动 30402"):
        select_bothdraw_activity({102: {"activity_id": 102}}, 30402)


def test_expected_id_key_identity_conflict_fails() -> None:
    # "30402" and 30402 are distinct Python dict keys but the same activity id.
    with pytest.raises(FanxiuRuntimeMemoryError, match="键身份冲突"):
        select_bothdraw_activity({"30402": {}, 30402: {}}, 30402)


def test_expected_id_must_be_positive() -> None:
    with pytest.raises(ValueError, match="expected_activity_id"):
        select_bothdraw_activity({30402: {}}, 0)
    with pytest.raises(ValueError, match="expected_activity_id"):
        select_bothdraw_activity({30402: {}}, -1)


def test_non_integer_keys_are_ignored_for_matching() -> None:
    holy = {"activity_id": 30402}
    activity_id, selected = select_bothdraw_activity(
        {"not-an-id": {}, "30402": holy}, 30402
    )

    assert activity_id == 30402
    assert selected is holy


def test_fractional_key_never_matches_exact_integer_id() -> None:
    holy = {"activity_id": 30402}
    with pytest.raises(FanxiuRuntimeMemoryError, match="未加载活动 30402"):
        select_bothdraw_activity({30402.5: holy}, 30402)


def test_integral_float_key_is_a_valid_normalization() -> None:
    holy = {"activity_id": 30402}
    activity_id, selected = select_bothdraw_activity({30402.0: holy}, 30402)

    assert activity_id == 30402
    assert selected is holy


def test_expected_id_accepts_integral_float() -> None:
    holy = {"activity_id": 30402}
    activity_id, selected = select_bothdraw_activity({30402: holy}, 30402.0)

    assert activity_id == 30402
    assert selected is holy


def test_expected_id_rejects_bool() -> None:
    with pytest.raises(ValueError, match="expected_activity_id"):
        select_bothdraw_activity({30402: {}}, True)


def test_expected_id_rejects_fractional_and_nan() -> None:
    with pytest.raises(ValueError, match="expected_activity_id"):
        select_bothdraw_activity({30402: {}}, 30402.5)
    with pytest.raises(ValueError, match="expected_activity_id"):
        select_bothdraw_activity({30402: {}}, float("nan"))
    with pytest.raises(ValueError, match="expected_activity_id"):
        select_bothdraw_activity({30402: {}}, float("inf"))


def test_singleton_with_non_integral_key_fails() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="精确正整数身份"):
        select_bothdraw_activity({30402.5: {"activity_id": 30402}})

