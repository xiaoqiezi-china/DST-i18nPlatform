<#
.SYNOPSIS
    DST MC I18n Tool 一键发布脚本（Windows / PowerShell）。

.DESCRIPTION
    调用 dotnet publish 生成 win-x64 自包含程序（普通多文件发布，不做单文件/自解压），产物输出到 dist/。
    前端资源（wwwroot）在构建时作为 EmbeddedResource 打进程序集，因此 dist 里不需要带 wwwroot。

.PARAMETER Configuration
    构建配置，默认 Release。

.PARAMETER Output
    输出目录，默认仓库根目录下的 dist。

.PARAMETER Clean
    发布前先清空输出目录。

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Configuration Debug -Output dist-debug
    .\build.ps1 -Clean
#>
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
