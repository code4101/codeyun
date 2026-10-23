"""Type-based extraction of SkillDep raw files into documents, tables and images (no network).

Each extractor returns ``{kind, method, title, text, pages, tables: [{name, rows}], images: [{name, bytes, page}],
links, note}``; ``text`` is Markdown-ish plain text in reading order.  Office binaries (doc/xls/ppt/wps/rtf)
are converted once by headless LibreOffice into a temp directory and then read like their OOXML siblings.
PDFs use the Unicode text layer when it is real and page OCR (library API) when the fonts are unmapped or
the page has no text; tables come from the PDF's vector rulings either way.
"""
from __future__ import annotations

import csv
import io
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree

from backend.core.temp_paths import codeyun_temp_root

SOFFICE = Path('C:/Program Files/LibreOffice/program/soffice.exe')
_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
_R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
HTML = {'.html', '.htm', '.shtml', '.xhtml'}
OFFICE_CONVERT = {'.doc': 'docx', '.wps': 'docx', '.rtf': 'docx', '.xls': 'xlsx', '.et': 'xlsx', '.ppt': 'pptx',
                  '.dps': 'pptx'}
DATA = {'.csv', '.tsv', '.json', '.jsonl', '.parquet', '.xml', '.sql'}
IMAGES = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg', '.tif', '.tiff'}
TEXT = {'.txt', '.md', '.sol', '.dp', '.php', '.py', '.yaml', '.yml', '.ini', '.cfg'}
SKIP = {'.log': '采集过程日志，不是素材内容', '.url': 'Windows 快捷方式，仅含链接'}


def sniff(path: Path, head: bytes) -> str:
    """Effective type by magic bytes first (``.raw`` downloads), then suffix."""
    if head.startswith(b'%PDF'):
        return '.pdf'
    if head.startswith(b'PK\x03\x04'):
        try:
            names = zipfile.ZipFile(path).namelist()
        except zipfile.BadZipFile:
            return '.zip'
        if 'word/document.xml' in names:
            return '.docx'
        if 'xl/workbook.xml' in names:
            return '.xlsx'
        if 'ppt/presentation.xml' in names:
            return '.pptx'
        return '.zip'
    if head.startswith(b'\xd0\xcf\x11\xe0'):
        suf = path.suffix.lower()
        return suf if suf in OFFICE_CONVERT else '.doc'
    if head.startswith((b'\x89PNG', b'\xff\xd8\xff', b'GIF8')):
        return path.suffix.lower() if path.suffix.lower() in IMAGES else '.png'
    lower = head[:2048].lower()
    if b'<html' in lower or b'<!doctype html' in lower or b'<body' in lower:
        return '.html'
    return path.suffix.lower() or '(none)'


