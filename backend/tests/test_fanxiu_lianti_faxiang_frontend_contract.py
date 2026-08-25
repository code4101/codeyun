from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_resource_ranking_family_registers_lianti_faxiang_without_a_new_route() -> None:
    family_page = (
        ROOT / "frontend/src/standard/fanxiu/resource-ranking/page.vue"
    ).read_text(encoding="utf-8")
    model = (
        ROOT / "frontend/src/standard/fanxiu/lianti-faxiang/model.ts"
    ).read_text(encoding="utf-8")

    assert "'lianti-faxiang'" in family_page
    assert "LiantiFaxiangPage" in family_page
    assert "LIANTI_FAXIANG_OFFICIAL_NAME = '炼体法相'" in model
    assert "canonicalPath" not in model


def test_lianti_page_reuses_family_activity_tasks_and_ranking_components() -> None:
    page = (
        ROOT / "frontend/src/standard/fanxiu/lianti-faxiang/page.vue"
    ).read_text(encoding="utf-8")

    assert "getFanxiuExchangeActivitySnapshot" in page
    assert "getFanxiuExchangeActivityTasks" in page
    assert "getFanxiuExchangeActivityRankings" in page
    assert "FanxiuActivityToolbar" in page
    assert "FanxiuActivityTaskMilestoneTable" in page
    assert "FanxiuActivityRankingSection" in page
    assert ':show-plane="false"' in page
    assert "getFanxiuLianti" not in page
