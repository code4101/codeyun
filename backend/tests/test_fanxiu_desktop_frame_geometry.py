"""桌面适配是纯几何契约，与 Android 逻辑分辨率正交。"""
from backend.core.fanxiu.windows_info_window import ScreenRect, calculate_fitted_window_rect


def test_portrait_window_fits_work_area_with_toolbar_and_border():
    work = ScreenRect(0, 0, 1920, 1040)
    fitted = calculate_fitted_window_rect(work, ScreenRect(1600, 700, 2200, 1800))
    assert fitted.right <= work.right and fitted.bottom <= work.bottom
    assert fitted.height <= int(work.height * .9)
    # Only the desktop shell scales. Game content retains its 900:1600 ratio.
    assert abs((fitted.width - 8) / (fitted.height - 44) - 900 / 1600) < .002


def test_fit_supports_secondary_monitor_negative_coordinates():
    work = ScreenRect(-1280, -200, 0, 824)
    fitted = calculate_fitted_window_rect(work, ScreenRect(-1100, -150, -200, 1450))
    assert fitted.left == -1100 and fitted.top == -150
    assert work.left <= fitted.left < fitted.right <= work.right
    assert work.top <= fitted.top < fitted.bottom <= work.bottom
