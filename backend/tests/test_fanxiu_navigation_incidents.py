import base64
import json
import time
from types import SimpleNamespace

from backend.core.fanxiu.data_annotation import navigation_incidents
from backend.core.fanxiu.data_annotation.navigation_incidents import NavigationIncidentRecorder


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_FRAME = "data:image/png;base64," + base64.b64encode(_PNG_1X1).decode("ascii")


class _Runner:
    def status(self):
        return {
            "current_task": "日常_测试",
            "current_task_id": "job-test",
            "task_type": "test",
            "phase": "go_scene",
            "current_cell_id": "cell-test",
        }

    def _decode_frame_data_url(self, frame_data_url):
        return base64.b64decode(frame_data_url.split(",", 1)[1])


def test_navigation_incident_persists_frames_timeline_and_resolution(tmp_path, monkeypatch):
    entry_dir = tmp_path / "entry"
    monkeypatch.setattr(navigation_incidents, "data_annotation_entry_dir", lambda _entry_id: entry_dir)
    monkeypatch.setattr(
        navigation_incidents,
        "build_unknown_evidence",
        lambda *_args, **_kwargs: SimpleNamespace(
            to_dict=lambda: {
                "classification": "matched_existing_frame",
                "candidates": [{"scene_id": 54, "scene_score": 86}],
                "ocr_texts": ["服用丹药"],
                "suggestion": "检查 #54 标注",
            }
        ),
    )
    asset_tree_path = entry_dir / "asset-tree.json"
    asset_tree_path.parent.mkdir(parents=True)
    asset_tree_path.write_text("[]", encoding="utf-8")
    ctx = {
        "entry_id": "entry-test",
        "images": {
            34: {"type": "image", "title": "世界", "filename": "0034.png"},
            54: {"type": "image", "title": "退出道场", "filename": "0054.png"},
        },
    }
    recorder = NavigationIncidentRecorder(
        _Runner(),
        ctx,
        asset_tree_path,
        target_scene_id=34,
        started_monotonic=time.monotonic(),
    )
    recorder.record_action(
        kind="navigation",
        source_scene_id=54,
        source_score=86,
        shape={"id": "confirm", "title": "确认"},
        reason="历史后验路径",
        before_frame=_FRAME,
        landing_scene_id=54,
        landing_score=86,
        after_frame=_FRAME,
        frame_similarity=100,
        navigation_state_key="scene:54:state:1",
        point=(321, 654),
    )
    recorder.trigger(
        trigger_type="stable_self_loop",
        trigger_label="稳定自环",
        threshold={"attempts": 2},
        frame_data_url=_FRAME,
        current_scene_id=54,
        current_score=86,
        candidate_scene_ids=[54, 34],
    )
    recorder.mark_fallback_used()
    recorder.record_action(
        kind="fallback",
        source_scene_id=54,
        source_score=86,
        shape={"id": "return", "title": "返回"},
        reason="正常动作耗尽",
        before_frame=_FRAME,
        landing_scene_id=34,
        landing_score=100,
        after_frame=_FRAME,
        frame_similarity=100,
        navigation_state_key="scene:54:state:1",
        attempt=1,
    )
    recorder.finalize(
        status="recovered_with_fallback",
        final_scene_id=34,
        final_score=100,
        final_frame=_FRAME,
        message="已回世界",
    )

    listed = navigation_incidents.list_navigation_incidents("entry-test")
    assert len(listed) == 1
    assert listed[0]["status"] == "recovered_with_fallback"
    assert listed[0]["review_status"] == "pending"
    assert listed[0]["fallback_used"] is True
    assert listed[0]["runtime"]["cell_id"] == "cell-test"
    assert listed[0]["timeline"][0]["point"] == [321.0, 654.0]
    assert [item["kind"] for item in listed[0]["timeline"]] == ["navigation", "fallback"]
    assert "_before_frame" not in json.dumps(listed[0], ensure_ascii=False)
    assert listed[0]["asset_tree"]["sha256"]
    assert {item["filename"] for item in listed[0]["scene_snapshots"]} == {"0034.png", "0054.png"}

    detail = navigation_incidents.load_navigation_incident(
        "entry-test",
        listed[0]["id"],
        include_frames=True,
    )
    assert detail is not None
    assert len(detail["frame_data_urls"]) >= 5
    assert all(value.startswith("data:image/png;base64,") for value in detail["frame_data_urls"].values())


def test_navigation_incident_summaries_cache_and_invalidate(tmp_path, monkeypatch):
    entry_dir = tmp_path / "entry"
    incident_dir = entry_dir / "recognition-ops" / "navigation-incidents" / "nav-1"
    incident_dir.mkdir(parents=True)
    incident_path = incident_dir / "incident.json"
    incident_path.write_text(
        json.dumps(
            {
                "id": "nav-1",
                "status": "recovering",
                "timeline": [
                    {
                        "source_scene_id": 54,
                        "landing_scene_id": 34,
                        "landing_score": 99,
                        "kind": "navigation",
                        "_frame": "private",
                    }
                ],
                "diagnostic": {"large": "unused"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(navigation_incidents, "data_annotation_entry_dir", lambda _entry_id: entry_dir)
    navigation_incidents._cached_navigation_incident_summaries.cache_clear()

    first = navigation_incidents.list_navigation_incident_summaries("entry-test")
    second = navigation_incidents.list_navigation_incident_summaries("entry-test")

    assert first == second
    assert first[0]["status"] == "recovering"
    assert "diagnostic" not in first[0]
    assert first[0]["timeline"] == [{"source_scene_id": 54, "landing_scene_id": 34, "landing_score": 99}]
    assert navigation_incidents._cached_navigation_incident_summaries.cache_info().hits == 1

    incident_path.write_text(
        json.dumps({"id": "nav-1", "status": "recovered_after_stall", "timeline": []}),
        encoding="utf-8",
    )
    refreshed = navigation_incidents.list_navigation_incident_summaries("entry-test")

    assert refreshed[0]["status"] == "recovered_after_stall"
    assert navigation_incidents._cached_navigation_incident_summaries.cache_info().misses == 2
