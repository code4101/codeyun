from pathlib import Path


RUNTIME_PAGE = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "standard"
    / "fanxiu"
    / "data-annotation-runtime"
    / "page.vue"
)


def test_runtime_page_keeps_game_state_inspection_ui_contract():
    """The game-state inspection panel is a permanent product surface."""

    source = RUNTIME_PAGE.read_text(encoding="utf-8")

    required_fragments = (
        "PRODUCT CONTRACT — DO NOT DELETE",
        'data-testid="game-state-inspection-panel"',
        "getFanxiuGameStateInspectionStatus",
        "FanxiuGameStateInspectionStatus",
        "const gameStateInspection = ref",
        "const refreshGameStateInspection = async",
        "refreshGameStateInspection(),",
        "游戏状态巡检",
        "gameStateInspectionIntervalText",
        "gameStateInspectionProbeText",
        "gameStateInspection?.last_checked_at",
        "gameStateInspection?.last_message",
    )
    missing = [fragment for fragment in required_fragments if fragment not in source]

    assert not missing, f"凡修 Runtime 页禁止删除游戏状态巡检 UI，缺少：{missing}"


def test_runtime_page_has_no_self_service_job_creation_ui():
    """A Scheduler row is useful only after its Job behavior exists in code."""

    source = RUNTIME_PAGE.read_text(encoding="utf-8")

    forbidden_fragments = (
        "作业 +",
        "schedulerJobCatalog",
        "schedulerJobCatalogVisible",
        "getFanxiuDataAnnotationSchedulerJobCatalog",
        "addFanxiuDataAnnotationSchedulerJob",
        "openSchedulerJobCatalog",
        "addSchedulerJob",
    )
    present = [fragment for fragment in forbidden_fragments if fragment in source]

    assert not present, f"凡修 Runtime 页禁止恢复脱离业务实现的自助添加作业入口：{present}"
    assert "时间编排" in source


def test_runtime_page_runs_both_business_time_modes_as_direct_cells():
    source = RUNTIME_PAGE.read_text(encoding="utf-8")

    assert "triggerOnceFanxiuDataAnnotationSchedulerTask" not in source
    assert "runContextTaskEarly" in source
    assert "runContextTaskNow" in source
    assert "'planned'" in source
    assert "'current'" in source


def test_runtime_page_exposes_planned_time_run_with_help_contract():
    source = RUNTIME_PAGE.read_text(encoding="utf-8")

    required_fragments = (
        "runNowFanxiuDataAnnotationSchedulerTask",
        "runContextTaskEarly",
        "runContextTaskNow",
        "提前运行（按计划时间）",
        "立即运行（按当前时间）",
        "默认方式",
        "业务时间模拟为原下次触发时间后 1 分钟",
        "把下次时间直接推进到下一周期",
    )
    missing = [fragment for fragment in required_fragments if fragment not in source]

    assert not missing, f"凡修 Runtime 页缺少手动运行时钟语义：{missing}"


def test_runtime_page_edits_only_the_explicit_next_time_model():
    source = RUNTIME_PAGE.read_text(encoding="utf-8")

    assert "setFanxiuDataAnnotationSchedulerTaskNextTime" in source
    assert "立即运行（按当前时间）" in source
    assert "提前运行（按计划时间）" in source
    assert "取消执行" in source
    assert "执行时间…" in source
    assert "调度规则" not in source
    assert "saveFanxiuDataAnnotationSchedulerTasks" not in source


def test_runtime_page_does_not_report_kernel_error_before_status_loads():
    source = RUNTIME_PAGE.read_text(encoding="utf-8")

    assert "type KernelDisplayState = 'loading' | 'disabled' | 'enabled' | 'error'" in source
    assert "if (runtimeStatus.value === null) return 'loading'" in source
    assert "if (runtimeStatus.value.kernel?.alive === false) return 'error'" in source
    assert "loading: '加载中'" in source
    assert ':disabled="kernelDisplayState === \'loading\'"' in source


def test_runtime_page_exposes_info_window_and_precise_scene_overlays():
    source = RUNTIME_PAGE.read_text(encoding="utf-8")

    required_fragments = (
        'data-testid="fanxiu-info-window-panel"',
        "getFanxiuInfoWindowStatus",
        "setFanxiuInfoWindowSettings",
        "凡修信息窗",
        "场景编号",
        "识别置信度",
        "场景标识框",
        "show_scene_identity_shapes",
        "show_all_shapes",
        "全部 Shape",
    )
    missing = [fragment for fragment in required_fragments if fragment not in source]

    assert not missing, f"凡修 Runtime 页信息窗控件不完整，缺少：{missing}"
