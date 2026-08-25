from __future__ import annotations

from collections import deque
from itertools import permutations, product
import re
from typing import Any, Sequence


COMMON_CAREERS = ("体", "魔", "剑", "法")
BEATS = {
    "剑": "魔",
    "魔": "体",
    "体": "法",
    "法": "剑",
}
BEATEN_BY = {target: source for source, target in BEATS.items()}

# 取自当前游戏配置 PartnerArena_ConfigValue.CAREER_RESTRAIN：1,3,4,2,1。
# 职业 ID 分别为 1=体、2=魔、3=法、4=剑。
XIANYUAN_CAREER_BEATS = {1: 3, 3: 4, 4: 2, 2: 1}
XIANYUAN_CAREER_LABELS = {1: "体", 2: "魔", 3: "法", 4: "剑"}

# 取自 Partner_Partner.xianLvCareer。阵位事实只携带 partnerId，职业由静态游戏
# 配置确定；遇到新伙伴 ID 时安全失败，避免退回 OCR 或图片相似匹配猜测。
XIANYUAN_PARTNER_CAREERS = {
    1: 1, 2: 3, 3: 3, 4: 4, 5: 2, 6: 4, 7: 2, 8: 1, 9: 4,
    10: 3, 11: 2, 12: 1, 13: 2, 14: 1, 15: 2, 16: 2, 17: 2, 18: 4,
    19: 1, 20: 2, 21: 3, 22: 1, 23: 4, 24: 1, 25: 3, 26: 4, 27: 2,
    28: 2, 29: 3, 30: 2, 31: 2, 32: 4, 33: 4, 34: 1, 35: 3, 36: 2,
}


def parse_slot_value_title(title: str, prefix: str) -> tuple[int, str] | None:
    """Parse titles like ``职业1:体`` and ``克制3:1``."""
    text = str(title or "").strip()
    match = re.fullmatch(rf"{re.escape(prefix)}(\d+)[:：](.+)", text)
    if not match:
        return None
    return int(match.group(1)), match.group(2).strip()


def relation(my_career: str, enemy_career: str) -> int:
    if BEATS.get(my_career) == enemy_career:
        return 1
    if BEATS.get(enemy_career) == my_career:
        return -1
    return 0


def infer_enemy_candidates(my_career: str, state: int) -> tuple[str, ...]:
    if state == 1 and my_career in BEATS:
        return (BEATS[my_career],)
    if state == -1 and my_career in BEATEN_BY:
        return (BEATEN_BY[my_career],)
    if state == 0 and my_career in COMMON_CAREERS:
        excluded = {BEATS[my_career], BEATEN_BY[my_career]}
        return tuple(career for career in COMMON_CAREERS if career not in excluded)
    return ("其他",)


def infer_enemy_candidate_order(my_order: Sequence[str], states: Sequence[int]) -> tuple[tuple[str, ...], ...]:
    if len(my_order) != len(states):
        raise ValueError("my_order and states length mismatch")
    return tuple(infer_enemy_candidates(career, int(state)) for career, state in zip(my_order, states))


def unique_orders(items: Sequence[str]) -> list[tuple[str, ...]]:
    return sorted(set(permutations(tuple(items))))


def primary_score(my_order: Sequence[str], enemy_order: Sequence[str]) -> int:
    return sum(relation(my, enemy) for my, enemy in zip(my_order, enemy_order))


def secondary_score(my_order: Sequence[str], enemy_order: Sequence[str], *, decay: float = 0.5) -> float:
    total = 0.0
    for i, my in enumerate(my_order):
        for j, enemy in enumerate(enemy_order):
            total += relation(my, enemy) * (float(decay) ** abs(i - j))
    return total


def min_swap_count(current_order: Sequence[str], target_order: Sequence[str]) -> int:
    current = tuple(current_order)
    target = tuple(target_order)
    if current == target:
        return 0
    if sorted(current) != sorted(target):
        raise ValueError("current_order and target_order must contain the same careers")
    queue: deque[tuple[tuple[str, ...], int]] = deque([(current, 0)])
    seen = {current}
    size = len(current)
    while queue:
        order, depth = queue.popleft()
        for i in range(size - 1):
            for j in range(i + 1, size):
                if order[i] == order[j]:
                    continue
                next_order = list(order)
                next_order[i], next_order[j] = next_order[j], next_order[i]
                candidate = tuple(next_order)
                if candidate == target:
                    return depth + 1
                if candidate not in seen:
                    seen.add(candidate)
                    queue.append((candidate, depth + 1))
    raise RuntimeError("failed to calculate swap count")


