"""Pure OCR row parsing regression from the 900x1600 audit capture."""
from backend.core.fanxiu.data_annotation.behavior_tree_executor import DailyFoundationTaskMixin


class Parser(DailyFoundationTaskMixin):
    def _find_shape(self, *args):
        return {}

    def _box(self, *args):
        return {"x": 80, "y": 390, "w": 780, "h": 900}

    def _frame_size(self, *args):
        return 900, 1600


def line(text, x, y, w=70, h=30):
    return {"text": text, "x": x, "y": y, "w": w, "h": h}


def test_title_digits_and_banner_do_not_become_progress_or_completion():
    parser = Parser()
    rows = parser._daily_audit_visible_rows([
        line("0完成双人修炼1次", 405, 570, 360),
        line("0/1", 475, 690),
        line("已完成", 700, 490, 110),
        line("获得600/379000榜单积分！", 200, 440, 650),
        line("◎收取两万九曜玄墨", 405, 790, 360),
        line("2/2", 475, 910),
        line("10/次", 475, 860),
    ], {})
    assert [r["title"] for r in rows] == ["完成双人修炼1次", "收取两万九曜玄墨"]
    assert rows[0]["progress"] == {"current": 0, "total": 1}
    assert rows[0]["done"] is False
    assert rows[1]["done"] is True


def test_partial_row_cannot_claim_identity_before_complete_observation():
    parser = Parser()
    assert parser._daily_audit_visible_rows([line("0/1", 475, 430)], {}) == []
    rows = parser._merge_daily_audit_rows(
        [{"title": "O完成双人修炼1次", "row_complete": False}],
        [{"title": "◎完成双人修炼1次", "row_complete": True, "done": False}],
    )
    assert len(rows) == 1
    assert rows[0]["row_complete"] is True


def test_daily_audit_maps_daily_window_titles_to_scheduler_tasks():
    parser = Parser()
    cases = {
        "参与宗门灵泉 0/1": "legacy-daily-lingquan",
        "参与宗门镇邪 0/1": "daily-zhenxie",
        "参与论道1小时 0/6": "daily-lundao-seat",
        "参与奇袭魔界 0/1": "legacy-daily-mojie-raid",
        "完成一次玄荒古域 2/3": "daily-xuanhuang",
        "完成一次魔狱封阵 0/1": "moyu-challenge",
    }
    for text, task_id in cases.items():
        identity = parser._daily_audit_task_identity(text)
        assert identity is not None, text
        assert identity["task_id"] == task_id


def test_daily_audit_leaves_weekly_titles_unmapped():
    parser = Parser()
    # 周常条目随周重置，混入日常映射会产生跨周假告警，保持未映射。
    assert parser._daily_audit_task_identity("参与击败圣祖 0/1") is None
    assert parser._daily_audit_task_identity("接受韩立指导 0/2") is None
    assert parser._daily_audit_task_identity("联盟灵脉争夺1小时 0/3") is None
