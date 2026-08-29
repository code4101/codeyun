from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


def test_task_configurator_uses_one_daily_trigger_and_gui_launcher() -> None:
    script = (ROOT_DIR / "scripts" / "configure_pinterest_backlog_task.ps1").read_text(
        encoding="utf-8"
    )

    assert "New-ScheduledTaskTrigger -Daily -At $DailyAt" in script
    assert "New-ScheduledTaskAction" in script
    assert '"System32\\wscript.exe"' in script
    assert "Set-ScheduledTask" in script
    assert "RepetitionInterval" in script
    assert "-RepetitionInterval" not in script


def test_vbs_launcher_hides_powershell_from_process_creation() -> None:
    script = (ROOT_DIR / "scripts" / "download_pinterest_backlog_hidden.vbs").read_text(
        encoding="utf-8"
    )

    assert "System32\\WindowsPowerShell\\v1.0\\powershell.exe" in script
    assert "-WindowStyle Hidden" in script
    assert "shell.Run(command, 0, True)" in script
