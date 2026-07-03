<#
.SYNOPSIS
  将 data/dashboard-data.json 打包为 data.js，使 index.html 可直接双击打开（无需服务器）。
.DESCRIPTION
  运行一次后，双击 index.html 即可在浏览器中查看最近一次的快照数据。
  若 data/dashboard-data.json 更新，重新运行此脚本即可。
#>
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$jsonPath = Join-Path $here "data\dashboard-data.json"
$outPath  = Join-Path $here "data.js"

if (-not (Test-Path -LiteralPath $jsonPath)) {
    Write-Host "未找到 $jsonPath ，请先使用 app.py 采集一次数据。" -ForegroundColor Red
    exit 1
}

$json = Get-Content -Path $jsonPath -Raw -Encoding UTF8
$js = "const DASHBOARD_DATA = $json;"
[System.IO.File]::WriteAllText($outPath, $js, [System.Text.Encoding]::UTF8)

Write-Host "✓ 已生成 $outPath" -ForegroundColor Green
Write-Host "  现在双击 index.html 即可在浏览器中直接查看。" -ForegroundColor Green
