import pytest

from backend.core.fanxiu.data_annotation.maintenance import maintenance_startup_frame_pending


@pytest.mark.parametrize("active,message,expected", [
    (True, "400: MuMu ADB截图疑似黑屏：{'black': True}", True),
    (True, "MuMu ADB screencap looks black", True),
    (False, "MuMu ADB截图疑似黑屏", False),
    (None, "MuMu ADB截图疑似黑屏", False),
    (True, "permission denied", False),
    (True, "MuMu ADB截图疑似黑屏; permission denied", False),
    (True, "ADB read timed out", False),
    (True, "unknown scene", False),
])
def test_only_known_black_frame_in_active_grace_can_defer(active, message, expected):
    assert maintenance_startup_frame_pending(RuntimeError(message), {"active": active}) is expected
