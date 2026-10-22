"""NPPA 游戏审批 sample package: synthetic build checks plus a read-back of the real sample when present."""

import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path

import pytest

from backend.core.skilldep_materials import finalize, nppa_finalizer as nppa

GAP_URL = nppa.SOURCE_URL + 'gcwlyxspxx/202208/t20220801_442173.html'
SAMPLE = Path('F:/data/成品数据样例/游戏/跨二级通用/国家新闻出版署 网络游戏审批信息_sd-b05c6a963661')


def sha(b):
    return hashlib.sha256(b).hexdigest()


def table(header, rows):
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\n').writerows([header, *rows])
    return b'\xef\xbb\xbf' + buf.getvalue().encode()


def staging(tmp_path, *, approvals_rows=2, tamper=False):
    root = tmp_path / 'stage'
    for d in ('raw', 'structured', 'manifests'):
        (root / d).mkdir(parents=True)
    page = b'<html>t</html>'
    (root / 'raw' / 'a.html').write_bytes(page)
    url = nppa.SOURCE_URL + 'gcwlyxspxx/202001/a.html'
    rows = [['国产网络游戏', str(i), f'游戏{i}', '', '出版', '运营', '文号', '', '2020年01月0%d日' % (i + 1),
             '2020-01-0%d' % (i + 1), '公告', '2020-01-02', url, 'raw/a.html'] for i in range(approvals_rows)]
    (root / 'structured' / 'approvals.csv').write_bytes(table(nppa.TABLES['approvals'][2], rows))
    for key in ('changes', 'revocations'):
        (root / 'structured' / f'{key}.csv').write_bytes(table(nppa.TABLES[key][2], []))
    if tamper:
        (root / 'raw' / 'a.html').write_bytes(b'<html>x</html>')
    note = '按类：{"国产网络游戏审批信息": 2, "游戏审批变更信息": 0, "游戏审批撤销信息": 0}；'
    manifest = {'material_id': nppa.MATERIAL_ID, 'name': '国家新闻出版署 网络游戏审批信息', 'items': 1, 'note': note,
                'files': [{'path': 'raw/a.html', 'sha256': sha(page), 'size': len(page)}],
                'gaps': [{'key': 'a', 'url': GAP_URL, 'reason': 'header only'}]}
    data = json.dumps(manifest).encode()
    path = root / 'manifests' / f'{sha(data)}.json'
    path.write_bytes(data)
    return path, sha(data)


def test_build_puts_public_tables_under_data_and_metadata_at_root(tmp_path):
    built = nppa.build(*staging(tmp_path))
    assert built['status'] == 'partial' and built['pending'][0].startswith(GAP_URL)
    assert sorted(p for p in built['files'] if p.startswith('data/')) == sorted(
        ['data/游戏审批信息.csv', 'data/游戏审批变更信息.csv', 'data/游戏审批撤销信息.csv', 'data/使用说明.md'])
    assert {'schema.json', 'provenance.json'} <= set(built['files'])
    text = built['files']['data/游戏审批信息.csv'].decode('utf-8-sig')
    assert text.splitlines()[0].split(',')[-1] == '公告URL' and 'raw/a.html' not in text
    readme = built['files']['data/使用说明.md'].decode()
    assert '2020-01-01 至 2020-01-02' in readme and '2022 年 8 月' in readme and '不是游戏原批准时间' in readme
    result = finalize.write_package(tmp_path / 'final', nppa.MATERIAL_ID, built['files'], records=built['records'],
                                    scope=built['scope'], status=built['status'], source=built['source'],
                                    pending=built['pending'])
    assert result['status'] == 'verified' and result['package_status'] == 'partial'


def test_build_rejects_count_mismatch_and_changed_raw(tmp_path):
    with pytest.raises(ValueError, match='row counts'):
        nppa.build(*staging(tmp_path / 'a', approvals_rows=3))
    with pytest.raises(ValueError, match='raw page changed'):
        nppa.build(*staging(tmp_path / 'b', tamper=True))


def read(name):
    return list(csv.DictReader(io.StringIO((SAMPLE / 'data' / name).read_bytes().decode('utf-8-sig'))))


@pytest.mark.skipif(not (SAMPLE / 'manifest.json').is_file(), reason='real NPPA sample not on this device')
def test_real_sample_rows_keys_sources_dates_and_gap():
    check = finalize.verify_package(SAMPLE)
    assert check['status'] == 'verified' and check['package_status'] == 'partial'
    ap, ch, rv = read('游戏审批信息.csv'), read('游戏审批变更信息.csv'), read('游戏审批撤销信息.csv')
    assert (len(ap), len(ch), len(rv)) == (20819, 616, 20)
    assert Counter(r['类别'] for r in ap) == {'国产网络游戏': 18959, '进口网络游戏': 1779, '进口电子游戏': 81}
    for rows in (ap, ch, rv):
        assert '来源文件' not in rows[0]
        assert all(r['名称'] and r['公告URL'].startswith(nppa.SOURCE_URL) for r in rows)
        assert max(Counter((r['公告URL'], r['序号']) for r in rows).values()) == 1  # no duplicate rows
    assert all(r['批复文号'] and r['批准日期'] for r in ap)
    assert min(r['批准日期'] for r in ap) == '2009-02-13' and max(r['批准日期'] for r in ap) == '2026-09-29'
    assert not any('/202208/' in r['公告URL'] for r in ap)  # header-only month: absent, not invented
    manifest = json.loads((SAMPLE / 'manifest.json').read_text(encoding='utf-8'))
    assert any(GAP_URL in p for p in manifest['pending'])
    readme = (SAMPLE / 'data' / '使用说明.md').read_text(encoding='utf-8')
    assert '2009-02-13 至 2026-09-29' in readme and '2022 年 8 月' in readme
