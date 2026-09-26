"""Pure geometry contracts; real claiming is verified against the game."""
from backend.core.fanxiu.runtime_gui.trial_manual import plan_trial_reward_click


def token(text, y):
    return {'text': text, 'x': 250, 'y': y, 'w': 60, 'h': 40}


COLUMN = {'x': 80, 'y': 660, 'w': 120, 'h': 510}


def test_claimed_rows_and_clipped_rows_are_not_candidates():
    rewards = [dict(reward_id=1, score=5400, claimable=False),
               dict(reward_id=2, score=5600, claimable=True)]
    assert plan_trial_reward_click(rewards, [token('5400', 820), token('5600', 1150)], COLUMN) is None
    plan = plan_trial_reward_click(rewards, [token('5600', 960)], COLUMN)
    assert plan == {'reward_id': 2, 'score': 5600, 'point': (140, 980)}


def test_current_score_is_not_a_reward_tier():
    rewards = [dict(reward_id=1, score=5400, claimable=True, current_marker=True),
               dict(reward_id=2, score=5600, claimable=True)]
    plan = plan_trial_reward_click(rewards, [token('当前', 680), token('5600', 726)], COLUMN)
    assert plan['reward_id'] == 1
    assert plan['point'] == (140, 720)


def test_duplicate_score_geometry_is_ambiguous():
    assert plan_trial_reward_click([dict(reward_id=1, score=5400, claimable=True)],
                                  [token('5400', 810), token('5400', 950)], COLUMN) is None
