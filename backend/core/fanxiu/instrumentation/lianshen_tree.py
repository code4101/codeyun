"""炼神技能树全图只读投影：不打开详情、不调用游戏 Lua、不推断条件状态。

只收敛当前 ``CultivationMainViewNew`` 窗口当前选中页签的完整技能格子，以及
每个技能格子所有 talent 变体的下一等级消耗与条件原文：

* 页面事实来自当前 ``selected panel.ItemListScroll.ItemInfoList`` CList，
  按原生顺序/下标投影 ``cells``；``subType == 1`` 的格子投影为 ``nodes``。
  不虚构 x/y、列数或红点排序，``nodes`` 仅保留原生 ordinal 顺序。
* 每个 talent 的当前等级来自 ``LianshenMgr.inst.roleTalentLevel[talentId]``
  （缺键视为 0）；``max_level`` 与下一等级消耗/条件来自
  ``mgr.Model.LianshenData.talentLevelNumCfg[talentId]`` 的完整 ``level=1..N``
  行组，列号取自 ``s_globalCfgIdx.Cultivation.CultivationLevel``，缺省值由
  ``read_runtime_config_defaults`` 在本次快照内解析。格子列号取自
  ``s_globalCfgIdx.Cultivation.CultivationCell``。
* 两选一技能格：若已有变体 ``current_level > 0``，仅该变体
  ``selectable=True``，其余变体 ``selectable=False``；都未激活时
  ``requires_choice=True``。
* 条件字段（``condition``/``condition_desc``）原样返回字符串。本模块不推断
  条件是否满足，也不提供 ``unlocked``/``can_upgrade`` 等判断；后续由主 agent
  扩展原生条件解析。

只读、无副作用：不写盘、不执行 Lua、不调用 Kernel/网络/数据库，不读取仓库
外路径；所有地址仅在当前 snapshot 内有效。窗口/页签缺失、格子或等级组不
完整、字段类型/取值不成立时抛 ``FanxiuRuntimeMemoryError``，绝不返回
``complete=False`` 或空成功。
"""
from __future__ import annotations

import re

from .resource_auto_use import read_runtime_config_defaults
from .runtime_memory import FanxiuRuntimeMemoryError, LuaRef, as_int, table_ref
from .ui_runtime_context import (
    read_active_ui_window_panels,
    read_ui_object_field,
    read_ui_runtime_snapshot,
    read_ui_selected_tab_panel,
)


_LIANSHEN_TREE_WINDOW = "CultivationMainViewNew"
_CELL_INDEXES = ("id", "type", "subType", "talentId")
_LEVEL_INDEXES = (
    "id",
    "talentId",
    "level",
    "condition",
    "conditionDesc",
    "consume",
)
_SKILL_SUB_TYPE = 1
_ITEM_CONSUME = re.compile(r"Item\|(\d+)_(\d+)")
_MAX_CONSUME_ENTRIES = 2
_MAX_TALENT_VARIANTS = 2


def cultivation_condition_met(condition, levels):
    """Native GameUtil: semicolon OR, comma AND, Cultivation compares >=."""
    if not condition:
        return True
    groups = []
    for group in condition.split(";"):
        parts = []
        for part in group.split(","):
            match = re.fullmatch(r"Cultivation\|(\d+)_(\d+)", part)
            if not match or int(match[1]) not in levels:
                raise FanxiuRuntimeMemoryError(f"炼神前置条件未覆盖：{part}", code="lianshen_condition_unknown")
            parts.append(levels[int(match[1])] >= int(match[2]))
        groups.append(all(parts))
    return any(groups)


