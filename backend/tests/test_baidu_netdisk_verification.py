import hashlib
import os
import sys

import pytest

from backend.core.baidu_netdisk import verification
from backend.core.baidu_netdisk.verification import verify_local


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "dl"
    (root / "sub").mkdir(parents=True)
    (root / "a.bin").write_bytes(b"alpha")
    (root / "sub" / "b.txt").write_bytes(b"beta!")
    return root


def _expected(with_sha=True, **identity):
    files = [{"rel": "a.bin", "size": 5}, {"rel": "sub/b.txt", "size": 5}]
    if with_sha:
        files[0]["sha256"], files[1]["sha256"] = _sha(b"alpha"), _sha(b"beta!").upper()
    return {"files": files, **identity}


def _snapshot(root):
    return sorted((p.relative_to(root).as_posix(), p.read_bytes()) for p in root.rglob("*") if p.is_file())


def test_trusted_hash_match_is_content_verified_and_read_only(tree):
    before = _snapshot(tree)
    result = verify_local(tree, _expected(resource_id="r1", resource_version=3))
    assert result["status"] == "content_verified"
    assert result["verification_level"] == "content_verified"
    assert result["content_verified"] and result["structure_verified"] and result["local_hash_recorded"]
    assert result["errors"] == [] and result["counts"]["content_matched"] == 2
    manifest = result["manifest"]
    assert manifest["resource_id"] == "r1" and manifest["resource_version"] == 3
    assert manifest["remote_verified"] is False and manifest["hash_source"] == "local_read"
    assert manifest["files"][0] == {"rel": "a.bin", "size": 5, "sha256": _sha(b"alpha").upper()}
    assert len(result["manifest_sha256"]) == 64
    assert _snapshot(tree) == before


def test_without_expected_only_records_local_fingerprint(tree):
    result = verify_local(tree)
    assert result["status"] == "local_hash_recorded" and result["verification_level"] == "local_hash_recorded"
    assert not result["structure_verified"] and not result["content_verified"]
    assert result["manifest"]["expected_provided"] is False
    assert verify_local(tree)["manifest_sha256"] == result["manifest_sha256"]  # deterministic


def test_size_only_expected_is_structure_not_content(tree):
    result = verify_local(tree, _expected(with_sha=False))
    assert result["status"] == "structure_verified" and result["structure_verified"]
    assert not result["content_verified"] and result["counts"]["without_trusted_sha256"] == 2
    assert result["verification_level"] == "structure_verified" and result["counts"]["content_matched"] == 0


def test_empty_expected_matches_empty_dir_as_structure_only(tmp_path, tree):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = verify_local(empty, {"files": []})
    assert result["status"] == "structure_verified" and result["structure_verified"]
    assert not result["content_verified"] and result["counts"]["content_matched"] == 0
    extra = verify_local(tree, {"files": []})
    assert extra["status"] == "mismatch" and not extra["structure_verified"]
    assert extra["counts"]["extra"] == 2


def test_content_matched_counts_never_negative(tree):
    expected = _expected()
    expected["files"].append({"rel": "gone.bin", "size": 1})  # missing and without sha
    expected["files"][1]["size"] = 6  # sha correct but size wrong: not a content match
    result = verify_local(tree, expected)
    assert result["status"] == "mismatch"
    assert result["counts"]["content_matched"] == 1
    assert all(value >= 0 for value in result["counts"].values())
    only_missing = verify_local(tree, {"files": [{"rel": "gone.bin", "size": 1}]})
    assert only_missing["counts"]["content_matched"] == 0  # missing+no-sha must not double-subtract


def test_wrong_hash_fails(tree):
    expected = _expected()
    expected["files"][0]["sha256"] = _sha(b"other")
    result = verify_local(tree, expected)
    assert result["status"] == "hash_mismatch" and not result["structure_verified"]
    assert result["counts"]["hash_mismatch"] == 1


@pytest.mark.parametrize("change, error", [
    (lambda root: (root / "extra.txt").write_bytes(b"x"), "多余：extra.txt"),
    (lambda root: (root / "a.bin").unlink(), "缺失：a.bin"),
    (lambda root: (root / "a.bin").write_bytes(b"alphaX"), "大小不符：a.bin"),
    (lambda root: (root / "c.zip.baiduyun.p.downloading").write_bytes(b"p"), "未完成下载文件"),
])
def test_missing_extra_size_partial_fail(tree, change, error):
    change(tree)
    result = verify_local(tree, _expected())
    assert result["status"] == "mismatch" and not result["content_verified"] and not result["structure_verified"]
    assert any(error in item for item in result["errors"])


