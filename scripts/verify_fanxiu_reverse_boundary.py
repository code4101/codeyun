from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu.catalog.resources import (  # noqa: E402
    DEFAULT_FANXIU_RESOURCE_EXPORT_ROOT,
    DEFAULT_FANXIU_RESOURCE_ROOT,
    DEFAULT_FANXIU_REVERSE_ROOT,
    FANXIU_RESOURCE_EXPORT_ROOT_ENV,
    FANXIU_RESOURCE_ROOT_ENV,
    resolve_fanxiu_export_root,
    resolve_fanxiu_resource_root,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


VM_PATH_MARKERS = (
    r"\TapTap\Support\android_emulator\engine\vms\\",
    r"/TapTap/Support/android_emulator/engine/vms/",
)

EXPECTED_RESOURCE_PATHS = (
    "filelist.csv",
    "filelistVersion",
    "lscripts",
    "atlasnew",
    "ui",
    "uieffect",
)

EXPECTED_EXPORT_PATHS = (
    "parsed_configs/Item/rows.json",
    "parsed_configs/Envelope/rows.json",
    "parsed_configs/item_catalog/item_catalog.json",
    "parsed_configs/activity_catalog/activity_catalog.json",
    "parsed_configs/gongfa_catalog/gongfa_catalog.json",
    "parsed_configs/visual_catalog/static_visual_catalog.json",
    "icons",
)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _count_files(path: Path, *, limit: int | None = None) -> tuple[int, int]:
    count = 0
    total_bytes = 0
    if not path.exists():
        return count, total_bytes
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        count += 1
        try:
            total_bytes += item.stat().st_size
        except OSError:
            pass
        if limit is not None and count >= limit:
            break
    return count, total_bytes


def _path_contains_vm_marker(path: Path) -> bool:
    text = str(path)
    normalized = text.replace("/", "\\")
    return any(marker.replace("/", "\\") in normalized for marker in VM_PATH_MARKERS)


def _path_summary(path: Path) -> dict[str, Any]:
    file_count, total_bytes = _count_files(path)
    return {
        "path": str(path),
        "exists": path.exists(),
        "is_dir": path.is_dir(),
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def _append_failure(failures: list[dict[str, Any]], kind: str, detail: str, **extra: Any) -> None:
    failures.append({"kind": kind, "detail": detail, **extra})


def _append_warning(warnings: list[dict[str, Any]], kind: str, detail: str, **extra: Any) -> None:
    warnings.append({"kind": kind, "detail": detail, **extra})


def audit_fanxiu_reverse_boundary(
    *,
    reverse_root: str | os.PathLike[str] | None = None,
    resource_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
    min_resource_files: int = 1000,
    min_export_files: int = 1000,
    strict_ambiguous_siblings: bool = False,
) -> dict[str, Any]:
    stable_root = Path(reverse_root).expanduser().resolve() if reverse_root else DEFAULT_FANXIU_REVERSE_ROOT.resolve()
    resolved_resource_root = resolve_fanxiu_resource_root(resource_root)
    resolved_export_root = resolve_fanxiu_export_root(export_root)
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    env_overrides = {
        FANXIU_RESOURCE_ROOT_ENV: os.environ.get(FANXIU_RESOURCE_ROOT_ENV),
        FANXIU_RESOURCE_EXPORT_ROOT_ENV: os.environ.get(FANXIU_RESOURCE_EXPORT_ROOT_ENV),
    }

    if DEFAULT_FANXIU_REVERSE_ROOT.resolve() != stable_root and not reverse_root:
        _append_failure(
            failures,
            "default_reverse_root_drifted",
            "backend default reverse root must remain the documented stable root",
            expected=str(stable_root),
            observed=str(DEFAULT_FANXIU_REVERSE_ROOT.resolve()),
        )

    if DEFAULT_FANXIU_RESOURCE_ROOT.resolve() != (DEFAULT_FANXIU_REVERSE_ROOT / "frxx_game_files").resolve():
        _append_failure(failures, "default_resource_root_drifted", "backend default resource root no longer points to frxx_game_files")
    if DEFAULT_FANXIU_RESOURCE_EXPORT_ROOT.resolve() != (DEFAULT_FANXIU_REVERSE_ROOT / "frxx_analysis_exports").resolve():
        _append_failure(failures, "default_export_root_drifted", "backend default export root no longer points to frxx_analysis_exports")

    for label, path in [("resource_root", resolved_resource_root), ("export_root", resolved_export_root)]:
        if _path_contains_vm_marker(path):
            _append_failure(
                failures,
                "vm_path_selected",
                "Fanxiu reverse roots must not point at the emulator VM directory",
                root=label,
                path=str(path),
            )
        if not _is_relative_to(path, stable_root):
            _append_failure(
                failures,
                "root_outside_stable_boundary",
                "Fanxiu reverse roots must stay under the stable external reverse root",
                root=label,
                stable_root=str(stable_root),
                path=str(path),
            )
        if not path.exists() or not path.is_dir():
            _append_failure(failures, "root_missing", "Fanxiu reverse root directory is missing", root=label, path=str(path))

    resource_file_count, resource_bytes = _count_files(resolved_resource_root)
    export_file_count, export_bytes = _count_files(resolved_export_root)
    if resource_file_count < min_resource_files:
        _append_failure(
            failures,
            "resource_root_too_small",
            "resource root has too few files to be a restored Fanxiu resource root",
            expected_at_least=min_resource_files,
            observed=resource_file_count,
        )
    if export_file_count < min_export_files:
        _append_failure(
            failures,
            "export_root_too_small",
            "export root has too few files to be a restored Fanxiu analysis export root",
            expected_at_least=min_export_files,
            observed=export_file_count,
        )

    missing_resource_paths = [rel for rel in EXPECTED_RESOURCE_PATHS if not (resolved_resource_root / rel).exists()]
    if missing_resource_paths:
        _append_failure(
            failures,
            "resource_artifacts_missing",
            "resource root is missing expected downloaded game resource artifacts",
            missing=missing_resource_paths,
        )

    missing_export_paths = [rel for rel in EXPECTED_EXPORT_PATHS if not (resolved_export_root / rel).exists()]
    if missing_export_paths:
        _append_failure(
            failures,
            "export_artifacts_missing",
            "export root is missing expected parsed/catalog/icon artifacts",
            missing=missing_export_paths,
        )

    ambiguous_siblings: list[dict[str, Any]] = []
    if stable_root.exists():
        expected_names = {"frxx_game_files", "frxx_analysis_exports"}
        for sibling in stable_root.iterdir():
            if not sibling.is_dir() or sibling.name in expected_names:
                continue
            if sibling.name.startswith("frxx_game") or sibling.name.startswith("frxx_analysis"):
                sibling_files, sibling_bytes = _count_files(sibling)
                ambiguous_siblings.append(
                    {
                        "name": sibling.name,
                        "path": str(sibling),
                        "file_count": sibling_files,
                        "total_bytes": sibling_bytes,
                    }
                )
    nonempty_ambiguous_siblings = [row for row in ambiguous_siblings if row["file_count"] > 0]
    if nonempty_ambiguous_siblings or (strict_ambiguous_siblings and ambiguous_siblings):
        _append_failure(
            failures,
            "ambiguous_reverse_siblings",
            "ambiguous frxx_* sibling directories can make scripts read the wrong root",
            siblings=ambiguous_siblings,
        )
    elif ambiguous_siblings:
        _append_warning(
            warnings,
            "empty_ambiguous_reverse_siblings",
            "empty ambiguous frxx_* sibling directories exist; remove them when convenient to avoid confusion",
            siblings=ambiguous_siblings,
        )

    return {
        "ok": not failures,
        "reverse_root": str(stable_root),
        "resource_root": str(resolved_resource_root),
        "export_root": str(resolved_export_root),
        "env_overrides": {key: value for key, value in env_overrides.items() if value},
        "resource_file_count": resource_file_count,
        "resource_total_bytes": resource_bytes,
        "export_file_count": export_file_count,
        "export_total_bytes": export_bytes,
        "missing_resource_paths": missing_resource_paths,
        "missing_export_paths": missing_export_paths,
        "ambiguous_siblings": ambiguous_siblings,
        "warnings": warnings,
        "failures": failures,
        "roots": {
            "stable": _path_summary(stable_root),
            "resource": _path_summary(resolved_resource_root),
            "export": _path_summary(resolved_export_root),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Fanxiu reverse resources stay inside the stable external boundary.")
    parser.add_argument("--reverse-root", default=None)
    parser.add_argument("--resource-root", default=None)
    parser.add_argument("--export-root", default=None)
    parser.add_argument("--min-resource-files", type=int, default=1000)
    parser.add_argument("--min-export-files", type=int, default=1000)
    parser.add_argument("--strict-ambiguous-siblings", action="store_true")
    args = parser.parse_args()

    summary = audit_fanxiu_reverse_boundary(
        reverse_root=args.reverse_root,
        resource_root=args.resource_root,
        export_root=args.export_root,
        min_resource_files=args.min_resource_files,
        min_export_files=args.min_export_files,
        strict_ambiguous_siblings=args.strict_ambiguous_siblings,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
