from __future__ import annotations

from pathlib import Path

import scripts.verify_fanxiu_reverse_manifest as manifest


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _minimal_manifest_roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    reverse_root = tmp_path / "m2606凡修逆向"
    resource_root = reverse_root / "frxx_game_files"
    export_root = reverse_root / "frxx_analysis_exports"

    _write(reverse_root / "raw_inputs" / "1023295.apk", "apk")
    for relative in manifest.RESOURCE_KEY_FILES:
        _write(resource_root / relative)
    for relative in manifest.RESOURCE_KEY_DIRS:
        _write(resource_root / relative / "sample.bytes")
    for relative in manifest.EXPORT_KEY_FILES:
        _write(export_root / relative)
    for relative in manifest.EXPORT_KEY_DIRS:
        _write(export_root / relative / "sample.out")
    return reverse_root, resource_root, export_root


def test_reverse_manifest_writes_traceable_outputs(tmp_path):
    reverse_root, resource_root, export_root = _minimal_manifest_roots(tmp_path)

    summary = manifest.build_fanxiu_reverse_manifest(
        reverse_root=reverse_root,
        resource_root=resource_root,
        export_root=export_root,
    )

    output_dir = export_root / "parsed_configs" / "reverse_manifest_audit"
    assert summary["ok"] is True
    assert summary["missing_count"] == 0
    assert summary["raw_input_count"] == 1
    assert summary["hashed_file_count"] >= len(manifest.RESOURCE_KEY_FILES) + len(manifest.EXPORT_KEY_FILES) + 1
    assert len(summary["manifest_digest"]) == 64
    assert (output_dir / "reverse_manifest_latest.json").is_file()
    assert (output_dir / "reverse_manifest_latest.tsv").is_file()


def test_reverse_manifest_fails_when_required_export_is_missing(tmp_path):
    reverse_root, resource_root, export_root = _minimal_manifest_roots(tmp_path)
    (export_root / manifest.EXPORT_KEY_FILES[0]).unlink()

    summary = manifest.build_fanxiu_reverse_manifest(
        reverse_root=reverse_root,
        resource_root=resource_root,
        export_root=export_root,
        write_outputs=False,
    )

    assert summary["ok"] is False
    assert summary["missing_count"] == 1
    assert summary["missing_entries"][0]["relative_path"].endswith(manifest.EXPORT_KEY_FILES[0])


def test_reverse_manifest_fails_without_raw_inputs(tmp_path):
    reverse_root, resource_root, export_root = _minimal_manifest_roots(tmp_path)
    (reverse_root / "raw_inputs" / "1023295.apk").unlink()

    summary = manifest.build_fanxiu_reverse_manifest(
        reverse_root=reverse_root,
        resource_root=resource_root,
        export_root=export_root,
        write_outputs=False,
    )

    assert summary["ok"] is False
    assert summary["raw_input_count"] == 0
