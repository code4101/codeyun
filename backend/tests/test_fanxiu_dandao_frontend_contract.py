from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_resource_ranking_family_registers_dandao_wending() -> None:
    family_page = (
        ROOT / "frontend/src/standard/fanxiu/resource-ranking/page.vue"
    ).read_text(encoding="utf-8")
    model = (
        ROOT / "frontend/src/standard/fanxiu/dandao-wending/model.ts"
    ).read_text(encoding="utf-8")

    assert "'dandao-wending'" in family_page
    assert "DandaoWendingPage" in family_page
    assert "DANDAO_WENDING_OFFICIAL_NAME = '丹道问鼎'" in model
    assert "canonicalPath" not in model


def test_dandao_page_reuses_exchange_family_contract() -> None:
    page = (
        ROOT / "frontend/src/standard/fanxiu/dandao-wending/page.vue"
    ).read_text(encoding="utf-8")

    assert "getFanxiuExchangeActivitySnapshot" in page
    assert "getFanxiuExchangeActivityTasks" in page
    assert "getFanxiuExchangeActivityRankings" in page
    assert "collectFanxiuExchangeActivity" in page
    assert "FanxiuActivityToolbar" in page
    assert "FanxiuActivityTaskMilestoneTable" in page
    assert "FanxiuActivityRankingSection" in page
    assert ':show-plane="false"' in page
    assert "getFanxiuDandao" not in page
