; Inno Setup 6 script for the Surge installer. packaging\build.ps1 builds the bundle first, then runs:
;   ISCC.exe /DAppVersion=0.1.0 packaging\surge.iss
; Per-user install (no admin prompt) into %LOCALAPPDATA%\Programs\Surge. User data lives apart, in %LOCALAPPDATA%\Surge,
; so updates and uninstalls never touch clips, accounts or settings unless you say so.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef AppVersionNumeric
  #define AppVersionNumeric "0.1.0.0"
#endif
#ifndef AppName
  #define AppName "Surge"
#endif
; Test builds override AppGuid and AppName so they can never touch a real install's registration or shortcuts.
#ifndef AppGuid
  #define AppGuid "1FFBB38F-6BD5-4B7A-8897-F019E3B7D4A5"
#endif
#define AppId "{{" + AppGuid + "}"
#define AppExe "Surge.exe"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Surge
VersionInfoVersion={#AppVersionNumeric}
VersionInfoProductVersion={#AppVersionNumeric}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
VersionInfoCompany=Surge
VersionInfoCopyright=MIT License
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

{ Closes Surge and the helper programs it started (ffmpeg, yt-dlp, streamlink) when they run from Dir, and checks that
  Surge.exe can be replaced. Windows' Restart Manager sometimes cannot close the app (error 350), and an update that
  starts replacing files while Surge.exe is still running leaves a broken install. This runs before anything is touched. }
function StopRunningCopies(Dir: String): Boolean;
var
  code: Integer;
  cmd: String;
begin
  StringChangeEx(Dir, '''', '''''', True);
  cmd := '-NoProfile -ExecutionPolicy Bypass -Command "'
    + '$ErrorActionPreference = ''SilentlyContinue''; $d = ''' + AddBackslash(Dir) + '''; '
    + '$stop = (Get-Date).AddSeconds(30); '
    + 'do { '
    + 'Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($d, [StringComparison]::OrdinalIgnoreCase) -and $_.ProcessName -notlike ''unins*'' } | '
    + 'ForEach-Object { if ($_.MainWindowHandle -ne 0) { [void]$_.CloseMainWindow(); [void]$_.WaitForExit(8000) }; '
    + 'if (-not $_.HasExited) { $_.Kill(); [void]$_.WaitForExit(5000) } }; '
    + '$exe = $d + ''Surge.exe''; $locked = $false; '
    + 'if (Test-Path $exe) { try { $f = [IO.File]::Open($exe, ''Open'', ''ReadWrite'', ''None''); $f.Close() } catch { $locked = $true } }; '
    + 'if (-not $locked) { exit 0 }; Start-Sleep -Seconds 1 '
    + '} while ((Get-Date) -lt $stop); exit 1"';
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), cmd, '', SW_HIDE, ewWaitUntilTerminated, code)
            and (code = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\Surge.exe')) then
    if not StopRunningCopies(ExpandConstant('{app}')) then
      Result := 'Surge is still running from ' + ExpandConstant('{app}') + ' and could not be closed.' + #13#10#13#10
                + 'Quit Surge (check Task Manager for Surge.exe, ffmpeg.exe and yt-dlp.exe), then run Setup again. '
                + 'Nothing has been changed.';
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo,
  MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo;
  if MemoGroupInfo <> '' then Result := Result + NewLine + NewLine + MemoGroupInfo;
  if MemoTasksInfo <> '' then Result := Result + NewLine + NewLine + MemoTasksInfo;
  if FileExists(ExpandConstant('{app}\Surge.exe')) then
    Result := Result + NewLine + NewLine + 'Surge is already installed here. If it is running, Setup closes it first. '
              + 'Your clips, accounts and settings are kept.';
end;

function InitializeUninstall: Boolean;
begin
  Result := True;
  if not StopRunningCopies(ExpandConstant('{app}')) then
  begin
    MsgBox('Surge is still running and could not be closed. Quit it (check Task Manager), then run the uninstaller again.',
           mbError, MB_OK);
    Result := False;
  end;
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
