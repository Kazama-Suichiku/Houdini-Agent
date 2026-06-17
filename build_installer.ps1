# ============================================================
# Houdini Agent — 构建 Windows 安装程序
#   1) PyInstaller 打包 one-folder 到 dist\Houdini Agent
#   2) Inno Setup 编译成单个 dist_installer\HoudiniAgent-Setup-<版本>.exe
# 前置：pip install pyinstaller，并安装 Inno Setup 6（https://jrsoftware.org/isdl.php）
# 用法：  powershell -ExecutionPolicy Bypass -File build_installer.ps1 [-Clean]
# ============================================================
param([switch]$Clean)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if ($Clean) {
    foreach ($d in @("build", "dist")) {
        $p = Join-Path $Root $d
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }
}

Write-Host "==> [1/2] PyInstaller 打包..." -ForegroundColor Cyan
python -m PyInstaller --noconfirm (Join-Path $Root "HoudiniAgent.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败 (exit $LASTEXITCODE)" }

$App = Join-Path $Root "dist\Houdini Agent\Houdini Agent.exe"
if (!(Test-Path $App)) { throw "未找到打包产物：$App" }

Write-Host "==> [2/2] Inno Setup 编译安装程序..." -ForegroundColor Cyan
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (!(Test-Path $iscc)) { $iscc = "$env:ProgramFiles\Inno Setup 6\ISCC.exe" }
# winget 默认按用户安装到 %LOCALAPPDATA%\Programs
if (!(Test-Path $iscc)) { $iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" }
if (!(Test-Path $iscc)) {
    throw "未找到 Inno Setup（ISCC.exe）。请先安装 Inno Setup 6：https://jrsoftware.org/isdl.php"
}
& $iscc (Join-Path $Root "installer\HoudiniAgent.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup 编译失败 (exit $LASTEXITCODE)" }

$out = Get-ChildItem (Join-Path $Root "dist_installer") -Filter "HoudiniAgent-Setup-*.exe" |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host ""
Write-Host "安装程序已生成：$($out.FullName)" -ForegroundColor Green
Write-Host "上传到网站：HA_SSH_PASS='密码' python deploy/upload_installer.py `"$($out.FullName)`""
