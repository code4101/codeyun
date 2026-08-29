Option Explicit

Dim shell, fileSystem, scriptDir, powershellScript, powershellExe, command, exitCode

Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

scriptDir = fileSystem.GetParentFolderName(WScript.ScriptFullName)
powershellScript = fileSystem.BuildPath(scriptDir, "download_pinterest_backlog.ps1")
powershellExe = fileSystem.BuildPath(shell.ExpandEnvironmentStrings("%SystemRoot%"), _
    "System32\WindowsPowerShell\v1.0\powershell.exe")
command = """" & powershellExe & """ -NoProfile -NonInteractive -WindowStyle Hidden " _
    & "-ExecutionPolicy Bypass -File """ _
    & powershellScript & """"

' Window style 0 is applied by the GUI-subsystem host before PowerShell starts,
' preventing the brief console flash that -WindowStyle Hidden cannot avoid.
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
