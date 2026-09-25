"""玩法榜最终更新：按资产声明打开各榜单，再调用该活动现有采集器。"""


def refresh_gameplay_final_rankings(context, *, tabs, collect):
    """Each tab is (source scene, click Shape, expected destination scene).

    The collector binds the exact occurrence and rejects incomplete facts.
    Navigation is shared; scene variation stays in declarative assets.
    """
    for source, shape, destination in tabs:
        yield from context.wait_click(source, shape)
        match = yield from context.wait_scene([destination], wait=15)
        if int(match) != destination:
            raise RuntimeError(f'最终榜单未进入 #{destination}')
        yield from context.wait_action_settle(1)
    return collect()
