"""用奖励分数对齐左侧免费奖励；付费列从不作为点击候选。"""
from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens


def plan_trial_reward_click(rewards, tokens, free_column):
    """用当前 OCR 几何定位 Runtime 已确认可领的行；不唯一则返回 None。

    调用方可重新开页，交游戏自动定位首个待领奖励；本函数不决定完成状态。
    普通分数档位的点击已实测；“当前”标记替代分数时的退路仅做纯算法验证，
    尚未实点验收。若发生错位，先核对 Runtime current_marker 对应的 _index，
    再看原始 OCR 框和 #863 两个列 Shape，避免拿当前总积分匹配奖励档位。
    45/100 像素边距基于 900×1600 参考帧；改布局时一起校验，不单独放宽阈值。
    """
    candidates = []
    x, y, w, h = (float(free_column[key]) for key in ('x', 'y', 'w', 'h'))
    fragments = group_ocr_tokens(tokens)
    current = [t for t in fragments if str(t['text']).strip() == '当前']
    for row in rewards:
        if not row['claimable']:
            continue
        matches = [token for token in fragments
                   if str(token['text']).strip() == str(row['score'])
                   and not any(0 < float(token['y']) - float(mark['y']) < 100 for mark in current)]
        if len(matches) != 1:
            continue
        token = matches[0]
        cy = float(token['y']) + float(token['h']) / 2
        if y + 45 < cy < y + h - 45:
            candidates.append({'reward_id': row['reward_id'], 'score': row['score'],
                               'point': (x + w / 2, cy)})
    if candidates:
        return candidates[0]
    marked_rows = [r for r in rewards if r['claimable'] and r.get('current_marker')]
    if len(current) == len(marked_rows) == 1:
        # The client's ProgressItem replaces this tier's score with 当前/score.
        # 当前 #863 样本中标签下沿接近奖励行中心；此退路尚待真实点击验收。
        cy = float(current[0]['y']) + float(current[0]['h'])
        if y + 45 < cy < y + h - 45:
            row = marked_rows[0]
            return {'reward_id': row['reward_id'], 'score': row['score'], 'point': (x + w / 2, cy)}
    return None
