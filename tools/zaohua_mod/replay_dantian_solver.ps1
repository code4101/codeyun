param(
    [string]$SnapshotPath = (Join-Path $env:TEMP "codeyun\zaohua_mod\dantian_solver\latest.request.json"),
    [int]$TimeLimitMs = 0,
    [int]$Seed = -1
)

$ErrorActionPreference = "Stop"
$solver = Join-Path $PSScriptRoot "Code4101.DantianSolver\bin\Release\Code4101.DantianSolver.exe"
if (-not (Test-Path -LiteralPath $SnapshotPath)) {
    throw "Dantian solver snapshot not found: $SnapshotPath"
}
if (-not (Test-Path -LiteralPath $solver)) {
    throw "Dantian solver has not been built: $solver"
}

$payload = [IO.File]::ReadAllText($SnapshotPath, [Text.Encoding]::UTF8)
if ($TimeLimitMs -gt 0) {
    $payload = [Text.RegularExpressions.Regex]::Replace(
        $payload, '"timeLimitMs":\d+', ('"timeLimitMs":' + $TimeLimitMs), 1)
}
if ($Seed -ge 0) {
    $payload = [Text.RegularExpressions.Regex]::Replace(
        $payload, '"seed":-?\d+', ('"seed":' + $Seed), 1)
}
$startInfo = [Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $solver
$startInfo.WorkingDirectory = Split-Path -Parent $solver
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardInput = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$startInfo.StandardInputEncoding = [Text.Encoding]::UTF8
$startInfo.StandardOutputEncoding = [Text.Encoding]::UTF8
$startInfo.StandardErrorEncoding = [Text.Encoding]::UTF8

$process = [Diagnostics.Process]::Start($startInfo)
$process.StandardInput.Write($payload)
$process.StandardInput.Close()
$stdout = $process.StandardOutput.ReadToEnd()
$stderr = $process.StandardError.ReadToEnd()
$process.WaitForExit()
if ($stderr) { [Console]::Error.WriteLine($stderr) }
$stdout
exit $process.ExitCode
