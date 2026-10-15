"""境界250→251：第六窍通窍准备与原生全部条件满足后的突破（LeiJieView #949）。"""

import time

STAGE_ID = 'realm250-sixth-hole-preparation'
STAGE_VERSION = '2'


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


REALM_BREAKTHROUGH_INTENT_PREFIX = 'realm-breakthrough:'
# The only implemented path: 250->251 with no tribulation dungeon (dujie 0).
SUPPORTED_REALM_BREAKTHROUGHS = {(250, 251): 0}


def prepare_sixth_xianqiao_daily(context):
    """Daily realm250 chain: close prior breakthrough intents, open affordable holes, break through.

    Any earlier breakthrough intent is reconciled from fresh native facts first
    (even if the level already moved), so a timed-out tap never stays open.
    Opening uses only currently affordable overflow. When the native next-realm
    conditions and EXP are all met in one process, the owner-authorized
    breakthrough runs once; otherwise the exact gap is returned as pending.
    """
    from backend.core.fanxiu.instrumentation.role_progression import read_role_progression_snapshot
    reconciled = reconcile_realm_breakthrough_intents()
    role = read_role_progression_snapshot()
    if role.get('complete') is not True:
        raise RuntimeError('境界准备缺少完整角色进度')
    if role['level'] != 250:
        # A breakthrough verified now or earlier (incl. recovery of a timed-out
        # tap) is reported as completed with its receipt; no new tap occurs.
        receipt = realm_breakthrough_receipt(role)
        pending = [row for row in reconciled if row['outcome'] == 'pending']
        outcome = ('realm_breakthrough_completed' if receipt else
                   'breakthrough_intent_pending' if pending else 'outside_realm250_preparation')
        return {'result':'success', 'outcome':outcome, 'level':role['level'],
                'realm_breakthrough_completed':bool(receipt), 'realm_breakthrough_receipt':receipt,
                'breakthrough_intents': reconciled}
    progress = yield from advance_sixth_xianqiao_opening(context)
    breakthrough = yield from execute_realm_breakthrough(context)
    return {**progress, 'result':'success', 'outcome':'affordable_opening_preparation_complete',
            'sixth_hole_opened':progress['cost']['opened'],
            'realm_breakthrough_completed':breakthrough['completed'],
            'realm_breakthrough': breakthrough, 'breakthrough_intents': reconciled}


def realm_breakthrough_admission():
    """Full native breakthrough gate; read-only, never starts it.

    ``ready`` only when EXP and every native condition are met in one process;
    pending/unknown name the unmet or unread condition.
    """
    from backend.core.fanxiu.instrumentation.realm_breakthrough import read_realm_breakthrough_requirements_snapshot
    snapshot = read_realm_breakthrough_requirements_snapshot()
    blockers = [row.get('description') or row['condition'] for row in snapshot.get('conditions') or []
                if row.get('met') is not True]
    if snapshot.get('exp_ready') is False:
        blockers.append('修为未达下一境界')
    if snapshot.get('state') == 'unknown' and snapshot.get('reason'):
        blockers.append(f"条件读取不完整：{snapshot['reason']}")
    return {'state': snapshot.get('state', 'unknown'), 'completed': False, 'blockers': blockers,
            'requirements': snapshot}


def realm_breakthrough_path_supported(requirements, window):
    """True only for an implemented path with matching native window semantics.

    level/target must be a supported pair whose configured dujie is exactly the
    expected integer; the open LeiJieView must report canUp and observe the
    same dungeon value (0 here). Unknown or positive dungeons never pass.
    """
    def exact_int(value):
        return type(value) is int
    pair = (requirements.get('level'), requirements.get('target_level'))
    expected = SUPPORTED_REALM_BREAKTHROUGHS.get(pair)
    observed = window.get('dungeon_id_observed')
    return (expected is not None and exact_int(requirements.get('tribulation_dungeon_id'))
            and requirements['tribulation_dungeon_id'] == expected
            and window.get('complete') is True and window.get('conditions_met') is True
            and window.get('implementation') == 'LeijieViewNew'
            and exact_int(observed) and observed == expected)


