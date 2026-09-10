from __future__ import annotations

from pathlib import Path
from typing import Iterable

from backend.core.codex import CodexDispatch, CodexEscalationRequest, escalate_to_codex


def scene_repair_guidance(
    *, scene_id: int | None, evidence_frame_path: str | None,
    expected_scene_ids: Iterable[int] = (),
) -> dict[str, str]:
    """One diagnostic route for both the current AI and a dispatched repair AI.

    Classify only the observed failure, not its unproven cause: unknown does not
    establish a new scene or a popup. Rendering consumes the original frame.
    This function neither renders nor dispatches or interacts with the game.
    """
    document = (Path(__file__).resolve().parents[5] / "skills" / "凡修" /
                "references" / "接口层" / "场景识别.md")
    evidence = repr(str(evidence_frame_path)) if evidence_frame_path else "exc.evidence_frame_path"
    scenes = list(dict.fromkeys(int(value) for value in expected_scene_ids))
    unknown = scene_id is None
    return {
        "problem_code": "scene.persistent_unknown" if unknown else "scene.repair_required",
        "problem": ("场景持续未匹配；尚不能断定是新场景、干扰弹窗或已有标识漏识别"
                    if unknown else f"已识别场景 #{scene_id} 的资产或动作契约无法继续"),
        "document": f"{document.as_posix()}#{'未匹配处理' if unknown else 'layer-2-命中的失败诊断'}",
        "diagnostic_call": (
            f"context.render_unknown_scene_overview({evidence}, {scenes!r}, top_k=2)"
            if unknown else f"context.render_scene_comparison({evidence}, {int(scene_id)})"
        ),
        "next_step": "读取标准对比图后判断复用、修订或新增资产；不能仅搜资产标题或按相似度认定身份",
        "recovery": "保留原始帧；故障 Cell 结束后由持有运行权的 AI 修复并在新 Cell 复验，不续跑旧 generator",
    }


def format_scene_repair_guidance(guidance: dict[str, str]) -> str:
    """Keep the actionable route visible even when callers retain only str(exc)."""
    labels = {"problem_code": "问题类型", "problem": "已知故障",
              "document": "请 AI 查阅", "diagnostic_call": "按手册调用",
              "next_step": "判断步骤", "recovery": "恢复边界"}
    return "\n" + "\n".join(f"{labels[key]}：{value}" for key, value in guidance.items())


class SceneRepairRequired(RuntimeError):
    """A recognized scene cannot safely progress with its current assets."""

    def __init__(self, message: str, *, scene_id: int | None, evidence_frame_path: str | None,
                 dispatch: CodexDispatch | None = None, escalation_error: str | None = None):
        self.repair_guidance = scene_repair_guidance(
            scene_id=scene_id, evidence_frame_path=evidence_frame_path,
        )
        super().__init__(message + format_scene_repair_guidance(self.repair_guidance))
        self.scene_id = scene_id
        self.evidence_frame_path = evidence_frame_path
        self.codex_dispatch_id = dispatch.dispatch_id if dispatch else None
        self.codex_request_path = dispatch.request_path if dispatch else None
        self.codex_escalation_error = escalation_error


def escalate_persistent_scene_unknown(
    *,
    task_id: str,
    task_label: str,
    entry_id: str,
    expected_scene_ids: Iterable[int],
    evidence_frame_path: str | None,
    asset_tree_path: Path | None,
    layer0_wait_seconds: float,
    unmatched_guard_seconds: float,
    attempt_id: str = "",
) -> CodexDispatch | None:
    """Escalate sustained unknown; an existing AI owner handles its own error."""
    return _escalate_scene_repair(
        task_id=task_id, task_label=task_label, entry_id=entry_id,
        expected_scene_ids=expected_scene_ids, evidence_frame_path=evidence_frame_path,
        asset_tree_path=asset_tree_path,
        problem=(f"正式 Scheduler Job {task_id} 在 wait_scene 的业务 Layer 0 "
                 f"等待 {layer0_wait_seconds:.1f}s 后，Layer 1/2 及随后 "
                 f"{unmatched_guard_seconds:.0f}s 的完整分层重试仍全部未命中。"),
        scene_id=None, attempt_id=attempt_id,
    )


def escalate_scene_repair_required(
    *, task_id: str, task_label: str, entry_id: str, scene_id: int | None,
    expected_scene_ids: Iterable[int], evidence_frame_path: str | None,
    asset_tree_path: Path | None, reason: str, attempt_id: str = "",
) -> CodexDispatch | None:
    """Hand missing exits or exhausted popup/navigation actions to AI once.

    No GUI operations are performed. Existing executable actions must have
    been exhausted by the caller; a low recognition score alone is not a fault.
    """
    return _escalate_scene_repair(
        task_id=task_id, task_label=task_label, entry_id=entry_id,
        expected_scene_ids=expected_scene_ids, evidence_frame_path=evidence_frame_path,
        asset_tree_path=asset_tree_path, scene_id=scene_id, problem=reason, attempt_id=attempt_id,
    )


