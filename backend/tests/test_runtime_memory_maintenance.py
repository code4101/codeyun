"""类身份、旧绑定方法、安装门禁；不启动Kernel或访问游戏。"""
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.instrumentation import runtime_memory as runtime
from backend.core.fanxiu.instrumentation import runtime_memory_maintenance as maintenance


@pytest.fixture(autouse=True)
def restore_methods():
    methods = (runtime.MumuProcessMemory.readable_region, runtime.LuaJitReader.numeric_fields)
    originals = [(fn, fn.__code__, fn.__doc__) for fn in methods]
    yield
    for fn, code, doc in originals:
        fn.__code__, fn.__doc__ = code, doc


def test_installs_in_place_for_old_instances_and_previously_bound_methods():
    identities = (runtime.LuaRef, runtime.MemoryRegion, runtime.MumuProcessMemory,
                  runtime.LuaJitReader, runtime.MumuProcessMemory.readable_region)
    region = runtime.MemoryRegion(0x1000, 0x2000, 'rw')
    memory = runtime.MumuProcessMemory(pid=1, process_start_ticks=1, adb_serial='offline', regions=[region])
    # Simulate a pre-index instance and retained bound-method reference.
    for key in tuple(vars(memory)):
        if key.startswith('_region_'):
            delattr(memory, key)
    bound = memory.readable_region
    runtime.MumuProcessMemory.readable_region.__code__ = (lambda self, address, size=1: None).__code__
    assert bound(0x1100) is None
    plan = maintenance.refresh_runtime_memory_methods()
    assert bound(0x1100) is None  # Preflight is not installation.
    result = maintenance.refresh_runtime_memory_methods(
        apply=True, expected_source_sha256=plan['source_sha256'], exclusive_runtime_access=True)
    assert result['status'] == 'installed'
    assert bound(0x1100) is region
    assert identities == (runtime.LuaRef, runtime.MemoryRegion, runtime.MumuProcessMemory,
                          runtime.LuaJitReader, runtime.MumuProcessMemory.readable_region)


def test_rejects_stale_source_or_absent_exclusivity_before_mutation():
    plan = maintenance.refresh_runtime_memory_methods()
    before = runtime.LuaJitReader.numeric_fields.__code__
    for digest, exclusive in ((plan['source_sha256'], False), ('wrong', True)):
        with pytest.raises(RuntimeError, match='独占Runtime'):
            maintenance.refresh_runtime_memory_methods(
                apply=True, expected_source_sha256=digest, exclusive_runtime_access=exclusive)
    assert runtime.LuaJitReader.numeric_fields.__code__ is before


def test_rejects_active_runtime_reader(monkeypatch):
    plan = maintenance.refresh_runtime_memory_methods()
    frame = SimpleNamespace(f_globals=vars(runtime), f_back=None)
    monkeypatch.setattr(maintenance.sys, '_current_frames', lambda: {1: frame})
    with pytest.raises(RuntimeError, match='活跃Runtime'):
        maintenance.refresh_runtime_memory_methods(
            apply=True, expected_source_sha256=plan['source_sha256'], exclusive_runtime_access=True)
