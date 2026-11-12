; Inno Setup script (SPEC M5): dist\SubWizard (scripts/build_windows.py) -> SubWizard-<ver>-setup.exe
; Build: iscc /DAppVersion=0.1.0 installer\subwizard.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C1E3E57-5B0F-4E0A-9A55-2C7B1D9E4A10}
AppName=SubWizard
AppVersion={#AppVersion}
AppPublisher=SubWizard
DefaultDirName={autopf}\SubWizard
DefaultGroupName=SubWizard
OutputDir=..\dist
OutputBaseFilename=SubWizard-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\SubWizard\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\SubWizard"; Filename: "{app}\SubWizard.exe"
Name: "{autodesktop}\SubWizard"; Filename: "{app}\SubWizard.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SubWizard.exe"; Description: "{cm:LaunchProgram,SubWizard}"; Flags: nowait postinstall skipifsilent
