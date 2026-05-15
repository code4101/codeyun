from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest
from sqlmodel import select

from backend.core.freebill import (
    archive_freebill_raw_directory,
    clear_freebill_record_overrides,
    deduplicate_freebill_records,
    get_freebill_connection,
    get_freebill_status,
    get_freebill_dashboard,
    import_bill_records,
    import_alipay_csv_bytes,
    import_alipay_excel_bytes,
    import_ccb_excel_bytes,
    import_wechat_excel_bytes,
    list_freebill_category_branch_records,
    list_freebill_filter_options,
    list_freebill_interpret_rules,
    list_freebill_raw_files,
    list_freebill_records,
    rebuild_freebill_records_from_raw_files,
    recompute_freebill_interpretation,
    save_freebill_interpret_rules,
    upsert_freebill_category_branch_overrides,
    upsert_freebill_category_branch_manual_overrides,
    upsert_freebill_record_manual_overrides,
    upsert_freebill_record_overrides,
)
from backend.core.freebill_sheet import get_freebill_sheet_workbook, refresh_freebill_sheet_workbook
from backend.models import SheetDocument, User, WorkbookDocument, WorkbookSheetLink


def _assert_category_tree_paths_match_branch_records(
    tree: list[dict],
    *,
    work_dir: Path,
) -> None:
    for item in tree:
        path = item.get("path") or []
        if path:
            branch_records = list_freebill_category_branch_records(
                path=path,
                limit=1,
                work_dir=work_dir,
            )
            assert branch_records["total"] == item["count"], path
        _assert_category_tree_paths_match_branch_records(
            item.get("children") or [],
            work_dir=work_dir,
        )


def _build_alipay_csv_bytes() -> bytes:
    header = [
        "交易订单号",
        "商家订单号",
        "交易时间",
        "交易分类",
        "交易对方",
        "商品说明",
        "金额",
        "收/支",
        "交易状态",
        "备注",
    ]
    rows = [
        ["202601010001", "M001", "2026-01-01 08:00:00", "餐饮美食", "早餐店", "豆浆", "12.5", "支出", "交易成功", ""],
        ["202601020001", "M002", "2026-01-02 09:00:00", "工资", "公司", "工资", "1000", "收入", "交易成功", ""],
    ]
    lines = [""] * 24
    lines.append(",".join(header))
    lines.extend(",".join(row) for row in rows)
    return "\n".join(lines).encode("gbk")


def _build_wechat_excel_bytes() -> bytes:
    output = io.BytesIO()
    columns = [
        "交易时间",
        "交易类型",
        "交易对方",
        "商品",
        "收/支",
        "金额(元)",
        "当前状态",
        "交易单号",
        "商户单号",
        "备注",
    ]
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df = pd.DataFrame(
            [["2026-01-03 10:00:00", "交通出行", "地铁", "地铁票", "支出", "¥6.00", "支付成功", "WX001", "WM001", ""]],
            columns=columns,
        )
        df.to_excel(writer, index=False, startrow=16)
    return output.getvalue()


def _build_wechat_compact_excel_bytes() -> bytes:
    output = io.BytesIO()
    columns = [
        "交易时间",
        "交易类型",
        "交易对方",
        "商品",
        "收/支",
        "金额(元)",
        "当前状态",
        "交易单号",
        "商户单号",
        "备注",
    ]
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df = pd.DataFrame(
            [["2026-01-04 10:00:00", "餐饮美食", "午餐店", "盖饭", "支出", "22", "支付成功", "WXCOMPACT001", "WMC001", "/"]],
            columns=columns,
        )
        df.to_excel(writer, index=False)
    return output.getvalue()


def _build_ccb_excel_bytes(
    *,
    total_expense: str = "12.34",
    total_income: str = "1,234.56",
) -> bytes:
    output = io.BytesIO()
    rows = [
        ["中国建设银行个人活期账户全部交易明细", "", "", "", "", "", "", "", ""],
        ["卡号/账号:6227001935470443242", "客户名称:测试用户", "", "起始日期:20260101", "结束日期:20260131", "", "", "", ""],
        ["当前时间段收支金额合计：人民币元；", f"总支出：{total_expense}", f"总收入：{total_income}", "", "", "", "", "", ""],
        ["序号", "摘要", "币别", "钞汇", "交易日期", "交易金额", "账户余额", "交易地点/附言", "对方账号与户名"],
        ["1", "支付机构提现", "人民币元", "钞", "20260105", "1,234.56", "2,000.00", "工资转入", "公司/工资"],
        ["2", "消费", "人民币元", "钞", "20260106", "-12.34", "1,987.66", "食堂刷卡", "食堂/刷卡"],
    ]
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, header=False)
    return output.getvalue()


def _build_alipay_legacy_csv_bytes() -> bytes:
    lines = [
        "支付宝交易记录明细查询",
        "账号:[test]",
        "起始日期:[2026-01-01 00:00:00]    终止日期:[2026-02-01 00:00:00]",
        "---------------------------------交易记录明细列表------------------------------------",
        "交易号,商家订单号,交易创建时间,付款时间,最近修改时间,交易来源地,类型,交易对方,商品名称,金额（元）,收/支,交易状态,服务费（元）,成功退款（元）,备注,资金状态,",
        "202601010001,M-OLD,2026-01-01 08:00:00,2026-01-01 08:00:00,2026-01-01 08:00:00,支付宝网站,即时到账交易,旧早餐店,旧商品,99.00,支出,交易成功,0.00,0.00,,已支出,",
        "202601030001,M-OTHER,2026-01-03 08:00:00,2026-01-03 08:00:00,2026-01-03 08:00:00,支付宝网站,即时到账交易,余额宝,余额宝-收益发放,1.23,其他,交易成功,0.00,0.00,,已收入,",
    ]
    return "\n".join(lines).encode("gb18030")


