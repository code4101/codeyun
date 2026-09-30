"""动作与 OCR 共用的层级 Shape 选择器；缺失和歧义不是业务阴性。"""
from pyxllib.autogui import Shape, View


def resolve_shape_path(view: View, selector: str) -> Shape:
    parts = [part.strip() for part in selector.strip().strip("[]").split("/") if part.strip()]
    if not parts:
        raise RuntimeError("shape 选择器为空")
    candidates = [shape for shape in view.get_shapes(include_descendants=len(parts) == 1)
                  if shape.title == parts[0]]
    for title in parts[1:]:
        candidates = [child for parent in candidates for child in parent.children()
                      if child.title == title]
    if not candidates:
        raise RuntimeError(f"shape 选择器 [{'/'.join(parts)}] 未命中")
    if len(candidates) != 1:
        raise RuntimeError(f"shape 选择器 [{'/'.join(parts)}] 命中多个目标，请使用更精确路径")
    return candidates[0]
