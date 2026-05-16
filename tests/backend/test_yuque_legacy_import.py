from __future__ import annotations

from bs4 import BeautifulSoup

from scripts.import_yuque_legacy_years import (
    legacy_day_candidates_from_items,
    legacy_day_candidates_from_week_item,
    normalized_title,
    normalized_week_node_content,
    table_context,
    week_row_content,
    week_row_items,
)


def test_legacy_week_row_content_preserves_source_table_values() -> None:
    soup = BeautifulSoup(
        """
        <table>
          <tr><td>ch1</td><td>周内容</td><td>周一</td><td>周二</td></tr>
          <tr>
            <td>w130114</td>
            <td><p data-lake-id="a"><span class="lake-fontsize-9">寒假</span><span>1</span></p></td>
            <td></td>
            <td><p>《胯下之辱》</p></td>
          </tr>
        </table>
        """,
        "html.parser",
    )
    table = soup.find("table")
    assert table is not None
    _section, headers = table_context(table)
    row = table.find_all("tr")[1]

    summary, body, text = week_row_content("130114", headers, row.find_all(["td", "th"]))

    assert summary == "寒假 1"
    assert text == "寒假 1 《胯下之辱》"
    assert "<table>" in body
    assert "<ol>" not in body
    assert "寒假" in body
    assert "《胯下之辱》" not in body
    assert "data-lake" not in body
    assert "lake-fontsize" not in body

    day_items = legacy_day_candidates_from_items(
        doc_id="16327325",
        week="130114",
        items=week_row_items(headers, row.find_all(["td", "th"])),
        root_doc_id="16327325",
        section_source_key="",
    )
    assert len(day_items) == 1
    assert day_items[0]["date"] == "2013-01-15"
    assert day_items[0]["title"] == "《胯下之辱》"


def test_legacy_week_title_uses_source_title_instead_of_ai_rewrite() -> None:
    assert normalized_title("week_parent", "w130114 寒假读书札记", "w130114: 寒假 1") == "w130114: 寒假 1"


def test_normalized_week_node_content_repairs_old_ordered_list_cache() -> None:
    source = (
        '<h2>w130114</h2><ol>'
        '<li><strong>周内容</strong> <p data-lake-id="a"><span class="lake-fontsize-9">寒假</span><span>1</span></p></li>'
        "<li><strong>周二</strong> <p>《胯下之辱》</p></li>"
        "</ol>"
    )

    body = normalized_week_node_content("130114", source)

    assert "<table>" in body
    assert "<th>周内容</th>" in body
    assert "寒假" in body
    assert "《胯下之辱》" not in body
    assert "data-lake" not in body


def test_legacy_day_candidates_can_be_derived_from_old_week_cache() -> None:
    week_item = {
        "kind": "week_parent",
        "root_doc_id": "90041302",
        "source_key": "24363220/90041302#w111003",
        "week": "111003",
        "content": (
            "<h2>w111003</h2><table><thead><tr>"
            "<th>周 ID</th><th>周三</th><th>周四</th><th>周五</th><th>周六</th>"
            "</tr></thead><tbody><tr><td>w111003</td>"
            "<td>各宿舍选联系人，</td><td>我只算半好人额，</td>"
            "<td>我要报厦马半程，</td><td>凡事要破其主要矛盾,先去其根,</td>"
            "</tr></tbody></table>"
        ),
    }

    days = legacy_day_candidates_from_week_item(week_item)

    assert [item["date"] for item in days] == ["2011-10-05", "2011-10-06", "2011-10-07", "2011-10-08"]
    assert days[0]["title"] == "各宿舍选联系人，"
    assert days[0]["parent_source_key"] == "24363220/90041302#w111003"
