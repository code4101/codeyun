$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$stateDir = Join-Path $env:TEMP "codeyun\media-sync"
$stdoutPath = Join-Path $stateDir "pinterest-backlog.stdout.log"
$stderrPath = Join-Path $stateDir "pinterest-backlog.stderr.log"
$uvPath = Join-Path $env:USERPROFILE ".local\bin\uv.exe"

New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
if (-not (Test-Path -LiteralPath $uvPath)) {
    $uvPath = (Get-Command uv -ErrorAction Stop).Source
}

$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = $repoRoot

Push-Location $repoRoot
try {
    $downloadArgs = @(
        "run",
        "python",
        (Join-Path $PSScriptRoot "download_pinterest_backlog.py"),
        "--user-id",
        "2",
        "--root-dir",
        "E:\data\m2510mn",
        "--batch-size",
        "500",
        "--state-dir",
        $stateDir
    )
    $process = Start-Process `
        -FilePath $uvPath `
        -ArgumentList $downloadArgs `
        -WorkingDirectory $repoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -Wait `
        -PassThru
    exit $process.ExitCode
}
finally {
    Pop-Location
}
