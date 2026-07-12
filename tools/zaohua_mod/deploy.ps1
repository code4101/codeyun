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
    Write-Host "Deployed to $pluginDir"
}
