"""Finalize MathDial 辅导对话 (sd-8858f4aff1df) into a cleaned package.

Input is the verified staging archive ``mathdial-HEAD.zip`` (SHA-256 from the
catalog).  All records of ``data/train.jsonl`` and ``data/test.jsonl`` are
parsed (the upstream README states 2861 conversations); ``example.jsonl`` is a
sample subset and is not data.  The CSV copies are only used as a cross-check.

Cleaning rules (all source fields kept):

* Keys normalized to snake_case (``self-correctness`` -> ``self_correctness``);
  values unchanged, ``null`` kept; ``split`` and source line added.
* ``conversation`` kept verbatim as ``conversation_raw`` and split on ``|EOM|``
  into ``turns``: ``Teacher: (move)text`` -> teacher turn with move label when it
  is one of the dataset's moves (focus/generic/probing/telling); any other
  ``Name: text`` -> student turn with that speaker label; segments without a
  speaker prefix are kept as ``speaker_role=unknown`` and counted.
* Duplicates: exact record and conversation-text duplicates are removed and
  counted; repeated ``qid`` is legitimate (several dialogues per problem).

Outputs: ``data/dialogues.jsonl`` (full records with turns), ``data/dialogues.csv``
(flat, no turns), ``data/turns.csv``, ``originals/mathdial-HEAD.zip``,
schema/provenance/manifest.

CLI::

    uv run python -m backend.core.skilldep_materials.mathdial_finalizer [--root F:/data/m2610skilldep]
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
from .finalize import layout_source, copy_original, csv_bytes, jsonl_bytes, sha256_bytes, write_package

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


def build(zip_path: Path, zip_sha256: str) -> dict[str, Any]:
    data = copy_original(zip_path, zip_sha256)
    z = zipfile.ZipFile(io.BytesIO(data))
    records, seen_rec, seen_conv = [], set(), set()
    stats = {'source_lines': {}, 'csv_crosscheck': {}, 'exact_duplicates_removed': 0,
             'conversation_duplicates_removed': 0, 'empty_segments_dropped': 0,
             'turn_roles': {}, 'teacher_moves': {}, 'unlabelled_teacher_turns': 0}
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
                stats['turn_roles'][t['speaker_role']] = stats['turn_roles'].get(t['speaker_role'], 0) + 1
                if t['speaker_role'] == 'teacher':
                    mv = t['teacher_move'] or 'none'
                    stats['teacher_moves'][mv] = stats['teacher_moves'].get(mv, 0) + 1
            rec = {'dialogue_id': f'mathdial-{split}-{line_no:04d}', 'split': split, 'source_line': line_no}
            rec.update({snake(k): src[k] for k in FIELDS if k != 'conversation'})
            rec.update({'conversation_raw': conv, 'conversation_sha256': conv_sha,
                        'turn_count': len(turns), 'turns': turns})
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
    turn_cols = ['dialogue_id', 'turn_index', 'speaker_role', 'speaker_label', 'teacher_move', 'text']
    turn_rows = [[r['dialogue_id'], i, t['speaker_role'], t['speaker_label'], t['teacher_move'], t['text']]
                 for r in records for i, t in enumerate(r['turns'])]
    readme = z.read('mathdial-HEAD/README.md').decode('utf-8')
    schema = {
        'schema': 'codeyun.skilldep_dataset_schema/v1', 'material_id': MATERIAL_ID,
        'files': {
            'data/dialogues.jsonl': {'record': 'one tutoring dialogue', 'fields': {
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
                'conversation_raw': 'upstream conversation verbatim, |EOM| delimited',
                'conversation_sha256': 'SHA-256 of conversation_raw (UTF-8)',
                'turn_count': 'len(turns)',
                'turns': 'list of {speaker_role teacher|student|unknown, speaker_label, teacher_move '
                         f'{"|".join(MOVES)}|null, text}}'}},
            'data/dialogues.csv': {'columns': flat_cols},
            'data/turns.csv': {'columns': turn_cols}},
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
        'data/dialogues.jsonl': jsonl_bytes(records),
        'data/dialogues.csv': csv_bytes(flat_cols, [[r[c] for c in flat_cols] for r in records]),
        'data/turns.csv': csv_bytes(turn_cols, turn_rows),
        'schema.json': (json.dumps(schema, ensure_ascii=False, indent=1) + '\n').encode(),
        'provenance.json': (json.dumps(provenance, ensure_ascii=False, indent=1) + '\n').encode(),
        'originals/mathdial-HEAD.zip': data,
    }
    scope = {'kind': 'full', 'dialogues': len(records), 'turns': len(turn_rows), **stats}
    return {'files': files, 'scope': scope,
            'records': {'data/dialogues.jsonl': len(records), 'data/dialogues.csv': len(records),
                        'data/turns.csv': len(turn_rows)},
            'source': {'staging_file': provenance['staging_file'], 'staging_sha256': zip_sha256}}


def finalize(item: dict[str, Any], naming: dict[str, Any], *,
             root: Path = layout.FINAL_LOCAL_ROOT) -> dict[str, Any]:
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
    target = where['local_dir']
    built['source'] |= layout_source(where)
    return write_package(target, item['id'], built['files'], records=built['records'], scope=built['scope'],
                         status='finalized', source=built['source'], pending=pending)


def main(argv: list[str] | None = None) -> int:
    from .catalog import CatalogStore
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--root', default=str(layout.FINAL_LOCAL_ROOT))
    p.add_argument('--naming', default=str(layout.DEFAULT_NAMING), help='cached CodeHub naming snapshot')
    a = p.parse_args(argv)
    item = CatalogStore().load()['items'][MATERIAL_ID]
    result = finalize(item, layout.load_naming(a.naming), root=Path(a.root))
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result['status'] in ('verified', 'unchanged') else 2


if __name__ == '__main__':
    raise SystemExit(main())
