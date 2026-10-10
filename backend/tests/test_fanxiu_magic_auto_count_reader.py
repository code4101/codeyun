"""Pure snapshot parsing/callback contracts; no fake game or GUI context."""
from copy import deepcopy

import pytest

from backend.core.fanxiu.runtime_gui.magic_auto_count_reader import (
    MagicAutoCountPanelRejected, MagicAutoCountObservationUnavailable,
    build_magic_auto_runtime_count_reader, validate_magic_auto_count_snapshot,
)


def snapshot(count=100, captured=1000.0):
    # Public active-settings projection schema, not a Runtime emulator.
    return {'ok': True, 'available': True, 'complete': True,
            'source': 'active_map_act_auto_tips_view', 'captured_at_epoch': captured,
            'times': {'selected': count, 'maximum': 1000},
            'evidence': {'pid': 2688, 'process_start_ticks': 5832, 'panel_address': 1234,
                         'active_membership': 'UIShowMgr.V_M_compDic',
                         'read_only': True, 'lua_methods_invoked': False}}


def test_each_feedback_obtains_a_new_observation_without_reusing_baseline():
    samples = iter([snapshot(99, 1000.2), snapshot(100, 1000.4)])
    calls = []

    def read():
        calls.append('read')
        return next(samples)

    baseline = snapshot(1)
    original = deepcopy(baseline)
    reader = build_magic_auto_runtime_count_reader(baseline, snapshot_reader=read, clock=lambda: 1001)
    assert calls == []
    assert reader() == {'current': 99, 'maximum': 1000}
    assert reader() == {'current': 100, 'maximum': 1000}
    assert calls == ['read', 'read']
    assert baseline == original


@pytest.mark.parametrize('field,value', [('pid', 2689), ('process_start_ticks', 5833),
                                       ('panel_address', 1235)])
def test_replacement_identity_hard_stops_instead_of_ocr_downgrade(field, value):
    changed = snapshot()
    changed['evidence'][field] = value
    reader = build_magic_auto_runtime_count_reader(
        snapshot(), snapshot_reader=lambda: changed, clock=lambda: 1001)
    with pytest.raises(MagicAutoCountPanelRejected, match='身份') as error:
        reader()
    assert not isinstance(error.value, RuntimeError)


@pytest.mark.parametrize('field,value', [('selected', True), ('selected', 1.2),
    ('selected', '100'), ('selected', 0), ('selected', 1001), ('maximum', 0)])
def test_count_values_are_not_coerced_or_clamped(field, value):
    sample = snapshot()
    sample['times'][field] = value
    with pytest.raises(MagicAutoCountPanelRejected):
        validate_magic_auto_count_snapshot(sample, now=1001)


@pytest.mark.parametrize('field,value', [('source', 'historical_catalog'), ('complete', False),
    ('ok', False), ('available', False), ('captured_at_epoch', 900),
    ('captured_at_epoch', 1002), ('captured_at_epoch', float('nan'))])
def test_incomplete_foreign_stale_and_future_snapshots_never_supply_count(field, value):
    sample = snapshot()
    sample[field] = value
    with pytest.raises(MagicAutoCountPanelRejected):
        validate_magic_auto_count_snapshot(sample, now=1001)


@pytest.mark.parametrize('field,value', [('active_membership', 'unbound'),
    ('read_only', False), ('lua_methods_invoked', True), ('pid', True)])
def test_active_read_only_membership_is_mandatory(field, value):
    sample = snapshot()
    sample['evidence'][field] = value
    with pytest.raises(MagicAutoCountPanelRejected):
        validate_magic_auto_count_snapshot(sample, now=1001)


def test_partial_projection_returns_no_cached_count_and_keeps_safe_fallback_signal():
    reader = build_magic_auto_runtime_count_reader(snapshot(), clock=lambda: 1001,
        snapshot_reader=lambda: {'source': 'active_map_act_auto_tips_view',
            'complete': False, 'error_code': 'memory_read_failed', 'reason': 'transient'})
    with pytest.raises(MagicAutoCountObservationUnavailable, match='transient'):
        reader()


def test_changed_maximum_is_not_accepted_on_same_panel():
    changed = snapshot()
    changed['times']['maximum'] = 2000
    reader = build_magic_auto_runtime_count_reader(snapshot(), clock=lambda: 1001,
                                                   snapshot_reader=lambda: changed)
    with pytest.raises(MagicAutoCountPanelRejected):
        reader()


@pytest.mark.parametrize('captured', [1000.0, 999.9])
def test_first_feedback_must_be_newer_than_baseline(captured):
    reader = build_magic_auto_runtime_count_reader(snapshot(), clock=lambda: 1001,
        snapshot_reader=lambda: snapshot(captured=captured))
    with pytest.raises(MagicAutoCountPanelRejected, match='递增'):
        reader()


