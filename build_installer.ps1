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

# 先给主程序 exe 签名（再计算运行时指纹并打进安装包、全量更新包与增量补丁包）
Sign-File $App

Write-Host "==> [2/4] 计算冻结运行时指纹并生成轻量化增量补丁 HoudiniAgent-Patch-<版本>.zip ..." -ForegroundColor Cyan
$Ver = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
$DistApp = Join-Path $Root "dist\Houdini Agent"
$DistInstaller = Join-Path $Root "dist_installer"
if (!(Test-Path $DistInstaller)) { New-Item -ItemType Directory -Path $DistInstaller | Out-Null }
$PatchZip = Join-Path $DistInstaller ("HoudiniAgent-Patch-" + $Ver + ".zip")
python -c "import os, sys, zipfile; from pathlib import Path; from houdini_agent.utils.updater import compute_runtime_hash, PATCH_LAYER_ITEMS; src, patch_dst = Path(sys.argv[1]), Path(sys.argv[2]); rh = compute_runtime_hash(src); (src / '_internal' / 'RUNTIME_HASH').write_text(rh + '\n', encoding='utf-8'); zf = zipfile.ZipFile(patch_dst, 'w', zipfile.ZIP_DEFLATED, compresslevel=6); [zf.write(p, p.relative_to(src).as_posix()) for item in PATCH_LAYER_ITEMS for p in ([src / item] if (src / item).is_file() else (list((src / item).rglob('*')) if (src / item).is_dir() else [])) if p.is_file()]; zf.close(); print('Runtime hash:', rh[:16] + '...', '| Patch zip:', patch_dst, os.path.getsize(patch_dst))" $DistApp $PatchZip
if ($LASTEXITCODE -ne 0) { throw "生成运行时指纹或增量补丁包失败 (exit $LASTEXITCODE)" }

Write-Host "==> [3/4] 生成全量静默更新包 HoudiniAgent-App-<版本>.zip ..." -ForegroundColor Cyan
$AppZip = Join-Path $DistInstaller ("HoudiniAgent-App-" + $Ver + ".zip")
python -c "import os, sys, zipfile; src, dst = sys.argv[1], sys.argv[2]; zf = zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED, compresslevel=6); [zf.write(os.path.join(dp, f), os.path.relpath(os.path.join(dp, f), src)) for dp, _, fs in os.walk(src) for f in fs]; zf.close(); print('App zip:', dst, os.path.getsize(dst))" $DistApp $AppZip
if ($LASTEXITCODE -ne 0) { throw "生成静默更新包失败 (exit $LASTEXITCODE)" }

Write-Host "==> [4/4] Inno Setup 编译安装程序..." -ForegroundColor Cyan
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
