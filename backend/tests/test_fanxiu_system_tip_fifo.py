from backend.core.fanxiu.instrumentation.system_tips import new_system_tip_messages


def snapshot(messages, *, panel='native-panel', pid=7, complete=True):
    return {'complete': complete, 'panel_open': panel is not None,
            'panel_identity': panel, 'live_messages': messages,
            'evidence': {'pid': pid, 'process_start_ticks': 99}}


def test_fifo_append_and_rollover_only_return_new_suffix():
    assert new_system_tip_messages(snapshot(['old']), snapshot(['old', 'response'])) == ['response']
    assert new_system_tip_messages(snapshot(['old', 'anchor']), snapshot(['anchor', 'response'])) == ['response']


def test_identical_repeated_buffer_does_not_prove_a_new_response():
    assert new_system_tip_messages(snapshot(['same'] * 10), snapshot(['same'] * 10)) == []


def test_closed_panel_boundary_and_identity_changes():
    assert new_system_tip_messages(snapshot([], panel=None), snapshot(['response'])) == ['response']
    assert new_system_tip_messages(snapshot(['old']), snapshot(['response'], panel='replaced')) == []
    assert new_system_tip_messages(snapshot(['old']), snapshot(['response'], pid=8)) == []
    assert new_system_tip_messages(snapshot([], panel=None), snapshot(['response'], complete=False)) == []


def test_missing_process_identity_never_acknowledges_a_response():
    before, after = snapshot([]), snapshot(['response'])
    before['evidence'] = after['evidence'] = {}
    assert new_system_tip_messages(before, after) == []
