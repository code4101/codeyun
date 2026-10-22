"""Final package writer, layout and both finalizers on synthetic inputs (no staging, no F:)."""

import csv
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

    train = [md_rec(1, 'Teacher: (focus)a'), md_rec(1, 'Teacher: (focus)a'), md_rec(1, 'Teacher: (focus)b')]
    test = [md_rec(2, 'Teacher: (focus)c')]
    built = md.build(*md_zip(tmp_path, train, test))
    scope = built['scope']
    assert scope['dialogues'] == 3 and scope['exact_duplicates_removed'] == 1
    assert scope['qid_with_multiple_dialogues'] == 1
    first = next(md_rows(built))
    assert first['dialogue_id'] == 'mathdial-train-0001' and 'self_correctness' in first


def md_rec(qid, conv, **kw):
    return {k: None for k in md.FIELDS} | {'qid': qid, 'scenario': 1, 'conversation': conv} | kw


def md_zip(tmp_path, train, test):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for split, rows in (('train', train), ('test', test)):
            z.writestr(f'{md.PREFIX}{split}.jsonl', ''.join(json.dumps(r) + '\n' for r in rows))
            z.writestr(f'{md.PREFIX}{split}.csv', 'conversation\n' + ''.join(f'"{r["conversation"]}"\n' for r in rows))
        z.writestr('mathdial-HEAD/README.md', 'x')
    zp = tmp_path / 'm.zip'
    zp.write_bytes(buf.getvalue())
    return zp, finalize.sha256_bytes(buf.getvalue())


def md_rows(built):
    return csv.DictReader(io.StringIO(built['files'][md.DATA_FILE].decode('utf-8-sig'), newline=''))


def md_sample(tmp_path):
    full = {'question': 'q, "x"\nline2', 'ground_truth': 'g', 'student_incorrect_solution': 's',
            'student_profile': 'p', 'teacher_described_confusion': 'c', 'self-correctness': 'Yes',
            'self-typical-confusion': 4.0, 'self-typical-interactions': None}
    train = [md_rec(7, 'Teacher: (probing)Why?|EOM|Kayla: Because, 3.|EOM|Teacher: (telling)', **full),
             md_rec(7, 'Teacher: (focus)a|EOM|odd'), md_rec(7, 'Teacher: (focus)a|EOM|odd')]
    test = [md_rec(8, 'Teacher: (focus)a|EOM|odd')]  # same conversation as a train dialogue
    return md_zip(tmp_path, train, test), train, test


def test_mathdial_single_csv_keeps_every_source_field_and_rebuilds_turns(tmp_path):
    (zp, zsha), train, test = md_sample(tmp_path)
    built = md.build(zp, zsha)
    assert sorted(p for p in built['files'] if p.startswith('data/')) == [md.DATA_FILE]
    assert built['records'] == {md.DATA_FILE: 2} and built['scope']['turns'] == 5
    assert built['scope']['exact_duplicates_removed'] == 1 and built['scope']['conversation_duplicates_removed'] == 1
    assert built['extra']['data_quality']['public_data_files'] == [md.DATA_FILE]
    assert built['extra']['data_quality']['empty_text_turns'] == 1
    schema = json.loads(built['files']['schema.json'])
    assert list(schema['files']) == [md.DATA_FILE]
    rows = list(md_rows(built))
    assert [r['dialogue_id'] for r in rows] == ['mathdial-train-0001', 'mathdial-train-0002']
    for row, src in zip(rows, train):  # every upstream field verbatim (null -> empty cell)
        for k in md.FIELDS:
            col = 'conversation_raw' if k == 'conversation' else md.snake(k)
            want = src[k]
            assert row[col] == ('' if want is None else str(want)), k
        assert row['turn_count'] == str(len(md.parse_turns(src['conversation'])[0]))
    turns = md.turns_from_csv(built['files'][md.DATA_FILE])
    assert len(turns) == 5 and turns[1] == {'dialogue_id': 'mathdial-train-0001', 'turn_index': 1,
                                            'speaker_role': 'student', 'speaker_label': 'Kayla',
                                            'teacher_move': None, 'text': 'Because, 3.'}
    assert md.build(zp, zsha)['files'] == built['files']  # deterministic re-run


