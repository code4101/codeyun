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
    assert ":activity-type=\"selectedType === 'yunmeng-trial' ? 'yunmeng-trial' : undefined\"" in source
    assert ":activity-name=\"selectedType === 'yunmeng-trial' ? '云梦试剑' : undefined\"" in source
