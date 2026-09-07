"""高级洗炼列表的程序内滚动经验；经验只预测滚动，不预测点击坐标。

实际起始视口由当前可见道具及位置标识，因此重开窗口不假定回到顶部。
缓存命中时批量拖动后才识别；末端仍要求当前目标名称命中。调用方确认
使用提示里的道具身份后再记住路线。对象仅存于一次程序的上下文块中。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any


def find_advanced_item_title(tokens, name: str):
    """完整「洗灵·道具名」且同行右侧有「拥有」才是列表标题，不点击说明引用。"""
    from ..ocr_spatial import find_text_matches, select_text_match

    normalize = lambda value: ''.join(c for c in str(value) if c.isalnum())
    normalized = [{**token, 'text': normalize(token.get('text', ''))} for token in tokens]
    inventories = find_text_matches(normalized, '拥有')
    # Paddle 返回逐字 token；标题中点可能被漏识，保留其实际几何间隔。
    matches = find_text_matches(normalized, normalize(name), max_gap_height_ratio=1.5)
    titles = [match for match in matches if any(
        inventory.x >= match.x + match.w
        and abs(inventory.y + inventory.h / 2 - (match.y + match.h / 2))
        <= max(inventory.h, match.h) * 0.6 for inventory in inventories)]
    return select_text_match(titles, name)


@dataclass(frozen=True)
class AdvancedScrollProfile:
    """冷发现与经验重放共用的手势参数；缩短参数仅在显式选择后启用。

    默认保留已实测的0.5/1.5/1.5。0.8/0.4/0.4为待实测配置，
    不能单凭预计拖动时间宣称定位成功或提速。
    """
    ratio: float = .5
    duration: float = 1.5
    settle_seconds: float = 1.5

    def __post_init__(self):
        if (any(type(v) not in (float, int) or not math.isfinite(v)
                for v in (self.ratio, self.duration, self.settle_seconds))
                or not 0 < self.ratio <= 1 or self.duration <= 0 or self.settle_seconds <= 0):
            raise ValueError('滚动 profile 需要有效比例及正数时长')


@dataclass(frozen=True)
class AdvancedScrollKey:
    layout: str
    start: tuple[tuple[int, int, int], ...]
    item_id: int


class AdvancedScrollMemory:
    """无全局缓存；相同列表但起点不同，不能借用同一路线。"""

    def __init__(self, *, profile: AdvancedScrollProfile | None = None):
        self._profile = profile if profile is not None else AdvancedScrollProfile()
        if not isinstance(self._profile, AdvancedScrollProfile):
            raise TypeError('profile 必须是 AdvancedScrollProfile')
        self._routes: dict[AdvancedScrollKey, tuple[str, ...]] = {}
        self._active = True

    @property
    def profile(self) -> AdvancedScrollProfile:
        return self._profile

    def route(self, key: AdvancedScrollKey) -> tuple[str, ...] | None:
        if not self._active:
            raise RuntimeError('高级洗炼滚动经验已离开本次程序')
        if key in self._routes:
            return self._routes[key]
        # OCR 字框会有数像素漂移；同组可见道具且每轴误差≤8px视为相同起点。
        # 不跨道具集合或明显滚动位置复用，末端仍需真实标题与确认页核验。
        nearby = {route for known, route in self._routes.items()
                  if known.layout == key.layout and known.item_id == key.item_id
                  and len(known.start) == len(key.start)
                  and all(a[0] == b[0] and abs(a[1] - b[1]) <= 2 and abs(a[2] - b[2]) <= 2
                          for a, b in zip(known.start, key.start))}
        return next(iter(nearby)) if len(nearby) == 1 else None

    def remember(self, key: AdvancedScrollKey, directions: tuple[str, ...]) -> None:
        self.route(key)
        if not key.start or len(directions) > 30 or any(d not in ('up', 'down') for d in directions):
            raise ValueError('滚动经验需要明确起点和有界方向序列')
        if len(self._routes) >= 128:
            self._routes.clear()
        self._routes[key] = directions

    def forget(self, key: AdvancedScrollKey) -> None:
        self._routes.pop(key, None)

    def close(self) -> None:
        self._routes.clear()
        self._active = False


def advanced_scroll_layout(catalog: Mapping[str, Any], shape: Mapping[str, Any], *,
                           profile: AdvancedScrollProfile | None = None) -> str:
    """库存数量不会改路线；进程、道具顺序、描述和标注布局改变则隔离。"""
    payload = {'process': [catalog['pid'], catalog['process_start_ticks']],
               'scroll_profile': asdict(profile if profile is not None else AdvancedScrollProfile()),
               'ware_id': catalog['ware_id'], 'shape': dict(shape),
               'items': [{key: row.get(key) for key in ('item', 'sort', 'name', 'shortDes')}
                         for row in catalog['items']]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def locate_advanced_item_with_experience(
    context: Any, execute: Callable[[Any], Any], *, scene_id: int,
    catalog: Mapping[str, Any], item_id: int, memory: AdvancedScrollMemory,
    max_scrolls_per_direction: int = 10,
) -> tuple[Any, AdvancedScrollKey | None, tuple[str, ...]]:
    """只定位、不点击；复用正式 Shape 拖动和区域 OCR，不读 Runtime。

    返回 match 仅可立即用于点击；key/route 在确认页身份验证后才可 remember。
    批量路径只等待拖动稳定，不逐次截图或 OCR；失败后一次有界双向重找。
    """
    shape = context.shape(scene_id, '道具列表')
    names = {row['item']: str(row['name'])
             for row in catalog['items']}
    if not names.get(item_id) or len(set(names.values())) != len(names):
        raise ValueError('高级洗炼道具名称缺失或不唯一')
    profile = memory.profile
    layout = advanced_scroll_layout(catalog, shape.raw, profile=profile)

    def observe_start():
        frame = context.cur_frame(update=True)
        tokens = context.ocr_tokens_in_shapes(scene_id, ['道具列表'], frame_data_url=frame)
        visible, target = [], None
        for current_id, name in names.items():
            match = find_advanced_item_title(tokens, name)
            if match is not None:
                x, y = match.point()
                visible.append((current_id, round(x / 4), round(y / 4)))
                if current_id == item_id:
                    target = match
        return target, tuple(sorted(visible))

    def find_target():
        frame = context.cur_frame(update=True)
        tokens = context.ocr_tokens_in_shapes(scene_id, ['道具列表'], frame_data_url=frame)
        return find_advanced_item_title(tokens, names[item_id])

    execute(context.wait_scene([scene_id], wait=10))
    match, start = observe_start()
    key = AdvancedScrollKey(layout, start, item_id) if start else None
    traversed: list[str] = []
    if match is not None:
        return match, key, ()
    route = memory.route(key) if key is not None else None
    if route is not None:
        # 缓存的是实际尝试过的拖动序列（含到边界的最后一次），不是部件下标。
        for direction in route:
            context.drag_shape_content(shape, direction=direction,
                                       ratio=profile.ratio, duration=profile.duration)
            execute(context.wait_action_settle(profile.settle_seconds))
            traversed.append(direction)
        execute(context.wait_scene([scene_id], wait=10))
        match = find_target()
        if match is not None:
            return match, key, tuple(traversed)
        memory.forget(key)

    for direction in ('down', 'up'):
        for _ in range(max(0, min(10, max_scrolls_per_direction))):
            changed = execute(context.scroll_shape_content(shape, direction=direction,
                ratio=profile.ratio, duration=profile.duration, settle_seconds=profile.settle_seconds))
            traversed.append(direction)
            match = find_target()
            if match is not None:
                return match, key, tuple(traversed)
            if not changed:
                break
    if key is not None:
        memory.forget(key)
    raise TimeoutError(f'高级洗炼列表有界定位失败：{names[item_id]}')