def test_partial_without_expected_is_not_recorded(tree):
    (tree / "a.zip.downloading").write_bytes(b"p")
    result = verify_local(tree)
    assert result["status"] == "incomplete" and not result["local_hash_recorded"]


@pytest.mark.parametrize("rel", [
    "../x", "/abs", "C:/x", "sub\\b.txt", "a/./b", "a//b", "con.txt", "a?b", "a:stream", "dot.",
])
def test_unsafe_expected_rel_rejected(tree, rel):
    result = verify_local(tree, {"files": [{"rel": rel, "size": 1}]})
    assert result["status"] == "invalid_input" and result["manifest"] is None


def test_case_conflict_and_md5_rejected(tree):
    conflict = {"files": [{"rel": "a.bin", "size": 5}, {"rel": "A.BIN", "size": 5}]}
    assert verify_local(tree, conflict)["status"] == "invalid_input"
    md5 = {"files": [{"rel": "a.bin", "size": 5, "sha256": hashlib.md5(b"alpha").hexdigest()}]}
    result = verify_local(tree, md5)
    assert result["status"] == "invalid_input" and "MD5" in result["errors"][0]
    assert verify_local(tree, {"files": [{"rel": "a.bin", "size": True}]})["status"] == "invalid_input"


def test_invalid_root_and_not_found_do_not_create(tmp_path):
    assert verify_local("")["status"] == "invalid_input"
    assert verify_local(None)["status"] == "invalid_input"  # type: ignore[arg-type]
    missing = tmp_path / "nope"
    assert verify_local(missing)["status"] == "not_found"
    assert not missing.exists()
    (tmp_path / "f").write_bytes(b"x")
    assert verify_local(tmp_path / "f")["status"] == "not_found"


def _make_dir_link(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))  # no admin needed
        return
    pytest.skip("cannot create directory link")


def test_link_or_junction_inside_root_rejected(tree, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"s")
    _make_dir_link(tree / "link", outside)
    result = verify_local(tree)
    assert result["status"] == "unsafe" and not result["local_hash_recorded"]
    assert all("secret" not in item for item in result["errors"])  # not followed


def test_root_link_rejected(tree, tmp_path):
    link = tmp_path / "rootlink"
    _make_dir_link(link, tree)
    assert verify_local(link)["status"] == "unsafe"


def test_file_changed_during_hash_is_unstable(tree, monkeypatch):
    real = verification.sha256_file

    def mutating(path):
        digest = real(path)
        if path.name == "a.bin":
            path.write_bytes(b"ALPHA-changed")
        return digest

    monkeypatch.setattr(verification, "sha256_file", mutating)
    result = verify_local(tree, _expected())
    assert result["status"] == "unstable" and not result["content_verified"]
    assert any("a.bin" in item for item in result["errors"])


def test_file_added_during_hash_is_unstable(tree, monkeypatch):
    real = verification.sha256_file

    def adding(path):
        (tree / "late.txt").write_bytes(b"late")
        return real(path)

    monkeypatch.setattr(verification, "sha256_file", adding)
    result = verify_local(tree)
    assert result["status"] == "unstable" and not result["local_hash_recorded"]


def test_link_swapped_in_before_read_is_rejected_unread(tree, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    real_enumerate, real_sha = verification._enumerate, verification.sha256_file
    read: list[str] = []
    calls = []

    def swapping(root):
        result = real_enumerate(root)
        if not calls:
            (tree / "a.bin").unlink()
            _make_dir_link(tree / "a.bin", outside)
        calls.append(root)
        return result

    def spy(path):
        read.append(path.name)
        return real_sha(path)

    monkeypatch.setattr(verification, "_enumerate", swapping)
    monkeypatch.setattr(verification, "sha256_file", spy)
    result = verify_local(tree, _expected())
    assert result["status"] == "unstable" and not result["local_hash_recorded"]
    assert "a.bin" not in read and any("a.bin" in item for item in result["errors"])
