import hashlib
import json

import pytest

from backend.core.skilldep_materials.catalog import evidence_problem
from backend.core.skilldep_materials.payload_evidence import SCHEMA


def setup_evidence(tmp_path):
    root = tmp_path / "pure-package"
    root.mkdir()
    (root / "a.bin").write_bytes(b"abc")
    content = {"schema": SCHEMA, "material_id": "sd-one", "root": str(root.resolve()), "complete": True,
               "files": [{"rel": "a.bin", "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}]}
    return root, content


def record(tmp_path, root, content, **overrides):
    manifest = tmp_path / "immutable-evidence.json"
    manifest.write_text(json.dumps(content), encoding="utf-8")
    return {"status": "downloaded", "path": str(root), "size_bytes": 3,
            "manifest_path": str(manifest), "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            **overrides}


def test_external_evidence_preserves_pure_payload_and_checks_actual_bytes(tmp_path):
    root, content = setup_evidence(tmp_path)
    rec = record(tmp_path, root, content)
    assert evidence_problem(rec, "sd-one") is None
    assert [p.name for p in root.iterdir()] == ["a.bin"]
    (root / "a.bin").write_bytes(b"bad")
    assert evidence_problem(rec, "sd-one") is not None


@pytest.mark.parametrize("change", [
    lambda c: c.update(material_id="sd-other"),
    lambda c: c.update(root="relative-root"),
    lambda c: c.update(complete=False),
    lambda c: c.update(schema="untyped"),
    lambda c: c["files"][0].update(rel="../a.bin"),
    lambda c: c["files"][0].update(sha256=None),
    lambda c: c["files"][0].update(size=4),
])
def test_unbound_or_invalid_manifest_is_refused(tmp_path, change):
    root, content = setup_evidence(tmp_path)
    change(content)
    assert evidence_problem(record(tmp_path, root, content), "sd-one") is not None


def test_manifest_cannot_hide_extra_payload_or_misstate_total(tmp_path):
    root, content = setup_evidence(tmp_path)
    assert evidence_problem(record(tmp_path, root, content, size_bytes=4), "sd-one") is not None
    (root / "unlisted.bin").write_bytes(b"extra")
    assert evidence_problem(record(tmp_path, root, content), "sd-one") is not None
