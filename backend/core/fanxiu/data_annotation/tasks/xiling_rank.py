"""本期洗灵任务完整闭环：选部件、洗炼达档、领奖、稳定离场。"""
from pathlib import Path
from .spirit_artifact_manual_rank import run_spirit_artifact_manual_rank
from .xiling_task_rewards import claim_xiling_task_rewards
from ...instrumentation.xiling_task_rewards import discover_xiling_task_spec, read_xiling_task_progress


def complete_xiling_rank(context, execute, *, activity_id: int, stop_at: float,
                        evidence_path: Path, initial_sample: dict | None = None) -> dict:
    """已授权消耗洗灵奇石与无双石；按本期最高任务档幂等停止。

    initial_sample 只接受调用方本期已核验的小批实耗和任务增量，跨期不能复用。
    截止时间由调度调用方提供；暂停不表示业务完成。异常保留游戏现场。
    """
    result = run_spirit_artifact_manual_rank(context, execute, activity_id=activity_id,
        stop_at=stop_at, evidence_path=evidence_path, max_rolls=1000,
        use_automatic=True, initial_sample=initial_sample)
    if result['status'] != 'complete':
        return result
    execute(context.go_scene(34))
    rewards = execute(claim_xiling_task_rewards(context, activity_id=activity_id))
    progress = read_xiling_task_progress(discover_xiling_task_spec(activity_id))
    if not progress['goal_reached'] or progress['claimable_task_ids'] or progress['pending_task_ids']:
        raise RuntimeError('洗灵最高档与全部奖励尚未完成，不能宣告闭环')
    execute(context.go_scene(34))
    landed = execute(context.wait_scene_exact([34], timeout=12))
    return {**result, 'rewards': rewards, 'task_progress': progress, 'final_scene': int(landed.scene_id)}
