"""兼容旧入口；购买框数量控制由 Runtime/GUI 接口层提供。"""

from backend.core.fanxiu.runtime_gui.common_shop_quantity import (
    COMMON_SHOP_DIALOG_SCENE,
    COMMON_SHOP_QUANTITY_ASSETS,
    SACRED_SHOP_DIALOG_SCENE,
    SACRED_SHOP_QUANTITY_ASSETS,
    set_verified_common_shop_quantity,
)

__all__ = ['COMMON_SHOP_DIALOG_SCENE', 'COMMON_SHOP_QUANTITY_ASSETS', 'SACRED_SHOP_DIALOG_SCENE', 'SACRED_SHOP_QUANTITY_ASSETS', 'set_verified_common_shop_quantity']
