; Inno Setup betigi - SAP Proje Radari kurulum sihirbazi
; Derleme:  iscc packaging\radar.iss   (build.ps1 bunu cagirir)

#define AppName "SAP Proje Radari"
#define AppVersion "1.0.0"
#define AppPublisher "Egebis"
#define AppExe "SAP Proje Radari.exe"

[Setup]
AppId={{8F3C7A21-4D6E-4B2A-9C10-SAPRADAR0001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename=SAPProjeRadari-Setup
SetupIconFile=radar.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Yonetici izni istemez: kullanici klasorune de kurulabilir
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "Masaüstüne kısayol ekle"; GroupDescription: "Kısayollar:"
Name: "startupicon"; Description: "Windows açılışında otomatik başlat (arka planda)"; GroupDescription: "Başlangıç:"

[Files]
Source: "..\dist\SAP Proje Radari\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{#AppName} Kaldır"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--minimized"; Tasks: startupicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{#AppName} programını şimdi başlat"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Program dosyalari silinir; ilan veritabani kullaniciya sorulur (asagidaki kod)
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\SAP Proje Radari');
    if DirExists(DataDir) then
      if MsgBox('Toplanan ilan veritabanı ve kayıtlar da silinsin mi?' + #13#10 +
                DataDir, mbConfirmation, MB_YESNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
