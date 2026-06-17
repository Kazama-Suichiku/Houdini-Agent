# ============================================================
# Houdini Agent — 一键发版：构建安装程序 + 上传到网站下载目录
#   powershell -ExecutionPolicy Bypass -File release.ps1            # 构建并上传
#   powershell -ExecutionPolicy Bypass -File release.ps1 -Clean     # 先清理再构建
#   powershell -ExecutionPolicy Bypass -File release.ps1 -UploadOnly # 跳过构建，只传最新产物
# 密码运行时安全输入（不写文件、不进命令历史）。
# 前置：Inno Setup 6 + pip install pyinstaller paramiko
# ============================================================
param(
    [switch]$Clean,
    [switch]$UploadOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

# 1) 构建（除非 -UploadOnly）
if (-not $UploadOnly) {
    Write-Host "==> 构建安装程序..." -ForegroundColor Cyan
    & (Join-Path $Root "build_installer.ps1") -Clean:$Clean
    if ($LASTEXITCODE -ne 0) { throw "构建失败" }
}

# 2) 找到最新的 setup.exe
$exe = Get-ChildItem (Join-Path $Root "dist_installer") -Filter "HoudiniAgent-Setup-*.exe" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $exe) { throw "dist_installer 下没有找到 HoudiniAgent-Setup-*.exe，请先构建" }
Write-Host "==> 待上传：$($exe.Name)" -ForegroundColor Cyan

# 3) 安全输入密码
$secure = Read-Host "服务器 SSH 密码 (ubuntu@houdini-agent.com)" -AsSecureString
$bstr   = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$plain  = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

# 4) 上传
try {
    $env:HA_SSH_PASS = $plain
    python (Join-Path $Root "deploy\upload_installer.py") $exe.FullName
    if ($LASTEXITCODE -ne 0) { throw "上传失败" }
}
finally {
    Remove-Item Env:HA_SSH_PASS -ErrorAction SilentlyContinue
    $plain = $null
}

Write-Host ""
Write-Host "发版完成。下载链接：https://houdini-agent.com/download/HoudiniAgent-Setup.exe" -ForegroundColor Green
