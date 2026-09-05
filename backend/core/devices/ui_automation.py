from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def ensure_ui_automation_thread_context() -> Iterator[None]:
    """Initialize UI Automation before a Windows worker thread touches UIA."""

    if sys.platform != "win32":
        yield
        return

    from uiautomation import (
        InitializeUIAutomationInCurrentThread,
        UninitializeUIAutomationInCurrentThread,
    )

    InitializeUIAutomationInCurrentThread()
    try:
        yield
    finally:
        UninitializeUIAutomationInCurrentThread()
