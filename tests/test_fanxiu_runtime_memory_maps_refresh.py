"""Maps-refresh boundary contract of MumuProcessMemory.readable_span.

Pure address-space bookkeeping: only the /proc file reader is replaced.
"""

from backend.core.fanxiu.instrumentation import runtime_memory as rm


def _stat(start_ticks: int) -> str:
    # Field 22 of /proc/<pid>/stat (index 19 after the comm suffix).
    return "1 (game) " + " ".join(["0"] * 19 + [str(start_ticks)])


def _memory(monkeypatch, *, start_ticks: int, maps_text: str):
    calls = []

    def fake_read(name, *, pid=None, timeout_s=8, expected_serial=None):
        calls.append(name)
        return (_stat(start_ticks) if name == "stat" else maps_text), {}

    monkeypatch.setattr(rm.mumu_control, "read_mumu_adb_runtime_process_file", fake_read)
    memory = rm.MumuProcessMemory(
        pid=7, process_start_ticks=100, adb_serial="s",
        regions=[rm.MemoryRegion(0x1000, 0x2000, "rw-p")],
    )
    return memory, calls


def test_new_arena_is_found_after_one_refresh(monkeypatch):
    memory, calls = _memory(
        monkeypatch, start_ticks=100,
        maps_text="1000-2000 rw-p 0 0 0\n8000-9000 rw-p 0 0 0\n",
    )
    assert memory.readable_span(0x8010, 24) is not None
    assert calls == ["stat", "maps"]
    # Still-unmapped addresses stay unreadable and do not refresh again.
    assert memory.readable_span(0x50000, 8) is None
    assert calls == ["stat", "maps"]


def test_changed_process_identity_never_adopts_new_maps(monkeypatch):
    memory, calls = _memory(
        monkeypatch, start_ticks=999, maps_text="8000-9000 rw-p 0 0 0\n",
    )
    assert memory.readable_span(0x8010, 24) is None
    assert calls == ["stat"]
