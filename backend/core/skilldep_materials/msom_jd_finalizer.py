"""Finalize 京东 MSOM 2020 交易级数据集 (sd-98717f83a58b) from the verified local HF snapshot.

Input is the staging ``manifest.json`` recorded in the catalog (11 of 12 files of
``a6687543/MSOM_Data_Driven_Challenge_2020``; ``JD_click_data.csv`` 920 MB was not
downloaded).  Staging is only read; every file is checked against its SHA-256.

The six JD tables are already clean CSV, so they are shipped byte-identical (same
SHA-256 as the source) and fully scanned: header, row count, per-column type,
empty/null-token counts, min/max, key uniqueness and cross-table key coverage.
The dataset paper (docx) is parsed in document order into full text with its 7
field tables and 14 figures; the field tables become ``数据字典.csv``.

Package (``storage_layout`` typed dir)::

    data/JD_*.csv                6 张原始表（字节不变）
    data/数据字典.csv             论文 Table 1–7 的字段、类型、说明、样例（含来源表号/行号）
    data/表结构与质量.csv         每列实测：类型、非空、空值记号、去重数、最小/最大
    data/说明文档正文.md          论文全文（段落与表格按原顺序）
    data/说明文档图片/imageN.png   论文内全部图片（docx 内原字节）
    data/使用说明.md
    originals/…                  docx、附录 notebook 压缩包与 HTML、HF README、.gitattributes
    schema.json / provenance.json / manifest.json

``partial`` while ``JD_click_data.csv`` is missing.

CLI::

    uv run python -m backend.core.skilldep_materials.msom_jd_finalizer [--dry-run] [--backup-dir D] [--register]
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from . import storage_layout as layout
from .finalize import (csv_bytes, layout_source, rewrite_package, sha256_bytes, sha256_file, verify_package,
                       write_package)

MATERIAL_ID = 'sd-98717f83a58b'
SOURCE_URL = 'https://huggingface.co/datasets/a6687543/MSOM_Data_Driven_Challenge_2020'
DOCX = 'MSOM Research Challenge_jan5_2020_final (1).docx'
ORIGINALS = (DOCX, 'Appendix Data Review and Illustrative Example.zip',
             'Appendix Data Review and Illustrative Example.html', 'README.md', '.gitattributes')
TABLES = {  # file -> (paper table name, key column unique per row or None)
    'JD_sku_data.csv': ('skus', 'sku_ID'),
    'JD_user_data.csv': ('users', 'user_ID'),
    'JD_click_data.csv': ('clicks', None),
    'JD_order_data.csv': ('orders', None),
    'JD_delivery_data.csv': ('delivery', 'package_ID'),
    'JD_inventory_data.csv': ('inventory', None),
    'JD_network_data.csv': ('network', None),
}
COMPOSITE_KEYS = {'JD_order_data.csv': ('order_ID', 'sku_ID'), 'JD_inventory_data.csv': ('dc_ID', 'sku_ID', 'date'),
                  'JD_network_data.csv': ('region_ID', 'dc_ID')}
# (child file, column) -> (parent file, column): share of child values found in parent
FOREIGN_KEYS = [
    ('JD_order_data.csv', 'sku_ID', 'JD_sku_data.csv', 'sku_ID'),
    ('JD_order_data.csv', 'user_ID', 'JD_user_data.csv', 'user_ID'),
    ('JD_delivery_data.csv', 'order_ID', 'JD_order_data.csv', 'order_ID'),
    ('JD_inventory_data.csv', 'sku_ID', 'JD_sku_data.csv', 'sku_ID'),
    ('JD_inventory_data.csv', 'dc_ID', 'JD_network_data.csv', 'dc_ID'),
]
NULL_TOKENS = ('', '-', 'U', '-1')
_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_INT = re.compile(r'-?\d+')
_FLOAT = re.compile(r'-?(\d+\.\d*|\.\d+|\d+)([eE]-?\d+)?')
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
_DATETIME = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?')
_MONTH = re.compile(r'\d{4}-\d{2}')


def _kind(v: str) -> str:
    for name, pat in (('int', _INT), ('float', _FLOAT), ('datetime', _DATETIME), ('date', _DATE), ('month', _MONTH)):
        if pat.fullmatch(v):
            return name
    return 'string'


def profile_csv(data: bytes) -> dict[str, Any]:
    """Full scan of one CSV: header, rows, per-column stats and the value sets of every column."""
    text = data.decode('utf-8')
    reader = csv.reader(io.StringIO(text, newline=''))
    header = next(reader)
    cols = [{'name': h, 'nonempty': 0, 'tokens': {t: 0 for t in NULL_TOKENS}, 'kinds': {}, 'values': set(),
             'min': None, 'max': None} for h in header]
    rows = 0
    for row in reader:
        if len(row) != len(header):
            raise ValueError(f'row {rows + 2}: {len(row)} fields for {len(header)} columns')
        rows += 1
        for c, v in zip(cols, row):
            c['values'].add(v)
            if v in c['tokens']:
                c['tokens'][v] += 1
            if v != '':
                c['nonempty'] += 1
            if v in NULL_TOKENS:
                continue
            k = _kind(v)
            c['kinds'][k] = c['kinds'].get(k, 0) + 1
            num = float(v) if k in ('int', 'float') else v
            if c['min'] is None or _lt(num, c['min']):
                c['min'] = num
            if c['max'] is None or _lt(c['max'], num):
                c['max'] = num
    if text and not text.endswith('\n'):
        raise ValueError('file does not end with a newline')
    return {'header': header, 'rows': rows, 'columns': cols}


def _lt(a: Any, b: Any) -> bool:
    return (a < b) if type(a) is type(b) else str(a) < str(b)


def _type(col: dict[str, Any]) -> str:
    kinds = set(col['kinds'])
    if kinds <= {'int'}:
        return 'int' if kinds else 'empty'
    if kinds <= {'int', 'float'}:
        return 'float'
    return '/'.join(sorted(kinds)) if len(kinds) > 1 else kinds.pop()


def _text(el: ElementTree.Element) -> str:
    out = []
    for node in el.iter():
        if node.tag == _W + 't' and node.text:
            out.append(node.text)
        elif node.tag == _W + 'tab':
            out.append('\t')
        elif node.tag in (_W + 'br', _W + 'cr'):
            out.append('\n')
        elif node.tag == _W + 'footnoteReference':
            out.append(f'[^{node.get(_W + "id")}]')
    return ''.join(out)


def parse_docx(data: bytes) -> dict[str, Any]:
    """Body of the paper in order: ``blocks`` (paragraph / table / image refs), ``tables`` and ``media``."""
    z = zipfile.ZipFile(io.BytesIO(data))
    rels = ElementTree.fromstring(z.read('word/_rels/document.xml.rels'))
    target = {r.get('Id'): r.get('Target') for r in rels}
    body = ElementTree.fromstring(z.read('word/document.xml')).find(_W + 'body')
    blocks, tables = [], []
    rid = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed'
    for i, el in enumerate(body):
        if el.tag == _W + 'p':
            images = [target[b.get(rid)] for b in el.iter('{http://schemas.openxmlformats.org/drawingml/2006/main}blip')
                      if b.get(rid) in target]
            blocks.append({'kind': 'paragraph', 'index': i, 'text': _text(el), 'images': images})
        elif el.tag == _W + 'tbl':
            rows = [[_text(tc).strip() for tc in tr.findall(_W + 'tc')] for tr in el.findall(_W + 'tr')]
            tables.append({'index': i, 'rows': rows})
            blocks.append({'kind': 'table', 'index': i, 'table': len(tables) - 1})
    media = {n: z.read(n) for n in z.namelist() if n.startswith('word/media/')}
    footnotes = {}
    if 'word/footnotes.xml' in z.namelist():
        for fn in ElementTree.fromstring(z.read('word/footnotes.xml')).findall(_W + 'footnote'):
            if fn.get(_W + 'type') is None:  # separator / continuation entries carry a type
                footnotes[fn.get(_W + 'id')] = ' '.join(_text(p).strip() for p in fn.findall(_W + 'p')).strip()
    return {'blocks': blocks, 'tables': tables, 'media': media, 'footnotes': footnotes}


def dictionary(doc: dict[str, Any]) -> list[list[str]]:
    """Rows of the 7 field tables, named by the caption that follows each table."""
    out = []
    for n, t in enumerate(doc['tables']):
        after = [b for b in doc['blocks'] if b['index'] > t['index'] and b['kind'] == 'paragraph' and b['text'].strip()]
        m = re.match(r'Table (\d+): Description of the (\w+) table', after[0]['text'].strip()) if after else None
        if not m or [c.strip() for c in t['rows'][0]] != ['Field', 'Data type', 'Description', 'Sample value']:
            raise ValueError(f'docx table {n + 1} is not a captioned field table')
        for r, row in enumerate(t['rows'][1:], 2):
            out.append([m.group(2), row[0], row[1], row[2], row[3], f'Table {m.group(1)}', str(r)])
    return out


def _markdown(doc: dict[str, Any]) -> str:
    lines = []
    for b in doc['blocks']:
        if b['kind'] == 'paragraph':
            if b['text'].strip():
                lines += [b['text'], '']
            lines += [f'![{Path(i).name}](说明文档图片/{Path(i).name})' for i in b['images']]
            if b['images']:
                lines.append('')
        else:
            rows = doc['tables'][b['table']]['rows']
            esc = lambda c: c.replace('|', '\\|').replace('\n', ' ')  # noqa: E731
            lines += ['| ' + ' | '.join(esc(c) for c in rows[0]) + ' |', '|' + '---|' * len(rows[0])]
            lines += ['| ' + ' | '.join(esc(c) for c in r) + ' |' for r in rows[1:]] + ['']
    if doc.get('footnotes'):
        lines += ['## 脚注', ''] + [f'[^{k}]: {v}' for k, v in doc['footnotes'].items()] + ['']
    return '\n'.join(lines)


QUALITY_COLUMNS = ['文件', '列', '论文类型', '实测类型', '行数', '非空', '空串', '“-”', '“U”', '“-1”', '去重值数',
                   '最小', '最大', '论文说明']
DICT_COLUMNS = ['表', '字段', '论文类型', '说明', '样例', '来源表', '来源表行']


def build(manifest_path: Path, manifest_sha256: str) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    if sha256_file(manifest_path) != manifest_sha256:
        raise ValueError('staging manifest SHA-256 mismatch')
    smf = json.loads(manifest_path.read_text(encoding='utf-8'))
    if smf.get('material_id') != MATERIAL_ID:
        raise ValueError('staging manifest is for another material')
    base = manifest_path.parent
    src: dict[str, bytes] = {}
    for f in smf['files']:
        data = (base / f['path']).read_bytes()
        if len(data) != f['size_bytes'] or sha256_bytes(data) != f['sha256']:
            raise ValueError(f'staging file differs from manifest: {f["path"]}')
        src[f['path']] = data
    listing = {f['path']: f['size'] for f in smf['listing']}
    missing = sorted(p for p in listing if p not in src)
    if set(missing) - {'JD_click_data.csv'} or set(src) - set(listing):
        raise ValueError(f'unexpected source coverage: missing {missing}')

    doc = parse_docx(src[DOCX])
    dict_rows = dictionary(doc)
    files: dict[str, bytes] = {}
    records, tables, quality, profiles = {}, {}, [], {}
    for name, (table, key) in TABLES.items():
        if name not in src:
            continue
        prof = profile_csv(src[name])
        documented = {r[1].lower(): r for r in dict_rows if r[0] == table}
        same = [h.lower() for h in prof['header']] == list(documented)
        cols = {c['name']: c for c in prof['columns']}
        body = list(csv.reader(io.StringIO(src[name].decode('utf-8'), newline='')))[1:]
        keys = {}
        for k in ([key] if key else []) + (['+'.join(COMPOSITE_KEYS[name])] if name in COMPOSITE_KEYS else []):
            idx = [prof['header'].index(c) for c in k.split('+')]
            distinct = len({tuple(r[i] for i in idx) for r in body})
            keys[k] = {'unique': distinct == prof['rows'], 'distinct': distinct, 'rows': prof['rows']}
        exact_dups = prof['rows'] - len({tuple(r) for r in body})
        for c in prof['columns']:
            p = documented.get(c['name'].lower())
            quality.append([name, c['name'], p[2] if p else '', _type(c), prof['rows'], c['nonempty'],
                            c['tokens'][''], c['tokens']['-'], c['tokens']['U'], c['tokens']['-1'],
                            len(c['values']), c['min'], c['max'], p[3] if p else ''])
        files[f'data/{name}'] = src[name]
        records[f'data/{name}'] = prof['rows']
        tables[name] = {'paper_table': table, 'rows': prof['rows'], 'columns': prof['header'],
                        'sha256': sha256_bytes(src[name]), 'bytes': len(src[name]), 'unique_keys': keys,
                        'exact_duplicate_rows': exact_dups,
                        'header_vs_paper': 'same (case-insensitive)' if same else
                        f'differs: paper {[r[1] for r in documented.values()]}'}
        profiles[name] = cols
    fk = []
    for child, ccol, parent, pcol in FOREIGN_KEYS:
        vals = profiles[child][ccol]['values'] - set(NULL_TOKENS)
        fk.append({'child': f'{child}.{ccol}', 'parent': f'{parent}.{pcol}', 'distinct': len(vals),
                   'found_in_parent': len(vals & profiles[parent][pcol]['values'])})
    files['data/数据字典.csv'] = csv_bytes(DICT_COLUMNS, dict_rows)
    files['data/表结构与质量.csv'] = csv_bytes(QUALITY_COLUMNS, quality)
    files['data/说明文档正文.md'] = _markdown(doc).encode()
    media = {}
    for n, b in sorted(doc['media'].items()):
        files[f'data/说明文档图片/{Path(n).name}'] = b
        media[Path(n).name] = {'docx_member': n, 'sha256': sha256_bytes(b), 'bytes': len(b)}
    for name in ORIGINALS:
        files[f'originals/{name.lstrip(".")}'] = src[name]
    gaps = [{'file': p, 'bytes': listing[p], 'reason': '采集时超出单次 200MB 预算未下载（不是来源缺失），由原下载会话负责补采'}
            for p in missing]
    scope = {'kind': 'partial' if gaps else 'full', 'tables_present': len(tables), 'tables_total': len(TABLES),
             'rows': {n: t['rows'] for n, t in tables.items()}, 'rows_total': sum(records.values()),
             'paper_field_rows': len(dict_rows),
             'paper_paragraphs': sum(1 for b in doc['blocks'] if b['kind'] == 'paragraph' and b['text'].strip()),
             'paper_tables': len(doc['tables']), 'paper_images': len(media), 'paper_footnotes': len(doc['footnotes']), 'foreign_keys': fk,
             'unique_keys': {n: t['unique_keys'] for n, t in tables.items()},
             'exact_duplicate_rows': {n: t['exact_duplicate_rows'] for n, t in tables.items()}, 'gaps': gaps,
             'source_files_verified': len(src)}
    provenance = {'material_id': MATERIAL_ID, 'title': '京东 MSOM 2020 交易级数据集', 'source_url': SOURCE_URL,
                  'publisher': 'JD.com / INFORMS MSOM（HF 镜像 a6687543）', 'license': 'ODbL（HF README），版权归 JD.com',
                  'staging_manifest': manifest_path.as_posix(), 'staging_manifest_sha256': manifest_sha256,
                  'source_files': {f['path']: {'sha256': f['sha256'], 'bytes': f['size_bytes']} for f in smf['files']},
                  'tables': tables, 'paper': {'file': DOCX, 'sha256': sha256_bytes(src[DOCX]), 'media': media},
                  'parser': 'backend/core/skilldep_materials/msom_jd_finalizer.py', 'gaps': gaps}
    schema = {'schema': 'codeyun.skilldep_dataset_schema/v1', 'material_id': MATERIAL_ID, 'files': {
        **{f'data/{n}': {'format': 'csv-utf8', 'record': f'{t["paper_table"]} 表一行', 'columns': {
            q[1]: {'paper_type': q[2], 'observed_type': q[3], 'description': q[13]} for q in quality if q[0] == n}}
           for n, t in tables.items()},
        'data/数据字典.csv': {'format': 'csv-utf8-bom', 'columns': DICT_COLUMNS, 'record': '论文字段表一行（原文）'},
        'data/表结构与质量.csv': {'format': 'csv-utf8-bom', 'columns': QUALITY_COLUMNS, 'record': '一列的全量实测'},
        'data/说明文档正文.md': {'format': 'markdown', 'record': '论文全文，段落与表格按原顺序'},
        'data/说明文档图片/': {'format': 'png', 'record': '论文内图片，docx 原字节'}}}
    files |= {'data/使用说明.md': _readme(tables, scope).encode(),
              'schema.json': (json.dumps(schema, ensure_ascii=False, indent=1) + '\n').encode(),
              'provenance.json': (json.dumps(provenance, ensure_ascii=False, indent=1) + '\n').encode()}
    return {'files': files, 'records': records, 'scope': scope, 'status': 'partial' if gaps else 'finalized',
            'pending': [f'{g["file"]} ({g["bytes"]} B): {g["reason"]}' for g in gaps],
            'source': {'staging_manifest': manifest_path.as_posix(), 'staging_manifest_sha256': manifest_sha256}}


def _readme(tables: dict[str, Any], scope: dict[str, Any]) -> str:
    lines = ['# 京东 MSOM 2020 交易级数据集', '',
             f'来源：{SOURCE_URL}（JD.com 为 2020 MSOM 数据驱动研究挑战赛提供的 2018 年 3 月单一品类全链路数据）。',
             '各表为原始 CSV，字节不变（SHA-256 与来源一致），已全量扫描校验。', '',
             '| 文件 | 论文表 | 行数 | 列 |', '|---|---|---|---|']
    for n, t in tables.items():
        lines.append(f'| {n} | {t["paper_table"]} | {t["rows"]} | {", ".join(t["columns"])} |')
    lines += ['', '- 数据字典.csv：论文 Table 1–7 的字段、类型、说明、样例原文（含未下载的 clicks 表字段定义）。',
              '- 表结构与质量.csv：每列实测类型、非空数、空值记号（空串、“-”、“U”、“-1”）计数、去重值数、最小/最大。',
              f'- 说明文档正文.md：论文全文（{scope["paper_paragraphs"]} 段、{scope["paper_tables"]} 张表、'
              f'{scope["paper_footnotes"]} 条脚注），'
              f'图片 {scope["paper_images"]} 张在 说明文档图片/。',
              '- originals/：论文 docx、附录 notebook（zip 与 HTML）、HF README 原件。', '', '## 键与关联（全量实测）', '']
    for n, k in scope['unique_keys'].items():
        if k:
            lines.append(f'- {n}：' + '，'.join(
                f'{c} ' + ('唯一' if v['unique'] else f'不唯一（{v["rows"]} 行 {v["distinct"]} 个取值）') for c, v in k.items())
                + f'；完全重复行 {scope["exact_duplicate_rows"][n]}（来源原样保留，未去重）')
    for f in scope['foreign_keys']:
        lines.append(f'- {f["child"]} → {f["parent"]}：{f["distinct"]} 个取值中 {f["found_in_parent"]} 个能在上级表找到')
    lines += ['', '## 已知缺口', '']
    lines += [f'- {g["file"]}（{g["bytes"]} 字节，点击表）：{g["reason"]}；本数据集因此为部分完成（partial）。'
              for g in scope['gaps']] or ['- 无']
    lines.append('')
    return '\n'.join(lines)


def _target(item: dict[str, Any], naming: dict[str, Any], root: Path) -> tuple[Path, dict[str, Any]]:
    local = item['local']
    if local.get('status') not in ('downloaded', 'partial'):
        raise ValueError('material has no verified staging acquisition')
    built = build(Path(local['manifest_path']), local['manifest_sha256'])
    where = layout.resolve(item['id'], naming, root)
    built['source'] |= layout_source(where)
    return Path(where['local_dir']), built


def finalize(item: dict[str, Any], naming: dict[str, Any], *, root: Path = layout.FINAL_LOCAL_ROOT,
             backup_dir: Path | None = None, dry_run: bool = False, target: Path | None = None) -> dict[str, Any]:
    """Write the package to the layout dir (or ``target``); an existing verified package is rewritten."""
    where, built = _target(item, naming, root)
    target = Path(target) if target else where
    kw = dict(records=built['records'], scope=built['scope'], status=built['status'], source=built['source'],
              pending=built['pending'])
    if dry_run:
        return {'status': 'dry_run', 'path': str(target), 'package_status': built['status'], 'scope': built['scope'],
                'files': len(built['files']), 'bytes': sum(len(b) for b in built['files'].values())}
    if not target.exists():
        return write_package(target, item['id'], built['files'], **kw)
    if backup_dir is None:
        raise ValueError(f'{target} exists; pass backup_dir to rewrite it')
    return rewrite_package(target, built['files'], backup_dir=backup_dir, reason='rebuild from verified staging', **kw)


def finalization_record(target: Path, previous: dict[str, Any]) -> dict[str, Any]:
    """CodeHub local finalization record of the verified package; partial packages stay ``cleaning``."""
    target = Path(target)
    check = verify_package(target)
    if check['status'] != 'verified':
        raise ValueError(f'package not verified: {check}')
    mbytes = (target / 'manifest.json').read_bytes()
    manifest = json.loads(mbytes)
    s = manifest['scope']
    files = [p for p in target.rglob('*') if p.is_file()]
    fk = '；'.join(f'{f["child"]}→{f["parent"]} {f["found_in_parent"]}/{f["distinct"]}' for f in s['foreign_keys'])
    note = (f"{s['kind']}: {s['tables_present']}/{s['tables_total']} 张京东表全量（{s['rows_total']} 行，字节与源一致，"
            f"逐列实测类型/空值/范围）；论文 {s['paper_paragraphs']} 段全文、{s['paper_field_rows']} 行字段字典、"
            f"{s['paper_images']} 张图片；外键 {fk}。缺口：{'、'.join(g['file'] for g in s['gaps']) or '无'}"
            f"（920MB 点击表未下载，由原下载会话补采）。源 manifest sha256 "
            f"{manifest['source']['staging_manifest_sha256']}；未上传百度网盘。")
    return {'status': 'cleaning' if manifest['status'] == 'partial' else 'finalized', 'path': target.as_posix(),
            'size_bytes': sum(p.stat().st_size for p in files), 'file_count': len(files),
            'manifest_path': (target / 'manifest.json').as_posix(), 'manifest_sha256': sha256_bytes(mbytes),
            'source_acquisition_device_id': previous.get('source_acquisition_device_id'),
            'expected_resource_version': previous.get('expected_resource_version'),
            'finished_at': (manifest.get('revised_at') or manifest['finalized_at']).replace('+00:00', 'Z'),
            'note': note, 'error': None}


def register(hub: Any, target: Path, source_device_id: str | None) -> dict[str, Any]:
    """PUT the local finalization record and read it back; ``ack`` only when every sent field matches."""
    def current() -> dict[str, Any]:
        items = hub.finalizations()['items']
        items = items.values() if isinstance(items, dict) else items
        return next((i for i in items if i.get('material_id') == MATERIAL_ID and i.get('root') == 'local'), {})
    previous = current() or {'source_acquisition_device_id': source_device_id}
    record = finalization_record(target, previous)
    put = hub.put_finalization(MATERIAL_ID, 'local', record)
    back = current()
    mismatch = sorted(k for k, v in record.items() if back.get(k) != v)
    return {'ack': not mismatch, 'mismatch': mismatch, 'record': record, 'put_response': put, 'read_back': back}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from .catalog import CatalogStore
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--root', default=str(layout.FINAL_LOCAL_ROOT))
    p.add_argument('--naming', default=str(layout.DEFAULT_NAMING), help='cached CodeHub naming snapshot')
    p.add_argument('--target', help='write a candidate here instead of the layout dir (never registered)')
    p.add_argument('--dry-run', action='store_true', help='check and report without writing')
    p.add_argument('--backup-dir', help='backup dir (outside the package) when rewriting an existing package')
    p.add_argument('--register', action='store_true', help='PUT the CodeHub local finalization and read it back')
    a = p.parse_args(argv)
    item = CatalogStore().load()['items'][MATERIAL_ID]
    result = finalize(item, layout.load_naming(a.naming), root=Path(a.root), dry_run=a.dry_run,
                      backup_dir=Path(a.backup_dir) if a.backup_dir else None,
                      target=Path(a.target) if a.target else None)
    ok = result['status'] in ('verified', 'unchanged', 'rewritten', 'dry_run')
    if ok and a.register and result['status'] != 'dry_run' and not a.target:
        from .codehub import CodeHubSkillDep
        hub = CodeHubSkillDep()
        hub.verify_peer()
        result['codehub'] = register(hub, Path(result['path']), item['local'].get('device_id'))
        ok = result['codehub']['ack']
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
