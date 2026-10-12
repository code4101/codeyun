"""纯地址区间查找契约；无进程、ADB或模拟游戏。"""
import random

import pytest

from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError, MemoryRegion, MumuProcessMemory


def memory(regions):
    return MumuProcessMemory(pid=1, process_start_ticks=1, adb_serial='test', regions=regions)


def linear(regions, address, size):
    return next((r for r in regions if 'r' in r.permissions and r.contains(address, size)), None)


def test_readable_span_accepts_adjacent_readable_maps_without_changing_region_lookup():
    reader = memory([MemoryRegion(0x1000, 0x3000, 'rw-p'), MemoryRegion(0x3000, 0x5000, 'r--p')])
    assert reader.readable_region(0x2fe0, 96) is None
    span = reader.readable_span(0x2fe0, 96)
    assert span is not None and span.contains(0x2fe0, 96)


@pytest.mark.parametrize('second', [MemoryRegion(0x3100, 0x5000, 'r'), MemoryRegion(0x3000, 0x5000, '-w')])
def test_readable_span_rejects_holes_and_nonreadable_neighbors(second):
    assert memory([MemoryRegion(0x1000, 0x3000, 'r'), second]).readable_span(0x2fe0, 96) is None


def test_span_uses_fresh_replaced_mapping_metadata():
    reader = memory([MemoryRegion(0x1000, 0x3000, 'r'), MemoryRegion(0x3000, 0x5000, 'r')])
    assert reader.readable_span(0x2fe0, 96) is not None
    reader.regions = (MemoryRegion(0x1000, 0x3000, 'r'), MemoryRegion(0x3000, 0x5000, '-w'))
    assert reader.readable_span(0x2fe0, 96) is None


def test_read_across_adjacent_maps_and_revoked_permission_cache(monkeypatch):
    reader = memory([MemoryRegion(0x1000, 0x2000, 'r'), MemoryRegion(0x2000, 0x3000, 'r')])
    calls = []
    def transport(spans, **kwargs):
        calls.append(spans)
        return b'x' * kwargs['expected_size']
    monkeypatch.setattr(reader, '_read_transport', transport)
    assert reader.read(0x1fe0, 96) == b'x' * 96
    assert len(calls) == 1
    reader.regions = (MemoryRegion(0x1000, 0x2000, 'r'), MemoryRegion(0x2000, 0x3000, '-w'))
    with pytest.raises(FanxiuRuntimeMemoryError):
        reader.read(0x1fe0, 96)
    assert len(calls) == 1


def test_read_across_mapping_hole_never_calls_transport(monkeypatch):
    reader = memory([MemoryRegion(0x1000, 0x2000, 'r'), MemoryRegion(0x2100, 0x3000, 'r')])
    def forbidden(*args, **kwargs):
        pytest.fail('unmapped span reached transport')
    monkeypatch.setattr(reader, '_read_transport', forbidden)
    with pytest.raises(FanxiuRuntimeMemoryError):
        reader.read(0x1fe0, 96)


@pytest.mark.parametrize('regions', [
    [],
    [MemoryRegion(20, 30, 'r'), MemoryRegion(10, 20, 'r'), MemoryRegion(30, 40, '-w')],
    [MemoryRegion(0, 30, '-w'), MemoryRegion(10, 20, 'r')],
    [MemoryRegion(10, 30, 'r'), MemoryRegion(0, 25, 'r')],
    [MemoryRegion(10, 20, 'r'), MemoryRegion(10, 30, 'r')],
    [MemoryRegion(10, 10, 'r'), MemoryRegion(20, 10, 'r')],
])
def test_lookup_matches_first_readable_region_including_endpoints(regions):
    reader = memory(regions)
    for address in range(-1, 42):
        for size in (-1, 0, 1, 8, 10, 50):
            assert reader.readable_region(address, size) is linear(regions, address, size)


def test_unordered_disjoint_maps_and_random_queries_are_equivalent():
    rng = random.Random(610)
    regions = [MemoryRegion(i * 4096, i * 4096 + 2048, 'r' if i % 4 else '-w') for i in range(100)]
    rng.shuffle(regions)
    reader = memory(regions)
    for _ in range(2000):
        address, size = rng.randrange(-100, 410000), rng.choice((0, 1, 64, 2048, 4096))
        assert reader.readable_region(address, size) is linear(regions, address, size)


def test_replaced_maps_permissions_and_mutable_input_invalidate_index():
    reader = memory([MemoryRegion(0, 100, 'r')])
    assert reader.readable_region(1) is not None
    reader.regions = (MemoryRegion(0, 100, '-w'), MemoryRegion(200, 300, 'r'))
    assert reader.readable_region(1) is None
    assert reader.readable_region(201) is reader.regions[1]
    reader.regions = [MemoryRegion(400, 500, 'r')]
    assert reader.readable_region(401) is reader.regions[0]
    reader.regions[:] = [MemoryRegion(600, 700, 'r')]
    assert reader.readable_region(401) is None
    assert reader.readable_region(601) is reader.regions[0]
