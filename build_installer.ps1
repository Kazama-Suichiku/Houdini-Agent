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

# ---- 代码签名（可选）：配了证书才签，否则跳过，消除 SmartScreen「发布者未知」----
# 用法（任选一种，密钥走环境变量，勿硬编码/提交）：
#   证书在系统证书库（EV / Azure Trusted Signing 等）：  $env:HA_SIGN_THUMBPRINT = '指纹'
#   PFX 文件（OV）：  $env:HA_SIGN_PFX = 'C:\path\cert.pfx'; $env:HA_SIGN_PFX_PASS = '口令'
#   可选时间戳：     $env:HA_SIGN_TS_URL = 'http://timestamp.digicert.com'（默认即此）
function Sign-File([string]$Path) {
    if (-not ($env:HA_SIGN_THUMBPRINT -or $env:HA_SIGN_PFX)) {
        Write-Host "    (未配置代码签名证书，跳过签名：$([IO.Path]::GetFileName($Path)))" -ForegroundColor DarkYellow
        return
    }
    $signtool = $env:HA_SIGNTOOL
    if (-not $signtool) {
        $signtool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source
    }
    if (-not $signtool) {
        $cand = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
                Sort-Object FullName -Descending | Select-Object -First 1
        if ($cand) { $signtool = $cand.FullName }
    }
    if (-not $signtool) { throw "已配置签名证书但找不到 signtool.exe（设 HA_SIGNTOOL 指向它，或装 Windows SDK）" }
    $ts = if ($env:HA_SIGN_TS_URL) { $env:HA_SIGN_TS_URL } else { "http://timestamp.digicert.com" }
    $args = @("sign", "/fd", "sha256", "/td", "sha256", "/tr", $ts)
    if ($env:HA_SIGN_THUMBPRINT) { $args += @("/sha1", $env:HA_SIGN_THUMBPRINT) }
    else { $args += @("/f", $env:HA_SIGN_PFX); if ($env:HA_SIGN_PFX_PASS) { $args += @("/p", $env:HA_SIGN_PFX_PASS) } }
    $args += $Path
    Write-Host "    签名 $([IO.Path]::GetFileName($Path)) ..." -ForegroundColor Cyan
    & $signtool @args
    if ($LASTEXITCODE -ne 0) { throw "签名失败：$Path (exit $LASTEXITCODE)" }
}

Write-Host "==> [1/2] PyInstaller 打包..." -ForegroundColor Cyan
python -m PyInstaller --noconfirm (Join-Path $Root "HoudiniAgent.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败 (exit $LASTEXITCODE)" }

$App = Join-Path $Root "dist\Houdini Agent\Houdini Agent.exe"
if (!(Test-Path $App)) { throw "未找到打包产物：$App" }

# 先给主程序 exe 签名（再打进安装包）
Sign-File $App

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
# 给最终安装包签名
Sign-File $out.FullName
Write-Host ""
Write-Host "安装程序已生成：$($out.FullName)" -ForegroundColor Green
Write-Host "上传到网站：HA_SSH_PASS='密码' python deploy/upload_installer.py `"$($out.FullName)`""
