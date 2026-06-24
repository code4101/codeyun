import os
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

from PIL import Image
from sqlmodel import select

from backend.api.filesystem import DEVICE_ROOT_SENTINEL
from backend.models import DeviceFile, UserDevice
from backend.core.settings import get_settings


def test_local_entry_proxy_create_and_list_tasks(client, auth_user, test_device):
    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    create_resp = client.post(
        f"/api/device-entries/{entry_id}/task/create",
        json={
            "name": "Proxy Local Task",
            "command": "python -V",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["device_id"] == test_device["id"]

    list_resp = client.get(f"/api/device-entries/{entry_id}/task/")
    assert list_resp.status_code == 200
    tasks = list_resp.json()
    assert len(tasks) == 1
    assert tasks[0]["name"] == "Proxy Local Task"
    assert tasks[0]["device_id"] == test_device["id"]
    assert tasks[0]["status"]["running"] is False


def test_remote_entry_proxy_forwards_request(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-1",
        mode="remote",
        name="Remote Device",
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
            return [{"id": "task-1", "name": "Remote Task", "status": {"running": False}}]

        @property
        def content(self):
            return b'[]'

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["json"] = json
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.get(f"/api/device-entries/{entry.entry_id}/task/")
    assert resp.status_code == 200
    assert resp.json()[0]["name"] == "Remote Task"

    assert captured["method"] == "GET"
    assert captured["url"] == "http://remote-device:8000/api/task/"
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["timeout"] == 10


def test_local_entry_proxy_lists_and_deletes_device_images(client, auth_user, test_device):
    settings = get_settings()
    attachments_dir = settings.attachments_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)
    image_path = attachments_dir / "proxy-device-image.png"
    image_path.write_bytes(b"proxy-device-image")

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    roots_resp = client.get(f"/api/device-entries/{entry_id}/files/roots")
    assert roots_resp.status_code == 200
    root_keys = {item["key"] for item in roots_resp.json()["roots"]}
    assert "attachments" in root_keys

    images_resp = client.post(
        f"/api/device-entries/{entry_id}/files/images/list",
        json={"root": "attachments", "path": ""},
    )
    assert images_resp.status_code == 200
    images = images_resp.json()["images"]
    matched = next(item for item in images if item["name"] == "proxy-device-image.png")
    assert matched["path"] == "proxy-device-image.png"

    content_resp = client.get(
        f"/api/device-entries/{entry_id}/files/content",
        params={"root": "attachments", "path": "proxy-device-image.png"},
    )
    assert content_resp.status_code == 200
    assert content_resp.content == b"proxy-device-image"

    delete_resp = client.post(
        f"/api/device-entries/{entry_id}/files/delete",
        json={"root": "attachments", "path": "proxy-device-image.png"},
    )
    assert delete_resp.status_code == 200
    assert not image_path.exists()


def test_local_entry_proxy_supports_absolute_image_path(client, auth_user, test_device, tmp_path):
    image_dir = tmp_path / "absolute-images"
    image_dir.mkdir(parents=True, exist_ok=True)
    image_path = image_dir / "proxy-absolute-image.png"
    image_path.write_bytes(b"proxy-absolute-image")

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    images_resp = client.post(
        f"/api/device-entries/{entry_id}/files/images/list",
        json={"absolute_path": str(image_dir)},
    )
    assert images_resp.status_code == 200
    images = images_resp.json()["images"]
    matched = next(item for item in images if item["name"] == "proxy-absolute-image.png")
    assert matched["path"] == str(image_path)
    assert matched["absolute_path"] == str(image_path)

    content_resp = client.get(
        f"/api/device-entries/{entry_id}/files/content",
        params={"absolute_path": str(image_path)},
    )
    assert content_resp.status_code == 200
    assert content_resp.content == b"proxy-absolute-image"

    delete_resp = client.post(
        f"/api/device-entries/{entry_id}/files/delete",
        json={"absolute_path": str(image_path)},
    )
    assert delete_resp.status_code == 200
    assert not image_path.exists()


def test_local_entry_proxy_lists_device_media(client, auth_user, test_device, session):
    settings = get_settings()
    attachments_dir = settings.attachments_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)
    image_path = attachments_dir / "proxy-device-media.png"
    video_path = attachments_dir / "proxy-device-media.mp4"
    Image.new("RGB", (320, 180), color=(24, 48, 72)).save(image_path, format="PNG")
    video_path.write_bytes(b"proxy-device-media-video")

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    media_resp = client.post(
        f"/api/device-entries/{entry_id}/files/media/list",
        json={"root": "attachments", "path": ""},
    )
    assert media_resp.status_code == 200
    media_items = media_resp.json()["media"]

    matched_image = next(item for item in media_items if item["name"] == "proxy-device-media.png")
    matched_video = next(item for item in media_items if item["name"] == "proxy-device-media.mp4")
    assert matched_image["kind"] == "image"
    assert matched_image["weight"] == 0
    assert matched_image["width"] == 320
    assert matched_image["height"] == 180
    assert matched_video["kind"] == "video"
    assert matched_video["mime_type"] == "video/mp4"
    assert matched_video["weight"] == 0

    indexed_rows = session.exec(
        select(DeviceFile).where(DeviceFile.device_id == test_device["id"]).order_by(DeviceFile.absolute_path)
    ).all()
    indexed_by_path = {row.absolute_path: row for row in indexed_rows}
    assert str(image_path) in indexed_by_path
    assert str(video_path) in indexed_by_path
    assert indexed_by_path[str(image_path)].media_kind == "image"
    assert indexed_by_path[str(image_path)].width_px == 320
    assert indexed_by_path[str(image_path)].height_px == 180
    assert indexed_by_path[str(video_path)].media_kind == "video"
    assert indexed_by_path[str(video_path)].mime_type == "video/mp4"
    assert indexed_by_path[str(video_path)].file_size == video_path.stat().st_size


def test_local_entry_proxy_lists_only_current_directory_media_by_default(client, auth_user, test_device, tmp_path):
    browse_dir = tmp_path / "media-root"
    nested_dir = browse_dir / "nested"
    nested_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color=(24, 48, 72)).save(browse_dir / "top.png", format="PNG")
    Image.new("RGB", (64, 64), color=(72, 48, 24)).save(nested_dir / "deep.png", format="PNG")

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    media_resp = client.post(
        f"/api/device-entries/{entry_id}/files/media/list",
        json={"absolute_path": str(browse_dir)},
    )
    assert media_resp.status_code == 200
    assert {item["name"] for item in media_resp.json()["media"]} == {"top.png"}

    recursive_resp = client.post(
        f"/api/device-entries/{entry_id}/files/media/list",
        json={"absolute_path": str(browse_dir), "recursive": True},
    )
    assert recursive_resp.status_code == 200
    assert {item["name"] for item in recursive_resp.json()["media"]} == {"top.png", "deep.png"}


