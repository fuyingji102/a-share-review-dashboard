$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $here "..\..\work\Financial-API\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到 Financial-API 虚拟环境，请先完成安装。"
}
Set-Location -LiteralPath $here
& $python (Join-Path $here "app.py")
