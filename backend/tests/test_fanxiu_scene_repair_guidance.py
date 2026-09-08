"""Exception transport contracts; no simulated recognition or game actions."""

from backend.core.fanxiu.data_annotation.behavior_tree_executor import SceneWaitTimeout
from backend.core.fanxiu.data_annotation.scene_escalation import (
    SceneRepairRequired, scene_repair_guidance,
)


def test_unknown_keeps_repair_route_in_plain_error_output():
    evidence = "C:/temp/original.png"
    error = SceneWaitTimeout(
        "持续未匹配", expected_scene_ids=iter([714, 714]), last_match=None,
        evidence_frame_path=evidence,
    )
    guidance = scene_repair_guidance(
        scene_id=None, evidence_frame_path=evidence, expected_scene_ids=[714],
    )
    assert error.repair_guidance == guidance
    assert guidance["problem_code"] == "scene.persistent_unknown"
    assert guidance["document"].endswith("skills/凡修/references/接口层/场景识别.md#未匹配处理")
    assert guidance["diagnostic_call"] == (
        "context.render_unknown_scene_overview('C:/temp/original.png', [714], top_k=2)"
    )
    assert "请 AI 查阅：" + guidance["document"] in str(error)
    assert guidance["diagnostic_call"] in str(error)


def test_known_scene_has_comparison_instead_of_unknown_route():
    error = SceneRepairRequired("缺少关闭", scene_id=728, evidence_frame_path="C:/temp/original.png")
    assert error.repair_guidance["problem_code"] == "scene.repair_required"
    assert "context.render_scene_comparison('C:/temp/original.png', 728)" in str(error)
    assert "render_unknown_scene_overview" not in str(error)