def test_local_entry_proxy_reuses_media_snapshot_for_follow_up_pages(
    client,
    auth_user,
    test_device,
    tmp_path,
    monkeypatch,
):
    browse_dir = tmp_path / "media-snapshot-root"
    nested_dir = browse_dir / "nested"
    nested_dir.mkdir(parents=True, exist_ok=True)
    for index in range(5):
        Image.new("RGB", (64, 64), color=(24 + index, 48, 72)).save(
            nested_dir / f"{index:02d}.png",
            format="PNG",
        )

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    import backend.api.filesystem as filesystem_module

    original_scandir = filesystem_module.os.scandir
    scandir_counter = {"count": 0}

    def counting_scandir(*args, **kwargs):
        scandir_counter["count"] += 1
        return original_scandir(*args, **kwargs)

    monkeypatch.setattr("backend.api.filesystem.os.scandir", counting_scandir)

    first_page_resp = client.post(
        f"/api/device-entries/{entry_id}/files/media/list",
        json={"absolute_path": str(browse_dir), "recursive": True, "limit": 2},
    )
    assert first_page_resp.status_code == 200
    first_page = first_page_resp.json()
    expected_total_bytes = sum(path.stat().st_size for path in nested_dir.glob("*.png"))
    assert first_page["total_count"] == 5
    assert first_page["total_bytes"] == expected_total_bytes
    assert first_page["limit"] == 2
    assert first_page["has_more"] is True
    assert first_page["next_offset"] == 2
    assert first_page["snapshot_id"]
    assert [item["name"] for item in first_page["media"]] == ["00.png", "01.png"]
    assert scandir_counter["count"] == 2

    second_page_resp = client.post(
        f"/api/device-entries/{entry_id}/files/media/list",
        json={
            "absolute_path": str(browse_dir),
            "recursive": True,
            "snapshot_id": first_page["snapshot_id"],
            "offset": 2,
            "limit": 2,
        },
    )
    assert second_page_resp.status_code == 200
    second_page = second_page_resp.json()
    assert second_page["snapshot_id"] == first_page["snapshot_id"]
    assert second_page["total_bytes"] == expected_total_bytes
    assert second_page["next_offset"] == 4
    assert [item["name"] for item in second_page["media"]] == ["02.png", "03.png"]
    assert scandir_counter["count"] == 2


def test_local_entry_proxy_applies_media_scan_limit_to_files_before_sorting(
    client,
    auth_user,
    test_device,
    tmp_path,
    monkeypatch,
):
    browse_dir = tmp_path / "media-scan-limit-root"
    first_dir = browse_dir / "01-first"
    second_dir = browse_dir / "02-second"
    first_dir.mkdir(parents=True, exist_ok=True)
    second_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color=(24, 48, 72)).save(first_dir / "a-deep.png", format="PNG")
    Image.new("RGB", (64, 64), color=(72, 48, 24)).save(second_dir / "b-deep.png", format="PNG")
    Image.new("RGB", (64, 64), color=(12, 24, 36)).save(browse_dir / "99-root.png", format="PNG")

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    import backend.api.filesystem as filesystem_module

    original_scandir = filesystem_module.os.scandir

    class SortedScandir:
        def __init__(self, *args, **kwargs):
            self._scandir = original_scandir(*args, **kwargs)

        def __enter__(self):
            iterator = self._scandir.__enter__()
            self._entries = sorted(list(iterator), key=lambda entry: entry.name)
            return iter(self._entries)

        def __exit__(self, exc_type, exc, tb):
            return self._scandir.__exit__(exc_type, exc, tb)

    monkeypatch.setattr("backend.api.filesystem.os.scandir", lambda *args, **kwargs: SortedScandir(*args, **kwargs))

    limited_resp = client.post(
        f"/api/device-entries/{entry_id}/files/media/list",
        json={"absolute_path": str(browse_dir), "recursive": True, "scan_limit": 2},
    )
    assert limited_resp.status_code == 200
    limited_payload = limited_resp.json()
    assert limited_payload["total_count"] == 2
    assert [item["name"] for item in limited_payload["media"]] == ["a-deep.png", "99-root.png"]

    full_resp = client.post(
        f"/api/device-entries/{entry_id}/files/media/list",
        json={"absolute_path": str(browse_dir), "recursive": True, "scan_limit": 10},
    )
    assert full_resp.status_code == 200
    assert [item["name"] for item in full_resp.json()["media"]] == ["a-deep.png", "b-deep.png", "99-root.png"]


def test_local_entry_proxy_lists_device_media_duration_ms(client, auth_user, test_device, monkeypatch, session):
    settings = get_settings()
    attachments_dir = settings.attachments_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)
    video_path = attachments_dir / "proxy-device-duration.mp4"
    video_path.write_bytes(b"fake-video")

    def fake_run(command, capture_output, check, timeout, text):
        assert command[0] == "ffprobe"

        class Result:
            stdout = '{"streams":[{"width":1920,"height":1080}],"format":{"duration":"21.18"}}'

        return Result()

    monkeypatch.setattr("backend.api.filesystem.shutil.which", lambda name: "ffprobe" if name == "ffprobe" else None)
    monkeypatch.setattr("backend.api.filesystem.subprocess.run", fake_run)

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    media_resp = client.post(
        f"/api/device-entries/{entry_id}/files/media/list",
        json={"root": "attachments", "path": ""},
    )
    assert media_resp.status_code == 200
    media_items = media_resp.json()["media"]
    matched_video = next(item for item in media_items if item["name"] == "proxy-device-duration.mp4")
    assert matched_video["duration_ms"] == 21180
    assert matched_video["width"] == 1920
    assert matched_video["height"] == 1080

    indexed = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == test_device["id"],
            DeviceFile.absolute_path == str(video_path),
        )
    ).one()
    assert indexed.duration_ms == 21180
    assert indexed.width_px == 1920
    assert indexed.height_px == 1080


