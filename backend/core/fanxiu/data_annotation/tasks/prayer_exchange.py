"""Weekly prayer exchange priorities shared by currency shops."""
from backend.core.fanxiu.prayer_cycle import current_prayer_cycle, next_prayer_cycle

def prayer_exchange_selection(moment, prayer_goods, offers, *, protect_next=True):
    """Current prayer is mandatory; protected next prayer never enters optional budget.

    This reserves a purchase quota, independently of the wallet reserve.
    Exclusion happens before planning, so surplus currency cannot unlock it.
    """
    current=current_prayer_cycle(moment)
    upcoming=next_prayer_cycle(moment)
    mandatory=prayer_goods[current]
    locked=prayer_goods[upcoming] if protect_next else None
    selected={gid:cfg for gid,cfg in offers.items() if gid!=locked}
    if mandatory not in selected:
        raise ValueError('本周祈愿商品不在兑换清单中')
    return current, upcoming, selected, frozenset(set(selected)-{mandatory}), locked
