from datetime import datetime,timedelta
import pytest
from backend.core.fanxiu.data_annotation.tasks.fengmosha_exchange import (
    exchange_policy, fengmosha_exchange_cycle, PRAYER_OFFERS)


def snapshot(policy):
    return dict(complete=True,shop_base_id=230000,currency_types=[221],items=[
        dict(goods_id=g,item_id=i,token_cost=p,discount=d,purchase_limit=l,
             purchased_count=0,currency_type=221)
        for g,(i,p,d,l) in policy.offers.items()])


def test_prayer_is_dynamic_across_five_weeks():
    names=set()
    for week in range(5):
        name,policy=exchange_policy(datetime(2026,9,21)+timedelta(weeks=week))
        names.add(name)
        mandatory=set(policy.offers)-policy.optional_goods
        assert mandatory=={PRAYER_OFFERS[name][0]}
    assert names==set(PRAYER_OFFERS)


def test_mandatory_ignores_reserve_optional_obeys_it():
    _,policy=exchange_policy(datetime(2026,9,25))
    data=snapshot(policy)
    assert [(a['goods_id'],a['quantity']) for a in policy.plan(data,2400)]==[(8230014,10)]
    assert [(a['goods_id'],a['quantity']) for a in policy.plan(data,20400)]==[(8230014,10),(8230001,1)]
    assert sum(a['quantity']*a['token_cost'] for a in policy.plan(data,275730))==62900
    with pytest.raises(ValueError): policy.plan(data,2399)
    for r in data['items']: r['purchased_count']=r['purchase_limit']
    assert policy.plan(data,212830)==[]


def test_two_separate_completion_keys():
    assert fengmosha_exchange_cycle(datetime(2026,9,21))=='2026-09-21'
    assert fengmosha_exchange_cycle(datetime(2026,9,22))=='2026-09-22'
    assert fengmosha_exchange_cycle(datetime(2026,9,23)) is None


def test_or_cross_conditions_do_not_claim_two_active_groups():
    from backend.core.fanxiu.instrumentation.activity_shop import _infer_show_list_cross_count
    row=[None]*17
    row[11]='EqualCrossGroup|230000_32;EqualCrossGroup|230000_64'
    assert _infer_show_list_cross_count([[row]],shop_base_id=230000) is None
    other=list(row);other[11]='EqualCrossGroup|230000_64'
    assert _infer_show_list_cross_count([[row],[other]],shop_base_id=230000)==64
