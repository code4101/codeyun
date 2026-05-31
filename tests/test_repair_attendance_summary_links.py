from pathlib import Path

from scripts.repair_attendance_summary_links import _build_expected_links, _normalize_stem


def test_repair_attendance_summary_links_matches_chinese_month_titles(tmp_path: Path):
    courses_dir = tmp_path / "courses"
    courses_dir.mkdir()
    script = courses_dir / "d250509梵呗初阶.py"
    script.write_text(
        "\n".join([
            "from xlsln.kq5034.courses.kqcourse import *",
            "class 考勤课程(KqCourse):",
            "    def __init__(self):",
            "        super().__init__(1, XlPath(__file__).stem, 'tokenFanbei', 'v2', 7)",
        ]),
        encoding="utf-8",
    )

    expected = _build_expected_links(courses_dir)

    assert _normalize_stem("2025年05月梵呗初阶") == "d2505梵呗初阶"
    assert expected[_normalize_stem("2025年05月梵呗初阶")]["url"] == "https://www.kdocs.cn/l/tokenFanbei"
