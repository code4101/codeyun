from backend.core.fanxiu.runtime import mumu_control


def test_native_shake_repeats_with_bounded_interval(monkeypatch):
    calls = []
    sleeps = []
    monkeypatch.setattr(
        mumu_control,
        "_run_mumu_manager_json",
        lambda args, **kwargs: calls.append((args, kwargs)) or {},
    )
    monkeypatch.setattr(mumu_control.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = mumu_control.shake_mumu_device(
        vmindex="3", repeats=3, interval_seconds=0.25
    )

    assert result == {
        "status": "sent", "vmindex": "3", "repeats": 3,
        "interval_seconds": 0.25,
    }
    assert [args for args, _kwargs in calls] == [
        ["control", "--vmindex", "3", "tool", "func", "--name", "shake"]
    ] * 3
    assert sleeps == [0.25, 0.25]
