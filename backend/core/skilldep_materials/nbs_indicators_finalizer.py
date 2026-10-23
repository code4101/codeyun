"""Complete 国家统计局主要统计指标解释 (sd-7c801abe5389): originals, per-record source locators, 2 formula glyphs.

Starts from the verified r2 package (548 records, 20 formula images) and keeps every existing data byte except
the two formula cells whose ``P65`` subscript suffix could not be read from the low-resolution web images.
Those are settled only by evidence from the same publisher: the 《中国统计年鉴2023》 indicator-explanation PDF
prints the same two formulas as vector text with ``+`` after ``65`` (Symbol font U+F02B), matching the page's
own variable definition ``P65+``.  The PDF is checked here on every build, not trusted from a note.

Added::

    data/来源定位.csv     每条记录：来源页面、原件路径/SHA-256、段落块 XPath 与字符区间、公式图 XPath/网址
    data/使用说明.md
    originals/html/…      2 目录页 + 27 正文页原件（字节不变）
    originals/formula/…   20 张公式原图（源文件名，字节不变）
    originals/supplement/ 年鉴 PDF 与 sources.json

Inputs are only read: staging acquisition group (49 files, SHA-256 each), the phase-A canonical blocks/documents
that the r2 records were projected from, and the supplement directory.

CLI::

    uv run python -m backend.core.skilldep_materials.nbs_indicators_finalizer --supplement-dir D --backup-dir B \
        [--target T | --register]
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from .finalize import csv_bytes, rewrite_package, sha256_bytes, sha256_file, verify_package, write_package

MATERIAL_ID = 'sd-7c801abe5389'
PACKAGE = Path('F:/data/m2610skilldep/数据分析/跨二级通用/国家统计局主要统计指标解释（15 类）_sd-7c801abe5389')
ACQUISITION = Path('F:/CodeYun/materials/m2610skilldependency/staging/nbs-composite-20261022/sd-7c801abe5389/'
                   'acquisition/2ebfe3ad57ef6c177a4971b731cc86a2b7013fed6766666c36e4c9ade8e8bc6a.json')
CANONICAL = Path('F:/CodeYun/materials/m2610skilldependency/orchestration/nbs-indicators-finalizer/phaseA/'
                 'stage2/internal/canonical')
DATA = 'data/国家统计局主要统计指标解释.csv'
LOCATOR = 'data/来源定位.csv'
README = 'data/使用说明.md'
UNREADABLE = '〔下标后符号原图不可辨〕'
GLYPH_STATE = ('公式已按原图转写；原图 P65 下标后符号分辨率不足，按国家统计局《中国统计年鉴2023》主要统计指标解释 PDF '
               '同式矢量文本（65 后为“+”）与正文变量说明“P65+”确定')
LOCATOR_COLUMNS = ['序号', '指标名称', '类别', '记录类型', '来源页面键', '来源网址', '原件', '原件SHA256', '段落块',
                   '段落XPath', '段落字符区间', '段落文本SHA256', '公式原件', '公式图XPath', '公式图网址']


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def glyph_evidence(pdf: bytes) -> dict[str, Any]:
    """Symbol-font glyph right after each ``65`` in the dependency-ratio formulas of the yearbook PDF."""
    import pymupdf
    page = pymupdf.open(stream=pdf, filetype='pdf')[0]
    spans = [s for b in page.get_text('rawdict')['blocks'] for ln in b.get('lines', []) for s in ln['spans']]
    found = []
    for i, s in enumerate(spans):
        if ''.join(c['c'] for c in s['chars']) != '65':
            continue
        x1, y0 = s['bbox'][2], s['bbox'][1]
        near = [t for t in spans if t['font'] == 'Symbol' and abs(t['bbox'][0] - x1) < 3 and abs(t['bbox'][1] - y0) < 6]
        if near:
            found.append({'bbox': [round(v, 1) for v in s['bbox']],
                          'next_glyph': [hex(ord(c['c'])) for t in near for c in t['chars']]})
    text = page.get_text()
    return {'plus_after_65': [f for f in found if f['next_glyph'] == ['0xf02b']], 'all': found,
            'has_GDR': 'GDR 为总抚养比' in text, 'has_old_age': '老年人口抚养比' in text and 'P65+为65 岁' in text}


def build(package: Path, supplement_dir: Path, acquisition: Path = ACQUISITION,
          canonical: Path = CANONICAL) -> dict[str, Any]:
    check = verify_package(package)
    if check['status'] != 'verified':
        raise ValueError(f'existing package not verified: {check}')
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    old = {e['path']: (package / e['path']).read_bytes() for e in manifest['files']}
    acq = json.loads(acquisition.read_bytes())
    base = acquisition.parent.parent
    originals, by_sha = {}, {}
    for f in acq['files']:
        data = (base / f['path']).read_bytes()
        if sha256_bytes(data) != f['sha256'] or len(data) != f['bytes']:
            raise ValueError(f'staging file differs: {f["path"]}')
        rel = 'originals/' + f['path'].removeprefix('raw/')
        originals[rel] = data
        by_sha[f['sha256']] = (rel, f)
    docs = {d['source_id']: d for d in _jsonl(canonical / 'documents.jsonl')}
    blocks = {b['block_id']: b for b in _jsonl(canonical / 'blocks.jsonl')}
    prov = json.loads(old['provenance.json'])
    rows = list(csv.DictReader(io.StringIO(old[DATA].decode('utf-8-sig'))))
    header = list(rows[0])
    if [int(r['序号']) for r in rows] != [p['序号'] for p in prov['rows']]:
        raise ValueError('provenance rows do not follow the CSV')

    sources = json.loads((supplement_dir / 'sources.json').read_text(encoding='utf-8'))
    pdf_name, pdf_meta = next(iter(sources['files'].items()))
    pdf = (supplement_dir / pdf_name).read_bytes()
    if sha256_bytes(pdf) != pdf_meta['sha256']:
        raise ValueError('supplement PDF changed')
    evidence = glyph_evidence(pdf)
    glyph_ok = len(evidence['plus_after_65']) >= 2 and evidence['has_GDR'] and evidence['has_old_age']

    locator, fixed = [], []
    for r, p in zip(rows, prov['rows']):
        sid = (p.get('stage2_record_id') or '').split('#')[0]
        doc = docs[sid]
        rel, entry = by_sha[doc['raw_sha256']]
        bl = [blocks[b] for b in p['stage2_block_ids']]
        imgs = p.get('image_files') or []
        img_entries = [by_sha[i['sha256']] for i in imgs]
        locator.append([r['序号'], r['指标名称'], r['类别'], r['记录类型'], sid, doc['url'], rel, entry['sha256'],
                        '；'.join(b['block_id'] for b in bl), '；'.join(b['xpath'] for b in bl),
                        '；'.join(f'{b["char_start"]}-{b["char_end"]}' for b in bl),
                        '；'.join(b['text_sha256'] for b in bl),
                        '；'.join(e[0] for e in img_entries), '；'.join(e[1].get('parent_xpath', '') for e in img_entries),
                        '；'.join(e[1]['url'] for e in img_entries)])
        if UNREADABLE in r['计算公式'] and glyph_ok:
            r['计算公式'] = r['计算公式'].replace(UNREADABLE, '+')
            r['公式核验状态'] = GLYPH_STATE
            fixed.append(r['序号'])
    pending_rows = [r['序号'] for r in rows if UNREADABLE in r['计算公式']]

    files = dict(old)
    files[DATA] = csv_bytes(header, [[r[c] for c in header] for r in rows])
    files[LOCATOR] = csv_bytes(LOCATOR_COLUMNS, locator)
    files |= originals
    files[f'originals/supplement/{pdf_name}'] = pdf
    files['originals/supplement/sources.json'] = (supplement_dir / 'sources.json').read_bytes()
    status = 'partial' if pending_rows else 'finalized'
    gaps = [g for g in prov.get('gaps', []) if pending_rows]
    scope = dict(manifest['scope']) | {
        'formula_transcriptions_accepted': manifest['scope']['formula_transcriptions_accepted'] + len(fixed),
        'formula_transcriptions_pending_glyph': len(pending_rows), 'glyph_rows_resolved': fixed,
        'originals': {'html': sum(1 for k in originals if k.startswith('originals/html/')),
                      'formula': sum(1 for k in originals if k.startswith('originals/formula/'))},
        'locator_rows': len(locator), 'staging_files_verified': len(acq['files'])}
    prov = prov | {'gaps': gaps, 'originals': {rel: {'sha256': f['sha256'], 'bytes': f['bytes'], 'url': f['url'],
                                                     'staging_path': f['path']} for rel, f in by_sha.values()},
                   'canonical': {n: sha256_file(canonical / n) for n in ('documents.jsonl', 'blocks.jsonl')},
                   'glyph_resolution': {'rows': fixed, 'source': pdf_meta, 'evidence': evidence,
                                        'rule': 'only replaces 〔下标后符号原图不可辨〕 when the yearbook PDF has a Symbol '
                                                '“+” right after “65” in both dependency-ratio formulas'}}
    schema = json.loads(old['schema.json'])
    schema['files'] = schema.get('files', {}) | {
        LOCATOR: '每条记录的来源定位：' + '、'.join(LOCATOR_COLUMNS),
        README: '使用说明', 'originals/': '官方原件（html 正文/目录页、formula 公式图、supplement 年鉴 PDF 证据），字节不变'}
    files['schema.json'] = (json.dumps(schema, ensure_ascii=False, indent=1) + '\n').encode()
    files['provenance.json'] = (json.dumps(prov, ensure_ascii=False, indent=1) + '\n').encode()
    files[README] = _readme(rows, scope, fixed, pending_rows, pdf_meta).encode()
    records = {e['path']: e['records'] for e in manifest['files'] if e.get('records') is not None}
    return {'files': files, 'records': records, 'scope': scope, 'status': status, 'source': manifest['source'],
            'pending': manifest['pending'] if pending_rows else [], 'preserve': sorted(
                k for k in old if k not in (DATA, 'schema.json', 'provenance.json'))}


def _readme(rows, scope, fixed, pending, pdf_meta) -> str:
    cats = sorted({r['类别'] for r in rows}, key=lambda c: next(i for i, r in enumerate(rows) if r['类别'] == c))
    lines = ['# 国家统计局主要统计指标解释', '',
             f'来源：国家统计局“主要统计指标解释”（https://www.stats.gov.cn/sj/zbjs/），{len(cats)} 类正文页逐段解析，'
             f'共 {len(rows)} 条记录（指标解释、说明，及 1 条仅见于公式图的指标）。', '',
             f'- 国家统计局主要统计指标解释.csv：主表，一行一条；“计算公式”为公式原图逐字转写，原图在 公式图片/。',
             f'- 来源定位.csv：每条记录对应的正文页、原件路径与 SHA-256、段落 XPath 与字符区间、公式图 XPath 与网址。',
             f'- originals/：{scope["originals"]["html"]} 个官方网页原件（2 目录页 + 27 正文页）、'
             f'{scope["originals"]["formula"]} 张公式原图、年鉴 PDF 证据，字节不变。', '',
             '## 公式核验', '',
             f'- 20 张公式图全部转写；其中 {len(fixed)} 条（序号 {"、".join(fixed) or "无"}：总抚养比、老年人口抚养比）'
             f'原图 P65 下标后的符号分辨率不足，按国家统计局《中国统计年鉴2023》指标解释 PDF（{pdf_meta["url"]}）'
             '同式矢量文本中 65 后的“+”与正文变量说明“P65+”确定为 P65+。', '', '## 已知缺口', '']
    lines += [f'- 序号 {s} 的公式符号仍未确定' for s in pending] or ['- 无']
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
    note = (f"{'full' if m['status'] == 'finalized' else 'partial'}: 27 类 548 条（547 条正文投影 + 1 条仅见于公式图），"
            f"20 张公式图全部转写，其中总抚养比/老年人口抚养比 P65+ 按《中国统计年鉴2023》指标解释 PDF 矢量公式确定；"
            f"新增逐条来源定位（段落 XPath/字符区间/原件 SHA-256）与 {s['originals']['html']} 个网页原件、"
            f"{s['originals']['formula']} 张公式原图原件；{s['staging_files_verified']} 个采集文件哈希核验；"
            f"原数据字节除 2 个公式格外不变（r{m.get('manifest_revision', 1)}，旧版已备份）；未上传百度网盘。")
    return {'status': m['status'] if m['status'] == 'finalized' else 'cleaning', 'path': target.as_posix(),
            'size_bytes': sum(p.stat().st_size for p in files), 'file_count': len(files),
            'manifest_path': (target / 'manifest.json').as_posix(), 'manifest_sha256': sha256_bytes(mbytes),
            'source_acquisition_device_id': previous.get('source_acquisition_device_id'),
            'expected_resource_version': previous.get('expected_resource_version'),
            'finished_at': (m.get('revised_at') or m['finalized_at']).replace('+00:00', 'Z'), 'note': note, 'error': None}


def register(hub: Any, target: Path) -> dict[str, Any]:
    def current() -> dict[str, Any]:
        items = hub.finalizations()['items']
        items = items.values() if isinstance(items, dict) else items
        return next((i for i in items if i.get('material_id') == MATERIAL_ID and i.get('root') == 'local'), {})
    record = finalization_record(target, current())
    put = hub.put_finalization(MATERIAL_ID, 'local', record)
    back = current()
    mismatch = sorted(k for k, v in record.items() if back.get(k) != v)
    return {'ack': not mismatch, 'mismatch': mismatch, 'record': record, 'put_response': put, 'read_back': back}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import shutil
    import sys
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--package', default=str(PACKAGE))
    p.add_argument('--supplement-dir', required=True)
    p.add_argument('--backup-dir', required=True)
    p.add_argument('--target', help='candidate: copy the package here first and rewrite the copy (never registered)')
    p.add_argument('--register', action='store_true')
    a = p.parse_args(argv)
    target = Path(a.package)
    if a.target:
        shutil.copytree(target, a.target)
        target = Path(a.target)
    built = build(target, Path(a.supplement_dir))
    result = rewrite_package(target, built['files'], records=built['records'], scope=built['scope'],
                             status=built['status'], source=built['source'], pending=built['pending'],
                             backup_dir=Path(a.backup_dir), preserve=built['preserve'],
                             reason='add originals and per-record source locators; settle 2 P65+ glyphs from the '
                                    'NBS yearbook 2023 indicator PDF')
    ok = result['status'] in ('rewritten', 'unchanged')
    if ok and a.register and not a.target:
        from .codehub import CodeHubSkillDep
        hub = CodeHubSkillDep()
        hub.verify_peer()
        result['codehub'] = register(hub, target)
        ok = result['codehub']['ack']
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0 if ok else 2


__all__ = ['build', 'glyph_evidence', 'finalization_record', 'register', 'write_package']

if __name__ == '__main__':
    raise SystemExit(main())
