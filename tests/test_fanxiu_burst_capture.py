from __future__ import annotations

import numpy as np

from backend.core.fanxiu.runtime import mumu_control


def test_burst_frame_rejects_near_black_placeholder(tmp_path, monkeypatch):
    frame = np.zeros((1600, 900, 3), dtype=np.uint8)
    frame[20:50, 20:50] = 255
    monkeypatch.setattr(mumu_control, "get_fanxiu_burst_frame_dir", lambda: tmp_path)
    monkeypatch.setattr(mumu_control, "_decode_image_data_url_bgr", lambda _value: frame)

    result = mumu_control.save_fanxiu_burst_frame(current_frame_data_url="data:image/png;base64,placeholder")

    assert result["saved"] is False
    assert result["skipped"] is True
    assert result["reason"] == "unusable_frame"
    assert list(tmp_path.iterdir()) == []


def test_burst_frame_retries_bad_cached_capture(tmp_path, monkeypatch):
    bad = np.zeros((1600, 900, 3), dtype=np.uint8)
    good = np.full((1600, 900, 3), 80, dtype=np.uint8)
    calls: list[bool] = []

    def fake_capture(**kwargs):
        calls.append(kwargs["prefer_cached"])
        return bad if kwargs["prefer_cached"] else good

    monkeypatch.setattr(mumu_control, "get_fanxiu_burst_frame_dir", lambda: tmp_path)
    monkeypatch.setattr(mumu_control, "capture_mumu_window_frame", fake_capture)

    result = mumu_control.save_fanxiu_burst_frame()

    assert calls == [True, False]
    assert result["saved"] is True
    assert (tmp_path / "0001.png").is_file()


def test_burst_frame_quality_keeps_dark_game_scene():
    frame = np.zeros((1600, 900, 3), dtype=np.uint8)
    frame[:, :300] = (35, 20, 10)
    frame[200:1400, 300:800] = (40, 50, 70)

    summary = mumu_control._burst_frame_unusable_summary(frame)

    assert summary["unusable"] is False
