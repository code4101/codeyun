"""Read the visible TowerDefense challenge selection without game methods."""
from datetime import datetime

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .resource_auto_use import read_runtime_config_rows
from .ui_runtime_context import (
    read_active_ui_window_panels, read_ui_object_field,
    read_ui_runtime_snapshot, read_ui_selected_tab_panel,
)


def read_national_challenge_panel():
    """Bind the current selected activity panel to its displayed level config.

    TowerDefenseInfoPanel.UpdateLevelTxtShow renders showLevelCfg.subLayer, rather
    than the reward threshold or the model's global pass level. Selecting a
    different chapter/level therefore changes this observation immediately.
    Missing, ambiguous or conflicting identities remain unknown.
    """
    def read(c):
        def field(address, name):
            return read_ui_object_field(c, address, name)

        def fail(reason):
            raise FanxiuRuntimeMemoryError('玄天镇魔：' + reason, code='runtime_incomplete')

        panels = {}
        for host in read_active_ui_window_panels(c, 'ActivityTemplateMainView'):
            selected = read_ui_selected_tab_panel(c, host.address)
            if selected is None:
                continue
            panel = selected[0]
            config = table_ref(field(panel, 'showLevelCfg'))
            if config is None or table_ref(field(panel, 'LevelTxt')) is None:
                continue
            activity = as_int(field(host.address, 'V_ActivityId'))
            panel_activity = as_int(field(panel, 'V_ActivityId'))
            if activity is None or activity <= 0 or panel_activity != activity:
                fail('父子活动身份不一致')
            row = read_runtime_config_rows(c.reader, [config],
                environment_address=c.binding.environment_address,
                group_name='TowerDefense', table_name='Level',
                fields=('id', 'type', 'layer', 'subLayer', 'stage'))[0]
            values = {k: as_int(v) for k, v in row.items()}
            show = as_int(field(panel, 'showLevel'))
            game_type = as_int(field(panel, 'V_GameType'))
            chapter = table_ref(field(panel, 'InitTypeData'))
            chapter_id = as_int(field(chapter.address, 'id')) if chapter else None
            if (any(v is None or v <= 0 for v in values.values())
                    or show != values['layer'] or game_type != values['type']
                    or chapter_id != values['stage']):
                fail(f'可见关卡与配置冲突：{row}, show={show}, type={game_type}')
            panels[panel] = dict(activity_id=activity, stage=values['subLayer'],
                                 level_config=values,
                                 passed_level=as_int(field(panel, 'curHasPassLevel')))
        if len(panels) != 1:
            fail(f'当前可见关卡面板不唯一：{len(panels)}')
        return dict(complete=True, **next(iter(panels.values())),
                    captured_at=datetime.now().astimezone().isoformat())

    return read_ui_runtime_snapshot(('s_globalCfgIdx',), read)
