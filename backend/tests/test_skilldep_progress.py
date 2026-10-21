from pathlib import Path

from backend.core.skilldep_materials import progress
from backend.core.skilldep_materials.progress import ProgressReporter, bytes_on_disk, report_downloading


class FakeHub:
    device_id = 'dev-1'
    device_name = 'CodeYun'

    def __init__(self, status=None):
        self.records = {}
        self.status = status
        self.puts = []

    def register(self, material_id, record):
        self.puts.append(dict(record))
        self.records[material_id] = dict(record)
        return record

    def acquisitions(self, material_id):
        if material_id in self.records:
            return [self.records[material_id]]
        return [{'device_id': self.device_id, 'status': self.status}] if self.status else []


def test_bytes_on_disk_counts_part_files(tmp_path: Path):
    (tmp_path / 'a.bin').write_bytes(b'x' * 10)
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'sub' / 'b.bin.part').write_bytes(b'y' * 5)
    assert bytes_on_disk(tmp_path) == 15


def test_report_downloading_registers_cumulative_size(tmp_path: Path):
    hub = FakeHub()
    out = report_downloading(hub, 'sd-1', tmp_path, 1234)
    assert out['reported'] is True
    put = hub.puts[0]
    assert put['status'] == 'downloading' and put['size_bytes'] == 1234
    assert put['availability'] == 'windows_local' and put['path'] == tmp_path.as_posix()
    assert 'sha256' not in put and 'manifest_path' not in put


def test_report_never_downgrades_downloaded(tmp_path: Path):
    hub = FakeHub(status='downloaded')
    out = report_downloading(hub, 'sd-1', tmp_path, 1)
    assert out == {'material_id': 'sd-1', 'reported': False, 'skipped': 'already downloaded', 'size_bytes': 1}
    assert hub.puts == []


def test_partial_may_be_reported_as_downloading(tmp_path: Path):
    hub = FakeHub(status='partial')
    assert report_downloading(hub, 'sd-1', tmp_path, 7)['reported'] is True


def test_reporter_throttles_by_time_or_bytes(tmp_path: Path):
    (tmp_path / 'old.bin').write_bytes(b'z' * 100)
    hub, now = FakeHub(), [0.0]
    rep = ProgressReporter(hub, 'sd-1', tmp_path, announce=True, interval=600, step_bytes=1000,
                           clock=lambda: now[0])
    rep.update(10)                      # announce
    rep.update(500)                     # neither 10 min nor 1000 new bytes
    rep.update(1010)                    # +1000 bytes
    now[0] = 601
    rep.update(1100)                    # 10 minutes
    assert [p['size_bytes'] for p in hub.puts] == [110, 1110, 1200]


def test_reporter_without_announce_waits_for_first_threshold(tmp_path: Path):
    hub, now = FakeHub(), [0.0]
    rep = ProgressReporter(hub, 'sd-1', tmp_path / 'new', interval=600, step_bytes=10 ** 9, clock=lambda: now[0])
    rep.update(5)
    assert hub.puts == []
    now[0] = 600
    rep.update(6)
    rep.close()
    rep.update(10 ** 10)
    assert [p['size_bytes'] for p in hub.puts] == [6]


def test_network_error_does_not_raise(tmp_path: Path):
    class Down(FakeHub):
        def acquisitions(self, material_id):
            raise ConnectionError('offline')

    out = report_downloading(Down(), 'sd-1', tmp_path, 3)
    assert out['reported'] is False and 'offline' in out['register_error']


def test_should_announce():
    assert progress.should_announce(size_mb_est=200)
    assert progress.should_announce(seconds_est=301)
    assert not progress.should_announce(size_mb_est=50, seconds_est=60)


def test_total_bytes_only_sent_when_known(tmp_path: Path):
    hub = FakeHub()
    report_downloading(hub, 'sd-1', tmp_path, 10)
    assert 'total_bytes' not in hub.puts[-1]
    ProgressReporter(hub, 'sd-1', tmp_path, total_bytes=500, baseline_bytes=0, announce=True).update(20)
    assert hub.puts[-1]['total_bytes'] == 500 and hub.puts[-1]['size_bytes'] == 20


def test_cloud_transfer_size_update_keeps_other_fields():
    stored = {'material_id': 'sd-1', 'account_id': 'baidu-self', 'resource_version': 'v9', 'recorded_at': 't',
              'recorded_by': 'me', 'status': 'transferred', 'cloud_path': '/x', 'size_bytes': None,
              'file_count': None, 'size_label': None, 'expected_resource_version': 'v9'}
    body = progress.cloud_transfer_size_update(stored, size_bytes=123, file_count=4)
    assert body == {'status': 'transferred', 'cloud_path': '/x', 'size_bytes': 123, 'file_count': 4,
                    'size_label': None, 'expected_resource_version': 'v9'}
    assert progress.cloud_transfer_size_update(stored, size_label='约 1.2G')['size_label'] == '约 1.2G'
