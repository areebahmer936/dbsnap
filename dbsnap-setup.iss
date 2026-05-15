; Inno Setup Script for dbsnap
; Requires Inno Setup 6.x or later
; Download: https://jrsoftware.org/isdl.php

#define MyAppName "dbsnap"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "areebahmer936"
#define MyAppExeName "dbsnap.exe"
#define MyAppURL "https://github.com/areebahmer936/dbsnap"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=
OutputDir=dist\installer
OutputBaseFilename=dbsnap-{#MyAppVersion}-setup
SetupIconFile=
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "addtopath"; Description: "Add dbsnap to system PATH"; GroupDescription: "Environment:"; Flags: unchecked

[Files]
Source: "dist\dbsnap\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\dbsnap\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Code]
const
    EnvironmentKey = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';
    PathName = 'Path';

function AddToPath(Param: String): Boolean;
var
    OrigPath: String;
begin
    Result := False;
    if not RegQueryStringValue(HKEY_LOCAL_MACHINE, EnvironmentKey, PathName, OrigPath) then
        Exit;

    if Pos(';' + ExpandConstant('{app}'), ';' + OrigPath) = 0 then
    begin
        if Length(OrigPath) > 0 then
            OrigPath := OrigPath + ';' + ExpandConstant('{app}')
        else
            OrigPath := ExpandConstant('{app}');

        if not RegWriteStringValue(HKEY_LOCAL_MACHINE, EnvironmentKey, PathName, OrigPath) then
            Exit;
    end;
    Result := True;
end;

function RemoveFromPath(Param: String): Boolean;
var
    OrigPath: String;
    NewPath: String;
    PosStart: Integer;
begin
    Result := False;
    if not RegQueryStringValue(HKEY_LOCAL_MACHINE, EnvironmentKey, PathName, OrigPath) then
        Exit;

    PosStart := Pos(';' + ExpandConstant('{app}') + ';', ';' + OrigPath + ';');
    if PosStart = 0 then
    begin
        if Pos(';' + ExpandConstant('{app}), ';' + OrigPath + ';') > 0 then
            PosStart := Pos(';' + ExpandConstant('{app}), ';' + OrigPath + ';');
    end;

    if PosStart > 0 then
    begin
        NewPath := Copy(OrigPath, 1, PosStart - 2) + Copy(OrigPath, PosStart + Length(ExpandConstant('{app}')) + 1, Length(OrigPath));
        RegWriteStringValue(HKEY_LOCAL_MACHINE, EnvironmentKey, PathName, NewPath);
    end;
    Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
    if CurStep = ssPostInstall then
    begin
        if WizardIsTaskSelected('addtopath') then
        begin
            AddToPath('');
            SendMessage(HWND_BROADCAST, WM_SETTINGCHANGE, 0, LPARAM(PChar('Environment')));
        end;
    end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
    if CurUninstallStep = usPostUninstall then
    begin
        RemoveFromPath('');
        SendMessage(HWND_BROADCAST, WM_SETTINGCHANGE, 0, LPARAM(PChar('Environment')));
    end;
end;

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
