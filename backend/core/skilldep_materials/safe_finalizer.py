"""Finalize SAFE 人民币汇率中间价 (sd-ffb089ebc29a) into a cleaned package.

Input is the verified staging manifest recorded in the catalog: the official
1994-2025 history workbook (xlsx) and the 2026 official export (xls), 25
currency columns per publication date.  Every row of both parts is parsed.

Cleaning rules (nothing invented):

* Values are kept exactly as published (decimal text) plus a numeric copy;
  no unit or quote-direction conversion — the source files state none.
* ``-`` means "no fixing published for this currency on that date" and becomes
  ``null`` with ``value_status=not_published``; empty cells (the source's own
  1999-02-01..03 泰铢 gap) become ``null`` with ``value_status=blank_in_source``.
* Dates are ISO ``YYYY-MM-DD``; rows sorted ascending; the two parts must not
  overlap with different values (equal overlaps are deduplicated and counted).

Outputs: ``data/rates_long.jsonl`` (one row per date x currency, with source
part), ``data/rates_wide.csv`` (one row per date, source column order),
``originals/`` (both official files), schema/provenance/manifest.

CLI::

    uv run python -m backend.core.skilldep_materials.safe_finalizer [--root F:/data/m2610skilldep]
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from . import storage_layout as layout
from .finalize import layout_source, copy_original, csv_bytes, jsonl_bytes, sha256_file, write_package

MATERIAL_ID = 'sd-ffb089ebc29a'
PARTS = ('history_1994_2025', 'export_2026')
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _rows_xlsx(data: bytes) -> list[list[Any]]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
    if len(wb.worksheets) != 1:
        raise ValueError('expected one sheet')
    return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]


def _rows_xls(data: bytes) -> list[list[Any]]:
    import xlrd
    book = xlrd.open_workbook(file_contents=data)
    if book.nsheets != 1:
        raise ValueError('expected one sheet')
    s = book.sheet_by_index(0)
    return [s.row_values(i) for i in range(s.nrows)]


def _cell(raw: Any) -> tuple[str | None, str | None, str]:
    """``(value_text, numeric_text, value_status)`` of one published cell."""
    text = '' if raw is None else str(raw).strip()
    if text == '':
        return None, None, 'blank_in_source'
    if text == '-':
        return None, None, 'not_published'
    try:
        num = Decimal(text)
    except InvalidOperation:
        raise ValueError(f'non-numeric cell {text!r}') from None
    if not num.is_finite() or num <= 0:
        raise ValueError(f'invalid rate {text!r}')
    return text, format(num.normalize(), 'f'), 'published'


def parse_part(rows: list[list[Any]], part: str) -> tuple[list[str], dict[str, list[tuple]]]:
    """Header currencies and ``{date: [(value_text, numeric, status), ...]}``."""
    header = [str(h).strip() for h in rows[0]]
    if header[0] != '日期' or len(header) < 2 or len(set(header)) != len(header):
        raise ValueError(f'{part}: unexpected header')
    by_date: dict[str, list[tuple]] = {}
    for i, row in enumerate(rows[1:], 2):
        if all(v in (None, '') for v in row):
            continue
        d = str(row[0]).strip()
        if not _DATE.match(d):
            raise ValueError(f'{part} row {i}: bad date {d!r}')
        date.fromisoformat(d)
        if d in by_date:
            raise ValueError(f'{part}: duplicate date {d}')
        cells = list(row[1:]) + [None] * (len(header) - len(row))
        by_date[d] = [_cell(c) for c in cells[:len(header) - 1]]
    return header[1:], by_date


def build(staging_manifest: Path, manifest_sha256: str) -> dict[str, Any]:
    """Parse all verified parts; return files/records/scope/source for the package."""
    if sha256_file(staging_manifest) != manifest_sha256:
        raise ValueError('staging manifest SHA-256 mismatch')
    smf = json.loads(staging_manifest.read_text(encoding='utf-8'))
    if not smf.get('complete'):
        raise ValueError('staging manifest not complete')
    base = staging_manifest.parent.parent
    entries = {f['key']: f for f in smf['files']}
    currencies, merged, origin, originals, dup_equal = None, {}, {}, {}, 0
    part_stats = {}
    for key in PARTS:
        f = entries[key]
        data = copy_original(base / f['path'], f['sha256'])
        rows = _rows_xlsx(data) if f['format'] == 'xlsx' else _rows_xls(data)
        cur, by_date = parse_part(rows, key)
        if currencies is None:
            currencies = cur
        elif cur != currencies:
            raise ValueError(f'{key}: currency columns differ')
        for d, vals in by_date.items():
            if d in merged:
                if merged[d] != vals:
                    raise ValueError(f'{d}: parts disagree')
                dup_equal += 1
                continue
            merged[d], origin[d] = vals, key
        part_stats[key] = {'dates': len(by_date), 'first': min(by_date), 'last': max(by_date),
                           'sha256': f['sha256'], 'source_url': f['url'], 'label': f['label'],
                           'fetched_at': f['fetched_at']}
        originals[f'originals/{key}.{f["format"]}'] = data

    dates = sorted(merged)
    long_rows, wide_rows, status_count = [], [], {}
    for d in dates:
        vals = merged[d]
        wide_rows.append([d] + ['-' if v[2] == 'not_published' else v[0] for v in vals])
        for cur, (text, num, st) in zip(currencies, vals):
            status_count[st] = status_count.get(st, 0) + 1
            long_rows.append({'date': d, 'currency': cur, 'value_text': text, 'value': num,
                              'value_status': st, 'source_part': origin[d]})
    blanks = [[r['date'], r['currency']] for r in long_rows if r['value_status'] == 'blank_in_source']

    schema = {
        'schema': 'codeyun.skilldep_dataset_schema/v1', 'material_id': MATERIAL_ID,
        'files': {
            'data/rates_long.jsonl': {'record': 'one publication date x currency', 'fields': {
                'date': 'ISO date of the official fixing',
                'currency': 'currency column name exactly as in the official file (Chinese)',
                'value_text': 'published value text, unmodified; null when not published/blank',
                'value': 'same value as a normalized decimal string; no unit/quote conversion',
                'value_status': 'published | not_published (source "-") | blank_in_source (empty cell)',
                'source_part': f'one of {list(PARTS)}'}},
            'data/rates_wide.csv': {'record': 'one publication date',
                                    'columns': ['日期', *currencies],
                                    'note': 'published value text; "-" = not published (as in source), empty = blank in source'}},
        'units': 'not stated in the source files; values kept as published, not converted',
    }
    scope = {'kind': 'full', 'dates': len(dates), 'first': dates[0], 'last': dates[-1],
             'currencies': len(currencies), 'cells': len(long_rows), 'value_status': status_count,
             'overlap_equal_dates_deduplicated': dup_equal, 'parts': part_stats,
             'staging_coverage': 'all rows of every verified data part parsed; html pages used only for '
                                 'staging cross-check, not as data'}
    provenance = {
        'material_id': MATERIAL_ID, 'publisher': '国家外汇管理局', 'title': '人民币汇率中间价',
        'catalog_url': 'https://www.safe.gov.cn/safe/rmbhlzjj/index.html',
        'staging_manifest': str(staging_manifest).replace('\\', '/'), 'staging_manifest_sha256': manifest_sha256,
        'parts': part_stats, 'as_of_note': 'last fixing published before capture; see staging coverage.json',
        'blank_cells_in_source': blanks,
        'cleaning': ['dates ISO, sorted ascending', '"-" -> null not_published', 'empty -> null blank_in_source',
                     'no unit or quote-direction conversion', 'part overlaps must agree; equal ones deduplicated'],
    }
    files = {
        'data/rates_long.jsonl': jsonl_bytes(long_rows),
        'data/rates_wide.csv': csv_bytes(['日期', *currencies], wide_rows),
        'schema.json': (json.dumps(schema, ensure_ascii=False, indent=1) + '\n').encode(),
        'provenance.json': (json.dumps(provenance, ensure_ascii=False, indent=1) + '\n').encode(),
        **originals,
    }
    return {'files': files, 'records': {'data/rates_long.jsonl': len(long_rows), 'data/rates_wide.csv': len(dates)},
            'scope': scope,
            'source': {'staging_manifest': provenance['staging_manifest'], 'staging_manifest_sha256': manifest_sha256}}


def finalize(item: dict[str, Any], naming: dict[str, Any], *,
             root: Path = layout.FINAL_LOCAL_ROOT) -> dict[str, Any]:
    """Finalize from a catalog item (needs ``local.manifest_path``/``manifest_sha256``)."""
    local = item['local']
    if local.get('status') != 'downloaded':
        raise ValueError('material not downloaded')
    built = build(Path(local['manifest_path']), local['manifest_sha256'])
    where = layout.resolve(item['id'], naming, root)
    target = where['local_dir']
    built['source'] |= layout_source(where)
    return write_package(target, item['id'], built['files'], records=built['records'], scope=built['scope'],
                         status='finalized', source=built['source'])


def main(argv: list[str] | None = None) -> int:
    from .catalog import CatalogStore
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--root', default=str(layout.FINAL_LOCAL_ROOT))
    p.add_argument('--naming', default=str(layout.DEFAULT_NAMING), help='cached CodeHub naming snapshot')
    a = p.parse_args(argv)
    item = CatalogStore().load()['items'][MATERIAL_ID]
    result = finalize(item, layout.load_naming(a.naming), root=Path(a.root))
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result['status'] in ('verified', 'unchanged') else 2


if __name__ == '__main__':
    raise SystemExit(main())
