from __future__ import annotations

import json
import re
from pathlib import Path


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "fanxiu"
    / "scene47_prompt_identity_replay.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _matches(sample: dict, *, title_pattern: str) -> bool:
    image_identity_matches = float(sample["imageIdentityScore"]) >= 70.0
    title_identity_matches = re.search(title_pattern, str(sample["roiOcrText"])) is not None
    return image_identity_matches and title_identity_matches


def test_scene47_prompt_title_typo_compatibility_is_bounded() -> None:
    fixture = _fixture()
    pattern = fixture["promptTitlePattern"]

    assert re.search(pattern, "提示") is not None
    assert re.search(pattern, "提宗") is not None
    assert re.search(pattern, "提") is None
    assert re.search(pattern, "宗") is None
    assert re.search(pattern, "使用") is None
    assert fixture["promptTitleRoi"]["w"] < 0.15
    assert fixture["promptTitleRoi"]["h"] < 0.06


def test_scene47_real_positive_and_storage_quantity_negatives() -> None:
    fixture = _fixture()
    outcomes = {
        sample["name"]: _matches(sample, title_pattern=fixture["promptTitlePattern"])
        for sample in fixture["replays"]
    }

    assert outcomes == {
        "0047-real-positive": True,
        "0319-storage-use-quantity-negative": False,
        "0584-storage-use-reference-negative": False,
    }


def test_scene47_keeps_image_and_prompt_title_as_two_required_identities() -> None:
    fixture = _fixture()

    assert fixture["assetIdentityShapeIds"] == [
        "shape-1780063512651-8f76a94d06f1b",
        "shape-47-title-prompt-20260819",
    ]
    # The 0584 divider alone scores 82%; title failure must still reject it.
    sample = next(item for item in fixture["replays"] if item["name"].startswith("0584"))
    assert sample["imageIdentityScore"] == 82.0
    assert not _matches(sample, title_pattern=fixture["promptTitlePattern"])
