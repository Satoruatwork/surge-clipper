; Inno Setup 6 script for the Surge installer. packaging\build.ps1 builds the bundle first, then runs:
;   ISCC.exe /DAppVersion=0.1.0 packaging\surge.iss
; Per-user install (no admin prompt) into %LOCALAPPDATA%\Programs\Surge. User data lives apart, in %LOCALAPPDATA%\Surge,
; so updates and uninstalls never touch clips, accounts or settings unless you say so.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppName "Surge"
#define AppExe "Surge.exe"

[Setup]
AppId={{1FFBB38F-6BD5-4B7A-8897-F019E3B7D4A5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Surge
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\build\installer
OutputBaseFilename=Surge-Setup-{#AppVersion}
SetupIconFile=..\assets\surge.ico
UninstallDisplayIcon={app}\{#AppExe}
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; clean upgrade: drop the previous bundle's libraries before the new ones go in
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\build\dist\Surge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch Surge"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
const
  WebView2Key = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2Installed: Boolean;
var
  v: String;
begin
  Result := (RegQueryStringValue(HKLM32, WebView2Key, 'pv', v) and (v <> '') and (v <> '0.0.0.0'))
         or (RegQueryStringValue(HKCU, WebView2Key, 'pv', v) and (v <> '') and (v <> '0.0.0.0'));
end;

function InitializeSetup: Boolean;
var
  code: Integer;
begin
  Result := True;
  if (not WebView2Installed) and (not WizardSilent) then
    if MsgBox('Surge draws its window with the Microsoft Edge WebView2 Runtime, which is not installed on this PC.'
              + #13#10#13#10 + 'Open the Microsoft download page now? You can finish installing Surge first, '
              + 'but it will not open until the runtime is there.', mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/', '', '', SW_SHOWNORMAL, ewNoWait, code);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  dataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    dataDir := ExpandConstant('{localappdata}\Surge');
    if DirExists(dataDir) and (not UninstallSilent) then
      if MsgBox('Also delete your Surge data (clips, accounts, settings, downloaded models) in:'
                + #13#10 + dataDir + #13#10#13#10 + 'Choose No to keep it for a later reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(dataDir, True, True, True);
  end;
end;