@pytest.mark.parametrize('captured', [1000.2, 1000.1])
def test_repeated_or_reverse_second_feedback_cannot_prove_stability(captured):
    samples = iter([snapshot(100, 1000.2), snapshot(100, captured)])
    reader = build_magic_auto_runtime_count_reader(snapshot(), clock=lambda: 1001,
        snapshot_reader=lambda: next(samples))
    assert reader()['current'] == 100
    with pytest.raises(MagicAutoCountPanelRejected, match='递增'):
        reader()


@pytest.mark.parametrize('reason', ['Ambiguous: 两个active弹层', 'autoTimes与useNum不一致'])
def test_incomplete_native_identity_or_count_is_not_transient_ocr_fallback(reason):
    reader = build_magic_auto_runtime_count_reader(snapshot(), clock=lambda: 1001,
        snapshot_reader=lambda: {'source': 'active_map_act_auto_tips_view',
            'complete': False, 'error_code': 'runtime_incomplete', 'reason': reason})
    with pytest.raises(MagicAutoCountPanelRejected) as error:
        reader()
    assert not isinstance(error.value, RuntimeError)


def test_temporary_memory_failure_keeps_existing_guarded_ocr_signal():
    assert issubclass(MagicAutoCountObservationUnavailable, RuntimeError)
    assert not issubclass(MagicAutoCountPanelRejected, RuntimeError)


def test_existing_integer_reader_consumes_adapter_current_without_ocr():
    from backend.core.fanxiu.runtime_gui.integer_count_control import read_positive_integer_count
    reader = build_magic_auto_runtime_count_reader(snapshot(1), clock=lambda: 1001,
        snapshot_reader=lambda: snapshot(100, 1000.2))
    reads = {'runtime': 0, 'ocr': 0}
    assert read_positive_integer_count(None, None, count_label='次数',
                                      runtime_reader=reader, read_counts=reads) == 100
    assert reads == {'runtime': 1, 'ocr': 0}


def test_existing_integer_reader_cannot_swallow_identity_rejection(monkeypatch):
    from backend.core.fanxiu.runtime_gui import integer_count_control as control
    changed = snapshot(100, 1000.2)
    changed['evidence']['panel_address'] += 1
    reader = build_magic_auto_runtime_count_reader(snapshot(), clock=lambda: 1001,
                                                   snapshot_reader=lambda: changed)

    def forbidden_ocr(*args):
        raise AssertionError('identity rejection must stop before OCR')

    monkeypatch.setattr(control, '_ocr_count', forbidden_ocr)
    with pytest.raises(MagicAutoCountPanelRejected):
        control.read_positive_integer_count(None, None, count_label='次数', runtime_reader=reader)


def test_existing_integer_reader_routes_only_transient_error_to_ocr(monkeypatch):
    # Deterministic read-selection policy only; no scene or GUI is simulated.
    from backend.core.fanxiu.runtime_gui import integer_count_control as control
    reader = build_magic_auto_runtime_count_reader(snapshot(), clock=lambda: 1001,
        snapshot_reader=lambda: {'source': 'active_map_act_auto_tips_view',
            'complete': False, 'error_code': 'memory_read_failed'})
    monkeypatch.setattr(control, '_ocr_count', lambda *args: 99)
    reads = {'runtime': 0, 'ocr': 0}
    assert control.read_positive_integer_count(None, None, count_label='次数',
                                              runtime_reader=reader, read_counts=reads) == 99
    assert reads == {'runtime': 1, 'ocr': 1}


@pytest.fixture(autouse=True)
def isolate_feedback_persistence(monkeypatch):
    from backend.core.fanxiu.runtime_gui import magic_auto_count_reader as module
    original = module.persist_magic_auto_count_feedback
    monkeypatch.setattr(module, 'persist_magic_auto_count_feedback', lambda stats: None)
    return original


@pytest.mark.parametrize('failure,expected', [
    (KeyboardInterrupt(), 'failed'),
    (MagicAutoCountPanelRejected('identity'), 'failed'),
    (MagicAutoCountObservationUnavailable('memory'), 'fallback_eligible'),
])
def test_feedback_retains_started_and_failed_query_without_controller_completion(failure, expected):
    recorded = []
    def read():
        raise failure
    reader = build_magic_auto_runtime_count_reader(
        snapshot(), snapshot_reader=read, clock=lambda: 1001,
        stats_recorder=lambda s: recorded.append(deepcopy(s)))
    with pytest.raises(type(failure)):
        reader()
    assert recorded[0]['events'][-1]['status'] == 'started'
    assert recorded[-1]['events'][-1]['status'] == expected
    assert recorded[-1]['failed'] == 1
    assert recorded[-1]['succeeded'] == 0
    assert recorded[-1]['fallback_eligible'] == int(expected == 'fallback_eligible')
    assert recorded[-1]['interrupted'] == int(isinstance(failure, KeyboardInterrupt))
    assert recorded[-1]['completed_query_timing']['n'] == 0


