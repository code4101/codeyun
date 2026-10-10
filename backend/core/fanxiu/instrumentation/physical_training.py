"""Read native physical-training batch semantics; never invoke Lua methods.

PhysicalAutoSelectedView.useNum is a training-attempt count, while targetNum is the
cumulative normal-material cost. They are different units. Neither the UI's
perValue nor a static score ratio alone proves this account's activity gain.
"""
from datetime import datetime, timezone
from time import monotonic

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    read_active_ui_window_panels, read_ui_object_field, read_ui_runtime_snapshot,
)


def read_physical_training_execution_snapshot():
    """Observe current auto-training membership, never a closed pooled panel.

    Native ActiveNode counts successful attempts separately from node gain
    (PhysicalAutoStartView.cuiTiCount versus offsetLevel). A closed panel plus
    exact material deduction proves a settled material effect; it does not
    reconstruct how many server responses succeeded or a fixed node increment.
    """
    def read(context):
        panels = read_active_ui_window_panels(context, 'PhysicalAutoStartView')
        if len(panels) > 1:
            raise FanxiuRuntimeMemoryError('炼体执行面板不唯一', code='runtime_incomplete')
        return {'complete': True, 'active': bool(panels),
                'captured_at': datetime.now(timezone.utc).isoformat(),
                'source': 'current_PhysicalAutoStartView_membership',
                'evidence': {'pid': context.memory.pid,
                             'process_start_ticks': context.memory.process_start_ticks,
                             'read_only': True, 'lua_methods_invoked': False}}
    return read_ui_runtime_snapshot((), read, fast=True)


