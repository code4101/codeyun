"""灵祖兑换：本周祈愿必兑，下一期额度保护，可选魂息保底 4000。"""
from backend.core.fanxiu.data_annotation.tasks.activity_purchase import ActivityPurchasePolicy
from backend.core.fanxiu.data_annotation.tasks.prayer_exchange import prayer_exchange_selection
from backend.core.fanxiu.data_annotation.effective_time import job_now

STAGE_ID='lingzu-exchange'
STAGE_VERSION='1'
PRAYER_GOODS={'洗灵':30009,'淬体':30005,'仙花':30006,'灵兽':30007,'炼丹':30008}
# Explicit authorized range, in native display order, ending at 炼丹灵草匣.
OFFERS={
    30029:(16001199,5000,50,1),30030:(16001199,10000,None,1),
    30025:(19070163,5000,None,3),30020:(19010194,4000,None,1),
    30021:(19010195,800,None,12),30026:(19010277,800,None,6),
    30023:(5050002,3000,None,1),30022:(5050001,1000,None,3),
    30010:(19070111,100,None,20),30009:(14000002,100,None,20),
    30005:(5030001,100,None,20),30006:(7020014,100,None,20),
    30007:(8022000,100,None,20),30008:(19070082,100,None,20),
}

class LingzuExchangePolicy(ActivityPurchasePolicy):
    def read_snapshot(self):
        from backend.core.fanxiu.instrumentation.lingzu_shop import read_lingzu_shop_snapshot
        return read_lingzu_shop_snapshot()

def lingzu_exchange_cycle(moment):
    return moment.date().isoformat() if moment.weekday() in (0,1) else None

def exchange_policy(moment):
    # Monday's pre-reset occurrence may use the expiring optional quota.
    # Other/manual runs protect next week's quota just like Tuesday.
    prayer,next_prayer,offers,optional,locked=prayer_exchange_selection(
        moment,PRAYER_GOODS,OFFERS,protect_next=moment.weekday()!=0)
    policy=LingzuExchangePolicy('灵祖兑换',844,188,'灵祖',9,147,offers,optional,4000,
        repeated_row_template=True,dialog_scene=634,dialog_close='关闭',dialog_confirm='兑换（高风险）',current_price_right_ratio=0.6)
    return prayer,next_prayer,locked,policy

def exchange_lingzu_resources(context, *, moment=None):
    import time
    moment=moment or job_now()
    prayer,next_prayer,locked,policy=exchange_policy(moment)
    ready=yield from context.wait_scene([844,188,187,184,183,34,69],wait=5,required=False)
    current=int(ready) if ready is not None else None
    if current not in (844,188,187,184,183):
        yield from context.go_scene(69)
        status=yield from context.open_daily_entry(label='灵祖兑换',title_pattern='灵祖',
            progress_can_mark_done=False,max_scrolls=30,initial_checks=2)
        if status!='open': raise RuntimeError('灵祖日常入口未打开')
        current=int((yield from context.wait_scene([183],wait=20)))
    if current==183:
        yield from context.wait_click(183,'灵祖挑战')
        current=int((yield from context.wait_scene([184],wait=15)))
    if current==184:
        yield from context.wait_click(184,'前往')
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            match=yield from context.wait_scene([187,188],wait=5,required=False)
            if match is not None and int(match) in (187,188):
                current=int(match)
                break
            yield from context.wait_action_settle(1)
        else: raise RuntimeError('灵祖寻路未到达战灵长老')
    if current==187:
        # NPC arrival can expose its identity before the option accepts input.
        # Retry only the idempotent navigation, only while still on this NPC.
        for _ in range(2):
            yield from context.wait_action_settle(0.8)
            yield from context.wait_click(187,'灵祖挑战')
            current=int((yield from context.wait_scene([188],wait=15)))
            if current==188: break
            if current!=187: raise RuntimeError('灵祖 NPC 点击后出现非预期页面')
        if current!=188: raise RuntimeError('灵祖 NPC 入口未响应')
    if current==188:
        yield from context.wait_click(188,'兑换商店')
    result=yield from policy.buy_current_shop(context)
    yield from context.go_scene(34)
    if int((yield from context.wait_scene([34],wait=20)))!=34:
        raise RuntimeError('灵祖兑换未返回世界')
    return {**result,'prayer':prayer,'next_prayer':next_prayer,'locked_goods':locked,'final_scene':34}
