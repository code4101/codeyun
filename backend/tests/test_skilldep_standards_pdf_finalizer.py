"""Standards PDF finalizer: vector-table cell assignment with a fake OCR, clause splitting, real package read-back."""

import json
from pathlib import Path

import pymupdf
import pytest

from backend.core.skilldep_materials import finalize, standards_pdf_finalizer as sp

FORMAL = Path('F:/data/m2610skilldep/数据分析/零售/GB、T 18106-2021 零售业态分类_sd-7771a1a59bfe')


def pdf_with_table(path):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=400)
    for x in (50, 150, 250):
        page.draw_line((x, 200), (x, 300))
    for y in (200, 250, 300):
        page.draw_line((50, y), (250, y))
    page.insert_text((60, 230), '12')  # text layer number for the cross-check
    doc.save(path)


def fake_ocr(lines):
    def ocr(source, *, content_hash, page_number):
        ls = [{'line_id': f'line-{i}', 'text': t, 'x': x, 'y': y, 'w': w, 'h': h, 'score': 0.9}
              for i, (t, x, y, w, h) in enumerate(lines, 1)]
        blocks = [{'kind': 'heading' if t.startswith('3.1') else 'paragraph', 'line_ids': [f'line-{i}']}
                  for i, (t, *_rest) in enumerate(lines, 1)]
        return {'geometry': {'pixel_to_page_matrix': [1, 0, 0, 1, 0, 0]}, 'lines': ls, 'layout': {'blocks': blocks}}
    return ocr


def test_parse_pdf_puts_ocr_lines_into_vector_cells_and_splits_clauses(tmp_path):
    path = tmp_path / 'a.pdf'
    pdf_with_table(path)
    ocr = fake_ocr([('3.1 便利店', 50, 60, 100, 12), ('小型零售', 50, 80, 100, 12),
                    ('12', 60, 215, 20, 12), ('面积', 160, 215, 40, 12), ('甲', 60, 265, 10, 12), ('乙', 160, 265, 10, 12)])
    r = sp.parse_pdf(path, 'x', ocr)
    assert len(r['tables']) == 1 and sp._table_rows(r['tables'][0]) == [['12', '面积'], ['甲', '乙']]
    assert [b['text'] for b in r['pages'][0]['blocks']] == ['3.1 便利店', '小型零售']
    assert sp.clauses(r['pages']) == [['3.1', '便利店', '小型零售', 1, 1]]
    assert r['number_check'][0]['matched'] == 1


def test_join_keeps_space_only_between_latin_tokens():
    assert sp._join(['门店面积', '50 m2', 'convenience', 'store']) == '门店面积50 m2 convenience store'


@pytest.mark.skipif(not (FORMAL / 'manifest.json').is_file(), reason='formal package not on this device')
def test_formal_gb18106_package():
    check = finalize.verify_package(FORMAL)
    assert check['status'] == 'verified' and check['package_status'] == 'finalized'
    m = json.loads((FORMAL / 'manifest.json').read_text(encoding='utf-8'))
    assert m['scope']['pages'] == 14 and m['scope']['tables'] == 6 and m['scope']['missing'] == []
