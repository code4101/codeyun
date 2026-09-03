from __future__ import annotations

import json

from backend.core.fanxiu.activity.rank_reward import load_activity_rank_reward_tiers


def test_reward_tiers_resolve_runtime_reward_group_to_revision_activity(tmp_path) -> None:
    activity_dir = tmp_path / "parsed_configs" / "Activity"
    reward_dir = tmp_path / "parsed_configs" / "ActivityListReward"
    activity_dir.mkdir(parents=True)
    reward_dir.mkdir(parents=True)
    (activity_dir / "rows.json").write_text(
        json.dumps([{"id": 80452, "rewardGroup": 80451}]),
        encoding="utf-8",
    )
    (reward_dir / "rows.json").write_text(
        json.dumps([
            {
                "id": 0,
                "group": 80451,
                "rankingRange": ["1", "8"],
                "reward": ["Item|9070095_10"],
                "condition": "ActivityIdOpenBefore|80452_20250411",
                "serverDay": [1, 9999],
            },
            {
                "id": 1,
                "group": 80451,
                "rankingRange": ["1", "8"],
                "reward": ["Item|9070095_20"],
                "condition": "ActivityIdOpenAfter|80452_20250411",
                "serverDay": [1, 9999],
            }
        ]),
        encoding="utf-8",
    )

    tiers = load_activity_rank_reward_tiers(
        reward_activity_id=80451,
        event_date="2026-08-31",
        export_root=tmp_path,
        server_day=100,
    )

    assert [(tier["rank_start"], tier["rank_end"]) for tier in tiers] == [(1, 8)]
