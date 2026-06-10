from backend.core.fanxiu_behavior_tree import create_fanxiu_runtime_runner
from pyxllib.autogui import ShapeMatchPlanner


def test_shape_match_planner_normalizes_roles_and_ocr_defaults():
    planner = ShapeMatchPlanner()

    assert planner.match_role({"imageMatchRole": "bad"}, "imageMatchRole", "optional") == "optional"
    assert planner.match_role({"imageMatchRole": "optional"}, "imageMatchRole") == "optional"
    assert planner.match_role({"imageMatchRole": "decisive"}, "imageMatchRole") == "decisive"
    assert planner.match_role({"imageMatchRole": "定"}, "imageMatchRole") == "decisive"
    assert planner.match_conditions({"imageMatchRole": "定", "ocrMatchRole": "定", "ocrText": "邮"}) == ["image", "ocr"]
    assert planner.image_role({"imageMatchRole": "off"}) == "off"
    assert planner.ocr_role({"ocrEnabled": True, "ocrText": "邮件"}) == "required"
    assert planner.ocr_role({"ocrEnabled": True, "ocrText": ""}) == "off"
    assert planner.ocr_fallback_enabled({"ocrText": "邮件", "ocrMatchRole": "optional"}) is True
    assert planner.ocr_fallback_enabled({"ocrText": "邮件", "ocrMatchRole": "off"}) is False


def test_shape_match_planner_uses_ocr_without_scan_for_floating_ocr_shape():
    flags = ShapeMatchPlanner().runtime_match_payload_flags({
        "floating": True,
        "imageMatchRole": "off",
        "ocrText": "邮件",
        "ocrMatchRole": "optional",
    })

    assert flags == {
        "image_role": "off",
        "ocr_role": "optional",
        "ocr_enabled": True,
        "scan": False,
        "match_strategy": "anchor_pixel",
    }


def test_shape_match_planner_scans_floating_image_without_ocr():
    flags = ShapeMatchPlanner().runtime_match_payload_flags({
        "floating": True,
        "imageMatchRole": "required",
        "ocrMatchRole": "off",
    })

    assert flags["image_role"] == "required"
    assert flags["ocr_role"] == "off"
    assert flags["ocr_enabled"] is False
    assert flags["scan"] is True
    assert flags["match_strategy"] == "auto"


def test_shape_match_planner_uses_auto_for_jitter_and_forced_ocr():
    planner = ShapeMatchPlanner()

    assert planner.runtime_match_payload_flags({"jitterEnabled": True})["match_strategy"] == "auto"
    assert planner.runtime_match_payload_flags({"ocrText": "邮件", "ocrMatchRole": "required"}, condition="ocr")["match_strategy"] == "auto"
    assert planner.runtime_match_payload_flags({"ocrText": "邮件", "ocrMatchRole": "required"}, condition="image")["ocr_enabled"] is False


def test_runner_shape_match_flags_delegate_to_planner():
    runner = create_fanxiu_runtime_runner()
    shape = {
        "floating": True,
        "imageMatchRole": "off",
        "ocrText": "邮件",
        "ocrMatchRole": "optional",
    }

    assert runner._shape_runtime_match_payload_flags(shape) == ShapeMatchPlanner().runtime_match_payload_flags(shape)
    assert runner._shape_image_role(shape) == "off"
    assert runner._shape_ocr_role(shape) == "optional"
