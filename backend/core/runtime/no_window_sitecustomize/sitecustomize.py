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


_install_no_window_popen_default()
