"""Runtime 与画面的语义接口：定位业务对象、验证身份、执行控件动作。

通用对齐与文字匹配从本包导入；具体适配器按业务意图从子模块导入：
mail_window 对齐邮件身份，integer_count_control 设置整数控件，
common_shop_quantity 调整购买数量并验证购买前证据。
适配器拥有坐标、OCR 和反馈细节；Task 保留业务策略、授权与完成判据。
只读解析函数不采集游戏状态；执行函数的场景前置与副作用见各自契约。
"""

from backend.core.fanxiu.runtime_gui.alignment import (
    GuiCandidate,
    RuntimeEntity,
    RuntimeEvidenceValidation,
    RuntimeGuiAlignment,
    RuntimeGuiMapping,
    RuntimeGuiPairEvidence,
    align_runtime_gui_candidates,
    score_runtime_gui_pair,
    validate_runtime_evidence,
)
from backend.core.fanxiu.runtime_gui.text import (
    DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD,
    OcrNameMatch,
    best_ocr_name_match,
    normalize_ocr_name,
    ocr_name_similarity,
    rank_ocr_name_matches,
)
from backend.core.fanxiu.runtime_gui.world_menu import (
    MenuAnchorEvidence,
    OrderedMenuGrid,
    WorldMenuClickPlan,
    plan_world_menu_click,
    verify_world_menu_successor,
)
from backend.core.fanxiu.runtime_gui.storage_bag_grid import (
    StorageBagGapMatch,
    StorageBagGrid,
    StorageBagViewport,
    register_storage_bag_viewport,
)
from backend.core.fanxiu.runtime_gui.storage_bag_alignment import (
    StorageBagItemClickPlan,
    StorageBagDetailVerification,
    StorageBagScrollDirective,
    StorageBagTarget,
    StorageBagQuantityObservation,
    StorageBagVisibleCell,
    plan_storage_bag_item_click,
    plan_storage_bag_scroll,
    prepare_storage_bag_target,
    prepare_storage_bag_target_by_name,
    quantity_observations_from_ocr,
    register_storage_bag_viewport_from_quantity_ocr,
    upscaled_ocr_fragments,
    verify_storage_bag_item_detail,
    visible_storage_bag_cells,
)
from backend.core.fanxiu.runtime_gui.sacred_exchange import (
    plan_sacred_exchange_item_click,
    sacred_exchange_quantity_observations,
    visible_sacred_exchange_rows,
)
from backend.core.fanxiu.runtime_gui.activity_bottom_tab import (
    VerticalBottomTabTarget,
    resolve_vertical_bottom_tab,
)
from backend.core.fanxiu.runtime_gui.magic_invasion import (
    MagicInvasionBottomTabTarget,
    resolve_magic_invasion_bottom_tab,
)
from backend.core.fanxiu.runtime_gui.exchange_shop import (
    ExchangeShopItemTarget,
    resolve_exchange_shop_item,
)

__all__ = [
    "DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD",
    "GuiCandidate",
    "ExchangeShopItemTarget",
    "OcrNameMatch",
    "MenuAnchorEvidence",
    "MagicInvasionBottomTabTarget",
    "OrderedMenuGrid",
    "RuntimeEntity",
    "RuntimeEvidenceValidation",
    "RuntimeGuiAlignment",
    "RuntimeGuiMapping",
    "RuntimeGuiPairEvidence",
    "StorageBagGapMatch",
    "StorageBagGrid",
    "StorageBagItemClickPlan",
    "StorageBagDetailVerification",
    "StorageBagScrollDirective",
    "StorageBagTarget",
    "StorageBagQuantityObservation",
    "StorageBagVisibleCell",
    "StorageBagViewport",
    "VerticalBottomTabTarget",
    "WorldMenuClickPlan",
    "align_runtime_gui_candidates",
    "best_ocr_name_match",
    "normalize_ocr_name",
    "ocr_name_similarity",
    "plan_world_menu_click",
    "plan_storage_bag_item_click",
    "plan_storage_bag_scroll",
    "prepare_storage_bag_target",
    "prepare_storage_bag_target_by_name",
    "plan_sacred_exchange_item_click",
    "sacred_exchange_quantity_observations",
    "quantity_observations_from_ocr",
    "register_storage_bag_viewport_from_quantity_ocr",
    "upscaled_ocr_fragments",
    "rank_ocr_name_matches",
    "resolve_magic_invasion_bottom_tab",
    "resolve_exchange_shop_item",
    "resolve_vertical_bottom_tab",
    "register_storage_bag_viewport",
    "score_runtime_gui_pair",
    "validate_runtime_evidence",
    "verify_world_menu_successor",
    "verify_storage_bag_item_detail",
    "visible_storage_bag_cells",
    "visible_sacred_exchange_rows",
]
