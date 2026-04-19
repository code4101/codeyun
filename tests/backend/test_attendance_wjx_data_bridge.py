import pandas as pd

from backend.core import attendance_wjx_data


def test_execute_wjx_data_sync_uses_shared_incremental_fetcher(monkeypatch):
    def fake_fetch(**kwargs):
        assert kwargs["exist_max_id"] == 11
        assert kwargs["activity_id"] == "264266843"
        assert kwargs["login_username"] == "user"
        assert kwargs["password"] == "pass"
        return {
            "df": pd.DataFrame(
                [
                    {"序号": 12, "1、所属课程": "20260415梵呗初阶", "3、姓名": "张三"},
                ]
            ),
            "exist_max_id": 11,
            "latest_max_id": 12,
            "recent_count": 1,
            "fetched_count": 1,
            "incremental_count": 1,
            "used_all_pages": False,
        }

    monkeypatch.setattr(attendance_wjx_data, "获取问卷星增量记录", fake_fetch)

    result = attendance_wjx_data.execute_wjx_data_sync(
        login_username="user",
        password="pass",
        activity_id="264266843",
        exist_max_id=11,
    )

    assert result["latest_max_id"] == 12
    assert result["incremental_count"] == 1
    assert result["rows"] == [{"序号": 12, "1、所属课程": "20260415梵呗初阶", "3、姓名": "张三"}]
