"""Finalize 国家新闻出版署 网络游戏审批信息 (sd-b05c6a963661) into a package with Chinese-named ``data/``.

Input is the verified staging manifest recorded in the catalog (222 official
announcement pages under ``raw/``, SHA-256 each) and the three tables that
``tools/nppa_tables.py`` parsed from exactly those pages into ``structured/``.
The tables themselves are not in the staging manifest, so they are accepted
only when every ``来源文件`` they cite is a manifest-listed raw page whose bytes
still match, their headers are the known ones, and the row count per category
equals the counts the verified manifest records.  Staging is only read.

Downstream reads ``data/`` only::

    data/游戏审批信息.csv        国产网络游戏 / 进口网络游戏 / 进口电子游戏 审批，一行一款
    data/游戏审批变更信息.csv    审批变更
    data/游戏审批撤销信息.csv    审批撤销
    data/使用说明.md            日期范围、字段含义、已知缺口

``schema.json`` / ``provenance.json`` / ``manifest.json`` stay internal at the
package root.  Cells are kept as parsed; only the internal ``来源文件`` column
(a staging path) is dropped — every row keeps its official ``公告URL`` and the
URL→raw page/SHA-256 mapping is in ``provenance.json``.  The package is
``partial`` while the staging manifest records gaps (the 2022-08 国产网络游戏
page was header-only).

CLI::

    uv run python -m backend.core.skilldep_materials.nppa_finalizer [--root F:/data/成品数据样例] [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from . import storage_layout as layout
from .finalize import csv_bytes, layout_source, sha256_file, write_package

MATERIAL_ID = 'sd-b05c6a963661'
SOURCE_URL = 'https://www.nppa.gov.cn/bsfw/jggs/yxspjg/'
INTERNAL_COLUMN = '来源文件'
README = 'data/使用说明.md'
# staging table -> (public file, verified-manifest category or '类别' column, staging header, date columns)
TABLES = {
    'approvals': ('data/游戏审批信息.csv', '类别',
                  ['类别', '序号', '名称', '申报类别', '出版单位', '运营单位', '批复文号', '出版物号', '批准时间',
                   '批准日期', '公告标题', '公告日期', '公告URL', '来源文件'], ('批准时间',)),
    'changes': ('data/游戏审批变更信息.csv', '游戏审批变更信息',
                ['序号', '名称', '申报类别', '出版单位', '运营单位', '变更信息', '文号', '变更时间', '批复文号',
                 '批准变更时间', '公告标题', '公告日期', '公告URL', '来源文件'], ('变更时间', '批准变更时间')),
    'revocations': ('data/游戏审批撤销信息.csv', '游戏审批撤销信息',
                    ['序号', '名称', '申报类别', '出版单位', '运营单位', '撤销信息', '文号', '出版物号', '批准时间',
                     '公告标题', '公告日期', '公告URL', '来源文件'], ('批准时间',)),
}
TITLES = {'approvals': '游戏审批信息', 'changes': '游戏审批变更信息', 'revocations': '游戏审批撤销信息'}
COMMON = {
    '序号': '该行在所属公告表格中的序号；只在同一公告内唯一，跨公告会重复',
    '名称': '游戏名称',
    '出版单位': '出版单位',
    '运营单位': '运营单位',
    '出版物号': 'ISBN 出版物号；公告未列时为空',
    '公告标题': '该行所在官方公告的标题',
    '公告日期': '官方公告列表日期（YYYY-MM-DD）；按年累计更新的公告页中，批准/变更时间可能晚于此日期',
    '公告URL': '官方公告网页地址，可据此逐行核验',
}
FIELDS = {
    'approvals': COMMON | {
        '类别': '国产网络游戏 / 进口网络游戏 / 进口电子游戏',
        '申报类别': '本表全部为空：公告表头列有“申报类别”，但数据行实际没有该格（已对照原公告核实）',
        '运营单位': '运营单位；进口电子游戏公告无此列，为空',
        '批复文号': '审批文号（进口电子游戏原表头为“文号”）',
        '批准时间': '公告原文的批准时间，如“2024年01月25日”（进口电子游戏原表头为“时间”）',
        '批准日期': '批准时间对应的日期（YYYY-MM-DD），全部可解析',
    },
    'changes': COMMON | {
        '申报类别': '移动 / 客户端 / 网页 / 游戏机 等，公告原文',
        '变更信息': '变更内容原文，如原名、原出版/运营单位、原批准文号与原批准时间',
        '文号': '2014—2022 年公告的“文号”列（2023 年起为空）：2014—2019 年多为本次变更的批复文号'
                '（原批准文号写在“变更信息”中），2020—2022 年多为游戏原有的批复文号',
        '变更时间': '2014—2022 年公告的变更批准时间原文（2023 年起为空）',
        '批复文号': '2023 年起公告的“批复文号”列（此前为空），为游戏原有的批复文号，不是变更文号',
        '批准变更时间': '2023 年起公告的变更批准时间原文（此前为空）',
    },
    'revocations': COMMON | {
        '申报类别': '移动 / 客户端 / 网页 等，公告原文',
        '撤销信息': '撤销原因与撤销日期原文',
        '文号': '被撤销游戏原有的批复文号',
        '批准时间': '公告原表头即“批准时间”；数值与“撤销信息”中的撤销日期相同或晚数日，'
                    '是撤销的批准时间，不是游戏原批准时间',
    },
}
_CN_DATE = re.compile(r'(\d{4})年(\d{1,2})月(\d{1,2})日')


def _note_counts(note: str) -> dict[str, int]:
    """Per-category data rows recorded in the verified staging manifest note."""
    m = re.search(r'按类：(\{.*?\})', note or '')
    if not m:
        raise ValueError('staging manifest note has no per-category counts')
    return json.loads(m.group(1))


def _read_table(path: Path, header: list[str]) -> list[list[str]]:
    rows = list(csv.reader(io.StringIO(path.read_bytes().decode('utf-8-sig'), newline='')))
    if not rows or rows[0] != header:
        raise ValueError(f'{path.name}: unexpected header {rows[:1]}')
    if any(len(r) != len(header) for r in rows[1:]):
        raise ValueError(f'{path.name}: ragged rows')
    return rows[1:]


def _iso(text: str) -> str | None:
    m = _CN_DATE.fullmatch(text.strip())
    return date(*map(int, m.groups())).isoformat() if m else None


def _date_range(rows: list[list[str]], header: list[str], columns: tuple[str, ...]) -> dict[str, Any]:
    """Range of the table's own event dates; non-empty cells that do not parse are counted, never guessed."""
    found, unparsed = [], 0
    for r in rows:
        for c in columns:
            text = r[header.index(c)]
            if text.strip():
                iso = _iso(text)
                if iso:
                    found.append(iso)
                else:
                    unparsed += 1
    return {'columns': list(columns), 'first': min(found, default=None), 'last': max(found, default=None),
            'unparsed': unparsed}


