"""Final package writer, layout and both finalizers on synthetic inputs (no staging, no F:)."""

import io
import json
import zipfile

import openpyxl
import pytest

from backend.core.skilldep_materials import finalize, mathdial_finalizer as md, safe_finalizer as sf
from backend.core.skilldep_materials import storage_layout as layout


def test_layout_paths_and_legacy_mapping():
    assert layout.relative_dir('EDU', '学生学习与AI辅导', 'A/B', 'sd-0123456789ab') ==         '教育/学生学习与AI辅导/A_B_sd-0123456789ab'
    assert layout.cloud_from_legacy('/data/m2610skilldependency/sources/法律/a/b') == '/data/m2610skilldep/法律/a/b'
    assert layout.cloud_from_legacy('/other') is None
    with pytest.raises(ValueError):
        layout.relative_dir('XX', 'x', 'x', 'sd-0123456789ab')


def naming_file(tmp_path, **override):
    rec = {'material_id': 'sd-0123456789ab', 'domain': 'EDU', 'label': 'EDU2', 'name': 'M', 'via': 'crawl',
           'desired_local_dir': 'F:/data/m2610skilldep/教育/学生学习与AI辅导/M_sd-0123456789ab',
           'desired_cloud_dir': '/data/m2610skilldep/教育/学生学习与AI辅导/M_sd-0123456789ab', 'error': None}
    path = tmp_path / 'naming.json'
    path.write_text(json.dumps({'schema_id': layout.NAMING_SCHEMA, 'count': 1, 'items': [rec | override]}),
                    encoding='utf-8')
    return layout.load_naming(path)


def test_resolve_uses_typed_naming_and_fails_instead_of_placeholder(tmp_path):
    where = layout.resolve('sd-0123456789ab', naming_file(tmp_path), root=tmp_path / 'final')
    assert where['local_dir'] == tmp_path / 'final' / '教育' / '学生学习与AI辅导' / 'M_sd-0123456789ab'
    assert where['subdomain'] == '学生学习与AI辅导' and len(where['naming_sha256']) == 64
    with pytest.raises(LookupError):
        layout.resolve('sd-ffffffffffff', naming_file(tmp_path))
    with pytest.raises(LookupError):
        layout.resolve('sd-0123456789ab', naming_file(tmp_path, error='no label'))
    bad = 'F:/data/m2610skilldep/金融/x/M_sd-0123456789ab'  # domain mismatch
    with pytest.raises(ValueError):
        layout.resolve('sd-0123456789ab', naming_file(tmp_path, desired_local_dir=bad))


def package(tmp_path, data=b'{"a":1}\n'):
    files = {'data/x.jsonl': data, 'schema.json': b'{}', 'provenance.json': b'{}'}
    return finalize.write_package(tmp_path / 'p', 'sd-0123456789ab', files, records={'data/x.jsonl': 1},
                                  scope={'kind': 'full'}, status='finalized', source={})


def test_package_atomic_idempotent_and_never_overwrites(tmp_path):
    first = package(tmp_path)
    assert first['status'] == 'verified' and first['records_total'] == 1
    assert package(tmp_path)['status'] == 'unchanged'
    assert package(tmp_path, b'{"a":2}\n')['status'] == 'conflict'
    assert (tmp_path / 'p' / 'data' / 'x.jsonl').read_bytes() == b'{"a":1}\n'
    rev = finalize.revise_manifest(tmp_path / 'p', {'source': {'x': 1}}, reason='test')
    assert rev['revision'] == 2 and (tmp_path / 'p' / 'manifest-history').is_dir()
    assert finalize.verify_package(tmp_path / 'p')['status'] == 'verified'
    with pytest.raises(ValueError):
        finalize.revise_manifest(tmp_path / 'p', {'files': []}, reason='x')
    (tmp_path / 'p' / 'data' / 'x.jsonl').write_bytes(b'tampered')
    assert finalize.verify_package(tmp_path / 'p')['bad'] == ['data/x.jsonl']


def test_safe_parse_keeps_published_text_and_marks_gaps():
    rows = [['日期', '美元', '泰铢'], ['2025-12-31', '702.88', ''], ['1994-01-01', '870.0', '-']]
    cur, by_date = sf.parse_part(rows, 'history')
    assert cur == ['美元', '泰铢']
    assert by_date['2025-12-31'] == [('702.88', '702.88', 'published'), (None, None, 'blank_in_source')]
    assert by_date['1994-01-01'] == [('870.0', '870', 'published'), (None, None, 'not_published')]
    with pytest.raises(ValueError):
        sf.parse_part(rows + [['1994-01-01', '1', '1']], 'history')  # duplicate date
    with pytest.raises(ValueError):
        sf.parse_part([['日期', '美元'], ['2025-01-01', 'abc']], 'history')


