from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.tasks import magic_invasion_tail as tail
from backend.core.fanxiu.data_annotation.tasks import common_shop_quantity as quantity
from backend.core.fanxiu.data_annotation.tasks import tiandi_yiju_tail
from backend.core.fanxiu.data_annotation.tasks import xianyuan_duokui_tail
from backend.core.fanxiu.data_annotation.tasks import yunmeng_tail


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def _dialog(*, show_num: int, maximum: int = 50, price: int = 2500):
    return {
        "complete": True,
        "showNum": show_num,
        "maxNum": maximum,
        "Price": price,
        "HadPrice": 500_000,
        "goodsNum": 1,
        "CanBuy": True,
        "isEnough": True,
    }


def test_common_shop_quantity_uses_shared_slider_and_runtime_proof(monkeypatch):
    snapshots = iter(
        (
            _dialog(show_num=1),
            _dialog(show_num=17),
            _dialog(show_num=17),
        )
    )
    snapshot_reader = lambda: next(snapshots)
    observed = {}

    def set_count(context, assets, desired, **kwargs):
        observed.update(
            context=context,
            assets=assets,
            desired=desired,
            maximum=kwargs["maximum"],
            runtime=kwargs["runtime_count_reader"](),
        )
        if False:
            yield None
        return {"after": desired, "phase": "verified"}

    monkeypatch.setattr(quantity, "set_verified_integer_slider_count", set_count)
    context = object()
    result = _drain(
        quantity.set_verified_common_shop_quantity(
            context,
            17,
            unit_price=2500,
            label="魔道_兑换收尾/测试商品",
            snapshot_reader=snapshot_reader,
        )
    )

    assert observed == {
        "context": context,
        "assets": quantity.COMMON_SHOP_QUANTITY_ASSETS,
        "desired": 17,
        "maximum": 50,
        "runtime": {"current": 17, "maximum": 50},
    }
    assert result["quantity"] == 17
    assert result["owned_currency"] == 500_000
    assert result["adjustment"]["after"] == 17


def test_common_shop_quantity_refuses_purchase_when_final_runtime_count_differs(
    monkeypatch,
):
    snapshots = iter((_dialog(show_num=1), _dialog(show_num=16)))
    snapshot_reader = lambda: next(snapshots)

    def set_count(_context, _assets, desired, **_kwargs):
        if False:
            yield None
        return {"after": desired}

    monkeypatch.setattr(quantity, "set_verified_integer_slider_count", set_count)
    with pytest.raises(RuntimeError, match="购买前数量 16 != 17"):
        _drain(
            quantity.set_verified_common_shop_quantity(
                object(),
                17,
                unit_price=2500,
                label="魔道_兑换收尾/测试商品",
                snapshot_reader=snapshot_reader,
            )
        )


def test_common_shop_quantity_rejects_target_outside_runtime_maximum(monkeypatch):
    snapshot_reader = lambda: _dialog(show_num=1, maximum=10)

    def unexpected_controller(*_args, **_kwargs):
        raise AssertionError("越界目标不得进入数量控制器")
        yield

    monkeypatch.setattr(
        quantity,
        "set_verified_integer_slider_count",
        unexpected_controller,
    )
    with pytest.raises(RuntimeError, match=r"购买数量 11 超出 1\.\.10"):
        _drain(
            quantity.set_verified_common_shop_quantity(
                object(),
                11,
                unit_price=2500,
                label="魔道_兑换收尾/测试商品",
                snapshot_reader=snapshot_reader,
            )
        )


def test_magic_tail_execution_no_longer_uses_direct_quantity_click_plan():
    source = inspect.getsource(tail.execute_magic_invasion_tail_checkpoint)
    assert "yield from set_verified_common_shop_quantity" in source
    assert "exchange_quantity_clicks" not in source
    assert "click_shape_center_fast" not in source
    assert 'click_shape_center(MAGIC_SHOP_SCENE, "兑换宝阁标题")' in source
    assert 'click_shape_center(66, "日程")' in source
    assert "if current_scene != 34" in source


def test_magic_tail_resolves_live_product_identity_before_click(monkeypatch):
    class Shape:
        def box(self):
            return {"x": 0, "y": 0, "w": 100, "h": 100}

    class View:
        def get_shape(self, _name):
            return Shape()

    class Context:
        def __init__(self):
            self.events = []

        def view(self, scene):
            assert scene == tail.MAGIC_SHOP_SCENE
            return View()

        def full_frame_ocr_tokens(self, *, update):
            assert update is True
            return []

        def ocr_tokens_in_shapes(self, scene, shapes, **kwargs):
            assert scene == tail.MAGIC_SHOP_SCENE
            assert shapes == ("商品列表",)
            assert kwargs == {"padding": 0, "crop": True}
            return []

        def scroll_shape_content(self, scene, shape, **kwargs):
            self.events.append(("scroll", scene, shape, kwargs))
            if False:
                yield None
            return kwargs["direction"] == "down"

        def wait_action_settle(self, seconds):
            self.events.append(("settle", seconds))
            if False:
                yield None

        def click_shape_center(self, scene, shape):
            self.events.append(("dismiss", scene, shape))

        def click_frame_point(self, scene, x, y):
            self.events.append(("click", scene, x, y))

        def wait_scene(self, scenes, **kwargs):
            self.events.append(("wait", tuple(scenes), kwargs))
            if False:
                yield None
            return tail.COMMON_SHOP_DIALOG_SCENE

    attempts = iter(
        (
            RuntimeError("not visible"),
            RuntimeError("not visible"),
            RuntimeError("not visible"),
            RuntimeError("not visible in crop"),
            SimpleNamespace(x=12, y=34),
        )
    )

    def resolve(*_args, **_kwargs):
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(tail, "resolve_exchange_shop_item", resolve)
    monkeypatch.setattr(tail, "_verify_dialog", lambda *_args, **_kwargs: None)
    context = Context()

    _drain(
        tail._open_verified_shop_product(
            context,
            name="目标商品",
            unit_price=100,
            max_scrolls=2,
        )
    )

    assert [event[0] for event in context.events] == [
        "scroll",
        "dismiss",
        "settle",
        "dismiss",
        "settle",
        "dismiss",
        "settle",
        "scroll",
        "click",
        "wait",
    ]
    assert context.events[0][3]["direction"] == "up"
    assert context.events[7][3]["direction"] == "down"
    assert context.events[7][3]["ratio"] == tail.SHOP_TRAVERSAL_RATIO
    assert context.events[7][3]["duration"] == 0.12
    assert context.events[8] == ("click", tail.MAGIC_SHOP_SCENE, 12, 34)


