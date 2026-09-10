"""Evidence-version and atomic cache contracts, independent of AI/gameplay."""
import pytest

from backend.core.fanxiu.catalog.cultivation_rules import (
    load_cultivation_rule, save_cultivation_rule, cultivation_document_batches, merge_cultivation_rules,
)
from backend.core.fanxiu.activity.cultivation_choice import CultivationChoiceError


def test_rule_cache_is_bound_to_documents_and_dimensions(tmp_path):
    bundle = {"item_id": 123, "name": "example", "fingerprint": "version-one", "catalog": {}}
    keys = {"pet:1:level"}
    rule = {"item_id": 123, "milestones": [{"targets": [{"key": "pet:1:level", "level": 8}],
        "category": "recurring_resources", "phase": "entry_milestone", "summary": "income at 8",
        "sources": ["catalog:123"]}]}
    assert load_cultivation_rule(bundle, allowed_keys=keys, directory=tmp_path) is None
    saved = save_cultivation_rule(bundle, rule, allowed_keys=keys, directory=tmp_path)
    assert load_cultivation_rule(bundle, allowed_keys=keys, directory=tmp_path) == saved
    assert load_cultivation_rule({**bundle, "fingerprint": "changed-image"}, allowed_keys=keys, directory=tmp_path) is None
    assert load_cultivation_rule(bundle, allowed_keys={"pet:1:pin"}, directory=tmp_path) is None
    assert list(tmp_path.glob("*.tmp")) == []


def test_unknown_rules_are_not_cached_as_success(tmp_path):
    bundle = {"item_id": 123, "name": "example", "fingerprint": "version-one", "catalog": {}}
    with pytest.raises(CultivationChoiceError, match="没有明确"):
        save_cultivation_rule(bundle, {"item_id": 123, "milestones": [], "limitations": "missing"},
                              allowed_keys={"pet:1:level"}, directory=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_image_batches_preserve_every_image_and_its_context():
    request = {"item_id": 1, "sources": [{"ref": "note:1", "text": "8阶"}] + [
        {"ref": f"image:{i}", "image_number": i, "preceding_text": str(i)} for i in range(1, 6)]}
    batches = cultivation_document_batches(request, ["a", "b", "c", "d", "e"])
    assert [len(images) for _, images in batches] == [2, 2, 1]
    assert [image for _, images in batches for image in images] == ["a", "b", "c", "d", "e"]
    assert [s["image_number"] for s in batches[1][0]["sources"] if "image_number" in s] == [1, 2]


def test_merge_same_milestone_unions_evidence_and_preserves_new_chain():
    common = {"targets": [{"key": "pet:1:level", "level": 8}], "category": "recurring_resources"}
    merged = merge_cultivation_rules(1, [{"item_id": 1, "milestones": [
        {**common, "phase": "increase", "summary": "weekly amount increases", "sources": ["a"]},
        {**common, "phase": "new_chain", "summary": "new weekly resource", "sources": ["b"]},
    ]}])
    assert len(merged["milestones"]) == 1
    assert merged["milestones"][0]["phase"] == "new_chain"
    assert merged["milestones"][0]["sources"] == ["a", "b"]