def read_physical_training_cost_range_snapshot(*, base_id, first_node, last_node):
    """Read a bounded already-loaded cost range even after the dialog closes.

    This diagnoses node jumps beyond a configured attempt prefix. It reads
    immutable native node/config membership and GetCostInfo's populated cache;
    it never opens a panel, loads a sheet, invokes Lua, or proves which nodes
    the server charged. Missing cache entries fail closed instead of guessing.
    """
    if (any(type(v) is not int for v in (base_id, first_node, last_node))
            or base_id <= 0 or first_node < 0 or not first_node <= last_node
            or last_node - first_node >= 512):
        raise ValueError('炼体成本查询需要功法身份及最多512个明确节点')

    def read(context):
        def field(address, name):
            return read_ui_object_field(context, address, name)
        manager = table_ref(field(context.binding.environment_address, 'PhysicalexerciseMgr'))
        instance = table_ref(field(manager.address, 'inst')) if manager else None
        model = table_ref(field(instance.address, 'Model')) if instance else None
        data = table_ref(field(model.address, 'PhysicalexerciseData')) if model else None
        cache = table_ref(field(data.address, 'PhysicalExerciseConsumeCfgTb')) if data else None
        path = table_ref(context.reader.numeric_fields(cache.address, frozenset({base_id})).get(base_id)) if cache else None
        costs = table_ref(field(instance.address, 'itemStrTb')) if instance else None
        if not path or not costs:
            raise FanxiuRuntimeMemoryError('炼体功法成本缓存尚未加载', code='not_loaded')
        wrapper = context.reader.fields(path)
        count, storage = as_int(wrapper.get('count')), table_ref(wrapper.get('_dt_'))
        if count is None or count <= 0 or storage is None:
            raise FanxiuRuntimeMemoryError('炼体成本缓存成员不完整', code='runtime_incomplete')
        ends = context.reader.numeric_fields(storage.address, frozenset({1, count}))
        first, last = table_ref(ends.get(1)), table_ref(ends.get(count))
        start = as_int(field(first.address, 'nodeId')) if first else None
        end = as_int(field(last.address, 'nodeId')) if last else None
        if start is None or end is None or end - start + 1 != count or not start <= first_node <= last_node <= end:
            raise FanxiuRuntimeMemoryError('炼体成本缓存未完整覆盖查询范围', code='runtime_incomplete')
        keys = frozenset(node - start + 1 for node in range(first_node, last_node + 1))
        records = context.reader.numeric_fields(storage.address, keys)
        if set(records) != set(keys):
            raise FanxiuRuntimeMemoryError('炼体成本节点成员缺失', code='runtime_incomplete')
        rows = []
        for key in sorted(keys):
            node = table_ref(records[key])
            identity = as_int(field(node.address, 'nodeId')) if node else None
            consume = field(node.address, 'consume') if node else None
            cost = table_ref(field(costs.address, consume)) if isinstance(consume, str) else None
            item = as_int(field(cost.address, 'itemId')) if cost else None
            quantity = as_int(field(cost.address, 'itemNum')) if cost else None
            stage = as_int(field(node.address, 'needJie')) if node else None
            if identity != start + key - 1 or item is None or item <= 0 or quantity is None or quantity < 0 or stage is None or stage < 0:
                raise FanxiuRuntimeMemoryError('炼体成本节点身份或材料不完整', code='runtime_incomplete')
            rows.append(dict(node_id=identity, item_id=item, quantity=quantity, required_stage=stage))
        return dict(complete=True, base_id=base_id, first_node=first_node, last_node=last_node,
                    cost_nodes=rows, captured_at=datetime.now(timezone.utc).isoformat(),
                    source='loaded_PhysicalExerciseConsumeCfgTb+itemStrTb',
                    evidence=dict(pid=context.memory.pid, process_start_ticks=context.memory.process_start_ticks,
                                  read_only=True, lua_methods_invoked=False))
    return read_ui_runtime_snapshot((), read, fast=True)


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
            process_ref = table_ref(field(book.address, 'process'))
            process = None
            if process_ref:
                process = {}
                for name in ('finish', 'leftNum', 'targetNum', 'activityId', 'createTime', 'startTime', 'endTime'):
                    value = field(process_ref.address, name)
                    process[name] = context.reader.long(value) if table_ref(value) else value
            books.append({"base_id": base_id, "current_node": node, "stage": stage,
                          "process": process,
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


def read_physical_training_scoring_snapshot(*, base_id: int, item_id: int):
    """Read loaded native scoring and deferred settlement, without settling it.

    A batch can create an unfinished process; its target/remaining/extra
    quantities must not be treated as a final task-score increment. The native
    GetPerScore fallback is reported separately from a loaded configured rate.
    This evidence alone never authorizes consumption or a fixed-rate plan.
    """
    if type(base_id) is not int or base_id <= 0 or type(item_id) is not int or item_id <= 0:
        raise ValueError('炼体功法与材料身份必须为正整数')

    def read(context):
        def field(address, name):
            return read_ui_object_field(context, address, name)
        manager = table_ref(field(context.binding.environment_address, 'PhysicalexerciseMgr'))
        instance = table_ref(field(manager.address, 'inst')) if manager else None
        model = table_ref(field(instance.address, 'Model')) if instance else None
        learned = table_ref(field(model.address, 'hadLearnList')) if model else None
        if learned is None:
            raise FanxiuRuntimeMemoryError('炼体结算事实尚未加载', code='not_loaded')
        records, count = context.reader.list_items(learned)
        if count != len(records):
            raise FanxiuRuntimeMemoryError('炼体功法记录不完整', code='runtime_incomplete')
        matches = [book for value in records if (book := table_ref(value))
                   and as_int(field(book.address, 'baseId')) == base_id]
        if len(matches) != 1:
            raise FanxiuRuntimeMemoryError('炼体结算功法不唯一', code='runtime_incomplete')
        process = table_ref(field(matches[0].address, 'process'))
        settlement = None
        if process:
            settlement = {}
            for name in ('baseId', 'activityId', 'targetNum', 'leftNum', 'extraNum', 'startTime', 'endTime', 'finish'):
                value = field(process.address, name)
                settlement[name] = context.reader.long(value) if table_ref(value) else value
        rates = table_ref(field(instance.address, 'perScoreValueTb'))
        rate = as_int(context.reader.numeric_fields(rates.address, frozenset({item_id})).get(item_id)) if rates else None
        return {'base_id': base_id, 'item_id': item_id, 'configured_score_per_item': rate,
                'native_fallback_score_per_item': 350, 'settlement': settlement,
                'captured_at': datetime.now(timezone.utc).isoformat(),
                'evidence': {'pid': context.memory.pid, 'process_start_ticks': context.memory.process_start_ticks,
                             'read_only': True, 'lua_methods_invoked': False}}
    return read_ui_runtime_snapshot((), read, fast=True)


def read_physical_training_dialog_snapshot(*, max_cost_nodes: int = 512):
    """Observe exact book, selected nodes, material cost and spend toggles.

    The returned data does not authorize starting training. Live acceptance
    must compare task progress after an authorized batch, and any planner must
    include breakthrough costs and stage boundaries from the selected book.
    A closed dialog is NotLoaded, not a reason to open it or scan memory.
    No dialog address or cost observation survives this request. A separate
    fresh context rejects changed selection/process identity. Live acceptance
    of slider changes, book switches and dialog reopen remains pending; a guard
    mismatch belongs to the UI observation layer, never grounds for a click retry.
    Cost coverage is an explicit bounded prefix; ``cost_path_nodes`` does not
    replace ``maximum_nodes``. A selection outside that prefix is incomplete.
    """
    if type(max_cost_nodes) is not int or not 1 <= max_cost_nodes <= 4096:
        raise ValueError("炼体成本观察范围必须为1到4096个节点")
    def activity_identity(context, activity):
        value = as_int(read_ui_object_field(context, activity.address, "id"))
        if value is not None:
            return value
        from .resource_auto_use import read_runtime_config_rows
        row = read_runtime_config_rows(
            context.reader, [activity],
            environment_address=context.binding.environment_address,
            state_address=context.binding.state_address,
            group_name="Activity", table_name="Activity", fields=("id",))[0]
        return as_int(row["id"])

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
            "activity_id": activity_identity(context, activity),
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
            raise FanxiuRuntimeMemoryError(
                f"炼体批量配置字段无效：{result}", code="runtime_incomplete")
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
        coverage = min(max_cost_nodes, result["maximum_nodes"])
        if path and costs:
            # Native cache is sorted by nodeId and can hold 8000+ entries.
            # Full table() expansion exceeds its general array safety bound.
            # Read only known CList integer keys, preserving exact membership
            # and validating the sorted index->node join before using costs.
            wrapper = context.reader.fields(path)
            count = as_int(wrapper.get("count"))
            storage = table_ref(wrapper.get("_dt_"))
            if count is None or count <= 0 or storage is None:
                raise FanxiuRuntimeMemoryError("炼体节点成本缓存不完整", code="runtime_incomplete")
            endpoints = context.reader.numeric_fields(storage.address, frozenset({1, count}))
            first, last = table_ref(endpoints.get(1)), table_ref(endpoints.get(count))
            first_id = as_int(field(first.address, "nodeId")) if first else None
            last_id = as_int(field(last.address, "nodeId")) if last else None
            if first_id is None or last_id is None or last_id - first_id + 1 != count:
                raise FanxiuRuntimeMemoryError("炼体成本缓存排序或跨度不完整", code="runtime_incomplete")
            end_node = result["current_node"] + coverage
            wanted = range(result["current_node"] + 1, end_node + 1)
            keys = frozenset(node_id - first_id + 1 for node_id in wanted)
            if any(not 1 <= key <= count for key in keys):
                raise FanxiuRuntimeMemoryError("炼体成本缓存未覆盖所需节点", code="runtime_incomplete")
            records = context.reader.numeric_fields(storage.address, keys)
            if set(records) != set(keys):
                raise FanxiuRuntimeMemoryError("炼体成本节点成员缺失", code="runtime_incomplete")
            for key in sorted(keys):
                record = records[key]
                node = table_ref(record)
                node_id = as_int(field(node.address, "nodeId")) if node else None
                if node_id != first_id + key - 1:
                    raise FanxiuRuntimeMemoryError("炼体成本节点索引身份不符", code="runtime_incomplete")
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
        selection_covered = path_complete and result["selected_nodes"] <= coverage
        if selection_covered and selected_normal != result["normal_material_cost"]:
            raise FanxiuRuntimeMemoryError("炼体成本缓存与批量配置不一致", code="runtime_incomplete")
        result.update(cost_nodes=cost_nodes, cost_path_complete=path_complete,
                      cost_path_nodes=coverage, selection_cost_verified=selection_covered,
                      breakthrough_material_cost=(sum(row["quantity"] for row in selected
                          if row["item_id"] == result["breakthrough_item_id"]) if selection_covered else None))
        return {"ok": True, "available": True, "complete": instance is not None
                and result["multiplicity_enabled"] is not None and selection_covered,
                "_selection_refs": (panel.address, data.address, activity.address),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "source": "active_PhysicalAutoSelectedView", **result,
                "evidence": {"pid": context.memory.pid,
                             "process_start_ticks": context.memory.process_start_ticks,
                             "read_only": True, "lua_methods_invoked": False}}

    read_started = monotonic()
    snapshot = read_ui_runtime_snapshot((), read, fast=True)
    read_seconds = monotonic() - read_started

    def guard(context):
        # A NEW context owns fresh bytes. Re-reading via the cost reader above
        # would only compare its cache to itself. Keep this guard narrow rather
        # than fetching the full node-cost path twice.
        panels = read_active_ui_window_panels(context, "PhysicalAutoSelectedView")
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError("炼体批量页在读取期间关闭或变更", code="runtime_incomplete")
        panel = panels[0]
        def field(address, name):
            return read_ui_object_field(context, address, name)
        data = table_ref(field(panel.address, "data"))
        activity = table_ref(field(panel.address, "actCfg"))
        if data is None or activity is None:
            raise FanxiuRuntimeMemoryError("炼体批量身份在读取期间丢失", code="runtime_incomplete")
        identities = ((data.address, "baseId", "base_id"),
                      (data.address, "nodeId", "current_node"),
                      (data.address, "jie", "stage"),
                      (panel.address, "useNum", "selected_nodes"),
                      (panel.address, "useMax", "maximum_nodes"),
                      (panel.address, "targetNum", "normal_material_cost"),
                      (panel.address, "normalConsume", "normal_item_id"),
                      (panel.address, "breakConsume", "breakthrough_item_id"),
                      (panel.address, "canUse", "available_normal_material"))
        manager = table_ref(field(context.binding.environment_address, "PhysicalexerciseMgr"))
        instance = table_ref(field(manager.address, "inst")) if manager else None
        settings = table_ref(field(instance.address, "_AutoSettingData")) if instance else None
        setting = table_ref(field(settings.address, "MultiplicityKey")) if settings else None
        multiplier = field(setting.address, "toggle") if setting else False
        if multiplier is None:
            multiplier = False
        if ((panel.address, data.address, activity.address) != snapshot["_selection_refs"]
                or (context.memory.pid, context.memory.process_start_ticks) !=
                (snapshot["evidence"]["pid"], snapshot["evidence"]["process_start_ticks"])
                or any(as_int(field(address, name)) != snapshot[key]
                       for address, name, key in identities)
                or activity_identity(context, activity) != snapshot["activity_id"]
                or field(panel.address, "isAutoBuy") != snapshot["auto_buy_enabled"]
                or multiplier != snapshot["multiplicity_enabled"]):
            raise FanxiuRuntimeMemoryError("炼体批量选择在读取期间变化", code="runtime_incomplete")
        return datetime.now(timezone.utc).isoformat()

    guard_started = monotonic()
    snapshot["selection_verified_at"] = read_ui_runtime_snapshot((), guard, fast=True)
    # Timings include context/root resolution, not just cost decoding. Keep
    # both independent reads visible so latency cannot justify dropping the
    # fresh selection guard or be mistaken for native training duration.
    snapshot["read_elapsed_seconds"] = read_seconds
    snapshot["selection_guard_elapsed_seconds"] = monotonic() - guard_started
    snapshot["total_read_elapsed_seconds"] = monotonic() - read_started
    del snapshot["_selection_refs"]
    return snapshot


__all__ = ["read_physical_training_dialog_snapshot", "read_physical_training_books_snapshot",
           "read_physical_training_scoring_snapshot"]
