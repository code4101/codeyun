import hashlib

import pytest

from backend.core.skilldep_materials import acquire
from backend.core.skilldep_materials.acquire import MB

URL = 'https://example.gov.cn/files/%E8%A7%84%E8%8C%83.pdf'


class FakeResponse:
    def __init__(self, body: bytes, headers: dict):
        self.body, self.headers = body, headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        for i in range(0, len(self.body), 4):
            yield self.body[i:i + 4]


class FakeSession:
    def __init__(self, body=b'%PDF-1.4 hello', declare=True):
        self.body, self.declare, self.calls = body, declare, 0

    def get(self, url, **kwargs):
        self.calls += 1
        headers = {'Content-Length': str(len(self.body))} if self.declare else {}
        return FakeResponse(self.body, headers)


class FakeRegistry:
    device_id, device_name = 'node-1', 'codepc_mf'

    def __init__(self, size_mb_est=0.001, **extra):
        self.records = {}
        self.plan = [{'id': 'sd-aaa', 'name': '规范', 'url': URL, 'size_mb_est': size_mb_est,
                      'requires_login_or_purchase': False, **extra},
                     {'id': 'sd-buy', 'name': '付费', 'url': 'https://shop', 'requires_login_or_purchase': True}]

    def materials(self):
        return self.plan

    def register(self, material_id, record):
        self.records[material_id] = {**record, 'recorded_at': 'now'}
        return self.records[material_id]

    def acquisitions(self, material_id):
        return [self.records[material_id]] if material_id in self.records else []


def test_download_registers_and_reuses_verified_copy(tmp_path):
    registry, session = FakeRegistry(), FakeSession()
    [first] = acquire(registry, ['sd-aaa'], root=tmp_path, session=session)
    assert first['status'] == 'downloaded' and first['registered'] and not first['reused_local']
    assert first['availability'] == 'windows_local'
    assert first['sha256'] == hashlib.sha256(session.body).hexdigest()
    assert first['path'].endswith('规范.pdf') and first['size_bytes'] == len(session.body)

    [again] = acquire(registry, ['sd-aaa'], root=tmp_path, session=session)
    assert session.calls == 1 and again['reused_local'] and again['registered']


def test_tampered_local_copy_is_downloaded_again(tmp_path):
    registry, session = FakeRegistry(), FakeSession()
    [first] = acquire(registry, ['sd-aaa'], root=tmp_path, session=session)
    open(first['path'], 'wb').write(b'changed')
    [again] = acquire(registry, ['sd-aaa'], root=tmp_path, session=session)
    assert session.calls == 2 and not again['reused_local'] and again['sha256'] == first['sha256']


@pytest.mark.parametrize('declare', [True, False])
def test_oversized_body_is_failed_not_downloaded(tmp_path, declare):
    # Estimate 0 MB -> limit 1 MB; the body is larger with or without Content-Length.
    registry, session = FakeRegistry(size_mb_est=0.0001), FakeSession(b'x' * (2 * MB), declare=declare)
    [result] = acquire(registry, ['sd-aaa'], root=tmp_path, session=session)
    assert result['status'] == 'failed' and 'DownloadTooLarge' in result['error']
    assert registry.records['sd-aaa']['status'] == 'failed' and 'sha256' not in registry.records['sd-aaa']
    assert not [p for p in tmp_path.rglob('*') if p.is_file() and p.name != 'manifest.json']


def test_total_budget_caps_unknown_sizes(tmp_path):
    registry, session = FakeRegistry(size_mb_est=None), FakeSession(b'x' * 100, declare=False)
    [result] = acquire(registry, ['sd-aaa'], root=tmp_path, session=session, max_total_bytes=50)
    assert result['status'] == 'failed'


def test_rejects_unknown_or_login_required_before_downloading(tmp_path):
    registry, session = FakeRegistry(), FakeSession()
    with pytest.raises(ValueError):
        acquire(registry, ['sd-missing'], root=tmp_path, session=session)
    with pytest.raises(ValueError):
        acquire(registry, ['sd-buy'], root=tmp_path, session=session)
    assert session.calls == 0


def test_mismatched_readback_is_not_reported_registered(tmp_path):
    registry, session = FakeRegistry(), FakeSession()
    registry.acquisitions = lambda material_id: [{'device_id': 'node-1', 'status': 'downloaded', 'sha256': 'x'}]
    [result] = acquire(registry, ['sd-aaa'], root=tmp_path, session=session)
    assert result['status'] == 'downloaded' and result['registered'] is False
