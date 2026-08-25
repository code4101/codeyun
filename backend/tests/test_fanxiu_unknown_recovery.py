from __future__ import annotations

from backend.core.fanxiu.data_annotation.unknown_recovery import build_unknown_evidence


class _Runner:
    def __init__(self) -> None:
        self.scored: list[int] = []

    def _scene_identity_shapes(self, image):
        return image.get("shapes") or []

    def _scene_score(self, _ctx, image, _frame):
        self.scored.append(int(image["number"]))
        return 0.0

    def _scene_reference_similarity(self, _ctx, image, _frame):
        self.scored.append(int(image["number"]))
        return 0.0

    def _cached_ocr_fragments(self, _ctx, _frame):
        return []

    def _decode_frame_data_url(self, _frame):
        return b""


def test_unknown_timeout_evidence_can_limit_expensive_candidate_scan(monkeypatch):
    runner = _Runner()
    images = {
        scene_id: {
            "number": scene_id,
            "title": f"scene-{scene_id}",
            "filename": f"{scene_id:04d}.png",
            "shapes": [],
        }
        for scene_id in range(1, 101)
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.unknown_recovery._save_frame_if_possible",
        lambda *_args, **_kwargs: (None, None),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.unknown_recovery._reference_frame_similarity",
        lambda probe_runner, _image, _frame: (
            probe_runner.scored.append(int(_image["number"])) or 0.0
        ),
    )

    evidence = build_unknown_evidence(
        runner,
        {"images": images},
        "frame",
        label="bounded-wait",
        expected_scene_ids=[10],
        last_scene_id=11,
        last_score=0.0,
        candidate_scene_ids=[10, 11],
    )

    assert evidence.expected_scene_ids == [10]
    assert set(runner.scored) == {10, 11}
