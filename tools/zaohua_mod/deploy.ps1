param(
    [string]$GameDir = "D:\SteamLibrary\steamapps\common\GodWorld"
)

$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "Zaohua.HelloWorld\bin\Release\CodeYun.Zaohua.HelloWorld.dll"
$pluginDir = Join-Path $GameDir "BepInEx\plugins\CodeYun.Zaohua.HelloWorld"

if (-not (Test-Path -LiteralPath $source)) {
    throw "Plugin has not been built: $source"
}

New-Item -ItemType Directory -Force -Path $pluginDir | Out-Null
Copy-Item -LiteralPath $source -Destination $pluginDir -Force
Write-Host "Deployed to $pluginDir"
