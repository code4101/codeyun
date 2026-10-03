"""法身模型列表：OCR 等级锚点与资产中的原生左右列、垂直间距对齐。"""
import re


def field_transform_model_points(tokens, *, levels, viewport, left, right, gap):
    """这里只产生查看候选；详情 Runtime 身份才授予后续提升权限。

    名称是竖排图片，等级是唯一稳定 OCR 锚点。真机 4001 与 6001 确认
    等级位于模型中心下方 180px，两列交错，名字在模型相反侧。
    页面部分显示的模型不点击，列表外数字和战力提示不参与定位。
    """
    points = []
    for token in tokens:
        text = re.sub(r'\s+', '', str(token.get('text', '')))
        if not text.isdecimal() or int(text) not in levels:
            continue
        x, y = token['x'] + token['w']/2, token['y'] + token['h']/2
        if not (viewport['x']+70 < x < viewport['x']+viewport['w']-70):
            continue
        model_y = y-gap
        if not viewport['y']+80 < model_y < viewport['y']+viewport['h']-80:
            continue
        column = right if x < viewport['x']+viewport['w']/2 else left
        points.append((column['x']+column['w']/2, model_y))
    return points
