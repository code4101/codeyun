from __future__ import annotations

import os
import platform


def _disable_windows_platform_wmi_probe() -> None:
    if os.name != "nt":
        return
    wmi_query = getattr(platform, "_wmi_query", None)
    if wmi_query is None:
        return

    def _no_wmi_query(*args, **kwargs):
        _ = args, kwargs
        return ("", "1", "", "", "")

    platform._wmi_query = _no_wmi_query


_disable_windows_platform_wmi_probe()
