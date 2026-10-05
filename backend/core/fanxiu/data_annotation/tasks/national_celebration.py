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
from collections import Counter
from datetime import date

from PIL import Image

from .activity_fund import collect_activity_fund
from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens

HOME, FUND, CHALLENGE, PARTNERS = 903, 904, 905, 906


def parse_active_challenge_state(tokens):
    """顶部独立关卡行和波次行共同证明正在战斗；时钟不能拼入波次。

    2026-10-06 全页文本将02:04与11/20拼成0411/20，且技能分支遮住
    进度日志。保留原生行边界，只接受唯一顶部第N关与0..20/20。
    """
    lines = [re.sub(r'\s+', '', x['text']) for x in group_ocr_tokens(tokens)
             if not x['fallback'] and 0 <= x['y'] < 160]
    stages = [int(m[1]) for line in lines if (m := re.fullmatch(r'第(\d+)关', line))]
    waves = [int(m[1]) for line in lines if (m := re.fullmatch(r'(\d{1,2})/20', line))]
    if len(stages) != 1 or len(waves) != 1 or stages[0] <= 0 or not 0 <= waves[0] <= 20:
        return None
    return dict(stage=stages[0], wave=waves[0])


def read_active_challenge_state(context, frame):
    """只读当前实帧，识别已启动的战斗，不恢复旧生成器或触发挑战。"""
    return parse_active_challenge_state(context.full_frame_ocr_tokens(frame))


def recover_active_challenge_state(context):
    """新入口只关闭明确的获技覆盖层，再取完整当前战斗身份。

    获技覆盖层会遮住顶部关卡，不能把未读到关卡当作没有已启动的挑战。
    明确战斗而身份仍未知时保留现场，禁止导航退场或再次开始。
    """
    skill_closed = False
    for _ in range(10):
        frame = context.cur_frame(update=True)
        text = _page_text(context, frame)
        active = read_active_challenge_state(context, frame)
        if active:
            return active
        if '获得技能' in text and not skill_closed:
            context.click_shape_center(CHALLENGE, '技能继续')
            skill_closed = True
        elif not skill_closed and not any(word in text for word in ('选择技能', '获得技能')):
            # 无顶部读数可能只是战斗特效帧。必须正向确认稳定业务入口，
            # 才能断言没有正在运行的挑战；不能把 None 当作空闲。
            for scene in (34, HOME, FUND, PARTNERS, CHALLENGE):
                matched, _, _ = context.match_view(scene, frame_data_url=frame)
                if matched:
                    return None
        yield from context.wait_action_settle(2)
    raise RuntimeError('当前战斗或稳定入口未完整确认，保留现场，禁止重开')


def validate_challenge_completion(hint, *, business_date, current_stage=None):
    """验收研发已取证的单关完成事实；日期、前后关卡和当前页须一致。

    只表达不重开已完成挑战的整单修复意图，不保存步骤或旧选择游标。
    正式默认路径不提供此参数，修复参数只用于该次 run_now override。
    """
    if hint is None:
        return None
    if (not isinstance(hint, dict) or hint.get('business_date') != business_date
            or type(hint.get('before_stage')) is not int or hint['before_stage'] <= 0
            or type(hint.get('stage')) is not int
            or hint['stage'] != hint['before_stage'] + 1):
        raise ValueError('已完成挑战取证须为当日明确的一关前后事实')
    if current_stage is not None and current_stage != hint['stage']:
        raise RuntimeError('当前关卡与已完成挑战取证不一致，禁止开始新挑战')
    return dict(branch='verified_settled', before_stage=hint['before_stage'],
                stage=hint['stage'], advanced=True, started=False)


def _text(context, scene, shape, frame, *, padding=0):
    return re.sub(r"\s+", "", context.ocr_text_in_shapes(
        scene, [shape], frame_data_url=frame, padding=padding, crop=True,
    ))


def _page_text(context, frame):
    lines = {}
    for token in context.full_frame_ocr_tokens(frame):
        lines.setdefault(token['parent_line_id'], []).append(token['text'])
    return re.sub(r"\s+", "", '\n'.join(''.join(t) for t in lines.values()))


def resolve_challenge_digits(line, candidates):
    """整行数字宽度与裁剪读数必须一致；漏位、矛盾均保持未知。"""
    extent = re.fullmatch(r'第(.+)关', line)
    if not extent:
        return None
    digits = {int(value) for value in candidates
              if re.fullmatch(r'[0-9]+', value) and len(value) == len(extent[1])}
    return digits.pop() if len(digits) == 1 else None


def read_challenge_stage(context, frame):
    """只读当前关卡；艺术字体整行识别失败时，使用数字专用 ROI。

    顶部奖励门槛不是当前关卡，不能用其替代或猜测错识别的字。
    """
    text = re.sub(r'\s+', '', context.ocr_text_in_shapes(
        CHALLENGE, ['当前关卡'], frame_data_url=frame, padding=0,
    ))
    match = re.fullmatch(r'第(\d+)关', text)
    if match:
        return int(match[1])
    cropped = _text(context, CHALLENGE, '当前关卡', frame)
    match = re.fullmatch(r'第(\d+)关', cropped)
    if match:
        return int(match[1])
    # 1 的细笔画在紧裁剪时会丢失，20/32px 留白可恢复检测。
    # 只接受完整数字；有效读数若矛盾则保留未知并等待下一帧。
    extent = re.fullmatch(r'第(.+)关', text) or re.fullmatch(r'第(.+)关', cropped)
    candidates = [_text(context, CHALLENGE, '当前关卡数字', frame, padding=p)
                  for p in (4, 12, 16, 20, 24, 28, 32)]
    # 整行第/关之间的字符数约束数字长度，防止 41 漏掉 1 被当成 4。
    stage = resolve_challenge_digits(extent[0] if extent else '', candidates)
    if stage is not None:
        return stage
    raise ValueError(f'当前关卡未识别：整行={text!r}/{cropped!r}，数字={candidates!r}')


