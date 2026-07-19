from __future__ import annotations

import threading

import pytest

from backend.plugins.modules.media_sync import sources


@pytest.fixture(autouse=True)
def no_pixiv_request_delay(monkeypatch):
    monkeypatch.setattr(sources, "PIXIV_MIN_REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(sources, "PIXIV_REQUEST_JITTER_SECONDS", (0.0, 0.0))
    monkeypatch.setattr(sources, "_pixiv_last_request_at", 0.0)


def test_pixiv_remote_budget_stops_before_operation_over_limit():
    with pytest.raises(sources.PixivRemoteOperationBudgetExceeded):
        with sources.pixiv_remote_run_audit(source="pytest", max_remote_operations=2) as audit:
            sources.wait_for_pixiv_request_slot("page_navigation")
            sources.wait_for_pixiv_request_slot("detail_api")
            sources.wait_for_pixiv_request_slot("download")

    snapshot = audit.snapshot()
    assert snapshot["remote_operations_total"] == 2
    assert snapshot["operation_counts"] == {"detail_api": 1, "page_navigation": 1}
    assert snapshot["stop_reason"] == "remote_operation_budget_exhausted"
    assert "2/2" in snapshot["error"]


def test_pixiv_remote_budget_is_shared_with_worker_threads():
    errors: list[Exception] = []

    def authorize_download() -> None:
        try:
            sources.wait_for_pixiv_request_slot("download")
        except Exception as exc:  # pragma: no cover - assertion checks the concrete error below
            errors.append(exc)

    with sources.pixiv_remote_run_audit(source="pytest", max_remote_operations=1) as audit:
        first = threading.Thread(target=authorize_download)
        second = threading.Thread(target=authorize_download)
        first.start()
        first.join()
        second.start()
        second.join()

    assert audit.snapshot()["remote_operations_total"] == 1
    assert len(errors) == 1
    assert isinstance(errors[0], sources.PixivRemoteOperationBudgetExceeded)


@pytest.mark.parametrize("budget", [0, 501])
def test_pixiv_remote_budget_rejects_unsafe_limits(budget):
    with pytest.raises(ValueError):
        with sources.pixiv_remote_run_audit(source="pytest", max_remote_operations=budget):
            pass
