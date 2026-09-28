"""红包雨/节庆发言抽奖的只读事实；与聊天红包 UID 模型独立。"""
from .ui_runtime_context import read_ui_runtime_snapshot, read_ui_object_field
from .runtime_memory import resolve_lua_global_manager_root, manager_index_fields, as_int, table_ref


def read_festival_speech_snapshot() -> dict:
    """读取服务端同步 answer 和当前聊天频道，不执行 Lua、不发送消息。"""
    def read(ctx):
        def data(reader, root):
            manager = manager_index_fields(reader, root, frozenset({'LuaFestivalquestionMgr', 'Inst_get', 'GetSpeechType'}))
            instance = reader.fields(manager.get('inst'))
            return reader.fields(reader.fields(instance.get('Model')).get('Data'))
        root, _, _ = resolve_lua_global_manager_root(ctx.memory,
            manager_key='festival-speech', state_address=ctx.binding.state_address,
            global_name='FestivalquestionMgr',
            required_methods=frozenset({'LuaFestivalquestionMgr', 'Inst_get', 'GetSpeechType'}),
            validate=data)
        value = data(ctx.reader, root)
        channels = []
        storage = ctx.reader.table(ctx.binding.component_storage_address)
        panels = {}
        for raw in [*storage['array'], *storage['fields'].values()]:
            group = table_ref(raw)
            if group is None:
                continue
            members, count = ctx.reader.list_items(group)
            if not count:
                continue
            component = table_ref(members[-1])
            panel = table_ref(read_ui_object_field(ctx, component.address, 'm_panel')) if component else None
            if panel:
                panels[panel.address] = panel
        for obj in panels.values():
            channel = read_ui_object_field(ctx, obj.address, '_CurChannelType')
            sub_id = read_ui_object_field(ctx, obj.address, '_CurSubId')
            if channel is not None and sub_id is not None:
                row = {'channel': channel, 'sub_channel_id': sub_id}
                if row not in channels:
                    channels.append(row)
        return {'available': True, 'answer': as_int(value.get('answer')),
                'channels': channels, 'pid': ctx.memory.pid,
                'process_start_ticks': ctx.memory.process_start_ticks}
    return read_ui_runtime_snapshot([], read, fast=True)
