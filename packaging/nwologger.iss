; Inno Setup script for the Windows installer. CI and build-windows.ps1 pass /DAppVersion=x.y.z; there is no
; fallback on purpose, so a build can never carry an old number.
#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z (the version in pyproject.toml)
#endif

[Setup]
AppId={{6E7C4C2B-5D2A-4F7E-9C1B-3A1F2E9B7D10}
AppName=NWOlogger
AppVersion={#AppVersion}
AppPublisher=Han Zhang
DefaultDirName={autopf}\NWOlogger
DefaultGroupName=NWOlogger
OutputDir=..\dist
OutputBaseFilename=NWOlogger-{#AppVersion}-windows-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
WizardStyle=modern

[InstallDelete]
; An upgrade copies over the previous version's folder without removing anything, so its _internal would keep the
; old nwologger-<version>.dist-info (which Python then found first, and the app said 0.1.8) and every file the new
; version no longer ships. Nothing of the user's lives there: the log is in Documents, settings in the registry.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\NWOlogger\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\NWOlogger"; Filename: "{app}\NWOlogger.exe"
Name: "{autodesktop}\NWOlogger"; Filename: "{app}\NWOlogger.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\NWOlogger.exe"; Description: "Start NWOlogger"; Flags: nowait postinstall skipifsilent
