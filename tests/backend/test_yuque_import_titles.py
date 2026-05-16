from __future__ import annotations

from scripts.import_yuque_journal import normalized_plan_title, source_title_for_item
from scripts.import_yuque_remaining_docs import normalized_source_title


def test_yuque_journal_import_uses_source_title_instead_of_ai_title() -> None:
    assert normalized_plan_title("child_doc", "AI 改写标题", "原始语雀标题") == "原始语雀标题"


def test_yuque_journal_day_entry_source_title_keeps_raw_text() -> None:
    item = {
        "kind": "day_entry",
        "raw_title": "1、08:46 昨晚看了下别人家的艾尔登法环",
    }

    assert source_title_for_item(item) == "1、08:46 昨晚看了下别人家的艾尔登法环"


def test_yuque_remaining_import_uses_source_title_instead_of_ai_title() -> None:
    assert normalized_source_title({"title": "2020年10月"}, "2020年10月团队周报") == "2020年10月"
