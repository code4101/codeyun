"""升阶/悟境共用的 GUI 数量循环；不读取 Runtime，不核对扣料。"""
import re

# 同一灵器窗口的合法页签落点；页面可能保留上次选中的页签。
ARTIFACT_TAB_SCENES = (667, 668, 714, 717, 731)


def parse_upgrade_material_count(text, *, numerator_only=False):
    """材料 OCR：分子须是整数；整行回退只接受单件消耗，不猜丢失斜线。"""
    text = str(text).strip().replace(' ', '').strip('_')
    pattern = r'(\d+)' if numerator_only else r'(\d+)[/／.·]1'
    found = re.fullmatch(pattern, text)
    return int(found[1]) if found else None


def use_visible_upgrade_materials(context, execute, *, scene_id=717,
                                  count_shape='材料分子', action_shape='执行升阶'):
    """当前每次消耗一件：OCR 仅读分子，一批用完；回读处理漏点。"""
    total = 0
    for _ in range(5):
        fraction = None
        for _ in range(3):
            frame = context.cur_frame(update=True)
            tokens = context.ocr_tokens_in_shapes(
                scene_id, [count_shape], crop=True, padding=1,
                frame_data_url=frame,
                options={'ocr_version': 'PP-OCRv5'})
            text = ''.join(str(t.get('text', '')) for t in tokens).replace(' ', '')
            value = parse_upgrade_material_count(text, numerator_only=True)
            if value is not None:
                fraction = (value, 1)
                break
            tokens = context.ocr_tokens_in_shapes(scene_id, ['材料数量'], crop=True,
                padding=6, frame_data_url=frame,
                options={'ocr_version': 'PP-OCRv5'})
            text = ''.join(str(t.get('text', '')) for t in tokens).replace(' ', '')
            value = parse_upgrade_material_count(text)
            if value is not None:
                fraction = (value, 1)
                break
            execute(context.wait_action_settle(.3))
        if fraction is None:
            landed = execute(context.wait_scene([scene_id, 718, 719, 720, 721], wait=2)).scene_id
            if landed in (720, 721):
                context.click_shape_center(landed, '点击屏幕继续')
                execute(context.wait_action_settle(1.2))
                continue
            if landed in (718, 719):
                context.click_shape_center(landed, '确认')
                execute(context.wait_action_settle(1.2))
                continue
            if landed != scene_id:
                raise RuntimeError(f'升级被场景 #{landed} 阻断；保留现场。请查阅 '
                    'C:/home/chenkunze/slns/skills/凡修/references/接口层/场景识别.md')
            raise RuntimeError(f'#{scene_id}[{count_shape}]数量无法读取: {text!r}；保留现场')
        count = fraction[0] // fraction[1]
        if not count:
            return {'status': 'complete', 'clicks': total, 'remaining': fraction[0]}
        if count > 100:
            raise RuntimeError('单批升级数量异常，保留现场')
        for _ in range(count):
            context.click_shape_center(scene_id, action_shape)
            execute(context.wait_action_settle(1.2))
        total += count
    raise RuntimeError('五批升级后仍未用完，保留现场')


def select_artifact_tab(context, execute, label, *, optional=False):
    return execute(select_artifact_tab_steps(context, label, optional=optional))


def select_artifact_tab_steps(context, label, *, optional=False):
    """页签数量会改变布局，按当前文字定位；升品缺席是正常能力差异。"""
    from ..ocr_spatial import find_text_matches, select_text_match
    band = context.shape_box(667, '升阶页签')
    for attempt in range(2):
        tokens = [t for t in context.full_frame_ocr_tokens(update=True)
                  if band['y'] <= t['y'] + t['h'] / 2 <= band['y'] + band['h']]
        def find(text):
            return select_text_match(find_text_matches(tokens, text), text)
        match = find(label)
        if match is not None:
            context.click_frame_point(667, *match.point())
            yield from context.wait_action_settle(1.2)
            return True
        if optional and all(find(text) is not None for text in ('装配', '升阶')):
            return False
        yield from context.wait_action_settle(.5)
    raise RuntimeError(f'灵器页签 {label} 未识别，保留现场')


def finish_visible_artifact(context, execute):
    """从装配页开始：本灵器升阶用完，再悟境用完；无 Runtime 读取。"""
    results = {}
    for page, tab, button in ((717, '升阶页签', '执行升阶'),
                              (731, '升品页签', '执行悟境')):
        batches = []
        for _ in range(7):
            if not select_artifact_tab(context, execute, '升阶' if page == 717 else '升品',
                                       optional=page == 731):
                results[tab] = {'status': 'unavailable'}
                break
            # 页签就绪后再读0，避免旧页的0/1被当成本页已完成。
            landed = execute(context.wait_scene([page], wait=5)).scene_id
            if landed != page:
                raise RuntimeError(f'切入升级页 #{page} 时遇到 #{landed}，保留现场')
            result = use_visible_upgrade_materials(context, execute,
                scene_id=page, action_shape=button)
            batches.append(result)
            select_artifact_tab(context, execute, '装配')
            if result['clicks'] == 0:
                break
        else:
            raise RuntimeError('灵器重新选中七次仍未用完，保留现场')
        results.setdefault(tab, batches)
    return results


def open_artifact_for_upgrade(context, execute, name):
    return execute(open_artifact_for_upgrade_steps(context, name))


def open_artifact_for_upgrade_steps(context, name):
    """按封面名称定位灵器，不为已识别 GUI 再扫描 Runtime。"""
    from ...instrumentation.spirit_artifact_ui import locate_spirit_artifact_name
    names = (name, name.replace('摩诃', '摩河'), name.replace('干天', '千天'))
    from ...catalog.spirit_artifact_identity import load_spirit_artifact_templates
    ordered = [value[0] for _, value in sorted(load_spirit_artifact_templates().items())]
    target = ordered.index(name)
    empty_observations = 0
    for _ in range(9):
        tokens = context.ocr_tokens_in_shapes(666, ['灵器列表'], crop=True, padding=0,
            frame_data_url=context.cur_frame(update=True),
            options={'ocr_version': 'PP-OCRv5'})
        point = locate_spirit_artifact_name(tokens, names)
        if point is not None:
            context.click_frame_point(666, *point)
            landed = (yield from context.wait_scene(list(ARTIFACT_TAB_SCENES), wait=8)).scene_id
            if landed not in ARTIFACT_TAB_SCENES:
                raise RuntimeError(f'灵器名称点击后进入 #{landed}，保留现场')
            return landed
        visible = [i for i, label in enumerate(ordered)
                   if locate_spirit_artifact_name(tokens, (label,)) is not None]
        if not visible:
            # 封面刚打开或滚动结束时，名称可能仍在入场动画中。
            if empty_observations == 0:
                empty_observations += 1
                yield from context.wait_action_settle(.6)
                continue
            raise RuntimeError('灵器列表 OCR 未识别出任何已配置名称，保留现场')
        empty_observations = 0
        if min(visible) < target < max(visible):
            raise RuntimeError(f'目标 {name} 位于可见范围但名称未识别，保留现场')
        direction = 'right' if target > max(visible) else 'left'
        yield from context.scroll_shape_content(context.shape(666, '灵器列表'), direction=direction)
        yield from context.wait_action_settle(.6)
    raise RuntimeError(f'封面未找到灵器 {name}')
