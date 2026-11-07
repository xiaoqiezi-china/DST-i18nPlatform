

[CmdletBinding()]
param(
    [string]$Configuration = 'Release',
    [string]$Output = 'dist',
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Join-Path $root 'DstMcI18nTool.csproj'
$outDir = if ([System.IO.Path]::IsPathRooted($Output)) { $Output } else { Join-Path $root $Output }

function Write-Step([string]$message) {
    Write-Host ''
    Write-Host ('==> ' + $message) -ForegroundColor Cyan
}

if (-not (Test-Path -LiteralPath $project)) {
    throw ('找不到项目文件: ' + $project)
}

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw '没有找到 dotnet 命令，请先安装 .NET SDK 10.0 或更高版本：https://dotnet.microsoft.com/download'
}

$sdkVersion = (& dotnet --version).Trim()
Write-Step ('使用 .NET SDK ' + $sdkVersion + ' 发布 ' + $Configuration + ' / win-x64')

if ($Clean -and (Test-Path -LiteralPath $outDir)) {
    Write-Step ('清空输出目录 ' + $outDir)
    Remove-Item -LiteralPath $outDir -Recurse -Force
}

Write-Step ('dotnet publish -> ' + $outDir)
$publishArgs = @(
    'publish', $project,
    '-c', $Configuration,
    '-r', 'win-x64',
    '-o', $outDir,
    '-p:SelfContained=true',
    '-p:PublishSingleFile=false',
    '-p:IncludeNativeLibrariesForSelfExtract=false',
    '--nologo'
)
& dotnet @publishArgs
if ($LASTEXITCODE -ne 0) {
    throw ('dotnet publish 失败（exit code ' + $LASTEXITCODE + '）')
}

$exe = Join-Path $outDir 'DST-MC-I18n-Tool.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    throw ('发布完成但没有找到 ' + $exe + '，请检查 AssemblyName 配置。')
}

$files = @(Get-ChildItem -LiteralPath $outDir -File)
$sizeMb = [math]::Round((($files | Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
Write-Step '发布完成'
Write-Host ('  可执行文件 : ' + $exe)
Write-Host ('  输出目录   : ' + $outDir + '（' + $files.Count + ' 个文件，共 ' + $sizeMb + ' MB）')
Write-Host '  注意       : 整个目录一起分发，不要只拷 exe' -ForegroundColor Yellow
Write-Host ''
Write-Host '运行方式：' -ForegroundColor Green
Write-Host ('  "' + $exe + '" --path "<整合包目录>"')
Write-Host ('  "' + $exe + '" --help')
Write-Host ''
