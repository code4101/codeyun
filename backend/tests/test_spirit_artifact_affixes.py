import pytest

from backend.core.fanxiu.instrumentation.spirit_artifact_affixes import classify_spirit_artifact_affix


@pytest.mark.parametrize("value,tier,affix", [
    (14999, 0, ""), (15000, 1, "满"), (15001, 2, "巅"),
    (18000, 2, "巅"), (22499, 2, "巅"), (22500, 3, "巅"),
])
def test_client_threshold_boundaries(value, tier, affix):
    result = classify_spirit_artifact_affix(value, 15000, 150)
    assert (result["max_type"], result["affix"]) == (tier, affix)
    assert result["peak_max"] == 22500


def test_full_flag_hides_icon_even_at_maximum():
    result = classify_spirit_artifact_affix(15000, 15000, 150, full=1)
    assert result["max_type"] == 1
    assert result["affix"] == result["affix_icon"] == ""
    assert result["affix_visible"] is False
