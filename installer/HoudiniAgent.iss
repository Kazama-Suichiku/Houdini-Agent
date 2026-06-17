; ============================================================
; Houdini Agent — Inno Setup installer script
; 用 build_installer.ps1 构建（先 PyInstaller，再 ISCC 编译本脚本）
; 需要 Inno Setup 6：https://jrsoftware.org/isdl.php
; 产物：dist_installer\HoudiniAgent-Setup-<版本>.exe（单个安装程序）
; ============================================================

#define AppName "Houdini Agent"
#define AppPublisher "Houdini Agent"
#define AppURL "https://houdini-agent.com"
#define AppExe "Houdini Agent.exe"
; PyInstaller one-folder 输出（相对本 .iss，在 installer\ 下）
#define SrcDir "..\dist\Houdini Agent"
; 从仓库根的 VERSION 读取版本号
#define AppVersion Trim(FileRead(FileOpen("..\VERSION")))

[Setup]
AppId={{B7E4B9C2-3A6D-4F18-9C2E-1D5A7F0E9C44}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
; 默认装到 Program Files\Houdini Agent；用户可在向导里改目录
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist_installer
OutputBaseFilename=HoudiniAgent-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 没有管理员权限时允许用户改装到个人目录
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
; 如需中文向导：装好 ChineseSimplified.isl 到 Inno 的 Languages\ 后取消下一行注释
; Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
; 桌面快捷方式：向导里显示复选框，默认勾选；用户可取消
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; 安装结束后可选立即启动
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
