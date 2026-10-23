"""Finalize 国家新闻出版署 网络游戏审批信息 (sd-b05c6a963661) from the saved official pages.

Input is the verified staging manifest recorded in the catalog: 222 official
announcement pages under ``raw/`` (``.html`` or ``.html.gz``), their ``meta/``
fetch records and the 15 ``lists/`` index pages, SHA-256 each.  Every page is
parsed here offline (no network, no JavaScript executed); staging is only read.

Each announcement body is one ``div.m3pageEdit`` table.  On 国产/进口网络游戏
pages the “申报类别” cell is not a ``<td>`` but one inline script of a single
known template (``var _sblb = '…'; '原' -> &nbsp; else the value``).  The script
is matched against that exact template and rendered by its semantics; any other
script fails the build.  Comments inside the table (commented-out columns on
进口电子游戏 pages) are not rendered and are kept separately.

Package (``storage_layout`` typed dir)::

    data/游戏审批信息.csv        国产网络游戏 / 进口网络游戏 / 进口电子游戏 审批，一行一款
    data/游戏审批变更信息.csv    审批变更
    data/游戏审批撤销信息.csv    审批撤销
    data/公告清单.csv            每份公告：类别、标题、日期、URL、原表头、行数、状态、原件哈希
    data/原表单元格.jsonl        逐行逐格原文：列名、值、来源（td/script）、原页行列位置、脚本原值
    data/使用说明.md
    originals/raw/…, originals/lists/…   官方页面原件（字节不变）
    schema.json / provenance.json / manifest.json

The package is ``partial`` while the staging manifest records gaps (the
2022-08 国产网络游戏 page was header-only when saved).

CLI::

    uv run python -m backend.core.skilldep_materials.nppa_finalizer [--dry-run] [--register]
"""
from __future__ import annotations

import gzip
import html
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

MATERIAL_ID = 'sd-b05c6a963661'
SOURCE_URL = 'https://www.nppa.gov.cn/bsfw/jggs/yxspjg/'
# The only script allowed inside a table row; whitespace-normalized, ``{v}`` is the JS string body.
_SBLB_TEMPLATE = ("var _sblb = '{v}'; if (_sblb == '原') { document.write(\"<td align='center'>&nbsp;</td>\"); } "
                  "else { document.write(\"<td align='center'>\" + _sblb + \"</td>\"); }")
_SBLB_VALUE = re.compile(r"var _sblb = '((?:[^'\\]|\\.)*)';")
_HTML_SPACE = re.compile(r'[ \t\r\n\f]+')


def read_page(path: Path) -> str:
    data = Path(path).read_bytes()
    if path.name.endswith('.gz'):
        data = gzip.decompress(data)
    return data.decode('utf-8')


def render_sblb(script: str) -> tuple[str, str]:
    """``(cell text, raw JS value)`` of one 申报类别 script; raises on any other script.

    The value is taken verbatim from the source (inner whitespace and line breaks kept).  A line break
    inside the JS string is a syntax error for a browser, so such a cell is not actually displayed;
    callers flag it via :func:`script_breaks`."""
    m = _SBLB_VALUE.search(script)
    norm = ' '.join(script.split())
    if not m or ' '.join((script[:m.start(1)] + '{v}' + script[m.end(1):]).split()) != _SBLB_TEMPLATE:
        raise ValueError(f'unknown inline script in table: {norm[:200]!r}')
    raw = m.group(1)
    if '\\' in raw or '<' in raw:
        raise ValueError(f'script value needs JS/HTML evaluation: {raw!r}')
    return ('' if raw == '原' else html.unescape(raw)), raw


def script_breaks(raw: str) -> bool:
    """True when the JS string literal contains a line break (browser would not run the script)."""
    return any(c in raw for c in '\n\r\u2028\u2029')


