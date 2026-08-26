from __future__ import annotations

from backend.core.fanxiu.instrumentation import xianyuan_duokui


class _Memory:
    pid = 123
    process_start_ticks = 456


def test_xianyuan_status_uses_cached_roots_and_exact_ids(monkeypatch) -> None:
    memory = _Memory()
    reader = object()
    calls: list[tuple] = []

    monkeypatch.setattr(
        xianyuan_duokui.MumuProcessMemory,
        "discover_cached",
        lambda **kwargs: calls.append(("memory", kwargs)) or memory,
    )
    monkeypatch.setattr(xianyuan_duokui, "LuaJitReader", lambda value: reader)

    def manager_root(*args, **kwargs):
        calls.append(("wallet_root", args, kwargs))
        kwargs["validate"](reader, 0x1000)
        return 0x1000, True

    monkeypatch.setattr(xianyuan_duokui, "resolve_manager_root", manager_root)
    monkeypatch.setattr(
        xianyuan_duokui,
        "wallet_currency_data",
        lambda current_reader, root, currency, **kwargs: (
            calls.append(("wallet", current_reader, root, currency, kwargs))
            or {
                "exchange_currency": 720,
                "cumulative_currency": 760,
            }
        ),
    )
    monkeypatch.setattr(
        xianyuan_duokui,
        "resolve_activity_rank_root",
        lambda *args, **kwargs: (0x2000, True),
    )
    monkeypatch.setattr(
        xianyuan_duokui,
        "load_activity_rank_reward_tiers",
        lambda **kwargs: [
            {"rank_start": 257, "rank_end": 512, "rewards": []}
        ],
    )
    monkeypatch.setattr(
        xianyuan_duokui,
        "_rank_data",
        lambda current_reader, root, activity_id, **kwargs: {
            "rank": 321,
            "score": 800,
            "rankings": [],
        },
    )

    snapshot = xianyuan_duokui.read_xianyuan_duokui_status_snapshot(
        event_date="2026-08-26"
    )

    assert snapshot["rank_activity_id"] == 46003
    assert snapshot["currency_type"] == 23002
    assert snapshot["rank"] == 321
    assert snapshot["exchange_currency"] == 720
    assert snapshot["evidence"]["discovery_allowed"] is False
    assert calls[0] == ("memory", {"fallback_to_discovery": False})
    assert calls[1][2]["allow_discovery"] is False
    wallet_calls = [call for call in calls if call[0] == "wallet"]
    assert len(wallet_calls) == 2
    assert all(call[-1] == {"missing_as_zero": True} for call in wallet_calls)
