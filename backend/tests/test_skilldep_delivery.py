"""NPPA 游戏审批 package: page parser, synthetic staging build, and a read-back of the formal package when present."""

import csv
import gzip
import hashlib
import io
import json
from collections import Counter
from pathlib import Path

import pytest

from backend.core.skilldep_materials import finalize, nppa_finalizer as nppa

GAP_URL = nppa.SOURCE_URL + 'gcwlyxspxx/202208/t20220801_442173.html'
FORMAL = Path('F:/data/m2610skilldep/游戏/跨二级通用/国家新闻出版署 网络游戏审批信息_sd-b05c6a963661')


def sha(b):
    return hashlib.sha256(b).hexdigest()


def script(value):
    return (f"<script language=\"javascript\">\n    var _sblb = '{value}';\n    if (_sblb == '原') {{\n"
            "        document.write(\"<td align='center'>&nbsp;</td>\");\n    }\n    else {\n"
            "        document.write(\"<td align='center'>\" + _sblb + \"</td>\");\n    }\n</script>")


HEAD = ['序号', '名称', '申报类别', '出版单位', '运营单位', '批复文号', '出版物号', '批准时间']


def page(rows, header=HEAD, extra=''):
    head = ''.join(f'<td style="x">{h}</td>' for h in header)
    body = ''.join(f'<tr height="60" class="item">{r}</tr>' for r in rows)
    return (f'<html><head><title>国家新闻出版署</title></head><body><div class="nav">导航</div>'
            f'<div class="m3pageEdit"><table class="trStyle"><tr height="40">{head}</tr>{extra}<tbody>{body}'
            f'</tbody></table></div><div>页脚</div></body></html>')


def game_row(i, sblb='移动'):
    return (f'<td align="center">{i}</td><td align="center">游戏{i} </td>{script(sblb)}'
            f'<td>出版</td><td>运营 </td><td>文号{i}</td><td>ISBN {i}</td><td>2020年01月0{i}日 </td>')


def test_render_sblb_template_and_rejects_other_scripts():
    assert nppa.render_sblb(script('移动-休闲益智')[30:-9]) == ('移动-休闲益智', '移动-休闲益智')
    assert nppa.render_sblb(script('原')[30:-9]) == ('', '原')
    assert nppa.render_sblb(script('网页    客户端 ')[30:-9]) == ('网页    客户端 ', '网页    客户端 ')
    assert nppa.render_sblb(script('移动\n客户端')[30:-9])[1] == '移动\n客户端'
    assert nppa.script_breaks('移动\n客户端') and not nppa.script_breaks('移动 客户端')
    assert nppa.clean(' 新广出审[2017]34      号\r\n\xa0') == '新广出审[2017]34 号'
    with pytest.raises(ValueError, match='unknown inline script'):
        nppa.render_sblb("var _sblb = 'x'; alert(1);")


def test_parse_page_keeps_script_cells_positions_and_comments():
    p = nppa.parse_page(page([game_row(1), game_row(2, '原')], extra='<!--td>旧列</td-->'))
    assert p['header'] == HEAD and [r['row'] for r in p['rows']] == [1, 2]
    cells = p['rows'][0]['cells']
    assert [nppa.clean(c['text']) for c in cells] == ['1', '游戏1', '移动', '出版', '运营', '文号1', 'ISBN 1', '2020年01月01日']
    assert cells[2]['source'] == 'script' and cells[2]['script_value'] == '移动' and cells[1]['text'] == '游戏1 '
    assert p['rows'][1]['cells'][2]['text'] == '' and p['comments'][0]['text'] == 'td>旧列</td'
    with pytest.raises(ValueError, match='expected one body table'):
        nppa.parse_page('<html><div class="m3pageEdit"></div></html>')
    with pytest.raises(ValueError, match='text outside cells'):
        nppa.parse_page(page([game_row(1)]).replace('<tbody>', '<tbody>散文'))