class _TableParser(HTMLParser):
    """Rows of the single ``div.m3pageEdit`` table with each cell's source and position."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.div_depth = 0          # >0 while inside div.m3pageEdit
        self.bodies = 0
        self.tables = 0
        self.rows: list[dict[str, Any]] = []
        self.comments: list[dict[str, Any]] = []
        self.other_tags: list[str] = []
        self._cell: dict[str, Any] | None = None
        self._script: dict[str, Any] | None = None

    def handle_starttag(self, tag, attrs):
        if self.div_depth:
            if tag == 'div':
                self.div_depth += 1
            elif tag == 'table':
                self.tables += 1
            elif tag == 'tr':
                self.rows.append({'pos': '%d:%d' % self.getpos(), 'class': dict(attrs).get('class'), 'cells': []})
            elif tag == 'td':
                if self._cell is not None or not self.rows:
                    raise ValueError(f'nested/orphan td at {self.getpos()}')
                self._cell = {'source': 'td', 'pos': '%d:%d' % self.getpos(), 'text': ''}
            elif tag == 'script':
                self._script = {'pos': '%d:%d' % self.getpos(), 'text': ''}
            elif tag not in ('tbody',):
                self.other_tags.append(tag)
        elif tag == 'div' and 'm3pageEdit' in (dict(attrs).get('class') or '').split():
            self.div_depth = 1
            self.bodies += 1

    def handle_endtag(self, tag):
        if not self.div_depth:
            return
        if tag == 'div':
            self.div_depth -= 1
        elif tag == 'td' and self._cell is not None:
            self.rows[-1]['cells'].append(self._cell)
            self._cell = None
        elif tag == 'script' and self._script is not None:
            text, raw = render_sblb(self._script['text'])
            self.rows[-1]['cells'].append({'source': 'script', 'pos': self._script['pos'], 'text': text,
                                           'script_value': raw, 'js_line_break': script_breaks(raw)})
            self._script = None

    def handle_data(self, data):
        if self._script is not None:
            self._script['text'] += data
        elif self._cell is not None:
            self._cell['text'] += data
        elif self.div_depth and data.strip():
            raise ValueError(f'text outside cells at {self.getpos()}: {data.strip()[:80]!r}')

    def handle_comment(self, data):
        if self.div_depth:
            self.comments.append({'pos': '%d:%d' % self.getpos(), 'text': data})


def clean(text: str) -> str:
    """Cell value as a browser displays it: HTML whitespace runs collapse to one space, ends (incl. &nbsp;) trimmed.

    The verbatim source text stays in ``原表单元格.jsonl`` (``原文``) whenever it differs."""
    return _HTML_SPACE.sub(' ', text).strip(' \xa0　')


def parse_page(text: str) -> dict[str, Any]:
    """``{header, rows: [{row, pos, cells}], comments}`` of one announcement page."""
    p = _TableParser()
    p.feed(text)
    p.close()
    if p.bodies != 1 or p.tables != 1 or not p.rows:
        raise ValueError(f'expected one body table, got bodies={p.bodies} tables={p.tables} rows={len(p.rows)}')
    if p.other_tags:
        raise ValueError(f'unexpected tags in body: {sorted(set(p.other_tags))}')
    head, *data = p.rows
    if head['class'] == 'item' or any(r['class'] != 'item' for r in data):
        raise ValueError('header/data row classes differ from the known layout')
    if any(c['source'] != 'td' for c in head['cells']):
        raise ValueError('script in header row')
    rows = [{'row': i, 'pos': r['pos'], 'cells': r['cells']} for i, r in enumerate(data, 1)]
    return {'header': [clean(c['text']) for c in head['cells']], 'header_pos': head['pos'], 'rows': rows,
            'comments': p.comments}


# ---- package build ---------------------------------------------------------------------------------------------

import json  # noqa: E402
from datetime import date  # noqa: E402

from . import storage_layout as layout  # noqa: E402
from .finalize import (csv_bytes, jsonl_bytes, layout_source, rewrite_package, sha256_bytes, sha256_file,  # noqa: E402
                       verify_package, write_package)

README = 'data/使用说明.md'
INDEX = 'data/公告清单.csv'
CELLS = 'data/原表单元格.jsonl'
COMMENTS = 'data/表内注释.csv'
# URL segment -> (category label, table key, header renames to the unified columns)
CATEGORIES = {
    'gcwlyxspxx': ('国产网络游戏', 'approvals', {}),
    'jkwlyxspxx': ('进口网络游戏', 'approvals', {}),
    'jkdzyxspxx': ('进口电子游戏', 'approvals', {'文号': '批复文号', '时间': '批准时间'}),
    'yxspbgxx': ('游戏审批变更信息', 'changes', {}),
    'yxspcxxx': ('游戏审批撤销信息', 'revocations', {}),
}
TAIL = ['公告标题', '公告日期', '公告URL', '公告键', '表内行号']
TABLES = {  # key -> (public file, columns, date columns, derived iso column)
    'approvals': ('data/游戏审批信息.csv', ['类别', '序号', '名称', '申报类别', '出版单位', '运营单位', '批复文号',
                                            '出版物号', '批准时间', '批准日期'] + TAIL, ('批准时间',), ('批准时间', '批准日期')),
    'changes': ('data/游戏审批变更信息.csv', ['序号', '名称', '申报类别', '出版单位', '运营单位', '变更信息', '文号',
                                              '变更时间', '批复文号', '批准变更时间'] + TAIL, ('变更时间', '批准变更时间'), None),
    'revocations': ('data/游戏审批撤销信息.csv', ['序号', '名称', '申报类别', '出版单位', '运营单位', '撤销信息', '文号',
                                                  '出版物号', '批准时间'] + TAIL, ('批准时间',), None),
}
TITLES = {'approvals': '游戏审批信息', 'changes': '游戏审批变更信息', 'revocations': '游戏审批撤销信息'}
INDEX_COLUMNS = ['公告键', '类别', '公告标题', '公告日期', '公告URL', '原表头', '数据行数', '状态', '申报类别脚本格数',
                 '表内注释数', '原件', '原件字节', '原件SHA256', '解压后SHA256', '抓取时间']
COMMENT_COLUMNS = ['公告键', '原页位置', '注释原文']
COMMON = {
    '序号': '该行在所属公告表格中的序号原文；只在同一公告内唯一，跨公告会重复',
    '名称': '游戏名称',
    '申报类别': '移动 / 客户端 / 网页 / 游戏机 等，公告原文；国产/进口网络游戏公告中此格由页面内联脚本输出，'
                '已按脚本固定模板还原（脚本值“原”显示为空格，即空），脚本原值见 原表单元格.jsonl',
    '出版单位': '出版单位',
    '运营单位': '运营单位',
    '出版物号': 'ISBN 出版物号；公告未列时为空',
    '公告标题': '该行所在官方公告的标题（列表页标题）',
    '公告日期': '官方公告列表日期（YYYY-MM-DD）；按年累计更新的公告页中，批准/变更时间可能晚于此日期',
    '公告URL': '官方公告网页地址，可据此逐行核验',
    '公告键': '公告页面键（URL 文件名），对应 公告清单.csv 与 originals/raw/ 原件',
    '表内行号': '该行在公告表格数据行中的位置（从 1 起，不含表头），对应 原表单元格.jsonl',
}
FIELDS = {
    'approvals': COMMON | {
        '类别': '国产网络游戏 / 进口网络游戏 / 进口电子游戏',
        '申报类别': COMMON['申报类别'] + '；进口电子游戏公告无此列，为空',
        '运营单位': '运营单位；进口电子游戏公告无此列，为空',
        '出版物号': 'ISBN 出版物号；进口电子游戏公告无此列，为空',
        '批复文号': '审批文号（进口电子游戏原表头为“文号”）',
        '批准时间': '公告原文的批准时间，如“2024年01月25日”（进口电子游戏原表头为“时间”）',
        '批准日期': '批准时间对应的日期（YYYY-MM-DD）；原文无法按“YYYY年M月D日”解析时为空，原文仍在批准时间',
    },
    'changes': COMMON | {
        '变更信息': '变更内容原文，如原名、原出版/运营单位、原批准文号与原批准时间',
        '文号': '2014—2022 年公告的“文号”列（2023 年起为空）：2014—2019 年多为本次变更的批复文号'
                '（原批准文号写在“变更信息”中），2020—2022 年多为游戏原有的批复文号',
        '变更时间': '2014—2022 年公告的变更批准时间原文（2023 年起为空）',
        '批复文号': '2023 年起公告的“批复文号”列（此前为空），为游戏原有的批复文号，不是变更文号',
        '批准变更时间': '2023 年起公告的变更批准时间原文（此前为空）',
    },
    'revocations': COMMON | {
        '撤销信息': '撤销原因与撤销日期原文',
        '文号': '被撤销游戏原有的批复文号',
        '批准时间': '公告原表头即“批准时间”；是撤销的批准时间，不是游戏原批准时间',
    },
}
_CN_DATE = re.compile(r'(\d{4})年(\d{1,2})月(\d{1,2})日')


def _iso(text: str) -> str | None:
    m = _CN_DATE.fullmatch(text.strip())
    try:
        return date(*map(int, m.groups())).isoformat() if m else None
    except ValueError:
        return None


def _verified_files(base: Path, smf: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every manifest-listed staging file, checked for size and SHA-256 now."""
    listed = {}
    for f in smf['files']:
        path = base / f['path']
        if not path.is_file() or path.stat().st_size != f['size'] or sha256_file(path) != f['sha256']:
            raise ValueError(f'staging file differs from manifest: {f["path"]}')
        listed[f['path']] = f
    return listed


