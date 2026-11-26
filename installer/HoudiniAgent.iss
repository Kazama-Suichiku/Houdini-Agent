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
; 写入安装包 exe 的 Windows 版本信息（资源管理器「文件版本/产品版本」列）
VersionInfoVersion={#AppVersion}
VersionInfoProductVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
; 默认安装到当前用户目录（%LOCALAPPDATA%\Programs\Houdini Agent）：免管理员权限、免 UAC，
; 支持 Claude 桌面版式后台静默更新；已有旧版装在 Program Files 的用户仍会沿用原目录平滑覆盖。
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UsePreviousAppDir=yes
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist_installer
OutputBaseFilename=HoudiniAgent-Setup-{#AppVersion}
SetupIconFile=..\assets\houdini-agent.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 应用内更新：若旧进程仍占用文件则请求关闭；装完按需重启
CloseApplications=yes
RestartApplications=no

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
; 安装结束后启动：交互安装显示复选框（默认勾选）；静默安装（立即重启更新）也会执行；
; 若由“退出时静默更新”传入 /NORELAUNCH=1，则装完不重开窗口，等用户下次打开即是新版。
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall; Check: ShouldRelaunch

[Code]
function ShouldRelaunch: Boolean;
begin
  Result := ExpandConstant('{param:NORELAUNCH|0}') <> '1';
end;
