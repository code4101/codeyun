"""Publish a verified frontend build to yun with an atomic, reversible switch.

Environment/SSH credentials come from xlproject.loadenv and XlHosts. The yun
host is named xlpr0 in the current host registry. Upload and SHA-256 validation
finish before changing the nginx document-root symlink; old releases remain.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import shlex
import sys
import tarfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "frontend" / "dist")
    parser.add_argument("--host", default="xlpr0")
    parser.add_argument("--user", default="chenkunze")
    args = parser.parse_args()
    dist = args.dist.resolve(strict=True)
    required = ["index.html", "offline-library/index.html", "offline-library/sw.js", "offline-library/runtime-manifest.json"]
    for name in required:
        if not (dist / name).is_file():
            raise SystemExit(f"Incomplete frontend build: {name}")
    files = sorted(path for path in dist.rglob("*") if path.is_file())
    manifest = {}
    for file in files:
        if file.is_symlink():
            raise SystemExit(f"Build contains a symlink: {file}")
        manifest[file.relative_to(dist).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    runtime = json.loads((dist / "offline-library/runtime-manifest.json").read_text(encoding="utf-8"))
    for url in runtime["assets"]:
        if url.lstrip("/") not in manifest:
            raise SystemExit(f"Offline runtime dependency is missing: {url}")

    # Support the sibling editable runtime without installing or switching
    # Python environments. All environment loading still uses its public API.
    sys.path.insert(0, str(ROOT.parent / "xlproject" / "src"))
    import xlproject.loadenv  # noqa: F401
    import paramiko
    from pyxllib.prog.xlenv import XlHosts
    from backend.core.temp_paths import codeyun_temp_root

    release = time.strftime("%Y%m%d%H%M%S") + "-offline-" + uuid.uuid4().hex[:6]
    archive = codeyun_temp_root("offline-library-release") / (release + ".tar.gz")
    with tarfile.open(archive, "w:gz", compresslevel=3) as tar:
        for file in files:
            tar.add(file, arcname=file.relative_to(dist).as_posix(), recursive=False)
        data = json.dumps(manifest, sort_keys=True).encode()
        info = tarfile.TarInfo("deployment-manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    connection = XlHosts.find_link(args.host)
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(connection["ip"], port=XlHosts.find_locator(connection, "ssh"), username=args.user,
                   password=XlHosts.find_passwd(args.host, "ssh", args.user), timeout=20,
                   allow_agent=False, look_for_keys=False)
    remote_archive = "/var/www/codeyun-frontend/" + release + ".tar.gz"
    try:
        with client.open_sftp() as sftp:
            sftp.put(str(archive), remote_archive)
        # This script only creates a new static release and switches one
        # symlink. It never resets repositories, restarts backend or deletes
        # previous deployments. Every extracted path stays in the new release.
        parameters = {"release": release, "archive": remote_archive, "sha": sha}
        remote = f"""
import hashlib, json, os, pathlib, shutil, tarfile
p = json.loads({json.dumps(json.dumps(parameters))})
root = pathlib.Path('/var/www/codeyun-frontend').resolve(strict=True)
assert str(root) == '/var/www/codeyun-frontend'
archive = pathlib.Path(p['archive']).resolve(strict=True)
assert archive.parent == root
assert hashlib.sha256(archive.read_bytes()).hexdigest() == p['sha'], 'Archive checksum mismatch'
release = root / 'releases' / p['release']
assert not release.exists(), 'Release already exists'
previous = (root / 'current').resolve(strict=True)
assert previous.is_relative_to(root / 'releases'), 'Unexpected current document root'
release.mkdir()
with tarfile.open(archive, 'r:gz') as tar:
    for item in tar.getmembers():
        assert not item.issym() and not item.islnk(), 'Archive contains links'
        assert (release / item.name).resolve().is_relative_to(release), 'Archive path escape'
    tar.extractall(release, filter='data')
manifest = json.loads((release / 'deployment-manifest.json').read_text())
for name, sha in manifest.items():
    assert hashlib.sha256((release / name).read_bytes()).hexdigest() == sha, 'File checksum mismatch: ' + name
# Already-open online tabs can still request their previous hashed chunks.
for directory in ['assets', 'plugins/project-graph/assets', 'offline-library/assets']:
    old = previous / directory
    if not old.is_dir(): continue
    for file in old.rglob('*'):
        if not file.is_file(): continue
        destination = release / directory / file.relative_to(old)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, destination)
link = root / ('.current-' + p['release'])
link.symlink_to(release, target_is_directory=True)
os.replace(link, root / 'current')
archive.unlink()
print(json.dumps({{'release': str(release), 'previous': str(previous), 'verified_files': len(manifest)}}))
"""
        _, stdout, stderr = client.exec_command("python3 -c " + shlex.quote(remote), timeout=180)
        result, errors = stdout.read().decode(), stderr.read().decode()
        if stdout.channel.recv_exit_status() != 0:
            raise RuntimeError(f"Deployment failed, previous release remains unless switch completed: {errors}")
        print(result.strip())
        print(f"Local archive for cleanup: {archive} ({archive.stat().st_size} bytes)")
    finally:
        client.close()


if __name__ == "__main__":
    main()
