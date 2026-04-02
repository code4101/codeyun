import json
import sqlite3

import pytest
from sqlmodel import select

from backend.core.ai_chat_user_config import save_user_ai_chat_provider_config
from backend.core.document_reduction_cache import get_document_cache_db_path
from backend.core.document_reduction_storage import get_document_assets_dir
from backend.core.ollama_access_keys import create_ollama_access_key
from backend.models import DocumentAsset, DocumentQueryHistory, DocumentReductionRun


@pytest.fixture(autouse=True)
def _configure_test_ollama_access(session, auth_user):
    created = create_ollama_access_key(session, created_by_user_id=auth_user.id, label="文档归纳测试 Key")
    save_user_ai_chat_provider_config(
        session,
        auth_user.id,
        "ollama",
        api_key=created["plaintext_value"],
    )
    return created["plaintext_value"]


def test_reduction_document_upload_index_and_query_flow(client, auth_user, monkeypatch):
    def fake_chat_with_provider(
        *,
        provider_id,
        base_url,
        api_key,
        messages,
        model=None,
        system_prompt=None,
        temperature=None,
        response_format=None,
        timeout_seconds=None,
        extra_providers=(),
    ):
        prompt_text = "\n".join(str(item.get("content") or "") for item in messages)
        if "文档问答助手" in (system_prompt or ""):
            return {
                "model": model or "qwen3.5:4b-instruct",
                "content": json.dumps(
                    {
                        "answer": "文档里提到部署失败的原因是配置缺失，后续补了修复步骤。",
                        "summary": "命中了部署排障相关片段。",
                        "matched_node_ids": [],
                        "matched_source_refs": [],
                        "needs_more_context": False,
                        "follow_up_questions": ["还记录了哪些修复动作"],
                    },
                    ensure_ascii=False,
                ),
            }

        topic = "部署排障" if "部署" in prompt_text else "文档概览"
        return {
            "model": model or "qwen3.5:4b-instruct",
            "content": json.dumps(
                {
                    "topic": topic,
                    "summary": "记录了部署失败原因、修复动作和后续建议。",
                    "keywords": ["部署", "失败", "修复", "配置"],
                    "possible_questions": ["为什么部署失败", "如何修复部署问题"],
                    "importance": "high",
                    "importance_reason": "内容直接涉及故障原因和修复。",
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        }

    monkeypatch.setattr("backend.core.document_reduction.chat_with_provider", fake_chat_with_provider)

    upload_response = client.post(
        "/api/reduction-documents/upload",
        files={"file": ("deploy-notes.txt", "部署失败，原因是缺少配置。\n\n后续补充了修复步骤。".encode("utf-8"), "text/plain")},
    )
    assert upload_response.status_code == 200
    document_payload = upload_response.json()
    assert document_payload["title"] == "deploy-notes"
    assert document_payload["status"] == "uploaded"
    document_id = document_payload["id"]

    index_response = client.post(
        f"/api/reduction-documents/{document_id}/index",
        json={"provider": "ollama", "model": "qwen3.5:4b-instruct"},
    )
    assert index_response.status_code == 200
    index_payload = index_response.json()
    assert index_payload["document"]["status"] == "indexed"
    assert index_payload["document"]["latest_run_id"]
    assert index_payload["run"]["status"] == "completed"
    assert index_payload["run"]["source_unit_count"] >= 1
    assert index_payload["run"]["estimated_level_count"] >= 1
    assert index_payload["run"]["completed_chunk_count"] >= 1
    assert index_payload["result"]["summary"]

    detail_response = client.get(f"/api/reduction-documents/{document_id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["active_run"]["completed_chunk_count"] >= 1
    assert detail_payload["latest_run"]["id"] == index_payload["run"]["id"]

    query_response = client.post(
        f"/api/reduction-documents/{document_id}/query",
        json={
            "query": "部署失败原因是什么",
            "provider": "ollama",
            "model": "qwen3.5:4b-instruct",
        },
    )
    assert query_response.status_code == 200
    query_payload = query_response.json()
    assert query_payload["document_id"] == document_id
    assert query_payload["run_id"] == index_payload["run"]["id"]
    assert "配置缺失" in query_payload["answer"]
    assert query_payload["matched_nodes"]
    assert get_document_cache_db_path().exists()


def test_reduction_document_upload_rejects_binary_file(client, auth_user):
    response = client.post(
        "/api/reduction-documents/upload",
        files={"file": ("archive.bin", b"\x00\x01\x02", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "文本" in response.json()["detail"]


def test_reduction_document_index_repairs_non_json_response(client, auth_user, monkeypatch):
    state = {"reduce_calls": 0, "repair_calls": 0}

    def fake_chat_with_provider(
        *,
        provider_id,
        base_url,
        api_key,
        messages,
        model=None,
        system_prompt=None,
        temperature=None,
        response_format=None,
        timeout_seconds=None,
        extra_providers=(),
    ):
        prompt_text = "\n".join(str(item.get("content") or "") for item in messages)
        if "JSON 修复助手" in (system_prompt or ""):
            state["repair_calls"] += 1
            return {
                "model": model or "qwen3.5:4b-instruct",
                "content": json.dumps(
                    {
                        "topic": "章节归并",
                        "summary": "修复后的高层摘要。",
                        "keywords": ["章节", "归并", "摘要"],
                        "possible_questions": ["这份文档讲了什么"],
                        "importance": "medium",
                        "importance_reason": "",
                        "reason": "",
                    },
                    ensure_ascii=False,
                ),
            }

        if "文档归并索引助手" in (system_prompt or ""):
            state["reduce_calls"] += 1
            if state["reduce_calls"] == 1:
                return {
                    "model": model or "qwen3.5:4b-instruct",
                    "content": "先给你一个结论：topic=章节归并, summary=修复前不是 JSON",
                }

        topic = "章节片段" if "片段" in prompt_text else "文档概览"
        return {
            "model": model or "qwen3.5:4b-instruct",
            "content": json.dumps(
                {
                    "topic": topic,
                    "summary": "叶子层摘要。",
                    "keywords": ["章节", "片段"],
                    "possible_questions": ["发生了什么"],
                    "importance": "medium",
                    "importance_reason": "",
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        }

    monkeypatch.setattr("backend.core.document_reduction.chat_with_provider", fake_chat_with_provider)

    long_text = ("\n\n".join(["第一段 " + ("甲" * 2200), "第二段 " + ("乙" * 2200), "第三段 " + ("丙" * 2200)])).encode("utf-8")
    upload_response = client.post(
        "/api/reduction-documents/upload",
        files={"file": ("huge-notes.txt", long_text, "text/plain")},
    )
    assert upload_response.status_code == 200
    document_id = upload_response.json()["id"]

    index_response = client.post(
        f"/api/reduction-documents/{document_id}/index",
        json={"provider": "ollama", "model": "qwen3.5:4b-instruct", "branch_factor": 2},
    )
    assert index_response.status_code == 200
    payload = index_response.json()
    assert payload["run"]["status"] == "completed"
    assert payload["result"]["summary"]
    assert state["repair_calls"] == 1


def test_reduction_document_delete_cascades_metadata_cache_and_assets(client, auth_user, monkeypatch, session):
    def fake_chat_with_provider(
        *,
        provider_id,
        base_url,
        api_key,
        messages,
        model=None,
        system_prompt=None,
        temperature=None,
        response_format=None,
        timeout_seconds=None,
        extra_providers=(),
    ):
        prompt_text = "\n".join(str(item.get("content") or "") for item in messages)
        if "文档问答助手" in (system_prompt or ""):
            return {
                "model": model or "qwen3.5:4b-instruct",
                "content": json.dumps(
                    {
                        "answer": "命中了测试片段。",
                        "summary": "问答完成。",
                        "matched_node_ids": [],
                        "matched_source_refs": [],
                        "needs_more_context": False,
                        "follow_up_questions": [],
                    },
                    ensure_ascii=False,
                ),
            }

        topic = "测试索引" if "测试" in prompt_text else "文档概览"
        return {
            "model": model or "qwen3.5:4b-instruct",
            "content": json.dumps(
                {
                    "topic": topic,
                    "summary": "测试摘要。",
                    "keywords": ["测试", "索引"],
                    "possible_questions": ["测试问题"],
                    "importance": "medium",
                    "importance_reason": "",
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        }

    monkeypatch.setattr("backend.core.document_reduction.chat_with_provider", fake_chat_with_provider)

    upload_response = client.post(
        "/api/reduction-documents/upload",
        files={"file": ("delete-me.txt", "测试文档内容。\n\n第二段内容。".encode("utf-8"), "text/plain")},
    )
    assert upload_response.status_code == 200
    document_id = upload_response.json()["id"]
    asset_dir = get_document_assets_dir() / f"user-{auth_user.id}" / document_id
    assert asset_dir.exists()

    index_response = client.post(
        f"/api/reduction-documents/{document_id}/index",
        json={"provider": "ollama", "model": "qwen3.5:4b-instruct"},
    )
    assert index_response.status_code == 200
    run_id = index_response.json()["run"]["id"]

    query_response = client.post(
        f"/api/reduction-documents/{document_id}/query",
        json={"query": "测试问题", "provider": "ollama", "model": "qwen3.5:4b-instruct"},
    )
    assert query_response.status_code == 200

    cache_db = get_document_cache_db_path()
    with sqlite3.connect(cache_db) as conn:
        cached_count = conn.execute(
            "SELECT COUNT(*) FROM document_node_index WHERE user_id = ? AND document_id = ?",
            (auth_user.id, document_id),
        ).fetchone()[0]
    assert cached_count > 0

    delete_response = client.delete(f"/api/reduction-documents/{document_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["document_id"] == document_id

    assert session.get(DocumentAsset, document_id) is None
    remaining_runs = session.exec(select(DocumentReductionRun).where(DocumentReductionRun.document_id == document_id)).all()
    remaining_queries = session.exec(select(DocumentQueryHistory).where(DocumentQueryHistory.document_id == document_id)).all()
    assert remaining_runs == []
    assert remaining_queries == []
    assert not asset_dir.exists()

    with sqlite3.connect(cache_db) as conn:
        remaining_cache = conn.execute(
            "SELECT COUNT(*) FROM document_node_index WHERE user_id = ? AND document_id = ?",
            (auth_user.id, document_id),
        ).fetchone()[0]
    assert remaining_cache == 0

    detail_response = client.get(f"/api/reduction-documents/{document_id}")
    assert detail_response.status_code == 404
