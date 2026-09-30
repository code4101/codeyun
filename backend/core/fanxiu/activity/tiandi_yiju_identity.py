"""Versioned playable boards shared by lifecycle, collection and task rewards.

Activity ids name the tournament edition, not the server count of each stage:
16090001 is still a local preliminary board. Group/schedule child activities
are deliberately excluded. Bindings come from Activity follow/baseId and
ActiveTask activityId; never derive rank or reward ids from decimal prefixes.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TiandiBoardIdentity:
    cross_count: int
    shop_base_id: int
    currency_type: int
    personal_rank_id: int
    alliance_rank_id: int
    task_activity_id: int


TIANDI_BOARDS = {
    8090001: TiandiBoardIdentity(1, 90000, 11, 90101, 90102, 8090001),
    8090004: TiandiBoardIdentity(8, 90002, 13, 90808, 90813, 8090003),
    16090001: TiandiBoardIdentity(1, 90000, 11, 90101, 90102, 16090001),
    16090004: TiandiBoardIdentity(16, 90002, 13, 91608, 91613, 16090003),
}
PLAYABLE_ACTIVITY_IDS = frozenset(TIANDI_BOARDS)
