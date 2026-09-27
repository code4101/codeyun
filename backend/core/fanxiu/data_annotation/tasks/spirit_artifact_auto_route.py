"""自动洗炼路线占位；待用户讲解后实现准入、配置和执行闭环。"""


def try_spirit_artifact_auto_route() -> dict:
    """当前无副作用 pass，交给手动路线；不声称已检查游戏入口是否开放。"""
    return {'status': 'pass', 'reason': 'awaiting_auto_route_instruction', 'consumed': 0}
