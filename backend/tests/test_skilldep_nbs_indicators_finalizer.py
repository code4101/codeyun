"""NBS indicators: yearbook glyph evidence and the formal package read-back (skipped off this device)."""

import csv
import io
import json
from pathlib import Path

import pytest

from backend.core.skilldep_materials import finalize, nbs_indicators_finalizer as nbs

PDF = Path('F:/CodeYun/materials/m2610skilldependency/orchestration/old-materials-cleaning-cc/sd-7c801abe5389/'
           'supplement/nbs-ndsj2023-zb02.pdf')


@pytest.mark.skipif(not PDF.is_file(), reason='yearbook PDF evidence not on this device')
def test_yearbook_pdf_has_symbol_plus_after_65_in_both_dependency_formulas():
    ev = nbs.glyph_evidence(PDF.read_bytes())
    assert len(ev['plus_after_65']) >= 2 and ev['has_GDR'] and ev['has_old_age']


@pytest.mark.skipif(not (nbs.PACKAGE / nbs.LOCATOR).is_file(), reason='formal package not on this device')
def test_formal_package_locators_originals_and_no_unreadable_glyph():
    check = finalize.verify_package(nbs.PACKAGE)
    assert check['status'] == 'verified' and check['package_status'] == 'finalized'
    rows = list(csv.DictReader(io.StringIO((nbs.PACKAGE / nbs.DATA).read_bytes().decode('utf-8-sig'))))
    loc = list(csv.DictReader(io.StringIO((nbs.PACKAGE / nbs.LOCATOR).read_bytes().decode('utf-8-sig'))))
    assert len(rows) == len(loc) == 548 and not any(nbs.UNREADABLE in r['计算公式'] for r in rows)
    assert {r['序号']: r['计算公式'] for r in rows}['29'] == 'ODR＝P65+/P15–64×100%'
    m = json.loads((nbs.PACKAGE / 'manifest.json').read_text(encoding='utf-8'))
    assert m['scope']['originals'] == {'html': 29, 'formula': 20} and m['pending'] == []