def decode(data: bytes) -> str:
    for enc in ('utf-8-sig', 'gb18030'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def _clean(text: str) -> str:
    return re.sub(r'[ \t\r\f\v\xa0]+', ' ', text).strip()


# ---- HTML ------------------------------------------------------------------------------------------------------

def _html_tables(soup) -> list[list[list[str]]]:
    tables = []
    for t in soup.find_all('table'):
        if t.find_parent('table') is not None:
            continue  # nested tables are kept as cell text of their parent
        grid: dict[tuple[int, int], str] = {}
        for r, tr in enumerate(t.find_all('tr')):
            c = 0
            for cell in tr.find_all(['td', 'th'], recursive=False):
                while (r, c) in grid:
                    c += 1
                text = _clean(cell.get_text(' '))
                rs, cs = int(cell.get('rowspan', 1) or 1), int(cell.get('colspan', 1) or 1)
                for dr in range(min(rs, 200)):
                    for dc in range(min(cs, 50)):
                        grid[(r + dr, c + dc)] = text if (dr, dc) == (0, 0) else ''
                c += cs
        if grid:
            nr, nc = max(k[0] for k in grid) + 1, max(k[1] for k in grid) + 1
            tables.append([[grid.get((r, c), '') for c in range(nc)] for r in range(nr)])
    return tables


def extract_html(data: bytes, url: str | None) -> dict[str, Any]:
    import trafilatura
    from bs4 import BeautifulSoup
    html = decode(data)
    soup = BeautifulSoup(html, 'lxml')
    for s in soup(['script', 'style', 'noscript']):
        s.decompose()
    title = _clean((soup.title.get_text() if soup.title else '') or (soup.h1.get_text() if soup.h1 else ''))
    main = trafilatura.extract(html, output_format='markdown', include_tables=True, include_images=True,
                               include_links=False, favor_recall=True) or ''
    method = 'trafilatura'
    if len(main) < 50:
        main, method = '\n'.join(_clean(x) for x in soup.get_text('\n').split('\n') if _clean(x)), 'full-page-text'
    images = [{'ref': urljoin(url or '', i.get('src')), 'alt': i.get('alt') or ''} for i in soup.find_all('img')
              if i.get('src') and not i.get('src').startswith('data:')]
    links = [{'text': _clean(a.get_text()), 'href': urljoin(url or '', a['href'])} for a in soup.find_all('a', href=True)
             if re.search(r'\.(pdf|docx?|xlsx?|pptx?|zip|rar|wps|et)(\?|$)', a['href'], re.I)]
    return {'kind': 'html', 'method': method, 'title': title, 'text': main, 'pages': None,
            'tables': [{'name': f'表{i}', 'rows': t} for i, t in enumerate(_html_tables(soup), 1)],
            'images': [], 'image_refs': images, 'links': links}


# ---- PDF -------------------------------------------------------------------------------------------------------

def _garbled(text: str) -> bool:
    """Unmapped CJK fonts: glyph names (/G21) or the 犃犅犆 substitution block dominate the layer."""
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    fake = sum(1 for c in text if '\u7280' <= c <= '\u72ff')
    return len(re.findall(r'/G[0-9A-F]{2}', text)) > 20 or (cjk > 50 and fake / cjk > 0.2)


def extract_pdf(path: Path, sha: str, ocr=None) -> dict[str, Any]:
    import pymupdf
    with pymupdf.open(path) as doc:
        layer = [p.get_text() for p in doc]
        n = len(doc)
        scanned = [i for i, t in enumerate(layer, 1) if len(t.strip()) < 20]
        need_ocr = _garbled(''.join(layer)) or len(scanned) > n / 2
        if need_ocr:
            from .standards_pdf_finalizer import _markdown, _table_rows, parse_pdf
            parsed = parse_pdf(path, sha, ocr)
            text = _markdown('', parsed).lstrip('# \n')
            tables = [{'name': f'表{t["index"]}-第{t["page"]}页', 'rows': _table_rows(t), 'page': t['page']}
                      for t in parsed['tables']]
            images = [{'bytes': i['bytes'], 'ext': i['ext'], 'page': i['pages'][0]} for i in parsed['images'].values()]
            nc = parsed['number_check']
            return {'kind': 'pdf', 'method': 'ocr', 'title': (doc.metadata or {}).get('title') or '', 'text': text,
                    'pages': n, 'tables': tables, 'images': images, 'image_refs': [], 'links': [],
                    'note': f'OCR（中文字体无 Unicode 映射或扫描页）；文本层数字核对 '
                            f'{sum(c["matched"] for c in nc)}/{sum(c["ocr_numbers"] for c in nc)}'}
        parts, tables, images = [], [], {}
        for pno, page in enumerate(doc, 1):
            parts.append(f'## 第 {pno} 页\n\n{layer[pno - 1].strip()}\n')
            for i, t in enumerate(page.find_tables().tables, 1):
                rows = [[_clean(c or '') for c in r] for r in t.extract()]
                if rows and any(any(c for c in r) for r in rows):
                    tables.append({'name': f'表{len(tables) + 1}-第{pno}页', 'rows': rows, 'page': pno})
            for x in page.get_images(full=True):
                try:
                    info = doc.extract_image(x[0])
                except Exception:  # noqa: BLE001 - broken image stream: keep the PDF original only
                    continue
                if info and info.get('image') and len(info['image']) > 2048:
                    images.setdefault(info['image'], {'bytes': info['image'], 'ext': info['ext'], 'page': pno})
        note = f'{len(scanned)} 页无文本层（扫描页，未 OCR）' if scanned else ''
        return {'kind': 'pdf', 'method': 'text-layer', 'title': (doc.metadata or {}).get('title') or '',
                'text': '\n'.join(parts), 'pages': n, 'tables': tables, 'images': list(images.values()),
                'image_refs': [], 'links': [], 'note': note}


# ---- OOXML -----------------------------------------------------------------------------------------------------

def _wtext(el) -> str:
    out = []
    for node in el.iter():
        if node.tag == _W + 't' and node.text:
            out.append(node.text)
        elif node.tag == _W + 'tab':
            out.append('\t')
        elif node.tag in (_W + 'br', _W + 'cr'):
            out.append('\n')
    return ''.join(out)


def extract_docx(data: bytes) -> dict[str, Any]:
    z = zipfile.ZipFile(io.BytesIO(data))
    body = ElementTree.fromstring(z.read('word/document.xml')).find(_W + 'body')
    parts, tables = [], []
    for el in body:
        if el.tag == _W + 'p':
            t = _wtext(el).strip()
            if t:
                parts.append(t)
        elif el.tag == _W + 'tbl':
            rows = [[_clean(_wtext(tc)) for tc in tr.findall(_W + 'tc')] for tr in el.findall(_W + 'tr')]
            tables.append({'name': f'表{len(tables) + 1}', 'rows': rows})
            parts.append(f'[表{len(tables)}]')
    for name in ('word/footnotes.xml', 'word/endnotes.xml'):
        if name in z.namelist():
            notes = [_clean(' '.join(_wtext(p) for p in fn.findall(_W + 'p')))
                     for fn in ElementTree.fromstring(z.read(name)) if fn.get(_W + 'type') is None]
            if any(notes):
                parts.append('\n## 注释\n' + '\n'.join(n for n in notes if n))
    images = [{'bytes': z.read(n), 'ext': Path(n).suffix.lstrip('.'), 'page': None}
              for n in z.namelist() if n.startswith('word/media/')]
    return {'kind': 'docx', 'method': 'ooxml', 'title': parts[0][:80] if parts else '', 'text': '\n\n'.join(parts),
            'pages': None, 'tables': tables, 'images': images, 'image_refs': [], 'links': []}


def extract_pptx(data: bytes) -> dict[str, Any]:
    z = zipfile.ZipFile(io.BytesIO(data))
    slides = sorted((n for n in z.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)),
                    key=lambda n: int(re.search(r'(\d+)', n.rsplit('/', 1)[1]).group(1)))
    parts, tables = [], []
    for i, n in enumerate(slides, 1):
        root = ElementTree.fromstring(z.read(n))
        paras = [''.join(t.text or '' for t in p.iter(_A + 't')) for p in root.iter(_A + 'p')]
        parts.append(f'## 第 {i} 张幻灯片\n\n' + '\n'.join(p for p in paras if p.strip()))
        for tbl in root.iter(_A + 'tbl'):
            rows = [[_clean(''.join(t.text or '' for t in tc.iter(_A + 't'))) for tc in tr.iter(_A + 'tc')]
                    for tr in tbl.iter(_A + 'tr')]
            tables.append({'name': f'表{len(tables) + 1}-第{i}张', 'rows': rows, 'page': i})
    images = [{'bytes': z.read(n), 'ext': Path(n).suffix.lstrip('.'), 'page': None}
              for n in z.namelist() if n.startswith('ppt/media/')]
    return {'kind': 'pptx', 'method': 'ooxml', 'title': '', 'text': '\n\n'.join(parts), 'pages': len(slides),
            'tables': tables, 'images': images, 'image_refs': [], 'links': []}


