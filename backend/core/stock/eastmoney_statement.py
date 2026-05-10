from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .eastmoney_trade import EastmoneyTradeError


@dataclass(frozen=True)
class EastmoneyStatementPosition:
    market: str
    security_code: str
    security_name: str
    quantity: str
    market_price: str
    cost_price: str
    market_value: str


@dataclass(frozen=True)
class EastmoneyStatementFundFlow:
    flow_date: str
    flow_category: str
    security_code: str
    security_name: str
    quantity: str
    price: str
    occurrence_amount: str
    fee: str
    stamp_tax: str
    transfer_fee: str
    fund_balance: str
    raw_text: str


@dataclass(frozen=True)
class EastmoneyStatement:
    file_path: str
    file_name: str
    file_size: int
    file_mtime: float
    file_sha256: str
    raw_text: str
    lines: list[str]
    print_time: str = ""
    printed_at: float | None = None
    query_start_date: str = ""
    query_end_date: str = ""
    customer_name: str = ""
    customer_no: str = ""
    fund_account: str = ""
    sh_account: str = ""
    sz_account: str = ""
    asset_summary: dict[str, str] = field(default_factory=dict)
    positions: list[EastmoneyStatementPosition] = field(default_factory=list)
    fund_flows: list[EastmoneyStatementFundFlow] = field(default_factory=list)


