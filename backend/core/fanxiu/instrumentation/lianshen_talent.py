"""炼神天赋详情的严格只读公共投影。

只收敛已真实验证的 ``LianShenTalentTipsView`` 详情字段：天赋 id/等级、
``LianshenData.talentLevelNumCfg[id]`` 等级组决定的上限，以及下一等级的
``consume``/``condition``/``conditionDesc``。条件原样返回字符串，本模块不
推断是否允许升级；不执行游戏 Lua、不写盘、不调用 Kernel/网络/数据库。
所有 Lua 地址仅在当前 snapshot 内有效。
"""
from __future__ import annotations

import re

from .resource_auto_use import read_runtime_config_defaults
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    has_ui_object_fields,
    read_active_ui_window_panels,
    read_ui_object_field,
    read_ui_runtime_snapshot,
)


_LIANSHEN_TIPS_WINDOW = "LianShenTalentTipsView"
_CULTIVATION_LEVEL_INDEXES = (
    "id",
    "talentId",
    "level",
    "condition",
    "conditionDesc",
    "consume",
    "skill",
    "describe",
    "attr",
)
_READY_PANEL_FIELDS = ("talentID", "curTalentLevel")
_ITEM_CONSUME = re.compile(r"Item\|(\d+)_(\d+)")
_MAX_CONSUME_ENTRIES = 2


