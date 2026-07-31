#define BaseVersion GetEnv("ACCOUNTINGDEMO_BASE_VERSION")
#define HotfixTag GetEnv("ACCOUNTINGDEMO_HOTFIX_TAG")
#define ProjectRoot GetEnv("ACCOUNTINGDEMO_PROJECT_ROOT")
#define HotfixExe GetEnv("ACCOUNTINGDEMO_HOTFIX_EXE")
#define ReleaseDir GetEnv("ACCOUNTINGDEMO_HOTFIX_RELEASE_DIR")

[Setup]
AppId={{8C5DAF20-8D7A-45B3-9281-5A58787D5981}
AppName=小企业会计 OCR 识别补丁
AppVersion={#BaseVersion}-{#HotfixTag}
AppPublisher=小企业会计项目
DefaultDirName={code:GetBaseInstallDir}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#ReleaseDir}
OutputBaseFilename=小企业会计-OCR补丁-{#BaseVersion}-{#HotfixTag}
SetupIconFile={#ProjectRoot}\assets\finance-app-icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
SetupLogging=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
Uninstallable=no

[Languages]
Name: "chinesesimp"; MessagesFile: "{#ProjectRoot}\installer\ChineseSimplified.isl"

[Files]
Source: "{#HotfixExe}"; DestDir: "{app}"; DestName: "小企业会计智能记账报税工具.exe"; Flags: ignoreversion

[Code]
const
  BaseUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{5D76D84B-EACF-4A77-A9EB-60B72CF9FC47}_is1';

function GetBaseInstallDir(Param: String): String;
begin
  if not RegQueryStringValue(HKCU, BaseUninstallKey, 'InstallLocation', Result) then
    Result := ExpandConstant('{localappdata}\Programs\SmallEnterpriseAccounting');
  Result := RemoveBackslashUnlessRoot(Result);
end;

function InitializeSetup(): Boolean;
var
  InstalledVersion: String;
  InstallDir: String;
  MainExe: String;
  RuntimeConfig: String;
begin
  Result := False;

  if not RegQueryStringValue(HKCU, BaseUninstallKey, 'DisplayVersion', InstalledVersion) then
  begin
    MsgBox('未检测到“小企业会计智能记账报税工具”正式安装版。请先安装 1.7.1，再运行本补丁。', mbError, MB_OK);
    Exit;
  end;

  if CompareText(InstalledVersion, '{#BaseVersion}') <> 0 then
  begin
    MsgBox(
      '本补丁只适用于 1.7.1。当前检测到的版本是 ' + InstalledVersion + '，未修改任何文件。',
      mbError,
      MB_OK
    );
    Exit;
  end;

  InstallDir := GetBaseInstallDir('');
  MainExe := AddBackslash(InstallDir) + '小企业会计智能记账报税工具.exe';
  RuntimeConfig := AddBackslash(InstallDir) + '_internal\config.json';
  if (not FileExists(MainExe)) or (not FileExists(RuntimeConfig)) then
  begin
    MsgBox('1.7.1 安装目录不完整，补丁已停止。请先用完整安装包修复安装。', mbError, MB_OK);
    Exit;
  end;

  Result := True;
end;