def best_orders_for_enemy(
    my_careers: Sequence[str],
    enemy_order: Sequence[str],
    *,
    current_order: Sequence[str] | None = None,
    decay: float = 0.5,
) -> list[dict[str, object]]:
    orders = unique_orders(my_careers)
    max_primary = max(primary_score(order, enemy_order) for order in orders)
    primary_best = [order for order in orders if primary_score(order, enemy_order) == max_primary]
    rows: list[dict[str, object]] = []
    for order in primary_best:
        swaps = min_swap_count(current_order, order) if current_order is not None else 0
        rows.append({
            "order": list(order),
            "primary_score": max_primary,
            "secondary_score": secondary_score(order, enemy_order, decay=decay),
            "swap_count": swaps,
        })
    rows.sort(key=lambda row: (float(row["secondary_score"]), -int(row["swap_count"]), tuple(row["order"])), reverse=True)
    return rows


def enumerate_enemy_orders(enemy_candidates: Sequence[Sequence[str]]) -> list[tuple[str, ...]]:
    return sorted(set(product(*(tuple(candidates) for candidates in enemy_candidates))))


def plan_swaps(current_order: Sequence[Any], target_order: Sequence[Any]) -> list[tuple[int, int]]:
    current = list(current_order)
    target = list(target_order)
    swaps: list[tuple[int, int]] = []
    for index in range(len(current)):
        if current[index] == target[index]:
            continue
        swap_index = None
        for candidate in range(index + 1, len(current)):
            if current[candidate] == target[index] and current[candidate] != target[candidate]:
                swap_index = candidate
                break
        if swap_index is None:
            for candidate in range(index + 1, len(current)):
                if current[candidate] == target[index]:
                    swap_index = candidate
                    break
        if swap_index is None:
            raise ValueError("target_order is not reachable from current_order")
        current[index], current[swap_index] = current[swap_index], current[index]
        swaps.append((index + 1, swap_index + 1))
    return swaps


def xianyuan_partner_career(partner_id: int) -> int:
    try:
        return XIANYUAN_PARTNER_CAREERS[int(partner_id)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"未知仙侣 partnerId={partner_id}，无法确定职业") from exc


def _xianyuan_relation(my_career: int, enemy_career: int) -> int:
    if XIANYUAN_CAREER_BEATS.get(int(my_career)) == int(enemy_career):
        return 1
    if XIANYUAN_CAREER_BEATS.get(int(enemy_career)) == int(my_career):
        return -1
    return 0


def best_xianyuan_partner_order(
    my_partner_ids: Sequence[int],
    enemy_partner_ids: Sequence[int],
    *,
    decay: float = 0.5,
) -> dict[str, object]:
    """Return the strongest five-slot order using authoritative partner careers."""

    current = tuple(int(value) for value in my_partner_ids)
    enemy = tuple(int(value) for value in enemy_partner_ids)
    if len(current) != 5 or len(enemy) != 5:
        raise ValueError("仙缘斗法敌我阵容都必须恰好包含 5 个位置")
    if len(set(current)) != 5:
        raise ValueError("我方仙缘斗法阵容含重复 partnerId，拒绝计算换位")
    enemy_careers = tuple(xianyuan_partner_career(value) for value in enemy)
    rows: list[dict[str, object]] = []
    for partner_order in permutations(current):
        careers = tuple(xianyuan_partner_career(value) for value in partner_order)
        primary = sum(_xianyuan_relation(my, other) for my, other in zip(careers, enemy_careers))
        secondary = sum(
            _xianyuan_relation(my, other) * (float(decay) ** abs(i - j))
            for i, my in enumerate(careers)
            for j, other in enumerate(enemy_careers)
        )
        rows.append(
            {
                "partner_ids": list(partner_order),
                "careers": list(careers),
                "enemy_careers": list(enemy_careers),
                "primary_score": primary,
                "secondary_score": secondary,
                "swap_count": len(plan_swaps(current, partner_order)),
            }
        )
    rows.sort(
        key=lambda row: (
            int(row["primary_score"]),
            float(row["secondary_score"]),
            -int(row["swap_count"]),
            tuple(row["partner_ids"]),
        ),
        reverse=True,
    )
    return rows[0]


def best_order_for_enemy_candidates(
    my_careers: Sequence[str],
    enemy_candidates: Sequence[Sequence[str]],
    *,
    current_order: Sequence[str] | None = None,
    decay: float = 0.5,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for enemy_order in enumerate_enemy_orders(enemy_candidates):
        best = best_orders_for_enemy(my_careers, enemy_order, current_order=current_order, decay=decay)[0]
        rows.append({"enemy_order": list(enemy_order), **best})
    rows.sort(
        key=lambda row: (
            int(row["primary_score"]),
            float(row["secondary_score"]),
            -int(row["swap_count"]),
            tuple(row["order"]),
        ),
        reverse=True,
    )
    return rows[0]
