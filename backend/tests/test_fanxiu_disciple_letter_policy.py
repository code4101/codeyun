import pytest
from backend.core.fanxiu.data_annotation.tasks.storage_bag_disciple_letter import disciple_letter_group_finished
from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import StorageBagRandomBoxBlocked


def test_inventory_overflow_cannot_prove_shared_capacity_full():
    assert not disciple_letter_group_finished(before=512, used=10, remaining=502)
    assert not disciple_letter_group_finished(before=5, used=5, remaining=0)


@pytest.mark.parametrize('before,used,remaining', [(512,10,512), (5,10,0), (5,0,5), (5,5,-1)])
def test_unconfirmed_or_inconsistent_consumption_cannot_complete_group(before,used,remaining):
    with pytest.raises(StorageBagRandomBoxBlocked):
        disciple_letter_group_finished(before=before,used=used,remaining=remaining)


def test_letter_adapter_rechecks_capacity_after_maximal_batch(monkeypatch):
    monkeypatch.setattr('backend.core.fanxiu.data_annotation.tasks.storage_bag_disciple_letter.list_pending_disciple_letter_submissions', lambda: [])
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_disciple_letter import StorageBagDiscipleLetterGuiAdapter
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import StorageBagRandomBoxRequest
    class Adapter(StorageBagDiscipleLetterGuiAdapter):
        def __init__(self):
            self.context = self.snapshot_reader = None
            self.quantities = []
        def _execute_batch(self, request):
            self.quantities.append(request.quantity)
            if len(self.quantities) == 1:
                return {'used_count': 10, 'remaining': 95, 'shared_capacity_finished': False, 'outcome': 'batch_consumed'}
            return {'used_count': 0, 'remaining': 95, 'shared_capacity_finished': True, 'outcome': 'capacity_full'}
            yield
    adapter = Adapter()
    action = adapter.execute(StorageBagRandomBoxRequest(base_id=20300003, instance_id='letter', name='异变弟子拜师函', quantity=105))
    with pytest.raises(StopIteration) as completed:
        next(action)
    result = completed.value.value
    assert adapter.quantities == [105, 95]
    assert result['used_count'] == 10
    assert result['outcome'] == 'capacity_full'
    assert len(result['batches']) == 2


def test_letter_adapter_bounds_unconfirmed_capacity_retries(monkeypatch):
    monkeypatch.setattr('backend.core.fanxiu.data_annotation.tasks.storage_bag_disciple_letter.list_pending_disciple_letter_submissions', lambda: [])
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_disciple_letter import StorageBagDiscipleLetterGuiAdapter
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import StorageBagRandomBoxRequest
    class Adapter(StorageBagDiscipleLetterGuiAdapter):
        def __init__(self):
            self.context = self.snapshot_reader = None
            self.calls = 0
        def _execute_batch(self, request):
            self.calls += 1
            return {'used_count': 1, 'remaining': request.quantity - 1, 'shared_capacity_finished': False, 'outcome': 'batch_consumed'}
            yield
    adapter = Adapter()
    action = adapter.execute(StorageBagRandomBoxRequest(base_id=20300003, instance_id='letter', name='异变弟子拜师函', quantity=105))
    with pytest.raises(StorageBagRandomBoxBlocked, match='32批'):
        next(action)
    assert adapter.calls == 32


@pytest.mark.parametrize('scenes,expected', [([942, 935, 936, 525], [942, 935, 936]),
                                            ([935, 936, 525], [935, 936]),
                                            ([936, 34], [936]),
                                            ([34], [])])
def test_existing_travel_recovery_closes_only_submitted_dialogue_and_results(scenes, expected):
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_disciple_letter import finish_existing_disciple_travel
    class Context:
        def __init__(self):
            self.scenes = iter(scenes)
            self.clicks = []
            self.navigations = []
        def wait_scene(self, candidates, **kwargs):
            scene = next(self.scenes)
            assert scene in candidates
            return scene
            yield
        def wait_click(self, scene, title, **kwargs):
            assert title not in {'使用', '同游传道'}
            self.clicks.append(scene)
            yield
        def wait_scene_exact(self, candidates, **kwargs):
            scene = next(self.scenes)
            assert scene in candidates
            return scene
            yield
        def go_scene(self, target):
            self.navigations.append(target)
            yield
        def wait_action_settle(self, seconds):
            yield
    context = Context()
    action = finish_existing_disciple_travel(context, recovery=True)
    list(action)
    assert context.clicks == expected
    assert context.clicks.count(935) <= 1
    assert context.navigations == ([525] if scenes == [936, 34] else [])
