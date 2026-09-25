"""玩法榜尾日规划接入活动通用购买器；不另实现点击、数量或确认循环。"""
from dataclasses import dataclass, field
from .activity_purchase import ActivityPurchasePolicy
from .exchange_tail_planning import plan_exchange_tail_purchases


@dataclass(frozen=True)
class GameplayExchangePurchasePolicy(ActivityPurchasePolicy):
    refresh_detail: object = None
    unlimited_receipts: dict = field(default_factory=dict)

    def read_snapshot(self):
        detail = self.refresh_detail()
        if not (detail.exchange_plan or {}).get('budget_ready'):
            raise RuntimeError('玩法榜兑换缺少同窗口最新钱包与商品事实')
        items = []
        for row in detail.shop_items:
            data = row.model_dump()
            data['currency_type'] = self.currency
            data['unlimited'] = row.purchase_limit < 0
            if data['unlimited']:
                data['purchased_count'] = self.unlimited_receipts.get(row.goods_id, row.purchased_count)
            # 无限库存以本次已分配预算形成有限购买上限。
            if row.goods_id in self.offers:
                cap = self.offers[row.goods_id][3]
                if row.purchase_limit >= 0 and cap > row.purchase_limit:
                    raise RuntimeError('玩法榜商品库存已变化')
                data['purchase_limit'] = cap
            items.append(data)
        return dict(complete=True, shop_base_id=self.shop_base_id,
                    currency_types=[self.currency], items=items, balance=detail.current_currency)

    def confirm_purchase(self, row, quantity, balance):
        snapshot = self.read_snapshot()
        if snapshot['balance'] != balance-quantity*row['token_cost']:
            raise RuntimeError('玩法榜购买后钱包未闭环，禁止重发')
        after = next(r for r in snapshot['items'] if r['goods_id'] == row['goods_id'])
        expected = row['purchased_count']+quantity
        if row['unlimited']:
            # Unlimited rows may never expose a bought counter. The verified
            # dialog identity and exact wallet delta prove this attempt's buy.
            # On reentry the real remaining wallet replans the allocation.
            self.unlimited_receipts[row['goods_id']] = expected
            after['purchased_count'] = expected
        elif after['purchased_count'] != expected:
            raise RuntimeError('玩法榜购买后计数未闭环，禁止重发')
        return snapshot

    def validate_dialog(self, dialog, row, quantity=None):
        super().validate_dialog(dialog, row, quantity)
        if quantity is not None and dialog['HadPrice']-quantity*row['token_cost'] < self.reserve:
            raise RuntimeError('玩法榜兑换将突破锁定资源保留额')


def redeem_gameplay_exchange_shop(context, *, refresh_detail, run_date,
                                 shop_scene, shop_base_id, currency, label):
    """Allocate by saved priority, then reuse the common dialog-identity executor."""
    detail = refresh_detail()
    purchases, locked, planning = plan_exchange_tail_purchases(detail, run_date=run_date, label=label)
    rows = {row.goods_id: row for row in detail.shop_items}
    # Allocation has already honored priority and reserves. Traverse selected
    # goods in native order; cap each offer at exactly its allocated quantity.
    ordered = sorted(purchases, key=lambda p: p.source_order)
    offers = {p.goods_id: (rows[p.goods_id].item_id, p.unit_price,
                         rows[p.goods_id].discount,
                         rows[p.goods_id].purchased_count+p.quantity) for p in ordered}
    if not offers:
        return dict(purchases=[], balance=detail.current_currency, planning=planning,
                    retained_locked_goods_ids=sorted(locked))
    policy = GameplayExchangePurchasePolicy(label, shop_scene, shop_scene, '',
        shop_base_id, currency, offers, reserve=planning['reserved_tokens'],
        refresh_detail=refresh_detail)
    result = yield from policy.buy_current_shop(context)
    final = refresh_detail()
    remaining, locked, planning = plan_exchange_tail_purchases(final, run_date=run_date, label=label)
    if remaining:
        raise RuntimeError('玩法榜兑换仍有可执行购买计划')
    return {**result, 'balance':final.current_currency, 'planning':planning,
            'retained_locked_goods_ids':sorted(locked)}
