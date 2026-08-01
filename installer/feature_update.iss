#define FromVersion GetEnv("ACCOUNTINGDEMO_PATCH_FROM_VERSION")
#define ToVersion GetEnv("ACCOUNTINGDEMO_PATCH_TO_VERSION")
#define ProjectRoot GetEnv("ACCOUNTINGDEMO_PROJECT_ROOT")
#define AppDir GetEnv("ACCOUNTINGDEMO_PATCH_APP_DIR")
#define ReleaseDir GetEnv("ACCOUNTINGDEMO_PATCH_RELEASE_DIR")

[Setup]
AppId={{D88C905C-3350-44EF-814E-F9042948C9CE}
AppName=小企业会计智能记账报税工具更新补丁
AppVersion={#ToVersion}
AppPublisher=小企业会计项目
DefaultDirName={code:GetBaseInstallDir}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#ReleaseDir}
OutputBaseFilename=小企业会计智能记账报税工具-更新补丁-{#FromVersion}-to-{#ToVersion}
SetupIconFile={#ProjectRoot}\assets\finance-app-icon.ico
Compression=lzma2/normal
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
Source: "{#AppDir}\小企业会计智能记账报税工具.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#AppDir}\_internal\config.json"; DestDir: "{app}\_internal"; Flags: ignoreversion
Source: "{#AppDir}\_internal\README.md"; DestDir: "{app}\_internal"; Flags: ignoreversion

[Code]
const
  BaseUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{5D76D84B-EACF-4A77-A9EB-60B72CF9FC47}_is1';

function GetBaseInstallDir(Param: String): String;
begin
  if not RegQueryStringValue(HKCU, BaseUninstallKey, 'InstallLocation', Result) then
    Result := ExpandConstant('{localappdata}\Programs\SmallEnterpriseAccounting');
  Result := RemoveBackslashUnlessRoot(Result);
end;

function ReadInstalledAppVersion(InstallDir: String; var Version: String): Boolean;
var
  ConfigText: AnsiString;
  Content: String;
  Tail: String;
  ConfigPath: String;
  VersionKey: String;
  StartPos: Integer;
  ValuePos: Integer;
  EndPos: Integer;
begin
  Result := False;
  ConfigPath := AddBackslash(InstallDir) + '_internal\config.json';
  if not LoadStringFromFile(ConfigPath, ConfigText) then
    Exit;

  VersionKey := '"version"';
  Content := String(ConfigText);
  StartPos := Pos(VersionKey, Content);
  if StartPos = 0 then
    Exit;

  Tail := Copy(Content, StartPos + Length(VersionKey), Length(Content));
  ValuePos := Pos('"', Tail);
  if ValuePos = 0 then
    Exit;
  Tail := Copy(Tail, ValuePos + 1, Length(Tail));
  EndPos := Pos('"', Tail);
  if EndPos = 0 then
    Exit;

  Version := Copy(Tail, 1, EndPos - 1);
  Result := Version <> '';
end;

function InitializeSetup(): Boolean;
var
  InstallDir: String;
  InstalledVersion: String;
  MainExe: String;
begin
  Result := False;
  InstallDir := GetBaseInstallDir('');
  MainExe := AddBackslash(InstallDir) + '小企业会计智能记账报税工具.exe';

  if not FileExists(MainExe) then
  begin
    SuppressibleMsgBox(
      '未检测到正式安装版。请先安装 ' + '{#FromVersion}' + ' 完整安装包，再运行本补丁。',
      mbError, MB_OK, IDOK
    );
    Exit;
  end;

  if not ReadInstalledAppVersion(InstallDir, InstalledVersion) then
  begin
    SuppressibleMsgBox(
      '无法读取已安装版本，未修改任何文件。请改用 ' + '{#ToVersion}' + ' 完整安装包。',
      mbError, MB_OK, IDOK
    );
    Exit;
  end;

  if CompareText(InstalledVersion, '{#FromVersion}') <> 0 then
  begin
    SuppressibleMsgBox(
      '本补丁只适用于 ' + '{#FromVersion}' + '。当前检测到版本 ' + InstalledVersion +
      '，未修改任何文件。请使用对应补丁或完整安装包。',
      mbError,
      MB_OK,
      IDOK
    );
    Exit;
  end;

  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RegWriteStringValue(HKCU, BaseUninstallKey, 'DisplayVersion', '{#ToVersion}');
end;
