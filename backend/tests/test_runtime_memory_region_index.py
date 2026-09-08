"""纯地址区间查找契约；无进程、ADB或模拟游戏。"""
import random

import pytest

from backend.core.fanxiu.instrumentation.runtime_memory import MemoryRegion, MumuProcessMemory


def memory(regions):
    return MumuProcessMemory(pid=1, process_start_ticks=1, adb_serial='test', regions=regions)


def linear(regions, address, size):
    return next((r for r in regions if 'r' in r.permissions and r.contains(address, size)), None)


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
