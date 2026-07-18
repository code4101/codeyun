from __future__ import annotations


# 这些编号只在仙缘业务步骤调用 current_scene(...) 时构成 Layer 0。
# 资产本身仍按场景身份规则派生到默认 Layer 1/2；其中 #201-#203 是 Layer 2。
DAILY_XIANYUAN_LAYER0_SCENE_IDS = (203, 202, 201, 200, 199, 198, 197, 69, 34)
DAILY_XIANYUAN_RETURN_LAYER0_SCENE_IDS = tuple(reversed(DAILY_XIANYUAN_LAYER0_SCENE_IDS))
DAILY_XIANYUAN_CHALLENGE_LAYER0_SCENE_IDS = frozenset({200, 201, 202, 203})
