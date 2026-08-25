from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/standard/fanxiu/wiki/page.vue"
HELPERS = ROOT / "frontend/src/standard/fanxiu/wiki/playerProfile.ts"


def test_player_profile_table_only_shows_the_two_power_metrics():
    source = PAGE.read_text(encoding="utf-8")

    assert '<th class="numeric-head">仙侣战力</th>' in source
    assert '>战力{{' in source
    assert '<th>仙侣更新</th>' not in source
    assert '<th class="numeric-head">攻击</th>' not in source
    assert '>更新时间{{' not in source


def test_player_profile_metrics_gray_independently_when_not_observed_today():
    page_source = PAGE.read_text(encoding="utf-8")
    helper_source = HELPERS.read_text(encoding="utf-8")

    assert "isPlayerProfileMetricFreshToday(row, 'observed_at')" in page_source
    assert "isPlayerProfileMetricFreshToday(row, 'xianlv_team_observed_at')" in page_source
    assert ".player-profile-table td.numeric-cell.is-stale" in page_source
    assert "timeZone: 'Asia/Shanghai'" in helper_source
