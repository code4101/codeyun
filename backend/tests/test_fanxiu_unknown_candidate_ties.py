import pytest

from backend.core.fanxiu.data_annotation.unknown_recovery import (
    UnknownSceneCandidate, _classification_and_suggestion,
)


@pytest.mark.parametrize('score,similarity', [(100, 40), (0, 100)])
def test_tied_diagnostic_candidates_are_order_independent(score, similarity):
    candidates = [UnknownSceneCandidate(scene, str(scene), score, similarity, [], [], False)
                  for scene in [698, 308, 361]]
    result = _classification_and_suggestion(candidates, [34], frame_stability_score=None)
    assert result[0] == 'ambiguous_existing_frames'
    assert '标准对比图' in result[1]
    assert '不能' in result[1]
    assert _classification_and_suggestion(list(reversed(candidates)), [34],
                                       frame_stability_score=None) == result


def test_one_candidate_duplicated_does_not_create_identity_tie():
    candidate = UnknownSceneCandidate(698, 'settings', 100, 40, [], [], False)
    result = _classification_and_suggestion([candidate, candidate], [34],
                                          frame_stability_score=None)
    assert result[0] == 'matched_existing_frame'