def test_local_entry_proxy_lists_device_media_sorted_by_size(client, auth_user, test_device):
    settings = get_settings()
    attachments_dir = settings.attachments_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)
    small_path = attachments_dir / "proxy-size-small.bin.jpg"
    large_path = attachments_dir / "proxy-size-large.bin.jpg"
    small_path.write_bytes(b"small")
    large_path.write_bytes(b"large-file-content")

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    media_resp = client.post(
        f"/api/device-entries/{entry_id}/files/media/list",
        json={"root": "attachments", "path": "", "sort_mode": "size-desc"},
    )
    assert media_resp.status_code == 200
    ordered_names = [
        item["name"]
        for item in media_resp.json()["media"]
        if item["name"] in {"proxy-size-small.bin.jpg", "proxy-size-large.bin.jpg"}
    ]
    assert ordered_names == ["proxy-size-large.bin.jpg", "proxy-size-small.bin.jpg"]


def test_local_entry_proxy_lists_media_by_sort_program(client, auth_user, test_device):
    settings = get_settings()
    attachments_dir = settings.attachments_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)
    high_weight_old = attachments_dir / "proxy-sort-high-weight-old.jpg"
    medium_weight_new = attachments_dir / "proxy-sort-medium-weight-new.jpg"
    medium_weight_old = attachments_dir / "proxy-sort-medium-weight-old.jpg"
    for target_path in (high_weight_old, medium_weight_new, medium_weight_old):
        target_path.write_bytes(b"proxy-sort")

    old_timestamp = 1_700_000_000
    new_timestamp = old_timestamp + 120
    os.utime(high_weight_old, (old_timestamp, old_timestamp))
    os.utime(medium_weight_old, (old_timestamp + 30, old_timestamp + 30))
    os.utime(medium_weight_new, (new_timestamp, new_timestamp))

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    for relative_path, weight in (
        ("proxy-sort-high-weight-old.jpg", 2),
        ("proxy-sort-medium-weight-new.jpg", 1),
        ("proxy-sort-medium-weight-old.jpg", 1),
    ):
        weight_resp = client.post(
            f"/api/device-entries/{entry_id}/files/weight",
            json={"root": "attachments", "path": relative_path, "weight": weight},
        )
        assert weight_resp.status_code == 200

    media_resp = client.post(
        f"/api/device-entries/{entry_id}/files/media/list",
        json={
            "root": "attachments",
            "path": "",
            "sort_program": {
                "rules": [
                    {"field": "weight", "direction": "desc", "nulls": "last"},
                    {"field": "modified_at", "direction": "desc", "nulls": "last"},
                ]
            },
        },
    )
    assert media_resp.status_code == 200
    ordered_names = [
        item["name"]
        for item in media_resp.json()["media"]
        if item["name"] in {
            "proxy-sort-high-weight-old.jpg",
            "proxy-sort-medium-weight-new.jpg",
            "proxy-sort-medium-weight-old.jpg",
        }
    ]
    assert ordered_names == [
        "proxy-sort-high-weight-old.jpg",
        "proxy-sort-medium-weight-new.jpg",
        "proxy-sort-medium-weight-old.jpg",
    ]


def test_local_entry_proxy_lists_media_by_random_sort_program(client, auth_user, test_device, monkeypatch):
    settings = get_settings()
    attachments_dir = settings.attachments_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)

    random_a = attachments_dir / "proxy-random-a.jpg"
    random_b = attachments_dir / "proxy-random-b.jpg"
    random_c = attachments_dir / "proxy-random-c.jpg"
    for target_path in (random_a, random_b, random_c):
        target_path.write_bytes(b"proxy-random")

    def fake_populate_media_random_sort_values(entries, rules):
        if not any(rule.field == "random" for rule in rules):
            return
        rank_by_name = {
            "proxy-random-a.jpg": 30,
            "proxy-random-b.jpg": 10,
            "proxy-random-c.jpg": 20,
        }
        for entry in entries:
            entry["_random_order"] = rank_by_name.get(entry["name"], 999)

    monkeypatch.setattr(
        "backend.api.filesystem._populate_media_random_sort_values",
        fake_populate_media_random_sort_values,
    )

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    media_resp = client.post(
        f"/api/device-entries/{entry_id}/files/media/list",
        json={
            "root": "attachments",
            "path": "",
            "sort_program": {
                "rules": [
                    {"field": "random", "direction": "asc", "nulls": "last"},
                ]
            },
        },
    )
    assert media_resp.status_code == 200
    ordered_names = [
        item["name"]
        for item in media_resp.json()["media"]
        if item["name"] in {
            "proxy-random-a.jpg",
            "proxy-random-b.jpg",
            "proxy-random-c.jpg",
        }
    ]
    assert ordered_names == [
        "proxy-random-b.jpg",
        "proxy-random-c.jpg",
        "proxy-random-a.jpg",
    ]


def test_local_entry_proxy_lists_media_with_created_at(client, auth_user, test_device):
    settings = get_settings()
    attachments_dir = settings.attachments_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)

    image_path = attachments_dir / "proxy-created-at.jpg"
    image_path.write_bytes(b"proxy-created-at")

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    media_resp = client.post(
        f"/api/device-entries/{entry_id}/files/media/list",
        json={"root": "attachments", "path": ""},
    )
    assert media_resp.status_code == 200

    matched_image = next(item for item in media_resp.json()["media"] if item["name"] == "proxy-created-at.jpg")
    assert isinstance(matched_image.get("created_at"), int)
    assert matched_image["created_at"] > 0


def test_local_entry_proxy_updates_device_media_weight(client, auth_user, test_device, session):
    settings = get_settings()
    attachments_dir = settings.attachments_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)
    image_path = attachments_dir / "proxy-device-weight.png"
    image_path.write_bytes(b"proxy-device-weight")

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    update_resp = client.post(
        f"/api/device-entries/{entry_id}/files/weight",
        json={"root": "attachments", "path": "proxy-device-weight.png", "weight": 3},
    )
    assert update_resp.status_code == 200
    update_payload = update_resp.json()
    assert update_payload["ok"] is True
    assert update_payload["weight"] == 3
    assert update_payload["absolute_path"] == str(image_path)

    record = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == test_device["id"],
            DeviceFile.absolute_path == str(image_path),
        )
    ).one()
    assert record.weight == 3

    media_resp = client.post(
        f"/api/device-entries/{entry_id}/files/media/list",
        json={"root": "attachments", "path": ""},
    )
    assert media_resp.status_code == 200
    media_items = media_resp.json()["media"]
    matched_image = next(item for item in media_items if item["name"] == "proxy-device-weight.png")
    assert matched_image["weight"] == 3


