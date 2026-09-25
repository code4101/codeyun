"""Regression cases from live choice nodes and swallowed animation taps."""
import pytest

from backend.core.fanxiu.data_annotation.tasks import three_emperors_comprehend as task


def result(value):
    yield from ()
    return value


def finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as done:
            return done.value


class Context:
    def __init__(self):
        self.clicks = []

    def wait_scene(self, scenes, **kwargs):
        return result(scenes[0])

    def wait_action_settle(self, seconds):
        return result(None)

    def click_shape_center_fast(self, scene, shape):
        self.clicks.append((scene, shape))

    def wait_click(self, scene, shape):
        self.clicks.append((scene, shape))
        return result(None)


def setup(monkeypatch, *, choice=False, stocks=(100,)):
    state = dict(isMax=False, curLevel=10, cost=10, isEnough=None if choice else True,
                 node=dict(skillGroup=1 if choice else 0, nodeType=1))
    balances = iter(stocks)
    monkeypatch.setattr(task, 'read_three_emperors', lambda: state.copy())
    monkeypatch.setattr(task, 'read_item_available_counts', lambda *a, **k: ({36: next(balances)},))
    monkeypatch.setattr(task, 'wait_emperors', lambda c, scenes: result(scenes[0]))
    monkeypatch.setattr(task, 'choose_left', lambda c: result(dict(cost=10, isEnough=True)))
    return Context()


def test_choice_uses_its_own_enough_flag(monkeypatch):
    context = setup(monkeypatch, choice=True)
    monkeypatch.setattr(task, 'await_level', lambda *a: result(dict(isMax=True, curLevel=11)))
    assert finish(task.comprehend_three_emperors(context))['upgrades'] == 1
    assert context.clicks[:2] == [(856, '领悟'), (857, '激活')]


@pytest.mark.parametrize('fresh, allowed', [(100, True), (90, False)])
def test_retry_requires_unchanged_stock(monkeypatch, fresh, allowed):
    context = setup(monkeypatch, stocks=(100, fresh))
    attempts = []

    def await_level(*args):
        attempts.append(True)
        if len(attempts) == 1:
            raise task.ComprehensionNotConfirmed('unconfirmed')
        return result(dict(isMax=True, curLevel=11))

    monkeypatch.setattr(task, 'await_level', await_level)
    if allowed:
        assert finish(task.comprehend_three_emperors(context))['upgrades'] == 1
        assert len(attempts) == 2
    else:
        with pytest.raises(task.ComprehensionNotConfirmed):
            finish(task.comprehend_three_emperors(context))
        assert len(attempts) == 1
