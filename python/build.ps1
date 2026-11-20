<#
构建 Python 版单文件可执行程序。

用法:
  powershell -ExecutionPolicy Bypass -File python\build.ps1 [-Python <python.exe>] [-OutDir <目录>]

依赖: Python 3.10+，且已安装 requirements.txt 中的 PyInstaller。
产物: dist-python\DST-MC-I18n-Tool.exe（自包含，无需目标机安装 Python）。
#>
param(
    [string]$Python = "python",
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OutDir)) {
    $OutDir = Join-Path $root "dist-python"
}
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("dstpy-build-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $work | Out-Null

Write-Host "Python: $Python"
Write-Host "输出:   $OutDir"

& $Python -m PyInstaller `
    --onefile `
    --console `
    --name "DST-MC-I18n-Tool" `
    --add-data "$root\wwwroot;wwwroot" `
    --distpath "$OutDir" `
    --workpath "$work" `
    --specpath "$work" `
    --exclude-module tkinter `
    --optimize 2 `
    --noconfirm `
    --clean `
    (Join-Path $PSScriptRoot "main.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 构建失败 (exit $LASTEXITCODE)"
}

Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue

$exe = Join-Path $OutDir "DST-MC-I18n-Tool.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "未找到构建产物: $exe"
}
$size = [math]::Round((Get-Item -LiteralPath $exe).Length / 1MB, 2)
Write-Host ("完成: {0} ({1} MB)" -f $exe, $size)