def test_mathdial_correct_existing_package_backs_up_and_is_idempotent(tmp_path, monkeypatch):
    (zp, zsha), _, _ = md_sample(tmp_path)
    built = md.build(zp, zsha)
    legacy = built['files'] | {'data/dialogues.jsonl': b'{"a":1}\n{"a":2}\n', 'data/turns.csv': b'x\n1\n'}
    target, backup = tmp_path / 'final' / 'pkg', tmp_path / 'backup'
    finalize.write_package(target, md.MATERIAL_ID, legacy, scope={}, status='finalized', source={},
                           records=built['records'] | {'data/dialogues.jsonl': 2, 'data/turns.csv': 5})
    before = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob('*') if p.is_file()}
    kw = dict(records=built['records'], scope=built['scope'], status='finalized', source=built['source'],
              extra=built['extra'], reason='single csv', backup_dir=backup, preserve=md.PRESERVE)

    real_replace = finalize.os.replace

    def fail_on_manifest(src, dst):
        if str(dst).endswith('manifest.json'):
            raise OSError('disk full')
        return real_replace(src, dst)
    monkeypatch.setattr(finalize.os, 'replace', fail_on_manifest)
    with pytest.raises(OSError):
        finalize.rewrite_package(target, built['files'], **kw)
    monkeypatch.setattr(finalize.os, 'replace', real_replace)
    after_fail = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob('*') if p.is_file()}
    assert after_fail == before and finalize.verify_package(target)['status'] == 'verified'

    with pytest.raises(ValueError, match='preserved'):  # data CSV must keep its bytes
        finalize.rewrite_package(target, built['files'] | {md.DATA_FILE: b'\xef\xbb\xbfx\n1\n'}, **kw)
    assert {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob('*') if p.is_file()} == before

    res = finalize.rewrite_package(target, built['files'], **kw)
    assert res['status'] == 'rewritten' and res['removed'] == ['data/dialogues.jsonl', 'data/turns.csv']
    assert set(res['kept']) == {md.DATA_FILE, 'originals/mathdial-HEAD.zip', 'schema.json', 'provenance.json'}
    assert sorted(p.name for p in (target / 'data').iterdir()) == ['dialogues.csv']
    assert (target / md.DATA_FILE).read_bytes() == before[md.DATA_FILE]
    check = finalize.verify_package(target)
    assert check['status'] == 'verified' and check['records_total'] == 2
    manifest = json.loads((target / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['manifest_revision'] == 2 and manifest['data_quality']['public_data_files'] == [md.DATA_FILE]
    saved = backup / res['backup'].rsplit('/', 1)[1]
    for rel in ('data/dialogues.jsonl', 'data/turns.csv', 'manifest.json'):
        assert (saved / rel).read_bytes() == before[rel]
    assert (target / 'manifest-history' / f"r1-{res['previous_manifest_sha256']}.json").read_bytes() == \
        before['manifest.json']
    again = finalize.rewrite_package(target, built['files'], **kw)
    assert again['status'] == 'unchanged' and again['unlisted'] == []

    record = md.finalization_record(target, {'source_acquisition_device_id': 'dev'})
    files = [p for p in target.rglob('*') if p.is_file()]
    assert record['file_count'] == len(files) == 6 and record['size_bytes'] == sum(p.stat().st_size for p in files)
    assert record['manifest_sha256'] == finalize.sha256_file(target / 'manifest.json')
    assert record['finished_at'].endswith('Z')

    class Hub:
        items = [{'material_id': md.MATERIAL_ID, 'root': 'local', 'source_acquisition_device_id': 'dev'}]

        def finalizations(self):
            return {'items': self.items}

        def put_finalization(self, mid, root, rec):
            self.items = [rec | {'material_id': mid, 'root': root}]
            return {'ok': True}
    ack = md.register(Hub(), target)
    assert ack['ack'] and ack['read_back']['source_acquisition_device_id'] == 'dev'


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