def test_freebill_import_and_dashboard(tmp_path: Path) -> None:
    alipay_result = import_alipay_csv_bytes("alipay.csv", _build_alipay_csv_bytes(), work_dir=tmp_path)
    wechat_result = import_wechat_excel_bytes("wechat.xlsx", _build_wechat_excel_bytes(), work_dir=tmp_path)

    assert alipay_result["inserted"] == 2
    assert wechat_result["inserted"] == 1
    assert alipay_result["raw_file"]["sha256"]
    assert wechat_result["raw_file"]["sha256"]

    duplicate_alipay_result = import_alipay_csv_bytes("alipay.csv", _build_alipay_csv_bytes(), work_dir=tmp_path)
    assert duplicate_alipay_result["processed"] == 2
    assert duplicate_alipay_result["inserted"] == 0
    assert duplicate_alipay_result["skipped"] == 2

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert dashboard["summary"]["total_count"] == 3
    assert dashboard["summary"]["total_income"] == 1000
    assert dashboard["summary"]["total_expense"] == 18.5
    assert dashboard["summary"]["balance"] == 981.5
    assert dashboard["category_dimensions"] == ["standard_direction", "standard_nature", "type", "counterparty"]
    assert dashboard["expense_categories"][0]["name"] == "餐饮美食"
    assert dashboard["expense_categories"][0]["children"][0] == {
        "name": "早餐店",
        "value": 12.5,
        "count": 1,
    }
    assert dashboard["income_categories"][0]["children"][0]["name"] == "公司"
    assert [item["name"] for item in dashboard["category_tree"]] == ["支出", "收入"]
    assert dashboard["category_tree"][0]["children"][0]["name"] == "常规"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "餐饮美食"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["children"][0]["name"] == "早餐店"
    _assert_category_tree_paths_match_branch_records(dashboard["category_tree"], work_dir=tmp_path)
    branch_records = list_freebill_category_branch_records(
        path=[
            {"dimension": "standard_direction", "value": "支出"},
            {"dimension": "standard_nature", "value": "常规"},
            {"dimension": "type", "value": "餐饮美食"},
        ],
        work_dir=tmp_path,
    )
    assert branch_records["total"] == 1
    assert branch_records["items"][0]["product_name"] == "豆浆"
    assert [
        item["amount"]
        for item in list_freebill_category_branch_records(direction="支出", work_dir=tmp_path)["items"]
    ] == [12.5, 6.0]
    assert [
        item["amount"]
        for item in list_freebill_category_branch_records(
            direction="支出",
            sort_by="amount",
            sort_order="asc",
            work_dir=tmp_path,
        )["items"]
    ] == [6.0, 12.5]
    assert [
        item["amount"]
        for item in list_freebill_category_branch_records(
            direction="支出",
            sort_by="create_time",
            sort_order="desc",
            work_dir=tmp_path,
        )["items"]
    ] == [6.0, 12.5]
    paged_branch_records = list_freebill_category_branch_records(
        direction="支出",
        limit=1,
        offset=1,
        work_dir=tmp_path,
    )
    assert paged_branch_records["total"] == 2
    assert [item["amount"] for item in paged_branch_records["items"]] == [6.0]
    nature_first_dashboard = get_freebill_dashboard(
        work_dir=tmp_path,
        category_dimensions=["standard_nature", "standard_direction", "type", "counterparty"],
    )
    assert [item["name"] for item in nature_first_dashboard["category_tree"]] == ["常规"]
    assert [item["name"] for item in nature_first_dashboard["category_tree"][0]["children"]] == ["支出", "收入"]
    assert dashboard["trend_granularity"] == "month"
    assert [item["month"] for item in dashboard["monthly_trend"]] == ["2026-01"]
    assert dashboard["monthly_trend"][0]["income_count"] == 1
    assert dashboard["monthly_trend"][0]["expense_count"] == 2
    assert dashboard["monthly_trend"][0]["count"] == 3

    daily_dashboard = get_freebill_dashboard(work_dir=tmp_path, trend_granularity="day")
    assert daily_dashboard["trend_granularity"] == "day"
    assert [item["month"] for item in daily_dashboard["monthly_trend"]] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
    ]

    weekly_dashboard = get_freebill_dashboard(work_dir=tmp_path, trend_granularity="week")
    assert weekly_dashboard["trend_granularity"] == "week"
    assert [item["month"] for item in weekly_dashboard["monthly_trend"]] == ["2025-12-29"]

    yearly_dashboard = get_freebill_dashboard(work_dir=tmp_path, trend_granularity="year")
    assert yearly_dashboard["trend_granularity"] == "year"
    assert [item["month"] for item in yearly_dashboard["monthly_trend"]] == ["2026"]

    expense_program_dashboard = get_freebill_dashboard(
        work_dir=tmp_path,
        program={
            "default": False,
            "rules": [
                {"action": "include", "matcher": {"kind": "all"}},
                {
                    "action": "filter",
                    "matcher": {"kind": "field", "field": "direction", "op": "eq", "value": "支出"},
                },
            ],
        },
    )
    assert expense_program_dashboard["summary"]["total_count"] == 2
    assert expense_program_dashboard["summary"]["total_income"] == 0
    assert expense_program_dashboard["summary"]["total_expense"] == 18.5

    layered_program_dashboard = get_freebill_dashboard(
        work_dir=tmp_path,
        programs=[
            {
                "default": False,
                "rules": [
                    {"action": "include", "matcher": {"kind": "all"}},
                    {
                        "action": "filter",
                        "matcher": {"kind": "field", "field": "source", "op": "eq", "value": "支付宝"},
                    },
                ],
            },
            {
                "default": False,
                "rules": [
                    {"action": "include", "matcher": {"kind": "all"}},
                    {
                        "action": "filter",
                        "matcher": {"kind": "field", "field": "direction", "op": "eq", "value": "支出"},
                    },
                ],
            },
        ],
    )
    assert layered_program_dashboard["summary"]["total_count"] == 1
    assert layered_program_dashboard["summary"]["total_expense"] == 12.5

    date_program_dashboard = get_freebill_dashboard(
        work_dir=tmp_path,
        trend_granularity="day",
        program={
            "default": True,
            "rules": [
                {
                    "action": "filter",
                    "matcher": {
                        "kind": "field",
                        "field": "create_time",
                        "op": "between",
                        "values": ["2026-01-02", "2026-01-03"],
                    },
                },
            ],
        },
    )
    assert date_program_dashboard["summary"]["total_count"] == 2
    assert [item["month"] for item in date_program_dashboard["monthly_trend"]] == ["2026-01-02", "2026-01-03"]

    year_program_dashboard = get_freebill_dashboard(
        work_dir=tmp_path,
        program={
            "default": True,
            "rules": [
                {
                    "action": "filter",
                    "matcher": {
                        "kind": "field",
                        "field": "create_time",
                        "op": "year",
                        "value": 2026,
                    },
                },
            ],
        },
    )
    assert year_program_dashboard["summary"]["total_count"] == 3
    empty_year_program_dashboard = get_freebill_dashboard(
        work_dir=tmp_path,
        program={
            "default": True,
            "rules": [
                {
                    "action": "filter",
                    "matcher": {
                        "kind": "field",
                        "field": "create_time",
                        "op": "year",
                        "value": 2025,
                    },
                },
            ],
        },
    )
    assert empty_year_program_dashboard["summary"]["total_count"] == 0

    records = list_freebill_records(source="微信", work_dir=tmp_path)
    assert records["total"] == 1
    assert records["items"][0]["counterparty"] == "地铁"
    assert records["items"][0]["standard_nature"] == "常规"
    assert records["items"][0]["standard_direction"] == "支出"

    options = list_freebill_filter_options(work_dir=tmp_path)
    assert options["sources"] == ["微信", "支付宝"]
    assert "支出" in options["directions"]

    status = get_freebill_status(work_dir=tmp_path)
    assert status["raw_file_count"] == 2


def test_freebill_imports_ccb_excel(tmp_path: Path) -> None:
    result = import_ccb_excel_bytes("建设银行流水_2026.xlsx", _build_ccb_excel_bytes(), work_dir=tmp_path)
    assert result["format"] == "ccb-excel"
    assert result["processed"] == 2
    assert result["inserted"] == 2
    assert result["raw_file"]["source"] == "建设银行"

    records = list_freebill_records(work_dir=tmp_path, limit=10)
    assert records["total"] == 2
    income = next(item for item in records["items"] if item["direction"] == "收入")
    expense = next(item for item in records["items"] if item["direction"] == "支出")
    assert income["source"] == "建设银行"
    assert income["type"] == "支付机构提现"
    assert income["amount"] == 1234.56
    assert income["account_no"] == "6227001935470443242"
    assert income["account_balance"] == 2000
    assert income["raw_sequence"] == "1"
    assert expense["product_name"] == "食堂刷卡"
    assert expense["counterparty"] == "食堂/刷卡"

    duplicate_result = import_ccb_excel_bytes("建设银行流水_2026.xlsx", _build_ccb_excel_bytes(), work_dir=tmp_path)
    assert duplicate_result["processed"] == 2
    assert duplicate_result["inserted"] == 0
    assert duplicate_result["skipped"] == 2

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert dashboard["summary"]["total_income"] == 1234.56
    assert dashboard["summary"]["total_expense"] == 12.34
    assert dashboard["sources"][0]["source"] == "建设银行"


def test_freebill_rejects_ccb_excel_when_reported_total_mismatches(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="建行流水总支出校验失败"):
        import_ccb_excel_bytes(
            "建设银行流水_2026.xlsx",
            _build_ccb_excel_bytes(total_expense="99.99"),
            work_dir=tmp_path,
        )


def test_freebill_imports_legacy_alipay_csv_and_compact_wechat_excel(tmp_path: Path) -> None:
    alipay_result = import_alipay_csv_bytes("alipay_record_legacy.csv", _build_alipay_legacy_csv_bytes(), work_dir=tmp_path)
    wechat_result = import_wechat_excel_bytes("wechat-compact.xlsx", _build_wechat_compact_excel_bytes(), work_dir=tmp_path)

    assert alipay_result["format"] == "alipay-legacy-csv"
    assert alipay_result["inserted"] == 2
    assert wechat_result["inserted"] == 1

    records = list_freebill_records(work_dir=tmp_path, limit=10)
    legacy_other = next(item for item in records["items"] if item["trade_no"] == "202601030001")
    assert legacy_other["direction"] == "收入"
    assert legacy_other["raw_direction"] == "不计收支"
    assert legacy_other["type"] == "即时到账交易"
    assert legacy_other["standard_nature"] == "理财"
    assert legacy_other["standard_direction"] == "收入"
    compact_wechat = next(item for item in records["items"] if item["trade_no"] == "WXCOMPACT001")
    assert compact_wechat["counterparty"] == "午餐店"


