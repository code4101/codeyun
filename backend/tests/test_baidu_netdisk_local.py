from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from backend.core.baidu_netdisk import (
    TransferLedger,
    collect_download,
    describe_upload_source,
    load_manifest,
    snapshot_dir,
    validate_remote_dir,
    write_manifest,
)
from backend.core.baidu_netdisk.bridge import parse_bridge_body


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _collect(root: Path, before, target: Path, data: bytes, name="a.zip", token=None, sha=True):
    return collect_download(download_root=root, before=before, expected_name=name, expected_size=len(data),
                            target_dir=target, expected_sha256=_sha(data) if sha else None, stable_token=token)


def test_existing_same_name_file_is_not_mistaken_for_new_download(tmp_path: Path) -> None:
    root, target = tmp_path / "dl", tmp_path / "biz"
    root.mkdir()
    (root / "a.zip").write_bytes(b"old-original")
    before = snapshot_dir(root)
    result = _collect(root, before, target, b"old-original")
    assert result.status == "not_found"
    assert (root / "a.zip").read_bytes() == b"old-original"


def test_partial_then_stable_then_collected_without_touching_original(tmp_path: Path) -> None:
    root, target = tmp_path / "dl", tmp_path / "biz"
    root.mkdir()
    (root / "a.zip").write_bytes(b"user-file")
    before = snapshot_dir(root)
    (root / "a(1).zip.baiduyun.p.downloading").write_bytes(b"pa")
    assert _collect(root, before, target, b"new-content").status == "in_progress"

    os.remove(root / "a(1).zip.baiduyun.p.downloading")
    (root / "a(1).zip").write_bytes(b"new-content")
    first = _collect(root, before, target, b"new-content")
    assert first.status == "unstable" and first.stable_token

    done = _collect(root, before, target, b"new-content", token=first.stable_token)
    assert done.status == "collected"
    assert Path(done.target_path).read_bytes() == b"new-content"
    assert not (root / "a(1).zip").exists()
    assert (root / "a.zip").read_bytes() == b"user-file"


def test_hash_mismatch_and_ambiguity_stop_for_review(tmp_path: Path) -> None:
    root, target = tmp_path / "dl", tmp_path / "biz"
    root.mkdir()
    before = snapshot_dir(root)
    (root / "a.zip").write_bytes(b"xxxx")
    token = _collect(root, before, target, b"yyyy").stable_token
    assert _collect(root, before, target, b"yyyy", token=token).status == "hash_mismatch"
    (root / "a(2).zip").write_bytes(b"zzzz")
    assert _collect(root, before, target, b"zzzz").status == "ambiguous"


def test_never_overwrites_business_target(tmp_path: Path) -> None:
    root, target = tmp_path / "dl", tmp_path / "biz"
    root.mkdir()
    target.mkdir()
    (target / "a.zip").write_bytes(b"keep")
    before = snapshot_dir(root)
    (root / "a.zip").write_bytes(b"newx")
    token = _collect(root, before, target, b"newx").stable_token
    result = _collect(root, before, target, b"newx", token=token)
    assert result.status == "target_exists"
    assert (target / "a.zip").read_bytes() == b"keep" and (root / "a.zip").exists()


def test_manifest_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "dl"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "x.txt").write_text("1")
    write_manifest(tmp_path / "m.json", root, snapshot_dir(root))
    loaded_root, files = load_manifest(tmp_path / "m.json")
    assert loaded_root == root.resolve() and set(files) == {"sub/x.txt"}


def test_remote_dir_validation_and_source_description(tmp_path: Path) -> None:
    assert validate_remote_dir("/CodeYun自动化测试//run-1/") == "/CodeYun自动化测试/run-1"
    for bad in ("relative/x", "/a/../b", "/a\\b", "/a:b"):
        with pytest.raises(ValueError):
            validate_remote_dir(bad)
    src = tmp_path / "probe.txt"
    src.write_bytes(b"hello")
    info = describe_upload_source(src, "/T/run")
    assert info["remote_path"] == "/T/run/probe.txt" and info["sha256"] == _sha(b"hello") and info["size"] == 5


def test_ledger_blocks_resubmission_until_failed(tmp_path: Path) -> None:
    ledger = TransferLedger(tmp_path / "ledger.json")
    inputs = {"sha256": "AB", "remote_dir": "/T/run"}
    record, created = ledger.begin("upload", **inputs)
    assert created
    ledger.update(record["key"], "submitted", via="gui")
    again, created_again = ledger.begin("upload", **inputs)
    assert not created_again and again["state"] == "submitted"
    ledger.update(record["key"], "failed", reason="client_error")
    _, retry_allowed = ledger.begin("upload", **inputs)
    assert retry_allowed
    other, created_other = ledger.begin("upload", sha256="AB", remote_dir="/T/other")
    assert created_other and other["key"] != record["key"]


def test_ledger_terminal_state_is_final(tmp_path: Path) -> None:
    ledger = TransferLedger(tmp_path / "ledger.json")
    record, _ = ledger.begin("download", name="a")
    ledger.update(record["key"], "verified")
    with pytest.raises(ValueError):
        ledger.update(record["key"], "submitted")


def test_bridge_body_parsing() -> None:
    assert parse_bridge_body('cb({"version":"8.1.5.103","errorno":0})')["version"] == "8.1.5.103"
    assert parse_bridge_body('{"info":"Frequency Invalid request!","error":31034}')["error"] == 31034


def test_already_collected_reports_duplicate_copy_without_deleting(tmp_path: Path) -> None:
    root, target = tmp_path / "dl", tmp_path / "biz"
    root.mkdir()
    target.mkdir()
    (target / "a.zip").write_bytes(b"same")
    before = snapshot_dir(root)
    (root / "a(1).zip").write_bytes(b"same")
    token = _collect(root, before, target, b"same").stable_token
    result = _collect(root, before, target, b"same", token=token)
    assert result.status == "already_collected"
    assert result.duplicate_source_path == os.fspath(root / "a(1).zip")
    assert result.sha256 == result.target_sha256 == _sha(b"same")
    assert (root / "a(1).zip").exists() and (target / "a.zip").read_bytes() == b"same"


def test_ledger_creates_missing_parent_and_rejects_op_override(tmp_path: Path) -> None:
    ledger = TransferLedger(tmp_path / "missing" / "deeper" / "ledger.json")
    record, created = ledger.begin("upload", sha256="AB")
    assert created and ledger.get(record["key"])["state"] == "planned"
    with pytest.raises(ValueError):
        ledger.begin("upload", op="download", sha256="AB")


@pytest.mark.parametrize("name", ["C:evil.txt", "a.txt:ads", "CON", "aux.log", "x.", " x", "a<b", "..", "a\\b", "a/b"])
def test_collect_rejects_names_escaping_target_dir(tmp_path: Path, name: str) -> None:
    root = tmp_path / "dl"
    root.mkdir()
    with pytest.raises(ValueError):
        collect_download(download_root=root, before={}, expected_name=name, expected_size=1, target_dir=tmp_path / "biz")
