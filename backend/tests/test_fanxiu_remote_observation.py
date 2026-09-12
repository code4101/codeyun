"""Only deterministic input and no-device-fallback contracts; no fake game."""
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.core.fanxiu.remote.observation import plan_remote_observation


@pytest.mark.parametrize("data,job,target", [
    (b"", "observe", None),
    (b"not an image", "observe", None),
    (b"x", "navigate", None),
    (b"x", "navigate", 0),
    (b"x", "arbitrary_cell", None),
    (b"x" * (6 * 1024 * 1024 + 1), "observe", None),
], ids=["empty", "invalid_image", "missing_target", "invalid_target", "unknown_job", "oversize"])
def test_invalid_inputs_are_rejected(data, job, target):
    with pytest.raises(ValueError):
        plan_remote_observation(data, job_id=job, target_scene_id=target)


def test_non_screenshot_format_rejected():
    stream = BytesIO()
    Image.new("RGB", (2, 2)).save(stream, "GIF")
    with pytest.raises(ValueError, match="PNG/JPEG"):
        plan_remote_observation(stream.getvalue(), job_id="observe")


def test_supplied_frame_never_falls_back_to_device(tmp_path, monkeypatch):
    from backend.core.fanxiu.client import mumu_control

    reference = tmp_path / "reference.png"
    Image.new("RGB", (8, 8)).save(reference)
    monkeypatch.setattr(mumu_control, "resolve_data_annotation_image_asset", lambda *a, **kw:
                        SimpleNamespace(exists=True, path=reference))

    def forbidden_capture(**kwargs):
        pytest.fail("external input must never capture the server device")

    monkeypatch.setattr(mumu_control, "capture_mumu_window_frame", forbidden_capture)
    with pytest.raises(ValueError, match="禁止回退设备截图"):
        mumu_control.match_fanxiu_screenshot_box_frame(
            filename="reference.png", box={"x": 0, "y": 0, "w": 8, "h": 8},
            current_frame_data_url=None, require_supplied_frame=True,
            save_match_frame=False,
        )
