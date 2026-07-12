param(
    [string]$GameDir = "D:\SteamLibrary\steamapps\common\GodWorld"
)

$ErrorActionPreference = "Stop"
$projects = @(
    "Zaohua.HelloWorld\Zaohua.HelloWorld.csproj",
    "Zaohua.NpcDifficulty\Zaohua.NpcDifficulty.csproj"
)

foreach ($relativeProject in $projects) {
    $project = Join-Path $PSScriptRoot $relativeProject
    dotnet build $project -c Release -p:ZaohuaGameDir=$GameDir
    if ($LASTEXITCODE -ne 0) {
        throw "Build failed: $project"
    }
}
