from backend.models import UserDevice


def _create_local_entry(client):
    response = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def test_local_entry_reports_missing_rime_context_prediction(client, auth_user, test_device, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEYUN_RIME_USER_DIR", str(tmp_path / "missing-rime"))
    entry_id = _create_local_entry(client)

    response = client.get(f"/api/device-entries/{entry_id}/rime/context-prediction/tree")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["status"] == "rime_missing"
    assert payload["summary"]["row_count"] == 0


def test_local_entry_reads_rime_context_prediction_snapshot(client, auth_user, test_device, tmp_path, monkeypatch):
    rime_dir = tmp_path / "Rime"
    rime_dir.mkdir()
    (rime_dir / "context_prediction_snapshot.tsv").write_text(
        "# context_key\tpinyin_prefix\tcandidate\tweight\tcomment\n"
        "占位\tfu\t符号\t100\t预测\n"
        "__global\tfu\t服务\t25\t全局\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEYUN_RIME_USER_DIR", str(rime_dir))
    entry_id = _create_local_entry(client)

    response = client.get(f"/api/device-entries/{entry_id}/rime/context-prediction/tree")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["status"] == "ready"
    assert payload["source"] == "context_prediction_snapshot.tsv"
    assert payload["summary"]["row_count"] == 2
    assert payload["summary"]["context_count"] == 2
    assert payload["rows"][0]["candidate"] == "符号"


def test_local_entry_refreshes_rime_context_prediction_from_pending(client, auth_user, test_device, tmp_path, monkeypatch):
    rime_dir = tmp_path / "Rime"
    rime_dir.mkdir()
    (rime_dir / "context_prediction_pending.tsv").write_text(
        "__global\tfu\t服务\t2\t自学习\n"
        "占位\tfu\t符号\t1\t自学习\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEYUN_RIME_USER_DIR", str(rime_dir))
    entry_id = _create_local_entry(client)

    response = client.post(f"/api/device-entries/{entry_id}/rime/context-prediction/tree/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["source"] == "context_prediction_snapshot.tsv"
    assert any(row["context"] == "__global" and row["prefix"] == "fu" and row["candidate"] == "服务" for row in payload["rows"])
    assert any(row["context"] == "占位" and row["prefix"] == "fu" and row["candidate"] == "符号" for row in payload["rows"])
    assert not (rime_dir / "context_prediction_pending.tsv").exists()
    assert (rime_dir / "context_prediction_model_counts.tsv").exists()


def test_local_entry_reads_rime_context_history_as_article(client, auth_user, test_device, tmp_path, monkeypatch):
    rime_dir = tmp_path / "Rime"
    rime_dir.mkdir()
    (rime_dir / "context_prediction_history.log").write_text(
        "2026-05-13 10:00:00\t你\t你\n"
        "2026-05-13 10:00:01\t好\t好\n"
        "2026-05-13 10:08:00\t世\t世\n"
        "2026-05-13 10:08:01\t界\t界\n",
        encoding="utf-8",
    )
    (rime_dir / "context_prediction_pending.tsv").write_text(
        "__global\tni\t你\t1\t自学习\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEYUN_RIME_USER_DIR", str(rime_dir))
    entry_id = _create_local_entry(client)

    response = client.get(f"/api/device-entries/{entry_id}/rime/context-prediction/history-article")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["source"] == "context_prediction_history.log"
    assert payload["summary"]["entry_count"] == 4
    assert payload["summary"]["pending_row_count"] == 1
    assert payload["content"] == "你好\n\n世界"


def test_local_entry_paginates_rime_context_history_article(client, auth_user, test_device, tmp_path, monkeypatch):
    rime_dir = tmp_path / "Rime"
    rime_dir.mkdir()
    (rime_dir / "context_prediction_history.log").write_text(
        "".join(
            f"2026-05-13 10:00:{index:02d}\t字{index}\t字{index}\n"
            for index in range(1, 7)
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEYUN_RIME_USER_DIR", str(rime_dir))
    entry_id = _create_local_entry(client)

    response = client.get(
        f"/api/device-entries/{entry_id}/rime/context-prediction/history-article",
        params={"page": 2, "page_size": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["summary"]["entry_count"] == 6
    assert payload["summary"]["char_count"] == len("字3字4")
    assert payload["content"] == "字3字4"
    assert payload["pagination"] == {
        "page": 2,
        "page_size": 2,
        "total": 6,
        "total_pages": 3,
        "start_index": 3,
        "end_index": 4,
        "has_prev": True,
        "has_next": True,
    }


def test_local_entry_refresh_rebuilds_prediction_from_history(client, auth_user, test_device, tmp_path, monkeypatch):
    rime_dir = tmp_path / "Rime"
    rime_dir.mkdir()
    (rime_dir / "context_prediction_history.log").write_text(
        "2026-05-13 10:00:00\t你好\t你好\n"
        "2026-05-13 10:00:01\t世界\t世界\n",
        encoding="utf-8",
    )
    (rime_dir / "context_prediction_pending.tsv").write_text(
        "__global\tjiu\t旧词\t1\t自学习\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEYUN_RIME_USER_DIR", str(rime_dir))
    entry_id = _create_local_entry(client)

    response = client.post(f"/api/device-entries/{entry_id}/rime/context-prediction/tree/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert "输入历史" in payload["message"]
    assert payload["summary"]["row_count"] > 0
    assert any(row["candidate"] == "世界" for row in payload["rows"])
    assert not (rime_dir / "context_prediction_pending.tsv").exists()
    counts_text = (rime_dir / "context_prediction_model_counts.tsv").read_text(encoding="utf-8")
    assert "世界" in counts_text
    assert "旧词" not in counts_text


def test_local_entry_saves_edited_history_article_for_prediction(client, auth_user, test_device, tmp_path, monkeypatch):
    rime_dir = tmp_path / "Rime"
    rime_dir.mkdir()
    (rime_dir / "context_prediction_history.log").write_text(
        "2026-05-13 10:00:00\t占位\t占位\n"
        "2026-05-13 10:00:01\t符号\t符号\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEYUN_RIME_USER_DIR", str(rime_dir))
    entry_id = _create_local_entry(client)

    save_response = client.put(
        f"/api/device-entries/{entry_id}/rime/context-prediction/history-article",
        json={"content": "占位服务。"},
    )
    assert save_response.status_code == 200
    save_payload = save_response.json()
    assert save_payload["summary"]["edited"] is True
    assert save_payload["content"] == "占位服务。"

    refresh_response = client.post(f"/api/device-entries/{entry_id}/rime/context-prediction/tree/refresh")

    assert refresh_response.status_code == 200
    rows = refresh_response.json()["rows"]
    assert any(row["candidate"] == "服务" for row in rows)
    assert all(row["candidate"] != "符号" for row in rows)


def test_local_entry_deletes_rime_context_prediction_candidate(client, auth_user, test_device, tmp_path, monkeypatch):
    rime_dir = tmp_path / "Rime"
    rime_dir.mkdir()
    (rime_dir / "context_prediction.tsv").write_text(
        "# context_key\tpinyin_prefix\tcandidate\tweight\tcomment\n"
        "占位\tfu\t符号\t100\t手动规则\n"
        "占位\tfu\t服务\t80\t手动规则\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEYUN_RIME_USER_DIR", str(rime_dir))
    entry_id = _create_local_entry(client)

    response = client.request(
        "DELETE",
        f"/api/device-entries/{entry_id}/rime/context-prediction/candidates",
        json={"context": "占位", "prefix": "fu", "candidate": "符号"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert all(row["candidate"] != "符号" for row in payload["rows"])
    assert any(row["context"] == "占位" and row["prefix"] == "fu" and row["candidate"] == "服务" for row in payload["rows"])
    assert "占位\tfu\t符号" in (rime_dir / "context_prediction_deleted_candidates.tsv").read_text(encoding="utf-8")

    rebuilt_response = client.get(f"/api/device-entries/{entry_id}/rime/context-prediction/tree")
    assert rebuilt_response.status_code == 200
    assert all(row["candidate"] != "符号" for row in rebuilt_response.json()["rows"])


def test_local_entry_updates_rime_context_prediction_candidate(client, auth_user, test_device, tmp_path, monkeypatch):
    rime_dir = tmp_path / "Rime"
    rime_dir.mkdir()
    (rime_dir / "context_prediction_snapshot.tsv").write_text(
        "# context_key\tpinyin_prefix\tcandidate\tweight\tcomment\n"
        "占位\tfu\t服务\t2\t输入历史\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEYUN_RIME_USER_DIR", str(rime_dir))
    entry_id = _create_local_entry(client)

    response = client.patch(
        f"/api/device-entries/{entry_id}/rime/context-prediction/candidates",
        json={
            "original_context": "占位",
            "original_prefix": "fu",
            "original_candidate": "服务",
            "context": "占位",
            "prefix": "fu",
            "candidate": "负责",
            "weight": 30,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert any(row["context"] == "占位" and row["prefix"] == "fu" and row["candidate"] == "负责" and row["weight"] == 30 for row in payload["rows"])
    assert all(row["candidate"] != "服务" for row in payload["rows"])
    assert "占位\tfu\t负责\t30\t手动规则" in (rime_dir / "context_prediction.tsv").read_text(encoding="utf-8")
    assert "占位\tfu\t服务" in (rime_dir / "context_prediction_deleted_candidates.tsv").read_text(encoding="utf-8")


def test_local_entry_updates_rime_context_prediction_candidate_weight_only(client, auth_user, test_device, tmp_path, monkeypatch):
    rime_dir = tmp_path / "Rime"
    rime_dir.mkdir()
    (rime_dir / "context_prediction_model_counts.tsv").write_text(
        "# context_key\tpinyin_prefix\tcandidate\tcount\tlast_seen\tcomment\n"
        "占位\tfu\t服务\t2\t2026-05-13 12:00:00\t输入历史\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEYUN_RIME_USER_DIR", str(rime_dir))
    entry_id = _create_local_entry(client)

    response = client.patch(
        f"/api/device-entries/{entry_id}/rime/context-prediction/candidates",
        json={
            "original_context": "占位",
            "original_prefix": "fu",
            "original_candidate": "服务",
            "context": "占位",
            "prefix": "fu",
            "candidate": "服务",
            "weight": 30,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert any(row["context"] == "占位" and row["prefix"] == "fu" and row["candidate"] == "服务" and row["weight"] == 30 for row in payload["rows"])
    deleted_path = rime_dir / "context_prediction_deleted_candidates.tsv"
    assert not deleted_path.exists() or "占位\tfu\t服务" not in deleted_path.read_text(encoding="utf-8")


def test_local_entry_imports_article_into_rime_context_prediction(client, auth_user, test_device, tmp_path, monkeypatch):
    rime_dir = tmp_path / "Rime"
    rime_dir.mkdir()
    monkeypatch.setenv("CODEYUN_RIME_USER_DIR", str(rime_dir))
    entry_id = _create_local_entry(client)

    response = client.post(
        f"/api/device-entries/{entry_id}/rime/context-prediction/articles",
        json={
            "title": "测试文章",
            "content": "占位符号。占位服务。",
            "enabled": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["summary"]["article_count"] == 1
    assert payload["summary"]["enabled_count"] == 1
    assert payload["articles"][0]["title"] == "测试文章"
    assert payload["articles"][0]["row_count"] > 0

    tree_response = client.get(f"/api/device-entries/{entry_id}/rime/context-prediction/tree")
    assert tree_response.status_code == 200
    rows = tree_response.json()["rows"]
    assert any(row["context"] == "占位" and row["prefix"] == "fuhao" and row["candidate"] == "符号" for row in rows)


def test_local_entry_disables_imported_article_from_snapshot(client, auth_user, test_device, tmp_path, monkeypatch):
    rime_dir = tmp_path / "Rime"
    rime_dir.mkdir()
    monkeypatch.setenv("CODEYUN_RIME_USER_DIR", str(rime_dir))
    entry_id = _create_local_entry(client)

    create_response = client.post(
        f"/api/device-entries/{entry_id}/rime/context-prediction/articles",
        json={
            "title": "测试文章",
            "content": "占位符号。",
            "enabled": True,
        },
    )
    article_id = create_response.json()["articles"][0]["id"]

    update_response = client.patch(
        f"/api/device-entries/{entry_id}/rime/context-prediction/articles/{article_id}",
        json={"enabled": False},
    )

    assert update_response.status_code == 200
    payload = update_response.json()
    assert payload["articles"][0]["enabled"] is False
    assert payload["summary"]["enabled_count"] == 0

    tree_response = client.get(f"/api/device-entries/{entry_id}/rime/context-prediction/tree")
    assert tree_response.status_code == 200
    tree_payload = tree_response.json()
    assert tree_payload["status"] == "empty"
    assert tree_payload["rows"] == []


def test_local_main_imports_remote_device_history_as_single_article(client, session, auth_user, test_device, tmp_path, monkeypatch):
    rime_dir = tmp_path / "Rime"
    rime_dir.mkdir()
    monkeypatch.setenv("CODEYUN_RIME_USER_DIR", str(rime_dir))
    target_entry_id = _create_local_entry(client)
    source_entry = UserDevice(
        user_id=auth_user.id,
        device_id="mi15",
        mode="remote",
        name="mi15",
        server_url="http://mi15-device:8000",
        token="remote-token",
    )
    session.add(source_entry)
    session.commit()
    session.refresh(source_entry)

    contents = ["占位服务。", "占位服务。\n\n结构数据。"]
    request_count = {"value": 0}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, content: str):
            self._content = content

        def json(self):
            return {
                "available": True,
                "status": "ready",
                "message": "ok",
                "rime_dir": r"C:\Users\mi15\AppData\Roaming\Rime",
                "source": "context_prediction_history.log",
                "source_path": r"C:\Users\mi15\AppData\Roaming\Rime\context_prediction_history.log",
                "updated_at": 1,
                "files": [],
                "summary": {
                    "entry_count": 2,
                    "char_count": len(self._content),
                    "paragraph_count": len([item for item in self._content.split("\n\n") if item]),
                    "first_seen": "2026-05-13 10:00:00",
                    "last_seen": "2026-05-13 10:00:01",
                    "pending_row_count": 0,
                    "model_count_row_count": 0,
                    "truncated": False,
                    "limit": 200000,
                    "edited": False,
                    "saved_at": 0,
                    "base_event_count": 0,
                    "appended_event_count": 0,
                },
                "content": self._content,
            }

    def fake_request(method, url, headers=None, params=None, json=None, proxies=None, timeout=None, stream=False):
        assert method == "GET"
        assert url == "http://mi15-device:8000/api/rime/context-prediction/history-article"
        assert params == {"limit": 200000}
        assert headers["Authorization"] == "Bearer remote-token"
        content = contents[min(request_count["value"], len(contents) - 1)]
        request_count["value"] += 1
        return FakeResponse(content)

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    first_response = client.post(
        f"/api/device-entries/{target_entry_id}/rime/context-prediction/articles/from-device-history",
        json={"source_entry_id": source_entry.entry_id, "enabled": True},
    )
    assert first_response.status_code == 200
    first_payload = first_response.json()
    assert first_payload["summary"]["article_count"] == 1
    first_article = first_payload["articles"][0]
    assert first_article["title"] == "输入历史 · mi15"
    assert first_article["source_type"] == "device_history"
    assert first_article["source_key"] == "device_history:mi15"
    assert first_article["source_label"] == "输入历史 · mi15"

    second_response = client.post(
        f"/api/device-entries/{target_entry_id}/rime/context-prediction/articles/from-device-history",
        json={"source_entry_id": source_entry.entry_id, "enabled": True},
    )
    assert second_response.status_code == 200
    second_payload = second_response.json()
    assert second_payload["summary"]["article_count"] == 1
    assert second_payload["articles"][0]["id"] == first_article["id"]

    content_path = rime_dir / "context_prediction_articles" / f"{first_article['id']}.txt"
    assert content_path.read_text(encoding="utf-8") == contents[-1]
    manifest_text = (rime_dir / "context_prediction_articles.json").read_text(encoding="utf-8")
    assert manifest_text.count('"source_key": "device_history:mi15"') == 1
    assert (rime_dir / "context_prediction_snapshot.tsv").exists()


def test_remote_entry_forwards_rime_context_prediction_request(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-rime-device",
        mode="remote",
        name="Remote Rime Device",
        server_url="http://remote-device:8000",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {
                "available": True,
                "status": "ready",
                "message": "ok",
                "rime_dir": r"C:\Users\remote\AppData\Roaming\Rime",
                "source": "context_prediction_snapshot.tsv",
                "source_path": r"C:\Users\remote\AppData\Roaming\Rime\context_prediction_snapshot.tsv",
                "updated_at": 1,
                "files": [],
                "summary": {
                    "row_count": 1,
                    "context_count": 1,
                    "prefix_count": 1,
                    "candidate_count": 1,
                },
                "rows": [
                    {
                        "context": "占位",
                        "prefix": "fu",
                        "candidate": "符号",
                        "weight": 100,
                        "comment": "预测",
                    }
                ],
            }

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, proxies=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    response = client.get(
        f"/api/device-entries/{entry.entry_id}/rime/context-prediction/tree",
        params={"limit": 50},
    )

    assert response.status_code == 200
    assert response.json()["rows"][0]["candidate"] == "符号"
    assert captured["method"] == "GET"
    assert captured["url"] == "http://remote-device:8000/api/rime/context-prediction/tree"
    assert captured["params"] == {"limit": 50}
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["timeout"] == 20


def test_remote_entry_reports_unsupported_rime_context_prediction(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-old-device",
        mode="remote",
        name="Remote Old Device",
        server_url="http://old-device:8000",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    class FakeResponse:
        status_code = 404
        headers = {"content-type": "application/json"}
        text = '{"detail":"Not Found"}'

        def json(self):
            return {"detail": "Not Found"}

    monkeypatch.setattr("backend.api.device_entries.requests.request", lambda *args, **kwargs: FakeResponse())

    response = client.get(f"/api/device-entries/{entry.entry_id}/rime/context-prediction/tree")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["status"] == "remote_unsupported"
    assert "尚未部署" in payload["message"]