def build(staging_manifest: Path, manifest_sha256: str) -> dict[str, Any]:
    """Check the staging tables against the verified raw pages; return package files/records/scope/source."""
    if sha256_file(staging_manifest) != manifest_sha256:
        raise ValueError('staging manifest SHA-256 mismatch')
    smf = json.loads(staging_manifest.read_text(encoding='utf-8'))
    if smf.get('material_id') != MATERIAL_ID:
        raise ValueError('staging manifest is for another material')
    base = staging_manifest.parent.parent
    listed = {f['path']: f for f in smf['files']}
    expected = _note_counts(smf.get('note'))
    files, records, counts, tables, raw_used = {}, {}, {}, {}, {}
    for key, (public, category, header, date_cols) in TABLES.items():
        src = base / 'structured' / f'{key}.csv'
        rows = _read_table(src, header)
        cut = header.index(INTERNAL_COLUMN)
        url_at = header.index('公告URL')
        for r in rows:
            entry = listed.get(r[cut])
            if entry is None or not r[url_at].startswith(SOURCE_URL):
                raise ValueError(f'{key}: row source {r[cut]!r} / {r[url_at]!r} not a verified official page')
            raw_used.setdefault(r[cut], {'url': r[url_at], 'sha256': entry['sha256']})
            label = r[0] + '审批信息' if category == '类别' else category
            counts[label] = counts.get(label, 0) + 1
        files[public] = csv_bytes(header[:cut] + header[cut + 1:], [r[:cut] + r[cut + 1:] for r in rows])
        records[public] = len(rows)
        tables[key] = {'public': public, 'rows': len(rows), 'dates': _date_range(rows, header, date_cols),
                       'staging_table': src.as_posix(), 'staging_table_sha256': sha256_file(src)}
    if counts != {k: v for k, v in expected.items() if v}:
        raise ValueError(f'row counts {counts} differ from verified staging counts {expected}')
    for rel, rec in raw_used.items():
        if sha256_file(base / rel) != rec['sha256']:
            raise ValueError(f'staging raw page changed: {rel}')

    gaps = smf.get('gaps') or []
    pending = [f'{g["url"]}: {g["reason"]}' for g in gaps]
    schema = {'schema': 'codeyun.skilldep_dataset_schema/v1', 'material_id': MATERIAL_ID,
              'files': {TABLES[k][0]: {'record': TITLES[k], 'columns': {
                  c: FIELDS[k][c] for c in TABLES[k][2] if c != INTERNAL_COLUMN}} for k in TABLES}}
    provenance = {'material_id': MATERIAL_ID, 'publisher': '国家新闻出版署', 'title': smf.get('name'),
                  'source_url': SOURCE_URL, 'staging_manifest': staging_manifest.as_posix(),
                  'staging_manifest_sha256': manifest_sha256, 'tables': tables,
                  'parser': 'F:/CodeYun/materials/m2610skilldependency/tools/nppa_tables.py',
                  'dropped_columns': {INTERNAL_COLUMN: 'staging raw path; replaced by 公告URL + raw_pages below'},
                  'raw_pages': dict(sorted(raw_used.items())), 'gaps': gaps}
    files |= {README: _readme(tables, counts, gaps).encode(),
              'schema.json': (json.dumps(schema, ensure_ascii=False, indent=1) + '\n').encode(),
              'provenance.json': (json.dumps(provenance, ensure_ascii=False, indent=1) + '\n').encode()}
    scope = {'kind': 'partial' if gaps else 'full', 'rows_by_category': counts,
             'announcement_pages': len(raw_used), 'staging_items': smf.get('items'), 'gaps': len(gaps),
             'dates': {TABLES[k][0]: tables[k]['dates'] for k in TABLES}}
    return {'files': files, 'records': records, 'scope': scope, 'pending': pending,
            'status': 'partial' if gaps else 'finalized',
            'source': {'staging_manifest': staging_manifest.as_posix(), 'staging_manifest_sha256': manifest_sha256}}