def read_lianshen_tree(*, all_tabs=False):
    """Return the complete read-only 炼神 tree of the selected tab, or fail closed.

    ``complete`` is only ever ``True`` for a fully validated projection. Every
    structural doubt (window/tab missing, CList incomplete, cell/level group
    gaps, non-contiguous ``level=1..N``, more than two ``Item|id_qty`` costs, a
    non-string condition, two active variants in a two-choice cell) raises
    ``FanxiuRuntimeMemoryError`` instead of degrading to an empty success.

    Condition text is returned verbatim and its satisfaction is intentionally
    not evaluated here. ``selectable`` only encodes the native two-choice rule,
    not affordability or prerequisite unlock.
    """

    def read(c):
        def fail(message):
            raise FanxiuRuntimeMemoryError(message, code="lianshen_tree_snapshot_invalid")

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

        def int_value(value):
            if isinstance(value, LuaRef):
                return c.reader.long(value)
            return as_int(value)

        def keyed_fields(root, wanted):
            """Read integer-keyed entries from either a plain table or a CList/CDictionary.

            ``numeric_fields`` only decodes a plain table; a wrapper keeps its
            entries behind ``_dt_``. A wrapper read through the wrong interface
            would silently report every level as its 0 default, so detect the
            wrapper explicitly instead of guessing.
            """
            if table_ref(c.reader.fields(root).get("_dt_")) is not None:
                decoded = c.reader.dictionary_fields(root)
                return {
                    as_int(key): value
                    for key, value in decoded.items()
                    if as_int(key) is not None
                }
            return c.reader.numeric_fields(root.address, wanted)

        def packed(obj, indexes, defaults=None):
            raw = values(obj)
            result = {}
            for name, index in indexes.items():
                value = raw.get(index)
                if value is None:
                    value = raw.get(name)
                if value is None and defaults is not None:
                    value = defaults.get(name)
                result[name] = value
            return result

        def decode_talent_ids(value, cell_id):
            if value is None or isinstance(value, bool):
                fail(f"炼神格子 talentId 无效：{cell_id}")
            if isinstance(value, int):
                ids = [value]
            elif isinstance(value, float) and value.is_integer():
                ids = [int(value)]
            elif isinstance(value, str):
                ids = [as_int(part) for part in value.split(",") if part]
            else:
                ref = table_ref(value)
                if ref is None:
                    fail(f"炼神格子 talentId 类型无效：{cell_id}")
                raw = values(ref)
                ids = [
                    as_int(item)
                    for key, item in sorted(
                        (key, item)
                        for key, item in raw.items()
                        if isinstance(key, int) and key >= 1 and item is not None
                    )
                ]
            if not 1 <= len(ids) <= _MAX_TALENT_VARIANTS or any(
                ident is None or ident <= 0 for ident in ids
            ):
                fail(f"炼神格子 talentId 取值无效：{cell_id}")
            if len(set(ids)) != len(ids):
                fail(f"炼神格子 talentId 重复：{cell_id}")
            return ids

        def decode_costs(consume):
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
            return costs

        # 1) 当前窗口唯一，且当前选中页签可解析。
        hosts = read_active_ui_window_panels(c, _LIANSHEN_TREE_WINDOW)
        if len(hosts) != 1:
            fail(f"炼神主界面不唯一：{len(hosts)}")
        selected = read_ui_selected_tab_panel(c, hosts[0].address)
        if selected is None:
            fail("炼神主界面当前页签未选中")
        panel_address, selected_tab_index = selected

        # 2) 两套 packed 列号必须完整，且全部为正向列号。
        cultivation = field(
            c.field(c.binding.environment_address, "s_globalCfgIdx"), "Cultivation"
        )
        cell_index_fields = values(field(cultivation, "CultivationCell"))
        cell_indexes = {
            name: as_int(cell_index_fields.get(name)) for name in _CELL_INDEXES
        }
        level_index_fields = values(field(cultivation, "CultivationLevel"))
        level_indexes = {
            name: as_int(level_index_fields.get(name)) for name in _LEVEL_INDEXES
        }
        if any(index is None or index < 1 for index in cell_indexes.values()):
            fail("炼神格子配置索引不完整")
        if any(index is None or index < 1 for index in level_indexes.values()):
            fail("炼神等级配置索引不完整")

        # 3) 管理器根：roleTalentLevel（缺键默认 0）与 talentLevelNumCfg。
        manager = table_ref(c.field(c.binding.environment_address, "LianshenMgr"))
        if manager is None:
            fail("LianshenMgr 尚未加载")
        instance = table_ref(field(manager, "inst"))
        if instance is None:
            fail("LianshenMgr.inst 尚未加载")
        role_levels_root = table_ref(field(instance, "roleTalentLevel"))
        if role_levels_root is None:
            fail("炼神角色天赋等级表未加载")
        model = table_ref(field(instance, "Model"))
        data = table_ref(field(model, "LianshenData"))
        group_root = table_ref(field(data, "talentLevelNumCfg"))
        if group_root is None:
            fail("炼神天赋等级组未加载")

        type_indexes = {k: as_int(v) for k, v in values(field(cultivation, "CultivationType")).items()}
        tab_items, tab_count = c.reader.indexed_list_items(table_ref(field(data, "CultivationTypeCfgList")))
        if tab_count != len(tab_items) or not tab_items:
            fail("炼神页签配置不完整")
        tab_defaults = read_runtime_config_defaults(c.reader, dict(tab_items), type_indexes)
        tabs = [packed(row, type_indexes, tab_defaults) for _, row in tab_items]
        types = values(field(data, "tallentTypeCfg"))

        def project(tab):
            type_id = as_int(tab["id"])
            cfgs = values(types.get(type_id))
            indexed_cells = sorted((as_int(k), v) for k, v in cfgs.items() if as_int(k) is not None)
            if not indexed_cells or [k for k, _ in indexed_cells] != list(range(1, len(indexed_cells)+1)):
                fail("炼神完整配置格子不连续")
            # 4) 投影全部格子；id 必须唯一且按原生顺序升序。
            cells = []
            cell_ids = []
            for position, (lua_index, raw_cell) in enumerate(indexed_cells):
                cell = table_ref(raw_cell)
                if cell is None:
                    fail("炼神技能格子列表含非对象行")
                cfg = cell
                if cfg is None:
                    fail("炼神技能格子缺少 cfg")
                row = packed(cfg, cell_indexes)
                cell_id = as_int(row.get("id"))
                cell_type = as_int(row.get("type"))
                sub_type = as_int(row.get("subType"))
                if cell_id is None or cell_id <= 0:
                    fail("炼神技能格子 id 无效")
                if cell_type is None or cell_type < 0:
                    fail("炼神技能格子 type 无效")
                if sub_type is None or sub_type < 0:
                    fail("炼神技能格子 subType 无效")
                cells.append({
                    "order": position,
                    "index": int(lua_index),
                    "id": cell_id,
                    "type": cell_type,
                    "sub_type": sub_type,
                    "talent_ids": decode_talent_ids(row.get("talentId"), cell_id) if sub_type == 1 else [],
                })
                cell_ids.append(cell_id)
            if len(set(cell_ids)) != len(cell_ids):
                fail("炼神技能格子 id 重复")
            if cell_ids != sorted(cell_ids):
                fail("炼神技能格子未按 id 升序")
            cell_types = {cell["type"] for cell in cells}
            if len(cell_types) != 1:
                fail(f"炼神技能格子 type 不一致：{sorted(cell_types)}")
            tree_type = next(iter(cell_types))

            skill_cells = [cell for cell in cells if cell["sub_type"] == _SKILL_SUB_TYPE]
            if not skill_cells:
                fail("炼神技能节点为空")

            # 5) 只按需读取技能涉及的天赋等级与完整 level 组。
            wanted_talent_ids = frozenset(
                talent_id
                for cell in skill_cells
                for talent_id in cell["talent_ids"]
            )
            role_levels = keyed_fields(role_levels_root, wanted_talent_ids)
            groups = keyed_fields(group_root, wanted_talent_ids)

            facts_cache: dict[int, dict] = {}

            def talent_facts(talent_id):
                cached = facts_cache.get(talent_id)
                if cached is not None:
                    return cached
                level = int_value(role_levels.get(talent_id))
                if level is None:
                    level = 0
                if level < 0:
                    fail(f"炼神天赋当前等级无效：{talent_id}")
                group = table_ref(groups.get(talent_id))
                if group is None:
                    fail(f"炼神天赋等级组未加载：{talent_id}")
                indexed_rows, row_count = c.reader.indexed_list_items(group)
                if not indexed_rows and row_count is None:
                    indexed_rows = sorted((as_int(k), v) for k, v in values(group).items() if as_int(k) is not None and as_int(k) >= 1)
                if not indexed_rows or (row_count is not None and row_count != len(indexed_rows)):
                    fail(f"炼神天赋等级组不完整：{talent_id}")
                raw_rows = dict(indexed_rows)
                defaults = read_runtime_config_defaults(c.reader, raw_rows, level_indexes)
                rows = {}
                for item in raw_rows.values():
                    row = packed(item, level_indexes, defaults)
                    position = as_int(row.get("level"))
                    if as_int(row.get("talentId")) != talent_id or position is None or position < 1 or position in rows:
                        fail(f"炼神天赋等级行身份错误：{talent_id}")
                    rows[position] = item
                max_level = len(rows)
                if sorted(rows) != list(range(1, max_level + 1)):
                    fail(f"炼神天赋等级组不连续：{talent_id}")
                if level > max_level:
                    fail(f"炼神天赋当前等级越界：{talent_id}")
                if level < max_level:
                    nxt = packed(rows[level + 1], level_indexes, defaults)
                    costs = decode_costs(nxt.get("consume"))
                    condition = nxt.get("condition")
                    condition_desc = nxt.get("conditionDesc")
                    if not isinstance(condition, str):
                        fail(f"炼神条件字段类型无效：{talent_id}, condition={condition!r}, desc={condition_desc!r}, defaults={defaults!r}")
                else:
                    # 满级没有下一等级配置，消耗与条件均为空。
                    costs = {}
                    condition = ""
                    condition_desc = ""
                facts = {
                    "talent_id": talent_id,
                    "current_level": level,
                    "max_level": max_level,
                    "costs": costs,
                    "condition": condition,
                    "condition_desc": condition_desc,
                }
                facts_cache[talent_id] = facts
                return facts

            def build_node(cell, ordinal):
                variants = []
                for talent_id in cell["talent_ids"]:
                    facts = talent_facts(talent_id)
                    variants.append({
                        "talent_id": facts["talent_id"],
                        "current_level": facts["current_level"],
                        "max_level": facts["max_level"],
                        "costs": dict(facts["costs"]),
                        "condition": facts["condition"],
                        "condition_desc": facts["condition_desc"],
                        "active": facts["current_level"] > 0,
                        "selectable": True,
                    })
                if len(variants) == _MAX_TALENT_VARIANTS:
                    active = [variant["active"] for variant in variants]
                    if sum(active) > 1:
                        fail(f"炼神二选一节点出现多个激活天赋：{cell['id']}")
                    requires_choice = sum(active) == 0
                    if not requires_choice:
                        for variant, is_active in zip(variants, active):
                            variant["selectable"] = is_active
                else:
                    requires_choice = False
                return {
                    "ordinal": ordinal,
                    "order": cell["order"],
                    "index": cell["index"],
                    "id": cell["id"],
                    "type": cell["type"],
                    "sub_type": cell["sub_type"],
                    "requires_choice": requires_choice,
                    "variants": variants,
                }

            nodes = [build_node(cell, ordinal) for ordinal, cell in enumerate(skill_cells)]
            return {
                "complete": True,
                "selected_tab_index": int(selected_tab_index),
                "type": tree_type,
                "cells": cells,
                "nodes": nodes,
                "read_only": True,
            }

        buttons, button_count = c.reader.indexed_list_items(table_ref(field(hosts[0], "btnList")))
        if button_count != len(tabs) or len(buttons) != len(tabs):
            fail("炼神页签按钮不完整")
        # The daily updater only needs the selected tab. Projecting all four
        # tabs on every read was the dominant cost of each upgrade cycle.
        indexes_to_project = range(len(tabs)) if all_tabs else (selected_tab_index,)
        results = []
        for index in indexes_to_project:
            tab = tabs[index]
            result = project(tab)
            result.update(tab_index=index, name=tab["name"], show_condition=tab["showCondition"],
                          tab_condition=tab["condition"], sort=tab["sort"])
            result["tab_unlocked"] = field(buttons[index][1], "isCustomFunctionOpen")
            if not isinstance(result["tab_unlocked"], bool):
                fail("炼神页签解锁状态不完整")
            results.append(result)
        levels = {v["talent_id"]: v["current_level"] for t in results for n in t["nodes"] for v in n["variants"]}
        # Prerequisites can name a talent on another tab. Read only those
        # missing level keys instead of projecting the unrelated full trees.
        dependencies = {
            int(match)
            for t in results for n in t["nodes"] for v in n["variants"]
            for match in re.findall(r"Cultivation\|(\d+)_\d+", v["condition"])
        } - levels.keys()
        if dependencies:
            external_levels = keyed_fields(role_levels_root, frozenset(dependencies))
            for talent_id in dependencies:
                level = int_value(external_levels.get(talent_id))
                if level is None:
                    level = 0
                if level < 0:
                    fail(f"炼神前置天赋等级无效：{talent_id}")
                levels[talent_id] = level
        for t in results:
            for n in t["nodes"]:
                for v in n["variants"]:
                    v["unlocked"] = cultivation_condition_met(v["condition"], levels)
        if all_tabs:
            return {"complete": True, "selected_tab_index": selected_tab_index, "tabs": results}
        return results[0]

    return read_ui_runtime_snapshot(("s_globalCfgIdx", "LianshenMgr"), read)