def _list_entries(base: Path, listed: dict[str, dict[str, Any]]) -> set[str]:
    """Announcement URLs linked from the saved list pages (cross-check of the page set)."""
    urls = set()
    for rel in sorted(p for p in listed if p.startswith('lists/')):
        text = (base / rel).read_bytes().decode('utf-8')
        for href in re.findall(r'href="(\./[^"]+?/t\d{8}_\d+\.html)"', text):
            urls.add(SOURCE_URL + href[2:])
    return urls


def build(staging_manifest: Path, manifest_sha256: str, supplement_dir: Path | None = None) -> dict[str, Any]:
    """Parse every saved announcement page; return package files/records/scope/source/pending.

    ``supplement_dir`` (``sources.json`` + files) holds the one-off 2022-08 re-fetch and a reprint of the
    list as first published; see :func:`_supplement`."""
    staging_manifest = Path(staging_manifest)
    if sha256_file(staging_manifest) != manifest_sha256:
        raise ValueError('staging manifest SHA-256 mismatch')
    smf = json.loads(staging_manifest.read_text(encoding='utf-8'))
    if smf.get('material_id') != MATERIAL_ID:
        raise ValueError('staging manifest is for another material')
    base = staging_manifest.parent.parent
    listed = _verified_files(base, smf)
    gaps = {g['key']: g for g in smf.get('gaps') or []}
    linked = _list_entries(base, listed)
    tables: dict[str, list[list[str]]] = {k: [] for k in TABLES}
    index, cells_out, comments_out, originals, raw_pages = [], [], [], {}, {}
    script_cells, line_breaks = 0, []
    for meta_rel in sorted(p for p in listed if p.startswith('meta/')):
        meta = json.loads((base / meta_rel).read_bytes())
        key, url = meta['key'], meta['url']
        raw_rel = Path(meta['raw']['path']).relative_to(base).as_posix()
        entry = listed.get(raw_rel)
        if entry is None or entry['sha256'] != meta['raw']['sha256'] or meta['final_url'] != url:
            raise ValueError(f'{key}: meta does not bind a manifest raw page')
        if url not in linked:
            raise ValueError(f'{key}: page not linked from the saved list pages')
        raw_bytes = (base / raw_rel).read_bytes()
        text = read_page(base / raw_rel)
        decoded_sha = sha256_bytes(text.encode('utf-8'))
        if meta['raw'].get('encoding') == 'gzip' and decoded_sha != meta['raw']['original_sha256']:
            raise ValueError(f'{key}: decompressed page differs from recorded original')
        label, table, renames = CATEGORIES[url.removeprefix(SOURCE_URL).split('/')[0]]
        page = parse_page(text)
        columns = [renames.get(h, h) for h in page['header']]
        out_cols = TABLES[table][1]
        if len(set(columns)) != len(columns) or not set(columns) <= set(out_cols):
            raise ValueError(f'{key}: header {page["header"]} does not map to {table}')
        if not page['rows'] and key not in gaps:
            raise ValueError(f'{key}: header-only page not recorded as a gap')
        if page['rows'] and key in gaps:
            raise ValueError(f'{key}: recorded gap page has rows')
        page_scripts = 0
        for r in page['rows']:
            if len(r['cells']) != len(columns):
                raise ValueError(f'{key} row {r["row"]}: {len(r["cells"])} cells for {len(columns)} columns')
            values = dict.fromkeys(out_cols, '')
            record_cells = []
            for col, header, c in zip(columns, page['header'], r['cells']):
                values[col] = clean(c['text'])
                cell = {'列': header, '值': values[col], '来源': c['source'], '位置': c['pos']}
                if c['text'] != values[col]:
                    cell['原文'] = c['text']
                if c['source'] == 'script':
                    cell['脚本值'] = c['script_value']
                    page_scripts += 1
                    if c['js_line_break']:
                        cell['脚本字面量含换行'] = True
                        line_breaks.append(f'{key}#{r["row"]}')
                record_cells.append(cell)
            if table == 'approvals':
                values['类别'] = label
                values['批准日期'] = _iso(values['批准时间']) or ''
            values |= {'公告标题': meta['list_title'], '公告日期': meta['list_date'], '公告URL': url,
                       '公告键': key, '表内行号': str(r['row'])}
            tables[table].append([values[c] for c in out_cols])
            cells_out.append({'公告键': key, '表内行号': r['row'], '类别': label, '位置': r['pos'],
                              '单元格': record_cells})
        script_cells += page_scripts
        comments_out += [[key, c['pos'], c['text']] for c in page['comments']]
        original = f'originals/{raw_rel}'
        originals[original] = raw_bytes
        index.append([key, label, meta['list_title'], meta['list_date'], url, ' | '.join(page['header']),
                      len(page['rows']), '仅表头（缺口）' if key in gaps else '有效', page_scripts,
                      len(page['comments']), original, len(raw_bytes), entry['sha256'], decoded_sha,
                      meta['fetched_at']])
        raw_pages[key] = {'url': url, 'category': label, 'staging_path': raw_rel, 'sha256': entry['sha256'],
                          'bytes': entry['size'], 'decoded_sha256': decoded_sha, 'meta': meta_rel,
                          'meta_sha256': listed[meta_rel]['sha256'], 'fetched_at': meta['fetched_at'],
                          'rows': len(page['rows']), 'script_cells': page_scripts,
                          'comments': len(page['comments'])}
    if set(raw_pages) != {Path(p).name.split('.')[0] for p in listed if p.startswith('raw/')}:
        raise ValueError('raw pages and meta records differ')
    if linked != {p['url'] for p in raw_pages.values()}:
        raise ValueError(f'list pages link {len(linked)} announcements, {len(raw_pages)} saved')
    if set(gaps) - set(raw_pages):
        raise ValueError('gap page not saved')
    for rel in sorted(p for p in listed if p.startswith('lists/')):
        originals[f'originals/{rel}'] = (base / rel).read_bytes()

    files: dict[str, bytes] = {}
    records, dates, counts = {}, {}, {}
    for k, (public, cols, date_cols, _) in TABLES.items():
        rows = tables[k]
        files[public] = csv_bytes(cols, rows)
        records[public] = len(rows)
        found = [_iso(r[cols.index(c)]) for r in rows for c in date_cols if r[cols.index(c)].strip()]
        dates[public] = {'columns': list(date_cols), 'first': min(filter(None, found), default=None),
                         'last': max(filter(None, found), default=None), 'unparsed': found.count(None)}
        for r in rows:
            label = r[0] + '审批信息' if k == 'approvals' else TITLES[k]
            counts[label] = counts.get(label, 0) + 1
    files[INDEX] = csv_bytes(INDEX_COLUMNS, index)
    files[CELLS] = jsonl_bytes(cells_out)
    files[COMMENTS] = csv_bytes(COMMENT_COLUMNS, comments_out)
    files |= originals
    resolution = None
    if supplement_dir is not None:
        extra, resolution = _supplement(Path(supplement_dir), tables['approvals'], gaps)
        files |= extra
        gaps = {k: g for k, g in gaps.items() if k not in resolution['resolved_gaps']}
        for row in index:
            if row[0] in resolution['resolved_gaps']:
                row[7] = '仅表头（官方页本身为空，2026-10-23 重抓核实；该批列在 7 月公告页）'
        files[INDEX] = csv_bytes(INDEX_COLUMNS, index)
    gap_list = [gaps[k] for k in sorted(gaps)]
    pending = [f'{g["url"]}: {g["reason"]}' for g in gap_list]
    scope = {'kind': 'partial' if gap_list else 'full', 'rows_by_category': counts,
             'rows_total': sum(records.values()), 'announcement_pages': len(raw_pages),
             'pages_with_rows': sum(1 for p in raw_pages.values() if p['rows']),
             'header_only_pages': sorted(k for k, p in raw_pages.items() if not p['rows']),
             'list_pages': sum(1 for p in listed if p.startswith('lists/')),
             'script_rendered_cells': script_cells, 'script_line_break_cells': line_breaks,
             'table_comments': len(comments_out),
             'staging_files_verified': len(listed), 'dates': dates, 'gaps': len(gap_list),
             'other_data_lines': {INDEX: len(index), CELLS: len(cells_out), COMMENTS: len(comments_out)},
             'gap_resolution': resolution and {k: v for k, v in resolution.items() if k != 'sources'}}
    provenance = {'material_id': MATERIAL_ID, 'publisher': '国家新闻出版署', 'title': smf.get('name'),
                  'source_url': SOURCE_URL, 'staging_manifest': staging_manifest.as_posix(),
                  'staging_manifest_sha256': manifest_sha256, 'staging_files_verified': len(listed),
                  'parser': 'backend/core/skilldep_materials/nppa_finalizer.py (offline; inline scripts not executed, '
                            'matched to the fixed 申报类别 template and rendered by its semantics)',
                  'script_template': _SBLB_TEMPLATE, 'raw_pages': raw_pages, 'gaps': gap_list,
                  'gap_resolution': resolution}
    files |= {README: _readme(records, counts, dates, scope, gap_list).encode(),
              'schema.json': (json.dumps(_schema(), ensure_ascii=False, indent=1) + '\n').encode(),
              'provenance.json': (json.dumps(provenance, ensure_ascii=False, indent=1) + '\n').encode()}
    return {'files': files, 'records': records, 'scope': scope, 'pending': pending,
            'status': 'partial' if gap_list else 'finalized',
            'source': {'staging_manifest': staging_manifest.as_posix(), 'staging_manifest_sha256': manifest_sha256}}


