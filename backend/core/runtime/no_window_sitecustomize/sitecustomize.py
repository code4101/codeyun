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

    class CodeYunNoWindowPopen(original_popen):  # type: ignore[misc, valid-type]
        _codeyun_no_window_default = True

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | WINDOWS_CREATE_NO_WINDOW
            if kwargs.get("startupinfo") is None:
                kwargs["startupinfo"] = _windows_startupinfo_hidden()
            super().__init__(*args, **kwargs)

    CodeYunNoWindowPopen.__name__ = getattr(original_popen, "__name__", "Popen")
    CodeYunNoWindowPopen.__qualname__ = getattr(original_popen, "__qualname__", "Popen")
    CodeYunNoWindowPopen.__module__ = getattr(original_popen, "__module__", "subprocess")
    subprocess.Popen = CodeYunNoWindowPopen  # type: ignore[assignment]


_install_no_window_popen_default()
