"""秘境封魔杀兑换：动态本周祈愿必兑；可选兑换固定预留一万积分。"""
from backend.core.fanxiu.data_annotation.tasks.activity_purchase import ActivityPurchasePolicy
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.prayer_cycle import current_prayer_cycle

STAGE_ID = 'fengmosha-exchange'
STAGE_VERSION = '1'
SHOP_SCENE = 843
# name -> goods id, item id. Pack quantities remain Runtime facts.
PRAYER_OFFERS = {
    '淬体': (8230010,5030001), '仙花': (8230011,7020014),
    '灵兽': (8230012,8022000), '炼丹': (8230013,19070082),
    '洗灵': (8230014,14000002),
}
OPTIONAL_OFFERS = {
    8230019:(39036225,32000,60,1),
    8230001:(19010207,8000,None,2),
    8230004:(19010200,12500,None,1),
}


def fengmosha_exchange_cycle(moment):
    """Two independent occurrences, Monday and Tuesday 00:00 parent runs.

    Monday captures old shop quota with the new prayer target; Tuesday revisits
    refreshed quota. Exact shop reset hour is not assumed or used to fabricate
    counts; fresh Runtime remaining limits govern every attempt.
    """
    return moment.date().isoformat() if moment.weekday() in (0,1) else None


def exchange_policy(moment):
    """Resolve the shared prayer cycle on each business occurrence, never cache it."""
    prayer = current_prayer_cycle(moment)
    goods,item = PRAYER_OFFERS[prayer]
    return prayer, ActivityPurchasePolicy('秘境封魔杀兑换',SHOP_SCENE,477,'秘境封魔杀',
        230000,221,{goods:(item,240,None,10),**OPTIONAL_OFFERS},
        frozenset(OPTIONAL_OFFERS),10000,repeated_row_template=True)


def exchange_fengmosha_resources(context, *, moment=None):
    from backend.core.fanxiu.data_annotation.schedule_navigation import select_schedule_activity
    moment = moment or job_now()
    prayer,policy = exchange_policy(moment)
    ready = yield from context.wait_scene([SHOP_SCENE,477,66,34],wait=5,required=False)
    current = int(ready) if ready is not None else None
    if current != SHOP_SCENE:
        if current != 477:
            yield from context.go_scene(66)
            yield from select_schedule_activity(context,r'秘境封魔杀',enter=True,
                allow_unique_runtime_card_with_bad_time_ocr=True)
        if int((yield from context.wait_scene([477],wait=20))) != 477:
            raise RuntimeError('秘境封魔杀入口未确认')
        yield from context.wait_click(477,'兑换商店')
    result = yield from policy.buy_current_shop(context)
    yield from context.go_scene(34)
    if int((yield from context.wait_scene([34],wait=15))) != 34:
        raise RuntimeError('封魔杀兑换未返回世界')
    return {**result,'prayer':prayer,'final_scene':34}
