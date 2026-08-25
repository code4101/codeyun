from __future__ import annotations

import argparse
import copy
import hashlib
import heapq
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.temp_paths import codeyun_temp_root


MAP_SIZE = 28
SOURCE_BUILD_ID = "24123658"
DEFAULT_SEED = 4_101_001

TERRAIN_PLAIN = 1
TERRAIN_BAMBOO = 2
TERRAIN_FOREST = 3
TERRAIN_MOUNTAIN = 4
TERRAIN_LAKE = 5
TERRAIN_OCEAN = 6
TERRAIN_GRASS = 14
TERRAIN_ROAD = 15
TERRAIN_RIVER = 17
TERRAIN_REST = 18
TERRAIN_BRIDGE = 22
TERRAIN_VILLAGE = 101
TERRAIN_TOWN = 102
TERRAIN_CITY = 103
TERRAIN_SECT = 104
TERRAIN_CAVE = 201
TERRAIN_TOMB = 202

TERRAIN_COLORS = {
    TERRAIN_PLAIN: "#B6CB87",
    TERRAIN_BAMBOO: "#6E9F68",
    TERRAIN_FOREST: "#47785A",
    TERRAIN_MOUNTAIN: "#8A8178",
    TERRAIN_LAKE: "#6096B4",
    TERRAIN_OCEAN: "#477D9F",
    TERRAIN_GRASS: "#CAD89A",
    TERRAIN_ROAD: "#C9AC7A",
    TERRAIN_RIVER: "#4F91B5",
    TERRAIN_REST: "#D8C98C",
    TERRAIN_BRIDGE: "#9D704B",
    TERRAIN_VILLAGE: "#D5B27A",
    TERRAIN_TOWN: "#C68C62",
    TERRAIN_CITY: "#B66A55",
    TERRAIN_SECT: "#876CA6",
    TERRAIN_CAVE: "#625F63",
    TERRAIN_TOMB: "#756A72",
}

TERRAIN_LABELS = {
    TERRAIN_PLAIN: "平原",
    TERRAIN_BAMBOO: "竹林",
    TERRAIN_FOREST: "森林",
    TERRAIN_MOUNTAIN: "山地",
    TERRAIN_LAKE: "湖泊",
    TERRAIN_OCEAN: "海洋",
    TERRAIN_GRASS: "草地",
    TERRAIN_ROAD: "道路",
    TERRAIN_RIVER: "河流",
    TERRAIN_REST: "休息点",
    TERRAIN_BRIDGE: "桥梁",
    TERRAIN_VILLAGE: "村庄",
    TERRAIN_TOWN: "城镇",
    TERRAIN_CITY: "城市",
    TERRAIN_SECT: "小宗门",
    TERRAIN_CAVE: "洞穴",
    TERRAIN_TOMB: "古墓",
}

PLACE_SPECS = (
    ("仙缘城", 213, TERRAIN_CITY, 4, False),
    ("青溪镇", 102, TERRAIN_TOWN, 3, False),
    ("望山镇", 102, TERRAIN_TOWN, 3, False),
    ("柳河村", 101, TERRAIN_VILLAGE, 2, False),
    ("白石村", 101, TERRAIN_VILLAGE, 2, False),
    ("栖云村", 101, TERRAIN_VILLAGE, 2, False),
    ("青竹门", 217, TERRAIN_SECT, 1, False),
    ("落霞洞", 201, TERRAIN_CAVE, 1, True),
    ("寒潭洞", 201, TERRAIN_CAVE, 1, True),
    ("古修遗冢", 100, TERRAIN_TOMB, 1, True),
)


