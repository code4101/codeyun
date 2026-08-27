from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TOP_ACTIVITY_PAGE = (
    REPO_ROOT / "frontend/src/standard/fanxiu/top-activity/page.vue"
)


def test_yunmeng_uses_generic_exchange_page_shell_without_eager_type_scan() -> None:
    source = TOP_ACTIVITY_PAGE.read_text(encoding="utf-8")

    assert "getFanxiuYunmengTrialSnapshot" not in source
    assert "YunmengTrialPage" not in source
    assert "Promise.allSettled" not in source
    assert "resolveLatestActivity" not in source
    assert "getLatestFanxiuExchangeActivitySnapshot" in source
    assert ":activity-type=\"['yunmeng-trial', 'xianyuan-duokui', 'tiandi-yiju'].includes(selectedType) ? selectedType : undefined\"" in source
    assert ":activity-name=\"['yunmeng-trial', 'xianyuan-duokui', 'tiandi-yiju'].includes(selectedType) ? selectedActivityName : undefined\"" in source
    assert "label: '天地弈局'" in source
    assert "value: 'tiandi-yiju'" in source


def test_exchange_shop_is_a_read_only_tiered_projection() -> None:
    source = (
        REPO_ROOT / "frontend/src/standard/fanxiu/xutian-palace/page.vue"
    ).read_text(encoding="utf-8")

    assert "<h3>资源策略</h3>" not in source
    assert "activity-strategy" not in source
    assert "原序" not in source
    assert "<th>等级</th>" in source
    assert "<th>ID</th>" in source
    assert ":rowspan=\"row.groupRowSpan\"" in source
    assert "priority_group_goods_ids" in source
    assert "priorityIds.indexOf(notNeededId)" in source
    assert "priorityLevel: 14" not in source
    assert "const notNeededId = '不需要领'" in source
    assert "priorityId: notNeededId" in source
    assert "<th>名称</th>" in source
    assert "限购数量" in source
    assert "el-checkbox" not in source
    assert "saveFanxiuExchangeActivityPriorities" not in source
    assert "saveFanxiuExchangeActivityShopItemLock" not in source
    assert "planFanxiuExchangeActivityShop" not in source
    assert "budget-summary" not in source
