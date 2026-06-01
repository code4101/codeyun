"""为第41届念住创建 codeyun workbook + 考勤表 + 报名表。

d260601第41届念住，2026-06-01 开课，21 课 + 21 打卡 + 觉观念住返款规则。
列结构和样式对齐第40届念住（sheet 21 的 3 行表头 + 色带分组）。
"""
from __future__ import annotations

import time
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlmodel import Session

from backend.core.note_sheet_access import ensure_attendance_sheet_anonymous_viewer
from backend.core.sheet_identity import allocate_new_sheet_identity, allocate_new_workbook_identity
from backend.db import engine
from backend.models import SheetDocument, WorkbookDocument, WorkbookSheetLink


# ── 参数 ──────────────────────────────────────────
COURSE_NAME = "d260601第41届念住"
OWNER_KEY = "20260601-nianzhu-41"

# 21 课的时间段（对齐第40届）
LESSON_SCHEDULE: list[tuple[str, str, int]] = [
    ("05:20", "06:43", 1),
    ("05:20", "06:52", 2),
    ("05:20", "06:42", 3),
    ("05:20", "06:49", 4),
    ("05:20", "06:17", 5),
    ("05:20", "06:30", 6),
    ("05:20", "06:24", 7),
    ("05:20", "06:26", 8),
    ("05:20", "06:32", 9),
    ("05:20", "06:05", 10),
    ("05:20", "06:23", 11),
    ("05:20", "06:32", 12),
    ("05:20", "06:27", 13),
    ("05:20", "06:31", 14),
    ("05:20", "06:36", 15),
    ("05:20", "07:04", 16),
    ("05:20", "06:12", 17),
    ("05:20", "07:01", 18),
    ("05:20", "06:18", 19),
    ("05:20", "06:25", 20),
    ("05:20", "06:19", 21),
]


def _lesson_col(start: str, end: str, num: int) -> str:
    return f"{start}~{end} 第{num:02d}课"


