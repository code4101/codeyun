"""Durable uncertainty barriers for invitation-letter consumption.

An armed record proves only that the click was about to be sent. Even a
successful input transport cannot prove consumption. Both phases block a new
use until native travel closure and an exact inventory delta verify the fact.
These records never contain a resumable GUI step or authorize replay.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from filelock import FileLock

from backend.core.settings import get_settings


def _root(directory: Path | None) -> Path:
    return directory if directory is not None else get_settings().data_dir / 'fanxiu' / 'disciple-letter-submissions'


def _write(path: Path, document: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(document, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def list_pending_disciple_letter_submissions(*, directory: Path | None = None) -> list[dict]:
    """Read unresolved facts; missing storage causes no directory creation."""
    return [record for path in sorted(_root(directory).glob('*.json'))
            if (record := json.loads(path.read_text(encoding='utf-8')))['phase'] != 'verified']


def arm_disciple_letter_submission(*, base_id: int, instance_id: str, before_count: int,
                                  submitted_count: int, process_identity: tuple[int, int],
                                  directory: Path | None = None) -> dict:
    """Write an uncertainty barrier immediately before one input submission."""
    if base_id not in (20300001, 20300003) or not isinstance(instance_id, str) or not instance_id.strip():
        raise ValueError('拜师函提交缺少合法道具身份')
    if any(type(value) is not int or value <= 0 for value in (before_count, submitted_count)) or submitted_count > before_count:
        raise ValueError('拜师函提交数量无效')
    if len(process_identity) != 2 or any(type(value) is not int or value <= 0 for value in process_identity):
        raise ValueError('拜师函提交进程身份无效')
    root = _root(directory)
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(root / 'submission.lock'), timeout=30):
        if list_pending_disciple_letter_submissions(directory=root):
            raise RuntimeError('存在未核账拜师函提交，禁止重新使用')
        record = {'version': 1, 'action_id': uuid4().hex, 'phase': 'armed',
                  'base_id': base_id, 'instance_id': instance_id,
                  'before_count': before_count, 'submitted_count': submitted_count,
                  'process_identity': list(process_identity),
                  'armed_at': datetime.now().astimezone().isoformat()}
        _write(root / (record['action_id'] + '.json'), record)
        return record


def mark_disciple_letter_result_observed(action_id: str, *, directory: Path | None = None) -> dict:
    """Persist a native #936 completion observation before its closing click.

    If the process stops after closing the result but before final accounting,
    this fact plus a fresh stable landing permits reconciliation without use.
    """
    if len(action_id) != 32 or any(c not in '0123456789abcdef' for c in action_id):
        raise ValueError('拜师函提交 action_id 无效')
    root = _root(directory)
    with FileLock(str(root / 'submission.lock'), timeout=30):
        path = root / (action_id + '.json')
        record = json.loads(path.read_text(encoding='utf-8'))
        if record['phase'] == 'armed':
            record.update(phase='result_observed', result_scene=936,
                          result_observed_at=datetime.now().astimezone().isoformat())
            _write(path, record)
        return record


def complete_disciple_letter_submission(action_id: str, *, remaining_count: int,
                                       travel_closed: bool, process_identity: tuple[int, int],
                                       directory: Path | None = None) -> dict:
    """Verify native closure and exact delta, or leave the barrier unresolved.

    Unchanged inventory never authorizes retry: the original click may have
    failed before input, failed after input, or still have an uncertain effect.
    Resolution requires new evidence, not replay of the saved request.
    """
    if len(action_id) != 32 or any(c not in '0123456789abcdef' for c in action_id):
        raise ValueError('拜师函提交 action_id 无效')
    if type(remaining_count) is not int or remaining_count < 0 or travel_closed is not True:
        raise ValueError('拜师函核账需要同游已关闭与完整库存数量')
    root = _root(directory)
    with FileLock(str(root / 'submission.lock'), timeout=30):
        path = root / (action_id + '.json')
        record = json.loads(path.read_text(encoding='utf-8'))
        if list(process_identity) != record['process_identity']:
            raise RuntimeError('拜师函提交核账游戏进程已变化')
        if record['before_count'] - record['submitted_count'] != remaining_count:
            raise RuntimeError('拜师函提交效果未确定，保留原凭证且禁止重放')
        if record['phase'] == 'verified':
            if record['remaining_count'] != remaining_count:
                raise RuntimeError('拜师函完成凭证冲突')
            return record
        if record['phase'] != 'result_observed':
            raise RuntimeError('拜师函提交尚无原生完成观察，禁止核账')
        record.update(phase='verified', remaining_count=remaining_count, travel_closed=True,
                      verified_at=datetime.now().astimezone().isoformat())
        _write(path, record)
        return record
