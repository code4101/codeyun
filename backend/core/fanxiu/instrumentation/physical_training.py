"""Read native physical-training batch semantics; never invoke Lua methods.

PhysicalAutoSelectedView.useNum is a node count, while targetNum is the
cumulative normal-material cost. They are different units. Neither the UI's
perValue nor a static score ratio alone proves this account's activity gain.
"""
from datetime import datetime, timezone

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    read_active_ui_window_panels, read_ui_object_field, read_ui_runtime_snapshot,
)


def read_physical_training_books_snapshot():
    """Read loaded learned books and material identities without opening UI.

    Missing configuration remains explicitly incomplete. The learned list is
    a source for choosing a book, not proof that its current node is trainable;
    execution still needs the active dialog's cost path and task progress.
    """
    def read(context):
        def field(address, name):
            return read_ui_object_field(context, address, name)
        manager = table_ref(field(context.binding.environment_address, "PhysicalexerciseMgr"))
        instance = table_ref(field(manager.address, "inst")) if manager else None
        model = table_ref(field(instance.address, "Model")) if instance else None
        learned = table_ref(field(model.address, "hadLearnList")) if model else None
        if learned is None:
            raise FanxiuRuntimeMemoryError("炼体已学功法尚未加载", code="not_loaded")
        records, count = context.reader.list_items(learned)
        if count is None or count != len(records):
            raise FanxiuRuntimeMemoryError("炼体已学功法不完整", code="runtime_incomplete")
        native_data = table_ref(field(model.address, "PhysicalexerciseData"))
        config = table_ref(field(native_data.address, "PhysicalCfg")) if native_data else None
        books, identities = [], set()
        for record in records:
            book = table_ref(record)
            base_id = as_int(field(book.address, "baseId")) if book else None
            node = as_int(field(book.address, "nodeId")) if book else None
            stage = as_int(field(book.address, "jie")) if book else None
            if (base_id is None or base_id <= 0 or base_id in identities
                    or node is None or node < 0 or stage is None or stage < 0):
                raise FanxiuRuntimeMemoryError("炼体已学功法身份无效", code="runtime_incomplete")
            identities.add(base_id)
            books.append({"base_id": base_id, "current_node": node, "stage": stage,
                          "normal_item_id": None, "breakthrough_item_id": None,
                          "config_complete": False})
        if config and identities:
            raw = context.reader.numeric_fields(config.address, frozenset(identities))
            loaded_ids = [book["base_id"] for book in books if table_ref(raw.get(book["base_id"]))]
            if loaded_ids:
                from .resource_auto_use import read_runtime_config_rows
                decoded = read_runtime_config_rows(
                    context.reader, [raw[key] for key in loaded_ids],
                    environment_address=context.binding.environment_address,
                    state_address=context.binding.state_address,
                    group_name="PhysicalExercise", table_name="Physical",
                    fields=("id", "normalConsume", "breakConsume"))
                by_id = {}
                for expected_id, row in zip(loaded_ids, decoded, strict=True):
                    if as_int(row["id"]) != expected_id:
                        raise FanxiuRuntimeMemoryError("炼体材料配置身份不一致", code="runtime_incomplete")
                    by_id[expected_id] = row
                for book in books:
                    row = by_id.get(book["base_id"], {})
                    normal, breakthrough = as_int(row.get("normalConsume")), as_int(row.get("breakConsume"))
                    book.update(normal_item_id=normal, breakthrough_item_id=breakthrough,
                                config_complete=normal is not None and normal > 0
                                and breakthrough is not None and breakthrough >= 0)
        return {"ok": True, "available": True, "complete": True,
                "config_complete": all(book["config_complete"] for book in books),
                "books": books, "captured_at": datetime.now(timezone.utc).isoformat(),
                "source": "loaded_PhysicalexerciseMgr.Model.hadLearnList",
                "evidence": {"pid": context.memory.pid,
                             "process_start_ticks": context.memory.process_start_ticks,
                             "read_only": True, "lua_methods_invoked": False}}
    return read_ui_runtime_snapshot((), read, fast=True)


