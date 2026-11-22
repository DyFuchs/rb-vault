; Rekordbox Vault - Windows Installer Script (Inno Setup)
#ifndef AppVersion
  #define AppVersion "0.3.3"
#endif

[Setup]
AppId={{8F6C7A2D-5C31-4B8E-9A14-REKORDBOXVAULT}}
AppName=Rekordbox Vault
AppVersion={#AppVersion}
AppPublisher=DyFuchs
AppPublisherURL=https://github.com/DyFuchs/rb-vault
DefaultDirName={autopf}\Rekordbox Vault
DefaultGroupName=Rekordbox Vault
UninstallDisplayIcon={app}\RekordboxVault.exe
SetupIconFile=..\app\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
OutputDir=..\dist_installers
OutputBaseFilename=RekordboxVault-v{#AppVersion}-Windows-Setup

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: checkedonce

[Files]
Source: "..\dist\RekordboxVault\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Rekordbox Vault"; Filename: "{app}\RekordboxVault.exe"; IconFilename: "{app}\RekordboxVault.exe"
Name: "{autodesktop}\Rekordbox Vault"; Filename: "{app}\RekordboxVault.exe"; Tasks: desktopicon; IconFilename: "{app}\RekordboxVault.exe"

[Run]
Filename: "{app}\RekordboxVault.exe"; Description: "Abrir Rekordbox Vault agora"; Flags: nowait postinstall skipifsilent
