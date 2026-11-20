[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$Output = "dist-python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$outDir = if ([System.IO.Path]::IsPathRooted($Output)) { $Output } else { Join-Path $root $Output }

& (Join-Path $root "python\build.ps1") -Python $Python -OutDir $outDir