def test_magic_tail_waits_for_async_row_refresh_before_scrolling(monkeypatch):
    calls = 0

    def resolve(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("rows still refreshing")
        return SimpleNamespace(x=12, y=34)

    monkeypatch.setattr(tail, "resolve_exchange_shop_item", resolve)
    monkeypatch.setattr(tail, "_verify_dialog", lambda *_args, **_kwargs: None)

    class Shape:
        def box(self):
            return {"x": 0, "y": 0, "w": 100, "h": 100}

    class Context:
        def __init__(self):
            self.events = []

        def view(self, _scene):
            return SimpleNamespace(get_shape=lambda _name: Shape())

        def full_frame_ocr_tokens(self, *, update):
            return []

        def wait_action_settle(self, seconds):
            self.events.append(("settle", seconds))
            if False:
                yield None

        def click_shape_center(self, scene, shape):
            self.events.append(("dismiss", scene, shape))

        def scroll_shape_content(self, *_args, **_kwargs):
            self.events.append(("scroll", _kwargs["direction"]))
            if False:
                yield None
            return False

        def click_frame_point(self, scene, x, y):
            self.events.append(("click", scene, x, y))

        def wait_scene(self, *_args, **_kwargs):
            if False:
                yield None
            return tail.COMMON_SHOP_DIALOG_SCENE

    context = Context()
    _drain(
        tail._open_verified_shop_product(
            context,
            name="目标商品",
            unit_price=100,
            max_scrolls=2,
        )
    )

    assert calls == 2
    assert context.events == [
        ("scroll", "up"),
        ("dismiss", tail.MAGIC_SHOP_SCENE, "兑换宝阁标题"),
        ("settle", 0.35),
        ("click", tail.MAGIC_SHOP_SCENE, 12, 34),
    ]


def test_magic_tail_preserves_raw_numeric_price_token(monkeypatch):
    observed_lines = []

    def resolve(lines, **_kwargs):
        observed_lines.extend(lines)
        assert any(item.get("text") == "所需：400" for item in lines)
        assert any(item.get("text") == "400" for item in lines)
        return SimpleNamespace(x=12, y=34)

    monkeypatch.setattr(tail, "resolve_exchange_shop_item", resolve)
    monkeypatch.setattr(tail, "_verify_dialog", lambda *_args, **_kwargs: None)

    class Shape:
        def box(self):
            return {"x": 0, "y": 0, "w": 100, "h": 100}

    class Context:
        def view(self, _scene):
            return SimpleNamespace(get_shape=lambda _name: Shape())

        def full_frame_ocr_tokens(self, *, update):
            assert update is True
            return [
                {
                    "text": "所需：",
                    "x": 10,
                    "y": 20,
                    "w": 20,
                    "h": 10,
                    "parent_line_id": "price",
                    "order": 0,
                },
                {
                    "text": "400",
                    "x": 35,
                    "y": 20,
                    "w": 20,
                    "h": 10,
                    "parent_line_id": "price",
                    "order": 1,
                },
            ]

        def scroll_shape_content(self, *_args, **kwargs):
            assert kwargs["direction"] == "up"
            if False:
                yield None
            return False

        def click_frame_point(self, *_args):
            return None

        def wait_scene(self, *_args, **_kwargs):
            if False:
                yield None
            return tail.COMMON_SHOP_DIALOG_SCENE

    _drain(
        tail._open_verified_shop_product(
            Context(),
            name="目标商品",
            unit_price=400,
            max_scrolls=0,
        )
    )

    assert observed_lines


@pytest.mark.parametrize(
    "executor",
    (
        tiandi_yiju_tail.execute_tiandi_yiju_exchange_tail,
        xianyuan_duokui_tail.execute_xianyuan_duokui_tail_checkpoint,
        yunmeng_tail.execute_yunmeng_tail_job,
    ),
)
def test_other_exchange_tails_use_common_shop_quantity_control(executor):
    source = inspect.getsource(executor)
    assert "yield from set_verified_common_shop_quantity" in source
    assert "exchange_quantity_clicks" not in source
    assert "click_shape_center_fast" not in source
    quantity_at = source.index("yield from set_verified_common_shop_quantity")
    total_price_at = source.index("ocr_numbers_in_shapes", quantity_at)
    purchase_at = source.index('"购买"', total_price_at)
    assert quantity_at < total_price_at < purchase_at
