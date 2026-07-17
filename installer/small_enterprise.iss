#define AppVersion GetEnv("APP_VERSION")
#define ProjectRoot GetEnv("ACCOUNTINGDEMO_PROJECT_ROOT")
#define SourceDir GetEnv("ACCOUNTINGDEMO_DIST_DIR")
#define ReleaseDir GetEnv("ACCOUNTINGDEMO_RELEASE_DIR")

[Setup]
AppId={{5D76D84B-EACF-4A77-A9EB-60B72CF9FC47}
AppName=小企业会计智能记账报税工具
AppVersion={#AppVersion}
AppPublisher=小企业会计项目
DefaultDirName={localappdata}\Programs\SmallEnterpriseAccounting
DefaultGroupName=小企业会计
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#ReleaseDir}
OutputBaseFilename=小企业会计智能记账报税工具-Setup-{#AppVersion}
SetupIconFile={#ProjectRoot}\assets\finance-app-icon.ico
UninstallDisplayIcon={app}\小企业会计智能记账报税工具.exe
LicenseFile={#ProjectRoot}\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "{#ProjectRoot}\installer\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkedonce

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\小企业会计智能记账报税工具"; Filename: "{app}\小企业会计智能记账报税工具.exe"
Name: "{autodesktop}\小企业会计智能记账报税工具"; Filename: "{app}\小企业会计智能记账报税工具.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\小企业会计智能记账报税工具.exe"; Description: "启动小企业会计"; Flags: nowait postinstall skipifsilent
