import hashlib
import io
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from filelock import FileLock
from PIL import Image

from backend.core.baidu_netdisk import assets
from backend.core.baidu_netdisk.assets import AssetError, AssetStore, SceneConflict


def _png(w=40, h=30):
    img = Image.new("RGB", (w, h), (255, 255, 255))
    img.paste((255, 0, 0), (10, 5, 20, 15))  # red block at x10..19, y5..14
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def store(tmp_path):
    return AssetStore(tmp_path / "store")


def test_capture_hash_size_and_immutable_metadata(store):
    data = _png()
    rec = store.capture(data, {"profile": "p1", "scale": 1.5, "window_id": 7})
    assert rec["sha256"] == hashlib.sha256(data).hexdigest()
    assert (rec["width"], rec["height"], rec["kind"]) == (40, 30, "originals")
    assert rec["source"]["window_id"] == 7 and rec["private"] is True
    assert store.read_image(rec["image_id"]) == data
    assert store.get_image(rec["image_id"]) == rec
    with pytest.raises(AssetError):
        store._create(store._path("originals", f"{rec['image_id']}.png"), b"x")
    with pytest.raises(AssetError):
        store.capture(b"not an image")


def test_derive_crop_scale_redact_and_transform(store):
    src = store.capture(_png())["image_id"]
    rec = store.derive(src, crop_xywh=(10, 5, 10, 10), redact_boxes=[(10, 5, 4, 4)], scale=2)
    assert (rec["width"], rec["height"]) == (20, 20)
    assert rec["transform"] == {"offset": [10, 5], "scale_xy": [2.0, 2.0]}
    assert rec["parent_id"] == rec["root_image_id"] == src
    data = store.read_image(rec["image_id"])
    assert rec["sha256"] == hashlib.sha256(data).hexdigest()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    assert img.getpixel((1, 1)) == (0, 0, 0)  # redacted
    assert img.getpixel((15, 15)) == (255, 0, 0)  # crop kept the red block
    child = store.derive(rec["image_id"], crop_xywh=(0, 0, 5, 5))
    assert child["root_image_id"] == src and child["redacted"] is False
    for bad in [(30, 20, 20, 20), (-1, 0, 5, 5), (0, 0, 0, 5)]:
        with pytest.raises(AssetError):
            store.derive(src, crop_xywh=bad)
    with pytest.raises(AssetError):
        store.derive(src, crop_xywh=(0, 0, 10, 10), redact_boxes=[(8, 8, 5, 5)])


def test_illegal_ids_and_symlinks(store, tmp_path):
    for bad in ["../x", "a" * 31, "A" * 32, "", None]:
        with pytest.raises(AssetError):
            store.get_image(bad)
    with pytest.raises(AssetError):
        store.get_scene("../etc")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, store.root / "derived", target_is_directory=True)
    except (OSError, NotImplementedError):
        _winapi = pytest.importorskip("_winapi")  # junctions need no privilege
        _winapi.CreateJunction(str(outside), str(store.root / "derived"))
    src = store.capture(_png())["image_id"]
    with pytest.raises(AssetError, match="symlink"):
        store.derive(src)
    assert not any(outside.iterdir())


def _scene(ref, **extra):
    shape = {"shape_id": "ok-btn", "role": "button", "bbox": [10, 5, 10, 10], "threshold": 0.9}
    return {"scene_id": "share-dialog", "title": "t", "profile": "p1",
            "reference_image_id": ref, "shapes": [shape], **extra}


def test_scene_revisions_and_conflict(store):
    ref = store.capture(_png())["image_id"]
    r1 = store.publish_scene(_scene(ref), 0)
    assert r1["revision"] == 1
    with pytest.raises(SceneConflict):
        store.publish_scene(_scene(ref, title="stale"), 0)
    r2 = store.publish_scene(_scene(ref, title="v2"), 1)
    assert store.get_scene("share-dialog")["scene"]["title"] == "v2" == r2["scene"]["title"]
    assert store.get_scene("share-dialog", 1)["scene"]["title"] == "t"
    bad = _scene(ref)
    bad["shapes"][0]["bbox"] = [35, 0, 10, 10]
    with pytest.raises(AssetError):
        store.publish_scene(bad, 2)


