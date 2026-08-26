import base64
import io

from PIL import Image

from backend.core.fanxiu.data_annotation import recognition_ambiguity_incidents as incidents
from backend.core.fanxiu.data_annotation import behavior_tree_runtime as runtime_module
from backend.core.fanxiu.data_annotation.behavior_tree_runtime import BehaviorTreeRuntimeRunner


def _frame_data_url(color: tuple[int, int, int]) -> str:
    output = io.BytesIO()
    Image.new("RGB", (4, 5), color).save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def test_recognition_ambiguity_deduplicates_same_frame_and_aggregates_distinct_frames(tmp_path, monkeypatch):
    entry_root = tmp_path / "entry"
    monkeypatch.setattr(incidents, "data_annotation_entry_dir", lambda _entry_id: entry_root)

    first = incidents.record_recognition_ambiguity(
        entry_id="entry-1",
        frame_data_url=_frame_data_url((10, 20, 30)),
        captured_at=1_800_000_000.0,
        layer=2,
        tied_scene_ids=[9, 3, 9],
        similarities={3: 91.2, 9: 94.3},
        fallback_scene_id=9,
        asset_tree_sha256="asset-a",
    )
    duplicate = incidents.record_recognition_ambiguity(
        entry_id="entry-1",
        frame_data_url=_frame_data_url((10, 20, 30)),
        captured_at=1_800_000_000.0,
        layer=2,
        tied_scene_ids=[3, 9],
        similarities={3: 91.2, 9: 94.3},
        fallback_scene_id=9,
        asset_tree_sha256="asset-a",
    )
    second = incidents.record_recognition_ambiguity(
        entry_id="entry-1",
        frame_data_url=_frame_data_url((30, 20, 10)),
        captured_at=1_800_000_002.0,
        layer=2,
        tied_scene_ids=[9, 3],
        similarities={3: 95.0, 9: 94.0},
        fallback_scene_id=3,
        asset_tree_sha256="asset-b",
    )

    assert first["tied_scene_ids"] == [3, 9]
    assert duplicate["occurrence_count"] == 1
    assert second["occurrence_count"] == 2
    assert second["distinct_frame_count"] == 2
    assert second["selected_scene_counts"] == {"9": 1, "3": 1}
    assert len(list((entry_root / "recognition-ops" / "frame-blobs").rglob("*.png"))) == 2
    assert len(list((entry_root / "recognition-ops" / "ambiguity-events").rglob("*.json"))) == 2

    summaries = incidents.list_recognition_ambiguity_summaries("entry-1")
    assert len(summaries) == 1
    detail = incidents.load_recognition_ambiguity("entry-1", second["signature"], include_frames=True)
    assert detail is not None
    assert len(detail["frame_data_urls"]) == 2


def test_unified_graph_matcher_records_real_similarity_tiebreak(monkeypatch):
    runner = BehaviorTreeRuntimeRunner()
    monkeypatch.setattr(runner, "_scene_candidate_scores_parallel", lambda *_args: [100.0, 100.0])
    monkeypatch.setattr(runner, "_scene_match_threshold", lambda _scene_id: 80.0)
    monkeypatch.setattr(runner, "_scene_match_edges_for_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        runner,
        "_scene_reference_similarity",
        lambda _ctx, image, _frame: 90.0 if image["scene_id"] == 2 else 80.0,
    )
    recorded = []
    monkeypatch.setattr(runtime_module, "record_recognition_ambiguity", lambda **payload: recorded.append(payload))
    ctx = {
        "entry_id": "entry-1",
        "asset_tree_revision": "asset-revision",
        "images": {
            1: {"scene_id": 1, "shapes": []},
            2: {"scene_id": 2, "shapes": []},
        },
    }

    scene_id, score, status = runner._identify_scene_number_in_graph_candidates(
        ctx,
        _frame_data_url((1, 2, 3)),
        [1, 2],
        layer_label="layer2",
    )

    assert (scene_id, score, status) == (2, 100.0, "similarity_tiebreak")
    assert recorded[0]["tied_scene_ids"] == (1, 2)
    assert recorded[0]["fallback_scene_id"] == 2
    assert recorded[0]["asset_tree_sha256"] == "asset-revision"
