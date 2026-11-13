#ifndef StageRoot
  #error StageRoot must point to a verified offline application directory.
#endif
#ifndef BuildLabel
  #define BuildLabel "1.0.0-zh"
#endif
#ifndef OutputRoot
  #error OutputRoot is required.
#endif

[Setup]
AppId={{5D4226FC-462C-4D3A-B5A1-13098E4617F5}
AppName=AutoKaraoke 中文离线完整版
AppVersion={#BuildLabel}
AppVerName=AutoKaraoke 中文离线完整版 {#BuildLabel}
AppPublisher=GiacoBot and contributors; Chinese fork by Dhz8678
AppPublisherURL=https://github.com/GiacoBot/AutoKaraoke
AppSupportURL=https://github.com/Dhz8678/AutoKaraoke
DefaultDirName={localappdata}\Programs\AutoKaraoke-Offline
DefaultGroupName=AutoKaraoke 中文离线版
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputRoot}
OutputBaseFilename=AutoKaraoke-{#BuildLabel}-windows-x64-offline-Setup
LicenseFile={#StageRoot}\app\LICENSE
InfoBeforeFile={#StageRoot}\README-离线版.txt
UninstallDisplayIcon={app}\AutoKaraoke.exe
Compression=lzma2/fast
SolidCompression=yes
DiskSpanning=yes
DiskSliceSize=1900000000
SlicesPerDisk=1
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=no
RestartApplications=no
UninstallFilesDir={app}\uninstall

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
Source: "{#StageRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AutoKaraoke 中文离线版"; Filename: "{app}\AutoKaraoke.exe"
Name: "{autodesktop}\AutoKaraoke 中文离线版"; Filename: "{app}\AutoKaraoke.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AutoKaraoke.exe"; Description: "启动 AutoKaraoke 中文离线版"; Flags: nowait postinstall skipifsilent
