from types import SimpleNamespace

import pytest

from backend.core.baidu_netdisk.download_batch import run_batch


def job(key, total, size, complete=True):
    return {"inventory_complete": complete, "resource_total_bytes": total,
            "material_id": key, "material_cloud_path": "/materials/" + key,
            "request": {"operation_id": key, "remote_path": "/materials/" + key,
                        "expected_manifest": {"files": [{"rel": "one.bin", "size": size},
                                                          {"rel": "nested/two.bin", "size": total-size}]}}}


class Service:
    config = SimpleNamespace(local_root=None)

    def __init__(self, statuses):
        self.calls, self.statuses = [], iter(statuses)

    def download_dir(self, **request):
        self.calls.append(request["operation_id"])
        return {"status": next(self.statuses)}


def test_measured_resource_order_disk_reserve_and_unknown_submission(tmp_path):
    s = Service(["verified", "submitted", "verified"])
    out = run_batch(s, [job("large", 100, 1), job("small-b", 20, 8), job("small-a", 20, 3)],
                    tmp_path, reserve_bytes=10, free_bytes=lambda: 1000)
    assert s.calls == ["small-a", "small-b"]
    assert out["status"] == "paused_needs_observation"
    assert (tmp_path / "current.json").exists()
    s = Service(["verified"])
    out = run_batch(s, [job("too-large", 100, 91)], tmp_path, reserve_bytes=10, free_bytes=lambda: 100)
    assert out["status"] == "paused_disk_budget" and not s.calls


def test_unknown_size_never_runs_and_limit_is_bounded(tmp_path):
    s = Service(["verified"])
    with pytest.raises(ValueError, match="complete byte inventory"):
        run_batch(s, [job("unknown", 0, 0, False)], tmp_path)
    assert not s.calls
    out = run_batch(s, [job("a", 10, 1), job("b", 20, 1)], tmp_path,
                    limit=1, reserve_bytes=0, free_bytes=lambda: 100)
    assert s.calls == ["a"] and out["status"] == "bounded_batch_complete"


def test_single_files_and_partial_package_manifest_are_rejected(tmp_path):
    s = Service([])
    j = job("row", 20, 3)
    j["request"]["expected_size"] = 3
    with pytest.raises(ValueError):
        run_batch(s, [j], tmp_path)
    del j["request"]["expected_size"]
    j["request"]["expected_manifest"]["files"].pop()
    with pytest.raises(ValueError):
        run_batch(s, [j], tmp_path)
    assert not s.calls


def test_five_inflight_packages_submit_sequentially_in_size_order(tmp_path):
    s = Service(["submitted"] * 5)
    out = run_batch(s, [job(str(n), n * 10, 1) for n in range(6, 0, -1)], tmp_path,
                    limit=6, max_inflight=5, reserve_bytes=0, free_bytes=lambda: 10000)
    assert s.calls == ["1", "2", "3", "4", "5"]
    assert out["status"] == "paused_inflight_capacity"
    assert len(out["inflight_jobs"]) == 5


def test_outstanding_bytes_and_slots_are_reserved_across_batches(tmp_path):
    active = [job("active", 50, 1)]
    s = Service(["submitted", "submitted"])
    out = run_batch(s, [job("a", 30, 1), job("b", 40, 1)], tmp_path,
                    max_inflight=5, inflight_jobs=active, reserve_bytes=10, free_bytes=lambda: 100)
    assert s.calls == ["a"]  # 10 reserve + 50 outstanding + 30 newly submitted + 40 > 100
    assert out["status"] == "paused_disk_budget"
    assert len(out["inflight_jobs"]) == 2


def test_unknown_submission_stops_without_filling_other_slots(tmp_path):
    s = Service(["submitted", "state_unknown", "submitted"])
    out = run_batch(s, [job(str(n), n, 1) for n in range(1, 4)], tmp_path,
                    max_inflight=5, reserve_bytes=0, free_bytes=lambda: 100)
    assert s.calls == ["1", "2"]
    assert out["status"] == "paused_needs_observation"


def test_existing_inflight_identity_cannot_be_enqueued_again(tmp_path):
    s = Service([])
    with pytest.raises(ValueError, match="duplicate"):
        run_batch(s, [job("same", 10, 1)], tmp_path, max_inflight=5,
                  inflight_jobs=[job("same", 10, 1)])
    assert not s.calls


def test_client_managed_queue_accepts_fifty_whole_packages_in_order(tmp_path):
    s = Service(["submitted"] * 50)
    out = run_batch(s, [job(f"p{n:02}", n, 1) for n in range(51, 0, -1)], tmp_path,
                    limit=51, max_inflight=5, max_queued=50, reserve_bytes=0,
                    free_bytes=lambda: 10000)
    assert s.calls == [f"p{n:02}" for n in range(1, 51)]
    assert out["status"] == "paused_queue_capacity"
    assert len(out["inflight_jobs"]) == 50


def test_client_queue_keeps_full_byte_budget_and_stops_on_unknown(tmp_path):
    s = Service(["submitted", "state_unknown", "submitted"])
    out = run_batch(s, [job("a", 20, 1), job("b", 25, 1), job("c", 30, 1)], tmp_path,
                    max_inflight=5, max_queued=50, inflight_jobs=[job("old", 60, 1)],
                    reserve_bytes=10, free_bytes=lambda: 100)
    assert s.calls == ["a"]
    assert out["status"] == "paused_disk_budget"
    s = Service(["submitted", "state_unknown", "submitted"])
    out = run_batch(s, [job("a", 20, 1), job("b", 25, 1), job("c", 30, 1)], tmp_path,
                    max_inflight=5, max_queued=50, reserve_bytes=0, free_bytes=lambda: 1000)
    assert s.calls == ["a", "b"]
    assert out["status"] == "paused_needs_observation"


def test_client_queue_recovery_preserves_all_prior_outstanding_slots(tmp_path):
    pending = [job(f"old{n}", 10, 1) for n in range(49)]
    s = Service(["submitted", "submitted"])
    out = run_batch(s, [job("a", 20, 1), job("b", 30, 1)], tmp_path,
                    max_inflight=5, max_queued=50, inflight_jobs=pending,
                    reserve_bytes=0, free_bytes=lambda: 10000)
    assert s.calls == ["a"]
    assert out["status"] == "paused_queue_capacity"
    assert len(out["inflight_jobs"]) == 50


@pytest.mark.parametrize("capacity", [0, 51, True, 5.5])
def test_client_queue_capacity_must_be_explicitly_bounded(tmp_path, capacity):
    with pytest.raises(ValueError, match="max_queued"):
        run_batch(Service([]), [], tmp_path, max_queued=capacity)
