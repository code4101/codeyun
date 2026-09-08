"""Single authorized treasure-shop purchase; no navigation, reset, or replay.

Quantity-one and quantity-six purchases have passed live acceptance, including
independent raw instance delivery and exact total currency debit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from typing import Any, Mapping

from ..ocr_spatial import group_ocr_tokens
from ...instrumentation.backpack import read_backpack_item_counts
from ...instrumentation.common_shop_buy_dialog import read_common_shop_buy_dialog_snapshot
from ...instrumentation.spirit_artifact import read_spirit_artifact_inventory_runtime
from ...runtime_gui.exchange_shop import resolve_exchange_shop_item
from ...runtime_gui.text import normalize_ocr_name
from ...catalog.spirit_artifact_wash_rules import load_spirit_artifact_wash_rules
from .common_shop_quantity import set_verified_common_shop_quantity
from .integer_count_control import IntegerSliderAssets
from .storage_bag_choice_box import StorageBagChoiceReward, verify_spirit_artifact_choice_outcome


SPIRIT_ARTIFACT_SHOP_QUANTITY_ASSETS = IntegerSliderAssets(
    settings_scene_id=725, count_region="数量",
    count_decrease="-", count_increase="+",
    count_decrease_large="-10", count_increase_large="+10", count_large_step=10,
    count_slider_thumb=None, count_slider_track="数量滑条",
)


@dataclass(frozen=True)
class SpiritArtifactPurchaseRequest:
    goods_id: int
    base_id: int
    ware_id: int
    part: int
    name: str
    cost_item_id: int = 15100001
    quantity: int = 1
    unit_price: int = 80
    currency_limit: int = 80


@dataclass(frozen=True)
class SpiritArtifactPurchaseAssets:
    shop_scene: int = 724
    dialog_scene: int = 725
    quantity_assets: IntegerSliderAssets | None = SPIRIT_ARTIFACT_SHOP_QUANTITY_ASSETS
    row_shapes: tuple[str, ...] = ("兑换列表",)
    list_shape: str = "兑换列表"
    confirm_shape: str = "兑换"


def validate_spirit_artifact_purchase_dialog(
    snapshot: Mapping[str, Any], request: SpiritArtifactPurchaseRequest,
    process_identity: tuple[int, int], *, require_quantity: bool = True,
) -> None:
    """Pure identity gate; explicit quantity is bounded by the authorized total."""
    if (any(type(value) is not int for value in
            (request.quantity, request.unit_price, request.currency_limit))
            or request.quantity <= 0 or request.unit_price <= 0
            or request.currency_limit < request.unit_price * request.quantity
            or min(request.goods_id, request.base_id, request.cost_item_id, request.ware_id) <= 0
            or request.part not in range(1, 7) or not request.name.strip()):
        raise ValueError("本体采购授权必须为明确目标、正整数数量及足够总费用上限")
    expected = {"goods_id": request.goods_id, "item_id": request.base_id,
                "cost_item_id": request.cost_item_id, "Price": request.unit_price,
                "goodsNum": 1, "ShopModelType": 4}
    if (snapshot.get("complete") is not True or snapshot.get("identity_complete") is not True
            or snapshot.get("source") != "active_common_shop_buy_tips"
            or (snapshot.get("pid"), snapshot.get("process_start_ticks")) != process_identity
            or any(snapshot.get(key) != value for key, value in expected.items())
            or snapshot.get("CanBuy") is not True or snapshot.get("isEnough") is not True
            or int(snapshot.get("HadPrice") or 0) < request.unit_price * request.quantity
            or (request.quantity > 1 and int(snapshot.get("maxNum") or 0) < request.quantity)
            or (require_quantity and snapshot.get("showNum") != request.quantity)):
        raise RuntimeError("珍宝阁购买详情身份、数量、费用或进程未通过校验")


def purchase_spirit_artifact_body(
    context, execute, *, request: SpiritArtifactPurchaseRequest,
    assets: SpiritArtifactPurchaseAssets, evidence_path: Path, stop_at: float,
) -> dict[str, Any]:
    """Buy the explicitly authorized quantity in one transaction and prove delivery.

    Formal asset geometry owns coordinates. OCR finds the row; Runtime verifies
    goods/base/currency/model/price before the single confirmation. A failed
    observation after confirmation never retries the purchase. Evidence is appended
    before the action and includes full pre/post inventory plus currency sources.
    The caller must reconcile an uncertain receipt before invoking this again.
    """
    path = Path(evidence_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            previous = json.loads(line)
            if previous.get("event") == "purchase_confirmation_may_be_sent":
                raise RuntimeError("此采购记录已发送或可能发送过确认；先核对已有回执，禁止再次采购")
    sent = False

    def record(event, **fields):
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(event=event, at=time.time(), request=asdict(request),
                                         **fields), ensure_ascii=False, default=str) + "\n")

    def budget():
        if time.time() >= stop_at:
            raise TimeoutError("本体采购达到截止时间")

    def wait_scene(scene, *, seconds, label):
        actual = execute(context.wait_scene([scene], wait=seconds, label=label))
        if actual is None or int(actual) != scene:
            raise RuntimeError(f"{label}：实际场景 {actual!r} != #{scene}")

    def currency():
        counts, metadata = read_backpack_item_counts(
            [request.cost_item_id], manager_key="spirit-artifact-purchase-currency")
        return {"amount": counts[request.cost_item_id], **metadata}

    try:
        budget()
        if assets.quantity_assets is not None and assets.quantity_assets.settings_scene_id != assets.dialog_scene:
            raise ValueError("数量组件与购买详情资产不一致")
        cfg = load_spirit_artifact_wash_rules()["items_by_base_id"].get(request.base_id)
        if cfg is None or (cfg["type"], cfg["parts"], cfg["quality"]) != (request.ware_id, request.part, 6):
            raise ValueError("授权 base 与正式红本体灵器/部位配置不一致")
        wait_scene(assets.shop_scene, seconds=10, label="本体采购：确认珍宝阁")
        view = context.view(assets.shop_scene)
        container = view.get_shape(assets.list_shape)
        rows = [view.get_shape(name) for name in assets.row_shapes]
        if container is None or not rows or any(row is None for row in rows):
            raise RuntimeError("珍宝阁缺少正式商品列表/行资产")
        # This list's scene mask can make image-signature progress false while
        # actual products move. Compare local visible text, never the drag's bool.
        # Keep observations local so another search's cached seen-set cannot
        # falsely classify our first screen as an exhausted list.
        previous_keys = None
        unchanged = 0
        aligned = None
        box = container.box()
        for attempt in range(13):
            budget()
            wait_scene(assets.shop_scene, seconds=10, label="本体采购：查找商品场景")
            lines = group_ocr_tokens(context.full_frame_ocr_tokens(update=True))
            visible = [line for line in lines
                       if box['x'] <= line['x'] + line['w'] / 2 <= box['x'] + box['w']
                       and box['y'] <= line['y'] + line['h'] / 2 <= box['y'] + box['h']]
            keys = tuple(normalize_ocr_name(line.get('text')) for line in visible)
            # resolve_exchange_shop_item uses the same public normalization,
            # including middle-dot removal, and demands one exact product.
            if normalize_ocr_name(request.name) in keys:
                aligned = resolve_exchange_shop_item(
                    lines, product_list_box=box, product_row_boxes=[row.box() for row in rows],
                    expected_name=request.name,
                    expected_unit_price=request.unit_price if assets.row_shapes != (assets.list_shape,) else None)
                break
            unchanged = unchanged + 1 if keys and keys == previous_keys else 0
            record("purchase_search_observed", attempt=attempt, visible_keys=keys,
                   unchanged=unchanged)
            if unchanged >= 2 or attempt == 12:
                break
            previous_keys = keys
            # Keep the public gesture defaults already verified on this shop;
            # only replace its image-signature progress decision with text.
            context.drag_shape_content(container, direction="down")
            execute(context.wait_action_settle(0.8))
        if aligned is None:
            raise RuntimeError("珍宝阁有界文本查找未发现目标商品；未执行采购")
        budget()
        context.click_frame_point(assets.shop_scene, aligned.x, aligned.y)
        wait_scene(assets.dialog_scene, seconds=12, label="本体采购：购买详情")
        before = read_spirit_artifact_inventory_runtime()
        if before.get("complete") is not True:
            raise RuntimeError("采购前灵器库存不完整")
        identity = (before["pid"], before["process_start_ticks"])
        initial = read_common_shop_buy_dialog_snapshot()
        validate_spirit_artifact_purchase_dialog(initial, request, identity, require_quantity=False)
        adjusted = None
        if assets.quantity_assets is None:
            # With no supplied quantity-control asset, the current Runtime count
            # must already equal authorization; never guess unannotated controls.
            validate_spirit_artifact_purchase_dialog(initial, request, identity)
        else:
            adjusted = execute(set_verified_common_shop_quantity(
                context, request.quantity, unit_price=request.unit_price,
                label="灵器本体采购", assets=assets.quantity_assets, initial_snapshot=initial))
        cash_before = currency()
        final = read_common_shop_buy_dialog_snapshot()
        validate_spirit_artifact_purchase_dialog(final, request, identity)
        if ((cash_before["pid"], cash_before["process_start_ticks"]) != identity
                or cash_before["amount"] != final["HadPrice"]):
            raise RuntimeError("采购前背包货币与购买框余额不一致")
        record("purchase_ready", inventory=before, currency=cash_before, dialog=final,
               row=asdict(aligned), quantity_adjustment=adjusted)
        budget()
        record("purchase_confirmation_may_be_sent")
        sent = True
        # Direct single click: a wait/retry click helper cannot replay this purchase.
        context.click_shape_center(assets.dialog_scene, assets.confirm_shape)
        wait_scene(assets.shop_scene, seconds=15, label="本体采购：等待返回珍宝阁")
        after = read_spirit_artifact_inventory_runtime()
        cash_after = currency()
        record("purchase_observed", inventory=after, currency=cash_after)
        proof = verify_spirit_artifact_choice_outcome(
            before, after, reward=StorageBagChoiceReward(1, request.base_id, request.name, 1,
                                                       is_spirit_artifact=True), opened_count=request.quantity)
        new_rows = [row for row in after["items"] if row["item_id"] in proof.added_item_ids]
        if any((row["ware_id"], row["part"]) != (request.ware_id, request.part) for row in new_rows):
            raise RuntimeError("新增本体不属于授权灵器/部位")
        if len(new_rows) != request.quantity or any(row["quantity"] != 1 for row in new_rows):
            raise RuntimeError("采购交付未形成授权数量的独立本体 UID，禁止重购")
        if ((cash_after["pid"], cash_after["process_start_ticks"]) != identity
                or cash_before["amount"] - cash_after["amount"] != request.unit_price * request.quantity):
            raise RuntimeError("本体已观察但货币扣费未精确闭环，禁止重购")
        result = dict(status="completed", item_ids=list(proof.added_item_ids),
                      new_items=new_rows, inventory=after,
                      spent=request.unit_price * request.quantity, currency_before=cash_before,
                      currency_after=cash_after, inventory_proof=asdict(proof))
        record("purchase_completed", result=result)
        return result
    except Exception as exc:
        exc.purchase_confirmation_may_have_been_sent = sent
        record("purchase_stopped", confirmation_may_have_been_sent=sent,
               error_type=type(exc).__name__, error=str(exc))
        raise
