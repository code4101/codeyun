"""Formal generator assembly contract, without frames, clicks or a fake game."""
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.runtime_gui import magic_auto_count_reader as adapter


def observation(selected, captured, *, panel=1234):
    return {'ok': True, 'available': True, 'complete': True,
            'source': 'active_map_act_auto_tips_view', 'captured_at_epoch': captured,
            'times': {'selected': selected, 'maximum': 1000},
            'evidence': {'pid': 2688, 'process_start_ticks': 5832, 'panel_address': panel,
                         'active_membership': 'UIShowMgr.V_M_compDic',
                         'read_only': True, 'lua_methods_invoked': False}}


@pytest.mark.parametrize('identity_drift', [False, True])
def test_formal_configure_generator_guards_and_wires_fresh_runtime_feedback(monkeypatch, identity_drift):
    from backend.core.fanxiu.data_annotation.tasks import magic_invasion_native_auto as native
    from backend.core.fanxiu.instrumentation import magic_invasion_auto_settings as provider
    from backend.core.fanxiu.runtime_gui.integer_count_control import read_positive_integer_count

    events = []
    samples = iter([observation(1, 1000),
                    observation(99, 1000.2, panel=1235 if identity_drift else 1234),
                    observation(100, 1000.4)])

    def snapshot():
        events.append('snapshot')
        return next(samples)

    def opening(*args):
        events.append('open')
        yield from ()

    def options(*args, **kwargs):
        events.append('options')
        yield from ()
        return {'configured': True}

    class GuardProtocol:
        def wait_scene(self, scenes, *, wait):
            assert scenes == [698] and wait == 10.0
            events.append('guard')
            yield from ()
            return SimpleNamespace(scene_id=698)

        def wait_action_settle(self, seconds):
            raise AssertionError('identity rejection must not enter reversible retry')

    original_factory = adapter.build_magic_auto_runtime_count_reader

    def factory(baseline, *, snapshot_reader):
        return original_factory(baseline, snapshot_reader=snapshot_reader, clock=lambda: 1001)

    def controller(context, assets, target, **kwargs):
        events.append('controller')
        assert target == 100
        assert kwargs['maximum'] == 1000 and kwargs['initial_count'] == 1
        assert kwargs['max_adjustments'] == 10
        callback = kwargs['runtime_count_reader']
        # Exercise the actual integer-reader policy, not a duplicate adapter.
        reads = {'runtime': 0, 'ocr': 0}
        first = read_positive_integer_count(None, assets, count_label='次数',
                                           runtime_reader=callback, read_counts=reads)
        second = read_positive_integer_count(None, assets, count_label='次数',
                                            runtime_reader=callback, read_counts=reads)
        assert (first, second) == (99, 100)
        assert reads == {'runtime': 2, 'ocr': 0}
        yield from ()
        return {'before': 1, 'after': second}

    monkeypatch.setattr(native, 'open_magic_invasion_auto_settings', opening)
    monkeypatch.setattr(native, 'configure_magic_invasion_auto_options', options)
    monkeypatch.setattr(native, 'set_verified_integer_slider_count', controller)
    monkeypatch.setattr(provider, 'read_magic_invasion_auto_settings_snapshot', snapshot)
    monkeypatch.setattr(provider, 'read_magic_invasion_auto_count_snapshot',
                        lambda *, expected_binding: snapshot())
    monkeypatch.setattr(adapter, 'build_magic_auto_runtime_count_reader', factory)
    generator = native.configure_magic_invasion_auto_for_batch(GuardProtocol(), count=100)
    if identity_drift:
        with pytest.raises(adapter.MagicAutoCountPanelRejected):
            next(generator)
        assert events == ['open', 'options', 'guard', 'snapshot', 'guard', 'controller', 'snapshot']
    else:
        with pytest.raises(StopIteration) as terminal:
            next(generator)
        assert terminal.value.value['count']['after'] == 100
        assert events == ['open', 'options', 'guard', 'snapshot', 'guard', 'controller', 'snapshot', 'snapshot']


@pytest.fixture(autouse=True)
def isolate_feedback_persistence(monkeypatch):
    from backend.core.fanxiu.runtime_gui import magic_auto_count_reader as module
    monkeypatch.setattr(module, 'persist_magic_auto_count_feedback', lambda stats: None)