def _escalate_scene_repair(
    *, task_id: str, task_label: str, entry_id: str,
    expected_scene_ids: Iterable[int], evidence_frame_path: str | None,
    asset_tree_path: Path | None, scene_id: int | None, problem: str, attempt_id: str,
) -> CodexDispatch | None:
    """Report a scene incident without changing scheduler ownership.

    Requesting an Agent does not prove that one has started or taken control.
    Only the active Agent/user may explicitly take_ai_control; evidence or
    dispatch failures must leave engineering mode and its retry policy intact.
    """

    normalized_task_id = str(task_id or "").strip()
    normalized_evidence = str(evidence_frame_path or "").strip()
    if not normalized_task_id:
        raise ValueError("场景异常升级缺少 Scheduler Job id")
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
        read_scheduler_settings,
    )

    # The unique Kernel serializes incidents. A disabled group already belongs
    # to AI: propagate to that owner instead of spawning recursive repair agents.
    if not read_scheduler_settings().get("job_group_enabled", True):
        return None
    if not normalized_evidence or not Path(normalized_evidence).is_file():
        raise ValueError("场景异常升级缺少可读取的原始帧；调度模式未改变")

    scene_ids = tuple(dict.fromkeys(int(scene_id) for scene_id in expected_scene_ids))
    guidance = scene_repair_guidance(
        scene_id=scene_id, evidence_frame_path=normalized_evidence,
        expected_scene_ids=scene_ids,
    )
    metadata_path = Path(normalized_evidence).with_suffix(".json")
    evidence = [
        f"失败原始帧：{normalized_evidence}",
        f"本次业务 Layer 0：{', '.join(f'#{scene_id}' for scene_id in scene_ids)}",
        f"Scheduler Job：{normalized_task_id}",
        f"凡修入口：{entry_id or 'unknown'}",
        f"失败 attempt：{attempt_id or '未提供'}",
    ]
    if metadata_path.is_file():
        evidence.append(f"识别证据元数据：{metadata_path}")
    if isinstance(asset_tree_path, Path):
        evidence.append(f"资产树：{asset_tree_path}")

    request = CodexEscalationRequest(
        title=f"凡修场景{'持续未匹配' if scene_id is None else f' #{scene_id} 需修复'}：{task_label}",
        problem=problem,
        objective=(
            "解决导致凡修工程无法继续运行的真实根因，必要时沿证据扩大到整个 CodeYun 的"
            "代码、数据、调度或运行环境；把新认知工程化收敛，并通过正式新 attempt 完成"
            "真实业务复验，恢复凡修稳定工程运行。"
        ),
        suggested_focus=(
            guidance["problem"],
            f"先读取 {guidance['document']}",
            guidance["diagnostic_call"],
            guidance["next_step"],
        ),
        evidence=tuple(evidence),
        attempted_actions=(
            problem,
            "工程仅使用已标注动作；未猜坐标、未使用 Android Back",
        ),
        recovery_instructions=(
            "工程未切换调度模式。你实际开始接管时，先调用 "
            "backend.core.fanxiu.data_annotation.kernel_scheduler_control.take_ai_control(entry_id) "
            f"取得运行权（entry_id={entry_id!r}），再操作共享 Kernel 或游戏。"
            f"先确认旧 Job {normalized_task_id} 的 attempt_id 已清空、last_result 已终态且 Kernel 空闲。修复并按需重载后，"
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
            "已授权通过正式资产 API 自主新增或修复 scene、Shape、识别与跳转关系，无需再次请示补标注",
            "缺出口时按实际控件补返回/关闭；没有明确控件时取证标注稳定非交互背景，实点验证落点",
            "新干扰弹窗需纳入弹窗分组或显式中断映射，让下次由工程自动关闭；不得新增 Layer 1 枢纽",
            "禁止 Android Back；无法证明安全动作时保留现场并说明真实阻塞，不以盲点代替标注",
            "运行权互斥只约束共享 Kernel 与游戏操作；不限制 Agent 先行读取证据、代码和状态",
            "继续遵守代币白名单及其它稳定业务安全门禁",
        ),
    )
    return escalate_to_codex(request)


__all__ = ["SceneRepairRequired", "scene_repair_guidance", "format_scene_repair_guidance",
           "escalate_persistent_scene_unknown", "escalate_scene_repair_required"]
