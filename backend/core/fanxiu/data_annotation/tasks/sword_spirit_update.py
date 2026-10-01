"""剑灵更新：按客户端规则清理未装配的仙品及以下剑纹。"""
from __future__ import annotations

import time
import re

from .world_menu_navigation import open_world_menu_function


STAGE_ID = 'sword-spirit-update'
STAGE_VERSION = '2'
STAGE_LABEL = '剑灵更新'
QUALITY_LABEL = '仙品及以下'


def decomposition_prompt_kind(prompt: str) -> str | None:
    """Recognize only the client's two confirmations for the selected batch.

    SwordSpiritBagView.OnClickDisassembleBtn first freezes at most 200
    unequipped items below the chosen quality. Its score/empty-equipment
    warning changes the wording, not that filtered batch or its callback.
    The caller must prove QUALITY_LABEL before requesting this confirmation;
    the warning itself carries no quality and cannot authorize a fresh batch.
    """
    text = re.sub(r'\s+', '', str(prompt or ''))
    if re.fullmatch(
        r'(?:本次)?批量分解[【\[]?仙品及以下[】\]]?剑纹[，,]?是否确认分解[？?]?',
        text,
    ):
        return 'quality'
    if re.fullmatch(
        r'批量分解的剑纹中存在比佩戴中剑纹评分更高或有未装配的剑纹[，,]?是否继续分解[？?]?',
        text,
    ):
        return 'equipment_warning'
    return None


def _ocr(context, scene_id: int, shape: str) -> str:
    frame = context.cur_frame(update=True)
    return context.ocr_text_in_shapes(
        scene_id, [shape], crop=True, padding=3, frame_data_url=frame,
    ).replace(' ', '').replace('\n', '')


def _selected_quality(context) -> str:
    return _ocr(context, 830, '分解档位下拉')


def _quality_is_selected(context) -> bool:
    # The bag redraws after entering and after server responses. One OCR frame
    # can be blank during that transition even though the selection persisted.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if QUALITY_LABEL in _selected_quality(context):
            return True
        time.sleep(0.4)
    return False


def _confirmation(context) -> str:
    return _ocr(context, 832, '分解档位提示')


def _choose_quality(context):
    if _quality_is_selected(context):
        return
    if (yield from context.wait_scene([830], wait=8)).scene_id != 830:
        raise RuntimeError('剑纹背包 #830 未就绪')
    context.click_shape_center(830, '分解档位下拉')
    if (yield from context.wait_scene([831], wait=8)).scene_id != 831:
        raise RuntimeError('剑纹分解档位列表 #831 未就绪')
    if QUALITY_LABEL not in _ocr(context, 831, QUALITY_LABEL):
        raise RuntimeError(f'剑纹档位列表未识别出{QUALITY_LABEL}')
    context.click_shape_center(831, QUALITY_LABEL)
    if (yield from context.wait_scene([830], wait=8)).scene_id != 830:
        raise RuntimeError('选择仙品及以下后未返回剑纹背包')
    if not _quality_is_selected(context):
        raise RuntimeError('剑纹背包的实际分解档位未切到仙品及以下')


def update_sword_spirit(context, *, max_batches: int = 50):
    """从 #34 进入剑灵，分批分解直到无候选，最终回到 #34。

    客户端 ``SwordSpiritBagView.OnClickDisassembleBtn`` 只收集未装配、
    质量不高于所选档位的剑纹；每次至多 200 件。每轮都读当前画面，
    点击前证明目标档位；确认客户端普通提示或同一候选批次的评分/空位警告。
    无确认窗时还须证明背包
    仍在、档位仍正确；异常弹窗或导航变化不能签发完成凭证。
    """
    if max_batches <= 0:
        raise ValueError('剑灵更新需要正的批次数上限')
    yield from open_world_menu_function(
        context, 1600000, expected_scene_ids=(829,), timeout_seconds=30,
    )
    context.click_shape_center(829, '剑纹背包')
    if (yield from context.wait_scene([830], wait=15)).scene_id != 830:
        raise RuntimeError('剑纹背包 #830 未就绪')
    yield from _choose_quality(context)

    batches = 0
    while True:
        if not _quality_is_selected(context):
            raise RuntimeError('剑纹分解前档位或背包画面已变化')
        context.click_shape_center(830, '执行快捷分解')
        # 弹窗有短动画；OCR 直接检查弹窗内容，避免背景 #830 的
        # 场景身份在遮罩下仍命中，导致在旧画面误点“确认”。
        prompt = ''
        for _ in range(10):
            prompt = _confirmation(context)
            if decomposition_prompt_kind(prompt) is not None:
                break
            time.sleep(0.35)
        else:
            if not _quality_is_selected(context):
                raise RuntimeError(f'剑纹快捷分解后画面变化且无确认窗：{prompt!r}')
            break
        kind = decomposition_prompt_kind(prompt)
        if (yield from context.wait_scene([832], wait=8)).scene_id != 832:
            raise RuntimeError('剑纹分解确认页 #832 未就绪')
        fresh_prompt = _confirmation(context)
        if decomposition_prompt_kind(fresh_prompt) != kind:
            raise RuntimeError(f'剑纹分解确认内容已变化：{fresh_prompt!r}')
        context.click_shape_center(832, '确认')
        yield from context.wait_action_settle(1)
        if not _quality_is_selected(context) or decomposition_prompt_kind(_confirmation(context)) is not None:
            raise RuntimeError('剑纹确认后未回到目标档位的背包')
        batches += 1
        if batches >= max_batches:
            raise RuntimeError(f'剑纹分解达到 {max_batches} 批上限，保留现场')

    context.click_shape_center(830, '返回背景')
    if (yield from context.wait_scene([829], wait=12)).scene_id != 829:
        raise RuntimeError('剑纹背包未退回剑灵首页')
    context.click_shape_center(829, '返回')
    yield from context.go_scene(34)
    if (yield from context.wait_scene([34], wait=15)).scene_id != 34:
        raise RuntimeError('剑灵更新未返回世界 #34')
    return {'result': 'success', 'outcome': 'complete',
            'quality': QUALITY_LABEL, 'decomposed_batches': batches,
            'empty_batch_checked': True}
