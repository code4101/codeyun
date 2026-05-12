from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func
from sqlmodel import Session, select

from backend.models import (
    EastmoneyAssetSnapshot,
    EastmoneyFundFlowRecord,
    EastmoneyPositionSnapshot,
    EastmoneyStatementImport,
    EastmoneyTradeRecord,
    EastmoneyTradeSyncRun,
)

from .eastmoney_statement import (
    EastmoneyStatement,
    EastmoneyStatementFundFlow,
    fund_flow_to_trade_row,
    read_eastmoney_statement_pdf,
    statement_to_raw_json,
)
from .eastmoney_trade import EastmoneyTable, EastmoneyTradeError, EastmoneyTradeSnapshot, read_trade_snapshot


TRADE_SOURCE_NORMAL = "normal_history_deal"
TRADE_SOURCE_HK = "hk_history_deal"
TRADE_SOURCE_MOBILE_DETAIL = "mobile_trade_detail"
TRADE_SOURCE_STATEMENT_FLOW = "pdf_statement_flow"
POSITION_SOURCE_STATEMENT = "pdf_statement_position"
FUND_FLOW_SOURCE_STATEMENT = "pdf_statement"


def sync_trade_data(
    session: Session,
    *,
    user_id: int,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    now = time.time()
    run = EastmoneyTradeSyncRun(
        user_id=user_id,
        start_date=start_date or "",
        end_date=end_date or "",
        status="running",
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    try:
        snapshot = read_trade_snapshot(start_date=start_date, end_date=end_date)
        _apply_snapshot_to_run(run, snapshot)

        if snapshot.login_required:
            run.status = "login_required"
            run.finished_at = time.time()
            run.updated_at = run.finished_at
            session.add(run)
            session.commit()
            session.refresh(run)
            return serialize_sync_run(run)

        _store_asset_snapshot(session, user_id=user_id, run=run, snapshot=snapshot)
        position_count = _store_position_snapshots(session, user_id=user_id, run=run, snapshot=snapshot)
        inserted_count, updated_count = _upsert_trade_records(session, user_id=user_id, run=run, snapshot=snapshot)

        run.status = "success"
        run.inserted_count = inserted_count
        run.updated_count = updated_count
        run.trade_record_count = inserted_count + updated_count
        run.position_count = position_count
        run.finished_at = time.time()
        run.updated_at = run.finished_at
        session.add(run)
        session.commit()
        session.refresh(run)
        return serialize_sync_run(run)
    except Exception as exc:
        session.rollback()
        run.status = "failed"
        run.error_message = str(exc)
        run.finished_at = time.time()
        run.updated_at = run.finished_at
        session.add(run)
        session.commit()
        raise


def import_mobile_trade_detail_record(
    session: Session,
    *,
    user_id: int,
    row: dict[str, str],
    ocr_lines: list[str] | None = None,
    account_label: str | None = None,
) -> dict[str, Any]:
    normalized_account_label = (account_label or _get_latest_account_label(session, user_id=user_id)).strip()
    normalized = _normalize_trade_row(row, TRADE_SOURCE_MOBILE_DETAIL, normalized_account_label)
    _validate_imported_trade_record(normalized)
    normalized["raw_json"] = _with_import_metadata(normalized["raw_json"], ocr_lines=ocr_lines)
    normalized["raw_text"] = _stable_json(normalized["raw_json"])

    now = time.time()
    run = EastmoneyTradeSyncRun(
        user_id=user_id,
        account_label=normalized_account_label,
        start_date=normalized["trade_date"],
        end_date=normalized["trade_date"],
        status="running",
        captured_at=now,
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    created = False
    record: EastmoneyTradeRecord | None = None
    try:
        existing = _find_existing_trade_record(
            session,
            user_id=user_id,
            account_label=normalized_account_label,
            normalized=normalized,
        )
        if existing is None:
            created = True
            record = EastmoneyTradeRecord(
                user_id=user_id,
                sync_run_id=run.id,
                account_label=normalized_account_label,
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
                **normalized,
            )
            session.add(record)
        else:
            record = existing
            _apply_trade_record_update(record, normalized, run.id, normalized_account_label)
            session.add(record)

        finished_at = time.time()
        run.status = "success"
        run.inserted_count = 1 if created else 0
        run.updated_count = 0 if created else 1
        run.trade_record_count = 1
        run.position_count = 0
        run.finished_at = finished_at
        run.updated_at = finished_at
        session.add(run)
        session.commit()
        session.refresh(run)
        session.refresh(record)
    except Exception:
        session.rollback()
        failed_at = time.time()
        run.status = "failed"
        run.error_message = "手机截图成交明细导入失败"
        run.finished_at = failed_at
        run.updated_at = failed_at
        session.add(run)
        session.commit()
        raise

    return {
        "created": created,
        "record": serialize_trade_record(record),
        "run": serialize_sync_run(run),
        "row": row,
        "lines": list(ocr_lines or []),
    }


def import_pdf_statement(
    session: Session,
    *,
    user_id: int,
    path: str | Path,
) -> dict[str, Any]:
    statement = read_eastmoney_statement_pdf(path)
    now = time.time()
    account_label = _statement_account_label(statement)
    captured_at = statement.printed_at or statement.file_mtime or now

    existing_import = session.exec(
        select(EastmoneyStatementImport).where(
            EastmoneyStatementImport.user_id == user_id,
            EastmoneyStatementImport.file_sha256 == statement.file_sha256,
        )
    ).first()

    if existing_import is None:
        run = EastmoneyTradeSyncRun(
            user_id=user_id,
            account_label=account_label,
            start_date=statement.query_start_date,
            end_date=statement.query_end_date,
            status="running",
            captured_at=captured_at,
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        statement_import = EastmoneyStatementImport(
            user_id=user_id,
            sync_run_id=run.id,
            created_at=now,
            **_statement_import_fields(statement, account_label=account_label, now=now),
        )
        session.add(statement_import)
        session.commit()
        session.refresh(statement_import)
    else:
        statement_import = existing_import
        run = session.get(EastmoneyTradeSyncRun, statement_import.sync_run_id) if statement_import.sync_run_id else None
        if run is None:
            run = EastmoneyTradeSyncRun(
                user_id=user_id,
                account_label=account_label,
                start_date=statement.query_start_date,
                end_date=statement.query_end_date,
                status="running",
                captured_at=captured_at,
                started_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            statement_import.sync_run_id = run.id
        _apply_statement_import_update(statement_import, statement, account_label=account_label, now=now)
        session.add(statement_import)
        session.exec(delete(EastmoneyFundFlowRecord).where(EastmoneyFundFlowRecord.statement_import_id == statement_import.id))
        session.exec(delete(EastmoneyPositionSnapshot).where(EastmoneyPositionSnapshot.sync_run_id == run.id))
        session.exec(delete(EastmoneyAssetSnapshot).where(EastmoneyAssetSnapshot.sync_run_id == run.id))
        session.commit()
        session.refresh(statement_import)

    inserted_flows, updated_flows = _upsert_statement_fund_flows(
        session,
        user_id=user_id,
        statement_import=statement_import,
        run=run,
        statement=statement,
        account_label=account_label,
    )
    inserted_trades, updated_trades = _upsert_statement_trade_records(
        session,
        user_id=user_id,
        run=run,
        statement=statement,
        account_label=account_label,
    )
    _store_statement_asset_snapshot(
        session,
        user_id=user_id,
        run=run,
        statement=statement,
        account_label=account_label,
        captured_at=captured_at,
    )
    position_count = _store_statement_positions(
        session,
        user_id=user_id,
        run=run,
        statement=statement,
        account_label=account_label,
        captured_at=captured_at,
    )

    finished_at = time.time()
    run.status = "success"
    run.inserted_count = inserted_trades
    run.updated_count = updated_trades
    run.trade_record_count = inserted_trades + updated_trades
    run.position_count = position_count
    run.finished_at = finished_at
    run.updated_at = finished_at
    run.asset_summary_json = statement.asset_summary
    session.add(run)

    statement_import.position_count = position_count
    statement_import.flow_count = len(statement.fund_flows)
    statement_import.trade_record_count = inserted_trades + updated_trades
    statement_import.updated_at = finished_at
    session.add(statement_import)
    session.commit()
    session.refresh(run)
    session.refresh(statement_import)

    return {
        "statement_import_id": statement_import.id,
        "file_name": statement.file_name,
        "query_start_date": statement.query_start_date,
        "query_end_date": statement.query_end_date,
        "flow_count": len(statement.fund_flows),
        "inserted_flow_count": inserted_flows,
        "updated_flow_count": updated_flows,
        "position_count": position_count,
        "inserted_trade_count": inserted_trades,
        "updated_trade_count": updated_trades,
        "run": serialize_sync_run(run),
    }


def list_trade_records(
    session: Session,
    *,
    user_id: int,
    start_date: str | None = None,
    end_date: str | None = None,
    source: str | None = None,
    security_code: str | None = None,
    limit: int = 300,
    offset: int = 0,
) -> dict[str, Any]:
    conditions = [EastmoneyTradeRecord.user_id == user_id]
    if start_date:
        conditions.append(EastmoneyTradeRecord.trade_date >= start_date)
    if end_date:
        conditions.append(EastmoneyTradeRecord.trade_date <= end_date)
    if source:
        conditions.append(EastmoneyTradeRecord.source == source)
    if security_code:
        conditions.append(EastmoneyTradeRecord.security_code == security_code.strip())

    total = session.exec(select(func.count()).select_from(EastmoneyTradeRecord).where(*conditions)).first() or 0
    rows = session.exec(
        select(EastmoneyTradeRecord)
        .where(*conditions)
        .order_by(
            EastmoneyTradeRecord.trade_date.desc(),
            EastmoneyTradeRecord.trade_time.desc(),
            EastmoneyTradeRecord.last_seen_at.desc(),
        )
        .offset(max(offset, 0))
        .limit(max(min(limit, 1000), 1))
    ).all()
    return {
        "total": int(total),
        "items": [serialize_trade_record(row) for row in rows],
    }


def list_fund_flow_records(
    session: Session,
    *,
    user_id: int,
    start_date: str | None = None,
    end_date: str | None = None,
    flow_category: str | None = None,
    security_code: str | None = None,
    security_name: str | None = None,
    limit: int = 300,
    offset: int = 0,
) -> dict[str, Any]:
    conditions = [EastmoneyFundFlowRecord.user_id == user_id]
    if start_date:
        conditions.append(EastmoneyFundFlowRecord.flow_date >= start_date)
    if end_date:
        conditions.append(EastmoneyFundFlowRecord.flow_date <= end_date)
    if flow_category:
        conditions.append(EastmoneyFundFlowRecord.flow_category == flow_category.strip())
    if security_code:
        conditions.append(EastmoneyFundFlowRecord.security_code == security_code.strip())
    if security_name:
        conditions.append(EastmoneyFundFlowRecord.security_name == security_name.strip())

    total = session.exec(select(func.count()).select_from(EastmoneyFundFlowRecord).where(*conditions)).first() or 0
    rows = session.exec(
        select(EastmoneyFundFlowRecord)
        .where(*conditions)
        .order_by(
            EastmoneyFundFlowRecord.flow_date.desc(),
            EastmoneyFundFlowRecord.last_seen_at.desc(),
        )
        .offset(max(offset, 0))
        .limit(max(min(limit, 1000), 1))
    ).all()
    return {
        "total": int(total),
        "items": [serialize_fund_flow_record(row) for row in rows],
    }


def list_fund_flow_categories(session: Session, *, user_id: int) -> list[str]:
    return list_fund_flow_filter_options(session, user_id=user_id)["categories"]


def list_fund_flow_filter_options(session: Session, *, user_id: int) -> dict[str, list[str]]:
    return {
        "categories": _list_distinct_fund_flow_values(
            session,
            user_id=user_id,
            column=EastmoneyFundFlowRecord.flow_category,
        ),
        "security_codes": _list_distinct_fund_flow_values(
            session,
            user_id=user_id,
            column=EastmoneyFundFlowRecord.security_code,
        ),
        "security_names": _list_distinct_fund_flow_values(
            session,
            user_id=user_id,
            column=EastmoneyFundFlowRecord.security_name,
        ),
    }


def _list_distinct_fund_flow_values(session: Session, *, user_id: int, column: Any) -> list[str]:
    rows = session.exec(
        select(column)
        .where(EastmoneyFundFlowRecord.user_id == user_id)
        .distinct()
        .order_by(column)
    ).all()
    return [str(row or "").strip() for row in rows if str(row or "").strip()]


def list_sync_runs(session: Session, *, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    rows = session.exec(
        select(EastmoneyTradeSyncRun)
        .where(EastmoneyTradeSyncRun.user_id == user_id)
        .order_by(EastmoneyTradeSyncRun.started_at.desc())
        .limit(max(min(limit, 100), 1))
    ).all()
    return [serialize_sync_run(row) for row in rows]


def get_latest_asset_snapshot(session: Session, *, user_id: int) -> dict[str, Any] | None:
    row = session.exec(
        select(EastmoneyAssetSnapshot)
        .where(EastmoneyAssetSnapshot.user_id == user_id)
        .order_by(
            EastmoneyAssetSnapshot.captured_at.desc(),
            EastmoneyAssetSnapshot.created_at.desc(),
        )
    ).first()
    return serialize_asset_snapshot(row) if row is not None else None


def list_latest_position_snapshots(session: Session, *, user_id: int) -> dict[str, Any]:
    latest_captured_at = session.exec(
        select(func.max(EastmoneyPositionSnapshot.captured_at)).where(
            EastmoneyPositionSnapshot.user_id == user_id,
        )
    ).first()
    if latest_captured_at is None:
        return {"total": 0, "items": []}

    rows = session.exec(
        select(EastmoneyPositionSnapshot)
        .where(
            EastmoneyPositionSnapshot.user_id == user_id,
            EastmoneyPositionSnapshot.captured_at == latest_captured_at,
        )
        .order_by(
            EastmoneyPositionSnapshot.market,
            EastmoneyPositionSnapshot.security_code,
        )
    ).all()
    return {
        "total": len(rows),
        "items": [serialize_position_snapshot(row) for row in rows],
    }


def _get_latest_account_label(session: Session, *, user_id: int) -> str:
    row = session.exec(
        select(EastmoneyAssetSnapshot)
        .where(EastmoneyAssetSnapshot.user_id == user_id)
        .order_by(
            EastmoneyAssetSnapshot.captured_at.desc(),
            EastmoneyAssetSnapshot.created_at.desc(),
        )
    ).first()
    return row.account_label if row is not None else ""


def serialize_sync_run(run: EastmoneyTradeSyncRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "user_id": run.user_id,
        "account_label": run.account_label,
        "start_date": run.start_date,
        "end_date": run.end_date,
        "status": run.status,
        "captured_at": run.captured_at,
        "inserted_count": run.inserted_count,
        "updated_count": run.updated_count,
        "trade_record_count": run.trade_record_count,
        "position_count": run.position_count,
        "asset_summary_json": run.asset_summary_json,
        "error_message": run.error_message,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def serialize_asset_snapshot(snapshot: EastmoneyAssetSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "sync_run_id": snapshot.sync_run_id,
        "account_label": snapshot.account_label,
        "captured_at": snapshot.captured_at,
        "total_asset": snapshot.total_asset,
        "market_value": snapshot.market_value,
        "cash_available": snapshot.cash_available,
        "cash_balance": snapshot.cash_balance,
        "withdrawable": snapshot.withdrawable,
        "frozen": snapshot.frozen,
        "pnl": snapshot.pnl,
        "raw_json": snapshot.raw_json,
        "created_at": snapshot.created_at,
    }


def serialize_position_snapshot(snapshot: EastmoneyPositionSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "sync_run_id": snapshot.sync_run_id,
        "account_label": snapshot.account_label,
        "source": snapshot.source,
        "market": snapshot.market,
        "captured_at": snapshot.captured_at,
        "security_code": snapshot.security_code,
        "security_name": snapshot.security_name,
        "quantity": snapshot.quantity,
        "available_quantity": snapshot.available_quantity,
        "cost_price": snapshot.cost_price,
        "current_price": snapshot.current_price,
        "market_value": snapshot.market_value,
        "pnl": snapshot.pnl,
        "pnl_ratio": snapshot.pnl_ratio,
        "currency": snapshot.currency,
        "raw_json": snapshot.raw_json,
        "created_at": snapshot.created_at,
    }


def serialize_trade_record(record: EastmoneyTradeRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "sync_run_id": record.sync_run_id,
        "account_label": record.account_label,
        "source": record.source,
        "source_key": record.source_key,
        "market": record.market,
        "trade_date": record.trade_date,
        "trade_time": record.trade_time,
        "security_code": record.security_code,
        "security_name": record.security_name,
        "direction": record.direction,
        "quantity": record.quantity,
        "price": record.price,
        "occurrence_date": record.occurrence_date,
        "occurrence_time": record.occurrence_time,
        "occurrence_amount": record.occurrence_amount,
        "amount": record.amount,
        "fee": record.fee,
        "commission": record.commission,
        "stamp_tax": record.stamp_tax,
        "transfer_fee": record.transfer_fee,
        "other_fee": record.other_fee,
        "currency": record.currency,
        "deal_id": record.deal_id,
        "shareholder_account": record.shareholder_account,
        "share_balance": record.share_balance,
        "fund_balance": record.fund_balance,
        "extended_name": record.extended_name,
        "raw_json": record.raw_json,
        "quantity_value": record.quantity_value,
        "price_value": record.price_value,
        "occurrence_amount_value": record.occurrence_amount_value,
        "amount_value": record.amount_value,
        "fee_value": record.fee_value,
        "commission_value": record.commission_value,
        "stamp_tax_value": record.stamp_tax_value,
        "transfer_fee_value": record.transfer_fee_value,
        "other_fee_value": record.other_fee_value,
        "share_balance_value": record.share_balance_value,
        "fund_balance_value": record.fund_balance_value,
        "first_seen_at": record.first_seen_at,
        "last_seen_at": record.last_seen_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def serialize_fund_flow_record(record: EastmoneyFundFlowRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "statement_import_id": record.statement_import_id,
        "sync_run_id": record.sync_run_id,
        "account_label": record.account_label,
        "source": record.source,
        "source_key": record.source_key,
        "flow_date": record.flow_date,
        "flow_category": record.flow_category,
        "market": record.market,
        "security_code": record.security_code,
        "security_name": record.security_name,
        "quantity": record.quantity,
        "price": record.price,
        "occurrence_amount": record.occurrence_amount,
        "fee": record.fee,
        "stamp_tax": record.stamp_tax,
        "transfer_fee": record.transfer_fee,
        "fund_balance": record.fund_balance,
        "currency": record.currency,
        "raw_json": record.raw_json,
        "quantity_value": record.quantity_value,
        "price_value": record.price_value,
        "occurrence_amount_value": record.occurrence_amount_value,
        "fee_value": record.fee_value,
        "stamp_tax_value": record.stamp_tax_value,
        "transfer_fee_value": record.transfer_fee_value,
        "fund_balance_value": record.fund_balance_value,
        "first_seen_at": record.first_seen_at,
        "last_seen_at": record.last_seen_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _statement_import_fields(statement: EastmoneyStatement, *, account_label: str, now: float) -> dict[str, Any]:
    return {
        "account_label": account_label,
        "source": FUND_FLOW_SOURCE_STATEMENT,
        "file_name": statement.file_name,
        "file_path": statement.file_path,
        "file_size": statement.file_size,
        "file_mtime": statement.file_mtime,
        "file_sha256": statement.file_sha256,
        "print_time": statement.print_time,
        "printed_at": statement.printed_at,
        "query_start_date": statement.query_start_date,
        "query_end_date": statement.query_end_date,
        "customer_name": statement.customer_name,
        "customer_no": statement.customer_no,
        "fund_account": statement.fund_account,
        "sh_account": statement.sh_account,
        "sz_account": statement.sz_account,
        "asset_summary_json": statement.asset_summary,
        "position_count": len(statement.positions),
        "flow_count": len(statement.fund_flows),
        "trade_record_count": len([flow for flow in statement.fund_flows if flow.flow_category in {"证券买入", "证券卖出"}]),
        "raw_text": statement.raw_text,
        "raw_json": statement_to_raw_json(statement),
        "imported_at": now,
        "updated_at": now,
    }


def _apply_statement_import_update(
    row: EastmoneyStatementImport,
    statement: EastmoneyStatement,
    *,
    account_label: str,
    now: float,
) -> None:
    for key, value in _statement_import_fields(statement, account_label=account_label, now=now).items():
        setattr(row, key, value)


def _statement_account_label(statement: EastmoneyStatement) -> str:
    if statement.customer_name and statement.fund_account:
        return f"{statement.customer_name}({statement.fund_account})"
    return statement.customer_name or statement.fund_account


def _upsert_statement_fund_flows(
    session: Session,
    *,
    user_id: int,
    statement_import: EastmoneyStatementImport,
    run: EastmoneyTradeSyncRun,
    statement: EastmoneyStatement,
    account_label: str,
) -> tuple[int, int]:
    inserted = 0
    updated = 0
    now = time.time()
    for flow in statement.fund_flows:
        source_key = _statement_flow_source_key(account_label, flow)
        raw_json = _statement_flow_raw_json(statement, flow)
        normalized = _statement_flow_fields(
            user_id=user_id,
            statement_import_id=statement_import.id,
            sync_run_id=run.id,
            account_label=account_label,
            source_key=source_key,
            flow=flow,
            raw_json=raw_json,
            now=now,
        )
        existing = session.exec(
            select(EastmoneyFundFlowRecord).where(
                EastmoneyFundFlowRecord.user_id == user_id,
                EastmoneyFundFlowRecord.source_key == source_key,
            )
        ).first()
        if existing is None:
            session.add(EastmoneyFundFlowRecord(**normalized))
            inserted += 1
        else:
            _apply_fund_flow_update(existing, normalized)
            session.add(existing)
            updated += 1
    return inserted, updated


def _upsert_statement_trade_records(
    session: Session,
    *,
    user_id: int,
    run: EastmoneyTradeSyncRun,
    statement: EastmoneyStatement,
    account_label: str,
) -> tuple[int, int]:
    inserted = 0
    updated = 0
    for flow in statement.fund_flows:
        row = fund_flow_to_trade_row(flow)
        if row is None:
            continue
        row["币种"] = "人民币"
        normalized = _normalize_trade_row(row, TRADE_SOURCE_STATEMENT_FLOW, account_label)
        normalized["raw_json"] = _statement_trade_raw_json(statement, flow, normalized["raw_json"])
        normalized["raw_text"] = _stable_json(normalized["raw_json"])
        existing = _find_existing_trade_record(
            session,
            user_id=user_id,
            account_label=account_label,
            normalized=normalized,
        )
        if existing is None:
            now = time.time()
            session.add(
                EastmoneyTradeRecord(
                    user_id=user_id,
                    sync_run_id=run.id,
                    account_label=account_label,
                    first_seen_at=now,
                    last_seen_at=now,
                    created_at=now,
                    updated_at=now,
                    **normalized,
                )
            )
            inserted += 1
            continue
        if existing.source == TRADE_SOURCE_STATEMENT_FLOW:
            _apply_trade_record_update(existing, normalized, run.id, account_label)
        else:
            existing.raw_json = _merge_raw_json(existing.raw_json, {"pdf_statement_flow": normalized["raw_json"]})
            existing.raw_text = _stable_json(existing.raw_json)
            existing.last_seen_at = time.time()
            existing.updated_at = existing.last_seen_at
        session.add(existing)
        updated += 1
    return inserted, updated


def _store_statement_asset_snapshot(
    session: Session,
    *,
    user_id: int,
    run: EastmoneyTradeSyncRun,
    statement: EastmoneyStatement,
    account_label: str,
    captured_at: float,
) -> None:
    summary = statement.asset_summary
    session.add(
        EastmoneyAssetSnapshot(
            user_id=user_id,
            sync_run_id=run.id,
            account_label=account_label,
            captured_at=captured_at,
            total_asset=summary.get("总资产", ""),
            market_value=summary.get("证券市值", ""),
            cash_available=summary.get("资金可用", ""),
            cash_balance=summary.get("资金余额", ""),
            raw_json=summary,
            created_at=time.time(),
        )
    )


def _store_statement_positions(
    session: Session,
    *,
    user_id: int,
    run: EastmoneyTradeSyncRun,
    statement: EastmoneyStatement,
    account_label: str,
    captured_at: float,
) -> int:
    for position in statement.positions:
        session.add(
            EastmoneyPositionSnapshot(
                user_id=user_id,
                sync_run_id=run.id,
                account_label=account_label,
                source=POSITION_SOURCE_STATEMENT,
                market=_statement_market(position.market, position.security_code),
                captured_at=captured_at,
                security_code=position.security_code,
                security_name=position.security_name,
                quantity=position.quantity,
                cost_price=position.cost_price,
                current_price=position.market_price,
                market_value=position.market_value,
                currency="人民币",
                raw_json=position.__dict__,
                created_at=time.time(),
            )
        )
    return len(statement.positions)


def _statement_flow_fields(
    *,
    user_id: int,
    statement_import_id: str,
    sync_run_id: str,
    account_label: str,
    source_key: str,
    flow: EastmoneyStatementFundFlow,
    raw_json: dict[str, Any],
    now: float,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "statement_import_id": statement_import_id,
        "sync_run_id": sync_run_id,
        "account_label": account_label,
        "source": FUND_FLOW_SOURCE_STATEMENT,
        "source_key": source_key,
        "flow_date": flow.flow_date,
        "flow_category": flow.flow_category,
        "market": _infer_market(flow.security_code),
        "security_code": flow.security_code,
        "security_name": flow.security_name,
        "quantity": flow.quantity,
        "price": flow.price,
        "occurrence_amount": flow.occurrence_amount,
        "fee": flow.fee,
        "stamp_tax": flow.stamp_tax,
        "transfer_fee": flow.transfer_fee,
        "fund_balance": flow.fund_balance,
        "currency": "人民币",
        "raw_json": raw_json,
        "raw_text": _stable_json(raw_json),
        "quantity_value": _parse_number(flow.quantity),
        "price_value": _parse_number(flow.price),
        "occurrence_amount_value": _parse_number(flow.occurrence_amount),
        "fee_value": _parse_number(flow.fee),
        "stamp_tax_value": _parse_number(flow.stamp_tax),
        "transfer_fee_value": _parse_number(flow.transfer_fee),
        "fund_balance_value": _parse_number(flow.fund_balance),
        "last_seen_at": now,
        "updated_at": now,
        "created_at": now,
        "first_seen_at": now,
    }


def _apply_fund_flow_update(record: EastmoneyFundFlowRecord, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if key in {"id", "user_id", "source_key", "first_seen_at", "created_at"}:
            continue
        setattr(record, key, value)


def _statement_flow_source_key(account_label: str, flow: EastmoneyStatementFundFlow) -> str:
    return _hash_key(
        FUND_FLOW_SOURCE_STATEMENT,
        account_label,
        flow.flow_date,
        flow.flow_category,
        flow.security_code,
        flow.security_name,
        flow.quantity,
        flow.price,
        flow.occurrence_amount,
        flow.fee,
        flow.stamp_tax,
        flow.transfer_fee,
        flow.fund_balance,
    )


def _statement_flow_raw_json(statement: EastmoneyStatement, flow: EastmoneyStatementFundFlow) -> dict[str, Any]:
    return {
        "query_start_date": statement.query_start_date,
        "query_end_date": statement.query_end_date,
        "print_time": statement.print_time,
        "file_sha256": statement.file_sha256,
        **flow.__dict__,
    }


def _statement_trade_raw_json(
    statement: EastmoneyStatement,
    flow: EastmoneyStatementFundFlow,
    row: dict[str, Any],
) -> dict[str, Any]:
    return {
        **row,
        "_import_source": TRADE_SOURCE_STATEMENT_FLOW,
        "_statement_query_start_date": statement.query_start_date,
        "_statement_query_end_date": statement.query_end_date,
        "_statement_print_time": statement.print_time,
        "_statement_file_sha256": statement.file_sha256,
        "_statement_flow_raw_text": flow.raw_text,
    }


def _statement_market(market_text: str, security_code: str) -> str:
    if "港" in market_text:
        return "HK"
    if "沪" in market_text:
        return "SH"
    if "深" in market_text:
        return "SZ"
    return _infer_market(security_code)


def _apply_snapshot_to_run(run: EastmoneyTradeSyncRun, snapshot: EastmoneyTradeSnapshot) -> None:
    run.account_label = snapshot.account_label
    run.start_date = snapshot.start_date
    run.end_date = snapshot.end_date
    run.captured_at = snapshot.captured_at
    run.asset_summary_json = snapshot.summary
    run.updated_at = time.time()


def _store_asset_snapshot(
    session: Session,
    *,
    user_id: int,
    run: EastmoneyTradeSyncRun,
    snapshot: EastmoneyTradeSnapshot,
) -> None:
    summary = snapshot.summary
    session.add(
        EastmoneyAssetSnapshot(
            user_id=user_id,
            sync_run_id=run.id,
            account_label=snapshot.account_label,
            captured_at=snapshot.captured_at,
            total_asset=_first_value(summary, "总资产"),
            market_value=_first_value(summary, "证券市值"),
            cash_available=_first_value(summary, "可用资金"),
            cash_balance=_first_value(summary, "资金余额"),
            withdrawable=_first_value(summary, "可取资金"),
            frozen=_first_value(summary, "冻结资金"),
            pnl=_first_value(summary, "持仓盈亏"),
            raw_json=summary,
            created_at=time.time(),
        )
    )


def _store_position_snapshots(
    session: Session,
    *,
    user_id: int,
    run: EastmoneyTradeSyncRun,
    snapshot: EastmoneyTradeSnapshot,
) -> int:
    count = 0
    for source, table in (
        ("normal_position", snapshot.positions),
        ("hk_position", snapshot.hk_positions),
        ("sgt_position", snapshot.sgt_positions),
    ):
        for row in table.rows:
            normalized = _normalize_position_row(row, source)
            session.add(
                EastmoneyPositionSnapshot(
                    user_id=user_id,
                    sync_run_id=run.id,
                    account_label=snapshot.account_label,
                    source=source,
                    market=normalized["market"],
                    captured_at=snapshot.captured_at,
                    security_code=normalized["security_code"],
                    security_name=normalized["security_name"],
                    quantity=normalized["quantity"],
                    available_quantity=normalized["available_quantity"],
                    cost_price=normalized["cost_price"],
                    current_price=normalized["current_price"],
                    market_value=normalized["market_value"],
                    pnl=normalized["pnl"],
                    pnl_ratio=normalized["pnl_ratio"],
                    currency=normalized["currency"],
                    raw_json=row,
                    created_at=time.time(),
                )
            )
            count += 1
    return count


def _upsert_trade_records(
    session: Session,
    *,
    user_id: int,
    run: EastmoneyTradeSyncRun,
    snapshot: EastmoneyTradeSnapshot,
) -> tuple[int, int]:
    inserted = 0
    updated = 0
    for source, table in (
        (TRADE_SOURCE_NORMAL, snapshot.history_deals),
        (TRADE_SOURCE_HK, snapshot.hk_history_deals),
    ):
        for row in table.rows:
            normalized = _normalize_trade_row(row, source, snapshot.account_label)
            existing = _find_existing_trade_record(
                session,
                user_id=user_id,
                account_label=snapshot.account_label,
                normalized=normalized,
            )
            if existing is None:
                now = time.time()
                session.add(
                    EastmoneyTradeRecord(
                        user_id=user_id,
                        sync_run_id=run.id,
                        account_label=snapshot.account_label,
                        first_seen_at=now,
                        last_seen_at=now,
                        created_at=now,
                        updated_at=now,
                        **normalized,
                    )
                )
                inserted += 1
            else:
                _apply_trade_record_update(existing, normalized, run.id, snapshot.account_label)
                session.add(existing)
                updated += 1
    return inserted, updated


def _find_existing_trade_record(
    session: Session,
    *,
    user_id: int,
    account_label: str,
    normalized: dict[str, Any],
) -> EastmoneyTradeRecord | None:
    exact = session.exec(
        select(EastmoneyTradeRecord).where(
            EastmoneyTradeRecord.user_id == user_id,
            EastmoneyTradeRecord.source_key == normalized["source_key"],
        )
    ).first()
    if exact is not None:
        return exact

    if not all(
        [
            account_label,
            normalized["trade_date"],
            normalized["security_code"],
            normalized["direction"],
            normalized["quantity_value"] is not None,
            normalized["price_value"] is not None,
            normalized["amount_value"] is not None,
        ]
    ):
        return None

    candidates = session.exec(
        select(EastmoneyTradeRecord).where(
            EastmoneyTradeRecord.user_id == user_id,
            EastmoneyTradeRecord.account_label == account_label,
            EastmoneyTradeRecord.trade_date == normalized["trade_date"],
            EastmoneyTradeRecord.security_code == normalized["security_code"],
            EastmoneyTradeRecord.direction == normalized["direction"],
        )
    ).all()
    matches = [
        record
        for record in candidates
        if _numbers_equal(record.quantity_value, normalized["quantity_value"])
        and _numbers_equal(record.price_value, normalized["price_value"])
        and _numbers_equal(record.amount_value, normalized["amount_value"])
    ]
    if len(matches) == 1:
        return matches[0]

    compatible_candidates = session.exec(
        select(EastmoneyTradeRecord).where(
            EastmoneyTradeRecord.user_id == user_id,
            EastmoneyTradeRecord.trade_date == normalized["trade_date"],
            EastmoneyTradeRecord.security_code == normalized["security_code"],
            EastmoneyTradeRecord.direction == normalized["direction"],
        )
    ).all()
    compatible_matches = [
        record
        for record in compatible_candidates
        if _account_labels_compatible(record.account_label, account_label)
        and _numbers_equal(record.quantity_value, normalized["quantity_value"])
        and _numbers_equal(record.price_value, normalized["price_value"])
        and _numbers_equal(record.amount_value, normalized["amount_value"])
    ]
    return compatible_matches[0] if len(compatible_matches) == 1 else None


def _apply_trade_record_update(
    record: EastmoneyTradeRecord,
    normalized: dict[str, Any],
    sync_run_id: str,
    account_label: str,
) -> None:
    record.sync_run_id = sync_run_id
    record.account_label = account_label
    record.source = normalized["source"]
    record.market = normalized["market"]
    record.trade_date = normalized["trade_date"]
    record.trade_time = normalized["trade_time"]
    record.security_code = normalized["security_code"]
    record.security_name = normalized["security_name"]
    record.direction = normalized["direction"]
    record.quantity = normalized["quantity"]
    record.price = normalized["price"]
    record.occurrence_date = _prefer_text(normalized["occurrence_date"], record.occurrence_date)
    record.occurrence_time = _prefer_text(normalized["occurrence_time"], record.occurrence_time)
    record.occurrence_amount = _prefer_text(normalized["occurrence_amount"], record.occurrence_amount)
    record.amount = normalized["amount"]
    record.fee = normalized["fee"]
    record.commission = _prefer_text(normalized["commission"], record.commission)
    record.stamp_tax = _prefer_text(normalized["stamp_tax"], record.stamp_tax)
    record.transfer_fee = _prefer_text(normalized["transfer_fee"], record.transfer_fee)
    record.other_fee = _prefer_text(normalized["other_fee"], record.other_fee)
    record.currency = normalized["currency"]
    record.deal_id = normalized["deal_id"]
    record.shareholder_account = _prefer_text(normalized["shareholder_account"], record.shareholder_account)
    record.share_balance = _prefer_text(normalized["share_balance"], record.share_balance)
    record.fund_balance = _prefer_text(normalized["fund_balance"], record.fund_balance)
    record.extended_name = _prefer_text(normalized["extended_name"], record.extended_name)
    record.raw_json = _merge_raw_json(record.raw_json, normalized["raw_json"])
    record.raw_text = _stable_json(record.raw_json)
    record.quantity_value = normalized["quantity_value"]
    record.price_value = normalized["price_value"]
    record.occurrence_amount_value = _prefer_number(
        normalized["occurrence_amount_value"],
        record.occurrence_amount_value,
    )
    record.amount_value = normalized["amount_value"]
    record.fee_value = normalized["fee_value"]
    record.commission_value = _prefer_number(normalized["commission_value"], record.commission_value)
    record.stamp_tax_value = _prefer_number(normalized["stamp_tax_value"], record.stamp_tax_value)
    record.transfer_fee_value = _prefer_number(normalized["transfer_fee_value"], record.transfer_fee_value)
    record.other_fee_value = _prefer_number(normalized["other_fee_value"], record.other_fee_value)
    record.share_balance_value = _prefer_number(normalized["share_balance_value"], record.share_balance_value)
    record.fund_balance_value = _prefer_number(normalized["fund_balance_value"], record.fund_balance_value)
    record.last_seen_at = time.time()
    record.updated_at = record.last_seen_at


def _normalize_trade_row(row: dict[str, str], source: str, account_label: str) -> dict[str, Any]:
    raw_json = dict(row)
    raw_text = _stable_json(raw_json)
    deal_id = _first_value(
        row,
        "成交编号",
        "合同编号",
        "委托编号",
        "流水号",
        "业务编号",
    )
    occurrence_date = _normalize_date_text(_first_value(row, "发生日期", "资金发生日期", "变动日期"))
    occurrence_time = _first_value(row, "发生时间", "资金发生时间", "变动时间")
    trade_date = _normalize_date_text(
        _first_value(row, "成交日期", "交易日期", "业务日期", "日期", "清算日期")
    ) or occurrence_date
    trade_time = _first_value(row, "成交时间", "交易时间", "时间") or occurrence_time
    security_code = _first_value(row, "证券代码", "股票代码", "代码")
    security_name = _first_value(row, "证券名称", "股票名称", "名称")
    quantity = _first_value(row, "成交数量", "成交股数", "数量", "发生数量")
    price = _first_value(row, "成交价格", "成交均价", "价格")
    amount = _first_value(row, "成交金额", "成交金额(元)", "成交额", "金额")
    occurrence_amount = _first_value(row, "发生金额", "资金发生金额", "净发生额")
    if not amount:
        amount = _first_value(row, "清算金额") or occurrence_amount
    commission = _first_value(row, "佣金")
    stamp_tax = _first_value(row, "印花税")
    transfer_fee = _first_value(row, "过户费")
    other_fee = _first_value(row, "其他费用", "其他费")
    fee = _first_value(row, "费用合计", "手续费")
    fee_value = _parse_number(fee)
    if fee_value is None:
        fee_value = _sum_number_values(
            row,
            "佣金",
            "交易规费",
            "经手费",
            "证管费",
            "印花税",
            "过户费",
            "交易费",
            "结算费",
            "其他费用",
            "其他费",
        )
        fee = _format_number(fee_value) if fee_value is not None else _first_value(row, "佣金")
    direction = _normalize_direction(
        _first_value(row, "委托方向", "买卖标志", "买卖类别", "操作", "交易类别", "业务名称")
    )
    occurrence_amount = occurrence_amount or _compute_occurrence_amount(amount, fee, direction)
    market = "HK" if source == TRADE_SOURCE_HK else _infer_market(security_code)
    basis = deal_id or raw_text
    source_key = _hash_key(source, account_label, trade_date, trade_time, security_code, basis)
    return {
        "source": source,
        "source_key": source_key,
        "market": market,
        "trade_date": trade_date,
        "trade_time": trade_time,
        "occurrence_date": occurrence_date,
        "occurrence_time": occurrence_time,
        "security_code": security_code,
        "security_name": security_name,
        "direction": direction,
        "quantity": quantity,
        "price": price,
        "occurrence_amount": occurrence_amount,
        "amount": amount,
        "fee": fee,
        "commission": commission,
        "stamp_tax": stamp_tax,
        "transfer_fee": transfer_fee,
        "other_fee": other_fee,
        "currency": _first_value(row, "币种", "交易币种", "结算币种"),
        "deal_id": deal_id,
        "shareholder_account": _first_value(row, "股东账号", "股东代码"),
        "share_balance": _first_value(row, "股份余额", "股票余额", "证券余额"),
        "fund_balance": _first_value(row, "资金余额"),
        "extended_name": _first_value(row, "扩位简称"),
        "raw_json": raw_json,
        "raw_text": raw_text,
        "quantity_value": _parse_number(quantity),
        "price_value": _parse_number(price),
        "occurrence_amount_value": _parse_number(occurrence_amount),
        "amount_value": _parse_number(amount),
        "fee_value": fee_value,
        "commission_value": _parse_number(commission),
        "stamp_tax_value": _parse_number(stamp_tax),
        "transfer_fee_value": _parse_number(transfer_fee),
        "other_fee_value": _parse_number(other_fee),
        "share_balance_value": _parse_number(_first_value(row, "股份余额", "股票余额", "证券余额")),
        "fund_balance_value": _parse_number(_first_value(row, "资金余额")),
    }


def _normalize_position_row(row: dict[str, str], source: str) -> dict[str, str]:
    security_code = _first_value(row, "证券代码", "股票代码", "代码")
    return {
        "market": "HK" if source in {"hk_position", "sgt_position"} else _infer_market(security_code),
        "security_code": security_code,
        "security_name": _first_value(row, "证券名称", "股票名称", "名称"),
        "quantity": _first_value(row, "证券数量", "持仓数量", "股票余额", "当前持仓"),
        "available_quantity": _first_value(row, "可卖数量", "可用数量", "可用余额"),
        "cost_price": _first_value(row, "成本价", "成本价格", "参考成本价"),
        "current_price": _first_value(row, "当前价", "最新价", "参考市价"),
        "market_value": _first_value(row, "证券市值", "参考市值", "市值"),
        "pnl": _first_value(row, "持仓盈亏", "参考盈亏(￥)", "参考盈亏", "浮动盈亏"),
        "pnl_ratio": _first_value(row, "盈亏比例(%)", "持仓盈亏比例", "盈亏比例"),
        "currency": _first_value(row, "币种", "交易币种"),
    }


def _first_value(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = str(row.get(name) or "").strip()
        if value:
            return value
    return ""


def _validate_imported_trade_record(normalized: dict[str, Any]) -> None:
    required_fields = [
        ("trade_date", "日期"),
        ("trade_time", "时间"),
        ("security_code", "代码"),
        ("security_name", "名称"),
        ("direction", "方向"),
        ("quantity_value", "数量"),
        ("price_value", "价格"),
        ("amount_value", "成交金额"),
    ]
    missing = [label for key, label in required_fields if normalized.get(key) in (None, "")]
    if missing:
        raise EastmoneyTradeError(f"手机截图交易明细字段不完整，缺少：{'、'.join(missing)}")


def _with_import_metadata(raw_json: dict[str, Any], *, ocr_lines: list[str] | None) -> dict[str, Any]:
    payload = dict(raw_json)
    payload["_import_source"] = TRADE_SOURCE_MOBILE_DETAIL
    if ocr_lines:
        payload["_ocr_lines"] = list(ocr_lines)
    return payload


def _compute_occurrence_amount(amount: str, fee: str, direction: str) -> str:
    amount_value = _parse_number(amount)
    if amount_value is None:
        return ""
    fee_value = _parse_number(fee) or 0.0
    if "卖" in direction:
        return _format_number(amount_value - fee_value)
    if "买" in direction:
        return _format_number(amount_value + fee_value)
    return _format_number(amount_value)


def _prefer_text(new_value: str, old_value: str) -> str:
    return new_value if str(new_value or "").strip() else old_value


def _prefer_number(new_value: float | None, old_value: float | None) -> float | None:
    return new_value if new_value is not None else old_value


def _merge_raw_json(old_value: dict[str, Any] | None, new_value: dict[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if isinstance(old_value, dict):
        merged.update(old_value)
    if isinstance(new_value, dict):
        merged.update(new_value)
    return merged


def _numbers_equal(left: float | None, right: float | None, tolerance: float = 1e-6) -> bool:
    if left is None or right is None:
        return left is right
    return abs(left - right) <= tolerance


def _account_labels_compatible(left: str, right: str) -> bool:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True

    left_name, left_account = _split_account_label(left_text)
    right_name, right_account = _split_account_label(right_text)
    if left_name and right_name and left_name != right_name:
        return False
    if left_account and right_account:
        return _masked_account_matches(left_account, right_account) or _masked_account_matches(right_account, left_account)
    return False


def _split_account_label(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"(.+?)\((.+)\)", value.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return value.strip(), ""


def _masked_account_matches(masked_or_full: str, full_or_masked: str) -> bool:
    pattern = "^" + re.escape(masked_or_full).replace(r"\*", r"\d") + "$"
    return bool(re.fullmatch(pattern, full_or_masked))


def _stable_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_key(*parts: str) -> str:
    raw = "\n".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_date_text(value: str) -> str:
    text = value.strip().replace("/", "-")
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return text


def _normalize_direction(value: str) -> str:
    text = value.strip()
    if "买" in text:
        return "买入"
    if "卖" in text:
        return "卖出"
    return text


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


def _sum_number_values(row: dict[str, str], *names: str) -> float | None:
    total = 0.0
    seen = False
    for name in names:
        number = _parse_number(row.get(name, ""))
        if number is None:
            continue
        total += number
        seen = True
    return total if seen else None


def _format_number(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _infer_market(security_code: str) -> str:
    code = security_code.strip()
    if re.fullmatch(r"\d{5}", code):
        return "HK"
    if code.startswith(("60", "68", "51", "56", "58", "11", "13")):
        return "SH"
    if code.startswith(("00", "30", "15", "16", "12")):
        return "SZ"
    return ""
