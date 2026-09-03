from __future__ import annotations

"""Asset-injected task-reward transaction for Xutian Palace.

The current Xutian task pages have not yet been assigned production scene
identities.  This module therefore owns only the stable business contract:
exactly one cultivation page (ActiveTask subtype 4) and one exploration page
(subtype 2), backed by a caller-supplied Runtime reader.  The concrete scene
and Shape identities remain an explicit adapter input until they have been
verified against the real page.
"""

from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass
from typing import Any

from backend.core.fanxiu.data_annotation.tasks.gameplay_rank_task_rewards import (
    GameplayRankTaskAssets,
    GameplayRankTaskTab,
    claim_gameplay_rank_task_tabs,
)


XUTIAN_CULTIVATION_TASK_SUBTYPE = 4
XUTIAN_EXPLORATION_TASK_SUBTYPE = 2
XUTIAN_TASK_SUBTYPES = frozenset(
    {XUTIAN_CULTIVATION_TASK_SUBTYPE, XUTIAN_EXPLORATION_TASK_SUBTYPE}
)

XutianTaskRewardReader = Callable[..., dict[str, Any]]


def _required_text(value: str, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"虚天任务奖励资产 {field} 不能为空")
    return text


@dataclass(frozen=True)
class XutianTaskRewardPageAssets:
    """One verified Xutian task tab; no scene or Shape is inferred."""

    key: str
    subtype: int
    scene_id: int
    tab_shape: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _required_text(self.key, field="page.key"))
        object.__setattr__(
            self,
            "tab_shape",
            _required_text(self.tab_shape, field=f"{self.key}.tab_shape"),
        )
        if int(self.subtype) not in XUTIAN_TASK_SUBTYPES:
            raise ValueError(f"虚天任务奖励不支持 subtype={self.subtype}")
        if int(self.scene_id) <= 0:
            raise ValueError(f"虚天任务奖励页 {self.key} 缺少已验证 scene_id")


@dataclass(frozen=True)
class XutianTaskRewardAssets:
    """Complete two-page GUI contract supplied by a verified site adapter."""

    home_scene_id: int
    pages: Sequence[XutianTaskRewardPageAssets]
    task_entry_shape: str
    first_row_claim_shape: str
    home_shape: str

    def __post_init__(self) -> None:
        pages = tuple(self.pages)
        if len(pages) != 2:
            raise ValueError("虚天任务奖励资产必须恰好包含修炼、探索两个页签")
        subtypes = [int(page.subtype) for page in pages]
        if set(subtypes) != XUTIAN_TASK_SUBTYPES or len(set(subtypes)) != len(subtypes):
            raise ValueError("虚天任务奖励资产必须唯一覆盖 subtype=4/2")
        scene_ids = [int(page.scene_id) for page in pages]
        if len(set(scene_ids)) != len(scene_ids):
            raise ValueError("虚天任务奖励两个页签必须使用不同 scene_id")
        if int(self.home_scene_id) <= 0:
            raise ValueError("虚天任务奖励资产缺少已验证 home_scene_id")
        if int(self.home_scene_id) in scene_ids:
            raise ValueError("虚天任务奖励主页不能复用任务页 scene_id")
        object.__setattr__(self, "pages", pages)
        object.__setattr__(
            self,
            "task_entry_shape",
            _required_text(self.task_entry_shape, field="task_entry_shape"),
        )
        object.__setattr__(
            self,
            "first_row_claim_shape",
            _required_text(self.first_row_claim_shape, field="first_row_claim_shape"),
        )
        object.__setattr__(
            self,
            "home_shape",
            _required_text(self.home_shape, field="home_shape"),
        )

    def as_gameplay_rank_assets(self) -> GameplayRankTaskAssets:
        return GameplayRankTaskAssets(
            activity_label="虚天殿",
            home_scene_id=int(self.home_scene_id),
            tabs=tuple(
                GameplayRankTaskTab(
                    key=page.key,
                    subtype=int(page.subtype),
                    scene_id=int(page.scene_id),
                    tab_shape=page.tab_shape,
                )
                for page in self.pages
            ),
            task_entry_shape=self.task_entry_shape,
            first_row_claim_shape=self.first_row_claim_shape,
            home_shape=self.home_shape,
        )


def claim_xutian_task_rewards(
    context: Any,
    *,
    assets: XutianTaskRewardAssets,
    reader: XutianTaskRewardReader,
    settle_seconds: float = 1.2,
) -> Generator[Any, None, dict[str, Any]]:
    """Check and settle both Xutian reward tabs as one idempotent gate.

    The reader owns occurrence-specific QuestMgr selection.  It must return a
    complete snapshot on the initial call and accept
    ``expected_claimed_task_id=...`` for exact post-click verification.
    """

    if not callable(reader):
        raise TypeError("虚天任务奖励 reader 必须可调用")
    return (
        yield from claim_gameplay_rank_task_tabs(
            context,
            assets=assets.as_gameplay_rank_assets(),
            reader=reader,
            settle_seconds=settle_seconds,
        )
    )


__all__ = [
    "XUTIAN_CULTIVATION_TASK_SUBTYPE",
    "XUTIAN_EXPLORATION_TASK_SUBTYPE",
    "XutianTaskRewardAssets",
    "XutianTaskRewardPageAssets",
    "claim_xutian_task_rewards",
]