GAP_KEY = 't20220801_442173'
JULY_KEY = 't20220712_442172'
SUPPLEMENT = 'data/转载补录.csv'
SUPPLEMENT_COLUMNS = ['类别', '序号', '名称', '申报类别', '出版单位', '运营单位', '批复文号', '出版物号', '批准时间',
                      '批准日期', '官方现页公告URL', '转载来源URL', '转载表内行号', '说明']
_REPRINT_FIELDS = ['序号', '名称', '申报类别', '出版单位', '运营单位', '批复文号', '出版物号', '批准时间']
_REPRINT_HEADER = ['序号', '名称', '申报类别', '出版单位', '运营单位', '文号', '出版物号', '时间']


class _AnyTable(HTMLParser):
    """All ``<tr>`` rows (``<td>`` texts) of every table in a page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.rows.append([])
        elif tag in ('td', 'th') and self.rows:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self._cell is not None:
            self.rows[-1].append(clean(''.join(self._cell)))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _supplement(directory: Path, approvals: list[list[str]], gaps: dict[str, Any]) -> tuple[dict[str, bytes], dict]:
    """Check the 2022-08 re-fetch and the first-published reprint; return extra files and the resolution record.

    The gap is resolved only when the official page is still header-only, the batch approved 2022-07-30 is on
    the official July page, and every official July-page row equals its reprint row field by field.  Rows of
    the reprint that the official page no longer lists go to ``转载补录.csv`` (not into the official tables)."""
    sources = json.loads((directory / 'sources.json').read_text(encoding='utf-8'))
    blobs = {}
    for name, meta in sources['files'].items():
        data = (directory / name).read_bytes()
        if sha256_bytes(data) != meta['sha256'] or len(data) != meta['bytes']:
            raise ValueError(f'supplement file changed: {name}')
        blobs[name] = data
    official_name = next(n for n, m in sources['files'].items() if m['url'].startswith(SOURCE_URL))
    reprint_name = next(n for n in sources['files'] if n != official_name)
    refetched = parse_page(blobs[official_name].decode('utf-8'))
    cols = TABLES['approvals'][1]
    july = {r[cols.index('批复文号')]: r for r in approvals if r[cols.index('公告键')] == JULY_KEY}
    late = [r for r in july.values() if r[cols.index('批准时间')] == '2022年07月30日']
    parser = _AnyTable()
    parser.feed(blobs[reprint_name].decode('utf-8'))
    # reprint header reads 序号 名称 申报类别 出版单位 运营单位 文号 出版物号 时间: same order as the official columns
    head = next(i for i, r in enumerate(parser.rows) if r == _REPRINT_HEADER)
    reprint = [r for r in parser.rows[head + 1:] if len(r) == len(_REPRINT_FIELDS)]
    by_no = {r[5]: (i, r) for i, r in enumerate(reprint, 1)}
    mismatches = []
    for no, row in july.items():
        hit = by_no.get(no)
        if hit is None:
            mismatches.append({'批复文号': no, 'problem': 'official row missing from reprint'})
            continue
        for f in _REPRINT_FIELDS[1:]:
            if hit[1][_REPRINT_FIELDS.index(f)] != row[cols.index(f)]:
                mismatches.append({'批复文号': no, 'field': f, 'official': row[cols.index(f)],
                                   'reprint': hit[1][_REPRINT_FIELDS.index(f)]})
    extra_rows = []
    url = sources['files'][reprint_name]['url']
    for i, r in enumerate(reprint, 1):
        if r[5] not in july:
            v = dict(zip(_REPRINT_FIELDS, r))
            extra_rows.append(['国产网络游戏', v['序号'], v['名称'], v['申报类别'], v['出版单位'], v['运营单位'], v['批复文号'],
                               v['出版物号'], v['批准时间'], _iso(v['批准时间']) or '',
                               SOURCE_URL + 'gcwlyxspxx/202207/t20220712_442172.html', url, str(i),
                               '首次公布时列于该公告，官方现页已不含此行；内容取自转载，未经官方现页核实'])
    # official values stay authoritative; field differences (later official edits) are recorded, not applied
    resolved = (GAP_KEY in gaps and not refetched['rows'] and late
                and not any('problem' in m for m in mismatches) and len(reprint) == len(july) + len(extra_rows))
    files = {f'originals/supplement/{n}': b for n, b in blobs.items()}
    files[f'originals/supplement/sources.json'] = (directory / 'sources.json').read_bytes()
    files[SUPPLEMENT] = csv_bytes(SUPPLEMENT_COLUMNS, extra_rows)
    return files, {'resolved_gaps': [GAP_KEY] if resolved else [],
                   'refetched_official_rows': len(refetched['rows']),
                   'official_july_page_rows': len(july), 'official_july_page_rows_approved_2022_07_30': len(late),
                   'reprint_rows': len(reprint), 'reprint_rows_matching_official': len(july) - len(
                       {m['批复文号'] for m in mismatches}), 'field_mismatches': mismatches,
                   'reprint_only_rows': len(extra_rows),
                   'reprint_only_approval_numbers': [r[6] for r in extra_rows], 'sources': sources}


def _schema() -> dict[str, Any]:
    files = {TABLES[k][0]: {'format': 'csv-utf8-bom', 'record': TITLES[k],
                            'columns': {c: FIELDS[k][c] for c in TABLES[k][1]}} for k in TABLES}
    files[INDEX] = {'format': 'csv-utf8-bom', 'record': '公告页面', 'columns': {
        '公告键': '页面键（URL 文件名）', '类别': '五类公告之一', '公告标题': '列表页标题', '公告日期': '列表日期',
        '公告URL': '官方网址', '原表头': '公告表格表头原文（| 分隔）', '数据行数': '表格数据行数（不含表头）',
        '状态': '有效 / 仅表头（缺口）', '申报类别脚本格数': '由内联脚本输出的单元格数',
        '表内注释数': '表格内 HTML 注释数（不显示）', '原件': '包内原件路径', '原件字节': '原件字节数',
        '原件SHA256': '原件 SHA-256（.gz 为压缩字节）', '解压后SHA256': '页面 HTML 解码前字节的 SHA-256（.gz 为解压后）',
        '抓取时间': '采集时间（UTC）'}}
    files[CELLS] = {'format': 'jsonl', 'record': '公告表格的一行', 'fields': {
        '公告键': '页面键', '表内行号': '数据行序（从 1 起）', '类别': '公告类别', '位置': '该行 <tr> 在原页 HTML 的 行:列',
        '单元格': '按原表顺序：列（原表头）、值（显示值：HTML 空白合并为一个空格并去首尾空白，与 CSV 一致）、来源（td / script）、位置（原页 行:列）、'
                  '原文（未去首尾空白的文本，仅与值不同时给出）、脚本值（script 来源的 _sblb 原值）、'
                  '脚本字面量含换行（true 表示该 JS 字符串含换行，浏览器不执行，原页该格实际不显示）'}}
    files[SUPPLEMENT] = {'format': 'csv-utf8-bom', 'record': '首次公布名单中有、官方现页已不列的条目（来自转载）',
                         'columns': dict.fromkeys(SUPPLEMENT_COLUMNS, '同 游戏审批信息.csv 同名列；来源列见说明')}
    files[COMMENTS] = {'format': 'csv-utf8-bom', 'record': '表格内 HTML 注释（页面不显示）',
                       'columns': {'公告键': '页面键', '原页位置': '原页 行:列', '注释原文': '注释内容原文'}}
    return {'schema': 'codeyun.skilldep_dataset_schema/v1', 'material_id': MATERIAL_ID, 'files': files,
            'originals': 'originals/raw/<公告键>.html[.gz] 官方公告页原件；originals/lists/ 列表页原件；字节与采集时一致'}


def _readme(records: dict[str, int], counts: dict[str, int], dates: dict[str, Any], scope: dict[str, Any],
            gaps: list[dict]) -> str:
    lines = ['# 国家新闻出版署 网络游戏审批信息', '',
             f'来源：国家新闻出版署官网“游戏审批结果”公告（{SOURCE_URL}），已保存的 {scope["announcement_pages"]} 份公告页'
             f'逐页离线解析，全部表格单元格保留，内容不改写。每行“公告URL”“公告键”“表内行号”可回到原页与原件逐行核验。', '',
             '## 文件', '', '| 文件 | 行数 | 日期范围 | 日期依据 |', '|---|---|---|---|']
    for k, (public, *_rest) in TABLES.items():
        d = dates[public]
        lines.append(f'| {public.removeprefix("data/")} | {records[public]} | {d["first"]} 至 {d["last"]} '
                     f'| {"、".join(d["columns"])} |')
    lines += ['', '游戏审批信息按类别：' + '，'.join(f'{c.removesuffix("审批信息")} {n}' for c, n in counts.items()
                                                   if c.endswith('游戏审批信息')) + '。', '',
              f'- 公告清单.csv：{scope["announcement_pages"]} 份公告逐份的类别、标题、日期、原表头、行数、状态与原件哈希。',
              f'- 原表单元格.jsonl：{scope["rows_total"]} 行逐格原文与原页位置；其中 {scope["script_rendered_cells"]} 个'
              '“申报类别”格由页面内联脚本输出，已按固定模板还原并保留脚本原值；其中 '
              f'{len(scope["script_line_break_cells"])} 格的脚本字符串内含换行（浏览器实际不执行、页面该格显示缺失），'
              '值取脚本字面量（换行按显示规则合并为空格），原值见“脚本值”，并标记“脚本字面量含换行”。',
              f'- 表内注释.csv：{scope["table_comments"]} 条表格内 HTML 注释（进口电子游戏页被注释掉的列），页面不显示，单独保留。',
              '- originals/：官方公告页与列表页原件（字节不变，部分为 .gz 压缩保存）。公告正文只有表格，无图片、附件或 PDF。', '',
              'CSV 为 UTF-8（带 BOM），可直接用 Excel 打开；单元格为浏览器显示文本（源码中的连续空白、换行合并为一个空格，逐字原文见 原表单元格.jsonl 的“原文”）；空单元格表示公告原表该格为空或该期公告没有此列。', '']
    for k, (public, cols, *_rest) in TABLES.items():
        lines += [f'## {public.removeprefix("data/")} 字段', '']
        lines += [f'- {c}：{FIELDS[k][c]}' for c in cols]
        lines.append('')
    res = scope.get('gap_resolution')
    if res:
        lines += ['## 2022 年 8 月公告说明', '',
                  f'- 官方“2022年8月份国产网络游戏审批信息”页（{GAP_KEY}）只有表头：采集时如此，'
                  f'2026-10-23 重新抓取官方页仍为 {res["refetched_official_rows"]} 行（原件见 originals/supplement/）。',
                  f'- 该批（批准时间 2022年07月30日）现列在官方 7 月公告页（{JULY_KEY}），共 '
                  f'{res["official_july_page_rows_approved_2022_07_30"]} 行，已在 游戏审批信息.csv 中；'
                  f'7 月公告页合计 {res["official_july_page_rows"]} 行。',
                  f'- 首次公布时的完整名单（新浪财经 2022-08-01 转载，{res["reprint_rows"]} 行）与官方现页逐字段比对：'
                  f'{res["reprint_rows_matching_official"]} 行完全一致；不一致 {len(res["field_mismatches"])} 处，'
                  '以官方现页为准（不一致明细见 provenance.json 的 gap_resolution.field_mismatches）'
                  + ''.join(f'：{m["批复文号"]} {m["field"]} 官方“{m["official"]}”/转载“{m["reprint"]}”'
                            for m in res['field_mismatches'] if 'field' in m) + '。',
                  f'- 官方现页已不再列出的 {res["reprint_only_rows"]} 款（批复文号 '
                  f'{"、".join(res["reprint_only_approval_numbers"])}）放在 转载补录.csv，来源标为转载，未混入官方表。', '']
    lines += ['## 已知缺口', '']
    for g in gaps:
        lines.append('- 2022 年 8 月国产网络游戏审批公告页面（' + g['url'] + '）保存时只有表头、没有数据行，该月数据可能缺失，'
                     '待官方页面正常或同源补齐后更新；本数据集因此为部分完成（partial）。' if '202208' in g['url']
                     else f'- {g["url"]}：{g["reason"]}')
    if not gaps:
        lines.append('- 无')
    lines.append('')
    return '\n'.join(lines)


def _target(item: dict[str, Any], naming: dict[str, Any], root: Path,
            supplement_dir: Path | None = None) -> tuple[Path, dict[str, Any]]:
    local = item['local']
    if local.get('status') not in ('downloaded', 'partial'):
        raise ValueError('material has no verified staging acquisition')
    built = build(Path(local['manifest_path']), local['manifest_sha256'], supplement_dir)
    where = layout.resolve(item['id'], naming, root)
    built['source'] |= layout_source(where)
    return Path(where['local_dir']), built


def finalize(item: dict[str, Any], naming: dict[str, Any], *, root: Path = layout.FINAL_LOCAL_ROOT,
             backup_dir: Path | None = None, dry_run: bool = False, supplement_dir: Path | None = None,
             target: Path | None = None) -> dict[str, Any]:
    """Write the package; an existing verified package is rewritten in place (``backup_dir`` required)."""
    where, built = _target(item, naming, root, supplement_dir)
    target = Path(target) if target else where
    kw = dict(records=built['records'], scope=built['scope'], status=built['status'], source=built['source'],
              pending=built['pending'])
    if dry_run:
        return {'status': 'dry_run', 'path': str(target), 'package_status': built['status'],
                'records': built['records'], 'scope': built['scope'], 'pending': built['pending'],
                'files': len(built['files']), 'bytes': sum(len(b) for b in built['files'].values())}
    if not target.exists():
        return write_package(target, item['id'], built['files'], **kw)
    if backup_dir is None:
        raise ValueError(f'{target} exists; pass backup_dir to rewrite it')
    return rewrite_package(target, built['files'], backup_dir=backup_dir, **kw,
                           reason='2022-08 gap checked by one authorized official re-fetch: page still header-only, '
                                  'batch of 2022-07-30 is on the official July page; first-published reprint '
                                  'compared field by field, reprint-only rows kept separately in 转载补录.csv')


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
    cats = '，'.join(f'{k} {v}' for k, v in s['rows_by_category'].items())
    note = (f"{s['kind']}: {s['announcement_pages']} 份已存公告页逐页离线解析，{s['rows_total']} 行全量表格（{cats}）；"
            f"{s['script_rendered_cells']} 个申报类别格由内联脚本模板还原（不执行脚本），{s['table_comments']} 条表内注释单列；"
            f"逐格原页位置、公告清单、{s['staging_files_verified']} 个采集文件哈希核验、页面原件随包。"
            + (f"2022-08 官方页重新抓取仍仅表头，该批（2022-07-30 批准）{s['gap_resolution']['official_july_page_rows_approved_2022_07_30']}"
               f" 行在官方 7 月页；首发转载 {s['gap_resolution']['reprint_rows']} 行逐字段比对，"
               f"{s['gap_resolution']['reprint_rows_matching_official']} 行一致、"
               f"{len(s['gap_resolution']['field_mismatches'])} 处以官方现页为准，官方现页已删的"
               f" {s['gap_resolution']['reprint_only_rows']} 款单列 转载补录.csv。缺口：无。"
               if s.get('gap_resolution') and s['kind'] == 'full' else
               f"缺口：{'、'.join(s['header_only_pages']) or '无'} 仅表头（2022-08 国产网络游戏），整体 partial。")
            +
            f"源 manifest sha256 {manifest['source']['staging_manifest_sha256']}；未上传百度网盘。")
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
    p.add_argument('--dry-run', action='store_true', help='check and report without writing the package')
    p.add_argument('--backup-dir', help='backup dir (outside the package) when rewriting an existing package')
    p.add_argument('--register', action='store_true', help='PUT the CodeHub local finalization and read it back')
    p.add_argument('--supplement-dir', help='dir with sources.json, the 2022-08 re-fetch and the reprint')
    p.add_argument('--target', help='write a candidate here instead of the layout dir (never registered)')
    a = p.parse_args(argv)
    item = CatalogStore().load()['items'][MATERIAL_ID]
    result = finalize(item, layout.load_naming(a.naming), root=Path(a.root), dry_run=a.dry_run,
                      backup_dir=Path(a.backup_dir) if a.backup_dir else None,
                      supplement_dir=Path(a.supplement_dir) if a.supplement_dir else None,
                      target=Path(a.target) if a.target else None)
    ok = result['status'] in ('verified', 'unchanged', 'rewritten', 'dry_run')
    if ok and a.register and result['status'] != 'dry_run' and not a.target:
        from .codehub import CodeHubSkillDep
        hub = CodeHubSkillDep()
        hub.verify_peer()
        result['codehub'] = register(hub, Path(result['path']), item['local'].get('device_id'))
        ok = result['codehub']['ack']
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
