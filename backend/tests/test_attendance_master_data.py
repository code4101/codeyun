from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.attendance.master_data import (
    PAYMENT_DATASET,
    USER_DATASET,
    ingest_master_data_file,
    lookup_payment_order,
    lookup_registration_user,
    payment_refund_rows,
)
from backend.models import (
    AttendanceDataImport,
    AttendancePaymentLedger,
    AttendancePaymentOrder,
    AttendanceUser,
)


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_user_snapshot_is_deduplicated_and_supports_local_registration_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEYUN_DATA_DIR", str(tmp_path))
    content = (
        "用户ID,昵称,姓名,账户绑定手机号,最近采集手机号,账号状态,来源渠道\n"
        "u_1,旧昵称,张三,13800000000,,正常,自然注册\n"
        "u_1,新昵称,张三,13800000000,,正常,自然注册\n"
        "u_2,李四,李四,,13900000000,正常,自然注册\n"
    ).encode("utf-8-sig")

    with _session() as session:
        result = ingest_master_data_file(
            session,
            dataset_type=USER_DATASET,
            scope_key="shop:1",
            source_filename="用户列表导出.csv",
            content=content,
            collector_device="codepc_mi15",
        )

        assert result["status"] == "completed"
        assert result["total_rows"] == 3
        assert result["inserted_rows"] == 2
        assert result["conflict_rows"] == 1
        users = session.exec(select(AttendanceUser).order_by(AttendanceUser.xiaoe_user_id)).all()
        assert [(user.xiaoe_user_id, user.nickname) for user in users] == [
            ("u_1", "新昵称"),
            ("u_2", "李四"),
        ]
        assert lookup_registration_user(
            ["张三"],
            ["13800000000"],
            shop_id=1,
            session=session,
        ) == ("u_1", 90)

        duplicate = ingest_master_data_file(
            session,
            dataset_type=USER_DATASET,
            scope_key="shop:1",
            source_filename="再次上传.csv",
            content=content,
        )
        assert duplicate["duplicate"] is True
        assert session.exec(select(AttendanceDataImport)).all()[0].status == "completed"


def test_registration_lookup_prefers_active_named_account_over_phone_import_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEYUN_DATA_DIR", str(tmp_path))
    content = (
        "用户ID,昵称,姓名,账户绑定手机号,账号状态,来源渠道\n"
        "u_pending,手机尾号5195用户,朱亚红,13851385195,待注册（手机号导入，用户尚未注册）,B端手机号导入\n"
        "u_active,中和,中和,13851315295,正常,微信\n"
    ).encode("utf-8-sig")

    with _session() as session:
        ingest_master_data_file(
            session,
            dataset_type=USER_DATASET,
            scope_key="shop:2",
            source_filename="用户列表导出.csv",
            content=content,
        )

        assert lookup_registration_user(
            ["朱亚红", "中和"],
            ["13851385195"],
            shop_id=2,
            session=session,
        ) == ("u_active", 95)


def test_payment_ledger_import_is_idempotent_and_rebuilds_order_projection(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEYUN_DATA_DIR", str(tmp_path))
    content = (
        "记账时间,微信支付业务单号,资金流水单号,业务类型,收支金额(元),账户结余(元),"
        "资金变更提交申请人,业务凭证号,备注\n"
        "2026-07-25 10:00:00,B1,420001,交易,499.00,1000.00,系统,MA001,含手续费 3.00\n"
        "2026-07-26 10:00:00,R1,420001,退款,-99.00,901.00,管理员,MA001-refund,总金额 99.00 含手续费 0.60\n"
    ).encode("utf-8-sig")

    with _session() as session:
        result = ingest_master_data_file(
            session,
            dataset_type=PAYMENT_DATASET,
            scope_key="merchant:1599622041",
            source_filename="微信支付账单.csv",
            content=content,
        )

        assert result["inserted_rows"] == 2
        assert len(session.exec(select(AttendancePaymentLedger)).all()) == 2
        assert len(session.exec(select(AttendancePaymentOrder)).all()) == 1
        order = lookup_payment_order("MA001", session=session)
        assert order == {
            "微信支付订单号": "420001",
            "订单日期": "20260725",
            "商户订单号": "MA001",
            "订单金额": "499",
            "已返款": "99",
        }
        refunds = payment_refund_rows(
            session,
            merchant_order_id="MA001",
            wechat_order_id="420001",
        )
        assert len(refunds) == 1
        assert refunds[0]["money"] == "-99"

        duplicate = ingest_master_data_file(
            session,
            dataset_type=PAYMENT_DATASET,
            scope_key="merchant:1599622041",
            source_filename="重复账单.csv",
            content=content,
        )
        assert duplicate["duplicate"] is True
        assert len(session.exec(select(AttendancePaymentLedger)).all()) == 2
