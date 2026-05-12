from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import threading
import time
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock, Timeout
from sqlmodel import Session

from pyxllib.prog.behavior_tree import Action, BehaviorTreeRunner, IdleUntilNextWake, MemorySelector, Root, Sequence, Status

from backend.core.background_task_queue import background_task_queue
from backend.core.fanbei_attendance_schedule import (
    FANBEI_ATTENDANCE_EVENING_RUN_TIME,
    FANBEI_ATTENDANCE_EVENING_TASK_KEY,
    FANBEI_ATTENDANCE_MORNING_RUN_TIME,
    FANBEI_ATTENDANCE_MORNING_TASK_KEY,
    enqueue_fanbei_attendance_evening_steps,
    enqueue_fanbei_attendance_morning_steps,
)
from backend.core.settings import get_settings
from backend.models import AppSetting


TaskAction = Callable[[], str | None]


@dataclass(frozen=True)
class BackgroundTaskSpec:
    key: str
    title: str
    category: str
    description: str
    schedule_label: str
    retry_label: str
    action: TaskAction
    manual_warning: str = ""


class _StoppableBehaviorTreeRunner(BehaviorTreeRunner):
    def __init__(
        self,
        *args: Any,
        stop_event: threading.Event,
        wake_event: threading.Event,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._stop_event = stop_event
        self._wake_event = wake_event

    def sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, float(seconds))
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            wait_seconds = min(remaining, 1.0)
            self._wake_event.wait(wait_seconds)
            if self._wake_event.is_set():
                self._wake_event.clear()
                return


def _setting_key(task_key: str) -> str:
    return f"background_task.{task_key}.enabled"


def _deleted_setting_key(task_key: str) -> str:
    return f"background_task.{task_key}.deleted"


def _is_task_deleted(task_key: str, session: Session | None = None) -> bool:
    def _read(current_session: Session) -> bool:
        row = current_session.get(AppSetting, _deleted_setting_key(task_key))
        return bool(row and isinstance(row.value, dict) and row.value.get("deleted", False))

    if session is not None:
        return _read(session)

    from backend.db import engine

    with Session(engine) as current_session:
        return _read(current_session)


def _is_task_enabled(task_key: str) -> bool:
    from backend.db import engine

    with Session(engine) as session:
        if _is_task_deleted(task_key, session):
            return False
        row = session.get(AppSetting, _setting_key(task_key))
        if row and isinstance(row.value, dict):
            return bool(row.value.get("enabled", False))
        if task_key == "storage_analysis":
            storage_row = session.get(AppSetting, "storage.schedule")
            if storage_row and isinstance(storage_row.value, dict):
                return bool(storage_row.value.get("schedule_enabled", False))
    return False


def set_background_task_enabled(task_key: str, enabled: bool) -> None:
    from backend.db import engine

    with Session(engine) as session:
        row = session.get(AppSetting, _setting_key(task_key))
        if row is None:
            row = AppSetting(key=_setting_key(task_key))
        row.value = {"enabled": bool(enabled)}
        row.updated_at = time.time()
        session.add(row)
        session.commit()


def set_background_task_deleted(task_key: str, deleted: bool = True) -> None:
    from backend.db import engine

    with Session(engine) as session:
        deleted_row = session.get(AppSetting, _deleted_setting_key(task_key))
        if deleted_row is None:
            deleted_row = AppSetting(key=_deleted_setting_key(task_key))
        deleted_row.value = {"deleted": bool(deleted)}
        deleted_row.updated_at = time.time()
        session.add(deleted_row)

        if deleted:
            enabled_row = session.get(AppSetting, _setting_key(task_key))
            if enabled_row is None:
                enabled_row = AppSetting(key=_setting_key(task_key))
            enabled_row.value = {"enabled": False}
            enabled_row.updated_at = time.time()
            session.add(enabled_row)

        session.commit()


def _enqueue_storage_analysis() -> str:
    from backend.api.admin import scheduled_analysis_job

    return background_task_queue.enqueue("storage_analysis", scheduled_analysis_job)


def _enqueue_attendance_summary_if_due() -> str | None:
    if dt.date.today().day != 27:
        return None
    from backend.api.note_sheets import run_attendance_summary_template_job

    return background_task_queue.enqueue("attendance_summary_monthly_templates", run_attendance_summary_template_job)


