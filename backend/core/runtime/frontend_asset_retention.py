"""Keep immutable frontend assets reachable by tabs opened before deployment."""
from __future__ import annotations


def retain_frontend_assets(root_path: str, release_path: str) -> dict:
    """Recover assets from all retained releases without replacing current files.

    Hard links avoid duplicating large viewer bundles. Index pages and manifests
    belong to the current release only. This self-contained function also runs
    on the static host before its atomic document-root switch.
    """
    import os
    from pathlib import Path
    import shutil
    root = Path(root_path).resolve(strict=True)
    releases = root / 'releases'
    target = Path(release_path).resolve(strict=True)
    if target.parent != releases or not target.is_dir():
        raise ValueError('Asset target must be a release inside the deployment root')
    count, size = 0, 0
    for previous in sorted(releases.iterdir()):
        if previous == target or not previous.is_dir() or previous.is_symlink():
            continue
        for directory in ('assets', 'plugins/project-graph/assets', 'offline-library/assets'):
            old = previous / directory
            if not old.is_dir() or old.is_symlink():
                continue
            for source in old.rglob('*'):
                if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(previous):
                    continue
                destination = target / directory / source.relative_to(old)
                if not destination.resolve().is_relative_to(target):
                    raise ValueError('Asset destination escapes the release')
                if destination.exists():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(source, destination)
                except FileExistsError:
                    continue
                except OSError:
                    shutil.copy2(source, destination)
                count += 1
                size += source.stat().st_size
    return {'release': str(target), 'retained_assets': count, 'retained_bytes': size}


def asset_retention_program(root: str, target: str) -> str:
    """Generate the identical tested implementation for the remote static host."""
    import inspect
    return inspect.getsource(retain_frontend_assets) + f'\nimport json\nprint(json.dumps(retain_frontend_assets({root!r}, {target!r})))\n'