def test_freebill_imports_legacy_alipay_excel(tmp_path: Path) -> None:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df = pd.DataFrame(
            [
                [
                    "202601040001",
                    "M-EXCEL",
                    "2026-01-04 09:00:00",
                    "2026-01-04 09:00:00",
                    "2026-01-04 09:00:00",
                    "支付宝网站",
                    "即时到账交易",
                    "余利宝",
                    "余利宝转出到支付宝-基金赎回",
                    "100",
                    "其他",
                    "交易成功",
                    "0",
                    "0",
                    "",
                    "已收入",
                    "支付宝",
                ],
                [
                    None,
                    None,
                    "2026-01-04 00:00:00",
                    None,
                    None,
                    None,
                    "往来款",
                    "银行",
                    None,
                    "100",
                    "往来款",
                    None,
                    None,
                    None,
                    None,
                    "1000",
                    "建行",
                ],
            ],
            columns=[
                "交易号",
                "商家订单号",
                "交易创建时间",
                "付款时间",
                "最近修改时间",
                "交易来源地",
                "类型",
                "交易对方",
                "商品名称",
                "金额（元）",
                "收/支",
                "交易状态",
                "服务费（元）",
                "成功退款（元）",
                "备注",
                "资金状态",
                "类别",
            ],
        )
        df.to_excel(writer, index=False, sheet_name="2026年")

    result = import_alipay_excel_bytes("legacy-alipay.xlsx", output.getvalue(), work_dir=tmp_path)
    assert result["inserted"] == 1
    records = list_freebill_records(work_dir=tmp_path, limit=10)
    assert records["total"] == 1
    assert records["items"][0]["direction"] == "收入"
    assert records["items"][0]["raw_direction"] == "不计收支"
    assert records["items"][0]["standard_nature"] == "理财"


def test_freebill_rebuild_from_raw_files_prefers_detail_sources(tmp_path: Path) -> None:
    import_alipay_csv_bytes("alipay_record_legacy.csv", _build_alipay_legacy_csv_bytes(), work_dir=tmp_path)
    import_alipay_csv_bytes("支付宝交易明细(20260101-20260131).csv", _build_alipay_csv_bytes(), work_dir=tmp_path)

    before = list_freebill_records(work_dir=tmp_path, limit=10)
    duplicate_before = next(item for item in before["items"] if item["trade_no"] == "202601010001")
    assert duplicate_before["amount"] == 99

    result = rebuild_freebill_records_from_raw_files(work_dir=tmp_path)
    assert result["before_records"] == 3
    assert result["after_records"] == 3
    assert result["duplicate_records"] == 1
    assert result["backup_path"]

    after = list_freebill_records(work_dir=tmp_path, limit=10)
    duplicate_after = next(item for item in after["items"] if item["trade_no"] == "202601010001")
    assert duplicate_after["amount"] == 12.5
    assert duplicate_after["type"] == "餐饮美食"
    legacy_other = next(item for item in after["items"] if item["trade_no"] == "202601030001")
    assert legacy_other["direction"] == "收入"
    assert legacy_other["raw_direction"] == "不计收支"
    assert legacy_other["type"] == "即时到账交易"
    assert legacy_other["standard_nature"] == "理财"


def test_freebill_rebuild_from_raw_files_includes_ccb_excel(tmp_path: Path) -> None:
    import_ccb_excel_bytes("建设银行流水_2026.xlsx", _build_ccb_excel_bytes(), work_dir=tmp_path)
    with get_freebill_connection(tmp_path) as conn:
        conn.execute("DELETE FROM bill_records")
        conn.commit()

    result = rebuild_freebill_records_from_raw_files(work_dir=tmp_path, backup=False)
    assert result["after_records"] == 2
    assert result["imported_files"] == 1
    records = list_freebill_records(work_dir=tmp_path, limit=10)
    assert {item["source"] for item in records["items"]} == {"建设银行"}


def test_freebill_record_overrides_survive_raw_rebuild(tmp_path: Path) -> None:
    import_alipay_csv_bytes("支付宝交易明细(20260101-20260131).csv", _build_alipay_csv_bytes(), work_dir=tmp_path)

    override_result = upsert_freebill_record_overrides(
        ["202601010001", "202601020001"],
        direction="不计收支",
        category="流水",
        note="人工确认不计收支",
        work_dir=tmp_path,
    )
    assert override_result["matched"] == 2
    assert override_result["updated"] == 2
    overridden_items = {
        item["trade_no"]: item
        for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
    }
    assert overridden_items["202601010001"]["direction"] == "支出"
    assert overridden_items["202601010001"]["type"] == "餐饮美食"
    assert overridden_items["202601010001"]["standard_nature"] == "流水"
    assert overridden_items["202601010001"]["standard_direction"] == "支出"
    assert overridden_items["202601020001"]["direction"] == "收入"
    assert overridden_items["202601020001"]["type"] == "工资"
    assert overridden_items["202601020001"]["standard_nature"] == "流水"
    assert overridden_items["202601020001"]["standard_direction"] == "收入"
    overridden_dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert [item["name"] for item in overridden_dashboard["category_tree"]] == ["支出", "收入"]
    assert overridden_dashboard["category_tree"][0]["children"][0]["name"] == "流水"
    assert overridden_dashboard["category_tree"][1]["children"][0]["name"] == "流水"
    ignored_expense_branch = list_freebill_category_branch_records(
        path=[
            {"dimension": "standard_direction", "value": "支出"},
            {"dimension": "standard_nature", "value": "流水"},
            {"dimension": "type", "value": "餐饮美食"},
        ],
        work_dir=tmp_path,
    )
    assert ignored_expense_branch["total"] == 1
    assert ignored_expense_branch["items"][0]["trade_no"] == "202601010001"
    assert ignored_expense_branch["items"][0]["has_record_override"] == 1

    rebuild_result = rebuild_freebill_records_from_raw_files(work_dir=tmp_path, backup=False)
    assert rebuild_result["applied_overrides"] == 2
    rebuilt_items = {
        item["trade_no"]: item
        for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
    }
    assert rebuilt_items["202601010001"]["direction"] == "支出"
    assert rebuilt_items["202601010001"]["type"] == "餐饮美食"
    assert rebuilt_items["202601010001"]["standard_nature"] == "流水"
    assert rebuilt_items["202601010001"]["standard_direction"] == "支出"
    assert rebuilt_items["202601020001"]["direction"] == "收入"
    assert rebuilt_items["202601020001"]["type"] == "工资"
    assert rebuilt_items["202601020001"]["standard_nature"] == "流水"
    assert rebuilt_items["202601020001"]["standard_direction"] == "收入"

    clear_result = clear_freebill_record_overrides(["202601010001", "202601020001"], work_dir=tmp_path)
    assert clear_result["cleared"] == 2
    restored_items = {
        item["trade_no"]: item
        for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
    }
    assert restored_items["202601010001"]["direction"] == "支出"
    assert restored_items["202601010001"]["type"] == "餐饮美食"
    assert restored_items["202601010001"]["standard_nature"] == "常规"
    assert restored_items["202601010001"]["standard_direction"] == "支出"
    assert restored_items["202601020001"]["direction"] == "收入"
    assert restored_items["202601020001"]["type"] == "工资"
    assert restored_items["202601020001"]["standard_nature"] == "常规"
    assert restored_items["202601020001"]["standard_direction"] == "收入"


