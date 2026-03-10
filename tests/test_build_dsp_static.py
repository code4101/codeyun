from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_dsp_static.py"
SPEC = importlib.util.spec_from_file_location("build_dsp_static", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_collect_source_snapshot_ignores_readme_changes(tmp_path: Path) -> None:
    source_dir = tmp_path / "dsp-calc"
    write_file(source_dir / "package.json", '{"name":"dsp-calc"}')
    write_file(source_dir / "src" / "App.jsx", "export const app = 1;\n")
    write_file(source_dir / "README.md", "doc-v1\n")

    snapshot1 = MODULE.collect_source_snapshot(source_dir)

    write_file(source_dir / "README.md", "doc-v2\n")
    snapshot2 = MODULE.collect_source_snapshot(source_dir)
    assert snapshot2.source_hash == snapshot1.source_hash

    write_file(source_dir / "src" / "App.jsx", "export const app = 2;\n")
    snapshot3 = MODULE.collect_source_snapshot(source_dir)
    assert snapshot3.source_hash != snapshot1.source_hash


def test_get_sync_reason_reports_up_to_date(tmp_path: Path) -> None:
    source_dir = tmp_path / "dsp-calc"
    target_dir = tmp_path / "public" / "dsp-calc"
    metadata_path = tmp_path / "state" / "dsp-calc.json"
    write_file(source_dir / "package.json", '{"name":"dsp-calc"}')
    write_file(source_dir / "src" / "App.jsx", "export const app = 1;\n")

    snapshot = MODULE.collect_source_snapshot(source_dir)
    metadata = MODULE.build_sync_metadata(snapshot)
    target_dir.mkdir(parents=True)
    MODULE.write_sync_metadata(metadata_path, metadata)

    should_sync, reason = MODULE.get_sync_reason(target_dir, snapshot, metadata, force=False)
    assert not should_sync
    assert "最新" in reason


def test_deploy_dist_dir_replaces_target(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    target_dir = tmp_path / "public" / "dsp-calc"
    write_file(dist_dir / "index.html", "<html>new</html>\n")
    write_file(target_dir / "index.html", "<html>old</html>\n")

    MODULE.deploy_dist_dir(dist_dir, target_dir)

    assert (target_dir / "index.html").read_text(encoding="utf-8") == "<html>new</html>\n"


def test_write_sync_metadata_uses_state_path(tmp_path: Path) -> None:
    metadata_path = tmp_path / "frontend" / ".codeyun-state" / "dsp-calc.json"
    metadata = {"source_hash": "abc123"}

    MODULE.write_sync_metadata(metadata_path, metadata)

    saved_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert saved_metadata["source_hash"] == "abc123"
