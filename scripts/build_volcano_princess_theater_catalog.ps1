param(
    [string]$GameRoot = 'D:\SteamLibrary\steamapps\common\VolcanoPrincess',
    [string]$ReverseRoot = 'D:\home\chenkunze\data\m2607火山的女儿逆向',
    [string]$SteamManifest = 'D:\SteamLibrary\steamapps\appmanifest_1669980.acf'
)

$ErrorActionPreference = 'Stop'

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

$gameRootPath = [System.IO.Path]::GetFullPath($GameRoot)
$reverseRootPath = [System.IO.Path]::GetFullPath($ReverseRoot)
$managedRoot = Join-Path $gameRootPath 'VolcanoPrincess_Data\Managed'
$configRoot = Join-Path $gameRootPath 'VolcanoPrincess_Data\StreamingAssets\dataConfig'
$assemblyPath = Join-Path $managedRoot 'Assembly-CSharp.dll'
$coreModulePath = Join-Path $managedRoot 'UnityEngine.CoreModule.dll'
$dataPath = Join-Path $configRoot 'data'
$txtPath = Join-Path $configRoot 'txt'

foreach ($path in @($assemblyPath, $coreModulePath, $dataPath, $txtPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required Volcano Princess file not found: $path"
    }
}

[void][System.Reflection.Assembly]::LoadFrom($coreModulePath)
$assembly = [System.Reflection.Assembly]::LoadFrom($assemblyPath)
$flags = [System.Reflection.BindingFlags]'Public,NonPublic,Static,Instance'
$dataSysType = $assembly.GetType('DataSys', $true)
$dataConfigType = $assembly.GetType('DataConfig', $true)
$languageType = $assembly.GetType('LanguageSys', $true)
$txtType = $assembly.GetType('Txt', $true)

# Load the four-language text table first. DataConfig stores text IDs and resolves
# them through LanguageSys while the binary config is read.
$languageSys = [System.Runtime.Serialization.FormatterServices]::GetUninitializedObject($languageType)
$languageType.GetField('Instan', $flags).SetValue($null, $languageSys)
$txts = [Array]::CreateInstance($txtType, 20000)
$txtStream = [System.IO.File]::OpenRead($txtPath)
$txtReader = [System.IO.BinaryReader]::new($txtStream)
try {
    for ($group = 0; $group -lt 4; $group++) {
        $count = $txtReader.ReadInt32()
        for ($i = 0; $i -lt $count; $i++) {
            $txt = [Activator]::CreateInstance($txtType)
            $index = [int]$txtReader.ReadString()
            $contents = [string[]]::new(4)
            for ($language = 0; $language -lt 4; $language++) {
                $contents[$language] = $txtReader.ReadString()
            }
            $txtType.GetField('index', $flags).SetValue($txt, $index)
            $txtType.GetField('contents', $flags).SetValue($txt, $contents)
            $txts.SetValue($txt, $index)
        }
    }
}
finally {
    $txtReader.Dispose()
    $txtStream.Dispose()
}
$txtArguments = [object[]]::new(1)
$txtArguments[0] = $txts
$languageType.GetMethod('DefaultSetTxt', $flags).Invoke($languageSys, $txtArguments)

$dataSys = [System.Runtime.Serialization.FormatterServices]::GetUninitializedObject($dataSysType)
$config = [Activator]::CreateInstance($dataConfigType)
$dataSysType.GetField('dataCfg', $flags).SetValue($dataSys, $config)
$dataSysType.GetField('Instan', $flags).SetValue($null, $dataSys)

# ReadDataConfig opens with exclusive access and expects a writable location, so
# parse an exact temporary copy instead of touching the Steam installation.
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) 'codeyun\volcano-princess'
[void][System.IO.Directory]::CreateDirectory($tempRoot)
$tempDataPath = Join-Path $tempRoot ('data-' + [guid]::NewGuid().ToString('N'))
Copy-Item -LiteralPath $dataPath -Destination $tempDataPath
try {
    $arguments = [object[]]@([string]$tempDataPath)
    $dataSysType.GetMethod('ReadDataConfig', $flags).Invoke($dataSys, $arguments)
}
finally {
    Remove-Item -LiteralPath $tempDataPath -Force -ErrorAction SilentlyContinue
}

$dramas = @($dataConfigType.GetField('dramaList', $flags).GetValue($config))
$dramaTypeNames = @($dataConfigType.GetField('dramaTypeName', $flags).GetValue($config))
$natureNames = @($dataConfigType.GetField('natureCh', $flags).GetValue($config))
if ($dramas.Count -eq 0) {
    throw 'DataConfig.dramaList is empty'
}