def read_physical_training_dialog_snapshot():
    """Observe exact book, selected nodes, material cost and spend toggles.

    The returned data does not authorize starting training. Live acceptance
    must compare task progress after an authorized batch, and any planner must
    include breakthrough costs and stage boundaries from the selected book.
    A closed dialog is NotLoaded, not a reason to open it or scan memory.
    """
    def read(context):
        panels = read_active_ui_window_panels(context, "PhysicalAutoSelectedView")
        if not panels:
            raise FanxiuRuntimeMemoryError("炼体批量配置页尚未打开", code="not_loaded")
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError("炼体批量配置页不唯一", code="runtime_incomplete")
        panel = panels[0]

        def field(address, name):
            return read_ui_object_field(context, address, name)

        data = table_ref(field(panel.address, "data"))
        activity = table_ref(field(panel.address, "actCfg"))
        if data is None or activity is None:
            raise FanxiuRuntimeMemoryError("炼体批量配置身份不完整", code="runtime_incomplete")
        result = {
            "base_id": as_int(field(data.address, "baseId")),
            "activity_id": as_int(field(activity.address, "id")),
            "current_node": as_int(field(data.address, "nodeId")),
            "stage": as_int(field(data.address, "jie")),
            "selected_nodes": as_int(field(panel.address, "useNum")),
            "maximum_nodes": as_int(field(panel.address, "useMax")),
            "normal_item_id": as_int(field(panel.address, "normalConsume")),
            "breakthrough_item_id": as_int(field(panel.address, "breakConsume")),
            "normal_material_cost": as_int(field(panel.address, "targetNum")),
            "available_normal_material": as_int(field(panel.address, "canUse")),
            "auto_buy_enabled": field(panel.address, "isAutoBuy"),
        }
        numeric = tuple(key for key in result if key != "auto_buy_enabled")
        if (any(result[key] is None or result[key] < 0 for key in numeric)
                or result["base_id"] <= 0 or result["activity_id"] <= 0
                or result["normal_item_id"] <= 0
                or result["selected_nodes"] > result["maximum_nodes"]
                or not isinstance(result["auto_buy_enabled"], bool)):
            raise FanxiuRuntimeMemoryError("炼体批量配置字段无效", code="runtime_incomplete")
        # The native default is false only when the setting/member is absent.
        # A malformed non-boolean setting must remain unknown, not disabled.
        manager = table_ref(field(context.binding.environment_address, "PhysicalexerciseMgr"))
        instance = table_ref(field(manager.address, "inst")) if manager else None
        settings = table_ref(field(instance.address, "_AutoSettingData")) if instance else None
        setting = table_ref(field(settings.address, "MultiplicityKey")) if settings else None
        multiplier = field(setting.address, "toggle") if setting else False
        if multiplier is None:
            multiplier = False  # Native InitLocateData keeps defaultBool=false.
        result["multiplicity_enabled"] = multiplier if isinstance(multiplier, bool) else None
        # UpdateView has already asked the native model for the reachable path.
        # Read that cache and GetCostInfo's populated entries, without calling
        # either method or loading config sheets. A slider's normal cost hides
        # breakthrough costs; expose both before a caller can plan a batch.
        model = table_ref(field(instance.address, "Model")) if instance else None
        native_data = table_ref(field(model.address, "PhysicalexerciseData")) if model else None
        cache = table_ref(field(native_data.address, "PhysicalExerciseConsumeCfgTb")) if native_data else None
        path = table_ref(context.reader.numeric_fields(
            cache.address, frozenset({result["base_id"]})).get(result["base_id"])) if cache else None
        costs = table_ref(field(instance.address, "itemStrTb")) if instance else None
        cost_nodes = []
        path_complete = result["maximum_nodes"] == 0
        if path and costs:
            records, count = context.reader.list_items(path)
            if count is None or count != len(records):
                raise FanxiuRuntimeMemoryError("炼体节点成本缓存不完整", code="runtime_incomplete")
            end_node = result["current_node"] + result["maximum_nodes"]
            for record in records:
                node = table_ref(record)
                node_id = as_int(field(node.address, "nodeId")) if node else None
                if node_id is None:
                    raise FanxiuRuntimeMemoryError("炼体成本节点身份缺失", code="runtime_incomplete")
                if not result["current_node"] < node_id <= end_node:
                    continue
                consume = field(node.address, "consume")
                cost = table_ref(field(costs.address, consume)) if isinstance(consume, str) else None
                item_id = as_int(field(cost.address, "itemId")) if cost else None
                quantity = as_int(field(cost.address, "itemNum")) if cost else None
                stage = as_int(field(node.address, "needJie"))
                if (item_id not in (result["normal_item_id"], result["breakthrough_item_id"])
                        or quantity is None or quantity < 0 or stage is None
                        or not 0 <= stage <= result["stage"]):
                    raise FanxiuRuntimeMemoryError("炼体成本或阶段边界不完整", code="runtime_incomplete")
                cost_nodes.append({"node_id": node_id, "item_id": item_id,
                                   "quantity": quantity, "required_stage": stage})
            cost_nodes.sort(key=lambda row: row["node_id"])
            path_complete = [row["node_id"] for row in cost_nodes] == list(
                range(result["current_node"] + 1, end_node + 1))
        selected = cost_nodes[:result["selected_nodes"]]
        selected_normal = sum(row["quantity"] for row in selected
                              if row["item_id"] == result["normal_item_id"])
        if path_complete and selected_normal != result["normal_material_cost"]:
            raise FanxiuRuntimeMemoryError("炼体成本缓存与批量配置不一致", code="runtime_incomplete")
        result.update(cost_nodes=cost_nodes, cost_path_complete=path_complete,
                      breakthrough_material_cost=(sum(row["quantity"] for row in selected
                          if row["item_id"] == result["breakthrough_item_id"]) if path_complete else None))
        # A pooled dialog can retain its address while switching book or slider.
        # Do not return a cost path joined to a different selection.
        identities = ((data.address, "baseId", "base_id"),
                      (data.address, "nodeId", "current_node"),
                      (data.address, "jie", "stage"),
                      (activity.address, "id", "activity_id"),
                      (panel.address, "useNum", "selected_nodes"),
                      (panel.address, "useMax", "maximum_nodes"),
                      (panel.address, "targetNum", "normal_material_cost"))
        if (table_ref(field(panel.address, "data")) != data
                or table_ref(field(panel.address, "actCfg")) != activity
                or any(as_int(field(address, name)) != result[key]
                       for address, name, key in identities)
                or field(panel.address, "isAutoBuy") != result["auto_buy_enabled"]):
            raise FanxiuRuntimeMemoryError("炼体批量选择在读取期间变化", code="runtime_incomplete")
        return {"ok": True, "available": True, "complete": instance is not None
                and result["multiplicity_enabled"] is not None and path_complete,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "source": "active_PhysicalAutoSelectedView", **result,
                "evidence": {"pid": context.memory.pid,
                             "process_start_ticks": context.memory.process_start_ticks,
                             "read_only": True, "lua_methods_invoked": False}}

    return read_ui_runtime_snapshot((), read, fast=True)


__all__ = ["read_physical_training_dialog_snapshot", "read_physical_training_books_snapshot"]
