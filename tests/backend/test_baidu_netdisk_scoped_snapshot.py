"""Regression checks for staging reads scoped to every matching whole root."""
from pathlib import Path
from types import SimpleNamespace
import stat
import pytest
from backend.core.baidu_netdisk import local_files
from backend.core.baidu_netdisk import operations_evidence as evidence


def request(tmp_path):
    return {'name': 'pack', 'local_dir': str(tmp_path / 'out'),
            'expected_manifest': {'files': [{'rel': 'a', 'size': 1, 'sha256': None}]}}


def test_reconcile_never_enters_unrelated_staging_root(tmp_path, monkeypatch):
    staging = tmp_path / 'staging'
    (staging / 'pack').mkdir(parents=True)
    (staging / 'pack' / 'a').write_bytes(b'x')
    unrelated = staging / 'unrelated'
    unrelated.mkdir()
    real = evidence.os.scandir
    def guarded(path):
        assert Path(path) != unrelated, 'another material tree must not be traversed'
        return real(path)
    monkeypatch.setattr(evidence.os, 'scandir', guarded)
    assert evidence.observe_dir(staging, {}, request(tmp_path))['status'] == 'ready'


def test_scoped_snapshot_keeps_all_client_copy_candidates(tmp_path):
    staging = tmp_path / 'staging'
    for name in ['pack', 'pack(1)']:
        (staging / name).mkdir(parents=True)
        (staging / name / 'a').write_bytes(b'x')
    assert evidence.observe_dir(staging, {}, request(tmp_path))['status'] == 'ambiguous'


def test_scoped_snapshot_refuses_matching_reparse_root(tmp_path, monkeypatch):
    staging = tmp_path / 'staging'
    candidate = staging / 'pack'
    candidate.mkdir(parents=True)
    real_stat = evidence.os.lstat
    real_scan = evidence.os.scandir
    def fake_stat(path, *args, **kwargs):
        if Path(path) == candidate:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return real_stat(path, *args, **kwargs)
    def guarded(path):
        assert Path(path) != candidate, 'reparse root must not be entered'
        return real_scan(path)
    monkeypatch.setattr(evidence.os, 'lstat', fake_stat)
    monkeypatch.setattr(evidence.os, 'scandir', guarded)
    assert evidence.observe_dir(staging, {}, request(tmp_path))['status'] == 'unsafe'


def test_collection_snapshot_skips_unrelated_and_keeps_copy_alias(tmp_path, monkeypatch):
    for name in ['pack', 'pack(1)', 'unrelated']:
        (tmp_path / name).mkdir()
        (tmp_path / name / 'a').write_bytes(b'x')
    real = local_files.os.scandir
    def guarded(path):
        assert Path(path) != tmp_path / 'unrelated'
        return real(path)
    monkeypatch.setattr(local_files.os, 'scandir', guarded)
    assert set(local_files.snapshot_dir(tmp_path, expected_dir='pack')) == {'pack/a', 'pack(1)/a'}
    result = local_files.collect_download_dir(download_root=tmp_path, before={}, expected_name='pack',
        expected_files={'a': '1'}, target_dir=tmp_path / 'out')
    assert result.status == 'ambiguous'


@pytest.mark.parametrize('relative', ['pack', 'pack/nested'])
def test_collection_snapshot_rejects_reparse_before_entering(tmp_path, monkeypatch, relative):
    candidate = tmp_path / relative
    candidate.mkdir(parents=True)
    real_stat, real_scan = local_files.os.lstat, local_files.os.scandir
    def fake_stat(path, *args, **kwargs):
        if Path(path) == candidate:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return real_stat(path, *args, **kwargs)
    def guarded(path):
        assert Path(path) != candidate
        return real_scan(path)
    monkeypatch.setattr(local_files.os, 'lstat', fake_stat)
    monkeypatch.setattr(local_files.os, 'scandir', guarded)
    with pytest.raises(ValueError, match='Unsafe staging'):
        local_files.snapshot_dir(tmp_path, expected_dir='pack')
