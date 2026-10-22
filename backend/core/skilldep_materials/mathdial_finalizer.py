"""Finalize MathDial 辅导对话 (sd-8858f4aff1df) into a cleaned package.

Input is the verified staging archive ``mathdial-HEAD.zip`` (SHA-256 from the
catalog).  All records of ``data/train.jsonl`` and ``data/test.jsonl`` are
parsed (the upstream README states 2861 conversations); ``example.jsonl`` is a
sample subset and is not data.  The CSV copies are only used as a cross-check.

Cleaning rules (all source fields kept):

* Keys normalized to snake_case (``self-correctness`` -> ``self_correctness``);
  values unchanged, ``null`` kept (empty CSV cell); ``split`` and source line added.
* ``conversation`` kept verbatim as ``conversation_raw``; ``parse_turns`` splits it
  on ``|EOM|`` for ``turn_count`` and statistics: ``Teacher: (move)text`` -> teacher
  turn with move label when it is one of the dataset's moves
  (focus/generic/probing/telling); any other ``Name: text`` -> student turn with
  that speaker label; segments without a speaker prefix are ``speaker_role=unknown``
  and counted.  Turns are not shipped as a separate table: ``turns_from_csv``
  rebuilds them from ``conversation_raw``.
* Duplicates: exact record and conversation-text duplicates are removed and
  counted; repeated ``qid`` is legitimate (several dialogues per problem).

Output: one public data file, ``data/dialogues.csv`` (one row per dialogue, all
source fields, ``conversation_raw``, ``turn_count``), plus internal
``originals/mathdial-HEAD.zip`` evidence and schema/provenance/manifest.

CLI::

    uv run python -m backend.core.skilldep_materials.mathdial_finalizer [--root F:/data/m2610skilldep]
    # bring an already finalized package to this file set (old files backed up outside it), then register:
    uv run python -m backend.core.skilldep_materials.mathdial_finalizer --correct-existing --backup-dir DIR --register
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

from . import storage_layout as layout
from .finalize import (layout_source, copy_original, csv_bytes, rewrite_package, sha256_bytes, sha256_file,
                       verify_package, write_package)

MATERIAL_ID = 'sd-8858f4aff1df'
PREFIX = 'mathdial-HEAD/data/'
SPLITS = ('train', 'test')
FIELDS = ('qid', 'scenario', 'question', 'ground_truth', 'student_incorrect_solution', 'student_profile',
          'teacher_described_confusion', 'self-correctness', 'self-typical-confusion', 'self-typical-interactions',
          'conversation')
MOVES = ('focus', 'generic', 'probing', 'telling')
EOM = '|EOM|'
_SPEAKER = re.compile(r'^([^:\n]{1,40}):[ \t]*(.*)$', re.S)
_MOVE = re.compile(r'^\((%s)\)\s*(.*)$' % '|'.join(MOVES), re.S)
DATA_FILE = 'data/dialogues.csv'
PRESERVE = (DATA_FILE, 'originals/mathdial-HEAD.zip')


def snake(key: str) -> str:
    return key.replace('-', '_')


def parse_turns(conversation: str) -> tuple[list[dict[str, Any]], int]:
    """Turns of one conversation and the number of empty segments dropped."""
    turns, empty = [], 0
    for seg in conversation.split(EOM):
        seg = seg.strip()
        if not seg:
            empty += 1
            continue
        m = _SPEAKER.match(seg)
        if not m:
            turns.append({'speaker_role': 'unknown', 'speaker_label': None, 'teacher_move': None, 'text': seg})
            continue
        label, text = m.group(1).strip(), m.group(2)
        if label == 'Teacher':
            mv = _MOVE.match(text)
            turns.append({'speaker_role': 'teacher', 'speaker_label': label,
                          'teacher_move': mv.group(1) if mv else None, 'text': mv.group(2) if mv else text})
        else:
            turns.append({'speaker_role': 'student', 'speaker_label': label, 'teacher_move': None, 'text': text})
    return turns, empty


def turns_from_csv(data: bytes) -> list[dict[str, Any]]:
    """Rebuild the per-turn table from ``data/dialogues.csv`` bytes (``conversation_raw`` + ``parse_turns``)."""
    rows = csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''))
    return [{'dialogue_id': r['dialogue_id'], 'turn_index': i, **t}
            for r in rows for i, t in enumerate(parse_turns(r['conversation_raw'])[0])]


def build(zip_path: Path, zip_sha256: str) -> dict[str, Any]:
    data = copy_original(zip_path, zip_sha256)
    z = zipfile.ZipFile(io.BytesIO(data))
    records, seen_rec, seen_conv = [], set(), set()
    stats = {'source_lines': {}, 'csv_crosscheck': {}, 'exact_duplicates_removed': 0,
             'conversation_duplicates_removed': 0, 'empty_segments_dropped': 0,
             'turn_roles': {}, 'teacher_moves': {}, 'unlabelled_teacher_turns': 0, 'empty_text_turns': 0}
    for split in SPLITS:
        lines = z.read(f'{PREFIX}{split}.jsonl').decode('utf-8').splitlines()
        n = 0
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            n += 1
            src = json.loads(line)
            if set(src) != set(FIELDS):
                raise ValueError(f'{split}:{line_no}: unexpected fields {sorted(set(src) ^ set(FIELDS))}')
            key = json.dumps(src, sort_keys=True, ensure_ascii=False)
            if key in seen_rec:
                stats['exact_duplicates_removed'] += 1
                continue
            conv = src['conversation']
            conv_sha = sha256_bytes(conv.encode())
            if conv_sha in seen_conv:
                stats['conversation_duplicates_removed'] += 1
                continue
            seen_rec.add(key)
            seen_conv.add(conv_sha)
            turns, empty = parse_turns(conv)
            stats['empty_segments_dropped'] += empty
            for t in turns:
                stats['empty_text_turns'] += not t['text']
                stats['turn_roles'][t['speaker_role']] = stats['turn_roles'].get(t['speaker_role'], 0) + 1
                if t['speaker_role'] == 'teacher':
                    mv = t['teacher_move'] or 'none'
                    stats['teacher_moves'][mv] = stats['teacher_moves'].get(mv, 0) + 1
            rec = {'dialogue_id': f'mathdial-{split}-{line_no:04d}', 'split': split, 'source_line': line_no}
            rec.update({snake(k): src[k] for k in FIELDS if k != 'conversation'})
            rec.update({'conversation_raw': conv, 'conversation_sha256': conv_sha, 'turn_count': len(turns)})
            records.append(rec)
        stats['source_lines'][split] = n
        csv_rows = list(csv.DictReader(io.StringIO(z.read(f'{PREFIX}{split}.csv').decode('utf-8'))))
        convs = {r['conversation_raw'] for r in records if r['split'] == split}
        stats['csv_crosscheck'][split] = {'rows': len(csv_rows), 'jsonl_records': n,
                                          'conversations_equal': {r['conversation'] for r in csv_rows} == convs}
    stats['unlabelled_teacher_turns'] = stats['teacher_moves'].get('none', 0)
    qids = {}
    for r in records:
        qids[r['qid']] = qids.get(r['qid'], 0) + 1
    stats['distinct_qid'] = len(qids)
    stats['qid_with_multiple_dialogues'] = sum(c > 1 for c in qids.values())

    flat_cols = ['dialogue_id', 'split', 'source_line'] + [snake(k) for k in FIELDS if k != 'conversation'] + \
                ['turn_count', 'conversation_sha256', 'conversation_raw']
    n_turns = sum(r['turn_count'] for r in records)
    readme = z.read('mathdial-HEAD/README.md').decode('utf-8')
    schema = {
        'schema': 'codeyun.skilldep_dataset_schema/v1', 'material_id': MATERIAL_ID,
        'files': {
            DATA_FILE: {'record': 'one tutoring dialogue', 'format': 'UTF-8 CSV with BOM, empty cell = null',
                        'columns': flat_cols, 'fields': {
                'dialogue_id': 'mathdial-<split>-<source line>', 'split': 'train | test',
                'source_line': '1-based line in upstream <split>.jsonl',
                'qid': 'upstream problem id (repeats across dialogues)',
                'scenario': 'order of the problem in the collection session (1-5)',
                'question': 'math word problem', 'ground_truth': 'correct solution text',
                'student_incorrect_solution': 'LLM student incorrect solution',
                'student_profile': 'LLM student persona', 'teacher_described_confusion': 'teacher note',
                'self_correctness': 'teacher self-report, verbatim (may be null)',
                'self_typical_confusion': 'teacher rating 1-5 (number, may be null)',
                'self_typical_interactions': 'teacher rating 1-5 (number, may be null)',
                'turn_count': 'number of |EOM| turns parsed from conversation_raw',
                'conversation_sha256': 'SHA-256 of conversation_raw (UTF-8)',
                'conversation_raw': 'upstream conversation verbatim, |EOM| delimited'},
                'turns': 'not a separate file; rebuild with mathdial_finalizer.turns_from_csv -> '
                         '{dialogue_id, turn_index, speaker_role teacher|student|unknown, speaker_label, '
                         f'teacher_move {"|".join(MOVES)}|null, text}}'}},
    }
    provenance = {
        'material_id': MATERIAL_ID, 'title': 'MathDial', 'source_url': 'https://github.com/eth-nlped/mathdial',
        'paper': 'https://arxiv.org/abs/2305.14536', 'license': 'CC BY-SA 4.0 (upstream README)',
        'staging_file': str(zip_path).replace('\\', '/'), 'staging_sha256': zip_sha256,
        'upstream_readme_sha256': sha256_bytes(readme.encode()),
        'not_data': ['data/example.jsonl (sample subset)', 'code, images, output examples'],
        'stats': stats,
    }
    files = {
        DATA_FILE: csv_bytes(flat_cols, [[r[c] for c in flat_cols] for r in records]),
        'schema.json': (json.dumps(schema, ensure_ascii=False, indent=1) + '\n').encode(),
        'provenance.json': (json.dumps(provenance, ensure_ascii=False, indent=1) + '\n').encode(),
        'originals/mathdial-HEAD.zip': data,
    }
    scope = {'kind': 'full', 'dialogues': len(records), 'turns': n_turns, **stats}
    data_quality = {
        'unit_of_record': {DATA_FILE: 'dialogue'}, 'public_data_files': [DATA_FILE],
        'dialogues': len(records), 'turns': n_turns, 'turns_rebuild': 'turns_from_csv(conversation_raw)',
        'splits': {s: sum(r['split'] == s for r in records) for s in SPLITS},
        'turn_roles': stats['turn_roles'], 'unknown_speaker_turns_preserved': stats['turn_roles'].get('unknown', 0),
        'empty_text_turns': stats['empty_text_turns'], 'teacher_moves': stats['teacher_moves'],
        'exact_duplicates_removed': stats['exact_duplicates_removed'],
        'conversation_duplicates_removed': stats['conversation_duplicates_removed'],
        'distinct_qid': stats['distinct_qid'], 'csv_crosscheck': stats['csv_crosscheck'],
        'internal_evidence': ['originals/mathdial-HEAD.zip (verbatim source, not downstream data)'],
    }
    return {'files': files, 'scope': scope, 'records': {DATA_FILE: len(records)},
            'extra': {'data_quality': data_quality},
            'source': {'staging_file': provenance['staging_file'], 'staging_sha256': zip_sha256}}


def _prepare(item: dict[str, Any], naming: dict[str, Any], root: Path) -> tuple[Path, dict[str, Any], list[str]]:
    local = item['local']
    if local.get('status') != 'downloaded':
        raise ValueError('material not downloaded')
    built = build(Path(local['path']), local['sha256'])
    pending = []
    if built['scope']['turn_roles'].get('unknown'):
        pending.append(f"{built['scope']['turn_roles']['unknown']} turn segments without speaker prefix kept raw")
    if not all(c['conversations_equal'] for c in built['scope']['csv_crosscheck'].values()):
        pending.append('upstream CSV and JSONL disagree; JSONL used')
    where = layout.resolve(item['id'], naming, root)
    built['source'] |= layout_source(where)
    return Path(where['local_dir']), built, pending


def finalize(item: dict[str, Any], naming: dict[str, Any], *,
             root: Path = layout.FINAL_LOCAL_ROOT) -> dict[str, Any]:
    target, built, pending = _prepare(item, naming, root)
    return write_package(target, item['id'], built['files'], records=built['records'], scope=built['scope'],
                         status='finalized', source=built['source'], pending=pending, extra=built['extra'])


def correct_existing(item: dict[str, Any], naming: dict[str, Any], *, backup_dir: Path,
                     root: Path = layout.FINAL_LOCAL_ROOT) -> dict[str, Any]:
    """Bring an already finalized package to the single-CSV file set; dialogues.csv/originals bytes unchanged."""
    target, built, pending = _prepare(item, naming, root)
    return rewrite_package(target, built['files'], records=built['records'], scope=built['scope'],
                           status='finalized', source=built['source'], pending=pending, extra=built['extra'],
                           reason='single public CSV: dialogues.jsonl and derived turns.csv withdrawn',
                           backup_dir=backup_dir, preserve=PRESERVE)


def finalization_record(target: Path, previous: dict[str, Any]) -> dict[str, Any]:
    """CodeHub local finalization record of a verified package (size/count = whole package directory)."""
    target = Path(target)
    check = verify_package(target)
    if check['status'] != 'verified':
        raise ValueError(f'package not verified: {check}')
    manifest = json.loads((target / 'manifest.json').read_text(encoding='utf-8'))
    files = [p for p in target.rglob('*') if p.is_file()]
    q = manifest['data_quality']
    note = (f"full: MathDial train.jsonl {q['splits']['train']} + test.jsonl {q['splits']['test']} = "
            f"{q['dialogues']} dialogues in the single public file {DATA_FILE} (all 11 upstream fields, "
            f"conversation_raw verbatim, turn_count); {q['turns']} turns rebuildable from conversation_raw "
            f"(turns_from_csv), not shipped separately; dialogues.jsonl and derived turns.csv withdrawn "
            f"(manifest r{manifest.get('manifest_revision', 1)}); {q['unknown_speaker_turns_preserved']} turn "
            f"segment without speaker prefix kept raw; {q['empty_text_turns']} turns have empty text (upstream "
            f"prefix-only segment, kept); 0 duplicates removed; originals/mathdial-HEAD.zip is internal evidence, "
            f"not data; size_bytes/file_count = whole package incl. manifest + manifest-history; source "
            f"acquisition sha256 {manifest['source']['staging_sha256']}; not uploaded to cloud")
    return {'status': 'finalized', 'path': target.as_posix(), 'size_bytes': sum(p.stat().st_size for p in files),
            'file_count': len(files), 'manifest_path': (target / 'manifest.json').as_posix(),
            'manifest_sha256': sha256_file(target / 'manifest.json'),
            'source_acquisition_device_id': previous.get('source_acquisition_device_id'),
            'expected_resource_version': previous.get('expected_resource_version'),
            'finished_at': (manifest.get('revised_at') or manifest['finalized_at']).replace('+00:00', 'Z'),
            'note': note, 'error': None}  # CodeHub stores UTC as ...Z


def register(hub: Any, target: Path) -> dict[str, Any]:
    """PUT the local finalization record and read it back; ``ack`` only when every sent field matches."""
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
    from .catalog import CatalogStore
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--root', default=str(layout.FINAL_LOCAL_ROOT))
    p.add_argument('--naming', default=str(layout.DEFAULT_NAMING), help='cached CodeHub naming snapshot')
    p.add_argument('--correct-existing', action='store_true',
                   help='rewrite the existing package to the current file set (needs --backup-dir)')
    p.add_argument('--backup-dir', help='recovery backup directory, outside the package, for replaced/removed files')
    p.add_argument('--register', action='store_true', help='PUT the CodeHub local finalization and read it back')
    a = p.parse_args(argv)
    item = CatalogStore().load()['items'][MATERIAL_ID]
    naming = layout.load_naming(a.naming)
    if a.correct_existing:
        if not a.backup_dir:
            p.error('--correct-existing needs --backup-dir')
        result = correct_existing(item, naming, backup_dir=Path(a.backup_dir), root=Path(a.root))
    else:
        result = finalize(item, naming, root=Path(a.root))
    ok = result['status'] in ('verified', 'unchanged', 'rewritten')
    if ok and a.register:
        from .codehub import CodeHubSkillDep
        hub = CodeHubSkillDep()
        hub.verify_peer()
        result['codehub'] = register(hub, Path(result['path']))
        ok = result['codehub']['ack']
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
