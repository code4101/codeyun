import os

import pytest

from backend.core.attendance import order as attendance_order
import kq5034.order_ops as order_ops
from kq5034.weipay import Weipay


class FakeWeipay:
    def __init__(self):
        self.refund_requests = []
        self.search_requests = []

    def search_refund(self, voucher_id):
        self.search_requests.append(voucher_id)
        if voucher_id == "BAD-ORDER":
            return {"error": "订单不存在"}
        if voucher_id == "TCCDN4-0OZRE8O-EI4L":
            return {
                "支付单号": "420000000000000000000123",
                "商户订单号": "TCCDN4-0OZRE8O-EI4L",
                "订单金额": 620.0,
                "已返款": 442.0,
            }
        if voucher_id == "MA2026":
            return {
                "支付单号": "420000000000000000000001",
                "商户订单号": "MA2026",
                "订单金额": 620.0,
                "已返款": 442.0,
            }
        return {"error": "订单不存在"}

    def request_single_refund(self, voucher_id, refund_amount, refund_reason):
        self.refund_requests.append((voucher_id, refund_amount, refund_reason))


class FakeKqdb:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []

    def exec2dict(self, *_args, **_kwargs):
        self.calls.append((_args, _kwargs))
        return self

    def fetchall(self):
        return list(self.rows)


class BrokenKqdb:
    def exec2dict(self, *_args, **_kwargs):
        raise RuntimeError("未配置环境变量XL_LINKS")


class _FakeClickable:
    def click(self, **_kwargs):
        return None

    def input(self, *_args, **_kwargs):
        return None


class _FakeTab:
    def get(self, _url):
        return None

    def ele(self, *_args, **_kwargs):
        return _FakeClickable()

    def find_ele_with_refresh(self, _locator):
        return _FakeClickable()

    def wait(self, _seconds):
        return None

    def __call__(self, _locator):
        return _FakeClickable()


def test_attendance_order_bridge_reexports_shared_symbols():
    assert attendance_order.lookup_order is order_ops.lookup_order
    assert attendance_order.OrderAutomationError is order_ops.OrderAutomationError
    assert attendance_order._execute_order_action is order_ops.execute_order_action


def test_query_order_refund_details_retries_auto_empty_with_precise_type(monkeypatch):
    calls = []

    def fake_query(order_id, *, query_type="auto", weipay=None, weipay_login_users=None):
        calls.append((order_id, query_type, weipay, weipay_login_users))
        if query_type == "merchant_order":
            return {
                "summary": {
                    "order_id": order_id,
                    "matched_order_id": order_id,
                    "query_type": query_type,
                    "row_count": 1,
                    "refund_amount_total": 550,
                },
                "rows": [{"refund_amount": 550}],
            }
        return {
            "summary": {
                "order_id": order_id,
                "matched_order_id": "TETVM2-OOZRE8O-17S8",
                "query_type": query_type,
                "row_count": 0,
                "refund_amount_total": 0,
            },
            "rows": [],
        }

    monkeypatch.setattr(attendance_order, "_query_order_refund_details", fake_query)

    result = attendance_order.query_order_refund_details(
        "TETVM2-0OZRE8O-17S8",
        query_type="auto",
        weipay="weipay",
        weipay_login_users=["考勤后台"],
    )

    assert result["summary"]["query_type"] == "merchant_order"
    assert result["summary"]["refund_amount_total"] == 550
    assert calls == [
        ("TETVM2-0OZRE8O-17S8", "auto", "weipay", ["考勤后台"]),
        ("TETVM2-0OZRE8O-17S8", "merchant_order", "weipay", ["考勤后台"]),
    ]


