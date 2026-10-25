"""Typography normalization from actual SDK OCR; no simulated game flow."""
import pytest
from backend.core.fanxiu.data_annotation.tasks.bubble_claim_pills import normalize_bubble_gift_identity


@pytest.mark.parametrize("text", [
    "（黄金）玩心每周礼包V7-V16可领",
    "(黄金)玩心每周礼包V7-V16可领",
    "（黄金） 玩心每周礼包 V7-V16 可领\n",
])
def test_same_sdk_gift_ignores_only_typography(text):
    assert normalize_bubble_gift_identity(text) == "(黄金)玩心每周礼包V7-V16可领"


@pytest.mark.parametrize("other", [
    "(白银)玩心每周礼包V0-V16可领",
    "(钻石)玩心每周礼包V10-V16可领",
    "(黄金)玩心新手礼包V7-V16可领",
    "(黄金)玩心每周礼包V8-V16可领",
])
def test_distinct_gift_semantics_are_preserved(other):
    assert normalize_bubble_gift_identity(other) != normalize_bubble_gift_identity("（黄金）玩心每周礼包V7-V16可领")
