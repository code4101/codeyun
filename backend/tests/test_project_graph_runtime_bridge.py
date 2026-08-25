from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import project_graph_runtime as project_graph_runtime_api
from backend.core.project_graph.runtime_bridge import (
    ProjectGraphRuntimeObject,
    ProjectGraphRuntimeSnapshot,
    ProjectGraphRuntimeSnapshotStore,
    is_loopback_host,
)


def build_snapshot() -> ProjectGraphRuntimeSnapshot:
    return ProjectGraphRuntimeSnapshot(
        bridge_version="0.1.0",
        session_id="session-a",
        observed_at=100.0,
        project_uri="file:///D:/graph.prg",
        project_title="graph.prg",
        project_state="Saved",
        objects=[
            ProjectGraphRuntimeObject(
                uuid="node-a",
                kind="TextNode",
                text="Runtime node",
                position={"x": 10, "y": 20},
                size={"x": 200, "y": 75},
            )
        ],
        selected_uuids=["node-a"],
    )


def test_runtime_snapshot_store_reports_live_then_stale():
    store = ProjectGraphRuntimeSnapshotStore(stale_seconds=10)

    accepted = store.publish(build_snapshot(), received_at=200.0)
    live = store.latest(now=205.0)
    stale = store.latest(now=211.0)

    assert accepted == {
        "accepted": True,
        "sequence": 1,
        "received_at": 200.0,
        "object_count": 1,
        "selected_count": 1,
    }
    assert live["connected"] is True
    assert live["snapshot"]["objects"][0]["text"] == "Runtime node"
    assert stale["connected"] is False
    assert stale["stale"] is True


def test_runtime_snapshot_store_starts_empty():
    store = ProjectGraphRuntimeSnapshotStore()

    assert store.latest(now=1.0) == {
        "connected": False,
        "stale": True,
        "sequence": 0,
        "received_at": None,
        "age_seconds": None,
        "snapshot": None,
    }


def test_loopback_host_detection():
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert is_loopback_host("localhost")
    assert not is_loopback_host("192.168.1.2")
    assert not is_loopback_host(None)


def test_runtime_api_accepts_and_returns_snapshot(monkeypatch):
    app = FastAPI()
    app.include_router(project_graph_runtime_api.router, prefix="/api/project-graph/runtime")
    project_graph_runtime_api.runtime_snapshot_store.clear()
    monkeypatch.setattr(project_graph_runtime_api, "is_loopback_host", lambda _host: True)

    with TestClient(app) as client:
        publish_response = client.post(
            "/api/project-graph/runtime/snapshot",
            json=build_snapshot().model_dump(mode="json"),
        )
        latest_response = client.get("/api/project-graph/runtime/latest")

    assert publish_response.status_code == 200
    assert publish_response.json()["object_count"] == 1
    assert latest_response.status_code == 200
    assert latest_response.json()["snapshot"]["project_uri"] == "file:///D:/graph.prg"