def test_freebill_manual_overrides_patch_effective_record_fields(tmp_path: Path) -> None:
    import_alipay_csv_bytes("支付宝交易明细(20260101-20260131).csv", _build_alipay_csv_bytes(), work_dir=tmp_path)

    result = upsert_freebill_record_manual_overrides(
        "202601010001",
        {
            "standard_nature": "借贷",
            "standard_direction": "支出",
            "product_name": "人工确认借呗还款",
            "type": "还款",
        },
        note="人工确认",
        work_dir=tmp_path,
    )
    assert result["updated"] == 1

    record = {
        item["trade_no"]: item
        for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
    }["202601010001"]
    assert record["trade_no"] == "202601010001"
    assert record["standard_nature"] == "借贷"
    assert record["standard_direction"] == "支出"
    assert record["type"] == "还款"
    assert record["product_name"] == "人工确认借呗还款"
    assert record["raw_values"]["type"] == "餐饮美食"
    assert record["manual_overrides"]["standard_nature"] == "借贷"

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert dashboard["category_tree"][0]["name"] == "支出"
    assert dashboard["category_tree"][0]["children"][0]["name"] == "借贷"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "还款"

    recompute_freebill_interpretation(work_dir=tmp_path)
    recomputed = {
        item["trade_no"]: item
        for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
    }["202601010001"]
    assert recomputed["standard_nature"] == "借贷"
    assert recomputed["product_name"] == "人工确认借呗还款"

    clear_freebill_record_overrides(["202601010001"], work_dir=tmp_path)
    restored = {
        item["trade_no"]: item
        for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
    }["202601010001"]
    assert restored["standard_nature"] == "常规"
    assert restored["type"] == "餐饮美食"
    assert restored["product_name"] == "豆浆"


def test_freebill_manual_overrides_compact_redundant_fields_but_pin_interpret_fields(tmp_path: Path) -> None:
    import_alipay_csv_bytes("支付宝交易明细(20260101-20260131).csv", _build_alipay_csv_bytes(), work_dir=tmp_path)

    result = upsert_freebill_record_manual_overrides(
        "202601010001",
        {
            "standard_nature": "常规",
            "standard_direction": "支出",
            "product_name": "豆浆",
        },
        work_dir=tmp_path,
    )
    assert result["updated"] == 1
    assert result["overrides"] == {
        "standard_direction": "支出",
        "standard_nature": "常规",
    }

    record = {
        item["trade_no"]: item
        for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
    }["202601010001"]
    assert record["product_name"] == "豆浆"
    assert record["manual_overrides"] == {
        "standard_direction": "支出",
        "standard_nature": "常规",
    }


def test_freebill_category_branch_manual_overrides_patch_group(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "建设银行",
                "trade_no": "CCB-GROUP-1",
                "create_time": "2025-01-16 00:00:00",
                "type": "消费",
                "counterparty": "Z******0010/**公司",
                "product_name": "支付宝-支付宝-蚂蚁（杭州）基金销售有限公司",
                "remark": "支付宝-支付宝-蚂蚁（杭州）基金销售有限公司",
                "amount": 6000,
                "direction": "支出",
                "status": "已入账",
            },
            {
                "source": "建设银行",
                "trade_no": "CCB-GROUP-2",
                "create_time": "2025-11-24 00:00:00",
                "type": "消费",
                "counterparty": "Z******0010/**公司",
                "product_name": "支付宝-支付宝-蚂蚁（杭州）基金销售有限公司",
                "remark": "支付宝-支付宝-蚂蚁（杭州）基金销售有限公司",
                "amount": 15000,
                "direction": "支出",
                "status": "已入账",
            },
        ],
        filename="ccb-group.xlsx",
        work_dir=tmp_path,
    )

    result = upsert_freebill_category_branch_manual_overrides(
        path=[
            {"dimension": "standard_direction", "value": "支出"},
            {"dimension": "standard_nature", "value": "转账"},
            {"dimension": "type", "value": "消费"},
            {"dimension": "counterparty", "value": "Z******0010/**公司"},
        ],
        overrides={"standard_nature": "转账", "type": "支付宝理财"},
        work_dir=tmp_path,
    )
    assert result["matched"] == 2
    assert result["updated"] == 2

    records = list_freebill_records(work_dir=tmp_path, limit=10)["items"]
    assert {record["standard_nature"] for record in records} == {"转账"}
    assert {record["type"] for record in records} == {"支付宝理财"}
    assert all(record["raw_values"]["type"] == "消费" for record in records)

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert dashboard["category_tree"][0]["name"] == "支出"
    assert dashboard["category_tree"][0]["children"][0]["name"] == "转账"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "支付宝理财"

    reset_result = upsert_freebill_category_branch_manual_overrides(
        path=[
            {"dimension": "standard_direction", "value": "支出"},
            {"dimension": "standard_nature", "value": "转账"},
            {"dimension": "type", "value": "支付宝理财"},
            {"dimension": "counterparty", "value": "Z******0010/**公司"},
        ],
        overrides={"standard_nature": "常规", "type": "消费"},
        work_dir=tmp_path,
    )
    assert reset_result["matched"] == 2
    assert reset_result["updated"] == 2

    reset_records = list_freebill_records(work_dir=tmp_path, limit=10)["items"]
    assert {record["standard_nature"] for record in reset_records} == {"常规"}
    assert {record["type"] for record in reset_records} == {"消费"}
    assert all(record["manual_overrides"] == {"standard_nature": "常规"} for record in reset_records)


def test_freebill_category_branch_overrides_mark_current_branch_as_flow(tmp_path: Path) -> None:
    import_alipay_csv_bytes("支付宝交易明细(20260101-20260131).csv", _build_alipay_csv_bytes(), work_dir=tmp_path)
    import_wechat_excel_bytes("wechat.xlsx", _build_wechat_excel_bytes(), work_dir=tmp_path)

    result = upsert_freebill_category_branch_overrides(
        program={"default": False, "rules": [{"action": "include", "matcher": {"kind": "all"}}]},
        direction="支出",
        category="餐饮美食",
        override_direction="不计收支",
        override_category="流水",
        note="分支转流水",
        work_dir=tmp_path,
    )

    assert result["matched"] == 1
    records = {
        item["trade_no"]: item
        for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
    }
    assert records["202601010001"]["direction"] == "支出"
    assert records["202601010001"]["type"] == "餐饮美食"
    assert records["202601010001"]["standard_nature"] == "流水"
    assert records["202601010001"]["standard_direction"] == "支出"
    assert records["WX001"]["direction"] == "支出"
    assert records["WX001"]["type"] == "交通出行"


def test_freebill_regular_transfer_keeps_regular_standard_nature(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "微信",
                "trade_no": "WX-REGULAR-TRANSFER",
                "create_time": "2026-01-01 08:00:00",
                "type": "转账",
                "counterparty": "张三",
                "product_name": "转账备注:微信转账",
                "amount": 100,
                "direction": "支出",
                "status": "支付成功",
            },
        ],
        filename="wechat-transfer.xlsx",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["standard_direction"] == "支出"
    assert record["standard_nature"] == "常规"

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert [item["name"] for item in dashboard["category_tree"]] == ["支出"]
    assert dashboard["category_tree"][0]["children"][0]["name"] == "常规"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "转账"


def test_freebill_ccb_bank_to_brokerage_is_finance_nature(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "建设银行",
                "trade_no": "CCB-BANK-TO-BROKERAGE",
                "create_time": "2025-02-27 00:00:00",
                "type": "银转证",
                "counterparty": "31001502500050030259/东方财富证券股份有限公司（客户）",
                "product_name": "银行转证券8888086017006374转入086",
                "remark": "银行转证券8888086017006374转入086",
                "amount": 100000,
                "direction": "支出",
                "status": "已完成",
            },
        ],
        filename="ccb.xlsx",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["standard_direction"] == "支出"
    assert record["standard_nature"] == "理财"

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert dashboard["category_tree"][0]["children"][0]["name"] == "理财"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "银转证"