def test_export_requires_explicit_redaction_and_whitelists(store, tmp_path):
    src = store.capture(_png(), {"account": "secret-user"})["image_id"]
    plain = store.derive(src, crop_xywh=(0, 0, 20, 20))["image_id"]
    dest = tmp_path / "export"
    for image_id in (src, plain):
        with pytest.raises(AssetError):
            store.export_sample(image_id, dest)
    red = store.derive(src, redact_boxes=[(0, 0, 5, 5)])["image_id"]
    with pytest.raises(AssetError):
        store.export_sample(red, store.root / "leak")
    out = store.export_sample(red, dest)
    assert out["requires_review"] is True
    meta = (dest / f"{red}.json").read_text("utf-8")
    assert "secret-user" not in meta and "source" not in meta
    assert (dest / f"{red}.png").read_bytes() == store.read_image(red)
    with pytest.raises(AssetError):
        store.export_sample(red, dest)


_CHILD = """
import sys
from backend.core.baidu_netdisk.assets import AssetStore, SceneConflict
root, ref, title = sys.argv[1:]
scene = {"scene_id": "share-dialog", "title": title, "profile": "p1", "reference_image_id": ref,
         "shapes": [{"shape_id": "ok-btn", "role": "button", "bbox": [10, 5, 10, 10], "threshold": 0.9}]}
try:
    AssetStore(root).publish_scene(scene, 0)
    print("ok")
except SceneConflict:
    print("conflict")
"""


def test_cross_process_publish_cas_single_winner(store):
    ref = store.capture(_png())["image_id"]
    repo = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(repo)}
    lock = FileLock(str(store.root / ".scenes.lock"))
    with lock:  # both children queue on the same cross-process lock
        procs = [subprocess.Popen([sys.executable, "-c", _CHILD, str(store.root), ref, t],
                                  cwd=repo, env=env, stdout=subprocess.PIPE, text=True)
                 for t in ("a", "b")]
        time.sleep(2)
        assert [p.poll() for p in procs] == [None, None]  # blocked on the lock
        assert not (store.root / "scenes" / "share-dialog").exists()
    outs = sorted(p.communicate(timeout=60)[0].strip() for p in procs)
    assert outs == ["conflict", "ok"]
    assert sorted(f.name for f in (store.root / "scenes" / "share-dialog").glob("r*.json")) == ["r1.json"]
    assert AssetStore(store.root).get_scene("share-dialog")["revision"] == 1


def test_orphan_revision_recovery_and_conflict(store, monkeypatch):
    ref = store.capture(_png())["image_id"]
    store.publish_scene(_scene(ref), 0)

    def crash(path, data):
        raise OSError("simulated crash before pointer commit")

    monkeypatch.setattr(store, "_replace", crash)
    with pytest.raises(OSError):
        store.publish_scene(_scene(ref, title="v2"), 1)
    monkeypatch.undo()
    orphan = store.root / "scenes" / "share-dialog" / "r2.json"
    before = orphan.read_bytes()
    assert store.get_scene("share-dialog")["revision"] == 1

    other = AssetStore(store.root)
    with pytest.raises(SceneConflict, match="different content"):
        other.publish_scene(_scene(ref, title="other"), 1)
    assert orphan.read_bytes() == before and other.get_scene("share-dialog")["revision"] == 1

    rec = other.publish_scene(_scene(ref, title="v2"), 1)
    assert rec["revision"] == 2 and other.get_scene("share-dialog") == rec
    assert orphan.read_bytes() == before
    with pytest.raises(SceneConflict):
        other.publish_scene(_scene(ref, title="v2"), 1)


def test_explicit_revision_must_be_positive_int(store):
    ref = store.capture(_png())["image_id"]
    store.publish_scene(_scene(ref), 0)
    assert store.get_scene("share-dialog", 1)["revision"] == 1
    for bad in (0, -1, True, False, 1.0, "1"):
        with pytest.raises(AssetError):
            store.get_scene("share-dialog", bad)
    with pytest.raises(AssetError):
        store.get_scene("share-dialog", 2)


