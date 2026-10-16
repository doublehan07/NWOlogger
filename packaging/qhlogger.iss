; Inno Setup script for the Windows installer. CI passes /DAppVersion=x.y.z.
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6E7C4C2B-5D2A-4F7E-9C1B-3A1F2E9B7D10}
AppName=QHlogger
AppVersion={#AppVersion}
AppPublisher=Han Zhang
DefaultDirName={autopf}\QHlogger
DefaultGroupName=QHlogger
OutputDir=..\dist
OutputBaseFilename=QHlogger-{#AppVersion}-windows-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
WizardStyle=modern

[Files]
Source: "..\dist\QHlogger\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\QHlogger"; Filename: "{app}\QHlogger.exe"
Name: "{autodesktop}\QHlogger"; Filename: "{app}\QHlogger.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\QHlogger.exe"; Description: "Start QHlogger"; Flags: nowait postinstall skipifsilent