def test_freebill_ccb_unionpay_alipay_income_is_transfer_nature(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "建设银行",
                "trade_no": "CCB-UNIONPAY-ALIPAY",
                "create_time": "2025-01-08 00:00:00",
                "type": "银联入账",
                "counterparty": "20888025971537120156/支付宝（中国）网络技术有限公司",
                "product_name": "支付宝款项",
                "remark": "支付宝款项",
                "amount": 50000,
                "direction": "收入",
                "status": "已完成",
            },
            {
                "source": "建设银行",
                "trade_no": "CCB-UNIONPAY-OTHER",
                "create_time": "2025-05-24 00:00:00",
                "type": "银联入账",
                "counterparty": "954100072996042/提现红包",
                "product_name": "抖音支付新人好礼",
                "remark": "抖音支付新人好礼",
                "amount": 20.88,
                "direction": "收入",
                "status": "已完成",
            },
        ],
        filename="ccb.xlsx",
        work_dir=tmp_path,
    )

    records = {
        item["trade_no"]: item
        for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
    }
    assert records["CCB-UNIONPAY-ALIPAY"]["standard_direction"] == "收入"
    assert records["CCB-UNIONPAY-ALIPAY"]["standard_nature"] == "转账"
    assert records["CCB-UNIONPAY-OTHER"]["standard_direction"] == "收入"
    assert records["CCB-UNIONPAY-OTHER"]["standard_nature"] == "常规"


def test_freebill_trend_can_be_limited_to_regular_nature(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "微信",
                "trade_no": "WX-REGULAR-EXPENSE",
                "create_time": "2026-01-01 08:00:00",
                "type": "餐饮美食",
                "counterparty": "小店",
                "product_name": "午餐",
                "amount": 30,
                "direction": "支出",
                "status": "支付成功",
            },
            {
                "source": "支付宝",
                "trade_no": "ALI-LOAN-INCOME",
                "create_time": "2026-01-02 08:00:00",
                "type": "信用借还",
                "counterparty": "借呗",
                "product_name": "借款到账",
                "amount": 1000,
                "direction": "不计收支",
                "status": "交易成功",
            },
        ],
        filename="mixed.xlsx",
        work_dir=tmp_path,
    )

    dashboard = get_freebill_dashboard(
        work_dir=tmp_path,
        trend_standard_nature="常规",
    )
    assert dashboard["summary"]["total_count"] == 2
    assert dashboard["monthly_trend"][0]["count"] == 1
    assert dashboard["monthly_trend"][0]["expense"] == 30
    assert dashboard["monthly_trend"][0]["income"] == 0


def test_freebill_yuebao_salary_plan_is_transfer_income(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "支付宝",
                "trade_no": "ALI-FINANCE-SALARY",
                "create_time": "2026-01-01 08:00:00",
                "type": "即时到账交易",
                "counterparty": "天弘基金管理有限公司",
                "product_name": "余额宝-工资理财",
                "amount": 6000,
                "direction": "不计收支",
                "status": "交易成功",
            },
        ],
        filename="alipay-finance.csv",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["direction"] == "收入"
    assert record["raw_direction"] == "不计收支"
    assert record["type"] == "即时到账交易"
    assert record["standard_direction"] == "收入"
    assert record["standard_nature"] == "转账"

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert [item["name"] for item in dashboard["category_tree"]] == ["收入"]
    assert dashboard["category_tree"][0]["children"][0]["name"] == "转账"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "即时到账交易"


def test_freebill_yuebao_transfer_out_to_balance_is_flow_neutral(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "支付宝",
                "trade_no": "ALI-YUEBAO-TO-BALANCE",
                "create_time": "2026-01-01 08:00:00",
                "type": "投资理财",
                "counterparty": "余额宝",
                "product_name": "余额宝-转出到余额",
                "amount": 51000,
                "direction": "不计收支",
                "status": "交易成功",
            },
        ],
        filename="alipay-yuebao-balance.csv",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["direction"] == "收支"
    assert record["raw_direction"] == "不计收支"
    assert record["type"] == "投资理财"
    assert record["standard_direction"] == "收支"
    assert record["standard_nature"] == "流水"

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert [item["name"] for item in dashboard["category_tree"]] == ["收支"]
    assert dashboard["category_tree"][0]["children"][0]["name"] == "流水"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "投资理财"


def test_freebill_alipay_yuebao_transfer_out_to_bank_is_transfer_expense(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "支付宝",
                "trade_no": "ALI-YUEBAO-TO-BANK",
                "create_time": "2026-01-05 09:00:00",
                "type": "投资理财",
                "counterparty": "中国建设银行",
                "product_name": "余额宝-转出到银行卡",
                "amount": 50000,
                "direction": "不计收支",
                "status": "交易成功",
            },
        ],
        filename="alipay-yuebao-transfer.csv",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["direction"] == "支出"
    assert record["raw_direction"] == "不计收支"
    assert record["standard_direction"] == "支出"
    assert record["standard_nature"] == "转账"

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert [item["name"] for item in dashboard["category_tree"]] == ["支出"]
    assert dashboard["category_tree"][0]["children"][0]["name"] == "转账"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "投资理财"


def test_freebill_interpret_rules_can_override_builtin_layer(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "支付宝",
                "trade_no": "ALI-FINANCE-SALARY",
                "create_time": "2026-01-01 08:00:00",
                "type": "即时到账交易",
                "counterparty": "天弘基金管理有限公司",
                "product_name": "余额宝-工资理财",
                "amount": 6000,
                "direction": "不计收支",
                "status": "交易成功",
            },
        ],
        filename="alipay-finance.csv",
        work_dir=tmp_path,
    )
    first_record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert first_record["standard_direction"] == "收入"
    assert first_record["standard_nature"] == "转账"

    rules_payload = save_freebill_interpret_rules(
        [
            {
                "name": "工资理财归理财",
                "enabled": True,
                "matcher": {
                    "kind": "field",
                    "field": "product_name",
                    "op": "contains",
                    "value": "工资理财",
                },
                "set_nature": "理财",
            },
        ],
        work_dir=tmp_path,
    )
    assert rules_payload["rules"][0]["match_count"] == 1

    result = recompute_freebill_interpretation(work_dir=tmp_path)
    assert result["total"] == 1
    assert result["updated"] == 1

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["standard_direction"] == "收入"
    assert record["standard_nature"] == "理财"

    saved_rules = list_freebill_interpret_rules(work_dir=tmp_path)["rules"]
    assert saved_rules[0]["name"] == "工资理财归理财"
    assert saved_rules[0]["match_count"] == 1

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert dashboard["category_tree"][0]["children"][0]["name"] == "理财"


def test_freebill_interpret_settings_can_disable_builtin_rules(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "支付宝",
                "trade_no": "ALI-CREDIT-RAW-EXPENSE",
                "create_time": "2026-01-07 08:00:00",
                "type": "信用借还",
                "counterparty": "街电",
                "product_name": "街电充电宝",
                "amount": 12,
                "direction": "支出",
                "status": "交易成功",
            },
        ],
        filename="alipay-credit.csv",
        work_dir=tmp_path,
    )
    assert list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]["standard_nature"] == "借贷"

    save_freebill_interpret_rules(
        [],
        settings={"built_in_rules": {"loan-keywords": False}},
        work_dir=tmp_path,
    )
    recompute_freebill_interpretation(work_dir=tmp_path)

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["standard_direction"] == "支出"
    assert record["standard_nature"] == "常规"
    built_in_rules = list_freebill_interpret_rules(work_dir=tmp_path)["built_in_rules"]
    assert next(rule for rule in built_in_rules if rule["key"] == "loan-keywords")["enabled"] is False


def test_freebill_category_tree_can_use_signed_net_values(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "微信",
                "trade_no": "WX-EXPENSE",
                "create_time": "2026-01-01 08:00:00",
                "type": "转账",
                "counterparty": "张三",
                "product_name": "转账备注:微信转账",
                "amount": 100,
                "direction": "支出",
                "status": "支付成功",
            },
            {
                "source": "微信",
                "trade_no": "WX-INCOME",
                "create_time": "2026-01-02 08:00:00",
                "type": "转账",
                "counterparty": "张三",
                "product_name": "微信转账收款",
                "amount": 60,
                "direction": "收入",
                "status": "已入账",
            },
        ],
        filename="wechat-transfer.xlsx",
        work_dir=tmp_path,
    )

    gross_dashboard = get_freebill_dashboard(
        category_dimensions=["standard_nature", "type", "counterparty"],
        work_dir=tmp_path,
    )
    assert gross_dashboard["category_tree"][0]["name"] == "常规"
    assert gross_dashboard["category_tree"][0]["value"] == 160

    save_freebill_interpret_rules([], settings={"signed_category_values": True}, work_dir=tmp_path)
    net_dashboard = get_freebill_dashboard(
        category_dimensions=["standard_nature", "type", "counterparty"],
        work_dir=tmp_path,
    )
    assert net_dashboard["category_tree"][0]["name"] == "常规"
    assert net_dashboard["category_tree"][0]["value"] == -40
    assert net_dashboard["category_tree"][0]["children"][0]["value"] == -40


