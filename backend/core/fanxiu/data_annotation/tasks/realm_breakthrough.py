"""境界突破研发组件：只打开已核实的250→251条件分支。"""

STAGE_ID = 'realm250-sixth-hole-preparation'
STAGE_VERSION = '1'


def open_realm_breakthrough_conditions(context):
    """自然打开条件窗；其他等级可能直接突破，故拒绝复用当前入口。

    返回的 conditions_met 仅证明窗口条件，不授权资源消耗或开始挑战。
    调用方完成观察后须使用#949返回，再归还工程运行权。
    """
    from backend.core.fanxiu.instrumentation.role_progression import read_role_progression_snapshot
    from backend.core.fanxiu.instrumentation.realm_breakthrough import read_realm_breakthrough_conditions_snapshot

    role = read_role_progression_snapshot()
    if role.get("complete") is not True or role.get("level") != 250 or role.get("can_upgrade") is not True:
        raise RuntimeError("尚未核实此等级的条件页入口，拒绝直接开始突破")
    yield from context.go_scene(20)
    entry_scroll_count = 0
    for attempt in range(31):
        frame = context.cur_frame(update=True)
        # The visible exact menu label is the navigation goal. Scrolling all
        # the way to the edge despite an already visible target can take
        # minutes when animated menu contents defeat pixel-stability checks.
        if context.find_ocr_text(20, '修炼', in_shapes=['菜单'],
                                 frame_data_url=frame, match_mode='exact') is not None:
            break
        if attempt == 30:
            raise RuntimeError('境界条件入口：菜单滚动30次仍未唯一识别修炼')
        changed = yield from context.scroll_shape_content(20, "菜单", direction="left")
        entry_scroll_count += 1
        if not changed:
            frame = context.cur_frame(update=True)
            break
    context.click_ocr_text(20, "修炼", in_shapes=["菜单"], frame_data_url=frame,
                           anchor="top_center", offset=(0, -1), offset_unit="height")
    yield from context.wait_scene_exact([405], timeout=25, observation_scenes=[405, 20])
    yield from context.wait_click(405, "突破境界")
    yield from context.wait_scene_exact([949], timeout=25, observation_scenes=[949, 364, 405])
    snapshot = read_realm_breakthrough_conditions_snapshot()
    if snapshot.get("complete") is not True:
        raise RuntimeError("境界条件窗实时判断不完整，保留现场")
    return {**snapshot, 'entry_scroll_count': entry_scroll_count}


def inspect_sixth_xianqiao_opening(context):
    """查看已验收的玉京瑶光通窍条件，退出到世界页；不提交通窍。

    目前只支持境界250的条件页入口。界面与原生进度、费用、修为须
    属于同一游戏进程且相互一致；失败保留现场供研发检查。
    返回的资源余量与客户端 enough 不构成消费授权。
    """
    from backend.core.fanxiu.instrumentation.role_progression import read_role_progression_snapshot
    from backend.core.fanxiu.instrumentation.xianqiao import read_xianqiao_opening_cost_snapshot
    from backend.core.fanxiu.instrumentation.xianqiao_opening import read_xianqiao_opening_dialog_snapshot

    yield from open_realm_breakthrough_conditions(context)
    yield from context.wait_click(949, "前往仙窍")
    yield from context.wait_scene_exact([484, 363], timeout=20)
    # The condition link can select the bottle-world menu rather than open
    # its page directly. Reuse normal navigation from that verified landing.
    yield from context.go_scene(484)
    yield from context.wait_click(484, "玉京")
    yield from context.wait_scene_exact([485], timeout=20)
    yield from context.wait_click(485, "瑶光")
    yield from context.wait_scene_exact([950], timeout=20)
    dialog = read_xianqiao_opening_dialog_snapshot()
    cost = read_xianqiao_opening_cost_snapshot(system_type=2, part_id=6)
    role = read_role_progression_snapshot()
    identity = lambda row: (row.get("pid"), row.get("process_start_ticks"))
    if (any(row.get("complete") is not True for row in (dialog, cost, role))
            or (dialog.get("system_type"), dialog.get("part_id")) != (2, 6)
            or dialog.get("active_level") != cost.get("active_level")
            or dialog.get("opened") is not False or cost.get("opened") is not False
            or role.get("level") != 250
            or identity(dialog) != identity(cost)
            or identity(dialog) != identity(role.get("evidence") or {})
            or cost.get("next_level_exp") is None
            or dialog.get("enough") != (role["overflow_exp"] >= cost["next_level_exp"])):
        raise RuntimeError("瑶光通窍界面与当前原生进度/成本/修为不一致，保留现场")
    yield from context.wait_click(950, "背景关闭")
    yield from context.wait_scene_exact([485], timeout=20)
    yield from context.go_scene(34)
    yield from context.wait_scene_exact([34], timeout=20)
    return {"dialog": dialog, "cost": cost, "role": role,
            "full_open_gap": max(0, cost["remaining_exp"] - role["overflow_exp"]),
            "resource_consumption": 0, "world_scene": 34}


def _sixth_opening_facts():
    from backend.core.fanxiu.instrumentation.role_progression import read_role_progression_snapshot
    from backend.core.fanxiu.instrumentation.xianqiao import read_xianqiao_opening_cost_snapshot
    role = read_role_progression_snapshot()
    cost = read_xianqiao_opening_cost_snapshot(system_type=2, part_id=6)
    if (role.get('complete') is not True or cost.get('complete') is not True
            or role['level'] != 250
            or any(role['evidence'][k] != cost[k] for k in ('pid', 'process_start_ticks'))):
        raise RuntimeError('瑶光通窍当前仅支持已核实的250级及同一原生进程')
    return role, cost