def test_local_entry_proxy_syncs_device_file_records(client, auth_user, test_device, session):
    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    resp = client.post(
        f"/api/device-entries/{entry_id}/files/sync",
        json={
            "items": [
                {
                    "absolute_path": r"D:\sync\movie.mp4",
                    "content_hash": "hash-movie",
                    "file_size": 2048,
                    "media_kind": "video",
                    "mime_type": "video/mp4",
                }
            ]
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["processed_count"] == 1

    indexed = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == test_device["id"],
            DeviceFile.absolute_path == r"D:\sync\movie.mp4",
        )
    ).one()
    assert indexed.content_hash == "hash-movie"
    assert indexed.media_kind == "video"
    assert indexed.mime_type == "video/mp4"


def test_local_entry_proxy_scans_device_files_with_auto_hash_reuse(client, auth_user, test_device, session, tmp_path):
    scan_dir = tmp_path / "scan-root"
    scan_dir.mkdir(parents=True, exist_ok=True)
    original_path = scan_dir / "a.txt"
    original_path.write_text("alpha", encoding="utf-8")

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    first_resp = client.post(
        f"/api/device-entries/{entry_id}/files/scan",
        json={"absolute_path": str(scan_dir), "hash_mode": "always"},
    )
    assert first_resp.status_code == 200
    first_payload = first_resp.json()
    assert first_payload["processed_count"] == 1
    assert first_payload["hashed_count"] == 1
    first_hash = first_payload["items"][0]["content_hash"]
    assert first_hash

    renamed_path = scan_dir / "c.txt"
    original_path.rename(renamed_path)

    second_resp = client.post(
        f"/api/device-entries/{entry_id}/files/scan",
        json={"absolute_path": str(scan_dir), "hash_mode": "auto"},
    )
    assert second_resp.status_code == 200
    second_payload = second_resp.json()
    assert second_payload["processed_count"] == 1
    assert second_payload["hashed_count"] == 1
    assert second_payload["rebound_count"] == 1
    assert second_payload["items"][0]["content_hash"] == first_hash

    third_resp = client.post(
        f"/api/device-entries/{entry_id}/files/scan",
        json={"absolute_path": str(scan_dir), "hash_mode": "auto"},
    )
    assert third_resp.status_code == 200
    third_payload = third_resp.json()
    assert third_payload["processed_count"] == 1
    assert third_payload["hashed_count"] == 0
    assert third_payload["items"][0]["content_hash"] == first_hash

    rows = session.exec(
        select(DeviceFile).where(DeviceFile.device_id == test_device["id"]).order_by(DeviceFile.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].absolute_path == str(renamed_path)
    assert rows[0].content_hash == first_hash


def test_local_entry_proxy_scan_merges_weight_when_directory_rename_left_zero_weight_duplicate(
    client,
    auth_user,
    test_device,
    session,
    tmp_path,
):
    scan_dir = tmp_path / "scan-root"
    scan_dir.mkdir(parents=True, exist_ok=True)
    original_path = scan_dir / "001_artist" / "set"
    original_path.mkdir(parents=True, exist_ok=True)
    old_file = original_path / "image-01.png"
    old_file.write_text("alpha", encoding="utf-8")

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    first_resp = client.post(
        f"/api/device-entries/{entry_id}/files/scan",
        json={"absolute_path": str(scan_dir), "hash_mode": "always"},
    )
    assert first_resp.status_code == 200
    first_payload = first_resp.json()
    assert first_payload["processed_count"] == 1
    assert first_payload["hashed_count"] == 1
    first_hash = first_payload["items"][0]["content_hash"]
    assert first_hash

    indexed_old = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == test_device["id"],
            DeviceFile.absolute_path == str(old_file),
        )
    ).one()
    indexed_old.weight = 5
    session.add(indexed_old)
    session.commit()

    renamed_dir = scan_dir / "artist" / "set"
    renamed_dir.mkdir(parents=True, exist_ok=True)
    renamed_file = renamed_dir / "image-01.png"
    old_file.rename(renamed_file)

    stat_result = renamed_file.stat()
    session.add(
        DeviceFile(
            device_id=test_device["id"],
            absolute_path=str(renamed_file),
            last_known_path=str(renamed_file),
            file_size=stat_result.st_size,
            modified_at_ms=int(stat_result.st_mtime * 1000),
            media_kind="image",
            mime_type="image/png",
            match_status="matched",
            weight=0,
        )
    )
    session.commit()

    second_resp = client.post(
        f"/api/device-entries/{entry_id}/files/scan",
        json={"absolute_path": str(scan_dir), "hash_mode": "auto"},
    )
    assert second_resp.status_code == 200
    second_payload = second_resp.json()
    assert second_payload["processed_count"] == 1
    assert second_payload["hashed_count"] == 1

    current_record = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == test_device["id"],
            DeviceFile.absolute_path == str(renamed_file),
        )
    ).one()
    assert current_record.weight == 5
    assert current_record.content_hash == first_hash

    legacy_record = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == test_device["id"],
            DeviceFile.last_known_path == str(old_file),
        )
    ).one()
    assert legacy_record.absolute_path is None
    assert legacy_record.match_status == "dangling"
    assert legacy_record.weight == 5


def test_local_entry_proxy_serves_image_thumbnail(client, auth_user, test_device):
    settings = get_settings()
    attachments_dir = settings.attachments_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)
    image_path = attachments_dir / "proxy-device-thumb.png"

    image = Image.new("RGB", (1200, 800), color=(40, 120, 210))
    image.save(image_path, format="PNG")

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "뎠품샙포",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    thumb_resp = client.get(
        f"/api/device-entries/{entry_id}/files/thumbnail",
        params={"root": "attachments", "path": "proxy-device-thumb.png", "max_edge": 240},
    )
    assert thumb_resp.status_code == 200
    assert thumb_resp.headers["content-type"].startswith("image/")

    thumb_image = Image.open(BytesIO(thumb_resp.content))
    assert max(thumb_image.size) <= 240


