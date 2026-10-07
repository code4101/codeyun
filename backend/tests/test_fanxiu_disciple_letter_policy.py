import pytest
from backend.core.fanxiu.data_annotation.tasks.storage_bag_disciple_letter import disciple_letter_group_finished
from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import StorageBagRandomBoxBlocked


def test_shared_capacity_stops_with_overflow_and_continues_after_exhaustion():
    assert disciple_letter_group_finished(before=512, used=10, remaining=502)
    assert not disciple_letter_group_finished(before=5, used=5, remaining=0)


@pytest.mark.parametrize('before,used,remaining', [(512,10,512), (5,10,0), (5,0,5), (5,5,-1)])
def test_unconfirmed_or_inconsistent_consumption_cannot_complete_group(before,used,remaining):
    with pytest.raises(StorageBagRandomBoxBlocked):
        disciple_letter_group_finished(before=before,used=used,remaining=remaining)
