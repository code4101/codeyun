"""固定六部件顺序与当前 OCR 的纯定位；不访问游戏或 Runtime。"""
from ..ocr_spatial import find_text_matches, select_text_match


def locate_spirit_artifact_part(tokens, names, part_id):
    """返回（点击点、滚动方向）。漏字按同排等距锚点推算。

    末端外推须有同列阶数标识。无锚点或顺序/行距不符时拒绝猜测。
    """
    if len(names) != 6 or len(set(names)) != 6 or part_id not in range(1, 7):
        raise ValueError('灵器六部件名称或目标编号无效')
    # 部件名是单字；公告长行即使包含“轴”等同字也不是列表锚点。
    line_lengths = {}
    for token in tokens:
        line = token.get('parent_line_id')
        if line is not None:
            line_lengths[line] = line_lengths.get(line, 0) + len(token.get('text', ''))
    tokens = [token for token in tokens
              if line_lengths.get(token.get('parent_line_id'), 0) <= 2]
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
    if len(anchors) == 1:
        return None, 'left' if part_id < first[0] else 'right'
    spacing = (last[1] - first[1]) / (last[0] - first[0])
    if any(abs(x - (first[1] + (index - first[0]) * spacing)) > tolerance
           for index, x, _, _ in anchors):
        raise ValueError('部件名称 OCR 不满足等间距，不能内插')
    x = first[1] + (part_id - first[0]) * spacing
    if not first[0] <= part_id <= last[0]:
        # 末端名称可能漏识别；同列“阶”证明卡片仍在可见范围，才允许外推。
        visible_card = any(token.get('text') == '阶'
                           and abs(token['x'] + token['w'] / 2 - x) <= tolerance * 2
                           for token in tokens)
        if not visible_card:
            return None, 'left' if part_id < first[0] else 'right'
    return (x, sum(a[2] for a in anchors) / len(anchors)), None