def read_lianshen_talent_detail():
    """Return the single open 炼神天赋详情 projection, or fail closed.

    ``talentLevelNumCfg[id]`` 只读取当前这一个 id 的等级组，不扫描整棵树；
    组的完整行数即 ``max_level``，并校验行内 ``level`` 严格为 ``1..N``。
    满级时 ``costs`` 为空，且没有下一等级配置可读，故条件字段为空字符串。
    未知条件只作为原始字符串交给后续 GUI 层，本模块不做解锁判断。
    """

    def read(c):
        def fail(message):
            raise FanxiuRuntimeMemoryError(message, code="lianshen_snapshot_invalid")

        def values(obj):
            ref = table_ref(obj)
            if ref is None:
                fail("炼神状态表未加载")
            decoded = c.reader.table(ref.address)
            return {
                **{i: v for i, v in enumerate(decoded["array"]) if v is not None},
                **decoded["fields"],
            }

        def field(obj, key):
            ref = table_ref(obj)
            if ref is None:
                fail(f"炼神对象未加载：{key}")
            return read_ui_object_field(c, ref.address, key)

        def require_bool(obj, key):
            value = field(obj, key)
            if not isinstance(value, bool):
                fail(f"炼神布尔字段类型无效：{key}")
            return value

        def optional_bool(obj, key):
            value = field(obj, key)
            if value is not None and not isinstance(value, bool):
                fail(f"炼神布尔字段类型无效：{key}")
            return bool(value)

        # 1) 详情窗口必须唯一，且实例字段已刷新。
        panels = read_active_ui_window_panels(c, _LIANSHEN_TIPS_WINDOW)
        if len(panels) != 1:
            fail(f"炼神详情不唯一：{len(panels)}")
        panel = panels[0]
        if not has_ui_object_fields(c, panel.address, _READY_PANEL_FIELDS):
            fail("炼神详情字段未就绪")

        talent_id = as_int(field(panel, "talentID"))
        if talent_id is None or talent_id <= 0:
            fail("炼神天赋 ID 无效")
        level = as_int(field(panel, "curTalentLevel"))
        if level is None or level < 0:
            fail("炼神当前等级无效")
        is_choice = optional_bool(panel, "isChoose")
        is_max = require_bool(panel, "isMaxLevel")
        # A fresh max-level panel returns before initializing material state.
        # A reused panel may retain stale True flags; neither allows upgrading.
        can_activate = False if is_max else require_bool(panel, "_CanActive")
        can_click = False if is_max else require_bool(panel, "_CanClick")
        optional_bool(panel, "isShowing")

        # 2) 等级配置索引必须完整，且全部为正向列号。
        global_cfg = field(c.field(c.binding.environment_address, "s_globalCfgIdx"), "Cultivation")
        index_fields = values(field(global_cfg, "CultivationLevel"))
        indexes = {
            name: as_int(index_fields.get(name))
            for name in _CULTIVATION_LEVEL_INDEXES
        }
        if any(index is None or index < 1 for index in indexes.values()):
            fail("炼神等级配置索引不完整")

        # 3) 管理器 -> Model -> LianshenData -> 当前 id 的等级组。
        manager = table_ref(c.field(c.binding.environment_address, "LianshenMgr"))
        if manager is None:
            fail("LianshenMgr 尚未加载")
        instance = table_ref(field(manager, "inst"))
        model = table_ref(field(instance, "Model"))
        data = table_ref(field(model, "LianshenData"))
        group_root = table_ref(field(data, "talentLevelNumCfg"))
        if group_root is None:
            fail("炼神等级组未加载")
        group = table_ref(
            c.reader.numeric_fields(group_root.address, frozenset({talent_id})).get(talent_id)
        )
        if group is None:
            fail("炼神等级组未加载")

        indexed, declared = c.reader.indexed_list_items(group)
        if not indexed and declared is None:
            raw = values(group)
            indexed = sorted(
                (key, value)
                for key, value in raw.items()
                if isinstance(key, int) and key >= 1 and value is not None
            )
        if declared is not None and declared != len(indexed):
            fail("炼神等级组行数不完整")
        if not indexed:
            fail("炼神等级组为空")
        defaults = read_runtime_config_defaults(c.reader, dict(indexed), indexes)

        def config(obj):
            raw = values(obj)
            return {name: raw.get(index, defaults.get(name)) for name, index in indexes.items()}

        rows = {}
        for _, item in indexed:
            row = config(item)
            position = as_int(row.get("level"))
            if as_int(row.get("talentId")) != talent_id or position is None or position < 1 or position in rows:
                fail("炼神等级行身份错误")
            rows[position] = item
        max_level = len(rows)
        if sorted(rows) != list(range(1, max_level + 1)):
            fail("炼神等级组等级不连续")
        if level > max_level:
            fail("炼神当前等级越界")
        if is_max != (level == max_level):
            fail("炼神满级标志与等级不符")
        current = config(field(panel, "curtalentLevelCfg"))
        # The client displays level-one effects before a talent is learned.
        if as_int(current.get("talentId")) != talent_id or as_int(current.get("level")) != max(1, level):
            fail("炼神当前等级配置尚未刷新")

        result = {
            "id": talent_id,
            "level": level,
            "max_level": max_level,
            "costs": {},
            "condition": "",
            "condition_description": "",
            "is_choice": is_choice,
            "can_activate": can_activate,
            "can_click": can_click,
        }
        if level == max_level:
            return result

        # 4) 下一等级配置必须与当前状态一致，消耗/条件原样收敛。
        nxt = config(field(panel, "curtalentNextLevelCfg"))
        if as_int(nxt.get("talentId")) != talent_id or as_int(nxt.get("level")) != level + 1:
            fail("炼神下一等级配置尚未刷新")
        condition = nxt.get("condition")
        condition_description = nxt.get("conditionDesc")
        if not isinstance(condition, str):
            fail("炼神条件字段类型无效")

        consume = nxt.get("consume")
        if consume is None:
            parts = []
        elif isinstance(consume, str):
            parts = [part for part in consume.split(",") if part]
        elif table_ref(consume) is not None:
            parts = [part for part in values(consume).values() if part is not None]
        else:
            fail("炼神消耗字段类型无效")
        if len(parts) > _MAX_CONSUME_ENTRIES:
            fail("炼神消耗条目超出上限")
        costs = {}
        for part in parts:
            match = _ITEM_CONSUME.fullmatch(str(part))
            if not match or int(match[1]) <= 0 or int(match[2]) <= 0:
                fail(f"炼神消耗尚不支持：{part}")
            item_id, amount = int(match[1]), int(match[2])
            costs[item_id] = costs.get(item_id, 0) + amount
        result["costs"] = costs
        result["condition"] = condition
        result["condition_description"] = condition_description
        return result

    return read_ui_runtime_snapshot(("s_globalCfgIdx", "LianshenMgr"), read)


__all__ = ["read_lianshen_talent_detail"]
