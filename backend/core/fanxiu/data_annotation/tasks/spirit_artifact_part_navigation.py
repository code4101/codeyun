"""固定六部件顺序与当前 OCR 的纯定位；不访问游戏或 Runtime。"""
from ..ocr_spatial import find_text_matches, select_text_match


def locate_spirit_artifact_part(tokens, names, part_id):
    """返回（点击点、滚动方向）。漏字仅在同排等距锚点之间内插。

    无锚点、歧义、顺序/行距不符时拒绝猜测；不在可见范围才建议滚动。
    """
    if len(names) != 6 or len(set(names)) != 6 or part_id not in range(1, 7):
        raise ValueError('灵器六部件名称或目标编号无效')
    anchors = []
    for index, name in enumerate(names, 1):
        match = select_text_match(find_text_matches(tokens, name), name)
        if match is not None:
            anchors.append((index, *match.point(), match.h))
    if not anchors:
        raise ValueError('部件列表 OCR 无已知名称')
    tolerance = min(a[3] for a in anchors) / 2
    if max(a[2] for a in anchors) - min(a[2] for a in anchors) > tolerance:
        raise ValueError('部件名称 OCR 不在同一行')
    if any(a[1] >= b[1] for a, b in zip(anchors, anchors[1:])):
        raise ValueError('部件名称 OCR 顺序与配置不符')
    for index, x, y, _ in anchors:
        if index == part_id:
            return (x, y), None
    first, last = anchors[0], anchors[-1]
    if part_id < first[0]:
        return None, 'left'
    if part_id > last[0]:
        return None, 'right'
    spacing = (last[1] - first[1]) / (last[0] - first[0])
    if any(abs(x - (first[1] + (index - first[0]) * spacing)) > tolerance
           for index, x, _, _ in anchors):
        raise ValueError('部件名称 OCR 不满足等间距，不能内插')
    return (first[1] + (part_id - first[0]) * spacing,
            sum(a[2] for a in anchors) / len(anchors)), None