def extract_xlsx(path: Path) -> dict[str, Any]:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    tables, parts = [], []
    for ws in wb.worksheets:
        rows = [['' if v is None else str(v) for v in r] for r in ws.iter_rows(values_only=True)]
        while rows and not any(rows[-1]):
            rows.pop()
        width = max((max((i + 1 for i, v in enumerate(r) if v), default=0) for r in rows), default=0)
        rows = [r[:width] for r in rows]
        tables.append({'name': f'工作表-{ws.title}', 'rows': rows})
        parts.append(f'## 工作表 {ws.title}（{len(rows)} 行 × {width} 列，见表格文件）')
    wb.close()
    return {'kind': 'xlsx', 'method': 'openpyxl', 'title': '', 'text': '\n'.join(parts), 'pages': None,
            'tables': tables, 'images': [], 'image_refs': [], 'links': []}


def convert_office(paths: list[Path], outdir: Path) -> dict[Path, Path]:
    """Headless LibreOffice conversion of legacy office files; returns source -> converted path."""
    done = {}
    by_target: dict[str, list[Path]] = {}
    for p in paths:
        by_target.setdefault(OFFICE_CONVERT[p.suffix.lower()], []).append(p)
    for target, group in by_target.items():
        for i in range(0, len(group), 40):
            chunk = group[i:i + 40]
            work = outdir / f'{target}-{i}'
            work.mkdir(parents=True, exist_ok=True)
            subprocess.run([str(SOFFICE), '--headless', '--norestore', '--convert-to', target, '--outdir', str(work),
                            *map(str, chunk)], capture_output=True, timeout=1800)
            for p in chunk:
                out = work / (p.stem + '.' + target)
                if out.is_file():
                    done[p] = out
    return done


def extract_text(data: bytes) -> dict[str, Any]:
    return {'kind': 'text', 'method': 'decode', 'title': '', 'text': decode(data), 'pages': None, 'tables': [],
            'images': [], 'image_refs': [], 'links': []}


def table_csv(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\n').writerows(rows)
    return b'\xef\xbb\xbf' + buf.getvalue().encode()


def temp_dir(name: str) -> Path:
    return Path(tempfile.mkdtemp(dir=codeyun_temp_root('skilldep-clean'), prefix=name))