def _build_attendance_sheet() -> dict:
    """构建第41届念住考勤表 document_json，3行表头 + 色带。"""

    # ── 列定义 ──
    columns: list[str] = [
        # 基础字段 (0-6, 7列) → 蓝色系
        "分组标记",
        "学号",
        "姓名",
        "昵称",
        "手机号",
        "关联学员账号",
        "微信支付订单号",
        # 返款字段 (7-12, 6列) → 橙色系
        "视频应返款",
        "打卡应返款",
        "总应返款",
        "已返款",
        "返款配置",
        "当前应返款",
        # 备注 (13, 1列) → 黄色
        "备注",
    ]

    # 课次列 (14-34, 21列) → 绿色系
    lesson_start_idx = len(columns)
    for start, end, num in LESSON_SCHEDULE:
        columns.append(_lesson_col(start, end, num))
    lesson_end_idx = len(columns)

    # 打卡数 (35, 1列) → 黄色
    columns.append("打卡数")

    # 追踪字段 (36-41, 6列) → 灰色
    tracking_start_idx = len(columns)
    columns.extend([
        "追踪分组",
        "追踪状态",
        "冻结时间",
        "关联用户ID",
        "规则版本",
        "来源sheet",
    ])
    column_count = len(columns)

    # ── 3行表头 ──
    # Row 0: 色带分组
    row_0 = [""] * column_count
    # Row 1: 列名（实际表头）
    row_1 = list(columns)
    # Row 2: 操作信息行（预留）
    row_2 = [""] * column_count

    # ── cell_meta: Row 0 色带 ──
    cell_meta: dict[str, dict] = {}

    # 基础字段 (0-6): 蓝色 B4C6E7
    for c in range(0, 7):
        cell_meta[f"0:{c}"] = {"style": {"background_color": "#B4C6E7"}}
    # 返款字段 (7-12): 橙色 FFBA84
    for c in range(7, 13):
        cell_meta[f"0:{c}"] = {"style": {"background_color": "#FFBA84"}}
    # 备注 (13): 黄色 FFE699
    cell_meta["0:13"] = {"style": {"background_color": "#FFE699"}}
    # 课次 (14-34): 绿色 C5E0B4
    for c in range(lesson_start_idx, lesson_end_idx):
        cell_meta[f"0:{c}"] = {"style": {"background_color": "#C5E0B4"}}
    # 打卡数 (35): 黄色 FFE699
    cell_meta["0:35"] = {"style": {"background_color": "#FFE699"}}
    # 追踪字段 (36-41): 灰色 E5E7EB
    for c in range(tracking_start_idx, column_count):
        cell_meta[f"0:{c}"] = {"style": {"background_color": "#E5E7EB"}}

    # ── cell_meta: Row 1 列背景 ──
    for c in range(0, 7):
        cell_meta[f"1:{c}"] = {"style": {"background_color": "#D9E1F2"}}
    for c in range(7, 13):
        cell_meta[f"1:{c}"] = {"style": {"background_color": "#FFDCC4"}}
    cell_meta["1:13"] = {"style": {"background_color": "#FFF2CC"}}
    for c in range(lesson_start_idx, lesson_end_idx):
        cell_meta[f"1:{c}"] = {"style": {"background_color": "#E2F0D9"}}
    cell_meta["1:35"] = {"style": {"background_color": "#FFF2CC"}}
    for c in range(tracking_start_idx, column_count):
        cell_meta[f"1:{c}"] = {"style": {"background_color": "#E5E7EB"}}

    # ── column_configs ──
    column_configs: dict[str, dict] = {}

    def _set(col: str, **kw):
        column_configs.setdefault(col, {}).update(kw)

    for col in columns[:7]:
        _set(col, header_background_color="#D9E1F2")
    for col in columns[7:13]:
        _set(col, header_background_color="#FFDCC4")
    _set(columns[13], header_background_color="#FFF2CC")
    for col in columns[lesson_start_idx:lesson_end_idx]:
        _set(col, header_background_color="#E2F0D9")
    _set(columns[35], header_background_color="#FFF2CC")
    for col in columns[tracking_start_idx:]:
        _set(col, header_background_color="#E5E7EB")

    # 数值列
    for col in columns:
        if col in ("手机号", "微信支付订单号", "视频应返款", "打卡应返款",
                    "总应返款", "已返款", "当前应返款", "打卡数", "关联学员账号"):
            _set(col, value_type="number")
        elif "课" in col:
            _set(col, value_type="number")

    # 日期列
    _set("冻结时间", value_type="date")

    # 隐藏列
    for col in ("分组标记", "关联学员账号", "规则版本", "来源sheet",
                "追踪分组", "追踪状态", "冻结时间"):
        _set(col, hidden=True)

    # 固定宽度
    for col in ("学号", "昵称", "姓名"):
        _set(col, width_mode="fixed")
    _set("关联学员账号", width_mode="fixed")

    # 字体
    for col in ("姓名", "昵称"):
        _set(col, font_size=11)
        _set(col, duplicate_value_highlight=True)

    # Notes
    _set(columns[lesson_start_idx], note=(
        "1、百分比是'总观看时间'除以'视频时长'的值。\n"
        "2、观看时长: 比如实际30分钟观看60分钟的视频，观看时长是30分钟而不是60分钟。"
        "同理，快进5分钟或跳跃式观看30分钟的片段，观看时长也是5分钟而不是30分钟。\n"
        "3、每节课的视频总观看时长达到视频的90%即为完成。可以看小鹅通标记的'已完成'状态为准。\n"
        "4、完成度可以超过100%，表示多次回放。每天凌晨在回放结束前，每天统计一次第二天凌晨更新。"
    ))
    _set("视频应返款", note="21课×20元=420元。\n进度达到90%即为完成，退还全部促学金")
    _set("打卡应返款", note="打卡达到'5/10/15/20'次，累计返款'100/150/180/200'元")
    _set("总应返款", note="微信-按视频返款不超过总返款上限，打卡不受此限。义工每月更新一次，此前统一暂结")
    _set("备注", note="只统计首次的打卡次数。")
    _set("当前应返款", note="包含尚未执行返款时间:\n2026/05/28 07:27:22,6")

    # 列宽
    column_widths: list[int] = [143]  # 分组标记
    column_widths += [88, 88, 110, 88, 90]  # 学号~关联学员账号
    column_widths += [99]  # 微信支付订单号
    column_widths += [99, 99, 88, 88, 88, 99]  # 返款字段
    column_widths += [88]  # 备注
    column_widths += [152] * 21  # 21课次
    column_widths += [88]  # 打卡数
    column_widths += [88, 88, 88, 88, 88, 88]  # 追踪

    return {
        "schema_version": 1,
        "columns": columns,
        "rows": [],
        "grid_rows": [row_0, row_1, row_2],
        "data_start_row": 3,
        "field_row_index": 1,
        "column_configs": column_configs,
        "column_widths": column_widths,
        "cell_meta": cell_meta,
        "merged_cells": [],
        "header_groups": [],
        "formula_reference_origin": "sheet_v2",
        "view_settings": {
            "show_row_numbers": True,
            "row_marker_numbering": "page",
            "row_marker_origin": "sheet",
            "show_column_markers": True,
            "column_marker_style": "letters",
            "column_note_display": "row",
            "height_mode": "fill",
            "mobile_default_view": "sheet",
            "frozen_column_count": 0,
            "pagination": {"enabled": True, "page_size": 50},
        },
    }


