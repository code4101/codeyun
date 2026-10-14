"""Read the native common-tip FIFO independently of attribute effect overlays."""
from .runtime_memory import FanxiuRuntimeMemoryError
from .ui_runtime_context import read_active_ui_window_panels, read_ui_object_field, read_ui_runtime_snapshot


def read_system_tips_snapshot():
    """Observe SystemTips' live/queued strings without invoking UI methods.

    SystemAttrTips and SystemAttrTips2 are separate channels. Their medicine
    effects do not acknowledge a common-tip request. The live FIFO is bounded
    and is not durable business history or permission to consume resources.
    """
    def read(c):
        panels = read_active_ui_window_panels(c, 'SystemTips')
        if not panels:
            # SystemTips destroys its queues on close. A verified absent
            # registry member is a lifecycle boundary, not unloaded strings.
            return {'complete': True, 'read_only': True, 'panel_open': False,
                    'live_messages': [], 'queued_messages': [], 'panel_identity': None,
                    'evidence': {'pid': c.binding.pid, 'process_start_ticks': c.binding.process_start_ticks}}
        if len(panels) != 1:
            return {'complete': False, 'read_only': True, 'reason': 'common_tips_panel_not_unique'}
        panel = panels[0]
        result = {}
        for field, output in (('temp_list', 'live_messages'), ('content_list_data', 'queued_messages')):
            messages, count = c.reader.list_items(read_ui_object_field(c, panel.address, field))
            if count is None or count != len(messages) or not 0 <= count <= 1024 or any(
                    not isinstance(message, str) for message in messages):
                raise FanxiuRuntimeMemoryError('普通提示字符串队列不完整', code='runtime_incomplete')
            result[output] = messages
        return {'complete': True, 'read_only': True, 'panel_open': True, **result,
                'panel_identity': f'{c.binding.pid}:{c.binding.process_start_ticks}:{panel.address:x}',
                'source': 'SystemTips.temp_list/content_list_data',
                'evidence': {'pid': c.binding.pid, 'process_start_ticks': c.binding.process_start_ticks}}
    return read_ui_runtime_snapshot(('temp_list', 'content_list_data'), read, fast=True)


def new_system_tip_messages(before, after):
    """Return newly appended FIFO messages, preserving ambiguous repeated buffers.

    Identity or incomplete reads cannot establish a response. A full buffer of
    identical messages is deliberately unchanged/ambiguous, never a new event.
    """
    identity = before.get('evidence') or {}
    if (any(type(identity.get(key)) is not int or identity[key] <= 0 for key in ('pid', 'process_start_ticks'))
            or not before.get('complete') or not after.get('complete') or identity != after.get('evidence')):
        return []
    if before.get('panel_open') is False:
        return after['live_messages'] if after.get('panel_open') is True else []
    if before.get('panel_identity') != after.get('panel_identity'):
        return []
    old, current = before['live_messages'], after['live_messages']
    for overlap in range(min(len(old), len(current)), -1, -1):
        if overlap == 0 or old[-overlap:] == current[:overlap]:
            return current[overlap:]
    return []
