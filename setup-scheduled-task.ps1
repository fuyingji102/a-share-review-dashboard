<#
.SYNOPSIS
  创建 Windows 定时任务：每个交易日 15:30 自动采集盘后数据。
.DESCRIPTION
  此脚本会在 Windows 任务计划程序中创建定时任务。

  两种模式（运行时会询问）：
  [1] 纯采集模式（默认）：采集数据，日志保存到 logs/ 目录
  [2] Netlify 推送模式：采集后自动 git push，触发 Netlify 部署
#>

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "python"
$collectScript = Join-Path $here "collect.py"
$pushScript = Join-Path $here "collect-and-push.ps1"
$serverScript = Join-Path $here "app.py"
$logDir = Join-Path $here "logs"

# 确保日志目录存在
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

# 检查必要文件
if (-not (Test-Path -LiteralPath $collectScript)) {
    Write-Host "错误：未找到 $collectScript" -ForegroundColor Red
    exit 1
}

# 检查是否以管理员身份运行
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "⚠ 建议以管理员身份运行此脚本（右键 → 以管理员身份运行）" -ForegroundColor Yellow
    Write-Host "  否则创建定时任务可能会失败。" -ForegroundColor Yellow
}

# ====== 选择模式 ======
Write-Host ""
Write-Host "请选择采集模式：" -ForegroundColor Cyan
Write-Host "  [1] 纯采集 — 采集后保存到本地（供局域网服务器使用）" -ForegroundColor White
Write-Host "  [2] 采集 + 推送到 Netlify — 采集后 git push，触发 Netlify 自动部署" -ForegroundColor White
Write-Host ""
$mode = Read-Host "请输入 1 或 2（默认 1）"
if ($mode -eq "2") {
    $usePush = $true
    Write-Host "已选择 Netlify 推送模式" -ForegroundColor Green
} else {
    $usePush = $false
    Write-Host "已选择纯采集模式" -ForegroundColor Green
}

# ====== 任务 1：每日盘后定时采集 ======
$collectTaskName = "AShareDashboard-Collect"

if ($usePush) {
    # Netlify 模式：采集 + git push
    if (-not (Test-Path -LiteralPath $pushScript)) {
        Write-Host "错误：未找到 $pushScript，请确保 collect-and-push.ps1 存在" -ForegroundColor Red
        exit 1
    }
    $collectAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$pushScript`""
    Write-Host "采集任务将使用 Netlify 推送模式" -ForegroundColor Yellow
} else {
    # 纯采集模式
    $collectAction = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c cd /d `"$here`" && $python `"$collectScript`" >> `"$logDir\collect.log`" 2>&1"
}

$collectTrigger = New-ScheduledTaskTrigger -Daily -At "15:30"
$collectTrigger.DaysOfWeek = "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
$collectSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

try {
    Register-ScheduledTask -TaskName $collectTaskName -Action $collectAction -Trigger $collectTrigger -Settings $collectSettings -Force
    Write-Host "✓ 已创建定时任务「$collectTaskName」" -ForegroundColor Green
    Write-Host "  执行时间：每个交易日 15:30" -ForegroundColor Green
} catch {
    Write-Host "✗ 创建定时任务失败：$_" -ForegroundColor Red
}

# ====== 任务 2（仅纯采集模式）：开机自动启动复盘台服务器 ======
if (-not $usePush) {
    $serverTaskName = "AShareDashboard-Server"
    $serverAction = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c cd /d `"$here`" && title 复盘台服务器 && $python `"$serverScript`" --bind 0.0.0.0"
    $serverTrigger = New-ScheduledTaskTrigger -AtLogOn

    try {
        Register-ScheduledTask -TaskName $serverTaskName -Action $serverAction -Trigger $serverTrigger -Settings $collectSettings -Force
        Write-Host "✓ 已创建登录启动任务「$serverTaskName」" -ForegroundColor Green
        Write-Host "  登录后自动启动复盘台服务器（绑定 0.0.0.0，局域网可访问）" -ForegroundColor Green
    } catch {
        Write-Host "✗ 创建登录启动任务失败：$_" -ForegroundColor Red
    }
}

# ====== 结果摘要 ======
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  部署完成！" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

if ($usePush) {
    Write-Host "✅ 每天 15:30 自动采集 → 推送到 GitHub" -ForegroundColor Green
    Write-Host "✅ Netlify 自动部署" -ForegroundColor Green
    Write-Host ""
    Write-Host "🌐 手机打开 Netlify 地址即可访问" -ForegroundColor Yellow
    Write-Host "   电脑不需要一直开机" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "⚠ 首次使用请确保：" -ForegroundColor Yellow
    Write-Host "  1. 项目已关联 GitHub 远程仓库 (git remote add origin ...)" -ForegroundColor Yellow
    Write-Host "  2. GitHub 仓库已在 Netlify 导入" -ForegroundColor Yellow
    Write-Host "  3. Git 已配置自动认证（或使用 SSH Key）" -ForegroundColor Yellow
} else {
    Write-Host "✅ 每天 15:30 自动采集盘后数据" -ForegroundColor Green
    Write-Host "✅ 登录后自动启动复盘台服务器" -ForegroundColor Green
    Write-Host ""
    Write-Host "📱 手机访问方式：" -ForegroundColor Yellow
    Write-Host "  1. 查看电脑的局域网 IP（cmd 输入 ipconfig）" -ForegroundColor Yellow
    Write-Host "  2. 手机浏览器打开 http://电脑IP:8765" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "🌐 如需外网访问，推荐使用 Netlify 模式" -ForegroundColor Yellow
    Write-Host "   重新运行此脚本并选择 [2] 即可切换" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "常用命令：" -ForegroundColor Gray
Write-Host "  手动采集：           python collect.py" -ForegroundColor Gray
if ($usePush) {
    Write-Host "  手动采集并推送：     .\collect-and-push.ps1" -ForegroundColor Gray
} else {
    Write-Host "  启动服务器：         python app.py --bind 0.0.0.0" -ForegroundColor Gray
}
Write-Host "  查看采集日志：       type logs\collect.log" -ForegroundColor Gray
Write-Host "  查看定时任务：       schtasks /query /tn AShareDashboard-*" -ForegroundColor Gray
