from __future__ import annotations

from collections import deque

from scripts.generate_zaohua_lianqi_map import (
    MAP_SIZE,
    TERRAIN_BRIDGE,
    TERRAIN_CITY,
    TERRAIN_ROAD,
    generate_lianqi_map,
)


def _positions(detail: dict) -> dict[tuple[int, int], dict]:
    return {
        (row["pos"]["x"], row["pos"]["y"]): row
        for row in detail["mapInfoStoList"]
    }


def test_lianqi_map_is_seeded_and_has_native_map_shape() -> None:
    first = generate_lianqi_map(4101001)
    repeated = generate_lianqi_map(4101001)
    different = generate_lianqi_map(4101002)

    assert first == repeated
    assert first != different
    assert first["mapCfg"]["size"] == {"x": MAP_SIZE, "y": MAP_SIZE}
    assert len(first["mapInfoStoList"]) == MAP_SIZE * MAP_SIZE
    assert len(_positions(first)) == MAP_SIZE * MAP_SIZE
    assert first["npcPlaceCfgList"] == []
    assert first["mapObjectStoList"] == []


def test_lianqi_map_has_one_city_and_non_overlapping_places() -> None:
    detail = generate_lianqi_map(4101001)
    positions = _positions(detail)
    occupied = [row for row in positions.values() if row["placeStoId"]]

    assert len(detail["placeStoList"]) == 10
    assert detail["placeStoList"][0]["name"] == "仙缘城"
    assert sum(row["terrainId"] == TERRAIN_CITY for row in occupied) == 16
    assert len({(row["pos"]["x"], row["pos"]["y"]) for row in occupied}) == len(occupied)
    assert {row["placeStoId"] for row in occupied} == set(range(1, 11))


def test_all_places_reach_xianyuan_city_through_generated_world() -> None:
    detail = generate_lianqi_map(4101001)
    positions = _positions(detail)
    city = detail["placeStoList"][0]["centerPos"]
    start = (city["x"], city["y"])
    queue = deque([start])
    visited = {start}
    while queue:
        x, y = queue.popleft()
        for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            row = positions.get(nxt)
            if row is None or nxt in visited:
                continue
            visited.add(nxt)
            queue.append(nxt)

    centers = {
        (place["centerPos"]["x"], place["centerPos"]["y"])
        for place in detail["placeStoList"]
    }
    assert centers <= visited
    assert any(row["terrainId"] == TERRAIN_ROAD for row in positions.values())
    assert any(row["terrainId"] == TERRAIN_BRIDGE for row in positions.values())