def test_execute_order_action_closes_owned_weipay_tabs(monkeypatch):
    events = []
    fake_weipay = object()

    monkeypatch.setattr(
        attendance_order,
        "_ensure_managed_weipay",
        lambda weipay=None, *, weipay_login_users=None: (fake_weipay, True),
    )
    monkeypatch.setattr(
        attendance_order,
        "_execute_order_action",
        lambda **kwargs: events.append(("execute", kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(
        attendance_order,
        "_close_extra_weipay_tabs",
        lambda weipay, *, min_tabs_to_keep=1: events.append(("close", weipay, min_tabs_to_keep)),
    )

    result = attendance_order.execute_order_action(
        action="inspect",
        rows=[{"商户订单号": "MA2026"}],
        weipay_login_users=["考勤后台"],
        lookup_mode="browser_only",
    )

    assert result == {"ok": True}
    assert events[0][0] == "execute"
    assert events[0][1]["weipay"] is fake_weipay
    assert events[-1] == ("close", fake_weipay, 1)


def test_execute_order_action_db_only_does_not_preload_weipay(monkeypatch):
    events = []

    def fail_ensure(*_args, **_kwargs):
        raise AssertionError("db_only inspect should not initialize Weipay in bridge")

    monkeypatch.setattr(attendance_order, "_ensure_managed_weipay", fail_ensure)
    monkeypatch.setattr(
        attendance_order,
        "_execute_order_action",
        lambda **kwargs: events.append(kwargs) or {"ok": True},
    )

    result = attendance_order.execute_order_action(
        action="inspect",
        rows=[{"商户订单号": "MA2026"}],
        lookup_mode="db_only",
    )

    assert result == {"ok": True}
    assert events[0]["weipay"] is None


def test_execute_order_action_loads_legacy_xl_env(monkeypatch, tmp_path):
    legacy_env = tmp_path / "slns" / "xlproject" / ".env"
    legacy_env.parent.mkdir(parents=True)
    legacy_env.write_text("XL_LINKS='[[\"from\", \"to\"], [\"*\", \"kq5034\"]]'\nIGNORED=value\n", encoding="utf-8")
    fake_order_file = tmp_path / "slns" / "codeyun" / "backend" / "core" / "attendance" / "order.py"
    fake_order_file.parent.mkdir(parents=True)
    fake_order_file.write_text("", encoding="utf-8")

    monkeypatch.delenv("XL_LINKS", raising=False)
    monkeypatch.delenv("IGNORED", raising=False)
    monkeypatch.setattr(attendance_order, "__file__", str(fake_order_file))
    monkeypatch.setattr(attendance_order, "_execute_order_action", lambda **kwargs: {"rows": []})

    attendance_order.execute_order_action(action="inspect", rows=[], lookup_mode="db_only")

    assert "kq5034" in os.environ["XL_LINKS"]
    assert "IGNORED" not in os.environ


def test_query_order_refund_details_closes_owned_weipay_tabs_on_error(monkeypatch):
    events = []
    fake_weipay = object()

    monkeypatch.setattr(
        attendance_order,
        "_ensure_managed_weipay",
        lambda weipay=None, *, weipay_login_users=None: (fake_weipay, True),
    )

    def raise_query(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(attendance_order, "_query_order_refund_details", raise_query)
    monkeypatch.setattr(
        attendance_order,
        "_close_extra_weipay_tabs",
        lambda weipay, *, min_tabs_to_keep=1: events.append(("close", weipay, min_tabs_to_keep)),
    )

    with pytest.raises(RuntimeError, match="boom"):
        attendance_order.query_order_refund_details("MA2026", weipay_login_users=["考勤后台"])

    assert events == [("close", fake_weipay, 1)]


def test_execute_order_action_inspect_updates_rows():
    result = order_ops.execute_order_action(
        action="inspect",
        rows=[
            {"学员名称": "张三", "商户订单号": "MA2026", "退款原因": "测试原因"},
            {"学员名称": "空白行"},
            {"学员名称": "李四", "微信支付订单号": "`BAD-ORDER"},
        ],
        weipay=FakeWeipay(),
        kqdb=FakeKqdb(),
    )

    assert result["action"] == "inspect"
    assert result["summary"]["input_count"] == 3
    assert result["summary"]["processed_count"] == 2
    assert result["summary"]["skipped_blank_count"] == 1
    assert result["summary"]["error_count"] == 1

    first, second = result["rows"]
    assert first["编号"] == "1"
    assert first["商户订单号"] == "MA2026"
    assert first["订单金额"] == 620.0
    assert first["已返款"] == 442.0
    assert second["编号"] == "2"
    assert second["订单金额"] == "订单不存在"
    assert second["已返款"] == ""


def test_execute_order_action_inspect_expands_zero_o_candidates():
    result = order_ops.execute_order_action(
        action="inspect",
        rows=[{"商户订单号": "TCCDN4-00ZRE80-EI4L"}],
        weipay=FakeWeipay(),
        kqdb=FakeKqdb(),
    )

    row = result["rows"][0]
    assert row["商户订单号"] == "TCCDN4-0OZRE8O-EI4L"
    assert row["微信支付订单号"] == "`420000000000000000000123"
    assert row["订单金额"] == 620.0
    assert row["已返款"] == 442.0


def test_lookup_order_falls_back_to_browser_when_db_unavailable():
    result = order_ops.lookup_order(
        "TCCDN4-00ZRE80-EI4L",
        kqdb=BrokenKqdb(),
        weipay=FakeWeipay(),
    )

    assert result["商户订单号"] == "TCCDN4-0OZRE8O-EI4L"
    assert result["微信支付订单号"] == "`420000000000000000000123"
    assert result["订单金额"] == 620.0
    assert result["已返款"] == 442.0


def test_execute_order_action_inspect_falls_back_to_browser_when_db_unavailable():
    result = order_ops.execute_order_action(
        action="inspect",
        rows=[{"商户订单号": "TCCDN4-00ZRE80-EI4L"}],
        weipay=FakeWeipay(),
        kqdb=BrokenKqdb(),
    )

    row = result["rows"][0]
    assert row["商户订单号"] == "TCCDN4-0OZRE8O-EI4L"
    assert row["微信支付订单号"] == "`420000000000000000000123"
    assert row["订单金额"] == 620.0
    assert row["已返款"] == 442.0


def test_lookup_order_db_only_skips_browser():
    fake_weipay = FakeWeipay()
    fake_kqdb = FakeKqdb()

    result = order_ops.lookup_order(
        "MA2026",
        kqdb=fake_kqdb,
        weipay=fake_weipay,
        lookup_mode="db_only",
    )

    assert result == {}
    assert fake_kqdb.calls
    assert fake_weipay.search_requests == []


def test_lookup_order_browser_only_skips_db():
    fake_weipay = FakeWeipay()
    fake_kqdb = FakeKqdb(
        rows=[
            {
                "flow_order": "420000000000000000000099",
                "voucher_id": "MA2026",
                "money": 1.0,
                "refund": 0.0,
            }
        ]
    )

    result = order_ops.lookup_order(
        "MA2026",
        kqdb=fake_kqdb,
        weipay=fake_weipay,
        lookup_mode="browser_only",
    )

    assert result["订单金额"] == 620.0
    assert fake_kqdb.calls == []
    assert fake_weipay.search_requests == ["MA2026"]


def test_execute_order_action_refund_uses_remaining_amount():
    fake_weipay = FakeWeipay()
    result = order_ops.execute_order_action(
        action="refund",
        rows=[
            {
                "学员名称": "王五",
                "商户订单号": "MA2026",
                "退款原因": "视觉课退款",
                "退款额度": "",
            }
        ],
        weipay=fake_weipay,
        kqdb=FakeKqdb(),
    )

    row = result["rows"][0]
    assert result["summary"]["refunded_count"] == 1
    assert fake_weipay.refund_requests == [("MA2026", 178.0, "视觉课退款")]
    assert row["退款额度"] == 178.0
    assert row["已返款"] == 620.0
    assert "已退款" in row["执行退款"]


def test_execute_order_action_rejects_unknown_action():
    with pytest.raises(order_ops.OrderAutomationError, match="不支持的订单动作"):
        order_ops.execute_order_action(action="unknown", rows=[])


def test_execute_order_action_rejects_unknown_lookup_mode():
    with pytest.raises(order_ops.OrderAutomationError, match="不支持的订单查单模式"):
        order_ops.execute_order_action(action="inspect", rows=[], lookup_mode="invalid")


def test_weipay_single_refund_waits_for_completion():
    events = []
    weipay = object.__new__(Weipay)
    weipay.tab = _FakeTab()
    weipay.填写密码与验证码 = lambda tab: events.append(("verify", tab))
    weipay.wait_refund_completion = lambda **kwargs: events.append(("wait", kwargs))

    weipay.request_single_refund("MA2026", 178.0, "视觉课退款")

    assert events == [
        ("verify", weipay.tab),
        (
            "wait",
            {
                "voucher_id": "MA2026",
                "expected_refund_amount": 178.0,
                "baseline_refunded_amount": 0,
            },
        ),
    ]
