from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOLVER = ROOT / "Code4101.DantianSolver" / "bin" / "Release" / "Code4101.DantianSolver.exe"
DEFAULT_SNAPSHOT = (
    Path(os.environ["TEMP"])
    / "codeyun"
    / "zaohua_mod"
    / "dantian_solver"
    / "latest.request.json"
)


def run_solver(request: dict) -> dict:
    completed = subprocess.run(
        [str(SOLVER)],
        input=json.dumps(request, ensure_ascii=False, separators=(",", ":")),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    response = json.loads(completed.stdout)
    assert response["status"] in {"FEASIBLE", "OPTIMAL"}, response
    return response


def relation(request: dict, source: int, target: int, code: int) -> bool:
    dx = request["cellX"][source] - request["cellX"][target]
    dy = request["cellY"][source] - request["cellY"][target]
    return {
        1: dx == 0 and dy < 0,
        2: dx == 0 and dy > 0,
        3: dy == 0 and dx > 0,
        4: dy == 0 and dx < 0,
        5: dx == dy,
        6: dx == -dy,
        7: source != target,
        10: abs(dx) + abs(dy) <= 1,
        11: abs(dx) + abs(dy) > 1,
    }[code]


def shape_hit(request: dict, placements: list[int], source_piece: int,
              target_piece: int, geometry: list[int]) -> bool:
    source_cells = request["pieces"][source_piece]["placements"][placements[source_piece]]["cells"]
    target_cells = request["pieces"][target_piece]["placements"][placements[target_piece]]["cells"]
    return any(
        all(relation(request, source, target, code) for code in geometry)
        for source in source_cells
        for target in target_cells
    )


def evaluate(request: dict, placements: list[int]) -> tuple[list[int], list[int]]:
    multipliers: list[int] = []
    targets: list[int] = []
    for rule in request["rules"]:
        count = 1 if rule["countSelf"] else sum(
            shape_hit(request, placements, rule["sourcePiece"], target, rule["countGeometry"])
            for target in rule["countTargetPieces"]
        )
        target_count = 1 if rule["gateSelf"] else sum(
            shape_hit(request, placements, rule["sourcePiece"], target, rule["gateGeometry"])
            for target in rule["gateTargetPieces"]
        )
        table = rule["multiplierByCount"]
        multipliers.append(table[min(count, len(table) - 1)] if target_count else 0)
        targets.append(target_count)
    return multipliers, targets


def normalized_order(request: dict) -> list[int]:
    result: list[int] = []
    for index in request.get("priorityOrder") or []:
        if 0 <= index < len(request["rules"]) and index not in result:
            result.append(index)
    result.extend(index for index in range(len(request["rules"])) if index not in result)
    return result


def vector(request: dict, multipliers: list[int], targets: list[int]) -> tuple[int, ...]:
    benefits = [multipliers[index] * targets[index] for index in normalized_order(request)]
    return (*benefits, sum(m * t for m, t in zip(multipliers, targets)))


def validate_response(request: dict, response: dict) -> None:
    placements = response["placements"]
    occupied: list[int] = []
    for piece, placement in zip(request["pieces"], placements):
        assert 0 <= placement < len(piece["placements"])
        occupied.extend(piece["placements"][placement]["cells"])
    assert len(occupied) == len(set(occupied)), "solution overlaps"
    multipliers, targets = evaluate(request, placements)
    assert multipliers == response["multipliers"], (multipliers, response["multipliers"])
    assert targets == response["targetCounts"], (targets, response["targetCounts"])
    expected_total = sum(m * t for m, t in zip(multipliers, targets))
    assert expected_total == response["total"], (expected_total, response["total"])


def cache_matrix(snapshot: Path, milliseconds: int) -> None:
    source = json.loads(snapshot.read_text(encoding="utf-8-sig"))
    count = len(source["rules"])
    orders = {
        "natural": list(range(count)),
        "reversed": list(reversed(range(count))),
        "target-first": [count - 1, *range(count - 1)],
        "middle-first": [count // 2, *[i for i in range(count) if i != count // 2]],
    }
    for name, order in orders.items():
        for seed in (17, 7919):
            request = json.loads(json.dumps(source))
            request["timeLimitMs"] = milliseconds
            request["seed"] = seed
            request["priorityOrder"] = order
            current_multipliers, current_targets = evaluate(request, request["currentPlacements"])
            response = run_solver(request)
            validate_response(request, response)
            assert vector(request, response["multipliers"], response["targetCounts"]) >= vector(
                request, current_multipliers, current_targets
            ), f"{name}/{seed}: priority regressed"
            print(
                f"cache {name}/{seed}: total={response['total']} "
                f"targets={response['targetCounts']} source={response['resultSource']}"
            )


def base_request(cell_x: list[int], cell_y: list[int], piece_count: int) -> dict:
    placements = [{"cells": [index]} for index in range(len(cell_x))]
    return {
        "version": 2,
        "timeLimitMs": 1000,
        "seed": 17,
        "cellCount": len(cell_x),
        "cellX": cell_x,
        "cellY": cell_y,
        "currentPlacements": list(range(piece_count)),
        "expectedCurrentMultipliers": [],
        "pieces": [{"name": f"p{i}", "placements": placements} for i in range(piece_count)],
        "rules": [],
    }


def rule(name: str, source: int, geometry: list[int], targets: list[int]) -> dict:
    return {
        "key": name,
        "name": name,
        "sourcePiece": source,
        "maxMultiplier": 1,
        "multiplierByCount": [0, 1],
        "countSelf": True,
        "countGeometry": [],
        "countTargetPieces": [],
        "gateSelf": False,
        "gateGeometry": geometry,
        "gateTargetPieces": targets,
        "sourceOptions": [],
    }


def synthetic_cases() -> None:
    tradeoff = base_request([0, 0], [0, 1], 2)
    tradeoff["rules"] = [rule("below", 0, [1], [1]), rule("above", 0, [2], [1])]
    tradeoff["expectedCurrentMultipliers"] = [1, 0]
    tradeoff["priorityOrder"] = [1, 0]
    response = run_solver(tradeoff)
    validate_response(tradeoff, response)
    assert response["phaseOneStatus"] in {"FEASIBLE", "OPTIMAL"}, response
    assert response["multipliers"] == [0, 1], response
    print("synthetic tradeoff: priority reversal passed")

    multi = base_request([-1, 0, 1], [0, 0, 0], 3)
    multi["rules"] = [rule("broadcast", 0, [10], [1, 2])]
    multi["expectedCurrentMultipliers"] = [1]
    multi["priorityOrder"] = [0]
    response = run_solver(multi)
    validate_response(multi, response)
    assert response["phaseOneStatus"] in {"FEASIBLE", "OPTIMAL"}, response
    assert response["targetCounts"] == [2], response
    print("synthetic broadcast: two-target benefit passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--milliseconds", type=int, default=1000)
    args = parser.parse_args()
    assert SOLVER.exists(), f"solver not built: {SOLVER}"
    assert args.snapshot.exists(), f"snapshot missing: {args.snapshot}"
    order_test = subprocess.run(
        [str(SOLVER), "--self-test-priority-order"],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    assert order_test.stdout == "priority order self-test passed", order_test.stdout
    print(order_test.stdout)
    floor_test = subprocess.run(
        [str(SOLVER), "--self-test-satisfaction-floor"],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    assert floor_test.stdout == "satisfaction floor self-test passed", floor_test.stdout
    print(floor_test.stdout)
    synthetic_cases()
    cache_matrix(args.snapshot, args.milliseconds)
    print("all dantian solver cache tests passed")


if __name__ == "__main__":
    main()
