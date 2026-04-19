import pytest

from backend.core import attendance_order
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

    def find_ele_with_refresh(self, _locator):
        return _FakeClickable()

    def wait(self, _seconds):
        return None

    def __call__(self, _locator):
        return _FakeClickable()


def test_attendance_order_bridge_reexports_shared_symbols():
    assert attendance_order.lookup_order is order_ops.lookup_order
    assert attendance_order.OrderAutomationError is order_ops.OrderAutomationError
    assert attendance_order.execute_order_action is order_ops.execute_order_action


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
    weipay.wait_refund_completion = lambda timeout=300: events.append(("wait", timeout))

    weipay.request_single_refund("MA2026", 178.0, "视觉课退款")

    assert events == [
        ("verify", weipay.tab),
        ("wait", 300),
    ]
