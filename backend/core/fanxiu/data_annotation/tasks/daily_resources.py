"""日常资源玩法的兼容组合入口。

供奉、仙盟、仙市与 VIP 各自拥有业务流程。新调用应依赖对应模块，
本类仅为现有执行器保留组合关系，不新增玩法实现。
"""
from .gongfeng import DailyGongfengTaskMixin
from .xianmeng import DailyXianmengTaskMixin
from .xianshi_resources import XianshiResourceTaskMixin
from .vip import DailyVipTaskMixin


class DailyResourceTaskMixin(
    DailyGongfengTaskMixin,
    DailyXianmengTaskMixin,
    XianshiResourceTaskMixin,
    DailyVipTaskMixin,
):
    pass
