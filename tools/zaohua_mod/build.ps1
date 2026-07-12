param(
    [string]$GameDir = "D:\SteamLibrary\steamapps\common\GodWorld"
)

$ErrorActionPreference = "Stop"
$project = Join-Path $PSScriptRoot "Zaohua.HelloWorld\Zaohua.HelloWorld.csproj"

dotnet build $project -c Release -p:ZaohuaGameDir=$GameDir
