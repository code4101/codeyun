"""仙府玄机阁的独立业务子任务；周期和完成记录由资源聚合作业持有。"""
from __future__ import annotations

STAGE_ID = "xianfu-science"
STAGE_VERSION = "2"  # All affordable nodes, not the historical first-node stop.


def execute_xianfu_science_task(runner, ctx, payload, stop_event):
    """Standalone Task adapter; the aggregate reuses the context component below."""
    context = runner._behavior_tree_context(ctx, stop_event=stop_event)
    return (yield from develop_xianfu_science(context))


def develop_xianfu_science(context):
    """Consume eligible upgrades, verify the tree terminal, and return to world."""
    from backend.core.fanxiu.runtime_gui.xianfu_science import fill_xianfu_science_tree
    yield from context.go_scene(768)
    result = yield from fill_xianfu_science_tree(context)
    if result.get("reason") not in {"全树已满", "无可升级节点"}:
        raise RuntimeError(f"玄机阁尚未取得全树终态：{result}")
    yield from context.go_scene(34)
    yield from context.wait_scene([34], wait=20)
    return {"result": "success", "outcome": "complete", "tree": result}
