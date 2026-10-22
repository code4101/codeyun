"""Per-client input gate over a projection driver (composition, no global patching).

:class:`GuardedDriver` wraps one driver instance for one client.  Every GUI
input (``input``, pointer parking) passes the same gate exactly once, in
this order: lease check, the caller's profile ``gate`` on the input frame,
``account.pace()`` (waits the action gap, re-checks the lease, records the
real permission moment), the ``gate`` again, generation increment, then one
delegation to the wrapped driver whose completion is recorded as account
activity in ``finally`` (also when it raises).  Nothing is retried; the
wrapped driver keeps its own input token / geometry fence.

``generation`` counts submitted inputs; capability holders compare it to
detect that any other input happened since they observed.

Read-only seams (frame, OCR, window list, window geometry, native
documents/controls, clipboard, profile facts) are forwarded; the ones touching the desktop check the lease
first.  ``close_window`` passes the same gate as an input (action ``close``)
only when called with the frame it was verified on; the legacy frame-less
call stays refused.  Handing URLs to the scheme handler is refused.
"""

from __future__ import annotations

from typing import Callable

from .client import StepFailed
from .ownership import NativeAccountHandle

PARK_POINTER = {"action": "move", "x": .995, "y": .995}


class GuardedDriver:
    """See the module docstring; ``gate(frame, action)`` raises to refuse an input."""

    def __init__(self, inner: object, *, account: NativeAccountHandle,
                 gate: Callable[[dict, dict], None]):
        self.inner = inner
        self.account = account
        self.gate = gate
        self.generation = 0

    def _guarded(self, frame: dict, action: dict, delegate: Callable[[], object]) -> object:
        self.account.check()
        self.gate(frame, action)
        self.account.pace()
        self.gate(frame, action)  # facts or an active capability may have aged while pacing
        self.generation += 1
        try:
            return delegate()
        finally:
            self.account.record_activity()  # conservative: the input may have landed before raising

    def input(self, frame: dict, **action: object) -> dict:
        return self._guarded(frame, action, lambda: self.inner.input(frame, **action))

    def park_pointer(self, frame: dict) -> dict:
        """Pointer-only move to the client's bottom-right edge (off hover cards); never clicks."""
        return self.input(frame, **PARK_POINTER)

    def activate(self, window_id: str) -> dict:
        self.account.check()
        return self.inner.activate(window_id)

    def frame(self, window_id: str) -> dict:
        self.account.check()  # capture may restore/repaint the window
        return self.inner.frame(window_id)

    def native_documents(self, window_id: str) -> dict:
        self.account.check()
        return self.inner.native_documents(window_id)

    def native_controls(self, window_id: str, **bounded: object) -> dict:
        """Read-only bounded UIA control values; lease-checked, no input, pace or generation change."""
        self.account.check()
        return self.inner.native_controls(window_id, **bounded)

    def window_geometry(self, window_id: str) -> dict:
        """Read-only public window geometry; lease-checked, no input, pace or generation change."""
        self.account.check()
        return self.inner.window_geometry(window_id)

    def clipboard_text(self) -> str | None:
        self.account.check()
        return self.inner.clipboard_text()

    def close_window(self, window_id: str, expected_title: str, *, frame: dict | None = None) -> bool:
        """One gated native close of the window ``frame`` was captured from; the inner driver keeps its
        exact-title (and window generation) fence and is called exactly once, never retried."""
        if frame is None or frame.get("window_id") != window_id:
            raise StepFailed("close_window_not_declared")
        action = {"action": "close", "title": expected_title}
        return bool(self._guarded(frame, action, lambda: self.inner.close_window(window_id, expected_title)))

    def open_scheme(self, url: str) -> None:
        raise StepFailed("open_scheme_not_supported")

    def list_windows(self) -> list[dict]:
        return self.inner.list_windows()

    def ocr(self, frame: dict, **options: object) -> dict:
        return self.inner.ocr(frame, **options)

    def client_version(self, window_id: str) -> str:
        return self.inner.client_version(window_id)

    def window_dpi(self, window_id: str) -> int:
        return self.inner.window_dpi(window_id)


__all__ = ["GuardedDriver", "PARK_POINTER"]
