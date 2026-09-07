"""纯候选身份及保存结果契约，不模拟游戏页面或点击。"""
from dataclasses import replace

import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_cleanse import (
    SpiritArtifactCleanseBlocked, SpiritArtifactEffect, SpiritArtifactObservation,
    SpiritArtifactPendingCandidate, SpiritArtifactTarget, verify_spirit_artifact_candidate_saved,
)


def records():
    target = SpiritArtifactTarget('24000000000000001', 1, 1, 14000106)
    locked = SpiritArtifactEffect(100, 2500, 7, True)
    current = (locked, SpiritArtifactEffect(200, 100, 6, False))
    pending = (locked, SpiritArtifactEffect(200, 200, 6, False))
    before = SpiritArtifactObservation(target, '灵器', '柄', 3, current, pending, (123, 456), 1.0, 'before')
    candidate = SpiritArtifactPendingCandidate('attempt', 1, target, pending, 'candidate', 'before')
    after = replace(before, effects=pending, pending_effects=(), fingerprint='after')
    return candidate, before, after


def test_save_requires_exact_candidate_but_not_ui_order():
    candidate, before, after = records()
    verify_spirit_artifact_candidate_saved(candidate, before, replace(after, effects=tuple(reversed(after.effects))))


@pytest.mark.parametrize('change', ['different_result', 'pending_remains', 'process_changes', 'candidate_changes'])
def test_unrelated_changes_are_not_success(change):
    candidate, before, after = records()
    if change == 'different_result':
        after = replace(after, effects=(after.effects[0], replace(after.effects[1], value=300)))
    elif change == 'pending_remains':
        after = replace(after, pending_effects=candidate.effects)
    elif change == 'process_changes':
        after = replace(after, process_identity=(123, 789))
    else:
        candidate = replace(candidate, observed_fingerprint='other')
    with pytest.raises(SpiritArtifactCleanseBlocked):
        verify_spirit_artifact_candidate_saved(candidate, before, after)


def test_even_exact_candidate_cannot_silently_drop_a_locked_effect():
    candidate, before, after = records()
    changed = (replace(candidate.effects[0], value=2499), candidate.effects[1])
    candidate = replace(candidate, effects=changed)
    before = replace(before, pending_effects=changed)
    after = replace(after, effects=changed)
    with pytest.raises(SpiritArtifactCleanseBlocked, match='锁定词条'):
        verify_spirit_artifact_candidate_saved(candidate, before, after)
