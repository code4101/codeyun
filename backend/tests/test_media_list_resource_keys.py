from __future__ import annotations

from backend.core.jobs.resource_keys import device_media_list_resource_key


def payload(path: str) -> dict:
    return {
        "entry_id": "codepc_mf",
        "request": {"absolute_path": path, "recursive": True},
        "metadata": {"absolute_path": path},
    }


def test_device_media_scans_for_different_platform_paths_are_independent() -> None:
    pinterest = device_media_list_resource_key(payload(r"E:\data\m2510mn\2、pinterest"))
    pixiv = device_media_list_resource_key(payload(r"E:\data\m2510mn\2、pixiv"))

    assert pinterest != pixiv


def test_device_media_scan_key_normalizes_windows_path_spelling() -> None:
    left = device_media_list_resource_key(payload("E:\\data\\m2510mn\\2、Pixiv\\"))
    right = device_media_list_resource_key(payload("e:/data/m2510mn/2、pixiv"))

    assert left == right