def decide_realm_breakthrough_outcome(intent, role, *, require_same_process):
    """Classify one breakthrough intent against a fresh native role snapshot.

    verified: same role at the target level (and, during the live action, the
    same process as the pre-click facts). Anything else stays pending with a
    reason: an unchanged level/EXP only means no effect is observed yet, not
    that the tap was rejected or never committed, so it never releases the
    intent. Another role or a mixed process never verifies.
    """
    before = intent.get('role_before') or {}
    if role.get('complete') is not True:
        return 'pending', 'role_snapshot_incomplete'
    if not before.get('role_id') or role.get('role_id') != before.get('role_id'):
        return 'pending', 'different_or_unknown_role'
    process = (role.get('evidence') or {}).get('pid'), (role.get('evidence') or {}).get('process_start_ticks')
    same_process = process == (before.get('pid'), before.get('process_start_ticks'))
    if require_same_process and not same_process:
        return 'pending', 'process_changed_during_action'
    if type(intent.get('target_level')) is int and role.get('level') == intent['target_level']:
        return 'verified', 'native_level_reached_target'
    return 'pending', 'target_level_not_observed'


def reconcile_realm_breakthrough_intents():
    """Formal recovery: close commit_pending breakthrough intents from fresh native facts.

    Never taps. Only an intent whose own role is now at its target level (fresh
    complete facts, any process) is recorded verified; everything else, incl.
    old records missing role/target fields, stays commit_pending with a reason.
    """
    from datetime import datetime
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_world_facts, record_world_discovery
    from backend.core.fanxiu.instrumentation.role_progression import read_role_progression_snapshot
    results = []
    pending = {key: value for key, value in read_world_facts().get('discoveries', {}).items()
               if key.startswith(REALM_BREAKTHROUGH_INTENT_PREFIX) and isinstance(value, dict)
               and value.get('outcome') == 'commit_pending'}
    if not pending:
        return results
    role = read_role_progression_snapshot()
    for key, intent in pending.items():
        outcome, reason = decide_realm_breakthrough_outcome(intent, role, require_same_process=False)
        if outcome == 'verified':
            record_world_discovery(key, {**intent, 'outcome': 'verified', 'reason': reason, 'role_after': role,
                                         'reconciled_at': datetime.now().astimezone().isoformat()})
        results.append({'key': key, 'outcome': outcome, 'reason': reason})
    return results


def realm_breakthrough_receipt(role):
    """The verified breakthrough receipt that produced this role's current level, if any."""
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_world_facts
    if role.get('complete') is not True or not role.get('role_id'):
        return None
    for key, value in read_world_facts().get('discoveries', {}).items():
        if (key.startswith(REALM_BREAKTHROUGH_INTENT_PREFIX) and isinstance(value, dict)
                and value.get('outcome') == 'verified' and value.get('target_level') == role.get('level')
                and (value.get('role_before') or {}).get('role_id') == role['role_id']):
            return {'receipt_key': key, **{k: value.get(k) for k in (
                'target_level', 'role_before', 'reason', 'verified_at', 'reconciled_at', 'created_at')}}
    return None


