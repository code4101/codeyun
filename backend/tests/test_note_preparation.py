import json
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.note_preparation import PreparationPacket, PreparationStore, automatic_note, graph_texts, plate_text
from backend.core.note_preparation_research import readonly_config, response_packets, bounded_evidence


def source(text="想尝试新工具"):
    return {"id": "pg:1:node", "hash": text, "text": text}


def packet(**kwargs):
    return PreparationPacket(title="研究新工具", intent="未来想尝试", evidence_ids=["pg:1:node"], **kwargs)


def test_source_changes_preserve_old_evidence_and_review(tmp_path):
    store = PreparationStore(1, tmp_path)
    old = store.cache_snapshot([source()], {"complete": True})
    first = store.save_packet(old["id"], packet(research="初步研究"))
    store.decision(first["id"], "deferred", "等有精力")
    store.cache_snapshot([source("更新后的意图")], {"complete": True})
    result = store.overview()["packets"][0]
    assert result["stale"]
    assert result["human_note"] == "等有精力"
    assert store.snapshot(old["id"])["sources"][0]["text"] == "想尝试新工具"
    assert PreparationStore(2, tmp_path).overview()["packets"] == []
    assert PreparationStore(2, tmp_path).snapshot(old["id"]) is None


def test_packet_identity_is_idempotent_and_research_is_versioned(tmp_path):
    store = PreparationStore(1, tmp_path)
    snapshot = store.cache_snapshot([source()], {})
    first = store.save_packet(snapshot["id"], packet(research="first"))
    repeated = store.save_packet(snapshot["id"], packet(research="first"))
    assert repeated == first
    second = store.save_packet(snapshot["id"], packet(research="second"))
    assert len(second["history"]) == 1
    assert second["history"][0]["research"] == "first"
    with pytest.raises(ValueError, match="原文证据"):
        store.save_packet(snapshot["id"], PreparationPacket(title="虚构", intent="虚构", evidence_ids=["missing"]))
    with pytest.raises(ValueError):
        store.snapshot("../../other")


def test_concurrent_packets_are_not_lost(tmp_path):
    store = PreparationStore(1, tmp_path)
    snapshot = store.cache_snapshot([source()], {})
    def save(index):
        PreparationStore(1, tmp_path).save_packet(snapshot["id"], PreparationPacket(title=f"task {index}", intent="研究", evidence_ids=["pg:1:node"]))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(save, range(20)))
    assert len(store.overview()["packets"]) == 20


def test_graph_context_includes_implicit_intent_without_binary_assets():
    objects = {"a": {"_": "TextNode", "text": "服务器维护"},
               "b": {"_": "TextNode", "text": "证书自动续期"},
               "edge": {"source": {"$graphRef": "a"}, "target": {"$graphRef": "b"}},
               "@attachment:image": {"value": "BASE64"}}
    result = graph_texts(objects)
    assert len(result) == 2
    assert result[0]["neighbours"] == ["b"]
    assert automatic_note([["__codex_diary_run_id", "string", "run"]])
    assert automatic_note([["wechat_daily_source", "string", "source"]])
    assert not automatic_note([["备注", "string", "手写"]])
    assert plate_text([{"type": "p", "children": [{"text": "研究方向"}]}, {"type": "img", "url": "data:secret", "children": []}]) == "研究方向\n[img]"
    projected = graph_texts({"url": {"_": "UrlNode", "title": "参考", "url": "https://example.org"}, "latex": {"_": "LatexNode", "latexSource": "x+y"}})
    assert projected[0]["text"] == "参考\nhttps://example.org"


def test_agent_permissions_deny_mutations_and_parser_requires_packets():
    assert readonly_config(web=False)["permission"] == {"*": "deny"}
    config = readonly_config(web=True)
    assert config["permission"]["*"] == "deny"
    assert config["agent"]["preparation"]["permission"] == config["permission"]
    assert config["permission"]["webfetch"] == "deny"
    assert response_packets('介绍\n```json\n{"packets": []}\n```') == []
    with pytest.raises(ValueError):
        response_packets("unstructured answer")
    assert response_packets('{"packets": [{"research": {"packets": ["nested"]}}]}') == [{"research": {"packets": ["nested"]}}]
    with pytest.raises(ValueError):
        response_packets('{"packets": []} {"packets": []}')


def test_large_evidence_is_bounded_and_invocation_passes_denied_tools(tmp_path, monkeypatch):
    from backend.core import note_preparation_research as research
    evidence = [{"id": str(i), "title": "标题", "text": "x" * 50000, "context": ["x" * 10000]} for i in range(10)]
    projected = bounded_evidence(evidence)
    assert len(json.dumps(projected, ensure_ascii=False)) < 20000
    assert len(projected) == len(evidence)
    assert all("节选" in item["text"] for item in projected)
    wrapper = tmp_path / "fake.ps1"
    wrapper.touch()
    monkeypatch.setenv("CODEYUN_OPENCODE_AGENT_WRAPPER", str(wrapper))
    captured = {}
    class FakeProcess:
        returncode = 0
        def __init__(self, argv, **kwargs):
            captured.update(argv=argv, **kwargs)
        def communicate(self, timeout):
            return json.dumps({"ok": True, "session_id": "test", "text": '{"packets": []}'}), ""
    monkeypatch.setattr(research.subprocess, "Popen", FakeProcess)
    research.invoke("safe prompt", web=False)
    assert captured["argv"][3] == str(wrapper)
    config = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
    assert config["agent"]["preparation"]["permission"] == {"*": "deny"}
    assert captured["env"]["OPENCODE_CONFIG_CONTENT"]


