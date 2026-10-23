"""Finalize openstd national-standard full texts (``codeyun.skilldep.standards-fulltext/1`` staging) into packages.

The openstd PDFs embed fonts without Unicode maps for Chinese (the text layer reads ``犌犅``/``/G21``), so
the text is recognized page by page through the library OCR API (``recognize_pdf_page``, PaddleOCR, cached
by PDF SHA-256).  Tables come from the PDF's own vector rulings (``page.find_tables``); each OCR line is put
in the cell that contains its centre, so cell boundaries are the publisher's, not guessed.  Digits and
Latin letters of the text layer are Unicode, so every page's numbers are cross-checked against OCR.

Package::

    data/全文.md              逐页正文与表格（按版面先后），页码标注
    data/段落.csv             每个非表格文本块：页、序、类型、文本、页面坐标（pt）、OCR 置信度
    data/条款.csv             按条款号（如 3.2.1、A.1）切分的条款：条款号、标题、正文、起止页
    data/表格单元格.csv        表序、页、行、列、文本、单元格坐标
    data/表格/表N-第P页.csv     每张表一份宽表（表头为首行）
    data/图片/                 PDF 内嵌图片（原字节）
    data/使用说明.md
    originals/                 标准 PDF、openstd 详情页、OCR 首末页核验、原文本层
    schema.json / provenance.json / manifest.json

CLI::

    uv run python -m backend.core.skilldep_materials.standards_pdf_finalizer sd-xxxx [--naming N] \
        [--target T | --backup-dir B --register]
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from . import storage_layout as layout
from .finalize import (csv_bytes, layout_source, rewrite_package, sha256_bytes, sha256_file, verify_package,
                       write_package)

PARA_COLUMNS = ['页', '序', '类型', '文本', 'x0', 'y0', 'x1', 'y1', 'OCR置信度', 'OCR行']
CELL_COLUMNS = ['表序', '页', '行', '列', '文本', 'x0', 'y0', 'x1', 'y1', 'OCR行']
CLAUSE_COLUMNS = ['条款号', '标题', '正文', '起始页', '结束页']
_CLAUSE = re.compile(r'^((?:[A-Z]\.)?\d+(?:\.\d+)*)\s*(.*)$')
_NUM = re.compile(r'\d+(?:\.\d+)?')
# running header (standard code) or bare page number, wherever the layout put it
_RUNNING = re.compile(r'(?:GB|GB/T|GB/Z)\s*[\d.]+\s*[—-]\s*\d{4}|GB|[IVXⅠ-Ⅻ]+|\d{1,3}')


def _join(parts: list[str]) -> str:
    out = ''
    for p in parts:
        if out and out[-1].isascii() and out[-1].isalnum() and p[:1].isascii() and p[:1].isalnum():
            out += ' '
        out += p
    return out


def _to_pt(line: dict[str, Any], m: list[float]) -> tuple[float, float, float, float]:
    a, b, c, d, e, f = m
    pts = [(x * a + y * c + e, x * b + y * d + f) for x, y in
           ((line['x'], line['y']), (line['x'] + line['w'], line['y'] + line['h']))]
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


def _numbers(text: str) -> list[str]:
    return _NUM.findall(unicodedata.normalize('NFKC', text))


def parse_pdf(pdf_path: Path, sha: str, ocr=None) -> dict[str, Any]:
    """Pages of OCR blocks and vector tables (cells filled with OCR lines), embedded images, number cross-check."""
    import pymupdf
    if ocr is None:
        from backend.core.library.pdf_ocr import recognize_pdf_page as ocr
    pages, tables, images, checks = [], [], {}, []
    with pymupdf.open(pdf_path) as doc:
        for pno, page in enumerate(doc, 1):
            r = ocr(pdf_path, content_hash=sha, page_number=pno)
            m = r['geometry']['pixel_to_page_matrix']
            lines = {ln['line_id']: ln | {'pt': _to_pt(ln, m)} for ln in r['lines']}
            used = set()
            for t in page.find_tables().tables:
                cells = []
                for ri, row in enumerate(t.rows, 1):
                    for ci, box in enumerate(row.cells, 1):
                        if box is None:  # spanned by a neighbouring cell
                            continue
                        inside = [ln for ln in lines.values() if ln['line_id'] not in used and
                                  box[0] <= (ln['pt'][0] + ln['pt'][2]) / 2 <= box[2] and
                                  box[1] <= (ln['pt'][1] + ln['pt'][3]) / 2 <= box[3]]
                        inside.sort(key=lambda ln: (round(ln['pt'][1] / 4), ln['pt'][0]))
                        used |= {ln['line_id'] for ln in inside}
                        cells.append({'row': ri, 'col': ci, 'text': _join([ln['text'] for ln in inside]),
                                      'box': [round(v, 1) for v in box], 'lines': [ln['line_id'] for ln in inside]})
                tables.append({'page': pno, 'bbox': [round(v, 1) for v in t.bbox], 'rows': t.row_count,
                               'cols': t.col_count, 'cells': cells, 'index': len(tables) + 1})
            blocks = []
            for b in r['layout']['blocks']:
                ids = [i for i in b['line_ids'] if i not in used]
                if not ids:
                    continue
                pts = [lines[i]['pt'] for i in ids]
                box = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[2] for p in pts), max(p[3] for p in pts))
                text = _join([lines[i]['text'] for i in ids])
                kind = ('页眉' if box[3] < page.rect.height * 0.08 or _RUNNING.fullmatch(text) and box[1] < page.rect.height / 2
                        else '页脚' if box[1] > page.rect.height * 0.92 or _RUNNING.fullmatch(text)
                        else '标题' if b['kind'] == 'heading' else '正文')
                blocks.append({'kind': kind, 'text': _join([lines[i]['text'] for i in ids]), 'box': box,
                               'score': round(min(lines[i]['score'] for i in ids), 3), 'lines': ids})
            leftover = set(lines) - used - {i for b in blocks for i in b['lines']}
            if leftover:
                raise ValueError(f'page {pno}: OCR lines in neither a block nor a table: {sorted(leftover)}')
            pages.append({'page': pno, 'blocks': blocks, 'ocr_lines': len(lines), 'size': [page.rect.width, page.rect.height]})
            for x in page.get_images(full=True):
                info = doc.extract_image(x[0])
                images.setdefault(sha256_bytes(info['image']), {'bytes': info['image'], 'ext': info['ext'],
                                                                'pages': []})['pages'].append(pno)
            # glyph-name runs like /G21 are unmapped Chinese, not numbers
            layer = sorted(_numbers(re.sub(r'/G[0-9A-F]{2}', ' ', page.get_text())))
            ocr_nums = sorted(_numbers('\n'.join(ln['text'] for ln in r['lines'])))
            common = sum(min(layer.count(n), ocr_nums.count(n)) for n in set(layer))
            checks.append({'page': pno, 'text_layer_numbers': len(layer), 'ocr_numbers': len(ocr_nums),
                           'matched': common})
    return {'pages': pages, 'tables': tables, 'images': images, 'number_check': checks}


def clauses(pages: list[dict[str, Any]]) -> list[list[Any]]:
    """Split body/heading blocks at clause numbers; text before the first clause is not a clause."""
    out, cur = [], None
    for p in pages:
        for b in p['blocks']:
            if b['kind'] in ('页眉', '页脚'):
                continue
            m = _CLAUSE.match(b['text'])
            if m and (b['kind'] == '标题' or len(m.group(1)) > 1):
                cur = [m.group(1), m.group(2) if b['kind'] == '标题' else '', '' if b['kind'] == '标题' else m.group(2),
                       p['page'], p['page']]
                out.append(cur)
            elif cur is not None:
                cur[2] = (cur[2] + '\n' + b['text']).strip()
                cur[4] = p['page']
    return out


def _table_rows(t: dict[str, Any]) -> list[list[str]]:
    grid = [[''] * t['cols'] for _ in range(t['rows'])]
    for c in t['cells']:
        grid[c['row'] - 1][c['col'] - 1] = c['text']
    return grid


def _markdown(title: str, parsed: dict[str, Any]) -> str:
    lines = [f'# {title}', '']
    for p in parsed['pages']:
        lines += [f'## 第 {p["page"]} 页', '']
        items = [(b['box'][1], 'b', b) for b in p['blocks']] + \
                [(t['bbox'][1], 't', t) for t in parsed['tables'] if t['page'] == p['page']]
        for _, kind, x in sorted(items, key=lambda i: i[0]):
            if kind == 'b':
                lines += [('### ' if x['kind'] == '标题' else '') + x['text'], '']
            else:
                rows = _table_rows(x)
                esc = lambda s: s.replace('|', '\\|').replace('\n', ' ')  # noqa: E731
                lines += [f'表格 {x["index"]}（第 {x["page"]} 页）', '', '| ' + ' | '.join(map(esc, rows[0])) + ' |',
                          '|' + '---|' * x['cols']] + ['| ' + ' | '.join(map(esc, r)) + ' |' for r in rows[1:]] + ['']
    return '\n'.join(lines)


def _safe(code: str) -> str:
    return re.sub(r'[\\/:*?"<>| ]+', '_', code)


def build(manifest_path: Path, manifest_sha256: str, material_id: str, ocr=None) -> dict[str, Any]:
    """Every standard of one staging manifest: verified originals, OCR text, vector tables, clauses."""
    manifest_path = Path(manifest_path)
    if sha256_file(manifest_path) != manifest_sha256:
        raise ValueError('staging manifest SHA-256 mismatch')
    smf = json.loads(manifest_path.read_text(encoding='utf-8'))
    if smf.get('material_id') != material_id:
        raise ValueError('staging manifest is for another material')
    base = manifest_path.parent.parent
    listed = {}
    for f in smf['files']:
        data = (base / f['path']).read_bytes()
        if len(data) != f['size'] or sha256_bytes(data) != f['sha256']:
            raise ValueError(f'staging file differs from manifest: {f["path"]}')
        listed[f['path']] = data
    files: dict[str, bytes] = {f'originals/{p}': b for p, b in listed.items()}
    paras, cells, clause_rows, standards, missing = [], [], [], [], []
    for item in smf['items']:
        code = item['code']
        if item.get('outcome') != 'downloaded' or not item.get('pdf'):
            missing.append({'code': code, 'name': item.get('name'), 'outcome': item.get('outcome'),
                            'reason': item.get('reason')})
            continue
        rel = item['pdf']['path'].split('/', 1)[1]
        if sha256_bytes(listed[rel]) != item['pdf']['sha256']:
            raise ValueError(f'{code}: PDF does not match its item record')
        parsed = parse_pdf(base / rel, item['pdf']['sha256'], ocr)
        if len(parsed['pages']) != item['pdf_pages']:
            raise ValueError(f'{code}: page count changed')
        for p in parsed['pages']:
            for n, b in enumerate(p['blocks'], 1):
                paras.append([code, p['page'], n, b['kind'], b['text'], *[round(v, 1) for v in b['box']], b['score'],
                              ' '.join(b['lines'])])
        for t in parsed['tables']:
            name = f'data/表格/{_safe(code)}-表{t["index"]}-第{t["page"]}页.csv'
            rows = _table_rows(t)
            files[name] = csv_bytes(rows[0], rows[1:])
            cells += [[code, t['index'], t['page'], c['row'], c['col'], c['text'], *c['box'], ' '.join(c['lines'])]
                      for c in t['cells']]
        clause_rows += [[code, *c] for c in clauses(parsed['pages'])]
        title = f'{code} {item["name"]}'
        files[f'data/全文/{_safe(code)}.md'] = _markdown(title, parsed).encode()
        for sha, img in parsed['images'].items():
            files[f'data/图片/{_safe(code)}-{sha[:12]}.{img["ext"]}'] = img['bytes']
        nc = parsed['number_check']
        standards.append({'code': code, 'name': item['name'], 'state': item.get('state'), 'pdf': rel,
                          'pdf_sha256': item['pdf']['sha256'], 'pages': len(parsed['pages']),
                          'ocr_lines': sum(p['ocr_lines'] for p in parsed['pages']),
                          'paragraphs': sum(len(p['blocks']) for p in parsed['pages']),
                          'tables': len(parsed['tables']), 'table_cells': sum(len(t['cells']) for t in parsed['tables']),
                          'clauses': sum(1 for c in clause_rows if c[0] == code), 'images': len(parsed['images']),
                          'detail_url': item.get('detail_url'), 'download_url': item.get('download_url'),
                          'number_check': {'text_layer_numbers': sum(c['text_layer_numbers'] for c in nc),
                                           'ocr_numbers': sum(c['ocr_numbers'] for c in nc),
                                           'matched': sum(c['matched'] for c in nc), 'pages': nc}})
    files['data/段落.csv'] = csv_bytes(['标准号'] + PARA_COLUMNS, paras)
    files['data/表格单元格.csv'] = csv_bytes(['标准号'] + CELL_COLUMNS, cells)
    files['data/条款.csv'] = csv_bytes(['标准号'] + CLAUSE_COLUMNS, clause_rows)
    status = 'partial' if missing or not smf.get('complete_fulltext') else 'finalized'
    scope = {'kind': 'full' if status == 'finalized' else 'partial', 'standards': len(smf['items']),
             'standards_with_fulltext': len(standards), 'missing': missing,
             'pages': sum(s['pages'] for s in standards), 'paragraphs': len(paras), 'table_cells': len(cells),
             'tables': sum(s['tables'] for s in standards), 'clauses': len(clause_rows),
             'number_check': {s['code']: {k: s['number_check'][k] for k in ('text_layer_numbers', 'ocr_numbers', 'matched')}
                              for s in standards}, 'staging_files_verified': len(listed)}
    schema = {'schema': 'codeyun.skilldep_dataset_schema/v1', 'material_id': material_id, 'files': {
        'data/段落.csv': {'columns': ['标准号'] + PARA_COLUMNS, 'record': '一个非表格文本块（OCR），坐标为 PDF 页面 pt、左上原点'},
        'data/表格单元格.csv': {'columns': ['标准号'] + CELL_COLUMNS, 'record': '一个表格单元格；边界取自 PDF 矢量表格线'},
        'data/条款.csv': {'columns': ['标准号'] + CLAUSE_COLUMNS, 'record': '一个编号条款（标题行的条款号起至下一条款号前）'},
        'data/表格/*.csv': {'record': '一张表（首行为原表首行）'}, 'data/全文/*.md': {'record': '一项标准全文'},
        'data/图片/*': {'record': 'PDF 内嵌图片原字节'}, 'originals/*': {'record': '采集原件（字节不变）'}}}
    provenance = {'material_id': material_id, 'source': smf.get('source'), 'staging_manifest': manifest_path.as_posix(),
                  'staging_manifest_sha256': manifest_sha256, 'standards': standards, 'missing': missing,
                  'ocr': 'backend.core.library.pdf_ocr.recognize_pdf_page (PaddleOCR, cache keyed by PDF SHA-256)',
                  'tables': 'pymupdf page.find_tables() on vector rulings; OCR line assigned to the cell containing its centre',
                  'parser': 'backend/core/skilldep_materials/standards_pdf_finalizer.py'}
    files |= {'data/使用说明.md': _readme(smf, standards, missing, scope).encode(),
              'schema.json': (json.dumps(schema, ensure_ascii=False, indent=1) + '\n').encode(),
              'provenance.json': (json.dumps(provenance, ensure_ascii=False, indent=1) + '\n').encode()}
    return {'files': files, 'records': {'data/段落.csv': len(paras), 'data/表格单元格.csv': len(cells),
                                        'data/条款.csv': len(clause_rows)},
            'scope': scope, 'status': status,
            'pending': [f'{m["code"]}: {m["outcome"]} {m["reason"] or ""}'.strip() for m in missing],
            'source': {'staging_manifest': manifest_path.as_posix(), 'staging_manifest_sha256': manifest_sha256}}


def _readme(smf, standards, missing, scope) -> str:
    lines = [f'# {smf.get("name")}', '', f'来源：全国标准信息公共服务平台 openstd.samr.gov.cn 官方页面下载的标准全文 PDF。', '',
             '| 标准号 | 名称 | 状态 | 页数 | 段落 | 表格/单元格 | 条款 | 数字核对（文本层/OCR/一致） |', '|---|---|---|---|---|---|---|---|']
    for s in standards:
        n = s['number_check']
        lines.append(f'| {s["code"]} | {s["name"]} | {s["state"] or ""} | {s["pages"]} | {s["paragraphs"]} | '
                     f'{s["tables"]}/{s["table_cells"]} | {s["clauses"]} | {n["text_layer_numbers"]}/{n["ocr_numbers"]}/{n["matched"]} |')
    lines += ['', '- 文字：PDF 的中文字体没有 Unicode 映射（文本层是乱码，原样保留在 originals/），正文用 OCR 逐页识别，'
              '未经人工逐字校对；数字与字母另用 PDF 文本层逐页核对，结果见上表最后一列。',
              '- 表格：单元格边界取自 PDF 自带的表格线，单元格文字为落在格内的 OCR 行；跨页续表按页分开存放。',
              '- 段落.csv / 表格单元格.csv 带页面坐标（pt，左上原点）与 OCR 行号，可回到 PDF 原页定位。',
              '- 全文/*.md 按页面先后排列正文与表格；条款.csv 按条款号切分。', '', '## 已知缺口', '']
    lines += [f'- {m["code"]} {m["name"] or ""}：未取得全文（{m["outcome"]}）' for m in missing] or ['- 无']
    lines.append('')
    return '\n'.join(lines)


def finalization_record(target: Path, previous: dict[str, Any]) -> dict[str, Any]:
    target = Path(target)
    check = verify_package(target)
    if check['status'] != 'verified':
        raise ValueError(f'package not verified: {check}')
    mbytes = (target / 'manifest.json').read_bytes()
    m = json.loads(mbytes)
    s = m['scope']
    files = [p for p in target.rglob('*') if p.is_file()]
    nc = '；'.join(f'{k} 数字 {v["matched"]}/{v["text_layer_numbers"]}' for k, v in s['number_check'].items())
    note = (f"{s['kind']}: {s['standards_with_fulltext']}/{s['standards']} 项标准全文 PDF 原件，{s['pages']} 页逐页 OCR，"
            f"{s['paragraphs']} 段、{s['tables']} 张表 {s['table_cells']} 格（PDF 矢量表格线定格）、{s['clauses']} 条款，带页面坐标；"
            f"文本层数字核对 {nc}；OCR 未人工逐字校对；{s['staging_files_verified']} 个采集文件哈希核验；"
            f"缺口：{'、'.join(x['code'] for x in s['missing']) or '无'}；未上传百度网盘。")
    return {'status': m['status'] if m['status'] == 'finalized' else 'cleaning', 'path': target.as_posix(),
            'size_bytes': sum(p.stat().st_size for p in files), 'file_count': len(files),
            'manifest_path': (target / 'manifest.json').as_posix(), 'manifest_sha256': sha256_bytes(mbytes),
            'source_acquisition_device_id': previous.get('source_acquisition_device_id'),
            'expected_resource_version': previous.get('expected_resource_version'),
            'finished_at': (m.get('revised_at') or m['finalized_at']).replace('+00:00', 'Z'), 'note': note, 'error': None}


def register(hub: Any, material_id: str, target: Path, source_device_id: str | None) -> dict[str, Any]:
    def current() -> dict[str, Any]:
        items = hub.finalizations()['items']
        items = items.values() if isinstance(items, dict) else items
        return next((i for i in items if i.get('material_id') == material_id and i.get('root') == 'local'), {})
    previous = current() or {'source_acquisition_device_id': source_device_id}
    record = finalization_record(target, previous)
    put = hub.put_finalization(material_id, 'local', record)
    back = current()
    mismatch = sorted(k for k, v in record.items() if back.get(k) != v)
    return {'ack': not mismatch, 'mismatch': mismatch, 'record': record, 'put_response': put, 'read_back': back}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from .catalog import CatalogStore
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('material_id')
    p.add_argument('--root', default=str(layout.FINAL_LOCAL_ROOT))
    p.add_argument('--naming', default=str(layout.DEFAULT_NAMING))
    p.add_argument('--target', help='write a candidate here instead of the layout dir (never registered)')
    p.add_argument('--backup-dir', help='backup dir when rewriting an existing package')
    p.add_argument('--register', action='store_true')
    a = p.parse_args(argv)
    item = CatalogStore().load()['items'][a.material_id]
    local = item['local']
    built = build(Path(local['manifest_path']), local['manifest_sha256'], a.material_id)
    where = layout.resolve(a.material_id, layout.load_naming(a.naming), Path(a.root))
    built['source'] |= layout_source(where)
    target = Path(a.target) if a.target else Path(where['local_dir'])
    kw = dict(records=built['records'], scope=built['scope'], status=built['status'], source=built['source'],
              pending=built['pending'])
    if target.exists():
        if not a.backup_dir:
            p.error(f'{target} exists; pass --backup-dir')
        result = rewrite_package(target, built['files'], backup_dir=Path(a.backup_dir),
                                 reason='rebuild from verified staging', **kw)
    else:
        result = write_package(target, a.material_id, built['files'], **kw)
    ok = result['status'] in ('verified', 'unchanged', 'rewritten')
    if ok and a.register and not a.target:
        from .codehub import CodeHubSkillDep
        hub = CodeHubSkillDep()
        hub.verify_peer()
        result['codehub'] = register(hub, a.material_id, target, local.get('device_id'))
        ok = result['codehub']['ack']
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