def test_local_entry_proxy_serves_video_thumbnail_with_ffmpeg(client, auth_user, test_device, monkeypatch):
    settings = get_settings()
    attachments_dir = settings.attachments_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)
    video_path = attachments_dir / "proxy-device-thumb.mp4"
    video_path.write_bytes(b"fake-video")

    output = BytesIO()
    Image.new("RGB", (320, 180), color=(120, 50, 160)).save(output, format="JPEG")

    def fake_run(command, capture_output, check, timeout):
        assert command[0] == "ffmpeg"
        assert command[-1] == "pipe:1"

        class Result:
            stdout = output.getvalue()

        return Result()

    monkeypatch.setattr("backend.api.filesystem.shutil.which", lambda name: "ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr("backend.api.filesystem.subprocess.run", fake_run)

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    thumb_resp = client.get(
        f"/api/device-entries/{entry_id}/files/thumbnail",
        params={"root": "attachments", "path": "proxy-device-thumb.mp4", "max_edge": 240},
    )
    assert thumb_resp.status_code == 200
    assert thumb_resp.headers["content-type"].startswith("image/jpeg")

    thumb_image = Image.open(BytesIO(thumb_resp.content))
    assert thumb_image.size == (320, 180)


def test_local_entry_proxy_lists_absolute_directory(client, auth_user, test_device, tmp_path):
    browse_dir = tmp_path / "browse-root"
    child_dir = browse_dir / "nested"
    child_dir.mkdir(parents=True, exist_ok=True)
    file_path = browse_dir / "notes.txt"
    file_path.write_text("hello", encoding="utf-8")

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    resp = client.post(
        f"/api/device-entries/{entry_id}/files/list_dir",
        json={"absolute_path": str(browse_dir)},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["absolute_path"] == str(browse_dir)
    assert payload["current_path"] == str(browse_dir)

    items = {item["name"]: item for item in payload["items"]}
    assert items["nested"]["is_dir"] is True
    assert items["nested"]["path"] == str(child_dir)
    assert items["notes.txt"]["is_dir"] is False
    assert items["notes.txt"]["path"] == str(file_path)


def test_local_entry_proxy_lists_device_root_directory(client, auth_user, test_device, monkeypatch):
    monkeypatch.setattr(
        "backend.api.filesystem._list_system_root_entries",
        lambda: [
            {
                "name": "C:",
                "path": r"C:\\",
                "is_dir": True,
                "size": None,
                "modified_at": None,
            },
            {
                "name": "D:",
                "path": r"D:\\",
                "is_dir": True,
                "size": None,
                "modified_at": None,
            },
        ],
    )

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    resp = client.post(
        f"/api/device-entries/{entry_id}/files/list_dir",
        json={"absolute_path": DEVICE_ROOT_SENTINEL},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["absolute_path"] == DEVICE_ROOT_SENTINEL
    assert payload["current_path"] == DEVICE_ROOT_SENTINEL
    assert payload["items"] == [
        {
            "name": "C:",
            "path": r"C:\\",
            "is_dir": True,
            "size": None,
            "modified_at": None,
        },
        {
            "name": "D:",
            "path": r"D:\\",
            "is_dir": True,
            "size": None,
            "modified_at": None,
        },
    ]


def test_local_entry_proxy_sorts_directories_by_indexed_recursive_bytes(
    client,
    auth_user,
    test_device,
    session,
    tmp_path,
    monkeypatch,
):
    browse_dir = tmp_path / "browse-root"
    large_dir = browse_dir / "large"
    small_dir = browse_dir / "small"
    nested_dir = large_dir / "nested"
    nested_dir.mkdir(parents=True, exist_ok=True)
    small_dir.mkdir(parents=True, exist_ok=True)
    (browse_dir / "notes.txt").write_text("root", encoding="utf-8")

    large_file = large_dir / "cover.jpg"
    nested_large_file = nested_dir / "detail.jpg"
    small_file = small_dir / "thumb.jpg"
    large_file.write_bytes(b"a" * 10)
    nested_large_file.write_bytes(b"b" * 30)
    small_file.write_bytes(b"c" * 5)

    monkeypatch.setattr("backend.api.filesystem.get_device_id", lambda: test_device["id"])

    session.add_all(
        [
            DeviceFile(
                device_id=test_device["id"],
                absolute_path=str(large_file),
                last_known_path=str(large_file),
                file_size=10,
                modified_at_ms=1000,
                match_status="matched",
                weight=1,
            ),
            DeviceFile(
                device_id=test_device["id"],
                absolute_path=str(nested_large_file),
                last_known_path=str(nested_large_file),
                file_size=30,
                modified_at_ms=3000,
                match_status="matched",
                weight=2,
            ),
            DeviceFile(
                device_id=test_device["id"],
                absolute_path=str(small_file),
                last_known_path=str(small_file),
                file_size=5,
                modified_at_ms=2000,
                match_status="matched",
                weight=0,
            ),
        ]
    )
    session.commit()

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    resp = client.post(
        f"/api/device-entries/{entry_id}/files/list_dir",
        json={
            "absolute_path": str(browse_dir),
            "sort_program": {
                "rules": [
                    {
                        "field": "recursive_total_bytes",
                        "direction": "desc",
                        "nulls": "last",
                    }
                ]
            },
        },
    )
    assert resp.status_code == 200
    payload = resp.json()

    assert [item["name"] for item in payload["items"][:3]] == ["large", "small", "notes.txt"]
    assert payload["items"][0]["recursive_total_bytes"] == 40
    assert payload["items"][0]["recursive_file_count"] == 2
    assert payload["items"][0]["latest_descendant_modified_at"] == 3000
    assert payload["items"][0]["max_weight"] == 2
    assert payload["items"][0]["weighted_file_count"] == 2
    assert payload["items"][1]["recursive_total_bytes"] == 5
    assert payload["items"][1]["recursive_file_count"] == 1


def test_remote_entry_proxy_forwards_files_request(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-1",
        mode="remote",
        name="Remote Device",
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
            return {"images": []}

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["json"] = json
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/images/list",
        json={"root": "attachments", "path": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["images"] == []

    assert captured["method"] == "POST"
    assert captured["url"] == "http://remote-device:8000/api/fs/images/list"
    assert captured["json"] == {"root": "attachments", "path": ""}
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["timeout"] == 10


def test_remote_entry_proxy_forwards_device_root_directory_request(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-1",
        mode="remote",
        name="Remote Device",
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
            return {"items": []}

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["json"] = json
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/list_dir",
        json={"absolute_path": DEVICE_ROOT_SENTINEL},
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []

    assert captured["method"] == "POST"
    assert captured["url"] == "http://remote-device:8000/api/fs/scoped/list_dir"
    assert captured["json"] == {"path": "", "absolute_path": DEVICE_ROOT_SENTINEL}
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["timeout"] == 10


def test_remote_entry_proxy_forwards_media_request(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-1",
        mode="remote",
        name="Remote Device",
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
            return {"media": []}

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["json"] = json
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/media/list",
        json={"root": "attachments", "path": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["media"] == []

    assert captured["method"] == "POST"
    assert captured["url"] == "http://remote-device:8000/api/fs/media/list"
    assert captured["json"] == {"root": "attachments", "path": ""}
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["timeout"] == 10


def test_local_entry_proxy_reveals_file_in_folder(client, auth_user, test_device, monkeypatch):
    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    captured = {}

    def fake_reveal_scoped_entry(root_key=None, rel_path="", *, absolute_path=""):
        captured["root"] = root_key
        captured["path"] = rel_path
        captured["absolute_path"] = absolute_path
        return {
            "ok": True,
            "supported": True,
            "launched": True,
            "method": "explorer",
            "detail": "",
            "root": root_key,
            "path": rel_path,
            "absolute_path": absolute_path,
            "target_path": absolute_path,
            "directory_path": r"C:\\demo",
        }

    monkeypatch.setattr("backend.api.device_entries.reveal_scoped_entry", fake_reveal_scoped_entry)

    resp = client.post(
        f"/api/device-entries/{entry_id}/files/reveal",
        json={"absolute_path": r"C:\\demo\\sample.jpg"},
    )
    assert resp.status_code == 200
    assert resp.json()["launched"] is True
    assert captured == {
        "root": None,
        "path": "",
        "absolute_path": r"C:\\demo\\sample.jpg",
    }


def test_remote_entry_proxy_forwards_media_sort_mode(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-sort",
        mode="remote",
        name="Remote Device",
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
            return {"media": []}

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/media/list",
        json={"root": "attachments", "path": "", "sort_mode": "size-desc"},
    )
    assert resp.status_code == 200
    assert captured["json"] == {"root": "attachments", "path": "", "sort_mode": "size-desc"}


def test_remote_entry_proxy_forwards_reveal_file_request(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-reveal",
        mode="remote",
        name="Remote Device",
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
            return {"ok": True, "supported": True, "launched": True}

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["json"] = json
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/reveal",
        json={"absolute_path": r"C:\\demo\\sample.jpg"},
    )
    assert resp.status_code == 200
    assert captured["url"] == "http://remote-device:8000/api/fs/reveal"
    assert captured["json"] == {"path": "", "absolute_path": r"C:\\demo\\sample.jpg"}


def test_remote_entry_proxy_forwards_media_sort_program(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-sort-program",
        mode="remote",
        name="Remote Device",
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
            return {"media": []}

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/media/list",
        json={
            "root": "attachments",
            "path": "",
            "sort_program": {
                "rules": [
                    {"field": "random", "direction": "asc", "nulls": "last"},
                    {"field": "modified_at", "direction": "desc", "nulls": "last"},
                ]
            },
        },
    )
    assert resp.status_code == 200
    assert captured["json"] == {
        "root": "attachments",
        "path": "",
        "sort_program": {
            "rules": [
                {"field": "random", "direction": "asc", "nulls": "last"},
                {"field": "modified_at", "direction": "desc", "nulls": "last"},
            ]
        },
    }


def test_remote_entry_proxy_forwards_media_recursive_flag(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-recursive",
        mode="remote",
        name="Remote Device",
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
            return {"media": []}

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/media/list",
        json={"absolute_path": r"C:\\demo", "recursive": True},
    )
    assert resp.status_code == 200
    assert captured["json"] == {"path": "", "absolute_path": r"C:\\demo", "recursive": True}


def test_remote_entry_proxy_forwards_media_snapshot_pagination(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-pagination",
        mode="remote",
        name="Remote Device",
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
            return {"media": [], "snapshot_id": "snap-1", "offset": 50, "limit": 50}

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/media/list",
        json={
            "absolute_path": r"C:\\demo",
            "scan_limit": 5000,
            "snapshot_id": "snap-1",
            "offset": 50,
            "limit": 50,
        },
    )
    assert resp.status_code == 200
    assert captured["json"] == {
        "path": "",
        "absolute_path": r"C:\\demo",
        "scan_limit": 5000,
        "snapshot_id": "snap-1",
        "offset": 50,
        "limit": 50,
    }


def test_remote_entry_proxy_indexes_media_records(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-2",
        mode="remote",
        name="Remote Indexed Device",
        server_url="http://remote-indexed-device:8000",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {
                "root": "attachments",
                "path": "",
                "absolute_path": "",
                "media": [
                    {
                        "id": "clip.mp4:10:20",
                        "name": "clip.mp4",
                        "path": "videos/clip.mp4",
                        "absolute_path": "",
                        "relative_path": "videos/clip.mp4",
                        "folder_path": "videos",
                        "size": 2048,
                        "modified_at": 1700000000123,
                        "duration_ms": 4500,
                        "kind": "video",
                        "mime_type": "video/mp4",
                    }
                ],
            }

        @property
        def content(self):
            return b"{}"

    monkeypatch.setattr("backend.api.device_entries.requests.request", lambda *args, **kwargs: FakeResponse())

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/media/list",
        json={"root": "attachments", "path": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["media"][0]["name"] == "clip.mp4"

    indexed = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == "remote-device-2",
            DeviceFile.absolute_path == "root://attachments/videos/clip.mp4",
        )
    ).one()
    assert indexed.last_known_path == "root://attachments/videos/clip.mp4"
    assert indexed.media_kind == "video"
    assert indexed.mime_type == "video/mp4"
    assert indexed.modified_at_ms == 1700000000123
    assert indexed.duration_ms == 4500
    assert indexed.file_size == 2048


def test_remote_entry_proxy_updates_device_media_weight_and_mirrors_local_cache(
    client,
    session,
    auth_user,
    monkeypatch,
):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-weight",
        mode="remote",
        name="Remote Weight Device",
        server_url="http://remote-weight-device:8000",
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
                "ok": True,
                "root": "attachments",
                "path": "videos/clip.mp4",
                "absolute_path": "/srv/media/videos/clip.mp4",
                "weight": 4,
            }

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/weight",
        json={"root": "attachments", "path": "videos/clip.mp4", "weight": 4},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["weight"] == 4

    assert captured["method"] == "POST"
    assert captured["url"] == "http://remote-weight-device:8000/api/fs/weight"
    assert captured["json"] == {
        "root": "attachments",
        "path": "videos/clip.mp4",
        "weight": 4,
    }
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["timeout"] == 10

    indexed = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == "remote-device-weight",
            DeviceFile.absolute_path == "root://attachments/videos/clip.mp4",
        )
    ).one()
    assert indexed.weight == 4


def test_remote_entry_proxy_syncs_device_files_and_mirrors_local_cache(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-sync",
        mode="remote",
        name="Remote Sync Device",
        server_url="http://remote-sync-device:8000",
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
                "ok": True,
                "device_id": "remote-device-sync",
                "processed_count": 1,
                "created_count": 1,
                "rebound_count": 0,
                "updated_count": 0,
                "dangling_count": 0,
                "records": [],
            }

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/sync",
        json={
            "items": [
                {
                    "absolute_path": "root://attachments/videos/clip.mp4",
                    "content_hash": "remote-hash",
                    "file_size": 4096,
                    "media_kind": "video",
                    "mime_type": "video/mp4",
                }
            ],
            "mark_missing_as_dangling": True,
            "scope_prefixes": ["root://attachments"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    assert captured["method"] == "POST"
    assert captured["url"] == "http://remote-sync-device:8000/api/fs/device-files/sync"
    assert captured["json"] == {
        "items": [
            {
                "absolute_path": "root://attachments/videos/clip.mp4",
                "hash_algorithm": "sha256",
                "content_hash": "remote-hash",
                "file_size": 4096,
                "media_kind": "video",
                "mime_type": "video/mp4",
            }
        ],
        "mark_missing_as_dangling": True,
        "scope_prefixes": ["root://attachments"],
    }
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["timeout"] == 10

    indexed = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == "remote-device-sync",
            DeviceFile.absolute_path == "root://attachments/videos/clip.mp4",
        )
    ).one()
    assert indexed.content_hash == "remote-hash"
    assert indexed.file_size == 4096


def test_remote_entry_proxy_scans_device_files_and_mirrors_local_cache(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-scan",
        mode="remote",
        name="Remote Scan Device",
        server_url="http://remote-scan-device:8000",
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
                "ok": True,
                "device_id": "remote-device-scan",
                "root": "attachments",
                "path": "videos",
                "absolute_path": "",
                "is_directory": True,
                "recursive": True,
                "hash_mode": "auto",
                "processed_count": 1,
                "hashed_count": 1,
                "created_count": 0,
                "rebound_count": 1,
                "updated_count": 0,
                "dangling_count": 0,
                "items": [
                    {
                        "name": "clip.mp4",
                        "path": "videos/clip.mp4",
                        "absolute_path": "",
                        "relative_path": "clip.mp4",
                        "folder_path": "",
                        "size": 4096,
                        "modified_at": 1700000000123,
                        "media_kind": "video",
                        "mime_type": "video/mp4",
                        "content_hash": "remote-scan-hash",
                        "hash_algorithm": "sha256",
                        "hashed": True,
                        "match_status": "matched",
                        "weight": 0,
                    }
                ],
            }

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/scan",
        json={"root": "attachments", "path": "videos", "hash_mode": "auto"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["hashed_count"] == 1

    assert captured["method"] == "POST"
    assert captured["url"] == "http://remote-scan-device:8000/api/fs/device-files/scan"
    assert captured["json"] == {
        "root": "attachments",
        "path": "videos",
        "recursive": True,
        "hash_mode": "auto",
        "mark_missing_as_dangling": True,
    }
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["timeout"] == 30

    indexed = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == "remote-device-scan",
            DeviceFile.absolute_path == "root://attachments/videos/clip.mp4",
        )
    ).one()
    assert indexed.content_hash == "remote-scan-hash"
    assert indexed.file_size == 4096


def test_remote_entry_proxy_forwards_directory_request(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-1",
        mode="remote",
        name="Remote Device",
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
            return {"items": []}

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["json"] = json
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/list_dir",
        json={"absolute_path": r"D:\home\chenkunze"},
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []

    assert captured["method"] == "POST"
    assert captured["url"] == "http://remote-device:8000/api/fs/scoped/list_dir"
    assert captured["json"] == {"path": "", "absolute_path": r"D:\home\chenkunze"}
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["timeout"] == 10


def test_remote_entry_proxy_forwards_directory_sort_program(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-1",
        mode="remote",
        name="Remote Device",
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
            return {"items": []}

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/list_dir",
        json={
            "absolute_path": r"D:\home\chenkunze",
            "sort_program": {
                "rules": [
                    {
                        "field": "recursive_total_bytes",
                        "direction": "desc",
                        "nulls": "last",
                    }
                ]
            },
        },
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []
    assert captured["method"] == "POST"
    assert captured["url"] == "http://remote-device:8000/api/fs/scoped/list_dir"
    assert captured["json"] == {
        "path": "",
        "absolute_path": r"D:\home\chenkunze",
        "sort_program": {
            "rules": [
                {
                    "field": "recursive_total_bytes",
                    "direction": "desc",
                    "nulls": "last",
                }
            ]
        },
    }


def test_remote_entry_proxy_forwards_absolute_files_request(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-1",
        mode="remote",
        name="Remote Device",
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
            return {"images": []}

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["json"] = json
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/images/list",
        json={"absolute_path": r"D:\Pictures\Wallpapers"},
    )
    assert resp.status_code == 200
    assert resp.json()["images"] == []

    assert captured["method"] == "POST"
    assert captured["url"] == "http://remote-device:8000/api/fs/images/list"
    assert captured["json"] == {"path": "", "absolute_path": r"D:\Pictures\Wallpapers"}
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["timeout"] == 10


def test_remote_entry_proxy_streams_file_content(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-1",
        mode="remote",
        name="Remote Device",
        server_url="http://remote-device:8000",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    captured = {}

    class FakeResponse:
        status_code = 206
        headers = {
            "content-type": "image/jpeg",
            "content-length": "4",
            "accept-ranges": "bytes",
            "content-range": "bytes 0-3/4",
        }

        def iter_content(self, chunk_size=1):
            captured["chunk_size"] = chunk_size
            yield b"ab"
            yield b"cd"

        def close(self):
            captured["closed"] = True

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["json"] = json
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.get(
        f"/api/device-entries/{entry.entry_id}/files/content",
        params={"root": "attachments", "path": "photo.jpg"},
        headers={"Range": "bytes=0-3"},
    )
    assert resp.status_code == 206
    assert resp.content == b"abcd"
    assert resp.headers["content-range"] == "bytes 0-3/4"

    assert captured["method"] == "GET"
    assert captured["url"] == "http://remote-device:8000/api/fs/content"
    assert captured["params"] == {"path": "photo.jpg", "root": "attachments"}
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["headers"]["Range"] == "bytes=0-3"
    assert captured["stream"] is True
    assert captured["timeout"] == 10
    assert captured["closed"] is True


def test_local_entry_proxy_generates_stream_url(client, auth_user, test_device):
    settings = get_settings()
    attachments_dir = settings.attachments_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)
    video_path = attachments_dir / "proxy-device-stream.mp4"
    video_path.write_bytes(b"stream-video-content")

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    stream_url_resp = client.post(
        f"/api/device-entries/{entry_id}/files/stream-url",
        json={"root": "attachments", "path": "proxy-device-stream.mp4"},
    )
    assert stream_url_resp.status_code == 200

    stream_url = stream_url_resp.json()["url"]
    parsed = urlsplit(stream_url)
    params = parse_qs(parsed.query)
    assert params["token"][0]

    stream_resp = client.get(stream_url)
    assert stream_resp.status_code == 200
    assert stream_resp.content == b"stream-video-content"


def test_remote_entry_proxy_stream_url_forwards_range_requests(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-1",
        mode="remote",
        name="Remote Device",
        server_url="http://remote-device:8000",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    stream_url_resp = client.post(
        f"/api/device-entries/{entry.entry_id}/files/stream-url",
        json={"root": "attachments", "path": "movie.webm"},
    )
    assert stream_url_resp.status_code == 200
    stream_url = stream_url_resp.json()["url"]

    captured = {}

    class FakeResponse:
        status_code = 206
        headers = {
            "content-type": "video/webm",
            "content-length": "4",
            "accept-ranges": "bytes",
            "content-range": "bytes 0-3/4",
        }

        def iter_content(self, chunk_size=1):
            captured["chunk_size"] = chunk_size
            yield b"ab"
            yield b"cd"

        def close(self):
            captured["closed"] = True

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["json"] = json
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.get(stream_url, headers={"Range": "bytes=0-3"})
    assert resp.status_code == 206
    assert resp.content == b"abcd"
    assert resp.headers["content-range"] == "bytes 0-3/4"

    assert captured["method"] == "GET"
    assert captured["url"] == "http://remote-device:8000/api/fs/content"
    assert captured["params"] == {"path": "movie.webm", "root": "attachments"}
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["headers"]["Range"] == "bytes=0-3"
    assert captured["stream"] is True
    assert captured["timeout"] == 10
    assert captured["closed"] is True


def test_remote_entry_proxy_forwards_thumbnail_request(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-1",
        mode="remote",
        name="Remote Device",
        server_url="http://remote-device:8000",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg"}

        @property
        def content(self):
            return b"thumb"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["json"] = json
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.get(
        f"/api/device-entries/{entry.entry_id}/files/thumbnail",
        params={"absolute_path": r"D:\Pictures\Wallpapers\sky.png", "max_edge": 256, "quality": 80},
    )
    assert resp.status_code == 200
    assert resp.content == b"thumb"

    assert captured["method"] == "GET"
    assert captured["url"] == "http://remote-device:8000/api/fs/thumbnail"
    assert captured["params"] == {
        "path": "",
        "max_edge": 256,
        "quality": 80,
        "absolute_path": r"D:\Pictures\Wallpapers\sky.png",
    }
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["stream"] is False
    assert captured["timeout"] == 20


def test_local_entry_proxy_accepts_manual_video_cover(client, auth_user, test_device, session):
    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    image = Image.new("RGB", (128, 72), color=(32, 96, 180))
    image_buffer = BytesIO()
    image.save(image_buffer, format="JPEG")

    resp = client.post(
        f"/api/device-entries/{entry_id}/files/cover",
        data={"absolute_path": r"D:\Videos\sample.mp4"},
        files={"cover": ("cover.jpg", image_buffer.getvalue(), "image/jpeg")},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["cover_source"] == "manual"
    assert payload["absolute_path"] == r"D:\Videos\sample.mp4"

    record = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == test_device["id"],
            DeviceFile.absolute_path == r"D:\Videos\sample.mp4",
        )
    ).one()
    assert record.cover_source == "manual"
    assert record.cover_path


def test_local_entry_proxy_thumbnail_prefers_cached_cover(client, auth_user, test_device, monkeypatch):
    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    image = Image.new("RGB", (96, 54), color=(18, 120, 200))
    image_buffer = BytesIO()
    image.save(image_buffer, format="JPEG")

    cover_resp = client.post(
        f"/api/device-entries/{entry_id}/files/cover",
        data={"absolute_path": r"D:\Videos\cached-cover.mp4"},
        files={"cover": ("cover.jpg", image_buffer.getvalue(), "image/jpeg")},
    )
    assert cover_resp.status_code == 200

    def fail_if_thumbnail_is_regenerated(*args, **kwargs):
        raise AssertionError("thumbnail should come from cached cover")

    monkeypatch.setattr("backend.api.device_entries.build_thumbnail_response", fail_if_thumbnail_is_regenerated)

    thumb_resp = client.get(
        f"/api/device-entries/{entry_id}/files/thumbnail",
        params={"absolute_path": r"D:\Videos\cached-cover.mp4"},
    )
    assert thumb_resp.status_code == 200
    assert thumb_resp.headers["content-type"].startswith("image/jpeg")