def parse_upgrade_state(material, level):
    """Missing OCR is unknown, never interpreted as zero resources."""
    amount = re.fullmatch(r"(\d+)/(\d+)", material)
    if not amount or not level.isdigit() or int(amount[2]) <= 0:
        raise ValueError(f'伙伴升级状态不完整：材料={material!r}，等级={level!r}')
    return int(amount[1]), int(amount[2]), int(level)


def resolve_partner_level(values):
    """统一升星的三个独立徽章至少两处一致才构成等级事实。"""
    counts = Counter(int(value) for value in values if value.isdigit())
    if counts:
        level, count = counts.most_common(1)[0]
        if count >= 2:
            return str(level)
    raise ValueError(f'伙伴等级识别未形成多数一致：{values}')


def _upgrade_state(context):
    deadline = time.monotonic() + 20
    while True:
        frame = context.cur_frame(update=True)
        try:
            # 同一徽章多种 padding 的读数并非独立证据。统一升星让
            # 所有伙伴等级一致，用同帧三个独立徽章验证，单处漏字
            # 或 5/0 混淆不能覆盖另两处的一致结果。
            level = resolve_partner_level([
                _text(context, PARTNERS, shape, frame, padding=12)
                for shape in ('伙伴等级', '伙伴等级2', '伙伴等级3')
            ])
            return parse_upgrade_state(
                _text(context, PARTNERS, '升级材料', frame),
                level,
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
    deadline = time.monotonic() + 20
    while True:
        try:
            before_stage = read_challenge_stage(context, context.cur_frame(update=True))
            break
        except ValueError:
            if time.monotonic() >= deadline:
                raise
            yield from context.wait_action_settle(1)
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
    last_battle_progress = None
    context.report_progress('玄天镇魔：等待本轮挑战结算', phase='national_celebration_challenge')
    while time.monotonic() < deadline:
        frame = context.cur_frame(update=True)
        text = _page_text(context, frame)
        last_text = text
        active = read_active_challenge_state(context, frame)
        if active:
            observed, branch = True, 'battle'
            progress = active['wave']
            if progress != last_battle_progress:
                context.report_progress(f'玄天镇魔：第{active["stage"]}关，本轮战斗 {progress}/20',
                                        phase='national_celebration_challenge')
                last_battle_progress = progress
        reward, _, _ = context.match_view(227, frame_data_url=frame)
        if reward:
            # 本期长奖励列表的标题会被遮掉，不能要求整帧 OCR 同时包含
            # 『恭喜获得』。复用已验收的奖励身份与继续 Shape，并优先于
            # 奖励覆盖层下面可能仍被识别的主页处理。
            observed = True
            if not reward_dismissed:
                context.click_shape_center(227, '继续')
                reward_dismissed = True
            yield from context.wait_action_settle(2)
            continue
        # The outer loop owns fresh-frame polling. Use the existing scene
        # identity on this exact frame, rather than a second full-screen OCR
        # interpretation of the home page. A settlement animation can retain
        # reward text while the underlying bottom tabs become clickable.
        home, _, _ = context.match_view(CHALLENGE, frame_data_url=frame)
        if home and observed:
            # 跳关结算动画会短暂露出主页，再显示奖励覆盖层。
            # 先等待本次动作的两种合法落点，再从该新帧核验关卡。
            landed = yield from context.wait_scene([CHALLENGE, 227], wait=15,
                                                   label='玄天镇魔结算落点')
            if landed.scene_id != CHALLENGE:
                continue
            try:
                after_stage = read_challenge_stage(context, landed.frame_data_url)
            except ValueError:
                yield from context.wait_action_settle(1)
                continue
            return dict(branch=branch, before_stage=before_stage,
                        stage=after_stage, advanced=after_stage > before_stage,
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
        elif active:
            pass  # 上方统一报告进度，技能选择分支同样可见。
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


def collect_national_celebration(context, *, verified_challenge=None):
    """每日组件；主题集以当前 Runtime 活动周期隔离实例，过期不派发。"""
    validated = validate_challenge_completion(verified_challenge, business_date=date.today().isoformat())
    # 新 attempt 从当前完整战斗帧收敛已启动的一轮，不能先退场再重开。
    active = yield from recover_active_challenge_state(context)
    recovered_challenge = None
    if active:
        recovered_challenge = yield from wait_challenge_result(
            context, before_stage=active['stage'], observed_branch='battle',
        )
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
    if validated is not None:
        current_stage = read_challenge_stage(context, context.cur_frame(update=True))
        challenge = validate_challenge_completion(
            verified_challenge, business_date=date.today().isoformat(), current_stage=current_stage,
        )
    else:
        challenge = recovered_challenge if recovered_challenge is not None else (yield from challenge_once(context))
    yield from context.wait_click(CHALLENGE, '返回')
    yield from context.wait_scene_exact([34], timeout=15)
    return dict(result='success', fund=fund, partners=partners,
                challenge=challenge, final_scene=34)
