"""One-time migration of KQ5034 user/payment master data from PG to CodeYun.

The runtime application must not import this script.  It exists only for the
observation-period migration and can be archived with the retired PG schema
after final acceptance.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from sqlmodel import SQLModel, Session, func, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.attendance.master_data import (
    PAYMENT_DATASET,
    USER_DATASET,
    ingest_master_data_file,
)
from backend.core.temp_paths import codeyun_temp_root
from backend.db import engine
from backend.models import (
    AttendancePaymentLedger,
    AttendancePaymentOrder,
    AttendanceUser,
)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def migrate(*, apply: bool) -> dict[str, Any]:
    from kq5034.db import get_kqdb

    pg = get_kqdb()
    pg_users = list(pg.exec2dict("SELECT * FROM user_table ORDER BY user_id").fetchall())
    pg_payments = list(pg.exec2dict("SELECT * FROM weipay_table ORDER BY weipay_id").fetchall())
    expected_unique_users = int(
        pg.exec2one(
            "SELECT COUNT(*) FROM ("
            "SELECT shop_id, user_id2 FROM user_table GROUP BY shop_id, user_id2"
            ") AS unique_users"
        )
    )
    expected_payment_orders = int(
        pg.exec2one("SELECT COUNT(DISTINCT flow_order) FROM weipay_table")
    )
    summary: dict[str, Any] = {
        "source": {
            "user_rows": len(pg_users),
            "unique_users": expected_unique_users,
            "payment_rows": len(pg_payments),
            "payment_orders": expected_payment_orders,
        },
        "applied": bool(apply),
    }
    if not apply:
        return summary

    temp_root = codeyun_temp_root("attendance-master-data-migration")
    payments_file = temp_root / "pg-weipay-table.csv"
    _write_csv(payments_file, pg_payments)

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        summary["user_import"] = ingest_master_data_file(
            session,
            dataset_type=USER_DATASET,
            scope_key="shop:1",
            source_filename="pg-user-table-shop1.csv",
            content=_filtered_user_csv(pg_users, shop_id=1),
            collector_device="pg-migration",
        )
        summary["user_import_shop2"] = ingest_master_data_file(
            session,
            dataset_type=USER_DATASET,
            scope_key="shop:2",
            source_filename="pg-user-table-shop2.csv",
            content=_filtered_user_csv(pg_users, shop_id=2),
            collector_device="pg-migration",
        )
        summary["payment_import"] = ingest_master_data_file(
            session,
            dataset_type=PAYMENT_DATASET,
            scope_key="merchant:1599622041",
            source_filename="pg-weipay-table.csv",
            content=payments_file.read_bytes(),
            collector_device="pg-migration",
        )
        target = {
            "users": int(session.exec(select(func.count()).select_from(AttendanceUser)).one()),
            "payment_rows": int(
                session.exec(select(func.count()).select_from(AttendancePaymentLedger)).one()
            ),
            "payment_orders": int(
                session.exec(select(func.count()).select_from(AttendancePaymentOrder)).one()
            ),
        }
        summary["target"] = target
        summary["verified"] = (
            target["users"] == expected_unique_users
            and target["payment_rows"] == len(pg_payments)
            and target["payment_orders"] == expected_payment_orders
        )
    return summary


def _filtered_user_csv(rows: list[dict[str, Any]], *, shop_id: int) -> bytes:
    selected = [row for row in rows if int(row.get("shop_id") or 0) == shop_id]
    if not selected:
        raise RuntimeError(f"PG user_table 没有 shop_id={shop_id} 数据")
    columns = list(selected[0])
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(selected)
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="实际写入 CodeYun；默认只预检")
    args = parser.parse_args()
    print(json.dumps(migrate(apply=args.apply), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