def _blank_map_detail(seed: int) -> dict[str, Any]:
    return {
        "mapCfg": {
            "uuid": f"code4101_lianqi_{seed}",
            "modId": "code4101.zaohua.tiandao",
            "name": "炼气试炼·随机余州",
            "groupId": "code4101_lianqi",
            "mapType": 0,
            "randomType": 0,
            "validCnt": 0,
            "fly": 1,
            "flag": 0,
            "sceneId": 4001,
            "riverSceneId": 4003,
            "defTerrainId": TERRAIN_GRASS,
            "size": {"x": MAP_SIZE, "y": MAP_SIZE},
            "borderSize": {"x": 3, "y": 3},
            "filterColor": {"r": 1, "g": 1, "b": 1, "a": 0.6380989},
            "backType": 0,
            "isIgnoreMoveCost": False,
            "isNoAllowLeave": False,
            "safetyPotion": None,
            "safetyRange": 0,
        },
        "mapTileCfgList": [],
        "npcPlaceCfgList": [],
        "startPosition": {"x": MAP_SIZE // 2, "y": MAP_SIZE // 2},
        "mapInfoStoList": [],
        "mapObjectStoList": [],
        "placeStoList": [],
        "placeLogList": [],
        "code4101Prototype": {
            "schemaVersion": 1,
            "seed": seed,
            "sourceBuildId": SOURCE_BUILD_ID,
            "sourceMapUuid": "1",
            "scope": "terrain-road-place-layout-only",
        },
    }


def _paint_cluster(
    grid: list[list[int]],
    rng: random.Random,
    terrain_id: int,
    *,
    count: int,
    radius_range: tuple[int, int],
) -> None:
    for _ in range(count):
        center_x = rng.randint(2, MAP_SIZE - 3)
        center_y = rng.randint(2, MAP_SIZE - 3)
        radius = rng.randint(*radius_range)
        for x in range(max(1, center_x - radius), min(MAP_SIZE - 1, center_x + radius + 1)):
            for y in range(max(1, center_y - radius), min(MAP_SIZE - 1, center_y + radius + 1)):
                distance = abs(x - center_x) + abs(y - center_y)
                if distance <= radius + rng.choice((0, 0, 1)):
                    grid[x][y] = terrain_id


def _paint_water(grid: list[list[int]], rng: random.Random) -> None:
    coast_on_left = bool(rng.getrandbits(1))
    for y in range(MAP_SIZE):
        depth = 1 + int(2 * (1 + rng.random()) * (0.5 + 0.5 * abs(y - 14) / 14))
        for offset in range(depth):
            x = offset if coast_on_left else MAP_SIZE - 1 - offset
            grid[x][y] = TERRAIN_OCEAN

    lake_x = rng.randint(5, MAP_SIZE - 6)
    lake_y = rng.randint(5, MAP_SIZE - 6)
    for x in range(lake_x - 2, lake_x + 3):
        for y in range(lake_y - 2, lake_y + 3):
            if abs(x - lake_x) + abs(y - lake_y) <= 3:
                grid[x][y] = TERRAIN_LAKE


def _paint_river(grid: list[list[int]], rng: random.Random) -> set[tuple[int, int]]:
    river: set[tuple[int, int]] = set()
    x = rng.randint(7, MAP_SIZE - 8)
    for y in range(MAP_SIZE):
        x = max(3, min(MAP_SIZE - 4, x + rng.choice((-1, 0, 0, 0, 1))))
        river.add((x, y))
        grid[x][y] = TERRAIN_RIVER
        if rng.random() < 0.22 and x + 1 < MAP_SIZE - 2:
            river.add((x + 1, y))
            grid[x + 1][y] = TERRAIN_RIVER
    return river


def _footprint(center: tuple[int, int], size: int) -> list[tuple[int, int]]:
    x, y = center
    if size == 1:
        return [(x, y)]
    start = -(size // 2)
    return [(x + dx, y + dy) for dx in range(start, start + size) for dy in range(start, start + size)]


def _choose_position(
    rng: random.Random,
    grid: list[list[int]],
    occupied: set[tuple[int, int]],
    size: int,
    *,
    near_center: bool = False,
) -> tuple[int, int]:
    candidates: list[tuple[int, int]] = []
    for x in range(3, MAP_SIZE - 3):
        for y in range(3, MAP_SIZE - 3):
            cells = _footprint((x, y), size)
            if any(cell in occupied for cell in cells):
                continue
            if any(grid[cx][cy] in {TERRAIN_OCEAN, TERRAIN_LAKE, TERRAIN_RIVER} for cx, cy in cells):
                continue
            if occupied and min(abs(x - ox) + abs(y - oy) for ox, oy in occupied) < size + 3:
                continue
            if near_center and abs(x - MAP_SIZE // 2) + abs(y - MAP_SIZE // 2) > 6:
                continue
            candidates.append((x, y))
    if not candidates:
        raise RuntimeError(f"无法为 {size}x{size} 地点找到合法位置")
    return rng.choice(candidates)


def _neighbors(pos: tuple[int, int]) -> Iterable[tuple[int, int]]:
    x, y = pos
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < MAP_SIZE and 0 <= ny < MAP_SIZE:
            yield nx, ny


def _find_path(
    grid: list[list[int]],
    start: tuple[int, int],
    goal: tuple[int, int],
    rng: random.Random,
) -> list[tuple[int, int]]:
    costs = {
        TERRAIN_OCEAN: 18,
        TERRAIN_LAKE: 12,
        TERRAIN_RIVER: 6,
        TERRAIN_MOUNTAIN: 4,
        TERRAIN_FOREST: 2,
        TERRAIN_BAMBOO: 2,
    }
    queue: list[tuple[float, tuple[int, int]]] = [(0, start)]
    came_from: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    best = {start: 0.0}
    while queue:
        _, current = heapq.heappop(queue)
        if current == goal:
            break
        adjacent = list(_neighbors(current))
        rng.shuffle(adjacent)
        for nxt in adjacent:
            terrain = grid[nxt[0]][nxt[1]]
            new_cost = best[current] + costs.get(terrain, 1)
            if new_cost >= best.get(nxt, float("inf")):
                continue
            best[nxt] = new_cost
            priority = new_cost + abs(nxt[0] - goal[0]) + abs(nxt[1] - goal[1])
            heapq.heappush(queue, (priority, nxt))
            came_from[nxt] = current
    if goal not in came_from:
        raise RuntimeError(f"地点之间无路径：{start} -> {goal}")
    path: list[tuple[int, int]] = []
    current: tuple[int, int] | None = goal
    while current is not None:
        path.append(current)
        current = came_from[current]
    return list(reversed(path))


def _place_layout(
    grid: list[list[int]], rng: random.Random
) -> tuple[list[dict[str, Any]], set[tuple[int, int]]]:
    occupied: set[tuple[int, int]] = set()
    places: list[dict[str, Any]] = []
    for index, (name, place_id, terrain_id, size, hidden) in enumerate(PLACE_SPECS, start=1):
        center = _choose_position(rng, grid, occupied, size, near_center=index == 1)
        cells = _footprint(center, size)
        occupied.update(cells)
        places.append(
            {
                "id": index,
                "placeId": place_id,
                "centerPos": {"x": center[0], "y": center[1]},
                "namePos": {"x": center[0], "y": center[1]},
                "isHideName": int(hidden),
                "name": name,
                "nameId": 0,
                "childMapId": None,
                "childMapPos": {"x": -2147483648, "y": -2147483648},
                "placeLogId": 0,
                "tileIndex": 1,
                "_terrainId": terrain_id,
                "_footprint": cells,
            }
        )
    return places, occupied


def _connect_places(
    grid: list[list[int]], places: list[dict[str, Any]], rng: random.Random
) -> set[tuple[int, int]]:
    city = places[0]
    city_center = (city["centerPos"]["x"], city["centerPos"]["y"])
    roads: set[tuple[int, int]] = set()
    for place in places[1:]:
        center = (place["centerPos"]["x"], place["centerPos"]["y"])
        for x, y in _find_path(grid, center, city_center, rng):
            if grid[x][y] == TERRAIN_RIVER:
                grid[x][y] = TERRAIN_BRIDGE
            elif grid[x][y] not in {
                TERRAIN_OCEAN,
                TERRAIN_LAKE,
                TERRAIN_CITY,
                TERRAIN_TOWN,
                TERRAIN_VILLAGE,
                TERRAIN_SECT,
                TERRAIN_CAVE,
                TERRAIN_TOMB,
            }:
                grid[x][y] = TERRAIN_ROAD
            roads.add((x, y))
    return roads


def _stamp_places(grid: list[list[int]], places: list[dict[str, Any]]) -> dict[tuple[int, int], int]:
    place_by_cell: dict[tuple[int, int], int] = {}
    for place in places:
        for x, y in place["_footprint"]:
            grid[x][y] = place["_terrainId"]
            place_by_cell[(x, y)] = place["id"]
    return place_by_cell


def generate_lianqi_map(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    rng = random.Random(seed)
    detail = _blank_map_detail(seed)
    grid = [[TERRAIN_GRASS for _ in range(MAP_SIZE)] for _ in range(MAP_SIZE)]

    _paint_cluster(grid, rng, TERRAIN_PLAIN, count=7, radius_range=(3, 5))
    _paint_cluster(grid, rng, TERRAIN_FOREST, count=6, radius_range=(2, 4))
    _paint_cluster(grid, rng, TERRAIN_BAMBOO, count=3, radius_range=(2, 3))
    _paint_cluster(grid, rng, TERRAIN_MOUNTAIN, count=5, radius_range=(2, 4))
    _paint_water(grid, rng)
    _paint_river(grid, rng)

    places, _ = _place_layout(grid, rng)
    roads = _connect_places(grid, places, rng)
    place_by_cell = _stamp_places(grid, places)

    city = places[0]
    detail["startPosition"] = copy.deepcopy(city["centerPos"])
    detail["placeStoList"] = [
        {key: value for key, value in place.items() if not key.startswith("_")} for place in places
    ]
    detail["mapInfoStoList"] = [
        {
            "pos": {"x": x, "y": y},
            "isFind": False,
            "placeStoId": place_by_cell.get((x, y), 0),
            "terrainId": grid[x][y],
            "lingqiGenId": 0,
            "attr": {"trigger": 1, "pass": 1},
            "tags": {},
            "placeLogId": 0,
        }
        for x in range(MAP_SIZE)
        for y in range(MAP_SIZE)
    ]
    detail["code4101Prototype"].update(
        {
            "roadCellCount": len(roads),
            "placeCount": len(places),
            "terrainCounts": dict(sorted(Counter(cell for column in grid for cell in column).items())),
        }
    )
    return detail


def _font(size: int) -> ImageFont.ImageFont:
    candidates = (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def render_preview(detail: dict[str, Any], output_path: Path) -> None:
    cell = 24
    margin = 28
    legend_width = 250
    width = MAP_SIZE * cell + margin * 2 + legend_width
    height = MAP_SIZE * cell + margin * 2
    image = Image.new("RGB", (width, height), "#EEE8DB")
    draw = ImageDraw.Draw(image)
    terrain_by_pos = {
        (row["pos"]["x"], row["pos"]["y"]): row["terrainId"]
        for row in detail["mapInfoStoList"]
    }
    for x in range(MAP_SIZE):
        for y in range(MAP_SIZE):
            x0 = margin + x * cell
            y0 = margin + (MAP_SIZE - 1 - y) * cell
            terrain_id = terrain_by_pos[(x, y)]
            draw.rectangle(
                (x0, y0, x0 + cell, y0 + cell),
                fill=TERRAIN_COLORS.get(terrain_id, "#D7D0C3"),
                outline="#B8B09F",
            )

    label_font = _font(15)
    title_font = _font(22)
    small_font = _font(13)
    for place in detail["placeStoList"]:
        x = place["centerPos"]["x"]
        y = place["centerPos"]["y"]
        px = margin + x * cell + cell // 2
        py = margin + (MAP_SIZE - 1 - y) * cell + cell // 2
        draw.ellipse((px - 5, py - 5, px + 5, py + 5), fill="#3B2B2A", outline="white")
        draw.text((px + 7, py - 9), place["name"], fill="#241F1D", font=label_font)

    legend_x = margin * 2 + MAP_SIZE * cell
    seed = detail["code4101Prototype"]["seed"]
    draw.text((legend_x, margin), "炼气试炼·随机余州", fill="#241F1D", font=title_font)
    draw.text((legend_x, margin + 36), f"Seed  {seed}", fill="#5A514B", font=small_font)
    draw.text((legend_x, margin + 58), "28×28 · 10处地点 · 中心仙缘城", fill="#5A514B", font=small_font)
    used = sorted(set(terrain_by_pos.values()))
    y = margin + 96
    for terrain_id in used:
        label = TERRAIN_LABELS.get(terrain_id, str(terrain_id))
        draw.rectangle((legend_x, y, legend_x + 18, y + 18), fill=TERRAIN_COLORS[terrain_id])
        draw.text((legend_x + 28, y - 1), label, fill="#3D3632", font=small_font)
        y += 25
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def write_artifacts(detail: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    seed = detail["code4101Prototype"]["seed"]
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"lianqi-map-{seed}.json"
    preview_path = output_dir / f"lianqi-map-{seed}.png"
    content = json.dumps(detail, ensure_ascii=False, indent=2) + "\n"
    json_path.write_text(content, encoding="utf-8")
    render_preview(detail, preview_path)
    detail["code4101Prototype"]["jsonSha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return json_path, preview_path


def main() -> None:
    parser = argparse.ArgumentParser(description="生成《造化仙缘》炼气期大世界随机地图原型")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output_dir = args.output_dir or codeyun_temp_root("zaohua", "random-map-prototype")
    detail = generate_lianqi_map(args.seed)
    json_path, preview_path = write_artifacts(detail, output_dir)
    print(json.dumps({
        "seed": args.seed,
        "json": str(json_path),
        "preview": str(preview_path),
        "place_count": len(detail["placeStoList"]),
        "grid_count": len(detail["mapInfoStoList"]),
        "terrain_counts": detail["code4101Prototype"]["terrainCounts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