def _enqueue_note_metadata_feedback() -> str | None:
    current_time = dt.datetime.now().time()
    if not (dt.time(0, 0) <= current_time <= dt.time(5, 59, 59)):
        return None
    from backend.core.note_metadata_feedback import create_note_metadata_feedback_optimization_run
    from backend.db import engine

    with Session(engine) as session:
        run = create_note_metadata_feedback_optimization_run(
            session,
            trigger_reason="auto_threshold",
            enqueue=True,
            require_auto_conditions=True,
        )
        return run.queue_task_id if run is not None else None


def _enqueue_codex_diary() -> str | None:
    from backend.api.notes import maybe_enqueue_codex_diary_yesterday_import

    return maybe_enqueue_codex_diary_yesterday_import(trigger_reason="scheduled")


def _enqueue_auto_git() -> str | None:
    from backend.core.auto_git_commit import create_auto_git_commit_run, mark_stale_auto_git_commit_runs
    from backend.db import engine
    from backend.models import AutoGitCommitRun
    from sqlmodel import select

    with Session(engine) as session:
        mark_stale_auto_git_commit_runs(session, queue_snapshot=background_task_queue.snapshot())
        if session.exec(select(AutoGitCommitRun.id).where(AutoGitCommitRun.status.in_(["pending", "running"])).limit(1)).first():
            return None
        run = create_auto_git_commit_run(session, trigger_reason="scheduled", enqueue=True)
        return run.queue_task_id


BACKGROUND_TASK_SPECS: tuple[BackgroundTaskSpec, ...] = (
    BackgroundTaskSpec(
        key="auto_git_commit",
        title="自动 Git 提交",
        category="Git",
        description="凌晨检查 pyxllib、xlproject、codeyun；pyxllib/xlproject 先调用 Codex CLI review 和优化，codeyun 只生成提交信息并提交。",
        schedule_label="每天 03:20",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_auto_git,
        manual_warning="会调用 Codex CLI 处理 pyxllib/xlproject，并提交 pyxllib、xlproject、codeyun 的当前工作区变更；codeyun 不做提交前自动优化。",
    ),
    BackgroundTaskSpec(
        key="note_metadata_feedback_optimization",
        title="元数据反馈优化",
        category="AI",
        description="凌晨窗口消费节点元数据修正样本，调用 Codex CLI 优化标题和元标签生成规则。",
        schedule_label="00:00-05:59 每 30 分钟尝试",
        retry_label="无额外重试",
        action=_enqueue_note_metadata_feedback,
        manual_warning="会调用 Codex CLI；失败会跳过，不影响普通功能。",
    ),
    BackgroundTaskSpec(
        key="codex_diary_yesterday_import",
        title="Codex 星图日记",
        category="AI",
        description="每天 1 点读取昨日 Codex 会话，复用现有日记导入流程写入星图笔记。",
        schedule_label="每天 01:00",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_codex_diary,
        manual_warning="会调用 AI 配置里的 Codex 星图日记模型生成昨日总结；已导入过的日期会自动跳过。",
    ),
    BackgroundTaskSpec(
        key="attendance_summary_monthly_templates",
        title="考勤汇总模板",
        category="表格",
        description="每月 27 日凌晨为考勤汇总表补下月模板。",
        schedule_label="每天 00:05 检查，27 日执行",
        retry_label="无额外重试",
        action=_enqueue_attendance_summary_if_due,
    ),
    BackgroundTaskSpec(
        key=FANBEI_ATTENDANCE_EVENING_TASK_KEY,
        title="梵呗考勤晚间流程",
        category="考勤",
        description="梵呗课程每天晚间执行 step1-step3。step1 会调用执行设备下载小鹅通数据，step2 会从执行设备读取考勤数据并写回表格，step3 会在本机计算返款并渲染课程进度高亮。",
        schedule_label=f"每天 {FANBEI_ATTENDANCE_EVENING_RUN_TIME}",
        retry_label="无额外重试",
        action=enqueue_fanbei_attendance_evening_steps,
        manual_warning="会调用考勤配置里的执行设备运行 step1，将 step2 结果写回当前梵呗考勤表，并在本机执行 step3 返款计算与进度高亮。",
    ),
    BackgroundTaskSpec(
        key=FANBEI_ATTENDANCE_MORNING_TASK_KEY,
        title="梵呗考勤上午流程",
        category="考勤",
        description="梵呗课程每天上午执行 step4-step6。当前仅保留调度框架，具体步骤为空实现。",
        schedule_label=f"每天 {FANBEI_ATTENDANCE_MORNING_RUN_TIME}",
        retry_label="无额外重试",
        action=enqueue_fanbei_attendance_morning_steps,
        manual_warning="当前仅执行空的 step4-step6 框架，不会修改考勤数据。",
    ),
    BackgroundTaskSpec(
        key="storage_analysis",
        title="存储分析",
        category="存储",
        description="按配置定期执行附件与死链维护分析。",
        schedule_label="每天 03:00",
        retry_label="无额外重试",
        action=_enqueue_storage_analysis,
    ),
)


