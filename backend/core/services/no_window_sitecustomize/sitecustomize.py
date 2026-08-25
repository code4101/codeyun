from __future__ import annotations

import os
import subprocess
from typing import Any

WINDOWS_CREATE_NO_WINDOW = 0x08000000


def _windows_startupinfo_hidden() -> Any:
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return startupinfo


def _install_no_window_popen_default() -> None:
    if os.name != "nt":
        return
    if os.getenv("CODEYUN_NO_WINDOW_SUBPROCESS_DEFAULT") != "1":
        return
    if getattr(subprocess.Popen, "_codeyun_no_window_default", False):
        return

    original_popen = subprocess.Popen
    original_init = original_popen.__init__

    def codeyun_no_window_init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | WINDOWS_CREATE_NO_WINDOW
        if kwargs.get("startupinfo") is None:
            kwargs["startupinfo"] = _windows_startupinfo_hidden()
        original_init(self, *args, **kwargs)

    codeyun_no_window_init.__name__ = getattr(original_init, "__name__", "__init__")
    codeyun_no_window_init.__qualname__ = getattr(original_init, "__qualname__", "Popen.__init__")
    codeyun_no_window_init.__module__ = getattr(original_init, "__module__", "subprocess")
    setattr(original_popen, "__init__", codeyun_no_window_init)
    setattr(original_popen, "_codeyun_no_window_default", True)
    setattr(original_popen, "_codeyun_no_window_original_init", original_init)


def _install_platform_wmi_processor_guard() -> None:
    """Avoid Python's optional WMI CPU probe in explicitly managed workers.

    Some Windows hosts return malformed or duplicated WMI rows.  Python 3.13's
    ``platform.processor()`` then raises while importing debugpy, which can kill
    an ipykernel before it answers its first kernel_info request.  Processor
    branding is not required by CodeYun workers, so use the stable environment
    value when the owning service explicitly opts in.
    """

    if os.name != "nt" or os.getenv("CODEYUN_SKIP_PLATFORM_WMI_PROCESSOR") != "1":
        return
    import platform

    processor = getattr(platform, "_Processor", None)
    if processor is None:
        return
    processor.get_win32 = staticmethod(lambda: os.getenv("PROCESSOR_IDENTIFIER", ""))


_install_no_window_popen_default()
_install_platform_wmi_processor_guard()