def test_completed_feedback_keeps_provider_phases_and_query_statistics():
    recorded = []
    sample = snapshot(captured=1000.1)
    sample['performance'] = {'non_overlapping_seconds': {'unique_panel': 0.1}}
    reader = build_magic_auto_runtime_count_reader(
        snapshot(), snapshot_reader=lambda: sample, clock=lambda: 1001,
        stats_recorder=lambda s: recorded.append(deepcopy(s)))
    reader()
    final = recorded[-1]
    assert final['succeeded'] == 1
    assert final['completed_query_timing']['n'] == 1
    assert final['completed_query_timing']['population_std_seconds'] == 0
    assert final['events'][-1]['provider_performance'] == sample['performance']


def test_canonical_full_reader_is_replaced_by_bound_public_count_projection(monkeypatch):
    from backend.core.fanxiu.instrumentation import magic_invasion_auto_settings as provider
    bindings = []
    def count_read(*, expected_binding):
        bindings.append(expected_binding)
        return snapshot(captured=1000.1)
    monkeypatch.setattr(provider, 'read_magic_invasion_auto_count_snapshot', count_read)
    reader = build_magic_auto_runtime_count_reader(
        snapshot(), snapshot_reader=provider.read_magic_invasion_auto_settings_snapshot,
        clock=lambda: 1001)
    assert reader()['current'] == 100
    assert bindings == [{'pid': 2688, 'process_start_ticks': 5832, 'panel_address': 1234, 'maximum': 1000}]



def test_durable_feedback_keeps_exact_reader_and_latest_pointer(monkeypatch, isolate_feedback_persistence):
    from backend.core.fanxiu.runtime_gui import magic_auto_count_reader as module
    from backend.core.messaging import codex_results
    from types import SimpleNamespace
    data = {}
    store = SimpleNamespace(set_meta=lambda k, v: data.update({k: deepcopy(v)}),
                            get_meta=lambda k: data.get(k))
    monkeypatch.setattr(codex_results, 'result_store', lambda: store)
    writer = isolate_feedback_persistence
    first, second = 'a' * 32, 'b' * 32
    writer({'reader_id': first, 'failed': 1})
    writer({'reader_id': second, 'succeeded': 1})
    assert module.read_magic_auto_count_feedback(reader_id=first)['statistics']['failed'] == 1
    assert module.read_magic_auto_count_feedback()['statistics']['reader_id'] == second
    with pytest.raises(ValueError):
        module.read_magic_auto_count_feedback(reader_id='../invalid')


@pytest.mark.parametrize('failure', [None, MagicAutoCountPanelRejected('original identity rejection'),
                                   MagicAutoCountObservationUnavailable('original memory failure')])
def test_recorder_and_logging_failures_preserve_original_result(monkeypatch, failure):
    import logging
    from types import SimpleNamespace
    queries = []
    def broken_logger(*args, **kwargs):
        raise OSError('broken log handler')
    monkeypatch.setattr(logging.getLogger('backend.core.fanxiu.runtime_gui.magic_auto_count_reader'), 'exception', broken_logger)
    def recorder(stats):
        raise OSError('telemetry unavailable')
    def read():
        queries.append(1)
        if failure is not None:
            raise failure
        return snapshot(99, 1000.2)
    reader = build_magic_auto_runtime_count_reader(snapshot(), snapshot_reader=read,
        clock=lambda: 1001, stats_recorder=recorder)
    if failure is None:
        assert reader() == {'current': 99, 'maximum': 1000}
    else:
        with pytest.raises(type(failure)) as raised:
            reader()
        assert raised.value is failure
    assert queries == [1]


def test_durable_publication_and_logging_failures_remain_secondary(monkeypatch, isolate_feedback_persistence):
    import logging
    from types import SimpleNamespace
    from backend.core.messaging import codex_results
    def failed_store(*args):
        raise OSError('store unavailable')
    def failed_log(*args, **kwargs):
        raise OSError('log unavailable')
    monkeypatch.setattr(codex_results, 'result_store', failed_store)
    monkeypatch.setattr(logging.getLogger('backend.core.fanxiu.runtime_gui.magic_auto_count_reader'), 'exception', failed_log)
    isolate_feedback_persistence({'reader_id': 'a' * 32})
