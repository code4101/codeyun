"""Observe the current RevenuePointsRedemption page without purchase actions."""
from datetime import datetime

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    active_ui_component_objects, has_ui_object_fields, read_ui_object_field,
    read_ui_runtime_snapshot, read_ui_selected_tab_panel,
)


def read_revenue_exchange_page_snapshot(activity_id: int) -> dict:
    """Return native displayed groups, cohort goods and currency identity.

    A group can contain several price/limit tiers for the same displayed
    product. Preserve their exact native order rather than aggregating prices
    or inventing an exchange priority. This observation grants no spend budget.
    """
    if type(activity_id) is not int or activity_id <= 0:
        raise ValueError('兑换页需要精确活动ID')

    def observe(ctx):
        candidates = set()
        for obj in active_ui_component_objects(ctx):
            address = obj.address
            for _ in range(4):
                if (as_int(read_ui_object_field(ctx, address, 'V_ActivityId')) == activity_id
                        and has_ui_object_fields(ctx, address, {'shopItem', 'scrollView', 'numTF', 'V_WalletType'})):
                    candidates.add(address)
                selected = read_ui_selected_tab_panel(ctx, address)
                if selected is None:
                    break
                address = selected[0]
        if len(candidates) != 1:
            raise FanxiuRuntimeMemoryError('当期兑换宝阁未唯一打开', code='data_not_loaded')
        address = next(iter(candidates))
        currency = as_int(read_ui_object_field(ctx, address, 'V_WalletType'))
        groups, count = ctx.reader.list_items(read_ui_object_field(ctx, address, 'V_ShowList'))
        if not currency or count is None or not 0 < count == len(groups) <= 1000:
            raise FanxiuRuntimeMemoryError('兑换页币种或商品组不完整', code='runtime_incomplete')
        result, seen = [], set()
        for index, group in enumerate(groups, start=1):
            values, declared = ctx.reader.list_items(group)
            if declared is None or not 0 < declared == len(values) <= 100:
                raise FanxiuRuntimeMemoryError('兑换商品分档不完整', code='runtime_incomplete')
            tiers = []
            for value in values:
                row = ctx.reader.fields(value)
                numeric = {key: as_int(row.get(key)) for key in (
                    'goodsId', 'itemId', 'goodsNum', 'buyPrice', 'currencyType', 'limitBuy', 'limitTimes',
                )}
                if (any(v is None for v in numeric.values()) or numeric['goodsId'] <= 0
                        or numeric['itemId'] <= 0 or numeric['goodsNum'] <= 0 or numeric['buyPrice'] < 0
                        or numeric['currencyType'] != currency or numeric['goodsId'] in seen):
                    raise FanxiuRuntimeMemoryError('兑换商品身份、价格或币种不一致', code='runtime_incomplete')
                seen.add(numeric['goodsId'])
                tiers.append({**numeric, **{key: row.get(key) for key in (
                    'reward', 'buyCondition', 'showLimit', 'disappearLimit', 'discount',
                )}})
            result.append({'display_position': index, 'tiers': tiers})
        return {'activity_id': activity_id, 'currency_type': currency, 'complete': True,
                'read_only': True, 'spend_authorized': False, 'groups': result,
                'captured_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                'source': 'activeRevenuePointsRedemption.V_ShowList',
                'evidence': {'pid': ctx.binding.pid, 'process_start_ticks': ctx.binding.process_start_ticks}}

    try:
        return read_ui_runtime_snapshot((), observe, fast=True)
    except (FanxiuRuntimeMemoryError, OSError, ValueError) as exc:
        return {'activity_id': activity_id, 'complete': False, 'read_only': True,
                'spend_authorized': False, 'reason': str(exc),
                'code': getattr(exc, 'code', 'runtime_unavailable')}
