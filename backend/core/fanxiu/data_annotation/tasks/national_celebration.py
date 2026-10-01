"""26 年国庆庆典适配：基金、统一升级、一次挑战，最后返回世界页。

活动存在性、日期与每日完成键归主题集；这里不绑定旧 ActivityId。
基金实时领取能力可复用，页面名称和资产仍属于本期适配。
挑战只处理一次开始后的合法落点，不连续推关、不重打失败关。
"""
from __future__ import annotations

import base64
import io
import re
import time

from PIL import Image

from .activity_fund import collect_activity_fund

HOME, FUND, CHALLENGE, PARTNERS = 903, 904, 905, 906


def _text(context, scene, shape, frame, *, padding=0):
    return re.sub(r"\s+", "", context.ocr_text_in_shapes(
        scene, [shape], frame_data_url=frame, padding=padding, crop=True,
    ))


def _page_text(context, frame):
    lines = {}
    for token in context.full_frame_ocr_tokens(frame):
        lines.setdefault(token['parent_line_id'], []).append(token['text'])
    return re.sub(r"\s+", "", '\n'.join(''.join(t) for t in lines.values()))


def parse_upgrade_state(material, level):
    """Missing OCR is unknown, never interpreted as zero resources."""
    amount = re.fullmatch(r"(\d+)/(\d+)", material)
    if not amount or not level.isdigit() or int(amount[2]) <= 0:
        raise ValueError(f'伙伴升级状态不完整：材料={material!r}，等级={level!r}')
    return int(amount[1]), int(amount[2]), int(level)


def _upgrade_state(context):
    deadline = time.monotonic() + 20
    while True:
        frame = context.cur_frame(update=True)
        try:
            return parse_upgrade_state(
                _text(context, PARTNERS, '升级材料', frame),
                # 小徽章紧裁会漏检，整帧也会漏掉部分等级数字。
                # 实机 31/32 级验证：外扩 8px 提供检测边缘，且不纳入姓名。
                _text(context, PARTNERS, '伙伴等级', frame, padding=8),
            )
        except ValueError:
            if time.monotonic() >= deadline:
                raise
            yield from context.wait_action_settle(1)


def upgrade_partners(context):
    """底部『升星』统一升所有伙伴；卡片上的可升级只打开说明。"""
    yield from context.wait_scene_exact([PARTNERS], timeout=15)
    before = yield from _upgrade_state(context)
    initial = before
    for _ in range(100):
        if before[0] < before[1]:
            return dict(before_level=initial[2], level=before[2], remaining=before[0])
        yield from context.wait_click(PARTNERS, '升星')
        deadline = time.monotonic() + 20
        while True:
            after = yield from _upgrade_state(context)
            if after[2] == before[2] + 1 and after[0] == before[0] - before[1]:
                before = after
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(f'伙伴升星未确认等级/材料变化：{before} -> {after}')
            yield from context.wait_action_settle(1)
    raise RuntimeError('伙伴升级超过本轮安全上限')


def _auto_selected(context, frame):
    """绿色勾的最小区域是视觉状态；文字『自动选择』本身不证明选中。"""
    box = context.shape_box(CHALLENGE, '自动选择')
    with Image.open(io.BytesIO(base64.b64decode(frame.split(',', 1)[1]))) as source:
        crop = source.convert('RGB').crop((
            int(box['x']), int(box['y']),
            int(box['x'] + box['w']), int(box['y'] + box['h']),
        ))
        green = sum(g > 90 and g > r * 1.2 and g > b * 1.1 for r, g, b in crop.getdata())
    return green >= 15


def challenge_once(context, *, timeout=900):
    """跳关→奖励，或战斗→自动选择/技能阻塞页→主页；只做一轮。

从开始动作起等待真实落点，返回主页后检查关卡前进。未知结算保留现场，
不会靠多点一次、重开挑战或递归调用掩盖状态。所有循环有超时。
"""
    yield from context.wait_scene_exact([CHALLENGE], timeout=15)
    frame = context.cur_frame(update=True)
    stage_text = _text(context, CHALLENGE, '当前关卡', frame)
    match = re.search(r'第(\d+)关', stage_text)
    if not match:
        raise RuntimeError(f'挑战前关卡未识别：{stage_text}')
    before_stage = int(match[1])
    yield from context.wait_click(CHALLENGE, '开始挑战')
    return (yield from wait_challenge_result(context, before_stage=before_stage, timeout=timeout))


