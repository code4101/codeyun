[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "CodeYun-Pinterest-Backlog",
    [datetime]$DailyAt = [datetime]::Today
)

$ErrorActionPreference = "Stop"

$launcherPath = Join-Path $PSScriptRoot "download_pinterest_backlog_hidden.vbs"
$wscriptPath = Join-Path $env:SystemRoot "System32\wscript.exe"

foreach ($requiredPath in @($launcherPath, $wscriptPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required task launcher does not exist: $requiredPath"
    }
}

# wscript.exe is a GUI-subsystem executable. It prevents Windows from allocating
# a console before the VBS launcher can hide the PowerShell process.
$action = New-ScheduledTaskAction `
    -Execute $wscriptPath `
    -Argument ('"{0}"' -f $launcherPath)
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($null -ne $existingTask) {
    if ($PSCmdlet.ShouldProcess($TaskName, "update to a hidden daily task at $($DailyAt.ToString('HH:mm:ss'))")) {
        Set-ScheduledTask `
            -TaskName $TaskName `
            -Action $action `
            -Trigger $trigger | Out-Null
    }
}
else {
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal `
        -UserId $currentUser `
        -LogonType Interactive `
        -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -DontStopIfGoingOnBatteries `
        -AllowStartIfOnBatteries

    if ($PSCmdlet.ShouldProcess($TaskName, "create a hidden daily task at $($DailyAt.ToString('HH:mm:ss'))")) {
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Description "Download the CodeYun Pinterest backlog once daily without opening a console window." `
            -Action $action `
            -Trigger $trigger `
            -Principal $principal `
            -Settings $settings | Out-Null
    }
}

$task = Get-ScheduledTask -TaskName $TaskName
$taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
[pscustomobject]@{
    TaskName = $task.TaskName
    State = $task.State
    Execute = $task.Actions[0].Execute
    Arguments = $task.Actions[0].Arguments
    StartBoundary = $task.Triggers[0].StartBoundary
    RepetitionInterval = $task.Triggers[0].Repetition.Interval
    NextRunTime = $taskInfo.NextRunTime
}
