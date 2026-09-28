"""Build the pinned GPL editor separately from CodeYun's Vue application.

uv run python integrations/project-graph/build.py --checkout <upstream checkout>

Setup: clone upstream.json's repository into a dedicated checkout, checkout the exact
commit, then pass --install on the first build (Git, pnpm and Node are required).
The initial migration was verified with Node 24; upstream declares Node >=26.
To upgrade: change upstream.json, use a clean checkout of that commit, rebuild, run
the upstream type-check and smoke.cjs, then retain the previous build and documents
before deployment. Never auto-upgrade stored PRG content or use a moving branch.

Runtime: /standalone/notes/project-graph. PG files live in the authenticated
server library with global ResourceIdentity numbers and ResourceAccessGrant rules.
GraphStorage is the editor boundary; the legacy IndexedDB store is read-only
migration input assigned to an explicitly designated owner. import_local.py uses
the shipped editor to upgrade local PRG copies, preserving the originals.
plate.html is an editor-only entry for Notes' format_type="plate". It shares
PlateDocumentEditor with PG node details but starts no graph/file services. Notes
persists a versioned codeyun.plate JSON envelope through its existing content API.
HTML remains a separate, unchanged body format. This does not turn notes into PRG
files or automatically link a PG node to an external note document.
Protocol v1 is same-origin, frame/session scoped. It carries native PRG bytes and
status, not upstream private objects. Desktop file/account/extension workflows are
outside this browser entry. Ordinary files use revision-checked autosave. Explicit
collaboration uses CodeYun's shared object session, durable commits and leases;
the PG provider owns UUID/reference validation and canvas gestures. Browser drafts
assist reconnect recovery but do not guarantee recovery after device/storage loss.

Only overlay files are added; upstream source files are not rewritten. Runtime output
is generated under frontend/public/plugins/project-graph and is not checked into Git.
Keep this directory and upstream.json together when upgrading or packaging the plugin.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    checkout = args.checkout.resolve()
    manifest = json.loads((HERE / 'upstream.json').read_text())
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=checkout, text=True).strip()
    if actual != manifest['commit']:
        raise SystemExit(f'Upstream mismatch: expected {manifest["commit"]}, got {actual}')
    subprocess.run(['git', 'diff', '--quiet', 'HEAD'], cwd=checkout, check=True)
    app = checkout / 'app'
    destination = app / 'src' / 'codeyun'
    destination.mkdir(parents=True, exist_ok=True)
    for source in (HERE / 'overlay').iterdir():
        target = app / source.name if source.name in {'embed.html', 'plate.html', 'vite.embed.config.ts'} else destination / source.name
        shutil.copy2(source, target)
        if target.suffix in {'.ts', '.tsx'}:
            target.write_text(target.read_text(encoding='utf-8').replace(
                '../../../frontend/src/collaboration/', './shared/'), encoding='utf-8')
    shared_collaboration = ROOT / 'frontend/src/collaboration'
    shutil.copytree(shared_collaboration, destination / 'shared', dirs_exist_ok=True)
    # The host and embedded editor share the same empty-body semantics.
    plate_value = ROOT / 'frontend/src/components/rich-text/plateValue.ts'
    shutil.copy2(plate_value, destination / 'plateValue.ts')
    pnpm = shutil.which('pnpm')
    if not pnpm:
        raise SystemExit('pnpm is required')
    if args.install:
        subprocess.run([pnpm, 'install', '--frozen-lockfile', '--ignore-scripts', '--config.engine-strict=false'], cwd=checkout, check=True)
    subprocess.run([pnpm, '--filter', '@graphif/project-graph', 'type-check'], cwd=checkout, check=True)
    subprocess.run([pnpm, 'exec', 'vite', 'build', '--config', 'vite.embed.config.ts'], cwd=app, check=True)
    output = ROOT / 'frontend/public/plugins/project-graph'
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(app / 'dist-codeyun', output, dirs_exist_ok=True)
    shutil.copy2(app / 'LICENSE', output / 'LICENSE.txt')
    # Stable host URL; keep the file icon aligned with the pinned upstream release.
    shutil.copy2(app / 'src/assets/icon.png', output / 'icon.png')
    (output / 'upstream.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    # Corresponding source: pinned upstream tracked files plus integration/build source.
    files = subprocess.check_output(['git', 'ls-files'], cwd=checkout, text=True).splitlines()
    with zipfile.ZipFile(output / 'source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for filename in files:
            source = checkout / filename
            if source.is_file():
                archive.write(source, 'upstream/' + filename)
        for source in HERE.rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts:
                archive.write(source, 'codeyun/integrations/project-graph/' + str(source.relative_to(HERE)))
        host = ROOT / 'frontend/src/plugins/modules/project-graph'
        for source in host.rglob('*'):
            if source.is_file():
                archive.write(source, 'codeyun/frontend/src/plugins/modules/project-graph/' + str(source.relative_to(host)))
        archive.write(plate_value, 'codeyun/frontend/src/components/rich-text/plateValue.ts')
        for source in shared_collaboration.glob('*.ts'):
            archive.write(source, 'codeyun/frontend/src/collaboration/' + source.name)
    print(f'Built {manifest["commit"]}: {output}')


if __name__ == '__main__':
    main()
