; Inno Setup script for ClassDeck-Setup.exe. Compiled by build.ps1 after PyInstaller has made
; dist\ClassDeck; see docs/local-app.md, "Desktop app (Windows)".
; build.ps1 passes /DAppVersion=<version from pyproject.toml>.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "ClassDeck"
#define AppExe "ClassDeck.exe"

[Setup]
; never change the AppId: it is how a newer setup finds and upgrades an installed copy
AppId={{6F1C2B7E-3D4A-4C8B-9E21-5A7D0C3B9F14}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
AppPublisherURL=https://github.com/orms147/classdeck
; per user by default (no administrator rights); the first page offers "for all users"
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DisableDirPage=no
UsePreviousAppDir=yes
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
SetupIconFile=classdeck.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
OutputDir=..\..\dist
OutputBaseFilename=ClassDeck-Setup

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\ClassDeck\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; an upgrade replaces the bundled libraries entirely
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; close a running ClassDeck so its files can be removed
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "StopClassDeck"