def test_freebill_yuebao_income_is_finance(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "支付宝",
                "trade_no": "ALI-YUEBAO-INCOME",
                "create_time": "2026-01-02 08:00:00",
                "type": "投资理财",
                "counterparty": "景顺长城基金管理有限公司",
                "product_name": "余额宝-2026.01.01-收益发放",
                "amount": 2.89,
                "direction": "不计收支",
                "status": "交易成功",
            },
        ],
        filename="alipay-yuebao-income.csv",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["direction"] == "收入"
    assert record["raw_direction"] == "不计收支"
    assert record["type"] == "投资理财"
    assert record["standard_direction"] == "收入"
    assert record["standard_nature"] == "理财"

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert [item["name"] for item in dashboard["category_tree"]] == ["收入"]
    assert dashboard["category_tree"][0]["children"][0]["name"] == "理财"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "投资理财"


def test_freebill_yulibao_operations_are_finance_not_transfer(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "支付宝",
                "trade_no": "ALI-YULIBAO-IN",
                "create_time": "2026-01-02 08:00:00",
                "type": "投资理财",
                "counterparty": "网商银行",
                "product_name": "支付宝转入到余利宝",
                "amount": 25000,
                "direction": "不计收支",
                "status": "交易成功",
            },
            {
                "source": "支付宝",
                "trade_no": "ALI-YULIBAO-OUT",
                "create_time": "2026-01-03 08:00:00",
                "type": "投资理财",
                "counterparty": "浙江网商银行股份有限公司",
                "product_name": "余利宝转出到支付宝-基金赎回",
                "amount": 30400,
                "direction": "不计收支",
                "status": "交易成功",
            },
        ],
        filename="alipay-yulibao.csv",
        work_dir=tmp_path,
    )

    records = {
        item["trade_no"]: item
        for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
    }
    assert records["ALI-YULIBAO-IN"]["standard_nature"] == "理财"
    assert records["ALI-YULIBAO-IN"]["standard_direction"] == "支出"
    assert records["ALI-YULIBAO-OUT"]["standard_nature"] == "理财"
    assert records["ALI-YULIBAO-OUT"]["standard_direction"] == "收入"

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert {item["name"] for item in dashboard["category_tree"]} == {"收入", "支出"}
    income = next(item for item in dashboard["category_tree"] if item["name"] == "收入")
    expense = next(item for item in dashboard["category_tree"] if item["name"] == "支出")
    assert income["children"][0]["name"] == "理财"
    assert expense["children"][0]["name"] == "理财"


def test_freebill_alipay_yuebao_import_normalizes_investment_type(tmp_path: Path) -> None:
    header = [
        "交易订单号",
        "商家订单号",
        "交易时间",
        "交易分类",
        "交易对方",
        "商品说明",
        "金额",
        "收/支",
        "交易状态",
        "备注",
    ]
    rows = [
        ["202601050001", "M005", "2026-01-05 08:00:00", "投资理财", "余额宝", "余额宝-单次转入", "200", "其他", "交易成功", ""],
    ]
    lines = [""] * 24
    lines.append(",".join(header))
    lines.extend(",".join(row) for row in rows)

    import_alipay_csv_bytes("alipay-yuebao.csv", "\n".join(lines).encode("gbk"), work_dir=tmp_path)

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["direction"] == "收入"
    assert record["raw_direction"] == "不计收支"
    assert record["type"] == "投资理财"
    assert record["standard_direction"] == "收入"
    assert record["standard_nature"] == "转账"


def test_freebill_alipay_investment_type_does_not_override_internal_transfer(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "支付宝",
                "trade_no": "ALI-INVESTMENT-INTERNAL",
                "create_time": "2026-01-05 09:00:00",
                "type": "投资理财",
                "counterparty": "建设银行",
                "product_name": "银行卡转出到支付宝",
                "amount": 200,
                "direction": "不计收支",
                "status": "交易成功",
            },
        ],
        filename="alipay-investment-transfer.csv",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["type"] == "投资理财"
    assert record["standard_direction"] == "收入"
    assert record["standard_nature"] == "转账"


def test_freebill_internal_transfer_direction_uses_record_source_perspective(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "支付宝",
                "trade_no": "ALI-TO-BANK",
                "create_time": "2026-01-05 09:00:00",
                "type": "支付机构提现",
                "counterparty": "建设银行",
                "product_name": "转出到银行卡",
                "amount": 200,
                "direction": "不计收支",
                "status": "交易成功",
            },
            {
                "source": "建设银行",
                "trade_no": "CCB-FROM-ALI",
                "create_time": "2026-01-05 09:01:00",
                "type": "银联入账",
                "counterparty": "20888025971537120156/支付宝（中国）网络技术有限公司",
                "product_name": "支付宝款项",
                "amount": 200,
                "direction": "收入",
                "status": "已完成",
            },
        ],
        filename="app-bank-transfer.xlsx",
        work_dir=tmp_path,
    )

    records = {
        item["trade_no"]: item
        for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
    }
    assert records["ALI-TO-BANK"]["standard_direction"] == "支出"
    assert records["ALI-TO-BANK"]["standard_nature"] == "转账"
    assert records["CCB-FROM-ALI"]["standard_direction"] == "收入"
    assert records["CCB-FROM-ALI"]["standard_nature"] == "转账"


def test_freebill_ccb_tenpay_wechat_transfer_is_transfer_expense(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "建设银行",
                "trade_no": "CCB-TENPAY-WECHAT",
                "create_time": "2025-10-28 00:00:00",
                "type": "充值",
                "counterparty": "Z*******0010/**转账",
                "product_name": "财付通-微信支付-微信转账",
                "remark": "财付通-微信支付-微信转账",
                "amount": 50000,
                "direction": "支出",
                "status": "已完成",
            },
        ],
        filename="ccb-tenpay.xlsx",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["standard_direction"] == "支出"
    assert record["standard_nature"] == "转账"

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert dashboard["category_tree"][0]["name"] == "支出"
    assert dashboard["category_tree"][0]["children"][0]["name"] == "转账"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "充值"


def test_freebill_ccb_fund_subscription_is_transfer_expense(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "建设银行",
                "trade_no": "CCB-FUND-SUBSCRIPTION",
                "create_time": "2025-01-24 00:00:00",
                "type": "无卡自助交易",
                "counterparty": "410584060120015/（特约）基金申购",
                "product_name": "（特约）基金申购",
                "remark": "（特约）基金申购",
                "amount": 50000,
                "direction": "支出",
                "status": "已完成",
            },
        ],
        filename="ccb-fund-subscription.xlsx",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["standard_direction"] == "支出"
    assert record["standard_nature"] == "转账"

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert dashboard["category_tree"][0]["name"] == "支出"
    assert dashboard["category_tree"][0]["children"][0]["name"] == "转账"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "无卡自助交易"


def test_freebill_ccb_alipay_finance_is_transfer_expense(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "建设银行",
                "trade_no": "CCB-ALIPAY-FINANCE",
                "create_time": "2025-02-16 00:00:00",
                "type": "消费",
                "counterparty": "4******9202/**公司",
                "product_name": "支付宝-支付宝-理财-蚂蚁（杭州）基金销售有限公司",
                "remark": "支付宝-支付宝-理财-蚂蚁（杭州）基金销售有限公司",
                "amount": 6000,
                "direction": "支出",
                "status": "已完成",
            },
        ],
        filename="ccb-alipay-finance.xlsx",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["standard_direction"] == "支出"
    assert record["standard_nature"] == "转账"

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert dashboard["category_tree"][0]["name"] == "支出"
    assert dashboard["category_tree"][0]["children"][0]["name"] == "转账"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "消费"