def staging(tmp_path, *, tamper=False, unlisted_gap=False):
    root = tmp_path / 'stage'
    for d in ('raw', 'meta', 'lists', 'manifests'):
        (root / d).mkdir(parents=True)
    pages = {'t20200102_1': ('gcwlyxspxx/202001/', page([game_row(1), game_row(2)]).encode(), False),
             't20220801_442173': ('gcwlyxspxx/202208/', page([]).encode(), True),
             't20200103_2': ('yxspcxxx/202001/', page(['<td>1</td><td>名</td>' + script('网页') +
                                                       '<td>出</td><td>运</td><td>撤销于2020年</td><td>号</td>'
                                                       '<td>ISBN</td><td>2020年06月29日</td>'],
                                                      header=['序号', '名称', '申报类别', '出版单位', '运营单位',
                                                              '撤销信息', '文号', '出版物号', '批准时间']).encode(), False)}
    files, links = [], ''
    for key, (folder, html_bytes, gz) in pages.items():
        name = key + ('.html.gz' if gz else '.html')
        stored = gzip.compress(html_bytes) if gz else html_bytes
        (root / 'raw' / name).write_bytes(stored)
        url = nppa.SOURCE_URL + folder + key + '.html'
        links += f'<a href="./{folder}{key}.html">x</a>'
        raw = {'path': (root / 'raw' / name).as_posix(), 'size': len(stored), 'sha256': sha(stored)}
        if gz:
            raw |= {'encoding': 'gzip', 'original_sha256': sha(html_bytes)}
        meta = json.dumps({'key': key, 'url': url, 'final_url': url, 'list_title': '标题' + key,
                           'list_date': '2020-01-02', 'fetched_at': '2026-10-20T18:06:42+00:00', 'raw': raw}).encode()
        (root / 'meta' / f'{key}.json').write_bytes(meta)
        files += [{'path': f'raw/{name}', 'sha256': sha(stored), 'size': len(stored)},
                  {'path': f'meta/{key}.json', 'sha256': sha(meta), 'size': len(meta)}]
    listing = links.encode()
    (root / 'lists' / 'page_000.html').write_bytes(listing)
    files.append({'path': 'lists/page_000.html', 'sha256': sha(listing), 'size': len(listing)})
    if tamper:
        (root / 'raw' / 't20200102_1.html').write_bytes(b'<html>x</html>')
    gaps = [] if unlisted_gap else [{'key': 't20220801_442173', 'url': GAP_URL, 'reason': 'header only'}]
    manifest = {'material_id': nppa.MATERIAL_ID, 'name': '国家新闻出版署 网络游戏审批信息', 'files': files, 'gaps': gaps}
    data = json.dumps(manifest).encode()
    path = root / 'manifests' / f'{sha(data)}.json'
    path.write_bytes(data)
    return path, sha(data)


def rows_of(data):
    return list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))


def test_build_full_tables_provenance_originals_and_gap(tmp_path):
    built = nppa.build(*staging(tmp_path))
    assert built['status'] == 'partial' and built['pending'][0].startswith(GAP_URL)
    files = built['files']
    ap = rows_of(files['data/游戏审批信息.csv'])
    assert [(r['类别'], r['名称'], r['申报类别'], r['批准日期'], r['公告键'], r['表内行号']) for r in ap] == [
        ('国产网络游戏', '游戏1', '移动', '2020-01-01', 't20200102_1', '1'),
        ('国产网络游戏', '游戏2', '移动', '2020-01-02', 't20200102_1', '2')]
    rv = rows_of(files['data/游戏审批撤销信息.csv'])
    assert rv[0]['申报类别'] == '网页' and rv[0]['撤销信息'] == '撤销于2020年'
    index = {r['公告键']: r for r in rows_of(files['data/公告清单.csv'])}
    assert index['t20220801_442173']['状态'] == '仅表头（缺口）' and index['t20200102_1']['申报类别脚本格数'] == '2'
    cells = [json.loads(line) for line in files['data/原表单元格.jsonl'].decode().splitlines()]
    assert cells[0]['单元格'][2] | {'位置': None} == {'列': '申报类别', '值': '移动', '来源': 'script', '位置': None,
                                                     '脚本值': '移动'}
    assert files['originals/raw/t20220801_442173.html.gz'] == (tmp_path / 'stage/raw/t20220801_442173.html.gz').read_bytes()
    assert 'originals/lists/page_000.html' in files
    assert built['scope']['script_rendered_cells'] == 3 and built['scope']['header_only_pages'] == ['t20220801_442173']
    result = finalize.write_package(tmp_path / 'final', nppa.MATERIAL_ID, files, records=built['records'],
                                    scope=built['scope'], status=built['status'], source=built['source'],
                                    pending=built['pending'])
    assert result['status'] == 'verified' and result['package_status'] == 'partial' and result['records_total'] == 3


def test_build_rejects_changed_raw_and_unrecorded_header_only_page(tmp_path):
    with pytest.raises(ValueError, match='differs from manifest'):
        nppa.build(*staging(tmp_path / 'a', tamper=True))
    with pytest.raises(ValueError, match='header-only page not recorded'):
        nppa.build(*staging(tmp_path / 'b', unlisted_gap=True))


@pytest.mark.skipif(not (FORMAL / 'data' / '原表单元格.jsonl').is_file(), reason='formal NPPA package not on this device')
def test_formal_package_rows_script_cells_and_gap():
    check = finalize.verify_package(FORMAL)
    assert check['status'] == 'verified' and check['package_status'] == 'partial'
    read = lambda n: rows_of((FORMAL / 'data' / n).read_bytes())  # noqa: E731
    ap, ch, rv = read('游戏审批信息.csv'), read('游戏审批变更信息.csv'), read('游戏审批撤销信息.csv')
    assert (len(ap), len(ch), len(rv)) == (20819, 616, 20)
    assert Counter(r['类别'] for r in ap) == {'国产网络游戏': 18959, '进口网络游戏': 1779, '进口电子游戏': 81}
    assert sum(1 for r in ap if r['申报类别']) == 15104  # the other 5634 script cells are '原' -> shown empty
    for rows in (ap, ch, rv):
        assert max(Counter((r['公告键'], r['表内行号']) for r in rows).values()) == 1
    assert not any('/202208/' in r['公告URL'] for r in ap)
    manifest = json.loads((FORMAL / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['scope']['script_rendered_cells'] == 20738 and any(GAP_URL in p for p in manifest['pending'])