def read_lianshen_choice():
    """Read the currently open choice; no selection or upgrade side effects."""
    def read(c):
        panels = read_active_ui_window_panels(c, "LianShenTalentChooseView")
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError("炼神抉择窗口未就绪", code="ui_snapshot_pending")
        def field(key):
            return read_ui_object_field(c, panels[0].address, key)
        ids_ref = table_ref(field("talentIDs"))
        if ids_ref is None:
            raise FanxiuRuntimeMemoryError("炼神抉择候选未就绪", code="ui_snapshot_pending")
        ids = c.reader.numeric_fields(ids_ref.address, frozenset({1, 2}))
        candidates = [as_int(ids.get(i)) for i in (1, 2)]
        index = as_int(field("selectIndex"))
        selected = as_int(field("talentID"))
        if any(x is None or x <= 0 for x in candidates) or index not in (0, 1, 2):
            raise FanxiuRuntimeMemoryError("炼神抉择身份不完整", code="lianshen_choice_invalid")
        if index and selected != candidates[index-1]:
            raise FanxiuRuntimeMemoryError("炼神抉择选中身份不符", code="ui_snapshot_pending")
        return {"candidates": candidates, "selected_index": index, "selected_id": selected,
                "type": as_int(field("curSelectTalentType")), "can_activate": field("_CanActive")}
    return read_ui_runtime_snapshot((), read)


__all__ = ["read_lianshen_tree", "read_lianshen_choice", "cultivation_condition_met"]
