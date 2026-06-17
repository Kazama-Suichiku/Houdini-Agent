param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Spec = Join-Path $Root "HoudiniAgent.spec"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$DistRoot = Join-Path $Root ("dist_builds\" + $Stamp)

if ($Clean) {
    $build = Join-Path $Root "build"
    if (Test-Path $build) { Remove-Item -Recurse -Force $build }
}

Set-Location $Root
python -m PyInstaller --noconfirm --distpath $DistRoot $Spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$Exe = Join-Path $DistRoot "Houdini Agent\Houdini Agent.exe"
if (!(Test-Path $Exe)) {
    throw "Build finished but launcher was not found: $Exe"
}

$Shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Houdini Agent.lnk"
$Wsh = New-Object -ComObject WScript.Shell
$Sc = $Wsh.CreateShortcut($Shortcut)
$Sc.TargetPath = $Exe
$Sc.WorkingDirectory = Split-Path $Exe
$Sc.IconLocation = $Exe
$Sc.Save()

Write-Host "Built: $Exe"
Write-Host "Shortcut: $Shortcut"
