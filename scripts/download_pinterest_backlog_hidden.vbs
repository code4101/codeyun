Option Explicit

Dim shell, fileSystem, scriptDir, powershellScript, command, exitCode

Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

scriptDir = fileSystem.GetParentFolderName(WScript.ScriptFullName)
powershellScript = fileSystem.BuildPath(scriptDir, "download_pinterest_backlog.ps1")
command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ _
    & powershellScript & """"

' Window style 0 is applied by the GUI-subsystem host before PowerShell starts,
' preventing the brief console flash that -WindowStyle Hidden cannot avoid.
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
