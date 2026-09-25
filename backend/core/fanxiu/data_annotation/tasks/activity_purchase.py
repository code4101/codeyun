"""活动兑换通用执行器：商品白名单、必买优先、可选预算与资源预留。"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActivityPurchasePolicy:
    label: str
    shop_scene: int
    entry_scene: int
    entry_pattern: str
    shop_base_id: int
    currency: int
    offers: dict[int, tuple[int, int, int | None, int]]
    optional_goods: frozenset[int] = field(default_factory=frozenset)
    reserve: int = 0
    repeated_row_template: bool = False

    @property
    def ordered_goods(self):
        """Mandatory offers always precede optional offers; retain priority within each."""
        return [g for g in self.offers if g not in self.optional_goods] + [
            g for g in self.offers if g in self.optional_goods]

    def authorized_rows(self, snapshot):
        """Require complete shop identity and exact authorized config/counts."""
        if (snapshot.get('complete') is not True or snapshot.get('shop_base_id') != self.shop_base_id
                or snapshot.get('currency_types') != [self.currency]):
            raise ValueError('活动兑换商店身份不符')
        rows = {}
        for row in snapshot['items']:
            gid = row['goods_id']
            if gid not in self.offers:
                continue
            if gid in rows or (row['item_id'], row['token_cost'], row.get('discount'),
                               row['purchase_limit']) != self.offers[gid] or row['currency_type'] != self.currency:
                raise ValueError('活动商品配置变化或重复')
            count = row['purchased_count']
            if type(count) is not int or not 0 <= count <= row['purchase_limit']:
                raise ValueError('活动已购计数无效')
            rows[gid] = row
        if set(rows) != set(self.offers):
            raise ValueError('活动目标商品缺失')
        return rows


    def plan(self, snapshot, balance=None):
        """Reserve applies to optional full-price purchases, after mandatory offers.

        Insufficient funds for mandatory offers fail instead of recording completion.
        Exactly 20000 remaining permits one optional item, leaving exactly 10000.
        """
        rows = self.authorized_rows(snapshot)
        if balance is None and not self.optional_goods:
            balance = sum((r['purchase_limit']-r['purchased_count'])*r['token_cost'] for r in rows.values())
        if type(balance) is not int or balance < 0:
            raise ValueError('活动兑换货币余额无效')
        actions = []
        for gid in self.ordered_goods:
            row = rows[gid]
            quantity = row['purchase_limit'] - row['purchased_count']
            if gid in self.optional_goods:
                quantity = min(quantity, max(0, balance - self.reserve) // row['token_cost'])
            cost = quantity * row['token_cost']
            if cost > balance:
                raise ValueError('兑换货币不足以购买必买项')
            if quantity:
                actions.append({**row, 'quantity': quantity})
                balance -= cost
        return actions


    def validate_dialog(self, dialog, row, quantity=None):
        gid = row['goods_id']
        if gid not in self.offers or not dialog.get('complete') or not dialog.get('identity_complete'):
            raise ValueError('活动兑换框身份不完整')
        item, price, _, _ = self.offers[gid]
        if (dialog.get('goods_id'), dialog.get('item_id'), dialog.get('cost_item_id'),
                dialog.get('Price')) != (gid, item, self.currency, price):
            raise ValueError('活动兑换框商品、币种或价格不符')
        if quantity is not None:
            if (not 0 < quantity <= dialog['maxNum'] or dialog['showNum'] != quantity
                    or not dialog.get('CanBuy') or not dialog.get('isEnough')):
                raise ValueError('活动兑换数量或可购买状态不符')
            remaining = dialog['HadPrice'] - quantity * price
            if remaining < (self.reserve if gid in self.optional_goods else 0):
                raise ValueError('活动兑换将突破余额门禁')


    def open_offer(self, context, row, snapshot):
        """Locate candidates with shared Runtime-GUI geometry; validate the opened dialog.

        Scroll only before opening. Confirmation is never retried here.
        Repeated-row pages derive card positions from the annotated template;
        fixed-row pages use their existing row Shapes.
        """
        from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens
        from backend.core.fanxiu.runtime_gui.exchange_shop import (
            resolve_exchange_shop_item, resolve_ordered_exchange_candidate)
        from backend.core.fanxiu.instrumentation.common_shop_buy_dialog import read_common_shop_buy_dialog_snapshot

        def locate():
            tokens = tuple(context.ocr_tokens_in_shapes(self.shop_scene, ('商品列表',), crop=True))
            lines = tuple(group_ocr_tokens(tokens))
            container = context.shape(self.shop_scene,'商品列表').box()
            if self.repeated_row_template:
                template = context.shape(self.shop_scene,'商品列表/商品模板').box()
                name_box = context.shape(self.shop_scene,'商品列表/商品模板/名称').box()
                offset = name_box['y']-template['y']
                boxes = [dict(x=template['x'],y=line['y']-offset,w=template['w'],h=template['h'])
                         for line in lines if str(line.get('text','')).replace(' ','')==row['name']]
            else:
                boxes = [context.shape(self.shop_scene,f'商品行{i}').box() for i in range(1,6)]
            try:
                return resolve_exchange_shop_item(
                    (*lines, *(t for t in tokens if str(t.get('text','')).isdigit())),
                    product_list_box=container,product_row_boxes=boxes,
                    expected_name=row['name'],expected_unit_price=row['token_cost'])
            except RuntimeError:
                if not self.repeated_row_template:
                    raise
                return resolve_ordered_exchange_candidate(lines,items=snapshot['items'],
                    goods_id=row['goods_id'],product_list_box=container,row_height=template['h'])

        try:
            target = locate()
        except RuntimeError:
            # Reset a retained scroll position, then traverse the bounded list.
            for _ in range(8):
                yield from context.scroll_shape_content(self.shop_scene,'商品列表',direction='up')
            for scan in range(9):
                try:
                    target = locate()
                    break
                except RuntimeError:
                    if scan == 8:
                        raise
                    yield from context.scroll_shape_content(self.shop_scene,'商品列表',direction='down')
        context.click_frame_point(self.shop_scene,target.x,target.y)
        if int((yield from context.wait_scene([566],wait=12))) != 566:
            raise RuntimeError('活动商品未打开购买框')
        dialog = read_common_shop_buy_dialog_snapshot()
        self.validate_dialog(dialog,row)
        return dialog

    def run(self, context):
        """Run from current shop or stable daily entry; verify counts and return #34.

        Balance comes from the active purchase dialog (wallet currency is not a
        backpack item). Confirmation is sent once; uncertain results fail closed.
        """
        from backend.core.fanxiu.instrumentation.activity_shop import collect_activity_shop_runtime
        from backend.core.fanxiu.data_annotation.tasks.common_shop_quantity import set_verified_common_shop_quantity
        ready = yield from context.wait_scene([self.shop_scene,self.entry_scene,34,69], wait=5, required=False)
        current = int(ready) if ready is not None else None
        if current != self.shop_scene:
            if current != self.entry_scene:
                yield from context.go_scene(69)
                status = yield from context.open_daily_entry(label=self.label,title_pattern=self.entry_pattern,
                    progress_can_mark_done=False,max_scrolls=30,initial_checks=2)
                if status != 'open':
                    raise RuntimeError('未找到活动入口')
            if int((yield from context.wait_scene([self.entry_scene],wait=30))) != self.entry_scene:
                raise RuntimeError('未进入活动争锋')
            yield from context.wait_click(self.entry_scene,'兑换宝阁')
        if int((yield from context.wait_scene([self.shop_scene],wait=15))) != self.shop_scene:
            raise RuntimeError('未进入活动兑换宝阁')
        result = yield from self.buy_current_shop(context)
        yield from context.go_scene(34)
        if int((yield from context.wait_scene([34],wait=15))) != 34:
            raise RuntimeError('活动兑换未返回世界')
        return {**result, 'final_scene':34}

    def buy_current_shop(self, context):
        """Complete one already-open tab; caller owns navigation and weekly receipts."""
        from backend.core.fanxiu.instrumentation.activity_shop import collect_activity_shop_runtime
        from backend.core.fanxiu.data_annotation.tasks.common_shop_quantity import set_verified_common_shop_quantity
        if int((yield from context.wait_scene([self.shop_scene],wait=15))) != self.shop_scene:
            raise RuntimeError('未进入目标兑换页')
        snapshot = collect_activity_shop_runtime(shop_base_id=self.shop_base_id, expected_currency_type=self.currency)
        purchases = []
        balance = None
        while True:
            rows = self.authorized_rows(snapshot)
            remaining = [rows[g] for g in self.ordered_goods if rows[g]['purchased_count'] < rows[g]['purchase_limit']]
            if not remaining:
                break
            row = remaining[0]
            dialog = yield from self.open_offer(context, row, snapshot)
            balance = int(dialog['HadPrice'])
            actions = self.plan(snapshot, balance)
            if not actions:
                yield from context.wait_click(566,'关闭详情')
                if int((yield from context.wait_scene([self.shop_scene],wait=10))) != self.shop_scene:
                    raise RuntimeError('未关闭活动兑换框')
                break
            action = actions[0]
            if action['goods_id'] != row['goods_id']:
                # An expensive optional offer may not fit, while a later one does.
                yield from context.wait_click(566,'关闭详情')
                if int((yield from context.wait_scene([self.shop_scene],wait=10))) != self.shop_scene:
                    raise RuntimeError('未关闭预算查询兑换框')
                row = rows[action['goods_id']]
                dialog = yield from self.open_offer(context,row,snapshot)
                balance = int(dialog['HadPrice'])
                refreshed = self.plan(snapshot,balance)
                if not refreshed or refreshed[0]['goods_id'] != row['goods_id']:
                    raise RuntimeError('兑换预算已改变，需重新观察')
                action = refreshed[0]
            quantity = action['quantity']
            if dialog['showNum'] != quantity:
                proof = yield from set_verified_common_shop_quantity(context,quantity,
                    unit_price=row['token_cost'],label=self.label,initial_snapshot=dialog)
                dialog = proof['snapshot']
            self.validate_dialog(dialog,row,quantity)
            context.click_shape_center(566,'购买')
            if int((yield from context.wait_scene([self.shop_scene],wait=15))) != self.shop_scene:
                raise RuntimeError('活动兑换已发送但落点不明，禁止重发')
            snapshot = collect_activity_shop_runtime(shop_base_id=self.shop_base_id,expected_currency_type=self.currency)
            after = self.authorized_rows(snapshot)[row['goods_id']]
            if after['purchased_count'] != row['purchased_count'] + quantity:
                raise RuntimeError('活动兑换已发送但计数未确认，禁止重发')
            balance -= quantity * row['token_cost']
            purchases.append({'goods_id':row['goods_id'],'quantity':quantity})
        return {'result':'success','outcome':'complete','purchases':purchases,
                'balance':balance,'reserve':self.reserve}
