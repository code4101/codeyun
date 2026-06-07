from __future__ import annotations

from pathlib import Path

import scripts.verify_fanxiu_reverse_boundary as boundary


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _minimal_valid_roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    reverse_root = tmp_path / "m2606凡修逆向"
    resource_root = reverse_root / "frxx_game_files"
    export_root = reverse_root / "frxx_analysis_exports"

    for relative in boundary.EXPECTED_RESOURCE_PATHS:
        target = resource_root / relative
        if "." in Path(relative).name:
            _write(target)
        else:
            target.mkdir(parents=True, exist_ok=True)

    for relative in boundary.EXPECTED_EXPORT_PATHS:
        target = export_root / relative
        if "." in Path(relative).name:
            _write(target)
        else:
            target.mkdir(parents=True, exist_ok=True)

    return reverse_root, resource_root, export_root


def test_reverse_boundary_accepts_stable_external_roots(tmp_path):
    reverse_root, resource_root, export_root = _minimal_valid_roots(tmp_path)

    summary = boundary.audit_fanxiu_reverse_boundary(
        reverse_root=reverse_root,
        resource_root=resource_root,
        export_root=export_root,
        min_resource_files=0,
        min_export_files=0,
    )

    assert summary["ok"] is True
    assert summary["failures"] == []
    assert summary["missing_resource_paths"] == []
    assert summary["missing_export_paths"] == []


def test_reverse_boundary_warns_for_empty_ambiguous_sibling(tmp_path):
    reverse_root, resource_root, export_root = _minimal_valid_roots(tmp_path)
    (reverse_root / "frxx_game_f").mkdir()

    summary = boundary.audit_fanxiu_reverse_boundary(
        reverse_root=reverse_root,
        resource_root=resource_root,
        export_root=export_root,
        min_resource_files=0,
        min_export_files=0,
    )

    assert summary["ok"] is True
    assert summary["failures"] == []
    assert summary["warnings"][0]["kind"] == "empty_ambiguous_reverse_siblings"


def test_reverse_boundary_fails_for_nonempty_ambiguous_sibling(tmp_path):
    reverse_root, resource_root, export_root = _minimal_valid_roots(tmp_path)
    _write(reverse_root / "frxx_game_f" / "filelist.csv")

    summary = boundary.audit_fanxiu_reverse_boundary(
        reverse_root=reverse_root,
        resource_root=resource_root,
        export_root=export_root,
        min_resource_files=0,
        min_export_files=0,
    )

    assert summary["ok"] is False
    assert {failure["kind"] for failure in summary["failures"]} == {"ambiguous_reverse_siblings"}


def test_reverse_boundary_fails_for_vm_resource_path(tmp_path):
    reverse_root, _resource_root, export_root = _minimal_valid_roots(tmp_path)
    vm_root = Path(r"D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_game_files")

    summary = boundary.audit_fanxiu_reverse_boundary(
        reverse_root=reverse_root,
        resource_root=vm_root,
        export_root=export_root,
        min_resource_files=0,
        min_export_files=0,
    )

    kinds = {failure["kind"] for failure in summary["failures"]}
    assert "vm_path_selected" in kinds
    assert "root_outside_stable_boundary" in kinds
