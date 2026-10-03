"""模板横排礼包：标题锚定首卡资产的购买按钮，只产生 GUI 查看/点击位置。"""
import unicodedata


def activity_gift_button(tokens, *, title, viewport, title_template, button_template):
    grouped = {}
    for t in tokens:
        cy = t['y']+t['h']/2
        if title_template['y']-20 < cy < title_template['y']+title_template['h']+20:
            grouped.setdefault(t['parent_line_id'], []).append(t)
    boxes = []
    for line in grouped.values():
        line.sort(key=lambda t: t['order'])
        for start in range(len(line)):
            text = ''
            for end in range(start, len(line)):
                text += unicodedata.normalize('NFKC', line[end]['text']).replace(' ', '')
                if text == title:
                    part = line[start:end+1]
                    boxes.append((min(t['x'] for t in part), max(t['x']+t['w'] for t in part)))
                if len(text) >= len(title):
                    break
    if len(boxes) != 1:
        raise ValueError('礼包标题未唯一完整显示')
    x0, x1 = boxes[0]
    dx = (x0+x1)/2 - (title_template['x']+title_template['w']/2)
    bx = button_template['x']+dx
    if bx < viewport['x'] or bx+button_template['w'] > viewport['x']+viewport['w']:
        raise ValueError('礼包购买按钮被裁剪')
    return bx+button_template['w']/2, button_template['y']+button_template['h']/2