def get_background_task_spec(task_key: str) -> BackgroundTaskSpec | None:
    normalized = task_key.strip()
    return next((spec for spec in BACKGROUND_TASK_SPECS if spec.key == normalized), None)


class BackgroundTaskRunner:
    def __init__(self) -> None:
        scheduler_dir = get_settings().data_dir / "scheduler"
        self.state_path = scheduler_dir / "background_tasks.state.json"
        self.log_path = scheduler_dir / "background_tasks.log"
        self.lock_path = scheduler_dir / "background_tasks.lock"
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._runner: _StoppableBehaviorTreeRunner | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        if get_settings().is_test:
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._thread = threading.Thread(target=self._run_thread, name="codeyun-background-task-runner", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)

    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive() and not self._stop_event.is_set())

    def _run_thread(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(self.lock_path))
        try:
            with lock.acquire(timeout=0):
                runner = self.build_runner()
                with self._lock:
                    self._runner = runner
                    self._last_error = None
                while not self._stop_event.is_set():
                    status = runner.run_once()
                    if status != Status.RUNNING:
                        self._stop_event.wait(1)
        except Timeout:
            self._last_error = f"后台任务行为树已在运行：{self.lock_path}"
        except Exception as exc:  # pragma: no cover - runner must not crash app startup.
            self._last_error = str(exc)
        finally:
            with self._lock:
                self._runner = None

    def build_runner(self) -> _StoppableBehaviorTreeRunner:
        return _StoppableBehaviorTreeRunner(
            self.build_tree(),
            self.state_path,
            trace=1,
            log_path=self.log_path,
            stop_event=self._stop_event,
            wake_event=self._wake_event,
        )

    def build_tree(self) -> Root:
        return Root(
            MemorySelector(
                Action(self._run_task_if_enabled, "auto_git_commit")
                .daily("03:20", label="auto_git_commit", start="next", enabled=_is_task_enabled("auto_git_commit"))
                .retry(minutes=10),
                Action(self._run_task_if_enabled, "note_metadata_feedback_optimization").every(
                    minutes=30,
                    label="note_metadata_feedback_optimization",
                    persist=True,
                    enabled=_is_task_enabled("note_metadata_feedback_optimization"),
                ),
                Action(self._run_task_if_enabled, "codex_diary_yesterday_import")
                .daily(
                    "01:00",
                    label="codex_diary_yesterday_import",
                    start="next",
                    enabled=_is_task_enabled("codex_diary_yesterday_import"),
                )
                .retry(minutes=10),
                Action(self._run_task_if_enabled, "attendance_summary_monthly_templates").daily(
                    "00:05",
                    label="attendance_summary_monthly_templates",
                    start="next",
                    enabled=_is_task_enabled("attendance_summary_monthly_templates"),
                ),
                Action(self._run_task_if_enabled, FANBEI_ATTENDANCE_EVENING_TASK_KEY).daily(
                    FANBEI_ATTENDANCE_EVENING_RUN_TIME,
                    label=FANBEI_ATTENDANCE_EVENING_TASK_KEY,
                    start="next",
                    enabled=_is_task_enabled(FANBEI_ATTENDANCE_EVENING_TASK_KEY),
                ),
                Action(self._run_task_if_enabled, FANBEI_ATTENDANCE_MORNING_TASK_KEY).daily(
                    FANBEI_ATTENDANCE_MORNING_RUN_TIME,
                    label=FANBEI_ATTENDANCE_MORNING_TASK_KEY,
                    start="next",
                    enabled=_is_task_enabled(FANBEI_ATTENDANCE_MORNING_TASK_KEY),
                ),
                Action(self._run_task_if_enabled, "storage_analysis").daily(
                    "03:00",
                    label="storage_analysis",
                    start="next",
                    enabled=_is_task_enabled("storage_analysis"),
                ),
                Sequence(
                    Action(self._record_idle_summary),
                    IdleUntilNextWake(ratio=0.8, min_seconds=1, max_seconds=300),
                ),
            )
        )

    def _run_task_if_enabled(self, task_key: str) -> None:
        if not _is_task_enabled(task_key):
            return
        spec = get_background_task_spec(task_key)
        if spec is None:
            return
        spec.action()

    def _record_idle_summary(self) -> None:
        return None

    def refresh_enabled_states(self, task_key: str | None = None) -> None:
        with self._lock:
            runner = self._runner
            if runner is not None:
                _sync_runner_enabled_states(runner, task_key=task_key)
            self._wake_event.set()

    def snapshot(self) -> dict[str, Any]:
        runner = self._runner or self.build_runner()
        enabled_by_key = _build_enabled_by_key()
        _sync_runner_enabled_states(runner, enabled_by_key=enabled_by_key)
        next_wake = runner.next_wake()
        node_states = runner.state.get("nodes", {}) if isinstance(runner.state, dict) else {}
        return {
            "runner_running": self.is_running(),
            "next_wake_at": _format_datetime(next_wake),
            "state_path": str(self.state_path),
            "log_path": str(self.log_path),
            "last_error": self._last_error,
            "tasks": {
                spec.key: {
                    "next_run_at": _format_datetime(_find_task_next_run(node_states, spec.key)),
                    "enabled": bool(enabled_by_key.get(spec.key)),
                    "schedule_label": spec.schedule_label,
                    "retry_label": spec.retry_label,
                }
                for spec in BACKGROUND_TASK_SPECS
                if not _is_task_deleted(spec.key)
            },
        }

    def reset_task(self, task_key: str) -> bool:
        runner = self._runner or self.build_runner()
        changed = False
        nodes = runner.state.setdefault("nodes", {})
        for path, state in list(nodes.items()):
            if not isinstance(state, dict):
                continue
            if _path_matches_task(path, task_key) and "next_run_at" in state:
                state.pop("next_run_at", None)
                changed = True
        if changed:
            runner.save_state()
        return changed