def read_eastmoney_statement_pdf(path: str | Path) -> EastmoneyStatement:
    pdf_path = Path(path)
    if not pdf_path.exists():
        raise EastmoneyTradeError(f"对账单文件不存在：{pdf_path}")
    if not pdf_path.is_file():
        raise EastmoneyTradeError(f"对账单路径不是文件：{pdf_path}")

    try:
        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted:
            raise EastmoneyTradeError("电子对账单 PDF 已加密，暂不支持导入")
        page_texts = [page.extract_text() or "" for page in reader.pages]
    except EastmoneyTradeError:
        raise
    except Exception as exc:
        raise EastmoneyTradeError(f"读取电子对账单 PDF 失败：{exc}") from exc

    raw_text = "\n".join(page_texts)
    lines = [_normalize_line(line) for line in raw_text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        raise EastmoneyTradeError("电子对账单没有可解析文本")

    stat = pdf_path.stat()
    statement = _parse_statement_lines(
        lines,
        file_path=str(pdf_path),
        file_name=pdf_path.name,
        file_size=stat.st_size,
        file_mtime=stat.st_mtime,
        file_sha256=_file_sha256(pdf_path),
        raw_text=raw_text,
    )
    if not statement.query_start_date or not statement.query_end_date:
        raise EastmoneyTradeError("未能识别电子对账单查询区间")
    return statement


def statement_to_raw_json(statement: EastmoneyStatement) -> dict[str, Any]:
    return {
        "print_time": statement.print_time,
        "query_start_date": statement.query_start_date,
        "query_end_date": statement.query_end_date,
        "customer_name": statement.customer_name,
        "customer_no": statement.customer_no,
        "fund_account": statement.fund_account,
        "sh_account": statement.sh_account,
        "sz_account": statement.sz_account,
        "asset_summary": statement.asset_summary,
        "positions": [position.__dict__ for position in statement.positions],
        "fund_flows": [flow.__dict__ for flow in statement.fund_flows],
    }


def fund_flow_to_trade_row(flow: EastmoneyStatementFundFlow) -> dict[str, str] | None:
    if flow.flow_category not in {"证券买入", "证券卖出"}:
        return None
    amount_value = _parse_number(flow.occurrence_amount)
    fee_value = _parse_number(flow.fee) or 0.0
    if amount_value is None:
        return None
    if flow.flow_category == "证券买入":
        trade_amount = abs(amount_value) - fee_value
    else:
        trade_amount = abs(amount_value) + fee_value
    return {
        "发生日期": flow.flow_date,
        "买卖类别": flow.flow_category,
        "代码": flow.security_code,
        "名称": flow.security_name,
        "成交数量": flow.quantity,
        "成交价格": flow.price,
        "发生金额": _format_number(abs(amount_value)),
        "成交金额": _format_number(max(trade_amount, 0.0)),
        "手续费": flow.fee,
        "印花税": flow.stamp_tax,
        "过户费": flow.transfer_fee,
        "资金余额": flow.fund_balance,
    }


def _parse_statement_lines(
    lines: list[str],
    *,
    file_path: str,
    file_name: str,
    file_size: int,
    file_mtime: float,
    file_sha256: str,
    raw_text: str,
) -> EastmoneyStatement:
    print_time = ""
    printed_at: float | None = None
    query_start_date = ""
    query_end_date = ""
    customer_name = ""
    customer_no = ""
    fund_account = ""
    sh_account = ""
    sz_account = ""
    asset_summary: dict[str, str] = {}
    positions: list[EastmoneyStatementPosition] = []
    fund_flows: list[EastmoneyStatementFundFlow] = []

    mode = ""
    for line in lines:
        if match := re.search(r"打印日期：\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line):
            print_time = match.group(1)
            printed_at = _parse_datetime_timestamp(print_time)
            continue
        if match := re.search(r"查询区间：\s*(\d{4})/(\d{1,2})/(\d{1,2})-(\d{4})/(\d{1,2})/(\d{1,2})", line):
            query_start_date = _format_date(match.group(1), match.group(2), match.group(3))
            query_end_date = _format_date(match.group(4), match.group(5), match.group(6))
            continue
        if match := re.search(r"客户编号：\s*(\S+)\s+客户姓名：\s*(\S+)", line):
            customer_no = match.group(1)
            customer_name = match.group(2)
            continue
        if match := re.search(r"资金账号：\s*(\S+)\s+沪市股东账号：\s*(\S+)\s+深市股东账号：\s*(\S+)", line):
            fund_account = match.group(1)
            sh_account = match.group(2)
            sz_account = match.group(3)
            continue

        for key in ("证券市值", "总资产", "资金余额", "资金可用", "前20个交易日日均资产"):
            if match := re.search(rf"{key}\(RMB\)：\s*([-\d.]+)", line):
                asset_summary[key] = match.group(1)

        if line == "汇总股票资料":
            mode = "positions"
            continue
        if line.startswith("资金流水明细"):
            mode = "fund_flows"
            continue
        if line.startswith("第") and "页" in line:
            continue
        if line.startswith("交易市场 ") or line.startswith("发生日期 "):
            continue

        if mode == "positions":
            position = _parse_position_line(line)
            if position is not None:
                positions.append(position)
            continue
        if mode == "fund_flows":
            flow = _parse_fund_flow_line(line)
            if flow is not None:
                fund_flows.append(flow)

    return EastmoneyStatement(
        file_path=file_path,
        file_name=file_name,
        file_size=file_size,
        file_mtime=file_mtime,
        file_sha256=file_sha256,
        raw_text=raw_text,
        lines=lines,
        print_time=print_time,
        printed_at=printed_at,
        query_start_date=query_start_date,
        query_end_date=query_end_date,
        customer_name=customer_name,
        customer_no=customer_no,
        fund_account=fund_account,
        sh_account=sh_account,
        sz_account=sz_account,
        asset_summary=asset_summary,
        positions=positions,
        fund_flows=fund_flows,
    )


def _parse_position_line(line: str) -> EastmoneyStatementPosition | None:
    parts = line.split(" ")
    if len(parts) < 7 or not re.fullmatch(r"\d{5,6}", parts[1]):
        return None
    return EastmoneyStatementPosition(
        market=parts[0],
        security_code=parts[1],
        security_name=parts[2],
        quantity=parts[3],
        market_price=parts[4],
        cost_price=parts[5],
        market_value=parts[6],
    )


def _parse_fund_flow_line(line: str) -> EastmoneyStatementFundFlow | None:
    parts = line.split(" ")
    if len(parts) < 8 or not re.fullmatch(r"\d{8}", parts[0]):
        return None

    flow_date = _normalize_yyyymmdd(parts[0])
    flow_category = parts[1]
    rest = parts[2:]
    if rest and re.fullmatch(r"\d{5,6}", rest[0]) and len(rest) >= 9:
        security_code = rest[0]
        security_name = rest[1]
        values = rest[2:]
    else:
        security_code = ""
        security_name = ""
        values = rest
    if len(values) < 7:
        return None

    return EastmoneyStatementFundFlow(
        flow_date=flow_date,
        flow_category=flow_category,
        security_code=security_code,
        security_name=security_name,
        quantity=values[0],
        price=values[1],
        occurrence_amount=values[2],
        fee=values[3],
        stamp_tax=values[4],
        transfer_fee=values[5],
        fund_balance=values[6],
        raw_text=line,
    )


def _normalize_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_yyyymmdd(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def _format_date(year: str, month: str, day: str) -> str:
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _parse_datetime_timestamp(value: str) -> float | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None


def _parse_number(value: str) -> float | None:
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.replace(",", "").replace("%", "").replace("，", "")
    text = re.sub(r"[^\d.+-]", "", text)
    if text in {"", "+", "-", ".", "+.", "-."}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def _format_number(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
