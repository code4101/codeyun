"""A client can leave old preallocation files after publishing final payloads."""
import hashlib
import os

import pytest

from backend.core.baidu_netdisk.local_files import collect_download_dir
from backend.core.baidu_netdisk.operations_evidence import observe_dir


def tree(tmp_path, *, final=b'abc', partial=b'\0\0\0', newer=True, suffix='.baiduyun.p.downloading'):
    root = tmp_path / 'staging'
    folder = root / 'package'
    folder.mkdir(parents=True)
    artifact = folder / ('a.xlsx' + suffix)
    artifact.write_bytes(partial)
    os.utime(artifact, ns=(2_000_000_000, 2_000_000_000))
    if final is not None:
        payload = folder / 'a.xlsx'
        payload.write_bytes(final)
        stamp = 3_000_000_000 if newer else 1_000_000_000
        os.utime(payload, ns=(stamp, stamp))
    request = {'name': 'package', 'local_dir': str(tmp_path / 'raw'),
               'expected_manifest': {'files': [{'rel': 'a.xlsx', 'size': 3, 'sha256': None}]}}
    return root, artifact, request


def test_newer_final_is_hashed_and_collected_artifact_preserved(tmp_path):
    root, artifact, request = tree(tmp_path)
    observed = observe_dir(root, {}, request)
    assert observed['status'] == 'ready'
    assert observed['evidence']['superseded_partials'] == ['a.xlsx.baiduyun.p.downloading']
    args = dict(download_root=root, before={}, expected_name='package',
                expected_files={'a.xlsx': '3'}, target_dir=tmp_path / 'raw')
    first = collect_download_dir(**args)
    assert first.status == 'unstable'
    result = collect_download_dir(**args, stable_token=first.stable_token)
    assert result.status == 'collected'
    assert artifact.read_bytes() == b'\0\0\0'
    assert (tmp_path / 'raw/package/a.xlsx').read_bytes() == b'abc'
    assert len(list((tmp_path / 'raw/package').iterdir())) == 1
    assert result.files[0]['sha256'] == hashlib.sha256(b'abc').hexdigest().upper()


@pytest.mark.parametrize('options', [
    {'final': None}, {'final': b'ab'}, {'partial': b'\0\0'},
    {'newer': False, 'partial': b'bad'}, {'suffix': '.downloading'},
])
def test_incomplete_or_unproven_siblings_still_block(tmp_path, options):
    root, _, request = tree(tmp_path, **options)
    assert observe_dir(root, {}, request)['status'] == 'in_progress'
    result = collect_download_dir(download_root=root, before={}, expected_name='package',
                                  expected_files={'a.xlsx': '3'}, target_dir=tmp_path / 'raw')
    assert result.status == 'in_progress'
    assert not (tmp_path / 'raw').exists()


def test_trusted_hash_mismatch_still_blocks(tmp_path):
    root, _, request = tree(tmp_path)
    request['expected_manifest']['files'][0]['sha256'] = hashlib.sha256(b'bad').hexdigest().upper()
    assert observe_dir(root, {}, request)['status'] == 'hash_mismatch'


def test_zero_preallocation_can_outlive_completed_final(tmp_path):
    root, artifact, request = tree(tmp_path, newer=False)
    assert observe_dir(root, {}, request)['status'] == 'ready'
    assert artifact.read_bytes() == b'\0\0\0'


def test_two_zero_files_do_not_prove_a_completed_counterpart(tmp_path):
    root, _, request = tree(tmp_path, final=b'\0\0\0', newer=False)
    assert observe_dir(root, {}, request)['status'] == 'in_progress'


def test_partial_change_resets_collection_stability(tmp_path):
    root, artifact, request = tree(tmp_path)
    args = dict(download_root=root, before={}, expected_name='package',
                expected_files={'a.xlsx': '3'}, target_dir=tmp_path / 'raw')
    first = collect_download_dir(**args)
    os.utime(artifact, ns=(2_500_000_000, 2_500_000_000))
    second = collect_download_dir(**args, stable_token=first.stable_token)
    assert second.status == 'unstable' and second.stable_token != first.stable_token
