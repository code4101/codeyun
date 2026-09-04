from __future__ import annotations

from pathlib import Path
from typing import Iterable

from backend.core.codex import CodexDispatch, CodexEscalationRequest, escalate_to_codex


def escalate_persistent_scene_unknown(
    *,
    task_id: str,
    task_label: str,
    entry_id: str,
    expected_scene_ids: Iterable[int],
    evidence_frame_path: str,
    asset_tree_path: Path | None,
    layer0_wait_seconds: float,
    unmatched_guard_seconds: float,
) -> CodexDispatch:
    """Stop engineering dispatch and hand one persistent unknown to Codex.

    This function runs at the end of the failing Scheduler Cell. It disables
    future Job dispatch directly instead of interrupting the Cell that is
    currently unwinding. The independent Agent must wait for that Cell to end
    before it uses the shared Kernel or GUI.
    """

    normalized_task_id = str(task_id or "").strip()
    normalized_evidence = str(evidence_frame_path or "").strip()
    if not normalized_task_id:
        raise ValueError("场景异常升级缺少 Scheduler Job id")
    if not normalized_evidence or not Path(normalized_evidence).is_file():
        raise ValueError("场景异常升级缺少可读取的原始帧")

    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
        set_scheduler_job_group_enabled,
    )

    # The queue must be stopped before the asynchronous Agent can start.
    set_scheduler_job_group_enabled(False)

    scene_ids = tuple(dict.fromkeys(int(scene_id) for scene_id in expected_scene_ids))
    metadata_path = Path(normalized_evidence).with_suffix(".json")
    evidence = [
        f"持续未匹配原始帧：{normalized_evidence}",
        f"本次业务 Layer 0：{', '.join(f'#{scene_id}' for scene_id in scene_ids)}",
        f"Scheduler Job：{normalized_task_id}",
        f"凡修入口：{entry_id or 'unknown'}",
    ]
    if metadata_path.is_file():
        evidence.append(f"识别证据元数据：{metadata_path}")
    if isinstance(asset_tree_path, Path):
        evidence.append(f"资产树：{asset_tree_path}")

    request = CodexEscalationRequest(
        title=f"凡修场景持续未匹配：{task_label}",
        problem=(
            f"正式 Scheduler Job {normalized_task_id} 在 wait_scene 的业务 Layer 0 "
            f"等待 {layer0_wait_seconds:.1f}s 后，Layer 1/2 及随后 "
            f"{unmatched_guard_seconds:.0f}s 的完整分层重试仍全部未命中。"
        ),
        objective=(
            "解决导致凡修工程无法继续运行的真实根因，必要时沿证据扩大到整个 CodeYun 的"
            "代码、数据、调度或运行环境；把新认知工程化收敛，并通过正式新 attempt 完成"
            "真实业务复验，恢复凡修稳定工程运行。"
        ),
        suggested_focus=(
            "可先按 fanxiu skill 的《场景识别》未匹配处理流程读取第一现场",
            "可调用 render_unknown_scene_overview 生成标准多候选总览",
            "可优先区分已有场景标识召回缺口、资产/Shape 缺失与全新场景",
        ),
        evidence=tuple(evidence),
        attempted_actions=(
            "已按业务 Layer 0 预算持续刷新观测并执行可处理弹窗",
            "已在同一事实帧依次执行全局 Layer 1 与 Layer 2 识别",
            f"已从行为树 tick 重跑完整分层识别 {unmatched_guard_seconds:.0f}s",
            "仍无正式场景身份，未使用 Layer 3 相似候选冒充识别结果",
        ),
        recovery_instructions=(
            f"先确认旧 Job attempt {normalized_task_id} 已结束且 Kernel 空闲。修复并按需重载后，"
            f"从 Scheduler 正式入口重新提交 Job {normalized_task_id} 的完整新 attempt；"
            "不得恢复旧 Cell、generator 或 scene cursor。新 attempt 形成合法业务终态后，"
            "再重新启用 Job 派发。"
        ),
        completion_criteria=(
            "根因落到识别规则、场景资产、Shape 标注或明确外部条件之一",
            "修复进入正式代码或获授权的资产，不留下临时识别旁路",
            "同一正式 Scheduler Job 的新 attempt 通过真实画面形成合法业务终态",
            "确认凡修已恢复稳定工程运行、next_time 正确写回并重新启用工程 Job 派发",
        ),
        constraints=(
            "原始帧是第一现场，不得用重新截图替代",
            "证据路径、疑似问题层和恢复入口只是建议切入点，不限制 Agent 的调查与修复范围",
            "多候选总览不叠加 Shape，相似候选只供 Agent 判断，不是识别结果",
            "未经点名授权不得修改既有 scene、Shape、阈值或跳转关系",
            "禁止 Android Back；没有可靠返回 Shape 时保留现场并请求人工授权",
            "运行权互斥只约束共享 Kernel 与游戏操作；不限制 Agent 先行读取证据、代码和状态",
            "继续遵守代币白名单及其它稳定业务安全门禁",
        ),
    )
    return escalate_to_codex(request)


__all__ = ["escalate_persistent_scene_unknown"]
