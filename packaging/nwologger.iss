; Inno Setup script for the Windows installer. CI passes /DAppVersion=x.y.z.
#ifndef AppVersion
  #define AppVersion "0.1.5"
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
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
WizardStyle=modern

[Files]
Source: "..\dist\NWOlogger\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\NWOlogger"; Filename: "{app}\NWOlogger.exe"
Name: "{autodesktop}\NWOlogger"; Filename: "{app}\NWOlogger.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\NWOlogger.exe"; Description: "Start NWOlogger"; Flags: nowait postinstall skipifsilent
