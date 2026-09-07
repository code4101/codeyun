"""洗炼锁状态的短生命周期账本；只处理状态，不读取 Runtime 或点击游戏。

每次进入部件由当前 UI Runtime 快照初始化，保留实际词条身份、行序和锁。
开始点击即使账本不可用，只有动作后明确观察到目标锁状态才提交；异常、
中断、外部变化、采用候选或离开部件均应 invalidate，并重新初始化。
该对象不能作为下一 Cell/attempt 的续跑状态。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class SpiritArtifactLockRow:
    row: int
    cleanse_id: int
    value: int
    quality: int
    locked: bool


@dataclass(frozen=True)
class SpiritArtifactLockChange:
    item_id: str
    effect: SpiritArtifactLockRow
    locked: bool


class SpiritArtifactLockState:
    """单个 attempt、单个已选部件的锁账本；无效后不能就地恢复。

confirm_change 的 observed_locked 必须来自动作后证据，不能传点击成功、
期望值或旧截图。GUI 证据还须确认目标身份及其他词条未变化。
"""

    def __init__(self, snapshot: Mapping[str, Any], *, attempt_id: str, kernel_generation: int):
        if not attempt_id or kernel_generation <= 0 or snapshot.get('is_wash') is not True:
            raise ValueError('锁状态必须从当前 attempt 的洗炼 Runtime 初始化')
        self.owner = (attempt_id, kernel_generation, snapshot['pid'],
                      snapshot['process_start_ticks'], str(snapshot['item_id']))
        if not self.owner[-1]:
            raise ValueError('锁状态缺少部件实例')
        rows = tuple(SpiritArtifactLockRow(**{key: effect[key] for key in (
            'row', 'cleanse_id', 'value', 'quality', 'locked')}) for effect in snapshot['effects'])
        if (not rows or len({r.cleanse_id for r in rows}) != len(rows)
                or sorted(r.row for r in rows) != list(range(len(rows)))
                or any(type(r.locked) is not bool or r.cleanse_id <= 0 for r in rows)):
            raise ValueError('锁状态的行序、词条身份或 locked 无效')
        self._rows = tuple(sorted(rows, key=lambda row: row.row))
        self._valid = True
        self._pending: SpiritArtifactLockChange | None = None

    def invalidate(self) -> None:
        self._valid = False
        self._pending = None

    @property
    def rows(self) -> tuple[SpiritArtifactLockRow, ...]:
        if not self._valid:
            raise ValueError('锁状态已失效，须重新读取当前 Runtime')
        return self._rows

    def changes(self, desired_locked_ids: set[int]) -> tuple[SpiritArtifactLockChange, ...]:
        """仅返回需切换的行，保留初始已锁项的真实状态。"""
        rows = self.rows
        if desired_locked_ids - {row.cleanse_id for row in rows}:
            raise ValueError('目标锁集合包含当前不存在的词条')
        return tuple(SpiritArtifactLockChange(self.owner[-1], row, row.cleanse_id in desired_locked_ids)
                     for row in rows if row.locked != (row.cleanse_id in desired_locked_ids))

    def begin_change(self, cleanse_id: int, locked: bool) -> SpiritArtifactLockChange | None:
        if type(locked) is not bool:
            raise ValueError('locked 必须为 bool')
        row = next((row for row in self.rows if row.cleanse_id == cleanse_id), None)
        if row is None:
            raise ValueError('当前部件没有该词条')
        if row.locked == locked:
            return None
        change = SpiritArtifactLockChange(self.owner[-1], row, locked)
        self._valid = False  # 点击前失效，中断不能留下看似可信的旧状态。
        self._pending = change
        return change

    def confirm_change(self, change: SpiritArtifactLockChange, *, observed_locked: bool | None) -> None:
        if self._pending is not change or observed_locked is not change.locked:
            self.invalidate()
            raise ValueError('锁动作结果不明确，须重新读取当前 Runtime')
        self._rows = tuple(replace(row, locked=change.locked) if row.row == change.effect.row else row
                           for row in self._rows)
        self._pending = None
        self._valid = True