def execute_realm_breakthrough(context, *, level_timeout_seconds=180):
    """Owner-authorized realm250->251 breakthrough through the native LeiJieView.

    Gate: read_realm_breakthrough_requirements_snapshot ready (role, EXP, every
    Realm.RealmResource[251] condition, one process), the open #949 LeiJieView
    complete with canUp in the same process, and a supported path (dujie 0).
    #949 is recognised by state-invariant texts: 前往完成 also shows on met rows
    (real frame; LeijieItem only recolours the count). One persisted intent per
    role/level precedes the single「突破」tap and is never replayed; acceptance
    requires the same role and process at the target level, else the scene and
    intent are kept for reconcile_realm_breakthrough_intents. The positive
    branch has no real acceptance until it first runs.
    """
    from datetime import datetime
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_world_facts, record_world_discovery
    from backend.core.fanxiu.data_annotation.scene_diagnostics import save_scene_diagnostic_frame
    from backend.core.fanxiu.instrumentation.role_progression import read_role_progression_snapshot

    reconciled = reconcile_realm_breakthrough_intents()
    if any(row['outcome'] == 'pending' for row in reconciled):
        return {'state': 'intent_pending', 'completed': False, 'clicks': 0, 'outcome': 'breakthrough_intent_pending',
                'blockers': [f"未结突破意图：{row['reason']}" for row in reconciled if row['outcome'] == 'pending'],
                'breakthrough_intents': reconciled}
    admission = realm_breakthrough_admission()
    requirements = admission['requirements']
    if admission['state'] != 'ready':
        return {**admission, 'outcome': f"breakthrough_{admission['state']}", 'clicks': 0,
                'breakthrough_intents': reconciled}
    if (requirements.get('level'), requirements.get('target_level')) not in SUPPORTED_REALM_BREAKTHROUGHS:
        return {**admission, 'state': 'unsupported_path', 'outcome': 'breakthrough_unsupported_path', 'clicks': 0}
    role = read_role_progression_snapshot()
    process = (role.get('evidence') or {}).get('pid'), (role.get('evidence') or {}).get('process_start_ticks')
    if (role.get('complete') is not True or role.get('role_id') != requirements.get('role_id')
            or role['level'] != requirements['level']
            or process != (requirements['pid'], requirements['process_start_ticks'])):
        raise RuntimeError('突破前角色、等级或进程与准入快照不一致，拒绝点击')
    level, target = requirements['level'], requirements['target_level']
    key = f"{REALM_BREAKTHROUGH_INTENT_PREFIX}{role['role_id']}:{level}->{target}"
    window = yield from open_realm_breakthrough_conditions(context)
    if (not realm_breakthrough_path_supported(requirements, window)
            or (window.get('pid'), window.get('process_start_ticks')) != process):
        yield from context.wait_click(949, '返回')
        yield from context.go_scene(34)
        return {**admission, 'state': 'native_window_not_ready', 'outcome': 'breakthrough_window_not_ready',
                'window': window, 'clicks': 0}
    before = {'role_id': role['role_id'], 'level': level, 'current_exp': role['current_exp'],
              'pid': process[0], 'process_start_ticks': process[1]}
    intent = {'outcome': 'commit_pending', 'role_before': before, 'target_level': target,
              'requirements': requirements, 'window': window,
              'created_at': datetime.now().astimezone().isoformat()}
    if key in read_world_facts().get('discoveries', {}):
        raise RuntimeError('该角色此境界已有突破意图记录，禁止覆盖或重复点击')
    record_world_discovery(key, intent)
    context.click_shape_center(949, '突破')
    frames = []
    deadline = time.monotonic() + max(10, int(level_timeout_seconds))
    outcome, reason, after = 'pending', 'not_observed', None
    while time.monotonic() < deadline:
        yield from context.wait_action_settle(2.0)
        after = read_role_progression_snapshot()
        outcome, reason = decide_realm_breakthrough_outcome(intent, after, require_same_process=True)
        if outcome == 'verified' or reason == 'process_changed_during_action':
            break
        if len(frames) < 6:
            frames.append(save_scene_diagnostic_frame(context.runner, context.cur_frame(update=True),
                                                      kind='realm_breakthrough', label=f'{level}->{target}',
                                                      expected_scene_ids=[949, 34]))
    if outcome != 'verified':
        raise RuntimeError(f'点击突破后未在同一角色/进程验收到 {target}（{reason}）；保留现场与意图，诊断帧 {frames}')
    record_world_discovery(key, {**intent, 'outcome': 'verified', 'reason': reason, 'role_after': after,
                                 'verified_at': datetime.now().astimezone().isoformat(),
                                 'diagnostic_frames': frames})
    yield from context.go_scene(34)
    return {**admission, 'state': 'completed', 'completed': True, 'outcome': 'realm_breakthrough_completed',
            'level_before': level, 'level_after': after['level'], 'clicks': 1, 'receipt_key': key}