def _build_registration_sheet() -> dict:
    columns = [
        "分组",
        "姓名",
        "微信昵称",
        "手机号",
        "错误手机号",
        "微信支付订单号",
        "订单日期",
        "商户订单号",
        "订单金额",
        "已返款",
        "用户ID",
        "匹配得分",
        "出生年月（必填）",
        "性别",
        "地区",
        "身份证号",
        "关联用户ID",
        "备注",
    ]
    row_0 = [""] * len(columns)
    row_1 = list(columns)
    row_2 = [""] * len(columns)

    configs: dict[str, dict] = {}
    for col in ("手机号", "微信支付订单号", "错误手机号", "订单金额", "已返款", "匹配得分"):
        configs[col] = {"value_type": "number"}
    for col in ("订单日期", "出生年月（必填）"):
        configs[col] = {"value_type": "date"}
    for col in ("分组", "关联用户ID"):
        configs[col] = {"hidden": True}
    for col in ("姓名", "微信昵称", "手机号"):
        configs[col] = {"duplicate_value_highlight": True}

    return {
        "schema_version": 1,
        "columns": columns,
        "rows": [],
        "grid_rows": [row_0, row_1, row_2],
        "data_start_row": 3,
        "field_row_index": 1,
        "column_configs": configs,
        "cell_meta": {},
        "merged_cells": [],
        "header_groups": [],
        "formula_reference_origin": "sheet_v2",
        "view_settings": {
            "show_row_numbers": True,
            "row_marker_numbering": "page",
            "row_marker_origin": "sheet",
            "show_column_markers": True,
            "column_marker_style": "letters",
            "height_mode": "fill",
            "pagination": {"enabled": True, "page_size": 50},
        },
    }


def main():
    with Session(engine) as session:
        now = time.time()

        # ── Workbook ──
        wb_id = allocate_new_workbook_identity(session)
        workbook = WorkbookDocument(
            id=wb_id.primary_id,
            numeric_id=wb_id.numeric_id,
            legacy_id=wb_id.legacy_id,
            title=COURSE_NAME,
            scope="notes",
            created_at=now,
            updated_at=now,
        )
        session.add(workbook)
        session.flush()
        print(f"[1] Workbook: numeric_id={workbook.numeric_id}")

        # ── 考勤表 ──
        att_id = allocate_new_sheet_identity(session)
        att_doc = _build_attendance_sheet()
        att = SheetDocument(
            id=att_id.primary_id,
            numeric_id=att_id.numeric_id,
            legacy_id=att_id.legacy_id,
            scope="notes",
            owner_type="course_workbook",
            owner_key=OWNER_KEY,
            sheet_key="attendance",
            title="考勤表",
            engine="handsontable",
            document_json=att_doc,
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(att)
        session.flush()
        ensure_attendance_sheet_anonymous_viewer(session, att)
        print(f"[2] Attendance: numeric_id={att.numeric_id}, cols={len(att_doc['columns'])}")

        # ── 报名表 ──
        reg_id = allocate_new_sheet_identity(session)
        reg_doc = _build_registration_sheet()
        reg = SheetDocument(
            id=reg_id.primary_id,
            numeric_id=reg_id.numeric_id,
            legacy_id=reg_id.legacy_id,
            scope="notes",
            owner_type="course_workbook",
            owner_key=OWNER_KEY,
            sheet_key="registration",
            title="报名表",
            engine="handsontable",
            document_json=reg_doc,
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(reg)
        session.flush()
        print(f"[3] Registration: numeric_id={reg.numeric_id}, cols={len(reg_doc['columns'])}")

        # ── Links ──
        session.add(WorkbookSheetLink(
            workbook_id=workbook.id, sheet_id=att.id, order_index=5, created_at=now))
        session.add(WorkbookSheetLink(
            workbook_id=workbook.id, sheet_id=reg.id, order_index=10, created_at=now))
        print(f"[4] Linked to workbook {workbook.numeric_id}")

        session.commit()
        print(f"Done: workbook={workbook.numeric_id}, attendance={att.numeric_id}, registration={reg.numeric_id}")
        print(f"URL: http://localhost:5173/workbook/{workbook.numeric_id}?sheet={att.numeric_id}")


if __name__ == "__main__":
    main()