def test_safe_build_end_to_end(tmp_path):
    base = tmp_path / 'sd'
    wb = openpyxl.Workbook()
    for r in [['日期', '美元', '欧元'], ['2025-12-31', '702.88', '823.55'], ['2025-12-30', '703.48', '-']]:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    xlsx = buf.getvalue()
    parts = []
    for key in sf.PARTS:  # second part reuses the xlsx format with a later date
        data = xlsx if key == sf.PARTS[0] else _xlsx([['日期', '美元', '欧元'], ['2026-01-05', '702.3', '820.27']])
        p = base / 'parts' / key / 'f.raw'
        p.parent.mkdir(parents=True)
        p.write_bytes(data)
        parts.append({'key': key, 'path': f'parts/{key}/f.raw', 'sha256': finalize.sha256_bytes(data),
                      'format': 'xlsx', 'url': 'u', 'label': 'l', 'fetched_at': 't'})
    mf = base / 'manifests' / 'm.json'
    mf.parent.mkdir()
    mf.write_text(json.dumps({'complete': True, 'files': parts}), encoding='utf-8')
    built = sf.build(mf, finalize.sha256_file(mf))
    assert built['scope']['dates'] == 3 and built['scope']['cells'] == 6
    lines = built['files']['data/rates_long.jsonl'].decode().splitlines()
    assert json.loads(lines[0])['date'] == '2025-12-30' and len(lines) == 6
    assert '2025-12-30,703.48,-' in built['files']['data/rates_wide.csv'].decode()
    with pytest.raises(ValueError):
        sf.build(mf, '0' * 64)


def _xlsx(rows):
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_mathdial_turns_and_dedup(tmp_path):
    turns, empty = md.parse_turns('Teacher: (probing)Why?|EOM|Steven: Because.|EOM|Teacher: (20-5) ok|EOM|odd|EOM|')
    assert [t['speaker_role'] for t in turns] == ['teacher', 'student', 'teacher', 'unknown']
    assert turns[0]['teacher_move'] == 'probing' and turns[0]['text'] == 'Why?'
    assert turns[2]['teacher_move'] is None and turns[2]['text'] == '(20-5) ok'
    assert empty == 1

    def rec(qid, conv):
        return {k: None for k in md.FIELDS} | {'qid': qid, 'scenario': 1, 'conversation': conv}
    train = [rec(1, 'Teacher: (focus)a'), rec(1, 'Teacher: (focus)a'), rec(1, 'Teacher: (focus)b')]
    test = [rec(2, 'Teacher: (focus)c')]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for split, rows in (('train', train), ('test', test)):
            z.writestr(f'{md.PREFIX}{split}.jsonl', ''.join(json.dumps(r) + '\n' for r in rows))
            z.writestr(f'{md.PREFIX}{split}.csv', 'conversation\n' + ''.join(f'"{r["conversation"]}"\n' for r in rows))
        z.writestr('mathdial-HEAD/README.md', 'x')
    zp = tmp_path / 'm.zip'
    zp.write_bytes(buf.getvalue())
    built = md.build(zp, finalize.sha256_bytes(buf.getvalue()))
    scope = built['scope']
    assert scope['dialogues'] == 3 and scope['exact_duplicates_removed'] == 1
    assert scope['qid_with_multiple_dialogues'] == 1
    first = json.loads(built['files']['data/dialogues.jsonl'].decode().splitlines()[0])
    assert first['dialogue_id'] == 'mathdial-train-0001' and 'self_correctness' in first


def test_package_rejects_traversal_reserved_and_metadata_only(tmp_path):
    base = {'schema.json': b'{}', 'provenance.json': b'{}'}
    for bad in ('../evil.jsonl', '/abs.jsonl', 'C:/x.jsonl', 'data/../../x', 'data\\x.jsonl', 'manifest.json',
                'manifest-history/r1.json', '.tmp-x/a', 'data/./x'):
        with pytest.raises(ValueError):
            finalize.write_package(tmp_path / 'p', 'sd-0123456789ab', {**base, bad: b'x\n'}, records={bad: 1},
                                   scope={}, status='finalized', source={})
    with pytest.raises(ValueError):  # schema/provenance only is not a finalized dataset
        finalize.write_package(tmp_path / 'p', 'sd-0123456789ab', base, records={}, scope={},
                               status='finalized', source={})
    with pytest.raises(ValueError):  # record counts must name packaged files
        finalize.write_package(tmp_path / 'p', 'sd-0123456789ab', {**base, 'data/x.jsonl': b'1\n'},
                               records={'data/x.jsonl': 1, 'data/y.jsonl': 1}, scope={}, status='finalized', source={})
    assert not (tmp_path / 'p').exists() and not list(tmp_path.glob('.tmp-*'))
    assert not (tmp_path.parent / 'evil.jsonl').exists()


def test_existing_package_is_reverified_before_unchanged(tmp_path):
    assert package(tmp_path)['status'] == 'verified'
    data = tmp_path / 'p' / 'data' / 'x.jsonl'
    data.write_bytes(b'{"a":9}\n')  # same size, different content
    result = package(tmp_path)
    assert result['status'] == 'corrupt' and result['bad'] == ['data/x.jsonl']
    assert data.read_bytes() == b'{"a":9}\n'  # left untouched for inspection
    data.write_bytes(b'{"a":1}\n')
    manifest = tmp_path / 'p' / 'manifest.json'
    m = json.loads(manifest.read_text(encoding='utf-8'))
    m['files'][0]['records'] = 5  # fingerprint no longer covers the listed files
    manifest.write_text(json.dumps(m), encoding='utf-8')
    assert package(tmp_path)['reason'] == 'fingerprint_mismatch'
    del m['content_fingerprint']
    manifest.write_text(json.dumps(m), encoding='utf-8')
    assert package(tmp_path)['reason'] == 'manifest_incomplete'
