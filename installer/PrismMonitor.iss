; SPDX-License-Identifier: GPL-3.0-only
; Inno Setup 6 script for the Prism Monitor widget.
; Built by build_monitor.py (ISCC /DAppVersion=x.y.z installer\PrismMonitor.iss)
; from the portable folder dist\PrismMonitor.
;
; Installs per user by default (no administrator prompt); the user can choose
; an all-users install in the first dialog. Installs no drivers or services.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "Prism Monitor"
#define AppExe "PrismMonitor.exe"

[Setup]
AppId={{6F0D7A2E-4C1B-4E8A-9B7D-2A5C3E9F1B40}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Prism Light contributors
AppPublisherURL=https://github.com/danny19862025/prism-light
AppSupportURL=https://github.com/danny19862025/prism-light/issues
DefaultDirName={autopf}\Prism Monitor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
LicenseFile=..\LICENSE
SetupIconFile=..\build\PrismMonitor.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\dist
OutputBaseFilename=PrismMonitor-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\PrismMonitor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\{#AppName} documentation"; Filename: "{app}\README.md"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; The app's own "Launch at login" setting writes this value; remove it on uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "PrismMonitor"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#AppExe} /F"; Flags: runhidden; RunOnceId: "StopPrismMonitor"