def wait_challenge_result(context, *, before_stage, timeout=900, observed_branch=None):
    """等待已经启动的挑战；研发可在有本次实机证据时单独验收结算组件。

工程新 attempt 仍由 challenge_once 从稳定入口开始；本函数不保存游标。
"""
    deadline = time.monotonic() + timeout
    observed = observed_branch is not None
    branch = observed_branch
    auto_clicked_at = None
    skill_dialogs = 0
    reward_dismissed = False
    last_text = ''
    while time.monotonic() < deadline:
        frame = context.cur_frame(update=True)
        text = _page_text(context, frame)
        last_text = text
        # The outer loop owns fresh-frame polling. Use the existing scene
        # identity on this exact frame, rather than a second full-screen OCR
        # interpretation of the home page. A settlement animation can retain
        # reward text while the underlying bottom tabs become clickable.
        home, _, _ = context.match_view(CHALLENGE, frame_data_url=frame)
        if home and observed:
            after_text = _text(context, CHALLENGE, '当前关卡', frame)
            after = re.search(r'第(\d+)关', after_text)
            if not after:
                yield from context.wait_action_settle(1)
                continue
            yield from context.wait_scene_exact([CHALLENGE], timeout=15)
            return dict(branch=branch, before_stage=before_stage,
                        stage=int(after[1]), advanced=int(after[1]) > before_stage,
                        skill_dialogs=skill_dialogs)
        if '跳过战斗' in text and '继续暴揍' in text:
            observed, branch = True, 'skip'
            context.click_shape_center(CHALLENGE, '跳过战斗')
        elif '获得技能' in text:
            observed, branch = True, 'battle'
            context.click_shape_center(CHALLENGE, '技能继续')
            skill_dialogs += 1
        elif '选择技能' in text:
            observed, branch = True, 'battle'
            if not _auto_selected(context, frame):
                if auto_clicked_at is not None and time.monotonic() - auto_clicked_at < 15:
                    # 先等选中/倒计时反馈，不能连点把开关又取消。
                    yield from context.wait_action_settle(1)
                    continue
                context.click_shape_center(CHALLENGE, '自动选择')
                auto_clicked_at = time.monotonic()
        elif '恭喜获得' in text and '点击屏幕继续' in text:
            observed = True
            if not reward_dismissed:
                context.click_shape_center(CHALLENGE, '结算继续')
                reward_dismissed = True
                # One close action, then observe its result. Never click the
                # same fixed position again while the animation is leaving;
                # that position overlaps a home-page business tab.
                yield from context.wait_scene([CHALLENGE], wait=30, required=False,
                                              label='玄天镇魔奖励关闭后等待主页')
        elif re.search(r'\d+/20', text) and '第' in text and '关' in text:
            observed, branch = True, 'battle'
        elif any(word in text for word in ('挑战失败', '挑战成功', '战斗失败', '战斗胜利')):
            # 此类结算尚未由用户讲解，不能猜按钮、购买复活或重开。
            raise RuntimeError(f'挑战出现待适配结算，保留现场：{text[:240]}')
        elif context.find_view('弹窗') is not None:
            # 活动公告可在战斗落回主页时插入。交给已有全局弹窗守护，
            # 不能只等业务文字，也不能把公告当战斗结算随意点击。
            yield from context.wait_scene([CHALLENGE], wait=2, required=False,
                                          label='挑战过程通用弹窗处理')
        yield from context.wait_action_settle(2)
    raise TimeoutError(f'玄天镇魔挑战未在时限内回到稳定主页，保留现场；'
                       f'分支={branch}，奖励已关闭={reward_dismissed}，'
                       f'最后观测={last_text[:400]}')


def collect_national_celebration(context):
    """每日组件；主题集以当前 Runtime 活动周期隔离实例，过期不派发。"""
    yield from context.go_scene(34)
    yield from context.wait_click_ocr_text(
        34, '凌霄庆典', in_shapes=['左侧菜单'], max_scrolls_per_direction=0,
    )
    yield from context.wait_scene_exact([HOME], timeout=15)
    yield from context.wait_click(HOME, '基金入口')
    fund = yield from collect_activity_fund(context)
    yield from context.wait_click(HOME, '玄天镇魔入口')
    yield from context.wait_scene_exact([CHALLENGE], timeout=15)
    # 本期入口的语义校验：主玩法必须包含兑换宝阁。
    yield from context.wait_shape(CHALLENGE, '兑换宝阁', timeout=15)
    yield from context.wait_click(CHALLENGE, '伙伴')
    partners = yield from upgrade_partners(context)
    context.click_shape_center(PARTNERS, '玄天镇魔')
    yield from context.wait_scene_exact([CHALLENGE], timeout=15)
    challenge = yield from challenge_once(context)
    yield from context.wait_click(CHALLENGE, '返回')
    yield from context.wait_scene_exact([34], timeout=15)
    return dict(result='success', fund=fund, partners=partners,
                challenge=challenge, final_scene=34)
