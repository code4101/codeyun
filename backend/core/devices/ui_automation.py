from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def ensure_ui_automation_thread_context() -> Iterator[None]:
    """Initialize UI Automation for worker threads before touching wxautox/UIA."""

    if sys.platform != "win32":
        yield
        return

    from wxautox.uiautomation import (
        InitializeUIAutomationInCurrentThread,
        UninitializeUIAutomationInCurrentThread,
    )

    InitializeUIAutomationInCurrentThread()
    try:
        yield
    finally:
        UninitializeUIAutomationInCurrentThread()