def test_research_resumes_cached_tasks_and_accepts_reordered_evidence(tmp_path, monkeypatch):
    from backend.core import note_preparation_research as research
    store = PreparationStore(1, tmp_path)
    sources = [{"id": f"pg:1:{i}", "title": "概念图", "text": "研究工具", "context": [],
                "provenance": "authored-graph", "kind": "pg", "hash": str(i), "updated_at": i} for i in range(2)]
    store.cache_snapshot(sources, {})
    monkeypatch.setattr(research, "PreparationStore", lambda owner: store)
    calls = []
    def fake_invoke(prompt, **kwargs):
        calls.append(prompt)
        item = {"title": "尝试工具", "intent": "研究新工具", "evidence_ids": ["pg:1:0", "pg:1:1"] if len(calls) == 1 else ["pg:1:1", "pg:1:0"],
                "confidence": "inferred", "research": f"phase {len(calls)}"}
        return {"ok": True, "session_id": "test", "text": json.dumps({"packets": [item]})}
    monkeypatch.setattr(research, "invoke", fake_invoke)
    first = research.run_research(1, workers=2)
    assert first["succeeded"] == 1
    second = research.run_research(1, workers=2)
    assert second["scheduled"] == 0 and second["cached"] == 1
    assert len(calls) == 1
    deeper = research.run_research(1, phase="deepen", workers=2)
    assert deeper["succeeded"] == 1
    assert len(store.overview()["packets"]) == 1
    assert len(store.overview()["packets"][0]["history"]) == 1


def test_format_recovery_only_updates_a_matching_existing_packet(tmp_path, monkeypatch):
    from backend.core import note_preparation_research as research
    store = PreparationStore(1, tmp_path)
    snapshot = store.cache_snapshot([source()], {})
    original = packet(research="初步材料")
    store.save_packet(snapshot["id"], original)
    key = "a" * 64
    store.write(f"attempt-{key}-1.json", {"response": {"session_id": "original-session"}, "snapshot_id": snapshot["id"]})
    monkeypatch.setattr(research, "PreparationStore", lambda owner: store)
    def repaired(response, **kwargs):
        assert response["session_id"] == "original-session"
        return {"session_id": "original-session", "text": json.dumps({"packets": [{**original.model_dump(), "research": "恢复后的完整材料"}]})}
    monkeypatch.setattr(research, "repair_response", repaired)
    assert research.repair_failed_task(1, key)["status"] == "succeeded"
    assert len(store.overview()["packets"]) == 1
    assert store.overview()["packets"][0]["research"] == "恢复后的完整材料"


def test_session_recovery_requires_the_latest_turn_to_be_complete(monkeypatch):
    from types import SimpleNamespace
    from backend.core import note_preparation_research as research
    messages = [{"info": {"role": "assistant", "finish": "stop"}, "parts": [{"type": "text", "text": '{"packets": []}'}]}]
    monkeypatch.setattr(research.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps({"messages": messages})))
    assert research.completed_session("ses_test")["text"] == '{"packets": []}'
    messages.append({"info": {"role": "user"}, "parts": []})
    with pytest.raises(ValueError, match="尚未完成"):
        research.completed_session("ses_test")


def test_api_owner_isolation_and_export(tmp_path, monkeypatch):
    from backend.api import notes_preparation as api
    from backend.core.access.auth import get_current_active_user
    from backend.models import User
    app = FastAPI()
    # Test authentication independently from the feature access registry/database.
    for dependency in api.router.dependencies:
        app.dependency_overrides[dependency.dependency] = lambda: None
    active = {"id": 1}
    app.dependency_overrides[get_current_active_user] = lambda: User(id=active["id"], username="test", hashed_password="")
    monkeypatch.setattr(api, "PreparationStore", lambda owner: PreparationStore(owner, tmp_path))
    app.include_router(api.router, prefix="/api/notes-preparation")
    store = PreparationStore(1, tmp_path)
    snapshot = store.cache_snapshot([source()], {})
    item = store.save_packet(snapshot["id"], packet())
    updated = store.cache_snapshot([source("新原文")], {})
    store.save_packet(updated["id"], packet(research="基于新原文"))
    client = TestClient(app)
    active["id"] = 2
    assert client.get("/api/notes-preparation").json()["packets"] == []
    assert client.get(f"/api/notes-preparation/snapshots/{snapshot['id']}").status_code == 404
    assert client.patch(f"/api/notes-preparation/packets/{item['id']}", json={"decision": "interested"}).status_code == 404
    active["id"] = 1
    response = client.get("/api/notes-preparation/export")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert f"snapshots/{snapshot['id']}.json" in archive.namelist()
        assert f"snapshots/{updated['id']}.json" in archive.namelist()
    assert "attachment" in response.headers["content-disposition"]
    assert client.patch(f"/api/notes-preparation/packets/{item['id']}", json={"decision": "execute"}).status_code == 422
