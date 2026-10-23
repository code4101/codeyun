"""京东 MSOM finalizer: CSV profiling, docx field tables, synthetic staging build, formal package read-back."""

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from backend.core.skilldep_materials import finalize, msom_jd_finalizer as jd

FORMAL = Path('F:/data/m2610skilldep/数据分析/零售/京东 MSOM 2020 交易级数据集_sd-98717f83a58b')
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def sha(b):
    return hashlib.sha256(b).hexdigest()


def para(text):
    return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'


def table(rows):
    return '<w:tbl>' + ''.join('<w:tr>' + ''.join(f'<w:tc>{para(c)}</w:tc>' for c in r) + '</w:tr>'
                               for r in rows) + '</w:tbl>'


HEAD = ['Field', 'Data type', 'Description', 'Sample value']
CSVS = {
    'JD_sku_data.csv': 'sku_ID,type\ns1,1\ns2,2\n',
    'JD_user_data.csv': 'user_ID,age\nu1,26-35\nu2,U\n',
    'JD_order_data.csv': 'order_ID,user_ID,sku_ID,price\no1,u1,s1,9.5\no1,u1,s1,9.5\no2,u2,s9,-\n',
    'JD_delivery_data.csv': 'package_ID,order_ID\np1,o1\n',
    'JD_inventory_data.csv': 'dc_ID,sku_ID,date\n1,s1,2018-03-01\n',
    'JD_network_data.csv': 'region_ID,dc_ID\n1,1\n',
}


def docx():
    body = ''
    for n, (name, text) in enumerate(CSVS.items(), 1):
        t = jd.TABLES[name][0]
        cols = text.split('\n')[0].split(',')
        body += para(f'Table: {t}') + table([HEAD] + [[c, 'string', f'{c} desc', 'x'] for c in cols])
        body += para(f'Table {n}: Description of the {t} table')
    body += table([HEAD, ['user_ID', 'string', 'clicker', 'u1']]) + para('Table 7: Description of the clicks table')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('word/document.xml', f'<w:document {W}><w:body>{body}</w:body></w:document>')
        z.writestr('word/_rels/document.xml.rels',
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        z.writestr('word/media/image1.png', b'PNG')
    return buf.getvalue()


def staging(tmp_path, tamper=False):
    root = tmp_path / 'stage'
    root.mkdir(parents=True)
    blobs = {k: v.encode() for k, v in CSVS.items()} | {jd.DOCX: docx()}
    blobs |= {n: n.encode() for n in jd.ORIGINALS if n != jd.DOCX}
    files = []
    for name, b in blobs.items():
        (root / name).write_bytes(b)
        files.append({'path': name, 'size_bytes': len(b), 'sha256': sha(b)})
    if tamper:
        (root / 'JD_sku_data.csv').write_bytes(b'sku_ID,type\nzz,1\n')
    listing = [{'path': f['path'], 'size': f['size_bytes']} for f in files] + [{'path': 'JD_click_data.csv', 'size': 9}]
    m = json.dumps({'material_id': jd.MATERIAL_ID, 'files': files, 'listing': listing}).encode()
    (root / 'manifest.json').write_bytes(m)
    return root / 'manifest.json', sha(m)


def test_profile_csv_counts_tokens_types_and_ranges():
    p = jd.profile_csv(b'a,b,c\n1,x,2018-03-01\n-,U,2018-03-02\n3,,\n')
    a, b, c = p['columns']
    assert p['rows'] == 3 and a['tokens']['-'] == 1 and (a['min'], a['max']) == (1.0, 3.0) and jd._type(a) == 'int'
    assert b['tokens']['U'] == 1 and b['tokens'][''] == 1 and jd._type(c) == 'date'
    with pytest.raises(ValueError, match='fields'):
        jd.profile_csv(b'a,b\n1\n')


def test_build_keeps_tables_byte_identical_and_reports_gap(tmp_path):
    built = jd.build(*staging(tmp_path))
    files, scope = built['files'], built['scope']
    assert built['status'] == 'partial' and 'JD_click_data.csv' in built['pending'][0]
    assert all(files[f'data/{n}'] == v.encode() for n, v in CSVS.items())
    assert scope['unique_keys']['JD_order_data.csv']['order_ID+sku_ID'] == {'unique': False, 'distinct': 2, 'rows': 3}
    assert scope['exact_duplicate_rows']['JD_order_data.csv'] == 1
    fk = {f['child']: f for f in scope['foreign_keys']}
    assert fk['JD_order_data.csv.sku_ID'] == {'child': 'JD_order_data.csv.sku_ID', 'parent': 'JD_sku_data.csv.sku_ID',
                                              'distinct': 2, 'found_in_parent': 1}
    rows = list(csv.reader(io.StringIO(files['data/数据字典.csv'].decode('utf-8-sig'))))
    assert ['clicks', 'user_ID', 'string', 'clicker', 'u1', 'Table 7', '2'] in rows
    assert files['data/说明文档图片/image1.png'] == b'PNG' and 'originals/gitattributes' in files
    assert 'Table 1: Description of the skus table' in files['data/说明文档正文.md'].decode()
    r = finalize.write_package(tmp_path / 'pkg', jd.MATERIAL_ID, files, records=built['records'], scope=scope,
                               status=built['status'], source=built['source'], pending=built['pending'])
    assert r['status'] == 'verified' and r['records_total'] == 10
    with pytest.raises(ValueError, match='differs from manifest'):
        jd.build(*staging(tmp_path / 'b', tamper=True))


@pytest.mark.skipif(not (FORMAL / 'manifest.json').is_file(), reason='formal package not on this device')
def test_formal_package():
    check = finalize.verify_package(FORMAL)
    assert check['status'] == 'verified' and check['package_status'] == 'partial' and check['records_total'] == 1468519
    m = json.loads((FORMAL / 'manifest.json').read_text(encoding='utf-8'))
    assert m['scope']['gaps'][0]['file'] == 'JD_click_data.csv' and m['scope']['paper_images'] == 14
