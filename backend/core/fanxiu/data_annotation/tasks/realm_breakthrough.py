"""境界突破研发组件：只打开已核实的250→251条件分支。"""


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
    for _ in range(30):
        changed = yield from context.scroll_shape_content(20, "菜单", direction="left")
        if not changed:
            break
    frame = context.cur_frame(update=True)
    context.click_ocr_text(20, "修炼", in_shapes=["菜单"], frame_data_url=frame,
                           anchor="top_center", offset=(0, -1), offset_unit="height")
    yield from context.wait_scene_exact([405], timeout=25, observation_scenes=[405, 20])
    yield from context.wait_click(405, "突破境界")
    yield from context.wait_scene_exact([949], timeout=25, observation_scenes=[949, 364, 405])
    snapshot = read_realm_breakthrough_conditions_snapshot()
    if snapshot.get("complete") is not True:
        raise RuntimeError("境界条件窗实时判断不完整，保留现场")
    return snapshot


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
    yield from context.wait_scene_exact([484], timeout=20)
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
