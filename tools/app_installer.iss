; Inno Setup Script - Trading AI PRO by Kha Tran Design
; Phien ban: 1.0.1
; Bo cai dat Setup thuong mai chuyen nghiep - Ma hoa nhi phan 100%

#define MyAppName "Trading AI PRO by Kha Tran Design"
#define MyAppVersion "1.0.1"
#define MyAppPublisher "Kha Tran Design"
#define MyAppExeName "Trading AI PRO.exe"

[Setup]
AppId={{E58C2928-4D6C-4788-B8C1-F0918D68A777}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Trading AI PRO
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest

OutputDir=..\installer_output
OutputBaseFilename=Setup_Trading_AI_PRO_v{#MyAppVersion}
SetupIconFile=license-key-icon.ico
UninstallDisplayIcon={app}\license-key-icon.ico

Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=8
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; 1. Cac file chuong trinh va Runtime da duoc build boi PyInstaller
Source: "..\build_staging\dist\Trading AI PRO\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; 2. Dong co Nhi phan Ma hoa (Chua toan bo thuat toan va ma nguon)
Source: "..\core_engine.bin"; DestDir: "{app}"; Flags: ignoreversion

; 3. Thong tin phien ban va Icon
Source: "..\version.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "license-key-icon.ico"; DestDir: "{app}"; Flags: ignoreversion

; 4. Trong so Mo hinh AI chuyen dung cho Vang (Exness)
Source: "..\model\weights\Kronos-Gold-Exness\*"; DestDir: "{app}\model\weights\Kronos-Gold-Exness"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\model\weights\Kronos-Tokenizer-Gold-Exness\*"; DestDir: "{app}\model\weights\Kronos-Tokenizer-Gold-Exness"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\license-key-icon.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\license-key-icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
