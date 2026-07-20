from __future__ import annotations

import threading
from dataclasses import dataclass

import pytest

from backend.plugins.modules.media_sync import sources


@pytest.fixture(autouse=True)
def no_pixiv_request_delay(tmp_path, monkeypatch):
    circuit_path = tmp_path / "pixiv-risk-circuit.json"
    monkeypatch.setattr(sources, "PIXIV_MIN_REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(sources, "PIXIV_REQUEST_JITTER_SECONDS", (0.0, 0.0))
    monkeypatch.setattr(sources, "_pixiv_last_request_at", 0.0)
    monkeypatch.setattr(sources, "pixiv_risk_circuit_path", lambda: circuit_path)
    return circuit_path


@pytest.fixture
def isolated_pixiv_risk_circuit(no_pixiv_request_delay):
    return no_pixiv_request_delay


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


def test_pixiv_risk_circuit_persists_and_blocks_before_next_operation(isolated_pixiv_risk_circuit):
    with sources.pixiv_remote_run_audit(source="pytest", max_remote_operations=2) as audit:
        sources.wait_for_pixiv_request_slot("detail_api")
        sources.trip_pixiv_risk_circuit(
            reason="http_rate_limited",
            signal="HTTP 429",
            operation_kind="detail_api",
            status_code=429,
            url="https://www.pixiv.net/ajax/illust/1",
        )
        with pytest.raises(sources.PixivRiskCircuitOpen):
            sources.wait_for_pixiv_request_slot("download")

    snapshot = audit.snapshot()
    assert isolated_pixiv_risk_circuit.exists()
    assert snapshot["remote_operations_total"] == 1
    assert snapshot["stop_reason"] == "risk_circuit_tripped"
    assert snapshot["risk_circuit"]["reason"] == "http_rate_limited"


@dataclass
class FakeResponse:
    status_code: int
    url: str
    text: str = ""


@dataclass
class FakeBody:
    text: str


@dataclass
class FakeTab:
    url: str
    body_text: str

    def ele(self, selector, timeout=None):
        del timeout
        return FakeBody(self.body_text) if selector == "tag:body" else None


def test_pixiv_http_429_trips_persistent_circuit(isolated_pixiv_risk_circuit):
    response = FakeResponse(status_code=429, url="https://www.pixiv.net/ajax/top/illust")

    with pytest.raises(sources.PixivRiskCircuitOpen):
        sources.raise_if_pixiv_http_risk(response, operation_kind="recommend_api", inspect_body=True)

    state = sources.read_pixiv_risk_circuit()
    assert state is not None
    assert state["reason"] == "http_rate_limited"
    assert state["status_code"] == 429


def test_pixiv_browser_account_warning_trips_persistent_circuit(
    isolated_pixiv_risk_circuit,
    monkeypatch,
):
    tab = FakeTab(url="https://www.pixiv.net/", body_text="Suspicious activity detected")
    monkeypatch.setattr(sources, "import_system_chrome_debug_cookies_to_tab", lambda *_args, **_kwargs: False)

    with pytest.raises(sources.BrowserActionRequiredError):
        sources.raise_if_browser_action_required(tab, context="Pixiv 首页")

    state = sources.read_pixiv_risk_circuit()
    assert state is not None
    assert state["reason"] == "account_warning"


def test_pixiv_home_collection_stops_immediately_after_circuit_trip(monkeypatch):
    calls: list[str] = []

    def fake_fetch(_session, *, artwork_id, lang):
        del lang
        calls.append(artwork_id)
        raise sources.PixivRiskCircuitOpen("blocked")

    monkeypatch.setattr(sources, "fetch_pixiv_illust_detail", fake_fetch)

    with pytest.raises(sources.PixivRiskCircuitOpen):
        sources.collect_pixiv_home_items(
            object(),
            artwork_urls=[
                "https://www.pixiv.net/artworks/100",
                "https://www.pixiv.net/artworks/200",
            ],
            source_kind="home_recommend",
            log=lambda _message: None,
        )

    assert calls == ["100"]