def _readme(tables: dict[str, Any], counts: dict[str, int], gaps: list[dict]) -> str:
    name = lambda k: TABLES[k][0].removeprefix('data/')  # noqa: E731
    lines = ['# 国家新闻出版署 网络游戏审批信息', '',
             f'来源：国家新闻出版署官网“游戏审批结果”公告（{SOURCE_URL}），逐份公告正文表格解析，单元格内容不改写。',
             '每行“公告URL”即官方出处，可逐行核验。', '',
             '## 文件', '', '| 文件 | 行数 | 日期范围 | 日期依据 |', '|---|---|---|---|']
    for k in TABLES:
        d = tables[k]['dates']
        lines.append(f'| {name(k)} | {tables[k]["rows"]} | {d["first"]} 至 {d["last"]} | {"、".join(d["columns"])} |')
    lines += ['', '游戏审批信息按类别：' + '，'.join(f'{c.removesuffix("审批信息")} {n}' for c, n in counts.items()
                                                   if c.endswith('游戏审批信息')) + '。', '',
              'CSV 为 UTF-8（带 BOM），可直接用 Excel 打开；空单元格表示公告原表该格为空或该期公告没有此列。', '']
    for k in TABLES:
        lines += [f'## {name(k)} 字段', '']
        lines += [f'- {c}：{FIELDS[k][c]}' for c in TABLES[k][2] if c != INTERNAL_COLUMN]
        lines.append('')
    lines += ['## 已知缺口', '']
    for g in gaps:
        lines.append('- 2022 年 8 月国产网络游戏审批公告页面采集时只有表头、没有数据行，该月数据可能缺失，'
                     '待官方页面正常或同源补齐后更新；本数据集因此为部分完成。' if '202208' in g['url']
                     else f'- {g["url"]}：{g["reason"]}')
    if not gaps:
        lines.append('- 无')
    lines.append('')
    return '\n'.join(lines)


def finalize(item: dict[str, Any], naming: dict[str, Any], *, root: Path = layout.FINAL_LOCAL_ROOT,
             dry_run: bool = False) -> dict[str, Any]:
    """Finalize from the catalog item (``local.manifest_path``/``manifest_sha256`` of the staging acquisition)."""
    local = item['local']
    if local.get('status') not in ('downloaded', 'partial'):
        raise ValueError('material has no verified staging acquisition')
    built = build(Path(local['manifest_path']), local['manifest_sha256'])
    where = layout.resolve(item['id'], naming, root)
    built['source'] |= layout_source(where)
    if dry_run:
        return {'status': 'dry_run', 'path': str(where['local_dir']), 'package_status': built['status'],
                'records': built['records'], 'scope': built['scope'], 'pending': built['pending'],
                'files': {p: len(b) for p, b in built['files'].items()}}
    return write_package(where['local_dir'], item['id'], built['files'], records=built['records'],
                         scope=built['scope'], status=built['status'], source=built['source'],
                         pending=built['pending'])


def main(argv: list[str] | None = None) -> int:
    from .catalog import CatalogStore
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--root', default=str(layout.FINAL_LOCAL_ROOT))
    p.add_argument('--naming', default=str(layout.DEFAULT_NAMING), help='cached CodeHub naming snapshot')
    p.add_argument('--dry-run', action='store_true', help='check and report without writing the package')
    a = p.parse_args(argv)
    item = CatalogStore().load()['items'][MATERIAL_ID]
    result = finalize(item, layout.load_naming(a.naming), root=Path(a.root), dry_run=a.dry_run)
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result['status'] in ('verified', 'unchanged', 'dry_run') else 2


if __name__ == '__main__':
    raise SystemExit(main())
