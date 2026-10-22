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
