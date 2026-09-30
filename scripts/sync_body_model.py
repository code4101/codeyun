"""Import MakeHuman's CC0 adult presets and optional opaque clothing shell.

Run: uv run --with trimesh --with scipy python scripts/sync_body_model.py
Only asset data is reused; no MakeHuman application source is embedded. The
MakeHuman target format is a vertex index plus XYZ offset. Apply the official
adult-male preset, retain body and eye surfaces, use trimesh Loop subdivision,
and export smooth normals/materials in a self-contained GLB for Model Viewer.
"""
from pathlib import Path
from urllib.request import urlopen
import hashlib
import json

import numpy as np
import trimesh

REVISION = 'a8bc2d54ff0ac92e78ff71431b1023eda42bf482'
ROOT = f'https://raw.githubusercontent.com/makehumancommunity/makehuman/{REVISION}/'
ASSETS = {
    'makehuman/data/3dobjs/base.obj': '8e761e6624b8f54536409135d1636da63b32486a90d4897f84e121d144f6fb4c',
    'makehuman/data/targets/macrodetails/asian-male-young.target': 'ed2e8c191cb6b87b4a2d97c80486acb2604fa549316c7aa2a738a5d5a14334dc',
    'makehuman/data/targets/macrodetails/asian-female-young.target': '095fe79694fa19e1fe98d93009ec116199bd524e081c640351a10eccf2cca1eb',
    'LICENSE.ASSETS.md': 'f6089cba01cb570a24712b41ab8a586ccd3cc5ef53dc266ca50b95c288956d2c',
}
OUTPUT = Path(__file__).resolve().parents[1] / 'frontend/public/models/human-body'


def main():
    contents = {}
    for name, sha in ASSETS.items():
        with urlopen(ROOT + name, timeout=45) as response:
            raw = response.read()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError(f'Upstream checksum changed: {name}')
        contents[name] = raw.decode('utf-8')
    points, groups = [], {'body': [], 'helper-l-eye': [], 'helper-r-eye': [], 'helper-tights': []}
    group = ''
    for line in contents['makehuman/data/3dobjs/base.obj'].splitlines():
        fields = line.split()
        if not fields: continue
        if fields[0] == 'v': points.append([float(x) for x in fields[1:4]])
        elif fields[0] == 'g': group = fields[1]
        elif fields[0] == 'f' and group in groups:
            face = [int(x.split('/')[0]) - 1 for x in fields[1:]]
            groups[group].extend([[face[0], face[i], face[i + 1]] for i in range(1, len(face) - 1)])
    original = np.array(points, dtype=np.float64)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    models = {}
    for sex in ('male', 'female'):
        for covered in (True, False):
            name = f'{sex}-{"ordinary" if covered else "surface"}.glb'
            result, triangles = build_model(original, groups, contents[f'makehuman/data/targets/macrodetails/asian-{sex}-young.target'], covered)
            (OUTPUT / name).write_bytes(result)
            models[name] = {'triangles': triangles, 'bytes': len(result), 'sha256': hashlib.sha256(result).hexdigest()}
            print(f'Imported {name}: {triangles} triangles / {len(result)} bytes')
    (OUTPUT / 'LICENSE.txt').write_text(contents['LICENSE.ASSETS.md'], encoding='utf-8')
    metadata = {
        'title': 'MakeHuman HM08 adult male and female surfaces', 'sourceProject': 'https://github.com/makehumancommunity/makehuman',
        'revision': REVISION, 'assets': [{'url': ROOT + name, 'sha256': sha} for name, sha in ASSETS.items()],
        'license': 'CC0 1.0 Universal', 'licenseUrl': 'https://creativecommons.org/publicdomain/zero/1.0/',
        'credits': 'MakeHuman community; Data Collection AB, Joel Palmius, Jonas Hauquier; original targets by Manuel Bastioni',
        'changes': ['Official asian-male-young / asian-female-young targets applied to HM08 mesh', 'Retained body and eye groups; ordinary variants also include the official helper-tights clothing shell, offset outward 8 mm', 'Uniform reference display height 1.75 m; centered X/Z, floor Y=0', 'One Loop subdivision via trimesh; smooth vertex normals; opaque neutral materials; GLB export'],
        'scope': 'External body surface only; not a clinical reproductive-system or internal-organ model',
        'models': models,
    }
    (OUTPUT / 'source.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build_model(original, groups, target, covered):
    points = original.copy()
    for line in target.splitlines():
        fields = line.split()
        if not fields or fields[0].startswith('#'): continue
        points[int(fields[0])] += np.array([float(x) for x in fields[1:4]])
    body_points = points[np.unique(groups['body'])]
    low, high = body_points.min(axis=0), body_points.max(axis=0)
    points -= [(low[0] + high[0]) / 2, low[1], (low[2] + high[2]) / 2]
    points *= 1.75 / (high[1] - low[1])
    scene = trimesh.Scene()
    triangles = 0
    for name, faces in groups.items():
        garment = name == 'helper-tights'
        if garment and not covered:
            continue
        mesh = trimesh.Trimesh(vertices=points.copy(), faces=faces, process=False)
        mesh.remove_unreferenced_vertices()
        mesh = mesh.subdivide_loop(iterations=1)
        if garment:
            # An actual opaque 3D shell remains covering from every camera angle
            # and in PNG export; no screen-space privacy overlay is involved.
            mesh.vertices += mesh.vertex_normals * 0.008
        triangles += len(mesh.faces)
        mesh.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            name='Clothing' if garment else ('Surface' if name == 'body' else 'Eyes'),
            baseColorFactor=[46, 79, 72, 255] if garment else ([145, 120, 98, 255] if name == 'body' else [182, 180, 169, 255]),
            metallicFactor=0, roughnessFactor=0.8, doubleSided=False,
        ))
        scene.add_geometry(mesh, node_name=name)
    return trimesh.exchange.gltf.export_glb(scene, include_normals=True), triangles


if __name__ == '__main__':
    main()
