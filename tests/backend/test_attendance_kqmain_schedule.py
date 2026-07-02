import sys
from pathlib import Path

sys.path.append(str(Path(r"C:\home\chenkunze\slns\xlproject\src")))

from xlsln.kq5034.kqmain import 考勤行为树


def test_daily_morning_course_task_precedes_store_user_exports():
    root = 考勤行为树().build_tree()
    selector = root.children[0]

    action_names = []
    for child in selector.children:
        inner = getattr(getattr(child, "child", None), "child", None)
        fn = getattr(inner, "func", None) or getattr(inner, "fn", None)
        action_names.append(getattr(fn, "__name__", type(child).__name__))

    assert action_names.index("每日早晨课程任务") < action_names.index("更新店铺2用户数据")
    assert action_names.index("每日早晨课程任务") < action_names.index("更新店铺1用户数据")
