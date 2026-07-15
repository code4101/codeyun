param(
    [string]$GameDir = "D:\SteamLibrary\steamapps\common\GodWorld"
)

$ErrorActionPreference = "Stop"
$plugins = @(
    @{
        Source = "Code4101.Tiandao\bin\Release\Code4101.Zaohua.Tiandao.dll"
        Directory = "Code4101.Zaohua.Tiandao"
    }
)

$legacyPluginDirs = @(
    (Join-Path $GameDir "BepInEx\plugins\CodeYun.Zaohua.SmartAlchemy"),
    (Join-Path $GameDir "BepInEx\plugins\CodeYun.Zaohua.NpcDifficulty")
)
$pluginRoot = Join-Path $GameDir "BepInEx\plugins"
foreach ($legacyPluginDir in $legacyPluginDirs) {
    if ((Test-Path -LiteralPath $legacyPluginDir) -and
        $legacyPluginDir.StartsWith($pluginRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $legacyPluginDir -Recurse -Force
        Write-Host "Removed legacy plugin from $legacyPluginDir"
    }
}

foreach ($plugin in $plugins) {
    $source = Join-Path $PSScriptRoot $plugin.Source
    $pluginDir = Join-Path $GameDir ("BepInEx\plugins\" + $plugin.Directory)
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Plugin has not been built: $source"
    }
    New-Item -ItemType Directory -Force -Path $pluginDir | Out-Null
    Copy-Item -LiteralPath $source -Destination $pluginDir -Force
    if ($plugin.Directory -eq "Code4101.Zaohua.Tiandao") {
        $jsonSource = Join-Path $PSScriptRoot "Code4101.Tiandao\bin\Release\Newtonsoft.Json.dll"
        if (-not (Test-Path -LiteralPath $jsonSource)) {
            throw "Plugin JSON dependency has not been built: $jsonSource"
        }
        Copy-Item -LiteralPath $jsonSource -Destination $pluginDir -Force
        $solverSource = Join-Path $PSScriptRoot "Code4101.DantianSolver\bin\Release"
        $solverDir = Join-Path $pluginDir "solver"
        if (-not (Test-Path -LiteralPath (Join-Path $solverSource "Code4101.DantianSolver.exe"))) {
            throw "Dantian solver has not been built: $solverSource"
        }
        if (Test-Path -LiteralPath $solverDir) {
            Remove-Item -LiteralPath $solverDir -Recurse -Force
        }
        New-Item -ItemType Directory -Force -Path $solverDir | Out-Null
        Get-ChildItem -LiteralPath $solverSource -File |
            Where-Object { $_.Extension -in ".exe", ".dll", ".config" } |
            Copy-Item -Destination $solverDir -Force
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot "THIRD_PARTY_NOTICES.md") `
            -Destination $solverDir -Force
    }
    Write-Host "Deployed to $pluginDir"
}