$referenceLineTypes = @($dramas[0].lineTypeCh)
$questions = [System.Collections.Generic.List[object]]::new()
$questionIndex = 0
for ($typeIndex = 0; $typeIndex -lt $referenceLineTypes.Count; $typeIndex++) {
    $lines = @($dramas[0].dramaLines[$typeIndex])
    for ($lineIndex = 0; $lineIndex -lt $lines.Count; $lineIndex++) {
        $questions.Add([ordered]@{
            index = $questionIndex
            line_type_index = $typeIndex
            line_type = [string]$referenceLineTypes[$typeIndex]
            line_index = $lineIndex
            content = [string]$lines[$lineIndex]
        })
        $questionIndex++
    }
}

# Every shipped drama points at the same acting-question bank. Fail loudly if a
# later game build changes that rule instead of silently dropping new questions.
$referenceTypesKey = $referenceLineTypes -join "`0"
$referenceLinesKey = @(
    for ($i = 0; $i -lt $referenceLineTypes.Count; $i++) {
        @($dramas[0].dramaLines[$i]) -join "`0"
    }
) -join "`1"
foreach ($drama in $dramas) {
    $typesKey = @($drama.lineTypeCh) -join "`0"
    $linesKey = @(
        for ($i = 0; $i -lt @($drama.lineTypeCh).Count; $i++) {
            @($drama.dramaLines[$i]) -join "`0"
        }
    ) -join "`1"
    if ($typesKey -ne $referenceTypesKey -or $linesKey -ne $referenceLinesKey) {
        throw "Drama $($drama.index) has a different question bank"
    }
}

$dramaRows = @($dramas | ForEach-Object {
    $drama = $_
    $requirements = @(
        for ($i = 0; $i -lt @($drama.natures).Count; $i++) {
            $value = [int]$drama.natures[$i]
            if ($value -gt 0) {
                [ordered]@{
                    nature_index = $i
                    nature = [string]$natureNames[$i]
                    value = $value
                }
            }
        }
    )
    [ordered]@{
        index = [int]$drama.index
        name = [string]$drama.chName
        description = [string]$drama.des
        role = [string]$drama.character
        theater_level = [int]$drama.level
        category_index = [int]$drama.type
        category = [string]$dramaTypeNames[$drama.type]
        drama_variant = [int]$drama.dramaType
        sponsor_index = [int]$drama.sponsor
        requirements = $requirements
        charm = [int]$drama.charm
        base_salary = [int]$drama.price
        fame = [int]$drama.fame
    }
})

$buildId = $null
if (Test-Path -LiteralPath $SteamManifest -PathType Leaf) {
    $manifestText = Get-Content -LiteralPath $SteamManifest -Raw
    $match = [regex]::Match($manifestText, '"buildid"\s+"([^"]+)"')
    if ($match.Success) {
        $buildId = $match.Groups[1].Value
    }
}

$lineColors = @('red', 'yellow', 'green', 'pink', 'grey')
$lineTypes = @(
    for ($i = 0; $i -lt $referenceLineTypes.Count; $i++) {
        [ordered]@{ index = $i; name = [string]$referenceLineTypes[$i]; game_color = $lineColors[$i] }
    }
)
$catalog = [ordered]@{
    schema_version = 1
    app_id = 'volcano_princess'
    app_name = '火山的女儿'
    generated_at = [DateTimeOffset]::UtcNow.ToString('o')
    source = [ordered]@{
        build_id = $buildId
        engine = 'Unity 6000.0.26f1'
        game_root = $gameRootPath
        config_data = 'VolcanoPrincess_Data/StreamingAssets/dataConfig/data'
        config_txt = 'VolcanoPrincess_Data/StreamingAssets/dataConfig/txt'
        assembly = 'VolcanoPrincess_Data/Managed/Assembly-CSharp.dll'
        data_sha256 = Get-Sha256 $dataPath
        txt_sha256 = Get-Sha256 $txtPath
        assembly_sha256 = Get-Sha256 $assemblyPath
    }
    summary = [ordered]@{
        drama_count = $dramaRows.Count
        question_count = $questions.Count
        line_type_count = $lineTypes.Count
        drama_category_count = $dramaTypeNames.Count
    }
    mechanics = [ordered]@{
        rounds = 3
        options_per_round = 3
        energy_cost = 6
        shared_question_bank = $true
        correct_rule = '选择与题目要求情绪相同的台词'
        correct_answer_bonus = 0.1
        performance_bgm_index = 22
        performance_bgm_name = '022 演出'
        performance_bgm_path_id = 5257
    }
    line_types = $lineTypes
    drama_categories = $dramaTypeNames
    nature_names = $natureNames
    questions = $questions
    dramas = $dramaRows
}

$outputRoot = Join-Path $reverseRootPath 'parsed_configs\theater_catalog'
[void][System.IO.Directory]::CreateDirectory($outputRoot)
$outputPath = Join-Path $outputRoot 'catalog.json'
$catalog | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $outputPath -Encoding utf8

Write-Host "奥拉夫剧院图鉴已生成：$outputPath"
Write-Host "剧目 $($dramaRows.Count) 个；台词题 $($questions.Count) 道；情绪类型 $($lineTypes.Count) 种。"