def test_freebill_ccb_to_alipay_is_transfer_expense(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "建设银行",
                "trade_no": "CCB-TO-ALIPAY",
                "create_time": "2025-02-27 00:00:00",
                "type": "充值",
                "counterparty": "Z*******0010/*坤泽",
                "product_name": "支付宝-支付宝-陈坤泽",
                "remark": "支付宝-支付宝-陈坤泽",
                "amount": 3000,
                "direction": "支出",
                "status": "已完成",
            },
        ],
        filename="ccb-to-alipay.xlsx",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["standard_direction"] == "支出"
    assert record["standard_nature"] == "转账"

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert dashboard["category_tree"][0]["name"] == "支出"
    assert dashboard["category_tree"][0]["children"][0]["name"] == "转账"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "充值"


def test_freebill_ccb_ant_loan_deduction_is_loan_expense(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "建设银行",
                "trade_no": "CCB-ANT-LOAN-DEDUCTION",
                "create_time": "2025-02-27 00:00:00",
                "type": "代收付",
                "counterparty": "50050107360000002979/重庆蚂蚁消费金融有限公司",
                "product_name": "蚂蚁花呗借呗扣款^2025022710255101045371003861568",
                "remark": "蚂蚁花呗借呗扣款^2025022710255101045371003861568^jiebei",
                "amount": 50000,
                "direction": "支出",
                "status": "已完成",
            },
        ],
        filename="ccb-ant-loan.xlsx",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["standard_direction"] == "支出"
    assert record["standard_nature"] == "借贷"

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert dashboard["category_tree"][0]["name"] == "支出"
    assert dashboard["category_tree"][0]["children"][0]["name"] == "借贷"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "代收付"


def test_freebill_ccb_alipay_repayment_is_loan_expense(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "建设银行",
                "trade_no": "CCB-ALIPAY-REPAYMENT",
                "create_time": "2025-04-08 00:00:00",
                "type": "消费",
                "counterparty": "Z*******0010/**账户",
                "product_name": "支付宝-支付宝-还款",
                "remark": "支付宝-支付宝-还款",
                "amount": 20150,
                "direction": "支出",
                "status": "已完成",
            },
        ],
        filename="ccb-alipay-repayment.xlsx",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["standard_direction"] == "支出"
    assert record["standard_nature"] == "借贷"

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert dashboard["category_tree"][0]["name"] == "支出"
    assert dashboard["category_tree"][0]["children"][0]["name"] == "借贷"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "消费"


def test_freebill_credit_borrowing_is_flow_loan(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "支付宝",
                "trade_no": "ALI-CREDIT-BORROW",
                "create_time": "2026-01-06 08:00:00",
                "type": "信用借还",
                "counterparty": "借呗",
                "product_name": "借款到账",
                "amount": 1000,
                "direction": "不计收支",
                "status": "交易成功",
            },
            {
                "source": "支付宝",
                "trade_no": "ALI-CREDIT-RAW-EXPENSE",
                "create_time": "2026-01-07 08:00:00",
                "type": "信用借还",
                "counterparty": "街电",
                "product_name": "街电充电宝",
                "amount": 12,
                "direction": "支出",
                "status": "交易成功",
            },
        ],
        filename="alipay-credit.csv",
        work_dir=tmp_path,
    )

    records = {
        item["trade_no"]: item
        for item in list_freebill_records(work_dir=tmp_path, limit=10)["items"]
    }
    assert records["ALI-CREDIT-BORROW"]["standard_direction"] == "收入"
    assert records["ALI-CREDIT-BORROW"]["standard_nature"] == "借贷"
    assert records["ALI-CREDIT-RAW-EXPENSE"]["standard_direction"] == "支出"
    assert records["ALI-CREDIT-RAW-EXPENSE"]["standard_nature"] == "借贷"


def test_freebill_alipay_jiebei_disbursement_to_bank_is_loan_income(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "支付宝",
                "trade_no": "ALI-JIEBEI-DISBURSEMENT",
                "create_time": "2024-01-19 10:31:00",
                "type": "即时到账交易",
                "counterparty": "借呗",
                "product_name": "借呗放款至银行卡",
                "amount": 30000,
                "direction": "不计收支",
                "status": "交易成功",
            },
        ],
        filename="alipay-jiebei-disbursement.csv",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["standard_direction"] == "收入"
    assert record["standard_nature"] == "借贷"


def test_freebill_closed_regular_food_order_is_regular_expense(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "支付宝",
                "trade_no": "ALI-CLOSED-FOOD",
                "create_time": "2024-09-07 18:03:29",
                "type": "餐饮美食",
                "counterparty": "活海鲜大排档",
                "product_name": "活海鲜大排档",
                "amount": 115,
                "direction": "不计收支",
                "status": "交易关闭",
            },
        ],
        filename="alipay-closed-food.csv",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["standard_direction"] == "支出"
    assert record["standard_nature"] == "常规"


def test_freebill_person_transfer_is_not_auto_loan_without_manual_override(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "支付宝",
                "trade_no": "ALI-PERSON-TRANSFER",
                "create_time": "2026-01-08 08:00:00",
                "type": "转账红包",
                "counterparty": "陶书敏",
                "product_name": "转账",
                "amount": 1000,
                "direction": "收入",
                "status": "交易成功",
            },
        ],
        filename="alipay-person-transfer.csv",
        work_dir=tmp_path,
    )

    record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert record["standard_direction"] == "收入"
    assert record["standard_nature"] == "常规"

    upsert_freebill_record_manual_overrides(
        "ALI-PERSON-TRANSFER",
        {"standard_direction": "收入", "standard_nature": "借贷"},
        work_dir=tmp_path,
    )

    manual_record = list_freebill_records(work_dir=tmp_path, limit=1)["items"][0]
    assert manual_record["standard_direction"] == "收入"
    assert manual_record["standard_nature"] == "借贷"
    assert manual_record["manual_overrides"] == {
        "standard_direction": "收入",
        "standard_nature": "借贷",
    }


def test_freebill_slash_direction_wallet_flows_are_displayed_as_flow_branches(tmp_path: Path) -> None:
    import_bill_records(
        [
            {
                "source": "微信",
                "trade_no": "WX-WALLET-IN",
                "create_time": "2026-01-01 08:00:00",
                "type": "零钱充值",
                "counterparty": "建设银行(3242)",
                "product_name": "/",
                "amount": 100,
                "direction": "/",
                "status": "已完成",
            },
            {
                "source": "微信",
                "trade_no": "WX-WALLET-OUT",
                "create_time": "2026-01-02 08:00:00",
                "type": "零钱提现",
                "counterparty": "建设银行(3242)",
                "product_name": "/",
                "amount": 50,
                "direction": "/",
                "status": "已完成",
            },
        ],
        filename="wechat-wallet.xlsx",
        work_dir=tmp_path,
    )

    dashboard = get_freebill_dashboard(work_dir=tmp_path)
    assert [item["name"] for item in dashboard["category_tree"]] == ["支出", "收入"]
    assert dashboard["category_tree"][0]["children"][0]["name"] == "转账"
    assert dashboard["category_tree"][0]["children"][0]["children"][0]["name"] == "零钱提现"
    assert dashboard["category_tree"][1]["children"][0]["name"] == "转账"
    assert dashboard["category_tree"][1]["children"][0]["children"][0]["name"] == "零钱充值"
    assert list_freebill_category_branch_records(
        path=[
            {"dimension": "standard_direction", "value": "收入"},
            {"dimension": "standard_nature", "value": "转账"},
            {"dimension": "type", "value": "零钱充值"},
        ],
        work_dir=tmp_path,
    )["total"] == 1
    assert list_freebill_category_branch_records(
        path=[
            {"dimension": "standard_direction", "value": "支出"},
            {"dimension": "standard_nature", "value": "转账"},
            {"dimension": "type", "value": "零钱提现"},
        ],
        work_dir=tmp_path,
    )["total"] == 1


