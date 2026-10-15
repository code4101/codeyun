"""Reveal a named bottle-world menu entry without clicking or consuming it."""


def reveal_bottle_menu_entry(context, target: str, *, max_scrolls=15):
    """Search fresh menu text in both directions; preserve unknown locations.

    Menu scroll position survives other tasks. Pixel-stability alone cannot
    prove that a target is visible, especially with animated icons. Callers
    still use the existing scene/Shape navigation after this observation.
    """
    if not isinstance(target, str) or not target.strip() or type(max_scrolls) is not int or max_scrolls <= 0:
        raise ValueError('菜单定位需要明确目标和正整数滚动上限')
    yield from context.go_scene(20, known_paths_only=True)
    scrolls = 0
    for direction in ('right', 'left'):
        for attempt in range(max_scrolls + 1):
            frame = context.cur_frame(update=True)
            if context.find_ocr_text(20, target, in_shapes=['菜单'],
                                     frame_data_url=frame, match_mode='exact') is not None:
                return {'target': target, 'visible': True, 'scroll_count': scrolls}
            if attempt == max_scrolls:
                break
            changed = yield from context.scroll_shape_content(20, '菜单', direction=direction)
            scrolls += 1
            if not changed:
                break
    raise RuntimeError(f'瓶中世界菜单未能唯一定位{target}，已滚动{scrolls}次，保留现场')


def enter_xianqiao_page(context):
    """Enter 仙窍 from its existing pages or reveal its current menu entry."""
    frame = context.cur_frame(update=True)
    if not any(context.match_view(scene, frame_data_url=frame)[0]
               for scene in (484, 485, 486, 487, 489, 950, 967, 968)):
        yield from reveal_bottle_menu_entry(context, '仙窍')
    yield from context.go_scene(484)
