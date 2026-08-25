from __future__ import annotations

from types import SimpleNamespace

from backend.api.eastmoney import (
    _start_hk_pool_backtest_job,
    _start_strategy_search_job,
    run_hk_pool_backtest_local_job_payload,
)
from backend.core.stock.qlib_screening import QlibStrategyCandidate


def _payload() -> dict:
    return {
        "cache_key": {"start_date": "2025-01-01", "score_threshold": 0},
        "refresh": True,
        "limit": 12,
        "start_date": "2025-01-01",
        "end_date": "2026-08-05",
        "score_threshold": 0,
        "score_profile": "balanced",
        "take_profit_percent": 5.0,
        "stop_loss_percent": 0.0,
        "max_holding_days": 0,
        "cost_rate": 0.01,
        "force_liquidate_end": True,
    }


def test_hk_pool_background_backtest_submits_local_job(monkeypatch, tmp_path) -> None:
    submitted = []
    monkeypatch.setattr("backend.api.eastmoney.find_active_local_job_run", lambda *_args: None)
    monkeypatch.setattr(
        "backend.api.eastmoney.submit_local_job",
        lambda **kwargs: submitted.append(kwargs) or SimpleNamespace(id="stock-local-1"),
    )
    payload = _payload()

    _start_hk_pool_backtest_job(
        job_key="legacy-key",
        progress_path=tmp_path / "progress.json",
        **payload,
    )

    assert submitted == [
        {
            "job_type": "stock.hk-pool-one-lot-backtest",
            "payload": payload,
        }
    ]


def test_hk_pool_local_job_executes_and_reports_compact_result(monkeypatch, tmp_path) -> None:
    progress = []
    written = []
    fake_result = SimpleNamespace(
        target_count=12,
        tested_count=10,
        skipped_count=2,
        source="computed:test",
    )
    monkeypatch.setattr(
        "backend.api.eastmoney._hk_pool_backtest_cache_paths",
        lambda _cache_key: (tmp_path / "cache.json", tmp_path / "progress.json"),
    )
    monkeypatch.setattr(
        "backend.api.eastmoney._write_hk_pool_backtest_cache",
        lambda path, key, result: written.append((path, key, result)),
    )

    def fake_backtest(**kwargs):
        assert kwargs["score_threshold"] == 0
        kwargs["progress_callback"](fake_result)
        return fake_result

    monkeypatch.setattr("backend.api.eastmoney.backtest_hk_pool_one_lot_score", fake_backtest)

    result = run_hk_pool_backtest_local_job_payload(
        _payload(),
        progress_callback=progress.append,
    )

    assert result == {
        "target_count": 12,
        "tested_count": 10,
        "skipped_count": 2,
        "source": "computed:test",
    }
    assert progress == [fake_result]
    assert len(written) == 1


def test_hk_pool_strategy_search_submits_serializable_local_job(monkeypatch, tmp_path) -> None:
    submitted = []
    candidate = QlibStrategyCandidate(
        key="balanced-70",
        name="平衡 70 分",
        score_threshold=70,
        score_profile="balanced",
        take_profit_percent=5.0,
        stop_loss_percent=0.0,
        max_holding_days=20,
        cost_rate=0.01,
    )
    monkeypatch.setattr(
        "backend.api.eastmoney.submit_local_job_once",
        lambda **kwargs: (submitted.append(kwargs) or SimpleNamespace(id="strategy-local-1"), True),
    )

    _start_strategy_search_job(
        job_key="strategy-key",
        cache_key={"years": [2025]},
        progress_path=tmp_path / "progress.json",
        years=(2025,),
        limit=100,
        candidates=(candidate,),
        min_annual_return_percent=5.0,
        require_beat_benchmark=True,
    )

    assert submitted[0]["job_type"] == "stock.hk-pool-strategy-search"
    assert submitted[0]["payload"]["candidates"] == [
        {
            "key": "balanced-70",
            "name": "平衡 70 分",
            "score_threshold": 70,
            "score_profile": "balanced",
            "take_profit_percent": 5.0,
            "stop_loss_percent": 0.0,
            "max_holding_days": 20,
            "cost_rate": 0.01,
        }
    ]
