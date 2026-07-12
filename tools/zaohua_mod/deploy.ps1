param(
    [string]$GameDir = "D:\SteamLibrary\steamapps\common\GodWorld"
)

$ErrorActionPreference = "Stop"
$plugins = @(
    @{
        Source = "Zaohua.HelloWorld\bin\Release\CodeYun.Zaohua.SmartAlchemy.dll"
        Directory = "CodeYun.Zaohua.SmartAlchemy"
    },
    @{
        Source = "Zaohua.NpcDifficulty\bin\Release\CodeYun.Zaohua.NpcDifficulty.dll"
        Directory = "CodeYun.Zaohua.NpcDifficulty"
    }
)

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
