from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.core.frontierbench import collect_snapshot, list_all, rejection_rows


def item(**changes):
    return {"id": 12, "taskId": 72486, "appId": 7392, "batchId": 5,
            "taskName": "frontier-bench-fw", "status": 3, "size": 1,
            "rejectReason": "Context\t未达标", "gmtModified": "2026-10-20", **changes}


def detail(**changes):
    return {"size": 1, "dataList": [{"dataId": "data-12", "file_names": [
        "FB-261011-0002_example.zip"]}], "rejectRecords": [
            {"rejectReason": "old", "rejectTime": "2026-10-17", "stage": "CHECK"},
            {"rejectReason": "Context\t未达标", "rejectTime": "2026-10-20", "stage": "CHECK"}],
        **changes}


def reader(rows, *, change_after_detail=False, catalog=True):
    state = {"reads": 0}

    def get(url):
        if "/center/tasks?" in url:
            projects = [{"name": "frontier-bench-fw", "taskId": 72486, "appId": 7392}]
            return {"recordCount": int(catalog), "data": projects if catalog else []}
        if "/subTasks?" in url:
            state["reads"] += 1
            output = deepcopy(rows)
            if change_after_detail and state["reads"] > 1:
                output[0]["status"] = 1
            return {"recordCount": len(output), "data": output}
        return detail()

    return get


def test_snapshot_preserves_reason_and_stable_question_identity():
    snapshot = collect_snapshot(reader([item()]))
    row = snapshot["rows"][0]
    assert row["question_id"] == "FB-261011-0002"
    assert row["latest_reject_reason_raw"] == "Context\t未达标"
    assert row["reject_record_count"] == 2
    assert snapshot["crawler"]["source_counts"] == {"3": 1}
    assert snapshot["source_total"] == 1
    assert snapshot["projects"] == [{"project_id": 7392, "task_id": 72486,
                                     "project_name": "frontier-bench-fw"}]
    assert snapshot["complete"] is True
    assert "rejecterName" not in str(snapshot)


def test_partial_or_changed_snapshot_never_publishes():
    with pytest.raises(ValueError, match="状态发生变化"):
        collect_snapshot(reader([item()], change_after_detail=True))
    with pytest.raises(ValueError, match="未找到目标系列"):
        collect_snapshot(reader([], catalog=False))


def test_zero_rejections_allowed_for_existing_visible_project():
    snapshot = collect_snapshot(reader([item(status=1)]))
    assert snapshot["rows"] == []
    assert snapshot["crawler"]["source_counts"] == {"1": 1}
    assert len(snapshot["projects"]) == 1  # a zero-row snapshot still declares coverage


def test_different_subtask_ids_cannot_duplicate_one_question():
    with pytest.raises(ValueError, match="重复题目"):
        collect_snapshot(reader([item(), item(id=13)]))


@pytest.mark.parametrize("changes, message", [
    ({"size": 2}, "数据粒度"),
    ({"dataList": []}, "数据粒度"),
    ({"rejectRecords": [{"rejectReason": "changed"}]}, "不一致"),
    ({"dataList": [{"dataId": "data", "file_names": [
        "FB-261011-0002_and_FB-261011-0003.zip"]}]}, "有歧义"),
])
def test_invalid_details_are_not_silently_truncated(changes, message):
    with pytest.raises(ValueError, match=message):
        rejection_rows(item(), detail(**changes))


def test_fallback_data_identity_when_filename_has_no_question_id():
    output = rejection_rows(item(), detail(dataList=[{"dataId": "stable-data", "file_names": ["unknown.zip"]}]))
    assert output[0]["question_id"] == ""
    assert output[0]["data_id"] == "stable-data"


@pytest.mark.parametrize("pages,message", [
    ([{"recordCount": 2, "data": [item()]}, {"recordCount": 2, "data": []}], "提前结束"),
    ([{"recordCount": 2, "data": [item(), item()]}], "重复"),
    ([{"recordCount": 2, "data": [item()]}, {"recordCount": 3, "data": [item(id=13)]}], "总数发生变化"),
])
def test_list_pagination_rejects_incomplete_or_duplicated_results(pages, message):
    def get(url):
        page = int(parse_qs(urlsplit(url).query)["page"][0])
        return pages[page - 1]
    with pytest.raises(ValueError, match=message):
        list_all(get, "/api/v1/label/center/subTasks")


def test_publish_rejects_external_destination_before_loading_credentials():
    from scripts.frontierbench_sync import publish
    with pytest.raises(ValueError, match="同源"):
        publish({}, peer_name="CodeHub", api_path="https://another.example/api/import")


def test_publish_requires_paired_identity_and_does_not_retry_write(monkeypatch, tmp_path):
    from scripts import frontierbench_sync as cli
    from lingnet import config, peers

    calls = []
    class Store:
        def __init__(self, _):
            pass
        def peer(self, name):
            assert name == "CodeHub"
            return {"url": "https://codehub.example/api/lingnet", "token": "test-token", "node_id": "expected"}
    class Client:
        node = "wrong"
        def __init__(self, url, token, **kwargs):
            self.url = url
        def manifest(self):
            return {"enabled": True, "device_id": self.node}
        def request(self, method, path, body):
            calls.append((self.url, method, path, body))
            raise TimeoutError("unknown delivery; inspect server before retry")
    monkeypatch.setattr(config, "ConfigStore", Store)
    monkeypatch.setattr(peers, "PeerClient", Client)
    monkeypatch.setattr(cli, "get_settings", lambda: type("Settings", (), {"data_dir": tmp_path})())
    monkeypatch.setenv("NO_PROXY", "existing.example")
    monkeypatch.setenv("no_proxy", "existing.example")
    with pytest.raises(RuntimeError, match="身份"):
        cli.publish({}, peer_name="CodeHub", api_path="/api/example/snapshots")
    assert calls == []
    Client.node = "expected"
    with pytest.raises(TimeoutError):
        cli.publish({"rows": []}, peer_name="CodeHub", api_path="/api/example/snapshots")
    assert calls == [("https://codehub.example", "POST", "/api/example/snapshots", {"rows": []})]
