"""仙府建筑等级升级；日周期凭证由现有养成集持有。"""
STAGE_ID = 'xianfu-building-upgrade'
STAGE_VERSION = '1'


def upgrade_xianfu_buildings(context):
    from backend.core.fanxiu.runtime_gui.xianfu_building import upgrade_xianfu_building_list
    return (yield from upgrade_xianfu_building_list(context))