def accept_sixth_xianqiao_opening_transaction(key):
    """Accept exactly one persisted level/expense intent; never replay its tap."""
    from datetime import datetime
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_world_facts, record_world_discovery
    intent = read_world_facts().get('discoveries', {}).get(key)
    if not isinstance(intent, dict):
        raise RuntimeError('瑶光通窍事务不存在')
    if intent['outcome'] == 'verified':
        return intent
    role, cost = _sixth_opening_facts()
    before, previous = intent['role_before'], intent['cost_before']
    expected_level = previous['active_level'] + 1
    if (cost['active_level'] != expected_level
            or cost['opened'] != (expected_level == previous['max_level'])
            or role['role_id'] != before['role_id']
            or role['required_exp'] != before['required_exp']
            or before['current_exp'] - role['current_exp'] != previous['next_level_exp']):
        raise RuntimeError('瑶光通窍进度或修为消费尚未一致，保留意图禁止重复提交')
    receipt = {**intent, 'outcome':'verified', 'role_after':role, 'cost_after':cost,
               'verified_at':datetime.now().astimezone().isoformat()}
    record_world_discovery(key, receipt)
    return receipt


def advance_sixth_xianqiao_opening(context, *, max_steps=50):
    """Spend only affordable overflow on the known sixth hole at realm250.

    Rebuild each level's native cost and reserve the next realm's required EXP.
    A pre-tap intent survives uncertain results. Insufficient overflow returns
    pending, never a completed breakthrough; no purchases or book use occur.
    """
    from datetime import datetime
    from backend.core.fanxiu.instrumentation.xianqiao_opening import read_xianqiao_opening_dialog_snapshot
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_world_facts, record_world_discovery
    if type(max_steps) is not int or not 1 <= max_steps <= 50:
        raise ValueError('max_steps must be an integer in 1..50')
    receipts = []
    role, cost = _sixth_opening_facts()
    prefix = f"xianqiao-open:{role['role_id']}:2:6:"
    for key, value in read_world_facts().get('discoveries', {}).items():
        if key.startswith(prefix) and value.get('outcome') == 'commit_pending':
            receipts.append(accept_sixth_xianqiao_opening_transaction(key))
    for _ in range(max_steps):
        role, cost = _sixth_opening_facts()
        if cost['opened'] or role['overflow_exp'] < cost['next_level_exp']:
            break
        frame = context.cur_frame(update=True)
        if not context.match_view(950, frame_data_url=frame)[0]:
            from .bottle_menu import enter_xianqiao_page
            yield from enter_xianqiao_page(context)
            yield from context.wait_click_then_scene(484, '玉京', [485], timeout=30)
            yield from context.wait_click_then_scene(485, '瑶光', [950], timeout=30)
        dialog = read_xianqiao_opening_dialog_snapshot()
        role, cost = _sixth_opening_facts()
        if (dialog.get('complete') is not True or dialog.get('enough') is not True
                or (dialog['system_type'], dialog['part_id'], dialog['active_level'], dialog['opened'])
                != (2, 6, cost['active_level'], False)
                or any(dialog[k] != cost[k] for k in ('pid', 'process_start_ticks'))
                or role['overflow_exp'] < cost['next_level_exp']):
            raise RuntimeError('通窍窗口、进度、预算未一致，拒绝消费')
        key = prefix + str(cost['active_level'])
        if read_world_facts().get('discoveries', {}).get(key):
            raise RuntimeError('已有此等级通窍意图，禁止重复消费')
        record_world_discovery(key, {'outcome':'commit_pending', 'role_before':role,
                                    'cost_before':cost, 'created_at':datetime.now().astimezone().isoformat()})
        yield from context.wait_click(950, '通窍（消耗修为）')
        yield from context.wait_action_settle(2)
        receipts.append(accept_sixth_xianqiao_opening_transaction(key))
    role, cost = _sixth_opening_facts()
    if context.match_view(950, frame_data_url=context.cur_frame(update=True))[0]:
        yield from context.wait_click_then_scene(950, '背景关闭', [485], timeout=30)
    yield from context.go_scene(34)
    return {'result':'success' if cost['opened'] else 'pending',
            'outcome':'sixth_hole_opened' if cost['opened'] else 'opening_progress_pending',
            'transactions':receipts, 'role':role, 'cost':cost,
            'full_open_gap':max(0, cost['remaining_exp']-role['overflow_exp']), 'scene':34}


def prepare_sixth_xianqiao_daily(context):
    """Daily budget preparation; completion never means realm breakthrough.

    The accepted realm250 path uses only currently affordable overflow. Later
    days retry after ordinary experience income; other realms take no actions.
    """
    from backend.core.fanxiu.instrumentation.role_progression import read_role_progression_snapshot
    role = read_role_progression_snapshot()
    if role.get('complete') is not True:
        raise RuntimeError('境界准备缺少完整角色进度')
    if role['level'] != 250:
        return {'result':'success', 'outcome':'outside_realm250_preparation', 'level':role['level']}
    progress = yield from advance_sixth_xianqiao_opening(context)
    return {**progress, 'result':'success', 'outcome':'affordable_opening_preparation_complete',
            'sixth_hole_opened':progress['cost']['opened'], 'realm_breakthrough_completed':False}