def test_pixel_budget_for_capture_and_derive(store, monkeypatch):
    src = store.capture(_png())["image_id"]
    monkeypatch.setattr(assets, "MAX_PIXELS", 40 * 30)
    store.derive(src)
    with pytest.raises(AssetError, match="exceeds"):
        store.derive(src, scale=2)
    with pytest.raises(AssetError, match="exceeds"):
        store.capture(_png(41, 30))
    assert len(list((store.root / "derived").glob("*.png"))) == 1


def _library(store):
    ref = store.capture(_png(), {"profile": "p1"})["image_id"]
    red = store.derive(ref, crop_xywh=(0, 0, 20, 20), redact_boxes=[(0, 0, 5, 5)])["image_id"]
    store.publish_scene(_scene(ref, positive_sample_ids=[red]), 0)
    store.publish_scene(_scene(ref, title="v2"), 1)
    return ref, red


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and p.name != ".scenes.lock"}


def _link_dir(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        _winapi = pytest.importorskip("_winapi")
        _winapi.CreateJunction(str(target), str(link))


def test_clone_copies_same_ids_and_is_idempotent(store, tmp_path):
    ref, red = _library(store)
    before = _snapshot(store.root)
    dest = tmp_path / "installed"
    out = store.clone_to(dest)
    assert (out["images"], out["scenes"], out["revisions"], out["files"]) == (2, 1, 2, 7)
    assert out["copied_files"] == 7 and out["copied_bytes"] == out["bytes"] > 0
    assert _snapshot(store.root) == before == _snapshot(dest)
    clone = AssetStore(dest)
    assert clone.get_image(red) == store.get_image(red)
    assert clone.get_image(red)["root_image_id"] == ref
    assert clone.read_image(ref) == store.read_image(ref)
    assert clone.get_scene("share-dialog") == store.get_scene("share-dialog")
    assert clone.get_scene("share-dialog", 1)["scene"]["positive_sample_ids"] == [red]
    again = store.clone_to(dest)
    assert again["copied_files"] == 0 and again["skipped_files"] == 7
    assert again["manifest_sha256"] == out["manifest_sha256"]
    assert clone.publish_scene(_scene(ref, title="v3"), 2)["revision"] == 3
    assert store.get_scene("share-dialog")["revision"] == 2


def test_clone_resumes_partial_copy(store, tmp_path, monkeypatch):
    _library(store)
    dest = tmp_path / "installed"
    calls = []
    real = AssetStore._create

    def flaky(self, path, data):
        if len(calls) == 3:
            raise OSError("simulated crash")
        calls.append(path)
        real(self, path, data)

    monkeypatch.setattr(AssetStore, "_create", flaky)
    with pytest.raises(OSError):
        store.clone_to(dest)
    monkeypatch.undo()
    assert not (dest / "scenes" / "share-dialog" / "current.json").exists()  # pointers last
    out = store.clone_to(dest)
    assert out["skipped_files"] == 3 and out["copied_files"] == 4
    assert _snapshot(dest) == _snapshot(store.root)


def test_clone_refuses_corrupt_source(store, tmp_path):
    ref, red = _library(store)
    png = store.root / "derived" / f"{red}.png"
    png.write_bytes(png.read_bytes() + b"x")
    with pytest.raises(AssetError, match="does not match"):
        store.clone_to(tmp_path / "a")
    assert not (tmp_path / "a" / "derived").exists()
    png.write_bytes(png.read_bytes()[:-1])
    (store.root / "scenes" / "share-dialog" / "r1.json").unlink()
    with pytest.raises(AssetError, match="contiguous"):
        store.clone_to(tmp_path / "b")
    assert not (tmp_path / "b" / "originals").exists()


def test_clone_refuses_missing_lineage(store, tmp_path):
    ref, red = _library(store)
    rec = store.root / "derived" / f"{red}.json"
    rec.write_bytes(rec.read_bytes().replace(ref.encode(), b"0" * 32))
    with pytest.raises(AssetError, match="lineage"):
        store.clone_to(tmp_path / "a")


def test_clone_refuses_conflicts_without_overwriting(store, tmp_path):
    ref, _ = _library(store)
    dest = tmp_path / "installed"
    (dest / "originals").mkdir(parents=True)
    bad = dest / "originals" / f"{ref}.png"
    bad.write_bytes(b"different")
    with pytest.raises(AssetError, match="conflict"):
        store.clone_to(dest)
    assert bad.read_bytes() == b"different"
    assert list(_snapshot(dest)) == [f"originals/{ref}.png"]
    bad.unlink()
    store.clone_to(dest)
    extra = dest / "scenes" / "share-dialog" / "r3.json"
    extra.write_bytes(b"{}")
    with pytest.raises(AssetError, match="extra"):
        store.clone_to(dest)
    assert extra.read_bytes() == b"{}"


def test_clone_refuses_same_nested_and_linked_roots(store, tmp_path):
    _library(store)
    for bad in (store.root, store.root / "sub", tmp_path):
        with pytest.raises(AssetError, match="source"):
            store.clone_to(bad)
    assert not (store.root / "sub").exists()
    real = tmp_path / "real"
    real.mkdir()
    _link_dir(tmp_path / "link", real)
    with pytest.raises(AssetError):
        store.clone_to(tmp_path / "link")
    assert not any(real.iterdir())
    dest = tmp_path / "installed"
    dest.mkdir()
    _link_dir(dest / "derived", real)
    with pytest.raises(AssetError, match="symlink"):
        store.clone_to(dest)
    assert not any(real.iterdir())
    _link_dir(store.root / "scenes" / "evil", real)
    with pytest.raises(AssetError):
        store.clone_to(tmp_path / "other")


def test_clone_refuses_linked_parent_of_source_or_destination(store, tmp_path, monkeypatch):
    _library(store)
    real = tmp_path / "real"
    real.mkdir()
    _link_dir(tmp_path / "parent", real)
    copies = []
    monkeypatch.setattr(AssetStore, "_create", lambda self, path, data: copies.append(path))
    for dest in (tmp_path / "parent" / "installed", tmp_path / "parent" / "new" / "installed"):
        with pytest.raises(AssetError, match="destination path must not traverse"):
            store.clone_to(dest)
    assert not any(real.iterdir()) and copies == []
    monkeypatch.undo()
    linked_source = AssetStore(tmp_path / "parent" / "src")
    _library(linked_source)
    monkeypatch.setattr(AssetStore, "_create", lambda self, path, data: copies.append(path))
    with pytest.raises(AssetError, match="source path must not traverse"):
        linked_source.clone_to(tmp_path / "other")
    assert not (tmp_path / "other").exists() and copies == []


def test_clone_refuses_unrelated_destination_files_but_allows_subset(store, tmp_path, monkeypatch):
    ref, red = _library(store)
    dest = tmp_path / "installed"
    (dest / "originals").mkdir(parents=True)
    (dest / "originals" / f"{ref}.png").write_bytes(store.read_image(ref))  # identical subset
    (dest / "originals" / f".{ref}.png.abc.tmp").write_bytes(b"temp")
    (dest / ".scenes.lock").write_bytes(b"")
    stray = [dest / "derived" / f"{'f' * 32}.png", dest / "scenes" / "unrelated" / "r1.json"]
    copies = []
    monkeypatch.setattr(AssetStore, "_create", lambda self, path, data: copies.append(path))
    for path in stray:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{}")
        with pytest.raises(AssetError, match="extra files"):
            store.clone_to(dest)
        assert copies == []
        path.unlink()
    monkeypatch.undo()
    (dest / "scenes" / "unrelated").rmdir()
    out = store.clone_to(dest)
    assert out["skipped_files"] == 1 and out["copied_files"] == 6
    assert store.clone_to(dest)["copied_files"] == 0


def test_clone_checks_free_disk_before_writing(store, tmp_path, monkeypatch):
    _library(store)
    monkeypatch.setattr(assets.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    with pytest.raises(AssetError, match="free disk"):
        store.clone_to(tmp_path / "installed")
    assert not (tmp_path / "installed" / "originals").exists()
