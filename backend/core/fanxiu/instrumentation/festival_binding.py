"""Read the client's existing Festival template/stage association without loading it."""
from datetime import datetime

from .activity_runtime import ACTIVITY_MANAGER_CACHE_KEY, ACTIVITY_MANAGER_METHODS
from .resource_auto_use import read_runtime_config_rows
from .runtime_memory import (
    FanxiuRuntimeMemoryError, LuaRef, as_int, manager_index_fields,
    resolve_lua_global_manager_root, table_ref,
)
from .ui_runtime_context import read_ui_runtime_snapshot


def read_festival_binding_runtime() -> dict:
    """Observe loaded LabourDay/MidAutumn VOs and Festival config rows.

    The base IDs 5100000/5800000 and table names follow the exported client
    managers. They identify framework routes, not a calendar festival or item
    consumption policy. Missing/uninstalled tables are explicitly unknown.
    No manager initialization, config load/uninstall, Lua call or GUI action
    occurs. References are decoded only within this process-bound snapshot.
    """
    def read(ctx):
        root, _, _ = resolve_lua_global_manager_root(
            ctx.memory, manager_key=ACTIVITY_MANAGER_CACHE_KEY,
            state_address=ctx.binding.state_address, global_name="ActivityMgr",
            required_methods=ACTIVITY_MANAGER_METHODS, validate=lambda *_: None,
        )
        manager = manager_index_fields(ctx.reader, root, ACTIVITY_MANAGER_METHODS)
        instance = ctx.reader.fields(manager.get("inst"))
        data = ctx.reader.fields(ctx.reader.fields(instance.get("Model")).get("ActivityData"))
        activities = table_ref(data.get("V_ActivationActivityDic"))
        if activities is None:
            raise FanxiuRuntimeMemoryError("活动VO字典尚未加载", code="data_not_loaded")
        values = ctx.reader.dictionary_fields(activities)
        if len(values) > 4096:
            raise FanxiuRuntimeMemoryError("活动VO超过有界范围", code="snapshot_incoherent")
        vos = []
        for value in values.values():
            row = ctx.reader.fields(value)
            if as_int(row.get("baseId")) in (5100000, 5800000):
                def integer(raw):
                    return ctx.reader.long(raw) if isinstance(raw, LuaRef) else as_int(raw)
                vos.append({key: integer(row.get(key)) for key in
                            ("id", "activityId", "baseId", "state", "startTime", "endTime")})
        methods = frozenset({"DBMgr", "GetConfigTable", "GetConfigTableByIdWithLog", "Inst_get"})
        db_root, _, environment = resolve_lua_global_manager_root(
            ctx.memory, manager_key="festival-binding-config",
            state_address=ctx.binding.state_address, global_name="DBMgr",
            required_methods=methods, validate=lambda *_: None,
        )
        db = manager_index_fields(ctx.reader, db_root, methods)
        configs = ctx.reader.fields(ctx.reader.fields(db.get("inst")).get("ConfigDic"))
        dictionary = table_ref(configs.get("_dt_"))
        if dictionary is None:
            raise FanxiuRuntimeMemoryError("已加载配置字典缺失", code="data_not_loaded")
        tables = {}
        for name, fields in (
            ("FestivalTemplate", ("winName",)),
            ("FestivalStage", ("type", "sort", "activityId")),
        ):
            full_name = "Festival." + name
            ref = table_ref(ctx.reader.string_fields(dictionary.address, frozenset({full_name})).get(full_name))
            if ref is None:
                tables[name] = {"status": "not_loaded", "rows": []}
                continue
            raw_rows = ctx.reader.dictionary_fields(ref)
            if len(raw_rows) > 256:
                raise FanxiuRuntimeMemoryError("节庆配置超过256行范围", code="snapshot_incoherent")
            keys = list(raw_rows)
            projected = read_runtime_config_rows(
                ctx.reader, raw_rows.values(), environment_address=environment,
                group_name="Festival", table_name=name, fields=fields,
                state_address=ctx.binding.state_address,
            )
            rows = []
            for key, row in zip(keys, projected):
                if not isinstance(key, (str, int)):
                    raise FanxiuRuntimeMemoryError("节庆配置身份不是标量", code="snapshot_incoherent")
                decoded = {"config_key": key}
                for field, value in row.items():
                    if isinstance(value, LuaRef):
                        if value.kind != "table":
                            raise FanxiuRuntimeMemoryError("节庆配置含非表引用", code="snapshot_incoherent")
                        array = ctx.reader.table(value.address)["array"]
                        if len(array) > 64 or any(not isinstance(v, (str, int, float, bool)) for v in array):
                            raise FanxiuRuntimeMemoryError("节庆配置关联不是有界标量数组", code="snapshot_incoherent")
                        value = array
                    decoded[field] = value
                rows.append(decoded)
            tables[name] = {"status": "loaded", "rows": rows}
        return {"captured_at": datetime.now().astimezone().isoformat(),
                "read_only": True, "scope": "currently_loaded_festival_binding",
                "classification": "framework_candidates_only",
                "pid": ctx.memory.pid, "process_start_ticks": ctx.memory.process_start_ticks,
                "activity_vos": vos, "tables": tables}
    return read_ui_runtime_snapshot(("baseId",), read, fast=True)
