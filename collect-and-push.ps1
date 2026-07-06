<#
.SYNOPSIS
  采集盘后数据 → 提交到 Git → 推送到 GitHub（触发 Netlify 自动部署）
.DESCRIPTION
  配合 Windows 定时任务每天 15:30 运行。
  前置条件：项目已初始化 Git 并关联到 GitHub，Netlify 已关联该仓库。
#>
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$logFile = Join-Path $here "logs\push.log"
$date = Get-Date -Format "yyyy-MM-dd"
$python = Join-Path $here "..\..\work\Financial-API\.venv\Scripts\python.exe"

# 确保 logs 目录存在
$logDir = Split-Path $logFile -Parent
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

function Log { param([string]$msg) $line = "[$(Get-Date -Format 'HH:mm:ss')] $msg"; Write-Host $line; $line | Out-File -FilePath $logFile -Append -Encoding UTF8 }

Log "===== 开始盘后采集：$date ====="

# 1. 采集数据
Log "正在采集..."
if (-not (Test-Path -LiteralPath $python)) {
    Log "未找到项目 Python 环境：$python"
    exit 1
}
$result = & $python "$here\collect.py" 2>&1
$result | ForEach-Object { Log $_ }

# 检查采集成败
$failed = $result -match "失败"
if ($failed) {
    Log "采集失败，跳过 Git 推送"
    exit 1
}

# 2. 提交到 Git
Log "提交到 Git..."
Set-Location $here
git add data/dashboard-data.json data/history/ logs/
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Log "数据无变化，无需推送"
    exit 0
}
git commit -m "daily snapshot $date"
git push
if ($LASTEXITCODE -ne 0) {
    Log "Git 推送失败，请检查远程仓库配置"
    exit 1
}

Log "✅ 推送成功！Netlify 将在几分钟内自动部署。"
Log "   在线地址：https://xxx.netlify.app"
