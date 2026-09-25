"""兼容旧导入；通用整数控件由 runtime_gui.integer_count_control 统一提供。"""

from backend.core.fanxiu.runtime_gui.integer_count_control import (
    IntegerCountAssets,
    IntegerButtonAssets,
    IntegerSliderAssets,
    read_positive_integer_count,
    read_integer_slider_count,
    set_verified_integer_slider_count,
    set_verified_integer_button_count,
    set_minimum_then_increment_count,
)

__all__ = ['IntegerCountAssets', 'IntegerButtonAssets', 'IntegerSliderAssets', 'read_positive_integer_count', 'read_integer_slider_count', 'set_verified_integer_slider_count', 'set_verified_integer_button_count', 'set_minimum_then_increment_count']