def _format_datetime(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat()


def _parse_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value.replace(microsecond=0)
    try:
        return dt.datetime.fromisoformat(str(value)).replace(microsecond=0)
    except ValueError:
        try:
            return dt.datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _path_matches_task(path: str, task_key: str) -> bool:
    return any(part == task_key or part.startswith(f"{task_key}[") for part in str(path).split("/"))


def _iter_tree_nodes(node: Any):
    yield node
    for child in getattr(node, "children", []) or []:
        yield from _iter_tree_nodes(child)


def _build_enabled_by_key() -> dict[str, bool]:
    return {spec.key: _is_task_enabled(spec.key) for spec in BACKGROUND_TASK_SPECS if not _is_task_deleted(spec.key)}


def _sync_runner_enabled_states(
    runner: BehaviorTreeRunner,
    *,
    task_key: str | None = None,
    enabled_by_key: dict[str, bool] | None = None,
) -> None:
    enabled_by_key = dict(enabled_by_key or _build_enabled_by_key())
    task_keys = [task_key] if task_key else list(enabled_by_key)
    for node in _iter_tree_nodes(runner.root):
        if not hasattr(node, "enabled"):
            continue
        for key in task_keys:
            if _path_matches_task(getattr(node, "path", ""), key):
                setattr(node, "enabled", bool(enabled_by_key.get(key)))
                break


def _find_task_next_run(node_states: dict[str, Any], task_key: str) -> dt.datetime | None:
    values: list[dt.datetime] = []
    for path, state in node_states.items():
        if not isinstance(state, dict) or not _path_matches_task(path, task_key):
            continue
        parsed = _parse_datetime(state.get("next_run_at"))
        if parsed is not None:
            values.append(parsed)
    return min(values) if values else None


background_task_runner = BackgroundTaskRunner()


def init_background_task_runner() -> None:
    background_task_runner.start()


def shutdown_background_task_runner() -> None:
    background_task_runner.shutdown()


def get_background_task_runner_snapshot() -> dict[str, Any]:
    return background_task_runner.snapshot()


def refresh_background_task_schedule_states(task_key: str | None = None) -> None:
    background_task_runner.refresh_enabled_states(task_key)


def reset_background_task_schedule(task_key: str) -> bool:
    return background_task_runner.reset_task(task_key)


def is_background_task_deleted(task_key: str) -> bool:
    return _is_task_deleted(task_key)
