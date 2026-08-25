"""仙窍试炼每日购买次数的纯业务模型。

本模块只回答“今天已经买了几次、还要不要买”，不负责截图、OCR 或点击。
界面编排由 :class:`BehaviorTreeRuntime` 完成。

当前规则是每天最多购买三次，依次显示 100、150、200 灵石。调用方传入的
``target_daily_purchases`` 表示“当天累计希望购买几次”，而不是“本次调用
再点击几次”。例如目标为 2 时，可以购买 100、150 两档；看到 200 时应
返回，因为这说明当天累计已买 2 次。
"""

from __future__ import annotations


XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES = (100, 150, 200)


def normalize_xianqiao_trial_purchase_target(target_daily_purchases: int) -> int:
    """校验并返回仙窍试炼当天累计购买目标。"""

    target = int(target_daily_purchases)
    maximum = len(XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES)
    if target < 0 or target > maximum:
        raise ValueError(f"仙窍试炼当天购买目标必须在 0..{maximum}，实际为 {target}")
    return target


def purchases_completed_before_price(price: int) -> int:
    """由 #363 当前价格反推今天已经完成的购买次数。

    价格是比“剩余次数”更直接的动作门槛：100/150/200 分别表示已购买
    0/1/2 次。未知价格不做猜测，避免 OCR 错误导致误消费灵石。
    """

    price = int(price)
    try:
        return XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES.index(price)
    except ValueError as exc:
        expected = ", ".join(map(str, XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES))
        raise ValueError(f"无法由价格 {price} 判断仙窍试炼购买档位；预期 {expected}") from exc
