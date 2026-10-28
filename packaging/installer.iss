; Inno Setup script. Build after PyInstaller one-folder:  iscc packaging\installer.iss /DAppVersion=0.3.0
#ifndef AppVersion
  #define AppVersion "0.3.0"
#endif
[Setup]
AppId={{B6F1E3C2-5A39-4E0B-9C57-2D8F4A6E7A10}
AppName=Writing Process Lab
AppVersion={#AppVersion}
AppPublisher=Writing Process Lab
DefaultDirName={autopf}\WritingProcessLab
DefaultGroupName=Writing Process Lab
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\release
OutputBaseFilename=WritingProcessLab-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
UninstallDisplayName=Writing Process Lab
ArchitecturesInstallIn64BitMode=x64compatible
; LicenseFile=..\LICENSE   (enable once the project owner has chosen and added a license)

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\WritingProcessLab\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Writing Process Lab"; Filename: "{app}\WritingProcessLab.exe"
Name: "{autodesktop}\Writing Process Lab"; Filename: "{app}\WritingProcessLab.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\WritingProcessLab.exe"; Description: "Launch Writing Process Lab"; Flags: nowait postinstall skipifsilent

; User data (%LOCALAPPDATA%\WritingProcessLab) is deliberately NOT removed on uninstall. Delete it from
; Writing Process Lab > Data && privacy before uninstalling, or remove that folder by hand.