def test_freebill_raw_directory_excludes_derived_files(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    work_dir = tmp_path / "work"
    (source_dir / "支付宝").mkdir(parents=True)
    (source_dir / "支付宝" / "账单.csv").write_bytes(b"raw bill")
    (source_dir / "bill.db").write_bytes(b"legacy database snapshot")
    (source_dir / "settings.json").write_text('{"amount_separator":"ten_thousands"}', encoding="utf-8")

    archive_result = archive_freebill_raw_directory(
        source_dir,
        work_dir=work_dir,
        include_database_snapshot=True,
    )
    assert archive_result["archived_count"] == 2
    assert archive_result["skipped_count"] == 1

    raw_files = list_freebill_raw_files(work_dir=work_dir)
    assert raw_files["total"] == 1
    assert raw_files["items"][0]["relative_path"] == "支付宝/账单.csv"
    assert raw_files["items"][0]["source"] == "支付宝"

    status = get_freebill_status(work_dir=work_dir)
    assert status["raw_file_count"] == 1


def test_freebill_deduplicates_trade_no_across_sources(tmp_path: Path) -> None:
    base_record = {
        "source": "支付宝",
        "trade_no": "DUP001",
        "merchant_order_no": "/",
        "create_time": "2026-01-01 08:00:00",
        "pay_time": None,
        "modify_time": None,
        "location": None,
        "type": "微信红包",
        "counterparty": "对方",
        "product_name": "红包",
        "amount": 8.0,
        "direction": "收入",
        "status": "已存入零钱",
        "service_fee": None,
        "refund_amount": None,
        "remark": "/",
        "fund_status": None,
        "imported_at": 1,
    }
    import_result = import_bill_records(
        [
            base_record,
            {**base_record, "source": "微信", "fund_status": "已存入零钱", "imported_at": 2},
        ],
        filename="duplicate",
        work_dir=tmp_path,
    )
    assert import_result["inserted"] == 1
    assert import_result["skipped"] == 1

    with get_freebill_connection(tmp_path) as conn:
        conn.execute(
            """
            INSERT INTO bill_records (
                source, trade_no, merchant_order_no, create_time, pay_time, modify_time,
                location, type, counterparty, product_name, amount, direction, status,
                service_fee, refund_amount, remark, fund_status, imported_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "微信",
                "LEGACYDUP",
                "/",
                "2026-01-02 09:00:00",
                None,
                None,
                None,
                "微信红包",
                "对方",
                "红包",
                9.0,
                "收入",
                "已存入零钱",
                None,
                None,
                "/",
                "已存入零钱",
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO bill_records (
                source, trade_no, merchant_order_no, create_time, pay_time, modify_time,
                location, type, counterparty, product_name, amount, direction, status,
                service_fee, refund_amount, remark, fund_status, imported_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "支付宝",
                "LEGACYDUP",
                "/",
                "2026-01-02 09:00:00",
                None,
                None,
                None,
                "微信红包",
                "对方",
                "红包",
                9.0,
                "收入",
                "已存入零钱",
                None,
                None,
                "/",
                None,
                1,
            ),
        )
        conn.commit()

    cleanup = deduplicate_freebill_records(work_dir=tmp_path)
    assert cleanup["deleted_records"] == 1
    records = list_freebill_records(work_dir=tmp_path, limit=20)
    legacy_rows = [item for item in records["items"] if item["trade_no"] == "LEGACYDUP"]
    assert len(legacy_rows) == 1
    assert legacy_rows[0]["source"] == "微信"


def test_freebill_refreshes_note_sheet_workbook(tmp_path: Path, monkeypatch, session) -> None:
    monkeypatch.setenv("CODEYUN_FREEBILL_WORK_DIR", str(tmp_path))
    import_alipay_csv_bytes("alipay.csv", _build_alipay_csv_bytes())
    import_wechat_excel_bytes("wechat.xlsx", _build_wechat_excel_bytes())
    with get_freebill_connection(tmp_path) as conn:
        for month in range(1, 14):
            conn.execute(
                """
                INSERT INTO bill_records (
                    source, trade_no, merchant_order_no, create_time, pay_time, modify_time,
                    location, type, counterparty, product_name, amount, direction, status,
                    service_fee, refund_amount, remark, fund_status, imported_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "测试",
                    f"MANUAL{month:02d}",
                    None,
                    f"{2024 + (month - 1) // 12}-{((month - 1) % 12) + 1:02d}-01 12:00:00",
                    None,
                    None,
                    None,
                    f"测试类目{month:02d}",
                    "测试对方",
                    "测试商品",
                    float(month),
                    "支出",
                    "交易成功",
                    None,
                    None,
                    None,
                    None,
                    0,
                ),
            )
        conn.commit()

    user = User(
        username="freebill-sheet-user",
        email="freebill-sheet-user@example.com",
        hashed_password="pw",
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    workbook_payload = refresh_freebill_sheet_workbook(
        session,
        user_id=int(user.id),
        actor_user_id=int(user.id),
    )

    assert workbook_payload["workbook"]["title"] == "Freebill 账单"
    assert [item["key"] for item in workbook_payload["sheets"]] == [
        "records",
        "monthly",
        "categories",
        "raw-files",
    ]
    assert workbook_payload["sheets"][0]["row_count"] == 16
    assert workbook_payload["sheets"][1]["row_count"] == 14
    assert workbook_payload["sheets"][2]["row_count"] == 16
    assert workbook_payload["sheets"][3]["row_count"] == 2
    assert get_freebill_sheet_workbook(session, user_id=int(user.id))["workbook"]["id"] == workbook_payload["workbook"]["id"]

    workbook = session.exec(select(WorkbookDocument)).one()
    links = session.exec(select(WorkbookSheetLink).order_by(WorkbookSheetLink.order_index)).all()
    sheets = session.exec(select(SheetDocument).order_by(SheetDocument.sheet_key)).all()
    assert workbook.owner_user_id == user.id
    assert len(links) == 4
    assert {sheet.owner_type for sheet in sheets} == {"freebill"}
    records_sheet = next(sheet for sheet in sheets if sheet.sheet_key == "records")
    assert records_sheet.document_json["columns"][:5] == ["交易时间", "来源", "收支", "类型", "分类"]
    assert len(records_sheet.document_json["rows"]) == 16
    assert "height_mode" not in records_sheet.document_json["view_settings"]
    assert records_sheet.document_json["view_settings"]["pagination"]["page_size"] == 50
    records_configs = records_sheet.document_json["column_configs"]
    assert records_configs["交易时间"]["value_type"] == "date"
    assert records_configs["金额"]["value_type"] == "number"
    assert records_configs["来源"]["value_mode"] == "fixed_options"
    assert records_configs["收支"]["value_mode"] == "fixed_options"
    assert records_configs["类型"]["value_mode"] == "fixed_options"
    assert records_configs["分类"]["value_mode"] == "fixed_options"
    assert records_configs["状态"]["value_mode"] == "fixed_options"
    assert records_configs["资金状态"]["value_mode"] == "fixed_options"
    assert "value_mode" not in records_configs["交易对方"]

    categories_sheet = next(sheet for sheet in sheets if sheet.sheet_key == "categories")
    categories_configs = categories_sheet.document_json["column_configs"]
    assert categories_configs["收支"]["value_mode"] == "fixed_options"
    assert categories_configs["分类"]["value_mode"] == "fixed_options"
    assert categories_configs["金额"]["value_type"] == "number"

    raw_files_sheet = next(sheet for sheet in sheets if sheet.sheet_key == "raw-files")
    raw_files_configs = raw_files_sheet.document_json["column_configs"]
    assert raw_files_configs["来源"]["value_mode"] == "fixed_options"
    assert raw_files_configs["类型"]["value_mode"] == "fixed_options"
    assert raw_files_configs["状态"]["value_mode"] == "fixed_options"

    import_bill_records(
        [
            {
                "source": "建设银行",
                "trade_no": "CCB-AFTER-WORKBOOK-REFRESH",
                "create_time": "2026-02-01 12:00:00",
                "type": "利息存入",
                "counterparty": "",
                "product_name": "利息存入",
                "amount": 1.23,
                "direction": "收入",
                "status": "已入账",
            },
        ],
        filename="ccb-after-workbook-refresh.xlsx",
        work_dir=tmp_path,
    )
    assert get_freebill_sheet_workbook(session, user_id=int(user.id)) is None
    refreshed_payload = refresh_freebill_sheet_workbook(
        session,
        user_id=int(user.id),
        actor_user_id=int(user.id),
    )
    assert refreshed_payload["sheets"][0]["row_count"] == 17
